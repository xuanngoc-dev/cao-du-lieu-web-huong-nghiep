# -*- coding: utf-8 -*-
"""
Chương trình: Tự động Tổng hợp Dữ liệu Tuyển sinh Đại học (2021 - 2025)
Tác giả: Antigravity - Python-huong-nghiep
Mô tả: Điểm khởi chạy chính của dự án. Hỗ trợ chạy dòng lệnh CLI hoặc menu tương tác.
"""

import os
import sys
import argparse
import json
from typing import List, Optional

from core.models import AdmissionRecord, ScoreConversionRecord, AdmissionRegulation, CrawlBundle
from core.aggregator import AdmissionAggregator
from core.normalizer import normalize_school_code, get_school_display_name
from parsers.excel_parser import ExcelAdmissionParser
from parsers.pdf_parser import PdfAdmissionParser
from parsers.docx_parser import DocxAdmissionParser
from crawlers.online_crawler import OnlineAdmissionCrawler
from crawlers.school_directory import SchoolDirectory
from exporter.excel_exporter import ExcelAdmissionExporter

try:
    from tabulate import tabulate
except ImportError:
    tabulate = None


def load_schools_config(config_path: str = "config/schools.json") -> List[dict]:
    """Đọc cấu hình danh sách trường từ file JSON."""
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("schools", [])
        except Exception as e:
            print(f"[CẢNH BÁO] Không thể đọc config {config_path}: {e}")
    return [
        {"code": "BKA", "name": "Đại học Bách khoa Hà Nội"},
        {"code": "NEU", "name": "Trường Đại học Kinh tế Quốc dân"},
        {"code": "QHI", "name": "Trường Đại học Công nghệ - ĐHQGHN"}
    ]


def resolve_school_codes(
    schools_arg: str = "",
    refresh: bool = False,
    limit: Optional[int] = None,
) -> List[str]:
    """
    Phân giải danh sách mã trường từ tham số --schools.
    Hỗ trợ:
      - BKA,NEU,FTU          → danh sách cụ thể
      - all                  → tất cả ĐH + HV + CĐ
      - all-dh / all-dai-hoc → chỉ Đại học + Học viện
      - all-cd / all-cao-dang→ chỉ Cao đẳng
      - @config/schools_all.json → đọc từ file JSON
      - (rỗng)               → lấy từ config/schools.json
    """
    raw = (schools_arg or "").strip()

    # Đọc từ file JSON nếu bắt đầu bằng @
    if raw.startswith("@"):
        path = raw[1:].strip()
        schools = load_schools_config(path)
        codes = [s["code"].strip().upper() for s in schools if s.get("code")]
        if limit:
            codes = codes[:limit]
        return codes

    special = raw.lower().replace("_", "-")
    if special in ("all", "all-dh", "all-dai-hoc", "all-cd", "all-cao-dang", "all-hv", "all-hoc-vien"):
        directory = SchoolDirectory()
        directory.load(force_refresh=refresh)
        if special == "all":
            codes = directory.get_codes()
        elif special in ("all-dh", "all-dai-hoc"):
            codes = directory.get_codes(school_type="dai_hoc_hoc_vien")
        elif special in ("all-cd", "all-cao-dang"):
            codes = directory.get_codes(school_type="cao_dang")
        else:
            codes = directory.get_codes(school_type="hoc_vien")
        if limit:
            codes = codes[:limit]
        print(f"[SCHOOLS] Đã chọn {len(codes)} mã trường (mode={special}).")
        return codes

    if raw:
        codes = [s.strip().upper() for s in raw.split(",") if s.strip()]
        if limit:
            codes = codes[:limit]
        return codes

    # Mặc định: config nhỏ để test nhanh
    config_schools = load_schools_config()
    codes = [s["code"] for s in config_schools[:3]]
    if limit:
        codes = codes[:limit]
    return codes


