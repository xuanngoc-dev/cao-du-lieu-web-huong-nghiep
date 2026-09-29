# -*- coding: utf-8 -*-
"""
Giao diện web Bootstrap (Flask) — Tổng hợp dữ liệu tuyển sinh ĐH / CĐ.

Chạy:
  python3 main.py --mode ui
  hoặc: python3 web/app.py
"""

import json
import os
import re
import sys
from datetime import datetime
from html import escape as html_escape
from typing import Any, Dict, List, Optional

from flask import (
    Flask,
    Response,
    render_template,
    request,
    jsonify,
    send_file,
    redirect,
    url_for,
    stream_with_context,
    send_from_directory,
)
from werkzeug.utils import secure_filename

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ui.helpers import parse_school_codes_text, format_codes_for_copy
from crawlers.school_directory import (
    SchoolDirectory,
    TYPE_LABELS,
    classify_school_sector,
    split_addresses_by_region,
)
from crawlers.online_crawler import OnlineAdmissionCrawler
from crawlers.method_catalog import (
    METHOD_CATALOG,
    AdmissionMethodCollector,
    program_rows_from_payload,
    records_from_local_file,
    records_from_programs,
)
from crawlers.score_conversion_crawler import ScoreConversionCrawler
from crawlers.official_site_crawler import filter_official_urls, lookup_local_school
from exporter.excel_exporter import ExcelAdmissionExporter
from dataclasses import fields as dataclass_fields

from core.models import (
    AdmissionRecord,
    AdmissionRegulation,
    CrawlBundle,
    MethodConversionBundle,
    MethodConversionImage,
    MethodConversionNote,
    MethodEquivalenceRow,
    MethodRangeHint,
    ScoreConversionRecord,
)
from core.conversion_calculator import (
    calculate_equivalence,
    calculate_certificate_conversion,
    list_methods_from_rows,
    merge_method_lists,
    rows_for_latest_year,
    METHOD_LABELS,
)
from core.admission_chance import (
    analyze_chance,
    build_trend_series,
    enrich_with_ai,
    list_admission_methods,
    list_school_majors,
)
from core.aggregator import METHOD_COLUMN_LABELS, build_grouped_score_view
from core.bonus_policy import list_bonus_records, summarize_certificate_bonus
from core import dataset_store


CONVERSION_IMPORT_SOURCE = "Nhập quy chế quy đổi"
CERTIFICATE_CATALOG_PATH = os.path.join(ROOT, "data", "constants", "bang_cap_chung_chi.md")
EXAM_CATALOG_PATH = os.path.join(ROOT, "data", "constants", "ky_thi.txt")
SUBJECT_COMBO_PATH = os.path.join(ROOT, "data", "constants", "to_hop_mon_hoc.txt")
CONVERSION_REGULATION_DIR = os.path.join(ROOT, "data", "constants", "quy-che-quy-doi-diem")
_REGULATION_FILE = re.compile(r"^([A-Za-z0-9]+)-(\d{4})\.md$", re.IGNORECASE)
_COMBO_GROUP_NAMES = {
    "A": "Khối A",
    "B": "Khối B",
    "C": "Khối C",
    "D": "Khối D",
    "H": "Khối H",
    "V": "Khối V",
}


