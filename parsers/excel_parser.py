# -*- coding: utf-8 -*-
"""
Module: parsers.excel_parser
Mô tả: Bóc tách dữ liệu tuyển sinh từ các tệp Excel (.xlsx, .xls).
Hỗ trợ tự động dò hàng tiêu đề, đọc nhiều sheet, và tự tách nếu một sheet có nhiều năm.
"""

import os
import re
from typing import List, Optional
import pandas as pd

from core.models import AdmissionRecord
from core.normalizer import (
    clean_text,
    normalize_school_code,
    normalize_major_code,
    normalize_score,
    normalize_integer,
    extract_year_from_text,
)
from parsers.base_parser import BaseParser
from crawlers.dean_extractor import detect_admission_method


class ExcelAdmissionParser(BaseParser):
    """Parser xử lý các file Excel chứa dữ liệu tuyển sinh."""

    def parse(
        self,
        file_path: str,
        default_school_code: str = "",
        default_school_name: str = "",
        default_year: Optional[int] = None
    ) -> List[AdmissionRecord]:
        """
        Đọc và phân tích file Excel.
        """
        records: List[AdmissionRecord] = []
        filename = os.path.basename(file_path)

        # Suy luận năm từ tên file nếu chưa có
        inferred_year = default_year or extract_year_from_text(filename) or 2024
        # Suy luận mã trường từ tên file (ví dụ: 'BKA_TuyenSinh_2023.xlsx' -> 'BKA')
        inferred_school_code = default_school_code
        if not inferred_school_code:
            match_code = re.search(r"([A-Z]{3,4})", filename)
            if match_code:
                inferred_school_code = match_code.group(1)

        try:
            excel_file = pd.ExcelFile(file_path)
        except Exception as e:
            print(f"[CẢNH BÁO] Không thể mở file Excel {file_path}: {e}")
            return records

        for sheet_name in excel_file.sheet_names:
            sheet_year = extract_year_from_text(sheet_name) or inferred_year
            try:
                # Đọc tối đa 20 hàng đầu để tìm vị trí hàng tiêu đề (header)
                df_preview = pd.read_excel(excel_file, sheet_name=sheet_name, header=None, nrows=20)
            except Exception:
                continue

            header_row_idx = self._detect_header_row(df_preview)
            if header_row_idx is None:
                continue

            # Đọc lại DataFrame với header thực tế
            df = pd.read_excel(excel_file, sheet_name=sheet_name, header=header_row_idx)
            df = df.dropna(how="all")  # Bỏ các dòng hoàn toàn trống

            # Phân tích các cột
            sheet_records = self._parse_dataframe(
                df=df,
                school_code=inferred_school_code,
                school_name=default_school_name or inferred_school_code,
                base_year=sheet_year,
                source_name=filename
            )
            records.extend(sheet_records)

        return records

    def _detect_header_row(self, df: pd.DataFrame) -> Optional[int]:
        """Dò tìm chỉ số dòng chứa tiêu đề bảng."""
        for r_idx in range(len(df)):
            row_values = [str(val) for val in df.iloc[r_idx].values if pd.notna(val)]
            joined_text = " ".join(row_values).lower()
            # Nếu dòng có chứa cả từ khóa mã ngành / tên ngành hoặc điểm chuẩn
            if ("mã ngành" in joined_text or "mã xét tuyển" in joined_text or "tên ngành" in joined_text) and \
               ("điểm" in joined_text or "chỉ tiêu" in joined_text or "tổ hợp" in joined_text or "ngành" in joined_text):
                return r_idx
        return 0  # Mặc định dòng đầu tiên nếu không tìm thấy dòng rõ ràng

    def _parse_dataframe(
        self,
        df: pd.DataFrame,
        school_code: str,
        school_name: str,
        base_year: int,
        source_name: str
    ) -> List[AdmissionRecord]:
        records: List[AdmissionRecord] = []
        columns = [str(c).strip() for c in df.columns]

        # Kiểm tra xem có cột riêng theo năm không (VD: 'Điểm 2021', 'Điểm 2022', ...)
        multi_year_score_cols = {}
        multi_year_quota_cols = {}
        multi_year_nv_cols = {}

        single_col_map = {}

        for col_name in columns:
            col_lower = col_name.lower()
            year_in_col = extract_year_from_text(col_name)

            if year_in_col and any(k in col_lower for k in ["điểm", "diem"]):
                multi_year_score_cols[year_in_col] = col_name
            elif year_in_col and any(k in col_lower for k in ["chỉ tiêu", "chi tieu", "ctts"]):
                multi_year_quota_cols[year_in_col] = col_name
            elif year_in_col and any(k in col_lower for k in ["nguyện vọng", "hồ sơ", "đăng ký"]):
                multi_year_nv_cols[year_in_col] = col_name
            else:
                matched_field = self.match_column_name(col_name)
                if matched_field and matched_field not in single_col_map:
                    single_col_map[matched_field] = col_name

        # Nếu phát hiện cột đa năm (wide table)
        detected_years = set(multi_year_score_cols.keys()) | set(multi_year_quota_cols.keys()) | set(multi_year_nv_cols.keys())
        if not detected_years:
            detected_years = {base_year}

        col_ma = single_col_map.get("ma_nganh")
        col_ten = single_col_map.get("ten_nganh")
        col_tohop = single_col_map.get("to_hop")
        col_ghichu = single_col_map.get("ghi_chu")
        col_ptxt = single_col_map.get("phuong_thuc")
        col_quydoi = single_col_map.get("diem_quy_doi")
        col_quyche = single_col_map.get("quy_che")

        # Suy luận phương thức từ tên sheet / file nếu có
        default_method = detect_admission_method(source_name) or "Điểm thi THPT"

        for _, row in df.iterrows():
            raw_ma = row.get(col_ma, "") if col_ma else ""
            raw_ten = row.get(col_ten, "") if col_ten else ""

            if pd.isna(raw_ma) and pd.isna(raw_ten):
                continue

            ma_nganh = normalize_major_code(raw_ma)
            ten_nganh = clean_text(raw_ten)
            to_hop = clean_text(row.get(col_tohop, "")) if col_tohop else ""
            ghi_chu = clean_text(row.get(col_ghichu, "")) if col_ghichu else ""
            raw_pt = clean_text(row.get(col_ptxt, "")) if col_ptxt else ""
            phuong_thuc = detect_admission_method(raw_pt) or raw_pt or default_method
            diem_quy_doi = clean_text(row.get(col_quydoi, "")) if col_quydoi else ""
            quy_che = clean_text(row.get(col_quyche, "")) if col_quyche else ""

            # Bỏ qua các dòng tiêu đề phụ lặp lại
            if not ten_nganh and not ma_nganh:
                continue
            if "tên ngành" in ten_nganh.lower() or "mã ngành" in ma_nganh.lower():
                continue

            # Duyệt qua từng năm để tạo bản ghi tương ứng
            for yr in sorted(detected_years):
                if multi_year_score_cols:
                    col_s = multi_year_score_cols.get(yr)
                    diem = normalize_score(row.get(col_s)) if col_s else None
                else:
                    col_s = single_col_map.get("diem_chuan")
                    diem = normalize_score(row.get(col_s)) if col_s else None

                if multi_year_quota_cols:
                    col_q = multi_year_quota_cols.get(yr)
                    chitieu = normalize_integer(row.get(col_q)) if col_q else None
                else:
                    col_q = single_col_map.get("chi_tieu")
                    chitieu = normalize_integer(row.get(col_q)) if col_q else None

                if multi_year_nv_cols:
                    col_nv = multi_year_nv_cols.get(yr)
                    so_nv = normalize_integer(row.get(col_nv)) if col_nv else None
                else:
                    col_nv = single_col_map.get("so_nguyen_vong")
                    so_nv = normalize_integer(row.get(col_nv)) if col_nv else None

                # Chỉ lưu bản ghi nếu có ít nhất 1 thông tin (điểm chuẩn, chỉ tiêu, hoặc tên ngành hợp lệ)
                if diem is not None or chitieu is not None or so_nv is not None or (ma_nganh and ten_nganh):
                    rec = AdmissionRecord(
                        ma_truong=school_code,
                        ten_truong=school_name,
                        ma_nganh=ma_nganh,
                        ten_nganh=ten_nganh,
                        nam=yr,
                        to_hop=to_hop,
                        chi_tieu=chitieu,
                        so_nguyen_vong=so_nv,
                        diem_chuan=diem,
                        phuong_thuc=phuong_thuc,
                        quy_che=quy_che,
                        diem_quy_doi=diem_quy_doi,
                        ghi_chu=ghi_chu,
                        nguon=f"Excel: {source_name}"
                    )
                    records.append(rec)

        return records
