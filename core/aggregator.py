# -*- coding: utf-8 -*-
"""
Module: core.aggregator
Mô tả: Hợp nhất, khử trùng lặp và chuyển đổi ma trận dữ liệu tuyển sinh 2021-2025
cho phép ghép nối thông tin Chỉ tiêu, Số nguyện vọng và Điểm chuẩn theo từng ngành.
"""

import re
from typing import List, Dict, Any, Tuple, Optional
import pandas as pd
import numpy as np

from core.models import AdmissionRecord
from core.normalizer import normalize_school_code, get_school_display_name
from core.major_resolver import MajorCodeResolver
from crawlers.dean_extractor import method_to_calc_id

_SOURCE_URL_RE = re.compile(r"https?://[^\s;]+")


def source_urls(text: str) -> List[str]:
    """Tách các URL gốc trong chuỗi nguồn, giữ thứ tự và bỏ trùng."""
    found: List[str] = []
    for raw in _SOURCE_URL_RE.findall(text or ""):
        url = raw.rstrip(".,)")
        if url and url not in found:
            found.append(url)
    return found


# Thứ tự cột điểm theo phương thức trên sheet tổng hợp
METHOD_COLUMN_ORDER = [
    "THPT",
    "HOC_BA",
    "HSA",
    "V-ACT",
    "TSA",
    "KET_HOP",
    "CCQT",
    "DGNL",
    "XTTN",
    "XTTN_1.2",
    "XTTN_1.3",
    "SAT",
    "ACT",
    "IELTS",
]

METHOD_COLUMN_LABELS = {
    "THPT": "Điểm thi THPT",
    "HOC_BA": "Điểm học bạ",
    "HSA": "ĐGNL HSA",
    "V-ACT": "ĐGNL V-ACT",
    "TSA": "ĐGTD TSA",
    "KET_HOP": "Xét tuyển kết hợp",
    "CCQT": "Chứng chỉ quốc tế",
    "DGNL": "Đánh giá năng lực",
    "XTTN": "Xét tuyển tài năng",
    "XTTN_1.2": "XTTN Diện 1.2",
    "XTTN_1.3": "XTTN Diện 1.3",
    "SAT": "SAT",
    "ACT": "ACT",
    "IELTS": "IELTS",
    "OTHER": "Khác",
}


def method_column_key(phuong_thuc: str) -> str:
    """Chuẩn hóa tên PTXT → mã cột ổn định (THPT, TSA, …)."""
    mid = method_to_calc_id(phuong_thuc or "")
    return mid or "OTHER"


