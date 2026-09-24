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
from typing import Any, Dict, List

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
from core.bonus_policy import summarize_certificate_bonus
from core import dataset_store


def _keep_other_school_rows(items, replaced: set, years: List[int]):
    """Giữ trường khác. Với trường vừa thu thập, chỉ thay các năm được chọn."""
    year_set = {int(y) for y in years}
    kept = []
    for item in items or []:
        code = str((item or {}).get("ma_truong") or "").upper()
        if code not in replaced:
            kept.append(item)
            continue
        nam = item.get("nam")
        try:
            year = int(nam) if nam not in (None, "") else None
        except (TypeError, ValueError):
            year = None
        if year is not None and year not in year_set:
            kept.append(item)
    return kept


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

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/schools")
    def schools_page():
        return render_template("schools.html")

    @app.route("/crawl")
    def crawl_page():
        return render_template("thu_thap.html")

    @app.route("/quy-doi")
    def quy_doi_page():
        return render_template("quy_doi.html")

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

        def keep_other(items):
            return _keep_other_school_rows(items, replaced, years)

        payload = dict(cached)
        payload["admissions"] = keep_other(cached.get("admissions"))
        payload["admissions"].extend(r.to_dict() for r in bundle.admissions)
        payload["conversions"] = keep_other(cached.get("conversions"))
        payload["conversions"].extend(c.to_dict() for c in bundle.conversions)
        payload["regulations"] = keep_other(cached.get("regulations"))
        payload["regulations"].extend(r.to_dict() for r in bundle.regulations)
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

        def keep(items):
            return [item for item in (items or []) if code_of(item) not in replaced]

        merged = dict(cached)
        for key in ("rows", "notes", "images", "ranges", "certificate_conversions", "school_results"):
            merged[key] = keep(cached.get(key))
            merged[key].extend(payload.get(key) or [])
        methods = {
            code: value
            for code, value in (cached.get("methods_by_school") or {}).items()
            if str(code).upper() not in replaced
        }
        methods.update(payload.get("methods_by_school") or {})
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

        for i, code in enumerate(codes, start=1):
            try:
                part = crawler.crawl_school_bundle(
                    code,
                    years=years,
                    delay=0.3,
                    source_urls=source_urls.get(code),
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
        cached = app.config.get("LAST_CRAWL") or {}
        conversions = cached.get("conversions") or []
        regulations = cached.get("regulations") or []
        admissions = cached.get("admissions") or []
        if not admissions and not conversions and not regulations:
            return jsonify({
                "ok": False,
                "error": "Chưa có dữ liệu đã tổng hợp. Hãy chạy bước 2 trước.",
            }), 400
        schools_raw = request.args.get("schools") or ""
        school_codes = [c.strip().upper() for c in schools_raw.split(",") if c.strip()]
        payload = summarize_certificate_bonus(
            conversions,
            regulations,
            school_codes=school_codes or None,
            admissions=admissions,
        )
        payload["ok"] = True
        return jsonify(payload)

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

        allowed = {".xlsx", ".xls", ".pdf", ".docx", ".jpg", ".jpeg", ".png", ".webp"}
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
                    path, original_name, code, school_name, year, source_url
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
        append_unique(
            admissions,
            [r.to_dict() for r in combined.admissions],
            ("ma_truong", "ma_nganh", "ten_nganh", "nam", "phuong_thuc", "to_hop", "diem_chuan"),
        )
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
        rows = []
        for item in cached.get("admissions") or []:
            rows.append({
                "ma_truong": item.get("ma_truong") or "",
                "ten_truong": item.get("ten_truong") or "",
                "nam": item.get("nam"),
                "ma_nganh": item.get("ma_nganh") or "",
                "ten_nganh": item.get("ten_nganh") or "",
                "phuong_thuc": item.get("phuong_thuc") or "",
                "diem_chuan": item.get("diem_chuan_ptxt") if item.get("diem_chuan_ptxt") is not None else item.get("diem_chuan"),
                "chi_tieu": item.get("chi_tieu"),
                "nguon": item.get("nguon") or "",
            })
        years = sorted({
            int(row["nam"]) for row in rows
            if row.get("nam") is not None
        })
        return jsonify({
            "ok": True,
            "total": len(rows),
            "years": years,
            "rows": rows,
            "download_url": cached.get("download_url") or "",
        })

    def _record_key(item: dict) -> tuple:
        return (
            str(item.get("ma_truong") or "").strip().upper(),
            str(item.get("ma_nganh") or item.get("ten_nganh") or "").strip(),
            str(item.get("phuong_thuc") or "").strip(),
        )

    @app.post("/api/crawl/records/delete")
    def api_crawl_records_delete():
        """Xoá một hoặc nhiều dòng kết quả (trường + ngành + phương thức) khỏi dữ liệu đã lưu."""
        data = request.get_json(silent=True) or {}
        raw_keys = data.get("keys") or []
        if not isinstance(raw_keys, list) or not raw_keys:
            return jsonify({"ok": False, "error": "Chưa chọn dòng cần xoá."}), 400
        drop = {_record_key(item) for item in raw_keys if isinstance(item, dict)}
        drop.discard(("", "", ""))
        if not drop:
            return jsonify({"ok": False, "error": "Chưa chọn dòng cần xoá."}), 400

        cached = dict(app.config.get("LAST_CRAWL") or dataset_store.load_admissions(ROOT) or {})
        admissions = [
            item for item in (cached.get("admissions") or [])
            if _record_key(item) not in drop
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
                "ma_nganh": item.get("ma_nganh") or "",
                "ten_nganh": item.get("ten_nganh") or "",
                "phuong_thuc": item.get("phuong_thuc") or "",
                "diem_chuan": item.get("diem_chuan_ptxt") if item.get("diem_chuan_ptxt") is not None else item.get("diem_chuan"),
                "chi_tieu": item.get("chi_tieu"),
                "nguon": item.get("nguon") or "",
            })
        return jsonify({
            "ok": True,
            "removed": len(drop),
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
        return jsonify({
            "ok": True,
            "total": len(items),
            "rows": items,
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
