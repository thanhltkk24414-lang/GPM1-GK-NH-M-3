
import time
import pandas as pd

from vnstock import Quote
from dnse_collector import DNSECollector


class HistoricalCollector:
    """Lấy lịch sử giá KBS trước, DNSE sau."""

    def __init__(self, delay_seconds=2):
        self.delay_seconds = delay_seconds
        self.dnse = DNSECollector()

    @staticmethod
    def _normalize_history(data, symbol):
        """Chuẩn hóa tên cột dữ liệu lịch sử."""
        if data is None or not isinstance(data, pd.DataFrame):
            raise ValueError("Nguồn dữ liệu không trả về DataFrame")

        if data.empty:
            raise ValueError("Nguồn dữ liệu trả về DataFrame rỗng")

        df = data.copy()
        df.columns = [
            str(col).strip().lower() for col in df.columns
        ]

        # Chuẩn hóa các tên cột thường gặp.
        aliases = {
            "time": "date",
            "trading_date": "date",
            "tradingdate": "date",
            "vol": "volume",
            "ticker": "symbol",
        }

        df = df.rename(columns=aliases)

        required = ["open", "high", "low", "close"]

        if "date" not in df.columns:
            if isinstance(df.index, pd.DatetimeIndex):
                df["date"] = df.index
            else:
                raise ValueError(
                    "Không tìm thấy cột ngày giao dịch"
                )

        missing = [
            col for col in required if col not in df.columns
        ]

        if missing:
            raise ValueError(
                f"Thiếu cột dữ liệu bắt buộc: {missing}"
            )

        if "volume" not in df.columns:
            df["volume"] = pd.NA

        df["date"] = pd.to_datetime(
            df["date"], errors="coerce", utc=True
        ).dt.tz_convert("Asia/Ho_Chi_Minh").dt.tz_localize(None)

        for col in required + ["volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df["symbol"] = symbol.upper()

        df = df.dropna(
            subset=["date", "open", "high", "low", "close"]
        )

        df = df[
            ["symbol", "date", "open", "high", "low", "close", "volume"]
        ]

        df = (
            df.drop_duplicates(subset=["symbol", "date"])
            .sort_values("date")
            .reset_index(drop=True)
        )

        if df.empty:
            raise ValueError("Không có dữ liệu hợp lệ sau chuẩn hóa")

        return df

    def _fetch_kbs(self, symbol, start, end):
        quote = Quote(symbol=symbol, source="KBS")

        data = quote.history(
            start=start,
            end=end,
            interval="1D",
        )

        df = self._normalize_history(data, symbol)

        # Đảm bảo dữ liệu chỉ nằm trong khoảng yêu cầu.
        df = df[
            (df["date"] >= pd.Timestamp(start))
            & (df["date"] < pd.Timestamp(end) + pd.Timedelta(days=1))
        ]

        if df.empty:
            raise ValueError("KBS không có dữ liệu trong khoảng ngày yêu cầu")

        return df

    def fetch_history(self, symbol, start, end):
        """Thử KBS, sau đó chuyển sang DNSE nếu KBS thất bại."""
        symbol = str(symbol).strip().upper()
        errors = []

        try:
            df = self._fetch_kbs(symbol, start, end)
            return df, "KBS"
        except Exception as exc:
            errors.append(f"KBS: {exc}")

        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)

        try:
            data = self.dnse.get_history(
                symbol=symbol,
                start=start,
                end=end,
                interval="1D",
            )

            df = self._normalize_history(data, symbol)
            return df, "DNSE"

        except Exception as exc:
            errors.append(f"DNSE: {exc}")

        raise RuntimeError(" | ".join(errors))

    def close(self):
        self.dnse.close()
