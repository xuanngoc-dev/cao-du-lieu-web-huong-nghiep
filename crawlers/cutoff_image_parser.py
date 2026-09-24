# -*- coding: utf-8 -*-
"""Đọc bảng điểm chuẩn trong ảnh (OCR) thành bản ghi theo ngành và phương thức."""

import os
import re
import tempfile
from typing import List, Optional, Tuple

from core.models import AdmissionRecord
from core.normalizer import clean_text, normalize_score, normalize_score_ptxt, strip_accents
from crawlers.table_image_parser import _cluster_rows, ocr_tokens

_CODE_RE = re.compile(r"^[A-Z]{2,4}(?:-[A-Z]{1,4}\d{0,2}|\d{1,2})$")
_BLOCK_RE = re.compile(
    r"^(?:A00|A01|B00|C00|D01|D04|D07|DD2|K00|K01)(?:\s*[;,]\s*(?:A00|A01|B00|C00|D01|D04|D07|DD2|K00|K01))*$"
)
_SCORE_RE = re.compile(r"^\d{1,3}(?:[.,]\d{1,2})?$")
_METHOD_RE = re.compile(r"xttn\s*1\.2|xttn\s*1\.3|\bxttn\b|\btsa\b|\bdgtd\b|thpt|tot nghiep")
_METHOD_ID = {
    "xttn 1.2": "XTTN_1.2",
    "xttn 1.3": "XTTN_1.3",
    "xttn": "XTTN",
    "tsa": "TSA",
    "dgtd": "TSA",
    "thpt": "THPT",
    "tot nghiep": "THPT",
}
_METHOD_LABEL = {
    "THPT": "Điểm thi THPT",
    "TSA": "ĐGTD TSA",
    "XTTN": "Xét tuyển tài năng",
    "XTTN_1.2": "Xét tuyển tài năng diện 1.2",
    "XTTN_1.3": "Xét tuyển tài năng diện 1.3",
}


def _methods_in_text(token: str) -> List[str]:
    folded = strip_accents(token)
    found: List[str] = []
    for match in _METHOD_RE.finditer(folded):
        key = re.sub(r"\s+", " ", match.group(0))
        method = _METHOD_ID.get(key)
        if method and method not in found:
            found.append(method)
    return found


def _parse_score(text: str) -> Optional[float]:
    if not _SCORE_RE.match(text or ""):
        return None
    try:
        value = float(text.replace(",", "."))
    except ValueError:
        return None
    if value < 10 or value > 1600:
        return None
    return round(value, 2)


def _is_code(text: str) -> bool:
    if not _CODE_RE.match(text or ""):
        return False
    if _BLOCK_RE.match(text):
        return False
    return True


def _fix_name(text: str) -> str:
    return re.sub(r"\bKỳ thuật\b", "Kỹ thuật", text)


def _cluster_centers(xs: List[float], gap: float = 0.05) -> List[float]:
    ordered = sorted(xs)
    groups: List[List[float]] = []
    for x in ordered:
        if not groups or x - groups[-1][-1] > gap:
            groups.append([x])
        else:
            groups[-1].append(x)
    return [sum(group) / len(group) for group in groups]


def _map_methods(header: List[Tuple[float, str]], score_xs: List[float]) -> List[Tuple[float, str]]:
    ordered: List[str] = []
    for _, text in sorted(header, key=lambda item: item[0]):
        for method in _methods_in_text(text):
            if method not in ordered:
                ordered.append(method)
    centers = _cluster_centers(score_xs)
    if ordered and len(ordered) == len(centers):
        return list(zip(centers, ordered))
    if not ordered:
        if len(centers) == 4:
            ordered = ["THPT", "XTTN_1.2", "XTTN_1.3", "TSA"]
        elif len(centers) == 2:
            ordered = ["TSA", "THPT"]
        elif len(centers) == 1:
            ordered = ["THPT"]
        if len(ordered) == len(centers):
            return list(zip(centers, ordered))
    return []


def _fill_blocks(rows: List[dict]) -> None:
    markers = [(idx, row["block"]) for idx, row in enumerate(rows) if row.get("block")]
    if not markers:
        return
    for k, (idx, block) in enumerate(markers):
        prev_i = markers[k - 1][0] if k else -1
        next_i = markers[k + 1][0] if k + 1 < len(markers) else len(rows)
        start = 0 if prev_i < 0 else (prev_i + idx + 1) // 2
        end = len(rows) if next_i >= len(rows) else (idx + next_i) // 2
        for j in range(start, end):
            if not rows[j].get("block"):
                rows[j]["block"] = block


