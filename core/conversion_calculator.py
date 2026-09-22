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
    all_methods: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["equivalents"] = [e if isinstance(e, dict) else e.to_dict() for e in self.equivalents]
        return d


def _score_in_range(score: float, lo: float, hi: float, eps: float = 1e-9) -> bool:
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
    order = ["THPT", "HOC_BA", "HSA", "V-ACT", "TSA", "KET_HOP", "DGNL", "SAT", "ACT", "IELTS", "XTTN"]
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


def calculate_equivalence(
    rows: List[Dict[str, Any]],
    school_code: str,
    source_method: str,
    score: float,
    table_title: Optional[str] = None,
) -> ConversionCalcResult:
    """
    Tìm khoảng chứa điểm nguồn và trả về điểm/khoảng tương đương các phương thức khác.
    rows: list dict từ MethodEquivalenceRow.to_dict()
    """
    school_rows = [r for r in rows if r.get("ma_truong") == school_code]
    if table_title:
        school_rows = [
            r for r in school_rows
            if (r.get("tieu_de_bang") or "") == table_title
        ]
    if not school_rows:
        # Nếu lọc theo tiêu đề làm rỗng, nới điều kiện
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
    # Map method -> list of (row, col_name, range)
    candidates: List[Tuple[Dict, str, Tuple[float, float]]] = []
    for r in school_rows:
        for col, val in (r.get("cot_gia_tri") or {}).items():
            if classify_method_column(col) not in match_ids:
                continue
            rng = parse_range(val)
            if not rng:
                continue
            if _score_in_range(score, rng[0], rng[1]):
                candidates.append((r, col, rng))

    if not candidates:
        # Gợi ý khoảng hợp lệ
        hints = []
        for r in school_rows:
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
            all_methods=[m["id"] for m in list_methods_from_rows(school_rows)],
        )

    # Chọn band có midpoint gần điểm nhập nhất
    def mid(rng: Tuple[float, float]) -> float:
        return (rng[0] + rng[1]) / 2

    candidates.sort(key=lambda x: abs(mid(x[2]) - score))
    best_row, best_col, best_rng = candidates[0]

    equivalents: List[ConvertedMethodResult] = []
    for col, val in (best_row.get("cot_gia_tri") or {}).items():
        method = classify_method_column(col)
        dst_rng = parse_range(val)
        est = None
        if dst_rng:
            est = _interpolate(score, best_rng, dst_rng)
        equivalents.append(
            ConvertedMethodResult(
                phuong_thuc=method,
                ten_hien_thi=METHOD_LABELS.get(method, method),
                cot_goc=col,
                khoang=str(val),
                diem_uoc_tinh=est,
                min_val=dst_rng[0] if dst_rng else None,
                max_val=dst_rng[1] if dst_rng else None,
            )
        )

    # Sắp xếp: nguồn trước, rồi theo order
    order = ["THPT", "HSA", "TSA", "V-ACT", "SAT", "ACT", "DGNL", "HOC_BA", "KET_HOP"]
    equivalents.sort(
        key=lambda e: (
            0 if e.phuong_thuc == source_method else 1,
            order.index(e.phuong_thuc) if e.phuong_thuc in order else 99,
        )
    )

    return ConversionCalcResult(
        ok=True,
        ma_truong=school_code,
        ten_truong=best_row.get("ten_truong", ""),
        tieu_de_bang=best_row.get("tieu_de_bang", ""),
        phuong_thuc_nguon=source_method,
        diem_nhap=score,
        khoang_khop=str(best_row.get("cot_gia_tri", {}).get(best_col, "")),
        message="Khớp khoảng quy đổi và ước tính điểm tương đương (nội suy tuyến tính trong khoảng).",
        equivalents=equivalents,
        all_methods=[m["id"] for m in list_methods_from_rows(school_rows)],
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