def process_list_schools(
    refresh: bool = False,
    school_filter: str = "all",
    output_excel: str = "data/output/danh_sach_ma_truong.xlsx",
    output_json: str = "data/output/danh_sach_ma_truong.json",
    include_profile: bool = True,
) -> List[str]:
    """
    Chức năng lấy danh sách mã trường ĐH/CĐ và xuất file.
    Trả về list mã trường để có thể dùng tiếp cho crawler.
    """
    directory = SchoolDirectory()
    directory.load(
        force_refresh=refresh,
        include_dai_hoc=True,
        include_cao_dang=True,
        include_profile=include_profile,
    )

    type_map = {
        "all": None,
        "dai_hoc": "dai_hoc",
        "cao_dang": "cao_dang",
        "hoc_vien": "hoc_vien",
        "dai_hoc_hoc_vien": "dai_hoc_hoc_vien",
    }
    stype = type_map.get(school_filter, None)

    directory.print_summary(school_type=stype)
    directory.export_json(output_path=output_json, school_type=stype, also_update_config=True)
    directory.export_excel(output_path=output_excel, school_type=stype)

    codes = directory.get_codes(school_type=stype)
    print(
        f"[DANH_BA] Dùng danh sách này để thu thập toàn bộ:\n"
        f"  python3 main.py --mode online --schools all\n"
        f"  python3 main.py --mode online --schools @config/schools_all.json\n"
        f"  python3 main.py --mode online --schools all-dh   # chỉ ĐH+HV\n"
        f"  python3 main.py --mode online --schools all-cd   # chỉ Cao đẳng\n"
    )
    return codes


def process_local_files(input_dir: str = "data/input_files") -> List[AdmissionRecord]:
    """
    Quét và tự động bóc tách tất cả file tài liệu (Excel, PDF, Word) trong thư mục.
    """
    records: List[AdmissionRecord] = []
    if not os.path.exists(input_dir):
        os.makedirs(input_dir, exist_ok=True)
        print(f"[LOCAL] Thư mục '{input_dir}' đã được tạo. Vui lòng đặt các file PDF/Excel/Word vào đây.")
        return records

    excel_parser = ExcelAdmissionParser()
    pdf_parser = PdfAdmissionParser()
    docx_parser = DocxAdmissionParser()

    supported_extensions = {".xlsx", ".xls", ".pdf", ".docx"}
    files = [f for f in os.listdir(input_dir) if os.path.splitext(f)[1].lower() in supported_extensions]

    if not files:
        print(f"[LOCAL] Chưa tìm thấy file nào trong thư mục '{input_dir}'.")
        return records

    print(f"\n[LOCAL] Tìm thấy {len(files)} file tài liệu trong '{input_dir}':")
    for file_name in files:
        file_path = os.path.join(input_dir, file_name)
        ext = os.path.splitext(file_name)[1].lower()
        print(f"  -> Đang xử lý: {file_name} ...", end="", flush=True)

        file_records = []
        if ext in [".xlsx", ".xls"]:
            file_records = excel_parser.parse(file_path)
        elif ext == ".pdf":
            file_records = pdf_parser.parse(file_path)
        elif ext == ".docx":
            file_records = docx_parser.parse(file_path)

        print(f" Xong ({len(file_records)} bản ghi)")
        records.extend(file_records)

    return records


def process_online_crawling(school_codes: List[str], years: List[int]) -> CrawlBundle:
    """
    Thu thập dữ liệu tuyển sinh từ các cổng trực tuyến theo danh sách mã trường.
    Bao gồm điểm chuẩn, mã ngành, phương thức, quy chế và bảng quy đổi chứng chỉ.
    """
    crawler = OnlineAdmissionCrawler()
    bundle = CrawlBundle()
    total = len(school_codes)

    preview = ", ".join(school_codes[:8])
    if total > 8:
        preview += f", ... (+{total - 8})"
    print(f"\n[ONLINE] Bắt đầu Thu thập dữ liệu cho {total} trường: {preview}")

    for idx, code in enumerate(school_codes, start=1):
        print(f"\n----- [{idx}/{total}] Mã trường: {code} -----")
        try:
            school_bundle = crawler.crawl_school_bundle(code, years=years)
            bundle.admissions.extend(school_bundle.admissions)
            bundle.conversions.extend(school_bundle.conversions)
            bundle.regulations.extend(school_bundle.regulations)
        except Exception as e:
            print(f"[CẢNH BÁO] Bỏ qua {code} do lỗi: {e}")
            continue

    return bundle


