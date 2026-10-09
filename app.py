import streamlit as st
import pandas as pd
import os
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
                
                # --- Hiển thị kết quả nhanh trên màn hình ---
                st.subheader(f"Kết quả phân tích: {ticker}")
                
                # Trích xuất 1 vài chỉ số tài chính cơ bản để hiển thị nhanh
                pe = "N/A"
                pb = "N/A"
                roe = "N/A"
                for section in report_data.get("financial_sections", []):
                    for row in section["rows"]:
                        if row["label"] == "P/E": pe = row["values"][0] if row["values"] else "N/A"
                        if row["label"] == "P/B": pb = row["values"][0] if row["values"] else "N/A"
                        if row["label"] == "ROE": roe = row["values"][0] if row["values"] else "N/A"

                col1, col2, col3 = st.columns(3)
                col1.metric("Giá hiện tại", report_data.get("current_price", "N/A"))
                col2.metric("P/E", pe)
                col3.metric("ROE", roe)

                st.write("**Nhận định tóm tắt:**")
                st.info(report_data.get("ai_summary", "Chưa có nhận định."))

                # --- Nút Tải Báo cáo PDF ---
                pdf_path = f"outputs/{ticker}_report.pdf"
                os.makedirs("outputs", exist_ok=True)
                
                # Tạo file PDF
                generate_pdf(report_data, output_path=pdf_path)
                
                with open(pdf_path, "rb") as f:
                    pdf_bytes = f.read()

                st.download_button(
                    label="📥 Tải Báo cáo PDF",
                    data=pdf_bytes,
                    file_name=f"Bao_Cao_{ticker}.pdf",
                    mime="application/pdf"
                )

            except Exception as e:
                st.error(f"Lỗi khi xử lý mã {ticker}: {e}")
