# -*- coding: utf-8 -*-
"""
Module: crawlers.dean_extractor
Mô tả: Bóc tách nội dung tuyển sinh trên website chính thức của trường:
- Bảng mã ngành + tổ hợp + phương thức
- Bảng quy đổi / hoán đổi điểm chứng chỉ (IELTS, TOEFL, SAT, ACT, A-Level,...)
- Đoạn quy chế / quy định xét tuyển
"""

import re
from typing import List, Dict, Optional, Tuple
from bs4 import BeautifulSoup, Tag

from core.models import (
    AdmissionRecord,
    ScoreConversionRecord,
    AdmissionRegulation,
)
from core.normalizer import (
    clean_text,
    normalize_major_code,
    normalize_integer,
    normalize_score,
    normalize_score_ptxt,
    strip_accents,
    extract_year_from_text,
)
from parsers.base_parser import BaseParser


# Từ khóa nhận diện bảng quy đổi chứng chỉ
CONVERSION_HEADER_KEYS = [
    "ielts", "toefl", "toeic", "sat", "act", "a-level", "alevel",
    "vstep", "cambridge", "quy doi", "quy đổi", "diem quy", "điểm quy",
    "he chu", "hệ chữ", "diem thuong", "điểm thưởng",
    "chung chi", "chứng chỉ", "muc diem", "mức điểm",
]

# Từ khóa nhận diện đoạn quy chế
REGULATION_TITLE_PATTERNS = [
    r"quy\s*chế",
    r"quy\s*định\s*về",
    r"quy\s*định\s*ngoại\s*ngữ",
    r"tham\s*chiếu\s*quy\s*đổi",
    r"đối\s*tượng\s*xét\s*tuyển",
    r"công\s*thức",
    r"điểm\s*xét\s*tuyển",
    r"bảng\s*quy\s*đổi",
]


def detect_admission_method(text: str) -> str:
    """
    Chuẩn hóa tên phương thức xét tuyển từ tiêu đề / ghi chú.
    Phân biệt rõ: ĐGNL HSA, ĐGNL V-ACT, ĐGTD TSA, học bạ, kết hợp, THPT,...
    """
    if not text:
        return ""
    t = strip_accents(text)
    # Bỏ qua tiêu đề chung không chỉ rõ phương thức
    generic = [
        "danh sach nganh",
        "phuong thuc xet tuyen nam",
        "thong tin tuyen sinh",
        "de an tuyen sinh",
        "chi tieu tuyen sinh",
    ]
    specific_keys = [
        "danh gia nang luc", "dgnl", "hsa", "v-act", "vact",
        "danh gia tu duy", "dgtd", "tsa",
        "hoc ba", "chung chi", "ccqt", "sat", "act", "a-level",
        "xet tuyen tai nang", "xttn", "uu tien", "ket hop",
        "thpt", "tot nghiep", "diem thi",
    ]
    is_generic_title = any(g in t for g in generic) and not any(k in t for k in specific_keys)
    if is_generic_title:
        return ""

    # Ưu tiên phân biệt HSA / V-ACT / TSA trước nhóm ĐGNL/ĐGTD chung
    if "hsa" in t:
        return "ĐGNL HSA"
    if "v-act" in t or "vact" in t:
        return "ĐGNL V-ACT"
    if "tsa" in t or "dgtd" in t or "danh gia tu duy" in t:
        return "ĐGTD TSA"
    if "dgnl" in t or "danh gia nang luc" in t:
        return "Đánh giá năng lực (ĐGNL)"
    if "hoc ba" in t:
        if "ket hop" in t or "chung chi" in t or "ccnn" in t:
            return "Xét học bạ kết hợp"
        return "Xét học bạ THPT"
    if any(k in t for k in ["chung chi quoc te", "ccqt"]) or (
        any(k in t for k in ["sat", "act", "a-level", "ib"]) and "chung chi" in t
    ):
        return "Chứng chỉ quốc tế"
    if any(k in t for k in ["xet tuyen tai nang", "xttn", "uu tien", "xt thang"]):
        return "Xét tuyển tài năng"
    if "ket hop" in t:
        return "Xét tuyển kết hợp"
    if any(k in t for k in ["thpt", "tot nghiep", "diem thi"]):
        return "Điểm thi THPT"
    return ""


