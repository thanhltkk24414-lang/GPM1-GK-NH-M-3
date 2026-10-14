
"""
KHAI THAC BAO CAO THUONG NIEN DOANH NGHIEP NIEM YET VIET NAM

Chuc nang:
- Nhap ma co phieu tuy y.
- Tim PDF bang DDGS, sau do Bing neu can.
- Tim link PDF truc tiep va tim link PDF trong trang ket qua.
- Tai file va kiem tra chu ky PDF.
- Ghi ket qua vao annual_report_sources.csv.
- Loi mot ma/nam khong lam dung ca chuong trinh.

Chay:
    python core/annual_report.py
"""

import argparse
import csv
from datetime import datetime
import hashlib
import io
import re
import tempfile
import time
import unicodedata
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

import pymupdf
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS


# ============================================================
# 1. CAU HINH
# ============================================================

PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "annual_reports"
LOG_FILE = OUTPUT_DIR / "annual_report_sources.csv"
ZENODO_RECORD_ID = "20949551"
ZENODO_RECORD_URL = f"https://zenodo.org/records/{ZENODO_RECORD_ID}"
ZENODO_INDEX_URL = f"{ZENODO_RECORD_URL}/files/file_index_full.csv?download=1"
ZENODO_INDEX_CACHE = OUTPUT_DIR / "zenodo_file_index_full.csv"
ZENODO_INDEX_MAX_AGE_SECONDS = 7 * 24 * 60 * 60

YEARS = [2022, 2023, 2024, 2025]

REQUEST_TIMEOUT = 25
MAX_SEARCH_RESULTS = 8
MAX_PAGES_TO_CRAWL = 5
DELAY_SECONDS = 1
MAX_VALIDATION_PAGES = 40
_PDF_OCR_ENGINE = None

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}

# Ten thuong gap giup tim chinh xac hon.
# Ma co phieu khong co trong danh sach nay van duoc chap nhan.
COMPANY_NAMES = {
    "VCB": ["Vietcombank", "Joint Stock Commercial Bank for Foreign Trade of Vietnam"],
    "BID": ["BIDV", "Bank for Investment and Development of Vietnam"],
    "CTG": ["VietinBank", "Vietnam Joint Stock Commercial Bank for Industry and Trade"],
    "TCB": ["Techcombank"],
    "MBB": ["MB Bank", "Military Commercial Joint Stock Bank"],
    "ACB": ["Asia Commercial Bank"],
    "VPB": ["VPBank"],
    "HDB": ["HDBank"],
    "STB": ["Sacombank"],
    "SHB": ["SHB Bank"],
    "SSB": ["SeABank"],
    "LPB": ["LPBank", "Lien Viet Post Bank"],
    "VNM": ["Vinamilk", "Vietnam Dairy Products"],
    "FPT": ["FPT Corporation"],
    "HPG": ["Hoa Phat Group", "Tập đoàn Hòa Phát"],
    "MWG": ["Mobile World Investment Corporation", "The Gioi Di Dong"],
    "PNJ": ["Phu Nhuan Jewelry"],
    "GMD": ["Gemadept"],
    "VIC": ["Vingroup"],
    "VHM": ["Vinhomes"],
    "VRE": ["Vincom Retail"],
    "MSN": ["Masan Group"],
    "GAS": ["Petrovietnam Gas"],
    "POW": ["Petrovietnam Power"],
    "PLX": ["Petrolimex"],
    "SSI": ["SSI Securities"],
    "VND": ["VNDirect Securities"],
    "VIX": ["VIX Securities"],
    "VCI": ["Vietcap Securities"],
    "DGC": ["Duc Giang Chemicals"],
    "REE": ["REE Corporation"],
    "SAB": ["Sabeco"],
    "BCM": ["Becamex"],
    "KDH": ["Khang Dien House"],
    "NLG": ["Nam Long Investment"],
    "DXG": ["Dat Xanh Group"],
    "PDR": ["Phat Dat Real Estate"],
    "GVR": ["Vietnam Rubber Group"],
    "VJC": ["Vietjet Air"],
    "HVN": ["Vietnam Airlines"],
}


# ============================================================
# 2. CAC HAM HO TRO
# ============================================================

def normalize_url(url):
    """Chuan hoa URL va loai bo URL khong hop le."""
    if not url:
        return None

    url = url.strip()

    if url.startswith("//"):
        url = "https:" + url

    if not url.startswith(("http://", "https://")):
        return None

    parsed = urlparse(url)

    if not parsed.netloc:
        return None

    if parsed.scheme not in ("http", "https"):
        return None

    return url


def get_company_names(ticker):
    """Lay cac ten cong ty da biet, khong bat buoc phai co trong danh sach."""
    return COMPANY_NAMES.get(ticker.upper(), [])


def build_queries(ticker, year):
    """Tao nhieu truy van de tang kha nang tim thay bao cao."""
    ticker = ticker.upper()
    names = get_company_names(ticker)

    queries = [
        f'"{ticker}" "bao cao thuong nien" {year} filetype:pdf',
        f'"{ticker}" "annual report" {year} PDF',
        f'"{ticker}" BCTN {year}',
    ]

    for name in names[:2]:
        queries.extend([
            f'"{name}" "annual report" {year} PDF',
            f'"{name}" "bao cao thuong nien" {year} PDF',
        ])

    # Bo cac truy van trung nhau
    return list(dict.fromkeys(queries))


def is_pdf_url(url):
    """Nhan dien URL co duoi PDF."""
    if not url:
        return False

    path = unquote(urlparse(url).path).lower()
    return path.endswith(".pdf")


