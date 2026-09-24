# -*- coding: utf-8 -*-
"""
Tóm tắt quy chế cộng điểm theo loại chứng chỉ và phương thức xét tuyển.

Nguồn: bảng quy đổi chứng chỉ (điểm cộng / điểm thưởng / điểm khuyến khích)
và đoạn quy chế trong đề án đã thu thập.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from crawlers.dean_extractor import method_to_calc_id

_VACT_RE = re.compile(r"v-act|vact", re.I)
_PRIZE_RE = re.compile(
    r"giải|học sinh giỏi|olympic|khoa học kỹ thuật|đội tuyển",
    re.I,
)
_HEADER_HANG_RE = re.compile(
    r"^(điểm quy đổi|điểm khuyến khích|điểm thưởng|chương trình|điều kiện|"
    r"thang điểm|mức điểm|tiêu chí)\b",
    re.I,
)
_BONUS_WORD_RE = re.compile(
    r"điểm cộng|cộng điểm|điểm thưởng|điểm khuyến khích|mức điểm cộng|cộng thêm",
    re.I,
)
_AMOUNT_DIEM_RE = re.compile(
    r"(?:cộng\s*)?(\d+(?:[.,]\d+)?)\s*điểm\b",
    re.I,
)
_AMOUNT_DETAIL_RE = re.compile(
    r"(?:mức\s*)?điểm cộng\s*=\s*(\d+(?:[.,]\d+)?(?:\s*điểm)?)",
    re.I,
)
_BARE_NUMBER_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*$")
_NEGATIVE_RE = re.compile(r"không cộng|không áp dụng", re.I)
_POSITIVE_RE = re.compile(
    r"được cộng|mức cộng|điểm khuyến khích|cộng điểm khuyến khích|điểm thưởng",
    re.I,
)
_LINK_RE = re.compile(
    r"(cộng điểm|điểm cộng|điểm thưởng|điểm khuyến khích|mức cộng)"
    r".{0,120}"
    r"(ielts|toefl|toeic|vstep|cambridge|(?<![\w\-])sat(?![\w])|(?<![\w\-])act(?![\w])|"
    r"a[\s\-]?level|jlpt|(?<![\w])hsk(?![\w])|delf|dalf|aptis|trki|"
    r"ccnn|ccqt|chứng chỉ ngoại ngữ|chứng chỉ quốc tế|chứng chỉ tiếng anh)"
    r"|"
    r"(ielts|toefl|toeic|vstep|cambridge|(?<![\w\-])sat(?![\w])|(?<![\w\-])act(?![\w])|"
    r"a[\s\-]?level|jlpt|(?<![\w])hsk(?![\w])|delf|dalf|aptis|trki|"
    r"ccnn|ccqt|chứng chỉ ngoại ngữ|chứng chỉ quốc tế|chứng chỉ tiếng anh)"
    r".{0,120}"
    r"(cộng điểm|điểm cộng|điểm thưởng|điểm khuyến khích|được cộng|cộng thêm|mức cộng)",
    re.I,
)

_CERT_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("IELTS", re.compile(r"ielts", re.I)),
    ("TOEFL", re.compile(r"toefl", re.I)),
    ("TOEIC", re.compile(r"toeic", re.I)),
    ("VSTEP", re.compile(r"vstep", re.I)),
    ("Cambridge", re.compile(r"cambridge", re.I)),
    ("SAT", re.compile(r"(?<![\w\-])sat(?![\w])", re.I)),
    ("ACT", re.compile(r"(?<![\w\-])act(?![\w])", re.I)),
    ("A-Level", re.compile(r"a[\s\-]?level", re.I)),
    ("JLPT", re.compile(r"jlpt", re.I)),
    ("HSK", re.compile(r"(?<![\w])hsk(?![\w])", re.I)),
    ("DELF", re.compile(r"delf|dalf", re.I)),
    ("APTIS", re.compile(r"aptis", re.I)),
    ("TRKI", re.compile(r"trki", re.I)),
    ("CCNN", re.compile(r"ccnn|chứng chỉ ngoại ngữ|chứng chỉ tiếng anh", re.I)),
    ("CCQT", re.compile(r"ccqt|chứng chỉ quốc tế", re.I)),
]
_GENERIC_CERTS = {"CCNN", "CCQT"}
_MAX_LEVELS = 8
_MAX_POLICIES = 6
_MAX_SNIPPET = 260


def _mask_vact(text: str) -> str:
    """Giữ nguyên độ dài để ACT không khớp nhầm V-ACT."""
    def repl(match: re.Match) -> str:
        return "x" * len(match.group(0))
    return _VACT_RE.sub(repl, text or "")


def _certs_in(text: str) -> List[str]:
    masked = _mask_vact(text or "")
    found: List[str] = []
    for name, pat in _CERT_PATTERNS:
        if pat.search(masked):
            found.append(name)
    if any(name not in _GENERIC_CERTS for name in found):
        found = [name for name in found if name not in _GENERIC_CERTS]
    return found


def _parse_vn_number(raw: str) -> Optional[float]:
    s = (raw or "").strip().replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _bonus_amount(diem: str, detail: str) -> str:
    m = _AMOUNT_DIEM_RE.search(diem or "")
    if m:
        num = m.group(1)
        prefix = "Cộng " if re.search(r"cộng", m.group(0), re.I) else ""
        return f"{prefix}{num} điểm".strip()
    m = _AMOUNT_DETAIL_RE.search(detail or "")
    if m:
        val = re.sub(r"\s+", " ", m.group(1)).strip()
        if "điểm" not in val.lower():
            val = f"{val} điểm"
        return val
    m = _BARE_NUMBER_RE.match(diem or "")
    if m:
        num = _parse_vn_number(m.group(1))
        if num is not None and 0 < num <= 4:
            return f"{m.group(1).strip()} điểm"
    return ""


def _equivalents(detail: str) -> str:
    parts: List[str] = []
    for piece in (detail or "").split(";"):
        piece = piece.strip()
        if not piece:
            continue
        key = piece.split("=", 1)[0].strip().lower()
        if key in {"tt", "stt", "ghi chú", "ghi chu"}:
            continue
        if _AMOUNT_DETAIL_RE.search(piece):
            continue
        if not _certs_in(piece):
            continue
        parts.append(piece.replace("=", ": "))
    return "; ".join(parts)[:180]


def _level_label(hang: str, certs: Sequence[str]) -> str:
    label = re.sub(r"\s+", " ", (hang or "").strip())
    if not label:
        return ", ".join(certs) or "Chứng chỉ"
    if any(name.lower() in label.lower() for name in certs):
        return label
    specific = [name for name in certs if name not in _GENERIC_CERTS]
    if specific:
        return f"{specific[0]} {label}"
    return label


def _clean_snippet(text: str, start: int, end: int) -> str:
    a = max(0, start - 8)
    b = min(len(text), end + 140)
    raw = text[a:b]
    if a > 0:
        cut = raw.find(" ")
        if 0 <= cut <= 16:
            raw = raw[cut + 1:]
    raw = re.sub(r"\s+", " ", raw).strip(" -–—;,. ")
    if len(raw) > _MAX_SNIPPET:
        raw = raw[: _MAX_SNIPPET - 1].rstrip() + "…"
    return raw


def _norm_method(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def _remember_name(names: Dict[str, str], code: str, name: str) -> None:
    name = re.sub(r"\s+", " ", (name or "").strip())
    if code and name and len(name) > len(names.get(code, "")):
        names[code] = name


def _table_groups(conversions: Iterable[Dict[str, Any]], codes: Optional[set]) -> Dict[str, List[dict]]:
    grouped: Dict[Tuple[str, str, Tuple[str, ...]], dict] = {}
    order: List[Tuple[str, str, Tuple[str, ...]]] = []
    for row in conversions:
        code = str(row.get("ma_truong") or "").strip().upper()
        if not code or (codes is not None and code not in codes):
            continue
        hang = str(row.get("hang_muc") or "").strip()
        loai = str(row.get("loai_bang") or "").strip()
        diem = str(row.get("diem_quy_doi") or "").strip()
        detail = str(row.get("chi_tiet_hang") or "").strip()
        if _PRIZE_RE.search(hang) and not _certs_in(hang):
            continue
        if _HEADER_HANG_RE.search(hang) and not _certs_in(hang):
            continue
        certs = _certs_in(" ".join([loai, hang, detail]))
        if not certs:
            continue
        # Chứng chỉ phải nằm ở loại bảng hoặc hạng mục, tránh nhận nhầm cột phụ.
        if not _certs_in(" ".join([loai, hang])):
            continue
        amount = _bonus_amount(diem, detail)
        blob = " ".join([loai, hang, diem, detail])
        if not amount:
            if not _BONUS_WORD_RE.search(blob):
                continue
            amount = diem if diem and len(diem) <= 80 else "Có cộng điểm"
        method = str(row.get("phuong_thuc") or "").strip()
        key = (code, method, tuple(certs))
        bucket = grouped.get(key)
        if bucket is None:
            bucket = {
                "phuong_thuc": method,
                "certificates": list(certs),
                "levels": [],
                "_seen": set(),
            }
            grouped[key] = bucket
            order.append(key)
        label = _level_label(hang, certs)
        sig = (label.casefold(), amount.casefold())
        if sig in bucket["_seen"]:
            continue
        bucket["_seen"].add(sig)
        if len(bucket["levels"]) >= _MAX_LEVELS:
            bucket["more"] = bucket.get("more", 0) + 1
            continue
        equiv = _equivalents(detail)
        bucket["levels"].append({
            "muc": label,
            "diem": amount,
            "tuong_duong": equiv,
        })
    by_school: Dict[str, List[dict]] = {}
    for key in order:
        item = grouped[key]
        item.pop("_seen", None)
        by_school.setdefault(key[0], []).append(item)
    return by_school


def _policy_rows(regulations: Iterable[Dict[str, Any]], codes: Optional[set]) -> Dict[str, List[dict]]:
    by_school: Dict[str, List[dict]] = {}
    seen = set()
    for row in regulations:
        code = str(row.get("ma_truong") or "").strip().upper()
        if not code or (codes is not None and code not in codes):
            continue
        title = str(row.get("tieu_de") or "")
        body = str(row.get("noi_dung") or "")
        original = re.sub(r"\s+", " ", f"{title} {body}").strip()
        masked = _mask_vact(original)
        match = _LINK_RE.search(masked)
        if not match:
            continue
        span = masked[match.start():match.end()]
        window = masked[max(0, match.start() - 40): match.end() + 40]
        if _NEGATIVE_RE.search(window) and not _POSITIVE_RE.search(window):
            continue
        # Công thức vừa quy đổi chứng chỉ vừa có điểm thưởng riêng (giải, ưu tiên)
        # không phải quy chế cộng điểm cho chứng chỉ.
        if re.search(r"quy đổi", span, re.I) and not re.search(
            r"cộng điểm|điểm cộng|điểm khuyến khích|mức cộng|nếu có ccnn|nếu có chứng chỉ",
            span,
            re.I,
        ):
            continue
        certs = _certs_in(masked[max(0, match.start() - 80): match.end() + 80])
        if not certs:
            continue
        method = str(row.get("phuong_thuc") or "").strip()
        snippet = _clean_snippet(original, match.start(), match.end())
        sig = (code, _norm_method(method), tuple(certs), snippet[:70].casefold())
        if sig in seen:
            continue
        seen.add(sig)
        by_school.setdefault(code, []).append({
            "phuong_thuc": method,
            "certificates": certs,
            "snippet": snippet,
        })
    trimmed: Dict[str, List[dict]] = {}
    for code, rows in by_school.items():
        trimmed[code] = _trim_policies(rows)
    return trimmed


def _trim_policies(rows: List[dict]) -> List[dict]:
    """Bỏ dòng quy chế trùng ý, ưu tiên dòng đã gắn phương thức."""
    specific = [row for row in rows if row.get("phuong_thuc")]
    kept: List[dict] = []
    for row in rows:
        snippet = row.get("snippet") or ""
        if not row.get("phuong_thuc"):
            covered = False
            for other in specific:
                if not (set(row.get("certificates") or []) & set(other.get("certificates") or [])):
                    continue
                other_snip = other.get("snippet") or ""
                head = snippet[20:70].casefold()
                if head and head in other_snip.casefold():
                    covered = True
                    break
            if covered:
                continue
        duplicate = False
        for other in kept:
            if _norm_method(other.get("phuong_thuc") or "") != _norm_method(row.get("phuong_thuc") or ""):
                continue
            if set(other.get("certificates") or []) != set(row.get("certificates") or []):
                continue
            a = (other.get("snippet") or "")[:80].casefold()
            b = snippet[:80].casefold()
            if a and b and (a in b or b in a or a[:40] == b[:40]):
                duplicate = True
                break
        if duplicate:
            continue
        kept.append(row)
        if len(kept) >= _MAX_POLICIES:
            break
    return kept


def _merge_rows(table_groups: List[dict], policies: List[dict]) -> List[dict]:
    used = set()
    rows: List[dict] = []
    for group in table_groups:
        snippets: List[str] = []
        g_method = _norm_method(group.get("phuong_thuc") or "")
        g_certs = set(group.get("certificates") or [])
        for idx, policy in enumerate(policies):
            if _norm_method(policy.get("phuong_thuc") or "") != g_method:
                continue
            if not (g_certs & set(policy.get("certificates") or [])):
                continue
            used.add(idx)
            text = policy.get("snippet") or ""
            if text and text not in snippets and len(snippets) < 2:
                snippets.append(text)
        rows.append({
            "phuong_thuc": group.get("phuong_thuc") or "Chưa gắn phương thức",
            "certificates": group.get("certificates") or [],
            "levels": group.get("levels") or [],
            "more_levels": group.get("more") or 0,
            "snippets": snippets,
            "source": "bang" if not snippets else "bang_quy_che",
        })
    for idx, policy in enumerate(policies):
        if idx in used:
            continue
        rows.append({
            "phuong_thuc": policy.get("phuong_thuc") or "Chưa rõ phương thức",
            "certificates": policy.get("certificates") or [],
            "levels": [],
            "more_levels": 0,
            "snippets": [policy.get("snippet") or ""],
            "source": "quy_che",
        })

    def sort_key(item: dict) -> Tuple[int, str]:
        method = item.get("phuong_thuc") or ""
        unknown = method.startswith("Chưa")
        return (1 if unknown else 0, method.casefold())

    rows.sort(key=sort_key)
    return rows


def _names_for(
    codes: Sequence[str],
    conversions: Sequence[Dict[str, Any]],
    regulations: Sequence[Dict[str, Any]],
    admissions: Sequence[Dict[str, Any]],
) -> Dict[str, str]:
    names: Dict[str, str] = {}
    wanted = {c.upper() for c in codes} if codes else None
    for src in (conversions, regulations):
        for row in src:
            code = str(row.get("ma_truong") or "").strip().upper()
            if wanted is not None and code not in wanted:
                continue
            _remember_name(names, code, str(row.get("ten_truong") or ""))
    missing = [c for c in (codes or []) if c.upper() not in names]
    if missing and admissions:
        need = {c.upper() for c in missing}
        for row in admissions:
            code = str(row.get("ma_truong") or "").strip().upper()
            if code not in need:
                continue
            _remember_name(names, code, str(row.get("ten_truong") or ""))
            if names.get(code):
                need.discard(code)
            if not need:
                break
    return names


_TEST_RE = re.compile(
    r"đánh giá tư duy|đgt[dđ]|\btsa\b|\bhsa\b|v-act|vact|đánh giá năng lực",
    re.I,
)


def _band_kind(text: str, cert: str) -> str:
    head = (text or "").split("=", 1)[0]
    if re.search(r"toefl\s*ibt", head, re.I):
        return "TOEFL iBT"
    if re.search(r"toefl\s*itp", head, re.I):
        return "TOEFL ITP"
    return cert


def _kind_bands(loai: str, hang: str, detail: str) -> List[dict]:
    """Khoảng điểm riêng cho từng loại điều kiện (IELTS khác thang TOEFL)."""
    items: List[dict] = []
    covered = set()
    for piece in (detail or "").split(";"):
        piece_certs = _certs_in(piece)
        piece_bands = _score_bands(piece)
        if not piece_certs or not piece_bands:
            continue
        label = piece.replace("=", ": ").strip()
        for cert in piece_certs:
            kind = _band_kind(piece, cert)
            covered.add(cert)
            covered.add(kind)
            for lo, hi in piece_bands:
                items.append({
                    "kind": kind,
                    "lo": lo,
                    "hi": None if hi == float("inf") else hi,
                    "label": label[:120],
                })
    hang_bands = _score_bands(hang)
    named = _certs_in(hang)
    if named:
        hang_certs = named
    else:
        hang_certs = [cert for cert in _certs_in(loai) if cert not in covered]
    for cert in hang_certs:
        kind = _band_kind(hang, cert)
        for lo, hi in hang_bands:
            items.append({
                "kind": kind,
                "lo": lo,
                "hi": None if hi == float("inf") else hi,
                "label": hang or loai,
            })
    return items


def _condition_label(loai: str, hang: str, certs: Sequence[str]) -> str:
    parts = [part for part in (loai, hang) if str(part or "").strip()]
    label = " · ".join(parts)
    if label:
        return label
    return ", ".join(certs) or "Điều kiện cộng điểm"


def list_bonus_records(
    conversions: Sequence[Dict[str, Any]],
    regulations: Sequence[Dict[str, Any]],
    school_codes: Optional[Sequence[str]] = None,
    years: Optional[Sequence[int]] = None,
) -> List[dict]:
    """
    Dòng điểm cộng theo trường, năm, phương thức và điều kiện
    (chứng chỉ IELTS/TOEFL…, giải thưởng, bài đánh giá tư duy/năng lực).
    """
    codes = {str(c).strip().upper() for c in (school_codes or []) if str(c).strip()} or None
    year_set = {int(y) for y in (years or []) if str(y).strip()} or None
    records: List[dict] = []
    seen = set()

    def keep_year(raw: Any) -> bool:
        if not year_set:
            return True
        try:
            year = int(raw)
        except (TypeError, ValueError):
            return True
        return year in year_set

    for row in conversions or []:
        code = str(row.get("ma_truong") or "").strip().upper()
        if not code or (codes is not None and code not in codes):
            continue
        if not keep_year(row.get("nam")):
            continue
        loai = str(row.get("loai_bang") or "").strip()
        hang = str(row.get("hang_muc") or "").strip()
        diem = str(row.get("diem_quy_doi") or "").strip()
        detail = str(row.get("chi_tiet_hang") or "").strip()
        blob = " ".join([loai, hang, diem, detail])
        amount = _bonus_amount(diem, detail)
        bonusish = bool(_BONUS_WORD_RE.search(blob) or re.search(r"thưởng|khuyến khích", blob, re.I))
        certs = _certs_in(blob)
        is_test = bool(_TEST_RE.search(blob))
        is_prize = bool(_PRIZE_RE.search(blob))
        if not amount or not (bonusish or certs or is_test or is_prize):
            continue
        if certs and not bonusish and not re.search(r"điểm\b", diem, re.I):
            continue
        condition = _condition_label(loai, hang, certs)
        method = str(row.get("phuong_thuc") or "").strip()
        sig = (code, method.casefold(), condition.casefold(), amount.casefold())
        if sig in seen:
            continue
        seen.add(sig)
        records.append({
            "ma_truong": code,
            "ten_truong": str(row.get("ten_truong") or "").strip(),
            "nam": row.get("nam"),
            "phuong_thuc": method,
            "dieu_kien": condition,
            "diem_cong": amount,
            "bands": _kind_bands(loai, hang, detail),
            "nguon": str(row.get("nguon") or "").strip(),
        })

    for row in regulations or []:
        code = str(row.get("ma_truong") or "").strip().upper()
        if not code or (codes is not None and code not in codes):
            continue
        if not keep_year(row.get("nam")):
            continue
        title = str(row.get("tieu_de") or "")
        body = str(row.get("noi_dung") or "")
        original = re.sub(r"\s+", " ", f"{title} {body}").strip()
        masked = _mask_vact(original)
        match = _LINK_RE.search(masked) or _TEST_RE.search(masked)
        if not match or not _BONUS_WORD_RE.search(masked):
            continue
        window = masked[max(0, match.start() - 40): match.end() + 80]
        if _NEGATIVE_RE.search(window) and not _POSITIVE_RE.search(window):
            continue
        amount_match = _AMOUNT_DIEM_RE.search(window) or _AMOUNT_DETAIL_RE.search(window)
        amount = ""
        if amount_match:
            amount = _bonus_amount(amount_match.group(0), window) or amount_match.group(0)
        snippet = _clean_snippet(original, match.start(), match.end())
        method = str(row.get("phuong_thuc") or "").strip()
        sig = (code, method.casefold(), snippet[:90].casefold(), amount.casefold())
        if sig in seen:
            continue
        seen.add(sig)
        records.append({
            "ma_truong": code,
            "ten_truong": str(row.get("ten_truong") or "").strip(),
            "nam": row.get("nam"),
            "phuong_thuc": method,
            "dieu_kien": snippet,
            "diem_cong": amount or "Có cộng điểm",
            "nguon": str(row.get("nguon") or "").strip(),
        })

    def sort_key(item: dict) -> Tuple:
        try:
            year = -int(item.get("nam") or 0)
        except (TypeError, ValueError):
            year = 0
        return (
            item.get("ma_truong") or "",
            year,
            (item.get("phuong_thuc") or "").casefold(),
            (item.get("dieu_kien") or "").casefold(),
        )

    records.sort(key=sort_key)
    return records


def summarize_certificate_bonus(
    conversions: Sequence[Dict[str, Any]],
    regulations: Sequence[Dict[str, Any]],
    school_codes: Optional[Sequence[str]] = None,
    admissions: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Trả về các trường có cộng điểm theo chứng chỉ.

    school_codes: nếu có, chỉ xét các mã này và liệt kê mã không thấy quy chế.
    """
    codes = [str(c).strip().upper() for c in (school_codes or []) if str(c).strip()]
    code_set = set(codes) if codes else None
    names = _names_for(codes, conversions, regulations, admissions or [])
    tables = _table_groups(conversions, code_set)
    policies = _policy_rows(regulations, code_set)

    found_codes = set(tables) | set(policies)
    if codes:
        ordered = [c for c in codes if c in found_codes]
        missing = [c for c in codes if c not in found_codes]
    else:
        ordered = sorted(found_codes)
        missing = []

    schools = []
    cert_options = []
    method_options = []
    seen_certs = set()
    seen_methods = set()
    for code in ordered:
        rows = _merge_rows(tables.get(code) or [], policies.get(code) or [])
        if not rows:
            missing.append(code)
            continue
        certs: List[str] = []
        methods: List[str] = []
        for row in rows:
            for cert in row["certificates"]:
                if cert not in certs:
                    certs.append(cert)
                if cert not in seen_certs:
                    seen_certs.add(cert)
                    cert_options.append(cert)
            method = row["phuong_thuc"]
            if method not in methods:
                methods.append(method)
            if method not in seen_methods:
                seen_methods.add(method)
                method_options.append(method)
        schools.append({
            "ma_truong": code,
            "ten_truong": names.get(code) or code,
            "certificates": certs,
            "methods": methods,
            "rows": rows,
        })

    return {
        "schools": schools,
        "missing": [
            {"ma_truong": code, "ten_truong": names.get(code) or code}
            for code in missing
        ],
        "with_count": len(schools),
        "selected_count": len(codes) if codes else len(schools),
        "certificates": cert_options,
        "methods": method_options,
    }


