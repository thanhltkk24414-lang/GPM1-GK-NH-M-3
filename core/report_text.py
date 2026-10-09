"""
TRICH XUAT VA TOM TAT BAO CAO THUONG NIEN

Dau vao:
    annual_reports/*.pdf

Dau ra:
    data/processed/annual_report_text/*.txt
    reports/annual_report_summaries/*_summary.txt
    reports/annual_report_text_log.csv

Cach chay:
    python core/report_text.py
"""

from pathlib import Path
from datetime import datetime
import csv
import re
import unicodedata

try:
    import pymupdf as fitz
except ImportError:
    print("Thieu thu vien PyMuPDF.")
    print("Hay chay lenh: python -m pip install pymupdf")
    raise SystemExit(1)


# ============================================================
# 1. CAU HINH DUONG DAN
# ============================================================

ROOT_DIR = Path(__file__).resolve().parent.parent

PDF_DIR = ROOT_DIR / "annual_reports"
TEXT_DIR = ROOT_DIR / "data" / "processed" / "annual_report_text"
SUMMARY_DIR = ROOT_DIR / "reports" / "annual_report_summaries"
LOG_FILE = ROOT_DIR / "reports" / "annual_report_text_log.csv"

MIN_TEXT_LENGTH = 200
MIN_TEXT_PER_PAGE = 80
MIN_ANNUAL_REPORT_PAGES = 5


# Tu khoa cho cac chu de trong bao cao thuong nien
TOPICS = {
    "Tong quan doanh nghiep": [
        "linh vuc kinh doanh",
        "hoat dong kinh doanh chinh",
        "cung cap dich vu",
        "san pham va dich vu",
        "business segments",
        "core business",
        "company operates",
        "business activities",
        "thi truong hoat dong",
    ],
    "Ket qua kinh doanh": [
        "doanh thu thuan",
        "doanh thu hop nhat",
        "loi nhuan sau thue",
        "loi nhuan truoc thue",
        "tang truong doanh thu",
        "tang truong loi nhuan",
        "ket qua kinh doanh",
        "net revenue",
        "profit after tax",
        "profit before tax",
        "revenue growth",
        "profit growth",
        "ebitda",
    ],
    "Tinh hinh tai chinh": [
        "tong tai san",
        "tong no phai tra",
        "von chu so huu",
        "dong tien kinh doanh",
        "no vay",
        "financial position",
        "balance sheet",
        "cash flow",
        "total assets",
        "total liabilities",
        "shareholders equity",
        "working capital",
        "debt ratio",
        "liquidity",
        "nguon von",
    ],
    "Chien luoc va ke hoach": [
        "chien luoc phat trien",
        "ke hoach kinh doanh",
        "muc tieu trong nam",
        "dinh huong phat trien",
        "ke hoach dau tu",
        "business strategy",
        "development strategy",
        "business plan",
        "strategic priorities",
        "investment plan",
    ],
    "Rui ro kinh doanh": [
        "rui ro",
        "quan tri rui ro",
        "rui ro tai chinh",
        "rui ro thi truong",
        "rui ro hoat dong",
        "risk management",
        "risk factors",
        "business risks",
        "market volatility",
        "cyber security risk",
        "exchange rate risk",
    ],
    "Phat trien ben vung": [
        "phat trien ben vung",
        "trach nhiem xa hoi",
        "bao ve moi truong",
        "giam phat thai",
        "phat thai carbon",
        "phat trien cong dong",
        "sustainability",
        "environment",
        "social responsibility",
        "carbon emissions",
        "net zero",
    ],
}


# ============================================================
# 2. TAO THU MUC
# ============================================================

def create_folders():
    """Tao cac thu muc dau ra neu chua ton tai."""

    for folder in [
        PDF_DIR,
        TEXT_DIR,
        SUMMARY_DIR,
        LOG_FILE.parent,
    ]:
        folder.mkdir(parents=True, exist_ok=True)


# ============================================================
# 3. CHUAN HOA VAN BAN
# ============================================================

def normalize_text(text):
    """
    Chuyen chu ve dang khong dau de tim tu khoa.
    Vi du: 'doanh thu' va 'doanh thu' deu duoc so sanh on dinh.
    """

    text = unicodedata.normalize("NFD", text)
    text = "".join(
        char for char in text
        if unicodedata.category(char) != "Mn"
    )

    # Vietnamese d-stroke is not decomposed by Unicode NFD.
    return text.lower().replace("đ", "d")