class AdmissionAggregator:
    """Bộ tổng hợp dữ liệu tuyển sinh đa nguồn qua các năm."""

    DEFAULT_YEARS = [2021, 2022, 2023, 2024, 2025, 2026]

    def __init__(self, years: List[int] = None):
        self.years = sorted(years or self.DEFAULT_YEARS)
        self.resolver = MajorCodeResolver()

    def merge_records(self, records: List[AdmissionRecord]) -> List[AdmissionRecord]:
        """
        Khử trùng lặp và ghép nối thông tin giữa các bản ghi cùng ngành & năm.
        (Ví dụ: Web crawler có Điểm chuẩn, còn PDF đề án có Chỉ tiêu và Số NV -> gộp thành 1 bản ghi đầy đủ).
        """
        merged_map: Dict[Tuple, AdmissionRecord] = {}

        for rec in records:
            norm_school = normalize_school_code(rec.ma_truong)
            norm_school_name = get_school_display_name(norm_school, rec.ten_truong)

            # Khóa định danh: (Mã trường, Tên ngành đã chuẩn hóa, Năm, Phương thức)
            key = (
                norm_school,
                rec.ten_nganh.strip().lower(),
                rec.nam,
                rec.phuong_thuc
            )

            if key not in merged_map:
                merged_map[key] = AdmissionRecord(
                    ma_truong=norm_school,
                    ten_truong=norm_school_name,
                    ma_nganh=rec.ma_nganh,
                    ten_nganh=rec.ten_nganh,
                    nam=rec.nam,
                    to_hop=rec.to_hop,
                    chi_tieu=rec.chi_tieu,
                    so_nguyen_vong=rec.so_nguyen_vong,
                    diem_chuan=rec.diem_chuan,
                    diem_chuan_ptxt=rec.diem_chuan_ptxt,
                    thang_diem=rec.thang_diem,
                    phuong_thuc=rec.phuong_thuc,
                    quy_che=rec.quy_che,
                    diem_quy_doi=rec.diem_quy_doi,
                    ghi_chu=rec.ghi_chu,
                    nguon=rec.nguon
                )
            else:
                existing = merged_map[key]
                if not existing.ma_nganh and rec.ma_nganh:
                    existing.ma_nganh = rec.ma_nganh
                if not existing.to_hop and rec.to_hop:
                    existing.to_hop = rec.to_hop
                if existing.chi_tieu is None and rec.chi_tieu is not None:
                    existing.chi_tieu = rec.chi_tieu
                if existing.so_nguyen_vong is None and rec.so_nguyen_vong is not None:
                    existing.so_nguyen_vong = rec.so_nguyen_vong
                if existing.diem_chuan is None and rec.diem_chuan is not None:
                    existing.diem_chuan = rec.diem_chuan
                if existing.diem_chuan_ptxt is None and rec.diem_chuan_ptxt is not None:
                    existing.diem_chuan_ptxt = rec.diem_chuan_ptxt
                if not existing.quy_che and rec.quy_che:
                    existing.quy_che = rec.quy_che
                if not existing.diem_quy_doi and rec.diem_quy_doi:
                    existing.diem_quy_doi = rec.diem_quy_doi
                if not existing.ghi_chu and rec.ghi_chu:
                    existing.ghi_chu = rec.ghi_chu
                if rec.nguon not in existing.nguon:
                    existing.nguon = f"{existing.nguon}; {rec.nguon}"

        for r in merged_map.values():
            if not r.ma_nganh:
                r.ma_nganh = self.resolver.resolve(
                    school_code=r.ma_truong,
                    major_name=r.ten_nganh
                )

        return list(merged_map.values())

    def _discover_methods(self, records: List[AdmissionRecord]) -> List[str]:
        """Danh sách mã PTXT có trong dữ liệu, theo thứ tự ưu tiên cố định."""
        present = set()
        for rec in records:
            mid = method_column_key(rec.phuong_thuc)
            if mid:
                present.add(mid)
        ordered = [m for m in METHOD_COLUMN_ORDER if m in present]
        extras = sorted(m for m in present if m not in METHOD_COLUMN_ORDER)
        return ordered + extras

    @staticmethod
    def _pick_score(rec: AdmissionRecord) -> Optional[float]:
        """Ưu tiên điểm chuẩn đã lưu; nếu thiếu thì lấy điểm thang PTXT."""
        if rec.diem_chuan is not None:
            return rec.diem_chuan
        return rec.diem_chuan_ptxt

    def create_pivot_summary(self, records: List[AdmissionRecord]) -> pd.DataFrame:
        """
        Bảng ngang: mỗi dòng = một ngành của một trường.
        Điểm chuẩn tách thành nhiều nhóm cột — mỗi phương thức xét tuyển một nhóm
        (vd: Điểm thi THPT / ĐGTD TSA / Xét kết hợp), mỗi nhóm có cột theo năm.
        """
        merged_records = self.merge_records(records)
        if not merged_records:
            return pd.DataFrame()

        methods = self._discover_methods(merged_records)
        grouped_data: Dict[Tuple, Dict[str, Any]] = {}

        for rec in merged_records:
            clean_ten_key = rec.ten_nganh.strip().lower()
            group_key = (rec.ma_truong, clean_ten_key)

            if group_key not in grouped_data:
                resolved_code = rec.ma_nganh or self.resolver.resolve(rec.ma_truong, rec.ten_nganh)
                init_row: Dict[str, Any] = {
                    "ma_truong": rec.ma_truong,
                    "ten_truong": rec.ten_truong,
                    "ma_nganh": resolved_code,
                    "ten_nganh": rec.ten_nganh,
                    "to_hop": rec.to_hop or "",
                    "phuong_thuc_co": set(),
                    "nguon_urls": [],
                }
                for yr in self.years:
                    init_row[f"chi_tieu_{yr}"] = None
                    init_row[f"so_nv_{yr}"] = None
                    for mid in methods:
                        init_row[f"diem_{mid}_{yr}"] = None
                grouped_data[group_key] = init_row

            row_dict = grouped_data[group_key]
            for url in source_urls(rec.nguon):
                if url not in row_dict["nguon_urls"]:
                    row_dict["nguon_urls"].append(url)
            mid = method_column_key(rec.phuong_thuc)
            row_dict["phuong_thuc_co"].add(METHOD_COLUMN_LABELS.get(mid, mid))

            if not row_dict["ma_nganh"] and rec.ma_nganh:
                row_dict["ma_nganh"] = rec.ma_nganh

            if rec.to_hop:
                if mid == "THPT" or not row_dict.get("to_hop"):
                    existing_th = row_dict.get("to_hop") or ""
                    if rec.to_hop not in existing_th:
                        row_dict["to_hop"] = (
                            f"{existing_th}; {rec.to_hop}".strip("; ")
                            if existing_th
                            else rec.to_hop
                        )

            if rec.nam not in self.years:
                continue

            yr = rec.nam
            if rec.chi_tieu is not None:
                if row_dict[f"chi_tieu_{yr}"] is None or mid == "THPT":
                    row_dict[f"chi_tieu_{yr}"] = rec.chi_tieu
            if rec.so_nguyen_vong is not None:
                if row_dict[f"so_nv_{yr}"] is None or mid == "THPT":
                    row_dict[f"so_nv_{yr}"] = rec.so_nguyen_vong

            score = self._pick_score(rec)
            if score is not None and mid:
                col = f"diem_{mid}_{yr}"
                prev = row_dict.get(col)
                if prev is None or score > prev:
                    row_dict[col] = score

        rows_list = []
        for row in grouped_data.values():
            methods_set = row.pop("phuong_thuc_co", set()) or set()

            def _method_sort_key(label: str) -> int:
                key = method_column_key(label)
                return METHOD_COLUMN_ORDER.index(key) if key in METHOD_COLUMN_ORDER else 99

            row["phuong_thuc"] = "; ".join(sorted(methods_set, key=_method_sort_key)) if methods_set else ""
            row["nguon"] = "\n".join(row.pop("nguon_urls", []) or [])

            score_series_key = "THPT" if "THPT" in methods else (methods[0] if methods else None)
            scores = []
            valid_years = []
            if score_series_key:
                for yr in self.years:
                    val = row.get(f"diem_{score_series_key}_{yr}")
                    if val is not None:
                        scores.append(val)
                        valid_years.append(yr)
            row["diem_tb"] = round(float(np.mean(scores)), 2) if scores else None
            if score_series_key and len(valid_years) >= 2:
                latest_yr, prev_yr = valid_years[-1], valid_years[-2]
                diff = round(
                    row[f"diem_{score_series_key}_{latest_yr}"]
                    - row[f"diem_{score_series_key}_{prev_yr}"],
                    2,
                )
                row["bien_dong_diem"] = f"{'+' if diff > 0 else ''}{diff} ({prev_yr}->{latest_yr})"
            else:
                row["bien_dong_diem"] = "-"

            rows_list.append(row)

        df = pd.DataFrame(rows_list)
        if not df.empty:
            df = df.sort_values(by=["ma_truong", "ten_nganh"]).reset_index(drop=True)
        df.attrs["method_keys"] = methods
        df.attrs["method_labels"] = {m: METHOD_COLUMN_LABELS.get(m, m) for m in methods}
        return df

    def create_flat_dataframe(self, records: List[AdmissionRecord]) -> pd.DataFrame:
        """
        Tạo DataFrame phẳng (mỗi dòng = 1 ngành × 1 năm × 1 PTXT),
        thích hợp để xuất ra làm nguồn dữ liệu phân tích (Pivot Table, PowerBI).
        """
        merged_records = self.merge_records(records)
        rows = []
        for r in merged_records:
            d = r.to_dict()
            d["ty_le_choi"] = r.ty_le_choi
            rows.append(d)

        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values(
                by=["ma_truong", "nam", "ten_nganh", "phuong_thuc"]
            ).reset_index(drop=True)
        return df


