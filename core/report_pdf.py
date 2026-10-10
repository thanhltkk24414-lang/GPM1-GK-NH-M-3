# core/report_pdf.py
import argparse
import base64
import html
import json
from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import jinja2
import pandas as pd

if __package__:
    from .financial_alignment import normalize_statement_year_order
else:
    from financial_alignment import normalize_statement_year_order

if __package__:
    from .market_data import (
        is_vietnam_trading_session,
        load_price_history,
        load_realtime_quote,
        load_symbol_metadata,
    )
    from .company_metadata import (
        load_company_metadata,
        load_or_fetch_company_metadata,
    )
    from .news import fetch_stock_news
    from .scoring import get_recommendation, to_report_context
else:
    from market_data import (
        is_vietnam_trading_session,
        load_price_history,
        load_realtime_quote,
        load_symbol_metadata,
    )
    from company_metadata import load_company_metadata, load_or_fetch_company_metadata
    from news import fetch_stock_news
    from scoring import get_recommendation, to_report_context


def _format_price(value):
    if pd.isna(value):
        return "—"
    return f"{value * 1000:,.0f} đ"


def _format_indicator(value, suffix=""):
    if pd.isna(value):
        return None
    return f"{value:,.2f}{suffix}"


def _score_value(value):
    value = pd.to_numeric(value, errors="coerce")
    return float(value) if pd.notna(value) else None


def _industry_in_vietnamese(industry):
    if not industry:
        return industry
    translations = {
        "automobiles & parts": "Ô tô và phụ tùng",
        "banks": "Ngân hàng",
        "basic resources": "Tài nguyên cơ bản",
        "chemicals": "Hóa chất",
        "construction & materials": "Xây dựng và vật liệu",
        "financial services": "Dịch vụ tài chính",
        "food & beverage": "Thực phẩm và đồ uống",
        "health care": "Chăm sóc sức khỏe",
        "industrial goods & services": "Hàng hóa và dịch vụ công nghiệp",
        "insurance": "Bảo hiểm",
        "media": "Truyền thông",
        "oil & gas": "Dầu khí",
        "personal & household goods": "Hàng hóa cá nhân và gia dụng",
        "real estate": "Bất động sản",
        "retail": "Bán lẻ",
        "technology": "Công nghệ",
        "travel & leisure": "Du lịch và giải trí",
        "utilities": "Tiện ích",
    }
    return translations.get(str(industry).strip().casefold(), industry)


def _apply_company_metadata(report_data, metadata):
    report = dict(report_data)
    if not metadata:
        return report

    for key in ("exchange", "industry", "company_name"):
        if metadata.get(key):
            report[key] = (
                _industry_in_vietnamese(metadata[key])
                if key == "industry"
                else metadata[key]
            )
    if metadata.get("market_cap") is not None:
        report["market_cap"] = (
            f"{float(metadata['market_cap']) / 1_000_000_000:,.2f} tỷ đồng"
        )
    if metadata.get("shares_outstanding") is not None:
        report["shares_outstanding"] = (
            f"{float(metadata['shares_outstanding']) / 1_000_000:,.2f} triệu CP"
        )
    if metadata.get("foreign_ownership") is not None:
        report["foreign_ownership"] = f"{float(metadata['foreign_ownership']):.2%}"
    if metadata.get("foreign_ownership_limit") is not None:
        report["foreign_ownership_limit"] = (
            f"{float(metadata['foreign_ownership_limit']):.2%}"
        )

    report["metadata_missing"] = [
        label
        for key, label in (
            ("industry", "ngành"),
            ("market_cap", "vốn hóa"),
            ("shares_outstanding", "số cổ phiếu lưu hành"),
        )
        if metadata.get(key) is None or pd.isna(metadata.get(key))
    ]
    report["company_metadata_source"] = metadata.get("source")
    report["company_metadata_as_of"] = metadata.get("fetched_at")
    sources = [
        item.strip()
        for item in str(report.get("data_sources", "")).split(";")
        if item.strip()
    ]
    if metadata.get("source") and metadata["source"] not in sources:
        sources.append(metadata["source"])
    report["data_sources"] = "; ".join(sources)
    return report