def create_sample_files(input_dir: str = "data/input_files"):
    """
    Tạo các file tài liệu mẫu (Excel, Word, PDF) chứa bảng điểm chuẩn & chỉ tiêu & số NV
    để người dùng có thể chạy thử nghiệm ngay lập tức.
    """
    import pandas as pd
    import docx
    os.makedirs(input_dir, exist_ok=True)

    # 1. File Excel mẫu: BKA_TuyenSinh_2023.xlsx
    excel_path = os.path.join(input_dir, "BKA_DeAnTuyenSinh_2023.xlsx")
    df_sample_excel = pd.DataFrame({
        "Mã ngành": ["7480201", "7480101", "7520216", "7340101"],
        "Tên ngành": [
            "Công nghệ thông tin (Việt - Nhật)",
            "Khoa học máy tính (IT1)",
            "Kỹ thuật Điều khiển và Tự động hóa",
            "Quản trị kinh doanh"
        ],
        "Tổ hợp xét tuyển": ["A00, A01", "A00, A01", "A00, A01", "A00, D01, D07"],
        "Chỉ tiêu tuyển sinh": [150, 300, 250, 180],
        "Số hồ sơ đăng ký (NV1)": [1850, 4200, 2100, 1600],
        "Điểm trúng tuyển 2023": [26.85, 29.42, 27.55, 25.60],
        "Ghi chú": ["Chương trình chất lượng cao", "Chuẩn", "Chuẩn", "Chuẩn"]
    })
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        df_sample_excel.to_excel(writer, sheet_name="Diem_Chuan_2023", index=False)
    print(f"[SAMPLE] Đã tạo file Excel mẫu: {excel_path}")

    # 2. File Word mẫu: NEU_ThongBaoDiem_2024.docx
    docx_path = os.path.join(input_dir, "NEU_ThongBaoDiem_2024.docx")
    doc = docx.Document()
    doc.add_heading("TRƯỜNG ĐẠI HỌC KINH TẾ QUỐC DÂN", level=1)
    doc.add_paragraph("THÔNG BÁO ĐIỂM CHUẨN VÀ CHỈ TIÊU TUYỂN SINH NĂM 2024")

    table = doc.add_table(rows=1, cols=6)
    table.style = "Table Grid"
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = "Mã ngành"
    hdr_cells[1].text = "Tên ngành đào tạo"
    hdr_cells[2].text = "Tổ hợp môn"
    hdr_cells[3].text = "Chỉ tiêu"
    hdr_cells[4].text = "Số lượng ĐK"
    hdr_cells[5].text = "Điểm trúng tuyển"

    neu_data = [
        ("7340101", "Quản trị kinh doanh", "A00, A01, D01, D07", "280", "3100", "27.40"),
        ("7340201", "Tài chính - Ngân hàng", "A00, A01, D01, D07", "250", "2950", "27.25"),
        ("7340301", "Kế toán", "A00, A01, D01, D07", "220", "2100", "26.90"),
        ("7310101", "Kinh tế học", "A00, A01, D01, D07", "180", "1500", "26.64"),
    ]
    for row in neu_data:
        row_cells = table.add_row().cells
        for idx, text in enumerate(row):
            row_cells[idx].text = text

    doc.save(docx_path)
    print(f"[SAMPLE] Đã tạo file Word mẫu: {docx_path}")

    # 3. File PDF mẫu nếu có reportlab hoặc xuất text:
    # Do người dùng có thể chưa có reportlab, ta kiểm tra import reportlab
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib import colors

        pdf_path = os.path.join(input_dir, "QHI_DeAnTuyenSinh_2023.pdf")
        doc_pdf = SimpleDocTemplate(pdf_path, pagesize=letter)
        elements = []
        styles = getSampleStyleSheet()
        elements.append(Paragraph("TRUONG DAI HOC CONG NGHE - DHQGHN", styles["Title"]))
        elements.append(Paragraph("DE AN TUYEN SINH NAM 2023", styles["Heading2"]))
        elements.append(Spacer(1, 10))

        pdf_table_data = [
            ["Ma nganh", "Ten nganh", "To hop", "Chi tieu", "So nguyen vong", "Diem chuan"],
            ["7480201", "Cong nghe thong tin", "A00, A01", "320", "4500", "27.85"],
            ["7480108", "Khoa hoc may tinh", "A00, A01", "150", "2200", "27.25"],
            ["7510401", "Cong nghe ky thuat dien tu - vien thong", "A00, A01", "200", "1800", "25.50"],
            ["7520216", "Ky thuat dieu khien va tu dong hoa", "A00, A01", "120", "1400", "26.10"]
        ]
        t = Table(pdf_table_data)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.navy),
            ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('GRID', (0,0), (-1,-1), 1, colors.black)
        ]))
        elements.append(t)
        doc_pdf.build(elements)
        print(f"[SAMPLE] Đã tạo file PDF mẫu: {pdf_path}")
    except ImportError:
        pass

    # 4. File Excel mẫu năm 2026: FTU_DeAnTuyenSinh_2026.xlsx
    ftu_excel_path = os.path.join(input_dir, "FTU_DeAnTuyenSinh_2026.xlsx")
    df_ftu_2026 = pd.DataFrame({
        "Mã ngành": ["7340120", "7340101", "7340201"],
        "Tên ngành": [
            "Kinh doanh quốc tế",
            "Quản trị kinh doanh",
            "Tài chính - Ngân hàng"
        ],
        "Tổ hợp môn": ["A00, A01, D01", "A00, A01, D01", "A00, A01, D01"],
        "Chỉ tiêu 2026": [350, 400, 300],
        "Số hồ sơ đăng ký": [4200, 3900, 3100],
        "Điểm chuẩn 2026": [28.20, 27.90, 27.80],
        "Ghi chú": ["Chương trình tiêu chuẩn", "Chương trình tiêu chuẩn", "Chương trình tiêu chuẩn"]
    })
    with pd.ExcelWriter(ftu_excel_path, engine="openpyxl") as writer:
        df_ftu_2026.to_excel(writer, sheet_name="TuyenSinh_2026", index=False)
    print(f"[SAMPLE] Đã tạo file Excel mẫu 2026: {ftu_excel_path}")