def _load_subject_combinations() -> Dict[str, Any]:
    """Danh sách mã tổ hợp môn xét tuyển từ to_hop_mon_hoc.txt."""
    empty: Dict[str, Any] = {"items": [], "groups": []}
    try:
        with open(SUBJECT_COMBO_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, list):
        return empty
    items: List[Dict[str, str]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        code = str(row.get("ma_to_hop") or "").strip()
        subjects = str(row.get("cac_mon") or "").strip()
        if not code or not subjects:
            continue
        group = code[:1].upper()
        items.append({
            "code": code,
            "subjects": subjects,
            "group": group,
            "group_name": _COMBO_GROUP_NAMES.get(group, f"Nhóm {group}"),
        })
    groups: List[Dict[str, Any]] = []
    for item in items:
        group = item["group"]
        found = next((entry for entry in groups if entry["id"] == group), None)
        if found:
            found["count"] += 1
            continue
        groups.append({
            "id": group,
            "name": item["group_name"],
            "count": 1,
        })
    return {"items": items, "groups": groups}


def _load_thpt_exam_category() -> Optional[Dict[str, Any]]:
    """Mức chứng chỉ được miễn thi ngoại ngữ khi xét tốt nghiệp THPT — nguồn ky_thi.txt."""
    try:
        with open(EXAM_CATALOG_PATH, encoding="utf-8") as fh:
            raw = fh.read().strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    items: List[Dict[str, Any]] = []
    for subject in data.get("subjects") or []:
        if not isinstance(subject, dict):
            continue
        language = str(subject.get("subject") or "").strip()
        for item in subject.get("items") or []:
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                continue
            row = dict(item)
            row["subject"] = language
            row["type"] = "Miễn thi THPT"
            items.append(row)
    if not items:
        return None
    description = str(data.get("rule") or "").strip()
    source = str(data.get("source") or "").strip()
    if source:
        description = f"{description} Nguồn: {source}.".strip()
    return {
        "category_id": "cat_05",
        "category_name": str(data.get("title") or "Miễn thi ngoại ngữ kỳ thi tốt nghiệp THPT"),
        "description": description,
        "items": items,
    }


def _load_certificate_catalog() -> Dict[str, Any]:
    """Danh mục bằng cấp, chứng chỉ và mức miễn thi THPT."""
    empty: Dict[str, Any] = {"certificate_categories": [], "item_count": 0}
    try:
        with open(CERTIFICATE_CATALOG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        data = dict(empty)
    if not isinstance(data, dict):
        data = dict(empty)
    categories = data.get("certificate_categories")
    if not isinstance(categories, list):
        categories = []
        data["certificate_categories"] = categories
    exam_category = _load_thpt_exam_category()
    if exam_category and not any(
        isinstance(category, dict) and category.get("category_id") == "cat_05"
        for category in categories
    ):
        categories.append(exam_category)
    total = 0
    for category in categories:
        items = category.get("items") if isinstance(category, dict) else None
        total += len(items) if isinstance(items, list) else 0
    data["item_count"] = total
    return data


_PROFILE_CERT_SPLITS = {
    "TOEFL": [("TOEFL iBT", "TOEFL iBT"), ("TOEFL ITP", "TOEFL ITP")],
    "TOEIC": [("TOEIC", "TOEIC"), ("TOEIC SW", "TOEIC SW")],
    "HSK": [("HSK", "HSK"), ("HSKK", "HSKK")],
    "DELF": [("DELF", "DELF"), ("DALF", "DALF")],
    "A-Level": [("A-Level", "A-Level"), ("IB", "IB")],
}
_PROFILE_CERT_SHORT = {
    "IELTS": "IELTS",
    "PTE Academic": "PTE",
    "Aptis": "Aptis",
    "Duolingo": "Duolingo",
    "Cambridge": "Cambridge",
    "VSTEP": "VSTEP",
    "JLPT": "JLPT",
    "TOPIK": "TOPIK",
    "TCF": "TCF",
    "DELE": "DELE",
    "TestDaF": "TestDaF",
    "Goethe": "Goethe",
    "TRKI": "TRKI",
    "TOCFL": "TOCFL",
    "HSA": "HSA",
    "V-ACT": "V-ACT",
    "TSA": "TSA",
    "SPT": "SPT",
    "SAT": "SAT",
    "ACT": "ACT",
    "GMAT": "GMAT",
    "GRE": "GRE",
    "MOS": "MOS",
    "IC3": "IC3",
}
_PROFILE_RANGE_FOCUS = {
    "TOEFL iBT": "iBT",
    "TOEFL ITP": "ITP",
    "TOEIC": "Nghe",
    "TOEIC SW": "Nói",
    "HSK": "HSK Cấp",
    "A-Level": "A-Level",
    "IB": "IB",
}
_SCORE_PAIR = re.compile(r"(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)")
_PROFILE_AWARDS = {
    "Học sinh giỏi Quốc gia": ("HSG quốc gia", ["Giải Nhất", "Giải Nhì", "Giải Ba", "Khuyến khích"]),
    "Khoa học Kỹ thuật": ("KHKT", ["Giải Nhất", "Giải Nhì", "Giải Ba", "Giải Tư"]),
    "Học sinh giỏi cấp Tỉnh": ("HSG tỉnh", ["Giải Nhất", "Giải Nhì", "Giải Ba"]),
    "Giải thưởng Năng khiếu": ("Năng khiếu", ["Huy chương Vàng", "Huy chương Bạc", "Huy chương Đồng", "Giải thưởng"]),
}


def _profile_cert_groups(catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Nhóm ô nhập trên màn cá nhân, cùng danh mục với trang bằng cấp (trừ mức miễn thi THPT)."""
    groups: List[Dict[str, Any]] = []
    for category in catalog.get("certificate_categories") or []:
        if not isinstance(category, dict) or category.get("category_id") == "cat_05":
            continue
        fields: List[Dict[str, Any]] = []
        for item in category.get("items") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            score_range = str(item.get("score_range") or "").strip()
            hint = " ".join(part for part in (score_range, str(item.get("admissions_usage") or "").strip()) if part)
            fields.extend(_profile_fields_for_item(name, hint, score_range))
        if fields:
            groups.append({
                "id": category.get("category_id") or "",
                "name": category.get("category_name") or "",
                "fields": fields,
            })
    return groups


def _score_bounds(text: str) -> str:
    """Rút khoảng điểm chấp nhận, dạng min–max, từ mô tả thang điểm."""
    pairs = _SCORE_PAIR.findall(text or "")
    if pairs:
        def span(pair: tuple) -> float:
            try:
                return float(pair[1].replace(",", ".")) - float(pair[0].replace(",", "."))
            except ValueError:
                return 0
        low, high = max(pairs, key=span)
        return f"{low.replace(',', '.')}–{high.replace(',', '.')}"
    ceiling = re.search(r"thang(?:\s+điểm|\s+tổ hợp)?\s+(\d+(?:[.,]\d+)?)", text or "", re.I)
    if not ceiling:
        ceiling = re.search(r"tối đa\s+(\d+(?:[.,]\d+)?)", text or "", re.I)
    if ceiling:
        return f"0–{ceiling.group(1).replace(',', '.')}"
    level = re.search(r"bậc\s+(\d+)\s+đến\s+bậc\s+(\d+)", text or "", re.I)
    if level:
        return f"{level.group(1)}–{level.group(2)}"
    band = re.search(r"cấp\s+(\d+)\s+đến\s+cấp\s+(\d+)", text or "", re.I)
    if band:
        return f"{band.group(1)}–{band.group(2)}"
    until = re.search(r"đến cấp\s+(\d+)", text or "", re.I)
    if until:
        return f"1–{until.group(1)}"
    letters = re.search(r"((?:[A-C]\d)|(?:[A-Z]\*))\s*[-–—]\s*([A-Z]\d?)", text or "")
    if letters:
        return f"{letters.group(1)}–{letters.group(2)}"
    step = re.search(r"\bN(\d+)\s+đến\s+N(\d+)", text or "", re.I)
    if step:
        return f"N{step.group(1)}–N{step.group(2)}"
    return ""


def _placeholder_for(score_range: str, key: str) -> str:
    text = score_range or ""
    focus = _PROFILE_RANGE_FOCUS.get(key)
    if focus and focus.lower() in text.lower():
        start = text.lower().find(focus.lower())
        rest = text[start:]
        cut = re.search(r",\s+(?=[A-Z])|;\s*|\.\s+", rest[len(focus):])
        text = rest[: len(focus) + cut.start()] if cut else rest
    bounds = _score_bounds(text)
    if key == "HSKK" and "HSKK" not in (score_range or ""):
        bounds = "0–100"
    return bounds or "Nhập điểm"


def _profile_fields_for_item(name: str, hint: str, score_range: str = "") -> List[Dict[str, Any]]:
    for prefix, (key, options) in _PROFILE_AWARDS.items():
        if name.startswith(prefix):
            return [{
                "key": key,
                "label": name,
                "hint": hint,
                "kind": "select",
                "options": options,
            }]
    for prefix, parts in _PROFILE_CERT_SPLITS.items():
        if name.startswith(prefix):
            return [
                {
                    "key": key,
                    "label": label,
                    "hint": hint,
                    "placeholder": _placeholder_for(score_range, key),
                    "kind": "number",
                }
                for key, label in parts
            ]
    for prefix, key in _PROFILE_CERT_SHORT.items():
        if name.startswith(prefix):
            return [{
                "key": key,
                "label": key,
                "hint": hint,
                "placeholder": _placeholder_for(score_range, key),
                "kind": "number",
            }]
    return [{
        "key": name[:40],
        "label": name,
        "hint": hint,
        "placeholder": _placeholder_for(score_range, name[:40]),
        "kind": "number",
    }]


_CONVERSION_METHOD_ALIASES = {
    "THPT": "THPT",
    "TN": "THPT",
    "TOTNGHIEP": "THPT",
    "KQHB": "HOC_BA",
    "HOCBA": "HOC_BA",
    "HOC_BA": "HOC_BA",
    "PT2": "HOC_BA",
    "2": "HOC_BA",
    "HSA": "HSA",
    "DGNL": "HSA",
    "PT4": "HSA",
    "4": "HSA",
    "TSA": "TSA",
    "DGTD": "TSA",
    "PT5": "TSA",
    "5": "TSA",
    "VACT": "V-ACT",
    "V-ACT": "V-ACT",
    "SAT": "SAT",
    "ACT": "ACT",
    "IELTS": "IELTS",
}

_CONVERSION_COLUMNS = {
    "THPT": "Điểm TN THPT",
    "HOC_BA": "Điểm học bạ",
    "HSA": "Điểm HSA",
    "TSA": "Điểm TSA",
    "V-ACT": "Điểm V-ACT",
    "SAT": "Điểm SAT",
    "ACT": "Điểm ACT",
    "IELTS": "Điểm IELTS",
}


def _conversion_method_id(raw: Any) -> str:
    text = re.sub(r"[^A-Z0-9]", "", str(raw or "").upper())
    if text in _CONVERSION_METHOD_ALIASES:
        return _CONVERSION_METHOD_ALIASES[text]
    folded = text.replace("PHUONGTHUC", "PT")
    return _CONVERSION_METHOD_ALIASES.get(folded, str(raw or "").strip()[:40] or "OTHER")


def _fmt_conversion_score(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def _conversion_bound(value: Any, label: str) -> float:
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise ValueError(f"Thiếu {label}.")
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        raise ValueError(f"{label} không phải số: {value}.")


def _conversion_band(level: Dict[str, Any], nxt_lo: Optional[float]) -> str:
    lo = _conversion_bound(
        level.get("tu", level.get("diem_nguon", level.get("nguon", level.get("from")))),
        "mức nguồn",
    )
    raw_hi = level.get("den", level.get("to"))
    if raw_hi is None or str(raw_hi).strip() == "":
        hi = None
    else:
        hi = _conversion_bound(raw_hi, "mức đến")
        if hi < lo:
            lo, hi = hi, lo
    if hi is None or abs(hi - lo) < 1e-9:
        if nxt_lo is not None and nxt_lo > lo:
            return f"{_fmt_conversion_score(lo)}-{_fmt_conversion_score(nxt_lo)}"
        if hi is None:
            return f"{_fmt_conversion_score(lo)}-9999"
        return _fmt_conversion_score(lo)
    return f"{_fmt_conversion_score(lo)}-{_fmt_conversion_score(hi)}"


def conversion_rows_from_payload(payload: Any, code: str, school_name: str, year: int) -> tuple:
    """JSON quy chế → dòng bảng quy đổi, mỗi mức một dòng theo phương thức."""
    source_url = ""
    methods = payload
    if isinstance(payload, dict):
        source_url = str(payload.get("nguon") or payload.get("url") or "").strip()
        methods = payload.get("phuong_thuc") or payload.get("methods") or payload.get("bang")
        if methods is None and any(key in payload for key in ("muc", "tu", "diem", "diem_quy_doi")):
            methods = [payload]
    if isinstance(methods, dict):
        methods = [methods]
    if not isinstance(methods, list) or not methods:
        raise ValueError("JSON cần có phuong_thuc, mỗi phương thức gồm danh sách muc.")

    rows = []
    notes = []
    seen_methods = []
    for index, method in enumerate(methods, start=1):
        if not isinstance(method, dict):
            raise ValueError(f"Phương thức thứ {index} không đúng định dạng.")
        levels = method.get("muc") or method.get("levels") or method.get("bang")
        if levels is None and any(key in method for key in ("tu", "diem", "diem_nguon", "diem_quy_doi")):
            levels = [method]
        if not isinstance(levels, list) or not levels:
            raise ValueError(f"Phương thức thứ {index} chưa có mức quy đổi.")
        method_id = _conversion_method_id(method.get("ma") or method.get("phuong_thuc") or method.get("ten"))
        title = str(method.get("ten") or method.get("tieu_de") or METHOD_LABELS.get(method_id, method_id)).strip()
        table_title = f"{method_id} · {title}" if method_id not in title.upper() else title
        target_id = _conversion_method_id(method.get("quy_ve") or method.get("dich") or "THPT")
        source_col = _CONVERSION_COLUMNS.get(method_id, f"Điểm {method_id}")
        target_col = _CONVERSION_COLUMNS.get(target_id, "Điểm TN THPT")
        parsed = []
        for level_index, level in enumerate(levels, start=1):
            if not isinstance(level, dict):
                raise ValueError(f"{table_title}: mức {level_index} không đúng định dạng.")
            score = _conversion_bound(
                level.get("diem", level.get("diem_quy_doi", level.get("score"))),
                f"điểm quy đổi của {table_title}",
            )
            parsed.append((level, score))
        parsed.sort(key=lambda item: _conversion_bound(
            item[0].get("tu", item[0].get("diem_nguon", item[0].get("nguon", item[0].get("from")))),
            "mức nguồn",
        ))
        for level_index, (level, score) in enumerate(parsed, start=1):
            nxt = None
            if level_index < len(parsed):
                nxt = _conversion_bound(
                    parsed[level_index][0].get("tu", parsed[level_index][0].get("diem_nguon", parsed[level_index][0].get("nguon", parsed[level_index][0].get("from")))),
                    "mức nguồn",
                )
            rows.append({
                "ma_truong": code,
                "ten_truong": school_name,
                "tieu_de_bang": table_title,
                "stt": f"{method_id}|{level_index}",
                "cot_gia_tri": {
                    source_col: _conversion_band(level, nxt),
                    target_col: _fmt_conversion_score(score),
                },
                "nam": year,
                "url_nguon": source_url,
                "nguon": CONVERSION_IMPORT_SOURCE,
            })
        if method_id not in seen_methods:
            seen_methods.append(method_id)
        formula = str(method.get("cong_thuc") or method.get("mo_ta") or "").strip()
        if formula:
            notes.append({
                "ma_truong": code,
                "ten_truong": school_name,
                "tieu_de": table_title,
                "noi_dung": formula,
                "nam": year,
                "url_nguon": source_url,
                "nguon": CONVERSION_IMPORT_SOURCE,
            })
    if source_url and not notes:
        notes.append({
            "ma_truong": code,
            "ten_truong": school_name,
            "tieu_de": "Nguồn quy chế quy đổi",
            "noi_dung": source_url,
            "nam": year,
            "url_nguon": source_url,
            "nguon": CONVERSION_IMPORT_SOURCE,
        })
    return rows, notes, seen_methods


def _upsert_records(existing, incoming, fields: tuple):
    """Ghi đè bản ghi trùng khoá và giữ mọi bản ghi cũ không có trong lần thu mới."""
    rows = [item for item in (existing or []) if isinstance(item, dict)]
    index = {}
    for position, row in enumerate(rows):
        index.setdefault(tuple(str(row.get(field) or "") for field in fields), position)
    for row in incoming or []:
        if not isinstance(row, dict):
            continue
        key = tuple(str(row.get(field) or "") for field in fields)
        previous = index.get(key)
        if previous is None:
            index[key] = len(rows)
            rows.append(row)
        else:
            rows[previous] = {**rows[previous], **row}
    return rows


def _quy_doi_bundle_from_cache(cached: dict) -> MethodConversionBundle:
    bundle = MethodConversionBundle()
    bundle.rows = _as_models(MethodEquivalenceRow, cached.get("rows"))
    bundle.notes = _as_models(MethodConversionNote, cached.get("notes"))
    bundle.images = _as_models(MethodConversionImage, cached.get("images"))
    bundle.ranges = _as_models(MethodRangeHint, cached.get("ranges"))
    bundle.conversions = _as_models(ScoreConversionRecord, cached.get("certificate_conversions"))
    bundle.school_results = list(cached.get("school_results") or [])
    return bundle


def _as_models(cls, items):
    names = {item.name for item in dataclass_fields(cls)}
    out = []
    for item in items or []:
        if isinstance(item, cls):
            out.append(item)
        elif isinstance(item, dict):
            out.append(cls(**{key: value for key, value in item.items() if key in names}))
    return out


def _enrich_methods_for_school(code: str, quy_doi_methods: List) -> List:
    """Danh sách phương thức lấy từ bảng quy đổi chính thức đã thu thập."""
    return merge_method_lists([], quy_doi_methods or [])


_REG_DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_REG_INLINE_MATH = re.compile(r"\$(.+?)\$")
_REG_MATH_TOKEN = re.compile(r"⟦MATH(\d+)⟧")


def _plain_inline_math(latex: str) -> str:
    """Số và chữ ngắn trong $...$ hiện như chữ thường, không giãn khoảng kiểu công thức."""
    text_only = re.fullmatch(r"\\text\{([^{}]+)\}", latex.strip())
    if text_only:
        return html_escape(text_only.group(1))
    if re.fullmatch(r"\d+(?:[.,]\d+)?", latex.strip()):
        return html_escape(latex.strip())
    return ""


def _regulation_inline(text: str, slots: List[str]) -> str:
    """Định dạng đậm, nghiêng, mã và công thức trong một dòng."""
    text = html_escape(text)

    def restore(match: re.Match) -> str:
        latex = slots[int(match.group(1))]
        plain = _plain_inline_math(latex)
        if plain:
            return plain
        return f'<span class="reg-math">${html_escape(latex)}$</span>'

    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    return _REG_MATH_TOKEN.sub(restore, text)


def _regulation_markdown(source: str) -> str:
    """Markdown quy chế (đề mục, danh sách, công thức) → HTML."""
    slots: List[str] = []

    def stash(match: re.Match) -> str:
        slots.append(match.group(1).strip())
        return f"⟦MATH{len(slots) - 1}⟧"

    text = _REG_DISPLAY_MATH.sub(stash, source.replace("\r\n", "\n"))
    text = _REG_INLINE_MATH.sub(stash, text)
    parts: List[str] = []
    stack: List[Dict[str, Any]] = []

    def close_li() -> None:
        if stack and stack[-1]["li_open"]:
            parts.append("</li>")
            stack[-1]["li_open"] = False

    def close_lists() -> None:
        while stack:
            close_li()
            parts.append(f"</{stack.pop()['kind']}>")

    def close_deeper_than(indent: int, kind: Optional[str] = None) -> None:
        while stack and stack[-1]["indent"] > indent:
            close_li()
            parts.append(f"</{stack.pop()['kind']}>")
        if stack and stack[-1]["indent"] == indent and kind and stack[-1]["kind"] != kind:
            close_li()
            parts.append(f"</{stack.pop()['kind']}>")
        elif stack and stack[-1]["indent"] == indent:
            close_li()

    def formula_html(index: int) -> str:
        latex = html_escape(slots[index])
        return f'<div class="reg-formula">$${latex}$$</div>'

    def append_block(raw_line: str) -> None:
        token = _REG_MATH_TOKEN.fullmatch(raw_line.strip())
        if token:
            parts.append(formula_html(int(token.group(1))))
            return
        inline = _regulation_inline(raw_line.strip(), slots)
        css = ' class="reg-note"' if inline.startswith("<em>Lưu ý") else ""
        parts.append(f"<p{css}>{inline}</p>")

    for raw in text.split("\n"):
        if not raw.strip():
            close_lists()
            continue
        heading = re.match(r"^(#{1,4})\s+(.*)$", raw.strip())
        if heading:
            close_lists()
            level = len(heading.group(1))
            parts.append(f"<h{level}>{_regulation_inline(heading.group(2), slots)}</h{level}>")
            continue
        if raw.strip() == "---":
            close_lists()
            parts.append("<hr>")
            continue
        bullet = re.match(r"^(\s*)[*-]\s+(.*)$", raw)
        number = re.match(r"^(\s*)\d+\.\s+(.*)$", raw)
        if bullet or number:
            matched = bullet or number
            indent = len(matched.group(1).replace("\t", "    "))
            kind = "ul" if bullet else "ol"
            if not stack or stack[-1]["indent"] < indent:
                parts.append(f"<{kind}>")
                stack.append({"kind": kind, "indent": indent, "li_open": False})
            else:
                close_deeper_than(indent, kind)
                if not stack or stack[-1]["kind"] != kind:
                    parts.append(f"<{kind}>")
                    stack.append({"kind": kind, "indent": indent, "li_open": False})
            parts.append(f"<li>{_regulation_inline(matched.group(2), slots)}")
            stack[-1]["li_open"] = True
            continue
        if stack and raw.startswith((" ", "\t")):
            append_block(raw)
            continue
        close_lists()
        append_block(raw)
    close_lists()
    return "\n".join(parts)


def _parse_regulation_document(source: str) -> Dict[str, str]:
    """Tách tiêu đề, tên trường và phần thân quy chế."""
    mark = "<!-- điểm thưởng -->"
    if mark in source:
        source = source.split(mark, 1)[0]
    lines = source.replace("\r\n", "\n").split("\n")
    title = ""
    subtitle = ""
    body_at = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not title and stripped.startswith("# "):
            title = stripped[2:].strip()
            body_at = index + 1
            continue
        if title and not subtitle and stripped.startswith("**") and stripped.endswith("**"):
            subtitle = stripped.strip("*").strip()
            body_at = index + 1
            continue
        if title and stripped == "---":
            body_at = index + 1
            break
        if title and stripped:
            break
    name = re.sub(r"\s*\([^)]*\)\s*$", "", subtitle).strip()
    return {
        "title": title,
        "name": name or subtitle,
        "html": _regulation_markdown("\n".join(lines[body_at:]).strip()),
    }


def _load_conversion_regulations() -> Dict[str, Any]:
    """Quy chế quy đổi điểm theo trường, mỗi file {mã}-{năm}.md."""
    documents: List[Dict[str, Any]] = []
    try:
        names = sorted(os.listdir(CONVERSION_REGULATION_DIR))
    except OSError:
        names = []
    for filename in names:
        matched = _REGULATION_FILE.match(filename)
        if not matched:
            continue
        path = os.path.join(CONVERSION_REGULATION_DIR, filename)
        try:
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
        except OSError:
            continue
        parsed = _parse_regulation_document(source)
        if not parsed["html"]:
            continue
        code = matched.group(1).upper()
        documents.append({
            "code": code,
            "year": int(matched.group(2)),
            "name": parsed["name"] or code,
            "title": parsed["title"],
            "html": parsed["html"],
            "calculator": _regulation_calculator_spec(code) or _default_regulation_calculator(),
        })
    documents.sort(key=lambda item: (item["name"], -item["year"]))
    schools: List[Dict[str, Any]] = []
    for doc in documents:
        school = next((item for item in schools if item["code"] == doc["code"]), None)
        if school:
            school["years"].append(doc["year"])
            continue
        schools.append({
            "code": doc["code"],
            "name": doc["name"],
            "years": [doc["year"]],
        })
    for school in schools:
        school["years"] = sorted(set(school["years"]), reverse=True)
    return {"schools": schools, "documents": documents}


def _fmt_reg_score(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}".replace(".", ",")


_PROFILE_REGION_POINTS = {"KV1": 0.75, "KV2-NT": 0.5, "KV2": 0.25, "KV3": 0.0}
_PROFILE_OBJECT_POINTS = {"01": 2.0, "02": 2.0, "03": 2.0, "04": 1.0, "05": 1.0, "06": 1.0}
_PROFILE_SUBJECT_LABELS = {item["key"]: item["label"] for item in dataset_store.PROFILE_SUBJECTS}
_BKA_THPT_COMBOS = (
    ("A00", ("toan", "ly", "hoa")),
    ("A01", ("toan", "ly", "anh")),
    ("A02", ("toan", "ly", "sinh")),
    ("B00", ("toan", "hoa", "sinh")),
    ("D01", ("van", "toan", "anh")),
    ("D04", ("van", "toan", "trung")),
    ("D07", ("toan", "hoa", "anh")),
    ("D26", ("toan", "ly", "duc")),
    ("D28", ("toan", "ly", "nhat")),
    ("D29", ("toan", "ly", "phap")),
)
_BKA_K01_THIRD = ("ly", "hoa", "sinh", "tin")
_BKA_CERT_BOUNDS = {
    "SAT": (400, 1600),
    "ACT": (1, 36),
    "TSA": (0, 100),
    "IELTS": (0, 9),
    "A-Level": (0, 30),
    "IB": (0, 45),
    "AP": (0, 15),
}
_BKA_INTL_SCALES = (
    ("sat", "SAT", "thang_sat_1600"),
    ("act", "ACT", "thang_act_36"),
    ("a-level", "A-Level", "thang_a_level_30"),
    ("ap", "AP", "thang_ap_15"),
    ("ib", "IB", "thang_ib_45"),
)
_BKA_PROFILE_ACHIEVEMENTS = {
    ("HSG quốc gia", "Giải Nhất"): ("ky_thi_hsg", "Giải Nhất quốc gia trở lên"),
    ("HSG quốc gia", "Giải Nhì"): ("ky_thi_hsg", "Giải Nhì quốc gia"),
    ("HSG quốc gia", "Giải Ba"): ("ky_thi_hsg", "Giải Ba quốc gia"),
    ("HSG quốc gia", "Khuyến khích"): ("ky_thi_hsg", "Giải Khuyến khích quốc gia"),
    ("HSG tỉnh", "Giải Nhất"): ("ky_thi_hsg", "Giải Nhất tỉnh hoặc tương đương"),
    ("HSG tỉnh", "Giải Nhì"): ("ky_thi_hsg", "Giải Nhì tỉnh hoặc tương đương"),
    ("HSG tỉnh", "Giải Ba"): ("ky_thi_hsg", "Giải Ba tỉnh hoặc tương đương"),
    ("KHKT", "Giải Nhất"): ("ky_thi_khkt", "Giải Nhất quốc gia"),
    ("KHKT", "Giải Nhì"): ("ky_thi_khkt", "Giải Nhì quốc gia"),
    ("KHKT", "Giải Ba"): ("ky_thi_khkt", "Giải Ba quốc gia"),
    ("KHKT", "Giải Tư"): ("ky_thi_khkt", "Giải Tư/Khuyến khích quốc gia"),
}
_BKA_BONUS_FIELDS = (
    ("ky_thi_hsg_them", "HSG môn khác hoặc năm khác"),
    ("ky_thi_khkt_them", "KHKT đề tài khác"),
    ("duong_len_dinh_olympia_them", "Olympia năm học khác"),
    ("giai_thuong_van_the_my", "Giải văn hóa, thể thao"),
    ("khen_thuong_xa_hoi", "Khen thưởng hoạt động xã hội"),
)


def _load_bka_talent_bonus() -> Optional[Dict[str, Any]]:
    """Bảng điểm thưởng xét tuyển tài năng nằm cuối file quy chế BKA."""
    path = os.path.join(CONVERSION_REGULATION_DIR, "bka-2026.md")
    try:
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
    except OSError:
        return None
    mark = "<!-- điểm thưởng -->"
    if mark not in source:
        return None
    try:
        data = json.loads(source.split(mark, 1)[1].strip())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _bka_bonus_rows(node: Any) -> List[Dict[str, Any]]:
    if isinstance(node, list):
        return [item for item in node if isinstance(item, dict)]
    if isinstance(node, dict):
        return [item for item in (node.get("danh_sach") or []) if isinstance(item, dict)]
    return []


def _bka_choice_points(bonus: Dict[str, Any], token: str) -> Optional[tuple]:
    """token dạng thanh:nhóm:chỉ số hoặc thuong:nhóm:chỉ số."""
    parts = str(token or "").split(":")
    if len(parts) != 3 or parts[0] not in ("thanh", "thuong"):
        return None
    try:
        index = int(parts[2])
    except ValueError:
        return None
    cases = (bonus.get("cac_dien_xet_tuyen") or {}).get("dien_1_3") or {}
    if parts[0] == "thanh":
        groups = (cases.get("diem_thanh_tich") or {}).get("cac_truong_hop") or {}
    else:
        groups = (cases.get("diem_thuong") or {}).get("cac_truong_hop") or {}
    rows = _bka_bonus_rows(groups.get(parts[1]))
    if index < 0 or index >= len(rows):
        return None
    row = rows[index]
    try:
        points = float(row.get("diem"))
    except (TypeError, ValueError):
        return None
    return str(row.get("thanh_tich") or ""), points


def _default_regulation_calculator() -> Dict[str, Any]:
    """Mọi quy chế đều đọc hồ sơ cá nhân, kể cả khi chưa có công thức riêng."""
    return {
        "lead": (
            "Học bạ, điểm thi tốt nghiệp THPT, chứng chỉ, giải thưởng và điểm ưu tiên "
            "lấy từ hồ sơ cá nhân."
        ),
        "inputs": [],
    }


def _regulation_calculator_spec(code: str) -> Optional[Dict[str, Any]]:
    """Ô nhập thêm theo quy chế. Học bạ, điểm thi, chứng chỉ và ưu tiên lấy từ hồ sơ."""
    if code == "DCN":
        return _dcn_calculator_spec()
    if code == "KHA":
        return _kha_calculator_spec()
    if code != "BKA":
        return None
    bonus = _load_bka_talent_bonus() or {}
    cases = (bonus.get("cac_dien_xet_tuyen") or {}).get("dien_1_3") or {}
    achievement_groups = (cases.get("diem_thanh_tich") or {}).get("cac_truong_hop") or {}
    achievement_options = [{"value": "", "label": "Lấy mức cao nhất từ hồ sơ"}]
    for group_key, node in achievement_groups.items():
        for index, item in enumerate(_bka_bonus_rows(node)):
            achievement_options.append({
                "value": f"thanh:{group_key}:{index}",
                "label": f"{item.get('thanh_tich')} — {item.get('diem')} điểm",
            })
    inputs: List[Dict[str, Any]] = [{
        "id": "thanh_tich",
        "label": "Điểm thành tích",
        "group": "Xét tuyển tài năng — điểm thành tích",
        "type": "select",
        "hint": "Chỉ tính một thành tích cao nhất, tối đa 50 điểm. Bỏ trống thì lấy mức cao nhất đã lưu trong hồ sơ.",
        "options": achievement_options,
    }]
    reward_groups = (cases.get("diem_thuong") or {}).get("cac_truong_hop") or {}
    for group_key, label in _BKA_BONUS_FIELDS:
        node = reward_groups.get(group_key)
        options = [{"value": "", "label": "Không tính"}]
        for index, item in enumerate(_bka_bonus_rows(node)):
            options.append({
                "value": f"thuong:{group_key}:{index}",
                "label": f"{item.get('thanh_tich')} — {item.get('diem')} điểm",
            })
        if len(options) == 1:
            continue
        hint = node.get("ghi_chu") if isinstance(node, dict) else ""
        inputs.append({
            "id": f"thuong_{group_key}",
            "label": label,
            "group": "Xét tuyển tài năng — điểm thưởng",
            "type": "select",
            "hint": hint or "Chỉ tính thành tích chưa được cộng vào điểm thành tích. Tổng điểm thưởng tối đa 10.",
            "options": options,
        })
    return {
        "lead": (
            "Điểm trung bình từng năm, điểm thi THPT, chứng chỉ và điểm ưu tiên lấy từ hồ sơ cá nhân. "
            "Điểm thưởng xét tuyển tài năng tính theo bảng trong quy chế: chọn thành tích bên dưới nếu hồ sơ chưa đủ."
        ),
        "inputs": inputs,
    }


def _bka_ielts_reward_row(ielts: Optional[float], bonus: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    rows = (((bonus or {}).get("cac_dien_xet_tuyen") or {}).get("dien_1_2") or {}).get("bang_diem_thuong") or []
    matched = None
    if ielts is None:
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("ielts_hoac_tuong_duong") or "").strip()
        try:
            threshold = float(label.replace(">=", "").replace(",", "."))
        except ValueError:
            continue
        if ielts + 1e-9 >= threshold:
            current = float(row.get("thang_100") or 0)
            previous = float(matched.get("thang_100") or 0) if matched else -1
            if matched is None or current >= previous:
                matched = row
    return matched


def _bka_table_achievement(bonus: Optional[Dict[str, Any]], group_key: str, title: str) -> Optional[tuple]:
    groups = (
        (((bonus or {}).get("cac_dien_xet_tuyen") or {}).get("dien_1_3") or {}).get("diem_thanh_tich") or {}
    ).get("cac_truong_hop") or {}
    for item in _bka_bonus_rows(groups.get(group_key)):
        if str(item.get("thanh_tich") or "") == title:
            try:
                return title, float(item.get("diem"))
            except (TypeError, ValueError):
                return None
    return None


def _bka_english_reward(ielts: Optional[float], bonus: Optional[Dict[str, Any]]) -> Optional[tuple]:
    rows = (
        (((bonus or {}).get("cac_dien_xet_tuyen") or {}).get("dien_1_3") or {}).get("diem_thuong") or {}
    ).get("cac_truong_hop", {}).get("chung_chi_tieng_anh") or []
    if ielts is None:
        return None
    matched = None
    for item in rows:
        if not isinstance(item, dict):
            continue
        title = str(item.get("thanh_tich") or "")
        number = re.search(r"(\d+(?:[.,]\d+)?)", title)
        if not number:
            continue
        threshold = float(number.group(1).replace(",", "."))
        if ielts + 1e-9 >= threshold:
            try:
                points = float(item.get("diem"))
            except (TypeError, ValueError):
                continue
            if matched is None or points >= matched[1]:
                matched = (title, points)
    return matched


def _profile_number(raw: Any, lo: Optional[float] = None, hi: Optional[float] = None) -> Optional[float]:
    text = str(raw or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        return None
    return value


def _profile_vstep(raw: Any) -> Optional[str]:
    text = str(raw or "").strip().upper().replace(" ", "")
    if not text:
        return None
    named = {"B1": "B1", "B2": "B2", "C1": "C1", "C2": "C1", "A1": "A2", "A2": "A2"}
    if text in named:
        return named[text]
    try:
        level = int(float(text.replace(",", ".")))
    except ValueError:
        return None
    return {1: "A2", 2: "A2", 3: "B1", 4: "B2", 5: "C1", 6: "C1"}.get(level)


def _subject_label(key: str) -> str:
    return _PROFILE_SUBJECT_LABELS.get(key, key)


def _bka_from_profile(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Điểm quy chế BKA lấy từ hồ sơ cá nhân: học bạ, điểm thi THPT, chứng chỉ, ưu tiên."""
    profile = profile or {}
    certs = profile.get("chung_chi") if isinstance(profile.get("chung_chi"), dict) else {}
    exam_raw = profile.get("diem_thi_thu") if isinstance(profile.get("diem_thi_thu"), dict) else {}
    transcript = profile.get("hoc_ba") if isinstance(profile.get("hoc_ba"), dict) else {}
    notes: List[str] = []
    cert_values: Dict[str, Any] = {}
    for key, bounds in _BKA_CERT_BOUNDS.items():
        raw = certs.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        value = _profile_number(raw, bounds[0], bounds[1])
        if value is None:
            notes.append(f"{key} trong hồ sơ không nằm trong thang {_fmt_reg_score(bounds[0])}–{_fmt_reg_score(bounds[1])}.")
            continue
        cert_values[key.lower()] = value
    vstep_raw = certs.get("VSTEP")
    if vstep_raw is not None and str(vstep_raw).strip():
        level = _profile_vstep(vstep_raw)
        if level:
            cert_values["vstep"] = level
        else:
            notes.append("VSTEP trong hồ sơ không đọc được (bậc 1–6 hoặc B1, B2, C1).")
    exam: Dict[str, float] = {}
    for key, raw in exam_raw.items():
        value = _profile_number(raw, 0, 10)
        if value is not None:
            exam[str(key)] = value
    gpa: Dict[str, float] = {}
    gpa_counts: Dict[str, int] = {}
    for grade in ("10", "11", "12"):
        scores = []
        grade_raw = transcript.get(grade) if isinstance(transcript.get(grade), dict) else {}
        for raw in grade_raw.values():
            value = _profile_number(raw, 0, 10)
            if value is not None:
                scores.append(value)
        if scores:
            gpa[grade] = sum(scores) / len(scores)
            gpa_counts[grade] = len(scores)
    region = str(profile.get("khu_vuc") or "")
    obj = str(profile.get("doi_tuong") or "")
    uu_tien = None
    uu_label = ""
    if region in _PROFILE_REGION_POINTS or obj in _PROFILE_OBJECT_POINTS:
        region_points = _PROFILE_REGION_POINTS.get(region, 0.0)
        object_points = _PROFILE_OBJECT_POINTS.get(obj, 0.0)
        uu_tien = min(2.75, region_points + object_points)
        parts = []
        if region in _PROFILE_REGION_POINTS:
            parts.append(f"{region} {_fmt_reg_score(region_points)}")
        if obj in _PROFILE_OBJECT_POINTS:
            parts.append(f"đối tượng {obj} {_fmt_reg_score(object_points)}")
        uu_label = " + ".join(parts)
    awards = {}
    for key in ("HSG quốc gia", "HSG tỉnh", "KHKT"):
        label = str(certs.get(key) or "").strip()
        if label:
            awards[key] = label
    return {
        "certs": cert_values,
        "awards": awards,
        "exam": exam,
        "gpa": gpa,
        "gpa_counts": gpa_counts,
        "uu_tien": uu_tien,
        "uu_label": uu_label,
        "notes": notes,
    }


def _bka_profile_groups(profile: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Các dòng hiển thị điểm hồ sơ đang được dùng cho quy đổi BKA."""
    bundle = _bka_from_profile(profile)
    gpa_rows = []
    low: List[str] = []
    missing: List[str] = []
    for grade in ("10", "11", "12"):
        value = bundle["gpa"].get(grade)
        if value is None:
            missing.append(grade)
            gpa_rows.append({"label": f"Lớp {grade}", "value": "Chưa có", "detail": "Chưa lưu học bạ năm này."})
            continue
        mark = "đạt" if value + 1e-9 >= 8 else "dưới 8,00"
        if value + 1e-9 < 8:
            low.append(grade)
        count = bundle["gpa_counts"].get(grade, 0)
        gpa_rows.append({
            "label": f"Lớp {grade}",
            "value": _fmt_reg_score(value),
            "detail": f"{mark}, trung bình {count} môn đã lưu.",
        })
    if missing and len(missing) == 3:
        verdict = "Chưa có học bạ"
    elif missing or low:
        verdict = "Chưa đủ 8,00 mỗi năm"
    else:
        verdict = "Đạt từ 8,00 mỗi năm"
    gpa_rows.append({"label": "Điều kiện TBC", "value": verdict, "detail": "Diện xét tuyển tài năng cần từng năm lớp 10, 11, 12 từ 8,00."})

    exam = bundle["exam"]
    if exam:
        exam_rows = [
            {"label": _subject_label(key), "value": _fmt_reg_score(value), "detail": "Điểm thi THPT đã lưu."}
            for key, value in exam.items()
        ]
    else:
        exam_rows = [{"label": "Điểm thi THPT", "value": "Chưa có", "detail": "Chưa lưu điểm thi thử THPT trong hồ sơ."}]

    cert_labels = {
        "sat": "SAT", "act": "ACT", "tsa": "TSA", "ielts": "IELTS", "vstep": "VSTEP",
        "a-level": "A-Level", "ap": "AP", "ib": "IB",
    }
    cert_rows = []
    for key, value in bundle["certs"].items():
        shown = value if isinstance(value, str) else _fmt_reg_score(value)
        if key == "vstep" and value == "A2":
            shown = "Dưới B1"
        cert_rows.append({
            "label": cert_labels.get(key, key.upper()),
            "value": shown,
            "detail": "Chứng chỉ đã lưu trong hồ sơ cá nhân.",
        })
    for key, label in (bundle.get("awards") or {}).items():
        cert_rows.append({"label": key, "value": label, "detail": "Giải thưởng đã lưu trong hồ sơ cá nhân."})
    if not cert_rows:
        cert_rows.append({
            "label": "Chứng chỉ",
            "value": "Chưa có",
            "detail": "Chưa lưu chứng chỉ, bài thi hoặc giải thưởng trong hồ sơ.",
        })
    for note in bundle["notes"]:
        cert_rows.append({"label": "Lưu ý", "value": note, "detail": ""})

    if bundle["uu_tien"] is None:
        uu_rows = [{"label": "Điểm ưu tiên", "value": "Chưa có", "detail": "Chưa chọn khu vực hoặc đối tượng ưu tiên."}]
    else:
        uu_rows = [{"label": "Điểm ưu tiên", "value": _fmt_reg_score(bundle["uu_tien"]), "detail": bundle["uu_label"]}]
    return [
        {"label": "Học bạ", "rows": gpa_rows},
        {"label": "Điểm thi THPT", "rows": exam_rows},
        {"label": "Chứng chỉ và bài thi", "rows": cert_rows},
        {"label": "Điểm ưu tiên", "rows": uu_rows},
    ]


def _read_regulation_score(raw: Any, spec: Dict[str, Any]) -> tuple:
    label = spec["label"]
    if spec.get("type") == "select":
        value = str(raw or "").strip()
        if not value:
            return None, None
        allowed = {opt.get("value") for opt in spec.get("options") or []}
        if value not in allowed:
            return None, f"{label} không hợp lệ."
        return value, None
    if raw is None or str(raw).strip() == "":
        return None, None
    try:
        value = float(str(raw).strip().replace(",", "."))
    except ValueError:
        return None, f"{label} không phải số."
    lo = spec.get("min")
    hi = spec.get("max")
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        return None, f"{label} phải từ {lo} đến {hi}."
    return value, None


def _convert_bka_scores(
    scores: Dict[str, Any],
    spec: Dict[str, Any],
    profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Tính các mức điểm BKA 2026. Học bạ, điểm thi THPT, chứng chỉ và ưu tiên lấy từ hồ sơ cá nhân."""
    parsed: Dict[str, Any] = {}
    errors: List[str] = []
    for item in spec["inputs"]:
        value, error = _read_regulation_score(scores.get(item["id"]), item)
        if error:
            errors.append(error)
            continue
        if value is not None and value != "":
            parsed[item["id"]] = value
    if errors:
        return {"ok": False, "error": " ".join(errors)}

    bundle = _bka_from_profile(profile)
    certs = bundle["certs"]
    exam: Dict[str, float] = bundle["exam"]

    if not parsed and not certs and not exam and not bundle["gpa"]:
        return {
            "ok": False,
            "error": "Hồ sơ cá nhân chưa có học bạ, điểm thi THPT hoặc chứng chỉ để quy đổi. Hãy bổ sung ở màn Cá nhân, hoặc nhập điểm thành tích và điểm thưởng.",
        }

    tiles: List[Dict[str, str]] = []
    uu = bundle["uu_tien"]
    uu_points = float(uu or 0)
    if uu is None:
        uu_note = " Chưa có khu vực hoặc đối tượng ưu tiên trong hồ sơ cá nhân, đang tính bằng 0."
    else:
        uu_note = f" Điểm ưu tiên {_fmt_reg_score(uu_points)} lấy từ hồ sơ ({bundle['uu_label']})."

    if "sat" in certs or "act" in certs:
        entered = []
        if "sat" in certs:
            entered.append(f"SAT {_fmt_reg_score(certs['sat'])}")
        if "act" in certs:
            entered.append(f"ACT {_fmt_reg_score(certs['act'])}")
        tiles.append({
            "label": "Chứng chỉ quốc tế",
            "value": ", ".join(entered),
            "detail": "Lấy từ hồ sơ cá nhân. Diện xét tuyển tài năng 1.2.",
            "kind": "source",
        })
        tiles.append({
            "label": "Điểm thi THPT tương đương",
            "value": "Chưa có trong quy chế",
            "detail": "Quy chế không công bố bảng đổi SAT hoặc ACT sang điểm thi tốt nghiệp THPT.",
            "kind": "missing",
        })
        if "tsa" not in certs:
            tiles.append({
                "label": "Điểm tư duy tương đương",
                "value": "Chưa có trong quy chế",
                "detail": "Điểm tư duy chỉ quy đổi từ điểm TSA theo công thức TSA × 40/60, không quy đổi từ SAT hoặc ACT.",
                "kind": "missing",
            })

    bonus = _load_bka_talent_bonus()
    ielts_row = _bka_ielts_reward_row(certs.get("ielts"), bonus)
    english_reward = _bka_english_reward(certs.get("ielts"), bonus)
    chosen_achievement = _bka_choice_points(bonus or {}, parsed.get("thanh_tich"))
    if chosen_achievement is None:
        profile_achievements = []
        for key, label in (bundle.get("awards") or {}).items():
            mapped = _BKA_PROFILE_ACHIEVEMENTS.get((key, label))
            if not mapped:
                continue
            found = _bka_table_achievement(bonus, mapped[0], mapped[1])
            if found:
                profile_achievements.append(found)
        chosen_achievement = max(profile_achievements, key=lambda item: item[1]) if profile_achievements else None
    achievement_points = min(50.0, chosen_achievement[1]) if chosen_achievement else 0.0
    reward_parts = []
    reward_sum = 0.0
    if english_reward:
        reward_parts.append(f"{english_reward[0]}: { _fmt_reg_score(english_reward[1]) }")
        reward_sum += english_reward[1]
    for group_key, _label in _BKA_BONUS_FIELDS:
        picked = _bka_choice_points(bonus or {}, parsed.get(f"thuong_{group_key}"))
        if not picked:
            continue
        reward_parts.append(f"{picked[0]}: {_fmt_reg_score(picked[1])}")
        reward_sum += picked[1]
    reward_points = min(10.0, reward_sum)
    language_on_100 = 0.0
    if ielts_row is not None:
        try:
            language_on_100 = float(ielts_row.get("thang_100") or 0)
        except (TypeError, ValueError):
            language_on_100 = 0.0

    needs_gpa = bool(
        bundle["gpa"]
        or certs.get("sat")
        or certs.get("act")
        or certs.get("tsa")
        or chosen_achievement
        or reward_parts
    )
    if needs_gpa:
        low = [grade for grade in ("10", "11", "12") if grade in bundle["gpa"] and bundle["gpa"][grade] + 1e-9 < 8]
        missing_years = [grade for grade in ("10", "11", "12") if grade not in bundle["gpa"]]
        lines = []
        for grade in ("10", "11", "12"):
            if grade in bundle["gpa"]:
                count = bundle["gpa_counts"].get(grade, 0)
                lines.append(f"Lớp {grade}: {_fmt_reg_score(bundle['gpa'][grade])} ({count} môn)")
            else:
                lines.append(f"Lớp {grade}: chưa có")
        if len(missing_years) == 3:
            gpa_value, gpa_kind = "Chưa có", "missing"
        elif missing_years or low:
            gpa_value, gpa_kind = "Chưa đủ", "missing"
        else:
            gpa_value, gpa_kind = "Đạt", "value"
        tiles.append({
            "label": "Điểm trung bình từng năm",
            "value": gpa_value,
            "detail": "Cần từ 8,00 mỗi năm lớp 10, 11, 12. " + ". ".join(lines) + ". Trung bình các môn học bạ đã lưu.",
            "kind": gpa_kind,
        })

    tu_duy = None
    if "tsa" in certs:
        raw_tu_duy = certs["tsa"] * 40 / 60
        tu_duy = min(40.0, raw_tu_duy)
        detail = f"TSA {_fmt_reg_score(certs['tsa'])} lấy từ hồ sơ. Điểm TSA × 40/60, tối đa 40 điểm."
        if tu_duy < raw_tu_duy - 1e-9:
            detail += " Điểm đã chạm trần 40."
        tiles.append({"label": "Điểm tư duy", "value": _fmt_reg_score(tu_duy), "detail": detail, "kind": "value"})
        dx_tsa = certs["tsa"] + uu_points + language_on_100
        bonus_note = "Điểm thi TSA + điểm ưu tiên + điểm thưởng chứng chỉ ngoại ngữ theo thang 100." + uu_note
        if ielts_row is None:
            bonus_note += " Hồ sơ chưa có IELTS từ 5,0 nên điểm thưởng ngoại ngữ = 0."
        else:
            bonus_note += f" IELTS {_fmt_reg_score(certs['ielts'])} được cộng {_fmt_reg_score(language_on_100)} điểm."
        tiles.append({
            "label": "Điểm xét tuyển theo TSA",
            "value": _fmt_reg_score(dx_tsa),
            "detail": bonus_note,
            "kind": "value",
        })

    talent_ready = bool(
        any(key in certs for key, _name, _scale in _BKA_INTL_SCALES)
        or certs.get("tsa") is not None
        or chosen_achievement
        or reward_parts
    )
    if talent_ready:
        national = []
        for key, label in (bundle.get("awards") or {}).items():
            if key in ("HSG quốc gia", "KHKT"):
                national.append(f"{key} {label}")
        tiles.append({
            "label": "Diện 1.1 — xét tuyển thẳng",
            "value": "Không cộng thưởng",
            "detail": "Diện này không có điểm thưởng, chỉ xét điều kiện giải thưởng."
            + (f" Hồ sơ có {', '.join(national)}." if national else " Hồ sơ chưa có giải HSG hoặc KHKT quốc gia."),
            "kind": "source" if national else "missing",
        })
        intl_found = False
        for key, name, scale in _BKA_INTL_SCALES:
            if key not in certs:
                continue
            intl_found = True
            added = 0.0
            if ielts_row is not None:
                try:
                    added = float(ielts_row.get(scale) or 0)
                except (TypeError, ValueError):
                    added = 0.0
            total = certs[key] + added
            if ielts_row is None:
                detail = f"{name} {_fmt_reg_score(certs[key])}. Hồ sơ chưa có IELTS từ 5,0 nên chưa cộng điểm thưởng ngoại ngữ."
            else:
                detail = (
                    f"{name} {_fmt_reg_score(certs[key])} + điểm thưởng IELTS {_fmt_reg_score(certs['ielts'])} "
                    f"= {_fmt_reg_score(added)} trên thang {name}."
                )
            tiles.append({
                "label": f"Diện 1.2 — tổng {name}",
                "value": _fmt_reg_score(total),
                "detail": detail,
                "kind": "value",
            })
        if not intl_found:
            tiles.append({
                "label": "Diện 1.2 — chứng chỉ quốc tế",
                "value": "Chưa có",
                "detail": "Hồ sơ chưa có SAT, ACT, A-Level, AP hoặc IB để cộng điểm thưởng ngoại ngữ.",
                "kind": "missing",
            })
        interview = (
            (((bonus or {}).get("cac_dien_xet_tuyen") or {}).get("dien_1_3") or {}).get("muc_toi_thieu_vao_phong_van")
        )
        try:
            interview_mark = float(interview)
        except (TypeError, ValueError):
            interview_mark = 55.0
        hsnl = (tu_duy or 0) + achievement_points + reward_points
        parts = [f"Tư duy {_fmt_reg_score(tu_duy or 0)}"]
        if chosen_achievement:
            shown = achievement_points
            parts.append(f"thành tích {_fmt_reg_score(shown)} ({chosen_achievement[0]})")
            if chosen_achievement[1] > 50:
                parts[-1] += ", quy về 50"
        else:
            parts.append("thành tích 0 vì hồ sơ chưa có giải được tính")
        if reward_parts:
            reward_text = f"thưởng {_fmt_reg_score(reward_points)} (" + "; ".join(reward_parts) + ")"
            if reward_sum > 10:
                reward_text += ", tổng vượt 10 nên quy về 10"
            parts.append(reward_text)
        else:
            parts.append("thưởng 0")
        if "tsa" not in certs:
            parts.append("chưa có TSA nên tư duy = 0")
        reached = hsnl + 1e-9 >= interview_mark
        parts.append(
            f"ngưỡng vào phỏng vấn {_fmt_reg_score(interview_mark)}: {'đủ' if reached else 'chưa đủ'}"
        )
        tiles.append({
            "label": "Diện 1.3 — tổng hồ sơ năng lực",
            "value": _fmt_reg_score(hsnl),
            "detail": " + ".join(parts[:3]) + ". " + ". ".join(parts[3:]),
            "kind": "value" if reached else "missing",
        })

    ready = []
    missing_combos = []
    for code, subjects in _BKA_THPT_COMBOS:
        absent = [key for key in subjects if key not in exam]
        if absent:
            missing_combos.append(f"{code} thiếu {', '.join(_subject_label(key) for key in absent)}")
            continue
        total = sum(exam[key] for key in subjects) + uu_points
        parts = [f"{_subject_label(key)} {_fmt_reg_score(exam[key])}" for key in subjects]
        detail = " + ".join(parts) + " + điểm ưu tiên." + uu_note
        if "toan" in subjects:
            with_main = (sum(exam[key] for key in subjects) + exam["toan"]) * 3 / 4 + uu_points
            detail += f" Nếu Toán là môn chính: {_fmt_reg_score(with_main)}."
        ready.append({
            "label": f"Điểm xét tuyển THPT {code}",
            "value": _fmt_reg_score(total),
            "detail": detail,
            "kind": "value",
        })
    if exam:
        tiles.extend(ready)
        if missing_combos:
            tiles.append({
                "label": "Tổ hợp THPT còn thiếu môn",
                "value": "Thiếu môn",
                "detail": ". ".join(missing_combos) + ".",
                "kind": "missing",
            })
        third_options = [(key, exam[key]) for key in _BKA_K01_THIRD if key in exam]
        if "toan" in exam and "van" in exam and third_options:
            third_key, third_score = max(third_options, key=lambda item: item[1])
            dx_k = (exam["toan"] * 3 + exam["van"] + third_score * 2) / 2 + uu_points
            choice = f"Môn thứ 3 lấy {_subject_label(third_key)} {_fmt_reg_score(third_score)}"
            if len(third_options) > 1:
                choice += ", cao nhất trong các môn Lý, Hóa, Sinh, Tin đã lưu"
            tiles.append({
                "label": "Điểm xét tuyển tổ hợp K01",
                "value": _fmt_reg_score(dx_k),
                "detail": f"(Toán × 3 + Ngữ văn × 1 + môn thứ 3 × 2) × 1/2 + điểm ưu tiên. {choice}." + uu_note,
                "kind": "value",
            })
        elif "toan" in exam or "van" in exam or third_options:
            tiles.append({
                "label": "Điểm xét tuyển tổ hợp K01",
                "value": "Thiếu môn",
                "detail": "Cần đủ Toán, Ngữ văn và một môn Lý, Hóa, Sinh hoặc Tin trong điểm thi THPT đã lưu.",
                "kind": "missing",
            })
    elif certs or bundle["gpa"] or parsed:
        tiles.append({
            "label": "Điểm xét tuyển THPT",
            "value": "Chưa có",
            "detail": "Chưa lưu điểm thi THPT trong hồ sơ cá nhân.",
            "kind": "missing",
        })

    language_notes: List[str] = []
    if "ielts" in certs:
        ielts = certs["ielts"]
        if ielts >= 5.5:
            language_notes.append("IELTS đủ liên kết quốc tế (≥ 5,5) và chương trình giảng dạy bằng tiếng Anh (≥ 5,0).")
        elif ielts >= 5.0:
            language_notes.append("IELTS đủ chương trình giảng dạy bằng tiếng Anh (≥ 5,0), chưa đủ liên kết quốc tế (cần ≥ 5,5).")
        else:
            language_notes.append("IELTS chưa đạt 5,0 cho chương trình giảng dạy bằng tiếng Anh.")
    if "vstep" in certs:
        if certs["vstep"] in ("B1", "B2", "C1"):
            language_notes.append(f"VSTEP {certs['vstep']} đủ chương trình giảng dạy bằng tiếng Anh.")
        else:
            language_notes.append("VSTEP dưới B1, chưa đủ chương trình giảng dạy bằng tiếng Anh.")
    if "anh" in exam:
        if exam["anh"] >= 6.5:
            language_notes.append(f"Điểm tiếng Anh THPT {_fmt_reg_score(exam['anh'])} đủ chương trình giảng dạy bằng tiếng Anh (≥ 6,5).")
        else:
            language_notes.append(f"Điểm tiếng Anh THPT {_fmt_reg_score(exam['anh'])} chưa đạt 6,5.")
    if language_notes:
        reached = [
            note for note in language_notes
            if "đủ" in note.lower() and "chưa" not in note.lower() and "dưới" not in note.lower()
        ]
        if reached and len(reached) == len(language_notes):
            verdict = "Đạt"
        elif reached:
            verdict = "Đạt một phần"
        else:
            verdict = "Chưa đạt"
        tiles.append({
            "label": "Điều kiện ngoại ngữ",
            "value": verdict,
            "detail": " ".join(language_notes),
            "kind": "value" if verdict == "Đạt" else "missing",
        })

    if bundle["notes"]:
        tiles.append({
            "label": "Hồ sơ chưa dùng được",
            "value": "Bỏ qua",
            "detail": " ".join(bundle["notes"]),
            "kind": "missing",
        })
    if not tiles:
        return {
            "ok": False,
            "error": "Hồ sơ cá nhân chưa có học bạ, điểm thi THPT hoặc chứng chỉ để quy đổi.",
        }
    return {"ok": True, "results": tiles}


_DCN_YEAR_WEIGHTS = (("10", 1), ("11", 1), ("12", 2))
_DCN_CERT_BOUNDS = {"HSA": (0, 150), "TSA": (0, 100)}


def _dcn_subject_options() -> List[Dict[str, str]]:
    options = [{"value": "", "label": "Chọn môn"}]
    options.extend(
        {"value": item["key"], "label": item["label"]}
        for item in dataset_store.PROFILE_SUBJECTS
    )
    return options


def _dcn_calculator_spec() -> Dict[str, Any]:
    """Chọn môn chính và hai môn còn lại. Điểm lấy từ hồ sơ cá nhân."""
    return {
        "lead": (
            "Học bạ, điểm thi tốt nghiệp THPT, điểm ĐGNL (HSA), điểm đánh giá tư duy (TSA) "
            "và điểm ưu tiên lấy từ hồ sơ cá nhân. Chọn môn chính và hai môn còn lại của tổ hợp "
            "để tính điểm học bạ và điểm xét tuyển."
        ),
        "inputs": [
            {
                "id": "mon_chinh",
                "label": "Môn chính M1",
                "group": "Tổ hợp xét tuyển",
                "type": "select",
                "hint": (
                    "M1 nhân hệ số 2 khi tính điểm học bạ. Ngữ văn áp dụng cho Thiết kế thời trang, "
                    "Ngôn ngữ học, Trung Quốc học và các ngành Ngôn ngữ, Văn hóa nước ngoài, "
                    "Du lịch, Khách sạn, Nhà hàng."
                ),
                "options": [
                    {"value": "toan", "label": "Toán"},
                    {"value": "van", "label": "Ngữ văn"},
                ],
            },
            {
                "id": "mon_2",
                "label": "Môn 2",
                "group": "Tổ hợp xét tuyển",
                "type": "select",
                "hint": "Hệ số 1 trong điểm học bạ và trong điểm thi tốt nghiệp THPT.",
                "options": _dcn_subject_options(),
            },
            {
                "id": "mon_3",
                "label": "Môn 3",
                "group": "Tổ hợp xét tuyển",
                "type": "select",
                "hint": "Hệ số 1 trong điểm học bạ và trong điểm thi tốt nghiệp THPT.",
                "options": _dcn_subject_options(),
            },
        ],
    }


def _dcn_priority(profile: Optional[Dict[str, Any]]) -> tuple:
    profile = profile or {}
    region = str(profile.get("khu_vuc") or "")
    obj = str(profile.get("doi_tuong") or "")
    if region not in _PROFILE_REGION_POINTS and obj not in _PROFILE_OBJECT_POINTS:
        return None, ""
    region_points = _PROFILE_REGION_POINTS.get(region, 0.0)
    object_points = _PROFILE_OBJECT_POINTS.get(obj, 0.0)
    total = min(2.75, region_points + object_points)
    parts = []
    if region in _PROFILE_REGION_POINTS:
        parts.append(f"{region} {_fmt_reg_score(region_points)}")
    if obj in _PROFILE_OBJECT_POINTS:
        parts.append(f"đối tượng {obj} {_fmt_reg_score(object_points)}")
    return total, " + ".join(parts)


def _dcn_transcript_component(hoc_ba: Dict[str, Any], key: str) -> Dict[str, Any]:
    parts: List[str] = []
    missing: List[str] = []
    total = 0.0
    for grade, weight in _DCN_YEAR_WEIGHTS:
        grade_raw = hoc_ba.get(grade) if isinstance(hoc_ba.get(grade), dict) else {}
        value = _profile_number(grade_raw.get(key), 0, 10)
        if value is None:
            missing.append(grade)
            continue
        total += value * weight
        shown = _fmt_reg_score(value)
        parts.append(f"lớp {grade}: {shown}" if weight == 1 else f"lớp {grade}: {shown} × {weight}")
    return {
        "total": None if missing else total,
        "missing": missing,
        "detail": "; ".join(parts) if parts else "chưa có điểm học bạ",
    }


def _dcn_from_profile(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    profile = profile or {}
    certs = profile.get("chung_chi") if isinstance(profile.get("chung_chi"), dict) else {}
    exam_raw = profile.get("diem_thi_thu") if isinstance(profile.get("diem_thi_thu"), dict) else {}
    hoc_ba = profile.get("hoc_ba") if isinstance(profile.get("hoc_ba"), dict) else {}
    notes: List[str] = []
    cert_values: Dict[str, float] = {}
    for key, bounds in _DCN_CERT_BOUNDS.items():
        raw = certs.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        value = _profile_number(raw, bounds[0], bounds[1])
        if value is None:
            notes.append(
                f"{key} trong hồ sơ không nằm trong thang {_fmt_reg_score(bounds[0])}–{_fmt_reg_score(bounds[1])}."
            )
            continue
        cert_values[key] = value
    exam: Dict[str, float] = {}
    for key, raw in exam_raw.items():
        value = _profile_number(raw, 0, 10)
        if value is not None:
            exam[str(key)] = value
    uu_tien, uu_label = _dcn_priority(profile)
    return {
        "certs": cert_values,
        "raw_certs": {str(key): str(value) for key, value in certs.items() if str(value).strip()},
        "exam": exam,
        "hoc_ba": hoc_ba,
        "uu_tien": uu_tien,
        "uu_label": uu_label,
        "notes": notes,
    }


def _dcn_profile_groups(profile: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    bundle = _dcn_from_profile(profile)
    hoc_ba = bundle["hoc_ba"]
    transcript_rows = []
    for item in dataset_store.PROFILE_SUBJECTS:
        key = item["key"]
        present = []
        for grade, _weight in _DCN_YEAR_WEIGHTS:
            grade_raw = hoc_ba.get(grade) if isinstance(hoc_ba.get(grade), dict) else {}
            value = _profile_number(grade_raw.get(key), 0, 10)
            present.append(_fmt_reg_score(value) if value is not None else "—")
        if present == ["—", "—", "—"]:
            continue
        transcript_rows.append({
            "label": item["label"],
            "value": " · ".join(present),
            "detail": "Lớp 10 · lớp 11 · lớp 12. Lớp 12 nhân hệ số 2 khi tính KQHB.",
        })
    if not transcript_rows:
        transcript_rows.append({
            "label": "Học bạ",
            "value": "Chưa có",
            "detail": "Chưa lưu điểm lớp 10, 11 hoặc 12.",
        })

    exam = bundle["exam"]
    if exam:
        exam_rows = [
            {"label": _subject_label(key), "value": _fmt_reg_score(value), "detail": "Điểm thi THPT đã lưu."}
            for key, value in exam.items()
        ]
    else:
        exam_rows = [{
            "label": "Điểm thi THPT",
            "value": "Chưa có",
            "detail": "Chưa lưu điểm thi tốt nghiệp THPT trong hồ sơ.",
        }]

    cert_rows = []
    for key, label, scale in (("HSA", "ĐGNL (HSA)", "thang 150"), ("TSA", "ĐGTD (TSA)", "thang 100")):
        value = bundle["certs"].get(key)
        if value is None:
            continue
        cert_rows.append({
            "label": label,
            "value": _fmt_reg_score(value),
            "detail": f"Đã lưu, {scale}.",
        })
    for key, raw in bundle["raw_certs"].items():
        if key in _DCN_CERT_BOUNDS:
            continue
        cert_rows.append({
            "label": key,
            "value": raw,
            "detail": "Chứng chỉ hoặc giải thưởng đã lưu. Quy chế chưa kèm bảng quy đổi sang thang 10.",
        })
    if not cert_rows:
        cert_rows.append({
            "label": "Bài thi và chứng chỉ",
            "value": "Chưa có",
            "detail": "Chưa lưu HSA, TSA, chứng chỉ quốc tế hoặc giải học sinh giỏi.",
        })
    for note in bundle["notes"]:
        cert_rows.append({"label": "Lưu ý", "value": note, "detail": ""})

    if bundle["uu_tien"] is None:
        uu_rows = [{
            "label": "Điểm ưu tiên",
            "value": "Chưa có",
            "detail": "Chưa chọn khu vực hoặc đối tượng ưu tiên.",
        }]
    else:
        uu_rows = [{
            "label": "Điểm ưu tiên",
            "value": _fmt_reg_score(bundle["uu_tien"]),
            "detail": bundle["uu_label"],
        }]
    return [
        {"label": "Học bạ", "rows": transcript_rows},
        {"label": "Điểm thi THPT", "rows": exam_rows},
        {"label": "Bài thi, chứng chỉ và giải thưởng", "rows": cert_rows},
        {"label": "Điểm ưu tiên", "rows": uu_rows},
    ]


def _dcn_priority_note(bundle: Dict[str, Any]) -> str:
    if bundle["uu_tien"] is None:
        return " Chưa có khu vực hoặc đối tượng ưu tiên trong hồ sơ, đang tính bằng 0."
    return f" Điểm ưu tiên {_fmt_reg_score(bundle['uu_tien'])} lấy từ hồ sơ ({bundle['uu_label']})."


def _dcn_scaled_admission(
    raw: Optional[float],
    source_max: float,
    source_label: str,
    method_label: str,
    uu_points: float,
    uu_note: str,
) -> Dict[str, str]:
    """Đổi bài thi sang thang 30 theo tỷ lệ thang điểm, rồi cộng điểm ưu tiên."""
    if raw is None:
        return {
            "label": method_label,
            "value": "Chưa có",
            "detail": f"Chưa lưu {source_label} trong hồ sơ." + uu_note,
            "kind": "missing",
        }
    converted = raw * 30 / source_max
    total = converted + uu_points
    return {
        "label": method_label,
        "value": _fmt_reg_score(total),
        "detail": (
            f"{source_label} {_fmt_reg_score(raw)} × 30/{_fmt_reg_score(source_max)} "
            f"= {_fmt_reg_score(converted)}. "
            f"ĐXT = {_fmt_reg_score(converted)} + điểm ưu tiên."
            + uu_note
            + " Thang 30, tương đương phương thức 3 theo tỷ lệ 1:1."
        ),
        "kind": "value",
    }


def _convert_dcn_scores(
    scores: Dict[str, Any],
    spec: Dict[str, Any],
    profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Tính điểm xét tuyển DCN. ĐGNL và ĐGTD đổi sang thang 30 theo tỷ lệ thang điểm."""
    parsed: Dict[str, Any] = {}
    errors: List[str] = []
    for item in spec["inputs"]:
        value, error = _read_regulation_score(scores.get(item["id"]), item)
        if error:
            errors.append(error)
            continue
        if value is not None and value != "":
            parsed[item["id"]] = value
    if errors:
        return {"ok": False, "error": " ".join(errors)}
    if "mon_2" not in parsed or "mon_3" not in parsed:
        return {"ok": False, "error": "Chọn đủ môn 2 và môn 3 của tổ hợp xét tuyển."}
    main = parsed.get("mon_chinh")
    if main not in ("toan", "van"):
        return {"ok": False, "error": "Chọn môn chính là Toán hoặc Ngữ văn."}
    subjects = [main, parsed["mon_2"], parsed["mon_3"]]
    if len(set(subjects)) < 3:
        return {"ok": False, "error": "Ba môn trong tổ hợp phải khác nhau."}

    bundle = _dcn_from_profile(profile)
    uu_note = _dcn_priority_note(bundle)
    uu_points = float(bundle["uu_tien"] or 0)
    tiles: List[Dict[str, str]] = []

    weighted = []
    missing_bits = []
    kqhb = None
    for key, coef in ((subjects[0], 2), (subjects[1], 1), (subjects[2], 1)):
        component = _dcn_transcript_component(bundle["hoc_ba"], key)
        label = _subject_label(key)
        if component["total"] is None:
            years = ", ".join(f"lớp {grade}" for grade in component["missing"])
            missing_bits.append(f"{label} thiếu {years}")
        weighted.append((label, coef, component))
    if missing_bits:
        tiles.append({
            "label": "KQHB — kết quả học tập THPT",
            "value": "Chưa đủ học bạ",
            "detail": (
                "Cần đủ điểm lớp 10, lớp 11 và lớp 12 của cả ba môn. "
                + ". ".join(missing_bits) + "."
            ),
            "kind": "missing",
        })
    else:
        numerator = sum(component["total"] * coef for _label, coef, component in weighted)
        kqhb = numerator / 16
        breakdown = []
        for label, coef, component in weighted:
            shown = _fmt_reg_score(component["total"])
            if coef == 1:
                breakdown.append(f"{label} = {shown} ({component['detail']})")
            else:
                breakdown.append(f"{label} = {shown}, nhân {coef} ({component['detail']})")
        tiles.append({
            "label": "KQHB — kết quả học tập THPT",
            "value": _fmt_reg_score(kqhb),
            "detail": (
                "Thang điểm 10. "
                + ". ".join(breakdown)
                + f". KQHB = ({_fmt_reg_score(numerator)}) / 16."
            ),
            "kind": "value",
        })

    exam = bundle["exam"]
    absent = [key for key in subjects if key not in exam]
    if absent:
        tiles.append({
            "label": "Phương thức 3 — thi tốt nghiệp THPT",
            "value": "Thiếu môn",
            "detail": (
                "Cần điểm thi của "
                + ", ".join(_subject_label(key) for key in absent)
                + ". Công thức: M1 + M2 + M3 + điểm ưu tiên."
                + uu_note
            ),
            "kind": "missing",
        })
    else:
        raw = sum(exam[key] for key in subjects)
        parts = [f"{_subject_label(key)} {_fmt_reg_score(exam[key])}" for key in subjects]
        tiles.append({
            "label": "Phương thức 3 — thi tốt nghiệp THPT",
            "value": _fmt_reg_score(raw + uu_points),
            "detail": " + ".join(parts) + " + điểm ưu tiên." + uu_note + " Thang điểm 30. Đây là phương thức cơ sở.",
            "kind": "value",
        })

    if kqhb is None:
        tiles.append({
            "label": "Phương thức 2 — học bạ kết hợp chứng chỉ hoặc giải",
            "value": "Chưa đủ học bạ",
            "detail": "Cần đủ học bạ ba môn để có KQHB, rồi tính ĐXT = ĐKQHT × 2 + ĐQĐCC + điểm ưu tiên." + uu_note,
            "kind": "missing",
        })
    else:
        dxt_pt2 = kqhb * 2 + uu_points
        tiles.append({
            "label": "Phương thức 2 — học bạ kết hợp chứng chỉ hoặc giải",
            "value": _fmt_reg_score(dxt_pt2),
            "detail": (
                f"ĐKQHT = KQHB {_fmt_reg_score(kqhb)} trên thang 10. "
                f"ĐXT = {_fmt_reg_score(kqhb)} × 2 + điểm ưu tiên."
                + uu_note
                + " ĐQĐCC đang tính bằng 0 vì quy chế chưa kèm bảng quy đổi chứng chỉ hoặc giải sang thang 10."
            ),
            "kind": "value",
        })

    tiles.append(_dcn_scaled_admission(
        bundle["certs"].get("HSA"),
        150,
        "HSA",
        "Phương thức 4 — đánh giá năng lực",
        uu_points,
        uu_note,
    ))
    tiles.append(_dcn_scaled_admission(
        bundle["certs"].get("TSA"),
        100,
        "TSA",
        "Phương thức 5 — đánh giá tư duy",
        uu_points,
        uu_note,
    ))
    if bundle["notes"]:
        tiles.append({
            "label": "Hồ sơ chưa dùng được",
            "value": "Bỏ qua",
            "detail": " ".join(bundle["notes"]),
            "kind": "missing",
        })
    return {"ok": True, "results": tiles}


_KHA_EQUIV_FIELDS = (
    ("thpt", "diem_tn_thpt", "TN THPT"),
    ("hsa", "diem_hsa", "HSA"),
    ("sat", "diem_sat", "SAT"),
    ("vact", "diem_v_act", "V-ACT"),
    ("tsa", "diem_tsa", "TSA"),
)
_KHA_CERT_BOUNDS = {
    "HSA": (0, 150),
    "SAT": (400, 1600),
    "V-ACT": (0, 1200),
    "TSA": (0, 100),
    "IELTS": (0, 9),
    "TOEFL iBT": (0, 120),
    "TOEIC": (0, 990),
}
_KHA_METHOD_CERTS = (
    ("hsa", "HSA", "HSA"),
    ("sat", "SAT", "SAT"),
    ("vact", "V-ACT", "V-ACT"),
    ("tsa", "TSA", "TSA"),
)


def _kha_calculator_spec() -> Dict[str, Any]:
    """Chọn một phương thức gốc. Ba môn chỉ dùng khi gốc là điểm thi tốt nghiệp THPT."""
    when_thpt = {"id": "nguon", "value": "thpt"}
    return {
        "lead": (
            "Chọn một phương thức gốc. Chỉ điểm đó được quy đổi sang các cột còn lại trong bảng, "
            "kể cả khi hồ sơ đồng thời có điểm thi tốt nghiệp THPT, HSA, SAT, V-ACT và TSA. "
            "Nếu gốc là điểm thi tốt nghiệp THPT, chọn thêm ba môn; điểm lấy từ hồ sơ. "
            "Kết quả là khoảng trong bảng; số ước tính nằm trong khoảng đó. Bảng không cộng điểm ưu tiên."
        ),
        "inputs": [
            {
                "id": "nguon",
                "label": "Phương thức gốc",
                "group": "Phương thức gốc",
                "type": "select",
                "hint": "Điểm của phương thức này là mốc để so với HSA, SAT, V-ACT, TSA và điểm thi tốt nghiệp THPT.",
                "options": [
                    {"value": "", "label": "Chọn phương thức"},
                    {"value": "thpt", "label": "Điểm thi tốt nghiệp THPT"},
                    {"value": "hsa", "label": "HSA"},
                    {"value": "sat", "label": "SAT"},
                    {"value": "vact", "label": "V-ACT"},
                    {"value": "tsa", "label": "TSA"},
                ],
            },
            {
                "id": "mon_1",
                "label": "Môn 1",
                "group": "Tổ hợp thi tốt nghiệp THPT",
                "type": "select",
                "hint": "Cộng với môn 2 và môn 3 thành điểm TN THPT thang 30.",
                "show_when": when_thpt,
                "options": _dcn_subject_options(),
            },
            {
                "id": "mon_2",
                "label": "Môn 2",
                "group": "Tổ hợp thi tốt nghiệp THPT",
                "type": "select",
                "hint": "Cộng với môn 1 và môn 3 thành điểm TN THPT thang 30.",
                "show_when": when_thpt,
                "options": _dcn_subject_options(),
            },
            {
                "id": "mon_3",
                "label": "Môn 3",
                "group": "Tổ hợp thi tốt nghiệp THPT",
                "type": "select",
                "hint": "Cộng với môn 1 và môn 2 thành điểm TN THPT thang 30.",
                "show_when": when_thpt,
                "options": _dcn_subject_options(),
            },
        ],
    }


def _load_kha_table() -> Optional[Dict[str, Any]]:
    path = os.path.join(CONVERSION_REGULATION_DIR, "kha-2026.md")
    try:
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
    except OSError:
        return None
    mark = "<!-- điểm thưởng -->"
    if mark not in source:
        return None
    try:
        data = json.loads(source.split(mark, 1)[1].strip())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _kha_span(value: Any) -> Optional[tuple]:
    """Khoảng '28-30', '77,90-100', '7.5 – 9.0', '≥ 102' hoặc một ngưỡng."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value), None
    text = str(value or "").strip().replace("–", "-").replace("—", "-")
    if not text:
        return None
    open_ended = text.startswith("≥") or text.startswith(">=")
    text = text.replace("≥", "").replace(">=", "").replace(",", ".").strip()
    if "-" in text:
        left, right = [part.strip() for part in text.split("-", 1)]
        try:
            return float(left), float(right)
        except ValueError:
            return None
    try:
        number = float(text)
    except ValueError:
        return None
    if open_ended:
        return number, None
    return number, number


def _kha_bands() -> List[Dict[str, Any]]:
    data = _load_kha_table() or {}
    bands = []
    for item in data.get("quy_doi_tuong_duong_diem_trung_tuyen") or []:
        if not isinstance(item, dict):
            continue
        spans = {}
        for key, field, _label in _KHA_EQUIV_FIELDS:
            span = _kha_span(item.get(field))
            if span is None or span[1] is None:
                spans = {}
                break
            spans[key] = span
        if len(spans) == len(_KHA_EQUIV_FIELDS):
            bands.append(spans)
    return bands


def _kha_english_rows() -> List[Dict[str, Any]]:
    data = _load_kha_table() or {}
    rows = []
    for item in data.get("quy_doi_chung_chi_tieng_anh") or []:
        if not isinstance(item, dict):
            continue
        toeic = item.get("toeic") if isinstance(item.get("toeic"), dict) else {}
        try:
            score = float(item.get("diem_quy_doi"))
        except (TypeError, ValueError):
            continue
        ielts = _kha_span(item.get("ielts"))
        toefl = _kha_span(item.get("toefl_ibt"))
        if ielts is None or toefl is None:
            continue
        if ielts[1] is not None and abs(ielts[1] - ielts[0]) < 1e-9:
            ielts = (ielts[0], None)
        rows.append({
            "ielts": ielts,
            "toefl": toefl,
            "toeic_lr": _kha_span(toeic.get("listening_reading")),
            "toeic_s": _kha_span(toeic.get("speaking")),
            "toeic_w": _kha_span(toeic.get("writing")),
            "score": score,
            "ielts_label": str(item.get("ielts") or ""),
            "toefl_label": str(item.get("toefl_ibt") or ""),
        })
    return rows


def _kha_band_for(bands: List[Dict[str, Any]], key: str, score: float) -> Optional[Dict[str, Any]]:
    matched = []
    ceiling = None
    floor = None
    for band in bands:
        lo, hi = band[key]
        if floor is None or lo < floor:
            floor = lo
        if ceiling is None or hi > ceiling:
            ceiling = hi
        if lo - 1e-9 <= score <= hi + 1e-9:
            matched.append(band)
    if matched:
        return max(matched, key=lambda band: band["thpt"][0])
    if ceiling is not None and score > ceiling:
        return max(bands, key=lambda band: band[key][1])
    return None


def _kha_interpolate(score: float, source: tuple, target: tuple) -> float:
    src_lo, src_hi = source
    dst_lo, dst_hi = target
    width = src_hi - src_lo
    if abs(width) < 1e-9:
        return dst_lo
    ratio = min(1.0, max(0.0, (score - src_lo) / width))
    return dst_lo + ratio * (dst_hi - dst_lo)


def _kha_range_text(span: tuple) -> str:
    lo, hi = span
    if hi is None or abs(hi - lo) < 1e-9:
        return _fmt_reg_score(lo)
    return f"{_fmt_reg_score(lo)}–{_fmt_reg_score(hi)}"


def _kha_threshold_row(rows: List[Dict[str, Any]], key: str, score: float) -> Optional[Dict[str, Any]]:
    eligible = [row for row in rows if score + 1e-9 >= row[key][0]]
    if not eligible:
        return None
    row = max(eligible, key=lambda item: item[key][0])
    hi = row[key][1]
    if hi is not None and score > hi + 1e-9:
        return None
    return row


def _kha_from_profile(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    profile = profile or {}
    certs = profile.get("chung_chi") if isinstance(profile.get("chung_chi"), dict) else {}
    exam_raw = profile.get("diem_thi_thu") if isinstance(profile.get("diem_thi_thu"), dict) else {}
    notes: List[str] = []
    values: Dict[str, float] = {}
    for key, bounds in _KHA_CERT_BOUNDS.items():
        raw = certs.get(key)
        if raw is None or str(raw).strip() == "":
            continue
        value = _profile_number(raw, bounds[0], bounds[1])
        if value is None:
            notes.append(
                f"{key} trong hồ sơ không nằm trong thang {_fmt_reg_score(bounds[0])}–{_fmt_reg_score(bounds[1])}."
            )
            continue
        values[key] = value
    exam: Dict[str, float] = {}
    for key, raw in exam_raw.items():
        value = _profile_number(raw, 0, 10)
        if value is not None:
            exam[str(key)] = value
    uu_tien, uu_label = _dcn_priority(profile)
    return {"certs": values, "exam": exam, "uu_tien": uu_tien, "uu_label": uu_label, "notes": notes, "raw_certs": certs}


def _kha_profile_groups(profile: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    bundle = _kha_from_profile(profile)
    exam = bundle["exam"]
    if exam:
        exam_rows = [
            {"label": _subject_label(key), "value": _fmt_reg_score(value), "detail": "Điểm thi THPT đã lưu."}
            for key, value in exam.items()
        ]
    else:
        exam_rows = [{
            "label": "Điểm thi THPT",
            "value": "Chưa có",
            "detail": "Chưa lưu điểm thi tốt nghiệp THPT trong hồ sơ.",
        }]
    method_rows = []
    for _key, cert_key, label in _KHA_METHOD_CERTS:
        value = bundle["certs"].get(cert_key)
        if value is None:
            continue
        method_rows.append({
            "label": label,
            "value": _fmt_reg_score(value),
            "detail": "Dùng cho bảng quy đổi tương đương.",
        })
    if not method_rows:
        method_rows.append({
            "label": "HSA, SAT, V-ACT, TSA",
            "value": "Chưa có",
            "detail": "Chưa lưu điểm các bài thi trong hồ sơ.",
        })
    english_rows = []
    for key in ("IELTS", "TOEFL iBT", "TOEIC"):
        value = bundle["certs"].get(key)
        if value is None:
            continue
        english_rows.append({
            "label": key,
            "value": _fmt_reg_score(value),
            "detail": "Dùng cho bảng quy đổi chứng chỉ tiếng Anh.",
        })
    speaking = str((bundle["raw_certs"] or {}).get("TOEIC SW") or "").strip()
    if speaking:
        english_rows.append({
            "label": "TOEIC SW",
            "value": speaking,
            "detail": "Bảng NEU yêu cầu điểm nói và điểm viết riêng.",
        })
    if not english_rows:
        english_rows.append({
            "label": "Chứng chỉ tiếng Anh",
            "value": "Chưa có",
            "detail": "Chưa lưu IELTS, TOEFL iBT hoặc TOEIC.",
        })
    for note in bundle["notes"]:
        english_rows.append({"label": "Lưu ý", "value": note, "detail": ""})
    if bundle["uu_tien"] is None:
        uu_rows = [{
            "label": "Điểm ưu tiên",
            "value": "Chưa có",
            "detail": "Bảng quy đổi năm 2026 không cộng điểm ưu tiên.",
        }]
    else:
        uu_rows = [{
            "label": "Điểm ưu tiên",
            "value": _fmt_reg_score(bundle["uu_tien"]),
            "detail": f"{bundle['uu_label']}. Bảng quy đổi không cộng điểm này.",
        }]
    return [
        {"label": "Điểm thi THPT", "rows": exam_rows},
        {"label": "Bài thi quy đổi tương đương", "rows": method_rows},
        {"label": "Chứng chỉ tiếng Anh", "rows": english_rows},
        {"label": "Điểm ưu tiên", "rows": uu_rows},
    ]


def _kha_cross_tiles(
    source_key: str,
    source_score: float,
    source_note: str,
    bands: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Một điểm nguồn ra khoảng tương đương của từng cột còn lại trong bảng."""
    labels = {key: label for key, _field, label in _KHA_EQUIV_FIELDS}
    source_label = labels[source_key]
    shown = _fmt_reg_score(source_score)
    band = _kha_band_for(bands, source_key, source_score)
    if band is None:
        return [{
            "label": f"{source_label} (gốc)",
            "value": shown,
            "detail": f"{source_note}Thấp hơn mức thấp nhất trong bảng, chưa quy đổi sang cột khác.",
            "kind": "source",
        }]
    source_span = band[source_key]
    above = source_score > source_span[1] + 1e-9
    if above:
        belong = f"cao hơn mức cao nhất, lấy khoảng {_kha_range_text(source_span)}"
    else:
        belong = f"thuộc {_kha_range_text(source_span)}"
    tiles = [{
        "label": f"{source_label} (gốc)",
        "value": shown,
        "detail": f"{source_note}{belong[0].upper()}{belong[1:]}.",
        "kind": "source",
    }]
    for key, _field, label in _KHA_EQUIV_FIELDS:
        if key == source_key:
            continue
        estimate = _kha_interpolate(source_score, source_span, band[key])
        tiles.append({
            "label": f"{source_label} → {label}",
            "value": _kha_range_text(band[key]),
            "detail": (
                f"{source_note}{source_label} {_fmt_reg_score(source_score)} {belong}. "
                f"Tương đương {label} {_kha_range_text(band[key])}. "
                f"Ước tính trong khoảng: {_fmt_reg_score(estimate)}."
            ),
            "kind": "value",
        })
    return tiles


def _convert_kha_scores(
    scores: Dict[str, Any],
    spec: Dict[str, Any],
    profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Xếp điểm hồ sơ vào bảng khoảng của NEU và quy đổi chứng chỉ tiếng Anh."""
    parsed: Dict[str, Any] = {}
    errors: List[str] = []
    for item in spec["inputs"]:
        value, error = _read_regulation_score(scores.get(item["id"]), item)
        if error:
            errors.append(error)
            continue
        if value not in (None, ""):
            parsed[item["id"]] = value
    if errors:
        return {"ok": False, "error": " ".join(errors)}
    source = parsed.get("nguon")
    labels = {key: label for key, _field, label in _KHA_EQUIV_FIELDS}
    if source not in labels:
        return {"ok": False, "error": "Chọn một phương thức gốc để quy đổi."}
    chosen = [parsed.get("mon_1"), parsed.get("mon_2"), parsed.get("mon_3")]
    if source == "thpt":
        if not all(chosen):
            return {"ok": False, "error": "Chọn đủ ba môn tổ hợp tốt nghiệp THPT."}
        if len(set(chosen)) < 3:
            return {"ok": False, "error": "Ba môn trong tổ hợp phải khác nhau."}

    bundle = _kha_from_profile(profile)
    bands = _kha_bands()
    if not bands:
        return {"ok": False, "error": "Chưa đọc được bảng quy đổi của Đại học Kinh tế Quốc dân."}
    tiles: List[Dict[str, str]] = []

    if source == "thpt":
        exam = bundle["exam"]
        absent = [key for key in chosen if key not in exam]
        if absent:
            tiles.append({
                "label": "Điểm TN THPT",
                "value": "Thiếu môn",
                "detail": "Cần điểm thi của " + ", ".join(_subject_label(key) for key in absent) + ".",
                "kind": "missing",
            })
        else:
            total = sum(exam[key] for key in chosen)
            parts = " + ".join(f"{_subject_label(key)} {_fmt_reg_score(exam[key])}" for key in chosen)
            tiles.extend(_kha_cross_tiles("thpt", total, parts + ". ", bands))
    else:
        cert_key = next(item[1] for item in _KHA_METHOD_CERTS if item[0] == source)
        raw = bundle["certs"].get(cert_key)
        if raw is None:
            return {
                "ok": False,
                "error": f"Hồ sơ chưa có điểm {labels[source]} để làm phương thức gốc.",
            }
        tiles.extend(_kha_cross_tiles(source, raw, "", bands))

    english = _kha_english_rows()
    english_hits = []
    ielts = bundle["certs"].get("IELTS")
    if ielts is not None:
        row = _kha_threshold_row(english, "ielts", ielts)
        if row is None:
            tiles.append({
                "label": "Chứng chỉ tiếng Anh",
                "value": "Chưa có trong bảng",
                "detail": f"IELTS {_fmt_reg_score(ielts)} thấp hơn 5,5 hoặc cao hơn 9,0.",
                "kind": "missing",
            })
        else:
            english_hits.append((row["score"], f"IELTS {_fmt_reg_score(ielts)} ({row['ielts_label']})"))
    toefl = bundle["certs"].get("TOEFL iBT")
    if toefl is not None:
        row = _kha_threshold_row(english, "toefl", toefl)
        if row is None:
            tiles.append({
                "label": "TOEFL iBT",
                "value": "Chưa có trong bảng",
                "detail": f"TOEFL iBT {_fmt_reg_score(toefl)} thấp hơn 46.",
                "kind": "missing",
            })
        else:
            english_hits.append((row["score"], f"TOEFL iBT {_fmt_reg_score(toefl)} ({row['toefl_label']})"))
    if english_hits:
        best_score, best_label = max(english_hits, key=lambda item: item[0])
        tiles.append({
            "label": "Chứng chỉ tiếng Anh",
            "value": _fmt_reg_score(best_score),
            "detail": (
                f"{best_label} quy đổi {_fmt_reg_score(best_score)} điểm thang 10. "
                "Dùng để kết hợp với điểm thi tốt nghiệp THPT hoặc bài thi đánh giá năng lực."
            ),
            "kind": "value",
        })
    toeic = bundle["certs"].get("TOEIC")
    if toeic is not None:
        tiles.append({
            "label": "TOEIC",
            "value": "Thiếu nói và viết",
            "detail": (
                f"TOEIC nghe-đọc {_fmt_reg_score(toeic)}. "
                "Bảng yêu cầu đồng thời điểm nghe-đọc, nói và viết; hồ sơ chưa có điểm nói và viết riêng."
            ),
            "kind": "missing",
        })

    if bundle["notes"]:
        tiles.append({
            "label": "Hồ sơ chưa dùng được",
            "value": "Bỏ qua",
            "detail": " ".join(bundle["notes"]),
            "kind": "missing",
        })
    if not tiles:
        return {
            "ok": False,
            "error": "Chọn phương thức gốc và điểm tương ứng trong hồ sơ để quy đổi.",
        }
    return {"ok": True, "results": tiles}


def calculate_regulation_scores(
    code: str,
    year: int,
    scores: Dict[str, Any],
    profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    catalog = _load_conversion_regulations()
    document = next(
        (
            item for item in catalog["documents"]
            if item["code"] == code and int(item["year"]) == int(year)
        ),
        None,
    )
    if document is None:
        return {"ok": False, "error": "Chưa có quy chế cho trường và năm đã chọn."}
    spec = document.get("calculator")
    if not spec:
        return {"ok": False, "error": "Quy chế trường này chưa có công thức để quy đổi điểm."}
    if code == "BKA":
        return _convert_bka_scores(scores, spec, profile)
    if code == "DCN":
        return _convert_dcn_scores(scores, spec, profile)
    if code == "KHA":
        return _convert_kha_scores(scores, spec, profile)
    return {"ok": False, "error": "Chưa hỗ trợ quy đổi cho trường này."}


def _shared_profile_groups(profile: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Học bạ, điểm thi, chứng chỉ và ưu tiên — dùng cho mọi quy chế chưa có cách đọc riêng."""
    profile = profile or {}
    hoc_ba = profile.get("hoc_ba") if isinstance(profile.get("hoc_ba"), dict) else {}
    exam_raw = profile.get("diem_thi_thu") if isinstance(profile.get("diem_thi_thu"), dict) else {}
    certs = profile.get("chung_chi") if isinstance(profile.get("chung_chi"), dict) else {}

    transcript_rows = []
    for item in dataset_store.PROFILE_SUBJECTS:
        present = []
        found = False
        for grade in ("10", "11", "12"):
            grade_raw = hoc_ba.get(grade) if isinstance(hoc_ba.get(grade), dict) else {}
            value = _profile_number(grade_raw.get(item["key"]), 0, 10)
            if value is None:
                present.append("—")
                continue
            found = True
            present.append(_fmt_reg_score(value))
        if not found:
            continue
        transcript_rows.append({
            "label": item["label"],
            "value": " · ".join(present),
            "detail": "Lớp 10 · lớp 11 · lớp 12, lấy từ hồ sơ cá nhân.",
        })
    if not transcript_rows:
        transcript_rows.append({
            "label": "Học bạ",
            "value": "Chưa có",
            "detail": "Chưa lưu điểm lớp 10, 11 hoặc 12.",
        })

    exam_rows = []
    for item in dataset_store.PROFILE_SUBJECTS:
        value = _profile_number(exam_raw.get(item["key"]), 0, 10)
        if value is None:
            continue
        exam_rows.append({
            "label": item["label"],
            "value": _fmt_reg_score(value),
            "detail": "Điểm thi THPT đã lưu trong hồ sơ cá nhân.",
        })
    if not exam_rows:
        exam_rows.append({
            "label": "Điểm thi THPT",
            "value": "Chưa có",
            "detail": "Chưa lưu điểm thi tốt nghiệp THPT trong hồ sơ.",
        })

    cert_rows = []
    for key, raw in certs.items():
        text = str(raw or "").strip()
        if not text:
            continue
        cert_rows.append({
            "label": str(key),
            "value": text,
            "detail": "Đã lưu trong hồ sơ cá nhân.",
        })
    if not cert_rows:
        cert_rows.append({
            "label": "Chứng chỉ",
            "value": "Chưa có",
            "detail": "Chưa lưu chứng chỉ, bài thi hoặc giải thưởng.",
        })

    uu_tien, uu_label = _dcn_priority(profile)
    if uu_tien is None:
        uu_rows = [{
            "label": "Điểm ưu tiên",
            "value": "Chưa có",
            "detail": "Chưa chọn khu vực hoặc đối tượng ưu tiên.",
        }]
    else:
        uu_rows = [{
            "label": "Điểm ưu tiên",
            "value": _fmt_reg_score(uu_tien),
            "detail": uu_label,
        }]
    return [
        {"label": "Học bạ", "rows": transcript_rows},
        {"label": "Điểm thi THPT", "rows": exam_rows},
        {"label": "Chứng chỉ, bài thi và giải thưởng", "rows": cert_rows},
        {"label": "Điểm ưu tiên", "rows": uu_rows},
    ]


def regulation_profile_groups(code: str, profile: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Mọi quy chế đều lấy điểm từ hồ sơ cá nhân. Trường có công thức riêng thì hiển thị theo công thức đó."""
    if code == "BKA":
        return _bka_profile_groups(profile)
    if code == "DCN":
        return _dcn_profile_groups(profile)
    if code == "KHA":
        return _kha_profile_groups(profile)
    return _shared_profile_groups(profile)


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "static"),
    )
    app.config["SECRET_KEY"] = "huong-nghiep-tuyen-sinh"
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["OUTPUT_DIR"] = os.path.join(ROOT, "data", "output")
    app.config["DATASETS_DIR"] = os.path.join(ROOT, "data", "datasets")
    app.config["UPLOADS_DIR"] = os.path.join(ROOT, "data", "uploads")
    os.makedirs(app.config["OUTPUT_DIR"], exist_ok=True)
    os.makedirs(app.config["DATASETS_DIR"], exist_ok=True)
    os.makedirs(app.config["UPLOADS_DIR"], exist_ok=True)

    # Nạp dữ liệu đã lưu (nếu có) để dùng lại sau khi restart
    _saved_crawl = dataset_store.load_admissions(ROOT)
    if _saved_crawl and (_saved_crawl.get("admissions") or []):
        app.config["LAST_CRAWL"] = _saved_crawl
    _saved_qd = dataset_store.load_quy_doi(ROOT)
    if _saved_qd and (_saved_qd.get("rows") is not None or _saved_qd.get("images")):
        app.config["LAST_QUY_DOI"] = _saved_qd
    _saved_methods = dataset_store.load_phuong_thuc(ROOT)
    if _saved_methods and (_saved_methods.get("records") or []):
        app.config["LAST_METHODS"] = _saved_methods

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/schools")
    def schools_page():
        return render_template("schools.html")

    @app.route("/crawl")
    def crawl_page():
        return redirect(url_for("phuong_thuc_page"))

    @app.route("/thu-thap/phuong-thuc")
    def phuong_thuc_page():
        return render_template("thu_thap/phuong_thuc.html")

    @app.route("/thu-thap/diem")
    def diem_page():
        return render_template("thu_thap/diem.html")

    @app.route("/thu-thap/quy-doi")
    def thu_thap_quy_doi_page():
        return render_template(
            "thu_thap/quy_doi.html",
            regulations=_load_conversion_regulations(),
        )

    @app.route("/thu-thap/diem-cong")
    def diem_cong_page():
        return render_template("thu_thap/diem_cong.html")

    @app.route("/thu-thap/uu-tien")
    def uu_tien_page():
        return render_template("thu_thap/uu_tien.html")

    @app.route("/thu-thap/bang-cap")
    def bang_cap_page():
        return render_template("thu_thap/bang_cap.html", catalog=_load_certificate_catalog())

    @app.route("/thu-thap/to-hop")
    def to_hop_page():
        return render_template("thu_thap/to_hop.html", catalog=_load_subject_combinations())

    @app.route("/kiem-chung")
    def kiem_chung_page():
        return render_template("kiem_chung.html")

    @app.route("/ca-nhan")
    def ca_nhan_page():
        return render_template(
            "ca_nhan.html",
            cert_groups=_profile_cert_groups(_load_certificate_catalog()),
            subjects=dataset_store.PROFILE_SUBJECTS,
        )

    @app.get("/api/ca-nhan")
    def api_ca_nhan_get():
        return jsonify({"ok": True, "profile": dataset_store.load_profile(ROOT)})

    @app.post("/api/ca-nhan")
    def api_ca_nhan_save():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"ok": False, "error": "Dữ liệu không hợp lệ."}), 400
        profile = dataset_store.save_profile(ROOT, data)
        return jsonify({"ok": True, "profile": profile})

    @app.route("/quy-doi")
    def quy_doi_page():
        return render_template("quy_doi.html")

    @app.post("/api/quy-che/tinh")
    def api_quy_che_tinh():
        data = request.get_json(silent=True) or {}
        code = str(data.get("code") or "").strip().upper()
        try:
            year = int(data.get("year") or 0)
        except (TypeError, ValueError):
            year = 0
        scores = data.get("scores") if isinstance(data.get("scores"), dict) else {}
        if not code or not year:
            return jsonify({"ok": False, "error": "Chọn trường và năm trước khi quy đổi."}), 400
        profile = dataset_store.load_profile(ROOT)
        return jsonify(calculate_regulation_scores(code, year, scores, profile))

    @app.get("/api/quy-che/ho-so")
    def api_quy_che_ho_so():
        code = str(request.args.get("code") or "").strip().upper()
        profile = dataset_store.load_profile(ROOT)
        groups = regulation_profile_groups(code, profile)
        if not groups:
            return jsonify({"ok": False, "error": "Quy chế trường này chưa lấy điểm từ hồ sơ cá nhân."}), 404
        return jsonify({"ok": True, "groups": groups})

    @app.route("/danh-gia")
    def danh_gia_page():
        return render_template("danh_gia.html")

    @app.route("/du-lieu")
    def datasets_page():
        return render_template("datasets.html")
    # ---------- API: Danh bạ trường ----------
    def _working_directory(refresh: bool = False) -> SchoolDirectory:
        """Danh sách đang hiện lấy từ file đã lưu. Làm mới thì nạp lại từ config."""
        directory = SchoolDirectory()
        saved = dataset_store.load_schools(ROOT) if not refresh else None
        saved_rows = (saved or {}).get("schools") or []
        if saved_rows:
            schools = {}
            for item in saved_rows:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code") or "").strip().upper()
                if not code:
                    continue
                entry = dict(item)
                entry["code"] = code
                schools[code] = entry
            directory.schools = schools
            return directory
        directory.load(force_refresh=refresh)
        return directory

    @app.post("/api/schools/fetch")
    def api_schools_fetch():
        data = request.get_json(silent=True) or {}
        refresh = bool(data.get("refresh", False))
        include_profile = bool(data.get("include_profile", False))
        # Mặc định vẫn lấy website + địa chỉ; tắt khi chỉ lọc/xuất lại Excel
        include_contact = bool(data.get("include_contact", True))
        school_filter = data.get("filter", "all")  # all | dai_hoc | cao_dang | hoc_vien | dai_hoc_hoc_vien
        keyword = (data.get("keyword") or "").strip()

        directory = _working_directory(refresh=refresh)

        # Bổ sung hồ sơ đầy đủ còn thiếu (chỉ khi bật option; giới hạn để tránh timeout)
        if include_profile:
            missing_full = sum(
                1 for inf in directory.schools.values()
                if not (inf.get("thong_tin_chung") or inf.get("vi_the_thanh_tuu"))
            )
            if 0 < missing_full <= 30:
                directory.enrich_profiles(only_missing=True, contact_only=False)
                directory._save_cache(directory.schools)

        type_map = {
            "all": None,
            "dai_hoc": "dai_hoc",
            "cao_dang": "cao_dang",
            "hoc_vien": "hoc_vien",
            "dai_hoc_hoc_vien": "dai_hoc_hoc_vien",
        }
        stype = type_map.get(school_filter, None)
        rows = directory.list_schools(school_type=stype, keyword=keyword)
        codes = [r["code"] for r in rows]

        # Xuất file nền
        excel_path = os.path.join(app.config["OUTPUT_DIR"], "danh_sach_ma_truong.xlsx")
        json_path = os.path.join(app.config["OUTPUT_DIR"], "danh_sach_ma_truong.json")
        try:
            directory.export_excel(output_path=excel_path, school_type=stype)
            directory.export_json(output_path=json_path, also_update_config=False)
            dataset_store.copy_schools_exports_stamp(ROOT)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

        stats = {}
        for r in rows:
            lb = r.get("type_label") or "?"
            stats[lb] = stats.get(lb, 0) + 1
        with_profile = sum(
            1 for r in rows
            if r.get("thong_tin_chung") or r.get("vi_the_thanh_tuu")
        )
        with_contact = sum(
            1 for r in rows
            if r.get("website") and r.get("dia_chi")
        )

        return jsonify({
            "ok": True,
            "total": len(rows),
            "stats": stats,
            "with_profile": with_profile,
            "with_contact": with_contact,
            "schools": rows,
            "codes_text": format_codes_for_copy(codes, one_per_line=True),
            "excel_url": url_for("download_file", name="danh_sach_ma_truong.xlsx"),
            "txt_url": url_for("download_codes_txt"),
            "profile_fetched": include_profile,
            "contact_fetched": include_contact and not include_profile,
        })

    @app.post("/api/schools/delete")
    def api_schools_delete():
        """Xoá một hoặc nhiều trường khỏi danh bạ đã lưu."""
        data = request.get_json(silent=True) or {}
        codes = []
        seen = set()
        for raw in data.get("codes") or []:
            code = str(raw or "").strip().upper()
            if not code or code in seen:
                continue
            seen.add(code)
            codes.append(code)
        if not codes:
            return jsonify({"ok": False, "error": "Chưa chọn trường để xoá."}), 400

        directory = _working_directory(refresh=False)
        deleted = [code for code in codes if code in directory.schools]
        missing = [code for code in codes if code not in directory.schools]
        for code in deleted:
            directory.schools.pop(code, None)

        excel_path = os.path.join(app.config["OUTPUT_DIR"], "danh_sach_ma_truong.xlsx")
        json_path = os.path.join(app.config["OUTPUT_DIR"], "danh_sach_ma_truong.json")
        try:
            # Chỉ xoá bản danh sách đã lưu. config/schools_all.json giữ nguyên
            # để «Làm mới từ web» nạp và thu thập lại được.
            directory.export_excel(output_path=excel_path)
            directory.export_json(output_path=json_path, also_update_config=False)
            dataset_store.copy_schools_exports_stamp(ROOT)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

        rows = directory.list_schools()
        return jsonify({
            "ok": True,
            "deleted": deleted,
            "missing": missing,
            "total": len(rows),
            "schools": rows,
            "excel_url": url_for("download_file", name="danh_sach_ma_truong.xlsx"),
        })

    @app.post("/api/schools/update")
    def api_schools_update():
        """Sửa website, domain điểm chuẩn hoặc link quy chế của một trường đã lưu."""
        data = request.get_json(silent=True) or {}
        code = str(data.get("code") or "").strip().upper()
        field = str(data.get("field") or "").strip()
        allowed = {"website", "domain_diem_chuan", "link_quy_che"}
        if not code or field not in allowed:
            return jsonify({"ok": False, "error": "Ô cần sửa không hợp lệ."}), 400
        value = str(data.get("value") or "").strip()[:500]
        directory = _working_directory(refresh=False)
        info = directory.schools.get(code)
        if not info:
            return jsonify({"ok": False, "error": "Không tìm thấy trường."}), 404
        info[field] = value
        excel_path = os.path.join(app.config["OUTPUT_DIR"], "danh_sach_ma_truong.xlsx")
        json_path = os.path.join(app.config["OUTPUT_DIR"], "danh_sach_ma_truong.json")
        try:
            directory.export_excel(output_path=excel_path)
            directory.export_json(output_path=json_path, also_update_config=False)
            dataset_store.copy_schools_exports_stamp(ROOT)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        row = next((item for item in directory.list_schools() if item["code"] == code), None)
        return jsonify({"ok": True, "school": row})

    @app.post("/api/schools/notices/stream")
    def api_schools_notices_stream():
        """Tìm domain điểm chuẩn và link quy chế trên website từng trường."""
        from crawlers.school_directory import discover_admission_notices

        data = request.get_json(silent=True) or {}
        refresh = bool(data.get("refresh"))
        directory = _working_directory(refresh=False)
        targets = []
        for code, info in directory.schools.items():
            if refresh or not info.get("notices_checked"):
                if info.get("website"):
                    targets.append(code)
                else:
                    info["domain_diem_chuan"] = ""
                    info["link_quy_che"] = ""
                    info["notices_checked"] = True
        targets.sort()

        @stream_with_context
        def generate():
            total = len(targets)
            yield json.dumps({
                "type": "start",
                "total": total,
            }, ensure_ascii=False) + "\n"
            done = 0
            found_domain = 0
            found_link = 0

            def job(code: str):
                info = directory.schools.get(code) or {}
                notices = discover_admission_notices(info.get("website") or "")
                return code, notices

            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = [pool.submit(job, code) for code in targets]
                for fut in as_completed(futures):
                    code, notices = fut.result()
                    info = directory.schools.get(code) or {}
                    info["domain_diem_chuan"] = notices.get("domain_diem_chuan") or ""
                    info["link_quy_che"] = notices.get("link_quy_che") or ""
                    info["notices_checked"] = True
                    directory.schools[code] = info
                    done += 1
                    if info["domain_diem_chuan"]:
                        found_domain += 1
                    if info["link_quy_che"]:
                        found_link += 1
                    yield json.dumps({
                        "type": "school",
                        "index": done,
                        "total": total,
                        "code": code,
                        "domain_diem_chuan": info["domain_diem_chuan"],
                        "link_quy_che": info["link_quy_che"],
                    }, ensure_ascii=False) + "\n"

            excel_path = os.path.join(app.config["OUTPUT_DIR"], "danh_sach_ma_truong.xlsx")
            json_path = os.path.join(app.config["OUTPUT_DIR"], "danh_sach_ma_truong.json")
            try:
                directory.export_excel(output_path=excel_path)
                directory.export_json(output_path=json_path, also_update_config=False)
                dataset_store.copy_schools_exports_stamp(ROOT)
            except Exception as exc:
                yield json.dumps({
                    "type": "error",
                    "error": str(exc),
                }, ensure_ascii=False) + "\n"
                return
            yield json.dumps({
                "type": "done",
                "total": total,
                "found_domain": found_domain,
                "found_link": found_link,
                "excel_url": url_for("download_file", name="danh_sach_ma_truong.xlsx"),
            }, ensure_ascii=False) + "\n"

        return Response(generate(), mimetype="application/x-ndjson")

    @app.get("/api/schools/codes.txt")
    def download_codes_txt():
        # Lấy từ query ?codes=... hoặc file mới nhất
        codes = request.args.get("codes", "")
        if not codes:
            directory = SchoolDirectory()
            directory.load()
            codes = format_codes_for_copy(directory.get_codes(), one_per_line=True)
        path = os.path.join(app.config["OUTPUT_DIR"], "ma_truong.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(codes if "\n" in codes else codes.replace(",", "\n"))
        return send_file(path, as_attachment=True, download_name="ma_truong.txt")

    def _parse_crawl_request():
        data = request.get_json(silent=True) or {}
        codes = parse_school_codes_text(data.get("codes") or "")
        years = data.get("years") or [2024, 2025, 2026]
        years = [int(y) for y in years]
        limit = int(data.get("limit") or 0)
        if limit > 0:
            codes = codes[:limit]
        return codes, years, data

    def _store_last_crawl(bundle: CrawlBundle, years: List[int], codes: List[str], meta: dict):
        admissions = [r.to_dict() for r in bundle.admissions]
        payload = {
            "admissions": admissions,
            "conversions": [c.to_dict() for c in bundle.conversions],
            "regulations": [r.to_dict() for r in bundle.regulations],
            "years": years,
            "codes": codes,
            **meta,
        }
        app.config["LAST_CRAWL"] = payload
        try:
            dataset_store.save_admissions(ROOT, payload)
        except OSError:
            pass
        return admissions

    def _merge_last_crawl(bundle: CrawlBundle, years: List[int], codes: List[str], meta: dict):
        """Thay dữ liệu của các trường vừa crawl, giữ nguyên các trường còn lại."""
        cached = app.config.get("LAST_CRAWL") or dataset_store.load_admissions(ROOT) or {}
        replaced = {str(code).upper() for code in codes}

        payload = dict(cached)
        payload["admissions"] = _upsert_records(
            cached.get("admissions"),
            [r.to_dict() for r in bundle.admissions],
            ("ma_truong", "ma_nganh", "ten_nganh", "nam", "phuong_thuc", "to_hop"),
        )
        payload["conversions"] = _upsert_records(
            cached.get("conversions"),
            [c.to_dict() for c in bundle.conversions],
            ("ma_truong", "loai_bang", "hang_muc", "nam", "phuong_thuc"),
        )
        payload["regulations"] = _upsert_records(
            cached.get("regulations"),
            [r.to_dict() for r in bundle.regulations],
            ("ma_truong", "tieu_de", "phuong_thuc", "nam"),
        )
        payload["years"] = sorted({
            *[int(y) for y in (cached.get("years") or [])],
            *[int(y) for y in years],
        })
        payload["codes"] = sorted({
            *[str(c).upper() for c in (cached.get("codes") or [])],
            *replaced,
        })
        payload.update(meta)
        app.config["LAST_CRAWL"] = payload
        try:
            dataset_store.save_admissions(ROOT, payload)
        except OSError:
            pass
        return payload["admissions"]

    def _saved_crawl_targets(codes: List[str]):
        """Website và domain điểm/quy chế đã lưu trong danh bạ."""
        directory = _working_directory(refresh=False)
        websites: Dict[str, str] = {}
        trusted: Dict[str, List[str]] = {}
        for code in codes:
            info = directory.schools.get(code) or {}
            websites[code] = str(info.get("website") or "").strip()
            urls = []
            for key in ("domain_diem_chuan", "link_quy_che"):
                raw = str(info.get(key) or "").strip()
                if raw and raw not in urls:
                    urls.append(raw)
            trusted[code] = urls
        return websites, trusted

    def _source_urls_by_school(data: dict, codes: List[str]) -> Dict[str, List[str]]:
        raw = data.get("source_urls") or {}
        if isinstance(raw, list) and len(codes) == 1:
            raw = {codes[0]: raw}
        if not isinstance(raw, dict):
            raise ValueError("Danh sách liên kết không hợp lệ.")
        result: Dict[str, List[str]] = {}
        for code in codes:
            values = raw.get(code) or raw.get(code.lower()) or []
            if isinstance(values, str):
                values = [line.strip() for line in values.splitlines() if line.strip()]
            if not isinstance(values, list):
                raise ValueError(f"Danh sách liên kết của {code} không hợp lệ.")
            values = [str(url).strip() for url in values if str(url).strip()][:20]
            if not values:
                continue
            school = lookup_local_school(code)
            accepted, rejected = filter_official_urls(
                (school or {}).get("website") or "",
                values,
            )
            if rejected:
                raise ValueError(
                    f"Liên kết của {code} phải thuộc website chính thức của trường: "
                    + ", ".join(rejected[:3])
                )
            result[code] = accepted
        return result

    def _store_last_quy_doi(payload: dict):
        app.config["LAST_QUY_DOI"] = payload
        try:
            dataset_store.save_quy_doi(ROOT, payload)
        except OSError:
            pass
        return payload

    def _merge_last_quy_doi(payload: dict, codes: List[str]) -> dict:
        """Thay quy đổi của trường vừa thu thập, giữ các trường còn lại."""
        cached = app.config.get("LAST_QUY_DOI") or dataset_store.load_quy_doi(ROOT) or {}
        replaced = {str(code).upper() for code in codes}

        def code_of(item):
            if not isinstance(item, dict):
                return ""
            return str(item.get("ma_truong") or item.get("code") or "").upper()

        merged = dict(cached)
        merged["rows"] = _upsert_records(
            cached.get("rows"), payload.get("rows"),
            ("ma_truong", "tieu_de_bang", "stt", "nam"),
        )
        merged["notes"] = _upsert_records(
            cached.get("notes"), payload.get("notes"),
            ("ma_truong", "tieu_de", "nam"),
        )
        merged["images"] = _upsert_records(
            cached.get("images"), payload.get("images"),
            ("ma_truong", "url_anh"),
        )
        merged["ranges"] = _upsert_records(
            cached.get("ranges"), payload.get("ranges"),
            ("ma_truong", "phuong_thuc", "nam"),
        )
        merged["certificate_conversions"] = _upsert_records(
            cached.get("certificate_conversions"), payload.get("certificate_conversions"),
            ("ma_truong", "loai_bang", "hang_muc", "nam", "phuong_thuc"),
        )
        merged["school_results"] = _upsert_records(
            cached.get("school_results"), payload.get("school_results"),
            ("code",),
        )
        methods = dict(cached.get("methods_by_school") or {})
        for code, names in (payload.get("methods_by_school") or {}).items():
            current = list(methods.get(code) or [])
            for name in names or []:
                if name not in current:
                    current.append(name)
            methods[code] = current
        merged["methods_by_school"] = methods
        merged["method_labels"] = payload.get("method_labels") or cached.get("method_labels")
        merged["codes"] = sorted({
            *[str(code).upper() for code in (cached.get("codes") or [])],
            *replaced,
        })
        merged["summary"] = {
            "schools": len({code_of(item) for item in merged["school_results"] if code_of(item)}),
            "rows": len(merged["rows"]),
            "notes": len(merged["notes"]),
            "images": len(merged["images"]),
            "ranges": len(merged["ranges"]),
            "certificates": len(merged["certificate_conversions"]),
        }
        merged["ok"] = True
        for key in ("download_url", "filename"):
            if payload.get(key):
                merged[key] = payload[key]
        return _store_last_quy_doi(merged)
    # ---------- API: thu thập điểm chuẩn ----------
    @app.post("/api/crawl")
    def api_crawl():
        codes, years, data = _parse_crawl_request()
        if not codes:
            return jsonify({"ok": False, "error": "Chưa có mã trường hợp lệ."}), 400
        if not years:
            return jsonify({"ok": False, "error": "Chọn ít nhất 1 năm."}), 400

        try:
            source_urls = _source_urls_by_school(data, codes)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        crawler = OnlineAdmissionCrawler()
        bundle = CrawlBundle()
        logs: List[str] = []
        saved_sites, saved_urls = _saved_crawl_targets(codes)

        for i, code in enumerate(codes, start=1):
            try:
                part = crawler.crawl_school_bundle(
                    code,
                    years=years,
                    delay=0.3,
                    source_urls=source_urls.get(code),
                    website=saved_sites.get(code) or None,
                    trusted_urls=saved_urls.get(code),
                )
                bundle.admissions.extend(part.admissions)
                bundle.conversions.extend(part.conversions)
                bundle.regulations.extend(part.regulations)
                note = (part.source_note or "").strip()
                logs.append(
                    f"✓ [{i}/{len(codes)}] {code}: "
                    f"{len(part.admissions)} ngành, {len(part.conversions)} quy đổi chứng chỉ, "
                    f"{len(part.regulations)} quy chế"
                    + (f" — {note}" if note else "")
                )
            except Exception as e:
                logs.append(f"✗ [{i}/{len(codes)}] {code}: {e}")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_name = f"tong_hop_tuyen_sinh_{ts}.xlsx"
        out_path = os.path.join(app.config["OUTPUT_DIR"], out_name)
        ExcelAdmissionExporter(years=sorted(years)).export(
            bundle.admissions,
            output_path=out_path,
            conversions=bundle.conversions,
            regulations=bundle.regulations,
        )
        download_url = url_for("download_file", name=out_name)
        admissions = _merge_last_crawl(
            bundle, years, codes,
            {"download_url": download_url, "filename": out_name, "logs": logs},
        )
        cached_crawl = app.config.get("LAST_CRAWL") or {}
        ExcelAdmissionExporter(years=sorted(cached_crawl.get("years") or years)).export(
            _as_models(AdmissionRecord, cached_crawl.get("admissions")),
            output_path=out_path,
            conversions=_as_models(ScoreConversionRecord, cached_crawl.get("conversions")),
            regulations=_as_models(AdmissionRegulation, cached_crawl.get("regulations")),
        )
        trends = build_trend_series(admissions)

        return jsonify({
            "ok": True,
            "schools": len(codes),
            "admissions": len(bundle.admissions),
            "conversions": len(bundle.conversions),
            "regulations": len(bundle.regulations),
            "logs": logs,
            "download_url": download_url,
            "filename": out_name,
            "preview": admissions[:80],
            "trends": trends,
        })

    @app.post("/api/crawl/stream")
    def api_crawl_stream():
        """NDJSON stream: mỗi trường xong → 1 dòng JSON (hiển thị realtime trên modal)."""
        codes, years, data = _parse_crawl_request()
        if not codes:
            return jsonify({"ok": False, "error": "Chưa có mã trường hợp lệ."}), 400
        if not years:
            return jsonify({"ok": False, "error": "Chọn ít nhất 1 năm."}), 400
        try:
            source_urls = _source_urls_by_school(data, codes)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        merge_existing = bool(data.get("merge", True))
        saved_sites, saved_urls = _saved_crawl_targets(codes)

        @stream_with_context
        def generate():
            crawler = OnlineAdmissionCrawler()
            bundle = CrawlBundle()
            logs: List[str] = []
            yield json.dumps({
                "type": "start",
                "total": len(codes),
                "years": years,
                "codes": codes,
            }, ensure_ascii=False) + "\n"

            for i, code in enumerate(codes, start=1):
                try:
                    part = crawler.crawl_school_bundle(
                        code,
                        years=years,
                        delay=0.25,
                        source_urls=source_urls.get(code),
                        website=saved_sites.get(code) or None,
                        trusted_urls=saved_urls.get(code),
                    )
                    bundle.admissions.extend(part.admissions)
                    bundle.conversions.extend(part.conversions)
                    bundle.regulations.extend(part.regulations)
                    preview = [r.to_dict() for r in part.admissions[:40]]
                    note = (part.source_note or "").strip()
                    msg = (
                        f"✓ [{i}/{len(codes)}] {code}: "
                        f"{len(part.admissions)} ngành, {len(part.conversions)} quy đổi, "
                        f"{len(part.regulations)} quy chế"
                        + (f" — {note}" if note else "")
                    )
                    logs.append(msg)
                    yield json.dumps({
                        "type": "school",
                        "index": i,
                        "total": len(codes),
                        "code": code,
                        "ok": True,
                        "admission_count": len(part.admissions),
                        "conversion_count": len(part.conversions),
                        "regulation_count": len(part.regulations),
                        "preview": preview,
                        "log": msg,
                        "totals": {
                            "admissions": len(bundle.admissions),
                            "conversions": len(bundle.conversions),
                            "regulations": len(bundle.regulations),
                        },
                    }, ensure_ascii=False) + "\n"
                except Exception as e:
                    msg = f"✗ [{i}/{len(codes)}] {code}: {e}"
                    logs.append(msg)
                    yield json.dumps({
                        "type": "school",
                        "index": i,
                        "total": len(codes),
                        "code": code,
                        "ok": False,
                        "error": str(e),
                        "preview": [],
                        "log": msg,
                        "totals": {
                            "admissions": len(bundle.admissions),
                            "conversions": len(bundle.conversions),
                            "regulations": len(bundle.regulations),
                        },
                    }, ensure_ascii=False) + "\n"

            # Báo UI biết đang xuất/lưu — tránh kẹt modal ở "Đang thu thập…"
            yield json.dumps({
                "type": "finalize",
                "message": "Đang xuất Excel và lưu dữ liệu lên máy…",
                "totals": {
                    "admissions": len(bundle.admissions),
                    "conversions": len(bundle.conversions),
                    "regulations": len(bundle.regulations),
                },
            }, ensure_ascii=False) + "\n"

            try:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                out_name = f"tong_hop_tuyen_sinh_{ts}.xlsx"
                out_path = os.path.join(app.config["OUTPUT_DIR"], out_name)
                ExcelAdmissionExporter(years=sorted(years)).export(
                    bundle.admissions,
                    output_path=out_path,
                    conversions=bundle.conversions,
                    regulations=bundle.regulations,
                )
                download_url = url_for("download_file", name=out_name)

                yield json.dumps({
                    "type": "finalize",
                    "message": "Đang ghi snapshot JSON (có thể mất vài phút với dữ liệu lớn)…",
                    "totals": {
                        "admissions": len(bundle.admissions),
                        "conversions": len(bundle.conversions),
                        "regulations": len(bundle.regulations),
                    },
                }, ensure_ascii=False) + "\n"

                store = _merge_last_crawl if merge_existing else _store_last_crawl
                admissions = store(
                    bundle,
                    years,
                    codes,
                    {"download_url": download_url, "filename": out_name, "logs": logs},
                )
                if merge_existing:
                    cached_crawl = app.config.get("LAST_CRAWL") or {}
                    ExcelAdmissionExporter(years=sorted(cached_crawl.get("years") or years)).export(
                        _as_models(AdmissionRecord, cached_crawl.get("admissions")),
                        output_path=out_path,
                        conversions=_as_models(ScoreConversionRecord, cached_crawl.get("conversions")),
                        regulations=_as_models(AdmissionRegulation, cached_crawl.get("regulations")),
                    )
                # Không nhúng trends vào stream (payload rất nặng) — lấy sau qua /api/crawl/trends
                yield json.dumps({
                    "type": "done",
                    "ok": True,
                    "schools": len(codes),
                    "admissions": len(bundle.admissions),
                    "conversions": len(bundle.conversions),
                    "regulations": len(bundle.regulations),
                    "logs": logs,
                    "download_url": download_url,
                    "filename": out_name,
                    "preview": admissions[:120],
                    "codes": codes,
                    "codes_text": "\n".join(codes),
                    "years": years,
                }, ensure_ascii=False) + "\n"
            except Exception as e:
                yield json.dumps({
                    "type": "done",
                    "ok": False,
                    "error": f"Thu thập xong nhưng lưu/xuất thất bại: {e}",
                    "schools": len(codes),
                    "admissions": len(bundle.admissions),
                    "conversions": len(bundle.conversions),
                    "regulations": len(bundle.regulations),
                    "logs": logs,
                }, ensure_ascii=False) + "\n"

        return Response(
            generate(),
            mimetype="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache, no-store",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/crawl/trends")
    def api_crawl_trends():
        cached = app.config.get("LAST_CRAWL") or {}
        admissions = cached.get("admissions") or []
        if not admissions:
            return jsonify({"ok": False, "error": "Chưa có dữ liệu đã tổng hợp. Hãy chạy bước 2 trước."}), 400
        school = (request.args.get("school") or "").strip().upper() or None
        method = (request.args.get("method") or "").strip().upper() or None
        trends = build_trend_series(admissions, school_code=school, method=method)
        return jsonify({"ok": True, "trends": trends})

    @app.get("/api/crawl/majors")
    def api_crawl_majors():
        """Danh sách ngành tuyển sinh theo trường (từ dữ liệu đã thu thập)."""
        cached = app.config.get("LAST_CRAWL") or {}
        admissions = cached.get("admissions") or []
        if not admissions:
            return jsonify({"ok": False, "error": "Chưa có dữ liệu đã tổng hợp. Hãy chạy bước 2 trước."}), 400
        school = (request.args.get("school") or "").strip().upper() or None
        schools_raw = request.args.get("schools") or ""
        school_codes = [c.strip().upper() for c in schools_raw.split(",") if c.strip()]
        majors = list_school_majors(
            admissions,
            school_code=school if not school_codes else None,
            school_codes=school_codes or None,
        )
        return jsonify({
            "ok": True,
            "school": school or "",
            "schools": school_codes,
            "majors": majors,
            "total": len(majors),
        })

    @app.get("/api/crawl/methods")
    def api_crawl_methods():
        """Danh sách phương thức xét tuyển có trong dữ liệu đã thu thập (+ quy đổi)."""
        cached = app.config.get("LAST_CRAWL") or {}
        admissions = cached.get("admissions") or []
        if not admissions:
            return jsonify({"ok": False, "error": "Chưa có dữ liệu đã tổng hợp. Hãy chạy bước 2 trước."}), 400
        school = (request.args.get("school") or "").strip().upper() or None
        schools_raw = request.args.get("schools") or ""
        school_codes = [c.strip().upper() for c in schools_raw.split(",") if c.strip()]
        qd = app.config.get("LAST_QUY_DOI") or {}
        methods = list_admission_methods(
            admissions,
            school_code=school if not school_codes else None,
            school_codes=school_codes or None,
            methods_by_school=qd.get("methods_by_school") or {},
        )
        return jsonify({
            "ok": True,
            "school": school or "",
            "schools": school_codes,
            "methods": methods,
            "total": len(methods),
        })

    @app.get("/api/crawl/bonus")
    def api_crawl_bonus():
        """Quy chế cộng điểm theo loại chứng chỉ và phương thức xét tuyển."""
        cached = app.config.get("LAST_CRAWL") or dataset_store.load_admissions(ROOT) or {}
        quy_doi = app.config.get("LAST_QUY_DOI") or dataset_store.load_quy_doi(ROOT) or {}
        conversions = list(cached.get("conversions") or [])
        conversions.extend(quy_doi.get("certificate_conversions") or [])
        regulations = list(cached.get("regulations") or [])
        for note in quy_doi.get("notes") or []:
            regulations.append({
                "ma_truong": note.get("ma_truong") or "",
                "ten_truong": note.get("ten_truong") or "",
                "tieu_de": note.get("tieu_de") or "",
                "noi_dung": note.get("noi_dung") or "",
                "phuong_thuc": note.get("phuong_thuc") or "",
                "nam": note.get("nam"),
                "nguon": note.get("url_nguon") or note.get("nguon") or "",
            })
        admissions = cached.get("admissions") or []
        if not admissions and not conversions and not regulations:
            return jsonify({
                "ok": False,
                "error": "Chưa có dữ liệu đã tổng hợp. Hãy thu thập dữ liệu điểm trước.",
            }), 400
        schools_raw = request.args.get("schools") or ""
        school_codes = [c.strip().upper() for c in schools_raw.split(",") if c.strip()]
        years = []
        for part in (request.args.get("years") or "").split(","):
            part = part.strip()
            if part.isdigit():
                years.append(int(part))
        payload = summarize_certificate_bonus(
            conversions,
            regulations,
            school_codes=school_codes or None,
            admissions=admissions,
        )
        payload["records"] = list_bonus_records(
            conversions,
            regulations,
            school_codes=school_codes or None,
            years=years or None,
        )
        payload["ok"] = True
        return jsonify(payload)

    def _bonus_identity(item: dict) -> tuple:
        return (
            str(item.get("ma_truong") or "").strip().upper(),
            str(item.get("nam") if item.get("nam") is not None else ""),
            str(item.get("phuong_thuc") or "").strip(),
            str(item.get("dieu_kien") or "").strip(),
            str(item.get("diem_cong") or "").strip(),
        )

    def _row_matches_bonus(row: dict, kind: str, wanted: set) -> bool:
        if kind == "conversion":
            produced = list_bonus_records([row], [])
        else:
            produced = list_bonus_records([], [row])
        return any(_bonus_identity(item) in wanted for item in produced)

    @app.post("/api/crawl/bonus/delete")
    def api_crawl_bonus_delete():
        """Xoá mức điểm cộng đã chọn khỏi quy đổi và quy chế đã lưu."""
        data = request.get_json(silent=True) or {}
        raw_keys = data.get("keys") or []
        if not isinstance(raw_keys, list) or not raw_keys:
            return jsonify({"ok": False, "error": "Chưa chọn dòng cần xoá."}), 400
        wanted = {_bonus_identity(item) for item in raw_keys if isinstance(item, dict)}
        wanted.discard(("", "", "", "", ""))
        if not wanted:
            return jsonify({"ok": False, "error": "Chưa chọn dòng cần xoá."}), 400

        crawl = dict(app.config.get("LAST_CRAWL") or dataset_store.load_admissions(ROOT) or {})
        quy_doi = dict(app.config.get("LAST_QUY_DOI") or dataset_store.load_quy_doi(ROOT) or {})
        crawl["conversions"] = [
            row for row in (crawl.get("conversions") or [])
            if not _row_matches_bonus(row, "conversion", wanted)
        ]
        crawl["regulations"] = [
            row for row in (crawl.get("regulations") or [])
            if not _row_matches_bonus(row, "regulation", wanted)
        ]
        quy_doi["certificate_conversions"] = [
            row for row in (quy_doi.get("certificate_conversions") or [])
            if not _row_matches_bonus(row, "conversion", wanted)
        ]
        quy_doi["notes"] = [
            row for row in (quy_doi.get("notes") or [])
            if not _row_matches_bonus(row, "regulation", wanted)
        ]
        app.config["LAST_CRAWL"] = crawl
        app.config["LAST_QUY_DOI"] = quy_doi
        try:
            if crawl.get("admissions") is not None or crawl.get("conversions") is not None:
                dataset_store.save_admissions(ROOT, crawl)
            if quy_doi:
                dataset_store.save_quy_doi(ROOT, quy_doi)
        except OSError:
            pass
        records = list_bonus_records(
            list(crawl.get("conversions") or []) + list(quy_doi.get("certificate_conversions") or []),
            list(crawl.get("regulations") or []) + [
                {
                    "ma_truong": note.get("ma_truong") or "",
                    "ten_truong": note.get("ten_truong") or "",
                    "tieu_de": note.get("tieu_de") or "",
                    "noi_dung": note.get("noi_dung") or "",
                    "phuong_thuc": note.get("phuong_thuc") or "",
                    "nam": note.get("nam"),
                    "nguon": note.get("url_nguon") or note.get("nguon") or "",
                }
                for note in (quy_doi.get("notes") or [])
            ],
        )
        return jsonify({"ok": True, "removed": len(wanted), "records": records})

    OFFICIAL_METHODS = [
        ("100", "Xét kết quả thi tốt nghiệp THPT"),
        ("200", "Xét kết quả học tập cấp THPT (học bạ)"),
        ("301", "Xét tuyển thẳng theo quy định của Quy chế tuyển sinh (Điều 8)"),
        ("401", "Thi đánh giá năng lực, đánh giá tư duy do CSĐT tự tổ chức để xét tuyển"),
        ("402", "Sử dụng kết quả thi đánh giá năng lực, đánh giá tư duy do đơn vị khác tổ chức để xét tuyển"),
        ("403", "Thi văn hóa do CSĐT tổ chức để xét tuyển"),
        ("404", "Sử dụng kết quả thi văn hóa do CSĐT khác tổ chức để xét tuyển"),
        ("405", "Kết hợp kết quả thi tốt nghiệp THPT với điểm thi năng khiếu để xét tuyển"),
        ("406", "Kết hợp kết quả học tập cấp THPT với điểm thi năng khiếu để xét tuyển"),
        ("407", "Kết hợp kết quả thi tốt nghiệp THPT với kết quả học tập cấp THPT để xét tuyển"),
        ("409", "Kết hợp kết quả thi tốt nghiệp THPT với chứng chỉ quốc tế để xét tuyển"),
        ("410", "Kết hợp kết quả học tập cấp THPT với chứng chỉ quốc tế để xét tuyển"),
        ("411", "Xét tuyển thí sinh tốt nghiệp THPT nước ngoài"),
        ("413", "Kết hợp kết quả thi tốt nghiệp THPT với phỏng vấn để xét tuyển"),
        ("414", "Kết hợp kết quả học tập cấp THPT với phỏng vấn để xét tuyển"),
        ("415", "Sử dụng chứng chỉ quốc tế SAT hoặc chứng chỉ quốc tế khác đủ điều kiện để xét tuyển"),
        ("416", "Kỳ thi V-SAT"),
        ("417", "Sử dụng kết quả Kỳ thi V-SAT do đơn vị khác tổ chức để xét tuyển"),
        ("500", "Sử dụng phương thức khác"),
    ]
    OFFICIAL_METHOD_CODES = {code for code, _name in OFFICIAL_METHODS}

    def _method_payload(records, years, codes, cached=None):
        previous = cached or {}
        return {
            "records": records,
            "catalog": METHOD_CATALOG,
            "years": years,
            "codes": codes,
            "documents": previous.get("documents") or [],
            "nhom_phuong_thuc": previous.get("nhom_phuong_thuc") or {},
        }

    @app.get("/api/phuong-thuc/records")
    def api_phuong_thuc_records():
        cached = app.config.get("LAST_METHODS") or dataset_store.load_phuong_thuc(ROOT) or {}
        return jsonify({
            "ok": True,
            "records": cached.get("records") or [],
            "documents": cached.get("documents") or [],
            "catalog": METHOD_CATALOG,
            "years": cached.get("years") or [],
            "nhom_phuong_thuc": cached.get("nhom_phuong_thuc") or {},
            "official_methods": [
                {"ma": code, "ten": name} for code, name in OFFICIAL_METHODS
            ],
        })

    @app.post("/api/phuong-thuc/nhom")
    def api_phuong_thuc_nhom():
        """Gắn một tên phương thức của trường với tổ hợp mã phương thức chuẩn."""
        data = request.get_json(silent=True) or {}
        school = str(data.get("ma_truong") or "").strip().upper()
        name = str(data.get("ten") or "").strip()
        try:
            year = int(data.get("nam") or 0)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Năm học không hợp lệ."}), 400
        if not school or not name or year < 2000:
            return jsonify({"ok": False, "error": "Thiếu trường, năm học hoặc tên phương thức."}), 400
        if name in OFFICIAL_METHOD_CODES:
            return jsonify({"ok": False, "error": "Mã phương thức chuẩn không cần gắn tổ hợp."}), 400
        raw_codes = data.get("ma") or []
        if not isinstance(raw_codes, list):
            return jsonify({"ok": False, "error": "Tổ hợp phương thức không hợp lệ."}), 400
        chosen = []
        for code, _label in OFFICIAL_METHODS:
            if code in {str(item).strip() for item in raw_codes}:
                chosen.append(code)
        cached = dict(app.config.get("LAST_METHODS") or dataset_store.load_phuong_thuc(ROOT) or {})
        groups = dict(cached.get("nhom_phuong_thuc") or {})
        school_groups = dict(groups.get(school) or {})
        year_groups = dict(school_groups.get(str(year)) or {})
        if chosen:
            year_groups[name] = chosen
        else:
            year_groups.pop(name, None)
        school_groups[str(year)] = year_groups
        groups[school] = school_groups
        cached["nhom_phuong_thuc"] = groups
        app.config["LAST_METHODS"] = cached
        try:
            dataset_store.save_phuong_thuc(ROOT, cached)
        except OSError:
            pass
        return jsonify({"ok": True, "nhom_phuong_thuc": groups})

    @app.post("/api/phuong-thuc/stream")
    def api_phuong_thuc_stream():
        """Nhận diện 6 nhóm phương thức tuyển sinh trên website trường, trước khi lấy điểm."""
        codes, years, _data = _parse_crawl_request()
        if not codes:
            return jsonify({"ok": False, "error": "Chưa có mã trường hợp lệ."}), 400
        if not years:
            return jsonify({"ok": False, "error": "Chọn ít nhất 1 năm."}), 400
        saved_sites, saved_urls = _saved_crawl_targets(codes)
        crawler = OnlineAdmissionCrawler()

        @stream_with_context
        def generate():
            collector = AdmissionMethodCollector()
            incoming = []
            logs = []
            yield json.dumps({
                "type": "start",
                "total": len(codes),
                "years": years,
            }, ensure_ascii=False) + "\n"
            for index, code in enumerate(codes, start=1):
                info = crawler._school_for_official_crawl(code) or {}
                school_name = info.get("name") or code
                website = saved_sites.get(code) or info.get("website") or ""
                try:
                    part = collector.collect(
                        school_code=code,
                        school_name=school_name,
                        website=website,
                        years=years,
                        delay=0.2,
                        trusted_urls=saved_urls.get(code),
                    )
                    incoming.extend(part.get("records") or [])
                    found = ", ".join(part.get("found") or []) or "không thấy"
                    program_count = int(part.get("programs") or 0)
                    msg = f"✓ [{index}/{len(codes)}] {code}: {program_count} ngành — {found}"
                    if part.get("note"):
                        msg += f" — {part['note']}"
                    logs.append(msg)
                    yield json.dumps({
                        "type": "school",
                        "index": index,
                        "total": len(codes),
                        "code": code,
                        "ok": bool(part.get("ok")),
                        "found": part.get("found") or [],
                        "programs": int(part.get("programs") or 0),
                        "log": msg,
                    }, ensure_ascii=False) + "\n"
                except Exception as exc:
                    msg = f"✗ [{index}/{len(codes)}] {code}: {exc}"
                    logs.append(msg)
                    yield json.dumps({
                        "type": "school",
                        "index": index,
                        "total": len(codes),
                        "code": code,
                        "ok": False,
                        "error": str(exc),
                        "log": msg,
                    }, ensure_ascii=False) + "\n"

            cached = app.config.get("LAST_METHODS") or dataset_store.load_phuong_thuc(ROOT) or {}
            replaced = {str(code).upper() for code in codes}
            year_set = {int(year) for year in years}
            kept = [
                row for row in (cached.get("records") or [])
                if str(row.get("ma_truong") or "").upper() not in replaced
                or int(row.get("nam") or 0) not in year_set
            ]
            records = kept + incoming
            payload = _method_payload(
                records,
                sorted({*[int(y) for y in (cached.get("years") or [])], *years}),
                sorted({*[str(c).upper() for c in (cached.get("codes") or [])], *replaced}),
                cached,
            )
            payload["logs"] = logs
            app.config["LAST_METHODS"] = payload
            try:
                dataset_store.save_phuong_thuc(ROOT, payload)
            except OSError:
                pass
            yield json.dumps({
                "type": "done",
                "ok": True,
                "records": records,
                "catalog": METHOD_CATALOG,
            }, ensure_ascii=False) + "\n"

        return Response(
            generate(),
            mimetype="application/x-ndjson",
            headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
        )

    def _delete_method_files(documents):
        base = os.path.realpath(app.config["UPLOADS_DIR"])
        for doc in documents or []:
            code = re.sub(r"[^A-Z0-9_-]", "", str(doc.get("ma_truong") or "").upper())
            name = os.path.basename(str(doc.get("stored_name") or ""))
            if not code or not name.startswith("pt_"):
                continue
            path = os.path.realpath(os.path.join(base, code, name))
            if os.path.commonpath([base, path]) != base or not os.path.isfile(path):
                continue
            try:
                os.remove(path)
            except OSError:
                pass

    @app.post("/api/phuong-thuc/delete")
    def api_phuong_thuc_delete():
        data = request.get_json(silent=True) or {}
        raw_keys = data.get("keys") or []
        wanted = set()
        wanted_years = set()
        for item in raw_keys:
            if isinstance(item, dict):
                school = str(item.get("ma_truong") or "").upper()
                year = int(item.get("nam") or 0)
                code = str(item.get("ma_xet_tuyen") or "")
                major = str(item.get("ma_nganh") or "")
                name = str(item.get("ten_nganh") or "")
                if code or major or name:
                    wanted.add((school, year, code, major, name))
                else:
                    wanted_years.add((school, year))
            elif isinstance(item, str) and "|" in item:
                parts = item.split("|")
                school = parts[0].upper()
                year = int(parts[1] or 0) if len(parts) > 1 else 0
                if len(parts) >= 5:
                    wanted.add((school, year, parts[2], parts[3], parts[4]))
                else:
                    wanted_years.add((school, year))
        drop_docs = []
        for item in data.get("documents") or []:
            if not isinstance(item, dict):
                continue
            school = str(item.get("ma_truong") or "").upper()
            stored = os.path.basename(str(item.get("stored_name") or ""))
            try:
                year = int(item.get("nam") or 0)
            except (TypeError, ValueError):
                year = 0
            if school and stored:
                drop_docs.append((school, year, stored))
        if not wanted and not wanted_years and not drop_docs:
            return jsonify({"ok": False, "error": "Chưa chọn dòng cần xoá."}), 400
        cached = dict(app.config.get("LAST_METHODS") or dataset_store.load_phuong_thuc(ROOT) or {})

        def _keep(row):
            school = str(row.get("ma_truong") or "").upper()
            year = int(row.get("nam") or 0)
            if (school, year) in wanted_years:
                return False
            identity = (
                school,
                year,
                str(row.get("ma_xet_tuyen") or ""),
                str(row.get("ma_nganh") or ""),
                str(row.get("ten_nganh") or ""),
            )
            return identity not in wanted

        records = [row for row in (cached.get("records") or []) if _keep(row)]
        documents = list(cached.get("documents") or [])
        removed_docs = []
        if drop_docs:
            chosen = set(drop_docs)
            kept_docs = []
            for doc in documents:
                school = str(doc.get("ma_truong") or "").upper()
                stored = os.path.basename(str(doc.get("stored_name") or ""))
                try:
                    year = int(doc.get("nam") or 0)
                except (TypeError, ValueError):
                    year = 0
                if (school, year, stored) in chosen:
                    removed_docs.append(doc)
                else:
                    kept_docs.append(doc)
            documents = kept_docs
            removed_names = {os.path.basename(str(doc.get("stored_name") or "")) for doc in removed_docs}
            records = [
                row for row in records
                if not any(
                    name and name in str(row.get("nguon") or "")
                    for name in removed_names
                )
            ]
            emptied = set()
            for school, year, _stored in drop_docs:
                still = any(
                    str(doc.get("ma_truong") or "").upper() == school and int(doc.get("nam") or 0) == year
                    for doc in documents
                )
                if not still:
                    emptied.add((school, year))
            if emptied:
                records = [
                    row for row in records
                    if (str(row.get("ma_truong") or "").upper(), int(row.get("nam") or 0)) not in emptied
                ]
        live_pairs = {
            (str(row.get("ma_truong") or "").upper(), int(row.get("nam") or 0))
            for row in records
        }
        orphan_docs = [
            doc for doc in documents
            if (str(doc.get("ma_truong") or "").upper(), int(doc.get("nam") or 0)) not in live_pairs
            and any(
                str(row.get("ma_truong") or "").upper() == str(doc.get("ma_truong") or "").upper()
                and int(row.get("nam") or 0) == int(doc.get("nam") or 0)
                for row in (cached.get("records") or [])
            )
        ]
        if orphan_docs:
            removed_docs.extend(orphan_docs)
            gone = {
                (
                    str(doc.get("ma_truong") or "").upper(),
                    int(doc.get("nam") or 0),
                    os.path.basename(str(doc.get("stored_name") or "")),
                )
                for doc in orphan_docs
            }
            documents = [
                doc for doc in documents
                if (
                    str(doc.get("ma_truong") or "").upper(),
                    int(doc.get("nam") or 0),
                    os.path.basename(str(doc.get("stored_name") or "")),
                ) not in gone
            ]
        _delete_method_files(removed_docs)
        cached["records"] = records
        cached["documents"] = documents
        cached["catalog"] = METHOD_CATALOG
        app.config["LAST_METHODS"] = cached
        try:
            dataset_store.save_phuong_thuc(ROOT, cached)
        except OSError:
            pass
        return jsonify({
            "ok": True,
            "removed": len(wanted),
            "records": records,
            "documents": cached.get("documents") or [],
            "catalog": METHOD_CATALOG,
        })

    @app.post("/api/phuong-thuc/upload")
    def api_phuong_thuc_upload():
        """Tải đề án hoặc bảng ngành theo một trường và một năm học."""
        if (request.content_length or 0) > 100 * 1024 * 1024:
            return jsonify({"ok": False, "error": "Tổng dung lượng vượt quá 100 MB."}), 413
        code = (request.form.get("code") or "").strip().upper()
        try:
            year = int(request.form.get("year") or 0)
        except ValueError:
            return jsonify({"ok": False, "error": "Năm học không hợp lệ."}), 400
        if not code or year < 2000 or year > 2100:
            return jsonify({"ok": False, "error": "Thiếu mã trường hoặc năm học không hợp lệ."}), 400
        files = [item for item in request.files.getlist("files") if item and item.filename]
        if not files:
            return jsonify({"ok": False, "error": "Chưa chọn tài liệu."}), 400
        if len(files) > 20:
            return jsonify({"ok": False, "error": "Chỉ được tải tối đa 20 tài liệu mỗi lần."}), 400
        schools_data = dataset_store.load_schools(ROOT) or {}
        school = next(
            (
                item for item in (schools_data.get("schools") or [])
                if str(item.get("code") or "").upper() == code
            ),
            lookup_local_school(code),
        )
        if not school:
            return jsonify({"ok": False, "error": f"Không tìm thấy trường {code}."}), 404
        school_name = school.get("name") or school.get("short_name") or code
        allowed = {".xlsx", ".xls", ".csv", ".pdf", ".docx", ".html", ".htm", ".json"}
        upload_dir = os.path.join(app.config["UPLOADS_DIR"], code)
        os.makedirs(upload_dir, exist_ok=True)
        documents = []
        incoming = []
        errors = []
        for uploaded in files:
            original_name = os.path.basename(uploaded.filename)
            ext = os.path.splitext(original_name)[1].lower()
            if ext not in allowed:
                errors.append(f"{original_name}: định dạng không được hỗ trợ")
                continue
            safe_original = secure_filename(original_name)
            if not safe_original:
                errors.append(f"{original_name}: tên file không hợp lệ")
                continue
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            stored_name = f"pt_{year}_{stamp}_{safe_original}"
            path = os.path.join(upload_dir, stored_name)
            uploaded.save(path)
            size = os.path.getsize(path)
            if size > 20 * 1024 * 1024:
                os.unlink(path)
                errors.append(f"{original_name}: vượt quá 20 MB")
                continue
            source_url = url_for(
                "download_school_document",
                code=code,
                name=stored_name,
            )
            try:
                parsed = records_from_local_file(
                    path, code, school_name, year, source_url, original_name
                )
            except Exception as exc:
                errors.append(f"{original_name}: không đọc được ({exc})")
                parsed = []
            incoming.extend(parsed)
            if not parsed:
                errors.append(f"{original_name}: không thấy chương trình đào tạo")
            documents.append({
                "ma_truong": code,
                "ten_truong": school_name,
                "nam": year,
                "filename": original_name,
                "stored_name": stored_name,
                "source_url": source_url,
                "size": size,
                "programs": len(parsed),
                "uploaded_at": datetime.now().isoformat(timespec="seconds"),
            })
        if not incoming:
            detail = "; ".join(errors) or "Không tìm thấy chương trình đào tạo trong tài liệu."
            return jsonify({"ok": False, "error": detail}), 400
        cached = dict(app.config.get("LAST_METHODS") or dataset_store.load_phuong_thuc(ROOT) or {})
        records = _upsert_records(
            cached.get("records"),
            incoming,
            ("ma_truong", "nam", "ma_xet_tuyen", "ma_nganh", "ten_nganh"),
        )
        kept_docs = [
            item for item in (cached.get("documents") or [])
            if not (
                str(item.get("ma_truong") or "").upper() == code
                and int(item.get("nam") or 0) == year
                and item.get("filename") in {doc["filename"] for doc in documents}
            )
        ]
        payload = _method_payload(
            records,
            sorted({*[int(item) for item in (cached.get("years") or []) if str(item).isdigit() or isinstance(item, int)], year}),
            sorted({*[str(item).upper() for item in (cached.get("codes") or [])], code}),
            cached,
        )
        payload["documents"] = kept_docs + documents
        app.config["LAST_METHODS"] = payload
        try:
            dataset_store.save_phuong_thuc(ROOT, payload)
        except OSError:
            pass
        return jsonify({
            "ok": True,
            "files": len(documents),
            "programs": len(incoming),
            "records": records,
            "documents": payload["documents"],
            "errors": errors,
        })

    @app.post("/api/phuong-thuc/import")
    def api_phuong_thuc_import():
        """Nhập JSON phương thức cho một trường và các năm học đã chọn."""
        data = request.get_json(silent=True) or {}
        code = str(data.get("code") or "").strip().upper()
        raw_years = data.get("years")
        if raw_years is None:
            raw_years = [data.get("year")]
        if not isinstance(raw_years, list):
            raw_years = [raw_years]
        years = []
        for item in raw_years:
            try:
                year = int(item or 0)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "Năm học không hợp lệ."}), 400
            if year < 2000 or year > 2100:
                return jsonify({"ok": False, "error": "Năm học không hợp lệ."}), 400
            if year not in years:
                years.append(year)
        if not code or not years:
            return jsonify({"ok": False, "error": "Hãy chọn một trường và ít nhất một năm học."}), 400
        raw_rows = data.get("rows")
        try:
            programs = program_rows_from_payload(raw_rows)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        schools_data = dataset_store.load_schools(ROOT) or {}
        school = next(
            (
                item for item in (schools_data.get("schools") or [])
                if str(item.get("code") or "").upper() == code
            ),
            lookup_local_school(code),
        )
        if not school:
            return jsonify({"ok": False, "error": f"Không tìm thấy trường {code}."}), 404
        school_name = school.get("name") or school.get("short_name") or code
        incoming = []
        for year in years:
            incoming.extend(records_from_programs(
                programs, code, school_name, year, "", "Nhập từ JSON"
            ))
        cached = dict(app.config.get("LAST_METHODS") or dataset_store.load_phuong_thuc(ROOT) or {})
        records = _upsert_records(
            cached.get("records"),
            incoming,
            ("ma_truong", "nam", "ma_xet_tuyen", "ma_nganh", "ten_nganh"),
        )
        payload = _method_payload(
            records,
            sorted({
                *[int(item) for item in (cached.get("years") or []) if str(item).isdigit() or isinstance(item, int)],
                *years,
            }),
            sorted({*[str(item).upper() for item in (cached.get("codes") or [])], code}),
            cached,
        )
        app.config["LAST_METHODS"] = payload
        try:
            dataset_store.save_phuong_thuc(ROOT, payload)
        except OSError:
            pass
        return jsonify({
            "ok": True,
            "programs": len(incoming),
            "majors": len(programs),
            "years": years,
            "records": records,
            "documents": payload.get("documents") or [],
        })

    @app.post("/api/crawl/danh-gia")
    def api_crawl_danh_gia():
        """Đánh giá cơ hội trúng tuyển (thống kê + AI miễn phí)."""
        data = request.get_json(silent=True) or {}
        cached = app.config.get("LAST_CRAWL") or {}
        admissions = cached.get("admissions") or []
        if not admissions:
            return jsonify({"ok": False, "error": "Chưa có dữ liệu đã tổng hợp. Hãy chạy bước 2 trước."}), 400

        scores_payload: List[Dict[str, Any]] = []
        raw_scores = data.get("scores")
        if isinstance(raw_scores, list) and raw_scores:
            for item in raw_scores:
                if not isinstance(item, dict):
                    continue
                try:
                    sc = float(str(item.get("score")).replace(",", "."))
                except (TypeError, ValueError):
                    continue
                mid = (item.get("method") or "").strip()
                if not mid:
                    continue
                scores_payload.append({"method": mid, "score": sc})

        certificates: List[Dict[str, Any]] = []
        raw_certs = data.get("certificates")
        if isinstance(raw_certs, list):
            for item in raw_certs:
                if not isinstance(item, dict):
                    continue
                cert_type = (item.get("type") or item.get("certificate") or "").strip()
                if not cert_type:
                    continue
                try:
                    cert_score = float(str(item.get("score")).replace(",", "."))
                except (TypeError, ValueError):
                    continue
                certificates.append({"type": cert_type, "score": cert_score})
        conversions = cached.get("conversions") or []

        score = None
        method = (data.get("method") or "THPT").upper().replace("VACT", "V-ACT")
        if not scores_payload:
            try:
                score = float(str(data.get("score")).replace(",", "."))
            except (TypeError, ValueError):
                return jsonify({
                    "ok": False,
                    "error": "Nhập ít nhất một điểm theo phương thức (vd: TSA 80, SAT 1500).",
                }), 400

        school = (data.get("school_code") or data.get("code") or "").strip().upper() or None
        schools_raw = data.get("school_codes") or data.get("schools") or []
        school_codes: List[str] = []
        if isinstance(schools_raw, list):
            school_codes = [str(c).strip().upper() for c in schools_raw if str(c).strip()]
        elif isinstance(schools_raw, str) and schools_raw.strip():
            school_codes = [c.strip().upper() for c in schools_raw.split(",") if c.strip()]
        if school and not school_codes:
            school_codes = [school]

        majors_raw = data.get("majors")
        majors: List[str] = []
        if isinstance(majors_raw, list):
            majors = [str(m).strip() for m in majors_raw if str(m).strip()]
        elif isinstance(majors_raw, str) and majors_raw.strip():
            majors = [majors_raw.strip()]
        major_kw = (data.get("major") or data.get("major_keyword") or "").strip() or None
        if majors:
            major_kw = None
        use_ai = bool(data.get("use_ai", True))

        # Nhiều phương thức → đánh giá riêng từng cái (UI pills)
        if len(scores_payload) > 1:
            method_results: List[Dict[str, Any]] = []
            for item in scores_payload:
                analysis = analyze_chance(
                    admissions,
                    score=float(item["score"]),
                    method=str(item["method"]),
                    school_codes=school_codes or None,
                    major_keyword=major_kw,
                    majors=majors or None,
                    certificates=certificates or None,
                    conversions=conversions,
                )
                # Khi multi: gọi AI theo từng phương thức nếu user bật checkbox
                one = enrich_with_ai(analysis, use_ai=use_ai)
                one["method_labels"] = METHOD_COLUMN_LABELS
                method_results.append(one)
            any_ok = any(bool(r.get("ok")) for r in method_results)
            return jsonify({
                "ok": True,
                "result": {
                    "ok": any_ok,
                    "multi": True,
                    "method_results": method_results,
                    "message": (
                        None if any_ok
                        else "Không đánh giá được với các phương thức đã chọn."
                    ),
                    "method_labels": METHOD_COLUMN_LABELS,
                },
            })

        analysis = analyze_chance(
            admissions,
            score=score,
            method=method,
            school_codes=school_codes or None,
            major_keyword=major_kw,
            majors=majors or None,
            scores=scores_payload or None,
            certificates=certificates or None,
            conversions=conversions,
        )
        result = enrich_with_ai(analysis, use_ai=use_ai)
        result["method_labels"] = METHOD_COLUMN_LABELS
        return jsonify({"ok": True, "result": result})

    # ---------- API: Quy đổi điểm phương thức ----------
    @app.post("/api/quy-doi")
    def api_quy_doi():
        data = request.get_json(silent=True) or {}
        codes = parse_school_codes_text(data.get("codes") or "")
        limit = int(data.get("limit") or 0)
        if limit > 0:
            codes = codes[:limit]
        if not codes:
            return jsonify({"ok": False, "error": "Chưa có mã trường hợp lệ."}), 400

        crawler = ScoreConversionCrawler()
        bundle = crawler.crawl_schools(codes)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_name = f"quy_doi_diem_{ts}.xlsx"
        out_path = os.path.join(app.config["OUTPUT_DIR"], out_name)

        payload = crawler.bundle_to_api_dict(bundle)
        payload["ok"] = True
        payload["download_url"] = url_for("download_file", name=out_name)
        payload["filename"] = out_name
        # Phương thức theo từng trường: ưu tiên list từ trang điểm chuẩn + cột bảng quy đổi
        methods_by_school = {}
        for meta in bundle.school_results:
            code = meta.get("code") or ""
            from_table = list_methods_from_rows(payload["rows"], code)
            methods_by_school[code] = _enrich_methods_for_school(code, from_table)
        payload["methods_by_school"] = methods_by_school
        payload["method_labels"] = METHOD_LABELS
        # Cache + lưu đĩa để dùng lại không cần thu thập
        merged_quy_doi = _merge_last_quy_doi(payload, codes)
        crawler.export_excel(_quy_doi_bundle_from_cache(merged_quy_doi), output_path=out_path)
        return jsonify(merged_quy_doi)

    @app.post("/api/quy-doi/stream")
    def api_quy_doi_stream():
        """NDJSON stream: mỗi trường xong → cập nhật progress 0–100% trên UI."""
        data = request.get_json(silent=True) or {}
        codes = parse_school_codes_text(data.get("codes") or "")
        limit = int(data.get("limit") or 0)
        if limit > 0:
            codes = codes[:limit]
        if not codes:
            return jsonify({"ok": False, "error": "Chưa có mã trường hợp lệ."}), 400

        @stream_with_context
        def generate():
            crawler = ScoreConversionCrawler()
            from core.models import MethodConversionBundle
            bundle = MethodConversionBundle()
            total = len(codes)
            yield json.dumps({
                "type": "start",
                "total": total,
                "codes": codes,
            }, ensure_ascii=False) + "\n"

            for i, code in enumerate(codes, start=1):
                try:
                    part, meta = crawler.crawl_school(code)
                    bundle.rows.extend(part.rows)
                    bundle.notes.extend(part.notes)
                    bundle.images.extend(part.images)
                    bundle.ranges.extend(part.ranges)
                    bundle.school_results.append(meta)
                    ok = not bool(meta.get("error"))
                    yield json.dumps({
                        "type": "school",
                        "index": i,
                        "total": total,
                        "code": code,
                        "ok": ok,
                        "error": meta.get("error") or "",
                        "rows": len(part.rows),
                        "notes": len(part.notes),
                        "images": len(part.images),
                        "ranges": len(part.ranges),
                        "totals": {
                            "rows": len(bundle.rows),
                            "notes": len(bundle.notes),
                            "images": len(bundle.images),
                            "ranges": len(bundle.ranges),
                            "schools": len(bundle.school_results),
                        },
                    }, ensure_ascii=False) + "\n"
                except Exception as e:
                    yield json.dumps({
                        "type": "school",
                        "index": i,
                        "total": total,
                        "code": code,
                        "ok": False,
                        "error": str(e),
                        "rows": 0,
                        "notes": 0,
                        "images": 0,
                        "ranges": 0,
                        "totals": {
                            "rows": len(bundle.rows),
                            "notes": len(bundle.notes),
                            "images": len(bundle.images),
                            "ranges": len(bundle.ranges),
                            "schools": len(bundle.school_results),
                        },
                    }, ensure_ascii=False) + "\n"

            # Sau trường cuối: lưu JSON trước (để F5 không mất data), Excel sau.
            yield json.dumps({
                "type": "finalize",
                "message": "Đã lấy xong các trường — đang lưu dữ liệu…",
                "totals": {
                    "rows": len(bundle.rows),
                    "notes": len(bundle.notes),
                    "images": len(bundle.images),
                    "ranges": len(bundle.ranges),
                    "schools": len(bundle.school_results),
                },
            }, ensure_ascii=False) + "\n"

            try:
                payload = crawler.bundle_to_api_dict(bundle)
                payload["ok"] = True
                methods_by_school = {}
                for meta in bundle.school_results:
                    code = meta.get("code") or ""
                    from_table = list_methods_from_rows(payload["rows"], code)
                    methods_by_school[code] = _enrich_methods_for_school(code, from_table)
                payload["methods_by_school"] = methods_by_school
                payload["method_labels"] = METHOD_LABELS
                payload["codes"] = codes

                # 1) Lưu bộ nhớ + JSON trước — quan trọng hơn Excel
                _merge_last_quy_doi(payload, codes)

                yield json.dumps({
                    "type": "finalize",
                    "message": "Đã lưu JSON — đang xuất Excel…",
                    "totals": payload.get("summary") or {},
                }, ensure_ascii=False) + "\n"

                download_url = ""
                out_name = ""
                try:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    out_name = f"quy_doi_diem_{ts}.xlsx"
                    out_path = os.path.join(app.config["OUTPUT_DIR"], out_name)
                    cached_before_excel = app.config.get("LAST_QUY_DOI") or {}
                    crawler.export_excel(
                        _quy_doi_bundle_from_cache(cached_before_excel),
                        output_path=out_path,
                    )
                    download_url = url_for("download_file", name=out_name)
                    payload["download_url"] = download_url
                    payload["filename"] = out_name
                    # Cập nhật meta file trên bản đã lưu
                    cached = app.config.get("LAST_QUY_DOI") or {}
                    cached["download_url"] = download_url
                    cached["filename"] = out_name
                    app.config["LAST_QUY_DOI"] = cached
                    try:
                        dataset_store.save_quy_doi(ROOT, cached)
                    except OSError:
                        pass
                except Exception as excel_err:
                    # Excel lỗi vẫn giữ được JSON đã lưu
                    yield json.dumps({
                        "type": "finalize",
                        "message": f"Lưu JSON xong; xuất Excel lỗi: {excel_err}",
                    }, ensure_ascii=False) + "\n"

                # done gọn — không nhúng toàn bộ rows (payload rất nặng → treo UI)
                yield json.dumps({
                    "type": "done",
                    "ok": True,
                    "saved": True,
                    "summary": payload.get("summary") or {},
                    "school_results": payload.get("school_results") or [],
                    "methods_by_school": methods_by_school,
                    "method_labels": METHOD_LABELS,
                    "download_url": download_url,
                    "filename": out_name,
                    "codes": codes,
                }, ensure_ascii=False) + "\n"
            except Exception as e:
                yield json.dumps({
                    "type": "done",
                    "ok": False,
                    "saved": False,
                    "error": f"Lấy xong trường nhưng lưu thất bại: {e}",
                    "schools": len(bundle.school_results),
                    "rows": len(bundle.rows),
                }, ensure_ascii=False) + "\n"

        return Response(
            generate(),
            mimetype="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache, no-store",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/quy-doi/tinh")
    def api_quy_doi_tinh():
        """
        Tính điểm quy đổi trên giao diện.
        body: {
          code, score, method,
          mode: "method" | "certificate",
          certificate_type?: "IELTS",
          table_title?: str,
          refresh?: bool  # bắt buộc crawl lại nếu chưa có cache
        }
        """
        data = request.get_json(silent=True) or {}
        code = (data.get("code") or "").strip().upper()
        aliases = {"NEU": "KHA", "FTU": "NTH", "HUST": "BKA", "UET": "QHI"}
        code = aliases.get(code, code)
        try:
            score = float(str(data.get("score")).replace(",", "."))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Điểm nhập không hợp lệ."}), 400

        mode = (data.get("mode") or "method").lower()
        method = (data.get("method") or "THPT").upper().replace("VACT", "V-ACT")
        table_title = data.get("table_title") or None

        cached = app.config.get("LAST_QUY_DOI") or {}
        rows = cached.get("rows") or []
        # Nếu cache không có trường này → crawl nhanh 1 trường
        need_fetch = data.get("refresh") or not any(r.get("ma_truong") == code for r in rows)
        if need_fetch or (mode == "method" and not any(r.get("ma_truong") == code for r in rows)):
            crawler = ScoreConversionCrawler()
            bundle, meta = crawler.crawl_school(code)
            if not meta.get("ok"):
                return jsonify({"ok": False, "error": meta.get("error") or "Không lấy được bảng quy đổi."}), 400
            part = crawler.bundle_to_api_dict(bundle)
            # merge vào cache
            if not cached:
                cached = part
            else:
                cached_rows = [r for r in (cached.get("rows") or []) if r.get("ma_truong") != code]
                cached_rows.extend(part.get("rows") or [])
                cached["rows"] = cached_rows
                cached_notes = [n for n in (cached.get("notes") or []) if n.get("ma_truong") != code]
                cached_notes.extend(part.get("notes") or [])
                cached["notes"] = cached_notes
                cached_certs = [
                    c for c in (cached.get("certificate_conversions") or [])
                    if c.get("ma_truong") != code
                ]
                cached_certs.extend(part.get("certificate_conversions") or [])
                cached["certificate_conversions"] = cached_certs
                mbs = cached.get("methods_by_school") or {}
                mbs[code] = _enrich_methods_for_school(
                    code, list_methods_from_rows(part.get("rows") or [], code)
                )
                cached["methods_by_school"] = mbs
            _store_last_quy_doi(cached)
            rows = cached.get("rows") or []

        if mode == "certificate":
            # Lấy thêm bảng chứng chỉ trực tiếp từ website trường nếu cần
            cert_type = (data.get("certificate_type") or method or "IELTS").upper()
            convs = cached.get("certificate_conversions") or []
            if not any(c.get("ma_truong") == code for c in convs):
                from crawlers.official_conversion import OfficialConversionCollector
                from crawlers.official_site_crawler import lookup_local_school
                info = lookup_local_school(code)
                if info and info.get("website"):
                    official = OfficialConversionCollector().collect(
                        code,
                        info.get("name") or code,
                        info["website"],
                    )
                    convs = [c.to_dict() for c in official.conversions]
                    cached["certificate_conversions"] = (
                        [c for c in (cached.get("certificate_conversions") or []) if c.get("ma_truong") != code]
                        + convs
                    )
                    _store_last_quy_doi(cached)
            result = calculate_certificate_conversion(convs, code, score, cert_type)
            return jsonify({"ok": result.ok, "result": result.to_dict(), "error": result.message if not result.ok else ""})

        result = calculate_equivalence(
            rows=rows,
            school_code=code,
            source_method=method,
            score=score,
            table_title=table_title,
        )
        methods = (cached.get("methods_by_school") or {}).get(code) or _enrich_methods_for_school(
            code, list_methods_from_rows(rows, code)
        )
        return jsonify({
            "ok": result.ok,
            "result": result.to_dict(),
            "methods": methods,
            "error": result.message if not result.ok else "",
        })

    # ---------- API: Dữ liệu hệ thống (lưu / nạp lại) ----------
    @app.get("/api/datasets")
    def api_datasets_list():
        return jsonify({"ok": True, **dataset_store.list_system_datasets(ROOT)})

    @app.get("/api/schools/saved")
    def api_schools_saved():
        data = dataset_store.load_schools(ROOT)
        if not data or not (data.get("schools") or []):
            data = dataset_store.load_json(os.path.join(ROOT, "config", "schools_all.json"))
        if not data or not (data.get("schools") or []):
            return jsonify({"ok": False, "error": "Chưa có danh sách trường đã lưu."}), 404
        schools = []
        for item in data.get("schools") or []:
            row = dict(item)
            regions = split_addresses_by_region(row.get("dia_chi") or "")
            row["loai_truong"] = row.get("loai_truong") or classify_school_sector(row.get("name") or "")
            for key, value in regions.items():
                row[key] = row.get(key) or value
            schools.append(row)
        stats = {}
        for r in schools:
            lb = r.get("type_label") or "?"
            stats[lb] = stats.get(lb, 0) + 1
        codes = [r.get("code") for r in schools if r.get("code")]
        return jsonify({
            "ok": True,
            "total": data.get("total") or len(schools),
            "stats": stats,
            "schools": schools,
            "codes_text": format_codes_for_copy(codes, one_per_line=True),
            "excel_url": url_for("download_file", name="danh_sach_ma_truong.xlsx")
            if os.path.isfile(os.path.join(app.config["OUTPUT_DIR"], "danh_sach_ma_truong.xlsx"))
            else "",
            "txt_url": url_for("download_codes_txt"),
            "from_disk": True,
            "meta": dataset_store.file_meta(dataset_store.schools_json_path(ROOT)),
        })

    @app.get("/api/schools/detail")
    def api_school_detail():
        """Tổng hợp toàn bộ dữ liệu đã lưu của một trường và nguồn tương ứng."""
        code = (request.args.get("code") or "").strip().upper()
        aliases = {"NEU": "KHA", "FTU": "NTH", "HUST": "BKA", "UET": "QHI"}
        code = aliases.get(code, code)
        if not code:
            return jsonify({"ok": False, "error": "Thiếu mã trường."}), 400

        schools_data = dataset_store.load_schools(ROOT) or {}
        school = next(
            (
                item for item in (schools_data.get("schools") or [])
                if str(item.get("code") or "").upper() == code
            ),
            lookup_local_school(code) or {"code": code, "name": code},
        )
        crawl = app.config.get("LAST_CRAWL") or dataset_store.load_admissions(ROOT) or {}
        quy_doi = app.config.get("LAST_QUY_DOI") or dataset_store.load_quy_doi(ROOT) or {}

        def match(item, field="ma_truong"):
            return str(item.get(field) or "").upper() == code

        admissions = [r for r in (crawl.get("admissions") or []) if match(r)]
        admissions.sort(
            key=lambda r: (
                str(r.get("ten_nganh") or ""),
                -(int(r.get("nam") or 0)),
                str(r.get("phuong_thuc") or ""),
            )
        )
        conversions = [r for r in (crawl.get("conversions") or []) if match(r)]
        regulations = [r for r in (crawl.get("regulations") or []) if match(r)]
        conversion_rows = [r for r in (quy_doi.get("rows") or []) if match(r)]
        conversion_notes = [r for r in (quy_doi.get("notes") or []) if match(r)]
        conversion_images = [r for r in (quy_doi.get("images") or []) if match(r)]
        certificates = [
            r for r in (quy_doi.get("certificate_conversions") or []) if match(r)
        ]
        uploaded_documents = [
            r for r in (crawl.get("uploaded_documents") or [])
            if str(r.get("ma_truong") or "").upper() == code
        ]
        summary_regulation = next(
            (
                r for r in (crawl.get("summaries") or [])
                if str(r.get("ma_truong") or "").upper() == code
            ),
            None,
        )
        for document in uploaded_documents:
            stored_name = document.get("stored_name") or ""
            document["download_url"] = url_for(
                "download_school_document",
                code=code,
                name=stored_name,
            ) if stored_name else ""

        majors = {}
        methods = set()
        for row in admissions:
            major_key = (row.get("ma_nganh") or "", row.get("ten_nganh") or "")
            major = majors.setdefault(major_key, {
                "ma_nganh": major_key[0],
                "ten_nganh": major_key[1],
                "years": set(),
                "methods": set(),
            })
            if row.get("nam"):
                major["years"].add(row["nam"])
            if row.get("phuong_thuc"):
                major["methods"].add(row["phuong_thuc"])
                methods.add(row["phuong_thuc"])
        major_rows = [
            {
                **major,
                "years": sorted(major["years"], reverse=True),
                "methods": sorted(major["methods"]),
            }
            for major in majors.values()
        ]
        for method in (quy_doi.get("methods_by_school") or {}).get(code) or []:
            label = method.get("label") if isinstance(method, dict) else str(method)
            if label:
                methods.add(label)
        for row in conversions + regulations + certificates:
            if row.get("phuong_thuc"):
                methods.add(str(row["phuong_thuc"]))
        for row in conversion_rows:
            methods.update(str(k) for k in (row.get("cot_gia_tri") or {}).keys() if k)

        sources = []
        seen_sources = set()

        def add_source(value, label):
            text = str(value or "")
            for url in re.findall(r"https?://[^\s<>'\")]+", text):
                clean_url = url.rstrip(".,;")
                if clean_url in seen_sources:
                    continue
                seen_sources.add(clean_url)
                sources.append({"url": clean_url, "label": label})

        add_source(school.get("website"), "Website trường")
        if summary_regulation:
            for source_url in summary_regulation.get("nguon_tai_lieu") or []:
                add_source(source_url, "Nguồn của quy chế tổng hợp")
        for document in uploaded_documents:
            download_url = document.get("download_url") or ""
            if download_url and download_url not in seen_sources:
                seen_sources.add(download_url)
                sources.append({
                    "url": download_url,
                    "label": f"Tài liệu tải lên: {document.get('filename') or 'Tài liệu'}",
                })
            if document.get("source_type") == "cdn":
                add_source(
                    document.get("source_url"),
                    f"Link CDN: {document.get('filename') or 'Tài liệu'}",
                )
        for rows, label in (
            (admissions, "Điểm chuẩn / ngành tuyển sinh"),
            (conversions, "Quy đổi chứng chỉ"),
            (regulations, "Quy chế tuyển sinh"),
            (conversion_rows, "Bảng quy đổi phương thức"),
            (conversion_notes, "Ghi chú quy đổi"),
            (conversion_images, "Ảnh bảng quy đổi"),
            (certificates, "Quy đổi chứng chỉ"),
        ):
            for row in rows:
                add_source(row.get("url_nguon") or row.get("nguon"), label)

        return jsonify({
            "ok": True,
            "school": school,
            "majors": major_rows,
            "admissions": admissions,
            "methods": sorted(methods),
            "conversions": conversions,
            "regulations": regulations,
            "conversion_rows": conversion_rows,
            "conversion_notes": conversion_notes,
            "conversion_images": conversion_images,
            "certificate_conversions": certificates,
            "uploaded_documents": uploaded_documents,
            "summary_regulation": summary_regulation,
            "sources": sources,
            "summary": {
                "majors": len(major_rows),
                "admissions": len(admissions),
                "methods": len(methods),
                "conversion_rows": len(conversion_rows),
                "conversions": len(conversions) + len(certificates),
                "regulations": len(regulations) + len(conversion_notes),
                "sources": len(sources),
                "documents": len(uploaded_documents),
                "has_summary": bool(summary_regulation),
            },
        })

    @app.get("/api/schools/document/<code>/<name>")
    def download_school_document(code: str, name: str):
        safe_code = re.sub(r"[^A-Z0-9_-]", "", (code or "").upper())
        safe_name = secure_filename(name or "")
        if not safe_code or not safe_name:
            return jsonify({"ok": False, "error": "Tên tài liệu không hợp lệ."}), 400
        directory = os.path.join(app.config["UPLOADS_DIR"], safe_code)
        return send_from_directory(directory, safe_name, as_attachment=True)

    @app.post("/api/schools/upload-documents")
    def api_school_upload_documents():
        """Nhập file cho một trường và gộp dữ liệu, không chạy crawler website."""
        if (request.content_length or 0) > 100 * 1024 * 1024:
            return jsonify({"ok": False, "error": "Tổng dung lượng vượt quá 100 MB."}), 413
        code = (request.form.get("code") or "").strip().upper()
        try:
            year = int(request.form.get("year") or datetime.now().year)
        except ValueError:
            return jsonify({"ok": False, "error": "Năm tuyển sinh không hợp lệ."}), 400
        if not code or year < 2000 or year > 2100:
            return jsonify({"ok": False, "error": "Thiếu mã trường hoặc năm không hợp lệ."}), 400
        method = (request.form.get("method") or "").strip()
        if len(method) > 120:
            return jsonify({"ok": False, "error": "Tên phương thức quá dài."}), 400
        files = [f for f in request.files.getlist("files") if f and f.filename]
        remote_urls = [
            line.strip()
            for line in (request.form.get("urls") or "").splitlines()
            if line.strip()
        ]
        if not files and not remote_urls:
            return jsonify({"ok": False, "error": "Chưa chọn file hoặc nhập link CDN."}), 400
        if len(files) + len(remote_urls) > 20:
            return jsonify({
                "ok": False,
                "error": "Chỉ được bổ sung tối đa 20 file/link mỗi lần.",
            }), 400

        allowed = {".xlsx", ".xls", ".csv", ".pdf", ".docx", ".jpg", ".jpeg", ".png", ".webp", ".html", ".htm"}
        schools_data = dataset_store.load_schools(ROOT) or {}
        school = next(
            (
                item for item in (schools_data.get("schools") or [])
                if str(item.get("code") or "").upper() == code
            ),
            lookup_local_school(code),
        )
        if not school:
            return jsonify({"ok": False, "error": f"Không tìm thấy trường {code}."}), 404
        school_name = school.get("name") or school.get("short_name") or code

        from crawlers.uploaded_document_parser import (
            download_remote_document,
            parse_uploaded_document,
        )
        from core.models import MethodConversionBundle

        combined = CrawlBundle()
        method_bundle = MethodConversionBundle()
        documents = []
        errors = []
        upload_dir = os.path.join(app.config["UPLOADS_DIR"], code)
        os.makedirs(upload_dir, exist_ok=True)

        def add_parsed_document(
            path, original_name, stored_name, size, source_url, source_type
        ):
            try:
                crawl_part, method_part = parse_uploaded_document(
                    path, original_name, code, school_name, year, source_url, method
                )
            except Exception as exc:
                errors.append(f"{original_name}: không đọc được ({exc})")
                crawl_part = CrawlBundle()
                method_part = MethodConversionBundle()
            combined.admissions.extend(crawl_part.admissions)
            combined.conversions.extend(crawl_part.conversions)
            combined.regulations.extend(crawl_part.regulations)
            method_bundle.rows.extend(method_part.rows)
            method_bundle.notes.extend(method_part.notes)
            method_bundle.images.extend(method_part.images)
            method_bundle.conversions.extend(method_part.conversions)
            documents.append({
                "ma_truong": code,
                "ten_truong": school_name,
                "filename": original_name,
                "stored_name": stored_name,
                "source_type": source_type,
                "source_url": source_url,
                "year": year,
                "size": size,
                "uploaded_at": datetime.now().isoformat(timespec="seconds"),
                "admissions": len(crawl_part.admissions),
                "conversions": len(crawl_part.conversions) + len(method_part.rows),
                "regulations": len(crawl_part.regulations),
            })

        for uploaded in files:
            original_name = os.path.basename(uploaded.filename)
            ext = os.path.splitext(original_name)[1].lower()
            if ext not in allowed:
                errors.append(f"{original_name}: định dạng không được hỗ trợ")
                continue
            safe_original = secure_filename(original_name)
            if not safe_original:
                errors.append(f"{original_name}: tên file không hợp lệ")
                continue
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            stored_name = f"{stamp}_{safe_original}"
            path = os.path.join(upload_dir, stored_name)
            uploaded.save(path)
            size = os.path.getsize(path)
            if size > 20 * 1024 * 1024:
                os.unlink(path)
                errors.append(f"{original_name}: vượt quá 20 MB")
                continue
            source_url = url_for(
                "download_school_document",
                code=code,
                name=stored_name,
            )
            add_parsed_document(
                path, original_name, stored_name, size, source_url, "file"
            )

        for remote_url in remote_urls:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            try:
                remote = download_remote_document(
                    remote_url, upload_dir, stamp
                )
            except Exception as exc:
                errors.append(f"{remote_url}: không tải được ({exc})")
                continue
            add_parsed_document(
                remote["path"],
                remote["filename"],
                remote["stored_name"],
                remote["size"],
                remote["source_url"],
                "cdn",
            )

        if not documents and errors:
            return jsonify({"ok": False, "error": "; ".join(errors)}), 400

        crawl = app.config.get("LAST_CRAWL") or dataset_store.load_admissions(ROOT) or {}

        def append_unique(target, additions, fields):
            seen = {tuple(str(row.get(field) or "") for field in fields) for row in target}
            for row in additions:
                key = tuple(str(row.get(field) or "") for field in fields)
                if key not in seen:
                    seen.add(key)
                    target.append(row)

        admissions = list(crawl.get("admissions") or [])
        conversions = list(crawl.get("conversions") or [])
        regulations = list(crawl.get("regulations") or [])
        def admission_identity(row):
            return tuple(str(row.get(field) or "") for field in (
                "ma_truong", "ma_nganh", "ten_nganh", "nam", "phuong_thuc", "to_hop",
            ))

        incoming = [r.to_dict() for r in combined.admissions]
        existing_index = {}
        for index, row in enumerate(admissions):
            existing_index.setdefault(admission_identity(row), index)
        for row in incoming:
            key = admission_identity(row)
            previous = existing_index.get(key)
            if previous is None:
                existing_index[key] = len(admissions)
                admissions.append(row)
                continue
            current = admissions[previous]
            if row.get("diem_chuan") is not None:
                current["diem_chuan"] = row["diem_chuan"]
            if row.get("diem_chuan_ptxt") is not None:
                current["diem_chuan_ptxt"] = row["diem_chuan_ptxt"]
            if row.get("chi_tieu") is not None:
                current["chi_tieu"] = row["chi_tieu"]
            if row.get("thang_diem") is not None:
                current["thang_diem"] = row["thang_diem"]
            if row.get("nguon"):
                current["nguon"] = row["nguon"]
        append_unique(
            conversions,
            [r.to_dict() for r in combined.conversions],
            ("ma_truong", "loai_bang", "hang_muc", "diem_quy_doi", "nam"),
        )
        append_unique(
            regulations,
            [r.to_dict() for r in combined.regulations],
            ("ma_truong", "tieu_de", "noi_dung", "nam"),
        )
        crawl.update({
            "admissions": admissions,
            "conversions": conversions,
            "regulations": regulations,
            "uploaded_documents": list(crawl.get("uploaded_documents") or []) + documents,
            "codes": sorted({*[str(c).upper() for c in (crawl.get("codes") or [])], code}),
            "years": sorted({*[int(y) for y in (crawl.get("years") or [])], year}),
        })
        app.config["LAST_CRAWL"] = crawl
        dataset_store.save_admissions(ROOT, crawl)

        quy_doi = app.config.get("LAST_QUY_DOI") or dataset_store.load_quy_doi(ROOT) or {}
        qd_rows = list(quy_doi.get("rows") or [])
        qd_certs = list(quy_doi.get("certificate_conversions") or [])
        append_unique(
            qd_rows,
            [r.to_dict() for r in method_bundle.rows],
            ("ma_truong", "tieu_de_bang", "stt", "cot_gia_tri", "nam"),
        )
        append_unique(
            qd_certs,
            [r.to_dict() for r in combined.conversions],
            ("ma_truong", "loai_bang", "hang_muc", "diem_quy_doi", "nam"),
        )
        quy_doi["rows"] = qd_rows
        quy_doi["certificate_conversions"] = qd_certs
        old_summary = quy_doi.get("summary") or {}
        quy_doi["summary"] = {
            **old_summary,
            "rows": len(qd_rows),
            "certificates": len(qd_certs),
        }
        app.config["LAST_QUY_DOI"] = quy_doi
        dataset_store.save_quy_doi(ROOT, quy_doi)

        return jsonify({
            "ok": True,
            "files": len(documents),
            "admissions": len(combined.admissions),
            "conversions": len(combined.conversions) + len(method_bundle.rows),
            "regulations": len(combined.regulations),
            "documents": documents,
            "errors": errors,
        })

    @app.post("/api/schools/summarize")
    def api_school_summarize():
        """Tạo lại quy chế tổng hợp từ mọi nguồn dữ liệu đang lưu của một trường."""
        data = request.get_json(silent=True) or {}
        code = (data.get("code") or "").strip().upper()
        if not code:
            return jsonify({"ok": False, "error": "Thiếu mã trường."}), 400
        schools_data = dataset_store.load_schools(ROOT) or {}
        school = next(
            (
                item for item in (schools_data.get("schools") or [])
                if str(item.get("code") or "").upper() == code
            ),
            lookup_local_school(code),
        )
        if not school:
            return jsonify({"ok": False, "error": f"Không tìm thấy trường {code}."}), 404
        crawl = app.config.get("LAST_CRAWL") or dataset_store.load_admissions(ROOT) or {}
        quy_doi = app.config.get("LAST_QUY_DOI") or dataset_store.load_quy_doi(ROOT) or {}

        def has_school_rows(payload, keys):
            return any(
                str(row.get("ma_truong") or "").upper() == code
                for key in keys
                for row in (payload.get(key) or [])
            )

        has_data = has_school_rows(
            crawl, ("admissions", "conversions", "regulations", "uploaded_documents")
        ) or has_school_rows(
            quy_doi, ("rows", "notes", "certificate_conversions", "images")
        )
        if not has_data:
            return jsonify({
                "ok": False,
                "error": "Trường chưa có dữ liệu crawl hoặc tài liệu tải lên để tổng hợp.",
            }), 400

        from core.regulation_summarizer import summarize_school_regulation

        summary = summarize_school_regulation(
            crawl,
            quy_doi,
            code,
            school.get("name") or school.get("short_name") or code,
        )
        summaries = [
            row for row in (crawl.get("summaries") or [])
            if str(row.get("ma_truong") or "").upper() != code
        ]
        summaries.append(summary)
        crawl["summaries"] = summaries
        app.config["LAST_CRAWL"] = crawl
        dataset_store.save_admissions(ROOT, crawl)
        return jsonify({"ok": True, "summary": summary})

    @app.get("/api/crawl/session")
    def api_crawl_session():
        cached = app.config.get("LAST_CRAWL") or {}
        summary = dataset_store.summarize_admissions(cached)
        if not summary.get("has_data"):
            return jsonify({"ok": True, "has_data": False})
        admissions = cached.get("admissions") or []
        trends = build_trend_series(admissions)
        codes = summary.get("codes") or []
        return jsonify({
            "ok": True,
            "has_data": True,
            "schools": summary.get("schools") or 0,
            "admissions": summary.get("admissions") or 0,
            "conversions": summary.get("conversions") or 0,
            "regulations": summary.get("regulations") or 0,
            "codes": codes,
            "codes_text": format_codes_for_copy(codes, one_per_line=True),
            "years": summary.get("years") or [],
            "filename": summary.get("filename") or "",
            "download_url": summary.get("download_url")
            or (url_for("download_file", name=summary["filename"]) if summary.get("filename") else ""),
            "logs": cached.get("logs") or [
                f"Đã nạp dữ liệu đã lưu ({summary.get('saved_at') or 'đĩa'})."
            ],
            "trends": trends,
            "preview": admissions[:200],
            "saved_at": summary.get("saved_at") or "",
            "from_disk": True,
        })

    @app.get("/api/crawl/records")
    def api_crawl_records():
        """Bảng ngành / phương thức / điểm chuẩn / chỉ tiêu vừa thu thập."""
        cached = app.config.get("LAST_CRAWL") or dataset_store.load_admissions(ROOT) or {}
        collected = str(cached.get("_saved_at") or "")
        rows = []
        for item in cached.get("admissions") or []:
            rows.append({
                "ma_truong": item.get("ma_truong") or "",
                "ten_truong": item.get("ten_truong") or "",
                "nam": item.get("nam"),
                "ma_xet_tuyen": item.get("ma_xet_tuyen") or "",
                "ma_nganh": item.get("ma_nganh") or "",
                "ten_nganh": item.get("ten_nganh") or "",
                "phuong_thuc": item.get("phuong_thuc") or "",
                "diem_chuan": item.get("diem_chuan_ptxt") if item.get("diem_chuan_ptxt") is not None else item.get("diem_chuan"),
                "chi_tieu": item.get("chi_tieu"),
                "nguon": item.get("nguon") or "",
                "thu_thap_luc": item.get("thu_thap_luc") or collected,
            })
        years = sorted({
            int(row["nam"]) for row in rows
            if row.get("nam") is not None
        })
        methods_by_school: Dict[str, List[str]] = {}

        def remember_method(code: str, name: str):
            school_code = str(code or "").strip().upper()
            method_name = str(name or "").strip()
            if not school_code or not method_name:
                return
            bucket = methods_by_school.setdefault(school_code, [])
            if method_name not in bucket:
                bucket.append(method_name)

        for item in cached.get("admissions") or []:
            remember_method(item.get("ma_truong"), item.get("phuong_thuc"))
        for item in cached.get("regulations") or []:
            if str(item.get("tieu_de") or "") == "Phương thức tuyển sinh":
                remember_method(item.get("ma_truong"), item.get("phuong_thuc"))
        return jsonify({
            "ok": True,
            "total": len(rows),
            "years": years,
            "rows": rows,
            "methods_by_school": methods_by_school,
            "download_url": cached.get("download_url") or "",
        })

    def _record_key(item: dict) -> tuple:
        return (
            str(item.get("ma_truong") or "").strip().upper(),
            str(item.get("ma_xet_tuyen") or "").strip().upper(),
            str(item.get("ma_nganh") or item.get("ten_nganh") or "").strip(),
            str(item.get("phuong_thuc") or "").strip(),
        )

    def _score_number(value):
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            raise ValueError("Điểm không hợp lệ.")
        text = str(value).strip().replace(",", ".")
        if not text:
            return None
        number = float(text)
        if number < 0 or number > 200:
            raise ValueError("Điểm nằm ngoài khoảng cho phép.")
        return round(number, 2)

    def _major_for_admission_code(school_code: str, year: int, admission_code: str):
        cached = app.config.get("LAST_METHODS") or dataset_store.load_phuong_thuc(ROOT) or {}
        wanted = admission_code.strip().upper()
        matches = [
            row for row in (cached.get("records") or [])
            if str(row.get("ma_truong") or "").strip().upper() == school_code
            and str(row.get("ma_xet_tuyen") or "").strip().upper() == wanted
        ]
        exact = next((row for row in matches if int(row.get("nam") or 0) == year), None)
        return exact or (matches[0] if matches else None)

    def _school_method_names(school_code: str, year: int):
        """Tên phương thức tuyển sinh trường đang dùng trong năm học."""
        cached = app.config.get("LAST_METHODS") or dataset_store.load_phuong_thuc(ROOT) or {}
        rows = [
            row for row in (cached.get("records") or [])
            if str(row.get("ma_truong") or "").strip().upper() == school_code
            and int(row.get("nam") or 0) == year
        ]
        if not rows:
            rows = [
                row for row in (cached.get("records") or [])
                if str(row.get("ma_truong") or "").strip().upper() == school_code
            ]
        names = []
        seen = set()
        for row in rows:
            for item in row.get("hinh_thuc") or []:
                if not isinstance(item, dict) or item.get("ap_dung") is False:
                    continue
                name = str(item.get("ten") or item.get("id") or "").strip()
                folded = name.casefold()
                if not name or ("tất cả" in folded and "phương thức" in folded):
                    continue
                key = folded
                if key in seen:
                    continue
                seen.add(key)
                names.append(name)
        return names

    @app.post("/api/crawl/records/import")
    def api_crawl_records_import():
        """Nhập điểm chuẩn JSON cho đúng một trường và một năm học."""
        data = request.get_json(silent=True) or {}
        code = str(data.get("code") or "").strip().upper()
        try:
            year = int(data.get("year") or 0)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Năm học không hợp lệ."}), 400
        if not code or year < 2000 or year > 2100:
            return jsonify({"ok": False, "error": "Hãy chọn một trường và một năm học."}), 400
        raw_rows = data.get("rows")
        if isinstance(raw_rows, dict):
            raw_rows = next(
                (
                    raw_rows.get(key)
                    for key in ("danh_sach", "records", "data", "diem")
                    if isinstance(raw_rows.get(key), list)
                ),
                [raw_rows],
            )
        if not isinstance(raw_rows, list) or not raw_rows:
            return jsonify({"ok": False, "error": "Dữ liệu phải là danh sách điểm của một trường, một năm."}), 400

        schools_data = dataset_store.load_schools(ROOT) or {}
        school = next(
            (
                item for item in (schools_data.get("schools") or [])
                if str(item.get("code") or "").upper() == code
            ),
            lookup_local_school(code),
        )
        if not school:
            return jsonify({"ok": False, "error": f"Không tìm thấy trường {code}."}), 404
        school_name = school.get("name") or school.get("short_name") or code
        collected_at = datetime.now().isoformat(timespec="seconds")
        source = "Nhập dữ liệu điểm"
        incoming = []
        method_rows = []
        missing = []

        def combo_list(value):
            if isinstance(value, str):
                value = [part.strip() for part in value.replace(";", ",").split(",")]
            if not isinstance(value, list):
                return []
            return [str(part).strip() for part in value if str(part).strip()]

        def add_score(admission_code, major_code, major_name, method, thpt_score, method_score):
            incoming.append({
                "ma_truong": code,
                "ten_truong": school_name,
                "ma_xet_tuyen": admission_code,
                "ma_nganh": major_code,
                "ten_nganh": major_name,
                "nam": year,
                "phuong_thuc": method,
                "diem_chuan": thpt_score,
                "diem_chuan_ptxt": method_score,
                "chi_tieu": None,
                "nguon": source,
                "thu_thap_luc": collected_at,
            })

        try:
            for index, item in enumerate(raw_rows, start=1):
                if not isinstance(item, dict):
                    raise ValueError(f"Dòng {index} không đúng định dạng.")
                admission_code = str(item.get("Ma_xet_tuyen") or item.get("ma_xet_tuyen") or "").strip()
                if not admission_code:
                    raise ValueError(f"Dòng {index} thiếu Ma_xet_tuyen.")
                stated_name = str(item.get("Ten_nganh") or item.get("ten_nganh") or "").strip()
                stated_major = str(item.get("Ma_nganh") or item.get("ma_nganh") or "").strip()
                major = _major_for_admission_code(code, year, admission_code)
                if major:
                    major_code = stated_major or str(major.get("ma_nganh") or "").strip() or admission_code
                    major_name = stated_name or str(major.get("ten_nganh") or major.get("ten_chuong_trinh") or "").strip()
                else:
                    major_code = stated_major or admission_code
                    major_name = stated_name
                    if not stated_name:
                        missing.append(admission_code)
                quota = item.get("Chi_tieu", item.get("chi_tieu"))
                quota_text = ""
                if quota is not None and str(quota).strip() not in {"", "None"}:
                    quota_text = re.sub(r"[^\d]", "", str(quota))[:6]
                thpt = _score_number(item.get("Diem_chuan_THPT", item.get("diem_chuan_thpt")))
                if thpt is not None:
                    add_score(admission_code, major_code, major_name, "THPT", thpt, None)
                methods = item.get("phuong_thuc") or []
                shared = _score_number(item.get("diem_chuan", item.get("Diem_chuan")))
                if methods and all(not isinstance(method, dict) for method in methods):
                    names = []
                    for method in methods:
                        name = str(method).strip()
                        if name and name not in names:
                            names.append(name)
                    if not names:
                        raise ValueError(f"Dòng {admission_code}: thiếu mã phương thức.")
                    combos = combo_list(item.get("To_hop", item.get("to_hop")))
                    note = f"Tổ hợp: {', '.join(combos)}" if combos else ""
                    method_rows.append({
                        "ma_truong": code,
                        "ten_truong": school_name,
                        "nam": year,
                        "ma_xet_tuyen": admission_code,
                        "ma_nganh": stated_major or (str(major.get("ma_nganh") or "").strip() if major else ""),
                        "ten_nganh": major_name,
                        "chi_tieu": quota_text,
                        "ghi_chu": source,
                        "hinh_thuc": [
                            {
                                "id": name,
                                "ten": name,
                                "ap_dung": True,
                                "mo_ta": note,
                                "chi_tiet": {"to_hop_xet_tuyen": combos} if combos else {},
                            }
                            for name in names
                        ],
                    })
                    if shared is not None:
                        for name in names:
                            add_score(admission_code, major_code, major_name, name, shared, None)
                    continue
                if not methods and shared is not None:
                    school_methods = _school_method_names(code, year)
                    if not school_methods:
                        raise ValueError(
                            "Trường chưa có phương thức tuyển sinh cho năm này, nên chưa gán được điểm chung."
                        )
                    for name in school_methods:
                        add_score(admission_code, major_code, major_name, name, shared, None)
                    continue
                if methods and not isinstance(methods, list):
                    raise ValueError(f"Dòng {admission_code}: phuong_thuc phải là danh sách.")
                for method in methods:
                    if not isinstance(method, dict):
                        raise ValueError(f"Dòng {admission_code}: phương thức không đúng định dạng.")
                    name = str(method.get("ten") or method.get("ma") or "").strip()
                    if not name:
                        raise ValueError(f"Dòng {admission_code}: thiếu tên phương thức.")
                    add_score(
                        admission_code,
                        major_code,
                        major_name,
                        name,
                        None,
                        _score_number(method.get("diem")),
                    )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if not incoming and not method_rows:
            return jsonify({"ok": False, "error": "Không có điểm hoặc phương thức nào để lưu."}), 400
        saved_programs = 0
        if method_rows:
            method_cache = dict(app.config.get("LAST_METHODS") or dataset_store.load_phuong_thuc(ROOT) or {})
            method_records = _upsert_records(
                method_cache.get("records"),
                method_rows,
                ("ma_truong", "nam", "ma_xet_tuyen", "ma_nganh", "ten_nganh"),
            )
            method_payload = _method_payload(
                method_records,
                sorted({
                    *[int(item) for item in (method_cache.get("years") or []) if str(item).isdigit() or isinstance(item, int)],
                    year,
                }),
                sorted({*[str(item).upper() for item in (method_cache.get("codes") or [])], code}),
                method_cache,
            )
            app.config["LAST_METHODS"] = method_payload
            try:
                dataset_store.save_phuong_thuc(ROOT, method_payload)
            except OSError:
                pass
            saved_programs = len(method_rows)
        if not incoming:
            return jsonify({
                "ok": True,
                "rows": 0,
                "majors": len({row["ma_xet_tuyen"] for row in method_rows}),
                "programs": saved_programs,
                "missing": missing,
            })

        cached = dict(app.config.get("LAST_CRAWL") or dataset_store.load_admissions(ROOT) or {})
        kept = [
            row for row in (cached.get("admissions") or [])
            if not (
                str(row.get("ma_truong") or "").strip().upper() == code
                and int(row.get("nam") or 0) == year
                and str(row.get("nguon") or "") == source
            )
        ]
        admissions = _upsert_records(
            kept,
            incoming,
            ("ma_truong", "nam", "ma_xet_tuyen", "phuong_thuc"),
        )
        years = sorted({
            int(item["nam"]) for item in admissions
            if item.get("nam") is not None
        })
        codes = sorted({
            str(item.get("ma_truong") or "").strip().upper()
            for item in admissions
            if item.get("ma_truong")
        })
        cached["admissions"] = admissions
        cached["years"] = years
        cached["codes"] = codes
        app.config["LAST_CRAWL"] = cached
        try:
            dataset_store.save_admissions(ROOT, cached)
        except OSError:
            pass
        return jsonify({
            "ok": True,
            "rows": len(incoming),
            "majors": len({row["ma_xet_tuyen"] for row in incoming}),
            "programs": saved_programs,
            "missing": missing,
        })

    @app.post("/api/crawl/records/delete")
    def api_crawl_records_delete():
        """Xoá một hoặc nhiều dòng kết quả (trường + ngành + phương thức) khỏi dữ liệu đã lưu."""
        data = request.get_json(silent=True) or {}
        raw_keys = data.get("keys") or []
        school_scope = [
            str(code).strip().upper()
            for code in (data.get("schools") or [])
            if str(code).strip()
        ]
        try:
            scope_year = int(data.get("nam") or 0)
        except (TypeError, ValueError):
            scope_year = 0
        if school_scope:
            if scope_year < 2000:
                return jsonify({"ok": False, "error": "Hãy chọn năm học cần xoá."}), 400
        elif not isinstance(raw_keys, list) or not raw_keys:
            return jsonify({"ok": False, "error": "Chưa chọn dòng cần xoá."}), 400
        drop_years = {}
        for item in raw_keys:
            if not isinstance(item, dict):
                continue
            key = _record_key(item)
            if key == ("", "", "", ""):
                continue
            try:
                year = int(item.get("nam") or 0)
            except (TypeError, ValueError):
                year = 0
            drop_years.setdefault(key, set()).add(year)
        if not school_scope and not drop_years:
            return jsonify({"ok": False, "error": "Chưa chọn dòng cần xoá."}), 400

        def _keep_admission(item):
            if school_scope:
                school = str(item.get("ma_truong") or "").strip().upper()
                return not (school in school_scope and int(item.get("nam") or 0) == scope_year)
            years_for_key = drop_years.get(_record_key(item))
            if not years_for_key:
                return True
            if 0 in years_for_key:
                return False
            return int(item.get("nam") or 0) not in years_for_key

        cached = dict(app.config.get("LAST_CRAWL") or dataset_store.load_admissions(ROOT) or {})
        admissions = [
            item for item in (cached.get("admissions") or [])
            if _keep_admission(item)
        ]
        years = sorted({
            int(item["nam"]) for item in admissions
            if item.get("nam") is not None
        })
        codes = sorted({
            str(item.get("ma_truong") or "").strip().upper()
            for item in admissions
            if item.get("ma_truong")
        })
        cached["admissions"] = admissions
        cached["years"] = years
        cached["codes"] = codes
        app.config["LAST_CRAWL"] = cached
        try:
            dataset_store.save_admissions(ROOT, cached)
        except OSError:
            pass

        rows = []
        for item in admissions:
            rows.append({
                "ma_truong": item.get("ma_truong") or "",
                "ten_truong": item.get("ten_truong") or "",
                "nam": item.get("nam"),
                "ma_xet_tuyen": item.get("ma_xet_tuyen") or "",
                "ma_nganh": item.get("ma_nganh") or "",
                "ten_nganh": item.get("ten_nganh") or "",
                "phuong_thuc": item.get("phuong_thuc") or "",
                "diem_chuan": item.get("diem_chuan_ptxt") if item.get("diem_chuan_ptxt") is not None else item.get("diem_chuan"),
                "chi_tieu": item.get("chi_tieu"),
                "nguon": item.get("nguon") or "",
            })
        return jsonify({
            "ok": True,
            "removed": len(drop_years),
            "total": len(rows),
            "years": years,
            "rows": rows,
            "download_url": cached.get("download_url") or "",
        })

    @app.post("/api/crawl/records/update")
    def api_crawl_records_update():
        """Sửa điểm chuẩn hoặc chỉ tiêu của một ngành, phương thức và năm."""
        data = request.get_json(silent=True) or {}
        field = str(data.get("field") or "")
        if field not in ("diem", "chi_tieu"):
            return jsonify({"ok": False, "error": "Ô cần sửa không hợp lệ."}), 400
        try:
            year = int(data.get("nam"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Thiếu năm của ô cần sửa."}), 400
        raw_value = data.get("value")
        value = None
        if raw_value is not None and raw_value != "":
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "Giá trị không hợp lệ."}), 400
            if field == "chi_tieu":
                value = int(round(value))
            else:
                value = round(value, 2)

        target = _record_key(data)
        if target == ("", "", ""):
            return jsonify({"ok": False, "error": "Không tìm thấy dòng cần sửa."}), 400

        cached = dict(app.config.get("LAST_CRAWL") or dataset_store.load_admissions(ROOT) or {})
        admissions = list(cached.get("admissions") or [])
        matched = [
            item for item in admissions
            if _record_key(item) == target and int(item.get("nam") or 0) == year
        ]
        if not matched:
            sample = next((item for item in admissions if _record_key(item) == target), None)
            if sample is None:
                return jsonify({"ok": False, "error": "Không tìm thấy dòng cần sửa."}), 404
            created = dict(sample)
            created["nam"] = year
            created["diem_chuan"] = None
            created["diem_chuan_ptxt"] = None
            created["chi_tieu"] = None
            admissions.append(created)
            matched = [created]

        for item in matched:
            if field == "chi_tieu":
                item["chi_tieu"] = value
                continue
            if item.get("diem_chuan_ptxt") is not None:
                item["diem_chuan_ptxt"] = value
            else:
                item["diem_chuan"] = value

        cached["admissions"] = admissions
        app.config["LAST_CRAWL"] = cached
        try:
            dataset_store.save_admissions(ROOT, cached)
        except OSError:
            pass
        return jsonify({"ok": True, "field": field, "nam": year, "value": value})

    @app.get("/api/crawl/table")
    def api_crawl_table():
        """Bảng xem lại: nhóm theo trường, điểm theo phương thức × năm."""
        cached = app.config.get("LAST_CRAWL") or {}
        admissions = cached.get("admissions") or []
        if not admissions:
            return jsonify({"ok": False, "error": "Chưa có dữ liệu đã tổng hợp."}), 400
        years = cached.get("years") or None
        payload = build_grouped_score_view(admissions, years=years)
        payload["ok"] = True
        return jsonify(payload)

    @app.get("/api/quy-doi/by-school")
    def api_quy_doi_by_school():
        """Lấy dữ liệu quy đổi đã lọc theo 1 mã trường (cho pills/bảng chi tiết)."""
        code = (request.args.get("code") or "").strip().upper()
        aliases = {"NEU": "KHA", "FTU": "NTH", "HUST": "BKA", "UET": "QHI"}
        code = aliases.get(code, code)
        if not code:
            return jsonify({"ok": False, "error": "Thiếu mã trường."}), 400

        cached = app.config.get("LAST_QUY_DOI") or {}
        if not (cached.get("rows") or cached.get("school_results") or cached.get("images")):
            disk = dataset_store.load_quy_doi(ROOT)
            if disk:
                app.config["LAST_QUY_DOI"] = disk
                cached = disk
        if not cached:
            return jsonify({"ok": False, "error": "Chưa có dữ liệu quy đổi đã lưu."}), 404

        def match(c):
            return str(c or "").upper() == code

        rows = [r for r in (cached.get("rows") or []) if match(r.get("ma_truong"))]
        notes = [n for n in (cached.get("notes") or []) if match(n.get("ma_truong"))]
        images = [i for i in (cached.get("images") or []) if match(i.get("ma_truong"))]
        ranges = [g for g in (cached.get("ranges") or []) if match(g.get("ma_truong"))]
        certificates = [
            c for c in (cached.get("certificate_conversions") or []) if match(c.get("ma_truong"))
        ]
        school_results = [
            s for s in (cached.get("school_results") or []) if match(s.get("code"))
        ]
        methods = (cached.get("methods_by_school") or {}).get(code) or []
        download_url = cached.get("download_url") or ""
        filename = cached.get("filename") or ""
        if not download_url and filename:
            download_url = url_for("download_file", name=filename)

        return jsonify({
            "ok": True,
            "code": code,
            "rows": rows,
            "notes": notes,
            "images": images,
            "ranges": ranges,
            "certificate_conversions": certificates,
            "school_results": school_results,
            "methods": methods,
            "method_labels": cached.get("method_labels") or METHOD_LABELS,
            "summary": {
                "schools": 1 if school_results else 0,
                "rows": len(rows),
                "notes": len(notes),
                "images": len(images),
                "ranges": len(ranges),
                "certificates": len(certificates),
            },
            "download_url": download_url,
            "filename": filename,
        })

    @app.get("/api/quy-doi/records")
    def api_quy_doi_records():
        """Bảng quy đổi chứng chỉ và điểm thi THPT đã thu thập."""
        cached = app.config.get("LAST_QUY_DOI") or {}
        if not (cached.get("rows") or cached.get("certificate_conversions") or cached.get("notes")):
            disk = dataset_store.load_quy_doi(ROOT)
            if disk:
                app.config["LAST_QUY_DOI"] = disk
                cached = disk
        items = []
        for cert in cached.get("certificate_conversions") or []:
            level = str(cert.get("hang_muc") or "").strip()
            score = str(cert.get("diem_quy_doi") or "").strip()
            content = " → ".join(part for part in (level, score) if part)
            scale = str(cert.get("thang_diem") or "").strip()
            if scale:
                content = f"{content} (thang {scale})" if content else f"Thang {scale}"
            if not content:
                content = str(cert.get("chi_tiet_hang") or "").strip()
            items.append({
                "ma_truong": cert.get("ma_truong") or "",
                "ten_truong": cert.get("ten_truong") or "",
                "nam": cert.get("nam"),
                "loai": cert.get("loai_bang") or "Chứng chỉ",
                "noi_dung": content,
                "nguon": cert.get("nguon") or "",
            })
        for row in cached.get("rows") or []:
            pairs = row.get("cot_gia_tri") or {}
            content = "; ".join(f"{key}: {value}" for key, value in pairs.items() if value)
            items.append({
                "ma_truong": row.get("ma_truong") or "",
                "ten_truong": row.get("ten_truong") or "",
                "nam": row.get("nam"),
                "loai": row.get("tieu_de_bang") or "Quy đổi phương thức",
                "noi_dung": content,
                "nguon": row.get("url_nguon") or row.get("nguon") or "",
            })
        for note in cached.get("notes") or []:
            items.append({
                "ma_truong": note.get("ma_truong") or "",
                "ten_truong": note.get("ten_truong") or "",
                "nam": note.get("nam"),
                "loai": note.get("tieu_de") or "Quy chế",
                "noi_dung": note.get("noi_dung") or "",
                "nguon": note.get("url_nguon") or note.get("nguon") or "",
            })
        calculators = []
        by_school: Dict[str, Dict[str, Any]] = {}
        for row in cached.get("rows") or []:
            code = str(row.get("ma_truong") or "").strip().upper()
            if not code:
                continue
            bucket = by_school.setdefault(code, {"name": row.get("ten_truong") or "", "rows": []})
            if row.get("ten_truong"):
                bucket["name"] = row.get("ten_truong")
            bucket["rows"].append(row)
        for code, bucket in sorted(by_school.items()):
            latest_rows, year, narrowed = rows_for_latest_year(bucket["rows"])
            methods = list_methods_from_rows(latest_rows, code)
            if len(methods) < 2:
                continue
            calculators.append({
                "ma_truong": code,
                "ten_truong": bucket["name"],
                "nam": year,
                "nhieu_nam": narrowed,
                "methods": [{"id": m.get("id"), "label": m.get("label") or m.get("id")} for m in methods],
            })
        return jsonify({
            "ok": True,
            "total": len(items),
            "rows": items,
            "calculators": calculators,
            "download_url": cached.get("download_url") or "",
        })

    def _quy_doi_identity(ma_truong: Any, nam: Any, loai: Any, noi_dung: Any) -> tuple:
        return (
            str(ma_truong or "").strip().upper(),
            str(nam if nam is not None else ""),
            str(loai or "").strip(),
            str(noi_dung or "").strip(),
        )

    def _certificate_content(cert: dict) -> str:
        level = str(cert.get("hang_muc") or "").strip()
        score = str(cert.get("diem_quy_doi") or "").strip()
        content = " → ".join(part for part in (level, score) if part)
        scale = str(cert.get("thang_diem") or "").strip()
        if scale:
            content = f"{content} (thang {scale})" if content else f"Thang {scale}"
        if not content:
            content = str(cert.get("chi_tiet_hang") or "").strip()
        return content

    @app.post("/api/quy-doi/records/import")
    def api_quy_doi_records_import():
        """Nhập quy chế quy đổi JSON: mỗi phương thức một danh sách mức."""
        data = request.get_json(silent=True) or {}
        code = str(data.get("code") or "").strip().upper()
        try:
            year = int(data.get("year") or 0)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "Năm học không hợp lệ."}), 400
        if not code or year < 2000 or year > 2100:
            return jsonify({"ok": False, "error": "Hãy chọn một trường và một năm học."}), 400
        school = lookup_local_school(code)
        if not school:
            schools_data = dataset_store.load_schools(ROOT) or {}
            school = next(
                (
                    item for item in (schools_data.get("schools") or [])
                    if str(item.get("code") or "").upper() == code
                ),
                None,
            )
        if not school:
            return jsonify({"ok": False, "error": f"Không tìm thấy trường {code}."}), 404
        school_name = school.get("name") or school.get("short_name") or code
        try:
            rows, notes, method_ids = conversion_rows_from_payload(
                data.get("quy_che") if "quy_che" in data else data.get("rows"),
                code,
                school_name,
                year,
            )
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

        cached = dict(app.config.get("LAST_QUY_DOI") or dataset_store.load_quy_doi(ROOT) or {})
        replaced_titles = {row["tieu_de_bang"] for row in rows}
        cached["rows"] = [
            row for row in (cached.get("rows") or [])
            if not (
                str(row.get("ma_truong") or "").upper() == code
                and int(row.get("nam") or 0) == year
                and row.get("nguon") == CONVERSION_IMPORT_SOURCE
                and row.get("tieu_de_bang") in replaced_titles
            )
        ]
        cached["notes"] = [
            note for note in (cached.get("notes") or [])
            if not (
                str(note.get("ma_truong") or "").upper() == code
                and int(note.get("nam") or 0) == year
                and note.get("nguon") == CONVERSION_IMPORT_SOURCE
                and (
                    note.get("tieu_de") in replaced_titles
                    or note.get("tieu_de") == "Nguồn quy chế quy đổi"
                )
            )
        ]
        app.config["LAST_QUY_DOI"] = cached
        methods = list_methods_from_rows(rows, code)
        payload = {
            "rows": rows,
            "notes": notes,
            "school_results": [{
                "code": code,
                "name": school_name,
                "ok": True,
                "row_count": len(rows),
                "notes": len(notes),
                "images": 0,
                "ranges": 0,
                "url": rows[0].get("url_nguon") or "",
                "error": "",
            }],
            "methods_by_school": {code: methods},
            "method_labels": METHOD_LABELS,
        }
        _merge_last_quy_doi(payload, [code])
        return jsonify({
            "ok": True,
            "rows": len(rows),
            "methods": len(method_ids),
            "method_ids": method_ids,
        })

    @app.post("/api/quy-doi/records/delete")
    def api_quy_doi_records_delete():
        """Xoá dòng quy đổi hoặc quy chế đã chọn khỏi dữ liệu đã lưu."""
        data = request.get_json(silent=True) or {}
        raw_keys = data.get("keys") or []
        if not isinstance(raw_keys, list) or not raw_keys:
            return jsonify({"ok": False, "error": "Chưa chọn dòng cần xoá."}), 400
        wanted = {
            _quy_doi_identity(item.get("ma_truong"), item.get("nam"), item.get("loai"), item.get("noi_dung"))
            for item in raw_keys if isinstance(item, dict)
        }
        wanted.discard(("", "", "", ""))
        if not wanted:
            return jsonify({"ok": False, "error": "Chưa chọn dòng cần xoá."}), 400

        cached = dict(app.config.get("LAST_QUY_DOI") or dataset_store.load_quy_doi(ROOT) or {})
        cached["certificate_conversions"] = [
            cert for cert in (cached.get("certificate_conversions") or [])
            if _quy_doi_identity(
                cert.get("ma_truong"),
                cert.get("nam"),
                cert.get("loai_bang") or "Chứng chỉ",
                _certificate_content(cert),
            ) not in wanted
        ]
        cached["rows"] = [
            row for row in (cached.get("rows") or [])
            if _quy_doi_identity(
                row.get("ma_truong"),
                row.get("nam"),
                row.get("tieu_de_bang") or "Quy đổi phương thức",
                "; ".join(
                    f"{key}: {value}"
                    for key, value in (row.get("cot_gia_tri") or {}).items()
                    if value
                ),
            ) not in wanted
        ]
        cached["notes"] = [
            note for note in (cached.get("notes") or [])
            if _quy_doi_identity(
                note.get("ma_truong"),
                note.get("nam"),
                note.get("tieu_de") or "Quy chế",
                note.get("noi_dung") or "",
            ) not in wanted
        ]
        app.config["LAST_QUY_DOI"] = cached
        try:
            dataset_store.save_quy_doi(ROOT, cached)
        except OSError:
            pass
        with app.test_request_context():
            listed = api_quy_doi_records().get_json()
        return jsonify({
            "ok": True,
            "removed": len(wanted),
            "rows": (listed or {}).get("rows") or [],
            "download_url": cached.get("download_url") or "",
        })

    @app.get("/api/quy-doi/session")
    def api_quy_doi_session():
        """
        Trả session quy đổi cho UI.
        Mặc định bản nhẹ (không nhúng hàng chục nghìn dòng) để tránh treo trình duyệt.
        ?full=1 chỉ dùng khi thật sự cần toàn bộ payload.
        """
        cached = app.config.get("LAST_QUY_DOI") or {}
        summary = dataset_store.summarize_quy_doi(cached)
        if not summary.get("has_data"):
            # Thử nạp lại từ đĩa nếu bộ nhớ trống
            disk = dataset_store.load_quy_doi(ROOT)
            if disk:
                app.config["LAST_QUY_DOI"] = disk
                cached = disk
                summary = dataset_store.summarize_quy_doi(cached)
            if not summary.get("has_data"):
                return jsonify({"ok": True, "has_data": False})

        want_full = (request.args.get("full") or "").strip().lower() in ("1", "true", "yes")
        download_url = cached.get("download_url") or ""
        filename = cached.get("filename") or ""
        if not download_url and filename:
            download_url = url_for("download_file", name=filename)

        if want_full:
            payload = dict(cached)
            payload["ok"] = True
            payload["has_data"] = True
            payload["from_disk"] = True
            payload["download_url"] = download_url
            payload["filename"] = filename
            return jsonify(payload)

        rows = cached.get("rows") or []
        notes = cached.get("notes") or []
        images = cached.get("images") or []
        ranges = cached.get("ranges") or []
        certificates = cached.get("certificate_conversions") or []
        # Preview giới hạn — đủ xem mẫu, không đủ để treo DOM
        ROW_CAP, NOTE_CAP, IMG_CAP, RANGE_CAP, CERT_CAP = 120, 40, 24, 80, 200
        return jsonify({
            "ok": True,
            "has_data": True,
            "from_disk": True,
            "light": True,
            "summary": summary if summary.get("has_data") else {
                "has_data": True,
                "schools": len(cached.get("school_results") or []),
                "rows": len(rows),
                "notes": len(notes),
                "images": len(images),
                "ranges": len(ranges),
                "certificates": len(certificates),
            },
            "school_results": cached.get("school_results") or [],
            "methods_by_school": cached.get("methods_by_school") or {},
            "method_labels": cached.get("method_labels") or METHOD_LABELS,
            "download_url": download_url,
            "filename": filename,
            "rows": rows[:ROW_CAP],
            "notes": notes[:NOTE_CAP],
            "images": images[:IMG_CAP],
            "ranges": ranges[:RANGE_CAP],
            "certificate_conversions": certificates[:CERT_CAP],
            "preview_capped": {
                "rows": len(rows) > ROW_CAP,
                "notes": len(notes) > NOTE_CAP,
                "images": len(images) > IMG_CAP,
                "ranges": len(ranges) > RANGE_CAP,
                "rows_total": len(rows),
                "notes_total": len(notes),
                "images_total": len(images),
                "ranges_total": len(ranges),
            },
        })

    @app.post("/api/datasets/load")
    def api_datasets_load():
        """Nạp snapshot JSON vào bộ nhớ (LAST_CRAWL / LAST_QUY_DOI)."""
        data = request.get_json(silent=True) or {}
        kind = (data.get("kind") or "").strip().lower()
        name = (data.get("name") or "").strip()
        path = dataset_store.resolve_dataset_file(ROOT, name) if name else None

        if kind in ("admissions", "crawl"):
            payload = dataset_store.load_admissions(ROOT, path)
            if not payload or not (payload.get("admissions") or []):
                return jsonify({"ok": False, "error": "Không tìm thấy dữ liệu đã tổng hợp."}), 404
            app.config["LAST_CRAWL"] = payload
            # Cập nhật latest nếu nạp từ snapshot
            if path and os.path.abspath(path) != os.path.abspath(dataset_store.admissions_latest_path(ROOT)):
                try:
                    dataset_store.save_admissions(ROOT, payload)
                except OSError:
                    pass
            summary = dataset_store.summarize_admissions(payload)
            trends = build_trend_series(payload.get("admissions") or [])
            return jsonify({"ok": True, "kind": "admissions", "summary": summary, "trends": trends})

        if kind in ("quy_doi", "quy-doi"):
            payload = dataset_store.load_quy_doi(ROOT, path)
            if not payload:
                return jsonify({"ok": False, "error": "Không tìm thấy dữ liệu quy đổi."}), 404
            app.config["LAST_QUY_DOI"] = payload
            if path and os.path.abspath(path) != os.path.abspath(dataset_store.quy_doi_latest_path(ROOT)):
                try:
                    dataset_store.save_quy_doi(ROOT, payload)
                except OSError:
                    pass
            return jsonify({
                "ok": True,
                "kind": "quy_doi",
                "summary": dataset_store.summarize_quy_doi(payload),
            })

        return jsonify({"ok": False, "error": "kind phải là admissions hoặc quy_doi."}), 400

    @app.post("/api/datasets/reuse")
    def api_datasets_reuse():
        """
        Tái sử dụng Excel quy đổi → NDJSON progress 0–100% + ghi Snapshot JSON.
        """
        data = request.get_json(silent=True) or {}
        kind = (data.get("kind") or "").strip().lower()
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"ok": False, "error": "Thiếu tên file."}), 400
        if kind not in ("quy_doi", "quy-doi", "quydoi"):
            return jsonify({"ok": False, "error": "Hiện chỉ hỗ trợ tái sử dụng file quy đổi điểm."}), 400

        path = dataset_store.resolve_dataset_file(ROOT, name)
        if not path:
            return jsonify({"ok": False, "error": "Không tìm thấy file."}), 404

        @stream_with_context
        def generate():
            def emit(pct, message, **extra):
                return json.dumps({
                    "type": "progress",
                    "pct": max(0, min(100, int(pct))),
                    "message": message,
                    **extra,
                }, ensure_ascii=False) + "\n"

            try:
                yield emit(2, "Đang chuẩn bị…")
                payload = None
                source = "excel"
                excel_ts = None
                base = os.path.basename(path)
                m = re.match(r"quy_doi_diem_(\d{8}_\d{6})\.xlsx$", base, re.I)
                if m:
                    excel_ts = m.group(1)
                    snap = os.path.join(
                        dataset_store.datasets_dir(ROOT), "quy_doi", f"quy_doi_{excel_ts}.json"
                    )
                    if os.path.isfile(snap):
                        yield emit(12, "Đang nạp Snapshot JSON cùng timestamp…")
                        payload = dataset_store.load_quy_doi(ROOT, snap)
                        source = "json"

                if not payload and base.lower().endswith(".xlsx"):
                    yield emit(8, f"Đang đọc Excel {base}…")
                    import threading
                    from queue import Queue, Empty

                    q: Queue = Queue()

                    def on_prog(pct, msg):
                        mapped = 8 + int(pct * 0.70)
                        q.put(("progress", mapped, msg))

                    def worker():
                        try:
                            crawler = ScoreConversionCrawler()
                            result = crawler.import_excel(path, progress_cb=on_prog)
                            q.put(("result", result, None))
                        except Exception as exc:
                            q.put(("error", None, exc))

                    threading.Thread(target=worker, daemon=True).start()
                    while True:
                        try:
                            kind_ev, a, b = q.get(timeout=180)
                        except Empty:
                            yield json.dumps({
                                "type": "done",
                                "ok": False,
                                "error": "Hết thời gian chờ khi đọc Excel.",
                            }, ensure_ascii=False) + "\n"
                            return
                        if kind_ev == "progress":
                            yield emit(a, b)
                        elif kind_ev == "result":
                            payload = a
                            yield emit(80, "Đã đọc xong Excel")
                            break
                        elif kind_ev == "error":
                            raise b
                elif not payload and base.lower().endswith(".json"):
                    yield emit(40, "Đang nạp file JSON…")
                    payload = dataset_store.load_quy_doi(ROOT, path)
                    source = "json"

                if not payload:
                    yield json.dumps({
                        "type": "done",
                        "ok": False,
                        "error": "Không đọc được dữ liệu quy đổi từ file.",
                    }, ensure_ascii=False) + "\n"
                    return

                yield emit(84, "Đang bổ sung phương thức theo trường…")
                payload = dict(payload)
                payload["ok"] = True
                payload["filename"] = base if base.lower().endswith(".xlsx") else (payload.get("filename") or base)
                if base.lower().endswith(".xlsx"):
                    payload["download_url"] = url_for("download_file", name=base)
                elif payload.get("filename"):
                    payload["download_url"] = url_for("download_file", name=payload["filename"])
                payload["_reused_from"] = base
                payload["_reused_at"] = datetime.now().isoformat(timespec="seconds")

                if not payload.get("methods_by_school"):
                    methods_by_school = {}
                    metas = payload.get("school_results") or []
                    total_m = max(len(metas), 1)
                    for i, meta in enumerate(metas, start=1):
                        code = meta.get("code") or ""
                        from_table = list_methods_from_rows(payload.get("rows") or [], code)
                        methods_by_school[code] = _enrich_methods_for_school(code, from_table)
                        if i % 20 == 0 or i == total_m:
                            pct = 84 + int(8 * i / total_m)
                            yield emit(pct, f"Đang gắn phương thức… ({i}/{total_m})")
                    payload["methods_by_school"] = methods_by_school
                if not payload.get("method_labels"):
                    payload["method_labels"] = METHOD_LABELS

                yield emit(94, "Đang ghi Snapshot JSON…")
                saved = dataset_store.save_quy_doi(ROOT, payload, snapshot_ts=excel_ts)
                app.config["LAST_QUY_DOI"] = payload
                snap_name = saved.get("snapshot_name") or os.path.basename(saved.get("snapshot") or "")
                yield emit(100, "Hoàn tất")
                yield json.dumps({
                    "type": "done",
                    "ok": True,
                    "kind": "quy_doi",
                    "source": source,
                    "filename": base,
                    "snapshot": snap_name,
                    "summary": dataset_store.summarize_quy_doi(payload),
                }, ensure_ascii=False) + "\n"
            except (OSError, ValueError, FileNotFoundError) as e:
                yield json.dumps({
                    "type": "done",
                    "ok": False,
                    "error": str(e),
                }, ensure_ascii=False) + "\n"
            except Exception as e:
                yield json.dumps({
                    "type": "done",
                    "ok": False,
                    "error": f"Tái sử dụng thất bại: {e}",
                }, ensure_ascii=False) + "\n"

        return Response(
            generate(),
            mimetype="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache, no-store",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/datasets/clear")
    def api_datasets_clear():
        """Làm sạch dữ liệu đã lưu theo nhóm (hoặc toàn bộ)."""
        data = request.get_json(silent=True) or {}
        kind = (data.get("kind") or "").strip().lower()
        try:
            result = dataset_store.clear_kind(ROOT, kind)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400
        except OSError as e:
            return jsonify({"ok": False, "error": str(e)}), 500

        # Xoá cache trong bộ nhớ
        if kind in ("schools", "truong", "school", "all"):
            pass  # schools không cache riêng trong app.config
        if kind in ("admissions", "crawl", "tong_hop", "all"):
            app.config["LAST_CRAWL"] = {}
        if kind in ("quy_doi", "quy-doi", "quydoi", "all"):
            app.config["LAST_QUY_DOI"] = {}

        return jsonify({"ok": True, **result})

    @app.post("/api/datasets/delete")
    def api_datasets_delete():
        """Xoá một file snapshot / Excel cụ thể."""
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"ok": False, "error": "Thiếu tên file."}), 400
        try:
            result = dataset_store.delete_dataset_file(ROOT, name)
        except FileNotFoundError as e:
            return jsonify({"ok": False, "error": str(e)}), 404
        except OSError as e:
            return jsonify({"ok": False, "error": str(e)}), 500

        # Nếu xoá bản latest → làm trống cache tương ứng
        base = result.get("removed") or ""
        if base == "admissions_latest.json":
            app.config["LAST_CRAWL"] = {}
        elif base == "quy_doi_latest.json":
            app.config["LAST_QUY_DOI"] = {}

        return jsonify(result)

    @app.get("/download/<name>")
    def download_file(name: str):
        safe = os.path.basename(name)
        path = os.path.join(app.config["OUTPUT_DIR"], safe)
        if not os.path.isfile(path):
            path = dataset_store.resolve_dataset_file(ROOT, safe) or ""
        if not path or not os.path.isfile(path):
            return "Không tìm thấy file.", 404
        return send_file(path, as_attachment=True, download_name=safe)

    return app

app = create_app()


if __name__ == "__main__":
    host = "127.0.0.1"
    port = int(os.environ.get("UI_PORT", "8080"))
    print(f"\n[UI] Bootstrap web: http://{host}:{port}")
    print("[UI] Nhấn Ctrl+C để dừng.\n")
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=4)
    except ImportError:
        print("[UI] Chưa có waitress — fallback Flask dev server.")
        print("     pip3 install waitress\n")
        app.run(host=host, port=port, debug=True)
