
import requests
import pandas as pd

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


class DNSECollector:
    """Thu thập dữ liệu lịch sử OHLCV từ DNSE."""

    BASE_URL = "https://api.dnse.com.vn/chart-api/v2/ohlcs/stock"

    def __init__(self, timeout=30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        })

    @staticmethod
    def _to_timestamp(date_value, end_of_day=False):
        """Chuyển ngày thành Unix timestamp theo giờ Việt Nam."""
        tz = ZoneInfo("Asia/Ho_Chi_Minh")

        if isinstance(date_value, datetime):
            dt = date_value
        else:
            dt = datetime.strptime(str(date_value)[:10], "%Y-%m-%d")

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        else:
            dt = dt.astimezone(tz)

        if end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59)

        return int(dt.timestamp())

    def get_history(
        self,
        symbol,
        start=None,
        end=None,
        interval="1D",
    ):
        """
        Trả về DataFrame gồm:
        symbol, date, open, high, low, close, volume.
        """
        symbol = str(symbol).strip().upper()

        if not start:
            start = (
                datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
                - timedelta(days=620)
            ).strftime("%Y-%m-%d")

        if not end:
            end = datetime.now(
                ZoneInfo("Asia/Ho_Chi_Minh")
            ).strftime("%Y-%m-%d")

        params = {
            "from": self._to_timestamp(start),
            "to": self._to_timestamp(end, end_of_day=True),
            "symbol": symbol,
            "resolution": interval,
        }

        response = self.session.get(
            self.BASE_URL,
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()

        # Một số API trả dữ liệu theo các mảng t, o, h, l, c, v.
        if not isinstance(payload, dict):
            raise ValueError(
                f"DNSE trả về dữ liệu không hợp lệ cho {symbol}"
            )

        required = ["t", "o", "h", "l", "c", "v"]
        missing = [key for key in required if key not in payload]

        if missing:
            raise ValueError(
                f"DNSE thiếu trường {missing} cho {symbol}. "
                f"Phản hồi: {str(payload)[:300]}"
            )

        lengths = [len(payload[key]) for key in required]

        if not lengths or min(lengths) == 0:
            raise ValueError(f"DNSE không có dữ liệu cho {symbol}")

        if len(set(lengths)) != 1:
            raise ValueError(
                f"Các mảng dữ liệu DNSE không cùng độ dài: {symbol}"
            )

        df = pd.DataFrame({
            "date": pd.to_datetime(
                payload["t"], unit="s", utc=True
            ).tz_convert("Asia/Ho_Chi_Minh").tz_localize(None),
            "open": payload["o"],
            "high": payload["h"],
            "low": payload["l"],
            "close": payload["c"],
            "volume": payload["v"],
        })

        df["symbol"] = symbol

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(
            subset=["date", "open", "high", "low", "close"]
        )

        df = df[
            (df["date"] >= pd.Timestamp(start))
            & (df["date"] < pd.Timestamp(end) + pd.Timedelta(days=1))
        ]

        df = df[
            ["symbol", "date", "open", "high", "low", "close", "volume"]
        ]

        df = (
            df.drop_duplicates(subset=["symbol", "date"])
            .sort_values("date")
            .reset_index(drop=True)
        )

        if df.empty:
            raise ValueError(f"DNSE không có dữ liệu hợp lệ cho {symbol}")

        return df

    def close(self):
        self.session.close()
