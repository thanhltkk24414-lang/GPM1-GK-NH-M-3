"""
THU THAP DU LIEU TAI CHINH CO PHIEU VIET NAM

Chuc nang:
- Nhap ma co phieu bat ky, khong co danh sach ma co dinh.
- Thu thap bao cao ket qua kinh doanh.
- Thu thap bang can doi ke toan.
- Thu thap bao cao luu chuyen tien te.
- Thu thap cac chi so tai chinh.
- Luu du lieu CSV, Excel va nhat ky chay chuong trinh.

Cach chay:
    python core/fundamental.py
"""

from pathlib import Path
from datetime import datetime
import re

import pandas as pd
from vnstock import Fundamental


# ============================================================
# 1. CAU HINH DUONG DAN
# ============================================================

ROOT_DIR = Path(__file__).resolve().parent.parent

RAW_DIR = ROOT_DIR / "data" / "raw"
PROCESSED_DIR = ROOT_DIR / "data" / "processed"
REPORT_DIR = ROOT_DIR / "reports"

EXCEL_FILE = PROCESSED_DIR / "tv2_financial_data.xlsx"
LOG_FILE = REPORT_DIR / "tv2_run_log.csv"


# Cac loai bao cao can thu thap
REPORT_TYPES = {
    "income_statement": "income_statement",
    "balance_sheet": "balance_sheet",
    "cash_flow": "cash_flow",
    "ratio": "ratio",
}


# ============================================================
# 2. TAO THU MUC
# ============================================================

def create_folders():
    """Tao cac thu muc dau ra neu chua ton tai."""
    for folder in [RAW_DIR, PROCESSED_DIR, REPORT_DIR]:
        folder.mkdir(parents=True, exist_ok=True)


# ============================================================
# 3. NHAP MA CO PHIEU BAT KY
# ============================================================

def get_tickers():
    """Cho phep nhap mot hoac nhieu ma co phieu tuy y."""

    while True:
        raw = input(
            "\nNhap ma co phieu, cach nhau bang dau phay "
            "(vi du: VCB, MWG, DGC, ACB): "
        ).strip()

        tickers = list(dict.fromkeys(
            item.strip().upper()
            for item in raw.split(",")
            if item.strip()
        ))

        if not tickers:
            print("[CANH BAO] Ban chua nhap ma co phieu.")
            continue

        invalid = [
            ticker
            for ticker in tickers
            if not re.fullmatch(r"[A-Z0-9]{2,10}", ticker)
        ]

        if invalid:
            print("[LOI] Ma khong hop le: " + ", ".join(invalid))
            continue

        return tickers


# ============================================================
# 4. LAY BAO CAO TAI CHINH
# ============================================================

def fetch_report(company, method_name):
    """Lay mot loai bao cao theo nam."""

    method = getattr(company, method_name)
    df = method(period="year")

    if df is None:
        return pd.DataFrame()

    if isinstance(df, pd.DataFrame):
        return df

    return pd.DataFrame(df)


# ============================================================
# 5. LUU CSV
# ============================================================

def save_csv(df, filepath):
    """Luu DataFrame thanh file CSV."""

    if df is None or df.empty:
        return False

    df.to_csv(
        filepath,
        index=False,
        encoding="utf-8-sig",
    )

    return True


# ============================================================
# 6. THU THAP DU LIEU MOT MA CO PHIEU
# ============================================================

def fetch_one_ticker(api, ticker):
    """
    Thu thap cac bao cao cua mot ma co phieu.
    Neu mot bao cao bi loi, tiep tuc thu cac bao cao khac.
    """

    print("\n" + "=" * 60)
    print(f"DANG THU THAP MA: {ticker}")
    print("=" * 60)

    company_data = {}

    try:
        company = api.equity(ticker)

    except Exception as error:
        print(
            f"[LOI] Khong tao duoc doi tuong {ticker}: {error}"
        )

        for report_name in REPORT_TYPES:
            company_data[report_name] = pd.DataFrame()

        return company_data

    for report_name, method_name in REPORT_TYPES.items():

        try:
            df = fetch_report(company, method_name)
            company_data[report_name] = df

            filepath = RAW_DIR / f"{ticker}_{report_name}.csv"

            if save_csv(df, filepath):
                print(
                    f"[OK] {ticker} - {report_name}: "
                    f"{len(df)} dong"
                )
                print(f"     File: {filepath.name}")

            else:
                print(
                    f"[CANH BAO] {ticker} - {report_name}: "
                    "khong co du lieu"
                )

        except Exception as error:
            print(
                f"[LOI] {ticker} - {report_name}: {error}"
            )

            company_data[report_name] = pd.DataFrame()

    return company_data