def clean_text(text):
    """Lam sach van ban trich xuat tu PDF."""

    text = text.replace("\x00", " ")
    text = text.replace("\r", "\n")

    # Noi lai tu bi ngat dong bang dau gach noi
    text = re.sub(r"-\s*\n\s*", "", text)

    # Chuan hoa khoang trang
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ============================================================
# 4. DOC PDF
# ============================================================

def extract_pdf_text(pdf_path):
    """
    Doc noi dung PDF.
    Tra ve van ban va tong so trang.
    """

    page_texts = []
    ocr_pages = 0

    with fitz.open(pdf_path) as document:
        page_count = len(document)

        for page_number, page in enumerate(document, start=1):
            page_text = page.get_text("text").strip()

            # PDF scan: thử OCR tiếng Việt nếu Tesseract và pytesseract có sẵn.
            if len(page_text) < 40:
                try:
                    import pytesseract
                    from PIL import Image
                    import io
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    image = Image.open(io.BytesIO(pix.tobytes("png")))
                    ocr_text = pytesseract.image_to_string(image, lang="vie+eng")
                    if len(ocr_text.strip()) > len(page_text):
                        page_text = ocr_text.strip()
                        ocr_pages += 1
                except Exception:
                    # Giữ văn bản có sẵn; trạng thái bên dưới sẽ báo cần OCR.
                    pass

            if page_text:
                page_texts.append(
                    f"\n--- TRANG {page_number} ---\n"
                    f"{page_text}"
                )

    full_text = clean_text("\n".join(page_texts))

    return full_text, page_count, ocr_pages


# ============================================================
# 5. TACH CAU
# ============================================================

def split_sentences(text):
    """Tach van ban thanh cac cau de phuc vu tom tat."""

    # Ghep cac dong trong cung mot doan
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r"[ \t]+", " ", text)

    sentences = re.split(
        r"(?<=[.!?])\s+",
        text,
    )

    result = []

    for sentence in sentences:
        sentence = sentence.strip()

        # Bo qua cau qua ngan, tieu de ngan va so trang
        if len(sentence) >= 45:
            result.append(sentence)

    return result


# ============================================================
# 6. TOM TAT THEO CHU DE
# ============================================================