def print_summary_table(records: List[AdmissionRecord]):
    """In bảng thống kê tóm tắt lên màn hình console."""
    if not records:
        print("[THỐNG KÊ] Chưa có bản ghi nào được thu thập.")
        return

    schools = {}
    for r in records:
        sch = normalize_school_code(r.ma_truong) or "N/A"
        ten = get_school_display_name(sch, r.ten_truong)
        if sch not in schools:
            schools[sch] = {"ten": ten, "records": 0, "majors": set(), "years": set()}
        schools[sch]["records"] += 1
        schools[sch]["majors"].add(r.ten_nganh)
        schools[sch]["years"].add(r.nam)

    table_data = []
    for code, info in schools.items():
        table_data.append([
            code,
            info["ten"][:35],
            len(info["majors"]),
            f"{min(info['years'])} - {max(info['years'])}",
            info["records"]
        ])

    headers = ["Mã trường", "Tên trường", "Số ngành", "Giai đoạn năm", "Tổng bản ghi"]
    print("\n" + "=" * 80)
    print(" BẢNG TÓM TẮT DỮ LIỆU TUYỂN SINH ĐÃ THU THẬP")
    print("=" * 80)
    if tabulate:
        print(tabulate(table_data, headers=headers, tablefmt="fancy_grid"))
    else:
        for row in table_data:
            print(f"| {row[0]:<5} | {row[1]:<35} | Ngành: {row[2]:<3} | Năm: {row[3]:<11} | Bản ghi: {row[4]:<4} |")
    print(f" Tổng cộng: {len(records)} bản ghi.")
    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Chương trình tổng hợp dữ liệu tuyển sinh đại học / cao đẳng 2021-2026"
    )
    parser.add_argument(
        "--mode",
        choices=["all", "online", "local", "demo", "schools", "ui"],
        default=None,
        help="Chế độ: ui (giao diện web), schools, online, local, all, demo",
    )
    parser.add_argument(
        "--schools",
        type=str,
        default="",
        help="Mã trường: BKA,NEU | all | all-dh | all-cd | @config/schools_all.json",
    )
    parser.add_argument(
        "--school-filter",
        type=str,
        default="all",
        choices=["all", "dai_hoc", "cao_dang", "hoc_vien", "dai_hoc_hoc_vien"],
        help="Lọc loại hình khi --mode schools (mặc định: all)",
    )
    parser.add_argument(
        "--refresh-schools",
        action="store_true",
        help="Bỏ cache, lấy lại danh bạ mã trường từ web",
    )
    parser.add_argument(
        "--no-profile",
        action="store_true",
        help="Khi --mode schools: bỏ qua hồ sơ giới thiệu (nhanh hơn)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Giới hạn số trường khi thu thập (hữu ích khi test với --schools all)",
    )
    parser.add_argument("--input-dir", type=str, default="data/input_files",
                        help="Đường dẫn thư mục chứa file PDF, Excel, Word cục bộ")
    parser.add_argument("--output", type=str, default="data/output/tong_hop_tuyen_sinh.xlsx",
                        help="Đường dẫn file Excel xuất ra")
    parser.add_argument("--years", type=str, default="2021,2022,2023,2024,2025,2026",
                        help="Danh sách năm cần tổng hợp, VD: 2021,2022,2023,2024,2025,2026")

    args = parser.parse_args()
    years = [int(y.strip()) for y in args.years.split(",") if y.strip().isdigit()]
    limit = args.limit if args.limit and args.limit > 0 else None

    mode = args.mode

    # Nếu không có tham số dòng lệnh, hiển thị menu tương tác
    if not mode:
        print("\n" + "=" * 70)
        print(" CHƯƠNG TRÌNH TỰ ĐỘNG TỔNG HỢP DỮ LIỆU TUYỂN SINH (2021 - 2026)")
        print("=" * 70)
        print(" [1] Thu thập dữ liệu trực tuyến theo danh sách mã trường (Online Crawling)")
        print(" [2] Bóc tách tài liệu từ thư mục cục bộ (data/input_files: PDF, Excel, Word)")
        print(" [3] Tổng hợp kết hợp cả hai nguồn (Online + File cục bộ)")
        print(" [4] Tạo file mẫu (Excel, Word, PDF) & Chạy thử nghiệm toàn diện")
        print(" [5] Lấy danh sách mã trường ĐH / CĐ / Học viện (phục vụ thu thập toàn bộ)")
        print(" [6] thu thập TẤT CẢ trường (lấy danh bạ → thu thập online)")
        print(" [7] Mở giao diện web Bootstrap (http://127.0.0.1:5000)")
        print(" [0] Thoát")
        print("=" * 70)

        choice = input("Nhập lựa chọn của bạn (1/2/3/4/5/6/7/0) [Mặc định 3]: ").strip()
        if choice == "1":
            mode = "online"
        elif choice == "2":
            mode = "local"
        elif choice == "4":
            mode = "demo"
        elif choice == "5":
            mode = "schools"
        elif choice == "6":
            mode = "online"
            if not args.schools:
                args.schools = "all"
            refresh_ask = input("Làm mới danh bạ từ web? (y/N): ").strip().lower()
            if refresh_ask == "y":
                args.refresh_schools = True
        elif choice == "7":
            mode = "ui"
        elif choice == "0":
            print("Đã thoát chương trình.")
            return
        else:
            mode = "all"

    # Chế độ giao diện web Bootstrap (Flask + Waitress)
    if mode == "ui":
        host = "127.0.0.1"
        # macOS thường chiếm sẵn cổng 5000 (AirPlay Receiver / Control Center)
        port = int(os.environ.get("UI_PORT", "8080"))
        url = f"http://{host}:{port}"
        print("\n[UI] Đang mở giao diện Bootstrap…")
        print(f"[UI] Truy cập: {url}")
        print("[UI] (Không dùng cổng 5000 — trên macOS cổng này thường bị hệ thống chiếm.)")
        print("[UI] Nhấn Ctrl+C để dừng.\n")
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass
        from web.app import app as flask_app
        try:
            from waitress import serve
            serve(flask_app, host=host, port=port, threads=4)
        except ImportError:
            print("[UI] Chưa có waitress — dùng Flask dev server (chỉ dùng local).")
            print("     Cài thêm: pip3 install waitress\n")
            flask_app.run(host=host, port=port, debug=False)
        return

    # Chế độ chỉ lấy danh sách mã trường
    if mode == "schools":
        process_list_schools(
            refresh=args.refresh_schools,
            school_filter=args.school_filter,
            include_profile=not args.no_profile,
        )
        return

    all_records: List[AdmissionRecord] = []
    all_conversions: List[ScoreConversionRecord] = []
    all_regulations: List[AdmissionRegulation] = []

    # Xử lý chế độ DEMO: Tạo file mẫu trước
    if mode == "demo":
        print("\n--- ĐANG KHỞI TẠO DỮ LIỆU MẪU ĐỂ KIỂM THỬ ---")
        create_sample_files(args.input_dir)
        mode = "all"

    # 1. Thu thập từ file cục bộ
    if mode in ["local", "all"]:
        local_records = process_local_files(args.input_dir)
        all_records.extend(local_records)

    # 2. Thu thập từ Online crawler
    if mode in ["online", "all"]:
        school_codes = resolve_school_codes(
            schools_arg=args.schools,
            refresh=args.refresh_schools,
            limit=limit,
        )
        if not school_codes:
            print("[THÔNG BÁO] Không có mã trường nào để thu thập.")
        else:
            # Cảnh báo khi thu thập số lượng lớn
            if len(school_codes) > 50:
                print(
                    f"[LƯU Ý] Bạn sắp thu thập {len(school_codes)} trường × {len(years)} năm. "
                    f"Quá trình có thể mất nhiều giờ. Dùng --limit N để thử trước."
                )
            online_bundle = process_online_crawling(school_codes, years=years)
            all_records.extend(online_bundle.admissions)
            all_conversions.extend(online_bundle.conversions)
            all_regulations.extend(online_bundle.regulations)

    if not all_records and not all_conversions and not all_regulations:
        print("\n[THÔNG BÁO] Không có dữ liệu nào để xuất file. Vui lòng kiểm tra lại cấu hình!")
        return

    # In tóm tắt
    print_summary_table(all_records)
    if all_conversions or all_regulations:
        print(
            f"[BỔ SUNG] Quy đổi chứng chỉ: {len(all_conversions)} dòng | "
            f"Quy chế: {len(all_regulations)} mục"
        )

    # Xuất ra file Excel
    exporter = ExcelAdmissionExporter(years=years)
    output_file = exporter.export(
        all_records,
        output_path=args.output,
        conversions=all_conversions,
        regulations=all_regulations,
    )

    print("\n" + "=" * 70)
    print(f" HOÀN TẤT! File tổng hợp đã được lưu tại:")
    print(f" -> {os.path.abspath(output_file)}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
