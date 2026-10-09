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
import os
import re
import unicodedata

import pandas as pd
# Disable optional VNStock telemetry by default. Users can override this before
# launching the application if they explicitly want to enable it.
os.environ.setdefault("VNSTOCK_TELEMETRY", "off")
try:
    from vnstock import Fundamental
except ImportError:
    Fundamental = None


# ============================================================
# 1. CAU HINH DUONG DAN
# ============================================================

ROOT_DIR = Path(__file__).resolve().parent.parent

RAW_DIR = ROOT_DIR / "data" / "raw"
PROCESSED_DIR = ROOT_DIR / "data" / "processed"
REPORT_DIR = ROOT_DIR / "reports"

METRICS_FILE = PROCESSED_DIR / "key_financial_metrics.csv"
LOG_FILE = REPORT_DIR / "tv2_run_log.csv"


# Cac loai bao cao can thu thap
REPORT_TYPES = {
    "income_statement": "income_statement",
    "balance_sheet": "balance_sheet",
    "cash_flow": "cash_flow",
    "ratio": "ratio",
}
FETCH_ERRORS = {}


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
    """Lay mot loai bao cao theo nam, tuong thich alias VNStock v4."""

    method_names = [method_name]
    if method_name == "ratio":
        method_names.append("ratios")
    method = next((getattr(company, name, None) for name in method_names
                   if callable(getattr(company, name, None))), None)
    if method is None:
        raise AttributeError(f"VNStock khong co endpoint {method_name}/{method_names[-1]}.")
    try:
        df = method(period="year")
    except TypeError:
        # Mot so phien ban Unified UI mac dinh tra bao cao nam, khong nhan period.
        df = method()

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


def _read_item_series(df, ids, preferred_labels=()):
    """Lấy chuỗi năm theo item_id; trả None nếu nguồn không có chỉ tiêu."""
    if df is None or df.empty or "item_id" not in df.columns:
        return None
    ids = set(ids)
    selected = df[df["item_id"].astype(str).str.lower().isin(ids)]
    if selected.empty:
        return None
    if len(selected) > 1 and preferred_labels:
        label_column = next(
            (column for column in ("item", "name", "label") if column in selected.columns),
            None,
        )
        if label_column:
            hints = [
                "".join(
                    char for char in unicodedata.normalize("NFD", hint.lower())
                    if unicodedata.category(char) != "Mn"
                )
                for hint in preferred_labels
            ]

            def preferred(label):
                normalized = "".join(
                    char for char in unicodedata.normalize("NFD", str(label).lower())
                    if unicodedata.category(char) != "Mn"
                )
                return any(hint in normalized for hint in hints)

            preferred_rows = selected[selected[label_column].apply(preferred)]
            if not preferred_rows.empty:
                selected = preferred_rows
    row = selected.iloc[0]
    result = {}
    for column in df.columns:
        if re.fullmatch(r"(?:19|20)\d{2}", str(column)):
            value = pd.to_numeric(pd.Series([row[column]]), errors="coerce").iloc[0]
            if pd.notna(value):
                result[int(column)] = float(value)
    return result or None


def _ratio_series(df, tokens):
    if df is None or df.empty or "item_id" not in df.columns:
        return None
    # Match complete item IDs. Substring matching "pe" also matches
    # "per_share" in book_value_per_share_bvps and returns BVPS as P/E.
    accepted_ids = {token.lower() for token in tokens}
    ids = df["item_id"].fillna("").astype(str).str.strip().str.lower()
    selected = df[ids.isin(accepted_ids)]
    if selected.empty:
        return None
    row = selected.iloc[0]
    periods = {}
    for col in df.columns:
        match = re.fullmatch(r"((?:19|20)\d{2})(?:-Q([1-4]))?(?:_\d+)?", str(col), re.I)
        if match:
            value = pd.to_numeric(pd.Series([row[col]]), errors="coerce").iloc[0]
            if pd.notna(value):
                year = int(match.group(1))
                quarter = int(match.group(2) or 0)
                if year not in periods or quarter > periods[year][0]:
                    periods[year] = (quarter, float(value))
    return {year: value for year, (_, value) in periods.items()} or None