def relevance_score(ticker, year, url, title="", description=""):
    """
    Cham diem link ung vien.
    Diem cao hon neu co ma co phieu, nam va dau hieu bao cao thuong nien.
    """
    text = unquote(
        f"{url} {title} {description}"
    ).lower()

    score = 0

    if ticker.lower() in text:
        score += 4

    if str(year) in text:
        score += 3

    annual_keywords = [
        "annual report",
        "annual-report",
        "bao cao thuong nien",
        "báo cáo thường niên",
        "bctn",
        "bao-cao-thuong-nien",
    ]

    if any(keyword in text for keyword in annual_keywords):
        score += 3

    if is_pdf_url(url):
        score += 2

    return score


def extract_pdf_links(page_url, ticker, year, page_title=""):
    """
    Mo mot trang web va tim cac link PDF co kha nang la bao cao thuong nien.
    Khong tai PDF o buoc nay.
    """
    page_url = normalize_url(page_url)

    if not page_url:
        return []

    try:
        response = requests.get(
            page_url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            return []

        content_type = response.headers.get("Content-Type", "").lower()

        # Neu trang tra ve PDF truc tiep, tra lai URL do.
        if (
            "application/pdf" in content_type
            or response.content[:5] == b"%PDF-"
        ):
            return [response.url]

        # Khong phan tich cac noi dung khong phai HTML.
        if "html" not in content_type and not response.text.lstrip().startswith("<"):
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        page_text = soup.get_text(" ", strip=True)
        page_context = f"{page_title} {page_text[:15000]}"

        links = []

        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "").strip()

            if not href:
                continue

            absolute_url = normalize_url(urljoin(response.url, href))

            if not absolute_url:
                continue

            anchor_text = anchor.get_text(" ", strip=True)
            combined = (
                f"{absolute_url} {anchor_text} "
                f"{page_title} {page_context[:3000]}"
            ).lower()

            pdf_hint = (
                is_pdf_url(absolute_url)
                or "pdf" in anchor_text.lower()
                or "download" in anchor_text.lower()
                or "tai ve" in anchor_text.lower()
                or "tải về" in anchor_text.lower()
            )

            year_hint = (
                str(year) in absolute_url
                or str(year) in anchor_text
                or str(year) in page_context
            )

            report_hint = any(
                keyword in combined
                for keyword in [
                    "annual report",
                    "annual-report",
                    "bao cao thuong nien",
                    "báo cáo thường niên",
                    "bctn",
                    "bao-cao-thuong-nien",
                ]
            )

            ticker_hint = ticker.lower() in combined

            if pdf_hint and year_hint and report_hint:
                # Uu tien link co ma co phieu trong URL/noi dung.
                # Neu ten ma khong xuat hien, van cho phep neu trang
                # nguon co tieu de lien quan den bao cao.
                if ticker_hint or relevance_score(
                    ticker, year, absolute_url, anchor_text, page_title
                ) >= 5:
                    links.append(absolute_url)

        return list(dict.fromkeys(links))

    except requests.RequestException as exc:
        print(f"    Khong doc duoc trang {page_url}: {exc}")
        return []

    except Exception as exc:
        print(f"    Loi phan tich trang {page_url}: {exc}")
        return []


# ============================================================
# 3. TIM KIEM BANG DDGS
# ============================================================

def search_with_ddgs(ticker, year):
    """Tim PDF va cac trang co the chua PDF bang DDGS."""
    candidates = []
    seen_urls = set()

    for query in build_queries(ticker, year):
        print(f'  DDGS: "{query}"')

        try:
            with DDGS() as ddgs:
                results = list(
                    ddgs.text(
                        query,
                        region="vn-vi",
                        max_results=MAX_SEARCH_RESULTS,
                    )
                )

        except Exception as exc:
            print(f"    Bo qua truy van loi: {exc}")
            continue

        for result in results:
            url = normalize_url(
                result.get("href")
                or result.get("url")
                or result.get("link")
            )

            if not url or url in seen_urls:
                continue

            seen_urls.add(url)

            title = result.get("title", "") or ""
            body = result.get("body", "") or ""

            score = relevance_score(
                ticker, year, url, title, body
            )

            # Link PDF truc tiep
            if is_pdf_url(url) and score >= 5:
                candidates.append((score, url))
                continue

            # Trang ket qua co the chua link PDF
            if score >= 3:
                print(f"    Kiem tra trang: {title[:90]}")

                pdf_links = extract_pdf_links(
                    url,
                    ticker,
                    year,
                    page_title=f"{title} {body}",
                )

                for pdf_url in pdf_links:
                    pdf_score = relevance_score(
                        ticker, year, pdf_url, title, body
                    )
                    candidates.append((pdf_score, pdf_url))

                time.sleep(0.3)

        time.sleep(0.3)

    # Sap xep link co diem cao nhat len truoc
    candidates.sort(key=lambda item: item[0], reverse=True)

    output = []
    for _, url in candidates:
        if url not in output:
            output.append(url)

    return output


# ============================================================
# 4. TIM KIEM DU PHONG BANG BING
# ============================================================

