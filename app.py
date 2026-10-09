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
    [data-testid="stMetric"], .summary-box, .welcome-box, .image-box {
        background-color: var(--secondary-background-color) !important;
        background-image: linear-gradient(135deg, rgba(255, 255, 255, 0.03) 0%, rgba(0, 0, 0, 0.05) 100%) !important;
        border-radius: 12px !important;
        padding: 20px !important;
        box-shadow: 0 8px 20px rgba(0,0,0,0.15) !important;
        transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1) !important;
        border: 1px solid rgba(138, 43, 226, 0.3) !important;
        margin-bottom: 15px;
    }

    [data-testid="stMetric"]:hover, .summary-box:hover, .welcome-box:hover, .image-box:hover {
        transform: translateY(-6px) scale(1.02) !important;
        box-shadow: 0 15px 30px rgba(138, 43, 226, 0.2) !important;
        z-index: 10;
        border: 1px solid rgba(138, 43, 226, 0.6) !important;
    }

    /* Metric Text Styling */
    [data-testid="stMetricValue"] {
        color: #FFFFFF !important;
        font-size: 28px !important;
        font-weight: 800 !important;
        text-shadow: 0 1px 2px rgba(0,0,0,0.1);
    }
    [data-testid="stMetricLabel"] {
        color: #d69e2e !important; /* In vàng các nhãn metric */
        opacity: 0.85;
        font-size: 15px !important;
        font-weight: 600 !important;
    }

    /* In vàng các thẻ <b> (tiêu đề nhỏ) trong hộp tóm tắt và phụ đề */
    .summary-box b, .sub-title b, .stMarkdown strong, .stMarkdown b {
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
        color: #FFFFFF;
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
        color: #FFFFFF;
        opacity: 0.6;
        margin-top: 50px;
        border-top: 1px solid rgba(138, 43, 226, 0.3);
        padding-top: 20px;
        text-align: center;
    }
    
    .stMarkdown p, .stMarkdown li {
        color: #FFFFFF;
        font-size: 16px;
        line-height: 1.6;
    }
    
    .custom-table {
        width: 100%;
        border-collapse: collapse;
        color: #FFFFFF;
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
    
    /* 1. Giãn chữ, IN ĐẬM và đổ bóng TRẮNG mặc định cho MỌI YẾU TỐ CHỨA TEXT */
    p, li, span, th, td, h1, h2, h3, h4, label, div {
        letter-spacing: 1.2px !important;
    }
    
    p, li, span, th, td, h1, h2, h3, h4, label {
        font-weight: 800 !important;
        text-shadow: 0 0 6px rgba(255, 255, 255, 0.5), 0 1px 3px rgba(0, 0, 0, 0.5) !important;
        transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
    }
    
    /* Ẩn dòng chữ Press Enter to submit form bị đè lên ô input */
    [data-testid="InputInstructions"] {
        display: none !important;
    }

    /* 2. Pop up mọi text khi rê chuột */
    p:hover, li:hover, th:hover, td:hover, h1:hover, h2:hover, h3:hover, label:hover {
        transform: translateY(-2px) scale(1.01);
        text-shadow: 0 0 8px rgba(138, 43, 226, 0.3);
        color: #d69e2e !important;
        z-index: 50;
        position: relative;
    }

    /* 3. Hiệu ứng hover từng chữ cho phần Intro */
    .hover-word {
        display: inline-block;
        transition: transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275), text-shadow 0.3s ease, color 0.3s ease;
    }
    .hover-word:hover {
        transform: translateY(-5px) scale(1.1);
        text-shadow: 0 5px 15px rgba(138, 43, 226, 0.4);
        color: #d69e2e !important;
        cursor: default;
        z-index: 100;
        position: relative;
    }

    /* 4. Đổ bóng lấp lánh liên tục và hiệu ứng shine cho các hộp (Box) ở vùng nền */
    [data-testid="stMetric"], .summary-box, .welcome-box, .image-box, table.custom-table {
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
    [data-testid="stMetric"]::before, .summary-box::before, .welcome-box::before, .image-box::before {
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
    .welcome-box p, .welcome-box ul, .summary-box *, [data-testid="stMetric"] * {
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
    # Tách chữ và bọc thẻ span, bỏ qua khoảng trắng thừa
    return ' '.join([f'<span class="hover-word">{w}</span>' for w in text.split()])

st.markdown(f'<div class="main-title">{hoverify("HỆ THỐNG PHÂN TÍCH CƠ HỘI ĐẦU TƯ CỔ PHIẾU")}</div>', unsafe_allow_html=True)

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
            st.markdown(f'<div class="main-title" style="font-size: 28px; margin-top: 20px;">{hoverify("BÁO CÁO PHÂN TÍCH:")} {hoverify(company_name.upper())}</div>', unsafe_allow_html=True)
            
            sub_info = (
                f"<b>{hoverify('Mã CK:')}</b> {hoverify(ticker)} &nbsp;|&nbsp; "
                f"<b>{hoverify('Sàn:')}</b> {hoverify(report_data.get('exchange', 'N/A'))} &nbsp;|&nbsp; "
                f"<b>{hoverify('Ngành:')}</b> {hoverify(clean_text(report_data.get('industry', 'N/A')))} &nbsp;|&nbsp; "
                f"<b>{hoverify('Ngày Dữ Liệu:')}</b> {hoverify(report_data.get('data_as_of', 'N/A'))}"
            )
            st.markdown(f'<div class="sub-title">{sub_info}</div>', unsafe_allow_html=True)
            
            # --- TẠO VÀ TẢI BÁO CÁO PDF (ĐƯA LÊN ĐẦU) ---
            pdf_path = f"outputs/{ticker}_report.pdf"
            os.makedirs("outputs", exist_ok=True)
            
            pdf_data = report_data.copy()
            pdf_data["price_chart"] = "" # Workaround lỗi SVG
            generate_pdf(pdf_data, output_path=pdf_path)
            
            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()

            col_empty1, col_dl, col_empty2 = st.columns([1, 1, 1])
            with col_dl:
                st.download_button(
                    label="📥 TẢI XUỐNG BÁO CÁO PHÂN TÍCH (PDF)",
                    data=pdf_bytes,
                    file_name=f"Bao_Cao_Phan_Tich_{ticker}.pdf",
                    mime="application/pdf",
                    type="primary",
                    use_container_width=True
                )
            
            st.markdown("<hr style='border: 1px solid rgba(138, 43, 226, 0.2); margin-top: 10px; margin-bottom: 20px;'>", unsafe_allow_html=True)

            # --- 1. TỔNG QUAN GIAO DỊCH & ĐỊNH GIÁ ---
            st.markdown(f'<div class="section-header">💎 {hoverify("1. Chỉ Số Giao Dịch & Khuyến Nghị")}</div>', unsafe_allow_html=True)
            
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
            st.markdown(f'<div class="section-header">📈 {hoverify("2. Biểu Đồ Diễn Biến Giá")}</div>', unsafe_allow_html=True)
            
            # Vẽ biểu đồ nến bằng Plotly thay vì SVG tĩnh
            try:
                df_all = pd.read_csv("output/stock_data.csv")
                df_stock = df_all[df_all['symbol'] == ticker].copy()
                if not df_stock.empty:
                    df_stock['date'] = pd.to_datetime(df_stock['date'])
                    df_stock = df_stock.sort_values('date')
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
                    
                    min_price = df_stock['low'].min() * 0.8
                    max_price = df_stock['high'].max() * 1.2
                    
                    min_date = df_stock['date'].min() - pd.Timedelta(days=10)
                    max_date = df_stock['date'].max() + pd.Timedelta(days=10)

                    fig.update_layout(
                        template="plotly_dark",
                        margin=dict(l=20, r=20, t=20, b=20),
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
                    st.plotly_chart(fig, use_container_width=True, config={'scrollZoom': True, 'displayModeBar': False})
                else:
                    st.warning("Không tìm thấy dữ liệu giá trong CSV để vẽ biểu đồ nến.")
            except Exception as e:
                # Fallback to SVG
                st.error(f"Error drawing Plotly: {e}")
                svg_data = report_data.get("price_chart", "")
                if svg_data:
                    st.markdown(f'<div class="image-box"><img src="{svg_data}" width="100%" /></div>', unsafe_allow_html=True)
                else:
                    st.warning("Không thể hiển thị biểu đồ.")

            # --- 3. TÓM TẮT PHÂN TÍCH & LUẬN ĐIỂM ---
            st.markdown(f'<div class="section-header">🧠 {hoverify("3. Tóm Tắt Phân Tích & Luận Điểm")}</div>', unsafe_allow_html=True)
            
            ai_sum = clean_text(report_data.get('ai_summary', 'Không có dữ liệu phân tích.'))
            st.markdown(f'<div class="summary-box"><b>{hoverify("Tổng hợp nhận định chuyên sâu:")}</b><br>{hoverify(ai_sum)}</div>', unsafe_allow_html=True)
            
            inv_thesis = clean_text(report_data.get("investment_thesis", ""))
            if inv_thesis:
                st.markdown(f'<div class="summary-box"><b>{hoverify("Cơ sở luận điểm:")}</b> {hoverify(inv_thesis)}</div>', unsafe_allow_html=True)

            col_points, col_risks = st.columns(2)
            with col_points:
                points_html = f'<div class="summary-box"><b>✅ {hoverify("Điểm Nhấn Kỹ Thuật/Đầu Tư:")}</b><ul style="margin-top: 10px;">'
                points = report_data.get("investment_points", [])
                if points:
                    for p in points:
                        points_html += f"<li>{hoverify(clean_text(p))}</li>"
                else:
                    points_html += f"<li>{hoverify('*(Không có dữ liệu)*')}</li>"
                points_html += '</ul></div>'
                st.markdown(points_html, unsafe_allow_html=True)
            
            with col_risks:
                risks_html = f'<div class="summary-box"><b>⚠️ {hoverify("Rủi Ro Cần Lưu Ý:")}</b><ul style="margin-top: 10px;">'
                risks = report_data.get("key_risks", [])
                if risks:
                    for r in risks:
                        risks_html += f"<li>{hoverify(clean_text(r))}</li>"
                else:
                    risks_html += f"<li>{hoverify('*(Không có dữ liệu)*')}</li>"
                risks_html += '</ul></div>'
                st.markdown(risks_html, unsafe_allow_html=True)

            # --- 4. DỮ LIỆU TÀI CHÍNH ---
            st.markdown(f'<div class="section-header">🏦 {hoverify("4. Dữ Liệu Tài Chính & Dự Phóng")}</div>', unsafe_allow_html=True)
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
    st.markdown(f"""
    <div class="welcome-box" style="margin-top: 50px; text-align: center; padding: 50px 20px !important;">
        <p style="color: #FFFFFF; font-size: 26px; max-width: 1100px; margin: 0 auto 30px auto; line-height: 1.8; font-weight: 700;">
            {hoverify('Dựa trên các nguồn dữ liệu:')}
        </p>
        <ul style="color: #FFFFFF; font-size: 36px; max-width: 1100px; margin: 0 auto 40px auto; padding-left: 0; line-height: 2.2; list-style-type: none; text-align: left; display: inline-block; font-weight: 800;">
            <li>• {hoverify('Giá và dữ liệu giao dịch chứng khoán')}</li>
            <li>• {hoverify('Báo cáo tài chính và các chỉ số tài chính doanh nghiệp niêm yết')}</li>
            <li>• {hoverify('Thông tin, tin tức doanh nghiệp cập nhật')}</li>
            <li>• {hoverify('Báo cáo phân tích của các công ty chứng khoán')}</li>
        </ul>
        <p style="color: #FFFFFF; font-size: 24px; max-width: 1100px; margin: 0 auto 20px auto; line-height: 1.8; font-weight: 500;">
            {hoverify('Báo cáo phân tích tự động trích xuất ra PDF theo nhu cầu người dùng')}
        </p>
        <p style="color: #FFFFFF; font-size: 28px; max-width: 1100px; margin: 0 auto; line-height: 1.8; font-weight: 800;">
            {hoverify('Chính xác về mặt dữ liệu, kết quả phân tích đánh giá thích hợp và sáng tạo')}
        </p>
    </div>
    """, unsafe_allow_html=True)
