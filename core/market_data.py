from datetime import datetime, time, timedelta, timezone
from contextlib import closing
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import pandas as pd
import requests


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "market_data.db"
REALTIME_MAX_AGE = timedelta(minutes=2)
VIETNAM_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")


def is_vietnam_trading_session(now=None):
    now = now or datetime.now(VIETNAM_TIMEZONE)
    if now.tzinfo is None:
        now = now.replace(tzinfo=VIETNAM_TIMEZONE)
    else:
        now = now.astimezone(VIETNAM_TIMEZONE)
    return (
        now.weekday() < 5
        and (
            time(9, 0) <= now.time() <= time(11, 30)
            or time(13, 0) <= now.time() <= time(15, 0)
        )
    )


def _price_in_thousands(value):
    price = pd.to_numeric(value, errors="coerce")
    if pd.isna(price):
        return None
    return float(price / 1000 if price > 2000 else price)


def load_price_history(ticker, database_path=None):
    database_path = Path(database_path) if database_path else DEFAULT_DATABASE
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path
    if not database_path.parent.exists():
        database_path.parent.mkdir(parents=True, exist_ok=True)

    history_query = """
        SELECT
            symbol, date, open, high, low, close, volume
        FROM historical_ohlcv
        WHERE symbol = ?
        ORDER BY date
    """
    indicators_query = """
        SELECT symbol, date, MA20, MA50, RSI14, MACD, MACD_signal, MACD_hist
        FROM technical_indicators
        WHERE symbol = ?
    """
    history = pd.DataFrame()
    indicators = pd.DataFrame()
    try:
        with closing(sqlite3.connect(database_path)) as connection:
            history = pd.read_sql_query(
                history_query, connection, params=(ticker.upper(),)
            )
            indicators = pd.read_sql_query(
                indicators_query, connection, params=(ticker.upper(),)
            )
    except Exception:
        pass

    if history.empty:
        # Nếu chưa có lịch sử, tự động tải
        try:
            import sys
            import io
            # Tránh in ra màn hình quá nhiều làm phiền giao diện web
            old_stdout = sys.stdout
            sys.stdout = io.StringIO()
            
            from stock_bot.data_pipeline.update_history import HistoricalUpdater
            updater = HistoricalUpdater(db_path=str(database_path))
            target_date = updater.get_last_completed_trading_date()
            if target_date is None:
                target_date = updater.now_vietnam().date()
            updater.update_symbol(ticker, target_date)
            updater.close()
            
            try:
                import sys
                import os
                if str(PROJECT_ROOT) not in sys.path:
                    sys.path.insert(0, str(PROJECT_ROOT))
                import calculate_indicators
                calculate_indicators.calculate_indicators()
            except Exception as calc_e:
                pass
                
            sys.stdout = old_stdout
            
            # Thử load lại
            with closing(sqlite3.connect(database_path)) as connection:
                history = pd.read_sql_query(
                    history_query, connection, params=(ticker.upper(),)
                )
                try:
                    indicators = pd.read_sql_query(
                        indicators_query, connection, params=(ticker.upper(),)
                    )
                except Exception:
                    indicators = pd.DataFrame()
        except Exception as e:
            if 'old_stdout' in locals():
                sys.stdout = old_stdout
            raise ValueError(f"Không tìm thấy dữ liệu lịch sử cho mã {ticker} và tải tự động thất bại: {e}")
            
    if history.empty:
        raise ValueError(f"Không tìm thấy dữ liệu lịch sử cho mã {ticker}.")
        
    if indicators.empty:
        try:
            import sys
            import os
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            import calculate_indicators
            calculate_indicators.calculate_indicators()
            with closing(sqlite3.connect(database_path)) as connection:
                indicators = pd.read_sql_query(
                    indicators_query, connection, params=(ticker.upper(),)
                )
        except Exception as e:
            pass

    if not indicators.empty:
        history = history.merge(
            indicators, on=["symbol", "date"], how="left", validate="one_to_one"
        )
    else:
        for column in ("MA20", "MA50", "RSI14", "MACD", "MACD_signal", "MACD_hist"):
            history[column] = pd.NA

    history["date"] = pd.to_datetime(history["date"], errors="coerce")
    numeric_columns = [
        "open", "high", "low", "close", "volume",
        "MA20", "MA50", "RSI14", "MACD", "MACD_signal", "MACD_hist",
    ]
    for column in numeric_columns:
        history[column] = pd.to_numeric(history[column], errors="coerce")
    history = history.dropna(subset=["date", "close"]).sort_values("date")
    if history.empty:
        raise ValueError(f"Mã {ticker} không có dữ liệu giá hợp lệ trong database.")
    return history.reset_index(drop=True)


