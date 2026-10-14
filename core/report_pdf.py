# core/report_pdf.py
import argparse
import base64
import html
from io import BytesIO
import json
import math
import os
import re
from datetime import date, datetime
from pathlib import Path
import tempfile
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET

import jinja2
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

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
    from .company_metadata import load_or_fetch_company_metadata
    from .news import fetch_stock_news
    from .scoring import get_recommendation, to_report_context
else:
    from market_data import (
        is_vietnam_trading_session,
        load_price_history,
        load_realtime_quote,
        load_symbol_metadata,
    )
    from company_metadata import load_or_fetch_company_metadata
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


def _price_to_vnd(value):
    if isinstance(value, (int, float)):
        price = float(value)
    else:
        text = str(value).replace("đ", "").replace(" ", "").strip()
        if not text or text in {"-", "N/A", "None"}:
            return None
        if "." in text and "," in text:
            text = text.replace(".", "").replace(",", ".")
        elif "," in text:
            decimal_places = len(text.rsplit(",", 1)[-1])
            text = text.replace(",", "." if decimal_places <= 2 else "")
        elif "." in text:
            decimal_places = len(text.rsplit(".", 1)[-1])
            if decimal_places == 3:
                text = text.replace(".", "")
        try:
            price = float(text)
        except ValueError:
            return None

    if 0 < price < 1000:
        price *= 1000
    return price if price > 0 else None


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
                ((
                    "net_profit",
                    "net_profit_atttributable_to_the_equity_holders_of_the_bank",
                    "net_profit_attributable_to_the_equity_holders_of_the_bank",
                ), "Lợi nhuận sau thuế", "billions"),
            ],
            "balance_sheet": [
                (("total_assets",), "Tổng tài sản", "billions"),
                (("total_liabilities",), "Nợ phải trả", "billions"),
                (
                    (
                        "owners_equity_2",
                        "owners_equity_3",
                        "owners_equity",
                        "owner_equity",
                        "total_equity",
                        "capital_and_reserves",
                        "equity",
                    ),
                    "Vốn chủ sở hữu",
                    "billions",
                ),
                (("loans_advances_and_finance_leases_to_customers", "loans_advances_and_finance_leases_to_customers_2"), "Cho vay khách hàng", "billions"),
                (("deposits_from_customers",), "Tiền gửi khách hàng", "billions"),
                (("demand_deposits", "deposits_from_customers_demand"), "CASA / tiền gửi không kỳ hạn", "billions"),
            ],
            "cash_flow": [
                (("operating_cash_flow",), "Lưu chuyển tiền thuần từ hoạt động kinh doanh", "billions"),
                (("investing_cash_flow",), "Lưu chuyển tiền thuần từ hoạt động đầu tư", "billions"),
                (("financing_cash_flow",), "Lưu chuyển tiền thuần từ hoạt động tài chính", "billions"),
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
                ((
                    "net_profit",
                    "net_profit_after_tax",
                    "profit_after_tax",
                    "profit_after_tax_for_shareholders_of_parent_company",
                    "profit_after_tax_for_shareholders_of_the_parent_company",
                ), "Lợi nhuận sau thuế hợp nhất", "billions"),
            ],
            "balance_sheet": [
                (("total_assets",), "Tổng tài sản", "billions"),
                (("total_liabilities",), "Tổng nợ phải trả", "billions"),
                (("owners_equity_2", "owners_equity_3", "owners_equity", "equity"), "Vốn chủ sở hữu", "billions"),
                (("short_term_borrowings_and_financial_leases", "short_term_borrowings"), "Nợ vay tài chính ngắn hạn", "billions"),
                (("long_term_borrowings_and_financial_leases", "long_term_borrowings"), "Nợ vay tài chính dài hạn", "billions"),
            ],
            "cash_flow": [
                (("operating_cash_flow",), "Lưu chuyển tiền thuần từ hoạt động kinh doanh", "billions"),
                (("investing_cash_flow",), "Lưu chuyển tiền thuần từ hoạt động đầu tư", "billions"),
                (("financing_cash_flow",), "Lưu chuyển tiền thuần từ hoạt động tài chính", "billions"),
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
                bank_company
                and sheet_name == "balance_sheet"
                and item_ids == ("total_liabilities",)
            ):
                assets, _ = statement_series("balance_sheet", ("total_assets",))
                equity, _ = statement_series(
                    "balance_sheet",
                    (
                        "owners_equity_2",
                        "owners_equity_3",
                        "owners_equity",
                        "owner_equity",
                        "total_equity",
                        "capital_and_reserves",
                        "equity",
                    ),
                )
                if assets is not None and equity is not None:
                    derived_liabilities = assets.reindex(years) - equity.reindex(years)
                    raw_values = (
                        raw_values.reindex(years).combine_first(derived_liabilities)
                        if raw_values is not None
                        else derived_liabilities
                    )
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
            if sheet_name == "cash_flow":
                formatted_values.reverse()
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
            "profit_after_tax_for_shareholders_of_parent_company",
            "profit_after_tax_for_shareholders_of_the_parent_company",
            "net_income",
            "net_profit_atttributable_to_the_equity_holders_of_the_bank",
        ),
    )
    eps_series, _ = statement_series("ratio", ("trailing_eps",))
    bvps_series, _ = statement_series(
        "ratio", ("book_value_per_share_bvps",)
    )
    equity_series, _ = statement_series(
        "balance_sheet",
        (
            "owners_equity_2",
            "owners_equity_3",
            "owners_equity",
            "owner_equity",
            "total_equity",
            "capital_and_reserves",
            "equity",
        ),
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
    operating_cash_flow, _ = statement_series(
        "cash_flow", ("operating_cash_flow",)
    )
    if operating_cash_flow is not None:
        latest_cash_flow = operating_cash_flow.get(latest_year)
        if pd.notna(latest_cash_flow):
            ratio_values["operating_cash_flow"] = (
                latest_cash_flow / 1_000_000_000
            )
    if bank_company:
        revenue_series, income_label = statement_series(
            "income_statement", ("net_interest_income",)
        )
        income_label = "Thu nhập lãi thuần"
        profit_series, _ = statement_series(
            "income_statement",
            (
                "net_profit",
                "net_profit_atttributable_to_the_equity_holders_of_the_bank",
                "net_profit_attributable_to_the_equity_holders_of_the_bank",
            ),
        )
    historical_pe, _ = statement_series("ratio", ("pe_ratio",))
    historical_pb, _ = statement_series("ratio", ("pb_ratio",))
    historical_eps, _ = statement_series("ratio", ("trailing_eps",))
    historical_bvps, _ = statement_series(
        "ratio", ("book_value_per_share_bvps",)
    )
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
            "EPS": (
                [float(value) if pd.notna(value) else None for value in historical_eps]
                if historical_eps is not None
                else []
            ),
            "BVPS": (
                [float(value) if pd.notna(value) else None for value in historical_bvps]
                if historical_bvps is not None
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
    """
    Load report context, market history/quotes, and financial statements.
    """
    ticker = str(ticker).strip().upper()
    if not ticker:
        raise ValueError("Vui lòng nhập mã cổ phiếu.")

    report_data = {}
    project_root = Path(__file__).resolve().parent.parent
    for json_path in (
        project_root / "data" / f"{ticker}.json",
        project_root / "data" / f"sample_{ticker}.json",
        project_root / "data" / f"sample_context_{ticker}.json",
        project_root / "data" / f"sample_output_{ticker}.json",
    ):
        if not json_path.is_file():
            continue
        try:
            with json_path.open(encoding="utf-8") as json_file:
                payload = json.load(json_file)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            report_data.update(payload)

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
        price_history_source = str(csv_path)
    else:
        history = load_price_history(ticker, database_path=database_path)
        price_history_source = "data/market_data.db: historical_ohlcv + technical_indicators"
    price_history_error = history.attrs.get("refresh_error")
    if history.empty:
        raise ValueError(f"Mã {ticker} không có dữ liệu giá hợp lệ.")

    financial_path = (
        Path(financial_path)
        if financial_path is not None
        else project_root / "data" / "processed" / "tv2_financial_data.xlsx"
    )
    if not financial_path.is_absolute():
        financial_path = project_root / financial_path
    financial_error = None
    try:
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
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        financial_years = []
        financial_sections = []
        financial_ratios = {}
        financial_chart = ""
        financial_chart_data = {}
        financial_source = None
        financial_error = str(exc)

    latest = history.iloc[-1]
    latest_date = pd.to_datetime(latest["date"])
    market_open = is_vietnam_trading_session()
    quote_error = None
    quote = None
    quote_database = database_path or project_root / "data" / "market_data.db"
    try:
        quote = load_realtime_quote(ticker, database_path=quote_database)
    except RuntimeError as exc:
        quote_error = str(exc)

    quote_price = _score_value(quote.get("price")) if quote else None
    waiting_for_realtime = market_open and quote_price is None
    close = quote_price if quote_price is not None else _score_value(latest["close"])
    current_price = (
        "Chờ realtime"
        if waiting_for_realtime
        else _format_price(close)
        if close is not None
        else "N/A"
    )
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
    if quote:
        price_source = f"{quote_source} lúc {quote_date}; đơn vị đồng/cổ phiếu"
    elif waiting_for_realtime:
        price_source = (
            "Đang trong giờ giao dịch nhưng chưa có quote realtime; "
            "không hiển thị giá lịch sử thay thế"
        )
    else:
        price_source = (
            f"Giá đóng cửa lịch sử {latest_date:%d/%m/%Y}; ngoài giờ giao dịch"
        )

    close_series = pd.to_numeric(history["close"], errors="coerce")
    stored_indicators = {
        column: pd.to_numeric(history[column], errors="coerce")
        for column in ("MA20", "MA50", "RSI14", "MACD", "MACD_signal")
        if column in history
    }
    history["MA10"] = close_series.rolling(10, min_periods=10).mean()
    history["MA20"] = close_series.rolling(20, min_periods=20).mean()
    history["MA50"] = close_series.rolling(50, min_periods=50).mean()
    history["MA200"] = close_series.rolling(200, min_periods=200).mean()

    price_change_series = close_series.pct_change()
    change = close_series.diff()
    average_gain = change.clip(lower=0).ewm(
        alpha=1 / 14, min_periods=14, adjust=False
    ).mean()
    average_loss = -change.clip(upper=0).ewm(
        alpha=1 / 14, min_periods=14, adjust=False
    ).mean()
    relative_strength = average_gain / average_loss.replace(0, float("nan"))
    rsi_series = 100 - 100 / (1 + relative_strength)
    rsi_series = rsi_series.mask(
        average_loss.eq(0) & average_gain.gt(0), 100
    ).mask(average_loss.eq(0) & average_gain.eq(0), 50)
    history["RSI14"] = rsi_series

    ema_fast = close_series.ewm(span=12, min_periods=12, adjust=False).mean()
    ema_slow = close_series.ewm(span=26, min_periods=26, adjust=False).mean()
    history["MACD"] = ema_fast - ema_slow
    history["MACD_signal"] = history["MACD"].ewm(
        span=9, min_periods=9, adjust=False
    ).mean()
    for column, stored_values in stored_indicators.items():
        history[column] = history[column].fillna(stored_values)

    history["volume"] = pd.to_numeric(history["volume"], errors="coerce")
    previous_volume_average = history["volume"].rolling(20, min_periods=20).mean().shift(1)
    history["volume_ratio"] = history["volume"] / previous_volume_average.replace(0, float("nan"))
    history["prior_20d_high"] = (
        pd.to_numeric(history["high"], errors="coerce")
        .rolling(20, min_periods=20)
        .max()
        .shift(1)
    )
    history["support_60d"] = (
        pd.to_numeric(history["low"], errors="coerce")
        .rolling(60, min_periods=60)
        .min()
        .shift(1)
    )
    history["resistance_60d"] = (
        pd.to_numeric(history["high"], errors="coerce")
        .rolling(60, min_periods=60)
        .max()
        .shift(1)
    )
    latest = history.iloc[-1]
    previous = history.iloc[-2] if len(history) > 1 else None
    rsi = latest.get("RSI14")
    signal_close = _score_value(latest["close"])
    support_60d = _score_value(latest.get("support_60d"))
    resistance_60d = _score_value(latest.get("resistance_60d"))
    support_distance_pct = (
        signal_close / support_60d - 1
        if signal_close and support_60d and support_60d > 0
        else None
    )
    resistance_distance_pct = (
        resistance_60d / signal_close - 1
        if signal_close and resistance_60d and resistance_60d > 0
        else None
    )
    if previous is not None and pd.notna(latest.get("MA50")) and pd.notna(
        latest.get("MA200")
    ) and pd.notna(previous.get("MA50")) and pd.notna(previous.get("MA200")):
        if previous["MA50"] <= previous["MA200"] and latest["MA50"] > latest["MA200"]:
            ma50_ma200_cross = "golden_cross"
        elif previous["MA50"] >= previous["MA200"] and latest["MA50"] < latest["MA200"]:
            ma50_ma200_cross = "death_cross"
        else:
            ma50_ma200_cross = "none"
    else:
        ma50_ma200_cross = None

    def metric_value(value, suffix=""):
        numeric_value = pd.to_numeric(value, errors="coerce")
        return (
            f"{numeric_value:,.2f}{suffix}"
            if pd.notna(numeric_value)
            else "N/A"
        )

    volume_20d = pd.to_numeric(history["volume"], errors="coerce").dropna().tail(20)
    recent_history = history.loc[
        history["date"] >= latest_date - pd.Timedelta(days=365)
    ]

    financial_metrics = dict(report_data.get("fundamental_metrics") or {})
    for source_key, target_key in (
        ("pe_ratio", "pe"),
        ("pb_ratio", "pb"),
        ("trailing_eps", "eps"),
        ("book_value_per_share_bvps", "bvps"),
        ("roe", "roe"),
        ("roa", "roa"),
    ):
        value = financial_ratios.get(source_key)
        if value is not None and pd.notna(value):
            financial_metrics[target_key] = float(value)
    beta_value = financial_ratios.get("beta")
    if beta_value is None or pd.isna(beta_value):
        beta_value = financial_metrics.get("beta", report_data.get("beta"))
    if beta_value is not None and pd.notna(pd.to_numeric(beta_value, errors="coerce")):
        beta_value = float(beta_value)
        financial_metrics["beta"] = beta_value
        report_data["beta"] = f"{beta_value:.2f}"

    existing_fund = report_data.get("fund")
    fundamental_data = dict(existing_fund) if isinstance(existing_fund, dict) else {}
    existing_tech = report_data.get("tech")
    technical_data = dict(existing_tech) if isinstance(existing_tech, dict) else {}
    for source_key, target_key in (
        ("pe_ratio", "PE"),
        ("pb_ratio", "PB"),
        ("trailing_eps", "EPS"),
        ("book_value_per_share_bvps", "BVPS"),
        ("roe", "ROE"),
        ("roa", "ROA"),
    ):
        value = financial_ratios.get(source_key)
        if value is not None and pd.notna(value):
            normalized_value = float(value)
            if target_key in {"ROE", "ROA"}:
                normalized_value /= 100
            fundamental_data[target_key] = normalized_value
    for source_key, target_key in (
        ("revenue_growth", "revenue_growth"),
        ("eps_growth", "EPS_growth"),
        ("profit_growth", "profit_growth"),
        ("debt_to_equity", "debt_to_equity"),
        ("current_ratio", "current_ratio"),
        ("operating_cash_flow", "operating_cash_flow"),
    ):
        value = financial_ratios.get(source_key)
        if value is not None and pd.notna(value):
            fundamental_data[target_key] = float(value)
    for source_key in (
        "gross_margin",
        "net_margin",
        "net_interest_margin_nim",
        "npl",
        "loan_loss_coverage_ratio",
        "capital_adequacy_ratio",
    ):
        value = financial_ratios.get(source_key)
        if value is not None and pd.notna(value):
            target_key = (
                "net_interest_margin"
                if source_key == "net_interest_margin_nim"
                else source_key
            )
            fundamental_data[target_key] = float(value) / 100
    fundamental_data["valuation_type"] = financial_chart_data.get(
        "company_type", "non-bank"
    )
    historical_valuation = financial_chart_data.get("historical_valuation", {})
    fundamental_data["historical_PE"] = historical_valuation.get("PE", [])
    fundamental_data["historical_PB"] = historical_valuation.get("PB", [])
    fundamental_data["historical_EPS"] = historical_valuation.get("EPS", [])
    fundamental_data["historical_BVPS"] = historical_valuation.get("BVPS", [])
    net_profit_by_year = financial_chart_data.get("net_profit", {})
    if net_profit_by_year:
        latest_financial_year = str(max(map(int, net_profit_by_year)))
        fundamental_data["net_profit"] = net_profit_by_year[latest_financial_year]
        fundamental_data["net_profit_year"] = latest_financial_year

    news_feed = fetch_stock_news(ticker, limit=8, period="d")
    report_data.update({
        "ticker": ticker,
        "current_price": current_price,
        "price_source": price_source,
        "data_as_of": quote_date or latest_date.strftime("%d/%m/%Y"),
        "price_history": history,
        "price_history_source": price_history_source,
        "price_history_error": price_history_error,
        "price_chart": _price_chart_data_uri(
            history, ticker, close if quote else None
        ),
        "technical_metrics": [
            {"label": "Giá gần nhất", "value": current_price},
            {"label": "Giá đóng cửa phiên", "value": metric_value(signal_close, " nghìn đ")},
            {"label": "MA10", "value": metric_value(latest.get("MA10"), " nghìn đ")},
            {"label": "MA20", "value": metric_value(latest.get("MA20"), " nghìn đ")},
            {"label": "MA50", "value": metric_value(latest.get("MA50"), " nghìn đ")},
            {"label": "MA200", "value": metric_value(latest.get("MA200"), " nghìn đ")},
            {"label": "RSI (14)", "value": metric_value(rsi)},
            {"label": "MACD", "value": metric_value(latest.get("MACD"))},
            {"label": "MACD Signal", "value": metric_value(latest.get("MACD_signal"))},
            {"label": "Khối lượng / TB20 phiên", "value": metric_value(latest.get("volume_ratio"), "x")},
        ],
        "price_52w_range": (
            f"{_format_price(pd.to_numeric(recent_history['low'], errors='coerce').min())} – "
            f"{_format_price(pd.to_numeric(recent_history['high'], errors='coerce').max())}"
            if {"low", "high"}.issubset(recent_history.columns)
            else "N/A"
        ),
        "avg_volume_20d": (
            f"{volume_20d.mean() / 1_000_000:,.2f} triệu CP"
            if not volume_20d.empty
            else "N/A"
        ),
        "financial_years": financial_years or report_data.get("financial_years", []),
        "financial_sections": financial_sections or report_data.get("financial_sections", []),
        "financial_chart": financial_chart,
        "financial_chart_data": financial_chart_data,
        "financial_ratios": financial_ratios,
        "fundamental_metrics": financial_metrics,
        "financial_data_source": (
            "Báo cáo tài chính đã lưu" if financial_source else None
        ),
        "financial_data_error": financial_error,
        "quote_error": quote_error,
        "tech": {
            **technical_data,
            "symbol": ticker,
            "close": close if close is not None else _score_value(latest["close"]),
            "signal_close": signal_close,
            "current_price": current_price,
            "MA10": _score_value(latest.get("MA10")),
            "MA20": _score_value(latest.get("MA20")),
            "MA50": _score_value(latest.get("MA50")),
            "MA200": _score_value(latest.get("MA200")),
            "RSI": _score_value(rsi),
            "MACD": _score_value(latest.get("MACD")),
            "MACD_signal": _score_value(latest.get("MACD_signal")),
            "MACD_hist": (
                _score_value(latest.get("MACD") - latest.get("MACD_signal"))
                if pd.notna(latest.get("MACD")) and pd.notna(latest.get("MACD_signal"))
                else None
            ),
            "ma50_ma200_cross": ma50_ma200_cross,
            "volume_ratio": _score_value(latest.get("volume_ratio")),
            "price_change_pct": (
                _score_value(price_change_series.iloc[-1])
                if len(price_change_series) and pd.notna(price_change_series.iloc[-1])
                else None
            ),
            "breakout_20d": (
                bool(latest["close"] > latest["prior_20d_high"])
                if pd.notna(latest.get("prior_20d_high"))
                else None
            ),
            "support_60d": support_60d,
            "resistance_60d": resistance_60d,
            "support_distance_pct": support_distance_pct,
            "resistance_distance_pct": resistance_distance_pct,
            "signal_as_of": latest_date.strftime("%Y-%m-%d"),
            "as_of": quote_timestamp or latest_date.strftime("%Y-%m-%d"),
            "source": quote_source or price_history_source,
        },
        "fund": {
            **fundamental_data,
            "symbol": ticker,
            "source": financial_source or fundamental_data.get("source"),
        },
        "news_feed": news_feed,
        "news_list": news_feed.get("headlines", []),
    })
    if financial_sections:
        report_data["financial_ratios_table"] = [
            {"label": row["label"], "values": row["values"]}
            for section in financial_sections
            if section.get("title") == "CHỈ SỐ TÀI CHÍNH"
            for row in section.get("rows", [])
        ]

    report_data["report_date"] = datetime.now().strftime("%d/%m/%Y")
    if not report_data.get("company_name") or report_data["company_name"] == ticker:
        report_data["company_name"] = f"Công ty Cổ phần {ticker}"
    symbol_metadata = load_symbol_metadata(ticker)
    metadata, metadata_error = load_or_fetch_company_metadata(
        ticker, exchange=symbol_metadata.get("exchange")
    )
    report_data = _apply_company_metadata(report_data, metadata)
    if (
        str(report_data.get("exchange", "")).strip().casefold()
        in {"", "n/a", "none"}
        and symbol_metadata.get("exchange")
    ):
        report_data["exchange"] = symbol_metadata["exchange"]
    if metadata_error:
        report_data["company_metadata_error"] = metadata_error
    return report_data


def refresh_stock_report_quote(report_data, database_path=None):
    """
    Refresh market-dependent report fields without replacing financial data.
    """
    if not isinstance(report_data, dict):
        return report_data

    refreshed = dict(report_data)
    ticker = str(refreshed.get("ticker", "")).strip().upper()
    if not ticker:
        raise ValueError("Báo cáo phải có mã cổ phiếu.")

    market_open = is_vietnam_trading_session()
    quote = None
    try:
        quote = load_realtime_quote(ticker, database_path=database_path)
    except RuntimeError as exc:
        refreshed["quote_error"] = str(exc)

    history = refreshed.get("price_history")
    latest = None
    if isinstance(history, pd.DataFrame) and not history.empty:
        latest = history.sort_values("date").iloc[-1]

    if quote and quote.get("price") is not None:
        current_price = _format_price(_score_value(quote["price"]))
        quote_timestamp = quote.get("timestamp")
        if quote_timestamp:
            try:
                quote_date = datetime.fromisoformat(
                    quote_timestamp.replace("Z", "+00:00")
                ).astimezone().strftime("%d/%m/%Y %H:%M")
            except ValueError:
                quote_date = quote_timestamp
        else:
            quote_date = None
        refreshed["current_price"] = current_price
        refreshed["price_source"] = (
            f"{quote.get('source', 'Nguồn realtime')}"
            + (f" lúc {quote_date}" if quote_date else "")
            + "; đơn vị đồng/cổ phiếu"
        )
        refreshed["data_as_of"] = quote_date or refreshed.get("data_as_of")
        refreshed["quote_error"] = None
        if isinstance(refreshed.get("tech"), dict):
            refreshed["tech"] = {
                **refreshed["tech"],
                "close": _score_value(quote["price"]),
                "current_price": current_price,
                "as_of": quote_timestamp or refreshed["tech"].get("as_of"),
                "source": quote.get("source"),
            }
    elif market_open:
        current_price = "Chờ realtime"
        refreshed["current_price"] = current_price
        refreshed["data_as_of"] = "Chờ realtime"
        refreshed["price_source"] = (
            "Đang trong giờ giao dịch nhưng chưa có quote realtime; "
            "không hiển thị giá lịch sử thay thế"
        )
    elif latest is not None:
        close = _score_value(latest.get("close"))
        current_price = _format_price(close)
        latest_date = pd.to_datetime(latest["date"])
        refreshed["current_price"] = current_price
        refreshed["data_as_of"] = latest_date.strftime("%d/%m/%Y")
        refreshed["price_source"] = (
            f"Giá đóng cửa lịch sử {latest_date:%d/%m/%Y}; ngoài giờ giao dịch"
        )
    else:
        current_price = refreshed.get("current_price", "N/A")

    for metric in refreshed.get("technical_metrics", []):
        if isinstance(metric, dict) and metric.get("label") == "Giá gần nhất":
            metric["value"] = current_price
    if isinstance(refreshed.get("tech"), dict):
        refreshed["tech"] = {
            **refreshed["tech"],
            "current_price": current_price,
        }
    target_data = refreshed.get("valuation") or refreshed.get("valuation_summary") or {}
    target_price_raw = (
        target_data.get("target_price")
        if isinstance(target_data, dict)
        else None
    ) or refreshed.get("target_price")
    target_price_float = _price_to_vnd(target_price_raw)
    if target_price_float is not None:
        refreshed["target_price"] = (
            f"{target_price_float:,.0f} đ".replace(",", ".")
        )
    current_price_float = _price_to_vnd(current_price)
    if current_price_float and target_price_float:
        upside_percent = (target_price_float - current_price_float) / current_price_float * 100
        refreshed["upside"] = f"{'+' if upside_percent > 0 else ''}{upside_percent:.1f}%"
    range_values = refreshed.get("target_price_range_values")
    if (
        current_price_float
        and isinstance(range_values, (list, tuple))
        and len(range_values) == 2
    ):
        range_upside = [
            (float(target) - current_price_float) / current_price_float * 100
            for target in range_values
        ]
        refreshed["upside_range"] = (
            f"{range_upside[0]:+.1f}% đến {range_upside[1]:+.1f}%"
        )
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
    report["report_date"] = (
        scoring_context.get("report_date")
        or report_data.get("report_date")
        or datetime.now().strftime("%d/%m/%Y")
    )
    
    # --- ĐÃ SỬA: LÙI ĐẦU DÒNG CHUẨN VÀ BỌC AN TOÀN .GET() ---
# SỬA ĐOẠN NÀY TRONG report_pdf.py:
    for key in (
        "recommendation",
        "total_score",
        "scoring_weights",
        "scores",
        "technical_score",
        "fundamental_score",
        "technical_coverage",
        "decision_note",
        "decision_rule",
        "target_price",
        "target_price_range",
        "target_price_range_values",
        "upside",
        "upside_range",
        "target_method",
        "target_confidence",
        "valuation_reference_pe",
        "valuation_current_pe",
        "valuation_history_count",
        "valuation_pe_low",
        "valuation_pe_high",
        "valuation_eps",
        "valuation_eps_count",
        "valuation_eps_normalized",
        "valuation_eps_basis",
        "valuation_pb_reference",
        "valuation_pb_count",
        "valuation_pb_target",
        "valuation_warning",
        "valuation_methods",
        "valuation_summary",
        "investment_thesis",
        "ai_summary",
        "ai_generated",
        "investment_points",
        "key_risks",
    ):
        if isinstance(scoring_context, dict) and scoring_context.get(key) is not None:
            report[key] = scoring_context.get(key)
        else:
            # Nếu key là các danh sách (points/risks), gán [] thay vì None
            if key in ("investment_points", "key_risks", "valuation_methods"):
                report[key] = report_data.get(key) or []
            else:
                report[key] = report_data.get(key) or ""
    report["score_breakdown"] = recommendation.get("score_breakdown", {})
    technical_factors = report["score_breakdown"].get("technical_factors", [])
    technical_factor_groups = {}
    for factor in technical_factors:
        category = factor.get("category", "Khác")
        technical_factor_groups.setdefault(category, []).append(factor)
    report["technical_factor_groups"] = [
        {
            "category": category,
            "weight": sum(factor.get("weight", 0) for factor in factors),
            "factors": factors,
        }
        for category, factors in technical_factor_groups.items()
    ]
    report["technical_metrics"] = report_data.get("technical_metrics", [])
    report["financial_sections"] = report_data.get("financial_sections", [])
    report["financial_years"] = report_data.get("financial_years", [])
    report["financial_error"] = report_data.get("financial_data_error") or ""
    report["technical_as_of"] = (
        tech.get("signal_as_of") or tech.get("as_of") or report_data.get("data_as_of")
    )
    report["data_as_of"] = report_data.get("data_as_of") or report_data.get("report_date")
    report["price_source"] = report_data.get("price_source") or tech.get("source") or ""
    report["price_chart"] = report_data.get("price_chart") or ""
    report["price_52w_range"] = report_data.get("price_52w_range") or "N/A"
    report["avg_volume_20d"] = report_data.get("avg_volume_20d") or "N/A"
    report["market_cap"] = report_data.get("market_cap") or "N/A"
    report["shares_outstanding"] = report_data.get("shares_outstanding") or "N/A"
    report["foreign_ownership"] = report_data.get("foreign_ownership") or "N/A"
    report["foreign_ownership_limit"] = report_data.get("foreign_ownership_limit") or ""
    report["beta"] = report_data.get("beta") or "N/A"
    news_feed = report_data.get("news_feed") or {}
    report["news_list"] = (
        report_data.get("news_list")
        or news_feed.get("headlines")
        or []
    )
    report["news_error"] = (
        news_feed.get("error")
        if news_feed.get("status") == "error"
        else ""
    )
    report["news_source"] = news_feed.get("source") or "Nguồn tìm kiếm chưa xác định"
    report["news_status"] = news_feed.get("status") or "unknown"
    report["news_period"] = {
        "d": "24 giờ",
        "w": "7 ngày",
        "m": "31 ngày",
        "y": "1 năm",
    }.get(news_feed.get("period_used"), "chưa xác định")

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


def _rasterize_svg_data_uri(data_uri, core_dir):
    match = re.fullmatch(
        r"data:image/svg\+xml;base64,([A-Za-z0-9+/=]+)",
        data_uri.strip(),
        flags=re.IGNORECASE,
    )
    if match is None:
        raise ValueError("Biểu đồ không có định dạng SVG base64 hợp lệ.")

    try:
        svg = base64.b64decode(match.group(1), validate=True).decode("utf-8")
        root = ET.fromstring(svg)
    except (UnicodeDecodeError, ET.ParseError, ValueError) as exc:
        raise ValueError(f"Không đọc được biểu đồ SVG: {exc}") from exc

    view_box = root.attrib.get("viewBox", "").replace(",", " ").split()
    if len(view_box) != 4:
        raise ValueError("Biểu đồ SVG thiếu viewBox hợp lệ.")
    try:
        view_x, view_y, view_width, view_height = map(float, view_box)
    except ValueError as exc:
        raise ValueError("Kích thước viewBox của biểu đồ không hợp lệ.") from exc
    if view_width <= 0 or view_height <= 0:
        raise ValueError("Kích thước biểu đồ SVG phải lớn hơn 0.")

    scale = 2
    image = Image.new(
        "RGB",
        (round(view_width * scale), round(view_height * scale)),
        "white",
    )
    draw = ImageDraw.Draw(image)
    font_path = core_dir.parent / "assets" / "fonts" / "arial.ttf"
    bold_font_path = core_dir.parent / "assets" / "fonts" / "arialbd.ttf"

    def point(value):
        return (
            round((float(value[0]) - view_x) * scale),
            round((float(value[1]) - view_y) * scale),
        )

    def dimension(value, reference):
        value = str(value)
        if value.endswith("%"):
            return round(float(value[:-1]) * reference / 100)
        return round(float(value) * scale)

    def color(value, default=None):
        if not value or value == "none":
            return default
        if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            return value
        if re.fullmatch(r"#[0-9a-fA-F]{3}", value):
            return value
        return default

    def dashed_line(points, fill, width, dash_pattern):
        if not dash_pattern:
            draw.line(points, fill=fill, width=width)
            return
        dash_values = [
            max(1, round(float(value) * scale))
            for value in re.findall(r"\d+(?:\.\d+)?", dash_pattern)
        ]
        if not dash_values:
            draw.line(points, fill=fill, width=width)
            return
        pattern_index = 0
        remaining = dash_values[0]
        draw_segment = True
        for start, end in zip(points, points[1:]):
            dx = end[0] - start[0]
            dy = end[1] - start[1]
            length = math.hypot(dx, dy)
            if length == 0:
                continue
            position = 0.0
            while position < length:
                segment_length = min(remaining, length - position)
                if draw_segment:
                    start_ratio = position / length
                    end_ratio = (position + segment_length) / length
                    draw.line(
                        (
                            (
                                round(start[0] + dx * start_ratio),
                                round(start[1] + dy * start_ratio),
                            ),
                            (
                                round(start[0] + dx * end_ratio),
                                round(start[1] + dy * end_ratio),
                            ),
                        ),
                        fill=fill,
                        width=width,
                    )
                position += segment_length
                remaining -= segment_length
                if remaining <= 0:
                    pattern_index = (pattern_index + 1) % len(dash_values)
                    remaining = dash_values[pattern_index]
                    draw_segment = not draw_segment

    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        attributes = element.attrib
        fill = color(attributes.get("fill"), "black")
        stroke = color(attributes.get("stroke"))
        stroke_width = max(
            1, round(float(attributes.get("stroke-width", 1)) * scale)
        )
        dash_pattern = attributes.get("stroke-dasharray")

        if tag == "rect":
            x, y = point(
                (attributes.get("x", 0), attributes.get("y", 0))
            )
            width = dimension(attributes.get("width", 0), view_width)
            height = dimension(attributes.get("height", 0), view_height)
            if width < 1 or height < 1:
                continue
            draw.rectangle(
                (x, y, x + width, y + height),
                fill=fill if attributes.get("fill") != "none" else None,
                outline=stroke,
                width=stroke_width if stroke else 1,
            )
        elif tag == "line":
            start = point((attributes.get("x1", 0), attributes.get("y1", 0)))
            end = point((attributes.get("x2", 0), attributes.get("y2", 0)))
            if stroke:
                dashed_line([start, end], stroke, stroke_width, dash_pattern)
        elif tag == "circle":
            center_x, center_y = point(
                (attributes.get("cx", 0), attributes.get("cy", 0))
            )
            radius = max(1, round(float(attributes.get("r", 0)) * scale))
            draw.ellipse(
                (
                    center_x - radius,
                    center_y - radius,
                    center_x + radius,
                    center_y + radius,
                ),
                fill=fill if attributes.get("fill") != "none" else None,
                outline=stroke,
                width=stroke_width,
            )
        elif tag == "path":
            tokens = re.findall(
                r"[ML]|[-+]?(?:\d*\.)?\d+(?:[eE][-+]?\d+)?",
                attributes.get("d", ""),
            )
            values = []
            command = None
            for token in tokens:
                if token in {"M", "L"}:
                    command = token
                elif command:
                    values.append(float(token))
            points = [
                point((values[index], values[index + 1]))
                for index in range(0, len(values) - 1, 2)
            ]
            if stroke and len(points) > 1:
                dashed_line(points, stroke, stroke_width, dash_pattern)
        elif tag == "text":
            text = "".join(element.itertext())
            if not text:
                continue
            font_size = max(
                1, round(float(attributes.get("font-size", 10)) * scale)
            )
            font_file = (
                bold_font_path
                if attributes.get("font-weight", "").lower() in {"bold", "700", "800"}
                else font_path
            )
            try:
                font = ImageFont.truetype(str(font_file), font_size)
            except OSError:
                font = ImageFont.load_default(size=font_size)
            x, y = point((attributes.get("x", 0), attributes.get("y", 0)))
            anchor = (
                "ms"
                if attributes.get("text-anchor") == "middle"
                else "rs"
                if attributes.get("text-anchor") == "end"
                else "ls"
            )
            rotation = re.search(
                r"rotate\(\s*([-+]?\d+(?:\.\d+)?)"
                r"(?:[\s,]+([-+]?\d+(?:\.\d+)?)[\s,]+"
                r"([-+]?\d+(?:\.\d+)?))?\s*\)",
                attributes.get("transform", ""),
            )
            if rotation:
                angle = float(rotation.group(1))
                pivot = point(
                    (
                        rotation.group(2) or attributes.get("x", 0),
                        rotation.group(3) or attributes.get("y", 0),
                    )
                )
                text_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
                layer_draw = ImageDraw.Draw(text_layer)
                layer_draw.text(
                    (x, y), text, fill=fill, font=font, anchor=anchor
                )
                text_layer = text_layer.rotate(
                    -angle,
                    resample=Image.Resampling.BICUBIC,
                    center=pivot,
                )
                image.paste(text_layer, (0, 0), text_layer)
                draw = ImageDraw.Draw(image)
            else:
                draw.text(
                    (x, y),
                    text,
                    fill=fill,
                    font=font,
                    anchor=anchor,
                )

    png = BytesIO()
    image.save(png, format="PNG", optimize=True)
    encoded = base64.b64encode(png.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


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
    with tempfile.NamedTemporaryFile(
        dir=output_path.parent, suffix=".pdf", delete=False
    ) as temporary_file:
        temporary_output = Path(temporary_file.name)

    try:
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
                str(temporary_output),
                stylesheets=stylesheets,
            )
            print("-> PDF exported successfully with WeasyPrint.")

        except (ImportError, OSError):
            # Fallback xhtml2pdf dành riêng cho Windows khi thiếu GTK3
            from xhtml2pdf import pisa

            def rasterize_chart(match):
                image_tag = match.group(0)
                source_match = re.search(
                    r"\bsrc=[\"'](data:image/svg\+xml;base64,[^\"']+)[\"']",
                    image_tag,
                    flags=re.IGNORECASE,
                )
                if source_match is None:
                    return image_tag
                png_uri = _rasterize_svg_data_uri(source_match.group(1), core_dir)
                return image_tag[:source_match.start(1)] + png_uri + image_tag[source_match.end(1):]

            fallback_html = re.sub(
                r"<img\b[^>]*>",
                rasterize_chart,
                rendered_html,
                flags=re.IGNORECASE | re.DOTALL,
            )
            css_path = core_dir / "report_style.css"
            if css_path.exists():
                fallback_css = css_path.read_text(encoding="utf-8")
                fallback_css = re.sub(
                    r"display\s*:\s*(?:flex|grid)\s*;",
                    "display: block;",
                    fallback_css,
                    flags=re.IGNORECASE,
                )
                fallback_css = re.sub(
                    r"(?:break-inside|page-break-inside)\s*:\s*avoid\s*;",
                    "",
                    fallback_css,
                    flags=re.IGNORECASE,
                )
                fallback_css += """
                    .chart-frame { height: auto; }
                    .chart-frame img { display: block; width: 180mm; height: 114mm; }
                    .small-chart-frame img { display: block; width: 180mm; height: 135mm; }
                """
                fallback_stylesheet = f"<style>{fallback_css}</style>"
                fallback_html = re.sub(
                    r"<link\b(?=[^>]*\bhref=[\"']report_style\.css[\"'])[^>]*>",
                    lambda _: fallback_stylesheet,
                    fallback_html,
                    flags=re.IGNORECASE,
                )

            def resolve_resource(uri, _):
                parsed_uri = urlparse(uri)
                if parsed_uri.scheme:
                    return uri
                resource_path = Path(unquote(parsed_uri.path))
                if not resource_path.is_absolute():
                    resource_path = core_dir / resource_path
                return str(resource_path.resolve())

            def create_pdf(source):
                with temporary_output.open("wb") as pdf_file:
                    result = pisa.CreatePDF(
                        src=source,
                        dest=pdf_file,
                        encoding='utf-8',
                        path=str(core_dir),
                        link_callback=resolve_resource
                    )
                if result.err:
                    raise RuntimeError(
                        f"xhtml2pdf không thể tạo báo cáo PDF ({result.err} lỗi)."
                    )

            try:
                create_pdf(fallback_html)
            except AttributeError as exc:
                if "KeepTogether" not in str(exc) or "draw" not in str(exc):
                    raise
                print(
                    "[PDF] xhtml2pdf KeepTogether error; retrying without CSS."
                )
                plain_html = re.sub(
                    r"<style\b[^>]*>.*?</style>|<link\b[^>]*>",
                    "",
                    rendered_html,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                create_pdf(plain_html)
            print("-> PDF exported successfully with xhtml2pdf.")

        if not temporary_output.is_file() or temporary_output.stat().st_size == 0:
            raise RuntimeError("Renderer không tạo được file PDF.")
        with temporary_output.open("rb") as pdf_file:
            if pdf_file.read(5) != b"%PDF-":
                raise RuntimeError("Renderer tạo ra file không đúng định dạng PDF.")
        os.replace(temporary_output, output_path)
        return str(output_path)
    finally:
        if temporary_output.exists():
            temporary_output.unlink()

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