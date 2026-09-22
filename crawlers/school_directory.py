# -*- coding: utf-8 -*-
"""
Module: crawlers.school_directory
Mô tả: Lấy và xuất danh sách mã trường Đại học / Cao đẳng / Học viện
từ cổng tuyensinh247, phục vụ làm đầu vào cho crawler điểm chuẩn & đề án.
"""

import os
import re
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Any, Tuple

import requests
from bs4 import BeautifulSoup

from core.normalizer import clean_text


# Nhãn loại hình chuẩn
TYPE_DAI_HOC = "dai_hoc"
TYPE_CAO_DANG = "cao_dang"
TYPE_HOC_VIEN = "hoc_vien"

TYPE_LABELS = {
    TYPE_DAI_HOC: "Đại học",
    TYPE_CAO_DANG: "Cao đẳng",
    TYPE_HOC_VIEN: "Học viện",
}

# Khóa hồ sơ trường (từ mục Giới thiệu trên trang đề án)
PROFILE_KEYS = [
    "thong_tin_chung",
    "dia_chi",
    "website",
    "hotline",
    "fanpage",
    "linh_vuc_chuong_trinh",
    "vi_the_thanh_tuu",
    "gioi_thieu_url",
]

# Liên hệ cơ bản — luôn lấy kể cả khi tắt hồ sơ giới thiệu đầy đủ
CONTACT_KEYS = ["dia_chi", "website", "hotline", "fanpage", "gioi_thieu_url"]


def _has_contact(info: dict) -> bool:
    """Đủ website + địa chỉ để hiển thị bảng danh bạ."""
    return bool((info or {}).get("website")) and bool((info or {}).get("dia_chi"))


def _has_full_profile(info: dict) -> bool:
    return bool(
        (info or {}).get("thong_tin_chung")
        or (info or {}).get("vi_the_thanh_tuu")
        or (info or {}).get("linh_vuc_chuong_trinh")
    )

_TRAINING_HINTS = (
    "đào tạo", "dao tao", "lĩnh vực", "linh vuc", "chương trình", "chuong trinh",
    "ngành", "nganh", "khối ngành", "chuyên ngành",
)
_ACHIEVE_HINTS = (
    "sứ mạng", "su mang", "tầm nhìn", "tam nhin", "thành tựu", "thanh tuu",
    "vị thế", "vi the", "xếp hạng", "hang đầu", "hàng đầu", "thành lập",
    "giá trị cốt lõi", "triết lý", "phương châm", "lịch sử", "lich su",
)


def _split_label_value(text: str) -> Tuple[str, str]:
    """Tách 'Địa chỉ : Số 1…' → ('địa chỉ', 'Số 1…')."""
    t = clean_text(text or "")
    if not t:
        return "", ""
    for sep in (":", "："):
        if sep in t:
            left, right = t.split(sep, 1)
            return clean_text(left).lower(), clean_text(right)
    return "", t


def _is_training_para(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in _TRAINING_HINTS)


def _is_achieve_para(text: str) -> bool:
    low = (text or "").lower()
    return any(k in low for k in _ACHIEVE_HINTS)


def classify_school_type(name: str, page_hint: str = "") -> str:
    """Phân loại loại hình trường theo tên và nguồn trang."""
    if page_hint == TYPE_CAO_DANG:
        return TYPE_CAO_DANG
    n = (name or "").lower()
    if "cao đẳng" in n or "cao dang" in n:
        return TYPE_CAO_DANG
    if "học viện" in n or "hoc vien" in n:
        return TYPE_HOC_VIEN
    return TYPE_DAI_HOC


def extract_code_from_slug_and_text(slug: str, text: str) -> str:
    """
    Trích mã trường từ slug hoặc text liên kết.
    Hỗ trợ mã ĐH (BKA, KHA) và mã CĐ dài (CDD0209, CDT026, 063).
    """
    code = ""
    # Ưu tiên mã ở đầu text: "BKA-Đại học...", "CDD0209-Trường Cao đẳng..."
    match_text = re.match(r"^([A-Za-z0-9]{2,10})[\s\-:]", text.strip())
    if match_text:
        code = match_text.group(1).upper()
    if not code:
        # Mã ở cuối slug: ...-BKA, ...-CDD0209, ...-063
        match_slug = re.search(r"-([A-Za-z0-9]{2,10})$", slug)
        if match_slug:
            code = match_slug.group(1).upper()
    return code