def method_to_calc_id(method_name: str) -> str:
    """Ánh xạ tên phương thức tiếng Việt → mã máy tính quy đổi."""
    t = strip_accents(method_name or "")
    if "hsa" in t:
        return "HSA"
    if "v-act" in t or "vact" in t:
        return "V-ACT"
    if "tsa" in t or "dgtd" in t:
        return "TSA"
    if "hoc ba" in t:
        return "HOC_BA"
    if "ket hop" in t:
        return "KET_HOP"
    if "dgnl" in t:
        return "DGNL"
    if "thpt" in t or "tot nghiep" in t or "diem thi" in t:
        return "THPT"
    if "sat" in t:
        return "SAT"
    if "act" in t:
        return "ACT"
    if "ielts" in t:
        return "IELTS"
    if "chung chi" in t or "ccqt" in t:
        return "CCQT"
    if "xttn" in t or "tai nang" in t:
        # Giữ tách diện khi tên có 1.2 / 1.3 (vd. XTTN Diện 1.2)
        if "1.2" in t or "dien 1.2" in t:
            return "XTTN_1.2"
        if "1.3" in t or "dien 1.3" in t:
            return "XTTN_1.3"
        return "XTTN"
    return ""


def _header_join(headers: List[str]) -> str:
    return strip_accents(" ".join(headers))


def _is_conversion_table(headers: List[str], sample_text: str = "") -> bool:
    joined = _header_join(headers) + " " + strip_accents(sample_text)
    return any(k in joined for k in CONVERSION_HEADER_KEYS)


def _is_major_table(headers: List[str]) -> bool:
    joined = _header_join(headers)
    has_code = any(k in joined for k in [
        "ma nganh", "ma xet tuyen", "ma xt", "ma tuyen sinh", "ma chuong trinh",
    ])
    has_name = any(k in joined for k in [
        "ten nganh", "nganh dao tao", "chuong trinh", "ten ma xet",
    ])
    return has_code and has_name


_COMBO_RE = re.compile(
    r"^(?:A00|A01|B00|C00|C01|D01|D04|D06|D07|D09|D10|D11|D14|D15|DD2|K00|K01)$",
    re.I,
)


def _combo_token(text: str) -> str:
    token = clean_text(text).upper().replace(" ", "")
    return token if _COMBO_RE.fullmatch(token) else ""


def _looks_like_combo_row(texts: List[str]) -> bool:
    return sum(1 for text in texts if _combo_token(text)) >= 3


def _cutoff_score_columns(headers: List[str]) -> List[Tuple[int, str, str]]:
    """Cột điểm chuẩn: tổ hợp môn (A01, D01, ...) hoặc một phương thức (ĐGNL)."""
    cols: List[Tuple[int, str, str]] = []
    for idx, header in enumerate(headers):
        tokens = header.split()
        combo = _combo_token(tokens[-1]) if tokens else ""
        folded = strip_accents(header)
        method = detect_admission_method(header)
        if combo:
            cols.append((idx, method or "Điểm thi THPT", combo))
        elif method and any(k in folded for k in ("thpt", "tot nghiep", "dgnl", "danh gia nang luc", "dgtd")):
            cols.append((idx, method, ""))
    if sum(1 for _, _, combo in cols if combo) < 3:
        return []
    return cols


def _flatten_header_grid(rows: List[Tag], max_rows: int = 3) -> Tuple[List[str], int]:
    """Ghép tiêu đề có rowspan/colspan thành một nhãn cho mỗi cột dữ liệu."""
    occupied: Dict[Tuple[int, int], str] = {}
    header_rows = rows[:max_rows]
    for r_i, tr in enumerate(header_rows):
        col = 0
        cells = tr.find_all(["th", "td"])
        if r_i > 0:
            texts = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
            nonempty = [text for text in texts if text]
            looks_like_methods = any(detect_admission_method(text) for text in nonempty)
            if not looks_like_methods and not _looks_like_combo_row(nonempty):
                break
        for cell in cells:
            while (r_i, col) in occupied:
                col += 1
            text = clean_text(cell.get_text(" ", strip=True))
            try:
                colspan = max(1, int(cell.get("colspan") or 1))
                rowspan = max(1, int(cell.get("rowspan") or 1))
            except ValueError:
                colspan, rowspan = 1, 1
            for rr in range(rowspan):
                for cc in range(colspan):
                    occupied[(r_i + rr, col + cc)] = text
            col += colspan
    if not occupied:
        return [], 0
    header_depth = max(r for r, _ in occupied) + 1
    ncols = max(c for _, c in occupied) + 1
    headers: List[str] = []
    for c in range(ncols):
        parts: List[str] = []
        for r in range(header_depth):
            text = occupied.get((r, c), "")
            if text and text not in parts:
                parts.append(text)
        headers.append(" ".join(parts))
    return headers, header_depth


