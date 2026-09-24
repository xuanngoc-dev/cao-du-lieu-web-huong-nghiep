# -*- coding: utf-8 -*-
"""
Lấy bảng quy đổi điểm từ website nhà trường (kể cả bảng đăng dạng ảnh).

Trang tiêu biểu:
  https://ts.hust.edu.vn/tin-tuc/cach-tinh-diem-xet-tuyen-cua-dhbk-ha-noi
  Thông cáo bảng phân vị / quy đổi điểm chuẩn trên cổng tuyển sinh của trường.
"""

import base64
import binascii
import os
import re
import tempfile
from typing import List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from core.models import (
    MethodConversionBundle,
    MethodConversionImage,
    MethodConversionNote,
    MethodEquivalenceRow,
    ScoreConversionRecord,
)
from core.normalizer import clean_text, extract_year_from_text, strip_accents
from crawlers.official_site_crawler import (
    OfficialSiteCrawler,
    _blob,
    _canon,
    _same_org,
    admission_portal_urls,
    normalize_site_url,
)
from crawlers.certificate_image_parser import rows_from_certificate_image
from crawlers.dean_extractor import DeanAdmissionExtractor
from crawlers.table_image_parser import (
    equivalence_from_tokens,
    ocr_tokens,
    scalar_conversion_from_tokens,
)


_CONV_HINTS = (
    ("chung chi quoc te", 18),
    ("quy doi tuong duong", 16),
    ("bang quy doi tuong duong", 16),
    ("bang phan vi", 14),
    ("quy doi diem", 12),
    ("cach tinh diem", 12),
    ("chung chi ngoai ngu", 12),
    ("thong tin tuyen sinh", 10),
    ("diem chuan nam", 9),
    ("diem xet tuyen", 8),
    ("noi suy", 8),
    ("do lech", 7),
    ("quy doi", 6),
    ("phan vi", 5),
    ("tuong duong", 5),
)
_NOTE_HINTS = (
    "diem xet tuyen",
    "noi suy",
    "do lech diem",
    "mon chinh",
    "bach phan vi",
    "quy doi diem",
    "chung chi quoc te",
    "tuong duong",
    "khong co chenh lech",
)
# Trang công bố bảng quy đổi chính thức (khi homepage không gắn link trực tiếp).
_KNOWN_CONVERSION_PAGES = {
    "KHA": [
        "https://daotao.neu.edu.vn/vi/tin-tuc-1689/dai-hoc-kinh-te-quoc-dan-cong-bo-bang-quy-doi-tuong-duong-diem-chuan-nam-2026",
    ],
    "BKA": [
        "https://ts.hust.edu.vn/tin-tuc/thong-cao-bao-chi-ve-do-lech-giua-cac-to-hop-xet-tuyen-bang-quy-doi-diem-chuan-va-du-bao-muc-diem-trung-tuyen-vao-cac-nganh-cua-dai-hoc-bach-khoa-ha-noi-nam-2026",
        "https://ts.hust.edu.vn/tin-tuc/thong-tin-tuyen-sinh-dai-hoc-chinh-quy-nam-2026",
    ],
    "NTH": [
        "https://thongtintuyensinh.ftu.edu.vn/thong-bao/thong-tin-tuyen-sinh-cap-nhat-2026",
    ],
}
_INTL_CERT_METHOD = (
    "Xét tuyển tài năng (Diện xét tuyển theo chứng chỉ quốc tế gồm SAT, ACT, A-Level, AP, IB)"
)
_LETTER_GRADES = {"A*", "A", "B", "C", "D", "E"}
_RANGE_RE = re.compile(r"\d+(?:[.,]\d+)?\s*[-–—]\s*\d+(?:[.,]\d+)?")


def _conversion_score(text: str) -> int:
    score = 0
    for hint, weight in _CONV_HINTS:
        if hint in text:
            score += weight
    if re.search(r"\b202[4-9]\b", text):
        score += 2
    return score


