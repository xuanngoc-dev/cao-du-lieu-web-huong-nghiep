# -*- coding: utf-8 -*-
"""
Module: parsers.base_parser
Mô tả: Lớp cơ sở (BaseParser) chứa thuật toán nhận diện cột thông minh dựa trên từ khóa tiếng Việt.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any
import unicodedata
import re
from core.models import AdmissionRecord
from core.normalizer import clean_text


class BaseParser(ABC):
    """Lớp trừu tượng cho tất cả các parser đọc tài liệu (Excel, PDF, Word)."""

    # Danh sách từ khóa nhận diện các cột dữ liệu phổ biến trong đề án tuyển sinh
    COLUMN_PATTERNS = {
        "ma_nganh": [
            r"mã\s*ngành", r"ma\s*nganh", r"mã\s*xét\s*tuyển", r"ma\s*xet\s*tuyen", 
            r"mã\s*xt", r"ma\s*xt", r"mã\s*ctđt", r"ma\s*ctdt", 
            r"mã\s*chương\s*trình", r"ma\s*chuong\s*trinh", r"mã\s*nhóm\s*ngành",
            r"mã\s*tuyển\s*sinh", r"ma\s*tuyen\s*sinh",
            r"^mã$", r"^ma$",
        ],
        "ten_nganh": [
            r"tên\s*ngành", r"ten\s*nganh", r"ngành\s*đào\s*tạo", r"nganh\s*dao\s*tao", 
            r"chuyên\s*ngành", r"chuyen\s*nganh", r"tên\s*chương\s*trình", r"ngành\s*học", r"nganh\s*hoc",
            r"ngành\s*/\s*chương\s*trình", r"nganh\s*/\s*chuong\s*trinh",
            r"^ngành$", r"^nganh$",
            r"chương\s*trình\s*đào\s*tạo", r"chuong\s*trinh\s*dao\s*tao",
            r"tên\s*mã\s*xét", r"ten\s*ma\s*xet",
        ],
        "to_hop": [
            r"tổ\s*hợp", r"to\s*hop", r"tổ\s*hợp\s*môn", r"to\s*hop\s*mon", 
            r"khối\s*thi", r"khoi\s*thi", r"khối\s*xét\s*tuyển", r"tổ\s*hợp\s*xt"
        ],
        "chi_tieu": [
            r"chỉ\s*tiêu", r"chi\s*tieu", r"ctts"
        ],
        "so_nguyen_vong": [
            r"số\s*nguyện\s*vọng", r"so\s*nguyen\s*vong", r"số\s*lượng\s*đk", r"so\s*luong\s*dk", 
            r"số\s*hồ\s*sơ", r"so\s*ho\s*so", r"hồ\s*sơ\s*đăng\s*ký", r"nv1", r"nguyện\s*vọng\s*1", 
            r"tổng\s*số\s*nv", r"số\s*thí\s*sinh\s*đk"
        ],
        "diem_chuan": [
            r"điểm\s*chuẩn", r"diem\s*chuan", r"điểm\s*trúng\s*tuyển", r"diem\s*trung\s*tuyen", 
            r"điểm\s*xét\s*tuyển", r"diem\s*xet\s*tuyen", r"điểm\s*xt", r"diem\s*xt"
        ],
        # Điểm chuẩn theo thang phương thức gốc (cột riêng: Điểm chuẩn ĐGNL HSA / V-ACT / TSA)
        "diem_chuan_ptxt": [
            r"điểm\s*chuẩn\s*đgnl", r"diem\s*chuan\s*dgnl",
            r"điểm\s*chuẩn\s*đgtd", r"diem\s*chuan\s*dgtd",
            r"điểm\s*chuẩn\s*hsa", r"diem\s*chuan\s*hsa",
            r"điểm\s*chuẩn\s*v-?act", r"diem\s*chuan\s*v-?act",
            r"điểm\s*chuẩn\s*tsa", r"diem\s*chuan\s*tsa",
            r"điểm\s*đgnl\s*hsa", r"điểm\s*đgnl\s*v-?act", r"điểm\s*đgtd\s*tsa",
        ],
        "ghi_chu": [
            r"ghi\s*chú", r"ghi\s*chu", r"tiêu\s*chí\s*phụ", r"tieu\s*chi\s*phu", r"lưu\s*ý", r"luu\s*y"
        ],
        "phuong_thuc": [
            r"phương\s*thức", r"phuong\s*thuc", r"hình\s*thức\s*xét", r"hinh\s*thuc\s*xet",
            r"phương\s*thức\s*xt", r"phương\s*thức\s*tuyển\s*sinh"
        ],
        "diem_quy_doi": [
            r"điểm\s*quy\s*đổi", r"diem\s*quy\s*doi", r"quy\s*đổi", r"quy\s*doi",
            r"hoán\s*đổi", r"hoan\s*doi", r"điểm\s*quy\s*ra", r"điểm\s*thưởng"
        ],
        "quy_che": [
            r"quy\s*chế", r"quy\s*che", r"quy\s*định", r"quy\s*dinh",
            r"điều\s*kiện", r"dieu\s*kien", r"đối\s*tượng"
        ],
    }

    @classmethod
    def match_column_name(cls, header_text: Any) -> Optional[str]:
        """
        Nhận diện xem tiêu đề một cột tương ứng với trường thông tin nào.
        Ví dụ: 'Mã xét tuyển' -> 'ma_nganh', 'Điểm trúng tuyển 2023' -> 'diem_chuan'.
        """
        if not header_text:
            return None
        text = clean_text(header_text).lower()
        text_normalized = unicodedata.normalize("NFC", text)
        nfd = unicodedata.normalize("NFD", text)
        text_no_accents = "".join(c for c in nfd if unicodedata.category(c) != "Mn").replace("đ", "d").replace("Đ", "D").lower()

        # Ưu tiên cột điểm chuẩn theo thang phương thức (HSA/V-ACT/TSA/…)
        if re.search(
            r"diem\s*chuan.*(dgnl|dgtd|hsa|v-?act|tsa)|diem\s*(dgnl|dgtd)\s*(hsa|v-?act|tsa)?",
            text_no_accents,
            re.IGNORECASE,
        ):
            return "diem_chuan_ptxt"

        for field_name, patterns in cls.COLUMN_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_normalized, re.IGNORECASE) or re.search(pattern, text_no_accents, re.IGNORECASE):
                    # Tránh gán nhầm cột "Điểm chuẩn ĐGNL HSA" vào diem_chuan thường
                    if field_name == "diem_chuan" and re.search(
                        r"(dgnl|dgtd|hsa|v-?act|tsa)", text_no_accents, re.IGNORECASE
                    ):
                        continue
                    return field_name
        return None

    @classmethod
    def map_table_headers(cls, headers: List[Any]) -> Dict[int, str]:
        """
        Tạo ánh xạ từ vị trí index cột (0, 1, 2,...) sang tên trường dữ liệu chuẩn.
        """
        column_map = {}
        for idx, col in enumerate(headers):
            matched = cls.match_column_name(col)
            if matched and matched not in column_map.values():
                column_map[idx] = matched
        return column_map

    @abstractmethod
    def parse(self, file_path: str, **kwargs) -> List[AdmissionRecord]:
        """Phương thức bóc tách dữ liệu từ file ra danh sách bản ghi tuyển sinh."""
        pass
