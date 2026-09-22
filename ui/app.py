# -*- coding: utf-8 -*-
"""
Giao diện web Streamlit — Hệ thống tổng hợp dữ liệu tuyển sinh ĐH / CĐ.

Chạy:
  streamlit run ui/app.py
  hoặc: python3 main.py --mode ui
"""

import os
import sys
from datetime import datetime
from typing import List

import pandas as pd
import streamlit as st

# Đảm bảo import được các module gốc dự án
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ui.helpers import parse_school_codes_text, format_codes_for_copy
from crawlers.school_directory import SchoolDirectory, TYPE_LABELS
from crawlers.online_crawler import OnlineAdmissionCrawler
from exporter.excel_exporter import ExcelAdmissionExporter
from core.models import CrawlBundle


# ---------------------------------------------------------------------------
# Cấu hình trang & CSS
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Tuyển sinh ĐH/CĐ — Thu thập dữ liệu",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap');

:root {
  --ink: #0f2a3d;
  --teal: #0d7377;
  --teal-dark: #095c5f;
  --sand: #f3f6f4;
  --line: #d5e0dc;
  --accent: #c45c26;
}

html, body, [class*="css"] {
  font-family: "Be Vietnam Pro", "IBM Plex Sans", sans-serif;
}

.stApp {
  background:
    radial-gradient(1200px 500px at 10% -10%, #d9efe9 0%, transparent 55%),
    radial-gradient(900px 400px at 100% 0%, #f7e8dc 0%, transparent 50%),
    linear-gradient(180deg, #f7faf8 0%, #eef3f1 100%);
}

.block-container { padding-top: 1.4rem; max-width: 1200px; }

h1, h2, h3 { color: var(--ink) !important; letter-spacing: -0.02em; }

.hero {
  background: linear-gradient(135deg, #0f2a3d 0%, #0d7377 70%, #14919b 100%);
  color: #fff;
  border-radius: 18px;
  padding: 1.4rem 1.6rem 1.5rem;
  margin-bottom: 1.2rem;
  box-shadow: 0 12px 30px rgba(15, 42, 61, 0.18);
}
.hero h1 {
  color: #fff !important;
  font-size: 1.65rem;
  margin: 0 0 0.35rem 0;
  font-weight: 700;
}
.hero p { margin: 0; opacity: 0.9; font-size: 0.98rem; }

.step-card {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 0.85rem 1rem;
  margin-bottom: 0.8rem;
}
.metric-row { display: flex; gap: 0.75rem; flex-wrap: wrap; margin: 0.6rem 0 1rem; }
.metric-pill {
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 0.35rem 0.85rem;
  font-size: 0.9rem;
  color: var(--ink);
}
.metric-pill b { color: var(--teal-dark); }

div.stButton > button[kind="primary"] {
  background: var(--teal);
  border: none;
  font-weight: 600;
}
div.stButton > button[kind="primary"]:hover {
  background: var(--teal-dark);
  border: none;
}

.stTabs [data-baseweb="tab-list"] {
  gap: 0.4rem;
  background: transparent;
}
.stTabs [data-baseweb="tab"] {
  border-radius: 10px 10px 0 0;
  padding: 0.55rem 1rem;
  font-weight: 600;
}
.stTabs [aria-selected="true"] {
  background: #fff;
  color: var(--teal-dark);
}

textarea { font-family: "IBM Plex Sans", monospace !important; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def _init_state():
    defaults = {
        "school_rows": [],
        "codes_clipboard": "",
        "crawl_codes_input": "",
        "last_output_path": "",
        "last_stats": {},
        "crawl_log": [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _rows_to_df(rows: List[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["Mã trường", "Tên trường", "Loại hình", "Slug"])
    return pd.DataFrame([
        {
            "Mã trường": r["code"],
            "Tên trường": r["name"],
            "Loại hình": r.get("type_label") or TYPE_LABELS.get(r.get("type", ""), ""),
            "Slug": r.get("slug", ""),
        }
        for r in rows
    ])


def _filter_type_key(label: str):
    mapping = {
        "Tất cả": None,
        "Đại học + Học viện": "dai_hoc_hoc_vien",
        "Đại học": "dai_hoc",
        "Học viện": "hoc_vien",
        "Cao đẳng": "cao_dang",
    }
    return mapping.get(label, None)


# ---------------------------------------------------------------------------
# Bước 1 — Danh bạ mã trường
# ---------------------------------------------------------------------------
def render_step1():
    st.subheader("Bước 1 — Lấy danh sách mã trường")
    st.caption(
        "Tải danh bạ Đại học / Học viện / Cao đẳng từ tuyensinh247, "
        "lọc theo loại hình, rồi **copy mã** sang Bước 2 để Thu thập dữ liệu."
    )

    c1, c2, c3 = st.columns([1.2, 1.2, 1])
    with c1:
        type_label = st.selectbox(
            "Loại hình",
            ["Tất cả", "Đại học + Học viện", "Đại học", "Học viện", "Cao đẳng"],
            index=0,
        )
    with c2:
        keyword = st.text_input("Tìm theo mã / tên trường", placeholder="VD: BKA, Ngoại thương,...")
    with c3:
        st.write("")
        st.write("")
        refresh = st.checkbox("Làm mới từ web", value=False, help="Bỏ cache, tải lại danh bạ")

    b1, b2 = st.columns([1, 3])
    with b1:
        load_btn = st.button("📥 Tải danh sách trường", type="primary", use_container_width=True)

    if load_btn:
        with st.spinner("Đang lấy danh bạ mã trường..."):
            directory = SchoolDirectory()
            directory.load(force_refresh=refresh, include_dai_hoc=True, include_cao_dang=True)
            # Luôn giữ full danh bạ; lọc hiển thị theo selectbox bên dưới
            rows = directory.list_schools(school_type=None, keyword="")
            st.session_state.school_rows = rows
            try:
                stype = _filter_type_key(type_label)
                directory.export_json(school_type=stype, also_update_config=True)
                directory.export_excel(school_type=stype)
            except Exception as e:
                st.warning(f"Xuất file danh bạ gặp lỗi (không ảnh hưởng UI): {e}")
        st.success(f"Đã tải {len(st.session_state.school_rows)} trường vào bộ nhớ.")

    # Áp filter lại khi user đổi selectbox mà đã có data
    rows = st.session_state.school_rows
    if rows:
        stype = _filter_type_key(type_label)
        filtered = []
        kw = (keyword or "").strip().lower()
        for r in rows:
            t = r.get("type")
            if stype == "dai_hoc_hoc_vien" and t == "cao_dang":
                continue
            if stype in ("dai_hoc", "cao_dang", "hoc_vien") and t != stype:
                continue
            if kw and kw not in r["code"].lower() and kw not in (r.get("name") or "").lower():
                continue
            filtered.append(r)

        # Thống kê
        stats = {}
        for r in filtered:
            lb = r.get("type_label") or "?"
            stats[lb] = stats.get(lb, 0) + 1
        pills = "".join(
            f'<span class="metric-pill"><b>{v}</b> {k}</span>' for k, v in sorted(stats.items())
        )
        st.markdown(
            f'<div class="metric-row">{pills}'
            f'<span class="metric-pill"><b>{len(filtered)}</b> tổng đang hiện</span></div>',
            unsafe_allow_html=True,
        )

        df = _rows_to_df(filtered)
        st.dataframe(df, use_container_width=True, height=360)

        codes_text = format_codes_for_copy([r["code"] for r in filtered], one_per_line=True)
        st.session_state.codes_clipboard = codes_text

        st.markdown("#### Copy danh sách mã trường")
        st.caption("Chọn toàn bộ ô bên dưới (Ctrl/Cmd + A) rồi Copy (Ctrl/Cmd + C), hoặc bấm nút chuyển sang Bước 2.")
        st.text_area(
            "Danh sách mã (mỗi dòng 1 mã)",
            value=codes_text,
            height=160,
        )

        a1, a2, a3 = st.columns(3)
        with a1:
            if st.button("➡️ Dùng danh sách này ở Bước 2", type="primary", use_container_width=True):
                st.session_state.crawl_codes_input = codes_text
                st.session_state["crawl_textarea"] = codes_text
                st.session_state["nav_to_crawl"] = True
                st.success(f"Đã chuyển {len(filtered)} mã sang Bước 2. Mở tab «② Thu thập dữ liệu».")
                st.rerun()
        with a2:
            excel_path = "data/output/danh_sach_ma_truong.xlsx"
            if os.path.exists(excel_path):
                with open(excel_path, "rb") as f:
                    st.download_button(
                        "⬇️ Tải Excel danh bạ",
                        data=f,
                        file_name="danh_sach_ma_truong.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                    )
        with a3:
            st.download_button(
                "⬇️ Tải file .txt mã trường",
                data=codes_text.encode("utf-8"),
                file_name="ma_truong.txt",
                mime="text/plain",
                use_container_width=True,
            )
    else:
        st.info("Nhấn **Tải danh sách trường** để bắt đầu Bước 1.")


# ---------------------------------------------------------------------------
# Bước 2 — Thu thập dữ liệu
# ---------------------------------------------------------------------------
def render_step2():
    st.subheader("Bước 2 — Thu thập dữ liệu tuyển sinh")
    st.caption(
        "Dán danh sách mã trường đã copy từ Bước 1 (hoặc nhập tay). "
        "Hỗ trợ: mỗi dòng 1 mã, hoặc cách nhau bởi dấu phẩy."
    )

    # Khởi tạo giá trị textarea lần đầu / sau khi chuyển từ Bước 1
    if "crawl_textarea" not in st.session_state:
        st.session_state.crawl_textarea = (
            st.session_state.get("crawl_codes_input")
            or st.session_state.get("codes_clipboard")
            or ""
        )

    codes_raw = st.text_area(
        "Danh sách mã trường",
        height=180,
        placeholder="BKA\nNEU\nFTU\nQHI\nCDD0209",
        help="Dán từ Bước 1. Có thể dùng dấu phẩy: BKA,NEU,FTU",
        key="crawl_textarea",
    )
    st.session_state.crawl_codes_input = codes_raw

    parsed = parse_school_codes_text(codes_raw)
    st.write(f"**Đã nhận diện:** {len(parsed)} mã trường"
             + (f" — `{', '.join(parsed[:12])}`" + ("…" if len(parsed) > 12 else "") if parsed else ""))

    y1, y2 = st.columns(2)
    with y1:
        years = st.multiselect(
            "Năm tuyển sinh",
            options=[2021, 2022, 2023, 2024, 2025, 2026],
            default=[2024, 2025, 2026],
        )
    with y2:
        limit = st.number_input(
            "Giới hạn số trường (0 = không giới hạn)",
            min_value=0,
            max_value=1000,
            value=0,
            help="Dùng khi thử nghiệm, VD: 3–5 trường trước khi thu thập hàng trăm trường.",
        )

    codes_to_run = parsed[: int(limit)] if limit and limit > 0 else parsed

    if len(codes_to_run) > 30:
        st.warning(
            f"Bạn sắp thu thập **{len(codes_to_run)} trường** × **{len(years)} năm**. "
            "Quá trình có thể mất nhiều giờ. Nên dùng giới hạn số trường để thử trước."
        )

    run = st.button("🚀 Bắt đầu Thu thập dữ liệu", type="primary", use_container_width=False, disabled=not codes_to_run)

    if run:
        if not years:
            st.error("Chọn ít nhất 1 năm tuyển sinh.")
            return
        if not codes_to_run:
            st.error("Chưa có mã trường hợp lệ.")
            return

        progress = st.progress(0.0, text="Đang khởi tạo crawler...")
        status = st.empty()
        log_box = st.empty()
        logs: List[str] = []

        crawler = OnlineAdmissionCrawler()
        bundle = CrawlBundle()
        total = len(codes_to_run)

        for i, code in enumerate(codes_to_run, start=1):
            status.markdown(f"**[{i}/{total}]** Đang thu thập: `{code}` …")
            try:
                school_bundle = crawler.crawl_school_bundle(code, years=years, delay=0.35)
                n_adm = len(school_bundle.admissions)
                n_conv = len(school_bundle.conversions)
                n_reg = len(school_bundle.regulations)
                bundle.admissions.extend(school_bundle.admissions)
                bundle.conversions.extend(school_bundle.conversions)
                bundle.regulations.extend(school_bundle.regulations)
                msg = f"✓ {code}: {n_adm} ngành, {n_conv} quy đổi, {n_reg} quy chế"
            except Exception as e:
                msg = f"✗ {code}: lỗi — {e}"
            logs.append(msg)
            log_box.code("\n".join(logs[-20:]), language=None)
            progress.progress(i / total, text=f"Hoàn thành {i}/{total} trường")

        # Xuất Excel
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join("data", "output", f"tong_hop_tuyen_sinh_{ts}.xlsx")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        status.markdown("**Đang xuất file Excel…**")
        exporter = ExcelAdmissionExporter(years=sorted(years))
        exporter.export(
            bundle.admissions,
            output_path=out_path,
            conversions=bundle.conversions,
            regulations=bundle.regulations,
        )

        st.session_state.last_output_path = out_path
        st.session_state.last_stats = {
            "schools": total,
            "admissions": len(bundle.admissions),
            "conversions": len(bundle.conversions),
            "regulations": len(bundle.regulations),
            "path": out_path,
        }
        st.session_state.crawl_log = logs
        progress.progress(1.0, text="Hoàn tất!")
        st.success(
            f"Xong! {total} trường → {len(bundle.admissions)} bản ghi ngành, "
            f"{len(bundle.conversions)} dòng quy đổi, {len(bundle.regulations)} mục quy chế."
        )

    # Kết quả lần thu thập gần nhất
    stats = st.session_state.get("last_stats") or {}
    if stats.get("path") and os.path.exists(stats["path"]):
        st.markdown("---")
        st.markdown("### Kết quả gần nhất")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Trường đã thu thập", stats.get("schools", 0))
        m2.metric("Bản ghi ngành", stats.get("admissions", 0))
        m3.metric("Quy đổi", stats.get("conversions", 0))
        m4.metric("Quy chế", stats.get("regulations", 0))
        with open(stats["path"], "rb") as f:
            st.download_button(
                "⬇️ Tải file Excel tổng hợp",
                data=f,
                file_name=os.path.basename(stats["path"]),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            )
        st.caption(f"Đường dẫn: `{stats['path']}`")


# ---------------------------------------------------------------------------
# Bước 3 — File cục bộ (tùy chọn)
# ---------------------------------------------------------------------------
def render_step3():
    st.subheader("Bước 3 (tuỳ chọn) — Bóc tách file cục bộ")
    st.caption("Đặt file PDF / Excel / Word vào `data/input_files/` rồi bóc tách tại đây.")

    input_dir = st.text_input("Thư mục đầu vào", value="data/input_files")
    if st.button("📂 Bóc tách file trong thư mục", type="primary"):
        from main import process_local_files
        with st.spinner("Đang đọc tài liệu cục bộ..."):
            records = process_local_files(input_dir)
        if not records:
            st.warning("Không tìm thấy bản ghi nào.")
            return
        years = sorted({r.nam for r in records if r.nam})
        out_path = os.path.join("data", "output", "tong_hop_tu_file_cuc_bo.xlsx")
        ExcelAdmissionExporter(years=years or [2024, 2025, 2026]).export(records, output_path=out_path)
        st.success(f"Đã bóc tách {len(records)} bản ghi → `{out_path}`")
        with open(out_path, "rb") as f:
            st.download_button(
                "⬇️ Tải Excel",
                data=f,
                file_name="tong_hop_tu_file_cuc_bo.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


def main():
    _init_state()

    st.markdown(
        """
        <div class="hero">
          <h1>Tổng hợp dữ liệu tuyển sinh ĐH / CĐ</h1>
          <p>Bước 1 lấy danh sách mã trường → Copy → Bước 2 dán danh sách và thu thập điểm chuẩn, mã ngành, phương thức, quy chế, điểm quy đổi.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Nếu vừa bấm "Dùng danh sách ở Bước 2" → ưu tiên mở tab crawl
    default_tab = 0
    if st.session_state.pop("nav_to_crawl", None):
        default_tab = 1

    tabs = st.tabs([
        "① Danh sách mã trường",
        "② Thu thập dữ liệu",
        "③ File cục bộ",
        "ℹ Hướng dẫn",
    ])

    with tabs[0]:
        render_step1()
    with tabs[1]:
        render_step2()
    with tabs[2]:
        render_step3()
    with tabs[3]:
        st.markdown(
            """
### Quy trình khuyến nghị
1. Vào tab **Danh sách mã trường** → bấm **Tải danh sách trường**.
2. Lọc loại hình (ĐH / CĐ) nếu cần → **Copy** danh sách mã, hoặc bấm **Dùng danh sách này ở Bước 2**.
3. Vào tab **Thu thập dữ liệu** → dán mã (nếu chưa chuyển sẵn) → chọn năm → **Bắt đầu thu thập**.
4. Tải file Excel kết quả (có sheet điểm chuẩn, quy đổi, quy chế).

### Gợi ý
- Thử `--limit` / giới hạn 3–5 trường trước khi thu thập hàng trăm trường.
- Có thể dán mã theo nhiều định dạng: `BKA,NEU,FTU` hoặc mỗi dòng một mã.
- CLI vẫn dùng được: `python3 main.py --mode schools` / `--mode online --schools all`.
            """
        )

    # Streamlit không cho set active tab dễ dàng; default_tab chỉ là tín hiệu UX qua success message
    _ = default_tab


if __name__ == "__main__":
    main()