# Phương thức thang ~30 điểm: bảng cộng điểm không ghi phương thức chỉ cộng vào các thang này.
_ADDITIVE_METHODS = {"THPT", "HOC_BA", "KET_HOP", "XTTN", "XTTN_1.2", "XTTN_1.3"}
_CERT_ALIASES = {
    "A-LEVEL": "A-Level",
    "ALEVEL": "A-Level",
    "TOEFL-IBT": "TOEFL",
    "TOEFL IBT": "TOEFL",
}
_BAND_RANGE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*-\s*(\d+(?:[.,]\d+)?)")
_BAND_UP_RE = re.compile(
    r"(?:≥|>=|từ)\s*(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s*(?:trở lên|\+)",
    re.I,
)
_BAND_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _canon_cert(name: str) -> str:
    raw = re.sub(r"\s+", " ", (name or "").strip())
    key = raw.upper().replace("_", " ")
    if key in _CERT_ALIASES:
        return _CERT_ALIASES[key]
    for label, _pat in _CERT_PATTERNS:
        if label.upper() == raw.upper() or label.upper() == key:
            return label
    return raw.upper()


def _amount_number(amount: str) -> Optional[float]:
    match = re.search(r"\d+(?:[.,]\d+)?", amount or "")
    if not match:
        return None
    return _parse_vn_number(match.group(0))