def _price_chart_data_uri(history, ticker, current_price=None):
    required_columns = {"date", "open", "high", "low", "close", "volume"}
    if not required_columns.issubset(history.columns):
        return ""

    chart_data = history.copy()
    for column in ("open", "high", "low", "close", "volume"):
        chart_data[column] = pd.to_numeric(chart_data[column], errors="coerce")
    chart_data["date"] = pd.to_datetime(chart_data["date"], errors="coerce")
    chart_data = (
        chart_data.dropna(subset=["date", "open", "high", "low", "close"])
        .sort_values("date")
        .drop_duplicates(subset=["date"], keep="last")
    )
    if len(chart_data) < 2:
        return ""

    close = chart_data["close"]
    chart_data["ema20"] = close.ewm(span=20, adjust=False).mean()
    chart_data["ema50"] = close.ewm(span=50, adjust=False).mean()
    middle_band = close.rolling(window=20, min_periods=20).mean()
    band_width = close.rolling(window=20, min_periods=20).std() * 2
    chart_data["bb_upper"] = middle_band + band_width
    chart_data["bb_lower"] = middle_band - band_width

    if "RSI14" in chart_data.columns:
        chart_data["rsi14"] = pd.to_numeric(
            chart_data["RSI14"], errors="coerce"
        )
    else:
        change = close.diff()
        average_gain = change.clip(lower=0).ewm(
            alpha=1 / 14, min_periods=14, adjust=False
        ).mean()
        average_loss = -change.clip(upper=0).ewm(
            alpha=1 / 14, min_periods=14, adjust=False
        ).mean()
        relative_strength = average_gain / average_loss.replace(0, float("nan"))
        chart_data["rsi14"] = 100 - 100 / (1 + relative_strength)
        chart_data.loc[
            (average_loss == 0) & (average_gain > 0), "rsi14"
        ] = 100
        chart_data.loc[
            (average_loss == 0) & (average_gain == 0), "rsi14"
        ] = 50

    chart_data = chart_data.tail(60).reset_index(drop=True)
    width, height = 900, 570
    left, right = 70, 884
    plot_width = right - left
    price_top, price_bottom = 65, 270
    rsi_top, rsi_bottom = 287, 395
    volume_top, volume_bottom = 412, 515
    x_step = plot_width / max(len(chart_data) - 1, 1)
    volume_max = chart_data["volume"].max()
    if pd.isna(volume_max) or volume_max <= 0:
        volume_max = 1
    label_count = min(6, len(chart_data))
    date_indices = [
        round(label_index * (len(chart_data) - 1) / max(label_count - 1, 1))
        for label_index in range(label_count)
    ]

    lower_candidates = [chart_data["low"].min(), chart_data["bb_lower"].min()]
    upper_candidates = [chart_data["high"].max(), chart_data["bb_upper"].max()]
    price_low = min(value for value in lower_candidates if pd.notna(value))
    price_high = max(value for value in upper_candidates if pd.notna(value))
    price_padding = max((price_high - price_low) * 0.08, abs(price_high) * 0.005)
    price_low -= price_padding
    price_high += price_padding
    if price_high == price_low:
        price_high += 1

    def x_position(index):
        return left + index * x_step

    def price_y(value):
        return price_bottom - (
            (value - price_low) / (price_high - price_low)
        ) * (price_bottom - price_top)

    def rsi_y(value):
        return rsi_bottom - (value / 100) * (rsi_bottom - rsi_top)

    def line_path(column, y_position):
        points = []
        for index, value in enumerate(chart_data[column]):
            if pd.notna(value):
                command = "M" if not points else "L"
                points.append(
                    f"{command}{x_position(index):.1f},{y_position(value):.1f}"
                )
        return " ".join(points)

    latest = chart_data.iloc[-1]
    latest_rsi = latest["rsi14"]
    displayed_price = latest["close"] if current_price is None else current_price
    price_label = f"{displayed_price * 1000:,.0f} đ"
    price_label_name = "Giá mới nhất" if current_price is None else "Giá realtime"
    rsi_label = f"{latest_rsi:.1f}" if pd.notna(latest_rsi) else "N/A"
    ticker_label = html.escape(str(ticker))
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{width / 2}" y="18" text-anchor="middle" '
        'font-family="Arial,sans-serif" font-size="16" font-weight="bold" '
        f'fill="#183247">Biểu đồ phân tích mã {ticker_label}</text>',
        f'<text x="{width / 2}" y="36" text-anchor="middle" '
        'font-family="Arial,sans-serif" font-size="11" fill="#42596b">'
        f'EMA20 xanh | EMA50 cam | Bollinger Bands nét đứt | '
        f'{price_label_name}: {price_label} | RSI14: {rsi_label}</text>',
    ]

    for panel_top, panel_bottom in (
        (price_top, price_bottom),
        (rsi_top, rsi_bottom),
        (volume_top, volume_bottom),
    ):
        elements.append(
            f'<rect x="{left}" y="{panel_top}" width="{plot_width}" '
            f'height="{panel_bottom - panel_top}" fill="none" '
            'stroke="#333333" stroke-width="1"/>'
        )

    price_ticks = 4
    for tick in range(price_ticks + 1):
        value = price_low + (price_high - price_low) * tick / price_ticks
        y = price_y(value)
        elements.extend([
            f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" '
            'stroke="#c8cdd1" stroke-dasharray="4 3"/>',
            f'<text x="{left - 7}" y="{y + 3:.1f}" text-anchor="end" '
            'font-family="Arial,sans-serif" font-size="10" fill="#35434d">'
            f'{round(value) * 1000:,}</text>',
        ])

    for index in date_indices:
        x = x_position(index)
        for panel_top, panel_bottom in (
            (price_top, price_bottom),
            (rsi_top, rsi_bottom),
            (volume_top, volume_bottom),
        ):
            elements.append(
                f'<line x1="{x:.1f}" y1="{panel_top}" x2="{x:.1f}" '
                f'y2="{panel_bottom}" stroke="#d4d8dc" '
                'stroke-dasharray="4 3"/>'
            )

    for tick in (30, 50, 70):
        y = rsi_y(tick)
        color = "#3d8050" if tick == 30 else "#b8544d" if tick == 70 else "#c8cdd1"
        dash = "5 3" if tick != 50 else "3 3"
        elements.extend([
            f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" '
            f'stroke="{color}" stroke-dasharray="{dash}"/>',
            f'<text x="{left - 7}" y="{y + 3:.1f}" text-anchor="end" '
            'font-family="Arial,sans-serif" font-size="9" fill="#35434d">'
            f'{tick}</text>',
        ])
    for fraction in (0, 0.5, 1):
        y = volume_bottom - fraction * (volume_bottom - volume_top)
        elements.extend([
            f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" '
            'stroke="#d4d8dc" stroke-dasharray="4 3"/>',
            f'<text x="{left - 7}" y="{y + 3:.1f}" text-anchor="end" '
            'font-family="Arial,sans-serif" font-size="9" fill="#35434d">'
            f'{volume_max * fraction / 1_000_000:,.1f}</text>',
        ])
    elements.extend([
        f'<text x="17" y="{(price_top + price_bottom) / 2:.1f}" '
        'transform="rotate(-90 17 '
        f'{(price_top + price_bottom) / 2:.1f})" text-anchor="middle" '
        'font-family="Arial,sans-serif" font-size="10" fill="#35434d">'
        'Giá (đồng)</text>',
        f'<text x="17" y="{(rsi_top + rsi_bottom) / 2:.1f}" '
        'transform="rotate(-90 17 '
        f'{(rsi_top + rsi_bottom) / 2:.1f})" text-anchor="middle" '
        'font-family="Arial,sans-serif" font-size="10" fill="#35434d">RSI14</text>',
        f'<text x="17" y="{(volume_top + volume_bottom) / 2:.1f}" '
        'transform="rotate(-90 17 '
        f'{(volume_top + volume_bottom) / 2:.1f})" text-anchor="middle" '
        'font-family="Arial,sans-serif" font-size="10" fill="#35434d">Volume</text>',
    ])

    candle_width = min(9, max(3, x_step * 0.62))
    for index, row in chart_data.iterrows():
        x = x_position(index)
        rising = row["close"] >= row["open"]
        color = "#159447" if rising else "#e32626"
        elements.extend([
            f'<line x1="{x:.1f}" y1="{price_y(row["high"]):.1f}" '
            f'x2="{x:.1f}" y2="{price_y(row["low"]):.1f}" '
            f'stroke="{color}" stroke-width="1.2"/>',
            f'<rect x="{x - candle_width / 2:.1f}" '
            f'y="{min(price_y(row["open"]), price_y(row["close"])):.1f}" '
            f'width="{candle_width:.1f}" '
            f'height="{max(abs(price_y(row["open"]) - price_y(row["close"])), 1):.1f}" '
            f'fill="{color}" stroke="{color}"/>',
        ])
        if pd.notna(row["volume"]):
            bar_height = (
                row["volume"] / volume_max * (volume_bottom - volume_top)
            )
            elements.append(
                f'<rect x="{x - candle_width / 2:.1f}" '
                f'y="{volume_bottom - bar_height:.1f}" width="{candle_width:.1f}" '
                f'height="{bar_height:.1f}" fill="{color}"/>'
            )

    for column, color, dash in (
        ("bb_upper", "#777777", "5 3"),
        ("bb_lower", "#777777", "5 3"),
        ("ema20", "#168a4a", ""),
        ("ema50", "#d7a829", ""),
    ):
        path = line_path(column, price_y)
        if path:
            elements.append(
                f'<path d="{path}" fill="none" stroke="{color}" '
                f'stroke-width="1.8" stroke-dasharray="{dash}"/>'
            )

    rsi_path = line_path("rsi14", rsi_y)
    if rsi_path:
        elements.append(
            f'<path d="{rsi_path}" fill="none" stroke="#7a3c78" '
            'stroke-width="1.8"/>'
        )

    for index in date_indices:
        x = x_position(index)
        date_label = chart_data.iloc[index]["date"].strftime("%d/%m")
        elements.append(
            f'<text x="{x:.1f}" y="550" text-anchor="middle" '
            'font-family="Arial,sans-serif" font-size="9" fill="#35434d">'
            f'{date_label}</text>'
        )

    elements.append("</svg>")
    svg = "".join(elements)
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _financial_chart_data_uri(years, chart_values):
    chart_specs = (
        (
            chart_values.get("income_label", "Doanh thu thuần"),
            chart_values.get("income", {}),
            chart_values.get("income_yoy", {}),
            "#1e3a8a",
        ),
        (
            chart_values.get("profit_label", "Lợi nhuận sau thuế"),
            chart_values.get("net_profit", {}),
            chart_values.get("net_profit_yoy", {}),
            "#10b981",
        ),
    )
    if not any(
        sum(values.get(str(year)) is not None for year in years) >= 2
        for _, values, _, _ in chart_specs
    ):
        return ""

    width, height = 760, 570
    plot_left, plot_right = 82, 730
    plot_height = 150
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{width / 2}" y="21" text-anchor="middle" '
        'font-family="Arial,sans-serif" font-size="14" font-weight="bold" '
        'fill="#183247">KẾT QUẢ KINH DOANH &amp; TĂNG TRƯỞNG YoY</text>',
    ]
    for panel_index, (label, values, yoy, color) in enumerate(chart_specs):
        points = [values.get(str(year)) for year in years]
        yoy_points = [yoy.get(str(year)) for year in years]
        if sum(value is not None for value in points) < 2:
            continue
        panel_top = 34 + panel_index * 260
        plot_top, plot_bottom = panel_top + 43, panel_top + 193
        step = (plot_right - plot_left) / len(years)
        bar_width = min(46, step * 0.36)
        finite_values = [value for value in points if value is not None]
        min_value = min(0, min(finite_values))
        max_value = max(0, max(finite_values))
        value_span = max(max_value - min_value, 1)
        min_value -= value_span * 0.08
        max_value += value_span * 0.08
        value_span = max_value - min_value
        zero_y = plot_bottom - (0 - min_value) / value_span * plot_height
        yoy_finite = [value for value in yoy_points if value is not None]
        yoy_span = max(max((abs(value) for value in yoy_finite), default=1), 1) * 1.25
        centers = [
            plot_left + step * (index + 0.5) for index in range(len(years))
        ]
        elements.extend([
            f'<text x="{width / 2}" y="{panel_top + 17}" text-anchor="middle" '
            'font-family="Arial,sans-serif" font-size="12" font-weight="bold" '
            f'fill="#183247">{html.escape(label)} (tỷ đồng)</text>',
            f'<line x1="{plot_left}" y1="{plot_bottom}" x2="{plot_right}" y2="{plot_bottom}" '
            'stroke="#d8e1e8"/>',
            f'<line x1="{plot_left}" y1="{zero_y:.1f}" x2="{plot_right}" y2="{zero_y:.1f}" '
            'stroke="#aab8c4" stroke-dasharray="3 3"/>',
        ])
        line_path_parts = []
        for index, (year, value, yoy_value) in enumerate(
            zip(years, points, yoy_points)
        ):
            center_x = centers[index]
            if value is not None:
                value_y = plot_bottom - (value - min_value) / value_span * plot_height
                elements.append(
                    f'<rect x="{center_x - bar_width / 2:.1f}" '
                    f'y="{min(zero_y, value_y):.1f}" width="{bar_width:.1f}" '
                    f'height="{max(abs(zero_y - value_y), 1):.1f}" fill="{color}"/>'
                )
                label_y = (
                    max(value_y - 5, plot_top + 9)
                    if value >= 0
                    else min(value_y + 12, plot_bottom - 2)
                )
                elements.append(
                    f'<text x="{center_x:.1f}" y="{label_y:.1f}" text-anchor="middle" '
                    'font-family="Arial,sans-serif" font-size="9" font-weight="bold" '
                    f'fill="#35434d">{value:,.1f}</text>'
                )
            if yoy_value is not None:
                line_y = (
                    plot_top
                    + (yoy_span - yoy_value) / (2 * yoy_span) * plot_height
                )
                command = "L" if (
                    index > 0 and yoy_points[index - 1] is not None
                ) else "M"
                line_path_parts.append(
                    f'{command}{center_x:.1f},{line_y:.1f}'
                )
                elements.extend([
                    f'<circle cx="{center_x:.1f}" cy="{line_y:.1f}" r="3.5" '
                    'fill="#e68a1f"/>',
                    f'<text x="{center_x:.1f}" y="{max(line_y - 7, plot_top + 8):.1f}" '
                    'text-anchor="middle" font-family="Arial,sans-serif" '
                    'font-size="9" font-weight="bold" fill="#b76800">'
                    f'{yoy_value:+.1f}%</text>',
                ])
            elif index == 0:
                elements.append(
                    f'<text x="{center_x:.1f}" y="{plot_top + 12}" text-anchor="middle" '
                    'font-family="Arial,sans-serif" font-size="8" fill="#718294">'
                    'YoY N/A*</text>'
                )
            elements.append(
                f'<text x="{center_x:.1f}" y="{plot_bottom + 16}" text-anchor="middle" '
                'font-family="Arial,sans-serif" font-size="9" fill="#35434d">'
                f'{html.escape(str(year))}</text>'
            )
        if line_path_parts:
            elements.append(
                f'<path d="{" ".join(line_path_parts)}" fill="none" '
                'stroke="#e68a1f" stroke-width="2"/>'
            )
    elements.extend([
        f'<rect x="{plot_left}" y="{height - 36}" width="10" height="10" fill="#1e3a8a"/>',
        f'<text x="{plot_left + 15}" y="{height - 27}" font-family="Arial,sans-serif" '
        'font-size="9" fill="#35434d">Giá trị (tỷ đồng)</text>',
        f'<line x1="{plot_left + 175}" y1="{height - 31}" '
        f'x2="{plot_left + 194}" y2="{height - 31}" stroke="#e68a1f" stroke-width="2"/>',
        f'<circle cx="{plot_left + 184}" cy="{height - 31}" r="3" fill="#e68a1f"/>',
        f'<text x="{plot_left + 200}" y="{height - 27}" font-family="Arial,sans-serif" '
        'font-size="9" fill="#35434d">Tăng trưởng YoY (%)</text>',
        f'<text x="{plot_left + 400}" y="{height - 27}" font-family="Arial,sans-serif" '
        'font-size="8" fill="#718294">* Năm đầu thiếu dữ liệu năm trước để tính YoY</text>',
    ])
    elements.append('</svg>')
    encoded = base64.b64encode("".join(elements).encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _load_financial_data(ticker, workbook_path, fetch_missing=False):
    project_root = Path(__file__).resolve().parent.parent
    workbook_path = Path(workbook_path)
    if not workbook_path.is_absolute():
        workbook_path = project_root / workbook_path

    raw_dir = project_root / "data" / "raw"
    raw_files = {
        sheet_name: raw_dir / f"{ticker}_{sheet_name}.csv"
        for sheet_name in ("income_statement", "balance_sheet", "cash_flow", "ratio")
    }
    if workbook_path.exists():
        workbook_sheets = pd.read_excel(
            workbook_path, sheet_name=None, engine="openpyxl"
        )
    else:
        workbook_sheets = {}

    saved_reports = {
        name
        for name, path in raw_files.items()
        if path.is_file()
    }
    if workbook_sheets:
        saved_reports.update(
            name
            for name in workbook_sheets
            if name in workbook_sheets
            and "ticker" in workbook_sheets[name].columns
            and workbook_sheets[name]["ticker"]
            .astype("string")
            .str.strip()
            .str.upper()
            .eq(ticker)
            .any()
        )
    financial_error = None
    required_sheets = {"income_statement", "balance_sheet", "cash_flow", "ratio"}
    if required_sheets.difference(saved_reports) and fetch_missing:
        try:
            if __package__:
                from .fundamental import collect_ticker_financials
            else:
                from fundamental import collect_ticker_financials
            collect_ticker_financials(ticker)
            workbook_sheets = {}
            saved_reports = {
                name for name, path in raw_files.items() if path.is_file()
            }
        except (RuntimeError, ValueError, ImportError, OSError) as exc:
            financial_error = str(exc)

    if not any(path.is_file() for path in raw_files.values()) and not workbook_sheets:
        if financial_error is None:
            raise FileNotFoundError(
                f"Không tìm thấy workbook hoặc dữ liệu tài chính riêng cho {ticker}."
            )

    workbook_sheets = {
        sheet_name: frame
        for sheet_name, frame in workbook_sheets.items()
        if sheet_name in required_sheets
    }
    sheets = {}
    required_columns = {"ticker", "item", "item_id"}
    for sheet_name in required_sheets:
        raw_path = raw_files[sheet_name]
        if raw_path.is_file():
            frame = pd.read_csv(raw_path, encoding="utf-8-sig")
            frame.insert(0, "ticker", ticker)
            sheets[sheet_name] = frame
        elif sheet_name in workbook_sheets:
            frame = workbook_sheets[sheet_name]
            sheets[sheet_name] = frame.loc[
                frame["ticker"].astype("string").str.strip().str.upper().eq(ticker)
            ].copy()
        else:
            sheets[sheet_name] = pd.DataFrame(
                columns=["ticker", "item", "item_id"]
            )

    for sheet_name in required_sheets:
        missing_columns = required_columns.difference(sheets[sheet_name].columns)
        if missing_columns:
            raise ValueError(
                f"Sheet {sheet_name} thiếu cột: {', '.join(sorted(missing_columns))}"
            )

    missing_reports = [
        name
        for name, frame in sheets.items()
        if not frame.empty
        and not frame["ticker"].astype("string").str.strip().str.upper().eq(ticker).any()
    ]
    missing_reports.extend(
        name for name, frame in sheets.items() if frame.empty
    )
    if missing_reports:
        missing_note = (
            "Nguồn chưa có báo cáo: " + ", ".join(sorted(set(missing_reports))) + "."
        )
        financial_error = (
            f"{financial_error} {missing_note}".strip()
            if financial_error
            else missing_note
        )

    ratio_data = sheets["ratio"]
    source = (
        f"data/raw/{ticker}_*.csv"
        if any(path.is_file() for path in raw_files.values())
        else f"data/processed/{workbook_path.name}"
    )
    year_columns = [
        column
        for column in ratio_data.columns
        if str(column).isdigit()
    ]
    year_reference = ratio_data
    if not year_columns:
        for sheet_name in ("income_statement", "balance_sheet", "cash_flow"):
            candidate = sheets[sheet_name]
            candidate = candidate.loc[
                candidate["ticker"].astype("string").str.strip().str.upper().eq(ticker)
            ]
            candidate_years = [
                column for column in candidate.columns if str(column).isdigit()
            ]
            if candidate_years:
                year_reference = candidate
                year_columns = candidate_years
                break
    years = sorted(year_columns, key=int)
    if not years:
        raise ValueError(f"Workbook BCTC không có cột năm cho mã {ticker}.")

    for sheet_name in ("income_statement", "balance_sheet"):
        sheets[sheet_name] = normalize_statement_year_order(
            sheets[sheet_name], year_reference, years, ticker=ticker
        )

    def statement_series(sheet_name, item_ids):
        sheet = sheets[sheet_name]
        ticker_rows = sheet.loc[
            sheet["ticker"].astype("string").str.strip().str.upper().eq(ticker)
        ]
        for item_id in item_ids:
            matches = ticker_rows.loc[ticker_rows["item_id"].isin([item_id])]
            if sheet_name == "income_statement" and item_id in {
                "revenue", "net_revenue", "total_revenue", "net_sales"
            }:
                matches = matches.assign(
                    _net_revenue=matches["item"]
                    .astype("string")
                    .str.contains("thuần|net revenue", case=False, na=False)
                ).sort_values("_net_revenue", ascending=False)
            for _, row in matches.iterrows():
                values = row[years].apply(pd.to_numeric, errors="coerce")
                if values.notna().any():
                    return values, str(row.get("item") or item_id[0])
        return None, None

    bank_company = statement_series(
        "income_statement", ("net_interest_income",)
    )[0] is not None
    if bank_company:
        selected_items = {
            "income_statement": [
                (("net_interest_income",), "Thu nhập lãi thuần", "billions"),
                (("net_fee_and_commission_income",), "Lãi thuần từ hoạt động dịch vụ", "billions"),
                (("profit_before_tax",), "Lợi nhuận trước thuế", "billions"),
                (("net_profit", "net_profit_atttributable_to_the_equity_holders_of_the_bank"), "Lợi nhuận sau thuế", "billions"),
            ],
            "balance_sheet": [
                (("total_assets",), "Tổng tài sản", "billions"),
                (("loans_advances_and_finance_leases_to_customers", "loans_advances_and_finance_leases_to_customers_2"), "Cho vay khách hàng", "billions"),
                (("deposits_from_customers",), "Tiền gửi khách hàng", "billions"),
                (("demand_deposits", "deposits_from_customers_demand"), "CASA / tiền gửi không kỳ hạn", "billions"),
            ],
            "cash_flow": [
                (("operating_cash_flow",), "Lưu chuyển tiền từ HĐKD", "billions"),
            ],
            "ratio": [
                (("trailing_eps",), "EPS (đồng/cổ phiếu)", "currency"),
                (("book_value_per_share_bvps",), "BVPS (đồng/cổ phiếu)", "currency"),
                (("pe_ratio",), "P/E (lần)", "multiple"),
                (("pb_ratio",), "P/B (lần)", "multiple"),
                (("roe",), "ROE (%)", "percent"),
                (("roa",), "ROA (%)", "percent"),
                (("net_interest_margin_nim",), "NIM (%)", "percent"),
                (("npl", "non_performing_loan_ratio", "bad_debt_ratio"), "Nợ xấu / NPL (%)", "percent"),
                (("loan_loss_coverage_ratio", "loan_loss_provision_coverage"), "Bao phủ nợ xấu / LLC (%)", "percent"),
                (("capital_adequacy_ratio", "car"), "An toàn vốn / CAR (%)", "percent"),
            ],
        }
    else:
        selected_items = {
            "income_statement": [
                (("revenue", "net_revenue", "total_revenue", "net_sales"), "Doanh thu thuần", "billions"),
                (("gross_profit",), "Lợi nhuận gộp", "billions"),
                (("profit_before_tax",), "Lợi nhuận trước thuế", "billions"),
                (("net_profit", "net_profit_after_tax", "profit_after_tax"), "Lợi nhuận sau thuế", "billions"),
            ],
            "balance_sheet": [
                (("total_assets",), "Tổng tài sản", "billions"),
                (("total_liabilities",), "Tổng nợ phải trả", "billions"),
                (("owners_equity_2", "owners_equity_3", "owners_equity", "equity"), "Vốn chủ sở hữu", "billions"),
                (("short_term_borrowings_and_financial_leases", "short_term_borrowings"), "Nợ vay tài chính ngắn hạn", "billions"),
                (("long_term_borrowings_and_financial_leases", "long_term_borrowings"), "Nợ vay tài chính dài hạn", "billions"),
            ],
            "cash_flow": [
                (("operating_cash_flow",), "Lưu chuyển tiền từ HĐKD", "billions"),
            ],
            "ratio": [
                (("trailing_eps",), "EPS (đồng/cổ phiếu)", "currency"),
                (("pe_ratio",), "P/E (lần)", "multiple"),
                (("pb_ratio",), "P/B (lần)", "multiple"),
                (("book_value_per_share_bvps",), "BVPS (đồng/cổ phiếu)", "currency"),
                (("roe",), "ROE (%)", "percent"),
                (("roa",), "ROA (%)", "percent"),
                (("gross_margin",), "Biên lợi nhuận gộp (%)", "percent"),
                (("net_margin",), "Biên lợi nhuận ròng (%)", "percent"),
                (("debt_to_equity", "liabilities_to_equity"), "Nợ/Vốn chủ sở hữu (D/E)", "number"),
                (("current_ratio",), "Khả năng thanh toán hiện hành", "number"),
            ],
        }

    sections = []
    ratio_values = {}
    latest_year = max(years, key=int)
    section_titles = {
        "income_statement": "KẾT QUẢ KINH DOANH (TỶ ĐỒNG)",
        "balance_sheet": "BẢNG CÂN ĐỐI KẾ TOÁN (TỶ ĐỒNG)",
        "cash_flow": "LƯU CHUYỂN TIỀN TỆ (TỶ ĐỒNG)",
        "ratio": "CHỈ SỐ TÀI CHÍNH",
    }
    optional_item_ids = {
        "demand_deposits",
        "deposits_from_customers_demand",
        "npl",
        "non_performing_loan_ratio",
        "bad_debt_ratio",
        "loan_loss_coverage_ratio",
        "loan_loss_provision_coverage",
        "capital_adequacy_ratio",
        "car",
    }
    for sheet_name, items in selected_items.items():
        rows = []
        for item_ids, label, value_type in items:
            raw_values, _ = statement_series(sheet_name, item_ids)
            if (
                set(item_ids).intersection(optional_item_ids)
                and (raw_values is None or not raw_values.notna().any())
            ):
                continue
            if raw_values is None or not raw_values.notna().any():
                raw_values = pd.Series(index=years, dtype="float64")
            if value_type == "billions":
                formatted_values = [
                    f"{value / 1_000_000_000:,.1f}" if pd.notna(value) else "N/A"
                    for value in raw_values
                ]
            elif value_type == "currency":
                formatted_values = [
                    f"{value:,.0f} đ" if pd.notna(value) else "N/A"
                    for value in raw_values
                ]
            elif value_type == "percent":
                formatted_values = [
                    f"{value:.2f}%" if pd.notna(value) else "N/A"
                    for value in raw_values
                ]
            elif value_type == "number":
                formatted_values = [
                    f"{value:.2f}" if pd.notna(value) else "N/A"
                    for value in raw_values
                ]
            else:
                formatted_values = [
                    f"{value:.2f}x" if pd.notna(value) else "N/A"
                    for value in raw_values
                ]
            rows.append({"label": label, "values": formatted_values})
            if sheet_name == "ratio":
                ratio_values[item_ids[0]] = raw_values.get(latest_year)

        if rows:
            sections.append({"title": section_titles[sheet_name], "rows": rows})

    beta_series, _ = statement_series("ratio", ("beta",))
    if beta_series is not None:
        beta_value = beta_series.get(latest_year)
        if pd.isna(beta_value):
            beta_value = beta_series.dropna().iloc[-1] if beta_series.notna().any() else None
        if beta_value is not None and pd.notna(beta_value):
            ratio_values["beta"] = beta_value

    revenue_series, _ = statement_series(
        "income_statement", ("revenue", "net_revenue", "total_revenue", "net_sales")
    )
    income_label = "Doanh thu thuần" if revenue_series is not None else None
    if revenue_series is None:
        revenue_series, _ = statement_series(
            "income_statement", ("net_interest_income",)
        )
        if revenue_series is not None:
            income_label = "Thu nhập lãi thuần"
    profit_series, _ = statement_series(
        "income_statement",
        (
            "net_profit",
            "net_profit_after_tax",
            "profit_after_tax",
            "net_income",
            "profit_after_tax_for_shareholders_of_parent_company",
            "net_profit_atttributable_to_the_equity_holders_of_the_bank",
        ),
    )
    eps_series, _ = statement_series("ratio", ("trailing_eps",))
    equity_series, _ = statement_series(
        "balance_sheet",
        ("owners_equity_2", "owners_equity_3", "owners_equity", "equity"),
    )
    if equity_series is None and not bank_company:
        assets_series, _ = statement_series("balance_sheet", ("total_assets",))
        liabilities_series, _ = statement_series("balance_sheet", ("total_liabilities",))
        if assets_series is not None and liabilities_series is not None:
            equity_series = assets_series - liabilities_series

    debt_values = None
    if not bank_company:
        short_debt, _ = statement_series(
            "balance_sheet",
            ("short_term_borrowings_and_financial_leases", "short_term_borrowings"),
        )
        long_debt, _ = statement_series(
            "balance_sheet",
            ("long_term_borrowings_and_financial_leases", "long_term_borrowings"),
        )
        if short_debt is not None or long_debt is not None:
            debt_values = (
                short_debt if short_debt is not None else pd.Series(0, index=years)
            ).fillna(0) + (
                long_debt if long_debt is not None else pd.Series(0, index=years)
            ).fillna(0)

    if equity_series is not None:
        ratio_values["equity"] = equity_series.get(latest_year)
    if debt_values is not None:
        balance_section = next(
            section
            for section in sections
            if section["title"] == section_titles["balance_sheet"]
        )
        balance_section["rows"].append(
            {
                "label": "Nợ vay tài chính (ngắn + dài hạn)",
                "values": [
                    f"{value / 1_000_000_000:,.1f}" if pd.notna(value) else "N/A"
                    for value in debt_values
                ],
            }
        )
        equity = equity_series.get(latest_year) if equity_series is not None else None
        debt = debt_values.get(latest_year)
        if pd.notna(equity) and equity:
            ratio_values["debt_to_equity"] = debt / equity
            de_series = debt_values / equity_series.replace(0, float("nan"))
            de_row = next(
                (
                    row
                    for section in sections
                    if section["title"] == section_titles["ratio"]
                    for row in section["rows"]
                    if row["label"] == "Nợ/Vốn chủ sở hữu (D/E)"
                ),
                None,
            )
            if de_row:
                de_row["values"] = [
                    f"{value:.2f}" if pd.notna(value) else "N/A"
                    for value in de_series
                ]

    if not bank_company:
        gross_profit_series, _ = statement_series(
            "income_statement", ("gross_profit",)
        )
        if gross_profit_series is not None and revenue_series is not None:
            gross_margins = gross_profit_series / revenue_series.replace(0, float("nan")) * 100
            revenue = revenue_series.get(latest_year)
            gross_profit = gross_profit_series.get(latest_year)
            if pd.notna(revenue) and revenue:
                ratio_values["gross_margin"] = gross_profit / revenue * 100
                ratio_row = next(
                    (
                        row
                        for section in sections
                        if section["title"] == section_titles["ratio"]
                        for row in section["rows"]
                        if row["label"] == "Biên lợi nhuận gộp (%)"
                    ),
                    None,
                )
                if ratio_row:
                    ratio_row["values"] = [
                        f"{value:.2f}%" if pd.notna(value) else "N/A"
                        for value in gross_margins
                    ]
        latest_profit = profit_series.get(latest_year) if profit_series is not None else None
        latest_revenue = revenue_series.get(latest_year) if revenue_series is not None else None
        if pd.notna(latest_profit) and pd.notna(latest_revenue) and latest_revenue:
            ratio_values["net_margin"] = latest_profit / latest_revenue * 100
            net_margins = profit_series / revenue_series.replace(0, float("nan")) * 100
            ratio_row = next(
                (
                    row
                    for section in sections
                    if section["title"] == section_titles["ratio"]
                    for row in section["rows"]
                    if row["label"] == "Biên lợi nhuận ròng (%)"
                ),
                None,
            )
            if ratio_row:
                ratio_row["values"] = [
                    f"{value:.2f}%" if pd.notna(value) else "N/A"
                    for value in net_margins
                ]

        current_assets, _ = statement_series("balance_sheet", ("current_assets",))
        current_liabilities, _ = statement_series(
            "balance_sheet", ("current_liabilities",)
        )
        if current_assets is not None and current_liabilities is not None:
            current_liability = current_liabilities.get(latest_year)
            if pd.notna(current_liability) and current_liability:
                ratio_values["current_ratio"] = (
                    current_assets.get(latest_year) / current_liability
                )
                ratio_row = next(
                    (
                        row
                        for section in sections
                        if section["title"] == section_titles["ratio"]
                        for row in section["rows"]
                        if row["label"] == "Khả năng thanh toán hiện hành"
                    ),
                    None,
                )
                if ratio_row:
                    ratio_row["values"] = [
                        (
                            f"{assets / liabilities:.2f}"
                            if pd.notna(assets) and pd.notna(liabilities) and liabilities
                            else "N/A"
                        )
                        for assets, liabilities in zip(current_assets, current_liabilities)
                    ]
    revenue_growth_series = revenue_series
    if revenue_growth_series is not None:
        previous_year = str(int(latest_year) - 1)
        current_revenue = revenue_growth_series.get(latest_year)
        previous_revenue = revenue_growth_series.get(previous_year)
        if pd.notna(current_revenue) and pd.notna(previous_revenue) and previous_revenue:
            ratio_values["revenue_growth"] = current_revenue / previous_revenue - 1
    if profit_series is not None:
        previous_year = str(int(latest_year) - 1)
        current_profit = profit_series.get(latest_year)
        previous_profit = profit_series.get(previous_year)
        if pd.notna(current_profit) and pd.notna(previous_profit) and previous_profit:
            ratio_values["profit_growth"] = current_profit / previous_profit - 1
    if eps_series is not None:
        previous_year = str(int(latest_year) - 1)
        current_eps = eps_series.get(latest_year)
        previous_eps = eps_series.get(previous_year)
        if pd.notna(current_eps) and pd.notna(previous_eps) and previous_eps:
            ratio_values["eps_growth"] = current_eps / previous_eps - 1
    if bank_company:
        revenue_series, income_label = statement_series(
            "income_statement", ("net_interest_income",)
        )
        income_label = "Thu nhập lãi thuần"
    historical_pe, _ = statement_series("ratio", ("pe_ratio",))
    historical_pb, _ = statement_series("ratio", ("pb_ratio",))
    def yoy_percent(series):
        if series is None:
            return {}
        result = {}
        for index, year in enumerate(years):
            if index == 0:
                result[str(year)] = None
                continue
            previous = pd.to_numeric(series.iloc[index - 1], errors="coerce")
            current = pd.to_numeric(series.iloc[index], errors="coerce")
            result[str(year)] = (
                float((current / previous - 1) * 100)
                if pd.notna(previous) and previous != 0 and pd.notna(current)
                else None
            )
        return result

    financial_chart_data = {
        "years": [str(year) for year in years],
        "income_label": income_label or "Doanh thu thuần",
        "income": {
            str(year): (
                float(value) / 1_000_000_000 if pd.notna(value) else None
            )
            for year, value in (revenue_series.items() if revenue_series is not None else [])
        },
        "net_profit": {
            str(year): (
                float(value) / 1_000_000_000 if pd.notna(value) else None
            )
            for year, value in (profit_series.items() if profit_series is not None else [])
        },
        "income_yoy": yoy_percent(revenue_series),
        "net_profit_yoy": yoy_percent(profit_series),
        "company_type": "bank" if bank_company else "non-bank",
        "profit_label": "Lợi nhuận sau thuế",
        "historical_valuation": {
            "PE": (
                [float(value) if pd.notna(value) else None for value in historical_pe]
                if historical_pe is not None
                else []
            ),
            "PB": (
                [float(value) if pd.notna(value) else None for value in historical_pb]
                if historical_pb is not None
                else []
            ),
        },
    }
    return (
        years,
        sections,
        ratio_values,
        _financial_chart_data_uri(years, financial_chart_data),
        financial_chart_data,
        source,
        financial_error,
    )


def load_stock_report_data(
    ticker,
    csv_path=None,
    financial_path=None,
    database_path=None,
    fetch_missing_financials=True,
):
    """Build report data from the shared market database and fundamental exports."""
    project_root = Path(__file__).resolve().parent.parent
    ticker = str(ticker).strip().upper()
    if not ticker:
        raise ValueError("Vui lòng nhập mã cổ phiếu.")

    if csv_path is not None:
        csv_path = Path(csv_path)
        if not csv_path.is_absolute():
            csv_path = project_root / csv_path
        history_all = pd.read_csv(csv_path)
        required_columns = {"symbol", "date", "open", "high", "low", "close", "volume"}
        missing_columns = required_columns.difference(history_all.columns)
        if missing_columns:
            raise ValueError(
                f"CSV thiếu các cột bắt buộc: {', '.join(sorted(missing_columns))}"
            )
        symbols = history_all["symbol"].astype("string").str.strip().str.upper()
        history = history_all.loc[symbols == ticker].copy()
        if history.empty:
            raise ValueError(f"Không tìm thấy mã {ticker} trong {csv_path}.")
        history["date"] = pd.to_datetime(history["date"], errors="coerce")
        for column in ("open", "high", "low", "close", "volume"):
            history[column] = pd.to_numeric(history[column], errors="coerce")
        history = history.dropna(subset=["date", "close"]).sort_values("date")
        try:
            price_history_source = str(csv_path.relative_to(project_root))
        except ValueError:
            price_history_source = str(csv_path)
    else:
        history = load_price_history(ticker, database_path=database_path)
        price_history_source = "data/market_data.db: historical_ohlcv + technical_indicators"

    if history.empty:
        raise ValueError(f"Mã {ticker} không có dữ liệu giá hợp lệ.")

    financial_path = (
        financial_path
        if financial_path is not None
        else project_root / "data" / "processed" / "tv2_financial_data.xlsx"
    )
    (
        financial_years,
        financial_sections,
        financial_ratios,
        financial_chart,
        financial_chart_data,
        financial_source,
        financial_error,
    ) = _load_financial_data(
        ticker,
        financial_path,
        fetch_missing=fetch_missing_financials,
    )

    latest = history.iloc[-1]
    latest_date = latest["date"]
    quote_error = None
    quote = None
    market_open = is_vietnam_trading_session()
    if database_path is None:
        database_path = project_root / "data" / "market_data.db"
    try:
        quote = load_realtime_quote(ticker, database_path=database_path)
    except RuntimeError as exc:
        quote_error = str(exc)
    if quote is None and quote_error is None and market_open:
        quote_error = f"Không tìm thấy báo giá realtime cho mã {ticker}."

    recent_history = history.loc[
        history["date"] >= latest_date - pd.Timedelta(days=365)
    ]
    volume_20d = history["volume"].dropna().tail(20)
    quote_price = quote.get("price") if quote else None
    quote_price = _score_value(quote_price)
    close = quote_price if quote_price is not None else float(latest["close"])
    quote_timestamp = quote.get("timestamp") if quote else None
    if quote_timestamp:
        try:
            quote_date = datetime.fromisoformat(
                quote_timestamp.replace("Z", "+00:00")
            ).astimezone().strftime("%d/%m/%Y %H:%M")
        except ValueError:
            quote_date = quote_timestamp
    else:
        quote_date = None

    quote_source = quote.get("source") if quote else None
    sources = [price_history_source]
    if quote_source:
        sources.append(f"{quote_source} ({quote_date})")
    if financial_sections:
        sources.append(financial_source)
    news_data = fetch_stock_news(ticker, limit=3, period="w")
    if news_data.get("source"):
        sources.append(news_data["source"])

    symbol_metadata = load_symbol_metadata(ticker)
    company_metadata, company_metadata_error = load_or_fetch_company_metadata(
        ticker,
        exchange=symbol_metadata.get("exchange"),
    )
    if company_metadata.get("source"):
        sources.append(company_metadata["source"])
    company_name = (
        company_metadata.get("company_name")
        or (quote.get("company_name") if quote else None)
        or latest.get("organ_name")
    )
    company_name = str(company_name) if pd.notna(company_name) else ticker
    exchange = (
        (quote.get("exchange") if quote else None)
        or company_metadata.get("exchange")
        or symbol_metadata.get("exchange")
        or latest.get("exchange")
    )
    exchange = str(exchange) if pd.notna(exchange) else "Chưa có dữ liệu"
    company_market_cap = company_metadata.get("market_cap")
    market_cap_is_estimated = False
    if (
        company_market_cap is None
        and company_metadata.get("shares_outstanding") is not None
        and close > 0
    ):
        company_market_cap = close * 1000 * float(
            company_metadata["shares_outstanding"]
        )
        market_cap_is_estimated = True

    technical_points = [
        (
            f"Giá realtime: {_format_price(close)}/cổ phiếu "
            f"(cập nhật {quote_date})."
            if quote
            else f"Giá đóng cửa ngày {latest_date:%d/%m/%Y}: "
            f"{_format_price(close)}/cổ phiếu (dự phòng, không phải realtime)."
        )
    ]
    for column, label in (("MA20", "MA20"), ("MA50", "MA50"), ("RSI14", "RSI 14")):
        value = pd.to_numeric(latest.get(column), errors="coerce")
        formatted = _format_indicator(value)
        if formatted is not None:
            technical_points.append(f"{label}: {formatted}.")
    moving_average = pd.to_numeric(latest.get("MA20"), errors="coerce")
    if pd.notna(moving_average):
        relation = "trên" if close >= moving_average else "dưới"
        price_label = "Giá hiện tại" if quote else "Giá đóng cửa"
        technical_points.append(f"{price_label} đang {relation} MA20.")

    prior_volumes = history.loc[
        history["date"] < latest_date, "volume"
    ].dropna().tail(20)
    average_prior_volume = prior_volumes.mean() if not prior_volumes.empty else None
    volume_ratio = (
        float(latest["volume"] / average_prior_volume)
        if average_prior_volume and pd.notna(latest["volume"])
        else None
    )

    summary_parts = list(technical_points)
    for item_id, label, suffix in (
        ("roe", "ROE", "%"),
        ("pe_ratio", "P/E", "x"),
        ("pb_ratio", "P/B", "x"),
    ):
        value = financial_ratios.get(item_id)
        if value is not None and pd.notna(value):
            summary_parts.append(
                f"{label} năm {financial_years[-1]}: {value:.2f}{suffix}."
            )

    report_data = {
        "ticker": ticker,
        "company_name": company_name,
        "report_date": date.today().strftime("%d/%m/%Y"),
        "recommendation": "—",
        "target_price": "—",
        "upside": "—",
        "exchange": exchange,
        "industry": _industry_in_vietnamese(company_metadata.get("industry"))
        or "Chưa có dữ liệu",
        "current_price": _format_price(close),
        "investment_horizon": "12 tháng",
        "price_chart": _price_chart_data_uri(history, ticker, close if quote else None),
        "price_source": (
            f"{quote_source} lúc {quote_date}; đơn vị quy đổi sang đồng/cổ phiếu"
            if quote
            else f"Giá đóng cửa lịch sử {latest_date:%d/%m/%Y}; "
            "không có báo giá realtime, đơn vị đồng/cổ phiếu"
        ),
        "market_cap": (
            (
                f"{company_market_cap / 1_000_000_000:,.2f} tỷ đồng"
                + (" (ước tính theo giá hiện tại)" if market_cap_is_estimated else "")
            )
            if company_market_cap is not None
            else "Chưa có dữ liệu"
        ),
        "shares_outstanding": (
            f"{company_metadata['shares_outstanding'] / 1_000_000:,.2f} triệu CP"
            if company_metadata.get("shares_outstanding")
            else "Chưa có dữ liệu"
        ),
        "avg_volume_20d": (
            f"{volume_20d.mean() / 1_000_000:,.2f} triệu CP"
            if not volume_20d.empty
            else "—"
        ),
        "price_52w_range": (
            f"{_format_price(recent_history['low'].min())} – "
            f"{_format_price(recent_history['high'].max())}"
        ),
        "foreign_ownership": (
            f"{company_metadata['foreign_ownership']:.2%}"
            if company_metadata.get("foreign_ownership") is not None
            else "—"
        ),
        "foreign_ownership_limit": (
            f"{company_metadata['foreign_ownership_limit']:.2%}"
            if company_metadata.get("foreign_ownership_limit") is not None
            else "Chưa có dữ liệu"
        ),
        "beta": _format_indicator(financial_ratios.get("beta")) or "—",
        "financial_years": financial_years,
        "financial_sections": financial_sections,
        "financial_chart": financial_chart,
        "financial_chart_data": financial_chart_data,
        "fundamental_metrics": {
            "pe": _score_value(financial_ratios.get("pe_ratio")),
            "eps": _score_value(financial_ratios.get("trailing_eps")),
            "source": financial_source,
        },
        "metadata_missing": [
            label
            for value, label in (
                (company_metadata.get("industry"), "ngành"),
                (company_market_cap, "vốn hóa"),
                (company_metadata.get("shares_outstanding"), "số cổ phiếu lưu hành"),
            )
            if value is None or pd.isna(value)
        ],
        "company_metadata_source": company_metadata.get("source"),
        "company_metadata_as_of": company_metadata.get("fetched_at"),
        "company_metadata_error": company_metadata_error,
        "technical_metrics": [
            {"label": "Giá gần nhất", "value": _format_price(close)},
            {"label": "MA20", "value": _format_price(latest.get("MA20"))},
            {"label": "MA50", "value": _format_price(latest.get("MA50"))},
            {"label": "RSI 14", "value": _format_indicator(latest.get("RSI14")) or "N/A"},
            {"label": "MACD", "value": _format_indicator(latest.get("MACD")) or "N/A"},
            {
                "label": "MACD Signal",
                "value": _format_indicator(latest.get("MACD_signal")) or "N/A",
            },
            {
                "label": "MACD Histogram",
                "value": _format_indicator(latest.get("MACD_hist")) or "N/A",
            },
            {
                "label": "KLGD gần nhất / TB20",
                "value": f"{volume_ratio:.2f}x" if volume_ratio is not None else "N/A",
            },
        ],
        "quote_error": quote_error,
        "quote_is_realtime": quote is not None,
        "technical_as_of": latest_date.strftime("%d/%m/%Y"),
        "financial_error": financial_error,
        "news_status": news_data.get("status", "empty"),
        "news_error": news_data.get("error"),
        "news_list": [
            {
                "title": item["title"],
                "url": (
                    item["url"]
                    if urlparse(item.get("url") or "").scheme in {"http", "https"}
                    and urlparse(item.get("url") or "").netloc
                    else ""
                ),
                "date": item.get("published_at") or "",
                "source": item.get("source") or news_data.get("source") or "",
                "summary": (
                    (item.get("summary") or "")[:180].rstrip()
                    + ("..." if len(item.get("summary") or "") > 180 else "")
                ),
            }
            for item in news_data.get("headlines", [])
        ],
        "price_history": history,
        "investment_thesis": (
            "Báo cáo sử dụng OHLCV và chỉ báo kỹ thuật từ database thị trường; "
            "giá realtime được lấy riêng từ snapshot Vietcap khi khả dụng. "
            + (
                f"Số liệu cơ bản lấy từ {financial_source}; "
                "các năm báo cáo theo đúng cột năm nguồn."
                if financial_sections
                else f"Hiện chưa có số liệu BCTC cho mã {ticker}."
            )
        ),
        "ai_summary": " ".join(summary_parts),
        "investment_points": technical_points[1:],
        "key_risks": [
            (
                "Chưa có dữ liệu BCTC cho mã này."
                if not financial_sections
                else "Một số chỉ tiêu định giá và thông tin doanh nghiệp chưa có trong dữ liệu đầu vào."
            ),
            "Dữ liệu giá lịch sử và chỉ báo kỹ thuật không đảm bảo kết quả trong tương lai.",
        ],
        "analyst_name": "Tổng hợp dữ liệu cổ phiếu",
        "analyst_contact": "",
        "data_sources": "; ".join(sources),
        "data_as_of": quote_date or latest_date.strftime("%d/%m/%Y"),
    }
    report_data = _apply_company_metadata(report_data, company_metadata)
    score_tech = {
        "symbol": ticker,
        "as_of": quote_timestamp or latest_date.strftime("%Y-%m-%d"),
        "source": quote_source or price_history_source,
        "close": close,
        "RSI": _score_value(latest.get("RSI14")),
        "MA20": _score_value(latest.get("MA20")),
        "MA50": _score_value(latest.get("MA50")),
        "MACD": _score_value(latest.get("MACD")),
        "MACD_signal": _score_value(latest.get("MACD_signal")),
        "volume_ratio": volume_ratio,
        "low_52w": _score_value(recent_history["low"].min()),
        "high_52w": _score_value(recent_history["high"].max()),
    }
    industry_key = str(company_metadata.get("industry") or "").casefold()
    valuation_type = (
        "bank"
        if financial_chart_data.get("company_type") == "bank"
        else (
            "real_estate"
            if any(
                marker in industry_key
                for marker in ("bất động sản", "real estate", "property")
            )
            else "non-bank"
        )
    )
    score_fund = {
        "symbol": ticker,
        "as_of": financial_years[-1] if financial_years else None,
        "source": financial_source if financial_sections else None,
        "PE": _score_value(financial_ratios.get("pe_ratio")),
        "PB": _score_value(financial_ratios.get("pb_ratio")),
        "ROE": (
            _score_value(financial_ratios.get("roe")) / 100
            if financial_ratios.get("roe") is not None
            else None
        ),
        "ROA": (
            _score_value(financial_ratios.get("roa")) / 100
            if financial_ratios.get("roa") is not None
            else None
        ),
        "EPS": _score_value(financial_ratios.get("trailing_eps")),
        "BVPS": _score_value(financial_ratios.get("book_value_per_share_bvps")),
        "valuation_type": valuation_type,
        "historical_PE": financial_chart_data.get("historical_valuation", {}).get("PE", []),
        "historical_PB": financial_chart_data.get("historical_valuation", {}).get("PB", []),
        "EPS_growth": _score_value(financial_ratios.get("eps_growth")),
        "revenue_growth": _score_value(financial_ratios.get("revenue_growth")),
        "profit_growth": _score_value(financial_ratios.get("profit_growth")),
        "financial_trend": {
            "years": financial_chart_data.get("years", []),
            "income_label": financial_chart_data.get("income_label"),
            "income_billions": financial_chart_data.get("income", {}),
            "income_yoy_percent": financial_chart_data.get("income_yoy", {}),
            "net_profit_billions": financial_chart_data.get("net_profit", {}),
            "net_profit_yoy_percent": financial_chart_data.get("net_profit_yoy", {}),
            "company_type": valuation_type,
        },
    }
    scoring_data = {
        "tech": score_tech,
        "fund": score_fund,
        "news": news_data,
    }
    report_data = apply_scoring_to_report(report_data, scoring_data)
    report_data["_scoring_data"] = scoring_data
    return report_data


def refresh_stock_report_quote(report_data, database_path=None):
    """Refresh only market-dependent report fields; preserve BCTC and news."""
    if not isinstance(report_data, dict):
        raise TypeError("Dữ liệu báo cáo phải là dict.")

    ticker = str(report_data.get("ticker", "")).strip().upper()
    if not ticker:
        raise ValueError("Báo cáo phải có mã cổ phiếu.")

    history = report_data.get("price_history")
    if not isinstance(history, pd.DataFrame) or history.empty:
        raise ValueError("Báo cáo không có dữ liệu giá lịch sử để cập nhật.")

    latest = history.iloc[-1]
    latest_date = pd.to_datetime(latest["date"])
    if database_path is None:
        database_path = Path(__file__).resolve().parent.parent / "data" / "market_data.db"

    quote = None
    quote_error = None
    market_open = is_vietnam_trading_session()
    if not market_open:
        refreshed = dict(report_data)
        refreshed["current_price"] = _format_price(latest["close"])
        refreshed["price_source"] = (
            f"Giá đóng cửa phiên gần nhất ({latest_date:%d/%m/%Y}); "
            "ngoài giờ giao dịch, đơn vị đồng/cổ phiếu"
        )
        refreshed["data_as_of"] = latest_date.strftime("%d/%m/%Y")
        refreshed["quote_error"] = None
        refreshed["quote_is_realtime"] = False
        refreshed["technical_as_of"] = latest_date.strftime("%d/%m/%Y")
        refreshed["price_chart"] = _price_chart_data_uri(history, ticker)
        refreshed["technical_metrics"] = [
            {
                "label": metric["label"],
                "value": _format_price(latest["close"])
                if metric["label"] == "Giá gần nhất"
                else metric["value"],
            }
            for metric in report_data.get("technical_metrics", [])
        ]
        refreshed = _apply_company_metadata(
            refreshed, load_company_metadata(ticker)
        )
        scoring_data = report_data.get("_scoring_data")
        if isinstance(scoring_data, dict) and isinstance(scoring_data.get("tech"), dict):
            scoring_data = {
                **scoring_data,
                "tech": {
                    **scoring_data["tech"],
                    "close": float(latest["close"]),
                    "as_of": latest_date.strftime("%Y-%m-%d"),
                },
            }
            refreshed = apply_scoring_to_report(
                refreshed, scoring_data, use_ai=False
            )
            refreshed["_scoring_data"] = scoring_data
        return refreshed

    try:
        quote = load_realtime_quote(ticker, database_path=database_path)
    except RuntimeError as exc:
        quote_error = str(exc)
    if quote is None and quote_error is None and market_open:
        quote_error = f"Không tìm thấy báo giá realtime cho mã {ticker}."

    if quote is not None:
        close = _score_value(quote.get("price"))
        quote_timestamp = quote.get("timestamp")
        try:
            quote_date = datetime.fromisoformat(
                quote_timestamp.replace("Z", "+00:00")
            ).astimezone().strftime("%d/%m/%Y %H:%M")
        except (AttributeError, TypeError, ValueError):
            quote_date = quote_timestamp or "Thời gian không rõ"
        quote_source = quote.get("source") or "market_data.db"
        price_source = (
            f"{quote_source} lúc {quote_date}; "
            "đơn vị quy đổi sang đồng/cổ phiếu"
        )
    else:
        close = float(latest["close"])
        quote_timestamp = None
        quote_date = None
        quote_source = None
        price_source = (
            f"Giá đóng cửa lịch sử {latest_date:%d/%m/%Y}; "
            "không có báo giá realtime, đơn vị đồng/cổ phiếu"
        )

    refreshed = dict(report_data)
    refreshed["current_price"] = _format_price(close)
    refreshed["price_source"] = price_source
    refreshed["data_as_of"] = quote_date or latest_date.strftime("%d/%m/%Y")
    refreshed["quote_error"] = quote_error
    refreshed["quote_is_realtime"] = quote is not None
    refreshed["technical_as_of"] = latest_date.strftime("%d/%m/%Y")
    refreshed["price_chart"] = _price_chart_data_uri(
        history, ticker, close if quote else None
    )
    refreshed["technical_metrics"] = [
        {
            "label": metric["label"],
            "value": _format_price(close) if metric["label"] == "Giá gần nhất"
            else metric["value"],
        }
        for metric in report_data.get("technical_metrics", [])
    ]
    refreshed = _apply_company_metadata(refreshed, load_company_metadata(ticker))

    scoring_data = report_data.get("_scoring_data")
    if isinstance(scoring_data, dict) and isinstance(scoring_data.get("tech"), dict):
        scoring_data = {
            **scoring_data,
            "tech": {
                **scoring_data["tech"],
                "close": close,
                "as_of": quote_timestamp or latest_date.strftime("%Y-%m-%d"),
                "source": quote_source or report_data.get("price_history_source"),
            },
        }
        refreshed = apply_scoring_to_report(
            refreshed, scoring_data, use_ai=False
        )
        refreshed["_scoring_data"] = scoring_data

    return refreshed


def apply_scoring_to_report(report_data, analysis_data, use_ai=False):
    """Add scoring results to a PDF context without replacing its source data."""
    if not isinstance(report_data, dict):
        raise TypeError("Dữ liệu báo cáo phải là dict.")
    if not isinstance(analysis_data, dict):
        raise TypeError("Dữ liệu phân tích phải là dict.")

    tech = analysis_data.get("tech")
    fund = analysis_data.get("fund")
    news = analysis_data.get("news") or {}
    if not isinstance(tech, dict) or not isinstance(fund, dict):
        raise ValueError("Dữ liệu phân tích cần có hai object 'tech' và 'fund'.")
    if not isinstance(news, dict):
        raise ValueError("Dữ liệu 'news' phải là object.")

    ticker = str(report_data.get("ticker", "")).strip().upper()
    analysis_tickers = {
        str(symbol).strip().upper()
        for symbol in (
            tech.get("symbol"),
            fund.get("symbol"),
            news.get("symbol"),
        )
        if symbol
    }
    if not ticker or not analysis_tickers:
        raise ValueError("Báo cáo và dữ liệu phân tích phải có mã cổ phiếu.")
    if analysis_tickers != {ticker}:
        analysis_ticker = ", ".join(sorted(analysis_tickers))
        raise ValueError(
            f"Mã trong báo cáo ({ticker}) không khớp mã phân tích ({analysis_ticker})."
        )

    recommendation = get_recommendation(tech, fund, news, use_ai=use_ai)
    scoring_context = to_report_context(
        recommendation,
        tech,
        fund,
        company_name=report_data.get("company_name") or "",
        exchange=report_data.get("exchange") or "",
        industry=report_data.get("industry") or "",
    )
    report = dict(report_data)
    for key in (
        "recommendation",
        "target_price",
        "upside",
        "target_method",
        "target_confidence",
        "valuation_methods",
        "valuation_summary",
        "investment_thesis",
        "ai_summary",
        "investment_points",
        "key_risks",
    ):
        report[key] = scoring_context[key]
    report["score_breakdown"] = recommendation["score_breakdown"]

    sources = [
        source.strip()
        for source in str(report_data.get("data_sources", "")).split(";")
        if source.strip()
    ]
    for source in (tech.get("source"), fund.get("source")):
        if source and not any(source in existing for existing in sources):
            sources.append(source)
    report["data_sources"] = "; ".join(sources)
    return report


def generate_pdf(data_dict, output_path="outputs/stock_report.pdf"):
    """
    Hàm xuất file PDF báo cáo phân tích cổ phiếu từ Jinja2 HTML/CSS Template.
    Hỗ trợ tự động chuyển đổi giữa WeasyPrint (Streamlit Cloud/Linux)
    và xhtml2pdf (Fallback cho Windows Local).
    """
    project_root = Path(__file__).resolve().parent.parent
    core_dir = project_root / "core"
    output_path = Path(output_path)
    if not output_path.is_absolute():
        output_path = project_root / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Nạp HTML Template từ thư mục core/
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(core_dir)),
        autoescape=jinja2.select_autoescape(["html", "xml"]),
    )
    template = env.get_template('report_template.html')

    # 2. Render dữ liệu vào Template HTML
    rendered_html = template.render(**data_dict)
    # 3. Tiến hành xuất PDF
    try:
        # Ưu tiên WeasyPrint (Chạy chuẩn trên Linux / Streamlit Cloud)
        from weasyprint import HTML, CSS
        css_path = core_dir / "report_style.css"
        stylesheets = [CSS(css_path)] if css_path.exists() else []
        stylesheets.append(
            CSS(
                string=".page-footer .page-number { display: none; }",
                base_url=str(core_dir),
            )
        )
        HTML(string=rendered_html, base_url=str(core_dir)).write_pdf(
            str(output_path),
            stylesheets=stylesheets,
        )
        print("-> PDF exported successfully with WeasyPrint.")

    except (ImportError, OSError):
        # Fallback xhtml2pdf dành riêng cho Windows khi thiếu GTK3
        from xhtml2pdf import pisa

        def resolve_resource(uri, _):
            parsed_uri = urlparse(uri)
            if parsed_uri.scheme:
                return uri
            resource_path = Path(unquote(parsed_uri.path))
            if not resource_path.is_absolute():
                resource_path = core_dir / resource_path
            return str(resource_path.resolve())

        with output_path.open("wb") as pdf_file:
            result = pisa.CreatePDF(
                src=rendered_html,
                dest=pdf_file,
                encoding='utf-8',
                path=str(core_dir),
                link_callback=resolve_resource
            )
        if result.err:
            raise RuntimeError(f"xhtml2pdf không thể tạo báo cáo PDF ({result.err} lỗi).")
        print("-> PDF exported successfully with xhtml2pdf.")

    return str(output_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Xuất báo cáo PDF từ database thị trường và dữ liệu BCTC."
    )
    parser.add_argument("ticker", help="Mã cổ phiếu có trong data/market_data.db")
    parser.add_argument(
        "--csv",
        type=Path,
        help="CSV lịch sử tùy chọn để thay cho nguồn OHLCV trong database.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Đường dẫn PDF đầu ra (mặc định: outputs/<MÃ>_stock_report.pdf)",
    )
    parser.add_argument(
        "--analysis-json",
        type=Path,
        help="JSON đầu vào scoring với các object tech, fund, news.",
    )
    parser.add_argument(
        "--ai",
        action="store_true",
        help="Cho phép scoring gọi AI (cần API key phù hợp).",
    )
    args = parser.parse_args()

    report_data = load_stock_report_data(args.ticker, args.csv)
    if args.analysis_json:
        with args.analysis_json.open(encoding="utf-8") as analysis_file:
            analysis_data = json.load(analysis_file)
        report_data = apply_scoring_to_report(
            report_data, analysis_data, use_ai=args.ai
        )
    output_path = args.output or Path("outputs") / f"{args.ticker.strip().upper()}_stock_report.pdf"
    generated_path = generate_pdf(report_data, output_path)
    print(f"PDF saved to: {generated_path}")