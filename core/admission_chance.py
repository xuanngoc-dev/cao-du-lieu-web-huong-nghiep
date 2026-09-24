# -*- coding: utf-8 -*-
"""
Đánh giá cơ hội trúng tuyển dựa trên điểm chuẩn đã thu thập + AI miễn phí (tuỳ chọn).

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
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from core.aggregator import METHOD_COLUMN_LABELS, method_column_key
from core.bonus_policy import best_certificate_bonus, index_certificate_bonus_bands
from core.normalizer import get_school_display_name

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCHOOL_CACHE = _PROJECT_ROOT / "config" / "schools_all.json"


@lru_cache(maxsize=1)
def _school_link_map() -> Dict[str, Dict[str, str]]:
    """Map mã trường → website / đề án / slug từ cache danh mục."""
    try:
        with open(_SCHOOL_CACHE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    items = raw.get("schools") if isinstance(raw, dict) else []
    if not isinstance(items, list):
        items = []
    for info in items:
        if not isinstance(info, dict):
            continue
        key = str(info.get("code") or "").strip().upper()
        if not key:
            continue
        website = (info.get("website") or "").strip()
        gioi = (info.get("gioi_thieu_url") or "").strip()
        slug = (info.get("slug") or "").strip()
        if not gioi:
            gioi = website
        out[key] = {
            "website": website,
            "gioi_thieu_url": gioi,
            "slug": slug,
            "name": (info.get("name") or "").strip(),
        }
    return out


def _links_for_school(ma_truong: Any) -> Dict[str, str]:
    key = str(ma_truong or "").strip().upper()
    return dict(_school_link_map().get(key) or {})


def _md_school_link(ma_truong: Any, ten_truong: str = "") -> str:
    """Markdown link ưu tiên website nhà trường, fallback đề án."""
    code = str(ma_truong or "").strip().upper()
    links = _links_for_school(code)
    name = (ten_truong or links.get("name") or code).strip()
    url = links.get("website") or links.get("gioi_thieu_url") or ""
    label = f"{name} [{code}]" if code else name
    if url:
        return f"[{label}]({url})"
    return label


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

    # Map mã → tên đầy đủ (ưu tiên tên chuẩn từ config, rồi dữ liệu đã thu thập)
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
    """Danh sách ngành tuyển sinh (unique) theo 1/nhiều trường từ dữ liệu đã thu thập."""
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
    # Điểm chuẩn thường chỉ ghi "Xét tuyển tài năng"; quy đổi tách Diện 1.2 / 1.3
    "XTTN": ("XTTN", "XTTN_1.2", "XTTN_1.3"),
    "XTTN_1.2": ("XTTN_1.2", "XTTN"),
    "XTTN_1.3": ("XTTN_1.3", "XTTN"),
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
    # XTTN ↔ Diện 1.2 / 1.3
    if mid in ("XTTN", "XTTN_1.2", "XTTN_1.3") and rid in ("XTTN", "XTTN_1.2", "XTTN_1.3"):
        return True
    return mid == rid


def list_admission_methods(
    admissions: List[Dict[str, Any]],
    school_code: Optional[str] = None,
    school_codes: Optional[List[str]] = None,
    methods_by_school: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """
    Danh sách phương thức xét tuyển = hợp (union) các phương thức
    có trong điểm chuẩn (+ bảng quy đổi nếu có) của các trường thuộc phạm vi.

    Ví dụ: A có THPT, B có HSA+TSA → THPT, HSA, TSA.
    BKA có XTTN Diện 1.2 & 1.3 trên bảng quy đổi → hiện cả hai diện đó.
    """
    codes = _parse_school_scope(school_code, school_codes)
    code_set = set(codes) if codes else None
    counts: Dict[str, int] = defaultdict(int)
    labels: Dict[str, str] = {}
    schools_by_method: Dict[str, set] = defaultdict(set)
    sources: Dict[str, set] = defaultdict(set)

    for rec in admissions:
        scode = (rec.get("ma_truong") or "").upper()
        if code_set is not None and scode not in code_set:
            continue
        if _score_of(rec) is None:
            continue
        mid = method_column_key(rec.get("phuong_thuc") or "") or "OTHER"
        counts[mid] += 1
        schools_by_method[mid].add(scode)
        sources[mid].add("diem-chuan")
        if mid not in labels:
            labels[mid] = METHOD_COLUMN_LABELS.get(
                mid, (rec.get("phuong_thuc") or mid)
            )

    # Bổ sung phương thức chi tiết từ bảng quy đổi (vd. XTTN_1.2 / XTTN_1.3)
    mbs = methods_by_school or {}
    if mbs:
        school_iter = codes if codes else sorted(mbs.keys())
        for scode in school_iter:
            for item in mbs.get(str(scode).upper()) or mbs.get(str(scode)) or []:
                if not isinstance(item, dict):
                    continue
                raw_id = str(item.get("id") or item.get("column") or "").strip()
                if not raw_id:
                    continue
                mid = method_column_key(raw_id) or _normalize_method_id(raw_id)
                if not mid or mid == "OTHER":
                    mid = raw_id.upper().replace(" ", "_")
                schools_by_method[mid].add(str(scode).upper())
                sources[mid].add(str(item.get("source") or "quy-doi"))
                if mid not in counts:
                    counts[mid] = 0
                if mid not in labels:
                    labels[mid] = (
                        item.get("label")
                        or METHOD_COLUMN_LABELS.get(mid)
                        or raw_id
                    )

    # Nếu đã có diện cụ thể (1.2/1.3) thì bỏ mã XTTN chung cho cùng phạm vi
    # để dropdown không trùng "Xét tuyển tài năng" + "XTTN Diện …"
    has_xttn_dien = any(m.startswith("XTTN_") for m in counts)
    if has_xttn_dien and "XTTN" in counts:
        # Chỉ ẩn XTTN chung khi mọi trường có XTTN đều đã có ít nhất một diện
        schools_generic = set(schools_by_method.get("XTTN") or ())
        schools_dien = set()
        for m, scs in schools_by_method.items():
            if m.startswith("XTTN_"):
                schools_dien |= set(scs)
        if schools_generic and schools_generic.issubset(schools_dien):
            counts.pop("XTTN", None)
            schools_by_method.pop("XTTN", None)
            labels.pop("XTTN", None)
            sources.pop("XTTN", None)

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
            "sources": sorted(sources.get(m) or []),
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


def _normalize_score_profiles(
    score: Optional[float] = None,
    method: str = "THPT",
    scores: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Chuẩn hoá danh sách điểm theo phương thức.
    Mỗi phần tử: {method_id, method_label, score, match_ids}.
    """
    raw_items: List[Dict[str, Any]] = []
    if scores:
        for item in scores:
            if not isinstance(item, dict):
                continue
            try:
                sc = float(str(item.get("score")).replace(",", "."))
            except (TypeError, ValueError):
                continue
            mid = _normalize_method_id(str(item.get("method") or method or "THPT"))
            raw_items.append({"method": mid, "score": sc})
    elif score is not None:
        raw_items.append({
            "method": _normalize_method_id(method),
            "score": float(score),
        })

    # Gộp trùng method → giữ điểm sau cùng
    by_method: Dict[str, float] = {}
    for it in raw_items:
        by_method[it["method"]] = it["score"]

    profiles: List[Dict[str, Any]] = []
    for mid, sc in by_method.items():
        profiles.append({
            "method_id": mid,
            "method_label": METHOD_COLUMN_LABELS.get(mid, mid),
            "score": sc,
            "match_ids": _method_match_ids(mid),
        })
    return profiles


