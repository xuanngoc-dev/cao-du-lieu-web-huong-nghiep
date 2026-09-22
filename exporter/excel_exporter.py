# -*- coding: utf-8 -*-
"""
Module: exporter.excel_exporter
Mô tả: Xuất báo cáo tổng hợp tuyển sinh 2021-2025 ra file Excel với định dạng chuyên nghiệp:
- Tiêu đề đa tầng (Multi-level Header) phân nhóm màu sắc rõ ràng.
- Tự động căn chỉnh độ rộng cột (Auto-fit Column Width).
- Định dạng số (nguyên, thập phân 2 chữ số).
- Cố định dòng tiêu đề và bật bộ lọc tự động (Freeze Panes & AutoFilter).
"""

import os
from typing import List
import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from core.models import AdmissionRecord, ScoreConversionRecord, AdmissionRegulation
from core.aggregator import AdmissionAggregator


class ExcelAdmissionExporter:
    """Xuất file Excel tổng hợp tuyển sinh với định dạng cao cấp bằng openpyxl."""

    # Bảng màu phong cách hiện đại cho các nhóm cột
    COLOR_GROUP_INFO = "1F4E79"       # Xanh Navy đậm (Thông tin chung)
    COLOR_GROUP_QUOTA = "2E75B6"      # Xanh lam (Chỉ tiêu)
    COLOR_GROUP_NV = "7030A0"         # Tím đậm (Số lượng nguyện vọng)
    COLOR_GROUP_SCORE = "375623"      # Xanh lục sẫm (Điểm chuẩn)
    COLOR_GROUP_TREND = "C65911"      # Cam đất (Phân tích xu hướng)
    COLOR_GROUP_CONV = "833C0C"       # Nâu (Quy đổi)
    COLOR_GROUP_REG = "385723"        # Xanh đậm (Quy chế)

    def __init__(self, years: List[int] = None):
        self.years = sorted(years or [2021, 2022, 2023, 2024, 2025, 2026])

    def export(
        self,
        records: List[AdmissionRecord],
        output_path: str = "data/output/tong_hop_tuyen_sinh.xlsx",
        conversions: List[ScoreConversionRecord] = None,
        regulations: List[AdmissionRegulation] = None,
    ) -> str:
        """
        Xuất toàn bộ bản ghi ra file Excel hoàn chỉnh gồm các Sheet:
        1. Tong_Hop_YYYY_YYYY (Bảng ngang pivot đa năm)
        2. Du_Lieu_Chi_Tiet (Bảng phẳng cơ sở dữ liệu)
        3. Diem_Quy_Doi (Bảng quy đổi / hoán đổi chứng chỉ)
        4. Quy_Che (Quy chế / quy định xét tuyển)
        5. Huong_Dan_Giai_Thich (Giải thích cột và nguồn dữ liệu)
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        aggregator = AdmissionAggregator(self.years)
        conversions = conversions or []
        regulations = regulations or []

        df_summary = aggregator.create_pivot_summary(records)
        df_flat = aggregator.create_flat_dataframe(records)

        wb = openpyxl.Workbook()
        # Sheet 1: Bảng tổng hợp Pivot
        ws_summary = wb.active
        yr_min = min(self.years) if self.years else 2021
        yr_max = max(self.years) if self.years else 2026
        ws_summary.title = f"Tong_Hop_{yr_min}_{yr_max}"
        self._build_summary_sheet(ws_summary, df_summary)

        # Sheet 2: Dữ liệu chi tiết
        ws_flat = wb.create_sheet(title="Du_Lieu_Chi_Tiet")
        self._build_flat_sheet(ws_flat, df_flat)

        # Sheet 3: Điểm quy đổi
        ws_conv = wb.create_sheet(title="Diem_Quy_Doi")
        self._build_conversion_sheet(ws_conv, conversions)

        # Sheet 4: Quy chế
        ws_reg = wb.create_sheet(title="Quy_Che")
        self._build_regulation_sheet(ws_reg, regulations)

        # Sheet 5: Hướng dẫn giải thích
        ws_guide = wb.create_sheet(title="Huong_Dan_Giai_Thich")
        self._build_guide_sheet(
            ws_guide,
            total_records=len(records),
            total_conversions=len(conversions),
            total_regulations=len(regulations),
        )

        wb.save(output_path)
        print(f"\n[EXPORTER] Đã xuất báo cáo Excel thành công: {output_path}")
        return output_path

    # Màu nhóm điểm theo từng phương thức (xoay vòng)
    COLOR_METHOD_SCORES = [
        "375623",  # THPT — xanh lục
        "2E75B6",  # học bạ — xanh lam
        "548235",  # HSA
        "5B9BD5",  # V-ACT
        "70AD47",  # TSA
        "833C0C",  # kết hợp
        "C65911",  # CCQT
        "7030A0",  # khác
    ]

    def _build_summary_sheet(self, ws: openpyxl.worksheet.worksheet.Worksheet, df: pd.DataFrame):
        """
        Bảng tổng hợp: 1 dòng = 1 ngành.
        Điểm chuẩn = nhiều nhóm cột, mỗi phương thức xét tuyển một nhóm (theo năm).
        """
        from core.aggregator import METHOD_COLUMN_LABELS

        ws.views.sheetView[0].showGridLines = True

        thin_border = Border(
            left=Side(style="thin", color="D3D3D3"),
            right=Side(style="thin", color="D3D3D3"),
            top=Side(style="thin", color="D3D3D3"),
            bottom=Side(style="thin", color="D3D3D3"),
        )
        header_border = Border(
            left=Side(style="thin", color="FFFFFF"),
            right=Side(style="thin", color="FFFFFF"),
            top=Side(style="thin", color="FFFFFF"),
            bottom=Side(style="medium", color="FFFFFF"),
        )

        font_header_grp = Font(name="Arial", size=11, bold=True, color="FFFFFF")
        font_header_sub = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        font_data = Font(name="Arial", size=10)

        method_keys = list(df.attrs.get("method_keys") or [])
        method_labels = dict(df.attrs.get("method_labels") or {})
        if not method_keys and not df.empty:
            # Suy ra từ tên cột nếu attrs bị mất
            for col in df.columns:
                if isinstance(col, str) and col.startswith("diem_") and col.count("_") >= 2:
                    # diem_THPT_2026 / diem_V-ACT_2026
                    parts = col.split("_")
                    # year is last
                    mid = "_".join(parts[1:-1])
                    if mid and mid not in method_keys:
                        method_keys.append(mid)
                        method_labels[mid] = METHOD_COLUMN_LABELS.get(mid, mid)

        y_count = len(self.years)
        c_info_end = 6
        c_quota_start = c_info_end + 1
        c_quota_end = c_info_end + y_count
        c_nv_start = c_quota_end + 1
        c_nv_end = c_quota_end + y_count

        # Mỗi phương thức chiếm y_count cột
        method_ranges = []  # (mid, label, start_col, end_col, color)
        cursor = c_nv_end + 1
        for i, mid in enumerate(method_keys):
            start_c = cursor
            end_c = cursor + y_count - 1
            color = self.COLOR_METHOD_SCORES[i % len(self.COLOR_METHOD_SCORES)]
            label = method_labels.get(mid) or METHOD_COLUMN_LABELS.get(mid, mid)
            method_ranges.append((mid, label, start_c, end_c, color))
            cursor = end_c + 1

        c_score_start = method_ranges[0][2] if method_ranges else (c_nv_end + 1)
        c_score_end = method_ranges[-1][3] if method_ranges else c_nv_end
        c_trend_start = (c_score_end + 1) if method_ranges else (c_nv_end + 1)
        c_trend_end = c_trend_start + 1

        groups = [
            (1, c_info_end, "THÔNG TIN CHUNG", self.COLOR_GROUP_INFO),
            (c_quota_start, c_quota_end, "CHỈ TIÊU TUYỂN SINH", self.COLOR_GROUP_QUOTA),
            (c_nv_start, c_nv_end, "SỐ LƯỢNG HỒ SƠ / NGUYỆN VỌNG", self.COLOR_GROUP_NV),
        ]
        for mid, label, start_c, end_c, color in method_ranges:
            groups.append((start_c, end_c, f"ĐIỂM CHUẨN — {label}", color))
        groups.append((c_trend_start, c_trend_end, "PHÂN TÍCH XU HƯỚNG", self.COLOR_GROUP_TREND))

        for start_c, end_c, title, color_hex in groups:
            if end_c < start_c:
                continue
            ws.merge_cells(start_row=1, start_column=start_c, end_row=1, end_column=end_c)
            cell = ws.cell(row=1, column=start_c, value=title)
            cell.font = font_header_grp
            cell.alignment = Alignment(horizontal="center", vertical="center")
            fill = PatternFill(start_color=color_hex, end_color=color_hex, fill_type="solid")
            for c in range(start_c, end_c + 1):
                ws.cell(row=1, column=c).fill = fill
                ws.cell(row=1, column=c).border = header_border

        ws.row_dimensions[1].height = 28

        sub_headers = [
            ("Mã trường", self.COLOR_GROUP_INFO),
            ("Tên trường", self.COLOR_GROUP_INFO),
            ("Mã ngành", self.COLOR_GROUP_INFO),
            ("Tên ngành đào tạo", self.COLOR_GROUP_INFO),
            ("Tổ hợp môn", self.COLOR_GROUP_INFO),
            ("Các PTXT có điểm", self.COLOR_GROUP_INFO),
        ]
        for yr in self.years:
            sub_headers.append((f"{yr}", self.COLOR_GROUP_QUOTA))
        for yr in self.years:
            sub_headers.append((f"{yr}", self.COLOR_GROUP_NV))
        for mid, label, start_c, end_c, color in method_ranges:
            for yr in self.years:
                sub_headers.append((f"{yr}", color))
        sub_headers.append(("Điểm TB (THPT)", self.COLOR_GROUP_TREND))
        sub_headers.append(("Biến động gần nhất", self.COLOR_GROUP_TREND))

        for col_idx, (text, color_hex) in enumerate(sub_headers, start=1):
            cell = ws.cell(row=2, column=col_idx, value=text)
            cell.font = font_header_sub
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.fill = PatternFill(start_color=color_hex, end_color=color_hex, fill_type="solid")
            cell.border = header_border

        ws.row_dimensions[2].height = 24

        if not df.empty:
            for r_idx, row in df.iterrows():
                row_num = r_idx + 3
                ws.row_dimensions[row_num].height = 20
                row_vals = [
                    row.get("ma_truong", ""),
                    row.get("ten_truong", ""),
                    row.get("ma_nganh", ""),
                    row.get("ten_nganh", ""),
                    row.get("to_hop", ""),
                    row.get("phuong_thuc", ""),
                ]
                for yr in self.years:
                    row_vals.append(row.get(f"chi_tieu_{yr}"))
                for yr in self.years:
                    row_vals.append(row.get(f"so_nv_{yr}"))
                for mid, *_rest in method_ranges:
                    for yr in self.years:
                        row_vals.append(row.get(f"diem_{mid}_{yr}"))
                row_vals.append(row.get("diem_tb"))
                row_vals.append(row.get("bien_dong_diem"))

                for c_idx, val in enumerate(row_vals, start=1):
                    cell = ws.cell(row=row_num, column=c_idx)
                    cell.value = val if val is not None else "-"
                    cell.font = font_data
                    cell.border = thin_border

                    if c_idx in [1, 3, 5]:
                        cell.alignment = Alignment(horizontal="center", vertical="center")
                    elif c_idx in [2, 4, 6]:
                        cell.alignment = Alignment(horizontal="left", vertical="center")
                    elif c_quota_start <= c_idx <= c_nv_end:
                        cell.alignment = Alignment(horizontal="right", vertical="center")
                        if isinstance(val, (int, float)):
                            cell.number_format = "#,##0"
                    elif c_score_start <= c_idx <= c_score_end or c_idx == c_trend_start:
                        cell.alignment = Alignment(horizontal="center", vertical="center")
                        if isinstance(val, (int, float)):
                            cell.number_format = "0.00"
                    else:
                        cell.alignment = Alignment(horizontal="center", vertical="center")

        ws.freeze_panes = "E3"
        total_cols = len(sub_headers)
        last_row = max(len(df) + 2, 2)
        ws.auto_filter.ref = f"A2:{get_column_letter(total_cols)}{last_row}"
        self._autofit_columns(ws, max_cols=total_cols)

    def _build_flat_sheet(self, ws: openpyxl.worksheet.worksheet.Worksheet, df: pd.DataFrame):
        """Xây dựng sheet dữ liệu phẳng phục vụ Pivot Table / PowerBI."""
        ws.views.sheetView[0].showGridLines = True
        header_fill = PatternFill(start_color="2F5597", end_color="2F5597", fill_type="solid")
        header_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        font_data = Font(name="Arial", size=10)
        thin_border = Border(
            left=Side(style="thin", color="D3D3D3"),
            right=Side(style="thin", color="D3D3D3"),
            top=Side(style="thin", color="D3D3D3"),
            bottom=Side(style="thin", color="D3D3D3")
        )

        headers_map = [
            ("ma_truong", "Mã trường"),
            ("ten_truong", "Tên trường"),
            ("ma_nganh", "Mã ngành"),
            ("ten_nganh", "Tên ngành"),
            ("nam", "Năm tuyển sinh"),
            ("to_hop", "Tổ hợp môn"),
            ("chi_tieu", "Chỉ tiêu"),
            ("so_nguyen_vong", "Số hồ sơ / NV"),
            ("ty_le_choi", "Tỷ lệ chọi (NV/CT)"),
            ("diem_chuan", "Điểm chuẩn (THPT/quy đổi)"),
            ("diem_chuan_ptxt", "Điểm chuẩn theo PTXT"),
            ("thang_diem", "Thang điểm"),
            ("phuong_thuc", "Phương thức xét tuyển"),
            ("quy_che", "Quy chế / điều kiện"),
            ("diem_quy_doi", "Điểm quy đổi (tóm tắt)"),
            ("ghi_chu", "Ghi chú"),
            ("nguon", "Nguồn dữ liệu"),
        ]

        ws.row_dimensions[1].height = 24
        for col_idx, (_, col_title) in enumerate(headers_map, start=1):
            cell = ws.cell(row=1, column=col_idx, value=col_title)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        if not df.empty:
            for r_idx, row in df.iterrows():
                row_num = r_idx + 2
                ws.row_dimensions[row_num].height = 19
                for c_idx, (col_key, _) in enumerate(headers_map, start=1):
                    val = row.get(col_key)
                    cell = ws.cell(row=row_num, column=c_idx)
                    cell.value = val if pd.notna(val) and val is not None else ""
                    cell.font = font_data
                    cell.border = thin_border

                    if col_key in ["chi_tieu", "so_nguyen_vong"]:
                        if isinstance(val, (int, float)):
                            cell.number_format = "#,##0"
                            cell.alignment = Alignment(horizontal="right", vertical="center")
                    elif col_key in ["diem_chuan", "diem_chuan_ptxt", "ty_le_choi"]:
                        if isinstance(val, (int, float)):
                            cell.number_format = "0.00"
                            cell.alignment = Alignment(horizontal="center", vertical="center")
                    elif col_key in ["ma_truong", "ma_nganh", "nam", "to_hop"]:
                        cell.alignment = Alignment(horizontal="center", vertical="center")
                    else:
                        cell.alignment = Alignment(horizontal="left", vertical="center")

        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers_map))}{len(df) + 1}"
        self._autofit_columns(ws, max_cols=len(headers_map))

    def _build_conversion_sheet(
        self,
        ws: openpyxl.worksheet.worksheet.Worksheet,
        conversions: List[ScoreConversionRecord],
    ):
        """Sheet bảng quy đổi / hoán đổi điểm chứng chỉ."""
        ws.views.sheetView[0].showGridLines = True
        header_fill = PatternFill(start_color=self.COLOR_GROUP_CONV, end_color=self.COLOR_GROUP_CONV, fill_type="solid")
        header_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        font_data = Font(name="Arial", size=10)
        thin_border = Border(
            left=Side(style="thin", color="D3D3D3"),
            right=Side(style="thin", color="D3D3D3"),
            top=Side(style="thin", color="D3D3D3"),
            bottom=Side(style="thin", color="D3D3D3"),
        )

        headers = [
            "Mã trường", "Tên trường", "Loại bảng quy đổi", "Hạng mục / chứng chỉ",
            "Điểm quy đổi", "Chi tiết các cột", "Phương thức áp dụng",
            "Thang điểm", "Năm", "Ghi chú", "Nguồn",
        ]
        ws.row_dimensions[1].height = 24
        for col_idx, title in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=title)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        for r_idx, rec in enumerate(conversions, start=2):
            vals = [
                rec.ma_truong, rec.ten_truong, rec.loai_bang, rec.hang_muc,
                rec.diem_quy_doi, rec.chi_tiet_hang, rec.phuong_thuc,
                rec.thang_diem, rec.nam or "", rec.ghi_chu, rec.nguon,
            ]
            for c_idx, val in enumerate(vals, start=1):
                cell = ws.cell(row=r_idx, column=c_idx, value=val if val is not None else "")
                cell.font = font_data
                cell.border = thin_border
                cell.alignment = Alignment(vertical="center", wrap_text=(c_idx in [6, 10]))

        if not conversions:
            ws.cell(row=2, column=1, value="Chưa thu thập được bảng quy đổi (cào online đề án hoặc bổ sung file cục bộ).")

        ws.freeze_panes = "A2"
        last_row = max(len(conversions) + 1, 2)
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{last_row}"
        self._autofit_columns(ws, max_cols=len(headers))

    def _build_regulation_sheet(
        self,
        ws: openpyxl.worksheet.worksheet.Worksheet,
        regulations: List[AdmissionRegulation],
    ):
        """Sheet quy chế / quy định xét tuyển."""
        ws.views.sheetView[0].showGridLines = True
        header_fill = PatternFill(start_color=self.COLOR_GROUP_REG, end_color=self.COLOR_GROUP_REG, fill_type="solid")
        header_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        font_data = Font(name="Arial", size=10)
        thin_border = Border(
            left=Side(style="thin", color="D3D3D3"),
            right=Side(style="thin", color="D3D3D3"),
            top=Side(style="thin", color="D3D3D3"),
            bottom=Side(style="thin", color="D3D3D3"),
        )

        headers = [
            "Mã trường", "Tên trường", "Tiêu đề", "Nội dung quy chế",
            "Phương thức liên quan", "Năm", "Nguồn",
        ]
        ws.row_dimensions[1].height = 24
        for col_idx, title in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=title)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        for r_idx, rec in enumerate(regulations, start=2):
            vals = [
                rec.ma_truong, rec.ten_truong, rec.tieu_de, rec.noi_dung,
                rec.phuong_thuc, rec.nam or "", rec.nguon,
            ]
            ws.row_dimensions[r_idx].height = min(80, 20 + len(rec.noi_dung) // 80 * 12)
            for c_idx, val in enumerate(vals, start=1):
                cell = ws.cell(row=r_idx, column=c_idx, value=val if val is not None else "")
                cell.font = font_data
                cell.border = thin_border
                cell.alignment = Alignment(vertical="top", wrap_text=(c_idx in [3, 4]))

        if not regulations:
            ws.cell(row=2, column=1, value="Chưa thu thập được quy chế (cào online đề án tuyển sinh).")

        ws.freeze_panes = "A2"
        last_row = max(len(regulations) + 1, 2)
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{last_row}"
        self._autofit_columns(ws, max_cols=len(headers))
        ws.column_dimensions["D"].width = 80

    def _build_guide_sheet(
        self,
        ws: openpyxl.worksheet.worksheet.Worksheet,
        total_records: int,
        total_conversions: int = 0,
        total_regulations: int = 0,
    ):
        """Tạo trang hướng dẫn đọc số liệu và lưu ý nghiệp vụ."""
        ws.views.sheetView[0].showGridLines = True
        ws.column_dimensions["B"].width = 28
        ws.column_dimensions["C"].width = 85

        title_font = Font(name="Arial", size=14, bold=True, color="1F4E79")
        section_font = Font(name="Arial", size=11, bold=True, color="2E75B6")
        bold_font = Font(name="Arial", size=10, bold=True)
        text_font = Font(name="Arial", size=10)

        yr_min = min(self.years) if self.years else 2021
        yr_max = max(self.years) if self.years else 2026
        ws["B2"] = f"HỆ THỐNG TỔNG HỢP DỮ LIỆU TUYỂN SINH ĐẠI HỌC ({yr_min} - {yr_max})"
        ws["B2"].font = title_font

        ws["B4"] = "Tổng số bản ghi ngành:"
        ws["B4"].font = bold_font
        ws["C4"] = f"{total_records:,} bản ghi"
        ws["C4"].font = text_font

        ws["B5"] = "Bảng quy đổi chứng chỉ:"
        ws["B5"].font = bold_font
        ws["C5"] = f"{total_conversions:,} dòng"
        ws["C5"].font = text_font

        ws["B6"] = "Mục quy chế:"
        ws["B6"].font = bold_font
        ws["C6"] = f"{total_regulations:,} mục"
        ws["C6"].font = text_font

        ws["B7"] = "Năm dữ liệu:"
        ws["B7"].font = bold_font
        ws["C7"] = ", ".join(str(y) for y in self.years)
        ws["C7"].font = text_font

        guide_items = [
            (f"Sheet Tong_Hop_{yr_min}_{yr_max}", f"Bảng dạng ngang tổng hợp đa năm của từng ngành, gom Chỉ tiêu, Số nguyện vọng và Điểm chuẩn từ {yr_min} đến {yr_max}."),
            ("Sheet Du_Lieu_Chi_Tiet", "Bảng phẳng: mỗi dòng = 1 ngành × 1 năm × 1 phương thức. Có cột Mã ngành, Phương thức, Quy chế, Điểm quy đổi (tóm tắt)."),
            ("Sheet Diem_Quy_Doi", "Bảng quy đổi/hoán đổi chứng chỉ (IELTS, TOEFL, SAT, ACT, A-Level, hệ chữ A*/A/B/C,...) → điểm xét tuyển. Lấy từ Đề án tuyển sinh online."),
            ("Sheet Quy_Che", "Các đoạn quy chế/quy định xét tuyển (ngoại ngữ, đối tượng, công thức điểm,...) trích từ đề án."),
            ("Mã ngành", "Mã xét tuyển của trường (VD: IT1, BF1) hoặc mã ngành chuẩn 7 chữ số Bộ GD&ĐT. Ưu tiên lấy từ đề án / bảng điểm, bổ sung bằng bộ tra cứu."),
            ("Phương thức xét tuyển", "Điểm thi THPT, Xét học bạ, ĐGNL, ĐGTD, Xét tuyển kết hợp, Chứng chỉ quốc tế, Xét tuyển tài năng,..."),
            ("Điểm quy đổi", "Ví dụ: IELTS 6.5 → 9.0 điểm môn Anh; SAT 1500 → 19.5/20. Xem chi tiết từng dòng ở sheet Diem_Quy_Doi."),
            ("Chỉ tiêu tuyển sinh", "Số lượng sinh viên trường dự kiến tuyển vào ngành theo phương thức tương ứng."),
            ("Số hồ sơ / Nguyện vọng", "Số lượng nguyện vọng hoặc hồ sơ thí sinh đăng ký vào ngành."),
            ("Tỷ lệ chọi (NV / CT)", "Tỷ số cạnh tranh = Số lượng nguyện vọng / Chỉ tiêu."),
            ("Điểm chuẩn", "Điểm trúng tuyển theo thang điểm tương ứng (thang 30 THPT, thang 40 nhân hệ số, hoặc thang ĐGTD/ĐGNL)."),
            ("Biến động điểm gần nhất", "Chênh lệch điểm giữa năm gần nhất và năm liền kề trước đó có dữ liệu."),
        ]

        ws["B9"] = "MỤC / CHỈ SỐ"
        ws["B9"].font = section_font
        ws["C9"] = "Ý NGHĨA VÀ CÁCH SỬ DỤNG"
        ws["C9"].font = section_font

        curr_row = 10
        for label, desc in guide_items:
            ws.cell(row=curr_row, column=2, value=label).font = bold_font
            cell_desc = ws.cell(row=curr_row, column=3, value=desc)
            cell_desc.font = text_font
            cell_desc.alignment = Alignment(wrap_text=True)
            ws.row_dimensions[curr_row].height = 28
            curr_row += 1

    def _autofit_columns(self, ws: openpyxl.worksheet.worksheet.Worksheet, max_cols: int):
        """Tự động tính toán độ rộng hợp lý cho từng cột."""
        for col in range(1, max_cols + 1):
            col_letter = get_column_letter(col)
            max_len = 0
            for row in range(1, min(ws.max_row + 1, 100)):  # Quét tối đa 100 dòng đầu để tối ưu tốc độ
                val = ws.cell(row=row, column=col).value
                if val is not None:
                    s_len = len(str(val))
                    if s_len > max_len:
                        max_len = s_len
            # Giới hạn độ rộng trong khoảng 10 đến 45
            adjusted_width = max(10, min(max_len + 3, 45))
            ws.column_dimensions[col_letter].width = adjusted_width
