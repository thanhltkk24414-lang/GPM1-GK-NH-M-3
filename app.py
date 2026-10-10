import html
import os
import traceback

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from core.report_pdf import (
    generate_pdf,
    load_stock_report_data,
    refresh_stock_report_quote,
)

# Cấu hình trang với giao diện rộng
st.set_page_config(page_title="Hệ Thống Phân Tích Cổ Phiếu", layout="wide", initial_sidebar_state="collapsed")


@st.cache_resource
def _start_realtime_pipeline():
    from stock_bot.data_pipeline.main import start_realtime_pipeline

    return start_realtime_pipeline()


@st.fragment(run_every="120s")
def _refresh_report_quote(ticker):
    report_data = st.session_state.get("report_data")
    if not isinstance(report_data, dict) or report_data.get("ticker") != ticker:
        return

    try:
        refreshed = refresh_stock_report_quote(report_data)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        st.warning(f"Không thể cập nhật giá hiện tại: {exc}")
        return

    changed = any(
        refreshed.get(key) != report_data.get(key)
        for key in (
            "current_price",
            "data_as_of",
            "recommendation",
            "target_price",
            "upside",
            "price_source",
            "exchange",
            "industry",
            "market_cap",
            "shares_outstanding",
            "foreign_ownership",
            "company_metadata_as_of",
        )
    )
    st.session_state["report_data"] = refreshed
    if changed:
        st.rerun(scope="app")

    if refreshed.get("quote_error"):
        st.warning(
            f"{refreshed['quote_error']} "
            "Không dùng giá lịch sử để thay thế giá realtime trong phiên."
        )
    else:
        if refreshed.get("quote_is_realtime"):
            st.caption(
                f"Đang dùng {refreshed.get('price_source', 'giá realtime')} · "
                "Dashboard tự kiểm tra dữ liệu mỗi 2 phút · "
                f"Cập nhật: {refreshed.get('data_as_of', 'N/A')}"
            )
        else:
            st.caption(
                f"Ngoài giờ giao dịch · Đang dùng giá đóng cửa phiên gần nhất "
                f"({refreshed.get('data_as_of', 'N/A')})."
            )