def cutoff_records_from_tokens(
    tokens: List[Tuple[float, float, str]],
    school_code: str,
    school_name: str,
    year: int,
    source: str,
    method_order: Optional[List[str]] = None,
) -> Tuple[List[AdmissionRecord], List[str]]:
    """Ghép token OCR của một ảnh bảng điểm chuẩn thành bản ghi tuyển sinh."""
    visual_rows = _cluster_rows(tokens, gap=0.012)
    parsed: List[dict] = []
    header: List[Tuple[float, str]] = []
    seen_data = False
    for row in visual_rows:
        code = ""
        name_parts: List[str] = []
        block = ""
        scores: List[Tuple[float, float]] = []
        for x, text in row:
            if _is_code(text) and x < 0.2 and not code:
                code = text
                continue
            score = _parse_score(text)
            if score is not None and x >= 0.45:
                scores.append((x, score))
                continue
            if _BLOCK_RE.match(text.replace(" ", "")) or _BLOCK_RE.match(text):
                block = text.replace(" ", "")
                continue
            if _methods_in_text(text) and not seen_data:
                header.append((x, text))
            if code and x < 0.42 and not text.isdigit():
                name_parts.append(text)
        if code and scores:
            seen_data = True
            parsed.append({
                "code": code,
                "name": _fix_name(clean_text(" ".join(name_parts))),
                "block": block,
                "scores": scores,
            })
    if len(parsed) < 3:
        return [], method_order or []
    deduped: List[dict] = []
    index_of = {}
    for row in parsed:
        prev = index_of.get(row["code"])
        if prev is None:
            index_of[row["code"]] = len(deduped)
            deduped.append(row)
        elif len(row["scores"]) > len(deduped[prev]["scores"]):
            deduped[prev] = row
    parsed = deduped
    _fill_blocks(parsed)
    score_xs = [x for row in parsed for x, _ in row["scores"]]
    columns = _map_methods(header, score_xs)
    if method_order and (not columns or len(columns) != len(method_order)):
        centers = _cluster_centers(score_xs)
        if len(centers) == len(method_order):
            columns = list(zip(centers, method_order))
    if not columns:
        return [], method_order or []
    used_order = [method for _, method in sorted(columns, key=lambda col: col[0])]

    records: List[AdmissionRecord] = []
    methods = used_order
    for row in parsed:
        scores = sorted(row["scores"], key=lambda item: item[0])
        pairs: List[Tuple[float, str]] = []
        if len(scores) == len(methods):
            pairs = [(value, methods[i]) for i, (_, value) in enumerate(scores)]
        else:
            for x, value in scores:
                center, method = min(columns, key=lambda col: abs(col[0] - x))
                if abs(center - x) <= 0.12:
                    pairs.append((value, method))
        for value, method in pairs:
            label = _METHOD_LABEL.get(method, method)
            diem = diem_ptxt = None
            thang = 30.0
            if method == "THPT":
                diem = normalize_score(value)
            else:
                diem_ptxt = normalize_score_ptxt(value)
                thang = 100.0 if (diem_ptxt or 0) <= 120 else 150.0
            if diem is None and diem_ptxt is None:
                continue
            records.append(
                AdmissionRecord(
                    ma_truong=school_code,
                    ten_truong=school_name,
                    ma_nganh=row["code"],
                    ten_nganh=row["name"] or row["code"],
                    nam=year,
                    to_hop=row.get("block") or "",
                    diem_chuan=diem,
                    diem_chuan_ptxt=diem_ptxt,
                    thang_diem=thang,
                    phuong_thuc=label,
                    nguon=source,
                )
            )
    return records, used_order


def records_from_cutoff_image(
    image_path: str,
    school_code: str,
    school_name: str,
    year: int,
    source: str,
) -> List[AdmissionRecord]:
    """Đọc một file ảnh bảng điểm chuẩn."""
    full_tokens = ocr_tokens(image_path)
    full_records, _ = cutoff_records_from_tokens(
        full_tokens, school_code, school_name, year, source
    )
    full_majors = len({r.ma_nganh for r in full_records})
    if full_majors >= 25 and len(full_records) >= full_majors * 1.8:
        return full_records
    split_records = _records_from_halves(image_path, school_code, school_name, year, source)
    split_majors = len({r.ma_nganh for r in split_records})
    return split_records if split_majors > full_majors else full_records