def _score_bands(text: str) -> List[Tuple[float, float]]:
    """Khoảng điểm đóng trong nhãn chứng chỉ. 'trở lên' → không giới hạn trên."""
    raw = (text or "").replace("–", "-").replace("—", "-").replace("−", "-")
    bands: List[Tuple[float, float]] = []
    for match in _BAND_RANGE_RE.finditer(raw):
        lo = _parse_vn_number(match.group(1))
        hi = _parse_vn_number(match.group(2))
        if lo is None or hi is None:
            continue
        if lo > hi:
            lo, hi = hi, lo
        bands.append((lo, hi))
    if bands:
        return bands
    up = _BAND_UP_RE.search(raw)
    if up:
        lo = _parse_vn_number(up.group(1) or up.group(2))
        if lo is not None:
            return [(lo, float("inf"))]
    nums = [n for n in (_parse_vn_number(x) for x in _BAND_NUM_RE.findall(raw)) if n is not None]
    if len(nums) == 1:
        return [(nums[0], nums[0])]
    return []


def _in_band(score: float, lo: float, hi: float) -> bool:
    if hi == float("inf"):
        return score + 1e-9 >= lo
    return (lo - 1e-9) <= score <= (hi + 1e-9)


def _method_allows_bonus(row_method: str, user_method: str) -> bool:
    if not row_method:
        return (user_method or "THPT") in _ADDITIVE_METHODS
    return row_method == (user_method or "")


