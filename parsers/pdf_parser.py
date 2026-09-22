# -*- coding: utf-8 -*-
"""
Module: parsers.pdf_parser
Mô tả: Bóc tách bảng dữ liệu tuyển sinh từ tệp PDF (như Đề án tuyển sinh, Thông báo điểm chuẩn)
sử dụng thư viện pdfplumber với thuật toán làm sạch ô và ghép bảng qua nhiều trang.
"""

import os
import re
from typing import List, Optional
import pdfplumber

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


class PdfAdmissionParser(BaseParser):
    """Parser xử lý bóc tách bảng biểu tuyển sinh từ file PDF."""

    def parse(
        self,
        file_path: str,
        default_school_code: str = "",
        default_school_name: str = "",
        default_year: Optional[int] = None
    ) -> List[AdmissionRecord]:
        records: List[AdmissionRecord] = []
        filename = os.path.basename(file_path)

        inferred_year = default_year or extract_year_from_text(filename) or 2024
        inferred_school_code = default_school_code
        if not inferred_school_code:
            match_code = re.search(r"([A-Z]{3,4})", filename)
            if match_code:
                inferred_school_code = match_code.group(1)

        try:
            with pdfplumber.open(file_path) as pdf:
                all_extracted_rows: List[List[str]] = []
                current_col_map = None

                for page_idx, page in enumerate(pdf.pages):
                    tables = page.extract_tables()
                    if not tables:
                        continue

                    for table in tables:
                        if not table or len(table) < 2:
                            continue

                        # Làm sạch các ô trong bảng (thay thế xuống dòng \n bằng khoảng trắng)
                        cleaned_table = []
                        for row in table:
                            cleaned_row = [clean_text(cell) if cell else "" for cell in row]
                            cleaned_table.append(cleaned_row)

                        # Dò tìm dòng tiêu đề trong 3 hàng đầu của bảng
                        header_found_idx = None
                        col_map = None
                        for r_i in range(min(3, len(cleaned_table))):
                            potential_map = self.map_table_headers(cleaned_table[r_i])
                            # Cần ít nhất nhận diện được cột mã ngành hoặc tên ngành
                            if "ma_nganh" in potential_map.values() or "ten_nganh" in potential_map.values():
                                header_found_idx = r_i
                                col_map = potential_map
                                current_col_map = col_map
                                break

                        # Nếu không tìm thấy header mới nhưng các trang trước đã có header và số cột khớp
                        if col_map is None and current_col_map is not None:
                            # Giả định đây là bảng nối dài từ trang trước
                            data_rows = cleaned_table
                            active_map = current_col_map
                        elif header_found_idx is not None:
                            data_rows = cleaned_table[header_found_idx + 1:]
                            active_map = col_map
                        else:
                            continue

                        # Bóc tách từng dòng dữ liệu
                        for row in data_rows:
                            rec = self._row_to_record(
                                row=row,
                                col_map=active_map,
                                school_code=inferred_school_code,
                                school_name=default_school_name or inferred_school_code,
                                year=inferred_year,
                                source_name=filename
                            )
                            if rec:
                                records.append(rec)

        except Exception as e:
            print(f"[CẢNH BÁO] Không thể đọc file PDF {file_path}: {e}")

        return records

    def _row_to_record(
        self,
        row: List[str],
        col_map: dict,
        school_code: str,
        school_name: str,
        year: int,
        source_name: str
    ) -> Optional[AdmissionRecord]:
        """Chuyển đổi một dòng dữ liệu từ bảng PDF thành AdmissionRecord."""
        extracted = {}
        for col_idx, field_name in col_map.items():
            if col_idx < len(row):
                extracted[field_name] = row[col_idx]

        raw_ma = extracted.get("ma_nganh", "")
        raw_ten = extracted.get("ten_nganh", "")

        ma_nganh = normalize_major_code(raw_ma)
        ten_nganh = clean_text(raw_ten)

        # Bỏ qua nếu không có cả mã và tên ngành
        if not ma_nganh and not ten_nganh:
            return None
        # Bỏ qua nếu dòng là tiêu đề lặp lại
        if "tên ngành" in ten_nganh.lower() or "mã ngành" in ma_nganh.lower():
            return None

        diem = normalize_score(extracted.get("diem_chuan"))
        chitieu = normalize_integer(extracted.get("chi_tieu"))
        so_nv = normalize_integer(extracted.get("so_nguyen_vong"))
        to_hop = clean_text(extracted.get("to_hop", ""))
        ghi_chu = clean_text(extracted.get("ghi_chu", ""))
        raw_pt = clean_text(extracted.get("phuong_thuc", ""))
        phuong_thuc = (
            detect_admission_method(raw_pt)
            or detect_admission_method(source_name)
            or raw_pt
            or "Điểm thi THPT"
        )
        diem_quy_doi = clean_text(extracted.get("diem_quy_doi", ""))
        quy_che = clean_text(extracted.get("quy_che", ""))

        return AdmissionRecord(
            ma_truong=school_code,
            ten_truong=school_name,
            ma_nganh=ma_nganh,
            ten_nganh=ten_nganh,
            nam=year,
            to_hop=to_hop,
            chi_tieu=chitieu,
            so_nguyen_vong=so_nv,
            diem_chuan=diem,
            phuong_thuc=phuong_thuc,
            quy_che=quy_che,
            diem_quy_doi=diem_quy_doi,
            ghi_chu=ghi_chu,
            nguon=f"PDF: {source_name}"
        )
