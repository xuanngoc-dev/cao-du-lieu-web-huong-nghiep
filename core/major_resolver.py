# -*- coding: utf-8 -*-
"""
Module: core.major_resolver
Mô tả: Bộ tra cứu và chuẩn hóa mã ngành (Mã xét tuyển riêng của từng trường hoặc Mã chuẩn Bộ GD&ĐT 7 chữ số).
Đảm bảo khi xuất ra file Excel, tất cả các ngành của mỗi trường đều có mã ngành tương ứng rõ ràng.
"""

import os
import re
import json
from typing import Dict, Optional
import requests
from bs4 import BeautifulSoup

from core.normalizer import clean_text, strip_accents, normalize_school_code


class MajorCodeResolver:
    """
    Bộ giải mã và bổ sung Mã ngành thông minh:
    1. Quét từ trang Đề án tuyển sinh trực tuyến của trường trên Tuyensinh247.
    2. Áp dụng bảng mã tuyển sinh đặc thù của các trường hàng đầu (BKA, FTU, QHI,...).
    3. Áp dụng Danh mục mã ngành chuẩn 7 chữ số của Bộ GD&ĐT (Thông tư 09/2022/TT-BGDĐT).
    """

    CACHE_FILE = "data/cache/school_majors_cache.json"

    # Danh mục mã ngành chuẩn Bộ GD&ĐT (Chuẩn 7 chữ số)
    NATIONAL_MAJOR_CODES = {
        # Máy tính & Công nghệ thông tin
        "khoa hoc may tinh": "7480101",
        "mang may tinh va truyen thong du lieu": "7480102",
        "ky thuat phan mem": "7480103",
        "he thong thong tin": "7480104",
        "ky thuat may tinh": "7480106",
        "tri tue nhan tao": "7480107",
        "khoa hoc du lieu": "7480108",
        "an toan thong tin": "7480109",
        "cong nghe thong tin": "7480201",

        # Kinh doanh & Quản lý
        "quan tri kinh doanh": "7340101",
        "marketing": "7340115",
        "bat dong san": "7340116",
        "kinh doanh quoc te": "7340120",
        "kinh doanh thuong mai": "7340121",
        "thuong mai dien tu": "7340122",
        "tai chinh - ngan hang": "7340201",
        "tai chinh ngan hang": "7340201",
        "tai chinh doanh nghiep": "7340201",
        "ngan hang": "7340201",
        "bao hiem": "7340204",
        "cong nghe tai chinh": "7340205",
        "fintech": "7340205",
        "ke toan": "7340301",
        "kiem toan": "7340302",
        "quan tri nhan luc": "7340401",
        "he thong thong tin quan ly": "7340405",
        "quan tri van phong": "7340406",
        "logistics va quan ly chuoi cung ung": "7340404",
        "logistics": "7340404",

        # Kinh tế
        "kinh te": "7310101",
        "kinh te hoc": "7310101",
        "kinh te phat trien": "7310102",
        "kinh te quoc te": "7310104",
        "kinh te doi ngoai": "7310104",
        "kinh te dau tu": "7310105",
        "thong ke kinh te": "7310108",
        "toan kinh te": "7310107",

        # Luật
        "luat": "7380101",
        "luat kinh te": "7380107",
        "luat quoc te": "7380108",
        "luat thuong mai quoc te": "7380109",

        # Ngôn ngữ
        "ngon ngu anh": "7220201",
        "tieng anh": "7220201",
        "ngon ngu nga": "7220202",
        "ngon ngu phap": "7220203",
        "ngon ngu trung quoc": "7220204",
        "tieng trung": "7220204",
        "ngon ngu nhat": "7220206",
        "tieng nhat": "7220206",
        "ngon ngu han quoc": "7220209",
        "tieng han": "7220209",

        # Kỹ thuật & Công nghệ
        "ky thuat co khi": "7520103",
        "ky thuat co dien tu": "7520114",
        "ky thuat nhiet": "7520115",
        "ky thuat oto": "7520130",
        "ky thuat dien": "7520201",
        "ky thuat dien tu - vien thong": "7520207",
        "ky thuat dien tu vien thong": "7520207",
        "ky thuat dieu khien va tu dong hoa": "7520216",
        "tu dong hoa": "7520216",
        "ky thuat hoa hoc": "7520301",
        "ky thuat sinh hoc": "7520401",
        "ky thuat thuc pham": "7540101",
        "cong nghe thuc pham": "7540101",
        "ky thuat moi truong": "7520320",
        "ky thuat vat lieu": "7520503",
        "ky thuat xay dung": "7580201",
        "kien truc": "7580101",
        "cong nghe ky thuat dien, dien tu": "7510401",
        "cong nghe ky thuat co khi": "7510201",
        "cong nghe ky thuat oto": "7510205",
        "cong nghe sinh hoc": "7420201",
        "hoa hoc": "7440112",
        "toan hoc": "7460101",
        "vat ly hoc": "7440102",

        # Y - Dược
        "y khoa": "7720101",
        "y hoc co truyen": "7720115",
        "rang - ham - mat": "7720501",
        "duoc hoc": "7720201",
        "dieu duong": "7720301",
        "ky thuat xet nghiem y hoc": "7720601",
        "y te cong cong": "7720701",

        # Sư phạm
        "giao duc mam non": "7140101",
        "giao duc tieu hoc": "7140102",
        "su pham toan hoc": "7140201",
        "su pham tin hoc": "7140202",
        "su pham vat ly": "7140203",
        "su pham hoa hoc": "7140204",
        "su pham sinh hoc": "7140205",
        "su pham ngu van": "7140209",
        "su pham tieng anh": "7140217",

        # Báo chí - Truyền thông & Xã hội
        "bao chi": "7320101",
        "truyen thong da phuong tien": "7320104",
        "quan he cong chung": "7320105",
        "tam ly hoc": "7310401",
        "xa hoi hoc": "7310301",
        "quan ly nha nuoc": "7340409",
        "quan tri dich vu du lich va lu hanh": "7810103",
        "quan tri khach san": "7810201",
        "quan tri nha hang va dich vu an uong": "7810202",
    }

    # Bảng mã riêng đặc thù của các trường hàng đầu
    SCHOOL_SPECIFIC_CODES = {
        "BKA": {
            "khoa hoc may tinh": "IT1",
            "ky thuat may tinh": "IT2",
            "cong nghe thong tin viet - nhat": "IT-E6",
            "cong nghe thong tin viet nhat": "IT-E6",
            "an toan khong gian so": "IT-E7",
            "cyber security": "IT-E7",
            "khoa hoc du lieu va tri tue nhan tao": "IT-E10",
            "ky thuat dien": "EE1",
            "ky thuat dieu khien va tu dong hoa": "EE2",
            "ky thuat dien tu - vien thong": "ET1",
            "ky thuat dien tu vien thong": "ET1",
            "ky thuat co dien tu": "ME1",
            "ky thuat co khi": "ME2",
            "ky thuat oto": "TE1",
            "ky thuat hoa hoc": "CH1",
            "hoa hoc": "CH2",
            "ky thuat sinh hoc": "BF1",
            "ky thuat thuc pham": "BF2",
            "ky thuat vat lieu": "MS1",
            "quan ly cong nghiep": "EM1",
            "quan tri kinh doanh": "EM2",
            "ke toan": "EM3",
            "tai chinh - ngan hang": "EM4",
            "tieng anh khoa hoc ky thuat va cong nghe": "FL1",
            "tieng anh chuyen nghiep quoc te": "FL2",
            "toan - tin": "MI1",
            "toan tin": "MI1",
            "he thong thong tin quan ly": "MI2",
            "vat ly ky thuat": "PH1",
            "ky thuat hat nhan": "PH2",
            "ky thuat in": "PR1",
            "ky thuat det may": "TX1",
        },
        "QHI": {
            "cong nghe thong tin": "CN1",
            "he thong thong tin": "CN2",
            "ky thuat dieu khien va tu dong hoa": "CN3",
            "cong nghe ky thuat dien tu - vien thong": "CN4",
            "cong nghe ky thuat co dien tu": "CN5",
            "cong nghe ky thuat xay dung": "CN6",
            "cong nghe hang khong vu tru": "CN7",
            "khoa hoc may tinh": "CN8",
            "ky thuat may tinh": "CN9",
            "ky thuat nang luong": "CN10",
            "mang may tinh va truyen thong du lieu": "CN11",
            "cong nghe nong nghiep": "CN12",
            "tri tue nhan tao": "CN14",
            "khoa hoc du lieu": "CN15",
            "ky thuat robot": "CN16",
        },
        "NTH": {
            "kinh te": "NTH01",
            "kinh te doi ngoai": "NTH01",
            "kinh te quoc te": "NTH02",
            "kinh doanh quoc te": "NTH03",
            "quan tri kinh doanh": "NTH04",
            "tai chinh - ngan hang": "NTH05",
            "tai chinh ngan hang": "NTH05",
            "ke toan": "NTH06",
            "luat": "NTH07",
            "luat thuong mai quoc te": "NTH07",
            "ngon ngu anh": "NTH08",
            "tieng anh thuong mai": "NTH08",
            "ngon ngu phap": "NTH09",
            "ngon ngu trung quoc": "NTH10",
            "ngon ngu nhat": "NTH11",
        }
    }

    def __init__(self):
        os.makedirs(os.path.dirname(self.CACHE_FILE), exist_ok=True)
        self.cached_mappings: Dict[str, Dict[str, str]] = self._load_cache()

    def _load_cache(self) -> Dict[str, Dict[str, str]]:
        if os.path.exists(self.CACHE_FILE):
            try:
                with open(self.CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_cache(self):
        try:
            with open(self.CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.cached_mappings, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def fetch_school_online_majors(self, slug: str, school_code: str) -> Dict[str, str]:
        """
        thu thập bảng mã ngành chính thức từ trang đề án tuyển sinh của trường (tuyensinh247).
        """
        norm_code = normalize_school_code(school_code)
        if norm_code in self.cached_mappings and len(self.cached_mappings[norm_code]) > 5:
            return self.cached_mappings[norm_code]

        mapping: Dict[str, str] = {}
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0.0.0 Safari/537.36"
        }
        url = f"https://diemthi.tuyensinh247.com/de-an-tuyen-sinh/{slug}.html"
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, "html.parser")
                for tbl in soup.find_all("table"):
                    rows = tbl.find_all("tr")
                    if not rows:
                        continue
                    header_cells = [clean_text(td.get_text()).lower() for td in rows[0].find_all(["th", "td"])]
                    code_idx = -1
                    name_idx = -1
                    for idx, h in enumerate(header_cells):
                        h_no = strip_accents(h)
                        if "ma nganh" in h_no or "ma xet tuyen" in h_no:
                            code_idx = idx
                        if "ten nganh" in h_no:
                            name_idx = idx

                    if code_idx != -1 and name_idx != -1:
                        for r in rows[1:]:
                            cells = [clean_text(td.get_text()) for td in r.find_all(["th", "td"])]
                            if len(cells) > max(code_idx, name_idx):
                                c_code = cells[code_idx].strip()
                                c_name = cells[name_idx].strip()
                                if c_code and c_name and "mã ngành" not in c_code.lower():
                                    mapping[c_name] = c_code
        except Exception:
            pass

        if mapping:
            self.cached_mappings[norm_code] = mapping
            self._save_cache()

        return mapping

    def clean_major_name_for_matching(self, name: str) -> str:
        """
        Loại bỏ các hậu tố chương trình như (CT tiên tiến), (CLC), - POHE,...
        để đối sánh với danh mục ngành gốc.
        """
        cleaned = clean_text(name)
        # Loại bỏ ngoặc đơn/kép chứa thông tin chương trình
        cleaned = re.sub(r"\(.*?(tiên tiến|clc|chất lượng cao|pohe|chuẩn|tiếng anh|quốc tế|liên kết|cử nhân|kỹ sư|định hướng).*?\)", "", cleaned, flags=re.IGNORECASE)
        # Loại bỏ tiền tố/hậu tố dạng: 'CNTT: ', ' - CLC', ' (mới)'
        cleaned = re.sub(r"^(cntt[:\s\-]+|ngành\s+)", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"(\s*-\s*(clc|chất lượng cao|tiên tiến)|\s*\(mới\))", "", cleaned, flags=re.IGNORECASE)
        return clean_text(cleaned)

    def resolve(
        self,
        school_code: str,
        major_name: str,
        existing_code: str = "",
        school_slug: str = ""
    ) -> str:
        """
        Trả về Mã ngành chuẩn xác nhất cho trường và ngành tương ứng.
        """
        # 1. Nếu bản ghi đã có mã ngành hợp lệ
        if existing_code and len(existing_code) >= 2:
            return existing_code.strip().upper()

        if not major_name:
            return ""

        norm_school = normalize_school_code(school_code)

        # 2. Tìm mã ngành nếu bị nhúng trong tên ngành (VD: 'Khoa học máy tính (IT1)', 'CNTT [7480201]')
        match_7 = re.search(r"\b(7\d{6})\b", major_name)
        if match_7:
            return match_7.group(1)
        match_brk = re.search(r"\(([A-Z0-9_\-]{2,8})\)", major_name)
        if match_brk:
            return match_brk.group(1).upper()

        # 3. Tra trong cache online thu thập từ đề án tuyển sinh của trường
        if school_slug and norm_school not in self.cached_mappings:
            self.fetch_school_online_majors(school_slug, norm_school)

        online_map = self.cached_mappings.get(norm_school, {})
        # Đối sánh trực tiếp tên
        if major_name in online_map:
            return online_map[major_name]

        cleaned_name = self.clean_major_name_for_matching(major_name)
        no_acc_name = strip_accents(cleaned_name)

        # Đối sánh gần đúng trong online map
        for k_name, k_code in online_map.items():
            if no_acc_name == strip_accents(self.clean_major_name_for_matching(k_name)):
                return k_code

        # 4. Tra trong bảng mã riêng của trường (BKA, QHI, NTH,...)
        spec_map = self.SCHOOL_SPECIFIC_CODES.get(norm_school, {})
        for spec_key, spec_code in spec_map.items():
            if spec_key in no_acc_name or no_acc_name in spec_key:
                return spec_code

        # 5. Tra trong Danh mục mã ngành chuẩn 7 chữ số của Bộ GD&ĐT
        # 5a. So khớp chính xác theo tên không dấu
        if no_acc_name in self.NATIONAL_MAJOR_CODES:
            return self.NATIONAL_MAJOR_CODES[no_acc_name]

        # 5b. So khớp từ khóa / chuỗi con
        for nat_key, nat_code in self.NATIONAL_MAJOR_CODES.items():
            if nat_key in no_acc_name or no_acc_name in nat_key:
                return nat_code

        return ""
