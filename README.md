# Hệ Thống Tự Động Tổng Hợp Dữ Liệu Tuyển Sinh Đại Học (2021 - 2026)

Hệ thống Python tự động thu thập, chuẩn hóa, ghép nối và tổng hợp dữ liệu tuyển sinh đại học giai đoạn 2021 - 2026 (bao gồm các năm 2021, 2022, 2023, 2024, 2025, 2026). Hệ thống hỗ trợ xử lý linh hoạt từ nguồn trực tuyến công khai và các tệp tài liệu cục bộ do người dùng cung cấp.

---

## 🌟 Tính Năng Nổi Bật

1. **Xử lý đa dạng nguồn dữ liệu đầu vào**:
   - **Cào tự động trực tuyến**: Chỉ cần nhập Mã trường (ví dụ: `BKA`, `NEU`, `FTU`, `QHI`,...) hoặc tên trường, hệ thống tự động tìm kiếm đường dẫn và cào điểm chuẩn qua các năm từ cổng thông tin tuyển sinh công khai.
   - **Bóc tách tài liệu cục bộ**: Tự động nhận diện cấu trúc bảng và trích xuất dữ liệu từ các file **Excel (`.xlsx`, `.xls`)**, **Word (`.docx`)** và **PDF (`.pdf`)** (như Đề án tuyển sinh, Thông báo điểm chuẩn) đặt trong thư mục `data/input_files/`.
2. **Thuật toán bóc tách & Chuẩn hóa thông minh**:
   - Tự động nhận diện cột bảng dựa trên từ khóa tiếng Việt đa dạng (`Mã ngành`, `Mã xét tuyển`, `Tên ngành`, `Tổ hợp`, `Chỉ tiêu`, `Số lượng ĐK / Nguyện vọng`, `Điểm trúng tuyển / Điểm chuẩn`, `Phương thức`, `Điểm quy đổi`).
   - Chuẩn hóa dấu phẩy/chấm thập phân (VD: `26,75` -> `26.75`), xử lý mã trường viết tắt (VD: `NEU` tự liên kết với mã tuyển sinh `KHA`).
   - Thuật toán **hợp nhất dữ liệu (Data Merging)**: Ghép thông tin Điểm chuẩn (từ cào online) với Chỉ tiêu và Số hồ sơ/nguyện vọng (từ Đề án tuyển sinh dạng PDF/Word) theo từng ngành học.
   - **Cào Đề án tuyển sinh online**: Lấy thêm **mã ngành**, **phương thức xét tuyển** (THPT / học bạ / ĐGNL / ĐGTD / kết hợp / chứng chỉ quốc tế), **quy chế**, và **bảng điểm quy đổi** (IELTS, TOEFL, SAT, ACT, A-Level, hệ chữ A*/A/B/C,...).
   - **Giao diện web Bootstrap**: Bước 1 danh sách trường → Bước 2 cào dữ liệu → Bước 3 quy đổi điểm phương thức (HSA/TSA/V-ACT/SAT…).
3. **Xuất báo cáo Excel (`tong_hop_tuyen_sinh.xlsx`) chuyên nghiệp**:
   - **Sheet `Tong_Hop_2021_2026`**: Mỗi dòng một ngành; điểm chuẩn tách theo phương thức (THPT, TSA, học bạ, HSA, …) × năm. Multi-level header, Freeze Panes, AutoFilter.
   - **Sheet `Du_Lieu_Chi_Tiet`**: Bảng dữ liệu phẳng (Flat Table) chuẩn cơ sở dữ liệu — gồm Mã ngành, Phương thức, Quy chế, Điểm quy đổi (tóm tắt).
   - **Sheet `Diem_Quy_Doi`**: Chi tiết từng dòng bảng quy đổi / hoán đổi chứng chỉ → điểm xét tuyển.
   - **Sheet `Quy_Che`**: Các đoạn quy chế / quy định xét tuyển trích từ đề án.
   - **Sheet `Huong_Dan_Giai_Thich`**: Bảng giải thích chi tiết ý nghĩa từng chỉ số và nguồn thu thập.

---

## 📂 Cấu Trúc Thư Mục Dự Án

