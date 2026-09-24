# -*- coding: utf-8 -*-
"""Đọc bảng quy đổi chứng chỉ dạng ảnh (lưới ô) thành từng mức điểm."""

import os
import re
import tempfile
from typing import Dict, List, Tuple

from PIL import Image

from core.normalizer import clean_text, strip_accents
from crawlers.table_image_parser import ocr_tokens


_CERT_KEYS = (
    "ielts", "toefl", "toeic", "vstep", "aptis", "peic", "pte",
    "linguaskill", "cambridge", "delf", "dalf", "tcf", "testdaf",
    "goethe", "osd", "telc", "dsh", "dsd", "jlpt", "hsk", "topik",
    "sat", "act", "a-level", "a level",
)


def _dark_lines(image: Image.Image, axis: str, at: int) -> List[int]:
    px = image.load()
    width, height = image.size
    span = width if axis == "x" else height
    hits = []
    for pos in range(span):
        if axis == "x":
            r, g, b = px[pos, at]
        else:
            r, g, b = px[at, pos]
        if r < 90 and g < 90 and b < 90:
            hits.append(pos)
    lines: List[int] = []
    if not hits:
        return lines
    start = prev = hits[0]
    for pos in hits[1:]:
        if pos - prev > 3:
            lines.append((start + prev) // 2)
            start = pos
        prev = pos
    lines.append((start + prev) // 2)
    return lines


def _bands(lines: List[int], min_gap: int = 28) -> List[Tuple[int, int]]:
    bands = []
    for left, right in zip(lines, lines[1:]):
        if right - left >= min_gap:
            bands.append((left, right))
    return bands


def _save_crop(image: Image.Image, box: Tuple[int, int, int, int], scale: int = 3) -> str:
    crop = image.crop(box)
    if crop.width < 8 or crop.height < 8:
        return ""
    crop = crop.resize(
        (max(crop.width * scale, 48), max(crop.height * scale, 48)),
        Image.Resampling.LANCZOS,
    )
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        crop.save(tmp.name, quality=95)
        return tmp.name


def _ocr_crop(image: Image.Image, box: Tuple[int, int, int, int], scale: int = 3) -> str:
    path = _save_crop(image, box, scale)
    if not path:
        return ""
    try:
        tokens = ocr_tokens(path)
    finally:
        if os.path.exists(path):
            os.unlink(path)
    ordered = sorted(tokens, key=lambda item: (-item[0], item[1]))
    return clean_text(" ".join(text for _, _, text in ordered))


def _ocr_column(image: Image.Image, box: Tuple[int, int, int, int], bands: List[Tuple[int, int]]) -> List[str]:
    """Đọc một cột dữ liệu, gán chữ vào từng hàng theo đường kẻ ngang."""
    path = _save_crop(image, box, scale=3)
    if not path:
        return [""] * len(bands)
    try:
        tokens = ocr_tokens(path)
    finally:
        if os.path.exists(path):
            os.unlink(path)
    height = max(1, box[3] - box[1])
    grouped = [[] for _ in bands]
    for y, _x, text in tokens:
        abs_y = box[1] + (1.0 - y) * height
        for idx, (y0, y1) in enumerate(bands):
            if y0 <= abs_y < y1 or (idx == len(bands) - 1 and abs_y >= y0):
                grouped[idx].append(text)
                break
    values = [clean_text(" ".join(parts)) for parts in grouped]
    for idx, value in enumerate(values):
        if value:
            continue
        y0, y1 = bands[idx]
        values[idx] = _ocr_crop(image, (box[0], y0, box[2], y1), scale=4)
    return values


def _folded(header: str) -> str:
    return strip_accents(header or "").replace(" ", "")


def _is_score_header(header: str) -> bool:
    folded = _folded(header)
    return "quydoi" in folded and "thuong" not in folded


def _is_bonus_header(header: str) -> bool:
    folded = _folded(header)
    return "thuong" in folded or "uutien" in folded


def _retag_skill_headers(headers: List[str]) -> List[str]:
    """Cột Nghe/Nói/Đọc/Viết dưới nhóm TOEIC giữ tên TOEIC + kỹ năng."""
    folded = [strip_accents(header) for header in headers]
    retagged = list(headers)
    skills = (("nghe", "Nghe"), ("noi", "Nói"), ("doc", "Đọc"), ("dog", "Đọc"), ("viet", "Viết"))
    for idx, text in enumerate(folded):
        compact = text.replace(" ", "")
        skill = next((label for key, label in skills if key in compact), "")
        if not skill:
            continue
        window = "".join(folded[max(0, idx - 3): idx + 4]).replace(" ", "")
        if "toeic" in window or "eic" in window:
            retagged[idx] = f"TOEIC {skill}"
    return retagged


def _is_level_index(values: List[str]) -> bool:
    nums = []
    for value in values:
        text = (value or "").strip()
        if not text:
            continue
        if not re.fullmatch(r"\d{1,2}", text):
            return False
        nums.append(int(text))
    return bool(nums) and max(nums) <= max(10, len(values))


def _is_cert_header(header: str) -> bool:
    folded = strip_accents(header)
    if not folded or _is_score_header(header) or _is_bonus_header(header):
        return False
    if folded in {"tt", "stt", "muc"}:
        return False
    return any(key in folded for key in _CERT_KEYS)


def rows_from_certificate_image(path: str) -> List[Dict[str, str]]:
    """
    Mỗi chứng chỉ trên một dòng của bảng ảnh thành một mức:
    hang_muc (ví dụ IELTS 6.5) → diem_quy_doi (cột điểm quy đổi).
    """
    image = Image.open(path).convert("RGB")
    width, height = image.size
    vertical = _dark_lines(image, "x", int(height * 0.45))
    horizontal = _dark_lines(image, "y", max(8, int(width * 0.12)))
    columns = _bands(vertical, min_gap=36)
    rows = _bands(horizontal, min_gap=36)
    if len(columns) < 3 or len(rows) < 3:
        return []

    headers = []
    for x0, x1 in columns:
        headers.append(_ocr_crop(image, (x0 + 2, rows[0][0] + 2, x1 - 2, rows[0][1] - 2), scale=3))
    headers = _retag_skill_headers(headers)
    score_idx = next((i for i, header in enumerate(headers) if _is_score_header(header)), -1)
    cert_indexes = [i for i, header in enumerate(headers) if _is_cert_header(header)]
    if score_idx < 0 or not cert_indexes:
        return []

    data_rows = rows[1:]
    columns_needed = sorted(set(cert_indexes + [score_idx] + [
        i for i, header in enumerate(headers) if _is_bonus_header(header)
    ]))
    y_top = data_rows[0][0]
    y_bottom = data_rows[-1][1]
    grid: Dict[int, List[str]] = {}
    for col in columns_needed:
        x0, x1 = columns[col]
        grid[col] = _ocr_column(image, (x0 + 2, y_top, x1 - 2, y_bottom), data_rows)

    score_header = headers[score_idx]
    thang = "10" if "10" in score_header else ""
    # Cột "Điểm thưởng" đôi khi OCR đúng tiêu đề nhưng ô chỉ chứa số thứ tự mức 1–5.
    level_cols = {
        col for col, header in enumerate(headers)
        if _is_bonus_header(header) and _is_level_index(grid.get(col, []))
    }
    records = []
    n_rows = len(data_rows)
    for row_i in range(n_rows):
        score = clean_text(grid.get(score_idx, [""] * n_rows)[row_i])
        if not score:
            continue
        detail_bits = []
        for col, header in enumerate(headers):
            if col == score_idx or col in level_cols or col not in grid:
                continue
            value = clean_text(grid[col][row_i])
            if value and header:
                detail_bits.append(f"{header}={value}")
        detail = "; ".join(detail_bits)[:500]
        for col in cert_indexes:
            hang = clean_text(grid.get(col, [""] * n_rows)[row_i])
            if not hang or hang in {"-", "–", "—"}:
                continue
            records.append({
                "loai_bang": headers[col][:80],
                "hang_muc": hang[:80],
                "diem_quy_doi": score[:40],
                "chi_tiet_hang": detail,
                "thang_diem": thang,
                "phuong_thuc": "Quy đổi tương đương chứng chỉ ngoại ngữ",
            })
    return records
