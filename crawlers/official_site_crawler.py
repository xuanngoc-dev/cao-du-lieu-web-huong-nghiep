# -*- coding: utf-8 -*-
"""
Thu thập dữ liệu tuyển sinh từ website chính thức của nhà trường.

Đi từ URL website trong danh bạ, mở các trang tuyển sinh đại học trên cùng tên miền
(kể cả tên miền phụ), rồi bóc bảng HTML và tệp PDF / Word / Excel do trường đăng.
"""

import os
import re
import json
import time
import tempfile
from html import escape
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import quote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from core.models import (
    AdmissionRecord,
    AdmissionRegulation,
    CrawlBundle,
    ScoreConversionRecord,
)
from core.normalizer import clean_text, strip_accents
from crawlers.dean_extractor import DeanAdmissionExtractor
from crawlers.cutoff_image_parser import records_from_cutoff_image
from crawlers.table_image_parser import _cluster_rows, ocr_tokens
from parsers.docx_parser import DocxAdmissionParser
from parsers.excel_parser import ExcelAdmissionParser
from parsers.pdf_parser import PdfAdmissionParser


_SCHOOLS_JSON = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "config", "schools_all.json")
)
_LOCAL_SCHOOLS: Optional[Dict[str, Dict]] = None

_DOC_RE = re.compile(r"\.(pdf|docx|xlsx|xls)(?:$|[?#])", re.IGNORECASE)
_SKIP_EXT_RE = re.compile(
    r"\.(jpg|jpeg|png|gif|webp|svg|css|js|zip|rar|mp4|mp3|ico)(?:$|[?#])",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(201[6-9]|202[0-9])\b")
_MAJOR_CODE_RE = re.compile(r"^[A-Z]{2,4}\d{2,8}$")
_NUM_MAJOR_RE = re.compile(r"\d{6,8}")

# Điểm càng cao càng ưu tiên trang / tệp tuyển sinh đại học chính quy.
_POS_HINTS = (
    ("diem chuan", 9),
    ("diem trung tuyen", 9),
    ("de an tuyen sinh", 8),
    ("thong tin tuyen sinh", 8),
    ("phuong an tuyen sinh", 7),
    ("tuyen sinh dai hoc", 7),
    ("nganh tuyen sinh", 6),
    ("chi tieu tuyen sinh", 6),
    ("xet tuyen", 5),
    ("chi tieu", 4),
    ("tuyen sinh", 4),
    ("chinh quy", 2),
    ("dai hoc", 1),
    ("danh gia tu duy", 8),
    ("dgtd", 6),
    ("ky thi danh gia", 6),
)
_NEG_HINTS = (
    ("tuyen dung", 25),
    ("thac si", 14),
    ("tien si", 14),
    ("cao hoc", 12),
    ("sau dai hoc", 12),
    ("lien thong", 10),
    ("mba", 8),
    ("viec lam", 10),
    ("ncs", 6),
    ("du bao", 14),
)


def _load_local_schools() -> Dict[str, Dict]:
    global _LOCAL_SCHOOLS
    if _LOCAL_SCHOOLS is not None:
        return _LOCAL_SCHOOLS
    found: Dict[str, Dict] = {}
    try:
        with open(_SCHOOLS_JSON, "r", encoding="utf-8") as f:
            payload = json.load(f)
        for row in payload.get("schools") or []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code") or "").strip().upper()
            if not code:
                continue
            found[code] = {
                "code": code,
                "name": row.get("name") or row.get("short_name") or code,
                "slug": row.get("online_slug") or "",
                "website": row.get("website") or "",
                "type": row.get("type") or "",
            }
    except Exception:
        found = {}
    _LOCAL_SCHOOLS = found
    return found


def lookup_local_school(code: str) -> Optional[Dict]:
    """Tra trường trong config/schools_all.json (đã có website chính thức)."""
    key = (code or "").strip().upper()
    if not key:
        return None
    return _load_local_schools().get(key)


def normalize_site_url(raw: str) -> str:
    """Chuẩn hóa chuỗi website thành URL http(s)."""
    text = clean_text(raw)
    if not text:
        return ""
    match = re.search(r"https?://[^\s,;]+", text, re.IGNORECASE)
    if match:
        return match.group(0).rstrip(".,)")
    match = re.search(
        r"(?:www\.)?[a-z0-9][-a-z0-9.]*\.[a-z]{2,}(?:/[^\s]*)?",
        text,
        re.IGNORECASE,
    )
    if match:
        return "https://" + match.group(0).rstrip(".,)")
    return ""


def _registrable_host(host: str) -> str:
    host = (host or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 3 and parts[-2] in {"edu", "com", "gov", "org", "net", "ac"}:
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def _rewrite_internal_file_href(href: str, page_url: str) -> str:
    """Đổi link file trên máy chủ nội bộ (ajc-app:1002) sang đúng website trường."""
    parsed = urlparse(href)
    if parsed.scheme not in ("http", "https"):
        return href
    host = parsed.netloc.lower().split(":")[0]
    if "." in host and not host.endswith(".local"):
        return href
    public = urlparse(page_url)
    if not public.netloc:
        return href
    return urlunparse(parsed._replace(scheme=public.scheme or "https", netloc=public.netloc))


def _same_org(url: str, base_host: str) -> bool:
    """Cùng website trường, kể cả tên miền phụ của chính host đó.

    ussh.vnu.edu.vn không nhận tuyensinh.vnu.edu.vn (cổng của ĐHQG, không phải trường).
    """
    host = urlparse(url).netloc.lower().split(":")[0]
    base = (base_host or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if base.startswith("www."):
        base = base[4:]
    if not host or not base:
        return False
    if host == base or host.endswith("." + base):
        return True
    org = _registrable_host(base)
    if base == org and (host == org or host.endswith("." + org)):
        return True
    return False


def filter_official_urls(website: str, urls: List[str]) -> Tuple[List[str], List[str]]:
    """Chỉ nhận URL http(s) cùng tên miền tổ chức với website chính thức."""
    site = normalize_site_url(website)
    base_host = urlparse(site).netloc
    accepted: List[str] = []
    rejected: List[str] = []
    for raw in urls or []:
        url = normalize_site_url(str(raw or "").strip())
        if not url or not _same_org(url, base_host):
            if str(raw or "").strip():
                rejected.append(str(raw).strip())
            continue
        if url not in accepted:
            accepted.append(url)
    return accepted, rejected


def _canon(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path}{query}".rstrip("/")


def _blob(*parts: str) -> str:
    text = strip_accents(" ".join(p for p in parts if p))
    return re.sub(r"[^a-z0-9]+", " ", text)


def _years_in(*parts: str) -> List[int]:
    found = []
    for part in parts:
        for match in _YEAR_RE.findall(part or ""):
            year = int(match)
            if year not in found:
                found.append(year)
    return found


def _host_bonus(url: str) -> int:
    """Cổng tuyển sinh (ts., tuyensinh.) ưu tiên hơn trang tin chung của trường."""
    host = urlparse(url).netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if host.startswith(("ts.", "tuyensinh.", "tuyensinhdh.", "admission.", "admissions.")):
        return 15
    return 0


def admission_portal_urls(site: str) -> List[str]:
    host = urlparse(site).netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    org = host or _registrable_host(urlparse(site).netloc)
    if not org:
        return []
    return [
        f"https://{prefix}.{org}/"
        for prefix in ("ts", "tuyensinh", "tuyensinhdh", "admission", "daotao")
    ]


def _quota_name_ok(name: str) -> bool:
    """Bỏ dòng OCR lệch cột (mã K46. nằm trong tên ngành, mảnh tiêu đề bảng)."""
    folded = strip_accents(name or "").lower().strip()
    if not folded:
        return False
    if re.search(r"k\d{2}\.", folded):
        return False
    if "nhom nganh" in folded or folded.endswith("nhom"):
        return False
    if folded.startswith(("nganh ", "hinh", "cau,", "cau ")):
        return False
    return True


def _score_text(text: str, years: List[int]) -> int:
    score = 0
    for hint, weight in _POS_HINTS:
        if hint in text:
            score += weight
    for hint, weight in _NEG_HINTS:
        if hint in text:
            score -= weight
    mentioned = _years_in(text)
    if mentioned:
        if any(y in years for y in mentioned):
            score += 3
        else:
            score -= 8
    return score


class OfficialSiteCrawler:
    """Bóc dữ liệu tuyển sinh đại học trên website của chính nhà trường."""

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    MAX_HTML_PAGES = 16
    MAX_DOCS = 3
    MAX_DOC_BYTES = 12 * 1024 * 1024
    MAX_PDF_PAGES = 20

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.dean = DeanAdmissionExtractor()
        self._insecure_ssl = False

    def crawl(
        self,
        school_code: str,
        school_name: str,
        website: str,
        years: List[int],
        delay: float = 0.2,
        seed_urls: Optional[List[str]] = None,
        trusted_urls: Optional[List[str]] = None,
    ) -> CrawlBundle:
        bundle = CrawlBundle()
        site = normalize_site_url(website)
        if not site:
            for raw in trusted_urls or []:
                site = normalize_site_url(str(raw or "").strip())
                if site:
                    break
        if not site:
            bundle.source_note = "Không có website chính thức trong danh bạ"
            print(f"[CRAWLER] {school_code}: {bundle.source_note}")
            return bundle

        years = sorted({int(y) for y in (years or [])})
        if not years:
            years = [2024, 2025, 2026]

        base = urlparse(site)
        org_host = base.netloc
        accepted_seeds, rejected_seeds = filter_official_urls(site, seed_urls or [])
        for raw in trusted_urls or []:
            url = normalize_site_url(str(raw or "").strip())
            if url and url not in accepted_seeds:
                accepted_seeds.append(url)
        print(f"[CRAWLER] Thu thập từ website trường {school_code}: {site}")

        pages_read = 0
        docs_read = 0
        seen_html: Set[str] = set()
        seen_docs: Set[str] = set()
        doc_queue: List[Tuple[int, str, str]] = []

        try:
            home = self._fetch(site, timeout=8)
        except Exception as e:
            home = None
            print(f"[CẢNH BÁO] Không mở được trang chủ {site}: {e}")
        if home is None and urlparse(site).netloc.lower().startswith("www."):
            bare = site.replace("://www.", "://", 1)
            print(f"[CẢNH BÁO] {school_code}: thử lại không có www — {bare}")
            try:
                home = self._fetch(bare, timeout=12)
            except Exception as e:
                print(f"[CẢNH BÁO] Không mở được {bare}: {e}")
            if home is not None:
                site = home.url or bare
                base = urlparse(site)
                org_host = base.netloc

        queue: List[Tuple[int, str]] = []
        home_url = site
        if home is not None:
            home_url = home.url or site
            home_canon = _canon(home_url)
            if home_canon:
                seen_html.add(home_canon)
            self._harvest(home, site, org_host, years, queue, doc_queue, seen_html)
        elif not accepted_seeds:
            print(
                f"[CẢNH BÁO] {school_code}: trang chủ không phản hồi, "
                "tiếp tục tìm trên cổng tuyển sinh (tên miền phụ)"
            )
        for seed_url in accepted_seeds:
            if _DOC_RE.search(seed_url):
                doc_queue.append((120, seed_url, seed_url))
            else:
                queue.append((120, seed_url))
        if rejected_seeds:
            print(
                f"[CẢNH BÁO] {school_code}: bỏ qua {len(rejected_seeds)} URL "
                "không thuộc website chính thức của trường"
            )
        # Trang chủ chỉ dùng để tìm đường dẫn; vẫn bóc nếu chính nó là cổng tuyển sinh.
        home_score = _score_text(_blob(site, home_url), years)
        if home is not None and home_score >= 4:
            self._extract_html(
                home, school_code, school_name, years, bundle, page_year=self._page_year(home, years)
            )

        queue.sort(key=lambda item: item[0], reverse=True)
        for portal in admission_portal_urls(site):
            queue.append((70, portal))
        pending = queue
        while pending and pages_read < self.MAX_HTML_PAGES:
            pending.sort(key=lambda item: item[0], reverse=True)
            score, url = pending.pop(0)
            canon = _canon(url)
            if not canon or canon in seen_html or score < 4:
                continue
            seen_html.add(canon)
            try:
                page = self._fetch(url, timeout=8 if _host_bonus(url) else 18)
            except Exception as e:
                print(f"[CẢNH BÁO] Bỏ qua {url}: {e}")
                continue
            if page is None:
                continue
            final_url = page.url or url
            if not _same_org(final_url, org_host):
                print(f"[CẢNH BÁO] Bỏ qua URL chuyển hướng ra ngoài website trường: {final_url}")
                continue
            final_canon = _canon(final_url)
            if final_canon:
                seen_html.add(final_canon)
            pages_read += 1
            if delay:
                time.sleep(delay)

            content_type = (page.headers.get("Content-Type") or "").lower()
            if "pdf" in content_type or _DOC_RE.search(final_url):
                doc_queue.append((score, final_url, final_url))
                continue
            if "html" not in content_type and "text" not in content_type:
                continue

            page_year = self._page_year(page, years)
            if page_year and page_year not in years:
                self._harvest(page, final_url, org_host, years, pending, doc_queue, seen_html)
                continue

            added = self._extract_html(
                page, school_code, school_name, years, bundle, page_year=page_year
            )
            print(f"[CRAWLER] {school_code} trang {final_url}: +{added} bản ghi")
            self._harvest(page, final_url, org_host, years, pending, doc_queue, seen_html)

        doc_queue.sort(key=lambda item: item[0], reverse=True)
        for score, doc_url, doc_text in doc_queue:
            if docs_read >= self.MAX_DOCS or score < 4:
                break
            canon = _canon(doc_url)
            if not canon or canon in seen_docs:
                continue
            seen_docs.add(canon)
            mentioned = _years_in(doc_text, doc_url)
            if mentioned and not any(y in years for y in mentioned):
                continue
            doc_year = next((y for y in mentioned if y in years), None)
            try:
                count = self._extract_document(
                    doc_url,
                    school_code,
                    school_name,
                    years,
                    bundle,
                    fallback_year=doc_year or max(years),
                )
            except Exception as e:
                print(f"[CẢNH BÁO] Không đọc tệp {doc_url}: {e}")
                continue
            if count:
                docs_read += 1
                print(f"[CRAWLER] {school_code} tệp {doc_url}: +{count} bản ghi")

        self._filter_years(bundle, years)
        host = urlparse(site).netloc or site
        if bundle.admissions or bundle.conversions or bundle.regulations:
            bundle.source_note = f"Website trường {host} — {pages_read} trang, {docs_read} tệp"
        else:
            bundle.source_note = (
                f"Website trường {host} — không thấy bảng hoặc tệp tuyển sinh đại học"
            )
        print(
            f"[CRAWLER] Hoàn thành {school_code}: "
            f"{len(bundle.admissions)} bản ghi, "
            f"{len(bundle.conversions)} quy đổi, "
            f"{len(bundle.regulations)} quy chế. {bundle.source_note}"
        )
        return bundle

    def _fetch(self, url: str, timeout: int = 18) -> Optional[requests.Response]:
        try:
            res = self.session.get(url, timeout=timeout, allow_redirects=True)
        except requests.exceptions.SSLError:
            if not self._insecure_ssl:
                print(f"[CẢNH BÁO] Chứng chỉ SSL của website trường không hợp lệ, thử lại: {url}")
                self._insecure_ssl = True
                try:
                    import urllib3
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                except Exception:
                    pass
            res = self.session.get(url, timeout=timeout, allow_redirects=True, verify=False)
        except requests.exceptions.RequestException as e:
            if timeout <= 8:
                return None
            raise e
        if res.status_code != 200:
            return None
        res = self._pass_js_cookie_challenge(res, timeout=timeout)
        return self._hydrate_dynamic_article(res, timeout=timeout)

    def _hydrate_dynamic_article(
        self,
        res: Optional[requests.Response],
        timeout: int = 18,
    ) -> Optional[requests.Response]:
        """
        Đọc nội dung bài viết mà cổng tuyển sinh Next.js nạp ở phía trình duyệt.

        API chỉ được dùng khi chính JavaScript trên trang trường công khai endpoint;
        URL bài viết của trường vẫn được giữ nguyên làm nguồn.
        """
        if res is None:
            return None
        text = res.text or ""
        path = urlparse(res.url or "").path
        slug_match = re.search(r"/thong-bao/([^/?#]+)", path, re.I)
        if not slug_match or "Đang tải chi tiết thông báo" not in text:
            return res

        try:
            soup = BeautifulSoup(text, "html.parser")
        except Exception:
            return res
        api_base = ""
        scripts = [
            urljoin(res.url, tag.get("src"))
            for tag in soup.find_all("script", src=True)
            if str(tag.get("src") or "").startswith(("/", "http://", "https://"))
        ]
        for script_url in scripts[:20]:
            if not _same_org(script_url, urlparse(res.url).netloc):
                continue
            try:
                js = self.session.get(script_url, timeout=min(timeout, 15))
            except requests.exceptions.RequestException:
                continue
            if js.status_code != 200 or len(js.content) > 3 * 1024 * 1024:
                continue
            script = js.text or ""
            if "/api/public/notifications/" not in script:
                continue
            candidates = re.findall(r"https://[A-Za-z0-9._:-]+", script)
            api_base = next(
                (item.rstrip("/") for item in candidates if "nextjs.org" not in item),
                "",
            )
            if api_base:
                break
        if not api_base:
            return res

        slug = quote(slug_match.group(1), safe="-._~")
        api_url = f"{api_base}/api/public/notifications/{slug}"
        try:
            article_res = self.session.get(api_url, timeout=max(timeout, 20))
            payload = article_res.json() if article_res.status_code == 200 else {}
        except (requests.exceptions.RequestException, ValueError):
            return res
        article = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(article, dict) or not article.get("htmlContent"):
            return res

        title = clean_text(article.get("title") or "")
        safe_title = escape(title)
        hydrated = (
            "<!doctype html><html><head><meta charset=\"utf-8\">"
            f"<title>{safe_title}</title></head><body><article>"
            f"<h1>{safe_title}</h1>{article['htmlContent']}</article></body></html>"
        )
        res._content = hydrated.encode("utf-8")
        res.encoding = "utf-8"
        res.headers["Content-Type"] = "text/html; charset=utf-8"
        print(f"[CRAWLER] Đã nạp nội dung động từ cổng tuyển sinh: {res.url}")
        return res

    def _pass_js_cookie_challenge(
        self,
        res: requests.Response,
        timeout: int = 18,
    ) -> Optional[requests.Response]:
        """Một số cổng (vd. daotao.neu.edu.vn) đặt cookie bằng script rồi reload."""
        text = res.text or ""
        head = text[:900]
        if "document.cookie" not in head or "location.reload" not in head:
            return res
        match = re.search(
            r'document\.cookie\s*=\s*["\']([^="\'\s]+)=([^"\';]+)',
            head,
            re.I,
        )
        if not match:
            return res
        host = urlparse(res.url).hostname or ""
        try:
            self.session.cookies.set(match.group(1), match.group(2), domain=host, path="/")
        except Exception:
            self.session.cookies.set(match.group(1), match.group(2))
        verify = not self._insecure_ssl
        try:
            again = self.session.get(res.url, timeout=timeout, allow_redirects=True, verify=verify)
        except requests.exceptions.SSLError:
            again = self.session.get(res.url, timeout=timeout, allow_redirects=True, verify=False)
        except requests.exceptions.RequestException:
            return res
        if again.status_code != 200:
            return res
        return again

    def _harvest(
        self,
        res: requests.Response,
        page_url: str,
        org_host: str,
        years: List[int],
        page_queue: List[Tuple[int, str]],
        doc_queue: List[Tuple[int, str, str]],
        seen_html: Set[str],
    ) -> None:
        content_type = (res.headers.get("Content-Type") or "").lower()
        if "html" not in content_type and "text" not in content_type:
            return
        try:
            soup = BeautifulSoup(res.text, "html.parser")
        except Exception:
            return
        page_title = ""
        if soup.title:
            page_title = soup.title.get_text(" ", strip=True)
        # Tệp đính kèm thường chỉ có tên mã (vd. 1772450163719_document_1.pdf).
        # Lấy điểm của trang chứa tệp để vẫn đọc phương án tuyển sinh.
        page_score = _score_text(_blob(page_url, page_title), years)
        for anchor in soup.find_all("a", href=True):
            href = (anchor.get("href") or "").strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            href = _rewrite_internal_file_href(href, page_url)
            absolute = urljoin(page_url, href)
            parsed = urlparse(absolute)
            page_parsed = urlparse(page_url)
            if (
                parsed.scheme == "http"
                and page_parsed.scheme == "https"
                and _same_org(absolute, page_parsed.netloc)
            ):
                absolute = urlunparse(parsed._replace(scheme="https"))
                parsed = urlparse(absolute)
            if parsed.scheme not in ("http", "https"):
                continue
            if not _same_org(absolute, org_host):
                continue
            if _SKIP_EXT_RE.search(parsed.path or ""):
                continue
            label = clean_text(anchor.get_text(" ", strip=True))
            text = _blob(label, absolute)
            score = _score_text(text, years) + _host_bonus(absolute)
            if _DOC_RE.search(absolute) and page_score >= 4:
                score = max(score, page_score)
                text = _blob(label, absolute, page_url, page_title)
            mentioned = _years_in(absolute, label)
            path = (parsed.path or "").lower().rstrip("/")
            if re.search(r"/news/(?:dao-tao|tuyen-sinh|thong-bao)$", path):
                score = max(score, 25)
            if re.search(r"/news/.*/page-([2-4])$", path):
                score = max(score, 24)
            if ("diem chuan" in text or "diem trung tuyen" in text) and "du bao" not in text:
                if not mentioned or any(y in years for y in mentioned):
                    score += 60
            if re.search(r"(?:[?&]page=|/page/)\d+", absolute) and "diem chuan" not in text:
                score -= 20
            if score < 4:
                continue
            if _DOC_RE.search(absolute):
                doc_queue.append((score, absolute, text))
            else:
                canon = _canon(absolute)
                if canon and canon not in seen_html:
                    page_queue.append((score, absolute))

    def _page_year(self, res: requests.Response, years: List[int]) -> Optional[int]:
        url_years = _years_in(res.url or "")
        if url_years:
            for year in url_years:
                if year in years:
                    return year
            return url_years[-1]
        title = ""
        try:
            soup = BeautifulSoup(res.text, "html.parser")
            if soup.title:
                title = soup.title.get_text(" ", strip=True)
            h1 = soup.find("h1")
            if h1:
                title = f"{title} {h1.get_text(' ', strip=True)}"
        except Exception:
            title = ""
        return self._pick_year(title, years=years)

    def _pick_year(self, *parts: str, years: List[int]) -> Optional[int]:
        found = _years_in(*parts)
        for year in found:
            if year in years:
                return year
        return found[-1] if found else None

    def _extract_html(
        self,
        res: requests.Response,
        school_code: str,
        school_name: str,
        years: List[int],
        bundle: CrawlBundle,
        page_year: Optional[int],
    ) -> int:
        if page_year and page_year not in years:
            return 0
        source = f"Website trường: {res.url}"
        admissions, conversions, regulations = self.dean.extract(
            html=res.text,
            school_code=school_code,
            school_name=school_name,
            source=source,
            default_year=page_year or max(years),
        )
        admissions.extend(
            self._cutoff_images(
                res, school_code, school_name, page_year or max(years), source
            )
        )
        before = len(bundle.admissions)
        self._merge(bundle, admissions, conversions, regulations, years, source)
        return len(bundle.admissions) - before

    def _cutoff_images(
        self,
        res: requests.Response,
        school_code: str,
        school_name: str,
        year: int,
        source: str,
    ) -> List[AdmissionRecord]:
        """Bóc điểm chuẩn khi trường đăng bảng bằng ảnh thay vì HTML."""
        try:
            soup = BeautifulSoup(res.text, "html.parser")
        except Exception:
            return []
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        blob = _blob(res.url or "", title)
        if "diem chuan" not in blob or "du bao" in blob:
            return []
        root = soup
        for sel in ("article", ".description", ".post-content", ".entry-content", "#content"):
            node = soup.select_one(sel)
            if node and node.find("img"):
                root = node
                break
        records: List[AdmissionRecord] = []
        seen_src = set()
        page_url = res.url or ""
        for img in root.find_all("img"):
            if len(seen_src) >= 4:
                break
            src = (img.get("src") or "").strip()
            if not src or src.startswith("data:"):
                continue
            absolute = urljoin(page_url, src)
            if absolute in seen_src:
                continue
            alt = clean_text(img.get("alt") or "")
            folded = strip_accents(alt)
            if any(k in folded for k in ["do lech", "quy doi", "du bao", "noi suy"]):
                continue
            path = urlparse(absolute).path.lower()
            if not path.endswith((".jpg", ".jpeg", ".png", ".webp")):
                continue
            seen_src.add(absolute)
            try:
                image = self._fetch(absolute, timeout=20)
            except Exception:
                continue
            if image is None or len(image.content) < 8000:
                continue
            suffix = ".png" if path.endswith(".png") else ".jpg"
            tmp_path = ""
            try:
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(image.content)
                    tmp_path = tmp.name
                records.extend(
                    records_from_cutoff_image(tmp_path, school_code, school_name, year, source)
                )
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
        return records

    def _extract_document(
        self,
        url: str,
        school_code: str,
        school_name: str,
        years: List[int],
        bundle: CrawlBundle,
        fallback_year: int,
    ) -> int:
        res = self._fetch(url)
        if res is None:
            return 0
        if len(res.content) > self.MAX_DOC_BYTES:
            print(f"[CẢNH BÁO] Bỏ qua tệp quá lớn: {url}")
            return 0
        path = urlparse(res.url or url).path.lower()
        content_type = (res.headers.get("Content-Type") or "").lower()
        if path.endswith(".pdf") or "pdf" in content_type or res.content[:4] == b"%PDF":
            suffix = ".pdf"
            parser = "pdf"
        elif path.endswith(".docx"):
            suffix = ".docx"
            parser = "docx"
        elif path.endswith(".xlsx") or path.endswith(".xls"):
            suffix = ".xlsx" if path.endswith(".xlsx") else ".xls"
            parser = "excel"
        else:
            return 0

        year = self._pick_year(url, res.url or "", years=years) or fallback_year
        if year not in years:
            year = fallback_year
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(res.content)
                tmp_path = tmp.name
            source = f"Website trường: {res.url or url}"
            if parser == "pdf":
                records = PdfAdmissionParser().parse(
                    tmp_path,
                    default_school_code=school_code,
                    default_school_name=school_name,
                    default_year=year,
                    max_pages=self.MAX_PDF_PAGES,
                )
                if not records:
                    records = self._records_from_scanned_pdf(
                        tmp_path,
                        school_code,
                        school_name,
                        year,
                        source,
                    )
            elif parser == "docx":
                records = DocxAdmissionParser().parse(
                    tmp_path,
                    default_school_code=school_code,
                    default_school_name=school_name,
                    default_year=year,
                )
            else:
                records = ExcelAdmissionParser().parse(
                    tmp_path,
                    default_school_code=school_code,
                    default_school_name=school_name,
                    default_year=year,
                )
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

        for rec in records:
            rec.ma_truong = school_code
            rec.ten_truong = school_name
            rec.nguon = source
            if rec.nam not in years:
                rec.nam = year
        before = len(bundle.admissions)
        self._merge(bundle, records, [], [], years, source)
        return len(bundle.admissions) - before

    def _records_from_scanned_pdf(
        self,
        path: str,
        school_code: str,
        school_name: str,
        year: int,
        source: str,
    ) -> List[AdmissionRecord]:
        """Đọc bảng chỉ tiêu trong PDF scan (mỗi trang là một ảnh)."""
        records: List[AdmissionRecord] = []
        try:
            pdf = __import__("pdfplumber").open(path)
        except Exception as exc:
            print(f"[CẢNH BÁO] Không mở được PDF scan {path}: {exc}")
            return []
        try:
            pages = pdf.pages[: self.MAX_PDF_PAGES]
            for page in pages:
                image_path = ""
                try:
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                        image_path = tmp.name
                    page.to_image(resolution=150).save(image_path)
                    records.extend(
                        self._quota_rows_from_image(
                            image_path, school_code, school_name, year, source
                        )
                    )
                    records.extend(
                        self._cutoff_rows_from_image(
                            image_path, school_code, school_name, year, source
                        )
                    )
                except Exception as exc:
                    print(f"[CẢNH BÁO] Không đọc trang scan: {exc}")
                finally:
                    if image_path and os.path.exists(image_path):
                        os.unlink(image_path)
        finally:
            pdf.close()
        return records

    def _quota_rows_from_image(
        self,
        image_path: str,
        school_code: str,
        school_name: str,
        year: int,
        source: str,
    ) -> List[AdmissionRecord]:
        tokens = ocr_tokens(image_path)
        if len(tokens) < 8:
            return []
        rows = _cluster_rows(tokens, gap=0.018)
        found: List[AdmissionRecord] = []
        for row in rows:
            code = ""
            quota: Optional[int] = None
            name_parts: List[str] = []
            for x, text in row:
                compact = re.sub(r"\s+", "", text).upper()
                if not code and 0.16 <= x <= 0.30 and _MAJOR_CODE_RE.match(compact):
                    code = compact
                    continue
                if x >= 0.65 and re.fullmatch(r"\d{2,4}", compact):
                    value = int(compact)
                    if 10 <= value <= 2000:
                        quota = value
                    continue
                if 0.28 <= x < 0.68 and re.search(r"[A-Za-zÀ-ỹ]", text):
                    name_parts.append(text)
            name = clean_text(" ".join(name_parts))
            if code and quota and len(name) >= 3:
                found.append(
                    AdmissionRecord(
                        ma_truong=school_code,
                        ten_truong=school_name,
                        ma_nganh=code,
                        ten_nganh=name,
                        nam=year,
                        chi_tieu=quota,
                        phuong_thuc="Chỉ tiêu dự kiến",
                        ghi_chu="Chỉ tiêu dự kiến trong phương án tuyển sinh, chưa có điểm chuẩn.",
                        nguon=source,
                    )
                )
                continue
            if found and not code and quota is None and len(name) >= 3:
                tail = found[-1].ten_nganh.rstrip()
                if re.search(r"(?:-|–|\bvà|\bChí|\bViệt|\bchính)$", tail, re.I) or re.match(
                    r"và\b", name, re.I
                ):
                    found[-1].ten_nganh = clean_text(f"{tail} {name}")
        found = [rec for rec in found if _quota_name_ok(rec.ten_nganh)]
        if len(found) < 3:
            return []
        return found

    def _cutoff_rows_from_image(
        self,
        image_path: str,
        school_code: str,
        school_name: str,
        year: int,
        source: str,
    ) -> List[AdmissionRecord]:
        """Đọc bảng điểm chuẩn scan: mã ngành số, tên ngành, điểm bên phải."""
        from core.normalizer import normalize_score

        tokens = ocr_tokens(image_path)
        if len(tokens) < 8:
            return []
        page_text = strip_accents(" ".join(text for _, _, text in tokens))
        if "thpt" in page_text or "tot nghiep" in page_text:
            method = "Điểm thi THPT"
        elif "diem chuan" in page_text or "trung tuyen" in page_text:
            method = "Điểm chuẩn"
        else:
            method = "Điểm chuẩn"
        rows = _cluster_rows(tokens, gap=0.012)
        found: List[AdmissionRecord] = []
        for row in rows:
            code = ""
            code_x = 0.0
            score = None
            score_x = 1.0
            name_parts: List[str] = []
            for x, text in row:
                match = _NUM_MAJOR_RE.search(text)
                if not code and match and 0.08 <= x <= 0.45:
                    code = match.group(0)
                    code_x = x
                value = normalize_score(text.replace(" ", ""))
                if value is not None and x >= 0.55 and (score is None or x > score_x):
                    score = value
                    score_x = x
            if not code or score is None:
                continue
            for x, text in row:
                if code_x < x < score_x and re.search(r"[A-Za-zÀ-ỹ]", text):
                    name_parts.append(text)
            name = clean_text(" ".join(name_parts))
            if len(name) < 3 or "nganh" in strip_accents(name) and "chuong trinh" in strip_accents(name):
                continue
            found.append(
                AdmissionRecord(
                    ma_truong=school_code,
                    ten_truong=school_name,
                    ma_nganh=code,
                    ten_nganh=name,
                    nam=year,
                    diem_chuan=score,
                    thang_diem=40.0 if score > 30 else 30.0,
                    phuong_thuc=method,
                    nguon=source,
                )
            )
        if len(found) < 3:
            return []
        return found

    def _merge(
        self,
        bundle: CrawlBundle,
        admissions: List[AdmissionRecord],
        conversions: List[ScoreConversionRecord],
        regulations: List[AdmissionRegulation],
        years: List[int],
        source: str,
    ) -> None:
        seen = {
            (
                r.ma_nganh,
                (r.ten_nganh or "").lower(),
                r.nam,
                r.phuong_thuc,
                (r.to_hop or "").lower(),
                r.diem_chuan,
                r.chi_tieu,
            )
            for r in bundle.admissions
        }
        for rec in admissions:
            if rec.nam not in years:
                continue
            if not rec.ten_nganh and not rec.ma_nganh:
                continue
            rec.nguon = source
            key = (
                rec.ma_nganh,
                (rec.ten_nganh or "").lower(),
                rec.nam,
                rec.phuong_thuc,
                (rec.to_hop or "").lower(),
                rec.diem_chuan,
                rec.chi_tieu,
            )
            if key in seen:
                continue
            seen.add(key)
            bundle.admissions.append(rec)

        seen_conv = {
            (c.loai_bang, c.hang_muc, c.diem_quy_doi, c.nam) for c in bundle.conversions
        }
        for conv in conversions:
            if conv.nam and conv.nam not in years:
                continue
            conv.nguon = source
            key = (conv.loai_bang, conv.hang_muc, conv.diem_quy_doi, conv.nam)
            if key in seen_conv:
                continue
            seen_conv.add(key)
            bundle.conversions.append(conv)

        seen_reg = {(r.tieu_de, r.noi_dung[:80]) for r in bundle.regulations}
        for reg in regulations:
            if reg.nam and reg.nam not in years:
                continue
            reg.nguon = source
            key = (reg.tieu_de, (reg.noi_dung or "")[:80])
            if key in seen_reg:
                continue
            seen_reg.add(key)
            bundle.regulations.append(reg)

    def _filter_years(self, bundle: CrawlBundle, years: List[int]) -> None:
        bundle.admissions = [r for r in bundle.admissions if r.nam in years]
        bundle.conversions = [
            c for c in bundle.conversions if not c.nam or c.nam in years
        ]
        bundle.regulations = [
            r for r in bundle.regulations if not r.nam or r.nam in years
        ]
