
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

import csv
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS


# ============================================================
# 1. CAU HINH
# ============================================================

PROJECT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_DIR / "annual_reports"
LOG_FILE = OUTPUT_DIR / "annual_report_sources.csv"

YEARS = [2022, 2023, 2024, 2025]

REQUEST_TIMEOUT = 25
MAX_SEARCH_RESULTS = 8
MAX_PAGES_TO_CRAWL = 5
DELAY_SECONDS = 1

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
    "HPG": ["Hoa Phat Group"],
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
        f'"{ticker}" "báo cáo thường niên" năm {year}',
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
    """Ghi tung ket qua xu ly vao CSV."""
    file_exists = LOG_FILE.exists()

    try:
        with open(
            LOG_FILE,
            "a",
            newline="",
            encoding="utf-8-sig",
        ) as file:

            writer = csv.writer(file)

            if not file_exists:
                writer.writerow([
                    "ticker",
                    "year",
                    "status",
                    "url",
                    "note",
                ])

            writer.writerow([
                ticker,
                year,
                status,
                url,
                note,
            ])

    except OSError as exc:
        print(f"  Khong ghi duoc log CSV: {exc}")


# ============================================================
# 8. XU LY MOT MA CO PHIEU VA MOT NAM
# ============================================================

def process_report(ticker, year):
    ticker = ticker.strip().upper()
    destination = OUTPUT_DIR / f"{ticker}_{year}.pdf"

    print("\n" + "-" * 60)
    print(f"DANG XU LY: {ticker} - {year}")

    # Khong tai lai file da ton tai neu co dung chu ky PDF.
    if destination.exists():
        try:
            with open(destination, "rb") as file:
                signature = file.read(5)

            if signature == b"%PDF-":
                print(f"  Da co file PDF: {destination.name}")
                write_log(
                    ticker,
                    year,
                    "exists_pdf",
                    note="File PDF da ton tai; chua xac minh noi dung bao cao.",
                )
                return True

        except OSError:
            pass

        print("  File cu khong hop le, se tim va tai lai.")

        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass

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
    for index, url in enumerate(urls, start=1):
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
    print("=" * 60)
    print("CONG CU THU THAP BAO CAO THUONG NIEN")
    print("=" * 60)
    print(f"Thu muc luu PDF: {OUTPUT_DIR}")
    print(f"File log: {LOG_FILE}")
    print(f"Cac nam xu ly: {', '.join(map(str, YEARS))}")

    tickers = get_tickers()

    total = len(tickers) * len(YEARS)
    completed = 0
    successful = 0
    failed = 0

    for ticker in tickers:
        for year in YEARS:
            completed += 1

            print(f"\nTIEN DO: {completed}/{total}")

            try:
                success = process_report(ticker, year)

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