import streamlit as st
import pandas as pd
import os
import base64
from core.report_pdf import load_stock_report_data, generate_pdf

# Cấu hình trang
st.set_page_config(page_title="Phân Tích Cổ Phiếu", page_icon="📈", layout="wide")

# CSS tùy chỉnh để làm giao diện bắt mắt hơn
st.markdown("""
<style>
    .report-header {
        font-size: 32px;
        font-weight: bold;
        color: #1E3A8A;
        margin-bottom: 5px;
    }
    .report-subtitle {
        font-size: 16px;
        color: #4B5563;
        margin-bottom: 20px;
    }
    .section-title {
        color: #1D4ED8;
        border-bottom: 2px solid #BFDBFE;
        padding-bottom: 5px;
        margin-top: 30px;
        margin-bottom: 15px;
    }
</style>
""", unsafe_allow_html=True)

st.title("📈 Bảng Điều Khiển Phân Tích Cổ Phiếu")
st.caption("Nhập mã cổ phiếu để xem báo cáo phân tích tự động và tải xuống bản PDF.")

# Ô nhập mã cổ phiếu và nút với layout đẹp hơn
col_input, col_btn, _ = st.columns([2, 1, 3])
with col_input:
    ticker = st.text_input("Mã cổ phiếu (VD: HPG, VNM, ACB):", value="", label_visibility="collapsed", placeholder="Nhập mã cổ phiếu (VD: HPG)").upper()
with col_btn:
    analyze_btn = st.button("🔍 Phân Tích Ngay", type="primary", use_container_width=True)

if analyze_btn:
    if not ticker:
        st.warning("⚠️ Vui lòng nhập mã cổ phiếu!")
    else:
        with st.spinner(f"⏳ Đang thu thập và phân tích dữ liệu cho mã {ticker}..."):
            try:
                # Lấy dữ liệu
                report_data = load_stock_report_data(ticker)
                
                # --- Tiêu đề Báo cáo ---
                st.markdown(f'<div class="report-header">Báo cáo phân tích: {report_data.get("company_name", ticker)}</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="report-subtitle">🏢 Sàn giao dịch: <b>{report_data.get("exchange", "N/A")}</b> &nbsp;|&nbsp; 📅 Ngày cập nhật: <b>{report_data.get("data_as_of", "N/A")}</b></div>', unsafe_allow_html=True)
                
                # --- 1. Tổng quan Giao dịch ---
                st.markdown('<h3 class="section-title">📊 1. Tổng quan Giao dịch</h3>', unsafe_allow_html=True)
                with st.container():
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("💰 Giá hiện tại", report_data.get("current_price", "N/A"))
                    m2.metric("📈 Biến động 52 tuần", report_data.get("price_52w_range", "N/A"))
                    m3.metric("🔄 KLGD BQ 20 phiên", report_data.get("avg_volume_20d", "N/A"))
                    m4.metric("⚖️ Beta", report_data.get("beta", "N/A"))

                # --- 2. Nhận định và Điểm nhấn đầu tư ---
                st.markdown('<h3 class="section-title">💡 2. Nhận định & Phân tích Tự động</h3>', unsafe_allow_html=True)
                
                # Highlight phần Tóm tắt AI
                st.success(f"**🤖 AI Nhận định:**\n\n{report_data.get('ai_summary', 'Chưa có nhận định.')}", icon="✨")
                
                col_inv, col_risk = st.columns(2)
                with col_inv:
                    with st.container(border=True):
                        st.markdown("#### 🎯 Điểm nhấn Kỹ thuật & Đầu tư")
                        points = report_data.get("investment_points", [])
                        if points:
                            for point in points:
                                st.write(f"✅ {point}")
                        else:
                            st.write("Không có điểm nhấn nổi bật.")

                with col_risk:
                    with st.container(border=True):
                        st.markdown("#### ⚠️ Rủi ro Cần lưu ý")
                        risks = report_data.get("key_risks", [])
                        if risks:
                            for risk in risks:
                                st.write(f"🔻 {risk}")
                        else:
                            st.write("Không có rủi ro đáng kể.")

                # --- 3. Dữ liệu Tài chính ---
                st.markdown('<h3 class="section-title">🏦 3. Dữ liệu Tài chính Trọng yếu</h3>', unsafe_allow_html=True)
                fin_sections = report_data.get("financial_sections", [])
                
                if fin_sections:
                    years = report_data.get("financial_years", [])
                    # Dùng columns để chia đôi các bảng nếu có nhiều bảng
                    for section in fin_sections:
                        st.markdown(f"**{section.get('label', '')}**")
                        table_data = []
                        for row in section.get("rows", []):
                            row_dict = {"Chỉ tiêu": row.get("label", "")}
                            for i, val in enumerate(row.get("values", [])):
                                if i < len(years):
                                    row_dict[str(years[i])] = val
                            table_data.append(row_dict)
                        
                        if table_data:
                            df = pd.DataFrame(table_data)
                            # Hiển thị bảng đẹp hơn và ẩn index
                            st.dataframe(df, use_container_width=True, hide_index=True)
                else:
                    st.info("Không có dữ liệu tài chính cho mã này.")

                # --- Nút Tải Báo cáo PDF ---
                st.markdown('<h3 class="section-title">📄 Tải Báo cáo PDF</h3>', unsafe_allow_html=True)
                pdf_path = f"outputs/{ticker}_report.pdf"
                os.makedirs("outputs", exist_ok=True)
                
                # Fix lỗi SVG cho PDF
                pdf_data = report_data.copy()
                pdf_data["price_chart"] = ""

                # Tạo PDF
                generate_pdf(pdf_data, output_path=pdf_path)
                
                with open(pdf_path, "rb") as f:
                    pdf_bytes = f.read()

                st.download_button(
                    label="⬇️ TẢI XUỐNG BÁO CÁO PDF DÀNH CHO NHÀ ĐẦU TƯ",
                    data=pdf_bytes,
                    file_name=f"Bao_Cao_{ticker}.pdf",
                    mime="application/pdf",
                    type="primary"
                )

            except Exception as e:
                st.error(f"Đã xảy ra lỗi khi xử lý mã {ticker}: {e}")
