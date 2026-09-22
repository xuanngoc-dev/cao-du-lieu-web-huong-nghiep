# -*- coding: utf-8 -*-
"""
Giao diện web Bootstrap (Flask) — Tổng hợp dữ liệu tuyển sinh ĐH / CĐ.

Chạy:
  python3 main.py --mode ui
  hoặc: python3 web/app.py
"""

import json
import os
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
)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ui.helpers import parse_school_codes_text, format_codes_for_copy
from crawlers.school_directory import SchoolDirectory, TYPE_LABELS
from crawlers.online_crawler import OnlineAdmissionCrawler
from crawlers.score_conversion_crawler import ScoreConversionCrawler
from exporter.excel_exporter import ExcelAdmissionExporter
from core.models import CrawlBundle
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
from core import dataset_store


def _enrich_methods_for_school(code: str, quy_doi_methods: List) -> List:
    """Gộp phương thức từ bảng quy-đổi + danh sách PTXT trên trang điểm chuẩn."""
    try:
        oc = OnlineAdmissionCrawler()
        dc_methods = oc.list_admission_methods(code)
    except Exception:
        dc_methods = []
    return merge_method_lists(dc_methods, quy_doi_methods or [])


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "static"),
    )
    app.config["SECRET_KEY"] = "huong-nghiep-tuyen-sinh"
    app.config["OUTPUT_DIR"] = os.path.join(ROOT, "data", "output")
    app.config["DATASETS_DIR"] = os.path.join(ROOT, "data", "datasets")
    os.makedirs(app.config["OUTPUT_DIR"], exist_ok=True)
    os.makedirs(app.config["DATASETS_DIR"], exist_ok=True)

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
        return render_template("crawl.html")

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
    @app.post("/api/schools/fetch")
    def api_schools_fetch():
        data = request.get_json(silent=True) or {}
        refresh = bool(data.get("refresh", False))
        include_profile = bool(data.get("include_profile", False))
        # Mặc định vẫn lấy website + địa chỉ; tắt khi chỉ lọc/xuất lại Excel
        include_contact = bool(data.get("include_contact", True))
        school_filter = data.get("filter", "all")  # all | dai_hoc | cao_dang | hoc_vien | dai_hoc_hoc_vien
        keyword = (data.get("keyword") or "").strip()

        directory = SchoolDirectory()
        # Hồ sơ đầy đủ chỉ khi include_profile; website + địa chỉ khi include_contact.
        directory.load(
            force_refresh=refresh,
            include_dai_hoc=True,
            include_cao_dang=True,
            include_profile=include_profile,
            include_contact=include_contact and not include_profile,
        )

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
            directory.export_json(output_path=json_path, school_type=stype, also_update_config=True)
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

    def _store_last_quy_doi(payload: dict):
        app.config["LAST_QUY_DOI"] = payload
        try:
            dataset_store.save_quy_doi(ROOT, payload)
        except OSError:
            pass
        return payload
    # ---------- API: thu thập điểm chuẩn ----------
    @app.post("/api/crawl")
    def api_crawl():
        codes, years, _ = _parse_crawl_request()
        if not codes:
            return jsonify({"ok": False, "error": "Chưa có mã trường hợp lệ."}), 400
        if not years:
            return jsonify({"ok": False, "error": "Chọn ít nhất 1 năm."}), 400

        crawler = OnlineAdmissionCrawler()
        bundle = CrawlBundle()
        logs: List[str] = []

        for i, code in enumerate(codes, start=1):
            try:
                part = crawler.crawl_school_bundle(code, years=years, delay=0.3)
                bundle.admissions.extend(part.admissions)
                bundle.conversions.extend(part.conversions)
                bundle.regulations.extend(part.regulations)
                logs.append(
                    f"✓ [{i}/{len(codes)}] {code}: "
                    f"{len(part.admissions)} ngành, {len(part.conversions)} quy đổi chứng chỉ, "
                    f"{len(part.regulations)} quy chế"
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
        admissions = _store_last_crawl(
            bundle, years, codes,
            {"download_url": download_url, "filename": out_name, "logs": logs},
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
        codes, years, _ = _parse_crawl_request()
        if not codes:
            return jsonify({"ok": False, "error": "Chưa có mã trường hợp lệ."}), 400
        if not years:
            return jsonify({"ok": False, "error": "Chọn ít nhất 1 năm."}), 400

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
                    part = crawler.crawl_school_bundle(code, years=years, delay=0.25)
                    bundle.admissions.extend(part.admissions)
                    bundle.conversions.extend(part.conversions)
                    bundle.regulations.extend(part.regulations)
                    preview = [r.to_dict() for r in part.admissions[:40]]
                    msg = (
                        f"✓ [{i}/{len(codes)}] {code}: "
                        f"{len(part.admissions)} ngành, {len(part.conversions)} quy đổi, "
                        f"{len(part.regulations)} quy chế"
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
            admissions = _store_last_crawl(
                bundle, years, codes,
                {"download_url": download_url, "filename": out_name, "logs": logs},
            )
            trends = build_trend_series(admissions)
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
                "trends": trends,
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
        """Danh sách phương thức xét tuyển có trong dữ liệu đã thu thập."""
        cached = app.config.get("LAST_CRAWL") or {}
        admissions = cached.get("admissions") or []
        if not admissions:
            return jsonify({"ok": False, "error": "Chưa có dữ liệu đã tổng hợp. Hãy chạy bước 2 trước."}), 400
        school = (request.args.get("school") or "").strip().upper() or None
        schools_raw = request.args.get("schools") or ""
        school_codes = [c.strip().upper() for c in schools_raw.split(",") if c.strip()]
        methods = list_admission_methods(
            admissions,
            school_code=school if not school_codes else None,
            school_codes=school_codes or None,
        )
        return jsonify({
            "ok": True,
            "school": school or "",
            "schools": school_codes,
            "methods": methods,
            "total": len(methods),
        })

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
        crawler.export_excel(bundle, output_path=out_path)

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
        _store_last_quy_doi(payload)
        return jsonify(payload)

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

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_name = f"quy_doi_diem_{ts}.xlsx"
            out_path = os.path.join(app.config["OUTPUT_DIR"], out_name)
            crawler.export_excel(bundle, output_path=out_path)

            payload = crawler.bundle_to_api_dict(bundle)
            payload["ok"] = True
            payload["download_url"] = url_for("download_file", name=out_name)
            payload["filename"] = out_name
            methods_by_school = {}
            for meta in bundle.school_results:
                code = meta.get("code") or ""
                from_table = list_methods_from_rows(payload["rows"], code)
                methods_by_school[code] = _enrich_methods_for_school(code, from_table)
            payload["methods_by_school"] = methods_by_school
            payload["method_labels"] = METHOD_LABELS
            _store_last_quy_doi(payload)

            yield json.dumps({
                "type": "done",
                **payload,
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
                mbs = cached.get("methods_by_school") or {}
                mbs[code] = _enrich_methods_for_school(
                    code, list_methods_from_rows(part.get("rows") or [], code)
                )
                cached["methods_by_school"] = mbs
            _store_last_quy_doi(cached)
            rows = cached.get("rows") or []

        if mode == "certificate":
            # Lấy thêm bảng chứng chỉ từ đề án nếu cần
            cert_type = (data.get("certificate_type") or method or "IELTS").upper()
            convs = cached.get("certificate_conversions") or []
            if not any(c.get("ma_truong") == code for c in convs):
                from crawlers.online_crawler import OnlineAdmissionCrawler
                oc = OnlineAdmissionCrawler()
                info = oc.find_school(code)
                if info:
                    _, dean_conv, _ = oc.crawl_dean_data(
                        info["code"], info.get("name") or code, info.get("slug") or ""
                    )
                    convs = [c.to_dict() for c in dean_conv]
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
        schools = data.get("schools") or []
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

    @app.get("/api/quy-doi/session")
    def api_quy_doi_session():
        cached = app.config.get("LAST_QUY_DOI") or {}
        summary = dataset_store.summarize_quy_doi(cached)
        if not summary.get("has_data"):
            return jsonify({"ok": True, "has_data": False})
        payload = dict(cached)
        payload["ok"] = True
        payload["has_data"] = True
        payload["from_disk"] = True
        if not payload.get("download_url") and payload.get("filename"):
            payload["download_url"] = url_for("download_file", name=payload["filename"])
        return jsonify(payload)

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
