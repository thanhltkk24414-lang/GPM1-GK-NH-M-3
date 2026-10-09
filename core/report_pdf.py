# core/report_pdf.py
import argparse
import base64
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlparse

import jinja2
import pandas as pd


def _format_price(value):
    if pd.isna(value):
        return "—"
    return f"{value:,.2f}"


def _format_indicator(value, suffix=""):
    if pd.isna(value):
        return None
    return f"{value:,.2f}{suffix}"


def _price_chart_data_uri(history):
    prices = pd.to_numeric(history["close"], errors="coerce").dropna().tail(60)
    if len(prices) < 2:
        return ""

    width, height, padding = 800, 320, 24
    low, high = prices.min(), prices.max()
    spread = high - low or 1
    points = " ".join(
        f"{padding + index * (width - 2 * padding) / (len(prices) - 1):.1f},"
        f"{height - padding - (price - low) * (height - 2 * padding) / spread:.1f}"
        for index, price in enumerate(prices)
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">'
        f'<rect width="{width}" height="{height}" fill="#fbfdff"/>'
        f'<polyline points="{points}" fill="none" stroke="#2376b9" '
        'stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>'
        "</svg>"
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _load_financial_data(ticker, workbook_path):
    workbook_path = Path(workbook_path)
    if not workbook_path.is_absolute():
        workbook_path = Path(__file__).resolve().parent.parent / workbook_path

    sheets = pd.read_excel(workbook_path, sheet_name=None, engine="openpyxl")
    required_sheets = {"income_statement", "balance_sheet", "cash_flow", "ratio"}
    missing_sheets = required_sheets.difference(sheets)
    if missing_sheets:
        raise ValueError(
            f"Workbook BCTC thiếu sheet: {', '.join(sorted(missing_sheets))}"
        )

    for sheet_name in required_sheets:
        required_columns = {"ticker", "item", "item_id"}
        missing_columns = required_columns.difference(sheets[sheet_name].columns)
        if missing_columns:
            raise ValueError(
                f"Sheet {sheet_name} thiếu cột: {', '.join(sorted(missing_columns))}"
            )

    ratio_data = sheets["ratio"]
    ticker_mask = ratio_data["ticker"].astype("string").str.strip().str.upper().eq(ticker)
    ratio_data = ratio_data.loc[ticker_mask]
    if ratio_data.empty:
        return [], [], {}

    year_columns = [
        column
        for column in ratio_data.columns
        if str(column).isdigit()
    ]
    years = sorted(year_columns, key=int, reverse=True)
    if not years:
        raise ValueError(f"Workbook BCTC không có cột năm cho mã {ticker}.")

    selected_items = {
        "income_statement": [
            ("net_interest_income", "Thu nhập lãi thuần", "billions"),
            ("net_fee_and_commission_income", "Lãi thuần từ dịch vụ", "billions"),
            ("profit_before_tax", "Lợi nhuận trước thuế", "billions"),
            ("net_profit", "Lợi nhuận sau thuế", "billions"),
        ],
        "balance_sheet": [
            ("total_assets", "Tổng tài sản", "billions"),
            ("total_liabilities", "Tổng nợ phải trả", "billions"),
        ],
        "cash_flow": [
            ("operating_cash_flow", "Lưu chuyển tiền kinh doanh", "billions"),
            ("investing_cash_flow", "Lưu chuyển tiền đầu tư", "billions"),
            ("financing_cash_flow", "Lưu chuyển tiền tài chính", "billions"),
        ],
        "ratio": [
            ("trailing_eps", "EPS", "currency"),
            ("pe_ratio", "P/E", "multiple"),
            ("pb_ratio", "P/B", "multiple"),
            ("roe", "ROE", "percent"),
            ("roa", "ROA", "percent"),
            ("net_interest_margin_nim", "NIM", "percent"),
            ("beta", "Beta", "number"),
        ],
    }

    sections = []
    ratio_values = {}
    section_titles = {
        "income_statement": "KẾT QUẢ KINH DOANH (TỶ ĐỒNG)",
        "balance_sheet": "BẢNG CÂN ĐỐI KẾ TOÁN (TỶ ĐỒNG)",
        "cash_flow": "LƯU CHUYỂN TIỀN TỆ (TỶ ĐỒNG)",
        "ratio": "CHỈ SỐ TÀI CHÍNH",
    }
    for sheet_name, items in selected_items.items():
        sheet = sheets[sheet_name]
        matches_ticker = (
            sheet["ticker"].astype("string").str.strip().str.upper().eq(ticker)
        )
        ticker_sheet = sheet.loc[matches_ticker]
        rows = []
        for item_id, label, value_type in items:
            matches = ticker_sheet.loc[ticker_sheet["item_id"].eq(item_id)]
            if matches.empty:
                continue
            values = matches[years].apply(pd.to_numeric, errors="coerce")
            populated = values.notna().any(axis=1)
            if not populated.any():
                continue
            source_row = matches.loc[populated].iloc[-1]
            raw_values = source_row[years].apply(pd.to_numeric, errors="coerce")
            if value_type == "billions":
                formatted_values = [
                    f"{value / 1_000_000_000:,.1f}" if pd.notna(value) else "–"
                    for value in raw_values
                ]
            elif value_type == "currency":
                formatted_values = [
                    f"{value:,.0f} đ" if pd.notna(value) else "–"
                    for value in raw_values
                ]
            elif value_type == "percent":
                formatted_values = [
                    f"{value:.2f}%" if pd.notna(value) else "–"
                    for value in raw_values
                ]
            elif value_type == "number":
                formatted_values = [
                    f"{value:.2f}" if pd.notna(value) else "–"
                    for value in raw_values
                ]
            else:
                formatted_values = [
                    f"{value:.2f}x" if pd.notna(value) else "–"
                    for value in raw_values
                ]
            rows.append({"label": label, "values": formatted_values})
            if sheet_name == "ratio":
                ratio_values[item_id] = raw_values.iloc[0]

        if rows:
            sections.append({"title": section_titles[sheet_name], "rows": rows})

    return years, sections, ratio_values


def load_stock_report_data(ticker, csv_path=None, financial_path=None):
    """Build report-template data from the merged historical stock CSV."""
    project_root = Path(__file__).resolve().parent.parent
    csv_path = Path(csv_path) if csv_path else project_root / "output" / "stock_data.csv"
    if not csv_path.is_absolute():
        csv_path = project_root / csv_path

    ticker = str(ticker).strip().upper()
    if not ticker:
        raise ValueError("Vui lòng nhập mã cổ phiếu.")

    history_all = pd.read_csv(csv_path)
    required_columns = {"symbol", "date", "close", "high", "low", "volume"}
    missing_columns = required_columns.difference(history_all.columns)
    if missing_columns:
        raise ValueError(
            f"CSV thiếu các cột bắt buộc: {', '.join(sorted(missing_columns))}"
        )

    symbols = history_all["symbol"].astype("string").str.strip().str.upper()
    history = history_all.loc[symbols == ticker].copy()
    if history.empty:
        available = ", ".join(sorted(symbols.dropna().unique()))
        raise ValueError(
            f"Không tìm thấy mã {ticker} trong {csv_path}. Mã có sẵn: {available}"
        )

    history["date"] = pd.to_datetime(history["date"], errors="coerce")
    for column in ("close", "high", "low", "volume"):
        history[column] = pd.to_numeric(history[column], errors="coerce")
    history = history.dropna(subset=["date", "close"]).sort_values("date")
    if history.empty:
        raise ValueError(f"Mã {ticker} không có ngày giao dịch hoặc giá đóng cửa hợp lệ.")

    financial_path = (
        financial_path
        if financial_path is not None
        else project_root / "data" / "processed" / "tv2_financial_data.xlsx"
    )
    financial_years, financial_sections, financial_ratios = _load_financial_data(
        ticker, financial_path
    )

    latest = history.iloc[-1]
    latest_date = latest["date"]
    recent_history = history.loc[
        history["date"] >= latest_date - pd.Timedelta(days=365)
    ]
    volume_20d = history["volume"].dropna().tail(20)
    source = latest.get("source")
    source = str(source) if pd.notna(source) else "CSV stock_data.csv"
    sources = [f"{source} — output/stock_data.csv"]
    if financial_sections:
        sources.append(f"data/processed/{Path(financial_path).name}")
    company_name = latest.get("organ_name")
    company_name = str(company_name) if pd.notna(company_name) else ticker
    exchange = latest.get("exchange")
    exchange = str(exchange) if pd.notna(exchange) else "Chưa có dữ liệu"

    technical_points = [
        f"Giá đóng cửa ngày {latest_date:%d/%m/%Y}: "
        f"{_format_price(latest['close'])} nghìn đồng/cổ phiếu."
    ]
    for column, label in (("MA20", "MA20"), ("MA50", "MA50"), ("RSI14", "RSI 14")):
        value = pd.to_numeric(latest.get(column), errors="coerce")
        formatted = _format_indicator(value)
        if formatted is not None:
            technical_points.append(f"{label}: {formatted}.")

    moving_average = pd.to_numeric(latest.get("MA20"), errors="coerce")
    close = float(latest["close"])
    if pd.notna(moving_average):
        relation = "trên" if close >= moving_average else "dưới"
        technical_points.append(f"Giá đóng cửa đang {relation} MA20.")

    summary_parts = list(technical_points)
    for item_id, label, suffix in (
        ("roe", "ROE", "%"),
        ("pe_ratio", "P/E", "x"),
        ("pb_ratio", "P/B", "x"),
    ):
        value = financial_ratios.get(item_id)
        if value is not None and pd.notna(value):
            summary_parts.append(
                f"{label} năm {financial_years[0]}: {value:.2f}{suffix}."
            )

    return {
        "ticker": ticker,
        "company_name": company_name,
        "report_date": date.today().strftime("%d/%m/%Y"),
        "recommendation": "—",
        "target_price": "—",
        "upside": "—",
        "exchange": exchange,
        "industry": "Chưa có dữ liệu",
        "current_price": _format_price(close),
        "investment_horizon": "Dữ liệu lịch sử",
        "price_chart": _price_chart_data_uri(history),
        "price_source": (
            f"{source} — output/stock_data.csv; "
            "giá theo nghìn đồng/cổ phiếu"
        ),
        "market_cap": "—",
        "shares_outstanding": "—",
        "avg_volume_20d": (
            f"{volume_20d.mean() / 1_000_000:,.2f} triệu CP"
            if not volume_20d.empty
            else "—"
        ),
        "price_52w_range": (
            f"{_format_price(recent_history['low'].min())} – "
            f"{_format_price(recent_history['high'].max())}"
        ),
        "foreign_ownership": "—",
        "beta": _format_indicator(financial_ratios.get("beta")) or "—",
        "financial_years": financial_years,
        "financial_sections": financial_sections,
        "investment_thesis": (
            "Báo cáo tổng hợp dữ liệu giá lịch sử và chỉ báo kỹ thuật từ CSV. "
            + (
                "Số liệu tài chính được lấy từ workbook BCTC; "
                "các năm báo cáo được ghi theo nguồn dữ liệu."
                if financial_sections
                else f"Workbook BCTC hiện chưa có dữ liệu cho mã {ticker}."
            )
        ),
        "ai_summary": " ".join(summary_parts),
        "investment_points": technical_points[1:],
        "key_risks": [
            (
                "Workbook BCTC hiện chưa có dữ liệu cho mã này."
                if not financial_sections
                else "Một số chỉ tiêu định giá và thông tin doanh nghiệp chưa có trong dữ liệu đầu vào."
            ),
            "Dữ liệu giá lịch sử và chỉ báo kỹ thuật không đảm bảo kết quả trong tương lai.",
        ],
        "analyst_name": "Tổng hợp dữ liệu cổ phiếu",
        "analyst_contact": "",
        "data_sources": "; ".join(sources),
        "data_as_of": latest_date.strftime("%d/%m/%Y"),
    }


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
    # A fixed page height lets the absolute footer anchor to the full printed page.
    layout_css = """
    <style>
      .page {
        position: relative;
        height: 272mm;
      }
      .page-footer {
        position: absolute;
        bottom: 0;
      }
    </style>
    """
    head_end = rendered_html.lower().find("</head>")
    if head_end == -1:
        raise ValueError("Template báo cáo thiếu thẻ đóng </head>.")
    rendered_html = f"{rendered_html[:head_end]}{layout_css}{rendered_html[head_end:]}"

    # 3. Tiến hành xuất PDF
    try:
        # Ưu tiên WeasyPrint (Chạy chuẩn trên Linux / Streamlit Cloud)
        from weasyprint import HTML, CSS
        css_path = core_dir / "report_style.css"
        HTML(string=rendered_html, base_url=str(core_dir)).write_pdf(
            str(output_path),
            stylesheets=[CSS(css_path)] if css_path.exists() else None
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
        description="Xuất báo cáo PDF từ dữ liệu lịch sử trong CSV."
    )
    parser.add_argument("ticker", help="Mã cổ phiếu có trong output/stock_data.csv")
    parser.add_argument(
        "--csv",
        type=Path,
        help="Đường dẫn CSV dữ liệu (mặc định: output/stock_data.csv)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Đường dẫn PDF đầu ra (mặc định: outputs/<MÃ>_stock_report.pdf)",
    )
    args = parser.parse_args()

    report_data = load_stock_report_data(args.ticker, args.csv)
    output_path = args.output or Path("outputs") / f"{args.ticker.strip().upper()}_stock_report.pdf"
    generated_path = generate_pdf(report_data, output_path)
    print(f"PDF saved to: {generated_path}")