class SchoolDirectory:
    """
    Danh bạ mã trường Đại học & Cao đẳng.
    Nguồn:
      - https://diemthi.tuyensinh247.com/diem-chuan.html              (ĐH / Học viện)
      - https://diemthi.tuyensinh247.com/diem-chuan.html?type=cao-dang (Cao đẳng)
    """

    BASE_URL = "https://diemthi.tuyensinh247.com"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    LISTING_SOURCES = [
        {"url": "/diem-chuan.html", "hint": TYPE_DAI_HOC, "label": "Đại học / Học viện"},
        {"url": "/diem-chuan.html?type=cao-dang", "hint": TYPE_CAO_DANG, "label": "Cao đẳng"},
    ]

    def __init__(self, cache_dir: str = "data/cache"):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self.cache_file = os.path.join(self.cache_dir, "school_slugs_cache.json")
        self.schools: Dict[str, Dict[str, Any]] = {}

    def _read_cache_file(self) -> Dict[str, Dict[str, Any]]:
        if not os.path.exists(self.cache_file):
            return {}
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _merge_preserved_fields(
        fetched: Dict[str, Dict[str, Any]],
        previous: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Giữ website/địa chỉ/hồ sơ cũ khi làm mới danh bạ từ web."""
        if not previous:
            return fetched
        for code, entry in fetched.items():
            prev = previous.get(code)
            if not isinstance(prev, dict):
                continue
            for key in PROFILE_KEYS:
                if not entry.get(key) and prev.get(key):
                    entry[key] = prev[key]
        return fetched

    def load(
        self,
        force_refresh: bool = False,
        include_dai_hoc: bool = True,
        include_cao_dang: bool = True,
        include_profile: bool = False,
        include_contact: bool = True,
        profile_limit: int = 0,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Nạp danh bạ từ cache hoặc thu thập mới từ web.
        include_profile: bổ sung hồ sơ giới thiệu đầy đủ (chậm hơn).
        include_contact: bổ sung website + địa chỉ (mặc định bật, kể cả khi tắt hồ sơ).
        """
        previous_cache = self._read_cache_file()

        if not force_refresh and previous_cache:
            try:
                data = previous_cache
                # Cache cũ (không có type) → gắn type suy luận
                normalized = {}
                for code, info in data.items():
                    if not isinstance(info, dict):
                        continue
                    item = dict(info)
                    item.setdefault("code", code)
                    item["type"] = item.get("type") or classify_school_type(
                        item.get("name", ""), ""
                    )
                    item["type_label"] = TYPE_LABELS.get(item["type"], item["type"])
                    normalized[code] = item
                # Nếu cache chỉ có ĐH (không có CĐ) và user muốn CĐ → refresh
                has_cd = any(v.get("type") == TYPE_CAO_DANG for v in normalized.values())
                if include_cao_dang and not has_cd:
                    print("[DANH_BA] Cache chưa có Cao đẳng → làm mới danh bạ...")
                else:
                    self.schools = self._filter_types(
                        normalized, include_dai_hoc, include_cao_dang
                    )
                    full = dict(normalized)
                    if include_profile:
                        self.enrich_profiles(full, only_missing=True, limit=profile_limit)
                        self._save_cache(full)
                    elif include_contact:
                        self.enrich_profiles(
                            full, only_missing=True, contact_only=True, limit=profile_limit
                        )
                        self._save_cache(full)
                    self.schools = self._filter_types(
                        full, include_dai_hoc, include_cao_dang
                    )
                    return self.schools
            except Exception:
                pass

        fetched = self.fetch_from_web(
            include_dai_hoc=include_dai_hoc,
            include_cao_dang=include_cao_dang,
        )
        fetched = self._merge_preserved_fields(fetched, previous_cache)
        if include_profile:
            self.enrich_profiles(fetched, only_missing=False, limit=profile_limit)
        elif include_contact:
            self.enrich_profiles(
                fetched, only_missing=True, contact_only=True, limit=profile_limit
            )
        self.schools = fetched
        self._save_cache(fetched)
        return self.schools

    def fetch_from_web(
        self,
        include_dai_hoc: bool = True,
        include_cao_dang: bool = True,
    ) -> Dict[str, Dict[str, Any]]:
        """thu thập danh sách trường từ các trang điểm chuẩn."""
        directory: Dict[str, Dict[str, Any]] = {}

        for src in self.LISTING_SOURCES:
            hint = src["hint"]
            if hint == TYPE_DAI_HOC and not include_dai_hoc:
                continue
            if hint == TYPE_CAO_DANG and not include_cao_dang:
                continue

            url = self.BASE_URL + src["url"]
            print(f"[DANH_BA] Đang lấy danh sách {src['label']}: {url}")
            try:
                res = requests.get(url, headers=self.HEADERS, timeout=20)
                if res.status_code != 200:
                    print(f"[CẢNH BÁO] HTTP {res.status_code} khi tải {url}")
                    continue
                soup = BeautifulSoup(res.text, "html.parser")
                count_before = len(directory)

                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if "/diem-chuan/" not in href or not href.endswith(".html"):
                        continue
                    text = clean_text(a.get_text())
                    slug = href.replace("/diem-chuan/", "").replace(".html", "")
                    if not slug or slug in ("index",):
                        continue

                    code = extract_code_from_slug_and_text(slug, text)
                    if not code:
                        continue

                    clean_name = re.sub(
                        r"^[A-Za-z0-9]{2,10}[\s\-:]+", "", text
                    ).strip() or text
                    school_type = classify_school_type(clean_name, page_hint=hint)

                    # Không ghi đè ĐH bằng bản ghi CĐ trùng mã (hiếm), ưu tiên bản ghi đầy đủ hơn
                    existing = directory.get(code)
                    entry = {
                        "code": code,
                        "name": clean_name,
                        "slug": slug,
                        "type": school_type,
                        "type_label": TYPE_LABELS.get(school_type, school_type),
                        "listing_url": url,
                    }
                    if existing:
                        # Giữ tên dài hơn / slug nếu đã có
                        if len(existing.get("name", "")) > len(clean_name):
                            entry["name"] = existing["name"]
                        if school_type == TYPE_CAO_DANG or existing.get("type") == TYPE_DAI_HOC:
                            # Ưu tiên type từ trang đúng hơn
                            if hint == TYPE_CAO_DANG:
                                entry["type"] = TYPE_CAO_DANG
                                entry["type_label"] = TYPE_LABELS[TYPE_CAO_DANG]
                    directory[code] = entry

                added = len(directory) - count_before
                print(f"[DANH_BA] +{added} trường từ {src['label']} (tổng: {len(directory)})")
            except Exception as e:
                print(f"[CẢNH BÁO] Lỗi khi lấy danh bạ {src['label']}: {e}")

        print(f"[DANH_BA] Hoàn tất: {len(directory)} trường (ĐH/HV/CĐ).")
        return directory

    def fetch_school_profile(
        self,
        slug: str,
        code: str = "",
        contact_only: bool = False,
    ) -> Dict[str, str]:
        """
        Lấy hồ sơ giới thiệu trường từ trang đề án (#gioi-thieu):
        - contact_only=True: chỉ website + địa chỉ (+ hotline/fanpage nếu có)
        - đầy đủ: thêm thông tin chung, lĩnh vực/CTĐT, vị thế/thành tựu
        """
        empty = {k: "" for k in PROFILE_KEYS}
        if not slug:
            return empty

        url = f"{self.BASE_URL}/de-an-tuyen-sinh/{slug}.html"
        empty["gioi_thieu_url"] = url + "#gioi-thieu"
        try:
            res = requests.get(url, headers=self.HEADERS, timeout=18)
            if res.status_code != 200:
                return empty
            soup = BeautifulSoup(res.text, "html.parser")
            section = soup.find(id="gioi-thieu")
            if not section:
                # Fallback: trang gioi-thieu-truong-{slug}
                alt = f"{self.BASE_URL}/gioi-thieu-truong-{slug}.html"
                res2 = requests.get(alt, headers=self.HEADERS, timeout=15)
                if res2.status_code == 200:
                    soup = BeautifulSoup(res2.text, "html.parser")
                    section = soup.find(id="gioi-thieu") or soup
                    empty["gioi_thieu_url"] = alt
            if not section:
                return empty

            info_box = section.select_one(".basic-info__info") or section
            achieve_box = None if contact_only else section.select_one(".basic-info__achievement")

            info_lines: List[str] = []
            dia_chi_parts: List[str] = []
            website = hotline = fanpage = ""

            for li in info_box.find_all("li"):
                raw = clean_text(li.get_text(" ", strip=True))
                if not raw:
                    continue
                label, value = _split_label_value(raw)
                info_lines.append(raw)
                if not value:
                    continue
                if any(k in label for k in ("địa chỉ", "dia chi", "cơ sở", "co so", "campus")):
                    dia_chi_parts.append(value if ":" not in raw else value)
                elif "website" in label or "web site" in label:
                    website = value
                elif any(k in label for k in ("hotline", "điện thoại", "dien thoai", "tel", "phone")):
                    hotline = value
                elif any(k in label for k in ("fanpage", "facebook", "fb")):
                    fanpage = value

            # Hotline đôi khi nằm trong cùng dòng địa chỉ / mô tả
            if not hotline:
                blob = " ".join(info_lines)
                m = re.search(
                    r"(?:hotline|điện thoại|tel)\s*[:\-]?\s*([0-9.\s\-–+/]{8,})",
                    blob,
                    re.IGNORECASE,
                )
                if m:
                    hotline = clean_text(m.group(1))

            if contact_only:
                return {
                    "thong_tin_chung": "",
                    "dia_chi": "\n".join(dict.fromkeys(dia_chi_parts)),
                    "website": website,
                    "hotline": hotline,
                    "fanpage": fanpage,
                    "linh_vuc_chuong_trinh": "",
                    "vi_the_thanh_tuu": "",
                    "gioi_thieu_url": empty["gioi_thieu_url"],
                }

            training_parts: List[str] = []
            achieve_parts: List[str] = []
            if achieve_box:
                paras = []
                for tag in achieve_box.find_all(["p", "li"]):
                    t = clean_text(tag.get_text(" ", strip=True))
                    if t and len(t) > 15:
                        paras.append(t)
                if not paras:
                    t = clean_text(achieve_box.get_text("\n", strip=True))
                    paras = [p.strip() for p in t.split("\n") if len(p.strip()) > 15]

                for p in paras:
                    if _is_training_para(p) and not _is_achieve_para(p):
                        training_parts.append(p)
                    elif _is_achieve_para(p):
                        achieve_parts.append(p)
                    elif _is_training_para(p):
                        training_parts.append(p)
                    else:
                        # Đoạn lịch sử / giới thiệu chung → vị thế & thành tựu
                        achieve_parts.append(p)

            # Nếu không tách được, đổ toàn bộ achievement vào vị thế
            if achieve_box and not training_parts and not achieve_parts:
                achieve_parts.append(clean_text(achieve_box.get_text(" ", strip=True))[:4000])

            return {
                "thong_tin_chung": "\n".join(info_lines),
                "dia_chi": "\n".join(dict.fromkeys(dia_chi_parts)),
                "website": website,
                "hotline": hotline,
                "fanpage": fanpage,
                "linh_vuc_chuong_trinh": "\n\n".join(training_parts)[:5000],
                "vi_the_thanh_tuu": "\n\n".join(achieve_parts)[:5000],
                "gioi_thieu_url": empty["gioi_thieu_url"],
            }
        except Exception as e:
            print(f"[CẢNH BÁO] Không lấy được hồ sơ {code or slug}: {e}")
            return empty

    def enrich_profiles(
        self,
        schools: Optional[Dict[str, Dict[str, Any]]] = None,
        only_missing: bool = True,
        max_workers: int = 6,
        limit: int = 0,
        contact_only: bool = False,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Bổ sung hồ sơ / liên hệ cho danh bạ (song song, có cache từng trường).
        contact_only=True: chỉ điền website + địa chỉ (và hotline/fanpage nếu có).
        """
        target = schools if schools is not None else self.schools
        if not target:
            return target

        items = list(target.items())
        if only_missing:
            if contact_only:
                items = [(c, inf) for c, inf in items if not _has_contact(inf)]
            else:
                items = [
                    (c, inf) for c, inf in items
                    if not (_has_full_profile(inf) or _has_contact(inf))
                ]
        if limit and limit > 0:
            items = items[:limit]

        if not items:
            label = "Website/địa chỉ" if contact_only else "Hồ sơ giới thiệu"
            print(f"[DANH_BA] {label} đã có sẵn — bỏ qua bước bổ sung.")
            return target

        label = "website/địa chỉ" if contact_only else "hồ sơ trường"
        print(f"[DANH_BA] Đang lấy {label} cho {len(items)} trường…")
        done = 0

        def _job(code_info):
            code, info = code_info
            profile = self.fetch_school_profile(
                info.get("slug") or "", code=code, contact_only=contact_only
            )
            return code, profile

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_job, it) for it in items]
            for fut in as_completed(futures):
                code, profile = fut.result()
                if code in target:
                    if contact_only:
                        for key in CONTACT_KEYS:
                            val = profile.get(key) or ""
                            if val and not target[code].get(key):
                                target[code][key] = val
                    else:
                        target[code].update(profile)
                if code in self.schools:
                    if contact_only:
                        for key in CONTACT_KEYS:
                            val = profile.get(key) or ""
                            if val and not self.schools[code].get(key):
                                self.schools[code][key] = val
                    else:
                        self.schools[code].update(profile)
                done += 1
                if done % 25 == 0 or done == len(items):
                    print(f"[DANH_BA] {label.capitalize()}: {done}/{len(items)}")

        return target

    def _filter_types(
        self,
        data: Dict[str, Dict[str, Any]],
        include_dai_hoc: bool,
        include_cao_dang: bool,
    ) -> Dict[str, Dict[str, Any]]:
        result = {}
        for code, info in data.items():
            t = info.get("type", TYPE_DAI_HOC)
            if t == TYPE_CAO_DANG and not include_cao_dang:
                continue
            if t != TYPE_CAO_DANG and not include_dai_hoc:
                # hoc_vien + dai_hoc gộp nhóm đại học
                continue
            result[code] = info
        return result

    def _save_cache(self, data: Dict[str, Dict[str, Any]]):
        try:
            # Lưu toàn bộ (không filter) để lần sau dùng lại
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"[DANH_BA] Đã lưu cache: {self.cache_file}")
        except Exception as e:
            print(f"[CẢNH BÁO] Không lưu được cache danh bạ: {e}")

    def list_schools(
        self,
        school_type: Optional[str] = None,
        keyword: str = "",
    ) -> List[Dict[str, Any]]:
        """
        Trả về danh sách trường đã sắp xếp theo mã.
        school_type: None | 'dai_hoc' | 'cao_dang' | 'hoc_vien' | 'dai_hoc_hoc_vien'
        """
        if not self.schools:
            self.load()

        rows = []
        kw = keyword.strip().lower()
        for code, info in self.schools.items():
            t = info.get("type", TYPE_DAI_HOC)
            if school_type == "dai_hoc_hoc_vien" and t == TYPE_CAO_DANG:
                continue
            if school_type in (TYPE_DAI_HOC, TYPE_CAO_DANG, TYPE_HOC_VIEN) and t != school_type:
                continue
            if kw and kw not in code.lower() and kw not in (info.get("name") or "").lower():
                continue
            rows.append({
                "code": code,
                "name": info.get("name", ""),
                "slug": info.get("slug", ""),
                "type": t,
                "type_label": info.get("type_label") or TYPE_LABELS.get(t, t),
                "thong_tin_chung": info.get("thong_tin_chung", ""),
                "dia_chi": info.get("dia_chi", ""),
                "website": info.get("website", ""),
                "hotline": info.get("hotline", ""),
                "fanpage": info.get("fanpage", ""),
                "linh_vuc_chuong_trinh": info.get("linh_vuc_chuong_trinh", ""),
                "vi_the_thanh_tuu": info.get("vi_the_thanh_tuu", ""),
                "gioi_thieu_url": info.get("gioi_thieu_url", ""),
            })

        rows.sort(key=lambda x: (x["type_label"], x["code"]))
        return rows

    def get_codes(
        self,
        school_type: Optional[str] = None,
    ) -> List[str]:
        """Danh sách mã trường (dùng làm đầu vào crawler)."""
        return [r["code"] for r in self.list_schools(school_type=school_type)]

    def export_json(
        self,
        output_path: str = "data/output/danh_sach_ma_truong.json",
        school_type: Optional[str] = None,
        also_update_config: bool = True,
    ) -> str:
        """Xuất JSON danh sách trường (+ tùy chọn ghi config/schools_all.json)."""
        rows = self.list_schools(school_type=school_type)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        payload = {
            "description": "Danh sách mã trường Đại học / Cao đẳng / Học viện (tự động lấy từ tuyensinh247)",
            "total": len(rows),
            "schools": [
                {
                    "code": r["code"],
                    "name": r["name"],
                    "short_name": r["name"][:40],
                    "online_slug": r["slug"],
                    "type": r["type"],
                    "type_label": r["type_label"],
                    "thong_tin_chung": r.get("thong_tin_chung", ""),
                    "dia_chi": r.get("dia_chi", ""),
                    "website": r.get("website", ""),
                    "hotline": r.get("hotline", ""),
                    "fanpage": r.get("fanpage", ""),
                    "linh_vuc_chuong_trinh": r.get("linh_vuc_chuong_trinh", ""),
                    "vi_the_thanh_tuu": r.get("vi_the_thanh_tuu", ""),
                    "gioi_thieu_url": r.get("gioi_thieu_url", ""),
                }
                for r in rows
            ],
            "years": [2021, 2022, 2023, 2024, 2025, 2026],
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"[DANH_BA] Đã xuất JSON: {output_path} ({len(rows)} trường)")

        if also_update_config:
            config_path = "config/schools_all.json"
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(f"[DANH_BA] Đã cập nhật config: {config_path}")

        return output_path

    def export_excel(
        self,
        output_path: str = "data/output/danh_sach_ma_truong.xlsx",
        school_type: Optional[str] = None,
    ) -> str:
        """Xuất Excel danh sách mã trường (dễ lọc / chọn để thu thập)."""
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        rows = self.list_schools(school_type=school_type)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Danh_Sach_Ma_Truong"

        headers = [
            "STT",
            "Mã trường",
            "Tên trường",
            "Loại hình",
            "Slug (URL)",
            "Link điểm chuẩn",
            "Thông tin chung",
            "Địa chỉ các cơ sở",
            "Website",
            "Hotline",
            "Fanpage",
            "Lĩnh vực & Chương trình đào tạo",
            "Vị thế & Thành tựu nổi bật",
            "Link giới thiệu",
        ]
        header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
        header_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        data_font = Font(name="Arial", size=10)
        thin = Border(
            left=Side(style="thin", color="D3D3D3"),
            right=Side(style="thin", color="D3D3D3"),
            top=Side(style="thin", color="D3D3D3"),
            bottom=Side(style="thin", color="D3D3D3"),
        )
        type_fills = {
            TYPE_DAI_HOC: PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid"),
            TYPE_HOC_VIEN: PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid"),
            TYPE_CAO_DANG: PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid"),
        }

        for col, title in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col, value=title)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        ws.row_dimensions[1].height = 32

        for idx, r in enumerate(rows, start=1):
            link = f"{self.BASE_URL}/diem-chuan/{r['slug']}.html" if r.get("slug") else ""
            vals = [
                idx,
                r["code"],
                r["name"],
                r["type_label"],
                r["slug"],
                link,
                r.get("thong_tin_chung") or "",
                r.get("dia_chi") or "",
                r.get("website") or "",
                r.get("hotline") or "",
                r.get("fanpage") or "",
                r.get("linh_vuc_chuong_trinh") or "",
                r.get("vi_the_thanh_tuu") or "",
                r.get("gioi_thieu_url") or "",
            ]
            fill = type_fills.get(r["type"])
            for c, val in enumerate(vals, start=1):
                cell = ws.cell(row=idx + 1, column=c, value=val)
                cell.font = data_font
                cell.border = thin
                cell.alignment = Alignment(
                    horizontal="center" if c in (1, 2, 4) else "left",
                    vertical="top",
                    wrap_text=c >= 7,
                )
                if fill and c <= 4:
                    cell.fill = fill
            # Hàng cao hơn nếu có nội dung dài
            if any(vals[i] for i in (6, 11, 12)):
                ws.row_dimensions[idx + 1].height = 60

        # Sheet thống kê
        ws2 = wb.create_sheet("Thong_Ke")
        stats = {}
        for r in rows:
            stats[r["type_label"]] = stats.get(r["type_label"], 0) + 1
        ws2["A1"] = "Loại hình"
        ws2["B1"] = "Số lượng"
        ws2["A1"].font = header_font
        ws2["B1"].font = header_font
        ws2["A1"].fill = header_fill
        ws2["B1"].fill = header_fill
        row_i = 2
        for label, cnt in sorted(stats.items()):
            ws2.cell(row=row_i, column=1, value=label).font = data_font
            ws2.cell(row=row_i, column=2, value=cnt).font = data_font
            row_i += 1
        ws2.cell(row=row_i, column=1, value="TỔNG").font = Font(name="Arial", bold=True)
        ws2.cell(row=row_i, column=2, value=len(rows)).font = Font(name="Arial", bold=True)
        with_profile = sum(1 for r in rows if r.get("thong_tin_chung") or r.get("website"))
        ws2.cell(row=row_i + 2, column=1, value="Có hồ sơ giới thiệu").font = data_font
        ws2.cell(row=row_i + 2, column=2, value=with_profile).font = data_font
        ws2.column_dimensions["A"].width = 28
        ws2.column_dimensions["B"].width = 12

        ws.freeze_panes = "A2"
        last_col = get_column_letter(len(headers))
        ws.auto_filter.ref = f"A1:{last_col}{len(rows) + 1}"
        widths = [6, 12, 40, 12, 40, 45, 40, 28, 28, 16, 28, 45, 45, 40]
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

        wb.save(output_path)
        print(f"[DANH_BA] Đã xuất Excel: {output_path} ({len(rows)} trường)")
        return output_path

    def print_summary(self, school_type: Optional[str] = None, preview: int = 15):
        """In tóm tắt danh bạ ra console."""
        rows = self.list_schools(school_type=school_type)
        stats: Dict[str, int] = {}
        for r in rows:
            stats[r["type_label"]] = stats.get(r["type_label"], 0) + 1

        print("\n" + "=" * 70)
        print(" DANH BẠ MÃ TRƯỜNG ĐẠI HỌC / CAO ĐẲNG / HỌC VIỆN")
        print("=" * 70)
        for label, cnt in sorted(stats.items()):
            print(f"  - {label}: {cnt}")
        print(f"  → TỔNG: {len(rows)} trường")
        print("-" * 70)
        print(f"  Xem trước {min(preview, len(rows))} mã đầu:")
        for r in rows[:preview]:
            print(f"    {r['code']:<10} | {r['type_label']:<10} | {r['name'][:45]}")
        if len(rows) > preview:
            print(f"    ... và {len(rows) - preview} trường khác")
        print("=" * 70 + "\n")
