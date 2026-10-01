# -*- coding: utf-8 -*-
"""Tư vấn ngành theo tiêu chí, điểm hồ sơ, tổ hợp môn, điểm chuẩn và phương thức."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core import dataset_store
from core.aggregator import METHOD_COLUMN_LABELS
from core.normalizer import strip_accents
from crawlers.school_directory import classify_school_sector, split_addresses_by_region

_COMBO_CODE = re.compile(r"\b([A-Z]\d{2})\b")
_REGION_BONUS = {"KV1": 0.75, "KV2-NT": 0.5, "KV2": 0.25, "KV3": 0.0}
_OBJECT_BONUS = {"01": 2.0, "02": 2.0, "03": 2.0, "04": 1.0, "05": 1.0, "06": 1.0}
_THPT_METHODS = {"THPT", "100"}
_FILE_SCHOOL = {
    "qsb.json": "QSB",
    "ueb.json": "QHE",
    "ngoai-thuong.json": "NTH",
    "buu-chinh-vien-thong.json": "BVH",
}
_MAJOR_ALIASES = {
    "cntt": ("cong nghe thong tin",),
    "it": ("cong nghe thong tin",),
    "cong nghe thong tin": ("cong nghe thong tin",),
}
_MAJOR_LABELS = {
    "cong nghe thong tin": "Công nghệ thông tin",
    "khoa hoc may tinh": "Khoa học máy tính",
    "ky thuat phan mem": "Kỹ thuật phần mềm",
    "he thong thong tin": "Hệ thống thông tin",
    "an toan thong tin": "An toàn thông tin",
    "tri tue nhan tao": "Trí tuệ nhân tạo",
    "ke toan": "Kế toán",
    "quan tri kinh doanh": "Quản trị kinh doanh",
    "ngon ngu anh": "Ngôn ngữ Anh",
    "cntt": "Công nghệ thông tin",
    "it": "Công nghệ thông tin",
}
_KNOWN_MAJOR_PHRASES = tuple(sorted(_MAJOR_LABELS, key=len, reverse=True))
_SECTOR_HINTS = (
    ("Kỹ thuật", ("ky thuat", "bach khoa", "xay dung", "giao thong")),
    ("Kinh tế", ("kinh te", "thuong mai", "tai chinh", "ngan hang", "ke toan")),
    ("Y - Dược", ("y duoc", "y khoa", "nganh duoc", "duoc hoc", "dai hoc duoc", "truong duoc")),
    ("Sư phạm", ("su pham",)),
    ("Luật", ("luat",)),
    ("Nông - Lâm - Ngư", ("nong nghiep", "lam nghiep", "thuy san")),
    ("Nghệ thuật", ("nghe thuat", "my thuat", "am nhac", "san khau", "dien anh")),
    ("Ngoại ngữ", ("ngoai ngu",)),
    ("An ninh - Quốc phòng", ("an ninh", "quoc phong", "cong an")),
    ("Du lịch", ("du lich",)),
    ("Báo chí - Truyền thông", ("bao chi", "truyen thong")),
    ("Thể dục - Thể thao", ("the duc", "the thao")),
    ("Đa ngành", ("da nganh",)),
)
_SUBJECT_ALIASES = (
    ("gdktpl", ("giao duc kinh te va phap luat", "giao duc cong dan", "gdcd", "gdktpl")),
    ("toan", ("toan",)),
    ("van", ("ngu van", "van")),
    ("anh", ("tieng anh", "anh van", "anh")),
    ("nga", ("tieng nga", "nga")),
    ("phap", ("tieng phap", "phap")),
    ("trung", ("tieng trung", "trung")),
    ("duc", ("tieng duc", "duc")),
    ("nhat", ("tieng nhat", "nhat")),
    ("han", ("tieng han", "han")),
    ("ly", ("vat li", "vat ly", "ly")),
    ("hoa", ("hoa hoc", "hoa")),
    ("sinh", ("sinh hoc", "sinh")),
    ("su", ("lich su", "su")),
    ("dia", ("dia li", "dia ly", "dia")),
    ("tin", ("tin hoc", "tin")),
    ("cn", ("cong nghe",)),
)
_SUBJECT_LABELS = {
    "toan": "Toán",
    "van": "Ngữ văn",
    "anh": "Tiếng Anh",
    "nga": "Tiếng Nga",
    "phap": "Tiếng Pháp",
    "trung": "Tiếng Trung",
    "duc": "Tiếng Đức",
    "nhat": "Tiếng Nhật",
    "han": "Tiếng Hàn",
    "ly": "Vật lí",
    "hoa": "Hóa học",
    "sinh": "Sinh học",
    "su": "Lịch sử",
    "dia": "Địa lí",
    "gdktpl": "Giáo dục kinh tế và pháp luật",
    "tin": "Tin học",
    "cn": "Công nghệ",
}
_STATUS_LABEL = {
    "dat": "Có thể dự tuyển",
    "sat": "Sát điểm chuẩn",
    "chua": "Thấp hơn điểm chuẩn",
    "thieu": "Chưa có điểm chuẩn THPT",
    "thang": "Khác thang điểm 30",
}


def fold(text: Any) -> str:
    raw = strip_accents(text or "")
    return re.sub(r"[^a-z0-9]+", " ", raw).strip()


def _num(value: Any) -> Optional[float]:
    raw = str(value or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        number = float(raw)
    except ValueError:
        return None
    if number < 0 or number > 10000:
        return None
    return number


def _unique(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def subject_key(label: str) -> str:
    folded = fold(label)
    if not folded:
        return ""
    padded = f" {folded} "
    best = ""
    best_len = 0
    for key, aliases in _SUBJECT_ALIASES:
        for alias in aliases:
            if f" {alias} " in padded and len(alias) > best_len:
                best = key
                best_len = len(alias)
    return best


def _combo_codes(value: Any) -> List[str]:
    found: List[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and re.fullmatch(r"[A-Za-z]\d{2}", item.strip()):
                found.append(item.strip().upper())
            elif isinstance(item, dict):
                code = str(item.get("ma") or item.get("ma_to_hop") or "").strip().upper()
                if re.fullmatch(r"[A-Z]\d{2}", code):
                    found.append(code)
            else:
                found.extend(_combo_codes(item))
        return _unique(found)
    if isinstance(value, str):
        return _unique(match.group(1) for match in _COMBO_CODE.finditer(value.upper()))
    return []


def _split_combo_text(text: str) -> Tuple[List[str], List[str]]:
    """Tách môn bắt buộc và môn trong ngoặc {a, b}."""
    required: List[str] = []
    options: List[str] = []
    for part in re.split(r",(?![^{}]*\})", text or ""):
        chunk = part.strip()
        if not chunk:
            continue
        braced = re.fullmatch(r"\{([^{}]+)\}", chunk)
        if braced:
            options.extend(piece.strip() for piece in braced.group(1).split(",") if piece.strip())
            continue
        required.append(chunk)
    return required, options


def text_covers_combo(text: str, combo_keys: List[str]) -> bool:
    if not text or not combo_keys or _combo_codes(text):
        return False
    required, options = _split_combo_text(text)
    required_keys = [subject_key(item) for item in required]
    option_keys = [subject_key(item) for item in options]
    pool = {key for key in required_keys + option_keys if key}
    if not pool or any(key not in pool for key in combo_keys):
        return False
    return all(key in combo_keys for key in required_keys if key)


def _priority(raw: float, region: str, obj: str) -> float:
    base = _REGION_BONUS.get(region, 0.0) + _OBJECT_BONUS.get(obj, 0.0)
    if base <= 0:
        return 0.0
    if raw <= 22.5:
        return round(base, 2)
    return round(base * max(30 - raw, 0) / 7.5, 2)


def _load_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _load_combos(root: str) -> List[Dict[str, Any]]:
    path = os.path.join(root, "data", "constants", "to_hop_mon_hoc.txt")
    data = _load_json(path)
    items: List[Dict[str, Any]] = []
    if not isinstance(data, list):
        return items
    for row in data:
        if not isinstance(row, dict):
            continue
        code = str(row.get("ma_to_hop") or "").strip().upper()
        subjects = str(row.get("cac_mon") or "").strip()
        if not re.fullmatch(r"[A-Z]\d{2}", code) or not subjects:
            continue
        keys = [subject_key(part) for part in subjects.split(",")]
        items.append({
            "code": code,
            "subjects": subjects,
            "group": code[:1],
            "keys": keys,
            "ready": all(keys),
        })
    return items


def _schools(root: str) -> List[Dict[str, str]]:
    data = dataset_store.load_schools(root) or _load_json(
        os.path.join(root, "config", "schools_all.json")
    ) or {}
    rows: List[Dict[str, str]] = []
    for item in data.get("schools") or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip().upper()
        name = str(item.get("name") or "").strip()
        if not code or not name:
            continue
        regions = split_addresses_by_region(item.get("dia_chi") or "")
        area = str(item.get("khu_vuc") or regions.get("khu_vuc") or "")
        rows.append({
            "code": code,
            "name": name,
            "khu_vuc": area,
            "loai_truong": str(item.get("loai_truong") or "") or classify_school_sector(name),
        })
    rows.sort(key=lambda row: (row["name"], row["code"]))
    return rows


def _pretty_method(name: str) -> str:
    text = str(name or "").strip()
    if not text or " " in text:
        return text
    upper = text.upper().replace("VACT", "V-ACT")
    if upper in METHOD_COLUMN_LABELS:
        return METHOD_COLUMN_LABELS[upper]
    tail = upper.split("_")[-1]
    if tail in METHOD_COLUMN_LABELS:
        return METHOD_COLUMN_LABELS[tail]
    if "_" in upper and tail.isalpha():
        return f"ĐGNL {tail}"
    fixed = {
        "100": "Thi THPT",
        "200": "Học bạ",
        "301": "Xét tuyển thẳng",
        "402": "ĐGNL / chứng chỉ",
        "407": "Xét tuyển kết hợp",
        "409": "Chứng chỉ ngoại ngữ và thi THPT",
        "410": "Chứng chỉ ngoại ngữ và học bạ",
        "500": "Giải học sinh giỏi",
    }
    return fixed.get(upper, text)


def _method_names(value: Any) -> List[str]:
    names: List[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                label = str(item.get("ten") or item.get("id") or "").strip()
            else:
                label = str(item or "").strip()
            label = _pretty_method(label)
            if label and label not in names and not label.isdigit():
                names.append(label)
    elif isinstance(value, str) and value.strip():
        names.append(value.strip())
    return names


def _program_from_method(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    school = str(record.get("ma_truong") or "").strip().upper()
    name = str(record.get("ten_nganh") or record.get("ten_chuong_trinh") or "").strip()
    if not school or not name:
        return None
    combos: List[str] = []
    methods = _method_names(record.get("hinh_thuc"))
    forms = record.get("hinh_thuc")
    if isinstance(forms, list):
        for form in forms:
            if not isinstance(form, dict):
                continue
            detail = form.get("chi_tiet") if isinstance(form.get("chi_tiet"), dict) else {}
            combos.extend(_combo_codes(detail.get("to_hop_xet_tuyen") or detail.get("to_hop")))
            combos.extend(_combo_codes(form.get("mo_ta")))
    combos.extend(_combo_codes(record.get("ghi_chu")))
    return {
        "ma_truong": school,
        "ten_nganh": name,
        "ma_xet_tuyen": str(record.get("ma_xet_tuyen") or "").strip().upper(),
        "ma_nganh": str(record.get("ma_nganh") or "").strip().upper(),
        "to_hop": _unique(combos),
        "to_hop_text": "",
        "phuong_thuc": methods,
        "nam": record.get("nam"),
    }


def _looks_like_major(node: Dict[str, Any]) -> bool:
    return any(node.get(key) for key in (
        "ten_nganh", "ten_chuong_trinh", "ten_nganh_chuong_trinh", "ten_nghanh",
    ))


def _school_code(node: Dict[str, Any], current: str, known: set) -> str:
    raw = node.get("ma_truong")
    if isinstance(raw, str) and raw.strip().upper() in known:
        return raw.strip().upper()
    return current


def _collect_local_programs(node: Any, school: str, inherited: List[str], known: set, out: List[Dict[str, Any]], depth: int = 0) -> None:
    if depth > 14:
        return
    if isinstance(node, list):
        for item in node:
            _collect_local_programs(item, school, inherited, known, out, depth + 1)
        return
    if not isinstance(node, dict):
        return
    school = _school_code(node, school, known)
    own = []
    texts = []
    for key in ("to_hop", "to_hop_xet_tuyen"):
        if key not in node:
            continue
        own.extend(_combo_codes(node.get(key)))
        if isinstance(node.get(key), str):
            texts.append(node.get(key))
    combos = _unique(own or inherited)
    if _looks_like_major(node) and school and (combos or texts):
        name = str(
            node.get("ten_nganh")
            or node.get("ten_chuong_trinh")
            or node.get("ten_nganh_chuong_trinh")
            or node.get("ten_nghanh")
            or ""
        ).strip()
        if name:
            out.append({
                "ma_truong": school,
                "ten_nganh": name,
                "ma_xet_tuyen": str(node.get("ma_xet_tuyen") or node.get("ma_tuyen_sinh") or "").strip().upper(),
                "ma_nganh": str(node.get("ma_nganh") or "").strip().upper(),
                "to_hop": combos,
                "to_hop_text": " ".join(texts),
                "phuong_thuc": _method_names(node.get("phuong_thuc") or node.get("phuong_thuc_xet_tuyen")),
                "nam": node.get("nam") or node.get("nam_tuyen_sinh"),
            })
    child_inherited = combos if own else inherited
    for key, value in node.items():
        if key in {"to_hop", "to_hop_xet_tuyen", "phuong_thuc_tuyen_sinh"}:
            continue
        next_school = school
        token = str(key).upper().split("_")[-1]
        if token in known:
            next_school = token
        _collect_local_programs(value, next_school, child_inherited, known, out, depth + 1)


def _programs(root: str, known: set) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    seen = set()
    payload = dataset_store.load_phuong_thuc(root) or {}
    for record in payload.get("records") or []:
        if not isinstance(record, dict):
            continue
        program = _program_from_method(record)
        if not program or not program["to_hop"]:
            continue
        key = (program["ma_truong"], program["ma_xet_tuyen"], fold(program["ten_nganh"]))
        if key in seen:
            continue
        seen.add(key)
        found.append(program)
    folder = os.path.join(root, "data", "tuyen-sinh")
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".json"):
                continue
            data = _load_json(os.path.join(folder, name))
            if data is None:
                continue
            local: List[Dict[str, Any]] = []
            _collect_local_programs(data, _FILE_SCHOOL.get(name, ""), [], known, local)
            for program in local:
                key = (program["ma_truong"], program["ma_xet_tuyen"] or program["ma_nganh"], fold(program["ten_nganh"]))
                if key in seen:
                    continue
                seen.add(key)
                found.append(program)
    return found


def _remember_cutoff(
    index: Dict[Tuple[str, str, str], Dict[str, Any]],
    school: str,
    kind: str,
    ident: str,
    row: Dict[str, Any],
    replace: bool = True,
) -> None:
    ident = ident.strip().upper() if kind == "code" else fold(ident)
    if not school or not ident:
        return
    key = (school, kind, ident)
    prev = index.get(key)
    if prev is not None and not replace:
        return
    if prev is None or int(row.get("nam") or 0) >= int(prev.get("nam") or 0):
        index[key] = row


def _thpt_cutoff_value(record: Dict[str, Any]) -> Optional[float]:
    method = str(record.get("phuong_thuc") or "").strip().upper()
    if method not in _THPT_METHODS:
        return None
    score = _num(record.get("diem_chuan"))
    if score is None or score <= 0 or score > 40:
        return None
    return score


def _cutoffs(root: str, admissions: Optional[List[Dict[str, Any]]] = None) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    index: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    if admissions is None:
        payload = dataset_store.load_admissions(root) or {}
        admissions = payload.get("admissions") or []
    for record in admissions:
        if not isinstance(record, dict):
            continue
        score = _thpt_cutoff_value(record)
        if score is None:
            continue
        school = str(record.get("ma_truong") or "").strip().upper()
        row = {
            "diem_chuan": score,
            "nam": record.get("nam"),
            "phuong_thuc": str(record.get("phuong_thuc") or ""),
            "nguon": "diem_chuan",
        }
        _remember_cutoff(index, school, "code", str(record.get("ma_xet_tuyen") or ""), row)
        _remember_cutoff(index, school, "code", str(record.get("ma_nganh") or ""), row)
        _remember_cutoff(index, school, "name", str(record.get("ten_nganh") or ""), row)
    folder = os.path.join(root, "data", "diem-chuan")
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".json"):
                continue
            data = _load_json(os.path.join(folder, name))
            if data is None:
                continue
            _collect_file_cutoffs(data, _FILE_SCHOOL.get(name, ""), None, index)
    return index


def _score_from_item(item: Dict[str, Any]) -> Optional[float]:
    for key in ("diem_thi_tn_thpt", "diem_trung_tuyen", "diem_chuan"):
        score = _num(item.get(key))
        if score is None:
            continue
        if key == "diem_chuan" and not (10 <= score <= 40):
            continue
        if key != "diem_chuan" and not (0 < score <= 40):
            continue
        return score
    return None


def _collect_file_cutoffs(node: Any, school: str, year: Optional[int], index: Dict[Tuple[str, str, str], Dict[str, Any]], known_only: bool = False) -> None:
    if isinstance(node, list):
        for item in node:
            _collect_file_cutoffs(item, school, year, index)
        return
    if not isinstance(node, dict):
        return
    raw_school = node.get("ma_truong")
    if isinstance(raw_school, str) and re.fullmatch(r"[A-Za-z0-9]{2,8}", raw_school.strip()):
        school = raw_school.strip().upper()
    for key in ("nam", "nam_tuyen_sinh"):
        parsed = _num(node.get(key))
        if parsed and 2000 <= parsed <= 2100:
            year = int(parsed)
    name = str(node.get("ten_nganh") or node.get("ten_nghanh") or "").strip()
    score = _score_from_item(node) if name or node.get("ma_xet_tuyen") or node.get("ma_nganh") else None
    if school and score is not None and not known_only:
        row = {"diem_chuan": score, "nam": year, "phuong_thuc": "THPT", "nguon": "ho_so_diem"}
        _remember_cutoff(index, school, "code", str(node.get("ma_xet_tuyen") or node.get("ma_tuyen_sinh") or ""), row, replace=False)
        _remember_cutoff(index, school, "code", str(node.get("ma_nganh") or ""), row, replace=False)
        _remember_cutoff(index, school, "name", name, row, replace=False)
    for value in node.values():
        if isinstance(value, (dict, list)):
            _collect_file_cutoffs(value, school, year, index)


def _lookup_cutoff(index: Dict[Tuple[str, str, str], Dict[str, Any]], program: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    school = program["ma_truong"]
    for code in (program.get("ma_xet_tuyen"), program.get("ma_nganh")):
        if code:
            found = index.get((school, "code", str(code).upper()))
            if found:
                return found
    return index.get((school, "name", fold(program.get("ten_nganh"))))


def _hoc_ba_scores(profile: Dict[str, Any]) -> Dict[str, float]:
    totals: Dict[str, List[float]] = {}
    grades = profile.get("hoc_ba") if isinstance(profile.get("hoc_ba"), dict) else {}
    for grade in ("10", "11", "12"):
        subjects = grades.get(grade) if isinstance(grades.get(grade), dict) else {}
        for key, raw in subjects.items():
            score = _num(raw)
            if score is None or score > 10:
                continue
            totals.setdefault(str(key), []).append(score)
    return {key: round(sum(values) / len(values), 2) for key, values in totals.items() if values}


def _profile_exam_scores(profile: Dict[str, Any]) -> Dict[str, float]:
    raw = profile.get("diem_thi_thu") if isinstance(profile.get("diem_thi_thu"), dict) else {}
    scores: Dict[str, float] = {}
    for key, value in raw.items():
        score = _num(value)
        if score is None or score > 10:
            continue
        scores[str(key)] = score
    return scores


def advisor_options(root: str) -> Dict[str, Any]:
    profile = dataset_store.load_profile(root)
    schools = _schools(root)
    combos = _load_combos(root)
    sectors: Dict[str, int] = {}
    for school in schools:
        sectors[school["loai_truong"]] = sectors.get(school["loai_truong"], 0) + 1
    used_keys = []
    for combo in combos:
        for key in combo["keys"]:
            if key and key not in used_keys:
                used_keys.append(key)
    exam = _profile_exam_scores(profile)
    transcript = _hoc_ba_scores(profile)
    return {
        "ok": True,
        "regions": ["Bắc", "Trung", "Nam"],
        "sectors": [
            {"id": name, "count": sectors[name]}
            for name in sorted(sectors, key=lambda item: (-sectors[item], item))
        ],
        "schools": schools,
        "combos": [
            {
                "code": item["code"],
                "subjects": item["subjects"],
                "group": item["group"],
                "keys": item["keys"],
            }
            for item in combos
        ],
        "subjects": [{"key": key, "label": _SUBJECT_LABELS.get(key, key)} for key in used_keys],
        "scores": {key: exam[key] for key in used_keys if key in exam},
        "hoc_ba": {key: transcript[key] for key in used_keys if key in transcript},
        "profile": {
            "ho_ten": profile.get("ho_ten") or "",
            "khu_vuc": profile.get("khu_vuc") or "",
            "doi_tuong": profile.get("doi_tuong") or "",
        },
    }


def _as_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _mentioned_combos(question: str, catalog: Dict[str, Dict[str, Any]]) -> List[str]:
    found = []
    for match in _COMBO_CODE.finditer(question.upper()):
        code = match.group(1)
        if code in catalog and code not in found:
            found.append(code)
    return found


def _extract_major(question: str, folded_question: str) -> Tuple[str, List[str]]:
    """Tên ngành trong câu hỏi, ví dụ «ngành công nghệ thông tin»."""
    match = re.search(
        r"ngành\s+(?!nào\b)(.+?)(?=\s+(?:ở|tại|của|không|được)\b|\s+trường\s+nào\b|\s+miền\s+|\s+khối\b|\s+cho\s+|$)",
        question,
        flags=re.IGNORECASE,
    )
    phrase = fold(match.group(1)) if match else ""
    phrase = re.sub(r"^(hoc|nay|do)\s+", "", phrase).strip(" .")
    if len(phrase) < 3:
        phrase = ""
    if not phrase:
        padded = f" {folded_question} "
        for known in _KNOWN_MAJOR_PHRASES:
            if f" {known} " in padded or padded.strip() == known:
                phrase = known
                break
    if not phrase:
        return "", []
    keywords = list(_MAJOR_ALIASES.get(phrase, (phrase,)))
    if phrase in _MAJOR_LABELS:
        label = _MAJOR_LABELS[phrase]
    elif match:
        label = re.sub(r"\s+", " ", match.group(1)).strip()
    else:
        label = phrase
    return label, keywords


def _major_matches(name: str, keywords: List[str]) -> bool:
    if not keywords:
        return True
    folded = fold(name)
    return any(keyword in folded for keyword in keywords)


def _mentioned_sectors(folded_question: str) -> List[str]:
    found = []
    padded = f" {folded_question} "
    for label, hints in _SECTOR_HINTS:
        if any(f" {hint} " in padded for hint in hints) and label not in found:
            found.append(label)
    return found


def _mentioned_regions(folded_question: str) -> List[str]:
    found = []
    if any(hint in folded_question for hint in ("mien bac", "phia bac", "ha noi")):
        found.append("Bắc")
    if any(hint in folded_question for hint in ("mien trung", "da nang", "hue")):
        found.append("Trung")
    if any(hint in folded_question for hint in ("mien nam", "phia nam", "ho chi minh", "sai gon", "tp hcm")):
        found.append("Nam")
    return found


def _mentioned_schools(folded_question: str, schools: List[Dict[str, str]]) -> List[str]:
    found = []
    padded = f" {folded_question} "
    for school in schools:
        code = school["code"].lower()
        if re.search(rf"(?<![a-z0-9]){re.escape(code)}(?![a-z0-9])", folded_question):
            found.append(school["code"])
            continue
        name = fold(school["name"])
        for stop in ("truong ", "dai hoc ", "hoc vien ", "thanh pho ", "tp "):
            name = name.replace(stop, " ")
        name = re.sub(r"\s+", " ", name).strip()
        if len(name) >= 8 and f" {name} " in padded:
            found.append(school["code"])
    return _unique(found)


def _parse_stated_total(question: str) -> Optional[float]:
    """Tổng điểm thí sinh nêu trong câu, ví dụ «điểm thi 20 điểm»."""
    text = str(question or "").replace(",", ".")
    text = re.sub(
        r"điểm\s+chuẩn(?:\s+\w+){0,4}\s*\d+(?:\.\d+)?",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    patterns = (
        r"(?:với|giả\s*sử|đạt|được|tổng)\s+(?:điểm(?:\s+thi)?(?:\s+thpt)?)?\s*(\d+(?:\.\d+)?)\s*điểm",
        r"điểm(?:\s+thi)(?:\s+thpt)?\s*(?:là|đạt|được|khoảng|của\s+(?:tôi|em))?\s*(\d+(?:\.\d+)?)",
        r"(\d+(?:\.\d+)?)\s*điểm",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = _num(match.group(1))
        if value is None or value <= 0 or value > 40:
            continue
        return value
    return None


def _parse_stated_subjects(question: str) -> Dict[str, float]:
    """Điểm từng môn nêu trong câu, ví dụ «Toán 8,5 Lý 8»."""
    text = strip_accents(str(question or "")).lower().replace(",", ".")
    text = re.sub(r"[^a-z0-9.]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    aliases = sorted(
        ((alias, key) for key, names in _SUBJECT_ALIASES for alias in names),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    found: Dict[str, float] = {}
    for alias, key in aliases:
        if key in found:
            continue
        match = re.search(rf"\b{re.escape(alias)}\b\s+(\d+(?:\.\d+)?)", text)
        if not match:
            match = re.search(rf"(\d+(?:\.\d+)?)\s+diem\s+{re.escape(alias)}\b", text)
        if not match:
            continue
        value = _num(match.group(1))
        if value is None or value < 0 or value > 10:
            continue
        found[key] = value
    return found


def _stated_card(combo: Dict[str, Any], total: float) -> Dict[str, Any]:
    return {
        "code": combo["code"],
        "subjects": combo["subjects"],
        "parts": [],
        "missing": [],
        "raw": total,
        "bonus": 0,
        "total": total,
        "stated": True,
    }


def _parse_scores(raw: Any) -> Dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    scores: Dict[str, float] = {}
    for key, value in raw.items():
        score = _num(value)
        if score is None or score > 10:
            continue
        scores[str(key)] = score
    return scores


def _combo_total(combo: Dict[str, Any], scores: Dict[str, float]) -> Dict[str, Any]:
    parts = []
    missing = []
    for key in combo["keys"]:
        label = _SUBJECT_LABELS.get(key, key or "môn chưa map")
        if not key:
            missing.append(label)
            continue
        if key not in scores:
            missing.append(label)
            continue
        parts.append({"key": key, "label": label, "score": scores[key]})
    raw = round(sum(part["score"] for part in parts), 2) if parts and not missing else None
    return {
        "code": combo["code"],
        "subjects": combo["subjects"],
        "parts": parts,
        "missing": missing,
        "raw": raw,
    }


def _program_matches(program: Dict[str, Any], combo: Dict[str, Any]) -> bool:
    if combo["code"] in (program.get("to_hop") or []):
        return True
    return text_covers_combo(program.get("to_hop_text") or "", [key for key in combo["keys"] if key])


_LIST_HINTS = (
    "tong hop", "liet ke", "danh sach", "ke ra", "cac truong", "nhung truong",
    "truong nao thuoc", "truong thuoc",
)
_ADVICE_HINTS = (
    "nen thi", "nen hoc", "nen chon", "nen dang ky",
    "co the thi", "co the du tuyen", "du tuyen", "trung tuyen",
    "phu hop", "co hoi", "diem cua", "nganh nao", "nganh ",
)


def _wants_score_stats(folded: str) -> bool:
    """Câu xin thống kê điểm chuẩn đã thu thập, không so với điểm cá nhân."""
    return "thong ke" in folded and "diem" in folded


def _mentioned_year(question: str) -> Optional[int]:
    years = [int(item) for item in re.findall(r"\b(20\d{2})\b", question)]
    return years[-1] if years else None


_METHOD_RANK = {
    "THPT": 0,
    "100": 1,
    "HOC_BA": 2,
    "200": 3,
    "TSA": 4,
    "HSA": 5,
    "V-ACT": 6,
    "DGNL": 7,
    "XTTN": 8,
    "XTTN_1_2": 9,
    "XTTN_1_3": 10,
    "SAT": 11,
    "ACT": 12,
}


def _method_sort_key(name: str) -> Tuple[int, str]:
    token = str(name or "").strip().upper()
    return (_METHOD_RANK.get(token, 50), token)


def _fmt_vi(value: Optional[float]) -> str:
    return _fmt(value).replace(".", ",")


def _score_records(root: str, admissions: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Cùng nguồn với bảng Dữ liệu điểm: mỗi dòng là một phương thức của một ngành."""
    if admissions is None:
        payload = dataset_store.load_admissions(root) or {}
        admissions = payload.get("admissions") or []
    rows: List[Dict[str, Any]] = []
    for item in admissions:
        if not isinstance(item, dict):
            continue
        raw = item.get("diem_chuan_ptxt")
        if raw is None:
            raw = item.get("diem_chuan")
        score = _num(raw)
        if score is None:
            continue
        year = _num(item.get("nam"))
        rows.append({
            "ma_truong": str(item.get("ma_truong") or "").strip().upper(),
            "ten_truong": str(item.get("ten_truong") or "").strip(),
            "nam": int(year) if year and 2000 <= year <= 2100 else None,
            "ma_xet_tuyen": str(item.get("ma_xet_tuyen") or "").strip().upper(),
            "ma_nganh": str(item.get("ma_nganh") or "").strip().upper(),
            "ten_nganh": str(item.get("ten_nganh") or "").strip(),
            "phuong_thuc": str(item.get("phuong_thuc") or "").strip(),
            "diem_chuan": score,
        })
    return rows


