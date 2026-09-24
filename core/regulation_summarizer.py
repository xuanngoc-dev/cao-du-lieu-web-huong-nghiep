# -*- coding: utf-8 -*-
"""Tổng hợp dữ liệu nhiều nguồn thành một bản quy chế tuyển sinh có dẫn nguồn."""

from datetime import datetime
import re
from typing import Any, Dict, Iterable, List

from core.normalizer import clean_text, strip_accents


def _matches(row: Dict[str, Any], code: str, field: str = "ma_truong") -> bool:
    return str(row.get(field) or "").upper() == code


def _unique_text(values: Iterable[str]) -> List[str]:
    output = []
    seen = set()
    for value in values:
        text = clean_text(value)
        key = strip_accents(text)
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def _source_urls(rows: Iterable[Dict[str, Any]]) -> List[str]:
    urls = []
    seen = set()
    for row in rows:
        text = str(row.get("url_nguon") or row.get("source_url") or row.get("nguon") or "")
        for match in re.findall(r"https?://[^\s<>'\")]+", text):
            url = match.rstrip(".,;")
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def summarize_school_regulation(
    crawl: Dict[str, Any],
    quy_doi: Dict[str, Any],
    code: str,
    school_name: str,
) -> Dict[str, Any]:
    code = (code or "").upper()
    admissions = [r for r in (crawl.get("admissions") or []) if _matches(r, code)]
    conversions = [r for r in (crawl.get("conversions") or []) if _matches(r, code)]
    regulations = [r for r in (crawl.get("regulations") or []) if _matches(r, code)]
    documents = [r for r in (crawl.get("uploaded_documents") or []) if _matches(r, code)]
    rows = [r for r in (quy_doi.get("rows") or []) if _matches(r, code)]
    notes = [r for r in (quy_doi.get("notes") or []) if _matches(r, code)]
    certificates = [
        r for r in (quy_doi.get("certificate_conversions") or []) if _matches(r, code)
    ]

    years = sorted({
        int(r.get("nam") or r.get("year"))
        for r in admissions + conversions + regulations + rows + notes + documents
        if str(r.get("nam") or r.get("year") or "").isdigit()
    })
    majors = {
        (clean_text(r.get("ma_nganh") or ""), clean_text(r.get("ten_nganh") or ""))
        for r in admissions
        if r.get("ma_nganh") or r.get("ten_nganh")
    }
    methods = _unique_text(
        [r.get("phuong_thuc") or "" for r in admissions + conversions + regulations + certificates]
        + [
            key
            for row in rows
            for key in (row.get("cot_gia_tri") or {}).keys()
            if key != "__stt"
        ]
    )
    certificate_types = _unique_text(
        r.get("loai_bang") or "" for r in conversions + certificates
    )

    score_groups: Dict[tuple, List[float]] = {}
    for row in admissions:
        score = row.get("diem_chuan")
        if not isinstance(score, (int, float)):
            continue
        key = (row.get("nam") or "?", clean_text(row.get("phuong_thuc") or "Chưa xác định"))
        score_groups.setdefault(key, []).append(float(score))
    score_lines = []
    for (year, method), scores in sorted(
        score_groups.items(),
        key=lambda item: (str(item[0][0]), item[0][1]),
        reverse=True,
    ):
        low, high = min(scores), max(scores)
        score_range = f"{low:g}" if low == high else f"{low:g}–{high:g}"
        score_lines.append(f"- {year} · {method}: {score_range} ({len(scores)} bản ghi)")

    policy_texts = _unique_text(
        [
            f"{r.get('tieu_de') or 'Quy định'}: {r.get('noi_dung') or ''}"
            for r in regulations + notes
        ]
    )
    policy_lines = [
        f"- {text[:1200]}{'…' if len(text) > 1200 else ''}"
        for text in policy_texts[:30]
    ]

    sections = [
        (
            "Phạm vi dữ liệu",
            f"Các năm: {', '.join(map(str, years)) or 'chưa xác định'}. "
            f"Có {len(majors)} ngành/chương trình, {len(admissions)} bản ghi tuyển sinh, "
            f"{len(rows)} dòng quy đổi và {len(documents)} tài liệu tải lên.",
        ),
        (
            "Phương thức tuyển sinh",
            "; ".join(methods) if methods else "Chưa nhận diện được phương thức tuyển sinh.",
        ),
        (
            "Điểm chuẩn trong dữ liệu hiện có",
            "\n".join(score_lines) if score_lines else "Chưa có điểm chuẩn dạng số để tổng hợp.",
        ),
        (
            "Hệ quy đổi",
            (
                f"Có {len(rows)} dòng quy đổi giữa các phương thức"
                + (f"; chứng chỉ/hệ điểm gồm: {', '.join(certificate_types)}." if certificate_types else ".")
            ),
        ),
        (
            "Quy chế, điều kiện và ghi chú từ nguồn",
            "\n".join(policy_lines) if policy_lines else "Chưa có nội dung quy chế dạng văn bản.",
        ),
    ]
    content = "\n\n".join(f"{title}\n{body}" for title, body in sections)
    all_source_rows = (
        admissions + conversions + regulations + documents + rows + notes + certificates
    )
    sources = _source_urls(all_source_rows)
    source_labels = _unique_text(
        [
            r.get("filename") or r.get("nguon") or r.get("url_nguon") or ""
            for r in all_source_rows
        ]
    )
    return {
        "ma_truong": code,
        "ten_truong": school_name,
        "tieu_de": "Quy chế tuyển sinh tổng hợp",
        "noi_dung": content,
        "phuong_thuc": "; ".join(methods),
        "nam": max(years) if years else None,
        "nguon": f"Tổng hợp từ {len(sources) or len(source_labels)} nguồn dữ liệu đã lưu",
        "nguon_tai_lieu": sources,
        "ten_nguon": source_labels[:100],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "thong_ke": {
            "years": years,
            "majors": len(majors),
            "admissions": len(admissions),
            "methods": len(methods),
            "conversion_rows": len(rows),
            "regulations": len(regulations) + len(notes),
            "documents": len(documents),
            "sources": len(sources),
        },
    }
