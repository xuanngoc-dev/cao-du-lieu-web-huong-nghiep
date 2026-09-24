# -*- coding: utf-8 -*-
"""
Module: core.models
Mô tả: Định nghĩa cấu trúc dữ liệu chuẩn cho các bản ghi tuyển sinh đại học.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List


@dataclass
class AdmissionRecord:
    """
    Bản ghi dữ liệu tuyển sinh của một ngành trong một năm học cụ thể.
    """
    ma_truong: str                          # Mã trường (VD: BKA, NEU, QHI)
    ten_truong: str                         # Tên trường (VD: Đại học Bách khoa Hà Nội)
    ma_nganh: str                           # Mã ngành tuyển sinh (VD: 7480201 hoặc IT1)
    ten_nganh: str                          # Tên ngành học (VD: Công nghệ thông tin)
    nam: int                                # Năm tuyển sinh (2021 - 2026)
    to_hop: str = ""                        # Tổ hợp môn xét tuyển (VD: A00, A01, D01)
    chi_tieu: Optional[int] = None          # Chỉ tiêu tuyển sinh
    so_nguyen_vong: Optional[int] = None    # Số lượng hồ sơ/nguyện vọng đăng ký
    diem_chuan: Optional[float] = None      # Điểm chuẩn (thường thang THPT / điểm quy đổi chung)
    diem_chuan_ptxt: Optional[float] = None # Điểm chuẩn theo thang phương thức gốc (HSA/V-ACT/TSA/…)
    thang_diem: float = 30.0                # Thang điểm (thường là 30, hoặc 40 nếu có môn nhân hệ số)
    phuong_thuc: str = "Điểm thi THPT"      # Phương thức xét tuyển (ĐGNL HSA, ĐGTD TSA,...)
    quy_che: str = ""                       # Tóm tắt quy chế / điều kiện xét tuyển liên quan
    diem_quy_doi: str = ""                  # Tóm tắt bảng quy đổi chứng chỉ áp dụng (nếu có)
    ghi_chu: str = ""                       # Tiêu chí phụ hoặc ghi chú thêm
    nguon: str = "Tài liệu cục bộ"          # Nguồn dữ liệu (Online / Tên file)

    def to_dict(self) -> Dict[str, Any]:
        """Chuyển đổi bản ghi sang định dạng Dictionary."""
        return asdict(self)

    @property
    def ty_le_choi(self) -> Optional[float]:
        """
        Tính toán tỷ lệ chọi (Số nguyện vọng / Chỉ tiêu).
        Trả về None nếu thiếu 1 trong 2 thông tin.
        """
        if self.so_nguyen_vong is not None and self.chi_tieu and self.chi_tieu > 0:
            return round(self.so_nguyen_vong / self.chi_tieu, 2)
        return None


@dataclass
class ScoreConversionRecord:
    """
    Một dòng trong bảng quy đổi / hoán đổi điểm chứng chỉ (IELTS, SAT, A-Level,...).
    Ví dụ: IELTS 6.5 → TOEFL 79-93 → Điểm quy đổi 9.0
    """
    ma_truong: str
    ten_truong: str
    loai_bang: str                          # VD: "IELTS/TOEFL/TOEIC", "SAT", "ACT", "A-Level"
    hang_muc: str                           # Giá trị chứng chỉ / mức điểm (VD: "6.5", "A*", "1380-1390")
    diem_quy_doi: str                       # Điểm được quy ra
    chi_tiet_hang: str = ""                 # Toàn bộ dòng bảng (các cột phụ dạng key=value; ...)
    phuong_thuc: str = ""                   # Phương thức áp dụng (nếu nhận diện được)
    thang_diem: str = ""                    # Thang điểm đích (10 / 20 / 30 / 40)
    nam: Optional[int] = None
    ghi_chu: str = ""
    nguon: str = "Online: Đề án tuyển sinh"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AdmissionRegulation:
    """
    Quy chế / quy định xét tuyển trích từ đề án (ngoại ngữ, đối tượng, công thức điểm,...).
    """
    ma_truong: str
    ten_truong: str
    tieu_de: str
    noi_dung: str
    phuong_thuc: str = ""
    nam: Optional[int] = None
    nguon: str = "Online: Đề án tuyển sinh"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MethodEquivalenceRow:
    """
    Một dòng trong bảng quy đổi tương đương giữa các phương thức
    (THPT ↔ HSA ↔ TSA ↔ V-ACT ↔ SAT ↔ học bạ / kết hợp,...).
    Ví dụ NEU: THPT 26–28 ↔ HSA 98–112 ↔ SAT 1500–1580 ↔ V-ACT 882–1004 ↔ TSA 66.19–77.90
    """
    ma_truong: str
    ten_truong: str
    tieu_de_bang: str                       # Tiêu đề bảng / section
    stt: str = ""
    cot_gia_tri: Dict[str, str] = field(default_factory=dict)  # {"Điểm TN THPT":"26–28","Điểm HSA":"98–112",...}
    nam: Optional[int] = None
    url_nguon: str = ""
    nguon: str = "Online: Quy đổi điểm"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class MethodConversionNote:
    """Đoạn mô tả / công thức / ghi chú quy đổi (chênh lệch tổ hợp, hướng dẫn,...)."""
    ma_truong: str
    ten_truong: str
    tieu_de: str
    noi_dung: str
    nam: Optional[int] = None
    url_nguon: str = ""
    nguon: str = "Online: Quy đổi điểm"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MethodConversionImage:
    """Ảnh bảng quy đổi (một số trường chỉ đăng ảnh, VD: BKA)."""
    ma_truong: str
    ten_truong: str
    url_anh: str
    mo_ta: str = ""
    nam: Optional[int] = None
    url_nguon: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MethodRangeHint:
    """Khoảng điểm đầu vào của công cụ quy đổi (VD: HSA 85-150, SAT 1200-1600)."""
    ma_truong: str
    ten_truong: str
    phuong_thuc: str
    khoang_diem: str
    nam: Optional[int] = None
    url_nguon: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MethodConversionBundle:
    """Gói kết quả trang /quy-doi-diem/ của một hoặc nhiều trường."""
    rows: List[MethodEquivalenceRow] = field(default_factory=list)
    notes: List[MethodConversionNote] = field(default_factory=list)
    images: List[MethodConversionImage] = field(default_factory=list)
    ranges: List[MethodRangeHint] = field(default_factory=list)
    conversions: List["ScoreConversionRecord"] = field(default_factory=list)
    school_results: List[Dict[str, Any]] = field(default_factory=list)  # meta từng trường


@dataclass
class CrawlBundle:
    """Gói kết quả thu thập: điểm chuẩn + bảng quy đổi + quy chế."""
    admissions: List[AdmissionRecord] = field(default_factory=list)
    conversions: List[ScoreConversionRecord] = field(default_factory=list)
    regulations: List[AdmissionRegulation] = field(default_factory=list)
    source_note: str = ""