def _answer_score_stats(
    root: str,
    schools: List[Dict[str, str]],
    regions: List[str],
    sectors: List[str],
    school_codes: List[str],
    major_label: str,
    major_keywords: List[str],
    question: str,
    admissions: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    school_map = {row["code"]: row for row in schools}
    scope = [school for school in schools if _school_ok(school, regions, sectors, school_codes)]
    if not school_codes and not regions and not sectors:
        return {
            "ok": True,
            "kind": "stats",
            "summary": "Hãy nói trường cần thống kê, ví dụ: thống kê điểm chuẩn các ngành công nghệ ở Bách khoa Hà Nội.",
            "notes": ["Số liệu lấy từ dữ liệu điểm đã thu thập."],
            "rows": [],
            "methods": [],
            "counts": {},
        }
    asked_year = _mentioned_year(question)
    records = [
        row for row in _score_records(root, admissions)
        if row["ma_truong"] in {school["code"] for school in scope}
        and _major_matches(row["ten_nganh"], major_keywords)
        and row["phuong_thuc"]
    ]
    years = sorted({row["nam"] for row in records if row["nam"]})
    year = asked_year if asked_year in years else (years[-1] if years else asked_year)
    if year:
        records = [row for row in records if row["nam"] == year]
    grouped: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for row in records:
        key = (row["ma_truong"], row["ma_xet_tuyen"], row["ma_nganh"] or fold(row["ten_nganh"]))
        bucket = grouped.get(key)
        if bucket is None:
            school = school_map.get(row["ma_truong"], {})
            bucket = {
                "ma_truong": row["ma_truong"],
                "ten_truong": school.get("name") or row["ten_truong"] or row["ma_truong"],
                "ten_nganh": row["ten_nganh"],
                "ma_xet_tuyen": row["ma_xet_tuyen"],
                "nam": row["nam"],
                "diem": {},
            }
            grouped[key] = bucket
        bucket["diem"][row["phuong_thuc"]] = row["diem_chuan"]
    methods = sorted(
        {name for bucket in grouped.values() for name in bucket["diem"]},
        key=_method_sort_key,
    )
    lead = next((name for name in methods if name.upper() in {"THPT", "100"}), methods[0] if methods else "")

    def lead_score(bucket: Dict[str, Any]) -> float:
        value = bucket["diem"].get(lead) if lead else None
        return value if isinstance(value, (int, float)) else -1

    ranked = sorted(
        grouped.values(),
        key=lambda bucket: (-lead_score(bucket), bucket["ten_truong"], bucket["ten_nganh"]),
    )
    shown = ranked[:40]
    names = []
    for code in _unique(bucket["ma_truong"] for bucket in ranked or [{"ma_truong": school["code"]} for school in scope]):
        school = school_map.get(code, {})
        names.append(school.get("name") or code)
    place = []
    if names:
        place.append("ở " + ", ".join(names[:4]))
    if year:
        place.append(f"năm {year}")
    place_text = ", ".join(place)
    major_text = f" có tên chứa «{major_label}»" if major_label else ""
    if not ranked:
        summary = f"Không thấy điểm chuẩn ngành{major_text} {place_text} trong dữ liệu điểm đã thu thập.".replace("  ", " ")
        stats_note = ""
    else:
        values = [bucket["diem"].get(lead) for bucket in ranked if lead and bucket["diem"].get(lead) is not None]
        summary = f"Có {len(ranked)} ngành{major_text}"
        if place_text:
            summary += " " + place_text
        summary += "."
        if values and lead:
            summary += (
                f" Điểm chuẩn {lead}: thấp nhất {_fmt_vi(min(values))},"
                f" cao nhất {_fmt_vi(max(values))},"
                f" trung bình {_fmt_vi(round(sum(values) / len(values), 2))}."
            )
        stats_note = ""
        if len(ranked) > len(shown):
            stats_note = f"Bảng hiện {len(shown)}/{len(ranked)} ngành, xếp theo điểm {lead or 'chuẩn'} giảm dần."
    notes = ["Số liệu lấy từ dữ liệu điểm đã thu thập, cùng bảng với mục Thu thập → Dữ liệu điểm."]
    if stats_note:
        notes.append(stats_note)
    if year and years and asked_year and asked_year not in years:
        notes.append(f"Không có năm {asked_year}. Đang dùng năm {year}.")
    return {
        "ok": True,
        "kind": "stats",
        "summary": summary,
        "notes": notes,
        "rows": shown,
        "methods": methods,
        "counts": {"nganh": len(ranked), "nam": year or 0},
        "filters": {
            "regions": regions,
            "sectors": sectors,
            "schools": [school["code"] for school in scope],
            "combos": [],
            "major": major_label,
        },
    }


_CERT_TOKENS = (
    ("V-ACT", r"V[\-\s]?ACT"),
    ("A-Level", r"A[\-\s]?Level"),
    ("TOEFL", r"TOEFL"),
    ("TOEIC", r"TOEIC"),
    ("IELTS", r"IELTS"),
    ("VSTEP", r"VSTEP"),
    ("JLPT", r"JLPT"),
    ("HSK", r"HSK"),
    ("TSA", r"TSA"),
    ("HSA", r"HSA"),
    ("SAT", r"SAT"),
    ("ACT", r"ACT"),
    ("MOS", r"MOS"),
)
_LOOKUP_HINTS = ("bao nhieu", "la may", "bao nhiu", "may diem")


def _mentioned_certs(question: str) -> List[str]:
    found: List[str] = []
    masked = str(question or "")
    for label, pattern in _CERT_TOKENS:
        regex = re.compile(rf"(?<![A-Za-z0-9])(?:{pattern})(?![A-Za-z0-9])", re.IGNORECASE)
        if not regex.search(masked):
            continue
        found.append(label)
        masked = regex.sub(" ", masked)
    return found


def _cert_in_text(text: str, cert: str) -> bool:
    pattern = next((item[1] for item in _CERT_TOKENS if item[0] == cert), re.escape(cert))
    return re.search(rf"(?<![A-Za-z0-9])(?:{pattern})(?![A-Za-z0-9])", str(text or ""), re.IGNORECASE) is not None


def _profile_cert_value(profile: Dict[str, Any], cert: str) -> Tuple[str, str]:
    certs = profile.get("chung_chi") if isinstance(profile.get("chung_chi"), dict) else {}
    exact = None
    prefix = None
    for key, value in certs.items():
        name = str(key or "").strip()
        if not name or not str(value or "").strip():
            continue
        if name.upper() == cert.upper():
            exact = (name, str(value).strip())
        elif name.upper().startswith(cert.upper()):
            prefix = prefix or (name, str(value).strip())
    return exact or prefix or ("", "")


def _stated_cert_score(question: str, cert: str) -> Optional[float]:
    text = str(question or "").replace(",", ".")
    pattern = next((item[1] for item in _CERT_TOKENS if item[0] == cert), re.escape(cert))
    token = rf"(?<![A-Za-z0-9])(?:{pattern})(?![A-Za-z0-9])"
    number = r"(\d+(?:\.\d+)?)"
    patterns = (
        rf"{token}\D{{0,16}}{number}",
        rf"{number}\D{{0,12}}{token}",
    )
    for item in patterns:
        match = re.search(item, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = _num(match.group(1))
        if value is None or value <= 0 or 2000 <= value <= 2100:
            continue
        return value
    return None


_OFFICIAL_METHODS = (
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
)
_OFFICIAL_BY_CODE = dict(_OFFICIAL_METHODS)
_OFFICIAL_ORDER = {code: index for index, (code, _name) in enumerate(_OFFICIAL_METHODS)}


def _official_label(code: str) -> str:
    name = _OFFICIAL_BY_CODE.get(str(code or "").strip())
    return f"{code} — {name}" if name else str(code or "").strip()


def _named_year(question: str) -> Optional[int]:
    found = [int(item) for item in re.findall(r"(?<!\d)(20\d{2})(?!\d)", str(question or ""))]
    return found[-1] if found else None


def _form_combos(form: Dict[str, Any]) -> List[str]:
    detail = form.get("chi_tiet") if isinstance(form.get("chi_tiet"), dict) else {}
    raw = detail.get("to_hop_xet_tuyen") or detail.get("to_hop") or form.get("to_hop") or []
    if isinstance(raw, list) and raw:
        return _unique(str(item).strip() for item in raw if str(item).strip())
    note = str(form.get("mo_ta") or "").strip()
    if fold(note).startswith("to hop"):
        tail = note.split(":", 1)[1] if ":" in note else ""
        return _unique(part.strip() for part in tail.split(",") if part.strip())
    return []


_METHOD_STOP = {
    "xet", "ket", "qua", "cap", "voi", "de", "su", "dung", "theo", "cua",
    "va", "hoac", "do", "don", "vi", "khac", "to", "chuc", "bang", "cho",
    "trong", "cac", "mot", "la", "co", "khong", "duoc", "nam",
    "hinh", "thuc", "phuong",
}


def _method_tokens(text: str) -> List[str]:
    return [word for word in fold(text).split() if word not in _METHOD_STOP and len(word) > 1]


def _method_phrase(folded: str) -> str:
    match = None
    for pattern in (r"hinh thuc\s+(.+)", r"phuong thuc\s+(.+)", r"tuyen sinh theo\s+(.+)"):
        match = re.search(pattern, folded)
        if match:
            break
    if not match:
        return ""
    phrase = re.split(r"\b(?:o|tai|cua|khong|duoc|cho|nam)\b", match.group(1))[0]
    return phrase.strip(" ?.!")


def _match_official_codes(phrase: str) -> List[str]:
    text = fold(phrase)
    if not text:
        return []
    direct = [code for code in re.findall(r"\b(\d{3})\b", text) if code in _OFFICIAL_BY_CODE]
    if direct:
        return _unique(direct)
    query = _method_tokens(text)
    if not query:
        return []
    ranked = []
    for code, name in _OFFICIAL_METHODS:
        name_folded = fold(name)
        if text in name_folded:
            ranked.append((1.0, 1.0, code))
            continue
        tokens = _method_tokens(name)
        if not tokens:
            continue
        overlap = [token for token in query if token in tokens]
        recall = len(overlap) / len(query)
        precision = len(set(overlap)) / len(set(tokens))
        ranked.append((recall, precision, code))
    if not ranked:
        return []
    best_recall = max(item[0] for item in ranked)
    if best_recall < 0.67:
        return []
    close = [item for item in ranked if item[0] >= best_recall - 0.01]
    best_precision = max(item[1] for item in close)
    return [code for _recall, precision, code in close if precision >= best_precision - 0.05]


def _label_matches_phrase(label: str, phrase: str) -> bool:
    folded_label = fold(label)
    if phrase and phrase in folded_label:
        return True
    query = _method_tokens(phrase)
    tokens = _method_tokens(label)
    if not query or not tokens:
        return False
    return len([token for token in query if token in tokens]) / len(query) >= 0.99


def _group_codes(groups: Dict[str, Any], school: str, year: int, label: str) -> List[str]:
    school_groups = groups.get(school) if isinstance(groups.get(school), dict) else {}
    year_groups = school_groups.get(str(year)) if isinstance(school_groups.get(str(year)), dict) else {}
    raw = year_groups.get(label) or []
    if not isinstance(raw, list):
        return []
    return [str(code).strip() for code in raw if str(code).strip() in _OFFICIAL_BY_CODE]


def _open_method_question(folded: str) -> bool:
    """Hỏi phương thức nào, chưa nêu tên một hình thức cụ thể."""
    if "phuong thuc" not in folded and "hinh thuc" not in folded:
        return False
    return _method_phrase(folded) in {"", "nao", "gi", "nao vay", "gi vay", "the nao"}


def _schools_in_scope(
    root: str,
    regions: List[str],
    sectors: List[str],
    school_codes: List[str],
    admissions: Optional[List[Dict[str, Any]]],
) -> List[str]:
    """Trường đã tổng hợp, lọc khu vực và loại hình theo danh mục trường học."""
    collected = _collected_schools(root, admissions)
    taxonomy = _taxonomy_by_code(root, collected)
    chosen = []
    for code in collected:
        if school_codes and code not in school_codes:
            continue
        meta = taxonomy.get(code, {})
        if regions and meta.get("khu_vuc") not in regions:
            continue
        if sectors and meta.get("loai_truong") not in sectors:
            continue
        chosen.append(code)
    chosen.sort(key=lambda code: (collected.get(code) or code, code))
    return chosen


def _wants_schools_by_method(folded: str) -> bool:
    """Hỏi trường nào tuyển sinh theo một hình thức, không so điểm cá nhân."""
    if _open_method_question(folded):
        return False
    if any(hint in folded for hint in ("cua toi", "cua em", "diem cua")):
        return False
    if "truong" not in folded or "nao" not in folded:
        return False
    return any(hint in folded for hint in ("hinh thuc", "phuong thuc", "tuyen sinh theo"))


def _answer_schools_by_method(
    root: str,
    schools: List[Dict[str, str]],
    regions: List[str],
    sectors: List[str],
    school_codes: List[str],
    question: str,
    folded: str,
) -> Dict[str, Any]:
    phrase = _method_phrase(folded)
    codes = _match_official_codes(phrase)
    scope = [school for school in schools if _school_ok(school, regions, sectors, school_codes)]
    scope_map = {school["code"]: school for school in scope}
    if not phrase:
        return {
            "ok": True,
            "kind": "methodSchools",
            "summary": "Hãy nêu hình thức cần tìm, ví dụ: trường nào ở miền Bắc tuyển sinh theo hình thức xét học bạ THPT?",
            "notes": [],
            "rows": [],
            "counts": {},
        }
    payload = dataset_store.load_phuong_thuc(root) or {}
    groups = payload.get("nhom_phuong_thuc") if isinstance(payload.get("nhom_phuong_thuc"), dict) else {}
    asked_year = _named_year(question)
    by_school: Dict[str, List[Dict[str, Any]]] = {}
    for record in payload.get("records") or []:
        if not isinstance(record, dict):
            continue
        code = str(record.get("ma_truong") or "").strip().upper()
        if code in scope_map:
            by_school.setdefault(code, []).append(record)
    rows = []
    for code, records in by_school.items():
        years = sorted({
            int(item) for item in (_num(record.get("nam")) for record in records)
            if item and 2000 <= item <= 2100
        })
        year = asked_year if asked_year in years else (years[-1] if years and asked_year is None else None)
        if year is None:
            continue
        labels: List[str] = []
        used = 0
        total = 0
        for record in records:
            if _num(record.get("nam")) != year:
                continue
            total += 1
            matched_labels = []
            for form in record.get("hinh_thuc") or []:
                if not isinstance(form, dict) or form.get("ap_dung") is False:
                    continue
                label = str(form.get("ten") or form.get("id") or "").strip()
                if not label:
                    continue
                mapped = _group_codes(groups, code, year, label)
                official = label in _OFFICIAL_BY_CODE
                if official:
                    accepted = label in codes or bool(mapped) and set(mapped) <= set(codes)
                elif mapped and set(mapped) <= set(codes):
                    accepted = True
                else:
                    accepted = _label_matches_phrase(label, phrase)
                    if not accepted and not codes:
                        accepted = bool(phrase) and phrase in fold(str(form.get("mo_ta") or ""))
                if accepted and label not in matched_labels:
                    matched_labels.append(label)
            if not matched_labels:
                continue
            used += 1
            for label in matched_labels:
                if label not in labels:
                    labels.append(label)
        if not used:
            continue
        school = scope_map[code]
        shown = []
        for label in labels:
            if label in _OFFICIAL_BY_CODE:
                shown.append(_official_label(label))
            else:
                shown.append(label)
        rows.append({
            "ma_truong": code,
            "ten_truong": school["name"],
            "khu_vuc": school["khu_vuc"],
            "loai_truong": school["loai_truong"],
            "phuong_thuc": "; ".join(shown),
            "so_nganh": used,
            "tong_nganh": total,
            "nam": year,
        })
    rows.sort(key=lambda row: (row["ten_truong"], row["ma_truong"]))
    method_text = ", ".join(_official_label(code) for code in codes) if codes else phrase
    scope_bits = []
    if sectors:
        scope_bits.append("khối " + ", ".join(sectors))
    if regions:
        scope_bits.append("miền " + ", ".join(regions))
    place = " ".join(scope_bits)
    where = f" ở {place}" if place else ""
    if rows:
        summary = f"Có {len(rows)} trường{where} tuyển sinh theo hình thức {method_text}."
    elif codes or phrase:
        summary = f"Không thấy trường nào{where} tuyển sinh theo hình thức {method_text} trong dữ liệu phương thức đã thu thập."
    else:
        summary = f"Chưa nhận ra hình thức «{phrase}»."
    return {
        "ok": True,
        "kind": "methodSchools",
        "summary": summary,
        "notes": ["Đối chiếu phương thức tuyển sinh đã thu thập. Trường được tính khi có ngành áp dụng đúng hình thức này."],
        "rows": rows,
        "counts": {"truong": len(rows)},
        "filters": {"regions": regions, "sectors": sectors, "schools": school_codes, "methods": codes},
    }


def _wants_school_methods(question: str, folded: str) -> bool:
    """Hỏi liệt kê phương thức tuyển sinh của một trường, không so điểm cá nhân."""
    if "phuong thuc" not in folded or _wants_schools_by_method(folded):
        return False
    if _mentioned_certs(question):
        return False
    if any(hint in folded for hint in ("cua toi", "cua em", "diem thi", "diem cua")):
        return False
    return True


def _answer_school_methods(
    root: str,
    schools: List[Dict[str, str]],
    school_codes: List[str],
    question: str,
    regions: Optional[List[str]] = None,
    sectors: Optional[List[str]] = None,
    admissions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    regions = regions or []
    sectors = sectors or []
    school_map = {school["code"]: school for school in schools}
    collected = _collected_schools(root, admissions)
    scope = []
    if sectors:
        scope.append("khối " + ", ".join(sectors))
    if regions:
        scope.append("miền " + ", ".join(regions))
    scope_text = ", ".join(scope)
    if not school_codes:
        summary = (
            f"Không thấy trường đã tổng hợp thuộc {scope_text}."
            if scope_text else
            "Hãy nêu tên hoặc mã trường, ví dụ: các phương thức tuyển sinh của Đại học Bách khoa Hà Nội?"
        )
        return {
            "ok": True,
            "kind": "catalog",
            "summary": summary,
            "notes": [],
            "rows": [],
            "counts": {},
        }
    payload = dataset_store.load_phuong_thuc(root) or {}
    groups = payload.get("nhom_phuong_thuc") if isinstance(payload.get("nhom_phuong_thuc"), dict) else {}
    asked_year = _named_year(question)
    rows = []
    missing = []
    for code in school_codes:
        records = [
            record for record in payload.get("records") or []
            if isinstance(record, dict) and str(record.get("ma_truong") or "").strip().upper() == code
        ]
        years = sorted({int(item) for item in (_num(record.get("nam")) for record in records) if item and 2000 <= item <= 2100})
        year = asked_year if asked_year in years else (years[-1] if years and asked_year is None else None)
        school = school_map.get(code, {})
        school_name = collected.get(code) or school.get("name") or code
        if year is None:
            if asked_year and years:
                missing.append(f"{school_name} ({code}) chưa có dữ liệu năm {asked_year}. Năm đang có: {', '.join(str(item) for item in years)}.")
            else:
                missing.append(f"{school_name} ({code}) chưa có phương thức tuyển sinh đã lưu.")
            continue
        year_groups = {}
        school_groups = groups.get(code) if isinstance(groups.get(code), dict) else {}
        raw_year = school_groups.get(str(year))
        if isinstance(raw_year, dict):
            year_groups = raw_year
        bucket: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        program_count = 0
        for record in records:
            record_year = _num(record.get("nam"))
            if record_year != year:
                continue
            program_count += 1
            for form in record.get("hinh_thuc") or []:
                if not isinstance(form, dict) or form.get("ap_dung") is False:
                    continue
                label = str(form.get("ten") or form.get("id") or "").strip()
                if not label:
                    continue
                current = bucket.get(label)
                if current is None:
                    current = {"combos": []}
                    bucket[label] = current
                    order.append(label)
                current["so_nganh"] = current.get("so_nganh", 0) + 1
                combos = _form_combos(form)
                current["combos"].append(tuple(combos))
        def sort_key(label: str) -> Tuple[int, int, str]:
            return (_OFFICIAL_ORDER.get(label, len(_OFFICIAL_ORDER)), order.index(label), label)
        for label in sorted(bucket, key=sort_key):
            item = bucket[label]
            if label in _OFFICIAL_BY_CODE:
                mapped = _official_label(label)
            else:
                codes = year_groups.get(label) or []
                if not isinstance(codes, list):
                    codes = []
                mapped = "; ".join(_official_label(str(code)) for code in codes if str(code).strip()) or "Chưa gắn mã phương thức chuẩn"
            variants = {combo for combo in item["combos"] if combo}
            if not variants:
                combo_text = "—"
            elif len(variants) == 1:
                combo_text = ", ".join(next(iter(variants)))
            else:
                combo_text = "Khác nhau theo ngành"
            rows.append({
                "ma_truong": code,
                "ten_truong": school_name,
                "ten": label,
                "ma_chuan": mapped,
                "so_nganh": item.get("so_nganh", 0),
                "to_hop": combo_text,
                "nam": year,
                "tong_nganh": program_count,
            })
    labels = []
    for code in school_codes:
        school = school_map.get(code, {})
        labels.append(collected.get(code) or school.get("name") or code)
    place = ", ".join(labels)
    if rows:
        year_text = " và ".join(str(year) for year in _unique(str(row["nam"]) for row in rows))
        school_count = len({row["ma_truong"] for row in rows})
        if scope_text and school_count > 1:
            summary = (
                f"Có {school_count} trường thuộc {scope_text} đã tổng hợp năm {year_text}. "
                "Bảng là phương thức từng trường đang dùng."
            )
        else:
            summary = f"{place} năm {year_text} có {len(rows)} phương thức tuyển sinh."
    elif scope_text and not missing:
        summary = f"Các trường thuộc {scope_text} chưa có phương thức tuyển sinh đã lưu."
    else:
        summary = " ".join(missing) if missing else f"{place} chưa có phương thức tuyển sinh đã lưu."
    notes = ["Lấy từ dữ liệu phương thức tuyển sinh đã thu thập. Mã chuẩn là mã dùng trên trang Phương thức tuyển sinh."]
    if asked_year is None and rows:
        notes.append("Câu hỏi không nêu năm, nên lấy năm mới nhất đang có.")
    if missing and rows:
        notes.extend(missing)
    return {
        "ok": True,
        "kind": "catalog",
        "summary": summary,
        "notes": notes,
        "rows": rows,
        "counts": {"phuong_thuc": len(rows)},
        "filters": {"schools": school_codes},
    }


def _wants_method_majors(question: str, folded: str) -> bool:
    """Hỏi ngành nào của một trường được xét bằng một phương thức, không so điểm cá nhân."""
    if not _mentioned_certs(question):
        return False
    if any(hint in folded for hint in ("cua toi", "cua em", "diem cua")):
        return False
    return "nganh" in folded and any(hint in folded for hint in ("nao", "cho phep", "ap dung", "xet tuyen"))


def _school_method_names(root: str, code: str) -> List[str]:
    payload = dataset_store.load_phuong_thuc(root) or {}
    names: List[str] = []
    for record in payload.get("records") or []:
        if str(record.get("ma_truong") or "").strip().upper() != code:
            continue
        for form in record.get("hinh_thuc") or []:
            if not isinstance(form, dict) or form.get("ap_dung") is False:
                continue
            label = str(form.get("ten") or form.get("id") or "").strip()
            if label and label not in names:
                names.append(label)
    return names


def _answer_method_majors(
    root: str,
    schools: List[Dict[str, str]],
    school_codes: List[str],
    question: str,
    admissions: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    cert = _mentioned_certs(question)[0]
    school_map = {school["code"]: school for school in schools}
    if not school_codes:
        return {
            "ok": True,
            "kind": "methods",
            "summary": f"Hãy nêu mã trường cần kiểm tra, ví dụ: ngành nào ở BVH cho phép xét tuyển bằng điểm {cert}?",
            "notes": [],
            "rows": [],
            "counts": {},
        }
    payload = dataset_store.load_phuong_thuc(root) or {}
    found: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for record in payload.get("records") or []:
        if not isinstance(record, dict):
            continue
        code = str(record.get("ma_truong") or "").strip().upper()
        if code not in school_codes:
            continue
        matched = []
        for form in record.get("hinh_thuc") or []:
            if not isinstance(form, dict) or form.get("ap_dung") is False:
                continue
            blob = " ".join(str(form.get(key) or "") for key in ("id", "ten", "mo_ta"))
            if _cert_in_text(blob, cert):
                label = str(form.get("ten") or form.get("id") or cert).strip()
                if label and label not in matched:
                    matched.append(label)
        if not matched:
            continue
        key = (code, str(record.get("ma_xet_tuyen") or "").strip().upper() or fold(record.get("ten_nganh")))
        school = school_map.get(code, {})
        found[key] = {
            "ma_truong": code,
            "ten_truong": school.get("name") or str(record.get("ten_truong") or code),
            "ten_nganh": str(record.get("ten_nganh") or record.get("ten_chuong_trinh") or "").strip(),
            "ma_xet_tuyen": str(record.get("ma_xet_tuyen") or "").strip().upper(),
            "phuong_thuc": matched,
            "diem_chuan": None,
            "nam": record.get("nam"),
        }
    scored = [item for item in _cert_score_rows(root, admissions, cert) if item["ma_truong"] in school_codes]
    latest_year = max((item["nam"] or 0 for item in scored), default=0)
    if latest_year:
        scored = [item for item in scored if item["nam"] in (None, latest_year)]
    for item in scored:
        code = item["ma_truong"]
        key = (code, item["ma_xet_tuyen"] or fold(item["ten_nganh"]))
        school = school_map.get(code, {})
        current = found.get(key)
        if current is None:
            current = {
                "ma_truong": code,
                "ten_truong": school.get("name") or code,
                "ten_nganh": item["ten_nganh"],
                "ma_xet_tuyen": item["ma_xet_tuyen"],
                "phuong_thuc": [],
                "diem_chuan": None,
                "nam": item["nam"],
            }
            found[key] = current
        method = item["phuong_thuc"]
        if method and method not in current["phuong_thuc"]:
            current["phuong_thuc"].append(method)
        if item["diem_chuan"] is not None:
            current["diem_chuan"] = item["diem_chuan"]
        if item["nam"]:
            current["nam"] = item["nam"]
    rows = sorted(found.values(), key=lambda row: (row["ten_truong"], row["ten_nganh"], row["ma_xet_tuyen"]))
    labels = []
    for code in school_codes:
        school = school_map.get(code)
        labels.append(f"{school['name']} ({code})" if school else code)
    place = ", ".join(labels)
    if rows:
        methods = _unique(method for row in rows for method in row["phuong_thuc"])
        summary = (
            f"{place} có xét tuyển bằng {cert}. "
            f"Có {len(rows)} ngành áp dụng phương thức {', '.join(methods)}."
        )
    else:
        available = []
        for code in school_codes:
            available.extend(_school_method_names(root, code))
        available = _unique(available)
        listed = ", ".join(available) if available else "chưa có phương thức đã lưu"
        summary = (
            f"{place} chưa ghi nhận phương thức xét tuyển bằng {cert}. "
            f"Phương thức đang có: {listed}."
        )
    notes = [
        f"Kiểm tra phương thức xét tuyển của trường và cột điểm có ghi {cert}.",
    ]
    return {
        "ok": True,
        "kind": "methods",
        "summary": summary,
        "notes": notes,
        "rows": rows[:40],
        "counts": {"nganh": len(rows)},
        "filters": {"schools": school_codes, "cert": cert},
    }


def _wants_cert_advice(question: str, folded: str) -> bool:
    if not _mentioned_certs(question):
        return False
    asks_admission = any(hint in folded for hint in (
        "du tuyen", "trung tuyen", "co the", "nen thi", "nen hoc", "xet tuyen",
        "truong nao", "nganh nao", "phu hop",
    ))
    if any(hint in folded for hint in _LOOKUP_HINTS) and not asks_admission:
        return False
    if asks_admission:
        return True
    return "truong" in folded and "nao" in folded


def _wants_profile_lookup(question: str, folded: str) -> bool:
    if _wants_cert_advice(question, folded) or not _mentioned_certs(question):
        return False
    return any(hint in folded for hint in _LOOKUP_HINTS) or "cua toi" in folded or "cua em" in folded or "ho so" in folded


def _answer_profile_lookup(profile: Dict[str, Any], question: str) -> Dict[str, Any]:
    cert = _mentioned_certs(question)[0]
    label, raw = _profile_cert_value(profile, cert)
    who = str(profile.get("ho_ten") or "").strip()
    owner = f" của {who}" if who else ""
    if not raw:
        summary = f"Hồ sơ{owner} chưa có điểm {cert}."
    else:
        summary = f"Điểm {label or cert} trong hồ sơ{owner} là {raw}."
    return {
        "ok": True,
        "kind": "profile",
        "summary": summary,
        "notes": ["Lấy từ hồ sơ cá nhân, không đối chiếu điểm chuẩn của trường."],
        "rows": [],
        "counts": {},
    }


def _schools_using_cert(root: str, cert: str) -> set:
    codes = set()
    payload = dataset_store.load_phuong_thuc(root) or {}
    for record in payload.get("records") or []:
        if not isinstance(record, dict):
            continue
        blob = json.dumps(record.get("hinh_thuc") or [], ensure_ascii=False)
        if _cert_in_text(blob, cert):
            code = str(record.get("ma_truong") or "").strip().upper()
            if code:
                codes.add(code)
    return codes


def _cert_score_rows(root: str, admissions: Optional[List[Dict[str, Any]]], cert: str) -> List[Dict[str, Any]]:
    if admissions is None:
        payload = dataset_store.load_admissions(root) or {}
        admissions = payload.get("admissions") or []
    rows = []
    for item in admissions:
        if not isinstance(item, dict) or not _cert_in_text(str(item.get("phuong_thuc") or ""), cert):
            continue
        raw = item.get("diem_chuan_ptxt")
        if raw is None:
            raw = item.get("diem_chuan")
        score = _num(raw)
        year = _num(item.get("nam"))
        rows.append({
            "ma_truong": str(item.get("ma_truong") or "").strip().upper(),
            "ten_nganh": str(item.get("ten_nganh") or "").strip(),
            "ma_xet_tuyen": str(item.get("ma_xet_tuyen") or "").strip().upper(),
            "phuong_thuc": str(item.get("phuong_thuc") or "").strip(),
            "diem_chuan": score,
            "nam": int(year) if year and 2000 <= year <= 2100 else None,
        })
    return rows


def _answer_cert_admission(
    root: str,
    profile: Dict[str, Any],
    schools: List[Dict[str, str]],
    regions: List[str],
    sectors: List[str],
    school_codes: List[str],
    major_keywords: List[str],
    major_label: str,
    question: str,
    admissions: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    cert = _mentioned_certs(question)[0]
    stated = _stated_cert_score(question, cert)
    label, raw = _profile_cert_value(profile, cert)
    profile_score = _num(raw)
    score = stated if stated is not None else profile_score
    scope = [school for school in schools if _school_ok(school, regions, sectors, school_codes)]
    scope_map = {school["code"]: school for school in scope}
    users = _schools_using_cert(root, cert)
    recorded = _cert_score_rows(root, admissions, cert)
    latest_year = max((row["nam"] or 0 for row in recorded), default=0)
    if latest_year:
        recorded = [row for row in recorded if row["nam"] in (None, latest_year)]
    by_school: Dict[str, List[Dict[str, Any]]] = {}
    for row in recorded:
        if row["ma_truong"] not in scope_map:
            continue
        if major_keywords and not _major_matches(row["ten_nganh"], major_keywords):
            continue
        by_school.setdefault(row["ma_truong"], []).append(row)
    method_only = [
        code for code in users
        if code in scope_map and code not in by_school and not major_keywords
    ]
    rows = []
    for code, items in by_school.items():
        school = scope_map[code]
        scored = [item for item in items if item["diem_chuan"] is not None]
        cutoffs = [item["diem_chuan"] for item in scored]
        reached = [item for item in scored if score is not None and item["diem_chuan"] <= score]
        low = min(cutoffs) if cutoffs else None
        high = max(cutoffs) if cutoffs else None
        if score is None or not cutoffs:
            status, status_label, need = "thieu", "Chưa có điểm chuẩn", None
        elif reached and len(reached) == len(scored):
            status, status_label, need = "dat", "Đủ các ngành đã có điểm", None
        elif reached:
            status, status_label = "dat", f"Đủ {len(reached)}/{len(scored)} ngành"
            need = round(high - score, 2) if high is not None and high > score else None
        else:
            status, status_label = "chua", "Thấp hơn điểm chuẩn"
            need = round(low - score, 2) if low is not None else None
        rows.append({
            "ma_truong": code,
            "ten_truong": school["name"],
            "loai_truong": school["loai_truong"],
            "khu_vuc": school["khu_vuc"],
            "diem_cua_toi": score,
            "diem_thap": low,
            "diem_cao": high,
            "du_nganh": len(reached),
            "tong_nganh": len(scored),
            "can_them": need,
            "trang_thai": status,
            "trang_thai_nhan": status_label,
        })
    for code in method_only:
        school = scope_map[code]
        rows.append({
            "ma_truong": code,
            "ten_truong": school["name"],
            "loai_truong": school["loai_truong"],
            "khu_vuc": school["khu_vuc"],
            "diem_cua_toi": score,
            "diem_thap": None,
            "diem_cao": None,
            "du_nganh": 0,
            "tong_nganh": 0,
            "can_them": None,
            "trang_thai": "thieu",
            "trang_thai_nhan": "Có xét, chưa có điểm chuẩn",
        })
    rows.sort(key=lambda row: (
        {"dat": 0, "chua": 1, "thieu": 2}.get(row["trang_thai"], 3),
        row["ten_truong"],
    ))
    scope_bits = []
    if major_label:
        scope_bits.append("ngành " + major_label)
    if sectors:
        scope_bits.append("loại trường " + ", ".join(sectors))
    if regions:
        scope_bits.append("miền " + ", ".join(regions))
    scope_text = ", ".join(scope_bits) if scope_bits else "các trường đã thu thập"
    if score is None:
        summary = f"Hồ sơ chưa có điểm {cert}, nên chưa đối chiếu được ngưỡng trúng tuyển."
    else:
        origin = "nêu trong câu hỏi" if stated is not None else "trong hồ sơ cá nhân"
        ready = [row for row in rows if row["trang_thai"] == "dat"]
        short = [row for row in rows if row["trang_thai"] == "chua"]
        summary = f"Điểm {label or cert} {origin} là {_fmt_vi(score)}."
        if ready:
            names = ", ".join(row["ten_truong"] for row in ready[:4])
            summary += f" Có {len(ready)} trường thuộc {scope_text} xét tuyển bằng {cert} và điểm này đủ để dự tuyển: {names}."
        elif short:
            nearest = min(short, key=lambda row: row["can_them"] if row["can_them"] is not None else 10**9)
            summary += (
                f" Chưa có trường nào thuộc {scope_text} có điểm chuẩn {cert} không cao hơn mốc này. "
                f"{nearest['ten_truong']} có mức thấp nhất {_fmt_vi(nearest['diem_thap'])}, "
                f"cần thêm khoảng {_fmt_vi(nearest['can_them'])} điểm để tiệm cận."
            )
        elif rows:
            summary += f" Có {len(rows)} trường thuộc {scope_text} xét {cert}, nhưng dữ liệu điểm chưa có mức chuẩn để so."
        else:
            summary += f" Trong dữ liệu đã thu thập, chưa thấy trường thuộc {scope_text} xét tuyển bằng {cert}."
    notes = [
        f"Trường được nhận là có xét {cert} khi phương thức tuyển sinh hoặc dữ liệu điểm ghi phương thức {cert}.",
        "Điểm chuẩn lấy mức đã lưu của phương thức đó, không quy về thang 30.",
    ]
    if stated is None and score is not None:
        notes.append("Điểm lấy từ hồ sơ cá nhân.")
    elif stated is not None:
        notes.append("Điểm lấy từ câu hỏi, không dùng hồ sơ cá nhân.")
    return {
        "ok": True,
        "kind": "certs",
        "summary": summary,
        "notes": notes,
        "rows": rows[:12],
        "counts": {"truong": len(rows), "dat": sum(1 for row in rows if row["trang_thai"] == "dat")},
        "filters": {
            "regions": regions,
            "sectors": sectors,
            "schools": school_codes,
            "combos": [],
            "major": major_label,
            "cert": cert,
        },
    }


def _norm_school_name(name: str) -> str:
    text = fold(name)
    text = re.sub(r"\bdhqg\b", "quoc gia", text)
    text = re.sub(r"\bhcm\b", "ho chi minh", text)
    text = re.sub(r"\bhn\b", "ha noi", text)
    text = re.sub(r"\b(truong|dai hoc|hoc vien|thanh pho|tp|co so|phan hieu)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _load_school_taxonomy(root: str) -> List[Dict[str, str]]:
    path = os.path.join(root, "data", "constants", "truong_hoc.md")
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    region_names = {"mien bac": "Bắc", "mien trung": "Trung", "mien nam": "Nam"}
    rows = []
    if not isinstance(payload, dict):
        return rows
    for region_label, sectors in payload.items():
        region = region_names.get(fold(region_label), "")
        if not isinstance(sectors, dict):
            continue
        for sector, names in sectors.items():
            if not isinstance(names, list):
                continue
            for name in names:
                label = str(name or "").strip()
                if label:
                    rows.append({
                        "name": label,
                        "norm": _norm_school_name(label),
                        "khu_vuc": region,
                        "loai_truong": str(sector).strip(),
                    })
    return rows


def _name_fit(left: str, right: str) -> int:
    if not left or not right:
        return 0
    if left == right:
        return 1000
    if left in right or right in left:
        return 800 - abs(len(left) - len(right))
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    overlap = left_tokens & right_tokens
    if len(overlap) < 2:
        return 0
    shorter = min(len(left_tokens), len(right_tokens))
    if len(overlap) / shorter < 0.67:
        return 0
    return 500 + len(overlap) * 10 - abs(len(left) - len(right))


def _taxonomy_by_code(root: str, collected: Dict[str, str]) -> Dict[str, Dict[str, str]]:
    catalog = _load_school_taxonomy(root)
    pairs = []
    for code, name in collected.items():
        school_norm = _norm_school_name(name)
        for item in catalog:
            fit = _name_fit(school_norm, item["norm"])
            if fit:
                pairs.append((fit, code, item))
    pairs.sort(key=lambda item: item[0], reverse=True)
    used_codes = set()
    used_names = set()
    assigned: Dict[str, Dict[str, str]] = {}
    for _fit, code, item in pairs:
        if code in used_codes or item["name"] in used_names:
            continue
        used_codes.add(code)
        used_names.add(item["name"])
        assigned[code] = {"khu_vuc": item["khu_vuc"], "loai_truong": item["loai_truong"]}
    return assigned


def _collected_schools(root: str, admissions: Optional[List[Dict[str, Any]]]) -> Dict[str, str]:
    found: Dict[str, str] = {}
    payload = dataset_store.load_phuong_thuc(root) or {}
    for record in payload.get("records") or []:
        if not isinstance(record, dict):
            continue
        code = str(record.get("ma_truong") or "").strip().upper()
        name = str(record.get("ten_truong") or "").strip()
        if code and name and code not in found:
            found[code] = name
    if admissions is None:
        saved = dataset_store.load_admissions(root) or {}
        admissions = saved.get("admissions") or []
    for record in admissions or []:
        if not isinstance(record, dict):
            continue
        code = str(record.get("ma_truong") or "").strip().upper()
        name = str(record.get("ten_truong") or "").strip()
        if code and name and code not in found:
            found[code] = name
    return found


def _wants_school_list(folded: str) -> bool:
    """Câu chỉ xin danh sách trường, không xét điểm cá nhân."""
    if any(hint in folded for hint in _ADVICE_HINTS):
        return False
    if "thong ke" in folded and "diem" in folded:
        return False
    if "truong" in folded and "thong ke" in folded:
        return True
    return "truong" in folded and any(hint in folded for hint in _LIST_HINTS)


def _school_ok(school: Dict[str, str], regions: List[str], sectors: List[str], codes: List[str]) -> bool:
    if codes and school["code"] not in codes:
        return False
    if sectors and school["loai_truong"] not in sectors:
        return False
    if regions and not any(region in school["khu_vuc"] for region in regions):
        return False
    return True


def _answer_school_list(
    root: str,
    regions: List[str],
    sectors: List[str],
    school_codes: List[str],
    admissions: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    collected = _collected_schools(root, admissions)
    taxonomy = _taxonomy_by_code(root, collected)
    rows = []
    for code, name in collected.items():
        if school_codes and code not in school_codes:
            continue
        meta = taxonomy.get(code, {})
        region = meta.get("khu_vuc") or ""
        sector = meta.get("loai_truong") or ""
        if regions and region not in regions:
            continue
        if sectors and sector not in sectors:
            continue
        rows.append({
            "ma_truong": code,
            "ten_truong": name,
            "khu_vuc": region,
            "loai_truong": sector,
        })
    rows.sort(key=lambda row: (row["khu_vuc"], row["loai_truong"], row["ten_truong"], row["ma_truong"]))
    scope = []
    if sectors:
        scope.append("khối " + ", ".join(sectors))
    if regions:
        scope.append("miền " + ", ".join(regions))
    scope_text = ", ".join(scope)
    where = f" thuộc {scope_text}" if scope_text else ""
    summary = (
        f"Có {len(rows)} trường đã tổng hợp{where}."
        if rows else
        f"Không thấy trường đã tổng hợp{where}."
    )
    return {
        "ok": True,
        "kind": "schools",
        "summary": summary,
        "notes": ["Chỉ tính trường đã có trong dữ liệu phương thức hoặc dữ liệu điểm. Khu vực và loại hình lấy từ danh mục trường học."],
        "rows": rows,
        "counts": {"truong": len(rows)},
        "filters": {
            "regions": regions,
            "sectors": sectors,
            "schools": school_codes,
            "combos": [],
            "major": "",
        },
    }


_CONVERT_METHODS = (
    ("v_act", r"v[\-\s]?act", "V-ACT (402)"),
    ("tsa", r"\btsa\b", "TSA (402)"),
    ("hsa", r"\bhsa\b", "HSA (402)"),
    ("spt", r"\bspt\b", "SPT (402)"),
    ("sat", r"\bsat\b", "SAT (415)"),
    ("act", r"\bact\b", "ACT (415)"),
    ("tai_nang", r"ho so nang luc|tai nang", "Hồ sơ năng lực (301)"),
    ("ket_hop", r"ket hop", "Kết hợp (410)"),
    ("hoc_ba", r"hoc ba", "Học bạ"),
    ("thpt", r"diem thi thpt|\bthpt\b", "Thi tốt nghiệp THPT (100)"),
)


def _conversion_methods(folded: str) -> List[str]:
    found = []
    masked = folded
    for key, pattern, _label in _CONVERT_METHODS:
        if not re.search(pattern, masked):
            continue
        found.append(key)
        masked = re.sub(pattern, " ", masked)
    return found


def _wants_score_conversion(folded: str) -> bool:
    if not any(hint in folded for hint in ("quy doi", "tuong duong", "bang bao nhieu", "bang may", "doi ra", "sang diem")):
        return False
    return len(_conversion_methods(folded)) >= 1


def _conversion_target(folded: str, methods: List[str]) -> str:
    match = re.search(r"(?:bang|sang|ra|thanh|tuong duong)\b.{0,48}", folded)
    tail = match.group(0) if match else ""
    for key, pattern, _label in _CONVERT_METHODS:
        if key in methods and re.search(pattern, tail):
            return key
    if "thpt" in methods and len(methods) > 1:
        return "thpt"
    return ""


def _load_percentile_bands(root: str, code: str) -> List[Dict[str, Any]]:
    path = os.path.join(root, "data", "constants", "quy-che-quy-doi-diem", f"{code.lower()}-2026.md")
    try:
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
    except OSError:
        return []
    mark = "<!-- điểm thưởng -->"
    if mark not in source:
        return []
    try:
        data = json.loads(source.split(mark, 1)[1].strip())
    except json.JSONDecodeError:
        return []
    bands = data.get("bang_quy_doi") if isinstance(data, dict) else None
    return [item for item in bands if isinstance(item, dict)] if isinstance(bands, list) else []


def _band_bounds(band: Dict[str, Any], field: str) -> Optional[Tuple[float, float]]:
    node = band.get(field)
    if not isinstance(node, dict):
        return None
    try:
        low = float(node["min"])
        high = float(node["max"])
    except (KeyError, TypeError, ValueError):
        return None
    if low > high:
        low, high = high, low
    return low, high


def _match_percentile_band(
    score: float,
    bands: List[Dict[str, Any]],
    field: str,
) -> Optional[Tuple[Dict[str, Any], float, float]]:
    usable = []
    for band in bands:
        bounds = _band_bounds(band, field)
        if bounds:
            usable.append((band, bounds[0], bounds[1]))
    if not usable:
        return None
    ceiling = max(high for _band, _low, high in usable)
    for band, low, high in usable:
        if low - 1e-9 <= score < high - 1e-9:
            return band, low, high
        if abs(score - ceiling) <= 1e-9 and abs(high - ceiling) <= 1e-9 and low - 1e-9 <= score:
            return band, low, high
    return None


def _interpolate(score: float, left: float, right: float, low: float, high: float) -> float:
    width = right - left
    raw = high if abs(width) < 1e-12 else low + (score - left) / width * (high - low)
    return round(raw + 0.0, 2)


def _answer_score_conversion(
    root: str,
    profile: Dict[str, Any],
    schools: List[Dict[str, str]],
    school_codes: List[str],
    question: str,
    folded: str,
) -> Dict[str, Any]:
    methods = _conversion_methods(folded)
    target = _conversion_target(folded, methods)
    source = next((key for key in methods if key != target), methods[0] if methods else "")
    labels = {key: label for key, _pattern, label in _CONVERT_METHODS}
    school_map = {school["code"]: school for school in schools}
    collected = _collected_schools(root, None)
    if not school_codes:
        return {
            "ok": True,
            "kind": "convert",
            "summary": "Hãy nêu trường cần quy đổi, ví dụ: 1500 điểm SAT bằng bao nhiêu điểm thi THPT ở BVH?",
            "notes": [],
            "rows": [],
            "counts": {},
        }
    code = school_codes[0]
    school_name = collected.get(code) or (school_map.get(code) or {}).get("name") or code
    cert_name = {"sat": "SAT", "act": "ACT", "tsa": "TSA", "hsa": "HSA", "v_act": "V-ACT", "spt": "SPT"}.get(source, "")
    stated = _stated_cert_score(question, cert_name) if cert_name else None
    if stated is None and source == "thpt":
        stated = _parse_stated_total(question)
    profile_score = None
    profile_label = ""
    if cert_name:
        profile_label, raw = _profile_cert_value(profile, cert_name)
        profile_score = _num(raw)
    score = stated if stated is not None else profile_score
    source_label = labels.get(source, source)
    if score is None:
        return {
            "ok": True,
            "kind": "convert",
            "summary": f"Chưa có điểm {source_label} trong câu hỏi hoặc hồ sơ để quy đổi tại {school_name}.",
            "notes": [],
            "rows": [],
            "counts": {},
        }
    bands = _load_percentile_bands(root, code)
    if not bands or not any(_band_bounds(band, source) for band in bands):
        return {
            "ok": True,
            "kind": "convert",
            "summary": f"Chưa có bảng quy đổi phân vị cho {source_label} tại {school_name} ({code}).",
            "notes": ["Bảng lấy từ quy chế quy đổi điểm đã lưu, không suy từ điểm chuẩn."],
            "rows": [],
            "counts": {},
        }
    matched = _match_percentile_band(score, bands, source)
    if matched is None:
        lows = [bounds[0] for band in bands if (bounds := _band_bounds(band, source))]
        highs = [bounds[1] for band in bands if (bounds := _band_bounds(band, source))]
        span = f" từ {_fmt_vi(min(lows))} đến {_fmt_vi(max(highs))}" if lows and highs else ""
        return {
            "ok": True,
            "kind": "convert",
            "summary": f"{_fmt_vi(score)} điểm {source_label} không nằm trong bảng quy đổi của {school_name}{span}.",
            "notes": [],
            "rows": [],
            "counts": {},
        }
    band, left, right = matched
    present = {
        key for key, _pattern, _label in _CONVERT_METHODS
        if any(_band_bounds(item, key) for item in bands)
    }
    order = [source]
    if target and target != source and target in present:
        order.append(target)
    order.extend(key for key, _pattern, _label in _CONVERT_METHODS if key in present and key not in order)
    rows = []
    converted: Dict[str, float] = {}
    for key in order:
        label = labels.get(key, key)
        if key == source:
            rows.append({
                "phuong_thuc": label,
                "diem": _fmt_vi(score),
                "khoang": f"{_fmt_vi(left)}–{_fmt_vi(right)}",
                "vai_tro": "Gốc",
            })
            continue
        bounds = _band_bounds(band, key)
        if not bounds:
            rows.append({
                "phuong_thuc": label,
                "diem": "Không quy đổi",
                "khoang": "—",
                "vai_tro": "Đích" if key == target else "",
            })
            continue
        value = _interpolate(score, left, right, bounds[0], bounds[1])
        converted[key] = value
        rows.append({
            "phuong_thuc": label,
            "diem": _fmt_vi(value),
            "khoang": f"{_fmt_vi(bounds[0])}–{_fmt_vi(bounds[1])}",
            "vai_tro": "Đích" if key == target else "",
        })
    if target and target in converted:
        summary = (
            f"{_fmt_vi(score)} điểm {source_label} tại {school_name} ({code}) "
            f"tương đương {_fmt_vi(converted[target])} điểm {labels[target]}."
        )
    elif target:
        summary = (
            f"Khoảng {_fmt_vi(left)}–{_fmt_vi(right)} của {source_label} tại {school_name} "
            f"không quy đổi sang {labels.get(target, target)}."
        )
    else:
        summary = f"{_fmt_vi(score)} điểm {source_label} tại {school_name} ({code}) quy đổi như bảng."
    notes = [
        "Nội suy trong cùng khoảng phân vị: y = c + ((x − a) / (b − a)) × (d − c), với a ≤ x < b.",
        "Bảng lấy từ quy chế quy đổi điểm đã lưu.",
    ]
    if stated is None:
        owner = f" của {profile.get('ho_ten')}" if profile.get("ho_ten") else ""
        notes.append(f"Điểm {profile_label or source_label} lấy từ hồ sơ{owner}.")
    else:
        notes.append("Điểm lấy từ câu hỏi, không dùng hồ sơ cá nhân.")
    return {
        "ok": True,
        "kind": "convert",
        "summary": summary,
        "notes": notes,
        "rows": rows,
        "counts": {"phuong_thuc": len(rows)},
        "filters": {"schools": [code], "source": source, "target": target},
    }


def answer_advisor(
    root: str,
    payload: Dict[str, Any],
    admissions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    question = str(payload.get("question") or "").strip()
    if not question:
        return {"ok": False, "error": "Hãy nhập câu hỏi."}
    options = advisor_options(root)
    schools = options["schools"]
    school_map = {row["code"]: row for row in schools}
    catalog = {item["code"]: item for item in _load_combos(root)}
    folded = fold(question)
    regions = _as_list(payload.get("regions"))
    sectors = _as_list(payload.get("sectors"))
    school_codes = [code.upper() for code in _as_list(payload.get("schools"))]
    combos = [code.upper() for code in _as_list(payload.get("combos")) if code.upper() in catalog]
    major_label, major_keywords = _extract_major(question, folded)
    asked_combos = _mentioned_combos(question, catalog)
    sector_text = folded
    for keyword in major_keywords:
        sector_text = sector_text.replace(keyword, " ")
    asked_sectors = _mentioned_sectors(sector_text)
    asked_regions = _mentioned_regions(folded)
    asked_schools = _mentioned_schools(folded, schools)
    if asked_combos:
        combos = asked_combos
    if asked_sectors:
        sectors = asked_sectors
    if asked_regions:
        regions = asked_regions
    if asked_schools:
        school_codes = asked_schools
    profile = dataset_store.load_profile(root)
    if _wants_score_conversion(folded):
        return _answer_score_conversion(root, profile, schools, school_codes, question, folded)
    if _wants_schools_by_method(folded):
        return _answer_schools_by_method(
            root, schools, regions, sectors, school_codes, question, folded,
        )
    if _wants_school_methods(question, folded):
        codes = _schools_in_scope(root, regions, sectors, school_codes, admissions) if regions or sectors else school_codes
        return _answer_school_methods(root, schools, codes, question, regions, sectors, admissions)
    if _wants_method_majors(question, folded):
        return _answer_method_majors(root, schools, school_codes, question, admissions)
    if _wants_cert_advice(question, folded):
        return _answer_cert_admission(
            root, profile, schools, regions, sectors, school_codes,
            major_keywords, major_label, question, admissions,
        )
    if _wants_profile_lookup(question, folded):
        return _answer_profile_lookup(profile, question)
    if _wants_score_stats(folded):
        return _answer_score_stats(
            root, schools, regions, sectors, school_codes,
            major_label, major_keywords, question, admissions,
        )
    if _wants_school_list(folded):
        return _answer_school_list(root, regions, sectors, school_codes, admissions)
    source = "hoc_ba" if "hoc ba" in folded and "thpt" not in folded else str(payload.get("score_source") or "thi")
    stated_total = _parse_stated_total(question)
    stated_subjects = {} if stated_total is not None else _parse_stated_subjects(question)
    if stated_total is not None or stated_subjects:
        scores = stated_subjects
    else:
        incoming = _parse_scores(payload.get("scores"))
        if incoming:
            scores = incoming
        elif source == "hoc_ba":
            scores = _hoc_ba_scores(profile)
        else:
            scores = _profile_exam_scores(profile)
    if not combos:
        combos = [
            item["code"] for item in catalog.values()
            if item["ready"] and all(key in scores for key in item["keys"])
        ]
        if not combos or (not sectors and not school_codes and not asked_combos and not major_keywords):
            ready = ", ".join(combos[:8]) if combos else "chưa đủ môn"
            return {
                "ok": True,
                "summary": (
                    "Mình cần một tổ hợp hoặc một loại trường để đối chiếu. "
                    "Hãy chọn tổ hợp (ví dụ A00) hoặc hỏi cụ thể như: "
                    "với điểm thi THPT khối A00, ngành nào của khối kỹ thuật có thể dự tuyển."
                ),
                "notes": [f"Các tổ hợp đã đủ điểm trong hồ sơ: {ready}."],
                "cards": [],
                "rows": [],
                "counts": {},
            }
    chosen = [catalog[code] for code in combos if code in catalog]
    cards = []
    region = str(profile.get("khu_vuc") or "")
    obj = str(profile.get("doi_tuong") or "")
    for combo in chosen:
        if stated_total is not None:
            cards.append(_stated_card(combo, stated_total))
            continue
        card = _combo_total(combo, scores)
        if card["raw"] is not None and not stated_subjects:
            card["bonus"] = _priority(card["raw"], region, obj)
            card["total"] = round(card["raw"] + card["bonus"], 2)
        elif card["raw"] is not None:
            card["bonus"] = 0
            card["total"] = card["raw"]
            card["from_question"] = True
        else:
            card["bonus"] = 0
            card["total"] = None
        cards.append(card)
    usable = [card for card in cards if card["total"] is not None]
    if not usable:
        missing = "; ".join(
            f"{card['code']} thiếu {', '.join(card['missing'])}" for card in cards if card["missing"]
        )
        return {
            "ok": True,
            "summary": "Chưa tính được tổng điểm vì còn môn chưa có điểm.",
            "notes": [missing] if missing else [],
            "cards": cards,
            "rows": [],
            "counts": {},
        }
    best_by_code = {card["code"]: card for card in usable}
    scope = [school for school in schools if _school_ok(school, regions, sectors, school_codes)]
    scope_codes = {school["code"] for school in scope}
    programs = [
        program for program in _programs(root, set(school_map))
        if program["ma_truong"] in scope_codes
    ]
    cutoffs = _cutoffs(root, admissions)
    pool = _score_program_rows(programs, usable, catalog, school_map, cutoffs)
    rows = [row for row in pool if _major_matches(row.get("ten_nganh") or "", major_keywords)]
    counts = {key: sum(1 for row in rows if row["trang_thai"] == key) for key in ("dat", "sat", "chua", "thang", "thieu")}
    ranked = _top_reachable(rows)
    counts["dat"] = len(ranked)
    near: List[Dict[str, Any]] = []
    target = None
    if not ranked:
        named_near = _nearest_short(rows, 5)
        if major_keywords and named_near:
            target = named_near[0]
        near = _nearest_short(pool, 5) if major_keywords else named_near
    visible = ranked[:5] if ranked else near
    summary, notes = _summarize(
        question, usable, regions, sectors, school_codes, counts, len(scope), region, obj,
        major_label=major_label,
        shown=len(visible),
        target=target,
        suggesting=not ranked and bool(near),
    )
    return {
        "ok": True,
        "summary": summary,
        "notes": notes,
        "cards": cards,
        "rows": visible,
        "counts": counts,
        "filters": {
            "regions": regions,
            "sectors": sectors,
            "schools": school_codes,
            "combos": [card["code"] for card in usable],
            "major": major_label,
        },
    }


def _score_program_rows(
    programs: List[Dict[str, Any]],
    usable: List[Dict[str, Any]],
    catalog: Dict[str, Dict[str, Any]],
    school_map: Dict[str, Dict[str, str]],
    cutoffs: Dict[Tuple[str, str, str], Dict[str, Any]],
) -> List[Dict[str, Any]]:
    rows = []
    seen = set()
    for program in programs:
        matched = [card for card in usable if _program_matches(program, catalog[card["code"]])]
        if not matched:
            continue
        card = max(matched, key=lambda item: item["total"])
        identity = (program["ma_truong"], program.get("ma_xet_tuyen") or fold(program["ten_nganh"]), card["code"])
        if identity in seen:
            continue
        seen.add(identity)
        school = school_map.get(program["ma_truong"], {})
        cutoff = _lookup_cutoff(cutoffs, program)
        score = cutoff.get("diem_chuan") if cutoff else None
        gap = round(card["total"] - score, 2) if score is not None else None
        if score is None:
            status = "thieu"
        elif score > 30:
            status = "thang"
        elif gap is not None and gap >= 0:
            status = "dat"
        elif gap is not None and gap >= -1:
            status = "sat"
        else:
            status = "chua"
        rows.append({
            "ma_truong": program["ma_truong"],
            "ten_truong": school.get("name") or program["ma_truong"],
            "loai_truong": school.get("loai_truong") or "",
            "khu_vuc": school.get("khu_vuc") or "",
            "ten_nganh": program["ten_nganh"],
            "ma_xet_tuyen": program.get("ma_xet_tuyen") or "",
            "to_hop": card["code"],
            "phuong_thuc": program.get("phuong_thuc") or [],
            "diem_chuan": score,
            "nam": (cutoff or {}).get("nam") or program.get("nam"),
            "diem_cua_toi": card["total"],
            "chenh": gap,
            "trang_thai": status,
            "trang_thai_nhan": _STATUS_LABEL[status],
        })
    return rows


def _best_by_major(rows: List[Dict[str, Any]], allowed: Optional[set] = None) -> List[Dict[str, Any]]:
    """Mỗi tên ngành một dòng, giữ bản có mức chênh cao nhất."""
    best: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        if row.get("chenh") is None or row.get("trang_thai") in ("thang", "thieu"):
            continue
        if allowed is not None and row.get("trang_thai") not in allowed:
            continue
        key = (row.get("ma_truong") or "", fold(row.get("ten_nganh")))
        current = best.get(key)
        if current is None or float(row["chenh"]) > float(current["chenh"]):
            best[key] = row
    return sorted(best.values(), key=lambda row: (-float(row["chenh"]), row.get("ten_truong") or "", row.get("ten_nganh") or ""))


def _top_reachable(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ngành đủ điểm, mỗi tên ngành một dòng, xếp mức chênh giảm dần."""
    return _best_by_major(rows, {"dat"})


def _nearest_short(rows: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    """Ngành chưa đủ điểm, sát mốc nhất trước."""
    return _best_by_major(rows, {"sat", "chua"})[:limit]


def _fmt(value: Optional[float]) -> str:
    if value is None:
        return "—"
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text


def _summarize(
    question: str,
    cards: List[Dict[str, Any]],
    regions: List[str],
    sectors: List[str],
    schools: List[str],
    counts: Dict[str, int],
    scope_count: int,
    region: str,
    obj: str,
    major_label: str = "",
    shown: int = 0,
    target: Optional[Dict[str, Any]] = None,
    suggesting: bool = False,
) -> Tuple[str, List[str]]:
    from_question = any(card.get("stated") or card.get("from_question") for card in cards)
    bits = []
    for card in cards[:4]:
        if card.get("stated"):
            bits.append(f"{card['code']} = {_fmt(card['total'])} điểm nêu trong câu hỏi")
            continue
        detail = ", ".join(f"{part['label']} {_fmt(part['score'])}" for part in card["parts"])
        extra = ""
        if card["bonus"]:
            extra = f", cộng ưu tiên {_fmt(card['bonus'])} thành {_fmt(card['total'])}"
        bits.append(f"{card['code']} ({detail}) = {_fmt(card['raw'])}{extra}")
    scope_bits = []
    if major_label:
        scope_bits.append("ngành " + major_label)
    if sectors:
        scope_bits.append("loại trường " + ", ".join(sectors))
    if regions:
        scope_bits.append("miền " + ", ".join(regions))
    if schools:
        scope_bits.append("trường " + ", ".join(schools))
    scope_text = ", ".join(scope_bits) if scope_bits else f"{scope_count} trường trong danh bạ"
    reached = counts.get("dat", 0)
    lead = f"Điểm thi THPT đang dùng: {'; '.join(bits)}."
    if reached:
        pick = f"Dưới đây là {shown} ngành có mức chênh so với điểm chuẩn lớn nhất." if shown < reached else "Dưới đây là các ngành đó."
        summary = f"{lead} Trong phạm vi {scope_text}, có {reached} ngành có thể dự tuyển. {pick}"
    elif suggesting and target and target.get("chenh") is not None:
        need = _fmt(round(-float(target["chenh"]), 2))
        code = f" ({target['ma_xet_tuyen']})" if target.get("ma_xet_tuyen") else ""
        summary = (
            f"{lead} Ngành {target.get('ten_nganh')}{code} có điểm chuẩn {_fmt(target.get('diem_chuan'))}, "
            f"cao hơn mốc {need} điểm. Cần thêm khoảng {need} điểm để tiệm cận ngành này. "
            f"Có thể tham khảo {shown} ngành có điểm chuẩn sát mốc hơn ở bảng dưới."
        )
    elif suggesting and major_label and not any(counts.values()):
        summary = (
            f"{lead} Không thấy ngành {major_label} trong phạm vi {scope_text}. "
            f"Có thể tham khảo {shown} ngành có điểm chuẩn sát mốc nhất ở bảng dưới."
        )
    elif suggesting:
        summary = (
            f"{lead} Trong phạm vi {scope_text}, chưa có ngành nào đủ điểm. "
            f"Dưới đây là {shown} ngành có điểm chuẩn sát mốc nhất để tham khảo."
        )
    else:
        summary = (
            f"{lead} Trong phạm vi {scope_text}, chưa có ngành nào có điểm chuẩn thi THPT không cao hơn điểm của bạn."
        )
    if suggesting:
        notes = [
            "Bảng là ngành chưa đủ điểm, xếp theo độ sát mốc. Chênh âm là số điểm còn thiếu.",
            "Điểm chuẩn lấy năm mới nhất đã lưu, phương thức thi THPT, thang khoảng 30.",
        ]
    else:
        notes = [
            "Chỉ liệt kê ngành đã đủ điểm. Mức chênh là điểm của bạn trừ điểm chuẩn.",
            "Điểm chuẩn lấy năm mới nhất đã lưu, phương thức thi THPT, thang khoảng 30.",
        ]
    if from_question:
        notes.append("Điểm lấy từ câu hỏi, không dùng hồ sơ cá nhân và không cộng điểm ưu tiên.")
    if re.search(r"\bkhoa\b", fold(question)) and "khoa hoc" not in fold(question):
        notes.append("Dữ liệu điểm đang theo ngành và chương trình đào tạo, chưa có danh sách khoa.")
    elif region or obj:
        notes.append(
            "Ưu tiên lấy từ hồ sơ"
            + (f" {region}" if region else "")
            + (f", đối tượng {obj}" if obj else "")
            + "."
        )
    if not any(counts.values()) and not suggesting:
        summary = (
            f"Điểm thi THPT đang dùng: {'; '.join(bits)}. "
            f"Chưa thấy ngành nào trong phạm vi {scope_text} ghi nhận tổ hợp đã chọn "
            "trong dữ liệu phương thức tuyển sinh."
        )
    return summary, notes