def _cert_score(item: Dict[str, Any]) -> Optional[float]:
    try:
        return float(str(item.get("score")).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _canon_cert_label(item: Dict[str, Any]) -> str:
    return str(item.get("type") or item.get("certificate") or "").strip()


def _bonus_note(hit: Optional[Dict[str, Any]]) -> str:
    if not hit or not hit.get("bonus"):
        return ""
    score = hit.get("certificate_score")
    score_txt = f"{score:g}" if isinstance(score, float) else str(score or "")
    muc = hit.get("muc") or hit.get("diem") or ""
    note = f"{hit.get('certificate') or ''} {score_txt} → +{float(hit['bonus']):g}"
    if muc:
        note += f" ({muc})"
    return note.strip()


def _pick_profile_for_record(
    profiles: List[Dict[str, Any]],
    rec_mid: str,
    rec_score: float,
) -> Optional[Dict[str, Any]]:
    """Chọn điểm thí sinh khớp bản ghi (ưu tiên đúng mã PTXT, rồi alias)."""
    exact: List[Dict[str, Any]] = []
    alias: List[Dict[str, Any]] = []
    for p in profiles:
        if rec_mid not in p["match_ids"]:
            continue
        if not _score_compatible(p["method_id"], rec_mid, p["score"], rec_score):
            continue
        if p["method_id"] == rec_mid:
            exact.append(p)
        else:
            alias.append(p)
    if exact:
        return exact[0]
    if alias:
        return alias[0]
    return None


def analyze_chance(
    admissions: List[Dict[str, Any]],
    score: Optional[float] = None,
    method: str = "THPT",
    school_code: Optional[str] = None,
    school_codes: Optional[List[str]] = None,
    major_keyword: Optional[str] = None,
    majors: Optional[List[str]] = None,
    scores: Optional[List[Dict[str, Any]]] = None,
    certificates: Optional[List[Dict[str, Any]]] = None,
    conversions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    So sánh điểm thí sinh với điểm chuẩn theo một hoặc nhiều phương thức.

    Ví dụ nhiều điểm: TSA 80 + SAT 1500 → mỗi ngành được so với điểm
    đúng thang phương thức tương ứng.
    """
    profiles = _normalize_score_profiles(score=score, method=method, scores=scores)
    if not profiles:
        return {
            "ok": False,
            "level": "unknown",
            "level_label": "Thiếu điểm",
            "message": "Chưa nhập điểm / phương thức hợp lệ để đánh giá.",
            "stats": {},
            "samples": [],
            "school_summary": [],
            "schools_no_method": [],
            "schools_evaluated": [],
        }

    multi = len(profiles) > 1
    method_id = profiles[0]["method_id"]
    method_labels = [f"{p['method_label']} {p['score']}" for p in profiles]
    method_label = " · ".join(method_labels)
    all_match_ids = set()
    for p in profiles:
        all_match_ids |= set(p["match_ids"])

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
    cert_items = [
        item for item in (certificates or [])
        if isinstance(item, dict) and item.get("type") and item.get("score") is not None
    ]
    bonus_index = index_certificate_bonus_bands(conversions or []) if cert_items else {}

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
        prof = _pick_profile_for_record(profiles, mid, sc_score)
        if not prof:
            continue
        base_score = float(prof["score"])
        bonus_hit = best_certificate_bonus(
            bonus_index, sc, cert_items, prof["method_id"],
        ) if cert_items else None
        bonus = float(bonus_hit["bonus"]) if bonus_hit else 0.0
        user_score = round(base_score + bonus, 2)
        schools_with_method.add(sc)
        matched.append({
            **rec,
            "_score": sc_score,
            "_method": mid,
            "_base_score": base_score,
            "_bonus": bonus,
            "_bonus_hit": bonus_hit,
            "_user_score": user_score,
            "_user_method": prof["method_id"],
            "_user_method_label": prof["method_label"],
            "_gap": round(user_score - sc_score, 2),
            "_rel_gap": (user_score - sc_score) / max(abs(sc_score), 1e-6),
        })

    methods_text = " / ".join(p["method_label"] for p in profiles)
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
                "message": f"{sc} — chưa có dữ liệu điểm chuẩn đã tổng hợp được để đánh giá.",
            })
        else:
            schools_no_method.append({
                "ma_truong": sc,
                "ten_truong": school_names.get(sc, sc),
                "reason": "no_method",
                "mark": "✗",
                "message": (
                    f"{sc} — không tuyển sinh / không có điểm chuẩn "
                    f"theo phương thức «{methods_text}» trong dữ liệu đã tổng hợp được."
                ),
            })

    no_method_map = {s["ma_truong"]: s for s in schools_no_method}
    score_profiles_out = [
        {
            "method": p["method_id"],
            "method_label": p["method_label"],
            "score": p["score"],
        }
        for p in profiles
    ]

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
                "website": _links_for_school(s["ma_truong"]).get("website") or "",
                "gioi_thieu_url": _links_for_school(s["ma_truong"]).get("gioi_thieu_url") or "",
            }
            for s in schools_no_method
        ]
        msg = (
            f"Không trường nào trong {len(scope_schools)} trường đã chọn "
            f"có điểm chuẩn phương thức «{methods_text}»."
            if len(scope_schools) > 1
            else (schools_no_method[0]["message"] if schools_no_method else
                  f"Không tìm thấy điểm chuẩn phương thức «{methods_text}».")
        )
        return {
            "ok": False,
            "level": "unknown",
            "level_label": "Không có dữ liệu / không tuyển sinh",
            "message": msg,
            "stats": {
                "method": method_id,
                "method_label": method_label,
                "score_profiles": score_profiles_out,
                "multi": multi,
                "school_codes": scope_schools,
                "majors": sorted(major_ids),
                "score": profiles[0]["score"],
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
    pass_rows = [r for r in latest_rows if r["_user_score"] >= r["_score"]]
    near_rows = [
        r for r in latest_rows
        if 0 <= (r["_score"] - r["_user_score"]) <= max(0.5, r["_score"] * 0.03)
    ]

    gaps = [r["_gap"] for r in latest_rows]
    rel_gaps = [r["_rel_gap"] for r in latest_rows]
    avg_rel_gap = sum(rel_gaps) / len(rel_gaps)
    pass_rate = len(pass_rows) / len(latest_rows)

    # Thống kê tuyệt đối chỉ meaningful khi 1 thang điểm
    avg_cutoff = None
    avg_gap = None
    min_cutoff = None
    max_cutoff = None
    if not multi:
        avg_cutoff = round(sum(r["_score"] for r in latest_rows) / len(latest_rows), 2)
        avg_gap = round(sum(gaps) / len(gaps), 2)
        min_cutoff = round(min(r["_score"] for r in latest_rows), 2)
        max_cutoff = round(max(r["_score"] for r in latest_rows), 2)

    by_method: Dict[str, Dict[str, Any]] = {}
    for r in latest_rows:
        um = r["_user_method"]
        bucket = by_method.setdefault(um, {
            "method": um,
            "method_label": r["_user_method_label"],
            "score": r["_user_score"],
            "n": 0,
            "n_pass": 0,
            "gaps": [],
            "cutoffs": [],
        })
        bucket["n"] += 1
        if r["_user_score"] >= r["_score"]:
            bucket["n_pass"] += 1
        bucket["gaps"].append(r["_gap"])
        bucket["cutoffs"].append(r["_score"])
    by_method_stats = []
    for um, b in by_method.items():
        by_method_stats.append({
            "method": b["method"],
            "method_label": b["method_label"],
            "score": b["score"],
            "n": b["n"],
            "n_pass": b["n_pass"],
            "pass_rate": round(100.0 * b["n_pass"] / b["n"], 1) if b["n"] else 0,
            "avg_cutoff": round(sum(b["cutoffs"]) / len(b["cutoffs"]), 2) if b["cutoffs"] else None,
            "avg_gap": round(sum(b["gaps"]) / len(b["gaps"]), 2) if b["gaps"] else None,
        })

    trend_by_year = []
    for y in years:
        rows_y = [r for r in matched if int(r["nam"]) == y]
        if not rows_y:
            continue
        # Khi multi: dùng tỷ lệ đạt theo năm thay vì TB điểm chuẩn lệch thang
        if multi:
            n_pass_y = sum(1 for r in rows_y if r["_user_score"] >= r["_score"])
            trend_by_year.append({
                "nam": y,
                "avg": round(100.0 * n_pass_y / len(rows_y), 1),
                "n": len(rows_y),
                "metric": "pass_rate",
            })
        else:
            avg_y = sum(r["_score"] for r in rows_y) / len(rows_y)
            trend_by_year.append({"nam": y, "avg": round(avg_y, 2), "n": len(rows_y)})

    trend_delta = None
    if len(trend_by_year) >= 2 and not multi:
        trend_delta = round(trend_by_year[-1]["avg"] - trend_by_year[0]["avg"], 2)

    if pass_rate >= 0.7 and avg_rel_gap >= 0.02:
        level, level_label = "high", "Cao"
    elif pass_rate >= 0.4 or avg_rel_gap >= 0:
        level, level_label = "medium", "Trung bình"
    elif pass_rate >= 0.15 or avg_rel_gap >= -0.05:
        level, level_label = "low", "Thấp"
    else:
        level, level_label = "very_low", "Rất thấp"

    ranked = sorted(latest_rows, key=lambda r: abs(r["_rel_gap"]))[:20]
    samples = []
    for r in ranked:
        code = r.get("ma_truong")
        links = _links_for_school(code)
        samples.append({
            "ma_truong": code,
            "ten_truong": r.get("ten_truong"),
            "ma_nganh": r.get("ma_nganh"),
            "ten_nganh": r.get("ten_nganh"),
            "nam": r.get("nam"),
            "phuong_thuc": r.get("phuong_thuc"),
            "method_id": r.get("_method"),
            "user_method": r.get("_user_method"),
            "user_method_label": r.get("_user_method_label"),
            "user_score": r.get("_user_score"),
            "base_score": r.get("_base_score"),
            "bonus": r.get("_bonus") or 0,
            "bonus_note": _bonus_note(r.get("_bonus_hit")),
            "diem_chuan": r["_score"],
            "chenh_lech": r["_gap"],
            "co_hoi": "đạt ngưỡng" if r["_user_score"] >= r["_score"] else "dưới ngưỡng",
            "mark": "✓",
            "website": links.get("website") or "",
            "gioi_thieu_url": links.get("gioi_thieu_url") or "",
            "slug": links.get("slug") or "",
        })

    school_summary = []
    for sc in scope_schools:
        name = school_names.get(sc, sc)
        links = _links_for_school(sc)
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
                "website": links.get("website") or "",
                "gioi_thieu_url": links.get("gioi_thieu_url") or "",
            })
            continue
        rows_sc = [r for r in latest_rows if (r.get("ma_truong") or "").upper() == sc]
        if not rows_sc:
            rows_sc = [r for r in matched if (r.get("ma_truong") or "").upper() == sc]
        n_pass = sum(1 for r in rows_sc if r["_user_score"] >= r["_score"])
        best_rel = min(rows_sc, key=lambda r: abs(r["_rel_gap"])) if rows_sc else None
        avg_sc = None
        if rows_sc and not multi:
            avg_sc = round(sum(r["_score"] for r in rows_sc) / len(rows_sc), 2)
        bonus_hit = next((r.get("_bonus_hit") for r in rows_sc if r.get("_bonus")), None)
        school_summary.append({
            "ma_truong": sc,
            "ten_truong": name,
            "ok": True,
            "mark": "✓",
            "reason": "ok",
            "message": _bonus_note(bonus_hit),
            "bonus": float(bonus_hit["bonus"]) if bonus_hit else 0,
            "base_score": rows_sc[0].get("_base_score") if rows_sc else None,
            "effective_score": rows_sc[0].get("_user_score") if rows_sc and bonus_hit else None,
            "n_nganh": len(rows_sc),
            "n_pass": n_pass,
            "avg_cutoff": avg_sc,
            "best_gap": best_rel["_gap"] if best_rel else None,
            "website": links.get("website") or "",
            "gioi_thieu_url": links.get("gioi_thieu_url") or "",
        })

    schools_evaluated = sorted(schools_with_method)
    stats = {
        "method": method_id,
        "method_label": method_label,
        "score_profiles": score_profiles_out,
        "by_method": by_method_stats,
        "multi": multi,
        "school_codes": scope_schools,
        "major_keyword": kw,
        "majors": sorted(major_ids),
        "majors_count": len(major_ids),
        "score": profiles[0]["score"],
        "latest_year": latest,
        "years": years,
        "n_latest": len(latest_rows),
        "n_pass": len(pass_rows),
        "n_near": len(near_rows),
        "pass_rate": round(pass_rate * 100, 1),
        "avg_cutoff": avg_cutoff,
        "avg_gap": avg_gap,
        "avg_rel_gap": round(avg_rel_gap * 100, 1),
        "min_cutoff": min_cutoff,
        "max_cutoff": max_cutoff,
        "trend_by_year": trend_by_year,
        "trend_delta": trend_delta,
        "schools_evaluated": schools_evaluated,
        "schools_no_method": schools_no_method,
    }

    msg = (
        f"Với {method_label}, năm {latest}: "
        f"đạt ngưỡng {len(pass_rows)}/{len(latest_rows)} ngành "
        f"({stats['pass_rate']}%) trên {len(schools_evaluated)}/{len(scope_schools)} trường."
    )
    if not multi and avg_cutoff is not None and avg_gap is not None:
        msg += f" TB điểm chuẩn {avg_cutoff}, chênh lệch TB {avg_gap:+}."
    if major_ids:
        msg += f" Đã lọc {len(major_ids)} ngành đã chọn."
    bonus_schools = []
    seen_bonus = set()
    for r in latest_rows:
        hit = r.get("_bonus_hit")
        code = (r.get("ma_truong") or "").upper()
        if not hit or not hit.get("bonus") or code in seen_bonus:
            continue
        seen_bonus.add(code)
        bonus_schools.append({
            "ma_truong": code,
            "ten_truong": r.get("ten_truong") or code,
            "bonus": hit["bonus"],
            "base_score": r.get("_base_score"),
            "effective_score": r.get("_user_score"),
            "note": _bonus_note(hit),
        })
    if bonus_schools:
        brief = "; ".join(
            f"{b['ma_truong']} +{b['bonus']:g} → {b['effective_score']:g}"
            for b in bonus_schools[:8]
        )
        extra = f" (+{len(bonus_schools) - 8} trường)" if len(bonus_schools) > 8 else ""
        msg += f" Đã cộng điểm chứng chỉ: {brief}{extra}."
    stats["certificates"] = [
        {"type": _canon_cert_label(item), "score": _cert_score(item)}
        for item in cert_items
        if _cert_score(item) is not None
    ]
    stats["bonus_by_school"] = bonus_schools
    if schools_no_method:
        msg += (
            f" {len(schools_no_method)} trường không có phương thức "
            f"«{methods_text}» (xem cột ✗)."
        )

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


def _study_tips(method_id: str, method_label: str) -> str:
    """Gợi ý học hỏi / trau dồi khi điểm chưa đạt."""
    mid = (method_id or "").lower()
    base = (
        "**Gợi ý học hỏi & trau dồi:**\n"
        "- Rà soát đề án / chỉ tiêu từng ngành trên trang trường (link bên dưới) để biết tổ hợp, điều kiện phụ.\n"
        "- Luyện đề sát cấu trúc kỳ thi bạn đang theo; ghi nhật ký lỗi sai và ôn lại phần yếu.\n"
        "- Cân nhắc chứng chỉ ngoại ngữ / tin học nếu ngành xét ưu tiên hoặc quy đổi điểm."
    )
    if "hoc_ba" in mid or "học bạ" in (method_label or "").lower():
        extra = (
            "- Cải thiện điểm trung bình các môn tổ hợp còn lại ở học kỳ tới; "
            "ưu tiên môn quyết định điểm xét học bạ."
        )
    elif "dgnl" in mid or "đánh giá năng lực" in (method_label or "").lower():
        extra = (
            "- Ôn kỹ tư duy logic, ngôn ngữ, khoa học tự nhiên theo đề ĐGNL; "
            "làm đề thi thử có chấm điểm và phân tích thời gian từng phần."
        )
    elif "dgtd" in mid or "tư duy" in (method_label or "").lower():
        extra = (
            "- Luyện đề ĐGTD theo từng phần (toán, đọc hiểu, khoa học/giải quyết vấn đề); "
            "đặt mục tiêu tăng dần từng tuần."
        )
    else:
        extra = (
            "- Ôn sâu tổ hợp môn thi tốt nghiệp / xét tuyển; "
            "ưu tiên môn đang lệch xa điểm chuẩn ngành mục tiêu."
        )
    return f"{base}\n{extra}"


def _fallback_advice(analysis: Dict[str, Any]) -> str:
    """Lời khuyên tiếng Việt khi không gọi được AI ngoài."""
    if not analysis.get("ok"):
        return analysis.get("message") or "Chưa đủ dữ liệu để đánh giá."

    st = analysis["stats"]
    level = analysis["level"]
    method_id = st.get("method") or ""
    method_label = st.get("method_label") or method_id

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
            "Ưu tiên vài ngành sát điểm nhất (xem mục tiêu bên dưới), "
            "đồng thời trau dồi kiến thức để kéo điểm lên trước kỳ xét."
        ),
        "very_low": (
            "Điểm hiện tại thấp hơn đáng kể so với phần lớn ngành đã lọc. "
            "Nên tập trung học hỏi / luyện đề để cải thiện điểm, "
            "và mở rộng danh sách trường–ngành dễ đạt hơn trong lúc chờ."
        ),
    }
    trend = ""
    if st.get("trend_delta") is not None:
        d = st["trend_delta"]
        if d > 0.3:
            trend = f" Điểm chuẩn có xu hướng tăng khoảng +{d} qua các năm đã tổng hợp được."
        elif d < -0.3:
            trend = f" Điểm chuẩn có xu hướng giảm khoảng {d} qua các năm đã tổng hợp được."
        else:
            trend = " Điểm chuẩn khá ổn định qua các năm đã tổng hợp được."

    samples = analysis.get("samples") or []
    pass_samples = [s for s in samples if (s.get("chenh_lech") or 0) >= 0]
    under_samples = [s for s in samples if (s.get("chenh_lech") or 0) < 0]

    focus_lines: List[str] = []
    # Ưu tiên ngành đã đạt ngưỡng; nếu không có thì lấy ngành sát nhất cần cố
    focus = (pass_samples[:4] if pass_samples else under_samples[:4])
    for s in focus:
        school = _md_school_link(s.get("ma_truong"), s.get("ten_truong") or "")
        major = s.get("ten_nganh") or s.get("ma_nganh") or "Ngành"
        gap = s.get("chenh_lech")
        gap_s = f"{gap:+}" if gap is not None else "?"
        status = "đạt ngưỡng" if (gap or 0) >= 0 else "còn thiếu điểm"
        focus_lines.append(
            f"- **{major}** tại {school}"
            + (f" ({s.get('user_method_label') or s.get('phuong_thuc') or ''})"
               if s.get("user_method_label") or s.get("phuong_thuc") else "")
            + f": chuẩn {s.get('diem_chuan')} · lệch {gap_s} ({status})"
        )

    focus_block = ""
    if focus_lines:
        title = (
            "Gợi ý tập trung (ngành/trường cụ thể)"
            if pass_samples
            else "Ngành/trường gần nhất cần cải thiện điểm"
        )
        focus_block = f"\n\n**{title}:**\n" + "\n".join(focus_lines)

    study_block = ""
    if level in ("low", "very_low") or (not pass_samples and under_samples):
        profiles = st.get("score_profiles") or []
        if len(profiles) > 1:
            study_block = "\n\n**Gợi ý học hỏi theo từng phương thức:**\n" + "\n".join(
                f"- **{p.get('method_label')} ({p.get('score')})**: ôn sát thang điểm / đề thi của phương thức này."
                for p in profiles
            )
        else:
            study_block = "\n\n" + _study_tips(method_id, method_label)

    return (
        f"**Mức đánh giá: {analysis['level_label']}**\n\n"
        f"{analysis['message']}{trend}\n\n"
        f"{tips.get(level, '')}"
        f"{focus_block}"
        f"{study_block}\n\n"
        "_Lưu ý: đây là ước lượng dựa trên điểm chuẩn công bố đã tổng hợp được, "
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
        "Trả lời tiếng Việt, ngắn gọn (200–320 từ), rõ ràng, không hứa hẹn chắc chắn.\n"
        "YÊU CẦU BẮT BUỘC:\n"
        "1) Focus vào 3–5 ngành/trường cụ thể từ danh sách samples (ghi rõ tên ngành + tên trường).\n"
        "2) Nếu sample có website hoặc gioi_thieu_url, chèn markdown link dạng [Tên trường](url).\n"
        "3) Nếu level là low/very_low hoặc hầu hết samples dưới ngưỡng: thêm mục "
        "«Học hỏi & trau dồi» với 3–4 gợi ý học tập cụ thể (ôn môn yếu, luyện đề, chứng chỉ…).\n"
        "4) Kết thúc bằng lưu ý đối chiếu đề án chính thức."
    )
    samples_for_ai = []
    for s in (analysis.get("samples") or [])[:8]:
        samples_for_ai.append({
            "ma_truong": s.get("ma_truong"),
            "ten_truong": s.get("ten_truong"),
            "ten_nganh": s.get("ten_nganh"),
            "phuong_thuc": s.get("phuong_thuc"),
            "user_method_label": s.get("user_method_label"),
            "user_score": s.get("user_score"),
            "diem_chuan": s.get("diem_chuan"),
            "chenh_lech": s.get("chenh_lech"),
            "co_hoi": s.get("co_hoi"),
            "website": s.get("website") or "",
            "gioi_thieu_url": s.get("gioi_thieu_url") or "",
        })
    prompt = (
        "Hãy đánh giá cơ hội trúng tuyển từ số liệu sau (JSON). "
        "Thí sinh có thể có nhiều điểm theo nhiều phương thức — so khớp từng ngành với đúng thang điểm. "
        "Ưu tiên gợi ý ngành đạt ngưỡng; nếu không có thì gợi ý ngành sát nhất kèm hướng học cải thiện điểm.\n"
        + json.dumps(
            {
                "level": analysis.get("level"),
                "level_label": analysis.get("level_label"),
                "message": analysis.get("message"),
                "stats": {
                    k: analysis.get("stats", {}).get(k)
                    for k in (
                        "method",
                        "method_label",
                        "score",
                        "score_profiles",
                        "by_method",
                        "multi",
                        "latest_year",
                        "pass_rate",
                        "avg_cutoff",
                        "avg_gap",
                        "n_pass",
                        "n_latest",
                        "trend_delta",
                        "certificates",
                        "bonus_by_school",
                    )
                },
                "samples": samples_for_ai,
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