# CSS: Nền tím than đậm, đổ bóng, hiệu ứng hover pop-up
st.markdown("""
<style>
    /* Plotly text global styling */
    .js-plotly-plot .plotly text {
        font-weight: bold !important;
        font-style: italic !important;
    }
    /* Nền ứng dụng tự động theo Light/Dark/Custom theme, phủ thêm lớp gradient tím trong trẻo hơn */
    [data-testid="stAppViewContainer"], .stApp {
        background-color: var(--background-color);
        background-image: linear-gradient(135deg, rgba(138, 43, 226, 0.08) 0%, rgba(75, 0, 130, 0.15) 100%) !important;
    }
    
    /* Header trong suốt */
    [data-testid="stHeader"] {
        background-color: transparent;
    }

    /* Hiệu ứng chung cho các khối: đổ bóng, bo góc, pop-up khi hover */
    [data-testid="stMetric"], .summary-box, .welcome-box, .image-box, [data-testid="stPlotlyChart"] {
        background-color: var(--secondary-background-color) !important;
        background-image: linear-gradient(135deg, rgba(255, 255, 255, 0.03) 0%, rgba(0, 0, 0, 0.05) 100%) !important;
        backdrop-filter: blur(10px) !important;
        -webkit-backdrop-filter: blur(10px) !important;
        border-radius: 12px !important;
        padding: 20px !important;
        box-shadow: 0 8px 20px rgba(0,0,0,0.15) !important;
        transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1) !important;
        border: 1px solid rgba(138, 43, 226, 0.3) !important;
        margin-bottom: 15px;
    }

    [data-testid="stMetric"]:hover, .summary-box:hover, .welcome-box:hover, .image-box:hover, [data-testid="stPlotlyChart"]:hover {
        transform: translateY(-6px) scale(1.02) !important;
        box-shadow: 0 15px 30px rgba(138, 43, 226, 0.2) !important;
        z-index: 10;
        border: 1px solid rgba(138, 43, 226, 0.6) !important;
    }

    /* Hiệu ứng riêng cho DataFrame và Expander (không padding đè để tránh vỡ layout) */
    [data-testid="stExpander"], [data-testid="stDataFrame"] {
        background-color: var(--secondary-background-color) !important;
        background-image: linear-gradient(135deg, rgba(255, 255, 255, 0.03) 0%, rgba(0, 0, 0, 0.05) 100%) !important;
        backdrop-filter: blur(10px) !important;
        -webkit-backdrop-filter: blur(10px) !important;
        border-radius: 12px !important;
        box-shadow: 0 8px 20px rgba(0,0,0,0.15) !important;
        transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1) !important;
        border: 1px solid rgba(138, 43, 226, 0.3) !important;
        margin-bottom: 15px;
    }
    
    [data-testid="stExpander"] *, [data-testid="stDataFrame"] * {
        font-weight: bold !important;
        font-style: italic !important;
    }
    
    [data-testid="stExpander"]:hover, [data-testid="stDataFrame"]:hover {
        transform: translateY(-4px) scale(1.01) !important;
        box-shadow: 0 15px 30px rgba(138, 43, 226, 0.2) !important;
        border-color: rgba(138, 43, 226, 0.6) !important;
        z-index: 10;
    }

    /* Metric Text Styling */
    [data-testid="stMetricValue"] {
        color: var(--text-color) !important;
        font-size: 28px !important;
        font-weight: 800 !important;
        text-shadow: 0 1px 2px rgba(0,0,0,0.1);
    }
    [data-testid="stMetricLabel"], [data-testid="stMetricLabel"] p, [data-testid="stMetricLabel"] div, [data-testid="stMetricLabel"] span {
        color: #d69e2e !important; /* In vàng các nhãn metric */
        opacity: 1.0 !important;
        font-size: 16px !important;
        font-weight: 800 !important;
    }

    /* In vàng các thẻ <b> (tiêu đề nhỏ) trong hộp tóm tắt và phụ đề */
    .summary-box, .summary-box * {
        font-weight: bold !important;
        font-style: italic !important;
    }
    .summary-box b, .sub-title b, .stMarkdown strong, .stMarkdown b, .summary-box th {
        color: #d69e2e !important;
    }

    /* Style button */
    [data-testid="stFormSubmitButton"] button, [data-testid="stDownloadButton"] button {
        background: linear-gradient(135deg, #805ad5 0%, #6b46c1 100%);
        color: white !important;
        border: none;
        border-radius: 10px;
        font-weight: bold;
        transition: all 0.3s ease;
        height: auto;
        padding: 10px 0;
        box-shadow: 0 4px 10px rgba(107, 70, 193, 0.3);
    }
    [data-testid="stFormSubmitButton"] button:hover, [data-testid="stDownloadButton"] button:hover {
        transform: scale(1.05) translateY(-3px);
        box-shadow: 0 8px 20px rgba(107, 70, 193, 0.5);
        background: linear-gradient(135deg, #9f7aea 0%, #805ad5 100%);
    }

    /* Typography */
    /* Tăng cỡ chữ cho các đoạn văn, caption và bảng để dễ nhìn hơn */
    [data-testid="stCaptionContainer"], 
    [data-testid="stText"],
    .stMarkdown p, 
    .stMarkdown li, 
    [data-testid="stDataFrame"] td, 
    [data-testid="stDataFrame"] th,
    .stDataFrame {
        font-size: 16px !important;
    }
    .main-title {
        font-family: 'Segoe UI', Tahoma, sans-serif;
        color: #d69e2e !important;
        font-size: 44px;
        font-weight: 800;
        text-align: center;
        margin-bottom: 40px;
        text-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }
    .sub-title {
        color: var(--text-color);
        opacity: 0.8;
        font-size: 16px;
        margin-bottom: 25px;
        text-align: center;
    }
    .section-header {
        color: #d69e2e !important;
        font-size: 24px;
        font-weight: 700;
        border-bottom: 2px solid rgba(138, 43, 226, 0.4);
        padding-bottom: 8px;
        margin-top: 40px;
        margin-bottom: 20px;
        transition: color 0.3s ease;
    }
    
    .footer-text {
        font-size: 14px;
        color: var(--text-color);
        opacity: 0.6;
        margin-top: 50px;
        border-top: 1px solid rgba(138, 43, 226, 0.3);
        padding-top: 20px;
        text-align: center;
    }
    
    .stMarkdown p, .stMarkdown li {
        color: var(--text-color);
        font-size: 16px;
        line-height: 1.6;
    }
    
    .custom-table {
        width: 100%;
        border-collapse: collapse;
        color: var(--text-color);
        font-size: 15px;
    }
    .custom-table th {
        border-bottom: 2px solid rgba(138, 43, 226, 0.4);
        padding: 12px;
        text-align: right;
        color: #d69e2e;
    }
    .custom-table th:first-child {
        text-align: left;
    }
    .custom-table td {
        border-bottom: 1px solid rgba(128, 128, 128, 0.15);
        padding: 12px;
        text-align: right;
    }
    .custom-table td:first-child {
        text-align: left;
        font-weight: 500;
    }
    .custom-table tr:hover {
        background-color: rgba(138, 43, 226, 0.05);
        transform: scale(1.01);
        box-shadow: 0 0 10px rgba(138, 43, 226, 0.1);
        position: relative;
        z-index: 10;
    }
    
    /* ---------------------------------------------------- */
    /* HIỆU ỨNG MỚI: POP UP TẤT CẢ, BLING BLING, ĐỔ BÓNG   */
    /* ---------------------------------------------------- */
    
    /* Ẩn dòng chữ Press Enter to submit form bị đè lên ô input */
    [data-testid="InputInstructions"] {
        display: none !important;
    }

    [data-testid="stMetric"] {
        overflow: hidden !important;
    }
    [data-testid="stMetricLabel"], [data-testid="stMetricLabel"] div,
    [data-testid="stMetricValue"] {
        white-space: normal !important;
        overflow-wrap: anywhere !important;
        text-overflow: clip !important;
    }
    .summary-box {
        overflow-wrap: anywhere;
        white-space: normal;
        line-height: 1.65;
        font-weight: 700;
    }

    /* 4. Đổ bóng lấp lánh liên tục và hiệu ứng shine cho các hộp (Box) ở vùng nền */
    [data-testid="stMetric"], .summary-box, .welcome-box, .image-box, table.custom-table, [data-testid="stPlotlyChart"] {
        animation: boxSparkle 3s infinite alternate;
        box-shadow: 0 8px 16px rgba(0,0,0,0.6) !important;
        position: relative;
        overflow: hidden;
    }

    @keyframes boxSparkle {
        0% { box-shadow: 0 5px 15px rgba(159, 122, 234, 0.4), 0 0 10px rgba(251, 211, 141, 0.2) !important; }
        100% { box-shadow: 0 10px 30px rgba(159, 122, 234, 0.8), 0 0 30px rgba(251, 211, 141, 0.5) !important; }
    }

    /* Tia sáng lướt qua lấp lánh (Bling Bling tự động) */
    [data-testid="stMetric"]::before, .summary-box::before, .welcome-box::before, .image-box::before, [data-testid="stPlotlyChart"]::before {
        content: '';
        position: absolute;
        top: 0;
        left: -100%;
        width: 50%;
        height: 100%;
        background: linear-gradient(to right, rgba(255,255,255,0) 0%, rgba(255,255,255,0.4) 50%, rgba(255,255,255,0) 100%);
        transform: skewX(-25deg);
        animation: shineBling 4s infinite;
        pointer-events: none;
        z-index: 1;
    }
    
    /* Đảm bảo nội dung trong box nằm trên tia sáng */
    .welcome-box p, .welcome-box ul, .summary-box *, [data-testid="stMetric"] *, [data-testid="stPlotlyChart"] * {
        position: relative;
        z-index: 2;
    }

    @keyframes shineBling {
        0% { left: -100%; }
        20% { left: 200%; }
        100% { left: 200%; }
    }
</style>
""", unsafe_allow_html=True)

