# -*- coding: utf-8 -*-
"""
Module: core.conversion_calculator
Tính điểm quy đổi tương đương giữa các phương thức dựa trên bảng khoảng điểm
(VD: THPT 26–28 ↔ HSA 98–112 ↔ SAT 1500–1580).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple

from core.normalizer import strip_accents, clean_text


def parse_number(text: Any) -> Optional[float]:
    """Parse số dạng 77,90 / 77.90 / 1.580 / 1580."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    s = s.replace(" ", "")
    # khoảng -> bỏ
    if re.search(r"[-–—]", s) and not re.fullmatch(r"-?\d+[.,]?\d*", s):
        return None
    # bỏ chữ
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s or s in {".", ",", "-", "-.", ",."}:
        return None
    # Nếu có cả . và , : giả định . là nghìn, , là thập phân (kiểu VN) hoặc ngược lại
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # 77,90 hoặc 1,580
        parts = s.split(",")
        if len(parts[-1]) <= 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_range(text: Any) -> Optional[Tuple[float, float]]:
    """
    Parse khoảng điểm: '28–30', '77,90–100', '85-87', '102 trở lên' (→ 102, 102*1.2 tạm).
    """
    if text is None:
        return None
    s = clean_text(str(text))
    if not s:
        return None
    s_norm = s.replace("–", "-").replace("—", "-").replace("−", "-")
    s_low = s_norm.lower()

    # "102 trở lên" / "từ 8.0"
    m_up = re.search(r"(?:từ\s+)?(\d+[.,]?\d*)\s*(?:trở lên|\+)", s_low)
    if m_up and "-" not in s_norm:
        lo = parse_number(m_up.group(1))
        if lo is not None:
            return (lo, lo)  # điểm ngưỡng; interpolate sẽ giữ nguyên

    m = re.search(
        r"(\d+[.,]?\d*)\s*-\s*(\d+[.,]?\d*)",
        s_norm,
    )
    if not m:
        # single number
        val = parse_number(s_norm)
        if val is None:
            return None
        return (val, val)

    lo = parse_number(m.group(1))
    hi = parse_number(m.group(2))
    if lo is None or hi is None:
        return None
    if lo > hi:
        lo, hi = hi, lo
    return (lo, hi)


def classify_method_column(header: str) -> str:
    """Chuẩn hóa tên cột bảng → mã phương thức ngắn."""
    h = strip_accents(header or "")
    if "v-act" in h or "vact" in h:
        return "V-ACT"
    if re.search(r"\bhsa\b", h):
        return "HSA"
    # Cột gộp ĐGNL+ĐGTD (vd NTH) → DGNL, không gắn nhầm TSA
    if "dgnl" in h and "dgtd" in h and not re.search(r"\btsa\b", h):
        return "DGNL"
    if re.search(r"\btsa\b", h) or ("dgtd" in h and "dgnl" not in h) or "tu duy" in h:
        return "TSA"
    if re.search(r"\bsat\b", h):
        return "SAT"
    if re.search(r"\bact\b", h) and "v-act" not in h and "vact" not in h:
        return "ACT"
    if "ielts" in h:
        return "IELTS"
    if "toefl" in h:
        return "TOEFL"
    if "toeic" in h:
        return "TOEIC"
    if "hoc ba" in h or "ket qua hoc tap" in h:
        if "ket hop" in h or "ccnn" in h:
            return "KET_HOP"
        return "HOC_BA"
    if "ket hop" in h or "ccnn" in h:
        return "KET_HOP"
    if "dgnl" in h or "nang luc" in h:
        return "DGNL"
    if "xttn" in h or "tai nang" in h:
        if "1.2" in h:
            return "XTTN_1.2"
        if "1.3" in h:
            return "XTTN_1.3"
        return "XTTN"
    if "thpt" in h or "tot nghiep" in h or "tn thpt" in h:
        return "THPT"
    # fallback: rút gọn header
    return clean_text(header)[:40] or "OTHER"