def _rows_with_y(tokens, gap: float = 0.025):
    ordered = sorted(tokens, key=lambda item: (-item[0], item[1]))
    rows = []
    bucket = []
    anchor = None
    for y, x, text in ordered:
        if anchor is None or abs(y - anchor) <= gap:
            bucket.append((y, x, text))
            if anchor is None:
                anchor = y
        else:
            rows.append(bucket)
            bucket = [(y, x, text)]
            anchor = y
    if bucket:
        rows.append(bucket)
    return rows


def _records_from_halves(image_path, school_code, school_name, year, source) -> List[AdmissionRecord]:
    """Tách cột mã ngành và cột điểm rồi OCR riêng — Vision bỏ số khi ảnh quá rộng."""
    try:
        from PIL import Image
        image = Image.open(image_path).convert("RGB")
    except Exception:
        return []
    width, height = image.size
    band_h = 420
    overlap = 24
    merged = {}
    order: List[str] = []
    top = 0
    temps: List[str] = []
    try:
        while top < height:
            bottom = min(height, top + band_h)
            crop = image.crop((0, top, width, bottom))
            left = crop.crop((0, 0, int(width * 0.50), crop.height))
            right = crop.crop((int(width * 0.40), 0, width, crop.height))
            paths = []
            for part in (left, right):
                tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
                part.save(tmp, format="JPEG", quality=95)
                tmp.close()
                paths.append(tmp.name)
                temps.append(tmp.name)
            left_rows = _rows_with_y(ocr_tokens(paths[0]))
            right_tokens = ocr_tokens(paths[1])
            if not order:
                header_bits = []
                for _, x, text in sorted(right_tokens, key=lambda item: item[1]):
                    for method in _methods_in_text(text):
                        if method not in header_bits:
                            header_bits.append(method)
                if header_bits:
                    order = header_bits
            right_rows = _rows_with_y(right_tokens)
            used_right = set()
            for left_row in left_rows:
                code = ""
                name_parts = []
                ly = sum(y for y, _, _ in left_row) / len(left_row)
                for _, x, text in sorted(left_row, key=lambda item: item[1]):
                    if _is_code(text) and not code:
                        code = text
                        continue
                    if code and not text.isdigit() and not _BLOCK_RE.match(text.replace(" ", "")):
                        name_parts.append(text)
                if not code:
                    continue
                best_i = None
                best_dy = 1.0
                for i, right_row in enumerate(right_rows):
                    ry = sum(y for y, _, _ in right_row) / len(right_row)
                    dy = abs(ry - ly)
                    if dy < best_dy:
                        best_dy = dy
                        best_i = i
                if best_i is None or best_dy > 0.04:
                    continue
                used_right.add(best_i)
                scores = []
                block = ""
                for _, x, text in sorted(right_rows[best_i], key=lambda item: item[1]):
                    value = _parse_score(text)
                    if value is not None:
                        scores.append(value)
                    elif _BLOCK_RE.match(text.replace(" ", "")):
                        block = text.replace(" ", "")
                methods = order or (
                    ["THPT", "XTTN_1.2", "XTTN_1.3", "TSA"] if len(scores) >= 4
                    else ["TSA", "THPT"] if len(scores) == 2
                    else ["THPT"] if len(scores) == 1
                    else []
                )
                if methods and len(scores) > len(methods):
                    scores = scores[-len(methods):]
                if len(scores) != len(methods) or not methods:
                    continue
                name = _fix_name(clean_text(" ".join(name_parts))) or code
                for value, method in zip(scores, methods):
                    label = _METHOD_LABEL.get(method, method)
                    diem = diem_ptxt = None
                    thang = 30.0
                    if method == "THPT":
                        diem = normalize_score(value)
                    else:
                        diem_ptxt = normalize_score_ptxt(value)
                        thang = 100.0
                    if diem is None and diem_ptxt is None:
                        continue
                    rec = AdmissionRecord(
                        ma_truong=school_code,
                        ten_truong=school_name,
                        ma_nganh=code,
                        ten_nganh=name,
                        nam=year,
                        to_hop=block,
                        diem_chuan=diem,
                        diem_chuan_ptxt=diem_ptxt,
                        thang_diem=thang,
                        phuong_thuc=label,
                        nguon=source,
                    )
                    key = (code, label)
                    current = merged.get(key)
                    if current is None or (not current.to_hop and block):
                        merged[key] = rec
            if bottom >= height:
                break
            top = bottom - overlap
    finally:
        image.close()
        for path in temps:
            if os.path.exists(path):
                os.unlink(path)
    last_block = ""
    for rec in merged.values():
        if rec.to_hop:
            last_block = rec.to_hop
        elif last_block:
            rec.to_hop = last_block
    return list(merged.values())