def hoverify(text):
    return html.escape(str(text)).replace("\n", "<br>")

st.markdown(f'<div class="main-title">{hoverify("HỆ THỐNG PHÂN TÍCH CƠ HỘI ĐẦU TƯ CỔ PHIẾU")}</div>', unsafe_allow_html=True)

# Giao diện Nhập liệu dùng Form để chỉ chạy khi nhấn nút
with st.form("search_form"):
    col_input, col_btn, _ = st.columns([2, 1, 3])
    with col_input:
        ticker_input = st.text_input("MÃ CHỨNG KHOÁN:", value="", label_visibility="collapsed", placeholder="Nhập mã cổ phiếu (VD: HPG, VNM, ACB)")
    with col_btn:
        analyze_btn = st.form_submit_button("🚀 Truy Xuất Báo Cáo", use_container_width=True)

if analyze_btn and not ticker_input:
    st.session_state.pop("report_ticker", None)
    st.session_state.pop("report_data", None)

if analyze_btn and ticker_input:
    submitted_ticker = ticker_input.strip().upper()
    if submitted_ticker != st.session_state.get("report_ticker"):
        st.session_state.pop("report_data", None)
    st.session_state["report_ticker"] = submitted_ticker

if st.session_state.get("report_ticker"):
    ticker = st.session_state["report_ticker"]
    with st.spinner(f"Hệ thống đang xử lý dữ liệu cho {ticker}..."):
        try:
            try:
                _start_realtime_pipeline()
            except Exception as exc:
                st.warning(
                    f"Không khởi động được realtime pipeline: {exc} "
                    "Dashboard vẫn tiếp tục bằng dữ liệu hiện có."
                )

            if analyze_btn or "report_data" not in st.session_state:
                # 1. Tải dữ liệu báo cáo trước để biết năm tài chính mới nhất
                report_data = load_stock_report_data(ticker)
                
                # 2. Lấy năm tài chính mới nhất từ báo cáo
                fin_years = report_data.get("financial_years", [])
                target_year = fin_years[-1] if fin_years else 2023
                
                # 3. Tải BCTN cho năm mục tiêu đó
                import core.annual_report as ar
                if not (ar.OUTPUT_DIR / f"{ticker}_{target_year}.pdf").exists():
                    try:
                        ar.process_report(ticker, target_year)
                    except Exception:
                        pass
                
                st.session_state["report_data"] = report_data
            else:
                report_data = st.session_state["report_data"]
            _refresh_report_quote(ticker)
            
            # Hàm loại bỏ từ workbook
            def clean_text(text):
                if not isinstance(text, str): return text
                return text.replace("workbook ", "").replace("Workbook ", "")

            # --- PHẦN HEADER BÁO CÁO ---
            company_name = report_data.get("company_name", ticker)
            st.markdown(f'<div class="main-title" style="font-size: 28px; margin-top: 20px;">{hoverify("BÁO CÁO PHÂN TÍCH:")} {hoverify(company_name.upper())}</div>', unsafe_allow_html=True)
            
            sub_info = (
                f"<b>{hoverify('Mã CK:')}</b> {hoverify(ticker)} &nbsp;|&nbsp; "
                f"<b>{hoverify('Sàn:')}</b> {hoverify(report_data.get('exchange', 'N/A'))} &nbsp;|&nbsp; "
                f"<b>{hoverify('Ngành:')}</b> {hoverify(clean_text(report_data.get('industry', 'N/A')))} &nbsp;|&nbsp; "
                f"<b>{hoverify('Ngày Dữ Liệu:')}</b> {hoverify(report_data.get('data_as_of', 'N/A'))}"
            )
            st.markdown(f'<div class="sub-title">{sub_info}</div>', unsafe_allow_html=True)
            if report_data.get("exchange") == "Chưa có dữ liệu":
                st.warning(
                    "Chưa tìm thấy sàn cho mã này. Đặt danh sách mã tại "
                    "data\\symbols.csv hoặc data\\symbol.csv với các cột "
                    "`symbol` và `exchange`, rồi tra cứu lại mã."
                )
            
            # --- TẠO VÀ TẢI BÁO CÁO PDF (ĐƯA LÊN ĐẦU) ---
            pdf_path = f"outputs/{ticker}_report.pdf"
            os.makedirs("outputs", exist_ok=True)
            
            pdf_data = report_data.copy()
            pdf_data.pop("price_history", None)
            pdf_data.pop("_scoring_data", None)
            pdf_bytes = None
            try:
                generate_pdf(pdf_data, output_path=pdf_path)
                with open(pdf_path, "rb") as f:
                    pdf_bytes = f.read()
            except (
                AttributeError,
                ImportError,
                OSError,
                RuntimeError,
                ZeroDivisionError,
            ) as exc:
                st.warning(
                    f"Không tạo được báo cáo PDF: {exc} "
                    "Các dữ liệu và biểu đồ trên dashboard vẫn được hiển thị."
                )

            col_dl1, col_dl2 = st.columns(2)
            with col_dl1:
                if pdf_bytes is not None:
                    st.download_button(
                        label="📥 TẢI XUỐNG BÁO CÁO PHÂN TÍCH (PDF)",
                        data=pdf_bytes,
                        file_name=f"Bao_Cao_Phan_Tich_{ticker}.pdf",
                        mime="application/pdf",
                        type="primary",
                        use_container_width=True
                    )
                else:
                    st.button(
                        "PDF hiện không khả dụng",
                        disabled=True,
                        use_container_width=True,
                    )
            
            bctn_path = None
            bctn_year = report_data.get("financial_years", [])[-1] if report_data.get("financial_years") else 2023
            
            import core.annual_report as ar
            file_path = ar.OUTPUT_DIR / f"{ticker}_{bctn_year}.pdf"
            if file_path.exists():
                bctn_path = file_path
                    
            with col_dl2:
                if bctn_path:
                    try:
                        with open(bctn_path, "rb") as f:
                            bctn_bytes = f.read()
                        st.download_button(
                            label=f"📥 TẢI BÁO CÁO THƯỜNG NIÊN ({bctn_year})",
                            data=bctn_bytes,
                            file_name=f"BCTN_{ticker}_{bctn_year}.pdf",
                            mime="application/pdf",
                            use_container_width=True
                        )
                    except Exception:
                        st.button("Lỗi Đọc Báo Cáo Thường Niên", disabled=True, use_container_width=True)
                else:
                    st.button("Không Tìm Thấy Báo Cáo Thường Niên", disabled=True, use_container_width=True)
            
            st.markdown("<hr style='border: 1px solid rgba(138, 43, 226, 0.2); margin-top: 10px; margin-bottom: 20px;'>", unsafe_allow_html=True)
            # --- 1. TỔNG QUAN GIAO DỊCH & ĐỊNH GIÁ ---
            st.markdown(f'<div class="section-header">💎 {hoverify("1. Chỉ Số Giao Dịch & Khuyến Nghị")}</div>', unsafe_allow_html=True)
            
            c1, c2, c3, c4 = st.columns(4)
            c1.metric(
                "Giá Hiện Tại",
                report_data.get("current_price", "N/A"),
                help=report_data.get("price_source"),
            )
            c2.metric(
                "Khuyến Nghị",
                report_data.get("recommendation", "N/A"),
                help=report_data.get("investment_thesis"),
            )
            c3.metric(
                "Giá Mục Tiêu",
                report_data.get("target_price", "N/A"),
                help=report_data.get("target_method", ""),
            )
            c4.metric("Tiềm Năng", report_data.get("upside", "N/A"))

            c5, c6, c7, c8 = st.columns(4)
            c5.metric(
                "Dải Giá 52 Tuần",
                report_data.get("price_52w_range", "N/A"),
                help="Biên độ thấp nhất–cao nhất trong khoảng 52 tuần gần nhất.",
            )
            c6.metric(
                "KLGD 20 Phiên",
                report_data.get("avg_volume_20d", "N/A"),
                help="Khối lượng giao dịch trung bình của 20 phiên gần nhất.",
            )
            c7.metric(
                "Vốn Hóa",
                report_data.get("market_cap", "N/A"),
                help=(
                    "Vốn hóa theo hồ sơ công ty; nếu nguồn chỉ có số cổ phiếu, "
                    "hệ thống ước tính bằng giá tham chiếu nhân số cổ phiếu."
                ),
            )
            c8.metric(
                "CP Lưu Hành",
                report_data.get("shares_outstanding", "N/A"),
                help="Số cổ phiếu phát hành/lưu hành do nguồn hồ sơ VNStock VCI cung cấp.",
            )

            c9, c10, c11, c12 = st.columns(4)
            foreign_limit = report_data.get("foreign_ownership_limit", "Chưa có dữ liệu")
            c9.metric(
                "Sở Hữu Nước Ngoài",
                report_data.get("foreign_ownership", "N/A"),
                help=(
                    "Tỷ lệ cổ phần hiện do nhà đầu tư nước ngoài nắm giữ; không phải room còn lại. "
                    f"Giới hạn sở hữu tối đa theo nguồn hồ sơ: {foreign_limit}."
                ),
            )
            c10.metric("Hệ số Beta", report_data.get("beta", "N/A"))
            c11.metric("Đầu Tư", report_data.get("investment_horizon", "N/A"))
            pe_value = report_data.get("fundamental_metrics", {}).get("pe")
            c12.metric(
                "P/E",
                f"{pe_value:.2f} lần" if pe_value is not None else "Chưa có dữ liệu",
                help="P/E được đọc từ chỉ tiêu pe_ratio trong bảng ratio của VNStock.",
            )
            eps_value = report_data.get("fundamental_metrics", {}).get("eps")
            eps_column, _ = st.columns([1, 3])
            eps_column.metric(
                "EPS (đồng/cổ phiếu)",
                f"{eps_value:,.0f} đ" if eps_value is not None else "Chưa có dữ liệu",
                help=(
                    "EPS trailing 4 quý do VNStock cung cấp; đơn vị là đồng trên "
                    "mỗi cổ phiếu, không phải nghìn đồng."
                ),
            )
            missing_metadata = report_data.get("metadata_missing", [])
            if report_data.get("company_metadata_error"):
                st.warning(report_data["company_metadata_error"])
            if missing_metadata:
                st.info(
                    "Chưa có dữ liệu nguồn cho: "
                    + ", ".join(missing_metadata)
                    + ". Dashboard không tự ước lượng các trường này."
                )
            if report_data.get("quote_error"):
                st.warning(
                    f"{report_data['quote_error']} "
                    "Không dùng giá lịch sử để thay thế giá realtime trong phiên."
                )
            st.caption(f"Nguồn giá: {report_data.get('price_source', 'Chưa có dữ liệu')}")
            if report_data.get("company_metadata_as_of"):
                st.caption(
                    f"Hồ sơ doanh nghiệp lấy từ "
                    f"{report_data.get('company_metadata_source', 'VNStock VCI')} · "
                    f"{report_data['company_metadata_as_of']}"
                )

            # --- 2. BIỂU ĐỒ GIÁ ---
            st.markdown(f'<div class="section-header">📈 {hoverify("2. Biểu Đồ Diễn Biến Giá")}</div>', unsafe_allow_html=True)
            
            # Vẽ biểu đồ nến bằng Plotly thay vì SVG tĩnh
            try:
                df_stock = report_data["price_history"].copy()
                if not df_stock.empty:
                    df_stock['date'] = pd.to_datetime(df_stock['date'])
                    df_stock = df_stock.sort_values('date')
                    current_price = (
                        report_data.get("_scoring_data", {})
                        .get("tech", {})
                        .get("close")
                    )
                    # Chuyển ngày sang chuỗi để loại bỏ hoàn toàn khoảng trống giữa các nến
                    df_stock['date_str'] = df_stock['date'].dt.strftime('%d-%m-%Y')
                    
                    fig = go.Figure(data=[go.Candlestick(x=df_stock['date'],
                                    open=df_stock['open'],
                                    high=df_stock['high'],
                                    low=df_stock['low'],
                                    close=df_stock['close'],
                                    increasing_line_color='#089981', increasing_fillcolor='#089981', # Xanh TradingView
                                    decreasing_line_color='#F23645', decreasing_fillcolor='#F23645', # Đỏ TradingView
                                    name='Giá')])
                    if current_price is not None:
                        current_label = (
                            "Giá realtime"
                            if report_data.get("quote_is_realtime")
                            else "Giá đóng cửa"
                        )
                        fig.add_hline(
                            y=current_price,
                            line_dash="dot",
                            line_color="#f6c85f",
                            annotation_text=current_label,
                            annotation_position="top left",
                        )
                    
                    min_price = min(
                        df_stock['low'].min(),
                        current_price if current_price is not None else float("inf"),
                    ) * 0.8
                    max_price = max(
                        df_stock['high'].max(),
                        current_price if current_price is not None else float("-inf"),
                    ) * 1.2
                    
                    min_date = df_stock['date'].min() - pd.Timedelta(days=10)
                    max_date = df_stock['date'].max() + pd.Timedelta(days=10)

                    fig.update_layout(
                        margin=dict(l=20, r=20, t=20, b=60),
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        xaxis_rangeslider_visible=False,
                        hovermode="closest",
                        dragmode="pan",
                        height=600, # Phóng to chiều cao chart
                        yaxis=dict(
                            side="right", # Chuyển trục giá sang bên phải
                            fixedrange=False,
                            minallowed=min_price,
                            maxallowed=max_price
                        ),
                        xaxis=dict(
                            type='date',
                            rangebreaks=[
                                dict(bounds=["sat", "mon"]), # Ẩn thứ 7, Chủ Nhật
                            ],
                            fixedrange=False,
                            minallowed=min_date,
                            maxallowed=max_date,
                            range=[df_stock['date'].min() - pd.Timedelta(days=2), df_stock['date'].max() + pd.Timedelta(days=2)],
                            nticks=10,
                            tickangle=0,
                            tickformat="%d-%m-%Y"
                        )
                    )
                    st.plotly_chart(
                        fig,
                        use_container_width=True,
                        config={"scrollZoom": True, "displayModeBar": False},
                    )
                else:
                    st.warning("Không tìm thấy dữ liệu lịch sử để vẽ biểu đồ nến.")
            except Exception as e:
                # Fallback to SVG
                st.error(f"Error drawing Plotly: {e}")
                svg_data = report_data.get("price_chart", "")
                if svg_data:
                    st.markdown(f'<div class="image-box"><img src="{svg_data}" width="100%" /></div>', unsafe_allow_html=True)
                else:
                    st.warning("Không thể hiển thị biểu đồ.")

            st.markdown(
                f'<div class="section-header">📊 {hoverify("3. Chỉ Báo Kỹ Thuật")}</div>',
                unsafe_allow_html=True,
            )
            technical_metrics = report_data.get("technical_metrics", [])
            if technical_metrics:
                st.caption(
                    f"Chỉ báo kỹ thuật tính đến phiên đóng cửa "
                    f"{report_data.get('technical_as_of', 'gần nhất')}; "
                    "giá khớp cập nhật riêng trong giờ giao dịch."
                )
                for start in range(0, len(technical_metrics), 6):
                    metric_row = technical_metrics[start:start + 6]
                    columns = st.columns(len(metric_row))
                    for column, metric in zip(columns, metric_row):
                        column.metric(metric["label"], metric["value"])

            # --- 3. TÓM TẮT PHÂN TÍCH & LUẬN ĐIỂM ---
            st.markdown(f'<div class="section-header">🧠 {hoverify("4. Tóm Tắt Phân Tích & Luận Điểm")}</div>', unsafe_allow_html=True)
            
            ai_sum = clean_text(report_data.get('ai_summary', 'Không có dữ liệu phân tích.'))
            st.markdown(f'<div class="summary-box"><b>{hoverify("Tổng hợp nhận định chuyên sâu:")}</b><br>{hoverify(ai_sum)}</div>', unsafe_allow_html=True)
            
            score_breakdown = report_data.get("score_breakdown", {})
            if score_breakdown:
                with st.expander("Vì sao hệ thống ra số điểm và khuyến nghị này?"):
                    st.markdown(
                        f"**{report_data.get('recommendation', 'Chưa có khuyến nghị')}** "
                        f"với tổng điểm **{score_breakdown.get('total_score')}/100**."
                    )
                    st.markdown(
                        f"Giá tham chiếu: **{report_data.get('current_price', 'N/A')}** · "
                        f"Giá mục tiêu: **{report_data.get('target_price', 'N/A')}** · "
                        f"Tiềm năng: **{report_data.get('upside', 'N/A')}**."
                    )
                    target_method = report_data.get("target_method")
                    target_confidence = report_data.get("target_confidence")
                    if target_method:
                        st.caption(
                            f"Định giá: {target_method}. "
                            f"Độ tin cậy: {target_confidence or 'chưa đánh giá'}."
                        )
                    valuation_summary = report_data.get("valuation_summary")
                    if valuation_summary:
                        st.caption(valuation_summary)
                    valuation_methods = report_data.get("valuation_methods", [])
                    if valuation_methods:
                        html_table = "<div class='summary-box'><table style='width:100%; border-collapse: collapse; font-size: 16px;'>"
                        html_table += "<tr><th style='text-align:left; border-bottom: 1px solid rgba(138,43,226,0.3); padding: 8px;'>Phương pháp</th><th style='text-align:left; border-bottom: 1px solid rgba(138,43,226,0.3); padding: 8px;'>Giá trị hợp lý (đồng/CP)</th><th style='text-align:left; border-bottom: 1px solid rgba(138,43,226,0.3); padding: 8px;'>Tỷ trọng</th></tr>"
                        for method in valuation_methods:
                            html_table += f"<tr><td style='border-bottom: 1px solid rgba(255,255,255,0.1); padding: 8px;'>{method['method_name']}</td><td style='border-bottom: 1px solid rgba(255,255,255,0.1); padding: 8px;'>{method['fair_value']}</td><td style='border-bottom: 1px solid rgba(255,255,255,0.1); padding: 8px;'>{method['weight']}</td></tr>"
                        target_price = report_data.get("target_price", "Chưa đủ dữ liệu")
                        html_table += f"<tr><td style='border-bottom: 1px solid rgba(255,255,255,0.1); padding: 8px;'>Giá mục tiêu tổng hợp</td><td style='border-bottom: 1px solid rgba(255,255,255,0.1); padding: 8px;'>{target_price}</td><td style='border-bottom: 1px solid rgba(255,255,255,0.1); padding: 8px;'>—</td></tr>"
                        html_table += "</table></div>"
                        st.markdown(html_table, unsafe_allow_html=True)
                    group_labels = {
                        "technical": "Kỹ thuật",
                        "fundamental": "Cơ bản",
                        "news": "Tin tức",
                    }
                    groups = score_breakdown.get("groups", [])
                    if groups:
                        html_table = "<div class='summary-box'><table style='width:100%; border-collapse: collapse; font-size: 16px;'>"
                        html_table += "<tr><th style='text-align:left; border-bottom: 1px solid rgba(138,43,226,0.3); padding: 8px;'>Nhóm</th><th style='text-align:left; border-bottom: 1px solid rgba(138,43,226,0.3); padding: 8px;'>Điểm</th><th style='text-align:left; border-bottom: 1px solid rgba(138,43,226,0.3); padding: 8px;'>Trọng số ban đầu</th><th style='text-align:left; border-bottom: 1px solid rgba(138,43,226,0.3); padding: 8px;'>Đóng góp sau chuẩn hóa</th></tr>"
                        for group in groups:
                            nhom = group_labels.get(group['name'], group['name'])
                            diem = group['score']
                            trong_so = f"{group['weight']:.0%}"
                            dong_gop = f"{group['weighted_points']:.1f}"
                            html_table += f"<tr><td style='border-bottom: 1px solid rgba(255,255,255,0.1); padding: 8px;'>{nhom}</td><td style='border-bottom: 1px solid rgba(255,255,255,0.1); padding: 8px;'>{diem}</td><td style='border-bottom: 1px solid rgba(255,255,255,0.1); padding: 8px;'>{trong_so}</td><td style='border-bottom: 1px solid rgba(255,255,255,0.1); padding: 8px;'>{dong_gop}</td></tr>"
                        html_table += "</table></div>"
                        st.markdown(html_table, unsafe_allow_html=True)
                    bonus = score_breakdown.get("bonus", 0)
                    st.caption(
                        f"Tổng điểm: {score_breakdown.get('total_score')}/100 · "
                        f"Khuyến nghị: {score_breakdown.get('recommendation')} · "
                        f"MUA từ {score_breakdown.get('buy_threshold')}/100, "
                        f"BÁN dưới {score_breakdown.get('sell_threshold')}/100, "
                        "còn lại là GIỮ. "
                        f"{score_breakdown.get('formula', '')} "
                        f"Điểm cộng kết hợp: +{bonus}."
                    )
                    for key, title in (
                        ("technical_factors", "Chi tiết điểm kỹ thuật"),
                        ("fundamental_factors", "Chi tiết điểm cơ bản"),
                        ("news_factors", "Chi tiết điểm tin tức"),
                    ):
                        factors = score_breakdown.get(key, [])
                        if factors:
                            st.markdown(f"**{title}**")
                            html_table = "<div class='summary-box'><table style='width:100%; border-collapse: collapse; font-size: 16px;'>"
                            html_table += "<tr><th style='text-align:left; border-bottom: 1px solid rgba(138,43,226,0.3); padding: 8px;'>Tiêu chí</th><th style='text-align:left; border-bottom: 1px solid rgba(138,43,226,0.3); padding: 8px;'>Dữ liệu</th><th style='text-align:left; border-bottom: 1px solid rgba(138,43,226,0.3); padding: 8px;'>Quy tắc</th><th style='text-align:left; border-bottom: 1px solid rgba(138,43,226,0.3); padding: 8px;'>Tác động</th></tr>"
                            for factor in factors:
                                t_chi = factor['label']
                                d_lieu = factor.get('evidence', '')
                                q_tac = factor.get('rule', '')
                                t_dong = f"{factor['points']:+d} điểm" if factor.get("points") is not None else "Không chấm"
                                html_table += f"<tr><td style='border-bottom: 1px solid rgba(255,255,255,0.1); padding: 8px;'>{t_chi}</td><td style='border-bottom: 1px solid rgba(255,255,255,0.1); padding: 8px;'>{d_lieu}</td><td style='border-bottom: 1px solid rgba(255,255,255,0.1); padding: 8px;'>{q_tac}</td><td style='border-bottom: 1px solid rgba(255,255,255,0.1); padding: 8px;'>{t_dong}</td></tr>"
                            html_table += "</table></div>"
                            st.markdown(html_table, unsafe_allow_html=True)

            col_points, col_risks = st.columns(2)
            with col_points:
                points_html = f'<div class="summary-box"><b>✅ {hoverify("Tín hiệu tích cực được bộ quy tắc ghi nhận:")}</b><ul style="margin-top: 10px;">'
                points = report_data.get("investment_points", [])
                if points:
                    for p in points:
                        points_html += f"<li>{hoverify(clean_text(p))}</li>"
                else:
                    points_html += f"<li>{hoverify('*(Không có dữ liệu)*')}</li>"
                points_html += '</ul></div>'
                st.markdown(points_html, unsafe_allow_html=True)
            
            with col_risks:
                risks_html = f'<div class="summary-box"><b>⚠️ {hoverify("Rủi ro hoặc điều kiện làm giảm điểm:")}</b><ul style="margin-top: 10px;">'
                risks = report_data.get("key_risks", [])
                if risks:
                    for r in risks:
                        risks_html += f"<li>{hoverify(clean_text(r))}</li>"
                else:
                    risks_html += f"<li>{hoverify('*(Không có dữ liệu)*')}</li>"
                risks_html += '</ul></div>'
                st.markdown(risks_html, unsafe_allow_html=True)

            # --- 4. DỮ LIỆU TÀI CHÍNH ---
            st.markdown(f'<div class="section-header">🏦 {hoverify("5. Dữ Liệu Tài Chính & Dự Phóng")}</div>', unsafe_allow_html=True)
            fin_sections = report_data.get("financial_sections", [])
            if report_data.get("financial_error"):
                st.warning(
                    f"Chưa tải được BCTC cho {ticker}: "
                    f"{report_data['financial_error']}"
                )
            
            if fin_sections:
                chart_data = report_data.get("financial_chart_data", {})
                chart_years = chart_data.get("years", [])
                chart_choices = {
                    "Lợi Nhuận Sau Thuế": ("net_profit", "net_profit_yoy", chart_data.get("profit_label", "Lợi nhuận sau thuế")),
                    chart_data.get("income_label", "Doanh thu thuần"): ("income", "income_yoy", chart_data.get("income_label", "Doanh thu thuần")),
                }
                available_charts = [
                    label
                    for label, (amount_key, _, _) in chart_choices.items()
                    if any(
                        value is not None
                        for value in chart_data.get(amount_key, {}).values()
                    )
                ]
                if chart_years and available_charts:
                    cols = st.columns(len(available_charts))
                    for col, label in zip(cols, available_charts):
                        amount_key, yoy_key, amount_label = chart_choices[label]
                        amounts = chart_data.get(amount_key, {})
                        yoy_values = chart_data.get(yoy_key, {})
                        financial_fig = go.Figure()
                        display_years = [f"Năm {y}" for y in chart_years]
                        financial_fig.add_bar(
                            x=display_years,
                            y=[amounts.get(year) for year in chart_years],
                            name=amount_label,
                            marker=dict(
                                color="rgba(59, 130, 246, 0.85)",
                                line=dict(color="#2563eb", width=1.5)
                            ),
                            hovertemplate="%{y:,.1f} Tỷ đồng<extra></extra>",
                        )
                        yoy_series = [yoy_values.get(year) for year in chart_years]
                        yoy_bound = max(
                            max((abs(value) for value in yoy_series if value is not None), default=1),
                            1,
                        ) * 1.25
                        financial_fig.add_scatter(
                            x=display_years,
                            y=yoy_series,
                            name="Tăng trưởng YoY",
                            mode="lines+markers",
                            connectgaps=False,
                            yaxis="y2",
                            line=dict(color="#f59e0b", width=3.5),
                            marker=dict(size=8, color="#f59e0b"),
                            hovertemplate="YoY: %{y:+.1f}%<extra></extra>",
                        )
                        if chart_years and yoy_series[0] is None:
                            pass
                        financial_fig.update_layout(

                            height=380,
                            margin=dict(l=30, r=30, t=50, b=30),
                            title=dict(text=label, font=dict(size=14, color="#e2e8f0")),
                            paper_bgcolor="rgba(0,0,0,0)",
                            plot_bgcolor="rgba(0,0,0,0)",
                            hovermode="x unified",
                            xaxis=dict(type="category", showgrid=False, title="", showspikes=False),
                            yaxis=dict(title="Tỷ đồng", showgrid=True, gridcolor="rgba(255,255,255,0.1)"),
                            yaxis2=dict(
                                title="YoY (%)",
                                overlaying="y",
                                side="right",
                                range=[-yoy_bound, yoy_bound],
                                showgrid=False,
                            ),
                            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1.0),
                        )
                        with col:
                            st.plotly_chart(
                                financial_fig,
                                use_container_width=True,
                                config={"displayModeBar": False},
                            )
                    
                    st.caption(
                        f"Nguồn: {report_data.get('fundamental_metrics', {}).get('source', 'BCTC')}; "
                        "cột hiển thị giá trị, đường hiển thị YoY. "
                        + (
                            f"* YoY năm {chart_years[0]} chưa tính vì thiếu số liệu "
                            f"{int(chart_years[0]) - 1}."
                            if chart_years and yoy_series[0] is None
                            else ""
                        )
                    )
                years = report_data.get("financial_years", [])
                for section in fin_sections:
                    st.markdown(
                        f"**{clean_text(section.get('title', section.get('label', ''))).upper()}**"
                    )
                    
                    html_table = "<div class='summary-box'><table class='custom-table'>"
                    # Header
                    html_table += "<tr><th>Chỉ Tiêu</th>"
                    for y in years:
                        html_table += f"<th>{y}</th>"
                    html_table += "</tr>"
                    
                    # Rows
                    for row in section.get("rows", []):
                        html_table += f"<tr><td>{clean_text(row.get('label', ''))}</td>"
                        for i, val in enumerate(row.get("values", [])):
                            if i < len(years):
                                html_table += f"<td>{val}</td>"
                        html_table += "</tr>"
                    html_table += "</table></div>"
                    
                    st.markdown(html_table, unsafe_allow_html=True)
            else:
                st.write("*(Chưa có dữ liệu tài chính cho mã này)*")

            st.markdown(
                f'<div class="section-header">📰 {hoverify("6. Tin Tức Gần Đây")}</div>',
                unsafe_allow_html=True,
            )
            news_list = report_data.get("news_list", [])
            if news_list:
                for news in news_list:
                    news_html = '<div class="summary-box">'
                    
                    if news.get("url"):
                        news_html += f'<a href="{news["url"]}" target="_blank" style="color: #d69e2e; font-weight: bold; text-decoration: none; font-size: 18px;">{news["title"]}</a><br>'
                    else:
                        news_html += f'<b style="color: #d69e2e; font-size: 18px;">{news["title"]}</b><br>'
                        
                    meta = " · ".join(value for value in (news.get("date"), news.get("source")) if value)
                    if meta:
                        news_html += f'<span style="font-size: 14px; opacity: 0.8; color: var(--text-color);">{meta}</span><br>'
                        
                    news_html += '</div>'
                    st.markdown(news_html, unsafe_allow_html=True)
            elif report_data.get("news_error"):
                st.warning(f"Không lấy được tin tức: {report_data['news_error']}")
            else:
                st.info("Chưa tìm thấy tin tức gần đây cho mã này.")

            # --- FOOTER ---
            footer_html = (
                f"<b>Chuyên viên:</b> {report_data.get('analyst_name', 'Hệ thống tự động')} "
                f"({report_data.get('analyst_contact', '')})<br>"
                f"<b>Nguồn:</b> {report_data.get('data_sources', 'N/A')}<br>"
                f"<i>Ngày xuất: {report_data.get('report_date', 'N/A')}</i>"
            )
            st.markdown(f'<div class="footer-text">{footer_html}</div>', unsafe_allow_html=True)

        except Exception as e:
            st.error(f"Đã xảy ra lỗi: {e}")
            st.code(traceback.format_exc(), language="python")
            print(traceback.format_exc())