def _method_mark_columns(headers: List[str]) -> List[Tuple[int, str]]:
    """Cột đánh dấu áp dụng từng phương thức (XTTN / ĐGTD / THPT, ...)."""
    found: List[Tuple[int, str]] = []
    seen = set()
    for idx, header in enumerate(headers):
        method = detect_admission_method(header)
        if not method or method in seen:
            continue
        # Chỉ nhận cột chỉ rõ một phương thức, không phải tiêu đề chung
        if method == "Theo đề án tuyển sinh":
            continue
        seen.add(method)
        found.append((idx, method))
    return found if len(found) >= 2 else []


def _is_method_mark(value: str) -> bool:
    """Ô đánh dấu ngành có dùng phương thức (✓, x, Ö, Có, ...)."""
    text = clean_text(value)
    if not text:
        return False
    folded = strip_accents(text).lower()
    if folded in {"x", "v", "co", "yes", "y", "1", "ok", "o"}:
        return True
    if len(text) <= 2 and any(ch in text for ch in "✓✔☑✅●•Öö×xXvV"):
        return True
    return False


def _row_cell_texts(tr: Tag) -> List[str]:
    cells: List[str] = []
    for td in tr.find_all(["th", "td"]):
        try:
            span = max(1, int(td.get("colspan") or 1))
        except ValueError:
            span = 1
        cells.append(clean_text(td.get_text(" ", strip=True)))
        cells.extend([""] * (span - 1))
    return cells


def _guess_conversion_type(headers: List[str]) -> str:
    joined = _header_join(headers)
    parts = []
    for key, label in [
        ("ielts", "IELTS"),
        ("toefl", "TOEFL"),
        ("toeic", "TOEIC"),
        ("sat", "SAT"),
        ("act", "ACT"),
        ("a-level", "A-Level"),
        ("alevel", "A-Level"),
        ("cambridge", "Cambridge"),
        ("vstep", "VSTEP"),
        ("he chu", "Hệ chữ"),
        ("diem thuong", "Điểm thưởng"),
        ("chung chi", "Chứng chỉ"),
        ("muc diem", "Mức điểm quy đổi"),
    ]:
        if key in joined and label not in parts:
            parts.append(label)
    if parts:
        return "/".join(parts)
    if "quy doi" in joined or "quy đổi" in joined:
        return "Bảng quy đổi"
    return "Quy đổi chứng chỉ"


def _is_wide_level_conversion_table(headers: List[str]) -> bool:
    """Bảng dạng ngang: Chứng chỉ | Mức điểm 5.0 | Mức điểm 6.5 | ..."""
    joined = _header_join(headers)
    muc_cols = sum(1 for h in headers if "muc diem" in strip_accents(h) or "mức điểm" in h.lower())
    return muc_cols >= 2 and ("chung chi" in joined or "ielts" in joined or "toefl" in joined)


def _find_converted_score_col(headers: List[str]) -> int:
    """Tìm cột chứa điểm quy đổi / điểm thưởng."""
    for idx, h in enumerate(headers):
        hn = strip_accents(h)
        if any(k in hn for k in ["diem quy doi", "diem quy", "quy doi", "diem thuong", "muc diem"]):
            # Ưu tiên cột "điểm quy đổi" hơn "mức điểm" đầu vào
            if "quy doi" in hn or "diem thuong" in hn:
                return idx
    for idx, h in enumerate(headers):
        hn = strip_accents(h)
        if "diem quy" in hn or hn.strip() in ["diem", "score"]:
            return idx
    # Mặc định cột cuối nếu có >= 2 cột
    return len(headers) - 1 if headers else -1


