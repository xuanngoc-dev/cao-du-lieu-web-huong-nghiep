# -*- coding: utf-8 -*-
"""Đọc bảng điểm trong ảnh (OCR Vision trên macOS) thành các dòng khoảng điểm."""

import os
import re
import subprocess
from typing import Dict, List, Optional, Tuple

from core.normalizer import clean_text, strip_accents

_SWIFT = os.path.join(os.path.dirname(__file__), "ocr_vision.swift")
_RANGE_RE = re.compile(r"\d+(?:[.,]\d+)?\s*[-–—]\s*\d+(?:[.,]\d+)?")
_METHOD_PATTERNS = (
    (re.compile(r"tn\s*thpt|diem\s*tn|tot\s*nghiep|thang\s*30|\bthpt\b"), "Điểm THPT"),
    (re.compile(r"\bhsa\b"), "HSA"),
    (re.compile(r"\bsat\b"), "SAT"),
    (re.compile(r"v\s*-?\s*act|\bvact\b"), "V-ACT"),
    (re.compile(r"\btsa\b|dgtd|tu\s*duy"), "TSA"),
    (re.compile(r"xttn\s*(dien\s*)?1\.2|dien\s*1\.2"), "XTTN diện 1.2"),
    (re.compile(r"xttn\s*(dien\s*)?1\.3|dien\s*1\.3"), "XTTN diện 1.3"),
    (re.compile(r"xttn|tai\s*nang"), "XTTN"),
)


