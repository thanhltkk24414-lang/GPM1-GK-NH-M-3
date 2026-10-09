import streamlit as st
import pandas as pd
import os
from core.report_pdf import load_stock_report_data, generate_pdf

# Cấu hình trang với giao diện rộng
st.set_page_config(page_title="Hệ Thống Phân Tích Cổ Phiếu", layout="wide")

# CSS: Nền tím than đậm, đổ bóng, hiệu ứng hover pop-up cho mọi thành phần
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
    [data-testid="stMetric"], .summary-box, .welcome-box, div[data-testid="stDataFrame"], .image-box {
        background-color: rgba(45, 27, 84, 0.6) !important;
        border-radius: 12px !important;
        padding: 15px !important;
        box-shadow: 0 4px 12px rgba(0,0,0,0.4) !important;
        transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1) !important;
        border: 1px solid rgba(159, 122, 234, 0.2) !important;
    }

    [data-testid="stMetric"]:hover, .summary-box:hover, .welcome-box:hover, div[data-testid="stDataFrame"]:hover, .image-box:hover {
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

    /* Input & Buttons */
    [data-testid="stTextInput"] input {
        background-color: rgba(255, 255, 255, 0.05);
        color: #fff;
        border: 2px solid rgba(159, 122, 234, 0.4);
        border-radius: 10px;
        padding: 10px 15px;
        transition: all 0.3s ease;
        box-shadow: 0 4px 6px rgba(0,0,0,0.2);
    }
    [data-testid="stTextInput"] input:focus, [data-testid="stTextInput"] input:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 15px rgba(159, 122, 234, 0.6);
        border-color: #b794f4;
    }

    [data-testid="stButton"] button {
        background: linear-gradient(135deg, #6b46c1 0%, #805ad5 100%);
        color: white;
        border: none;
        border-radius: 10px;
        font-weight: bold;
        box-shadow: 0 4px 10px rgba(107, 70, 193, 0.5);
        transition: all 0.3s ease;
        height: auto;
        padding: 12px 0;
    }
    [data-testid="stButton"] button:hover {
        transform: scale(1.05) translateY(-3px);
        box-shadow: 0 10px 20px rgba(159, 122, 234, 0.8);
        background: linear-gradient(135deg, #805ad5 0%, #9f7aea 100%);
        color: white;
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
    
    /* Markdown text */
    .stMarkdown p, .stMarkdown li {
        color: #e2e8f0;
        font-size: 16px;
        line-height: 1.6;
    }
    
    /* Make SVG chart look good on dark mode */
    .image-box img {
        filter: drop-shadow(0 0 8px rgba(255,255,255,0.2));
        border-radius: 8px;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-title">HỆ THỐNG TỔNG HỢP & PHÂN TÍCH CỔ PHIẾU</div>', unsafe_allow_html=True)

# Giao diện Nhập liệu
col_input, col_btn, _ = st.columns([2, 1, 3])
with col_input:
    ticker = st.text_input("MÃ CHỨNG KHOÁN:", value="", label_visibility="collapsed", placeholder="Nhập mã cổ phiếu (VD: HPG, VNM, ACB)").upper()
with col_btn:
    analyze_btn = st.button("🚀 Truy Xuất Báo Cáo", type="primary", use_container_width=True)

# Phân tích ngay khi có mã cổ phiếu
if ticker:
    with st.spinner(f"Hệ thống đang xử lý và dựng 3D Dashboard cho {ticker}..."):
        try:
            report_data = load_stock_report_data(ticker)
            
            # --- PHẦN HEADER BÁO CÁO ---
            company_name = report_data.get("company_name", ticker)
            st.markdown(f'<div class="main-title" style="font-size: 28px;">BÁO CÁO PHÂN TÍCH: <span style="color: #fbd38d;">{company_name.upper()}</span></div>', unsafe_allow_html=True)
            
            sub_info = (
                f"<b>Mã CK:</b> {ticker} &nbsp;|&nbsp; "
                f"<b>Sàn:</b> {report_data.get('exchange', 'N/A')} &nbsp;|&nbsp; "
                f"<b>Ngành:</b> {report_data.get('industry', 'N/A')} &nbsp;|&nbsp; "
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

            st.write("") 

            c5, c6, c7, c8 = st.columns(4)
            c5.metric("Dải Giá 52 Tuần", report_data.get("price_52w_range", "N/A"))
            c6.metric("KLGD 20 Phiên", report_data.get("avg_volume_20d", "N/A"))
            c7.metric("Vốn Hóa", report_data.get("market_cap", "N/A"))
            c8.metric("CP Lưu Hành", report_data.get("shares_outstanding", "N/A"))

            st.write("") 

            c9, c10, c11, c12 = st.columns(4)
            c9.metric("Sở Hữu Nước Ngoài", report_data.get("foreign_ownership", "N/A"))
            c10.metric("Hệ số Beta", report_data.get("beta", "N/A"))
            c11.metric("Đầu Tư", report_data.get("investment_horizon", "N/A"))
            c12.metric("Nguồn Giá", report_data.get("price_source", "N/A"))

            # --- 2. BIỂU ĐỒ GIÁ ---
            svg_data = report_data.get("price_chart", "")
            if svg_data:
                st.markdown('<div class="section-header">📈 2. Biểu Đồ Diễn Biến Giá</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="image-box"><img src="{svg_data}" width="100%" /></div>', unsafe_allow_html=True)

            # --- 3. TÓM TẮT PHÂN TÍCH & LUẬN ĐIỂM ---
            st.markdown('<div class="section-header">🧠 3. Tóm Tắt Phân Tích & Luận Điểm</div>', unsafe_allow_html=True)
            
            ai_sum = report_data.get('ai_summary', 'Không có dữ liệu phân tích.')
            st.markdown(f'<div class="summary-box"><b>Tổng hợp nhận định chuyên sâu:</b><br>{ai_sum}</div>', unsafe_allow_html=True)
            
            inv_thesis = report_data.get("investment_thesis", "")
            if inv_thesis:
                st.markdown(f'<div class="summary-box"><b>Cơ sở luận điểm:</b> {inv_thesis}</div>', unsafe_allow_html=True)

            col_points, col_risks = st.columns(2)
            with col_points:
                st.markdown('<div class="summary-box"><b>✅ Điểm Nhấn Kỹ Thuật/Đầu Tư:</b><br><br>', unsafe_allow_html=True)
                points = report_data.get("investment_points", [])
                if points:
                    for p in points:
                        st.markdown(f"- {p}")
                else:
                    st.markdown("- *(Không có dữ liệu)*")
                st.markdown('</div>', unsafe_allow_html=True)
            
            with col_risks:
                st.markdown('<div class="summary-box"><b>⚠️ Rủi Ro Cần Lưu Ý:</b><br><br>', unsafe_allow_html=True)
                risks = report_data.get("key_risks", [])
                if risks:
                    for r in risks:
                        st.markdown(f"- {r}")
                else:
                    st.markdown("- *(Không có dữ liệu)*")
                st.markdown('</div>', unsafe_allow_html=True)

            # --- 4. DỮ LIỆU TÀI CHÍNH ---
            st.markdown('<div class="section-header">🏦 4. Dữ Liệu Tài Chính & Dự Phóng</div>', unsafe_allow_html=True)
            fin_sections = report_data.get("financial_sections", [])
            
            if fin_sections:
                years = report_data.get("financial_years", [])
                for section in fin_sections:
                    st.markdown(f"**{section.get('label', '').upper()}**")
                    table_data = []
                    for row in section.get("rows", []):
                        row_dict = {"Chỉ Tiêu": row.get("label", "")}
                        for i, val in enumerate(row.get("values", [])):
                            if i < len(years):
                                row_dict[str(years[i])] = val
                        table_data.append(row_dict)
                    
                    if table_data:
                        df = pd.DataFrame(table_data)
                        st.dataframe(df, use_container_width=True, hide_index=True)
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
else:
    # Màn hình chào mừng khi chưa nhập mã
    st.markdown("""
    <div class="welcome-box" style="text-align: center; margin-top: 50px;">
        <h2 style="color: #e9d8fd; font-family: 'Segoe UI', Tahoma, sans-serif; margin-bottom: 20px; font-weight: 800;">🌌 HỆ THỐNG PHÂN TÍCH ĐÃ SẴN SÀNG</h2>
        <p style="color: #d6bcfa; font-size: 18px; max-width: 600px; margin: 0 auto; line-height: 1.6;">
            Vui lòng nhập mã chứng khoán (VD: <b>HPG, VNM, FPT</b>) vào ô tìm kiếm để hệ thống khởi chạy thuật toán tổng hợp:
        </p>
        <ul style="color: #e2e8f0; font-size: 17px; text-align: left; max-width: 450px; margin: 30px auto; line-height: 2;">
            <li>✨ Trích xuất Dữ liệu Giao dịch & Định giá Real-time</li>
            <li>✨ Phân tích Biểu đồ Diễn biến giá Tự động</li>
            <li>✨ Tổng hợp Dữ liệu Tài chính chuyên sâu</li>
            <li>✨ Kết xuất Báo cáo Bản in PDF chuẩn Chuyên gia</li>
        </ul>
        <p style="color: #b794f4; font-size: 14px; margin-top: 20px;"><em>Trải nghiệm phân tích thông minh, siêu tốc và bắt mắt.</em></p>
    </div>
    """, unsafe_allow_html=True)
