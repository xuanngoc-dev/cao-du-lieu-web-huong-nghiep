# -*- coding: utf-8 -*-
"""
Module: crawlers.online_crawler
Mô tả: Tự động tìm kiếm và cào dữ liệu điểm chuẩn tuyển sinh đại học từ các cổng trực tuyến
qua các năm 2021 đến 2025 theo Mã trường hoặc Tên trường.
"""

import os
import re
import json
import time
from typing import List, Dict, Optional, Tuple
import requests
from bs4 import BeautifulSoup

from core.models import (
    AdmissionRecord,
    ScoreConversionRecord,
    AdmissionRegulation,
    CrawlBundle,
)
from core.normalizer import (
    clean_text,
    normalize_school_code,
    normalize_major_code,
    normalize_score,
    normalize_score_ptxt,
    normalize_integer,
)
from parsers.base_parser import BaseParser
from core.major_resolver import MajorCodeResolver
from crawlers.dean_extractor import (
    DeanAdmissionExtractor,
    detect_admission_method,
    method_to_calc_id,
)
from crawlers.school_directory import SchoolDirectory


def _section_title_for_table(table) -> str:
    """Lấy tiêu đề phương thức gần bảng (ưu tiên h2/h3/h4, tránh nhầm thẻ p)."""
    for tag_name in ("h2", "h3", "h4"):
        prev = table.find_previous(tag_name)
        if prev:
            text = clean_text(prev.get_text(" ", strip=True))
            if text and len(text) < 220:
                return text
    for tag_name in ("strong", "b"):
        prev = table.find_previous(tag_name)
        if prev:
            text = clean_text(prev.get_text(" ", strip=True))
            if text and len(text) < 220 and detect_admission_method(text):
                return text
    return ""