def index_certificate_bonus_bands(
    conversions: Sequence[Dict[str, Any]],
) -> Dict[str, List[dict]]:
    """
    Bảng cộng điểm theo trường: mỗi mức là một khoảng chứng chỉ → số điểm cộng.
    """
    index: Dict[str, List[dict]] = {}
    for row in conversions or []:
        code = str(row.get("ma_truong") or "").strip().upper()
        if not code:
            continue
        hang = str(row.get("hang_muc") or "").strip()
        loai = str(row.get("loai_bang") or "").strip()
        diem = str(row.get("diem_quy_doi") or "").strip()
        detail = str(row.get("chi_tiet_hang") or "").strip()
        if _PRIZE_RE.search(hang) and not _certs_in(hang):
            continue
        if _HEADER_HANG_RE.search(hang) and not _certs_in(hang):
            continue
        amount = _bonus_amount(diem, detail)
        bonus = _amount_number(amount)
        if bonus is None or bonus <= 0:
            continue
        method = method_to_calc_id(str(row.get("phuong_thuc") or ""))
        primary_certs = _certs_in(" ".join([loai, hang]))
        if not primary_certs:
            continue
        bands = _score_bands(hang)
        targets: List[Tuple[List[str], float, float, str]] = []
        if bands:
            hang_certs = _certs_in(hang) or primary_certs
            for lo, hi in bands:
                targets.append((hang_certs, lo, hi, hang))
        for piece in detail.split(";"):
            piece_certs = _certs_in(piece)
            if not piece_certs:
                continue
            for lo, hi in _score_bands(piece):
                targets.append((piece_certs, lo, hi, piece.replace("=", ": ")))
        bucket = index.setdefault(code, [])
        for certs, lo, hi, label in targets:
            bucket.append({
                "certs": certs,
                "method": method,
                "lo": lo,
                "hi": hi,
                "bonus": bonus,
                "muc": re.sub(r"\s+", " ", label).strip()[:120],
                "diem": amount,
            })
    return index


