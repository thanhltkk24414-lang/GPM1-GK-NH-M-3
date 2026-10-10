"""Collect and persist exchange/company profile metadata for the dashboard."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import threading
import time

import pandas as pd
import requests

try:
    from vnstock import Company
except ImportError:
    Company = None


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SYMBOLS_FILE = PROJECT_ROOT / "data" / "symbols.csv"
METADATA_FILE = PROJECT_ROOT / "data" / "processed" / "company_metadata.csv"
FAILURES_FILE = PROJECT_ROOT / "reports" / "company_metadata_errors.csv"
METADATA_COLUMNS = (
    "symbol",
    "exchange",
    "industry",
    "market_cap",
    "shares_outstanding",
    "foreign_ownership",
    "foreign_ownership_limit",
    "company_name",
    "source",
    "fetched_at",
)


def _clean_value(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _number(value):
    value = pd.to_numeric(value, errors="coerce")
    return float(value) if pd.notna(value) and value > 0 else None


def _first_column_value(frame, name):
    matches = [
        index for index, column in enumerate(frame.columns) if column == name
    ]
    for index in matches:
        value = _clean_value(frame.iloc[0, index])
        if value is not None:
            return value
    return None


def fetch_company_metadata(ticker, exchange=None, source="VCI"):
    if Company is None:
        raise ImportError(
            "Chưa có VNStock. Cài môi trường từ requirements.txt để tải hồ sơ doanh nghiệp."
        )

    ticker = str(ticker).strip().upper()
    if not ticker:
        raise ValueError("Mã cổ phiếu không được để trống.")

    source = str(source).strip().upper()
    if source not in {"VCI", "KBS"}:
        raise ValueError(f"Nguồn hồ sơ không được hỗ trợ: {source}")

    try:
        profile = Company(symbol=ticker, source=source).overview()
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Không tải được hồ sơ {ticker} từ VNStock {source}: {exc}"
        ) from exc

    if not isinstance(profile, pd.DataFrame) or profile.empty:
        raise ValueError(f"VNStock VCI không trả hồ sơ doanh nghiệp cho {ticker}.")

    actual_symbol = _clean_value(_first_column_value(profile, "symbol"))
    if actual_symbol and str(actual_symbol).strip().upper() != ticker:
        raise ValueError(
            f"Nguồn trả hồ sơ {actual_symbol} khi đang yêu cầu mã {ticker}."
        )

    sector = _clean_value(
        _first_column_value(profile, "sector")
        or _first_column_value(profile, "company_type")
    )
    market_cap = _number(_first_column_value(profile, "market_cap"))
    shares_outstanding = _number(
        _first_column_value(profile, "issue_share")
        or _first_column_value(profile, "outstanding_shares")
    )
    foreign_ownership = pd.to_numeric(
        _first_column_value(profile, "foreigner_percentage"), errors="coerce"
    )
    foreign_ownership = (
        float(foreign_ownership) if pd.notna(foreign_ownership) else None
    )
    if foreign_ownership is not None and foreign_ownership > 1:
        foreign_ownership /= 100
    foreign_ownership_limit = pd.to_numeric(
        _first_column_value(profile, "maximum_foreign_percentage"), errors="coerce"
    )
    foreign_ownership_limit = (
        float(foreign_ownership_limit)
        if pd.notna(foreign_ownership_limit)
        else None
    )
    if foreign_ownership_limit is not None and foreign_ownership_limit > 1:
        foreign_ownership_limit /= 100

    return {
        "symbol": ticker,
        "exchange": _clean_value(_first_column_value(profile, "exchange")) or exchange,
        "industry": sector,
        "market_cap": market_cap,
        "shares_outstanding": shares_outstanding,
        "foreign_ownership": foreign_ownership,
        "foreign_ownership_limit": foreign_ownership_limit,
        "company_name": (
            _clean_value(_first_column_value(profile, "organ_name"))
            or _clean_value(_first_column_value(profile, "company_name"))
        ),
        "source": f"VNStock {source} Company.overview",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def load_company_metadata(ticker):
    ticker = str(ticker).strip().upper()
    if not METADATA_FILE.is_file():
        return {}

    data = pd.read_csv(METADATA_FILE, encoding="utf-8-sig")
    if "symbol" not in data.columns:
        raise ValueError(f"{METADATA_FILE} thiếu cột symbol.")
    matches = data.loc[
        data["symbol"].astype("string").str.strip().str.upper().eq(ticker)
    ]
    if matches.empty:
        return {}

    record = matches.iloc[-1].to_dict()
    return {
        key: value
        for key, value in record.items()
        if key in METADATA_COLUMNS and _clean_value(value) is not None
    }


def load_or_fetch_company_metadata(ticker, exchange=None):
    """Use cached metadata or fetch just the requested ticker and cache it."""
    ticker = str(ticker).strip().upper()
    metadata = load_company_metadata(ticker)
    required_fields = ("industry", "shares_outstanding")
    if all(metadata.get(field) is not None for field in required_fields):
        return metadata, None

    errors = []
    for source in ("VCI", "KBS"):
        try:
            fetched = fetch_company_metadata(
                ticker,
                exchange=exchange or metadata.get("exchange"),
                source=source,
            )
        except (
            ImportError,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            requests.RequestException,
            TimeoutError,
            OSError,
        ) as exc:
            errors.append(f"{source}: {exc}")
            continue

        merged = {
            key: fetched.get(key) if fetched.get(key) is not None else metadata.get(key)
            for key in METADATA_COLUMNS
        }
        if not merged.get("exchange"):
            merged["exchange"] = exchange
        if merged.get("shares_outstanding") is not None:
            _save_metadata([merged])
            return merged, None
        errors.append(f"{source}: hồ sơ không có số cổ phiếu lưu hành.")

    error_message = " | ".join(errors)
    return metadata, (
        f"Không tải được hồ sơ doanh nghiệp cho {ticker}. {error_message}"
        if error_message
        else None
    )


def _save_metadata(records):
    if not records:
        return
    METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        pd.read_csv(METADATA_FILE, encoding="utf-8-sig")
        if METADATA_FILE.is_file()
        else pd.DataFrame(columns=METADATA_COLUMNS)
    )
    updates = pd.DataFrame(records).reindex(columns=METADATA_COLUMNS)
    combined = (
        updates
        if existing.empty
        else pd.concat([existing, updates], ignore_index=True)
    )
    combined["symbol"] = combined["symbol"].astype("string").str.strip().str.upper()
    combined = combined.drop_duplicates(subset=["symbol"], keep="last")
    temporary_path = METADATA_FILE.with_suffix(".tmp")
    combined.to_csv(temporary_path, index=False, encoding="utf-8-sig")
    os.replace(temporary_path, METADATA_FILE)


def _save_failures(failures, resolved_symbols=()):
    if not failures and not FAILURES_FILE.is_file():
        return
    FAILURES_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        pd.read_csv(FAILURES_FILE, encoding="utf-8-sig")
        if FAILURES_FILE.is_file()
        else pd.DataFrame(columns=("symbol", "error", "failed_at"))
    )
    resolved = {str(symbol).strip().upper() for symbol in resolved_symbols}
    if resolved and not existing.empty:
        existing = existing.loc[
            ~existing["symbol"].astype("string").str.strip().str.upper().isin(resolved)
        ]
    updates = pd.DataFrame(failures)
    if updates.empty:
        combined = existing
    elif existing.empty:
        combined = updates
    else:
        combined = pd.concat([existing, updates], ignore_index=True)
    combined["symbol"] = combined["symbol"].astype("string").str.strip().str.upper()
    combined = combined.drop_duplicates(subset=["symbol"], keep="last")
    temporary_path = FAILURES_FILE.with_suffix(".tmp")
    combined.to_csv(temporary_path, index=False, encoding="utf-8-sig")
    os.replace(temporary_path, FAILURES_FILE)


def collect_all_company_metadata(delay_seconds=1.1, batch_size=30):
    if delay_seconds < 1:
        raise ValueError("Khoảng nghỉ phải ít nhất 1 giây để tuân thủ giới hạn nguồn.")
    if batch_size < 1:
        raise ValueError("Kích thước lô phải lớn hơn 0.")
    if not SYMBOLS_FILE.is_file():
        raise FileNotFoundError(f"Không tìm thấy danh sách mã: {SYMBOLS_FILE}")

    symbols = pd.read_csv(SYMBOLS_FILE, encoding="utf-8-sig")
    if not {"symbol", "exchange"}.issubset(symbols.columns):
        raise ValueError("data/symbols.csv phải có các cột symbol và exchange.")
    symbols = symbols.dropna(subset=["symbol"])
    existing = (
        pd.read_csv(METADATA_FILE, encoding="utf-8-sig")
        if METADATA_FILE.is_file()
        else pd.DataFrame(columns=METADATA_COLUMNS)
    )
    complete = set()
    if {"symbol", "industry", "market_cap", "shares_outstanding"}.issubset(
        existing.columns
    ):
        valid = existing.dropna(
            subset=["symbol", "industry", "market_cap", "shares_outstanding"]
        )
        complete = (
            valid["symbol"].astype("string").str.strip().str.upper().tolist()
        )

    pending = symbols.loc[
        ~symbols["symbol"].astype("string").str.strip().str.upper().isin(complete)
    ]
    if pending.empty:
        print("[PROFILE] Hồ sơ đủ dữ liệu cho toàn bộ danh sách mã.")
        return
    results, failures = [], []
    request_lock = threading.Lock()
    next_request_at = [time.monotonic()]

    def wait_for_request_slot():
        with request_lock:
            wait = next_request_at[0] - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            next_request_at[0] = time.monotonic() + delay_seconds

    def fetch_with_rate_limit(ticker, exchange):
        for attempt in range(2):
            wait_for_request_slot()
            try:
                return fetch_company_metadata(ticker, exchange=exchange)
            except (requests.RequestException, TimeoutError, OSError) as exc:
                if attempt == 1:
                    raise
                wait_seconds = 2 ** (attempt + 1)
                print(
                    f"[PROFILE RETRY] {ticker}: {type(exc).__name__}; "
                    f"thử lại sau {wait_seconds}s.",
                    file=sys.stderr,
                )
                time.sleep(wait_seconds)
            except RuntimeError as exc:
                message = str(exc).casefold()
                transient = any(
                    marker in message
                    for marker in ("timeout", "timed out", "connection", "temporarily")
                )
                if not transient or attempt == 1:
                    raise
                wait_seconds = 2 ** (attempt + 1)
                print(
                    f"[PROFILE RETRY] {ticker}: lỗi kết nối; "
                    f"thử lại sau {wait_seconds}s.",
                    file=sys.stderr,
                )
                time.sleep(wait_seconds)
        raise RuntimeError(f"Hết lượt thử hồ sơ mã {ticker}.")

    def record_failure(ticker, exc):
        return {
            "symbol": ticker,
            "error": str(exc),
            "failed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    total = len(pending)
    jobs = [
        (
            str(row["symbol"]).strip().upper(),
            row["exchange"],
        )
        for _, row in pending.iterrows()
    ]
    completed = successful = failed = 0
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(fetch_with_rate_limit, ticker, exchange): ticker
            for ticker, exchange in jobs
        }
        for future in as_completed(futures):
            ticker = futures[future]
            completed += 1
            try:
                results.append(future.result())
                successful += 1
            except (
                ImportError,
                RuntimeError,
                ValueError,
                TypeError,
                KeyError,
                IndexError,
                requests.RequestException,
                TimeoutError,
                OSError,
            ) as exc:
                failures.append(record_failure(ticker, exc))
                failed += 1
                print(f"[PROFILE ERROR] {ticker}: {exc}", file=sys.stderr)

            if completed % batch_size == 0 or completed == total:
                _save_metadata(results)
                _save_failures(
                    failures,
                    resolved_symbols=[record["symbol"] for record in results],
                )
                print(
                    f"[PROFILE] {completed}/{total} processed; "
                    f"{successful} successful, {failed} failed."
                )
                results, failures = [], []


def main():
    parser = argparse.ArgumentParser(
        description="Cào hồ sơ doanh nghiệp cho toàn bộ mã trong data/symbols.csv."
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.1,
        help="Khoảng nghỉ giữa các request, mặc định 1.1 giây.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=30,
        help="Số mã giữa mỗi lần lưu tiến độ, mặc định 30.",
    )
    args = parser.parse_args()
    collect_all_company_metadata(args.delay, args.batch_size)


if __name__ == "__main__":
    main()