```text
Python-huong-nghiep/
├── config/
│   └── schools.json             # Cấu hình danh sách trường & năm cần thu thập
├── data/
│   ├── input_files/             # Thư mục để người dùng đặt file PDF, Excel, Word vào
│   ├── output/                  # Nơi lưu file kết quả: tong_hop_tuyen_sinh.xlsx
│   └── cache/                   # Cache danh bạ trường để cào dữ liệu nhanh hơn
├── parsers/
│   ├── base_parser.py           # Lớp cơ sở nhận diện tiêu đề cột tiếng Việt
│   ├── excel_parser.py          # Bóc tách file Excel (.xlsx, .xls)
│   ├── pdf_parser.py            # Bóc tách bảng từ file PDF (pdfplumber)
│   └── docx_parser.py           # Bóc tách bảng từ file Word (.docx)
├── crawlers/
│   ├── online_crawler.py        # Cào điểm chuẩn + đề án (mã ngành, PTXT, quy chế, quy đổi)
│   ├── dean_extractor.py        # Bóc tách bảng quy đổi chứng chỉ & quy chế từ HTML đề án
│   ├── school_directory.py      # Lấy / xuất danh sách mã trường ĐH, CĐ, Học viện
│   └── score_conversion_crawler.py  # Cào /quy-doi-diem/ (THPT↔HSA/TSA/V-ACT/SAT…)
├── core/
│   ├── models.py                # Schema dữ liệu AdmissionRecord
│   ├── normalizer.py            # Làm sạch chuỗi, điểm, mã ngành, chuẩn hóa viết tắt trường
│   └── aggregator.py            # Hợp nhất, khử trùng lặp và tính toán bảng ma trận 2021-2025
├── exporter/
│   └── excel_exporter.py        # Định dạng và xuất file Excel cao cấp (openpyxl)
├── web/
│   ├── app.py                   # Flask + Bootstrap UI (danh bạ, cào, quy đổi điểm)
│   ├── templates/               # HTML Bootstrap 5
│   └── static/                  # CSS / JS
├── ui/
│   ├── app.py                   # (tuỳ chọn) giao diện Streamlit cũ
│   └── helpers.py               # Parse / format danh sách mã trường từ text dán
├── main.py                      # Điểm khởi chạy chính (CLI + menu + --mode ui)
├── requirements.txt             # Danh sách thư viện cần thiết
└── README.md                    # Hướng dẫn chi tiết này
```

---

## 🛠️ Hướng Dẫn Cài Đặt

### 1. Cài đặt các thư viện phụ thuộc:
Mở terminal tại thư mục dự án và chạy:
```bash
pip install -r requirements.txt
```

Các thư viện chính bao gồm:
- `pandas`, `openpyxl`: Xử lý dữ liệu bảng và định dạng Excel chuyên nghiệp.
- `pdfplumber`, `pypdf`: Bóc tách bảng biểu và cấu trúc văn bản từ tệp PDF.
- `python-docx`: Đọc dữ liệu bảng biểu từ tài liệu Word (.docx).
- `requests`, `beautifulsoup4`: Thu thập dữ liệu trực tuyến.
- `flask`: Giao diện web Bootstrap.
- `tabulate`: Hiển thị bảng tóm tắt đẹp mắt trên terminal.
- `reportlab`: Hỗ trợ tạo file PDF kiểm thử.

---

## 🖥️ Giao diện web Bootstrap (khuyến nghị)

```bash
pip install -r requirements.txt
python3 main.py --mode ui
# hoặc:
python3 web/app.py
```

Mở trình duyệt tại `http://127.0.0.1:8080` (không dùng cổng 5000 — trên macOS cổng này thường bị AirPlay chiếm):

