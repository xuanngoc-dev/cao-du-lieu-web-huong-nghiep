# -*- coding: utf-8 -*-
"""
Module: crawlers.online_crawler
Mô tả: Thu thập dữ liệu tuyển sinh chỉ từ website chính thức của nhà trường.
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
from crawlers.official_site_crawler import OfficialSiteCrawler, lookup_local_school
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
    Thu thập điểm chuẩn, đề án và phương thức từ website chính thức của nhà trường.
    """

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
        self._refresh_directory = refresh_directory
        self.school_slugs: Optional[Dict[str, Dict[str, str]]] = None
        if refresh_directory:
            self._ensure_school_slugs()
        # Cache phụ: quy chế & bảng quy đổi theo mã trường (trong phiên chạy)
        self._dean_cache: Dict[str, Tuple[List[AdmissionRecord], List[ScoreConversionRecord], List[AdmissionRegulation]]] = {}

    def refresh_school_directory(
        self,
        include_dai_hoc: bool = True,
        include_cao_dang: bool = True,
    ) -> Dict[str, Dict]:
        """Nạp lại danh bạ cục bộ chứa website chính thức của trường."""
        self.school_slugs = self.directory.load(
            force_refresh=True,
            include_dai_hoc=include_dai_hoc,
            include_cao_dang=include_cao_dang,
        )
        return self.school_slugs

    def _ensure_school_slugs(self) -> Dict[str, Dict[str, str]]:
        """Nạp danh bạ online khi thực sự cần tra cứu mã/slug."""
        if self.school_slugs is None:
            self.school_slugs = self.directory.load(
                force_refresh=self._refresh_directory
            )
        return self.school_slugs

    def list_school_codes(
        self,
        school_type: Optional[str] = None,
    ) -> List[str]:
        """Trả về danh sách mã trường (all / dai_hoc / cao_dang / hoc_vien / dai_hoc_hoc_vien)."""
        if not self.directory.schools:
            self.directory.schools = self._ensure_school_slugs()
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

        slugs = self._ensure_school_slugs()
        # Tìm chính xác theo mã trường
        if clean_key in slugs:
            return slugs[clean_key]

        # Tìm gần đúng theo tên
        lower_key = keyword_or_code.strip().lower()
        for code, info in slugs.items():
            if lower_key in info["name"].lower() or lower_key in info["slug"].lower():
                return info

        return None

    def crawl_dean_data(
        self,
        school_code: str,
        school_name: str,
        slug: str,
    ) -> Tuple[List[AdmissionRecord], List[ScoreConversionRecord], List[AdmissionRegulation]]:
        """Thu thập đề án, quy chế và bảng chứng chỉ từ website trường."""
        if school_code in self._dean_cache:
            return self._dean_cache[school_code]

        empty = ([], [], [])
        info = lookup_local_school(school_code)
        website = (info or {}).get("website") or ""
        if not website:
            self._dean_cache[school_code] = empty
            return empty
        try:
            bundle = OfficialSiteCrawler().crawl(
                school_code=school_code,
                school_name=school_name,
                website=website,
                years=[2026],
                delay=0,
            )
            admissions = bundle.admissions
            conversions = bundle.conversions
            regulations = bundle.regulations
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

    def resolve_school_api_id(self, school_code: str, slug: str = "") -> Optional[int]:
        """API nguồn tổng hợp đã ngừng sử dụng."""
        return None

    def fetch_cutoff_scores_api(
        self,
        school_id: int,
        year: int,
        method_id: Optional[int] = None,
    ) -> List[Dict]:
        """API nguồn tổng hợp đã ngừng sử dụng."""
        return []

    def _append_cutoff_api_records(
        self,
        bundle: CrawlBundle,
        items: List[Dict],
        *,
        school_code: str,
        school_name: str,
        slug: str,
        year: int,
        regulation_by_method: Dict[str, str],
        regulation_all: str,
        conversion_summary: str,
        default_method: str = "",
    ) -> int:
        """Chuyển bản ghi API → AdmissionRecord. Trả về số bản ghi thêm."""
        added = 0
        for item in items:
            raw_ten = clean_text(item.get("name") or "")
            raw_ma = clean_text(item.get("code") or item.get("display_code") or "")
            raw_tohop = clean_text(item.get("block") or "")
            raw_ghichu = clean_text(item.get("introtext") or "")
            admission_label = clean_text(
                item.get("admission_name") or default_method or ""
            )
            row_method = (
                detect_admission_method(admission_label)
                or detect_admission_method(item.get("admission_alias") or "")
                or admission_label
                or "Điểm thi THPT"
            )

            if not raw_ma and raw_ten:
                raw_ma = self.major_resolver.resolve(
                    school_code=school_code,
                    major_name=raw_ten,
                    existing_code="",
                    school_slug=slug,
                )

            raw_mark = item.get("mark")
            diem = normalize_score(raw_mark)
            diem_ptxt = None
            if diem is None and raw_mark is not None:
                native = normalize_score_ptxt(raw_mark)
                if native is not None:
                    diem = native
                    diem_ptxt = native
            elif (
                diem is not None
                and row_method not in ("Điểm thi THPT",)
                and "học bạ" not in row_method.lower()
            ):
                diem_ptxt = diem

            chitieu = normalize_integer(item.get("quota"))

            if raw_ten and (diem is not None or diem_ptxt is not None or chitieu is not None):
                bundle.admissions.append(
                    AdmissionRecord(
                        ma_truong=school_code,
                        ten_truong=school_name,
                        ma_nganh=normalize_major_code(raw_ma),
                        ten_nganh=raw_ten,
                        nam=year,
                        to_hop=raw_tohop,
                        chi_tieu=chitieu,
                        diem_chuan=diem,
                        diem_chuan_ptxt=diem_ptxt,
                        phuong_thuc=row_method,
                        quy_che=regulation_by_method.get(row_method, regulation_all),
                        diem_quy_doi=conversion_summary,
                        ghi_chu=raw_ghichu,
                        nguon="Online: Tuyensinh247 API",
                    )
                )
                added += 1
        return added

    def crawl_school_bundle(
        self,
        school_code_or_name: str,
        years: List[int] = None,
        delay: float = 0.5,
        source_urls: Optional[List[str]] = None,
    ) -> CrawlBundle:
        """
        Thu thập điểm chuẩn, mã ngành, phương thức và quy chế từ website chính thức
        của nhà trường (không lấy từ cổng tuyển sinh bên ngoài).
        """
        years = years or [2021, 2022, 2023, 2024, 2025, 2026]
        school_info = self._school_for_official_crawl(school_code_or_name)
        if not school_info:
            bundle = CrawlBundle()
            bundle.source_note = f"Không tìm thấy trường '{school_code_or_name}' trong danh bạ"
            print(f"[CRAWLER] {bundle.source_note}.")
            return bundle

        school_code = school_info["code"]
        school_name = school_info["name"]
        website = school_info.get("website") or ""
        print(f"\n[CRAWLER] Bắt đầu thu thập từ website trường: {school_name} (Mã: {school_code})")
        return OfficialSiteCrawler().crawl(
            school_code=school_code,
            school_name=school_name,
            website=website,
            years=years,
            delay=delay,
            seed_urls=source_urls,
        )

    def _school_for_official_crawl(self, school_code_or_name: str) -> Optional[Dict[str, str]]:
        """Ưu tiên danh bạ cục bộ (đã có website). Chỉ tra danh bạ online khi thiếu."""
        raw = (school_code_or_name or "").strip()
        key = raw.upper()
        if key in self.COMMON_ALIASES:
            key = self.COMMON_ALIASES[key]
        local = lookup_local_school(key)
        if local and (local.get("website") or local.get("name")):
            return local
        info = self.find_school(raw)
        if not info:
            return None
        if not info.get("website"):
            extra = lookup_local_school(info.get("code") or "")
            if extra and extra.get("website"):
                info = dict(info)
                info["website"] = extra["website"]
        return info

    def list_admission_methods(self, school_code_or_name: str) -> List[Dict[str, str]]:
        """Lấy phương thức từ dữ liệu đã bóc trên website chính thức của trường."""
        bundle = self.crawl_school_bundle(
            school_code_or_name,
            years=[2026],
            delay=0,
        )
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
                "source": "website-truong",
            }
        for rec in bundle.admissions:
            _add(rec.phuong_thuc)

        order = ["THPT", "HOC_BA", "HSA", "V-ACT", "TSA", "KET_HOP", "DGNL", "SAT", "ACT", "XTTN"]
        out = list(methods.values())
        out.sort(key=lambda x: order.index(x["id"]) if x["id"] in order else 99)
        return out

    def crawl_school_data(
        self,
        school_code_or_name: str,
        years: List[int] = None,
        delay: float = 0.5,
        source_urls: Optional[List[str]] = None,
    ) -> List[AdmissionRecord]:
        """
        Thu thập dữ liệu điểm chuẩn (+ gắn mã ngành / PTXT / quy chế / quy đổi từ đề án).
        Giữ API cũ: trả về List[AdmissionRecord].
        """
        return self.crawl_school_bundle(
            school_code_or_name,
            years=years,
            delay=delay,
            source_urls=source_urls,
        ).admissions