def make_summary(text, pdf_name, page_count):
    """Create a concise extractive summary without inventing claims or figures."""

    def clip_sentence(sentence, limit):
        sentence = " ".join(sentence.split())
        if len(sentence) <= limit:
            return sentence
        front_size = int(limit * 0.58)
        back_size = limit - front_size - 5
        front = sentence[:front_size].rsplit(" ", 1)[0].rstrip(" ,;:-")
        back = sentence[-back_size:].lstrip(" ,;:-")
        if " " in back:
            back = back.split(" ", 1)[1]
        return front + " ... " + back

    lines = [
        "TOM TAT BAO CAO THUONG NIEN",
        "=" * 65,
        f"Ten file: {pdf_name}",
        f"So trang PDF: {page_count}",
        "",
        "Cac y duoi day duoc trich tu bao cao goc; so lieu va dien giai can doi chieu tai lieu.",
        "",
    ]

    if not text.strip():
        lines.extend([
            "KHONG TRICH XUAT DUOC VAN BAN.",
            "Tai lieu co the la PDF scan hoac khong co lop van ban.",
            "Can kiem tra file PDF va xem xet su dung OCR.",
        ])
        return "\n".join(lines)

    if len(text) < MIN_TEXT_LENGTH:
        lines.extend([
            "CAN KIEM TRA TAI LIEU:",
            "Van ban trich xuat qua ngan.",
            "File co the la ban scan, tai lieu khong day du",
            "hoac khong phai bao cao thuong nien day du.",
            "",
        ])

    source_sentences = []
    for sentence in split_sentences(text):
        page_match = re.search(r"---\s*TRANG\s+(\d+)\s*--", sentence, re.I)
        page_number = page_match.group(1) if page_match else ""
        clean_sentence = re.sub(r"---\s*TRANG\s+\d+\s*--", " ", sentence, flags=re.I)
        clean_sentence = re.sub(r"[•▪●]", " ", clean_sentence)
        clean_sentence = " ".join(clean_sentence.split()).strip(" -|;:")
        normalized = normalize_text(clean_sentence)

        # Skip contents pages, navigation labels, and digit-heavy index rows.
        if any(marker in normalized for marker in (
            "muc luc", "table of contents", "con so noi bat", "giai thuong tieu bieu",
            "hoat dong noi bat", "danh muc noi dung",
        )):
            continue
        short_numbers = re.findall(r"(?<!\w)\d{1,2}(?!\w)", normalized)
        if len(short_numbers) >= 4 or len(clean_sentence) < 60:
            continue
        # Skip company-registration rows and other directory metadata.
        if re.search(r"(?<!\d)\d{10,13}(?!\d)", clean_sentence):
            continue
        if not re.search(r"[a-zA-ZÀ-ỹ]{3,}", clean_sentence):
            continue
        source_sentences.append((clean_sentence, normalized, page_number))

    used_sentences = []
    topic_exclusions = {
        "Tong quan doanh nghiep": (
            "ghi nhan doanh thu", "doanh thu duoc ghi nhan", "doanh thu cung cap dich vu chi",
            "co kha nang thu duoc loi ich kinh te",
        ),
        "Ket qua kinh doanh": (
            "loi the thuong mai", "thoai von", "ghi nhan", "ghi giam",
            "chinh sach ke toan", "nguyen tac ke toan",
        ),
        "Tinh hinh tai chinh": (
            "dieu chinh lai so luong co phieu", "chi tieu ve co cau von",
            "he so no/tong tai san", "he so no/von chu so huu",
        ),
        "Chien luoc va ke hoach": (
            "thong qua tai dai hoi dong co dong", "ban kiem soat",
            "nghi quyet hoi dong quan tri", "hdqt xem xet va phe duyet",
            "thanh vien hdqt doc lap", "uy ban", "tieu ban",
            "chiu trach nhiem", "giam sat cac van de",
        ),
        "Rui ro kinh doanh": ("ket qua noi bat", "highlights",),
    }
    for topic, keywords in TOPICS.items():
        normalized_keywords = [normalize_text(keyword) for keyword in keywords]
        exclusions = tuple(normalize_text(term) for term in topic_exclusions.get(topic, ()))
        candidates = []
        for original, normalized, page_number in source_sentences:
            if any(term in normalized for term in exclusions):
                continue
            hits = [keyword for keyword in normalized_keywords if keyword in normalized]
            if not hits:
                continue

            # Multiword concepts are more meaningful than generic single words.
            score = sum(2.0 if " " in keyword else 1.0 for keyword in hits)
            score += min(2.0, max(0, sum(normalized.count(keyword) for keyword in hits) - len(hits)) * 0.25)
            if len(hits) == 1 and " " not in hits[0]:
                continue
            if re.search(r"\d|%", original):
                score += 1.0
            if 100 <= len(original) <= 500:
                score += 1.0
            if len(original) > 700:
                score -= 2.0

            # Penalize passages that mostly look like headings or navigation.
            alpha = [char for char in original if char.isalpha()]
            uppercase_ratio = sum(char.isupper() for char in alpha) / max(len(alpha), 1)
            if uppercase_ratio > 0.65:
                score -= 2.0
            if score >= 2.0:
                candidates.append((score, original, normalized, page_number))

        candidates.sort(key=lambda candidate: (candidate[0], min(len(candidate[1]), 360)), reverse=True)
        selected = []
        for _, original, normalized, page_number in candidates:
            words = set(normalized.split())
            if any(
                len(words & prior_words) / max(len(words | prior_words), 1) > 0.65
                for prior_words in used_sentences
            ):
                continue
            selected.append((original, page_number, words))
            if len(selected) == 1:
                break

        lines.extend([topic.upper(), "-" * 45])
        if selected:
            for original, page_number, words in selected:
                page_ref = f" (tr. {page_number})" if page_number else ""
                lines.append("- " + clip_sentence(original, 230) + page_ref)
                used_sentences.append(words)
        else:
            lines.append("Chua tim thay doan trich du tin cay cho muc nay.")
        lines.append("")

    if not used_sentences:
        lines.append("CAN KIEM TRA: khong tim thay doan trich phu hop; xem van ban day du va bao cao goc.")

    return "\n".join(lines)


# ============================================================
# 7. XU LY MOT FILE PDF
# ============================================================

