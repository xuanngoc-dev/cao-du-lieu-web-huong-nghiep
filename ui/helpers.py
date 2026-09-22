# -*- coding: utf-8 -*-
"""
Module: ui.helpers
Tiện ích dùng chung cho giao diện web (parse mã trường, format danh sách).
"""

import re
from typing import List


def parse_school_codes_text(text: str) -> List[str]:
    """
    Phân tích chuỗi người dùng dán/nhập thành danh sách mã trường.
    Hỗ trợ:
      - BKA,NEU,FTU
      - Mỗi mã một dòng
      - Cách nhau bởi khoảng trắng / ; / |
      - Dòng dạng "BKA - Đại học Bách khoa Hà Nội" (lấy mã đầu)
    """
    if not text or not str(text).strip():
        return []

    raw = str(text).replace("\r\n", "\n").replace("\r", "\n")
    # Tách theo dòng trước, rồi theo dấu phân cách
    tokens: List[str] = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Bỏ số thứ tự đầu dòng: "1. BKA" / "1) BKA" / "1\tBKA"
        line = re.sub(r"^\d+[\.\)\-\:\t ]+\s*", "", line)

        # Dạng "MÃ - Tên trường" / "MÃ: Tên" (không phải danh sách nhiều mã)
        m_named = re.match(
            r"^([A-Za-z0-9]{2,10})\s*[-–—:]\s+\S+",
            line,
        )
        if m_named and "," not in line and ";" not in line:
            tokens.append(m_named.group(1))
            continue

        # Tách theo dấu phẩy / chấm phẩy / pipe / tab
        parts = re.split(r"[,;\|\t]+", line)
        for p in parts:
            p = p.strip()
            if not p:
                continue
            # Có thể còn nhiều mã cách nhau bởi space
            for piece in re.split(r"\s+", p):
                piece = piece.strip().strip("()[]{}")
                if piece:
                    tokens.append(piece)

    codes: List[str] = []
    seen = set()
    for tok in tokens:
        # Chỉ giữ token giống mã trường (2–10 ký tự chữ/số)
        if not re.fullmatch(r"[A-Za-z0-9]{2,10}", tok):
            continue
        code = tok.upper()
        if code in seen:
            continue
        # Bỏ từ khóa nhiễu
        if code in {"STT", "MA", "CODE", "TYPE", "NAME", "ALL"}:
            continue
        seen.add(code)
        codes.append(code)
    return codes


def format_codes_for_copy(codes: List[str], one_per_line: bool = True) -> str:
    """Chuỗi mã trường sẵn sàng để copy."""
    if not codes:
        return ""
    if one_per_line:
        return "\n".join(codes)
    return ",".join(codes)
