import streamlit as st
import pandas as pd
import os
import base64
from core.report_pdf import load_stock_report_data, generate_pdf

# Cấu hình trang cơ bản
st.set_page_config(page_title="Phân Tích Cổ Phiếu", layout="wide")

st.title("Phân Tích Cổ Phiếu Tự Động")

# Ô nhập mã cổ phiếu
ticker = st.text_input("Nhập Mã cổ phiếu (VD: HPG, VNM, ACB):", value="").upper()

if st.button("Phân Tích", type="primary"):
    if not ticker:
        st.warning("Vui lòng nhập mã cổ phiếu!")
    else:
        with st.spinner(f"Đang xử lý dữ liệu cho mã {ticker}..."):
            try:
                # Gọi hàm từ core (Thành viên 4 & 5)
                report_data = load_stock_report_data(ticker)
                
                # --- Hiển thị kết quả đầy đủ trên màn hình ---
                st.header(f"Báo cáo phân tích: {report_data.get('company_name', ticker)}")
                st.write(f"**Sàn giao dịch:** {report_data.get('exchange', 'N/A')} | **Ngày cập nhật:** {report_data.get('data_as_of', 'N/A')}")
                
                st.divider()

                # --- 1. Tổng quan giá và Giao dịch ---
                st.subheader("1. Tổng quan Giao dịch")
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Giá hiện tại", report_data.get("current_price", "N/A"))
                col2.metric("Biến động 52 tuần", report_data.get("price_52w_range", "N/A"))
                col3.metric("KLGD BQ 20 phiên", report_data.get("avg_volume_20d", "N/A"))
                col4.metric("Beta", report_data.get("beta", "N/A"))

                st.divider()

                # --- 2. Nhận định và Điểm nhấn đầu tư ---
                st.subheader("2. Nhận định & Phân tích")
                st.info(f"**Tóm tắt AI:**\n\n{report_data.get('ai_summary', 'Chưa có nhận định.')}")
                
                col_inv, col_risk = st.columns(2)
                with col_inv:
                    st.write("**Điểm nhấn kỹ thuật / đầu tư:**")
                    for point in report_data.get("investment_points", []):
                        st.write(f"- {point}")
                with col_risk:
                    st.write("**Rủi ro cần lưu ý:**")
                    for risk in report_data.get("key_risks", []):
                        st.write(f"- {risk}")

                st.divider()

                # --- 3. Dữ liệu Tài chính ---
                st.subheader("3. Dữ liệu Tài chính")
                fin_sections = report_data.get("financial_sections", [])
                if fin_sections:
                    years = report_data.get("financial_years", [])
                    for section in fin_sections:
                        st.write(f"**{section.get('label', '')}**")
                        # Chuyển đổi thành DataFrame để hiển thị bảng
                        table_data = []
                        for row in section.get("rows", []):
                            row_dict = {"Chỉ tiêu": row.get("label", "")}
                            for i, val in enumerate(row.get("values", [])):
                                if i < len(years):
                                    row_dict[str(years[i])] = val
                            table_data.append(row_dict)
                        if table_data:
                            st.dataframe(pd.DataFrame(table_data), use_container_width=True)
                else:
                    st.write("Không có dữ liệu tài chính.")

                st.divider()

                # --- Nút Tải Báo cáo PDF ---
                st.subheader("Tải Báo cáo PDF")
                pdf_path = f"outputs/{ticker}_report.pdf"
                os.makedirs("outputs", exist_ok=True)
                
                # Fix lỗi Division by Zero của xhtml2pdf với file SVG:
                # Tạo bản sao dữ liệu và xóa price_chart để xhtml2pdf không render SVG lỗi
                pdf_data = report_data.copy()
                pdf_data["price_chart"] = ""

                # Tạo file PDF với dữ liệu đã được xử lý
                generate_pdf(pdf_data, output_path=pdf_path)
                
                with open(pdf_path, "rb") as f:
                    pdf_bytes = f.read()

                st.success("Tạo báo cáo PDF thành công! Nhấn nút bên dưới để tải về.")
                st.download_button(
                    label="⬇️ Tải Báo cáo PDF",
                    data=pdf_bytes,
                    file_name=f"Bao_Cao_{ticker}.pdf",
                    mime="application/pdf"
                )

            except Exception as e:
                st.error(f"Lỗi khi xử lý mã {ticker}: {e}")
