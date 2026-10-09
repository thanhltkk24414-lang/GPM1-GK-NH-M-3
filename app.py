import streamlit as st
import pandas as pd
import os
import plotly.graph_objects as go
from core.report_pdf import load_stock_report_data, generate_pdf

# Cấu hình trang với giao diện rộng
st.set_page_config(page_title="Hệ Thống Phân Tích Cổ Phiếu", layout="wide", initial_sidebar_state="collapsed")

# CSS: Nền tím than đậm, đổ bóng, hiệu ứng hover pop-up
st.markdown("""
<style>
    /* Nền tím than đậm toàn trang */
    [data-testid="stAppViewContainer"], .stApp {
        background-color: #1a0b2e;
        background-image: linear-gradient(180deg, #1a0b2e 0%, #0d041c 100%);
        color: #e2e8f0;
    }
    
    /* Header trong suốt */
    [data-testid="stHeader"] {
        background-color: transparent;
    }

    /* Hiệu ứng chung cho các khối: đổ bóng, bo góc, pop-up khi hover */
    [data-testid="stMetric"], .summary-box, .welcome-box, .image-box {
        background-color: rgba(45, 27, 84, 0.6) !important;
        border-radius: 12px !important;
        padding: 20px !important;
        box-shadow: 0 4px 12px rgba(0,0,0,0.4) !important;
        transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1) !important;
        border: 1px solid rgba(159, 122, 234, 0.2) !important;
        margin-bottom: 15px;
    }

    [data-testid="stMetric"]:hover, .summary-box:hover, .welcome-box:hover, .image-box:hover {
        transform: translateY(-6px) scale(1.02) !important;
        box-shadow: 0 15px 25px rgba(159, 122, 234, 0.5) !important;
        border-color: rgba(159, 122, 234, 0.8) !important;
        z-index: 10;
    }

    /* Metric Text Styling */
    [data-testid="stMetricValue"] {
        color: #fbd38d !important; /* Vàng kim/cam sáng */
        font-size: 28px !important;
        font-weight: 800 !important;
        text-shadow: 0 2px 4px rgba(0,0,0,0.5);
    }
    [data-testid="stMetricLabel"] {
        color: #d6bcfa !important;
        font-size: 15px !important;
        font-weight: 600 !important;
    }

    /* Bỏ style custom input để tránh lỗi không thấy chữ, chỉ style button */
    [data-testid="stFormSubmitButton"] button, [data-testid="stDownloadButton"] button {
        background: linear-gradient(135deg, #6b46c1 0%, #805ad5 100%);
        color: white !important;
        border: none;
        border-radius: 10px;
        font-weight: bold;
        box-shadow: 0 4px 10px rgba(107, 70, 193, 0.5);
        transition: all 0.3s ease;
        height: auto;
        padding: 10px 0;
    }
    [data-testid="stFormSubmitButton"] button:hover, [data-testid="stDownloadButton"] button:hover {
        transform: scale(1.05) translateY(-3px);
        box-shadow: 0 10px 20px rgba(159, 122, 234, 0.8);
        background: linear-gradient(135deg, #805ad5 0%, #9f7aea 100%);
    }

    /* Typography */
    .main-title {
        font-family: 'Segoe UI', Tahoma, sans-serif;
        color: #faf5ff !important;
        font-size: 34px;
        font-weight: 800;
        text-shadow: 0 0 15px rgba(233, 216, 253, 0.6);
        text-align: center;
        margin-bottom: 30px;
    }
    .sub-title {
        color: #b794f4;
        font-size: 16px;
        margin-bottom: 25px;
        text-align: center;
    }
    .section-header {
        color: #e9d8fd !important;
        font-size: 24px;
        font-weight: 700;
        border-bottom: 2px solid #805ad5;
        padding-bottom: 8px;
        margin-top: 40px;
        margin-bottom: 20px;
        text-shadow: 0 2px 4px rgba(0,0,0,0.5);
    }
    
    .footer-text {
        font-size: 14px;
        color: #a0aec0;
        margin-top: 50px;
        border-top: 1px solid rgba(159, 122, 234, 0.3);
        padding-top: 20px;
        text-align: center;
    }
    
    .stMarkdown p, .stMarkdown li {
        color: #e2e8f0;
        font-size: 16px;
        line-height: 1.6;
    }
    
    .custom-table {
        width: 100%;
        border-collapse: collapse;
        color: #e2e8f0;
        font-size: 15px;
    }
    .custom-table th {
        border-bottom: 2px solid #805ad5;
        padding: 12px;
        text-align: right;
        color: #d6bcfa;
    }
    .custom-table th:first-child {
        text-align: left;
    }
    .custom-table td {
        border-bottom: 1px solid rgba(159, 122, 234, 0.2);
        padding: 12px;
        text-align: right;
    }
    .custom-table td:first-child {
        text-align: left;
        font-weight: 500;
    }
    .custom-table tr:hover {
        background-color: rgba(159, 122, 234, 0.1);
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-title">HỆ THỐNG PHÂN TÍCH CƠ HỘI ĐẦU TƯ CỔ PHIẾU</div>', unsafe_allow_html=True)

# Giao diện Nhập liệu dùng Form để chỉ chạy khi nhấn nút
with st.form("search_form"):
    col_input, col_btn, _ = st.columns([2, 1, 3])
    with col_input:
        ticker_input = st.text_input("MÃ CHỨNG KHOÁN:", value="", label_visibility="collapsed", placeholder="Nhập mã cổ phiếu (VD: HPG, VNM, ACB)")
    with col_btn:
        analyze_btn = st.form_submit_button("🚀 Truy Xuất Báo Cáo", use_container_width=True)

if analyze_btn and ticker_input:
    ticker = ticker_input.strip().upper()
    with st.spinner(f"Hệ thống đang xử lý dữ liệu cho {ticker}..."):
        try:
            report_data = load_stock_report_data(ticker)
            
            # Hàm loại bỏ từ workbook
            def clean_text(text):
                if not isinstance(text, str): return text
                return text.replace("workbook ", "").replace("Workbook ", "")

            # --- PHẦN HEADER BÁO CÁO ---
            company_name = report_data.get("company_name", ticker)
            st.markdown(f'<div class="main-title" style="font-size: 28px; margin-top: 20px;">BÁO CÁO PHÂN TÍCH: <span style="color: #fbd38d;">{company_name.upper()}</span></div>', unsafe_allow_html=True)
            
            sub_info = (
                f"<b>Mã CK:</b> {ticker} &nbsp;|&nbsp; "
                f"<b>Sàn:</b> {report_data.get('exchange', 'N/A')} &nbsp;|&nbsp; "
                f"<b>Ngành:</b> {clean_text(report_data.get('industry', 'N/A'))} &nbsp;|&nbsp; "
                f"<b>Ngày Dữ Liệu:</b> {report_data.get('data_as_of', 'N/A')}"
            )
            st.markdown(f'<div class="sub-title">{sub_info}</div>', unsafe_allow_html=True)
            
            # --- 1. TỔNG QUAN GIAO DỊCH & ĐỊNH GIÁ ---
            st.markdown('<div class="section-header">💎 1. Chỉ Số Giao Dịch & Khuyến Nghị</div>', unsafe_allow_html=True)
            
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Giá Hiện Tại", report_data.get("current_price", "N/A"))
            c2.metric("Khuyến Nghị", report_data.get("recommendation", "N/A"))
            c3.metric("Giá Mục Tiêu", report_data.get("target_price", "N/A"))
            c4.metric("Tiềm Năng", report_data.get("upside", "N/A"))

            c5, c6, c7, c8 = st.columns(4)
            c5.metric("Dải Giá 52 Tuần", report_data.get("price_52w_range", "N/A"))
            c6.metric("KLGD 20 Phiên", report_data.get("avg_volume_20d", "N/A"))
            c7.metric("Vốn Hóa", report_data.get("market_cap", "N/A"))
            c8.metric("CP Lưu Hành", report_data.get("shares_outstanding", "N/A"))

            c9, c10, c11, c12 = st.columns(4)
            c9.metric("Sở Hữu Nước Ngoài", report_data.get("foreign_ownership", "N/A"))
            c10.metric("Hệ số Beta", report_data.get("beta", "N/A"))
            c11.metric("Đầu Tư", report_data.get("investment_horizon", "N/A"))
            c12.metric("Nguồn Giá", report_data.get("price_source", "N/A"))

            # --- 2. BIỂU ĐỒ GIÁ ---
            st.markdown('<div class="section-header">📈 2. Biểu Đồ Diễn Biến Giá</div>', unsafe_allow_html=True)
            
            # Vẽ biểu đồ nến bằng Plotly thay vì SVG tĩnh
            try:
                df_all = pd.read_csv("output/stock_data.csv")
                df_stock = df_all[df_all['symbol'] == ticker].copy()
                if not df_stock.empty:
                    df_stock['date'] = pd.to_datetime(df_stock['date'])
                    df_stock = df_stock.sort_values('date')
                    
                    fig = go.Figure(data=[go.Candlestick(x=df_stock['date'],
                                    open=df_stock['open'],
                                    high=df_stock['high'],
                                    low=df_stock['low'],
                                    close=df_stock['close'],
                                    increasing_line_color='#26a69a', decreasing_line_color='#ef5350')])
                    
                    fig.update_layout(
                        template="plotly_dark",
                        margin=dict(l=20, r=20, t=20, b=20),
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        xaxis_rangeslider_visible=False,
                        height=400
                    )
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.warning("Không tìm thấy dữ liệu giá trong CSV để vẽ biểu đồ nến.")
            except Exception as e:
                # Fallback to SVG
                svg_data = report_data.get("price_chart", "")
                if svg_data:
                    st.markdown(f'<div class="image-box"><img src="{svg_data}" width="100%" /></div>', unsafe_allow_html=True)
                else:
                    st.warning("Không thể hiển thị biểu đồ.")

            # --- 3. TÓM TẮT PHÂN TÍCH & LUẬN ĐIỂM ---
            st.markdown('<div class="section-header">🧠 3. Tóm Tắt Phân Tích & Luận Điểm</div>', unsafe_allow_html=True)
            
            ai_sum = clean_text(report_data.get('ai_summary', 'Không có dữ liệu phân tích.'))
            st.markdown(f'<div class="summary-box"><b>Tổng hợp nhận định chuyên sâu:</b><br>{ai_sum}</div>', unsafe_allow_html=True)
            
            inv_thesis = clean_text(report_data.get("investment_thesis", ""))
            if inv_thesis:
                st.markdown(f'<div class="summary-box"><b>Cơ sở luận điểm:</b> {inv_thesis}</div>', unsafe_allow_html=True)

            col_points, col_risks = st.columns(2)
            with col_points:
                points_html = '<div class="summary-box"><b>✅ Điểm Nhấn Kỹ Thuật/Đầu Tư:</b><ul style="margin-top: 10px;">'
                points = report_data.get("investment_points", [])
                if points:
                    for p in points:
                        points_html += f"<li>{clean_text(p)}</li>"
                else:
                    points_html += "<li>*(Không có dữ liệu)*</li>"
                points_html += '</ul></div>'
                st.markdown(points_html, unsafe_allow_html=True)
            
            with col_risks:
                risks_html = '<div class="summary-box"><b>⚠️ Rủi Ro Cần Lưu Ý:</b><ul style="margin-top: 10px;">'
                risks = report_data.get("key_risks", [])
                if risks:
                    for r in risks:
                        risks_html += f"<li>{clean_text(r)}</li>"
                else:
                    risks_html += "<li>*(Không có dữ liệu)*</li>"
                risks_html += '</ul></div>'
                st.markdown(risks_html, unsafe_allow_html=True)

            # --- 4. DỮ LIỆU TÀI CHÍNH ---
            st.markdown('<div class="section-header">🏦 4. Dữ Liệu Tài Chính & Dự Phóng</div>', unsafe_allow_html=True)
            fin_sections = report_data.get("financial_sections", [])
            
            if fin_sections:
                years = report_data.get("financial_years", [])
                for section in fin_sections:
                    st.markdown(f"**{clean_text(section.get('label', '')).upper()}**")
                    
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

            # --- 5. TẢI BÁO CÁO PDF ---
            st.markdown('<div class="section-header">📄 5. Trích Xuất Báo Cáo PDF</div>', unsafe_allow_html=True)
            
            pdf_path = f"outputs/{ticker}_report.pdf"
            os.makedirs("outputs", exist_ok=True)
            
            pdf_data = report_data.copy()
            pdf_data["price_chart"] = "" # Workaround lỗi SVG
            generate_pdf(pdf_data, output_path=pdf_path)
            
            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()

            st.download_button(
                label="📥 TẢI XUỐNG BÁO CÁO PHÂN TÍCH (PDF)",
                data=pdf_bytes,
                file_name=f"Bao_Cao_Phan_Tich_{ticker}.pdf",
                mime="application/pdf",
                type="primary"
            )

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
elif analyze_btn and not ticker_input:
    st.warning("Vui lòng nhập mã cổ phiếu!")
else:
    # Màn hình chào mừng khi chưa nhập mã
    st.markdown("""
    <div class="welcome-box" style="margin-top: 50px;">
        <p style="color: #d6bcfa; font-size: 18px; max-width: 800px; margin: 0 auto 10px auto; line-height: 1.6;">
            Dựa trên các nguồn dữ liệu có thể khai thác như:
        </p>
        <ul style="color: #e2e8f0; font-size: 18px; max-width: 800px; margin: 0 auto 20px auto; padding-left: 40px; line-height: 1.8; list-style-type: disc;">
            <li><b>Giá và dữ liệu giao dịch</b> chứng khoán;</li>
            <li><b>Báo cáo tài chính và các chỉ số tài chính</b> doanh nghiệp niêm yết;</li>
            <li><b>Thông tin, tin tức</b> doanh nghiệp cập nhật;</li>
            <li>Báo cáo phân tích của các công ty chứng khoán – format/trình bày;</li>
            <li>Các nguồn dữ liệu tài chính và công nghệ phù hợp khác;</li>
        </ul>
        <p style="color: #e9d8fd; font-size: 18px; max-width: 800px; margin: 0 auto 10px auto; line-height: 1.6; font-weight: bold;">
            Mỗi nhóm hãy thiết kế, xây dựng và vận hành một hệ thống phân tích cơ hội đầu tư vào cổ phiếu bất kỳ; báo cáo phân tích tự động trích xuất ra PDF theo nhu cầu người dùng.
        </p>
        <p style="color: #e9d8fd; font-size: 18px; max-width: 800px; margin: 0 auto; line-height: 1.6;">
            Yêu cầu: <b>chính xác về mặt dữ liệu, kết quả phân tích đánh giá thích hợp và sự sáng tạo</b>
        </p>
    </div>
    """, unsafe_allow_html=True)