def search_with_bing(ticker, year):
    """Tim kiem HTML Bing neu DDGS khong tim thay link."""
    candidates = []

    for query in build_queries(ticker, year)[:4]:
        print(f'  Bing du phong: "{query}"')

        try:
            response = requests.get(
                "https://www.bing.com/search",
                params={
                    "q": query,
                    "count": MAX_SEARCH_RESULTS,
                    "setlang": "vi",
                },
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code != 200:
                print(f"    Bing tra ma HTTP {response.status_code}")
                continue

            soup = BeautifulSoup(response.text, "html.parser")

            results = soup.select("li.b_algo")

            # Mot so phien ban HTML khong co selector tren.
            if not results:
                results = soup.find_all("a", href=True)

            pages_crawled = 0

            for result in results:
                if getattr(result, "name", None) == "a":
                    anchor = result
                else:
                    anchor = result.find("a", href=True)

                if not anchor:
                    continue

                url = normalize_url(anchor.get("href"))

                if not url:
                    continue

                title = anchor.get_text(" ", strip=True)
                description = result.get_text(" ", strip=True)

                score = relevance_score(
                    ticker, year, url, title, description
                )

                if is_pdf_url(url) and score >= 5:
                    candidates.append((score, url))
                    continue

                if score >= 3 and pages_crawled < MAX_PAGES_TO_CRAWL:
                    pages_crawled += 1

                    pdf_links = extract_pdf_links(
                        url,
                        ticker,
                        year,
                        page_title=f"{title} {description}",
                    )

                    for pdf_url in pdf_links:
                        pdf_score = relevance_score(
                            ticker, year, pdf_url, title, description
                        )
                        candidates.append((pdf_score, pdf_url))

                    time.sleep(0.3)

        except requests.RequestException as exc:
            print(f"    Loi ket noi Bing: {exc}")

        except Exception as exc:
            print(f"    Loi xu ly ket qua Bing: {exc}")

        time.sleep(0.5)

    candidates.sort(key=lambda item: item[0], reverse=True)

    output = []
    for _, url in candidates:
        if url not in output:
            output.append(url)

    return output


# ============================================================
# 5. HAM TIM KIEM CHINH
# ============================================================

def search_pdf_links(ticker, year):
    """
    Tra ve danh sach link ung vien.
    Khong dung chuong trinh chi vi mot truy van khong co ket qua.
    """
    ticker = ticker.strip().upper()

    print(f"  Dang tim bao cao {ticker} nam {year}...")

    links = search_with_ddgs(ticker, year)

    if links:
        print(f"  DDGS tim thay {len(links)} link ung vien.")
        return links

    print("  DDGS chua tim thay PDF. Thu Bing...")

    links = search_with_bing(ticker, year)

    if links:
        print(f"  Bing tim thay {len(links)} link ung vien.")
        return links

    print("  Khong tim thay link PDF tu cac nguon da thu.")
    return []


def build_financial_statement_queries(ticker, year):
    """Create search queries for audited annual financial statements."""
    ticker = ticker.strip().upper()
    queries = [
        f'"{ticker}" "báo cáo tài chính năm" "đã kiểm toán" {year} filetype:pdf',
        f'"{ticker}" "báo cáo tài chính hợp nhất" "kiểm toán độc lập" {year} filetype:pdf',
        f'"{ticker}" "audited financial statements" "year ended" {year} PDF',
    ]
    for company_name in get_company_names(ticker)[:2]:
        queries.extend([
            f'"{company_name}" "báo cáo tài chính năm" "đã kiểm toán" {year} PDF',
            f'"{company_name}" "audited financial statements" "year ended" {year} PDF',
        ])
    return list(dict.fromkeys(queries))


def _search_text(value):
    text = unicodedata.normalize("NFKD", str(value)).casefold()
    text = text.replace("đ", "d")
    return "".join(char for char in text if not unicodedata.combining(char))


def financial_statement_relevance_score(ticker, year, url, title="", description=""):
    """Score a candidate PDF using company, report year, and audit terms."""
    text = _search_text(unquote(f"{url} {title} {description}"))
    if any(term in text for term in (
        "explanation",
        "giai trinh",
        "annual report",
        "bao cao thuong nien",
        "six months",
        "6 months",
        "six-month",
        "half-year",
        "semi-annual",
        "semiannual",
        "quarterly",
        "bao cao quy",
        "ban nien",
    )):
        return 0
    if str(year) not in text:
        return 0

    company_terms = [
        _search_text(ticker),
        *(_search_text(name) for name in get_company_names(ticker)),
    ]
    if not any(term in text for term in company_terms):
        return 0

    statement_terms = (
        "bao cao tai chinh",
        "financial statements",
        "financial-statement",
    )
    audit_terms = ("kiem toan", "audited", "audit report")
    if not any(term in text for term in statement_terms):
        return 0
    if not any(term in text for term in audit_terms):
        return 0

    score = 4
    if _search_text(ticker) in text:
        score += 4
    if "hop nhat" in text or "consolidated" in text:
        score += 2
    if is_pdf_url(url):
        score += 2
    return score


def financial_statement_source(url):
    host = (urlparse(url).hostname or "").casefold()
    if host == "hsx.vn" or host.endswith(".hsx.vn"):
        return "HOSE"
    if host == "hnx.vn" or host.endswith(".hnx.vn"):
        return "HNX"
    if (
        host == "cafef.vn"
        or host.endswith(".cafef.vn")
        or host.endswith(".mediacdn.vn")
    ):
        return "CafeF"
    if host == "vietstock.vn" or host.endswith(".vietstock.vn"):
        return "Vietstock"
    return "Trang doanh nghiệp / nguồn web"


def _is_exchange_source(url):
    host = (urlparse(url).hostname or "").casefold()
    return (
        host == "hsx.vn"
        or host.endswith(".hsx.vn")
        or host == "hnx.vn"
        or host.endswith(".hnx.vn")
    )


def _is_issuer_ir_page(url):
    path = _search_text(urlparse(url).path)
    return any(
        term in path
        for term in (
            "investor",
            "quan-he-co-dong",
            "co-dong",
            "shareholder",
            "financial-report",
        )
    )


def _ocr_pdf_page(page):
    """Read a scanned statement page using the optional local OCR extra."""
    global _PDF_OCR_ENGINE
    try:
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError(
            "PDF dạng scan cần OCR. Cài thêm bằng: "
            "python -m pip install -r requirements-ocr.txt"
        ) from exc

    if _PDF_OCR_ENGINE is None:
        _PDF_OCR_ENGINE = RapidOCR()

    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, pixmap.n
    )
    result, _ = _PDF_OCR_ENGINE(image)
    return "\n".join(
        str(item[1]).strip()
        for item in (result or [])
        if len(item) > 1 and str(item[1]).strip()
    )


