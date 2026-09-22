# -*- coding: utf-8 -*-
"""
Đánh giá cơ hội trúng tuyển dựa trên điểm chuẩn đã cào + AI miễn phí (tuỳ chọn).

Ưu tiên gọi AI (không cần key):
  1. Pollinations (text.pollinations.ai) — miễn phí, không API key
  2. Groq nếu có GROQ_API_KEY
  3. Gemini nếu có GEMINI_API_KEY
Fallback: phân tích thống kê nội bộ (không cần mạng).
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import requests

from core.aggregator import METHOD_COLUMN_LABELS, method_column_key
from core.normalizer import get_school_display_name


def _score_of(rec: Dict[str, Any]) -> Optional[float]:
    """Ưu tiên điểm theo thang PTXT nếu có, không thì điểm chuẩn chung."""
    for key in ("diem_chuan_ptxt", "diem_chuan"):
        v = rec.get(key)
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def build_trend_series(
    admissions: List[Dict[str, Any]],
    school_code: Optional[str] = None,
    method: Optional[str] = None,
    max_majors: int = 18,
) -> Dict[str, Any]:
    """
    Biểu đồ Line + Drilldown (ApexCharts):
      - Root: mỗi ngành = 1 line, trục X = năm
      - Drilldown theo năm: điểm từng ngành trong năm đó
    """
    all_schools = sorted(
        {(r.get("ma_truong") or "").upper() for r in admissions if r.get("ma_truong")}
    )

    # method -> count (để đổ select)
    method_counts: Dict[str, int] = defaultdict(int)
    for rec in admissions:
        sc = _score_of(rec)
        if sc is None:
            continue
        mid = method_column_key(rec.get("phuong_thuc") or "") or "OTHER"
        method_counts[mid] += 1

    methods = sorted(
        method_counts.keys(),
        key=lambda m: (
            list(METHOD_COLUMN_LABELS.keys()).index(m)
            if m in METHOD_COLUMN_LABELS
            else 99,
            m,
        ),
    )
    method_options = [
        {"id": m, "label": METHOD_COLUMN_LABELS.get(m, m), "count": method_counts[m]}
        for m in methods
    ]

    code = (school_code or "").strip().upper() or None
    if not code and all_schools:
        code = all_schools[0]

    method_id = (method or "").strip().upper().replace("VACT", "V-ACT") or None
    if not method_id:
        # Ưu tiên THPT nếu có
        method_id = "THPT" if "THPT" in method_counts else (methods[0] if methods else "THPT")

    filtered: List[Dict[str, Any]] = []
    for rec in admissions:
        if code and (rec.get("ma_truong") or "").upper() != code:
            continue
        mid = method_column_key(rec.get("phuong_thuc") or "") or "OTHER"
        if mid != method_id:
            if not (
                method_id in ("HSA", "V-ACT", "TSA") and mid == "DGNL"
            ) and not (method_id == "DGNL" and mid in ("HSA", "V-ACT", "TSA")):
                continue
        sc = _score_of(rec)
        if sc is None:
            continue
        year = rec.get("nam")
        if year is None:
            continue
        try:
            year = int(year)
        except (TypeError, ValueError):
            continue
        filtered.append({**rec, "_score": sc, "_year": year, "_method": mid})

    # major_key -> year -> best score (lấy max nếu trùng tổ hợp)
    by_major: Dict[str, Dict[int, float]] = defaultdict(dict)
    major_meta: Dict[str, Dict[str, str]] = {}
    for rec in filtered:
        ma = (rec.get("ma_nganh") or "").strip()
        ten = (rec.get("ten_nganh") or "").strip() or "(Không rõ ngành)"
        key = f"{ma}|{ten}" if ma else ten
        y = rec["_year"]
        sc = rec["_score"]
        prev = by_major[key].get(y)
        if prev is None or sc > prev:
            by_major[key][y] = sc
        if key not in major_meta:
            major_meta[key] = {
                "ma_nganh": ma,
                "ten_nganh": ten,
                "label": f"{ten}" + (f" ({ma})" if ma else ""),
            }

    all_years = sorted({y for years in by_major.values() for y in years})

    # Xếp ngành: nhiều năm dữ liệu trước, rồi điểm năm mới nhất
    def major_rank(key: str) -> Tuple:
        years_map = by_major[key]
        latest = max(years_map) if years_map else 0
        return (-len(years_map), -(years_map.get(latest) or 0), major_meta[key]["label"])

    ranked_keys = sorted(by_major.keys(), key=major_rank)
    shown_keys = ranked_keys[: max(1, max_majors)] if ranked_keys else []
    hidden_count = max(0, len(ranked_keys) - len(shown_keys))

    root_series = []
    for key in shown_keys:
        years_map = by_major[key]
        data = []
        for y in all_years:
            if y not in years_map:
                continue
            point = {"x": str(y), "y": round(years_map[y], 2)}
            # Cho phép click năm → xem toàn bộ ngành năm đó
            if len(by_major) > 1:
                point["drilldown"] = f"y{y}"
            data.append(point)
        if data:
            root_series.append({
                "name": major_meta[key]["label"][:48],
                "data": data,
            })

    # Drilldown: mỗi năm → điểm theo ngành (line qua các ngành)
    drilldown_series = []
    drill_limit = max(max_majors * 2, 40)
    for y in all_years:
        points = []
        for key in ranked_keys:
            if y not in by_major[key]:
                continue
            points.append({
                "x": major_meta[key]["label"][:40],
                "y": round(by_major[key][y], 2),
            })
        # Sắp xếp theo điểm giảm dần cho dễ đọc
        points.sort(key=lambda p: p["y"], reverse=True)
        if len(points) > drill_limit:
            points = points[:drill_limit]
        if points:
            drilldown_series.append({
                "id": f"y{y}",
                "name": f"Năm {y} — điểm theo ngành",
                "data": points,
            })

    school_name = ""
    for rec in filtered:
        if rec.get("ten_truong"):
            school_name = get_school_display_name(code or "", rec["ten_truong"])
            break
    if not school_name and code:
        school_name = get_school_display_name(code)

    # Map mã → tên đầy đủ (ưu tiên tên chuẩn từ config, rồi dữ liệu đã cào)
    names_by_code: Dict[str, str] = {}
    for rec in admissions:
        sc = (rec.get("ma_truong") or "").upper()
        if not sc or sc in names_by_code:
            continue
        names_by_code[sc] = get_school_display_name(sc, rec.get("ten_truong") or "")
    school_options = [
        {
            "id": s,
            "name": names_by_code.get(s) or get_school_display_name(s) or s,
            "label": f"{names_by_code.get(s) or get_school_display_name(s) or s} - [{s}]",
        }
        for s in all_schools
    ]

    return {
        "schools": all_schools,
        "school_options": school_options,
        "methods": method_options,
        "school": code or "",
        "school_name": school_name,
        "method": method_id,
        "method_label": METHOD_COLUMN_LABELS.get(method_id, method_id),
        "years": all_years,
        "major_count": len(ranked_keys),
        "shown_major_count": len(shown_keys),
        "hidden_major_count": hidden_count,
        "total_records": len(filtered),
        # ApexCharts Line + Drilldown
        "apex": {
            "series": root_series,
            "drilldown": {
                "enabled": bool(drilldown_series),
                "breadcrumb": {
                    "show": True,
                    "rootLabel": "Theo ngành · các năm",
                },
                "series": drilldown_series,
            },
        },
    }


def major_record_id(rec: Dict[str, Any]) -> str:
    """Khóa ổn định cho ngành: ưu tiên mã ngành, không có thì theo tên."""
    ma = (rec.get("ma_nganh") or "").strip()
    if ma:
        return ma
    ten = (rec.get("ten_nganh") or "").strip()
    return f"name:{ten}" if ten else ""


def list_school_majors(
    admissions: List[Dict[str, Any]],
    school_code: Optional[str] = None,
    school_codes: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """Danh sách ngành tuyển sinh (unique) theo 1/nhiều trường từ dữ liệu đã cào."""
    scope = _parse_school_scope(school_code, school_codes)
    code_set = set(scope) if scope else None
    seen: Dict[str, Dict[str, str]] = {}
    for rec in admissions:
        sc = (rec.get("ma_truong") or "").upper()
        if code_set is not None and sc not in code_set:
            continue
        mid = major_record_id(rec)
        if not mid or mid in seen:
            if mid and mid in seen:
                ten = (rec.get("ten_nganh") or "").strip()
                if ten and len(ten) > len(seen[mid].get("ten_nganh") or ""):
                    seen[mid]["ten_nganh"] = ten
                    ma = seen[mid].get("ma_nganh") or ""
                    seen[mid]["label"] = f"{ten}" + (f" ({ma})" if ma else "")
            continue
        ma = (rec.get("ma_nganh") or "").strip()
        ten = (rec.get("ten_nganh") or "").strip() or "(Không rõ ngành)"
        seen[mid] = {
            "id": mid,
            "ma_nganh": ma,
            "ten_nganh": ten,
            "label": f"{ten}" + (f" ({ma})" if ma else ""),
        }
    return sorted(seen.values(), key=lambda x: (x["ten_nganh"].lower(), x["ma_nganh"]))


# Phương thức có thể khớp lẫn nhau khi lọc điểm chuẩn
_METHOD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "HSA": ("HSA", "DGNL"),
    "V-ACT": ("V-ACT", "DGNL"),
    "TSA": ("TSA", "DGNL"),
    "DGNL": ("DGNL", "HSA", "V-ACT", "TSA"),
    # Nhiều trường ghi "Chứng chỉ quốc tế" (CCQT) dù điểm là thang SAT/ACT/IELTS
    "SAT": ("SAT", "CCQT"),
    "ACT": ("ACT", "CCQT"),
    "IELTS": ("IELTS", "CCQT"),
    "CCQT": ("CCQT", "SAT", "ACT", "IELTS"),
}


def _normalize_method_id(method: str) -> str:
    raw = (method or "THPT").strip()
    mid = method_column_key(raw) or raw.upper().replace("VACT", "V-ACT")
    return mid or "OTHER"


def _method_match_ids(method_id: str) -> set:
    """Tập mã phương thức được coi là khớp khi đánh giá."""
    mid = _normalize_method_id(method_id)
    return set(_METHOD_ALIASES.get(mid, (mid,)))


def _score_compatible(method_id: str, rec_method: str, score: float, rec_score: float) -> bool:
    """
    Khi khớp qua alias (vd SAT ↔ CCQT), chỉ lấy bản ghi cùng thang điểm hợp lý.
    Khớp đúng mã phương thức thì luôn chấp nhận.
    """
    mid = _normalize_method_id(method_id)
    rid = _normalize_method_id(rec_method)
    if mid == rid:
        return True
    # SAT thang ~400–1600
    if mid == "SAT" and rid == "CCQT":
        return 400 <= score <= 1600 and 400 <= rec_score <= 1600
    if mid == "ACT" and rid == "CCQT":
        return 1 <= score <= 36 and 1 <= rec_score <= 36
    if mid == "IELTS" and rid == "CCQT":
        return 0 < score <= 9.5 and 0 < rec_score <= 9.5
    if mid == "CCQT" and rid in ("SAT", "ACT", "IELTS"):
        return True
    # HSA/V-ACT/TSA ↔ DGNL: không lọc thang (đã gần nhau hơn)
    if mid in ("HSA", "V-ACT", "TSA", "DGNL") and rid in ("HSA", "V-ACT", "TSA", "DGNL"):
        return True
    return mid == rid


def list_admission_methods(
    admissions: List[Dict[str, Any]],
    school_code: Optional[str] = None,
    school_codes: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Danh sách phương thức xét tuyển có trong dữ liệu đã cào
    (theo 1 trường, nhiều trường, hoặc toàn bộ).
    """
    codes = _parse_school_scope(school_code, school_codes)
    code_set = set(codes) if codes else None
    counts: Dict[str, int] = defaultdict(int)
    labels: Dict[str, str] = {}
    schools_by_method: Dict[str, set] = defaultdict(set)

    for rec in admissions:
        scode = (rec.get("ma_truong") or "").upper()
        if code_set is not None and scode not in code_set:
            continue
        if _score_of(rec) is None:
            continue
        mid = method_column_key(rec.get("phuong_thuc") or "") or "OTHER"
        counts[mid] += 1
        schools_by_method[mid].add(scode)
        if mid not in labels:
            labels[mid] = METHOD_COLUMN_LABELS.get(
                mid, (rec.get("phuong_thuc") or mid)
            )

    order = list(METHOD_COLUMN_LABELS.keys())
    methods = sorted(
        counts.keys(),
        key=lambda m: (order.index(m) if m in order else 99, m),
    )
    return [
        {
            "id": m,
            "label": labels.get(m, METHOD_COLUMN_LABELS.get(m, m)),
            "count": counts[m],
            "schools": sorted(schools_by_method[m]),
            "school_count": len(schools_by_method[m]),
        }
        for m in methods
    ]


