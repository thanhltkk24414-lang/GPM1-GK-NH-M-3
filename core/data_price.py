
import time
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
from vnstock import Listing

from historical_collector import HistoricalCollector


# ==================================================
# 1. CẤU HÌNH
# ==================================================

# 20: chạy thử 20 mã
# None: chạy toàn bộ danh sách
MAX_SYMBOLS = None

END_DATE = datetime.now().strftime("%Y-%m-%d")
START_DATE = (
    datetime.now() - timedelta(days=620)
).strftime("%Y-%m-%d")

DELAY_BETWEEN_SYMBOLS = 3

# Nguồn danh sách mã có thông tin sàn
LISTING_SOURCE = "KBS"

VALID_EXCHANGES = ["HOSE", "HNX", "UPCOM"]

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ==================================================
# 2. LẤY DANH SÁCH MÃ VÀ SÀN
# ==================================================

def get_stock_listing():
    print(f"Đang lấy danh sách mã từ VNStock ({LISTING_SOURCE})...")

    listing_api = Listing(source=LISTING_SOURCE)
    listing = listing_api.symbols_by_exchange(get_all=True)

    if listing is None or listing.empty:
        raise RuntimeError("VNStock không trả về danh sách mã.")

    listing = listing.copy()
    listing.columns = [
        str(col).strip().lower() for col in listing.columns
    ]

    print("Các cột nguồn trả về:", list(listing.columns))

    # Xác định cột mã
    possible_columns = ["symbol", "ticker", "stock_code", "code"]

    symbol_column = next(
        (col for col in possible_columns if col in listing.columns),
        None,
    )

    if symbol_column is None:
        raise RuntimeError(
            f"Không tìm thấy cột mã. Các cột hiện có: "
            f"{list(listing.columns)}"
        )

    if "exchange" not in listing.columns:
        raise RuntimeError(
            "Nguồn không trả về cột exchange; không thể phân loại sàn."
        )

    # Chuẩn hóa mã cổ phiếu
    listing["symbol"] = (
        listing[symbol_column]
        .astype("string")
        .str.strip()
        .str.upper()
    )

    # Chuẩn hóa tên sàn
    listing["exchange"] = (
        listing["exchange"]
        .astype("string")
        .str.strip()
        .str.upper()
        .replace({
            "HSX": "HOSE",
            "UPCOM MARKET": "UPCOM",
        })
    )

    # Lọc mã gồm đúng 3 chữ cái
    listing = listing[
        listing["symbol"].str.fullmatch(r"[A-Z]{3}", na=False)
    ].copy()

    # Lọc các sàn mục tiêu
    listing = listing[
        listing["exchange"].isin(VALID_EXCHANGES)
    ].copy()

    # Nếu có cột type, lọc cổ phiếu khi nguồn nhận diện STOCK
    if "type" in listing.columns:
        listing["type"] = (
            listing["type"]
            .astype("string")
            .str.strip()
            .str.upper()
        )

        print("\nPhân loại type từ nguồn:")
        print(
            listing["type"]
            .value_counts(dropna=False)
            .to_string()
        )

        if "STOCK" in listing["type"].dropna().unique():
            listing = listing[
                listing["type"] == "STOCK"
            ].copy()

    listing = (
        listing.dropna(subset=["symbol", "exchange"])
        .drop_duplicates(subset=["symbol", "exchange"])
        .sort_values(["exchange", "symbol"])
        .reset_index(drop=True)
    )

    if listing.empty:
        raise RuntimeError(
            "Danh sách rỗng sau khi lọc. "
            "Hãy kiểm tra cột exchange và type."
        )

    # Thống kê số mã theo sàn
    print("\n===== DANH SÁCH MÃ THEO SÀN =====")

    counts = (
        listing.groupby("exchange")["symbol"]
        .nunique()
        .reindex(VALID_EXCHANGES, fill_value=0)
    )

    for exchange, count in counts.items():
        print(f"{exchange}: {count} mã")

    print(f"Tổng số mã duy nhất: {listing['symbol'].nunique()}")

    return listing


def get_stock_symbols(listing):
    """Tạo danh sách mã duy nhất để lấy giá lịch sử."""

    symbols = sorted(
        listing["symbol"].dropna().unique().tolist()
    )

    if MAX_SYMBOLS is not None:
        symbols = symbols[:MAX_SYMBOLS]
        print(f"\nChạy thử {len(symbols)} mã.")
    else:
        print(f"\nTổng số mã sẽ quét: {len(symbols)}")

    print("Một số mã:", symbols[:20])

    return symbols


# ==================================================
# 3. TÍNH CHỈ BÁO KỸ THUẬT
# ==================================================