def build_key_metrics(all_data, write_csv=True):
    """Chuẩn hóa các chỉ số có sẵn và tính tăng trưởng/ROE/ROA có thể kiểm chứng."""
    output = []
    for ticker, reports in all_data.items():
        income = reports.get("income_statement", pd.DataFrame())
        balance = reports.get("balance_sheet", pd.DataFrame())
        ratios = reports.get("ratio", pd.DataFrame())
        profit = _read_item_series(income, {
            "net_profit", "net_profit_attributable_to_the_equity_holders_of_the_bank",
            "net_profit_after_tax", "profit_after_tax", "net_income",
        }) or {}
        # Không gọi tổng thu nhập hoạt động ngân hàng là "doanh thu";
        # lưu proxy rõ ràng để tránh so sánh sai ngành.
        revenue = _read_item_series(
            income,
            {"net_revenue", "revenue", "total_revenue", "net_sales"},
            preferred_labels=("doanh thu thuần", "net revenue", "net sales"),
        )
        revenue_basis = "reported_revenue"
        if not revenue:
            bank_parts = [
                _read_item_series(income, {"net_interest_income"}),
                _read_item_series(income, {"net_fee_and_commission_income"}),
                _read_item_series(income, {"net_gain_loss_from_foreign_currencies_and_gold_trading"}),
                _read_item_series(income, {"net_gain_loss_from_trading_securities"}),
                _read_item_series(income, {"net_gain_loss_from_investment_securities"}),
                _read_item_series(income, {"net_other_income"}),
            ]
            if any(bank_parts):
                years = set().union(*(set(part or {}) for part in bank_parts))
                revenue = {year: sum((part or {}).get(year, 0) for part in bank_parts) for year in years}
                revenue_basis = "bank_operating_income_proxy"
        assets = _read_item_series(balance, {"total_assets", "assets"}) or {}
        equity = _read_item_series(balance, {"owner_equity", "owners_equity", "total_equity", "equity"}) or {}
        if not equity:
            liabilities = _read_item_series(balance, {"total_liabilities"}) or {}
            liabilities_and_equity = _read_item_series(
                balance, {"total_liabilities_and_owners_equity"}
            ) or {}
            common_years = set(liabilities) & set(liabilities_and_equity)
            equity = {year: liabilities_and_equity[year] - liabilities[year]
                      for year in common_years}
        pe = _ratio_series(ratios, ["pe_ratio", "price_earnings", "price_to_earnings", "price_to_earnings_ratio", "pe"])
        pb = _ratio_series(ratios, ["pb_ratio", "price_book", "price_to_book", "price_to_book_ratio", "pb"])
        roe_vendor = _ratio_series(ratios, ["roe", "return_on_equity"])
        roa_vendor = _ratio_series(ratios, ["roa", "return_on_assets"])
        years = sorted(set(profit) | set(revenue or {}) | set(assets) | set(equity) | set(pe or {}) | set(pb or {}))
        for year in years:
            prior = year - 1
            avg_assets = (assets[year] + assets[prior]) / 2 if year in assets and prior in assets else None
            avg_equity = (equity[year] + equity[prior]) / 2 if year in equity and prior in equity else None
            revenue_growth = None
            if revenue and year in revenue and prior in revenue and revenue[prior] != 0:
                revenue_growth = (revenue[year] / revenue[prior] - 1) * 100
            profit_growth = None
            if year in profit and prior in profit and profit[prior] != 0:
                profit_growth = (profit[year] / profit[prior] - 1) * 100
            roe = roe_vendor.get(year) if roe_vendor else None
            roa = roa_vendor.get(year) if roa_vendor else None
            if roe is None and year in profit and avg_equity:
                roe = profit[year] / avg_equity * 100
            if roa is None and year in profit and avg_assets:
                roa = profit[year] / avg_assets * 100
            output.append({
                "ticker": ticker, "year": year, "PE": (pe or {}).get(year), "PB": (pb or {}).get(year),
                "ROE_pct": roe, "ROA_pct": roa, "revenue_growth_pct": revenue_growth,
                "profit_growth_pct": profit_growth, "revenue_basis": revenue_basis if revenue else "unavailable",
                "source": "vnstock: ratio/income_statement/balance_sheet",
                "quality_note": "ROE/ROA vendor nếu có; nếu tự tính dùng LNST chia bình quân vốn/tài sản. Tăng trưởng cần năm trước.",
            })
    metrics = pd.DataFrame(output)
    if write_csv and not metrics.empty:
        metrics.to_csv(METRICS_FILE, index=False, encoding="utf-8-sig")
    return metrics