def best_certificate_bonus(
    index: Dict[str, List[dict]],
    school_code: str,
    certificates: Sequence[Dict[str, Any]],
    method_id: str = "THPT",
) -> Optional[dict]:
    """
    Mức cộng cao nhất của trường cho các chứng chỉ đã nhập.
    Nhiều chứng chỉ không cộng dồn.
    """
    bands = (index or {}).get((school_code or "").upper()) or []
    if not bands or not certificates:
        return None
    best: Optional[Tuple[Tuple[float, float], dict]] = None
    for item in certificates:
        if not isinstance(item, dict):
            continue
        try:
            score = float(str(item.get("score")).replace(",", "."))
        except (TypeError, ValueError):
            continue
        cert = _canon_cert(str(item.get("type") or item.get("certificate") or ""))
        if not cert:
            continue
        for band in bands:
            if cert not in (band.get("certs") or []):
                continue
            if not _method_allows_bonus(band.get("method") or "", method_id):
                continue
            if not _in_band(score, float(band["lo"]), float(band["hi"])):
                continue
            hi = float(band["hi"])
            width = 1e12 if hi == float("inf") else hi - float(band["lo"])
            payload = {
                "bonus": float(band["bonus"]),
                "certificate": cert,
                "certificate_score": score,
                "muc": band.get("muc") or "",
                "diem": band.get("diem") or "",
                "method": band.get("method") or "",
            }
            rank = (width, -payload["bonus"])
            if best is None or rank < best[0]:
                best = (rank, payload)
    return best[1] if best else None
