"""Lấy tin tức gần đây về một mã cổ phiếu Việt Nam.

Data contract tương thích với ``core.scoring.score_news``::

    {
        "symbol": "FPT",
        "headlines": [{"title": "...", "url": "...", ...}],
        "status": "ok" | "empty" | "error",
        "source": "DuckDuckGo News",
        "fetched_at": "2026-10-09T12:00:00+00:00",
        "error": None,
    }

Kết quả tin tức là dữ liệu tìm kiếm, không phải nguồn xác nhận sự kiện.
Dashboard nên hiện tiêu đề, nguồn, ngày và liên kết để người dùng mở bài gốc.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

try:
    from ddgs import DDGS
except ImportError:  # Cho phép import ứng dụng khi máy chưa cài requirements.
    DDGS = None


_TICKER_RE = re.compile(r"^[A-Z0-9]{1,10}$")
_ALLOWED_PERIODS = {"d", "w", "m", "y"}
_PERIOD_ORDER = ("d", "w", "m", "y")
_PERIOD_DAYS = {"d": 1, "w": 7, "m": 31, "y": 366}
_NON_ARTICLE_RE = re.compile(
    r"\b(technical|chart|stock quote|price history|technical analysis)\b|"
    r"bảng giá|biểu đồ|tín hiệu (mua|bán)|mua mạnh|bán mạnh|"
    r"chỉ báo kỹ thuật|giá cổ phiếu trực tuyến",
    re.IGNORECASE,
)
_COMPANY_ALIASES = {
    "HPG": ("Hòa Phát", "Tập đoàn Hòa Phát"),
    "FPT": ("Tập đoàn FPT",),
    "VNM": ("Vinamilk", "Sữa Việt Nam"),
    "VIC": ("Vingroup",),
    "VHM": ("Vinhomes",),
    "VRE": ("Vincom Retail",),
    "MWG": ("Thế Giới Di Động", "Mobile World"),
    "TCB": ("Techcombank", "Ngân hàng Techcombank"),
    "MBB": ("MBBank", "MB Bank", "Ngân hàng Quân đội"),
    "ACB": ("Ngân hàng Á Châu", "Asia Commercial Bank"),
    "VCB": ("Vietcombank", "Ngân hàng Ngoại thương"),
    "BID": ("BIDV", "Ngân hàng Đầu tư và Phát triển Việt Nam"),
    "CTG": ("VietinBank", "Ngân hàng Công thương"),
    "VPB": ("VPBank",),
    "STB": ("Sacombank",),
    "HDB": ("HDBank",),
    "TPB": ("TPBank",),
    "GAS": ("PV GAS", "Tổng công ty Khí Việt Nam"),
    "PLX": ("Petrolimex",),
    "DGC": ("Hóa chất Đức Giang",),
}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _fold_text(value: str) -> str:
    """Chuẩn hóa dấu và dấu câu để so khớp tên DN trong tin tiếng Việt."""
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _relevance_for(item: dict[str, str | None], symbol: str) -> str | None:
    title = item.get("title") or ""
    url = item.get("url") or ""
    if _NON_ARTICLE_RE.search(title) or _NON_ARTICLE_RE.search(url):
        return None
    if re.search(r"/tags?(?:/|-|\?)|tag\d+|category", url, re.IGNORECASE):
        return None
    url_leaf = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1].casefold()
    company_page_slugs = {
        _fold_text(alias).replace(" ", "-") + ".html"
        for alias in _COMPANY_ALIASES.get(symbol, ())
    }
    if url_leaf in company_page_slugs:
        return None
    if len(_fold_text(title)) < 15 or re.fullmatch(r"\d+ lien quan", _fold_text(title)):
        return None

    # Phân biệt bài về đúng mã với bài liên quan tới cùng tập đoàn/nhóm ngành.
    summary_raw = item.get("summary") or ""
    explicit_tickers = re.findall(
        r"\(([A-Z]{3})\)|[Mm]ã(?:\s+chứng khoán)?\s*[:：]?\s*([A-Z]{3})(?![A-Za-z0-9])",
        f"{title} {summary_raw}",
    )
    other_tickers = {code.upper() for pair in explicit_tickers for code in pair if code}
    title_url = _fold_text(f"{title} {url}")
    summary = _fold_text(summary_raw)

    def contains_phrase(text: str, phrase: str) -> bool:
        normalized = _fold_text(phrase)
        return bool(normalized and f" {normalized} " in f" {text} ")

    # Ưu tiên mã trong tiêu đề/link; tên doanh nghiệp trong tiêu đề cũng là
    # tin trực tiếp, trừ trường hợp tiêu đề xác định rõ mã khác trong tập đoàn.
    if contains_phrase(title_url, symbol):
        if other_tickers and symbol not in other_tickers:
            return "related"
        return "direct"

    other_company_mentioned = any(
        alias_symbol != symbol
        and any(contains_phrase(title_url, alias) for alias in aliases)
        for alias_symbol, aliases in _COMPANY_ALIASES.items()
    )
    for alias in _COMPANY_ALIASES.get(symbol, ()):
        if contains_phrase(title_url, alias):
            if (
                (other_tickers and symbol not in other_tickers)
                or other_company_mentioned
            ):
                return "related"
            return "direct"

    # Nới lọc: bài có nhắc mã hoặc tên doanh nghiệp trong phần tóm tắt được
    # giữ lại ở mức liên quan, kể cả khi đó là bài thị trường rộng hơn.
    if contains_phrase(summary, symbol):
        return "related"
    for alias in _COMPANY_ALIASES.get(symbol, ()):
        if contains_phrase(summary, alias):
            return "related"
    return None


def _normalize_result(item: dict[str, Any]) -> dict[str, str | None] | None:
    """Chuẩn hóa schema của DDGS về schema ổn định cho dashboard/scoring."""
    title = _clean(item.get("title"))
    url = _clean(item.get("url") or item.get("href"))
    if not title or not url:
        return None

    return {
        "title": title,
        "url": url,
        "published_at": _clean(item.get("date") or item.get("published")),
        "source": _clean(item.get("source") or item.get("publisher")),
        "summary": _clean(item.get("body") or item.get("description")),
    }


def _published_timestamp(item: dict[str, str | None]) -> float:
    published_at = item.get("published_at")
    if not published_at:
        return 0.0
    try:
        parsed = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _is_within_period(item: dict[str, str | None], period: str, now: datetime) -> bool:
    """Loại bài có ngày rõ ràng nằm ngoài khoảng, phòng khi search trả sai kỳ."""
    content = " ".join((item.get("title") or "", item.get("summary") or ""))
    content_years = [int(year) for year in re.findall(r"\b(?:19|20)\d{2}\b", content)]
    # DDGS đôi khi gắn ngày mới cho trang cũ. Nếu toàn bộ năm được nêu trong
    # tiêu đề/tóm tắt đã cũ hơn năm trước, không coi đó là tin cập nhật.
    if content_years and max(content_years) < now.year - 1:
        return False

    timestamp = _published_timestamp(item)
    if timestamp == 0.0:
        # Một số nguồn không cung cấp ngày parse được; khi đó dùng bộ lọc kỳ
        # của DDGS thay vì bỏ mất tin hoàn toàn.
        return True
    published_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return now - timedelta(days=_PERIOD_DAYS[period]) <= published_at <= now + timedelta(days=1)


def fetch_stock_news(
    ticker: str,
    limit: int = 8,
    period: str = "d",
    timeout: int = 10,
) -> dict[str, Any]:
    """Tìm tin mới cho mã cổ phiếu.

    Args:
        ticker: Mã cổ phiếu, ví dụ ``FPT`` hoặc ``VNM``.
        limit: Số kết quả tối đa (1–20).
        period: Khoảng thời gian tìm kiếm: ``d`` (ngày), ``w`` (tuần),
            ``m`` (tháng) hoặc ``y`` (năm). Mặc định thử gần nhất trước,
            rồi tự mở rộng lần lượt sang tuần, tháng và năm.
        timeout: Timeout tìm kiếm, tính bằng giây.

    Trả về dict có ``headlines`` luôn là list. Khi không cài ``ddgs`` hoặc
    nguồn tìm kiếm lỗi, status/error được trả về thay vì làm sập dashboard.
    """
    symbol = str(ticker or "").strip().upper()
    result: dict[str, Any] = {
        "symbol": symbol,
        "headlines": [],
        "status": "empty",
        "source": "DuckDuckGo News",
        "period_requested": period,
        "period_used": None,
        "periods_checked": [],
        "fallback_used": False,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "error": None,
    }

    if not _TICKER_RE.fullmatch(symbol):
        result.update(status="error", error="Mã cổ phiếu không hợp lệ.")
        return result
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 20:
        result.update(status="error", error="limit phải là số nguyên từ 1 đến 20.")
        return result
    if period not in _ALLOWED_PERIODS:
        result.update(status="error", error="period chỉ nhận d, w, m hoặc y.")
        return result
    if DDGS is None:
        result.update(
            status="error",
            error="Thiếu thư viện ddgs. Cài bằng: pip install -r requirements.txt",
        )
        return result

    # Thử tên doanh nghiệp trước cho các mã có bí danh phổ biến (ví dụ HPG
    # thường được báo chí viết là Hòa Phát), sau đó tìm trực tiếp theo mã.
    aliases = _COMPANY_ALIASES.get(symbol, ())
    queries = [f'"{aliases[0]}" cổ phiếu'] if aliases else []
    queries.append(f'"{symbol}" cổ phiếu')
    requested_index = _PERIOD_ORDER.index(period)
    periods_to_try = _PERIOD_ORDER[requested_index:]
    now = datetime.now(timezone.utc)
    try:
        seen: set[str] = set()
        headlines: list[dict[str, str | None]] = []
        last_search_error = None
        successful_searches = 0
        for current_period in periods_to_try:
            result["periods_checked"].append(current_period)
            period_headlines: list[dict[str, str | None]] = []
            for query in queries:
                try:
                    with DDGS(timeout=timeout) as search:
                        raw_items = search.news(
                            query,
                            region="vn-vi",
                            safesearch="off",
                            timelimit=current_period,
                            max_results=min(limit * 3, 25),
                        )
                    successful_searches += 1
                except Exception as exc:
                    last_search_error = exc
                    continue

                for item in raw_items or []:
                    normalized = _normalize_result(item)
                    if normalized is None or not _is_within_period(normalized, current_period, now):
                        continue
                    relevance = _relevance_for(normalized, symbol)
                    if relevance is None:
                        continue
                    normalized["relevance"] = relevance
                    key = normalized["url"] or normalized["title"]
                    if key in seen:
                        continue
                    seen.add(key)
                    period_headlines.append(normalized)

            if period_headlines:
                headlines = period_headlines
                result["period_used"] = current_period
                break

        if not headlines and successful_searches == 0 and last_search_error is not None:
            raise last_search_error

        # Trong cùng khoảng thời gian, xếp tin trực tiếp trước tin chỉ liên quan;
        # mỗi nhóm đều xếp ngày mới nhất trước.
        headlines.sort(
            key=lambda item: (
                0 if item.get("relevance") == "direct" else 1,
                -_published_timestamp(item),
            )
        )
        result["headlines"] = headlines[:limit]
        result["status"] = "ok" if headlines else "empty"
        result["fallback_used"] = any(
            checked_period != period for checked_period in result["periods_checked"]
        )
    except Exception as exc:
        # Ghi loại lỗi ngắn gọn để UI/log không bộc lộ quá nhiều chi tiết kết nối.
        result.update(
            status="error",
            error=f"{type(exc).__name__}: không truy vấn được nguồn tin.",
            fallback_used=any(
                checked_period != period for checked_period in result["periods_checked"]
            ),
        )

    return result


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Lấy tin tức mới theo mã cổ phiếu.")
    parser.add_argument("ticker", help="Mã cổ phiếu, ví dụ FPT")
    parser.add_argument("--limit", type=int, default=8, help="Số tin tối đa (mặc định 8)")
    parser.add_argument(
        "--period", choices=sorted(_ALLOWED_PERIODS), default="d",
        help="Bắt đầu tìm từ d/ngày (mặc định), w/tuần, m/tháng hoặc y/năm; tự mở rộng nếu thiếu tin",
    )
    args = parser.parse_args()
    import json

    print(json.dumps(fetch_stock_news(args.ticker, args.limit, args.period), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
