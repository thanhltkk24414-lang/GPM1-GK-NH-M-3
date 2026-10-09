import streamlit as st
import pandas as pd
import os
from core.report_pdf import load_stock_report_data, generate_pdf

# Cấu hình trang với giao diện rộng, tiêu đề chuyên nghiệp
st.set_page_config(page_title="Hệ Thống Phân Tích Cổ Phiếu", layout="wide")

# CSS để có giao diện chuyên nghiệp, loại bỏ màu mè, sử dụng tông màu Corporate (Xanh đậm, Xám, Trắng)
st.markdown("""
<style>
    .main-title {
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        color: #1a365d;
        font-size: 32px;
        font-weight: 700;
        margin-bottom: 0px;
    }
    .sub-title {
        color: #4a5568;
        font-size: 16px;
        margin-bottom: 25px;
    }
    .section-header {
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        color: #2b6cb0;
        font-size: 22px;
        font-weight: 600;
        border-bottom: 2px solid #e2e8f0;
        padding-bottom: 8px;
        margin-top: 35px;
        margin-bottom: 20px;
        text-transform: uppercase;
    }
    .data-label {
        font-weight: 600;
        color: #4a5568;
    }
    .summary-box {
        background-color: #f7fafc;
        border-left: 4px solid #3182ce;
        padding: 15px 20px;
        margin-bottom: 20px;
        font-size: 15px;
        line-height: 1.6;
        color: #2d3748;
    }
    .footer-text {
        font-size: 13px;
        color: #718096;
        margin-top: 50px;
        border-top: 1px solid #e2e8f0;
        padding-top: 15px;
    }
    /* Chỉnh sửa Metric của Streamlit cho pro hơn */
    [data-testid="stMetricValue"] {
        font-size: 24px;
        font-weight: bold;
        color: #1a202c;
    }
    [data-testid="stMetricLabel"] {
        font-size: 14px;
        font-weight: 600;
        color: #4a5568;
    }
</style>
""", unsafe_allow_html=True)

# Giao diện Nhập liệu
col_input, col_btn, _ = st.columns([2, 1, 3])
with col_input:
    ticker = st.text_input("MÃ CHỨNG KHOÁN:", value="", label_visibility="collapsed", placeholder="Nhập mã cổ phiếu (VD: HPG, VNM, ACB)").upper()
with col_btn:
    analyze_btn = st.button("Truy Xuất Báo Cáo", type="primary", use_container_width=True)

