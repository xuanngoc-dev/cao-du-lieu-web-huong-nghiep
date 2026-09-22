# -*- coding: utf-8 -*-
"""
Module: core.normalizer
Mô tả: Các hàm tiện ích làm sạch, chuẩn hóa dữ liệu tuyển sinh (mã ngành, tên ngành, điểm số, chỉ tiêu).
"""

import re
import unicodedata
from typing import Optional, Union, Any


def clean_text(text: Any) -> str:
    """Loại bỏ khoảng trắng thừa, ký tự ẩn và chuẩn hóa unicode."""
    if text is None:
        return ""
    text_str = str(text).strip()
    # Chuẩn hóa Unicode sang dạng NFC (chuẩn tiếng Việt thông dụng)
    text_str = unicodedata.normalize("NFC", text_str)
    # Thay thế nhiều khoảng trắng liền nhau thành 1 khoảng trắng
    text_str = re.sub(r"\s+", " ", text_str)
    return text_str


def strip_accents(s: Any) -> str:
    """Loại bỏ hoàn toàn dấu tiếng Việt để so khớp không phân biệt dấu."""
    if not s:
        return ""
    cleaned = clean_text(s)
    nfd = unicodedata.normalize("NFD", cleaned)
    no_accent = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return no_accent.replace("đ", "d").replace("Đ", "D").lower()


SCHOOL_ALIASES = {
    "NEU": "KHA",       # ĐH Kinh tế Quốc dân
    "FTU": "NTH",       # ĐH Ngoại thương HN
    "FTU2": "NTS",      # ĐH Ngoại thương TP.HCM
    "HUST": "BKA",      # ĐH Bách khoa Hà Nội
    "HCMUT": "QSB",     # ĐH Bách khoa ĐHQG TP.HCM
    "UET": "QHI",       # ĐH Công nghệ ĐHQGHN
    "ULIS": "QHF",      # ĐH Ngoại ngữ ĐHQGHN
    "USSH": "QHX",      # ĐH KHXH&NV Hà Nội
    "HUS": "QHT",       # ĐH Khoa học Tự nhiên HN
    "AOF": "HTC",       # Học viện Tài chính
    "BA": "NHH",        # Học viện Ngân hàng
    "PTIT": "BVH",      # Học viện Công nghệ Bưu chính Viễn thông HN
    "TMU": "TMU",       # ĐH Thương Mại
    "DAV": "HVA",       # Học viện Ngoại giao
    "AJC": "HCH",       # Học viện Báo chí và Tuyên truyền
    "HNUE": "SPH",      # ĐH Sư phạm Hà Nội
    "HMU": "YHB",       # ĐH Y Hà Nội
    "UEH": "KSA",       # ĐH Kinh tế TP.HCM
    "UIT": "QSC",       # ĐH Công nghệ Thông tin ĐHQG TP.HCM
    "HCMUS": "QST",     # ĐH Khoa học Tự nhiên ĐHQG TP.HCM
}

SCHOOL_NAMES = {
    "BKA": "Đại học Bách khoa Hà Nội",
    "KHA": "Trường Đại học Kinh tế Quốc dân",
    "QHI": "Trường Đại học Công nghệ - ĐHQGHN",
    "NTH": "Trường Đại học Ngoại thương (Hà Nội)",
    "NTS": "Trường Đại học Ngoại thương (Cơ sở II TP.HCM)",
    "QSB": "Trường Đại học Bách khoa - ĐHQG TP.HCM",
    "KSA": "Đại học Kinh tế TP.HCM (UEH)",
    "QSC": "Trường Đại học Công nghệ Thông tin - ĐHQG TP.HCM",
    "QST": "Trường Đại học Khoa học Tự nhiên - ĐHQG TP.HCM",
    "HTC": "Học viện Tài chính",
    "NHH": "Học viện Ngân hàng",
    "BVH": "Học viện Công nghệ Bưu chính Viễn thông",
    "TMU": "Trường Đại học Thương Mại",
    "HVA": "Học viện Ngoại giao",
    "HCH": "Học viện Báo chí và Tuyên truyền",
    "SPH": "Trường Đại học Sư phạm Hà Nội",
    "YHB": "Trường Đại học Y Hà Nội",
}


