# -*- coding: utf-8 -*-
"""
Lưu / nạp dataset dùng lại trên UI (không cần thu thập lại).

Cấu trúc:
  data/datasets/admissions_latest.json
  data/datasets/quy_doi_latest.json
  data/datasets/admissions/admissions_YYYYMMDD_HHMMSS.json  (bản snapshot)
  data/datasets/quy_doi/quy_doi_YYYYMMDD_HHMMSS.json
  data/output/danh_sach_ma_truong.json  (bước 1 — đã có sẵn)
  data/output/*.xlsx                    (file tải Excel lịch sử)
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional


def _ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def datasets_dir(root: str) -> str:
    return _ensure_dir(os.path.join(root, "data", "datasets"))


def output_dir(root: str) -> str:
    return _ensure_dir(os.path.join(root, "data", "output"))


def _atomic_write_json(path: str, data: Any) -> None:
    _ensure_dir(os.path.dirname(path))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def profile_path(root: str) -> str:
    return os.path.join(datasets_dir(root), "ca_nhan.json")


_PROFILE_REGIONS = {"KV1", "KV2-NT", "KV2", "KV3"}
_PROFILE_OBJECTS = {"01", "02", "03", "04", "05", "06"}
_PROFILE_GRADES = ("10", "11", "12")
PROFILE_SUBJECTS = [
    {"key": "toan", "label": "Toán"},
    {"key": "van", "label": "Ngữ văn"},
    {"key": "anh", "label": "Tiếng Anh"},
    {"key": "nga", "label": "Tiếng Nga"},
    {"key": "phap", "label": "Tiếng Pháp"},
    {"key": "trung", "label": "Tiếng Trung"},
    {"key": "duc", "label": "Tiếng Đức"},
    {"key": "nhat", "label": "Tiếng Nhật"},
    {"key": "han", "label": "Tiếng Hàn"},
    {"key": "ly", "label": "Vật lí"},
    {"key": "hoa", "label": "Hóa học"},
    {"key": "sinh", "label": "Sinh học"},
    {"key": "su", "label": "Lịch sử"},
    {"key": "dia", "label": "Địa lí"},
    {"key": "gdktpl", "label": "Giáo dục kinh tế và pháp luật"},
    {"key": "tin", "label": "Tin học"},
    {"key": "cn", "label": "Công nghệ"},
]
_PROFILE_SUBJECT_KEYS = {item["key"] for item in PROFILE_SUBJECTS}


def _empty_hoc_ba() -> Dict[str, Dict[str, str]]:
    return {grade: {} for grade in _PROFILE_GRADES}


def _subject_score(value: Any) -> str:
    raw = str(value or "").strip().replace(",", ".")
    if not raw or any(ch in raw for ch in "\r\n\t"):
        return ""
    try:
        number = float(raw)
    except ValueError:
        return ""
    if number < 0 or number > 10:
        return ""
    return f"{number:.2f}".rstrip("0").rstrip(".") or "0"


def _subject_scores(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    scores: Dict[str, str] = {}
    for key, value in raw.items():
        subject = str(key or "").strip()
        score = _subject_score(value)
        if subject in _PROFILE_SUBJECT_KEYS and score:
            scores[subject] = score
    return scores


def _hoc_ba_scores(raw: Any) -> Dict[str, Dict[str, str]]:
    grades = _empty_hoc_ba()
    if not isinstance(raw, dict):
        return grades
    for grade in _PROFILE_GRADES:
        grades[grade] = _subject_scores(raw.get(grade))
    return grades


def _empty_profile() -> Dict[str, Any]:
    return {
        "ho_ten": "",
        "ngay_sinh": "",
        "gioi_tinh": "",
        "dia_chi": "",
        "khu_vuc": "",
        "doi_tuong": "",
        "diem_thi_thu": {},
        "hoc_ba": _empty_hoc_ba(),
        "chung_chi": {},
    }


def load_profile(root: str) -> Dict[str, Any]:
    data = load_json(profile_path(root))
    if not isinstance(data, dict):
        return _empty_profile()
    certs = data.get("chung_chi") if isinstance(data.get("chung_chi"), dict) else {}
    gender = str(data.get("gioi_tinh") or "").strip()
    region = str(data.get("khu_vuc") or "").strip()
    obj = str(data.get("doi_tuong") or "").strip()
    return {
        "ho_ten": str(data.get("ho_ten") or ""),
        "ngay_sinh": str(data.get("ngay_sinh") or ""),
        "gioi_tinh": gender if gender in {"Nam", "Nữ", "Khác"} else "",
        "dia_chi": str(data.get("dia_chi") or ""),
        "khu_vuc": region if region in _PROFILE_REGIONS else "",
        "doi_tuong": obj if obj in _PROFILE_OBJECTS else "",
        "diem_thi_thu": _subject_scores(data.get("diem_thi_thu")),
        "hoc_ba": _hoc_ba_scores(data.get("hoc_ba")),
        "chung_chi": {str(k): str(v) for k, v in certs.items() if str(v).strip()},
    }


def save_profile(root: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    gender = str(profile.get("gioi_tinh") or "").strip()
    region = str(profile.get("khu_vuc") or "").strip()
    obj = str(profile.get("doi_tuong") or "").strip()
    existing = load_json(profile_path(root))
    existing = existing if isinstance(existing, dict) else {}
    cleaned = {
        "ho_ten": str(profile.get("ho_ten") or "").strip()[:120],
        "ngay_sinh": str(profile.get("ngay_sinh") or "").strip()[:10],
        "gioi_tinh": gender if gender in {"Nam", "Nữ", "Khác"} else "",
        "dia_chi": str(profile.get("dia_chi") or "").strip()[:300],
        "khu_vuc": region if region in _PROFILE_REGIONS else "",
        "doi_tuong": obj if obj in _PROFILE_OBJECTS else "",
        "diem_thi_thu": _subject_scores(profile.get("diem_thi_thu")) if "diem_thi_thu" in profile else _subject_scores(existing.get("diem_thi_thu")),
        "hoc_ba": _hoc_ba_scores(profile.get("hoc_ba")) if "hoc_ba" in profile else _hoc_ba_scores(existing.get("hoc_ba")),
        "chung_chi": {},
    }
    raw_certs = profile.get("chung_chi") if isinstance(profile.get("chung_chi"), dict) else {}
    for key, value in raw_certs.items():
        name = re.sub(r"\s+", " ", str(key or "").strip())[:40]
        raw = str(value or "").strip()
        if not name or not raw or any(ch in raw for ch in "\r\n\t"):
            continue
        score = raw.replace(",", ".")
        try:
            number = float(score)
        except ValueError:
            number = None
        if number is not None:
            if number < 0 or number > 10000:
                continue
            cleaned["chung_chi"][name] = score
            continue
        cleaned["chung_chi"][name] = raw[:80]
    _atomic_write_json(profile_path(root), cleaned)
    return cleaned


def load_json(path: str) -> Optional[Any]:
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def format_size(num_bytes: int) -> str:
    """Hiển thị kích thước: B / KB / MB / GB tùy ngưỡng."""
    try:
        n = float(num_bytes or 0)
    except (TypeError, ValueError):
        n = 0.0
    if n < 1024:
        return f"{int(n)} B"
    if n < 1024 * 1024:
        return f"{round(n / 1024, 1)} KB"
    if n < 1024 * 1024 * 1024:
        return f"{round(n / (1024 * 1024), 1)} MB"
    return f"{round(n / (1024 * 1024 * 1024), 2)} GB"


def file_meta(path: str) -> Optional[Dict[str, Any]]:
    if not path or not os.path.isfile(path):
        return None
    st = os.stat(path)
    size = st.st_size
    return {
        "name": os.path.basename(path),
        "path": path,
        "size": size,
        "size_kb": round(size / 1024, 1),
        "size_label": format_size(size),
        "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "mtime_ts": int(st.st_mtime),
    }


def admissions_latest_path(root: str) -> str:
    return os.path.join(datasets_dir(root), "admissions_latest.json")


def quy_doi_latest_path(root: str) -> str:
    return os.path.join(datasets_dir(root), "quy_doi_latest.json")


def phuong_thuc_latest_path(root: str) -> str:
    return os.path.join(datasets_dir(root), "phuong_thuc_latest.json")


def schools_json_path(root: str) -> str:
    return os.path.join(output_dir(root), "danh_sach_ma_truong.json")


def save_admissions(root: str, payload: Dict[str, Any]) -> Dict[str, str]:
    """Lưu snapshot + bản latest. Trả về {latest, snapshot}."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    data = dict(payload)
    data["_saved_at"] = datetime.now().isoformat(timespec="seconds")
    data["_kind"] = "admissions"

    latest = admissions_latest_path(root)
    snap_dir = _ensure_dir(os.path.join(datasets_dir(root), "admissions"))
    snap = os.path.join(snap_dir, f"admissions_{ts}.json")
    _atomic_write_json(latest, data)
    _atomic_write_json(snap, data)
    return {"latest": latest, "snapshot": snap}