def process_one_pdf(pdf_path):
    """Trich xuat va tao tom tat cho mot file PDF."""

    print("\n" + "=" * 60)
    print(f"DANG XU LY: {pdf_path.name}")
    print("=" * 60)

    result = {
        "file": pdf_path.name,
        "status": "",
        "pages": 0,
        "characters": 0,
        "text_file": "",
        "summary_file": "",
        "error": "",
        "processed_at": datetime.now().isoformat(
            timespec="seconds"
        ),
    }

    try:
        # Doc PDF
        text, page_count, ocr_pages = extract_pdf_text(pdf_path)

        result["pages"] = page_count
        result["characters"] = len(text)
        result["ocr_pages"] = ocr_pages

        # Luu van ban day du
        text_path = TEXT_DIR / f"{pdf_path.stem}.txt"

        text_path.write_text(
            text,
            encoding="utf-8",
        )

        result["text_file"] = str(
            text_path.relative_to(ROOT_DIR)
        )

        # Tao va luu ban tom tat
        summary = make_summary(
            text=text,
            pdf_name=pdf_path.name,
            page_count=page_count,
        )

        summary_path = (
            SUMMARY_DIR / f"{pdf_path.stem}_summary.txt"
        )

        summary_path.write_text(
            summary,
            encoding="utf-8",
        )

        result["summary_file"] = str(
            summary_path.relative_to(ROOT_DIR)
        )

        # Danh dau cac file can kiem tra
        if (len(text) < MIN_TEXT_LENGTH
                or page_count < MIN_ANNUAL_REPORT_PAGES
                or (page_count and len(text) / page_count < MIN_TEXT_PER_PAGE)):
            result["status"] = "needs_review"
            result["error"] = (
                "PDF quá ngắn/ít chữ so với số trang; kiểm tra file, OCR và đúng năm báo cáo."
            )

            print("[CAN KIEM TRA] Van ban trich xuat qua ngan.")

        else:
            result["status"] = "success"
            print("[OK] Trich xuat thanh cong.")

        print(f"So trang: {page_count}")
        print(f"So ky tu: {len(text):,}")
        print(f"Van ban: {text_path}")
        print(f"Tom tat: {summary_path}")

    except Exception as error:
        result["status"] = "error"
        result["error"] = str(error)

        print(f"[LOI] {pdf_path.name}: {error}")

    return result


# ============================================================
# 8. LUU LOG CSV
# ============================================================

def save_log(results):
    """Ghi ket qua xu ly vao CSV."""

    fields = [
        "file",
        "status",
        "pages",
        "characters",
        "ocr_pages",
        "text_file",
        "summary_file",
        "error",
        "processed_at",
    ]

    with open(
        LOG_FILE,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fields,
        )
        writer.writeheader()
        writer.writerows(results)

    print(f"\n[OK] Da luu log: {LOG_FILE}")


# ============================================================
# 9. CHUONG TRINH CHINH
# ============================================================

def main():
    create_folders()

    pdf_files = sorted(PDF_DIR.glob("*.pdf"))

    print("=" * 60)
    print("TRICH XUAT VA TOM TAT BAO CAO THUONG NIEN")
    print("=" * 60)
    print(f"Thu muc PDF: {PDF_DIR}")

    if not pdf_files:
        print("[CANH BAO] Khong tim thay file PDF.")
        print("Hay chay python core/annual_report.py truoc.")
        return

    print(f"So file PDF tim thay: {len(pdf_files)}")

    results = []

    for index, pdf_path in enumerate(pdf_files, start=1):
        print(f"\nTIEN DO: {index}/{len(pdf_files)}")

        result = process_one_pdf(pdf_path)
        results.append(result)

    save_log(results)

    successful = sum(
        1 for item in results
        if item["status"] == "success"
    )

    needs_review = sum(
        1 for item in results
        if item["status"] == "needs_review"
    )

    failed = sum(
        1 for item in results
        if item["status"] == "error"
    )

    print("\n" + "=" * 60)
    print("HOAN TAT TRICH XUAT BCTN")
    print(f"Tong so PDF: {len(pdf_files)}")
    print(f"Thanh cong: {successful}")
    print(f"Can kiem tra: {needs_review}")
    print(f"Loi xu ly: {failed}")
    print(f"Thu muc van ban: {TEXT_DIR}")
    print(f"Thu muc tom tat: {SUMMARY_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()