def _content_root(soup):
    """Phần nội dung bài viết, tránh ảnh menu và cột tin liên quan."""
    for sel in (
        ".chitietbaiviet",
        "#content-tintuc",
        "#print-chitiet",
        "article",
        ".post-content",
        ".entry-content",
        ".news-content",
        ".description",
        "#content",
    ):
        node = soup.select_one(sel)
        if node and (node.find("img") or node.find("table") or node.find("p")):
            return node
    return soup


def _image_caption(img) -> Tuple[str, str]:
    alt = clean_text(img.get("alt") or "")
    parent = img.parent
    near = ""
    if parent and parent.name in {"p", "figure", "td", "li"}:
        bits = []
        for child in parent.children:
            if getattr(child, "name", None) in {"img", "script", "style"}:
                continue
            if hasattr(child, "get_text"):
                text = clean_text(child.get_text(" ", strip=True))
            else:
                text = clean_text(str(child))
            if text:
                bits.append(text)
        near = clean_text(" ".join(bits))
        if len(near) > 240:
            near = ""
    if not near:
        for previous in img.find_all_previous(["p", "h2", "h3", "h4"], limit=3):
            text = clean_text(previous.get_text(" ", strip=True))
            if 8 <= len(text) <= 320:
                near = text
                break
    return alt, (near or alt)


def _is_band_map(values: dict) -> bool:
    hits = 0
    for key, value in values.items():
        if key == "__stt":
            continue
        if _RANGE_RE.search(str(value or "")):
            hits += 1
    return hits >= 2


def _intl_cert_method(text: str) -> str:
    """Nhãn phương thức khi đoạn văn nêu đủ SAT/ACT/A-Level/AP/IB."""
    folded = strip_accents(text or "")
    if "chung chi" not in folded:
        return ""
    hits = sum(1 for key in ("sat", "act", "a-level", "ap", "ib") if key in folded)
    if hits >= 3:
        return _INTL_CERT_METHOD
    return ""


def _is_letter_conversion(record: ScoreConversionRecord) -> bool:
    hang = (record.hang_muc or "").strip().upper().replace(" ", "")
    loai = strip_accents(record.loai_bang or "")
    return hang in _LETTER_GRADES or "he chu" in loai