def calculate_indicators(df):
    if df is None or df.empty:
        raise ValueError("Không có dữ liệu lịch sử để tính chỉ báo.")

    df = df.copy()

    if "date" not in df.columns or "close" not in df.columns:
        raise ValueError("Dữ liệu phải có cột date và close.")

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")

    df = (
        df.dropna(subset=["date", "close"])
        .sort_values("date")
        .drop_duplicates(subset=["date"], keep="last")
        .reset_index(drop=True)
    )

    if df.empty:
        raise ValueError("Không còn dữ liệu ngày và giá đóng cửa hợp lệ.")

    close = df["close"]

    # Moving Average
    df["MA20"] = close.rolling(
        window=20, min_periods=20
    ).mean()

    df["MA50"] = close.rolling(
        window=50, min_periods=50
    ).mean()

    # RSI 14
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / 14,
        min_periods=14,
        adjust=False,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14,
        min_periods=14,
        adjust=False,
    ).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))
    df["RSI14"] = 100 - 100 / (1 + rs)

    df.loc[
        (avg_loss == 0) & (avg_gain > 0), "RSI14"
    ] = 100

    df.loc[
        (avg_loss == 0) & (avg_gain == 0), "RSI14"
    ] = 50

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()

    df["MACD"] = ema12 - ema26

    df["MACD_signal"] = df["MACD"].ewm(
        span=9, adjust=False
    ).mean()

    df["MACD_hist"] = df["MACD"] - df["MACD_signal"]

    return df


# ==================================================
# 4. GỘP DỮ LIỆU VÀ XUẤT CSV
# ==================================================

def save_results(all_data, failed, listing):
    data_path = OUTPUT_DIR / "stock_data.csv"
    failed_path = OUTPUT_DIR / "failed_symbols.csv"

    if all_data:
        result = pd.concat(all_data, ignore_index=True)

        result["symbol"] = (
            result["symbol"]
            .astype(str)
            .str.strip()
            .str.upper()
        )

        # Lấy thông tin mã, tên doanh nghiệp và sàn
        metadata_columns = [
            col for col in [
                "symbol",
                "organ_name",
                "en_organ_name",
                "exchange",
                "type",
            ]
            if col in listing.columns
        ]

        metadata = listing[metadata_columns].drop_duplicates(
            subset=["symbol"]
        )

        # Tránh trùng cột khi ghép thông tin
        columns_to_drop = [
            col for col in [
                "organ_name",
                "en_organ_name",
                "exchange",
                "type",
            ]
            if col in result.columns
        ]

        result = result.drop(
            columns=columns_to_drop,
            errors="ignore",
        )

        result = result.merge(
            metadata,
            on="symbol",
            how="left",
        )

        result = result.sort_values(
            ["symbol", "date"]
        ).reset_index(drop=True)

        # Đưa các cột nhận diện lên đầu file
        first_columns = [
            col for col in [
                "symbol",
                "organ_name",
                "en_organ_name",
                "exchange",
                "type",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "MA20",
                "MA50",
                "RSI14",
                "MACD",
                "MACD_signal",
                "MACD_hist",
                "source",
            ]
            if col in result.columns
        ]

        other_columns = [
            col for col in result.columns
            if col not in first_columns
        ]

        result = result[first_columns + other_columns]

        result.to_csv(
            data_path,
            index=False,
            encoding="utf-8-sig",
        )

        print("\n===== ĐÃ LƯU DỮ LIỆU GIÁ =====")
        print(f"CSV: {data_path.resolve()}")
        print(f"Số mã có dữ liệu: {result['symbol'].nunique()}")
        print(f"Tổng số dòng: {len(result)}")

    else:
        print("\nKhông thu thập được dữ liệu giá.")
        print("Không tạo file stock_data.csv vì không có dữ liệu.")

    # Danh sách lỗi được lưu riêng, tránh trộn lỗi vào dữ liệu giá
    pd.DataFrame(
        failed,
        columns=["symbol", "error"],
    ).to_csv(
        failed_path,
        index=False,
        encoding="utf-8-sig",
    )

    print(f"\nCSV mã lỗi: {failed_path.resolve()}")
    print(f"Số mã thất bại: {len(failed)}")


# ==================================================
# 5. THU THẬP LỊCH SỬ GIÁ
# ==================================================

def main():
    listing = get_stock_listing()
    symbols = get_stock_symbols(listing)

    collector = HistoricalCollector(delay_seconds=2)

    all_data = []
    failed = []

    try:
        for i, symbol in enumerate(symbols, start=1):
            print(
                f"\n[{i}/{len(symbols)}] Đang xử lý {symbol}..."
            )

            try:
                history, source = collector.fetch_history(
                    symbol=symbol,
                    start=START_DATE,
                    end=END_DATE,
                )

                if history is None or history.empty:
                    raise ValueError("Nguồn dữ liệu trả về bảng rỗng.")

                history = history.copy()
                history["symbol"] = symbol

                history = calculate_indicators(history)
                history["source"] = source

                all_data.append(history)

                print(
                    f"Thành công: {len(history)} dòng | Nguồn: {source}"
                )

            except Exception as exc:
                error_message = f"{type(exc).__name__}: {exc}"

                print(f"Thất bại: {error_message}")

                failed.append({
                    "symbol": symbol,
                    "error": error_message,
                })

            if i < len(symbols):
                time.sleep(DELAY_BETWEEN_SYMBOLS)

    except KeyboardInterrupt:
        print(
            "\nĐã nhận Ctrl+C. "
            "Đang lưu dữ liệu đã thu thập được..."
        )

    finally:
        try:
            collector.close()
        except Exception as exc:
            print(f"Cảnh báo khi đóng collector: {exc}")

        # Lưu kết quả ngay cả khi bị ngắt giữa chừng
        save_results(all_data, failed, listing)


if __name__ == "__main__":
    main()