def ocr_tokens(image_path: str) -> List[Tuple[float, float, str]]:
    """Trả về (y, x, text), y lớn hơn là phía trên ảnh. Rỗng nếu máy không có Vision."""
    if not os.path.isfile(image_path) or not os.path.isfile(_SWIFT):
        return []
    try:
        proc = subprocess.run(
            ["swift", _SWIFT, image_path],
            capture_output=True,
            text=True,
            timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    tokens: List[Tuple[float, float, str]] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        try:
            y = float(parts[0])
            x = float(parts[1])
        except ValueError:
            continue
        text = clean_text(parts[2])
        if text:
            tokens.append((y, x, text))
    return tokens


def _cluster_rows(tokens: List[Tuple[float, float, str]], gap: float = 0.045) -> List[List[Tuple[float, str]]]:
    ordered = sorted(tokens, key=lambda item: (-item[0], item[1]))
    rows: List[List[Tuple[float, str]]] = []
    bucket: List[Tuple[float, str]] = []
    anchor: Optional[float] = None
    for y, x, text in ordered:
        if anchor is None or abs(y - anchor) <= gap:
            bucket.append((x, text))
            if anchor is None:
                anchor = y
        else:
            rows.append(sorted(bucket, key=lambda item: item[0]))
            bucket = [(x, text)]
            anchor = y
    if bucket:
        rows.append(sorted(bucket, key=lambda item: item[0]))
    return rows


def _is_range(text: str) -> bool:
    return bool(_RANGE_RE.search(text or ""))


def _methods_in_text(text: str) -> List[str]:
    folded = strip_accents(text or "")
    found: List[Tuple[int, str]] = []
    for pattern, label in _METHOD_PATTERNS:
        match = pattern.search(folded)
        if match:
            found.append((match.start(), label))
    found.sort(key=lambda item: item[0])
    labels: List[str] = []
    for _, label in found:
        if label not in labels:
            labels.append(label)
    return labels


def _header_name(parts: List[str]) -> str:
    text = clean_text(" ".join(parts))
    folded = strip_accents(text)
    # "Điểm kết quả học tập THPT" là học bạ, không phải điểm thi THPT.
    if "ket qua hoc tap" in folded or "hoc ba" in folded:
        return text
    # Cột gộp nhiều kỳ thi phải được giữ nguyên, không gán nhầm thành TSA.
    if "dgnl" in folded and ("dgtd" in folded or "tu duy" in folded):
        return text
    methods = _methods_in_text(text)
    if methods:
        return methods[0]
    if folded in {"tt", "stt"}:
        return ""
    return text


def _expand_named_columns(columns: List[Tuple[float, List[str]]]) -> List[Tuple[float, str]]:
    """Tách tiêu đề OCR gộp (vd. 'Điểm TN THPT Điểm HSA Điểm SAT…') thành từng cột phương thức."""
    named: List[Tuple[float, str]] = []
    for x, parts in columns:
        methods = _methods_in_text(" ".join(parts))
        if len(methods) >= 2:
            # Chưa biết bề rộng blob — tạm trải đều; sẽ neo lại theo tọa độ dữ liệu.
            for idx, label in enumerate(methods):
                named.append((x + idx * 0.001, label))
            continue
        label = _header_name(parts)
        if label:
            named.append((x, label))
    return named


def _realign_columns_by_data(
    named: List[Tuple[float, str]],
    data_rows: List[List[Tuple[float, str]]],
) -> List[Tuple[float, str]]:
    """Gán lại tọa độ cột theo trung vị x của các khoảng điểm trên từng vị trí."""
    if len(named) < 2:
        return named
    labels = [name for _, name in named]
    buckets: List[List[float]] = [[] for _ in labels]
    for row in data_rows:
        xs = sorted(x for x, text in row if _is_range(text))
        if len(xs) < 2:
            continue
        if len(xs) == len(labels):
            for idx, x in enumerate(xs):
                buckets[idx].append(x)
        elif len(xs) > len(labels):
            # bỏ cột STT số đơn nếu có
            for idx, x in enumerate(xs[-len(labels):]):
                buckets[idx].append(x)
    realigned = []
    for idx, label in enumerate(labels):
        if buckets[idx]:
            xs = sorted(buckets[idx])
            mid = xs[len(xs) // 2]
            realigned.append((mid, label))
        else:
            realigned.append(named[idx])
    return realigned


def _table_title(headers: List[str], caption: str) -> str:
    blob = strip_accents(" ".join(headers) + " " + (caption or ""))
    block = ""
    for code in ("A00", "A01", "D01", "D07", "D04", "DD2", "B00", "K01"):
        if code.lower() in blob or code in (caption or "") or code in " ".join(headers):
            block = code
            break
    if not block and "d01" in blob:
        block = "D01"
    if block:
        return f"Công cụ quy đổi BPV — {block} · Tổ hợp gốc {block}"
    cap = clean_text(caption) or "Bảng quy đổi"
    return f"Công cụ quy đổi BPV — {cap[:80]}"


def equivalence_from_tokens(
    tokens: List[Tuple[float, float, str]],
    caption: str = "",
) -> Tuple[str, List[Dict[str, str]]]:
    """
    Ghép token OCR thành các dòng {cột: khoảng điểm}.
    Trả về (tiêu đề bảng, danh sách dòng).
    """
    rows = _cluster_rows(tokens)
    if len(rows) < 2:
        return "", []

    header_rows: List[List[Tuple[float, str]]] = []
    data_rows: List[List[Tuple[float, str]]] = []
    seen_data = False
    for row in rows:
        texts = [text for _, text in row]
        if any(_is_range(text) for text in texts) or any(
            strip_accents(text).startswith("khoang") for text in texts
        ):
            seen_data = True
            data_rows.append(row)
        elif not seen_data:
            header_rows.append(row)
    if not header_rows or not data_rows:
        return "", []

    columns: List[Tuple[float, List[str]]] = []
    for row in header_rows:
        for x, text in row:
            placed = False
            for idx, (cx, parts) in enumerate(columns):
                if abs(x - cx) <= 0.08:
                    count = len(parts) + 1
                    columns[idx] = ((cx * (count - 1) + x) / count, parts + [text])
                    placed = True
                    break
            if not placed:
                columns.append((x, [text]))
    columns.sort(key=lambda item: item[0])
    named = _expand_named_columns(columns)
    named = _realign_columns_by_data(named, data_rows)
    if len(named) < 2:
        return "", []

    parsed: List[Dict[str, str]] = []
    for row in data_rows:
        values: Dict[str, str] = {}
        label = ""
        range_cells = [(x, text) for x, text in row if _is_range(text)]
        if len(range_cells) >= len(named) and len(named) >= 2:
            # Khớp trái→phải theo số cột phương thức (ổn định hơn nearest-x khi tiêu đề bị gộp).
            ordered = sorted(range_cells, key=lambda item: item[0])[-len(named):]
            for (x, text), (_cx, name) in zip(ordered, named):
                values[name] = text
        else:
            for x, text in row:
                folded = strip_accents(text)
                if folded.startswith("khoang"):
                    label = text
                    continue
                if not _is_range(text):
                    continue
                nearest = min(named, key=lambda col: abs(col[0] - x))
                if abs(nearest[0] - x) > 0.14:
                    continue
                values[nearest[1]] = text
        range_count = sum(1 for value in values.values() if _is_range(value))
        if range_count < 2:
            continue
        if label:
            values["__stt"] = label
        parsed.append(values)
    if not parsed:
        return "", []
    raw_headers = [" ".join(parts) for _, parts in columns]
    return _table_title(raw_headers, caption), parsed


def scalar_conversion_from_tokens(
    tokens: List[Tuple[float, float, str]],
    caption: str = "",
) -> Tuple[str, List[Dict[str, str]]]:
    """Đọc bảng một khoảng điểm nguồn → một điểm quy đổi (ví dụ SAT → thang 20)."""
    rows = _cluster_rows(tokens)
    if len(rows) < 2:
        return "", []
    header = clean_text(" ".join(text for _, text in rows[0]))
    folded = strip_accents(header)
    source = "SAT" if "sat" in folded else ""
    scale_match = re.search(r"thang\s*diem\s*(\d+(?:[.,]\d+)?)", folded)
    target = f"Điểm quy đổi (thang {scale_match.group(1)})" if scale_match else "Điểm quy đổi"
    if not source or "quy doi" not in folded:
        return "", []

    number_re = re.compile(r"^\d+(?:[.,]\d+)?$")
    parsed: List[Dict[str, str]] = []
    pending_range = ""
    for row in rows[1:]:
        range_value = next(
            (_RANGE_RE.search(text).group(0) for _, text in row if _RANGE_RE.search(text)),
            "",
        )
        score_candidates = [
            (x, text)
            for x, text in row
            if number_re.fullmatch(text.strip()) and x > 0.35
        ]
        score = score_candidates[0][1] if score_candidates else ""
        if range_value:
            pending_range = range_value
        if pending_range and score:
            parsed.append({source: pending_range, target: score})
            pending_range = ""
    if not parsed:
        return "", []
    title = clean_text(caption) or f"Bảng quy đổi {source}"
    return title, parsed