class OfficialConversionCollector:
    """Đi từ website trường, mở trang cách tính / quy đổi điểm, bóc bảng số hoặc ảnh bảng."""

    MAX_PAGES = 8

    def collect(self, school_code: str, school_name: str, website: str) -> MethodConversionBundle:
        bundle = MethodConversionBundle()
        site = normalize_site_url(website)
        if not site:
            return bundle
        fetcher = OfficialSiteCrawler()
        self.fetcher = fetcher
        org_host = urlparse(site).netloc
        queued: Set[str] = set()
        pages: List[Tuple[int, str]] = []

        def enqueue(score: int, url: str) -> None:
            canon = _canon(url)
            if not canon or canon in queued:
                return
            queued.add(canon)
            pages.append((score, url))

        seeds = [site] + admission_portal_urls(site)
        for known in _KNOWN_CONVERSION_PAGES.get((school_code or "").upper(), []):
            enqueue(40, known)
        for seed in seeds:
            try:
                res = fetcher._fetch(seed, timeout=12)
            except Exception:
                continue
            if res is None:
                continue
            final = res.url or seed
            if not _same_org(final, org_host):
                continue
            self._harvest(res, final, org_host, enqueue)
            if _conversion_score(_blob(final, "")) >= 6:
                enqueue(20, final)

        read: Set[str] = set()
        visited = 0
        while pages and visited < self.MAX_PAGES:
            pages.sort(key=lambda item: item[0], reverse=True)
            score, url = pages.pop(0)
            if score < 6:
                break
            try:
                res = fetcher._fetch(url, timeout=18)
            except Exception:
                continue
            if res is None:
                continue
            final = res.url or url
            final_canon = _canon(final)
            if not final_canon or final_canon in read:
                continue
            read.add(final_canon)
            visited += 1
            self._read_page(res, final, school_code, school_name, bundle)
            self._harvest(res, final, org_host, enqueue)

        if not bundle.conversions:
            pending = []
            for score, url in pages:
                canon = _canon(url)
                if not canon or canon in read or score < 8:
                    continue
                folded = _blob(url, "")
                if "chung chi" in folded or "thong tin tuyen sinh" in folded:
                    pending.append((score, url))
            pending.sort(reverse=True)
            for _score, url in pending[:2]:
                try:
                    res = fetcher._fetch(url, timeout=18)
                except Exception:
                    continue
                if res is None:
                    continue
                final = res.url or url
                final_canon = _canon(final)
                if not final_canon or final_canon in read:
                    continue
                read.add(final_canon)
                self._read_page(res, final, school_code, school_name, bundle)
        return bundle

    def _harvest(self, res, page_url: str, org_host: str, enqueue) -> None:
        ctype = (res.headers.get("Content-Type") or "").lower()
        if "html" not in ctype and "text" not in ctype:
            return
        try:
            soup = BeautifulSoup(res.text, "html.parser")
        except Exception:
            return
        for anchor in soup.find_all("a", href=True):
            href = (anchor.get("href") or "").strip()
            if not href or href.startswith(("#", "mailto:", "javascript:")):
                continue
            absolute = urljoin(page_url, href)
            if not _same_org(absolute, org_host):
                continue
            label = clean_text(anchor.get_text(" ", strip=True))
            score = _conversion_score(_blob(label, absolute))
            if score < 6:
                continue
            enqueue(score, absolute)

    def _read_page(self, res, url: str, code: str, name: str, bundle: MethodConversionBundle) -> None:
        try:
            soup = BeautifulSoup(res.text, "html.parser")
        except Exception:
            return
        year = extract_year_from_text(url) or extract_year_from_text(
            soup.title.get_text(" ", strip=True) if soup.title else ""
        )
        root = _content_root(soup)
        page_title = clean_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
        self._notes_from_html(root, code, name, year, url, bundle)
        self._certificate_notes(root, code, name, year, url, bundle)
        self._tables_from_html(root, code, name, year, url, bundle)
        self._certificate_tables(root, code, name, year, url, bundle)
        self._images_from_html(root, res, code, name, year, url, bundle, page_title=page_title)

    def _notes_from_html(self, soup, code, name, year, url, bundle: MethodConversionBundle) -> None:
        seen = set()
        for tag in soup.find_all(["p", "li", "td"]):
            text = clean_text(tag.get_text(" ", strip=True))
            if len(text) < 50 or len(text) > 900:
                continue
            folded = strip_accents(text)
            if not any(hint in folded for hint in _NOTE_HINTS):
                continue
            key = folded[:160]
            if key in seen:
                continue
            seen.add(key)
            title = "Cách tính điểm xét tuyển"
            if "noi suy" in folded or "phan vi" in folded:
                title = "Công thức quy đổi điểm chuẩn"
            elif "do lech" in folded:
                title = "Độ lệch điểm giữa các tổ hợp"
            bundle.notes.append(
                MethodConversionNote(
                    ma_truong=code,
                    ten_truong=name,
                    tieu_de=title,
                    noi_dung=text,
                    nam=year,
                    url_nguon=url,
                    nguon="Website trường",
                )
            )

    def _certificate_notes(self, soup, code, name, year, url, bundle: MethodConversionBundle) -> None:
        for tag in soup.find_all(["p", "li"]):
            text = clean_text(tag.get_text(" ", strip=True))
            method = _intl_cert_method(text)
            if not method or len(text) < 40:
                continue
            bundle.notes.append(
                MethodConversionNote(
                    ma_truong=code,
                    ten_truong=name,
                    tieu_de="Phương thức áp dụng",
                    noi_dung=f"{method}. {text}"[:1200],
                    nam=year,
                    url_nguon=url,
                    nguon="Website trường",
                )
            )
            return

    def _certificate_tables(self, soup, code, name, year, url, bundle: MethodConversionBundle) -> None:
        source = f"Website trường: {url}"
        try:
            _admissions, conversions, _regs = DeanAdmissionExtractor().extract(
                str(soup),
                code,
                name,
                source=source,
                default_year=year,
            )
        except Exception:
            return
        page_text = clean_text(soup.get_text(" ", strip=True))
        method = _intl_cert_method(page_text)
        seen = {
            (item.loai_bang, item.hang_muc, item.diem_quy_doi)
            for item in bundle.conversions
            if item.ma_truong == code
        }
        for record in conversions:
            if method and _is_letter_conversion(record):
                record.phuong_thuc = method
                record.thang_diem = record.thang_diem or "10"
            key = (record.loai_bang, record.hang_muc, record.diem_quy_doi)
            if key in seen or not (record.hang_muc or record.diem_quy_doi):
                continue
            seen.add(key)
            record.nguon = source
            bundle.conversions.append(record)

    def _tables_from_html(self, soup, code, name, year, url, bundle: MethodConversionBundle) -> None:
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            headers = [clean_text(td.get_text(" ", strip=True)) for td in rows[0].find_all(["th", "td"])]
            for tr in rows[1:]:
                cells = [clean_text(td.get_text(" ", strip=True)) for td in tr.find_all(["th", "td"])]
                values = {}
                stt = ""
                for idx, header in enumerate(headers):
                    if idx >= len(cells) or not cells[idx]:
                        continue
                    if strip_accents(header) in {"tt", "stt"}:
                        stt = cells[idx]
                        continue
                    values[header] = cells[idx]
                if not _is_band_map(values):
                    continue
                bundle.rows.append(
                    MethodEquivalenceRow(
                        ma_truong=code,
                        ten_truong=name,
                        tieu_de_bang="Bảng quy đổi trên website trường",
                        stt=stt,
                        cot_gia_tri=values,
                        nam=year,
                        url_nguon=url,
                        nguon="Website trường",
                    )
                )

    def _images_from_html(
        self, soup, res, code, name, year, url, bundle: MethodConversionBundle, page_title: str = ""
    ) -> None:
        page_url = res.url or url
        page_blob = strip_accents(
            f"{page_url} {page_title} {soup.get_text(' ', strip=True)[:1800]}"
        )
        page_is_conv = _conversion_score(page_blob) >= 8
        ocr_budget = 4
        for image_index, img in enumerate(soup.find_all("img"), start=1):
            src = (img.get("src") or "").strip()
            if not src:
                continue
            is_embedded = src.startswith("data:image/")
            absolute = src if is_embedded else urljoin(page_url, src)
            low = absolute.lower()
            if any(skip in low for skip in ("/logo", "icon-", "qr-code", "favicon", "house3")):
                continue
            alt, caption = _image_caption(img)
            source_hint = "" if is_embedded else absolute
            folded = strip_accents(f"{alt} {caption} {source_hint}")
            is_equiv = any(k in folded for k in ("phan vi", "to hop", "tuong duong", "quy doi"))
            is_cert = any(k in folded for k in ("chung chi", "ngoai ngu", "ccnn", "ccqt"))
            looks_useful = is_equiv or is_cert or any(
                k in folded for k in ("noi suy", "do lech", "diem chuan")
            )
            supported_image = is_embedded or low.endswith((".jpg", ".jpeg", ".png", ".webp"))
            if not looks_useful and page_is_conv and supported_image:
                looks_useful = True
                is_equiv = True
            if not looks_useful:
                continue
            desc = caption or alt
            if not desc or re.fullmatch(r"[a-z0-9_\-.]+\.(jpg|jpeg|png|webp)", desc, re.I):
                desc = page_title or "Ảnh bảng quy đổi"
            bundle.images.append(
                MethodConversionImage(
                    ma_truong=code,
                    ten_truong=name,
                    url_anh=absolute,
                    mo_ta=(desc or f"Ảnh bảng quy đổi {image_index}")[:180],
                    nam=year,
                    url_nguon=url,
                )
            )
            if is_cert and "phan vi" not in folded and "to hop" not in folded:
                self._certificate_image(absolute, code, name, year, url, bundle)
                continue
            if not is_equiv or ocr_budget <= 0:
                continue
            before = len(bundle.rows)
            self._ocr_image(absolute, desc, code, name, year, url, bundle)
            if len(bundle.rows) > before:
                ocr_budget -= 1

    def _certificate_image(self, image_url, code, name, year, page_url, bundle: MethodConversionBundle) -> None:
        done = getattr(self, "_cert_images", 0)
        if done >= 2:
            return
        self._cert_images = done + 1
        content, suffix = self._image_content(image_url)
        if not content or len(content) < 4000:
            return
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            parsed = rows_from_certificate_image(tmp_path)
        except Exception:
            return
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
        source = f"Website trường: {page_url}"
        seen = {
            (item.loai_bang, item.hang_muc, item.diem_quy_doi)
            for item in bundle.conversions
            if item.ma_truong == code
        }
        for item in parsed:
            key = (item["loai_bang"], item["hang_muc"], item["diem_quy_doi"])
            if key in seen:
                continue
            seen.add(key)
            bundle.conversions.append(
                ScoreConversionRecord(
                    ma_truong=code,
                    ten_truong=name,
                    loai_bang=item["loai_bang"],
                    hang_muc=item["hang_muc"],
                    diem_quy_doi=item["diem_quy_doi"],
                    chi_tiet_hang=item.get("chi_tiet_hang") or "",
                    phuong_thuc=item.get("phuong_thuc") or "",
                    thang_diem=item.get("thang_diem") or "",
                    nam=year,
                    ghi_chu="Bảng tham chiếu quy đổi chứng chỉ ngoại ngữ",
                    nguon=source,
                )
            )

    def _ocr_image(self, image_url, caption, code, name, year, page_url, bundle: MethodConversionBundle) -> None:
        content, suffix = self._image_content(image_url)
        if not content:
            return
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            tokens = ocr_tokens(tmp_path)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
        if not tokens:
            return
        title, parsed = equivalence_from_tokens(tokens, caption)
        if not parsed:
            title, parsed = scalar_conversion_from_tokens(tokens, caption)
        if not title:
            return
        for item in parsed:
            stt = item.pop("__stt", "")
            if not _is_band_map(item) and not (
                any(_RANGE_RE.search(str(value or "")) for value in item.values())
                and len(item) >= 2
            ):
                continue
            bundle.rows.append(
                MethodEquivalenceRow(
                    ma_truong=code,
                    ten_truong=name,
                    tieu_de_bang=title,
                    stt=stt,
                    cot_gia_tri=item,
                    nam=year,
                    url_nguon=page_url,
                    nguon="Website trường",
                )
            )

    def _image_content(self, image_url: str) -> Tuple[bytes, str]:
        if image_url.startswith("data:image/"):
            match = re.match(
                r"data:image/([a-zA-Z0-9.+-]+);base64,(.+)",
                image_url,
                re.DOTALL,
            )
            if not match:
                return b"", ".jpg"
            kind = match.group(1).lower()
            suffix = ".png" if kind == "png" else ".webp" if kind == "webp" else ".jpg"
            try:
                return base64.b64decode(match.group(2), validate=True), suffix
            except (ValueError, binascii.Error):
                return b"", suffix

        fetcher = getattr(self, "fetcher", None) or OfficialSiteCrawler()
        try:
            res = fetcher._fetch(image_url, timeout=20)
        except Exception:
            return b"", ".jpg"
        if res is None or not res.content:
            return b"", ".jpg"
        suffix = ".jpg"
        path = urlparse(image_url).path.lower()
        for ext in (".png", ".webp", ".jpeg", ".jpg"):
            if path.endswith(ext):
                suffix = ext if ext != ".jpeg" else ".jpg"
                break
        return res.content, suffix
