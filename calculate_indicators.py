
import sqlite3
import pandas as pd
import numpy as np


# =========================================================
# CẤU HÌNH
# =========================================================

DB_PATH = "data/market_data.db"


# =========================================================
# TÍNH CHỈ BÁO KỸ THUẬT
# =========================================================

def calculate_indicators():

    conn = sqlite3.connect(DB_PATH)

    try:
        # Đọc dữ liệu lịch sử từ database hiện tại
        df = pd.read_sql_query(
            """
            SELECT symbol, date, close
            FROM historical_ohlcv
            WHERE close IS NOT NULL
            ORDER BY symbol, date
            """,
            conn
        )

        if df.empty:
            print("Không có dữ liệu lịch sử để tính.")
            return

        df["date"] = pd.to_datetime(
            df["date"], errors="coerce"
        )

        df["close"] = pd.to_numeric(
            df["close"], errors="coerce"
        )

        df = df.dropna(
            subset=["symbol", "date", "close"]
        )

        df = df.sort_values(
            ["symbol", "date"]
        )

        # -------------------------------------------------
        # TÍNH RIÊNG CHO TỪNG MÃ CỔ PHIẾU
        # -------------------------------------------------

        def calculate_one_stock(stock):

            stock = stock.copy()
            close = stock["close"]

            # MA20 và MA50
            stock["MA20"] = close.rolling(
                window=20, min_periods=20
            ).mean()

            stock["MA50"] = close.rolling(
                window=50, min_periods=50
            ).mean()

            # RSI14 - phương pháp Wilder
            delta = close.diff()

            gain = delta.clip(lower=0)
            loss = -delta.clip(upper=0)

            avg_gain = gain.ewm(
                alpha=1 / 14,
                min_periods=14,
                adjust=False
            ).mean()

            avg_loss = loss.ewm(
                alpha=1 / 14,
                min_periods=14,
                adjust=False
            ).mean()

            rs = avg_gain / avg_loss

            stock["RSI14"] = (
                100 - 100 / (1 + rs)
            )

            # Trường hợp giá chỉ tăng hoặc chỉ giảm
            stock.loc[
                (avg_loss == 0) & (avg_gain > 0),
                "RSI14"
            ] = 100

            stock.loc[
                (avg_gain == 0) & (avg_loss > 0),
                "RSI14"
            ] = 0

            # MACD: EMA12 - EMA26
            ema12 = close.ewm(
                span=12,
                adjust=False,
                min_periods=12
            ).mean()

            ema26 = close.ewm(
                span=26,
                adjust=False,
                min_periods=26
            ).mean()

            stock["MACD"] = ema12 - ema26

            # Đường tín hiệu EMA9
            stock["MACD_signal"] = stock["MACD"].ewm(
                span=9,
                adjust=False,
                min_periods=9
            ).mean()

            # Histogram
            stock["MACD_hist"] = (
                stock["MACD"] - stock["MACD_signal"]
            )

            return stock

        # Tính chỉ báo riêng biệt cho từng mã
        result = (
            df.groupby("symbol", group_keys=False)
            .apply(calculate_one_stock)
            .reset_index(drop=True)
        )

        # -------------------------------------------------
        # LƯU VÀO BẢNG MỚI
        # -------------------------------------------------

        result["date"] = result["date"].dt.strftime(
            "%Y-%m-%d"
        )

        result.to_sql(
            "technical_indicators",
            conn,
            if_exists="replace",
            index=False
        )

        print("=" * 60)
        print("ĐÃ TÍNH XONG CHỈ BÁO KỸ THUẬT")
        print("=" * 60)
        print(f"Số dòng dữ liệu: {len(result):,}")
        print(
            f"Số mã cổ phiếu: "
            f"{result['symbol'].nunique():,}"
        )
        print("Bảng kết quả: technical_indicators")
        print("\n10 dòng dữ liệu cuối:")
        print(result.tail(10).to_string(index=False))

    finally:
        conn.close()


if __name__ == "__main__":
    calculate_indicators()