elif analyze_btn and not ticker_input:
    st.warning("Vui lòng nhập mã cổ phiếu!")
else:
    # Màn hình chào mừng khi chưa nhập mã
    st.markdown(f"""
    <div class="welcome-box" style="margin-top: 50px; text-align: center; padding: 50px 20px !important;">
        <p style="color: var(--text-color); font-size: 26px; max-width: 1100px; margin: 0 auto 30px auto; line-height: 1.8; font-weight: 700;">
            {hoverify('Dựa trên các nguồn dữ liệu:')}
        </p>
        <ul style="color: var(--text-color); font-size: 36px; max-width: 1100px; margin: 0 auto 40px auto; padding-left: 0; line-height: 2.2; list-style-type: none; text-align: left; display: inline-block; font-weight: 800;">
            <li>• {hoverify('Giá và dữ liệu giao dịch chứng khoán')}</li>
            <li>• {hoverify('Báo cáo tài chính và các chỉ số tài chính doanh nghiệp niêm yết')}</li>
            <li>• {hoverify('Thông tin, tin tức doanh nghiệp cập nhật')}</li>
            <li>• {hoverify('Báo cáo phân tích của các công ty chứng khoán')}</li>
        </ul>
        <p style="color: var(--text-color); font-size: 24px; max-width: 1100px; margin: 0 auto 20px auto; line-height: 1.8; font-weight: 500;">
            {hoverify('Báo cáo phân tích tự động trích xuất ra PDF theo nhu cầu người dùng')}
        </p>
        <p style="color: var(--text-color); font-size: 28px; max-width: 1100px; margin: 0 auto; line-height: 1.8; font-weight: 800;">
            {hoverify('Chính xác về mặt dữ liệu, kết quả phân tích đánh giá thích hợp và sáng tạo')}
        </p>
    </div>
    """, unsafe_allow_html=True)