def _parse_school_scope(
    school_code: Optional[str] = None,
    school_codes: Optional[List[str]] = None,
) -> Optional[List[str]]:
    """None = tất cả trường; list = phạm vi chọn."""
    out: List[str] = []
    seen = set()
    for raw in (school_codes or []):
        c = str(raw or "").strip().upper()
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    if not out and school_code:
        c = str(school_code).strip().upper()
        if c:
            out.append(c)
    return out or None


def analyze_chance(
    admissions: List[Dict[str, Any]],
    score: float,
    method: str = "THPT",
    school_code: Optional[str] = None,
    school_codes: Optional[List[str]] = None,
    major_keyword: Optional[str] = None,
    majors: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    So sánh điểm thí sinh với điểm chuẩn theo đúng phương thức đã chọn.

    - Chỉ lấy bản ghi khớp phương thức (có alias hợp lý: SAT↔CCQT, HSA↔DGNL…).
    - Nếu đánh giá nhiều trường: trường không có phương thức đó → báo
      «không tuyển sinh với phương thức …» (mark ✗ trong bảng tổng kết).
    """
    method_id = _normalize_method_id(method)
    method_label = METHOD_COLUMN_LABELS.get(method_id, method_id)
    match_ids = _method_match_ids(method_id)
    scope = _parse_school_scope(school_code, school_codes)
    kw = (major_keyword or "").strip().lower() or None
    major_ids = {
        str(m).strip()
        for m in (majors or [])
        if m is not None and str(m).strip()
    }

    if scope:
        scope_schools = list(scope)
    else:
        scope_schools = sorted({
            (r.get("ma_truong") or "").upper()
            for r in admissions
            if r.get("ma_truong")
        })
    scope_set = set(scope_schools)

    school_names: Dict[str, str] = {}
    for rec in admissions:
        sc = (rec.get("ma_truong") or "").upper()
        if sc and sc not in school_names and rec.get("ten_truong"):
            school_names[sc] = rec.get("ten_truong") or sc

    def _pass_major_filter(rec: Dict[str, Any]) -> bool:
        if major_ids:
            rid = major_record_id(rec)
            ma = (rec.get("ma_nganh") or "").strip()
            return rid in major_ids or ma in major_ids
        if kw:
            hay = f"{rec.get('ten_nganh') or ''} {rec.get('ma_nganh') or ''}".lower()
            return kw in hay
        return True

    matched: List[Dict[str, Any]] = []
    schools_with_method: set = set()

    for rec in admissions:
        sc = (rec.get("ma_truong") or "").upper()
        if sc not in scope_set:
            continue
        if not _pass_major_filter(rec):
            continue
        sc_score = _score_of(rec)
        if sc_score is None:
            continue
        mid = method_column_key(rec.get("phuong_thuc") or "") or "OTHER"
        if mid not in match_ids:
            continue
        if not _score_compatible(method_id, mid, score, sc_score):
            continue
        schools_with_method.add(sc)
        matched.append({**rec, "_score": sc_score, "_method": mid})

    schools_no_method = []
    for sc in scope_schools:
        if sc in schools_with_method:
            continue
        has_any = any((r.get("ma_truong") or "").upper() == sc for r in admissions)
        if not has_any:
            schools_no_method.append({
                "ma_truong": sc,
                "ten_truong": school_names.get(sc, sc),
                "reason": "no_data",
                "mark": "✗",
                "message": f"{sc} — chưa có dữ liệu điểm chuẩn đã cào để đánh giá.",
            })
        else:
            schools_no_method.append({
                "ma_truong": sc,
                "ten_truong": school_names.get(sc, sc),
                "reason": "no_method",
                "mark": "✗",
                "message": (
                    f"{sc} — không tuyển sinh / không có điểm chuẩn "
                    f"theo phương thức «{method_label}» trong dữ liệu đã cào."
                ),
            })

    no_method_map = {s["ma_truong"]: s for s in schools_no_method}

    if not matched:
        school_summary = [
            {
                "ma_truong": s["ma_truong"],
                "ten_truong": s["ten_truong"],
                "ok": False,
                "mark": "✗",
                "reason": s.get("reason") or "no_method",
                "message": s.get("message") or "",
                "n_nganh": 0,
                "n_pass": 0,
                "avg_cutoff": None,
                "best_gap": None,
            }
            for s in schools_no_method
        ]
        msg = (
            f"Không trường nào trong {len(scope_schools)} trường đã chọn "
            f"có điểm chuẩn phương thức «{method_label}»."
            if len(scope_schools) > 1
            else (schools_no_method[0]["message"] if schools_no_method else
                  f"Không tìm thấy điểm chuẩn phương thức «{method_label}».")
        )
        return {
            "ok": False,
            "level": "unknown",
            "level_label": "Không có dữ liệu / không tuyển sinh",
            "message": msg,
            "stats": {
                "method": method_id,
                "method_label": method_label,
                "school_codes": scope_schools,
                "majors": sorted(major_ids),
                "score": score,
                "schools_evaluated": [],
                "schools_no_method": schools_no_method,
            },
            "samples": [],
            "school_summary": school_summary,
            "schools_no_method": schools_no_method,
            "schools_evaluated": [],
        }

    years = sorted({int(r["nam"]) for r in matched if r.get("nam") is not None})
    latest = years[-1]
    latest_rows = [r for r in matched if int(r["nam"]) == latest]
    pass_rows = [r for r in latest_rows if score >= r["_score"]]
    near_rows = [
        r for r in latest_rows
        if 0 <= (r["_score"] - score) <= max(0.5, r["_score"] * 0.03)
    ]

    gaps = [score - r["_score"] for r in latest_rows]
    avg_cutoff = sum(r["_score"] for r in latest_rows) / len(latest_rows)
    avg_gap = sum(gaps) / len(gaps)
    pass_rate = len(pass_rows) / len(latest_rows)

    trend_by_year = []
    for y in years:
        rows_y = [r for r in matched if int(r["nam"]) == y]
        if not rows_y:
            continue
        avg_y = sum(r["_score"] for r in rows_y) / len(rows_y)
        trend_by_year.append({"nam": y, "avg": round(avg_y, 2), "n": len(rows_y)})

    trend_delta = None
    if len(trend_by_year) >= 2:
        trend_delta = round(trend_by_year[-1]["avg"] - trend_by_year[0]["avg"], 2)

    if pass_rate >= 0.7 and avg_gap >= 0.5:
        level, level_label = "high", "Cao"
    elif pass_rate >= 0.4 or avg_gap >= 0:
        level, level_label = "medium", "Trung bình"
    elif pass_rate >= 0.15 or avg_gap >= -1.0:
        level, level_label = "low", "Thấp"
    else:
        level, level_label = "very_low", "Rất thấp"

    ranked = sorted(latest_rows, key=lambda r: abs(score - r["_score"]))[:20]
    samples = [
        {
            "ma_truong": r.get("ma_truong"),
            "ten_truong": r.get("ten_truong"),
            "ma_nganh": r.get("ma_nganh"),
            "ten_nganh": r.get("ten_nganh"),
            "nam": r.get("nam"),
            "phuong_thuc": r.get("phuong_thuc"),
            "method_id": r.get("_method"),
            "diem_chuan": r["_score"],
            "chenh_lech": round(score - r["_score"], 2),
            "co_hoi": "đạt ngưỡng" if score >= r["_score"] else "dưới ngưỡng",
            "mark": "✓",
        }
        for r in ranked
    ]

    # Bảng tổng kết theo từng trường (có ✗ nếu không có PTXT)
    school_summary = []
    for sc in scope_schools:
        name = school_names.get(sc, sc)
        if sc in no_method_map:
            info = no_method_map[sc]
            school_summary.append({
                "ma_truong": sc,
                "ten_truong": name,
                "ok": False,
                "mark": "✗",
                "reason": info.get("reason") or "no_method",
                "message": info.get("message") or "",
                "n_nganh": 0,
                "n_pass": 0,
                "avg_cutoff": None,
                "best_gap": None,
            })
            continue
        rows_sc = [r for r in latest_rows if (r.get("ma_truong") or "").upper() == sc]
        if not rows_sc:
            # Có method ở năm khác nhưng không có năm mới nhất
            rows_sc = [r for r in matched if (r.get("ma_truong") or "").upper() == sc]
        n_pass = sum(1 for r in rows_sc if score >= r["_score"])
        avg_sc = sum(r["_score"] for r in rows_sc) / len(rows_sc) if rows_sc else None
        best_gap = min((score - r["_score"] for r in rows_sc), key=abs) if rows_sc else None
        school_summary.append({
            "ma_truong": sc,
            "ten_truong": name,
            "ok": True,
            "mark": "✓",
            "reason": "ok",
            "message": "",
            "n_nganh": len(rows_sc),
            "n_pass": n_pass,
            "avg_cutoff": round(avg_sc, 2) if avg_sc is not None else None,
            "best_gap": round(best_gap, 2) if best_gap is not None else None,
        })

    schools_evaluated = sorted(schools_with_method)
    stats = {
        "method": method_id,
        "method_label": method_label,
        "school_codes": scope_schools,
        "major_keyword": kw,
        "majors": sorted(major_ids),
        "majors_count": len(major_ids),
        "score": score,
        "latest_year": latest,
        "years": years,
        "n_latest": len(latest_rows),
        "n_pass": len(pass_rows),
        "n_near": len(near_rows),
        "pass_rate": round(pass_rate * 100, 1),
        "avg_cutoff": round(avg_cutoff, 2),
        "avg_gap": round(avg_gap, 2),
        "min_cutoff": round(min(r["_score"] for r in latest_rows), 2),
        "max_cutoff": round(max(r["_score"] for r in latest_rows), 2),
        "trend_by_year": trend_by_year,
        "trend_delta": trend_delta,
        "schools_evaluated": schools_evaluated,
        "schools_no_method": schools_no_method,
    }

    msg = (
        f"Với điểm {score} («{method_label}»), năm {latest}: "
        f"đạt ngưỡng {len(pass_rows)}/{len(latest_rows)} ngành "
        f"({stats['pass_rate']}%) trên {len(schools_evaluated)}/{len(scope_schools)} trường. "
        f"TB điểm chuẩn {stats['avg_cutoff']}, chênh lệch TB {stats['avg_gap']:+}."
    )
    if major_ids:
        msg += f" Đã lọc {len(major_ids)} ngành đã chọn."
    if schools_no_method:
        msg += f" {len(schools_no_method)} trường không có phương thức «{method_label}» (xem cột ✗)."

    return {
        "ok": True,
        "level": level,
        "level_label": level_label,
        "message": msg,
        "stats": stats,
        "samples": samples,
        "school_summary": school_summary,
        "schools_no_method": schools_no_method,
        "schools_evaluated": schools_evaluated,
    }


def _call_pollinations(prompt: str, system: str) -> Optional[str]:
    """AI miễn phí không cần API key."""
    try:
        r = requests.post(
            "https://text.pollinations.ai/openai",
            json={
                "model": "openai",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.4,
            },
            timeout=45,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        choices = data.get("choices") or []
        if choices:
            return (choices[0].get("message") or {}).get("content") or None
        # Một số bản trả plain text
        if isinstance(data, str):
            return data
        text = r.text.strip()
        return text or None
    except Exception:
        return None


def _call_groq(prompt: str, system: str) -> Optional[str]:
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        return None
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.4,
                "max_tokens": 900,
            },
            timeout=40,
        )
        if r.status_code != 200:
            return None
        return (
            (((r.json().get("choices") or [{}])[0]).get("message") or {}).get("content")
        )
    except Exception:
        return None


def _call_gemini(prompt: str, system: str) -> Optional[str]:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return None
    try:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.0-flash:generateContent?key={key}"
        )
        r = requests.post(
            url,
            json={
                "contents": [{"parts": [{"text": f"{system}\n\n{prompt}"}]}],
                "generationConfig": {"temperature": 0.4, "maxOutputTokens": 900},
            },
            timeout=40,
        )
        if r.status_code != 200:
            return None
        parts = (
            (((r.json().get("candidates") or [{}])[0]).get("content") or {}).get("parts")
            or []
        )
        if parts:
            return parts[0].get("text")
        return None
    except Exception:
        return None


def _fallback_advice(analysis: Dict[str, Any]) -> str:
    """Lời khuyên tiếng Việt khi không gọi được AI ngoài."""
    if not analysis.get("ok"):
        return analysis.get("message") or "Chưa đủ dữ liệu để đánh giá."

    st = analysis["stats"]
    level = analysis["level"]
    tips = {
        "high": (
            "Điểm của bạn đang ở vùng an toàn với nhiều ngành trong bộ lọc. "
            "Vẫn nên xếp nguyện vọng chiến lược: ngành yêu thích trước, "
            "sau đó vài ngành gần điểm chuẩn làm phương án dự phòng."
        ),
        "medium": (
            "Cơ hội ở mức khả quan với một phần ngành. "
            "Nên cân bằng giữa ngành hot (điểm cao) và ngành phù hợp điểm số; "
            "theo dõi biến động điểm chuẩn các năm gần đây."
        ),
        "low": (
            "Điểm đang sát hoặc hơi dưới trung bình điểm chuẩn. "
            "Ưu tiên ngành có điểm chuẩn thấp hơn điểm của bạn, "
            "cân nhắc phương thức khác (học bạ / ĐGNL / ĐGTD) nếu đủ điều kiện."
        ),
        "very_low": (
            "Điểm hiện tại thấp hơn đáng kể so với phần lớn ngành đã lọc. "
            "Nên mở rộng danh sách trường/ngành, cải thiện điểm (thi lại / chứng chỉ), "
            "hoặc chuyển sang phương thức xét tuyển khác."
        ),
    }
    trend = ""
    if st.get("trend_delta") is not None:
        d = st["trend_delta"]
        if d > 0.3:
            trend = f" Điểm chuẩn có xu hướng tăng khoảng +{d} qua các năm đã cào."
        elif d < -0.3:
            trend = f" Điểm chuẩn có xu hướng giảm khoảng {d} qua các năm đã cào."
        else:
            trend = " Điểm chuẩn khá ổn định qua các năm đã cào."

    samples = analysis.get("samples") or []
    sample_lines = ""
    if samples:
        bits = []
        for s in samples[:5]:
            bits.append(
                f"- {s.get('ten_nganh')} ({s.get('ma_truong')}): "
                f"chuẩn {s.get('diem_chuan')} · lệch {s.get('chenh_lech'):+}"
            )
        sample_lines = "\nMột số ngành gần điểm của bạn:\n" + "\n".join(bits)

    return (
        f"**Mức đánh giá: {analysis['level_label']}**\n\n"
        f"{analysis['message']}{trend}\n\n"
        f"{tips.get(level, '')}"
        f"{sample_lines}\n\n"
        "_Lưu ý: đây là ước lượng dựa trên điểm chuẩn công bố đã cào, "
        "không phải cam kết trúng tuyển. Cần đối chiếu đề án chính thức của trường._"
    )


def enrich_with_ai(analysis: Dict[str, Any], use_ai: bool = True) -> Dict[str, Any]:
    """Gắn lời giải thích AI (hoặc fallback) vào kết quả phân tích."""
    out = dict(analysis)
    out["ai_provider"] = "local"
    out["ai_advice"] = _fallback_advice(analysis)

    if not use_ai or not analysis.get("ok"):
        return out

    system = (
        "Bạn là cố vấn hướng nghiệp tuyển sinh đại học Việt Nam. "
        "Trả lời tiếng Việt, ngắn gọn (180–280 từ), rõ ràng, không hứa hẹn chắc chắn. "
        "Dựa trên số liệu thống kê được cung cấp: nêu mức cơ hội, gợi ý chiến lược "
        "nguyện vọng, và lưu ý cần đối chiếu đề án chính thức."
    )
    prompt = (
        "Hãy đánh giá cơ hội trúng tuyển từ số liệu sau (JSON):\n"
        + json.dumps(
            {
                "level": analysis.get("level"),
                "level_label": analysis.get("level_label"),
                "message": analysis.get("message"),
                "stats": analysis.get("stats"),
                "samples": (analysis.get("samples") or [])[:8],
            },
            ensure_ascii=False,
        )
    )

    providers: List[Tuple[str, Any]] = [
        ("pollinations", _call_pollinations),
        ("groq", _call_groq),
        ("gemini", _call_gemini),
    ]
    for name, fn in providers:
        text = fn(prompt, system)
        if text and len(text.strip()) > 40:
            # Làm sạch markdown code fence nếu có
            cleaned = re.sub(r"^```\w*\n?", "", text.strip())
            cleaned = re.sub(r"\n?```$", "", cleaned)
            out["ai_provider"] = name
            out["ai_advice"] = cleaned.strip()
            return out

    return out