# ============================================================
# 7. XUAT EXCEL
# ============================================================

def export_excel(all_data):
    """Gop du lieu cac ma co phieu vao mot file Excel."""

    try:
        with pd.ExcelWriter(
            EXCEL_FILE,
            engine="openpyxl",
        ) as writer:

            for report_name in REPORT_TYPES:
                frames = []

                for ticker, reports in all_data.items():
                    df = reports.get(
                        report_name,
                        pd.DataFrame(),
                    )

                    if df is None or df.empty:
                        continue

                    df = df.copy()

                    # Them ma co phieu vao dau moi dong
                    df.insert(0, "ticker", ticker)
                    frames.append(df)

                if frames:
                    combined = pd.concat(
                        frames,
                        ignore_index=True,
                        sort=False,
                    )
                else:
                    combined = pd.DataFrame(
                        columns=["ticker"]
                    )

                combined.to_excel(
                    writer,
                    sheet_name=report_name,
                    index=False,
                )

        print(f"\n[OK] Da xuat Excel: {EXCEL_FILE}")

    except Exception as error:
        print(f"[LOI] Khong xuat duoc Excel: {error}")


# ============================================================
# 8. LUU NHAT KY CHAY
# ============================================================

def save_log(all_data):
    """Luu trang thai thu thap cua tung ma va tung bao cao."""

    rows = []
    checked_at = datetime.now().isoformat(timespec="seconds")

    for ticker, reports in all_data.items():

        for report_name in REPORT_TYPES:
            df = reports.get(
                report_name,
                pd.DataFrame(),
            )

            if df is None or df.empty:
                status = "empty_or_error"
                row_count = 0
            else:
                status = "success"
                row_count = len(df)

            rows.append({
                "ticker": ticker,
                "report": report_name,
                "row_count": row_count,
                "status": status,
                "checked_at": checked_at,
            })

    log_df = pd.DataFrame(rows)

    try:
        log_df.to_csv(
            LOG_FILE,
            index=False,
            encoding="utf-8-sig",
        )

        print(f"[OK] Da luu log: {LOG_FILE}")

    except OSError as error:
        print(f"[LOI] Khong ghi duoc log: {error}")


# ============================================================
# 9. CHUONG TRINH CHINH
# ============================================================

def main():
    create_folders()

    print("=" * 60)
    print("CHUONG TRINH THU THAP DU LIEU TAI CHINH")
    print("=" * 60)
    print("Ban co the nhap bat ky ma co phieu nao.")
    print("Nhap nhieu ma thi cach nhau bang dau phay.")

    # Nhap ma tu nguoi dung, khong dung danh sach co dinh
    tickers = get_tickers()

    all_data = {}

    try:
        api = Fundamental()

    except Exception as error:
        print(f"[LOI] Khong khoi tao duoc VNStock: {error}")
        print("Hay kiem tra cai dat va phien ban vnstock.")
        return

    total = len(tickers)

    for index, ticker in enumerate(tickers, start=1):
        print(f"\nTIEN DO: {index}/{total}")

        try:
            all_data[ticker] = fetch_one_ticker(
                api,
                ticker,
            )

        except Exception as error:
            print(f"[LOI] Ma {ticker}: {error}")

            all_data[ticker] = {
                report_name: pd.DataFrame()
                for report_name in REPORT_TYPES
            }

    # Xuat du lieu va log
    export_excel(all_data)
    save_log(all_data)

    print("\n" + "=" * 60)
    print("DA HOAN TAT")
    print(f"So ma co phieu da nhap: {total}")
    print(f"File Excel: {EXCEL_FILE}")
    print(f"Thu muc CSV: {RAW_DIR}")
    print(f"File log: {LOG_FILE}")
    print("=" * 60)

    print(
        "\nLuu y: Can kiem tra du lieu thuc te de xac nhan "
        "cac chi so P/E, P/B, ROE, ROA va tang truong "
        "doanh thu, loi nhuan co day du hay khong."
    )


if __name__ == "__main__":
    main()