METHOD_LABELS = {
    "THPT": "Điểm thi THPT",
    "HSA": "Điểm ĐGNL HSA",
    "TSA": "Điểm ĐGTD TSA",
    "V-ACT": "Điểm ĐGNL V-ACT",
    "SAT": "SAT",
    "ACT": "ACT",
    "DGNL": "Điểm ĐGNL/ĐGTD (quy đổi)",
    "HOC_BA": "Điểm học bạ",
    "KET_HOP": "Điểm xét tuyển kết hợp",
    "IELTS": "IELTS",
    "TOEFL": "TOEFL",
    "TOEIC": "TOEIC",
    "XTTN": "Xét tuyển tài năng",
    "XTTN_1.2": "XTTN Diện 1.2",
    "XTTN_1.3": "XTTN Diện 1.3",
}

# Khi bảng quy đổi không tách HSA/V-ACT/TSA, dùng cột DGNL chung
METHOD_FALLBACKS = {
    "HSA": ["DGNL"],
    "V-ACT": ["DGNL"],
    "TSA": ["DGNL", "TSA"],
    "DGNL": ["HSA", "V-ACT", "TSA"],
}


@dataclass
class ConvertedMethodResult:
    phuong_thuc: str
    ten_hien_thi: str
    cot_goc: str
    khoang: str
    diem_uoc_tinh: Optional[float]
    min_val: Optional[float]
    max_val: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConversionGroupResult:
    """Một nhóm quy đổi theo tổ hợp / bảng (VD: A00 · Ngành có tổ hợp gốc A00)."""
    tieu_de: str
    block: str = ""
    applied: str = ""
    khoang_khop: str = ""
    diff: float = 0.0
    equivalents: List[ConvertedMethodResult] = field(default_factory=list)
    ok: bool = True
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["equivalents"] = [
            e if isinstance(e, dict) else e.to_dict() for e in self.equivalents
        ]
        return d


@dataclass
class ConversionCalcResult:
    ok: bool
    ma_truong: str = ""
    ten_truong: str = ""
    tieu_de_bang: str = ""
    phuong_thuc_nguon: str = ""
    diem_nhap: Optional[float] = None
    khoang_khop: str = ""
    message: str = ""
    equivalents: List[ConvertedMethodResult] = field(default_factory=list)
    groups: List[ConversionGroupResult] = field(default_factory=list)
    all_methods: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["equivalents"] = [e if isinstance(e, dict) else e.to_dict() for e in self.equivalents]
        d["groups"] = [g if isinstance(g, dict) else g.to_dict() for g in self.groups]
        return d


def _score_in_range(
    score: float,
    lo: float,
    hi: float,
    eps: float = 1e-9,
    half_open: bool = True,
) -> bool:
    """
    Mặc định nửa mở [lo, hi) giống công cụ tuyensinh247.
    Khoảng suy biến (lo≈hi) hoặc half_open=False → đóng [lo, hi].
    """
    if abs(hi - lo) < eps:
        return abs(score - lo) <= eps
    if half_open:
        return (lo - eps) <= score < hi
    return (lo - eps) <= score <= (hi + eps)


def _interpolate(score: float, src: Tuple[float, float], dst: Tuple[float, float]) -> Optional[float]:
    s_lo, s_hi = src
    d_lo, d_hi = dst
    if abs(s_hi - s_lo) < 1e-12:
        return round((d_lo + d_hi) / 2, 2)
    ratio = (score - s_lo) / (s_hi - s_lo)
    ratio = max(0.0, min(1.0, ratio))
    val = d_lo + ratio * (d_hi - d_lo)
    return round(val, 2)


def list_methods_from_rows(rows: List[Dict[str, Any]], school_code: str = "") -> List[Dict[str, str]]:
    """Danh sách phương thức (cột) có trong bảng của trường."""
    methods: Dict[str, str] = {}
    for r in rows:
        if school_code and r.get("ma_truong") != school_code:
            continue
        for col in (r.get("cot_gia_tri") or {}).keys():
            m = classify_method_column(col)
            if m not in methods:
                methods[m] = col
    out = []
    for m, col in methods.items():
        out.append({
            "id": m,
            "label": METHOD_LABELS.get(m, m),
            "column": col,
            "source": "quy-doi",
        })
    # Ưu tiên thứ tự quen thuộc
    order = ["THPT", "HOC_BA", "HSA", "V-ACT", "TSA", "KET_HOP", "DGNL", "SAT", "ACT", "IELTS"]
    out.sort(key=lambda x: order.index(x["id"]) if x["id"] in order else 99)
    return out