def normalize_school_code(code: Any) -> str:
    """Chuẩn hóa mã trường: chữ in hoa, không khoảng trắng, ánh xạ từ viết tắt sang mã chuẩn."""
    if not code:
        return ""
    cleaned = str(code).strip().upper()
    return SCHOOL_ALIASES.get(cleaned, cleaned)


def get_school_display_name(code: str, fallback_name: str = "") -> str:
    """Lấy tên đầy đủ chính thức của trường từ mã trường."""
    norm_code = normalize_school_code(code)
    if norm_code in SCHOOL_NAMES:
        return SCHOOL_NAMES[norm_code]
    if fallback_name and fallback_name.upper() != code.upper():
        return fallback_name
    return norm_code


def normalize_major_code(code: Any) -> str:
    """
    Chuẩn hóa mã ngành tuyển sinh.
    Ví dụ: '7.48.02.01' -> '7480201', ' IT1 ' -> 'IT1'.
    """
    if not code:
        return ""
    cleaned = str(code).strip().upper()
    # Nếu là mã chuẩn 7 chữ số có dấu chấm (VD: 7.48.02.01)
    if re.match(r"^7\.\d{2}\.\d{2}\.\d{2}$", cleaned):
        cleaned = cleaned.replace(".", "")
    return cleaned


def normalize_score(score_val: Any, max_score: float = 45.0) -> Optional[float]:
    """
    Chuẩn hóa điểm chuẩn sang kiểu float.
    Xử lý dấu phẩy thập phân kiểu Việt Nam (VD: '26,50' -> 26.5),
    loại bỏ các chữ kèm theo (VD: '25.5 điểm' -> 25.5).

    max_score: ngưỡng hợp lệ. Mặc định 45 (thang THPT 30/40).
    Dùng max_score cao hơn cho điểm theo thang PTXT (HSA ~150, V-ACT ~1200, SAT ~1600).
    """
    if score_val is None:
        return None
    val_str = str(score_val).strip()
    if not val_str or val_str.lower() in ["-", "n/a", "none", "chưa có", "đang cập nhật"]:
        return None

    # Tìm số thực trong chuỗi (ví dụ: '27.25' hoặc '27,25')
    match = re.search(r"(\d+([.,]\d+)?)", val_str)
    if match:
        raw_num = match.group(1).replace(",", ".")
        try:
            score = float(raw_num)
            if 0.0 <= score <= max_score:
                return round(score, 2)
        except ValueError:
            return None
    return None


def normalize_score_ptxt(score_val: Any) -> Optional[float]:
    """Điểm chuẩn theo thang phương thức gốc (HSA/V-ACT/TSA/SAT…)."""
    return normalize_score(score_val, max_score=1600.0)


def normalize_integer(int_val: Any) -> Optional[int]:
    """
    Chuẩn hóa chỉ tiêu tuyển sinh hoặc số lượng nguyện vọng/hồ sơ.
    Xử lý các dấu phân cách hàng nghìn (VD: '1.200' hoặc '1,500' -> 1200 / 1500).
    """
    if int_val is None:
        return None
    val_str = str(int_val).strip()
    if not val_str or val_str.lower() in ["-", "n/a", "none", "chưa có"]:
        return None

    # Loại bỏ dấu phân cách hàng nghìn kiểu '1,200' hoặc '1.200'
    val_str = re.sub(r"[,\.](\d{3})", r"\1", val_str)
    match = re.search(r"\d+", val_str)
    if match:
        try:
            return int(match.group(0))
        except ValueError:
            return None
    return None


def extract_year_from_text(text: str) -> Optional[int]:
    """Tìm năm từ chuỗi văn bản (ví dụ: 'Điểm chuẩn 2026' -> 2026)."""
    match = re.search(r"\b(202[0-9])\b", text)
    if match:
        return int(match.group(1))
    return None