def load_admissions(root: str, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return load_json(path or admissions_latest_path(root))


def save_quy_doi(
    root: str,
    payload: Dict[str, Any],
    snapshot_ts: Optional[str] = None,
) -> Dict[str, str]:
    """
    Lưu latest + snapshot JSON.
    snapshot_ts: tuỳ chọn dạng YYYYMMDD_HHMMSS (vd. khớp tên file Excel tái sử dụng).
    """
    ts = (snapshot_ts or "").strip() or datetime.now().strftime("%Y%m%d_%H%M%S")
    if not re.fullmatch(r"\d{8}_\d{6}", ts):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    data = dict(payload)
    data["_saved_at"] = datetime.now().isoformat(timespec="seconds")
    data["_kind"] = "quy_doi"

    latest = quy_doi_latest_path(root)
    snap_dir = _ensure_dir(os.path.join(datasets_dir(root), "quy_doi"))
    snap = os.path.join(snap_dir, f"quy_doi_{ts}.json")
    _atomic_write_json(latest, data)
    _atomic_write_json(snap, data)
    return {"latest": latest, "snapshot": snap, "snapshot_name": os.path.basename(snap)}


def load_quy_doi(root: str, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return load_json(path or quy_doi_latest_path(root))


def save_phuong_thuc(root: str, payload: Dict[str, Any]) -> Dict[str, str]:
    """Lưu danh mục phương thức tuyển sinh đã nhận diện theo trường và năm."""
    data = dict(payload)
    data["_saved_at"] = datetime.now().isoformat(timespec="seconds")
    data["_kind"] = "phuong_thuc"
    latest = phuong_thuc_latest_path(root)
    _atomic_write_json(latest, data)
    return {"latest": latest}


def load_phuong_thuc(root: str, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return load_json(path or phuong_thuc_latest_path(root))


def load_schools(root: str) -> Optional[Dict[str, Any]]:
    return load_json(schools_json_path(root))


def _list_glob(pattern: str, limit: int = 40) -> List[Dict[str, Any]]:
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    out: List[Dict[str, Any]] = []
    for p in files[:limit]:
        meta = file_meta(p)
        if meta:
            out.append(meta)
    return out


def summarize_admissions(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not payload:
        return {"has_data": False}
    admissions = payload.get("admissions") or []
    codes = payload.get("codes") or []
    if not codes:
        codes = sorted({
            (r.get("ma_truong") or "").upper()
            for r in admissions if r.get("ma_truong")
        })
    return {
        "has_data": bool(admissions),
        "schools": len(codes),
        "codes": codes,
        "admissions": len(admissions),
        "conversions": len(payload.get("conversions") or []),
        "regulations": len(payload.get("regulations") or []),
        "years": payload.get("years") or [],
        "filename": payload.get("filename") or "",
        "download_url": payload.get("download_url") or "",
        "saved_at": payload.get("_saved_at") or "",
    }


def summarize_quy_doi(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not payload:
        return {"has_data": False}
    summary = payload.get("summary") or {}
    codes = []
    for r in payload.get("rows") or []:
        c = (r.get("ma_truong") or "").upper()
        if c and c not in codes:
            codes.append(c)
    if not codes:
        for m in payload.get("school_results") or []:
            c = (m.get("code") or "").upper()
            if c and c not in codes:
                codes.append(c)
    return {
        "has_data": bool(payload.get("rows") or payload.get("images")),
        "schools": summary.get("schools") or len(codes),
        "codes": codes,
        "rows": summary.get("rows") or len(payload.get("rows") or []),
        "notes": summary.get("notes") or len(payload.get("notes") or []),
        "images": summary.get("images") or len(payload.get("images") or []),
        "filename": payload.get("filename") or "",
        "download_url": payload.get("download_url") or "",
        "saved_at": payload.get("_saved_at") or "",
    }


def summarize_schools(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not payload:
        return {"has_data": False}
    schools = payload.get("schools") or []
    return {
        "has_data": bool(schools),
        "total": payload.get("total") or len(schools),
        "description": payload.get("description") or "",
    }


def list_system_datasets(root: str) -> Dict[str, Any]:
    """Tóm tắt 3 nhóm dữ liệu cho trang Dữ liệu hệ thống."""
    schools_path = schools_json_path(root)
    schools_data = load_schools(root)
    schools_xlsx = os.path.join(output_dir(root), "danh_sach_ma_truong.xlsx")

    adm_latest = admissions_latest_path(root)
    adm_data = load_admissions(root)
    qd_latest = quy_doi_latest_path(root)
    qd_data = load_quy_doi(root)

    return {
        "schools": {
            **summarize_schools(schools_data),
            "json": file_meta(schools_path),
            "excel": file_meta(schools_xlsx),
            "download_json": "danh_sach_ma_truong.json",
            "download_excel": "danh_sach_ma_truong.xlsx",
        },
        "admissions": {
            **summarize_admissions(adm_data),
            "latest": file_meta(adm_latest),
            "snapshots": _list_glob(
                os.path.join(datasets_dir(root), "admissions", "admissions_*.json")
            ),
            "excels": _list_glob(
                os.path.join(output_dir(root), "tong_hop_tuyen_sinh_*.xlsx")
            ),
        },
        "quy_doi": {
            **summarize_quy_doi(qd_data),
            "latest": file_meta(qd_latest),
            "snapshots": _list_glob(
                os.path.join(datasets_dir(root), "quy_doi", "quy_doi_*.json")
            ),
            "excels": _list_glob(
                os.path.join(output_dir(root), "quy_doi_diem_*.xlsx")
            ),
        },
    }


def resolve_dataset_file(root: str, name: str) -> Optional[str]:
    """Chỉ cho phép file trong datasets/ hoặc output/ (basename an toàn)."""
    safe = os.path.basename(name or "")
    if not safe or safe in (".", ".."):
        return None
    for base in (datasets_dir(root), output_dir(root),
                 os.path.join(datasets_dir(root), "admissions"),
                 os.path.join(datasets_dir(root), "quy_doi")):
        path = os.path.join(base, safe)
        if os.path.isfile(path):
            return path
    return None


def copy_schools_exports_stamp(root: str) -> None:
    """Đảm bảo thư mục datasets tồn tại (bước 1 đã ghi vào output)."""
    datasets_dir(root)


def _unlink(path: str) -> bool:
    if not path or not os.path.isfile(path):
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def _unlink_glob(pattern: str) -> int:
    n = 0
    for p in glob.glob(pattern):
        if _unlink(p):
            n += 1
    return n


def clear_schools(root: str) -> Dict[str, Any]:
    """Xoá danh sách trường đã lưu (JSON + Excel + mã txt)."""
    out = output_dir(root)
    removed = []
    for name in (
        "danh_sach_ma_truong.json",
        "danh_sach_ma_truong.xlsx",
        "ma_truong.txt",
    ):
        path = os.path.join(out, name)
        if _unlink(path):
            removed.append(name)
    return {"kind": "schools", "removed": removed, "count": len(removed)}


def clear_admissions(root: str) -> Dict[str, Any]:
    """Xoá dữ liệu tổng hợp: latest, snapshot JSON, Excel xuất."""
    removed: List[str] = []
    latest = admissions_latest_path(root)
    if _unlink(latest):
        removed.append(os.path.basename(latest))
    snap_n = _unlink_glob(
        os.path.join(datasets_dir(root), "admissions", "admissions_*.json")
    )
    xls_n = _unlink_glob(os.path.join(output_dir(root), "tong_hop_tuyen_sinh_*.xlsx"))
    return {
        "kind": "admissions",
        "removed": removed,
        "snapshots_removed": snap_n,
        "excels_removed": xls_n,
        "count": len(removed) + snap_n + xls_n,
    }


def clear_quy_doi(root: str) -> Dict[str, Any]:
    """Xoá bảng quy đổi: latest, snapshot JSON, Excel xuất."""
    removed: List[str] = []
    latest = quy_doi_latest_path(root)
    if _unlink(latest):
        removed.append(os.path.basename(latest))
    snap_n = _unlink_glob(
        os.path.join(datasets_dir(root), "quy_doi", "quy_doi_*.json")
    )
    xls_n = _unlink_glob(os.path.join(output_dir(root), "quy_doi_diem_*.xlsx"))
    return {
        "kind": "quy_doi",
        "removed": removed,
        "snapshots_removed": snap_n,
        "excels_removed": xls_n,
        "count": len(removed) + snap_n + xls_n,
    }


def clear_kind(root: str, kind: str) -> Dict[str, Any]:
    """
    Làm sạch theo nhóm: schools | admissions | quy_doi | all.
    """
    k = (kind or "").strip().lower()
    if k in ("schools", "truong", "school"):
        return clear_schools(root)
    if k in ("admissions", "crawl", "tong_hop"):
        return clear_admissions(root)
    if k in ("quy_doi", "quy-doi", "quydoi"):
        return clear_quy_doi(root)
    if k == "all":
        parts = [clear_schools(root), clear_admissions(root), clear_quy_doi(root)]
        return {
            "kind": "all",
            "parts": parts,
            "count": sum(p.get("count") or 0 for p in parts),
        }
    raise ValueError("kind phải là schools, admissions, quy_doi hoặc all.")


def delete_dataset_file(root: str, name: str) -> Dict[str, Any]:
    """Xoá một file cụ thể trong datasets/ hoặc output/ (basename an toàn)."""
    path = resolve_dataset_file(root, name)
    if not path:
        raise FileNotFoundError("Không tìm thấy file.")
    # Không cho xoá file ngoài phạm vi đã resolve
    if not _unlink(path):
        raise OSError("Không xoá được file.")
    return {"ok": True, "removed": os.path.basename(path)}