def _find_primary_cert_col(headers: List[str], score_col: int) -> int:
    """Cột chứng chỉ chính (IELTS / SAT / hệ chữ / ...)."""
    priority = ["ielts", "sat", "act", "a-level", "alevel", "chung chi", "he chu", "diem theo he"]
    for key in priority:
        for idx, h in enumerate(headers):
            if key in strip_accents(h) and idx != score_col:
                return idx
    # Cột đầu tiên khác cột điểm (bỏ STT)
    for idx, h in enumerate(headers):
        hn = strip_accents(h)
        if idx == score_col:
            continue
        if hn in ["stt", "tt", "so tt"]:
            continue
        return idx
    return 0


def _row_detail(headers: List[str], cells: List[str], skip_idxs: set) -> str:
    parts = []
    for i, h in enumerate(headers):
        if i in skip_idxs or i >= len(cells):
            continue
        val = clean_text(cells[i])
        if val:
            parts.append(f"{clean_text(h)}={val}")
    return "; ".join(parts)


def _scores_from_row(extracted: Dict[str, str], method: str) -> Tuple[Optional[float], Optional[float], float]:
    """Điểm THPT để ở diem_chuan; điểm TSA/XTTN/HSA để ở diem_chuan_ptxt."""
    raw_ptxt = extracted.get("diem_chuan_ptxt", "")
    raw_thpt = extracted.get("diem_chuan", "")
    diem = normalize_score(raw_thpt) if raw_thpt else None
    diem_ptxt = normalize_score_ptxt(raw_ptxt) if raw_ptxt else None
    mid = method_to_calc_id(method)
    if mid and mid not in {"THPT", "HOC_BA"}:
        if diem_ptxt is None and raw_thpt:
            diem_ptxt = normalize_score_ptxt(raw_thpt)
        diem = None
    elif diem is None and raw_thpt:
        diem_ptxt = diem_ptxt or normalize_score_ptxt(raw_thpt)
    thang = 30.0
    if diem_ptxt is not None:
        if diem_ptxt <= 120:
            thang = 100.0
        elif diem_ptxt <= 200:
            thang = 150.0
        else:
            thang = 1600.0
    return diem, diem_ptxt, thang


def _nearby_method(table: Tag) -> str:
    """Lấy phương thức từ đoạn chữ gần bảng nhất."""
    for prev in table.find_all_previous(["h1", "h2", "h3", "h4", "strong", "b", "p"], limit=6):
        method = detect_admission_method(prev.get_text(" ", strip=True))
        if method:
            return method
    return ""


def _nearby_title(table: Tag) -> str:
    for tag_name in ["h2", "h3", "h4", "strong", "b", "p"]:
        prev = table.find_previous(tag_name)
        if prev:
            text = clean_text(prev.get_text(" ", strip=True))
            if text and len(text) < 200:
                return text
    return ""