class OnlineAdmissionCrawler:
    """
    Crawler thu thập điểm chuẩn trực tuyến từ cổng tuyển sinh công khai.
    Có cơ chế tự động tìm kiếm đường dẫn trường dựa trên Mã trường (VD: BKA, NEU, QHI).
    """

    BASE_URL = "https://diemthi.tuyensinh247.com"
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    def __init__(self, cache_dir: str = "data/cache", refresh_directory: bool = False):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self.major_resolver = MajorCodeResolver()
        self.dean_extractor = DeanAdmissionExtractor()
        self.directory = SchoolDirectory(cache_dir=cache_dir)
        self.school_slugs: Dict[str, Dict[str, str]] = self.directory.load(
            force_refresh=refresh_directory
        )
        # Cache phụ: quy chế & bảng quy đổi theo mã trường (trong phiên chạy)
        self._dean_cache: Dict[str, Tuple[List[AdmissionRecord], List[ScoreConversionRecord], List[AdmissionRegulation]]] = {}

    def refresh_school_directory(
        self,
        include_dai_hoc: bool = True,
        include_cao_dang: bool = True,
    ) -> Dict[str, Dict]:
        """Làm mới danh bạ mã trường từ web."""
        self.school_slugs = self.directory.load(
            force_refresh=True,
            include_dai_hoc=include_dai_hoc,
            include_cao_dang=include_cao_dang,
        )
        return self.school_slugs

    def list_school_codes(
        self,
        school_type: Optional[str] = None,
    ) -> List[str]:
        """Trả về danh sách mã trường (all / dai_hoc / cao_dang / hoc_vien / dai_hoc_hoc_vien)."""
        if not self.directory.schools:
            self.directory.schools = self.school_slugs
        return self.directory.get_codes(school_type=school_type)

    def _load_or_fetch_school_directory(self) -> Dict[str, Dict[str, str]]:
        """Giữ API cũ: ủy quyền cho SchoolDirectory."""
        return self.directory.load()

    # Bản đồ bí danh viết tắt thông dụng sang mã tuyển sinh chính thức
    COMMON_ALIASES = {
        "NEU": "KHA",       # ĐH Kinh tế Quốc dân
        "FTU": "NTH",       # ĐH Ngoại thương HN
        "FTU2": "NTS",      # ĐH Ngoại thương TP.HCM
        "HUST": "BKA",      # ĐH Bách khoa Hà Nội
        "HCMUT": "QSB",     # ĐH Bách khoa ĐHQG TP.HCM
        "UET": "QHI",       # ĐH Công nghệ ĐHQGHN
        "ULIS": "QHF",      # ĐH Ngoại ngữ ĐHQGHN
        "USSH": "QHX",      # ĐH KHXH&NV Hà Nội
        "HUS": "QHT",       # ĐH Khoa học Tự nhiên HN
        "AOF": "HTC",       # Học viện Tài chính
        "BA": "NHH",        # Học viện Ngân hàng
        "PTIT": "BVH",      # Học viện Công nghệ Bưu chính Viễn thông HN
        "TMU": "TMU",       # ĐH Thương Mại
        "DAV": "HVA",       # Học viện Ngoại giao
        "AJC": "HCH",       # Học viện Báo chí và Tuyên truyền
        "HNUE": "SPH",      # ĐH Sư phạm Hà Nội
        "HMU": "YHB",       # ĐH Y Hà Nội
        "UEH": "KSA",       # ĐH Kinh tế TP.HCM
        "UIT": "QSC",       # ĐH Công nghệ Thông tin ĐHQG TP.HCM
        "HCMUS": "QST",     # ĐH Khoa học Tự nhiên ĐHQG TP.HCM
    }

    def find_school(self, keyword_or_code: str) -> Optional[Dict[str, str]]:
        """
        Tìm kiếm trường theo mã (BKA, NEU,...) hoặc từ khóa tên trường.
        """
        clean_key = keyword_or_code.strip().upper()
        # Tra cứu qua bí danh nếu có (VD: NEU -> KHA, FTU -> NTH)
        if clean_key in self.COMMON_ALIASES:
            clean_key = self.COMMON_ALIASES[clean_key]

        # Tìm chính xác theo mã trường
        if clean_key in self.school_slugs:
            return self.school_slugs[clean_key]

        # Tìm gần đúng theo tên
        lower_key = keyword_or_code.strip().lower()
        for code, info in self.school_slugs.items():
            if lower_key in info["name"].lower() or lower_key in info["slug"].lower():
                return info

        return None

    def crawl_dean_data(
        self,
        school_code: str,
        school_name: str,
        slug: str,
    ) -> Tuple[List[AdmissionRecord], List[ScoreConversionRecord], List[AdmissionRegulation]]:
        """
        Cào trang Đề án tuyển sinh: mã ngành, phương thức, quy chế, bảng quy đổi chứng chỉ.
        """
        if school_code in self._dean_cache:
            return self._dean_cache[school_code]

        empty = ([], [], [])
        url = f"{self.BASE_URL}/de-an-tuyen-sinh/{slug}.html"
        try:
            print(f"[CRAWLER] Đang lấy Đề án tuyển sinh: {url}")
            res = requests.get(url, headers=self.HEADERS, timeout=20)
            if res.status_code != 200:
                self._dean_cache[school_code] = empty
                return empty

            admissions, conversions, regulations = self.dean_extractor.extract(
                html=res.text,
                school_code=school_code,
                school_name=school_name,
                source="Online: Đề án tuyển sinh",
            )
            print(
                f"[CRAWLER] Đề án {school_code}: "
                f"{len(admissions)} ngành/PTXT, "
                f"{len(conversions)} dòng quy đổi, "
                f"{len(regulations)} mục quy chế."
            )
            self._dean_cache[school_code] = (admissions, conversions, regulations)
            return admissions, conversions, regulations
        except Exception as e:
            print(f"[CẢNH BÁO] Không đọc được đề án {school_code}: {e}")
            self._dean_cache[school_code] = empty
            return empty

    def crawl_school_bundle(
        self,
        school_code_or_name: str,
        years: List[int] = None,
        delay: float = 0.5,
    ) -> CrawlBundle:
        """
        Cào đầy đủ: điểm chuẩn theo năm + đề án (mã ngành, PTXT, quy chế, điểm quy đổi).
        """
        years = years or [2021, 2022, 2023, 2024, 2025, 2026]
        bundle = CrawlBundle()
        school_info = self.find_school(school_code_or_name)

        if not school_info:
            print(f"[CRAWLER] Không tìm thấy trường với từ khóa '{school_code_or_name}'.")
            return bundle

        school_code = school_info["code"]
        school_name = school_info["name"]
        slug = school_info["slug"]
        print(f"\n[CRAWLER] Bắt đầu cào dữ liệu: {school_name} (Mã: {school_code})")

        # 1) Đề án: mã ngành, phương thức, quy chế, bảng quy đổi
        dean_admissions, conversions, regulations = self.crawl_dean_data(
            school_code, school_name, slug
        )
        bundle.conversions.extend(conversions)
        bundle.regulations.extend(regulations)

        # Nạp mapping mã ngành phục vụ resolve khi cào điểm chuẩn
        self.major_resolver.fetch_school_online_majors(slug, school_code)

        conversion_summary = ""
        if conversions:
            bits = []
            for c in conversions[:10]:
                if c.hang_muc and c.diem_quy_doi:
                    bits.append(f"{c.loai_bang}: {c.hang_muc}→{c.diem_quy_doi}")
            conversion_summary = "; ".join(bits)
            if len(conversions) > 10:
                conversion_summary += f" (+{len(conversions) - 10} dòng)"

        regulation_by_method: Dict[str, str] = {}
        regulation_all = ""
        if regulations:
            regulation_all = " | ".join(
                f"[{r.tieu_de}] {r.noi_dung[:180]}..."
                if len(r.noi_dung) > 180
                else f"[{r.tieu_de}] {r.noi_dung}"
                for r in regulations[:3]
            )
            for r in regulations:
                if r.phuong_thuc and r.phuong_thuc not in regulation_by_method:
                    regulation_by_method[r.phuong_thuc] = r.noi_dung[:500]

        # Gắn tóm tắt vào bản ghi ngành từ đề án
        for rec in dean_admissions:
            if not rec.diem_quy_doi:
                rec.diem_quy_doi = conversion_summary
            if not rec.quy_che:
                rec.quy_che = regulation_by_method.get(rec.phuong_thuc, regulation_all)
            bundle.admissions.append(rec)

        # 2) Điểm chuẩn theo từng năm / phương thức
        for yr in years:
            url = f"{self.BASE_URL}/diem-chuan/{slug}.html?y={yr}"
            try:
                res = requests.get(url, headers=self.HEADERS, timeout=12)
                if res.status_code != 200:
                    continue

                soup = BeautifulSoup(res.text, "html.parser")
                tables = soup.find_all("table")

                for table in tables:
                    section_title = _section_title_for_table(table)
                    method = detect_admission_method(section_title) or "Điểm thi THPT"

                    rows = table.find_all("tr")
                    if len(rows) < 2:
                        continue

                    headers = [clean_text(td.get_text()) for td in rows[0].find_all(["th", "td"])]
                    col_map = BaseParser.map_table_headers(headers)
                    # Nếu header có cột điểm theo thang PTXT → xác nhận lại phương thức
                    for h in headers:
                        m_from_h = detect_admission_method(h)
                        if m_from_h and m_from_h not in ("Điểm thi THPT",):
                            method = m_from_h
                            break

                    for tr in rows[1:]:
                        cells = [clean_text(td.get_text()) for td in tr.find_all(["th", "td"])]
                        if not cells or len(cells) < 2:
                            continue
                        if any("tuyensinh247" in c.lower() for c in cells):
                            continue

                        extracted = {}
                        for c_idx, f_name in col_map.items():
                            if c_idx < len(cells):
                                extracted[f_name] = cells[c_idx]

                        raw_ten = extracted.get("ten_nganh") or (cells[0] if len(cells) > 0 else "")
                        raw_ma = extracted.get("ma_nganh") or ""
                        raw_tohop = extracted.get("to_hop") or ""
                        raw_diem = extracted.get("diem_chuan") or ""
                        raw_diem_ptxt = extracted.get("diem_chuan_ptxt") or ""
                        raw_ghichu = extracted.get("ghi_chu") or ""
                        raw_method = extracted.get("phuong_thuc") or ""
                        row_method = method
                        if raw_method:
                            row_method = detect_admission_method(raw_method) or row_method

                        if not raw_ma:
                            raw_ma = self.major_resolver.resolve(
                                school_code=school_code,
                                major_name=raw_ten,
                                existing_code="",
                                school_slug=slug,
                            )

                        diem = normalize_score(raw_diem)
                        diem_ptxt = normalize_score_ptxt(raw_diem_ptxt) if raw_diem_ptxt else None

                        # Nhiều trường (vd BKA TSA) chỉ có 1 cột "Điểm chuẩn" trên thang PTXT
                        # (45.62, 55, …) — normalize_score thang THPT (≤45) sẽ loại → cứu bằng ptxt.
                        if raw_diem and diem is None:
                            native = normalize_score_ptxt(raw_diem)
                            if native is not None:
                                diem = native
                                if diem_ptxt is None:
                                    diem_ptxt = native
                        elif (
                            raw_diem
                            and diem is not None
                            and not raw_diem_ptxt
                            and diem_ptxt is None
                            and row_method
                            and row_method not in ("Điểm thi THPT",)
                            and "học bạ" not in row_method.lower()
                        ):
                            # Một cột điểm trong mục TSA/HSA/kết hợp/… → cũng gắn vào điểm PTXT
                            diem_ptxt = diem

                        chitieu = normalize_integer(extracted.get("chi_tieu"))
                        so_nv = normalize_integer(extracted.get("so_nguyen_vong"))

                        if raw_ten and (diem is not None or diem_ptxt is not None or chitieu is not None):
                            rec = AdmissionRecord(
                                ma_truong=school_code,
                                ten_truong=school_name,
                                ma_nganh=normalize_major_code(raw_ma),
                                ten_nganh=clean_text(raw_ten),
                                nam=yr,
                                to_hop=clean_text(raw_tohop),
                                chi_tieu=chitieu,
                                so_nguyen_vong=so_nv,
                                diem_chuan=diem,
                                diem_chuan_ptxt=diem_ptxt,
                                phuong_thuc=row_method,
                                quy_che=regulation_by_method.get(row_method, regulation_all),
                                diem_quy_doi=conversion_summary,
                                ghi_chu=clean_text(raw_ghichu),
                                nguon="Online: Tuyensinh247",
                            )
                            bundle.admissions.append(rec)

                time.sleep(delay)
            except Exception as e:
                print(f"[CẢNH BÁO] Lỗi khi cào năm {yr} của {school_code}: {e}")

        print(
            f"[CRAWLER] Hoàn thành {school_code}: "
            f"{len(bundle.admissions)} bản ghi ngành, "
            f"{len(bundle.conversions)} dòng quy đổi, "
            f"{len(bundle.regulations)} mục quy chế."
        )
        return bundle

    def list_admission_methods(self, school_code_or_name: str) -> List[Dict[str, str]]:
        """
        Lấy danh sách phương thức xét tuyển từ trang điểm chuẩn (nav + tiêu đề mục).
        Dùng cho dropdown máy tính quy đổi (vd NTH: THPT, học bạ, HSA, V-ACT, TSA, kết hợp).
        """
        info = self.find_school(school_code_or_name)
        if not info:
            return []
        slug = info.get("slug") or ""
        url = f"{self.BASE_URL}/diem-chuan/{slug}.html"
        methods: Dict[str, Dict[str, str]] = {}

        def _add(label_text: str):
            method_vn = detect_admission_method(label_text)
            if not method_vn:
                return
            mid = method_to_calc_id(method_vn)
            if not mid or mid in methods:
                return
            label_map = {
                "THPT": "Điểm thi THPT",
                "HOC_BA": "Điểm học bạ",
                "HSA": "Điểm ĐGNL HSA",
                "V-ACT": "Điểm ĐGNL V-ACT",
                "TSA": "Điểm ĐGTD TSA",
                "KET_HOP": "Điểm xét tuyển kết hợp",
                "DGNL": "Điểm ĐGNL",
            }
            methods[mid] = {
                "id": mid,
                "label": label_map.get(mid, method_vn),
                "column": "",
                "source": "diem-chuan",
            }

        try:
            res = requests.get(url, headers=self.HEADERS, timeout=15)
            if res.status_code != 200:
                return []
            soup = BeautifulSoup(res.text, "html.parser")

            # 1) Nav nhanh: #diem-thi-thpt, #diem-thi-dgnl-hn, ...
            for a in soup.find_all("a", href=True):
                href = (a.get("href") or "").strip()
                if not href.startswith("#"):
                    continue
                text = clean_text(a.get_text(" ", strip=True)).lstrip("✯ ").strip()
                if text:
                    _add(text)

            # 2) Tiêu đề mục bảng điểm chuẩn
            for tag in soup.find_all(["h2", "h3", "h4"]):
                text = clean_text(tag.get_text(" ", strip=True))
                if "phương thức" in text.lower() or detect_admission_method(text):
                    _add(text)
        except Exception as e:
            print(f"[CẢNH BÁO] Không lấy được PTXT từ điểm chuẩn {info.get('code')}: {e}")
            return []

        order = ["THPT", "HOC_BA", "HSA", "V-ACT", "TSA", "KET_HOP", "DGNL", "SAT", "ACT", "XTTN"]
        out = list(methods.values())
        out.sort(key=lambda x: order.index(x["id"]) if x["id"] in order else 99)
        return out

    def crawl_school_data(
        self,
        school_code_or_name: str,
        years: List[int] = None,
        delay: float = 0.5,
    ) -> List[AdmissionRecord]:
        """
        Cào dữ liệu điểm chuẩn (+ gắn mã ngành / PTXT / quy chế / quy đổi từ đề án).
        Giữ API cũ: trả về List[AdmissionRecord].
        """
        return self.crawl_school_bundle(school_code_or_name, years=years, delay=delay).admissions