def _merge_metrics_csv(new_metrics):
    """Upsert one ticker without deleting metrics of other tickers."""
    if new_metrics is None or new_metrics.empty:
        return
    create_folders()
    if METRICS_FILE.exists():
        try:
            existing = pd.read_csv(METRICS_FILE)
        except (OSError, pd.errors.ParserError, UnicodeError):
            existing = pd.DataFrame()
    else:
        existing = pd.DataFrame()
    ticker = str(new_metrics.iloc[0]["ticker"]).upper()
    if not existing.empty and "ticker" in existing.columns:
        existing = existing[existing["ticker"].astype(str).str.upper() != ticker]
    combined = pd.concat([existing, new_metrics], ignore_index=True, sort=False)
    combined = combined.drop_duplicates(subset=["ticker", "year"], keep="last")
    combined.to_csv(METRICS_FILE, index=False, encoding="utf-8-sig")


def collect_ticker_financials(ticker, api=None):
    """Fetch raw annual statements and normalized indicators for one dashboard request."""
    ticker = ticker.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{2,10}", ticker):
        raise ValueError("Ma co phieu khong hop le.")
    if Fundamental is None:
        raise RuntimeError(
            "Chua co VNStock. Cai dat bang: python -m pip install -r requirements-financial.txt"
        )
    api = api or Fundamental()
    reports = fetch_one_ticker(api, ticker)
    errors = FETCH_ERRORS.get(ticker, {})
    if all(frame is None or frame.empty for frame in reports.values()):
        detail = "; ".join(f"{name}: {message}" for name, message in errors.items())
        raise RuntimeError(
            f"VNStock không trả được dữ liệu tài chính cho {ticker}. "
            f"Kiểm tra kết nối/API và thử lại. {detail}"
        )
    metrics = build_key_metrics({ticker: reports}, write_csv=False)
    _merge_metrics_csv(metrics)
    return reports, metrics


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
    FETCH_ERRORS[ticker] = {}
    create_folders()

    try:
        company = api.equity(ticker)

    except Exception as error:
        print(
            f"[LOI] Khong tao duoc doi tuong {ticker}: {error}"
        )

        for report_name in REPORT_TYPES:
            company_data[report_name] = pd.DataFrame()
            FETCH_ERRORS[ticker][report_name] = f"Could not initialize ticker: {error}"

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
                filepath.unlink(missing_ok=True)
                FETCH_ERRORS[ticker][report_name] = "VNStock trả về bảng rỗng cho truy vấn này."
                print(
                    f"[CANH BAO] {ticker} - {report_name}: "
                    "khong co du lieu"
                )

        except Exception as error:
            print(
                f"[LOI] {ticker} - {report_name}: {error}"
            )

            company_data[report_name] = pd.DataFrame()
            FETCH_ERRORS[ticker][report_name] = str(error)

    return company_data


# ============================================================
# 7. LUU NHAT KY CHAY
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
                "error": FETCH_ERRORS.get(ticker, {}).get(report_name, ""),
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
# 8. CHUONG TRINH CHINH
# ============================================================

def main():
    create_folders()

    if Fundamental is None:
        print("[LOI] Chua co VNStock. Module nay can goi vnstock de truy cap BCTC truc tuyen.")
        print("      Cai dat bang: python -m pip install -r requirements-financial.txt")
        return

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
            FETCH_ERRORS[ticker] = {
                report_name: f"Unexpected ticker-level error: {error}"
                for report_name in REPORT_TYPES
            }

            all_data[ticker] = {
                report_name: pd.DataFrame()
                for report_name in REPORT_TYPES
            }

    # Luu chi so tong hop va log; raw statements da duoc luu thanh CSV rieng.
    metrics = build_key_metrics(all_data)
    if not metrics.empty:
        print(f"[OK] Chi so chuan hoa: {METRICS_FILE}")
    save_log(all_data)

    print("\n" + "=" * 60)
    print("DA HOAN TAT")
    print(f"So ma co phieu da nhap: {total}")
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