class DeanAdmissionExtractor:
    """Trích xuất mã ngành, phương thức, quy chế và bảng quy đổi từ HTML đề án."""

    def extract(
        self,
        html: str,
        school_code: str,
        school_name: str,
        source: str = "Online: Đề án tuyển sinh",
        default_year: Optional[int] = None,
    ) -> Tuple[List[AdmissionRecord], List[ScoreConversionRecord], List[AdmissionRegulation]]:
        soup = BeautifulSoup(html, "html.parser")
        year = default_year or self._infer_year_from_page(soup)

        admissions = self._extract_major_tables(soup, school_code, school_name, year, source)
        conversions = self._extract_conversion_tables(soup, school_code, school_name, year, source)
        regulations = self._extract_regulations(soup, school_code, school_name, year, source)

        # Gắn tóm tắt quy đổi / quy chế vào bản ghi ngành (theo trường)
        conversion_summary = self._summarize_conversions(conversions)
        regulation_summary = self._summarize_regulations(regulations)
        for rec in admissions:
            if conversion_summary and not rec.diem_quy_doi:
                rec.diem_quy_doi = conversion_summary
            if regulation_summary and not rec.quy_che:
                rec.quy_che = regulation_summary

        return admissions, conversions, regulations

    def _infer_year_from_page(self, soup: BeautifulSoup) -> Optional[int]:
        title = clean_text(soup.get_text(" ", strip=True)[:1500])
        return extract_year_from_text(title)

    def _append_cutoff_grid_rows(
        self,
        records: List[AdmissionRecord],
        seen: set,
        data_rows: List[Tag],
        headers: List[str],
        score_cols: List[Tuple[int, str, str]],
        school_code: str,
        school_name: str,
        year: int,
        source: str,
    ) -> None:
        """Mỗi ô điểm trong bảng tổ hợp / phương thức thành một bản ghi."""
        col_map = BaseParser.map_table_headers(headers)
        for tr in data_rows:
            cells = _row_cell_texts(tr)
            if len(cells) < 3:
                continue
            extracted: Dict[str, str] = {}
            for c_idx, field_name in col_map.items():
                if c_idx < len(cells) and field_name not in {"phuong_thuc", "diem_chuan", "diem_chuan_ptxt"}:
                    extracted[field_name] = cells[c_idx]
            ma_nganh = normalize_major_code(extracted.get("ma_nganh", ""))
            ten_nganh = clean_text(extracted.get("ten_nganh", ""))
            if not ma_nganh and not ten_nganh:
                continue
            if "mã" in ten_nganh.lower() and "ngành" in ten_nganh.lower():
                continue
            ghi_chu = clean_text(extracted.get("ghi_chu", ""))
            for idx, method, combo in score_cols:
                if idx >= len(cells):
                    continue
                score = normalize_score(cells[idx])
                if score is None:
                    continue
                folded = strip_accents(headers[idx] if idx < len(headers) else "")
                if combo:
                    label = f"Điểm thi THPT · {combo}"
                elif "dhqgh" in folded or "q21" in folded:
                    label = "ĐGNL ĐHQGHN"
                else:
                    label = method
                key = (ma_nganh, ten_nganh.lower(), combo.lower(), label, year)
                if key in seen:
                    continue
                seen.add(key)
                records.append(
                    AdmissionRecord(
                        ma_truong=school_code,
                        ten_truong=school_name,
                        ma_nganh=ma_nganh,
                        ten_nganh=ten_nganh or ma_nganh,
                        nam=year,
                        to_hop=combo,
                        diem_chuan=score,
                        thang_diem=30.0,
                        phuong_thuc=label,
                        ghi_chu=ghi_chu,
                        nguon=source,
                    )
                )

    def _append_method_mark_rows(
        self,
        records: List[AdmissionRecord],
        seen: set,
        data_rows: List[Tag],
        headers: List[str],
        method_cols: List[Tuple[int, str]],
        school_code: str,
        school_name: str,
        year: int,
        source: str,
        section_method: str,
    ) -> None:
        """Mỗi ô đánh dấu (XTTN / ĐGTD / THPT) thành một bản ghi phương thức."""
        col_map = BaseParser.map_table_headers(headers)
        for idx, header in enumerate(headers):
            if idx in col_map:
                continue
            folded = strip_accents(header)
            if "khoi" in folded and "to_hop" not in col_map.values():
                col_map[idx] = "to_hop"

        for tr in data_rows:
            cells = _row_cell_texts(tr)
            if len(cells) < 2:
                continue
            extracted: Dict[str, str] = {}
            for c_idx, field_name in col_map.items():
                if c_idx < len(cells) and field_name != "phuong_thuc":
                    extracted[field_name] = cells[c_idx]
            raw_ma = extracted.get("ma_nganh", "")
            raw_ten = extracted.get("ten_nganh", "")
            ma_nganh = normalize_major_code(raw_ma)
            ten_nganh = clean_text(raw_ten)
            if not ma_nganh and not ten_nganh:
                continue
            if "mã ngành" in (ma_nganh + " " + ten_nganh).lower() or "tên ngành" in ten_nganh.lower():
                continue
            if not ma_nganh and re.match(r"^[A-ZIVXLC\d]+[\.\)]\s+", ten_nganh):
                continue

            to_hop = clean_text(extracted.get("to_hop", ""))
            chi_tieu = normalize_integer(extracted.get("chi_tieu"))
            ghi_chu = clean_text(extracted.get("ghi_chu", ""))
            applied = []
            for idx, method in method_cols:
                if idx < len(cells) and _is_method_mark(cells[idx]):
                    applied.append(method)
            if not applied and section_method:
                applied = [section_method]
            if not applied:
                continue
            for method in applied:
                key = (ma_nganh, ten_nganh.lower(), to_hop.lower(), method, year)
                if key in seen:
                    continue
                seen.add(key)
                records.append(
                    AdmissionRecord(
                        ma_truong=school_code,
                        ten_truong=school_name,
                        ma_nganh=ma_nganh,
                        ten_nganh=ten_nganh or ma_nganh,
                        nam=year,
                        to_hop=to_hop,
                        chi_tieu=chi_tieu,
                        phuong_thuc=method,
                        ghi_chu=ghi_chu,
                        nguon=source,
                    )
                )

    def _extract_major_tables(
        self,
        soup: BeautifulSoup,
        school_code: str,
        school_name: str,
        year: Optional[int],
        source: str,
    ) -> List[AdmissionRecord]:
        records: List[AdmissionRecord] = []
        seen = set()

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            flat_headers, header_depth = _flatten_header_grid(rows)
            headers = [clean_text(td.get_text()) for td in rows[0].find_all(["th", "td"])]
            if _is_major_table(flat_headers):
                headers_for_check = flat_headers
            else:
                headers_for_check = headers
                header_depth = 1
            if not _is_major_table(headers_for_check):
                continue

            score_cols = _cutoff_score_columns(flat_headers if flat_headers else headers)
            if score_cols:
                self._append_cutoff_grid_rows(
                    records,
                    seen,
                    rows[header_depth:],
                    flat_headers or headers,
                    score_cols,
                    school_code,
                    school_name,
                    year or 2026,
                    source,
                )
                continue

            method_cols = _method_mark_columns(flat_headers if flat_headers else headers)
            if method_cols:
                self._append_method_mark_rows(
                    records,
                    seen,
                    rows[header_depth:],
                    flat_headers or headers,
                    method_cols,
                    school_code,
                    school_name,
                    year or 2026,
                    source,
                    _nearby_method(table),
                )
                continue

            col_map = BaseParser.map_table_headers(headers)
            # Bổ sung map thủ công nếu BaseParser bỏ sót
            for idx, h in enumerate(headers):
                hn = strip_accents(h)
                if idx not in col_map:
                    if "phuong thuc" in hn:
                        col_map[idx] = "phuong_thuc"
                    elif "khoi" in hn and "to_hop" not in col_map.values():
                        col_map[idx] = "to_hop"

            method_from_section = _nearby_method(table)
            yr = year or 2026

            for tr in rows[1:]:
                cells = [clean_text(td.get_text()) for td in tr.find_all(["th", "td"])]
                if not cells or len(cells) < 2:
                    continue
                if len(cells) == 1 or (len(set(cells)) == 1 and not cells[0]):
                    continue

                extracted: Dict[str, str] = {}
                for c_idx, f_name in col_map.items():
                    if c_idx < len(cells):
                        extracted[f_name] = cells[c_idx]

                raw_ma = extracted.get("ma_nganh", "")
                raw_ten = extracted.get("ten_nganh", "")
                # Một số bảng có STT ở cột 0 -> mã ở cột 1
                if not raw_ma and not raw_ten:
                    continue

                ma_nganh = normalize_major_code(raw_ma)
                ten_nganh = clean_text(raw_ten)
                if not ma_nganh and not ten_nganh:
                    continue
                if "mã ngành" in (ma_nganh + ten_nganh).lower() or "tên ngành" in ten_nganh.lower():
                    continue
                # Dòng tiêu đề nhóm kiểu "I. Chương trình chuẩn"
                if not ma_nganh and re.match(r"^[IVXLC\d]+[\.\)]\s+", ten_nganh):
                    continue
                if ten_nganh and not ma_nganh and len(cells) <= 2 and not extracted.get("to_hop"):
                    # Có thể là dòng nhóm
                    if not any(ch.isdigit() for ch in ten_nganh):
                        continue

                to_hop = clean_text(extracted.get("to_hop", ""))
                chi_tieu = normalize_integer(extracted.get("chi_tieu"))
                ghi_chu = clean_text(extracted.get("ghi_chu", ""))
                method_raw = extracted.get("phuong_thuc", "")
                method = (
                    detect_admission_method(method_raw)
                    or detect_admission_method(method_from_section)
                )
                if not method:
                    # Giữ nguyên text cột phương thức nếu có (thường liệt kê nhiều PTXT)
                    if method_raw and len(clean_text(method_raw)) >= 3:
                        method = clean_text(method_raw)[:150]
                    else:
                        method = "Theo đề án tuyển sinh"
                diem_chuan, diem_ptxt, thang = _scores_from_row(extracted, method)

                key = (ma_nganh, ten_nganh.lower(), to_hop.lower(), method, yr)
                if key in seen:
                    continue
                seen.add(key)

                records.append(
                    AdmissionRecord(
                        ma_truong=school_code,
                        ten_truong=school_name,
                        ma_nganh=ma_nganh,
                        ten_nganh=ten_nganh or ma_nganh,
                        nam=yr,
                        to_hop=to_hop,
                        chi_tieu=chi_tieu,
                        diem_chuan=diem_chuan,
                        diem_chuan_ptxt=diem_ptxt,
                        thang_diem=thang,
                        phuong_thuc=method,
                        ghi_chu=ghi_chu,
                        nguon=source,
                    )
                )

        return records

    def _extract_conversion_tables(
        self,
        soup: BeautifulSoup,
        school_code: str,
        school_name: str,
        year: Optional[int],
        source: str,
    ) -> List[ScoreConversionRecord]:
        records: List[ScoreConversionRecord] = []
        seen = set()

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            headers = [clean_text(td.get_text()) for td in rows[0].find_all(["th", "td"])]
            sample = " ".join(headers)
            if not _is_conversion_table(headers, sample):
                continue
            # Bỏ qua bảng mã ngành dù có từ "học bạ" trong ghi chú
            if _is_major_table(headers) and not any(
                k in _header_join(headers) for k in ["ielts", "toefl", "sat", "act", "quy doi", "he chu"]
            ):
                continue

            loai_bang = _guess_conversion_type(headers)
            method = _nearby_method(table)
            title = _nearby_title(table)
            thang = ""
            title_l = strip_accents(title + " " + " ".join(headers))
            for m in re.finditer(r"thang\s*(?:diem\s*)?(\d+)", title_l):
                thang = m.group(1)
                break

            # Dạng bảng ngang: Chứng chỉ | Nội dung | Mức điểm 5.0 | Mức điểm 6.5 | ...
            if _is_wide_level_conversion_table(headers):
                cert_col = 0
                for idx, h in enumerate(headers):
                    hn = strip_accents(h)
                    if "chung chi" in hn or "ielts" in hn or hn in ["ten", "loai"]:
                        cert_col = idx
                        break
                level_cols = [
                    (idx, clean_text(h))
                    for idx, h in enumerate(headers)
                    if "muc diem" in strip_accents(h) or "mức điểm" in h.lower()
                ]
                for tr in rows[1:]:
                    cells = [clean_text(td.get_text()) for td in tr.find_all(["th", "td"])]
                    if not cells or cert_col >= len(cells):
                        continue
                    cert_name = cells[cert_col]
                    if not cert_name or "chứng chỉ" in cert_name.lower():
                        continue
                    for col_idx, level_header in level_cols:
                        if col_idx >= len(cells):
                            continue
                        hang_muc = cells[col_idx]
                        if not hang_muc:
                            continue
                        # Mức điểm đích lấy từ tên cột (VD: "Mức điểm 9.0" -> 9.0)
                        diem_match = re.search(r"(\d+[.,]?\d*)", level_header)
                        diem = diem_match.group(1) if diem_match else level_header
                        key = (cert_name, hang_muc, diem)
                        if key in seen:
                            continue
                        seen.add(key)
                        records.append(
                            ScoreConversionRecord(
                                ma_truong=school_code,
                                ten_truong=school_name,
                                loai_bang=cert_name[:80] or loai_bang,
                                hang_muc=hang_muc,
                                diem_quy_doi=diem,
                                chi_tiet_hang=f"{level_header}: {hang_muc}",
                                phuong_thuc=method,
                                thang_diem=thang or "10",
                                nam=year,
                                ghi_chu=title[:150] if title else "",
                                nguon=source,
                            )
                        )
                continue

            score_col = _find_converted_score_col(headers)
            primary_col = _find_primary_cert_col(headers, score_col)

            for tr in rows[1:]:
                cells = [clean_text(td.get_text()) for td in tr.find_all(["th", "td"])]
                if not cells or len(cells) < 2:
                    continue
                # Bỏ dòng header lặp
                if strip_accents(cells[0]) in ["stt", "tt"] and len(cells) > 1 and "ielts" in strip_accents(cells[1]):
                    continue

                hang_muc = clean_text(cells[primary_col]) if primary_col < len(cells) else ""
                diem = clean_text(cells[score_col]) if 0 <= score_col < len(cells) else ""
                if not hang_muc and not diem:
                    continue
                # Bỏ dòng chỉ có nhãn nhóm
                if hang_muc.lower().startswith("khoảng") and not diem:
                    pass

                detail = _row_detail(headers, cells, {primary_col, score_col})
                key = (loai_bang, hang_muc, diem, detail[:80])
                if key in seen:
                    continue
                seen.add(key)

                records.append(
                    ScoreConversionRecord(
                        ma_truong=school_code,
                        ten_truong=school_name,
                        loai_bang=loai_bang,
                        hang_muc=hang_muc,
                        diem_quy_doi=diem,
                        chi_tiet_hang=detail,
                        phuong_thuc=method,
                        thang_diem=thang,
                        nam=year,
                        ghi_chu=title[:150] if title else "",
                        nguon=source,
                    )
                )

        return records

    def _extract_regulations(
        self,
        soup: BeautifulSoup,
        school_code: str,
        school_name: str,
        year: Optional[int],
        source: str,
    ) -> List[AdmissionRegulation]:
        records: List[AdmissionRegulation] = []
        seen = set()

        # 1) Tiêu đề chứa từ khóa quy chế + đoạn văn ngay sau
        candidates = soup.find_all(["h2", "h3", "h4", "strong", "b", "p"])
        for el in candidates:
            title = clean_text(el.get_text(" ", strip=True))
            if not title or len(title) > 180:
                continue
            title_l = title.lower()
            if not any(re.search(p, title_l) for p in REGULATION_TITLE_PATTERNS):
                continue

            # Thu thập nội dung đoạn kế tiếp
            chunks: List[str] = []
            # Nếu chính phần tử đã dài (là đoạn văn), dùng luôn
            if el.name == "p" and len(title) > 80:
                body = title
                short_title = title[:80] + ("..." if len(title) > 80 else "")
            else:
                short_title = title
                sibling = el.find_next_sibling()
                steps = 0
                while sibling is not None and steps < 6:
                    if getattr(sibling, "name", None) in ["h2", "h3", "h4"]:
                        break
                    if getattr(sibling, "name", None) == "table":
                        break
                    text = clean_text(sibling.get_text(" ", strip=True)) if hasattr(sibling, "get_text") else ""
                    if text and len(text) > 30:
                        chunks.append(text)
                    sibling = sibling.find_next_sibling()
                    steps += 1
                # Fallback: parent paragraph / next p
                if not chunks:
                    nxt = el.find_next("p")
                    if nxt:
                        chunks.append(clean_text(nxt.get_text(" ", strip=True)))
                body = " ".join(chunks).strip()

            if not body or len(body) < 40:
                continue
            # Cắt bớt nội dung quá dài
            if len(body) > 2500:
                body = body[:2500] + "..."

            key = (short_title[:60], body[:120])
            if key in seen:
                continue
            seen.add(key)

            records.append(
                AdmissionRegulation(
                    ma_truong=school_code,
                    ten_truong=school_name,
                    tieu_de=short_title,
                    noi_dung=body,
                    phuong_thuc=detect_admission_method(short_title + " " + body[:200]),
                    nam=year,
                    nguon=source,
                )
            )

        return records

    def _summarize_conversions(self, conversions: List[ScoreConversionRecord], limit: int = 12) -> str:
        if not conversions:
            return ""
        parts = []
        for c in conversions[:limit]:
            if c.hang_muc and c.diem_quy_doi:
                parts.append(f"{c.loai_bang}: {c.hang_muc}→{c.diem_quy_doi}")
            elif c.hang_muc:
                parts.append(f"{c.loai_bang}: {c.hang_muc}")
        extra = f" (+{len(conversions) - limit} dòng)" if len(conversions) > limit else ""
        return "; ".join(parts) + extra

    def _summarize_regulations(self, regulations: List[AdmissionRegulation], limit: int = 3) -> str:
        if not regulations:
            return ""
        parts = []
        for r in regulations[:limit]:
            snippet = r.noi_dung[:220].rstrip()
            if len(r.noi_dung) > 220:
                snippet += "..."
            parts.append(f"[{r.tieu_de}] {snippet}")
        return " | ".join(parts)