def load_symbol_metadata(ticker, listing_path=None):
    """Load exchange information from the project's symbol listing CSV."""
    if listing_path is None:
        candidates = (
            PROJECT_ROOT / "data" / "symbols.csv",
            PROJECT_ROOT / "data" / "symbol.csv",
            PROJECT_ROOT / "symbols.csv",
            PROJECT_ROOT / "symbol.csv",
        )
        listing_path = next(
            (path for path in candidates if path.is_file()), None
        )
    else:
        listing_path = Path(listing_path)

    if listing_path is None or not listing_path.is_file():
        return {}

    symbols = pd.read_csv(listing_path, encoding="utf-8-sig")
    columns = {
        str(column).strip().casefold(): column
        for column in symbols.columns
    }
    missing_columns = {"symbol", "exchange"}.difference(columns)
    if missing_columns:
        raise ValueError(
            f"Danh sách mã {listing_path} thiếu cột: "
            f"{', '.join(sorted(missing_columns))}."
        )

    symbol_column = columns["symbol"]
    exchange_column = columns["exchange"]
    matches = symbols.loc[
        symbols[symbol_column].astype("string").str.strip().str.upper().eq(
            str(ticker).strip().upper()
        )
    ]
    if matches.empty:
        return {}

    exchange = matches.iloc[0][exchange_column]
    try:
        source = str(listing_path.relative_to(PROJECT_ROOT))
    except ValueError:
        source = str(listing_path)
    return {
        "exchange": str(exchange).strip()
        if pd.notna(exchange) and str(exchange).strip()
        else None,
        "source": source,
    }


def _parse_timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _cached_quote(ticker, database_path):
    try:
        connection = sqlite3.connect(database_path, timeout=1)
    except sqlite3.Error:
        return None
    with closing(connection):
        has_latest_table = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'market_quote_latest'
            """
        ).fetchone()
        row = None
        if has_latest_table:
            row = connection.execute(
                """
                SELECT price, volume, bid, ask, data_type, timestamp
                FROM market_quote_latest
                WHERE symbol = ? AND timestamp IS NOT NULL
                LIMIT 1
                """,
                (ticker.upper(),),
            ).fetchone()
        if row is None:
            row = connection.execute(
                """
                SELECT price, volume, bid, ask, data_type, timestamp
                FROM market_data
                WHERE symbol = ? AND timestamp IS NOT NULL
                ORDER BY timestamp DESC, id DESC
                LIMIT 1
                """,
                (ticker.upper(),),
            ).fetchone()
    if row is None:
        return None

    price = _price_in_thousands(row[0])
    timestamp = _parse_timestamp(row[5])
    now = datetime.now(timezone.utc)
    if (
        price is None
        or price <= 0
        or timestamp is None
        or now - timestamp > REALTIME_MAX_AGE
        or timestamp - now > timedelta(minutes=1)
    ):
        return None

    return {
        "price": price,
        "volume": row[1],
        "bid": row[2],
        "ask": row[3],
        "data_type": row[4] or "market_data",
        "timestamp": timestamp.isoformat(timespec="seconds"),
        "source": {
            "match_price": "Vietcap WebSocket",
            "dnse_latest_trade": "DNSE realtime",
            "snapshot": "Vietcap priceboard snapshot",
        }.get(row[4], "market_data.db"),
    }


def _fetch_vietcap_quote(ticker):
    from stock_bot.data_pipeline.collectors.vietcap_snapshot import (
        VietcapSnapshotCollector,
    )

    collector = VietcapSnapshotCollector(timeout=10)
    successful_exchanges = 0
    failures = []
    for exchange in collector.EXCHANGES:
        try:
            records = collector.get_exchange(exchange)
        except (requests.RequestException, ValueError) as exc:
            failures.append(f"{exchange}: {type(exc).__name__}")
            continue

        successful_exchanges += 1
        match = next(
            (
                item
                for item in records
                if str(item.get("s", "")).strip().upper() == ticker
            ),
            None,
        )
        if match is None:
            continue

        normalized = collector.normalize(match)
        if normalized is None:
            continue
        price = _price_in_thousands(normalized.get("price"))
        if price is None or price <= 0:
            raise ValueError(f"Vietcap trả giá không hợp lệ cho mã {ticker}.")

        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return {
            "price": price,
            "volume": pd.to_numeric(normalized.get("volume"), errors="coerce"),
            "bid": pd.to_numeric(normalized.get("bid_price"), errors="coerce"),
            "ask": pd.to_numeric(normalized.get("ask_price"), errors="coerce"),
            "data_type": "snapshot",
            "timestamp": fetched_at,
            "source": "Vietcap priceboard snapshot",
            "company_name": normalized.get("company_name"),
            "exchange": normalized.get("exchange") or exchange,
        }

    if successful_exchanges == 0:
        details = "; ".join(failures)
        raise RuntimeError(f"Không truy vấn được báo giá Vietcap. {details}")
    if failures:
        details = "; ".join(failures)
        raise RuntimeError(
            f"Không tìm thấy báo giá {ticker}; một số sàn không truy vấn được: {details}"
        )
    return None


def load_realtime_quote(ticker, database_path=None):
    database_path = Path(database_path) if database_path else DEFAULT_DATABASE
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path

    if not is_vietnam_trading_session():
        return None

    quote = _cached_quote(ticker, database_path)
    if quote is not None:
        return quote
    return _fetch_vietcap_quote(ticker.upper())
