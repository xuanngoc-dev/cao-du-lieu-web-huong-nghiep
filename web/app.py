# -*- coding: utf-8 -*-
"""
Giao diện web Bootstrap (Flask) — Tổng hợp dữ liệu tuyển sinh ĐH / CĐ.

Chạy:
  python3 main.py --mode ui
  hoặc: python3 web/app.py
"""

import os
import sys
from datetime import datetime
from typing import List

from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    send_file,
    redirect,
    url_for,
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
    os.makedirs(app.config["OUTPUT_DIR"], exist_ok=True)

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

    # ---------- API: Cào điểm chuẩn ----------
    @app.post("/api/crawl")
    def api_crawl():
        data = request.get_json(silent=True) or {}
        codes = parse_school_codes_text(data.get("codes") or "")
        years = data.get("years") or [2024, 2025, 2026]
        years = [int(y) for y in years]
        limit = int(data.get("limit") or 0)
        if limit > 0:
            codes = codes[:limit]

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

        return jsonify({
            "ok": True,
            "schools": len(codes),
            "admissions": len(bundle.admissions),
            "conversions": len(bundle.conversions),
            "regulations": len(bundle.regulations),
            "logs": logs,
            "download_url": url_for("download_file", name=out_name),
            "filename": out_name,
        })

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
        # Cache nhẹ trong app để máy tính không cần gửi lại toàn bộ bảng
        app.config["LAST_QUY_DOI"] = payload
        return jsonify(payload)

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
            app.config["LAST_QUY_DOI"] = cached
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
                    app.config["LAST_QUY_DOI"] = cached
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

    @app.get("/download/<name>")
    def download_file(name: str):
        # Chỉ cho phép file trong output dir
        safe = os.path.basename(name)
        path = os.path.join(app.config["OUTPUT_DIR"], safe)
        if not os.path.isfile(path):
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