if analyze_btn:
    if not ticker:
        st.warning("Vui lòng nhập mã cổ phiếu cần phân tích.")
    else:
        with st.spinner(f"Hệ thống đang tổng hợp dữ liệu cho {ticker}..."):
            try:
                # Trích xuất dữ liệu
                report_data = load_stock_report_data(ticker)
                
                # --- PHẦN HEADER BÁO CÁO ---
                company_name = report_data.get("company_name", ticker)
                st.markdown(f'<div class="main-title">BÁO CÁO PHÂN TÍCH CỔ PHIẾU: {company_name.upper()}</div>', unsafe_allow_html=True)
                
                sub_info = (
                    f"<b>Mã CK:</b> {ticker} &nbsp;|&nbsp; "
                    f"<b>Sàn Giao Dịch:</b> {report_data.get('exchange', 'N/A')} &nbsp;|&nbsp; "
                    f"<b>Ngành:</b> {report_data.get('industry', 'N/A')} &nbsp;|&nbsp; "
                    f"<b>Ngày Dữ Liệu:</b> {report_data.get('data_as_of', 'N/A')}"
                )
                st.markdown(f'<div class="sub-title">{sub_info}</div>', unsafe_allow_html=True)
                
                # --- 1. TỔNG QUAN GIAO DỊCH & ĐỊNH GIÁ ---
                st.markdown('<div class="section-header">1. Chỉ Số Giao Dịch & Khuyến Nghị</div>', unsafe_allow_html=True)
                
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Giá Hiện Tại", report_data.get("current_price", "N/A"))
                c2.metric("Khuyến Nghị", report_data.get("recommendation", "N/A"))
                c3.metric("Giá Mục Tiêu", report_data.get("target_price", "N/A"))
                c4.metric("Tiềm Năng Tăng Giá", report_data.get("upside", "N/A"))

                st.write("") # Khảng cách

                c5, c6, c7, c8 = st.columns(4)
                c5.metric("Dải Giá 52 Tuần", report_data.get("price_52w_range", "N/A"))
                c6.metric("KLGD BQ 20 Phiên", report_data.get("avg_volume_20d", "N/A"))
                c7.metric("Vốn Hóa Thị Trường", report_data.get("market_cap", "N/A"))
                c8.metric("Cổ Phiếu Lưu Hành", report_data.get("shares_outstanding", "N/A"))

                st.write("") 

                c9, c10, c11, c12 = st.columns(4)
                c9.metric("Sở Hữu Nước Ngoài", report_data.get("foreign_ownership", "N/A"))
                c10.metric("Hệ số Beta", report_data.get("beta", "N/A"))
                c11.metric("Chân Trời Đầu Tư", report_data.get("investment_horizon", "N/A"))
                c12.metric("Mức Giá Nguồn", report_data.get("price_source", "N/A"))

                # --- 2. BIỂU ĐỒ GIÁ ---
                svg_data = report_data.get("price_chart", "")
                if svg_data:
                    st.markdown('<div class="section-header">2. Biểu Đồ Diễn Biến Giá</div>', unsafe_allow_html=True)
                    st.markdown(f'<div style="text-align: center; border: 1px solid #e2e8f0; padding: 10px; background: white;"><img src="{svg_data}" width="100%" /></div>', unsafe_allow_html=True)

                # --- 3. TÓM TẮT PHÂN TÍCH & LUẬN ĐIỂM ---
                st.markdown('<div class="section-header">3. Tóm Tắt Phân Tích & Luận Điểm Đầu Tư</div>', unsafe_allow_html=True)
                
                # Loại bỏ chữ "AI" và hiển thị phân tích trong khối xám trang trọng
                ai_sum = report_data.get('ai_summary', 'Không có dữ liệu phân tích.')
                st.markdown(f'<div class="summary-box"><b>Tổng hợp phân tích:</b><br>{ai_sum}</div>', unsafe_allow_html=True)
                
                inv_thesis = report_data.get("investment_thesis", "")
                if inv_thesis:
                    st.write(f"**Cơ sở luận điểm:** {inv_thesis}")

                col_points, col_risks = st.columns(2)
                with col_points:
                    st.markdown("**Các Điểm Nhấn Kỹ Thuật/Đầu Tư:**")
                    points = report_data.get("investment_points", [])
                    if points:
                        for p in points:
                            st.markdown(f"- {p}")
                    else:
                        st.markdown("- *(Không có dữ liệu)*")
                
                with col_risks:
                    st.markdown("**Rủi Ro Cần Lưu Ý:**")
                    risks = report_data.get("key_risks", [])
                    if risks:
                        for r in risks:
                            st.markdown(f"- {r}")
                    else:
                        st.markdown("- *(Không có dữ liệu)*")

                # --- 4. DỮ LIỆU TÀI CHÍNH ---
                st.markdown('<div class="section-header">4. Dữ Liệu Tài Chính & Dự Phóng</div>', unsafe_allow_html=True)
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
                st.markdown('<div class="section-header">5. Trích Xuất Báo Cáo PDF</div>', unsafe_allow_html=True)
                
                pdf_path = f"outputs/{ticker}_report.pdf"
                os.makedirs("outputs", exist_ok=True)
                
                pdf_data = report_data.copy()
                pdf_data["price_chart"] = "" # Workaround lỗi chia cho 0 với xhtml2pdf

                generate_pdf(pdf_data, output_path=pdf_path)
                
                with open(pdf_path, "rb") as f:
                    pdf_bytes = f.read()

                st.download_button(
                    label="📥 TẢI XUỐNG BÁO CÁO PHÂN TÍCH (PDF)",
                    data=pdf_bytes,
                    file_name=f"Bao_Cao_Phan_Tich_{ticker}.pdf",
                    mime="application/pdf",
                )

                # --- FOOTER ---
                footer_html = (
                    f"<b>Chuyên viên tổng hợp:</b> {report_data.get('analyst_name', 'Hệ thống tự động')} "
                    f"({report_data.get('analyst_contact', '')})<br>"
                    f"<b>Nguồn dữ liệu:</b> {report_data.get('data_sources', 'N/A')}<br>"
                    f"<i>Ngày xuất báo cáo: {report_data.get('report_date', 'N/A')}</i>"
                )
                st.markdown(f'<div class="footer-text">{footer_html}</div>', unsafe_allow_html=True)

            except Exception as e:
                st.error(f"Đã xảy ra lỗi hệ thống trong quá trình xử lý: {e}")