1. **① Danh sách mã trường** — Tải danh bạ ĐH/CĐ, lọc, **Copy mã** (hoặc chuyển sang bước 2/3).
2. **② Cào dữ liệu** — Dán danh sách mã, chọn năm, cào điểm chuẩn / đề án, tải Excel.
3. **③ Quy đổi điểm** — Cào bảng quy đổi phương thức từ trang
   [`/quy-doi-diem/`](https://diemthi.tuyensinh247.com/quy-doi-diem/dai-hoc-kinh-te-quoc-dan-KHA.html)
   (THPT ↔ HSA / TSA / V-ACT / SAT / học bạ / xét kết hợp; ảnh bảng nếu trường chỉ đăng ảnh).

> Giao diện Streamlit cũ vẫn còn tại `ui/app.py` (`streamlit run ui/app.py`) nếu cần.

---

## ⚙️ Cấu Hình Dữ Liệu Đầu Vào

Hệ thống hỗ trợ 2 dạng đầu vào (có thể dùng riêng lẻ hoặc kết hợp cả hai):

### Dạng 0: Lấy danh sách mã trường ĐH / CĐ (khuyến nghị trước khi cào toàn bộ)
```bash
python3 main.py --mode schools --refresh-schools
```
- Xuất `data/output/danh_sach_ma_truong.xlsx` và `.json`
- Đồng thời ghi `config/schools_all.json` để dùng làm đầu vào cào
- Lọc loại hình: `--school-filter all|dai_hoc|cao_dang|hoc_vien|dai_hoc_hoc_vien`

### Dạng 1: Cung cấp danh sách Mã trường / Tên trường
- Bạn có thể chỉnh sửa file `config/schools.json` để thêm bớt các trường muốn theo dõi
- Hoặc truyền trực tiếp: `--schools BKA,NEU,FTU`
- **Cào tất cả trường** (dùng danh bạ đã lấy):
  ```bash
  python3 main.py --mode online --schools all
  python3 main.py --mode online --schools all-dh          # chỉ ĐH + Học viện
  python3 main.py --mode online --schools all-cd          # chỉ Cao đẳng
  python3 main.py --mode online --schools @config/schools_all.json
  python3 main.py --mode online --schools all --limit 5   # thử 5 trường trước
  ```

### Dạng 2: Cung cấp file tài liệu có sẵn (PDF, Excel, Word)
- Copy/Paste các file bạn tải về từ website trường (Đề án tuyển sinh, Thông báo điểm chuẩn, file Thống kê tuyển sinh) vào thư mục:
  `data/input_files/`
- **Quy tắc đặt tên file (khuyến khích)**: Đặt tên chứa Mã trường và Năm tuyển sinh để hệ thống tự nhận diện chính xác nhất.
  - Ví dụ: `BKA_DeAnTuyenSinh_2023.xlsx`, `NEU_ThongBaoDiem_2024.docx`, `QHI_DiemChuan_2023.pdf`.
- **Cấu trúc bảng trong file**: Hệ thống tự động quét dòng tiêu đề có chứa các từ khóa như *Mã ngành/Mã XT*, *Tên ngành*, *Tổ hợp*, *Chỉ tiêu*, *Số hồ sơ / Nguyện vọng*, *Điểm trúng tuyển / Điểm chuẩn*.

---

## 🚀 Hướng Dẫn Sử Dụng

### Cách 1: Sử dụng Menu Tương Tác Trực Quan (Khuyên dùng)
Chỉ cần chạy lệnh:
```bash
python main.py
```
Hệ thống sẽ hiển thị menu:
```text
======================================================================
 CHƯƠNG TRÌNH TỰ ĐỘNG TỔNG HỢP DỮ LIỆU TUYỂN SINH (2021 - 2026)
======================================================================
 [1] Cào dữ liệu trực tuyến theo danh sách mã trường (Online Crawling)
 [2] Bóc tách tài liệu từ thư mục cục bộ (data/input_files: PDF, Excel, Word)
 [3] Tổng hợp kết hợp cả hai nguồn (Online + File cục bộ)
 [4] Tạo file mẫu (Excel, Word, PDF) & Chạy thử nghiệm toàn diện
 [5] Lấy danh sách mã trường ĐH / CĐ / Học viện (phục vụ cào toàn bộ)
 [6] Cào TẤT CẢ trường (lấy danh bạ → cào online)
 [0] Thoát
======================================================================
```

### Cách 2: Chạy Dòng Lệnh với Tham Số (Command Line Arguments)

1. **Chế độ Demo (Tạo file mẫu kiểm thử & cào online ngay lập tức)**:
   ```bash
   python main.py --mode demo --schools BKA,NEU,QHI
   ```

2. **Chế độ chỉ cào dữ liệu trực tuyến**:
   ```bash
   python main.py --mode online --schools BKA,NEU,FTU --years 2021,2022,2023,2024,2025
   ```

3. **Chế độ chỉ bóc tách các file tài liệu trong máy**:
   ```bash
   python main.py --mode local --input-dir data/input_files
   ```

4. **Chế độ kết hợp đầy đủ**:
   ```bash
   python main.py --mode all --schools BKA,NEU --input-dir data/input_files --output data/output/tong_hop_tuyen_sinh.xlsx
   ```

---

## 📊 Cấu Trúc Báo Cáo Excel Đầu Ra

File kết quả được lưu tại: `data/output/tong_hop_tuyen_sinh.xlsx` với các trang tính:

1. **Trang `Tong_Hop_2021_2026`**:
   - Nhóm **THÔNG TIN CHUNG**: Mã trường | Tên trường | Mã ngành | Tên ngành | Tổ hợp | Các PTXT có điểm.
   - Nhóm **CHỈ TIÊU** / **SỐ NV**: theo từng năm.
   - Nhóm **ĐIỂM CHUẨN**: mỗi phương thức xét tuyển một nhóm cột (Điểm thi THPT, ĐGTD TSA, Xét kết hợp, ĐGNL HSA, …), mỗi nhóm có điểm theo năm — một dòng = một ngành, đủ điểm mọi PTXT.
   - Nhóm **XU HƯỚNG**: Điểm TB (theo THPT) và biến động gần nhất.
   - Nhóm **CHỈ TIÊU TUYỂN SINH**: theo từng năm.
   - Nhóm **SỐ LƯỢNG HỒ SƠ / NGUYỆN VỌNG**: theo từng năm.
   - Nhóm **ĐIỂM CHUẨN TRÚNG TUYỂN**: theo từng năm.
   - Nhóm **PHÂN TÍCH XU HƯỚNG**: Điểm trung bình | Biến động gần nhất (+/- điểm).
2. **Trang `Du_Lieu_Chi_Tiet`**:
   - Bảng phẳng: Mã ngành, Phương thức, Quy chế, Điểm quy đổi (tóm tắt), Tỷ lệ chọi, Nguồn dữ liệu.
3. **Trang `Diem_Quy_Doi`**:
   - Chi tiết quy đổi chứng chỉ (VD: IELTS 6.5 → 9.0; SAT 1500–1520 → 19.5; A* → 10).
4. **Trang `Quy_Che`**:
   - Quy định ngoại ngữ, đối tượng xét tuyển, công thức điểm,... từ đề án.
5. **Trang `Huong_Dan_Giai_Thich`**:
   - Thống kê tổng số bản ghi và hướng dẫn ý nghĩa các chỉ số.