def validate_audited_annual_financial_statement(pdf_path, ticker, year):
    """Check extracted PDF content for audited annual financial statements."""
    ocr_error = None
    try:
        with pymupdf.open(pdf_path) as document:
            pages = []
            for page in document[:MAX_VALIDATION_PAGES]:
                page_text = page.get_text()
                if len(page_text.strip()) < 40:
                    try:
                        page_text = _ocr_pdf_page(page)
                    except RuntimeError as exc:
                        ocr_error = str(exc)
                        break
                pages.append(page_text)
            opening_text = _search_text(" ".join(pages[:5]))
            text = _search_text(" ".join(pages))
    except (OSError, RuntimeError, ValueError) as exc:
        return False, f"Không đọc được nội dung PDF: {exc}"

    if not text.strip():
        if ocr_error:
            return False, ocr_error
        return False, "PDF không có lớp văn bản để xác minh nội dung báo cáo."

    if any(term in opening_text for term in (
        "explanation",
        "giai trinh",
        "explain the business result",
    )):
        return False, "Tài liệu là văn bản giải trình, không phải bộ báo cáo tài chính."

    interim_terms = (
        "six months",
        "6 months",
        "six-month",
        "half-year",
        "semi-annual",
        "semiannual",
        "interim financial",
        "bao cao ban nien",
        "6 thang",
        "sau thang",
        "quarter ended",
        "annual report",
        "bao cao thuong nien",
    )
    if any(term in opening_text for term in interim_terms):
        return False, "Tài liệu không phải BCTC năm đã kiểm toán."

    company_terms = [
        _search_text(ticker),
        *(_search_text(name) for name in get_company_names(ticker)),
    ]
    if not any(term in text for term in company_terms):
        return False, f"Không xác minh được doanh nghiệp {ticker} trong nội dung PDF."
    if str(year) not in text:
        return False, f"Không tìm thấy năm báo cáo {year} trong nội dung PDF."

    statement_groups = (
        ("bang can doi ke toan", "statement of financial position", "balance sheet"),
        (
            "bao cao ket qua hoat dong kinh doanh",
            "statement of profit or loss",
            "income statement",
        ),
        (
            "bao cao luu chuyen tien te",
            "statement of cash flows",
            "cash flow statement",
        ),
    )
    statement_count = sum(
        any(term in text for term in terms) for terms in statement_groups
    )
    if statement_count < 2:
        return False, "PDF không có đủ các bảng báo cáo tài chính cơ bản."

    audit_terms = (
        "bao cao kiem toan doc lap",
        "kiem toan doc lap",
        "chung toi da kiem toan",
        "we have audited",
        "independent auditor",
        "independent auditors report",
    )
    if not any(term in text for term in audit_terms):
        return False, "Không tìm thấy ý kiến/báo cáo kiểm toán trong PDF."

    annual_period_patterns = (
        rf"31\s*/\s*12\s*/?\s*{year}\b",
        rf"31\s+thang\s+12\s+nam\s+{year}\b",
        rf"31\s+(?:december|dec)\s+{year}\b",
        rf"year ended[^.\n]{{0,100}}{year}\b",
        rf"nam tai chinh ket thuc[^.\n]{{0,100}}{year}\b",
    )
    if not any(re.search(pattern, text) for pattern in annual_period_patterns):
        return False, "Không xác minh được kỳ báo cáo năm kết thúc ngày 31/12."

    return True, "Đã xác minh BCTC năm đã kiểm toán."


