"""
core/adapter.py - nối code của TV1 (data_price.py, historical_collector.py) với data contract.
Chạy thử:  py core/adapter.py FPT     (cần có mạng, chạy từ thư mục gốc của repo)

Lý do cần file này: data_price.py quét TOÀN BỘ ~1.600 mã rồi ghi CSV, còn scoring cần
dict của 1 mã duy nhất (đúng tên khóa: RSI, MA20, MA50, MACD, close...).
"""
import json
import sys
from datetime import datetime, timedelta

import pandas as pd


def _num(x):
    """Đổi NaN/None thành None, còn lại thành float."""
    if x is None or pd.isna(x):
        return None
    return round(float(x), 4)


def df_to_tech(df, symbol, source=None):
    """df: bảng đã có cột close, volume, MA20, MA50, RSI14, MACD, MACD_signal
    (kết quả của calculate_indicators trong data_price.py). Trả về dict theo data contract."""
    df = df.sort_values("date").reset_index(drop=True)
    last = df.iloc[-1]

    # Giá trong contract tính bằng NGHÌN ĐỒNG. Nếu nguồn trả về đồng (vd 98500) thì đổi lại.
    scale = 1000.0 if last["close"] > 1000 else 1.0
    price = lambda x: _num(x / scale) if _num(x) is not None else None

    close = df["close"]
    vol = df["volume"] if "volume" in df.columns else pd.Series(dtype=float)
    ma200 = close.rolling(200, min_periods=200).mean().iloc[-1]
    vol_ratio = avg_vol20 = None
    if len(vol) >= 21 and pd.notna(vol.iloc[-1]):
        avg_vol20 = vol.iloc[-21:-1].mean()          # trung bình 20 phiên trước hôm nay
        if pd.notna(avg_vol20) and avg_vol20 > 0:
            vol_ratio = vol.iloc[-1] / avg_vol20
    last_252 = df.tail(252)

    return {
        "symbol": symbol,
        "as_of": pd.Timestamp(last["date"]).strftime("%Y-%m-%d"),
        "source": source or "vnstock",
        "close": price(last["close"]),
        "RSI": _num(last.get("RSI14")),
        "MA20": price(last.get("MA20")),
        "MA50": price(last.get("MA50")),
        "MA200": price(ma200),
        "MACD": price(last.get("MACD")),
        "MACD_signal": price(last.get("MACD_signal")),
        "volume_ratio": _num(vol_ratio),
        "avg_volume_20d": _num(avg_vol20),
        "high_52w": price(last_252["close"].max()),
        "low_52w": price(last_252["close"].min()),
        "chart_path": None,   # TV1 chưa vẽ biểu đồ, TV4/TV5 sẽ dùng placeholder
    }


def analyze_technical(symbol, days=620):
    """Lấy giá 1 mã bằng code của TV1 rồi trả về dict theo data contract."""
    # import trong hàm để file này kiểm tra được mà không cần vnstock
    from data_price import calculate_indicators
    from historical_collector import HistoricalCollector

    symbol = symbol.strip().upper()
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    collector = HistoricalCollector(delay_seconds=2)
    try:
        history, source = collector.fetch_history(symbol, start, end)
        history["symbol"] = symbol
        df = calculate_indicators(history)
    finally:
        collector.close()
    return df_to_tech(df, symbol, source)


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "FPT"
    print(json.dumps(analyze_technical(sym), ensure_ascii=False, indent=2))