def merge_method_lists(*lists: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Gộp danh sách phương thức (ưu tiên bản ghi xuất hiện trước — thường là diem-chuan)."""
    merged: Dict[str, Dict[str, str]] = {}
    for lst in lists:
        for item in lst or []:
            mid = item.get("id") or ""
            if not mid:
                continue
            if mid not in merged:
                merged[mid] = dict(item)
            else:
                # Giữ label từ nguồn đầu; bổ sung column nếu thiếu
                if not merged[mid].get("column") and item.get("column"):
                    merged[mid]["column"] = item["column"]
    order = ["THPT", "HOC_BA", "HSA", "V-ACT", "TSA", "KET_HOP", "DGNL", "SAT", "ACT", "IELTS", "XTTN", "XTTN_1.2", "XTTN_1.3"]
    out = list(merged.values())
    out.sort(key=lambda x: order.index(x["id"]) if x["id"] in order else 99)
    return out


def _method_match_ids(source_method: str) -> List[str]:
    """Các mã cột có thể khớp với phương thức nguồn (kèm fallback)."""
    src = (source_method or "").upper().replace("VACT", "V-ACT")
    ids = [src]
    for fb in METHOD_FALLBACKS.get(src, []):
        if fb not in ids:
            ids.append(fb)
    return ids


def _parse_group_meta(title: str) -> Tuple[str, str]:
    """Tách 'Công cụ quy đổi BPV — A00 · Ngành có tổ hợp gốc A00' → (block, applied)."""
    t = clean_text(title or "")
    t = re.sub(r"^Công cụ quy đổi BPV\s*[—\-–]?\s*", "", t, flags=re.I).strip()
    if "·" in t:
        left, right = t.split("·", 1)
        return clean_text(left), clean_text(right)
    m = re.match(r"^(A00|A01|B00|D01|D04|D07|DD2|K01)\b(.*)$", t, flags=re.I)
    if m:
            return m.group(1).upper(), clean_text(m.group(2).lstrip("·—- "))
    return "", t


def _equivalents_from_row(
    row: Dict[str, Any],
    source_method: str,
    score: float,
    src_rng: Tuple[float, float],
    thpt_diff: float = 0.0,
) -> List[ConvertedMethodResult]:
    order = [
        "THPT", "HSA", "TSA", "V-ACT", "SAT", "ACT", "DGNL",
        "HOC_BA", "KET_HOP", "XTTN", "XTTN_1.2", "XTTN_1.3",
    ]
    equivalents: List[ConvertedMethodResult] = []
    for col, val in (row.get("cot_gia_tri") or {}).items():
        method = classify_method_column(col)
        dst_rng = parse_range(val)
        est = None
        min_v = max_v = None
        khoang = str(val)
        if dst_rng:
            lo, hi = dst_rng
            if method == "THPT" and abs(thpt_diff) > 1e-9:
                lo, hi = lo + thpt_diff, hi + thpt_diff
                khoang = f"{lo:g}-{hi:g}"
            est = _interpolate(score, src_rng, (lo, hi))
            min_v, max_v = lo, hi
        equivalents.append(
            ConvertedMethodResult(
                phuong_thuc=method,
                ten_hien_thi=METHOD_LABELS.get(method, method),
                cot_goc=col,
                khoang=khoang,
                diem_uoc_tinh=est,
                min_val=min_v,
                max_val=max_v,
            )
        )
    equivalents.sort(
        key=lambda e: (
            0 if e.phuong_thuc == source_method else 1,
            order.index(e.phuong_thuc) if e.phuong_thuc in order else 99,
        )
    )
    return equivalents


def _best_candidate_in_rows(
    rows_subset: List[Dict[str, Any]],
    match_ids: set,
    score: float,
) -> Optional[Tuple[Dict, str, Tuple[float, float]]]:
    def mid(rng: Tuple[float, float]) -> float:
        return (rng[0] + rng[1]) / 2

    candidates: List[Tuple[Dict, str, Tuple[float, float]]] = []

    def collect(half_open: bool) -> None:
        for r in rows_subset:
            for col, val in (r.get("cot_gia_tri") or {}).items():
                if classify_method_column(col) not in match_ids:
                    continue
                rng = parse_range(val)
                if not rng:
                    continue
                if _score_in_range(score, rng[0], rng[1], half_open=half_open):
                    candidates.append((r, col, rng))

    collect(True)
    if not candidates:
        collect(False)
    if not candidates:
        return None
    candidates.sort(key=lambda x: (abs(mid(x[2]) - score), -x[2][0]))
    return candidates[0]


def calculate_equivalence(
    rows: List[Dict[str, Any]],
    school_code: str,
    source_method: str,
    score: float,
    table_title: Optional[str] = None,
) -> ConversionCalcResult:
    """
    Tìm khoảng chứa điểm nguồn và trả về điểm/khoảng tương đương các phương thức khác.
    Nếu có nhiều bảng BPV theo tổ hợp (A00/D01…), trả về `groups` cho từng tổ hợp.
    """
    school_rows = [r for r in rows if r.get("ma_truong") == school_code]
    if table_title:
        school_rows = [
            r for r in school_rows
            if (r.get("tieu_de_bang") or "") == table_title
        ]
    if not school_rows:
        school_rows = [r for r in rows if r.get("ma_truong") == school_code]
    if not school_rows:
        return ConversionCalcResult(
            ok=False,
            ma_truong=school_code,
            message=f"Chưa có bảng quy đổi HTML cho {school_code}. "
                    f"Một số trường chỉ đăng ảnh — xem tab Ảnh bảng.",
        )

    source_method = (source_method or "").upper().replace("VACT", "V-ACT")
    match_ids = set(_method_match_ids(source_method))
    all_methods = [m["id"] for m in list_methods_from_rows(school_rows)]

    bpv_rows = [r for r in school_rows if "BPV" in (r.get("tieu_de_bang") or "").upper()]
    work_rows = bpv_rows or school_rows

    # Nhóm theo tiêu đề bảng (mỗi tổ hợp / ngành một nhóm)
    grouped: Dict[str, List[Dict]] = {}
    for r in work_rows:
        title = r.get("tieu_de_bang") or "Bảng quy đổi"
        grouped.setdefault(title, []).append(r)

    # Giữ thứ tự ổn định: A00 trước, rồi D01…
    def _group_sort_key(title: str) -> Tuple[int, str]:
        block, applied = _parse_group_meta(title)
        order_blocks = ["A00", "A01", "B00", "D01", "D04", "D07", "DD2", "K01"]
        bi = order_blocks.index(block) if block in order_blocks else 50
        ai = 0 if "A00" in applied else (1 if "D01" in applied else 9)
        return (bi, ai, title)

    groups: List[ConversionGroupResult] = []
    for title in sorted(grouped.keys(), key=_group_sort_key):
        subset = grouped[title]
        best = _best_candidate_in_rows(subset, match_ids, score)
        block, applied = _parse_group_meta(title)
        if not best:
            groups.append(
                ConversionGroupResult(
                    tieu_de=title,
                    block=block,
                    applied=applied,
                    ok=False,
                    message=f"Điểm {score} không nằm trong khoảng {source_method} của nhóm này.",
                )
            )
            continue

        best_row, best_col, best_rng = best
        # diff lưu trong stt dạng "4|diff=0.5"
        thpt_diff = 0.0
        stt = str(best_row.get("stt") or "")
        m_diff = re.search(r"diff\s*=\s*([-+]?\d+(?:[.,]\d+)?)", stt, flags=re.I)
        if m_diff:
            thpt_diff = parse_number(m_diff.group(1)) or 0.0

        eqs = _equivalents_from_row(
            best_row, source_method, score, best_rng, thpt_diff=thpt_diff
        )
        groups.append(
            ConversionGroupResult(
                tieu_de=title,
                block=block,
                applied=applied,
                khoang_khop=str(best_row.get("cot_gia_tri", {}).get(best_col, "")),
                diff=thpt_diff,
                equivalents=eqs,
                ok=True,
            )
        )

    ok_groups = [g for g in groups if g.ok]
    if not ok_groups:
        hints = []
        for r in work_rows:
            for col, val in (r.get("cot_gia_tri") or {}).items():
                if classify_method_column(col) in match_ids and parse_range(val):
                    hints.append(str(val))
        hint_txt = ", ".join(dict.fromkeys(hints)) if hints else "không rõ"
        return ConversionCalcResult(
            ok=False,
            ma_truong=school_code,
            ten_truong=school_rows[0].get("ten_truong", ""),
            phuong_thuc_nguon=source_method,
            diem_nhap=score,
            message=f"Điểm {score} không nằm trong các khoảng {source_method} của bảng "
                    f"({hint_txt}).",
            groups=groups,
            all_methods=all_methods,
        )

    primary = ok_groups[0]
    n_ok = len(ok_groups)
    msg = (
        f"Khớp {n_ok} nhóm tổ hợp/bảng — nội suy tuyến tính trong từng khoảng BPV."
        if n_ok > 1
        else "Khớp khoảng quy đổi BPV/bảng và ước tính điểm tương đương (nội suy tuyến tính trong khoảng)."
    )
    return ConversionCalcResult(
        ok=True,
        ma_truong=school_code,
        ten_truong=school_rows[0].get("ten_truong", ""),
        tieu_de_bang=primary.tieu_de,
        phuong_thuc_nguon=source_method,
        diem_nhap=score,
        khoang_khop=primary.khoang_khop,
        message=msg,
        equivalents=primary.equivalents,
        groups=groups,
        all_methods=all_methods,
    )


def calculate_certificate_conversion(
    conversions: List[Dict[str, Any]],
    school_code: str,
    certificate_score: float,
    certificate_type: str = "IELTS",
) -> ConversionCalcResult:
    """
    Quy đổi chứng chỉ (IELTS/TOEFL/SAT…) → điểm quy đổi từ bảng đề án
    (ScoreConversionRecord dạng hang_muc → diem_quy_doi).
    """
    cert = (certificate_type or "IELTS").upper()
    matched_rows = []
    for c in conversions:
        if c.get("ma_truong") != school_code:
            continue
        loai = strip_accents(c.get("loai_bang") or "")
        hang = str(c.get("hang_muc") or "")
        if cert.lower() not in loai and cert.lower() not in strip_accents(hang):
            # bảng wide: loai_bang = "IELTS (Academic)"
            if cert not in (c.get("loai_bang") or "").upper():
                continue
        rng = parse_range(hang)
        # single value hang_muc like "6.5"
        if rng is None:
            val = parse_number(hang)
            if val is not None:
                rng = (val, val)
        if rng is None:
            continue
        if _score_in_range(certificate_score, rng[0], rng[1]):
            matched_rows.append((c, rng))

    if not matched_rows:
        return ConversionCalcResult(
            ok=False,
            ma_truong=school_code,
            phuong_thuc_nguon=cert,
            diem_nhap=certificate_score,
            message=f"Không tìm thấy mức {cert}={certificate_score} trong bảng quy đổi chứng chỉ của {school_code}.",
        )

    matched_rows.sort(key=lambda x: abs((x[1][0] + x[1][1]) / 2 - certificate_score))
    best, rng = matched_rows[0]
    diem = parse_number(best.get("diem_quy_doi"))
    return ConversionCalcResult(
        ok=True,
        ma_truong=school_code,
        ten_truong=best.get("ten_truong", ""),
        tieu_de_bang=best.get("loai_bang", ""),
        phuong_thuc_nguon=cert,
        diem_nhap=certificate_score,
        khoang_khop=str(best.get("hang_muc") or ""),
        message="Khớp bảng quy đổi chứng chỉ (đề án tuyển sinh).",
        equivalents=[
            ConvertedMethodResult(
                phuong_thuc="DIEM_QUY_DOI",
                ten_hien_thi="Điểm quy đổi",
                cot_goc="diem_quy_doi",
                khoang=str(best.get("diem_quy_doi") or ""),
                diem_uoc_tinh=diem,
                min_val=diem,
                max_val=diem,
            )
        ],
    )