def build_grouped_score_view(
    admissions: List[Dict[str, Any]],
    years: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    Bảng xem lại: nhóm theo trường → ngành, điểm theo từng phương thức × năm.
    """
    year_list = list(years) if years else list(AdmissionAggregator.DEFAULT_YEARS)
    present_years = sorted({
        int(r["nam"]) for r in admissions
        if r.get("nam") is not None
    })
    if present_years:
        year_list = [y for y in year_list if y in present_years] or present_years

    methods_present: set = set()
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    school_names: Dict[str, str] = {}

    for rec in admissions:
        code = normalize_school_code(rec.get("ma_truong") or "")
        if not code:
            continue
        ten_truong = (rec.get("ten_truong") or "").strip() or get_school_display_name(code)
        school_names[code] = ten_truong or school_names.get(code) or code

        ten_nganh = (rec.get("ten_nganh") or "").strip() or "—"
        key = (code, ten_nganh.lower())
        mid = method_column_key(rec.get("phuong_thuc") or "") or "OTHER"
        methods_present.add(mid)

        if key not in grouped:
            grouped[key] = {
                "ma_truong": code,
                "ten_truong": ten_truong,
                "ma_nganh": (rec.get("ma_nganh") or "").strip(),
                "ten_nganh": ten_nganh,
                "to_hop": (rec.get("to_hop") or "").strip(),
                "scores": {},
                "nguon_urls": [],
            }
        row = grouped[key]
        for url in source_urls(rec.get("nguon") or ""):
            if url not in row["nguon_urls"]:
                row["nguon_urls"].append(url)
        if not row["ma_nganh"] and rec.get("ma_nganh"):
            row["ma_nganh"] = str(rec.get("ma_nganh")).strip()
        to_hop = (rec.get("to_hop") or "").strip()
        if to_hop:
            if mid == "THPT" or not row["to_hop"]:
                if to_hop not in (row["to_hop"] or ""):
                    row["to_hop"] = (
                        f"{row['to_hop']}; {to_hop}".strip("; ")
                        if row["to_hop"]
                        else to_hop
                    )

        try:
            yr = int(rec.get("nam"))
        except (TypeError, ValueError):
            continue
        if yr not in year_list:
            continue

        score = None
        for sk in ("diem_chuan_ptxt", "diem_chuan"):
            v = rec.get(sk)
            if v is None or v == "":
                continue
            try:
                score = float(v)
                break
            except (TypeError, ValueError):
                continue
        if score is None:
            continue

        by_year = row["scores"].setdefault(mid, {})
        prev = by_year.get(yr)
        if prev is None or score > prev:
            by_year[yr] = round(score, 2)

    methods = sorted(
        methods_present,
        key=lambda m: (METHOD_COLUMN_ORDER.index(m) if m in METHOD_COLUMN_ORDER else 99, m),
    )
    methods = [m for m in methods if any(
        (row["scores"].get(m) or {}) for row in grouped.values()
    )]

    schools_map: Dict[str, Dict[str, Any]] = {}
    for row in grouped.values():
        code = row["ma_truong"]
        if code not in schools_map:
            schools_map[code] = {
                "ma_truong": code,
                "ten_truong": school_names.get(code) or row["ten_truong"] or code,
                "majors": [],
                "nguon_urls": [],
            }
        scores_out: Dict[str, Dict[str, Any]] = {}
        for mid in methods:
            ymap = row["scores"].get(mid) or {}
            scores_out[mid] = {str(y): ymap.get(y) for y in year_list if y in ymap}
        for url in row.get("nguon_urls") or []:
            if url not in schools_map[code]["nguon_urls"]:
                schools_map[code]["nguon_urls"].append(url)
        schools_map[code]["majors"].append({
            "ma_nganh": row["ma_nganh"],
            "ten_nganh": row["ten_nganh"],
            "to_hop": row["to_hop"],
            "scores": scores_out,
            "nguon_urls": row.get("nguon_urls") or [],
        })

    schools = []
    for code in sorted(schools_map.keys()):
        block = schools_map[code]
        block["majors"].sort(key=lambda m: (m.get("ten_nganh") or "").lower())
        for i, maj in enumerate(block["majors"], start=1):
            maj["stt"] = i
        block["major_count"] = len(block["majors"])
        schools.append(block)

    return {
        "ok": True,
        "years": year_list,
        "methods": [
            {"id": m, "label": METHOD_COLUMN_LABELS.get(m, m)} for m in methods
        ],
        "schools": schools,
        "school_count": len(schools),
        "major_count": sum(s["major_count"] for s in schools),
    }
