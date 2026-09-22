# -*- coding: utf-8 -*-
"""
Module: crawlers.school_directory
Mô tả: Lấy và xuất danh sách mã trường Đại học / Cao đẳng / Học viện
từ cổng tuyensinh247, phục vụ làm đầu vào cho crawler điểm chuẩn & đề án.
"""

import os
import re
import json
from typing import Dict, List, Optional, Any

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

    def load(
        self,
        force_refresh: bool = False,
        include_dai_hoc: bool = True,
        include_cao_dang: bool = True,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Nạp danh bạ từ cache hoặc cào mới từ web.
        """
        if not force_refresh and os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data and isinstance(data, dict):
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
                        return self.schools
            except Exception:
                pass

        fetched = self.fetch_from_web(
            include_dai_hoc=include_dai_hoc,
            include_cao_dang=include_cao_dang,
        )
        self.schools = fetched
        self._save_cache(fetched)
        return self.schools

    def fetch_from_web(
        self,
        include_dai_hoc: bool = True,
        include_cao_dang: bool = True,
    ) -> Dict[str, Dict[str, Any]]:
        """Cào danh sách trường từ các trang điểm chuẩn."""
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
        """Xuất Excel danh sách mã trường (dễ lọc / chọn để cào)."""
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        rows = self.list_schools(school_type=school_type)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Danh_Sach_Ma_Truong"

        headers = ["STT", "Mã trường", "Tên trường", "Loại hình", "Slug (URL)", "Link điểm chuẩn"]
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
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for idx, r in enumerate(rows, start=1):
            link = f"{self.BASE_URL}/diem-chuan/{r['slug']}.html" if r.get("slug") else ""
            vals = [idx, r["code"], r["name"], r["type_label"], r["slug"], link]
            fill = type_fills.get(r["type"])
            for c, val in enumerate(vals, start=1):
                cell = ws.cell(row=idx + 1, column=c, value=val)
                cell.font = data_font
                cell.border = thin
                if fill:
                    cell.fill = fill
                if c in (1, 2, 4):
                    cell.alignment = Alignment(horizontal="center", vertical="center")

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
        ws2.column_dimensions["A"].width = 20
        ws2.column_dimensions["B"].width = 12

        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:F{len(rows) + 1}"
        widths = [8, 14, 55, 14, 55, 70]
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