def search_cafef_financial_statement_pdf_links(ticker, year):
    """Read CafeF's public per-ticker document feed and select audited annual PDFs."""
    ticker = ticker.strip().upper()
    endpoint = (
        "https://cafef.vn/du-lieu/Ajax/PageNew/FileBCTC.ashx"
        f"?Symbol={ticker}&Type=1&Year=0"
    )
    try:
        response = requests.get(
            endpoint,
            headers={
                **HEADERS,
                "Referer": "https://cafef.vn/",
                "Accept": "application/json, text/plain, */*",
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        print(f"  Khong truy cap duoc danh muc BCTC CafeF cho {ticker}: {exc}")
        return []
    except ValueError as exc:
        raise RuntimeError(f"CafeF trả dữ liệu JSON không hợp lệ cho {ticker}.") from exc

    records = payload.get("Data") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise RuntimeError(f"CafeF trả cấu trúc danh mục BCTC không hợp lệ cho {ticker}.")

    candidates = []
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            record_year = int(record.get("Year"))
        except (TypeError, ValueError):
            continue
        if record_year != year:
            continue

        title = str(record.get("Name") or "").strip()
        normalized_title = _search_text(title)
        if any(term in normalized_title for term in (
            "giai trinh",
            "quy ",
            "q1",
            "q2",
            "q3",
            "q4",
            "ban nien",
            "6 thang",
            "soat xet",
            "interim",
            "quarter",
            "semiannual",
        )):
            continue
        is_annual = (
            "nam" in normalized_title
            or "year ended" in normalized_title
            or str(record.get("Quarter")) == "5"
        )
        is_audited = any(
            term in normalized_title
            for term in ("kiem toan", "audited", "audit report")
        )
        if not is_annual or not is_audited:
            continue

        url = normalize_url(record.get("Link"))
        if not url or not is_pdf_url(url):
            continue
        score = (
            2
            if "hop nhat" in normalized_title or "consolidated" in normalized_title
            else 0
        )
        candidates.append((score, url))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return list(dict.fromkeys(url for _, url in candidates))


def extract_financial_statement_pdf_links(page_url, ticker, year, page_context=""):
    """Extract relevant PDF links from a company or search-result page."""
    page_url = normalize_url(page_url)
    if not page_url:
        return []

    try:
        response = requests.get(
            page_url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        if response.content[:5] == b"%PDF-":
            if financial_statement_relevance_score(
                ticker, year, response.url, page_context
            ):
                return [response.url]
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        candidates = []
        for anchor in soup.find_all("a", href=True):
            url = normalize_url(urljoin(response.url, anchor["href"].strip()))
            if not url:
                continue
            title = anchor.get_text(" ", strip=True)
            if financial_statement_relevance_score(
                ticker, year, url, title, page_context
            ):
                candidates.append(url)
        return list(dict.fromkeys(candidates))
    except requests.RequestException as exc:
        print(f"  Khong doc duoc trang ket qua {page_url}: {exc}")
        return []


def _search_financial_statement_results(queries, ticker, year, official_only=False):
    ticker = ticker.strip().upper()
    candidates = []
    seen_urls = set()
    failed_queries = []

    for query in queries:
        try:
            with DDGS() as search:
                results = list(search.text(
                    query,
                    region="vn-vi",
                    max_results=MAX_SEARCH_RESULTS,
                ))
        except Exception as exc:
            print(f"  Bo qua truy van BCTC loi: {exc}")
            failed_queries.append(str(exc))
            continue

        for result in results:
            url = normalize_url(
                result.get("href") or result.get("url") or result.get("link")
            )
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            is_official_result = _is_exchange_source(url) or _is_issuer_ir_page(url)
            if official_only and not is_official_result:
                continue
            if not official_only and is_official_result:
                continue
            title = result.get("title", "") or ""
            description = result.get("body", "") or ""
            score = financial_statement_relevance_score(
                ticker, year, url, title, description
            )
            if score and is_pdf_url(url):
                candidates.append((score, url))
                continue
            if _is_exchange_source(url) or _is_issuer_ir_page(url):
                for pdf_url in extract_financial_statement_pdf_links(
                    url, ticker, year, f"{title} {description}"
                ):
                    pdf_score = financial_statement_relevance_score(
                        ticker, year, pdf_url, title, description
                    )
                    candidates.append((pdf_score, pdf_url))

    candidates.sort(key=lambda item: item[0], reverse=True)
    if not candidates and failed_queries and len(failed_queries) == len(queries):
        raise RuntimeError(
            f"Dịch vụ tìm kiếm web không phản hồi: {failed_queries[-1]}"
        )
    return list(dict.fromkeys(url for _, url in candidates))


def search_official_financial_statement_pdf_links(ticker, year):
    """Search exchange disclosures and company investor-relations pages first."""
    ticker = ticker.strip().upper()
    names = get_company_names(ticker)
    queries = [
        f'site:hsx.vn "{ticker}" "báo cáo tài chính" "kiểm toán" {year} PDF',
        f'site:hnx.vn "{ticker}" "báo cáo tài chính" "kiểm toán" {year} PDF',
        f'"{ticker}" "investor relations" "audited financial statements" {year}',
        f'"{ticker}" "quan hệ cổ đông" "báo cáo tài chính" "kiểm toán" {year}',
    ]
    for name in names[:2]:
        queries.extend([
            f'"{name}" "investor relations" "audited financial statements" {year}',
            f'"{name}" "báo cáo tài chính năm" "đã kiểm toán" {year}',
        ])
    return _search_financial_statement_results(
        list(dict.fromkeys(queries)), ticker, year, official_only=True
    )


def search_financial_statement_pdf_links(ticker, year):
    """Search the web as a fallback after checking official sources."""
    return _search_financial_statement_results(
        build_financial_statement_queries(ticker, year),
        ticker,
        year,
        official_only=False,
    )


def download_financial_statement_report(ticker, year, destination=None):
    """Find and download an audited annual financial statement PDF."""
    ticker = ticker.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{2,10}", ticker):
        raise ValueError("Mã cổ phiếu không hợp lệ.")
    if not isinstance(year, int) or isinstance(year, bool) or not 2000 <= year <= 2100:
        raise ValueError("Năm báo cáo không hợp lệ.")

    destination = Path(destination) if destination else (
        OUTPUT_DIR / f"{ticker}_financial_statements_{year}.pdf"
    )
    if not destination.is_absolute():
        destination = PROJECT_DIR / destination
    destination.parent.mkdir(parents=True, exist_ok=True)

    cached_error = None
    if destination.is_file():
        try:
            valid, cached_error = validate_audited_annual_financial_statement(
                destination, ticker, year
            )
            if valid:
                return {
                    "ok": True,
                    "path": destination,
                    "url": None,
                    "source": "Tệp PDF đã xác minh",
                    "source_type": "cached",
                }
            destination.unlink()
        except OSError as exc:
            raise OSError(f"Không xác minh/xóa được PDF đã lưu: {exc}") from exc

    rejected_reasons = []
    search_sources = (
        ("cafef", search_cafef_financial_statement_pdf_links),
        ("official", search_official_financial_statement_pdf_links),
        ("fallback", search_financial_statement_pdf_links),
    )
    search_errors = []
    for source_type, search_source in search_sources:
        try:
            links = search_source(ticker, year)
        except RuntimeError as exc:
            search_errors.append(f"{source_type}: {exc}")
            continue
        for url in links:
            with tempfile.NamedTemporaryFile(
                prefix=f"{ticker}_{year}_",
                suffix=".pdf",
                dir=destination.parent,
                delete=False,
            ) as temp_file:
                candidate_path = Path(temp_file.name)
            candidate_path.unlink()
            try:
                if not download_pdf(url, candidate_path):
                    continue
                valid, reason = validate_audited_annual_financial_statement(
                    candidate_path, ticker, year
                )
                if not valid:
                    rejected_reasons.append(reason)
                    print(f"    Bo qua PDF khong dung loai ({url})")
                    continue
                candidate_path.replace(destination)
                return {
                    "ok": True,
                    "path": destination,
                    "url": url,
                    "source": financial_statement_source(url),
                    "source_type": source_type,
                }
            finally:
                candidate_path.unlink(missing_ok=True)

    return {
        "ok": False,
        "path": None,
        "url": None,
        "error": (
            (
                f"Tìm thấy PDF nhưng không xác minh được đó là BCTC năm đã kiểm toán "
                f"của {ticker} năm {year}."
                if rejected_reasons
                else f"Không tìm thấy PDF BCTC năm {year} đã kiểm toán cho {ticker}."
            )
            + (
                f" Lý do: {'; '.join(dict.fromkeys(rejected_reasons))}"
                if rejected_reasons else ""
            )
            + (
                f" Nguồn tìm kiếm lỗi: {'; '.join(search_errors)}"
                if search_errors else ""
            )
        ),
    }


def load_zenodo_catalog():
    """Load and cache the official file index from the reference dataset."""
    if ZENODO_INDEX_CACHE.exists():
        age = time.time() - ZENODO_INDEX_CACHE.stat().st_mtime
        if age < ZENODO_INDEX_MAX_AGE_SECONDS:
            try:
                content = ZENODO_INDEX_CACHE.read_text(encoding="utf-8-sig")
                return list(csv.DictReader(io.StringIO(content)))
            except (OSError, csv.Error, UnicodeError):
                pass

    response = requests.get(ZENODO_INDEX_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    if not rows or "relative_path" not in rows[0]:
        raise ValueError("Zenodo catalog response has an unexpected CSV schema")

    temp_path = ZENODO_INDEX_CACHE.with_suffix(".part")
    temp_path.write_bytes(response.content)
    temp_path.replace(ZENODO_INDEX_CACHE)
    return rows


def find_zenodo_report(ticker, year):
    """Find an exact ticker/year BCTN entry from Zenodo's published index."""
    try:
        rows = load_zenodo_catalog()
    except Exception as exc:
        print(f"  Khong tai duoc catalog Zenodo, se tim web: {exc}")
        return None

    matches = [
        row for row in rows
        if row.get("ticker_folder", "").strip().upper() == ticker.upper()
        and row.get("year_full", "").strip() == str(year)
        and row.get("document_type", "").strip().lower() == "annual_report"
        and row.get("status", "").strip().lower() == "ok"
        and row.get("relative_path", "").strip()
        and row.get("archive_period", "").strip()
        and row.get("sha256", "").strip()
    ]
    return matches[0] if matches else None


def download_zenodo_report(entry, destination):
    """Range-download one indexed PDF member; verify its size and SHA-256."""
    try:
        from remotezip import RemoteZip

        archive_name = f"vn_bctn_{entry['archive_period']}.zip"
        archive_url = f"{ZENODO_RECORD_URL}/files/{archive_name}?download=1"
        print(f"  Tim thay trong catalog Zenodo: {entry['relative_path']}")
        print("  Tai rieng PDF tu ZIP qua HTTP Range (khong tai ca archive).")

        with RemoteZip(
            archive_url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            initial_buffer_size=1024 * 1024,
        ) as archive:
            # The catalog stores paths as TICKER/file.pdf, while the ZIPs
            # currently place files under full_data/TICKER/file.pdf.
            catalog_path = entry["relative_path"].lstrip("/")
            candidates = (catalog_path, f"full_data/{catalog_path}")
            member_path = None
            for path in candidates:
                try:
                    archive.getinfo(path)
                    member_path = path
                    break
                except KeyError:
                    continue
            if member_path is None:
                raise KeyError(
                    f"Catalog path {catalog_path!r} not found in archive; "
                    f"tried {', '.join(repr(path) for path in candidates)}"
                )
            pdf_bytes = archive.read(member_path)

        expected_size = int(entry["file_size_bytes"])
        if not pdf_bytes.startswith(b"%PDF-"):
            raise ValueError("Zenodo archive member is not a valid PDF")
        if len(pdf_bytes) != expected_size:
            raise ValueError(f"Size mismatch: expected {expected_size}, got {len(pdf_bytes)}")
        actual_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        if actual_sha256.lower() != entry["sha256"].strip().lower():
            raise ValueError("SHA-256 mismatch against Zenodo catalog")

        temp_path = destination.with_suffix(".part")
        temp_path.write_bytes(pdf_bytes)
        temp_path.replace(destination)
        print(f"  Tai va xac minh SHA-256 thanh cong: {destination.name}")
        return True
    except Exception as exc:
        print(f"  Khong lay duoc file tu Zenodo: {exc}")
        return False


# ============================================================
# 6. TAI VA KIEM TRA PDF
# ============================================================

def download_pdf(url, destination):
    """
    Tai PDF vao file tam, kiem tra chu ky PDF va kich thuoc.
    Chi doi ten file tam thanh file chinh khi kiem tra thanh cong.
    """
    temp_path = destination.with_suffix(".part")

    try:
        with requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            stream=True,
            allow_redirects=True,
        ) as response:

            response.raise_for_status()

            first_chunk = next(
                response.iter_content(chunk_size=8192),
                b"",
            )

            if not first_chunk.startswith(b"%PDF-"):
                print("    Bo qua: URL khong tra ve noi dung PDF hop le.")
                return False

            total_size = len(first_chunk)

            with open(temp_path, "wb") as file:
                file.write(first_chunk)

                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        file.write(chunk)
                        total_size += len(chunk)

            # Bao cao thuong nien thuong lon hon muc toi thieu nay.
            if total_size < 10_000:
                print(
                    f"    Bo qua: file qua nho ({total_size} bytes), "
                    "co the la PDF loi."
                )
                temp_path.unlink(missing_ok=True)
                return False

            temp_path.replace(destination)

            print(
                f"    Tai thanh cong: {destination.name} "
                f"({total_size / 1024 / 1024:.2f} MB)"
            )
            return True

    except requests.RequestException as exc:
        print(f"    Loi tai file: {exc}")

    except OSError as exc:
        print(f"    Loi ghi file: {exc}")

    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass

    return False


# ============================================================
# 7. GHI LOG CSV
# ============================================================

def write_log(ticker, year, status, url="", note=""):
    """Ghi 1 dong moi theo schema nhat quan va chuan hoa file log cu."""
    fields = ["ticker", "year", "url", "title", "domain", "file", "status", "note", "checked_at"]
    rows = []
    if LOG_FILE.exists():
        try:
            with LOG_FILE.open("r", newline="", encoding="utf-8-sig") as source:
                rows = list(csv.DictReader(source))
        except (OSError, csv.Error):
            rows = []
    valid_statuses = {"downloaded_zenodo_pdf", "downloaded_pdf", "downloaded_check_content", "exists_verified", "exists_pdf", "not_found", "download_failed", "error"}
    rows = [
        row for row in rows
        if row.get("status") in valid_statuses
        and (row.get("ticker", "").upper(), str(row.get("year", ""))) != (ticker, str(year))
    ]
    rows.append({
        "ticker": ticker, "year": year, "url": url,
        "title": "", "domain": urlparse(url).netloc if url else "",
        "file": f"{ticker}_{year}.pdf" if status in {"downloaded_zenodo_pdf", "downloaded_pdf", "exists_verified", "exists_pdf"} else "",
        "status": status, "note": note,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    })
    try:
        with LOG_FILE.open("w", newline="", encoding="utf-8-sig") as target:
            writer = csv.DictWriter(target, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    except OSError as exc:
        print(f"  Khong ghi duoc log CSV: {exc}")


# ============================================================
# 8. XU LY MOT MA CO PHIEU VA MOT NAM
# ============================================================

def process_report(ticker, year, allow_web_fallback=True):
    ticker = ticker.strip().upper()
    destination = OUTPUT_DIR / f"{ticker}_{year}.pdf"

    print("\n" + "-" * 60)
    print(f"DANG XU LY: {ticker} - {year}")
    catalog_entry = find_zenodo_report(ticker, year)
    zenodo_attempted = False

    # Khong tai lai file da ton tai neu co dung chu ky PDF.
    if destination.exists():
        try:
            with open(destination, "rb") as file:
                signature = file.read(5)

            if signature == b"%PDF-":
                if catalog_entry:
                    existing_sha256 = hashlib.sha256(destination.read_bytes()).hexdigest()
                    if existing_sha256.lower() == catalog_entry["sha256"].strip().lower():
                        print(f"  Da co PDF va SHA-256 khop catalog: {destination.name}")
                        write_log(ticker, year, "exists_verified", note="PDF khớp SHA-256 trong catalog Zenodo.")
                        return True
                    print("  PDF cu khong khop catalog; thu lay ban chuan tu Zenodo.")
                    zenodo_attempted = True
                    if download_zenodo_report(catalog_entry, destination):
                        archive_url = f"{ZENODO_RECORD_URL}/files/vn_bctn_{catalog_entry['archive_period']}.zip?download=1"
                        write_log(ticker, year, "downloaded_zenodo_pdf", url=archive_url,
                                  note=f"Da xac minh SHA-256: {catalog_entry['sha256']}")
                        return True
                else:
                    print(f"  Da co file PDF: {destination.name}")
                    write_log(ticker, year, "exists_pdf",
                              note="File PDF da ton tai; khong co ban ghi trong catalog de xac minh hash.")
                    return True

        except OSError:
            pass

        print("  File cu khong hop le, se tim va tai lai.")

        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass

    if catalog_entry and not zenodo_attempted and download_zenodo_report(catalog_entry, destination):
        archive_url = f"{ZENODO_RECORD_URL}/files/vn_bctn_{catalog_entry['archive_period']}.zip?download=1"
        write_log(ticker, year, "downloaded_zenodo_pdf", url=archive_url,
                  note=f"Da xac minh SHA-256: {catalog_entry['sha256']}")
        return True

    if not allow_web_fallback:
        write_log(ticker, year, "download_failed",
                  note="Khong lay duoc PDF tu Zenodo; bo qua tim web theo yeu cau.")
        return False

    print("  Catalog khong co file/phuong phap Range gap loi; chuyen sang DDGS/Bing.")
    urls = search_pdf_links(ticker, year)

    if not urls:
        write_log(
            ticker,
            year,
            "not_found",
            note="Khong tim thay link PDF qua cac nguon da thu.",
        )
        return False

    # Thu tung link cho den khi tai duoc mot PDF hop le.
    tried = set()
    for index, url in enumerate(urls, start=1):
        tried.add(url)
        print(f"  Thu link {index}/{len(urls)}: {url}")

        if download_pdf(url, destination):
            write_log(
                ticker,
                year,
                "downloaded_pdf",
                url=url,
                note=(
                    "Da tai va kiem tra chu ky PDF; "
                    "can kiem tra lai dung cong ty va nam."
                ),
            )
            return True

        time.sleep(0.5)

    # DDGS đôi khi trả kết quả nhưng toàn bộ link chết/không phải PDF.
    # Vẫn thử Bing trước khi kết luận tải thất bại.
    fallback_urls = [url for url in search_with_bing(ticker, year) if url not in tried]
    for index, url in enumerate(fallback_urls, start=1):
        print(f"  Thu link Bing du phong {index}/{len(fallback_urls)}: {url}")
        if download_pdf(url, destination):
            write_log(
                ticker, year, "downloaded_pdf", url=url,
                note="Da tai sau khi thu Bing; can xac minh dung cong ty va nam.",
            )
            return True

    write_log(
        ticker,
        year,
        "download_failed",
        note="Co link tim thay nhung khong tai duoc PDF hop le.",
    )

    return False


# ============================================================
# 9. NHAP MA CO PHIEU VA CHAY CHUONG TRINH
# ============================================================

def get_tickers():
    while True:
        raw = input(
            "\nNhap ma co phieu, cach nhau bang dau phay "
            "(vi du: MWG, VCB, HPG): "
        ).strip()

        tickers = list(dict.fromkeys(
            ticker.strip().upper()
            for ticker in raw.split(",")
            if ticker.strip()
        ))

        if not tickers:
            print("Ban chua nhap ma co phieu. Hay nhap lai.")
            continue

        invalid = [
            ticker for ticker in tickers
            if not re.fullmatch(r"[A-Z0-9]{2,10}", ticker)
        ]

        if invalid:
            print("Ma khong hop le: " + ", ".join(invalid))
            continue

        return tickers


def main():
    parser = argparse.ArgumentParser(description="Tai BCTN theo ma va nam.")
    parser.add_argument("--ticker", help="Ma co phieu, vi du FPT")
    parser.add_argument("--year", type=int, help="Nam bao cao, vi du 2023")
    parser.add_argument(
        "--zenodo-only", action="store_true",
        help="Chi tai tu catalog Zenodo, khong chay tim kiem DDGS/Bing neu loi.",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("CONG CU THU THAP BAO CAO THUONG NIEN")
    print("=" * 60)
    print(f"Thu muc luu PDF: {OUTPUT_DIR}")
    print(f"File log: {LOG_FILE}")
    years = [args.year] if args.year else YEARS
    if any(year < 2000 or year > 2100 for year in years):
        parser.error("Nam khong hop le.")
    print(f"Cac nam xu ly: {', '.join(map(str, years))}")

    if args.ticker:
        tickers = [item.strip().upper() for item in args.ticker.split(",") if item.strip()]
        invalid = [ticker for ticker in tickers if not re.fullmatch(r"[A-Z0-9]{2,10}", ticker)]
        if not tickers or invalid:
            parser.error("Ma co phieu khong hop le.")
    else:
        tickers = get_tickers()

    total = len(tickers) * len(years)
    completed = 0
    successful = 0
    failed = 0

    for ticker in tickers:
        for year in years:
            completed += 1

            print(f"\nTIEN DO: {completed}/{total}")

            try:
                success = process_report(ticker, year, allow_web_fallback=not args.zenodo_only)

                if success:
                    successful += 1
                else:
                    failed += 1

            except Exception as exc:
                # Loi bat ngo cua mot ma/nam khong dung ca chuong trinh.
                failed += 1
                print(f"  Loi khong du kien voi {ticker} - {year}: {exc}")

                write_log(
                    ticker,
                    year,
                    "error",
                    note=str(exc),
                )

    print("\n" + "=" * 60)
    print("DA HOAN TAT")
    print(f"Tong so ma-nam: {total}")
    print(f"Da co/tai duoc PDF: {successful}")
    print(f"Chua tim thay hoac tai that bai: {failed}")
    print(f"Thu muc PDF: {OUTPUT_DIR}")
    print(f"Log chi tiet: {LOG_FILE}")
    print("=" * 60)

    print(
        "\nLuu y: PDF hop le ve mat ky thuat chua chac la bao cao "
        "dung cong ty va dung nam. Hay kiem tra ten doanh nghiep "
        "va trang bia truoc khi dung cho phan tich."
    )


if __name__ == "__main__":
    main()
