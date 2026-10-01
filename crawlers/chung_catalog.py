"""Bổ sung phương thức và điểm chuẩn từ data/tuyen-sinh/chung.json."""

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from core import dataset_store
from core.normalizer import strip_accents

CHUNG_PATH = os.path.join("data", "tuyen-sinh", "chung.json")
SOURCE = "data/tuyen-sinh/chung.json"
YEAR = 2026


def _fold(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", strip_accents(text or "")).strip()


def official_codes_for_method(name: str) -> List[str]:
    """Gắn tên phương thức tự do với mã chuẩn dùng trên trang Phương thức tuyển sinh."""
    text = _fold(name)
    codes: List[str] = []

    def add(code: str) -> None:
        if code not in codes:
            codes.append(code)

    if "tuyen thang" in text or "uu tien xet" in text:
        add("301")
    if "nuoc ngoai" in text:
        add("411")
    if "sat" in text or re.search(r"\bact\b", text):
        add("415")
    if "hoc sinh gioi" in text:
        add("500")
    if "nang khieu" in text:
        add("405")
    has_hoc_ba = "hoc ba" in text or "hoc tap" in text or "qua trinh hoc" in text
    has_cert = "chung chi" in text or "ngoai ngu" in text
    has_exam = "thi thpt" in text or "tot nghiep thpt" in text or "thi tot nghiep" in text
    if has_hoc_ba and has_cert:
        add("410")
    elif has_hoc_ba and "phong van" in text:
        add("414")
    elif has_hoc_ba:
        add("200")
    if has_exam and has_cert:
        add("409")
    elif has_exam:
        add("100")
    if any(hint in text for hint in ("dgnl", "danh gia nang luc", "danh gia tu duy", "thi rieng")):
        add("402")
    return codes


def _parse_cutoff(raw: str) -> Tuple[Any, Optional[float], str]:
    text = str(raw or "").strip()
    folded = _fold(text)
    scale = 100.0 if "thang 100" in folded else 30.0
    span = re.search(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", text)
    if span:
        return f"{span.group(1)} - {span.group(2)}", scale, text
    number = re.search(r"(\d+(?:\.\d+)?)", text)
    if not number:
        return None, scale, text
    return float(number.group(1)), scale, text


def _score_method(methods: List[str], note: str) -> str:
    folded = _fold(note)
    if "quy doi" in folded:
        return "Quy đổi"
    if "thang 100" in folded:
        return "Thang 100"
    for name in methods:
        if "100" in official_codes_for_method(name):
            return name
    return methods[0] if methods else "100"


def _occupied(rows: List[Dict[str, Any]], school: str, year: int) -> Set[str]:
    found: Set[str] = set()
    for row in rows:
        if str(row.get("ma_truong") or "").strip().upper() != school:
            continue
        if int(row.get("nam") or 0) != year:
            continue
        for field in ("ma_xet_tuyen", "ma_nganh"):
            value = str(row.get(field) or "").strip().upper()
            if value:
                found.add(value)
    return found


def _forms(methods: List[str], combos: List[str]) -> List[Dict[str, Any]]:
    forms = []
    for name in methods:
        detail = {"to_hop_xet_tuyen": list(combos)} if combos else {}
        forms.append({
            "id": name,
            "ten": name,
            "ap_dung": True,
            "mo_ta": f"Tổ hợp: {', '.join(combos)}" if combos else "",
            "chi_tiet": detail,
        })
    return forms


def import_chung(root: str) -> Dict[str, int]:
    path = os.path.join(root, CHUNG_PATH)
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    schools = payload.get("danh_sach_truong") or []
    methods_payload = dataset_store.load_phuong_thuc(root) or {}
    scores_payload = dataset_store.load_admissions(root) or {}
    method_rows = list(methods_payload.get("records") or [])
    score_rows = list(scores_payload.get("admissions") or [])
    groups = dict(methods_payload.get("nhom_phuong_thuc") or {})
    added_methods = 0
    added_scores = 0
    collected_at = datetime.now().isoformat(timespec="seconds")

    for school in schools:
        code = str(school.get("ma_truong") or "").strip().upper()
        name = str(school.get("ten_truong") or "").strip()
        method_names = [str(item).strip() for item in school.get("phuong_thuc_tuyen_sinh") or [] if str(item).strip()]
        if not code or not name:
            continue
        taken_methods = _occupied(method_rows, code, YEAR)
        taken_scores = _occupied(score_rows, code, YEAR)
        school_groups = dict(groups.get(code) or {})
        year_groups = dict(school_groups.get(str(YEAR)) or {})
        for method_name in method_names:
            codes = official_codes_for_method(method_name)
            if codes and method_name not in year_groups:
                year_groups[method_name] = codes
        if year_groups:
            school_groups[str(YEAR)] = year_groups
            groups[code] = school_groups
        for major in school.get("danh_sach_nganh") or []:
            major_code = str(major.get("ma_nganh") or "").strip().upper()
            major_name = str(major.get("ten_nganh") or "").strip()
            combos = [str(item).strip() for item in major.get("to_hop_xet_tuyen") or [] if str(item).strip()]
            if not major_code or not major_name:
                continue
            if major_code not in taken_methods:
                method_rows.append({
                    "ma_truong": code,
                    "ten_truong": name,
                    "nam": YEAR,
                    "ma_xet_tuyen": major_code,
                    "ma_nganh": major_code,
                    "ten_nganh": major_name,
                    "ten_chuong_trinh": "",
                    "chi_tieu": major.get("chi_tieu"),
                    "hinh_thuc": _forms(method_names, combos),
                    "ghi_chu": f"Bổ sung từ {SOURCE}",
                    "nguon": SOURCE,
                })
                taken_methods.add(major_code)
                added_methods += 1
            if major_code in taken_scores:
                continue
            score, scale, original = _parse_cutoff(str(major.get("diem_chuuan_2026") or ""))
            if score is None:
                continue
            score_rows.append({
                "ma_truong": code,
                "ten_truong": name,
                "nam": YEAR,
                "ma_xet_tuyen": major_code,
                "ma_nganh": major_code,
                "ten_nganh": major_name,
                "to_hop": ", ".join(combos),
                "chi_tieu": major.get("chi_tieu"),
                "diem_chuan": score,
                "diem_chuan_ptxt": None,
                "thang_diem": scale,
                "phuong_thuc": _score_method(method_names, original),
                "ghi_chu": original,
                "nguon": SOURCE,
                "thu_thap_luc": collected_at,
            })
            taken_scores.add(major_code)
            added_scores += 1

    method_codes = sorted({
        *[str(item).upper() for item in (methods_payload.get("codes") or [])],
        *[str(row.get("ma_truong") or "").upper() for row in method_rows],
    })
    score_codes = sorted({
        *[str(item).upper() for item in (scores_payload.get("codes") or [])],
        *[str(row.get("ma_truong") or "").upper() for row in score_rows],
    })
    methods_payload["records"] = method_rows
    methods_payload["codes"] = method_codes
    methods_payload["nhom_phuong_thuc"] = groups
    years = set(methods_payload.get("years") or [])
    years.add(YEAR)
    methods_payload["years"] = sorted(int(item) for item in years)
    scores_payload["admissions"] = score_rows
    scores_payload["codes"] = score_codes
    score_years = set(scores_payload.get("years") or [])
    score_years.add(YEAR)
    scores_payload["years"] = sorted(int(item) for item in score_years)
    dataset_store.save_phuong_thuc(root, methods_payload)
    dataset_store.save_admissions(root, scores_payload)
    return {"phuong_thuc": added_methods, "diem": added_scores}
