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
    import fitz  # PyMuPDF
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


# Tu khoa cho cac chu de trong bao cao thuong nien
TOPICS = {
    "Tong quan doanh nghiep": [
        "gioi thieu cong ty",
        "tong quan doanh nghiep",
        "company overview",
        "general information",
        "linh vuc kinh doanh",
        "nganh nghe kinh doanh",
    ],
    "Ket qua kinh doanh": [
        "ket qua kinh doanh",
        "doanh thu",
        "loi nhuan",
        "business results",
        "revenue",
        "profit",
        "financial performance",
    ],
    "Tinh hinh tai chinh": [
        "tinh hinh tai chinh",
        "tai san",
        "nguon von",
        "financial position",
        "total assets",
        "liabilities",
        "equity",
    ],
    "Chien luoc va ke hoach": [
        "chien luoc phat trien",
        "ke hoach kinh doanh",
        "dinh huong phat trien",
        "business strategy",
        "development strategy",
        "business plan",
    ],
    "Rui ro kinh doanh": [
        "rui ro",
        "quan tri rui ro",
        "risk management",
        "business risks",
        "risk factors",
    ],
    "Phat trien ben vung": [
        "phat trien ben vung",
        "bao ve moi truong",
        "trach nhiem xa hoi",
        "sustainability",
        "environment",
        "social responsibility",
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

    return text.lower()


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

    with fitz.open(pdf_path) as document:
        page_count = len(document)

        for page_number, page in enumerate(document, start=1):
            page_text = page.get_text("text").strip()

            if page_text:
                page_texts.append(
                    f"\n--- TRANG {page_number} ---\n"
                    f"{page_text}"
                )

    full_text = clean_text("\n".join(page_texts))

    return full_text, page_count


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
    """
    Tao tom tat nhanh bang cach trich cac cau tu PDF.
    Khong tu tao so lieu hay ket luan khong co trong tai lieu.
    """

    lines = [
        "TOM TAT NHANH BAO CAO THUONG NIEN",
        "=" * 65,
        f"Ten file: {pdf_name}",
        f"So trang PDF: {page_count}",
        f"So ky tu trich xuat: {len(text):,}",
        f"Thoi gian xu ly: {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        "GHI CHU:",
        "- Tom tat duoc tao tu van ban trich xuat trong PDF.",
        "- Can doi chieu voi bao cao goc truoc khi su dung so lieu.",
        "- PDF scan co the khong trich xuat duoc van ban neu chua OCR.",
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

    # Trich phan dau van ban
    lines.extend([
        "1. NOI DUNG DAU BAO CAO",
        "-" * 45,
        text[:2000],
        "",
    ])

    sentences = split_sentences(text)
    normalized_sentences = [
        (sentence, normalize_text(sentence))
        for sentence in sentences
    ]

    # Tim cac cau theo tung chu de
    for topic, keywords in TOPICS.items():
        normalized_keywords = [
            normalize_text(keyword)
            for keyword in keywords
        ]

        matches = []

        for original_sentence, normalized_sentence in normalized_sentences:
            if any(
                keyword in normalized_sentence
                for keyword in normalized_keywords
            ):
                if original_sentence not in matches:
                    matches.append(original_sentence)

            if len(matches) >= 5:
                break

        lines.append(topic.upper())
        lines.append("-" * 45)

        if matches:
            for sentence in matches:
                lines.append("- " + sentence)
        else:
            lines.append(
                "Chua tim thay cau phu hop trong van ban trich xuat."
            )

        lines.append("")

    # Tim mot so cau co chua chu so de doi chieu
    numeric_sentences = []

    for sentence in sentences:
        if re.search(r"\d", sentence) and len(sentence) >= 50:
            if sentence not in numeric_sentences:
                numeric_sentences.append(sentence)

        if len(numeric_sentences) >= 10:
            break

    lines.extend([
        "CAC CAU CO CHUA SO LIEU DE DOI CHIEU",
        "-" * 45,
    ])

    if numeric_sentences:
        for sentence in numeric_sentences:
            lines.append("- " + sentence)
    else:
        lines.append("Khong tim thay cau co chua so lieu.")

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
        text, page_count = extract_pdf_text(pdf_path)

        result["pages"] = page_count
        result["characters"] = len(text)

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
        if len(text) < MIN_TEXT_LENGTH:
            result["status"] = "needs_review"
            result["error"] = (
                "Van ban qua ngan; kiem tra PDF hoac OCR."
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

