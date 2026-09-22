# -*- coding: utf-8 -*-
"""
Module: parsers.docx_parser
Mô tả: Bóc tách bảng dữ liệu tuyển sinh từ tệp văn bản Word (.docx)
sử dụng thư viện python-docx.
"""

import os
import re
from typing import List, Optional
import docx

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


class DocxAdmissionParser(BaseParser):
    """Parser xử lý bóc tách bảng biểu tuyển sinh từ file Word (.docx)."""

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
            doc = docx.Document(file_path)
            for table in doc.tables:
                if len(table.rows) < 2:
                    continue

                # Đọc cấu trúc bảng thành danh sách chuỗi
                table_matrix: List[List[str]] = []
                for row in table.rows:
                    row_data = [clean_text(cell.text) for cell in row.cells]
                    table_matrix.append(row_data)

                # Dò tìm dòng header
                header_row_idx = None
                col_map = None
                for r_idx in range(min(4, len(table_matrix))):
                    pot_map = self.map_table_headers(table_matrix[r_idx])
                    if "ma_nganh" in pot_map.values() or "ten_nganh" in pot_map.values():
                        header_row_idx = r_idx
                        col_map = pot_map
                        break

                if col_map is None:
                    continue

                # Bóc tách các dòng dữ liệu sau header
                for row_data in table_matrix[header_row_idx + 1:]:
                    extracted = {}
                    for c_idx, field_name in col_map.items():
                        if c_idx < len(row_data):
                            extracted[field_name] = row_data[c_idx]

                    raw_ma = extracted.get("ma_nganh", "")
                    raw_ten = extracted.get("ten_nganh", "")
                    ma_nganh = normalize_major_code(raw_ma)
                    ten_nganh = clean_text(raw_ten)

                    if not ma_nganh and not ten_nganh:
                        continue
                    if "tên ngành" in ten_nganh.lower() or "mã ngành" in ma_nganh.lower():
                        continue

                    diem = normalize_score(extracted.get("diem_chuan"))
                    chitieu = normalize_integer(extracted.get("chi_tieu"))
                    so_nv = normalize_integer(extracted.get("so_nguyen_vong"))
                    to_hop = clean_text(extracted.get("to_hop", ""))
                    ghi_chu = clean_text(extracted.get("ghi_chu", ""))
                    raw_pt = clean_text(extracted.get("phuong_thuc", ""))
                    phuong_thuc = (
                        detect_admission_method(raw_pt)
                        or detect_admission_method(filename)
                        or raw_pt
                        or "Điểm thi THPT"
                    )
                    diem_quy_doi = clean_text(extracted.get("diem_quy_doi", ""))
                    quy_che = clean_text(extracted.get("quy_che", ""))

                    rec = AdmissionRecord(
                        ma_truong=inferred_school_code,
                        ten_truong=default_school_name or inferred_school_code,
                        ma_nganh=ma_nganh,
                        ten_nganh=ten_nganh,
                        nam=inferred_year,
                        to_hop=to_hop,
                        chi_tieu=chitieu,
                        so_nguyen_vong=so_nv,
                        diem_chuan=diem,
                        phuong_thuc=phuong_thuc,
                        quy_che=quy_che,
                        diem_quy_doi=diem_quy_doi,
                        ghi_chu=ghi_chu,
                        nguon=f"Word: {filename}"
                    )
                    records.append(rec)

        except Exception as e:
            print(f"[CẢNH BÁO] Không thể đọc file Word {file_path}: {e}")

        return records
