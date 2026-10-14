import html
import re
import traceback
from urllib.parse import urlparse

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from core.report_pdf import (
    apply_scoring_to_report,
    generate_pdf,
    load_stock_report_data,
    refresh_stock_report_quote,
)
from core.scoring import get_recommendation, call_ai_for_tabs
# Cấu hình trang rộng tối đa
st.set_page_config(page_title="Hệ Thống Phân Tích Cổ Phiếu", layout="wide", initial_sidebar_state="collapsed")
REPORT_DATA_REVISION = "cash-flow-news-refresh-v7-report-display"


@st.cache_resource
def _start_realtime_pipeline():
    from stock_bot.data_pipeline.main import start_realtime_pipeline
    return start_realtime_pipeline()


@st.fragment(run_every="120s")
def _refresh_report_quote(ticker):
    report_data = st.session_state.get("report_data")
    if not isinstance(report_data, dict) or report_data.get("ticker") != ticker:
        return None
    try:
        refreshed = refresh_stock_report_quote(report_data)
    except Exception as exc:
        st.error(f"Không cập nhật được giá realtime cho {ticker}: {exc}")
        return None
    
    changed = any(
        refreshed.get(key) != report_data.get(key)
        for key in ("current_price", "recommendation", "target_price", "upside")
    )
    st.session_state["report_data"] = refreshed
    
    if changed:
        st.rerun(scope="app")
        
    return refreshed  # THÊM DÒNG NÀY ĐỂ TRẢ VỀ DICT DATA MỚI NHẤT

# --- CSS CHUẨN HÓA GIAO DIỆN, FONT TO, TABS ĐẸP MẮT ---
st.markdown("""
<style>
    /* Tổng quan nền */
    [data-testid="stAppViewContainer"], .stApp {
        background-color: var(--background-color);
        background-image: linear-gradient(135deg, rgba(138, 43, 226, 0.1) 0%, rgba(75, 0, 130, 0.18) 100%) !important;
    }
    [data-testid="stHeader"] { background-color: transparent; }

    /* Khung hiển thị & Hiệu ứng pop-up */
    [data-testid="stMetric"], .summary-box, .welcome-box, .image-box, [data-testid="stPlotlyChart"], [data-testid="stExpander"], [data-testid="stDataFrame"] {
        background-color: var(--secondary-background-color) !important;
        background-image: linear-gradient(135deg, rgba(255, 255, 255, 0.04) 0%, rgba(0, 0, 0, 0.08) 100%) !important;
        backdrop-filter: blur(12px) !important;
        border-radius: 14px !important;
        padding: 22px !important;
        box-shadow: 0 10px 25px rgba(0,0,0,0.2) !important;
        border: 1px solid rgba(138, 43, 226, 0.35) !important;
        margin-bottom: 20px;
        transition: all 0.3s ease;
    }
    [data-testid="stMetric"]:hover, .summary-box:hover, .welcome-box:hover, [data-testid="stPlotlyChart"]:hover {
        transform: translateY(-4px);
        border-color: rgba(138, 43, 226, 0.7) !important;
        box-shadow: 0 15px 35px rgba(138, 43, 226, 0.3) !important;
    }

    /* Tiêu đề & Chữ to rõ, dễ nhìn */
    .main-title {
        color: #f6c85f !important;
        font-size: 42px;
        font-weight: 800;
        text-align: center;
        margin-bottom: 10px;
        text-shadow: 0 2px 4px rgba(0,0,0,0.3);
    }
    .sub-title {
        color: var(--text-color);
        font-size: 18px;
        text-align: center;
        margin-bottom: 30px;
        opacity: 0.9;
        font-weight: 600;
    }
    .section-header {
        color: #f6c85f !important;
        font-size: 26px;
        font-weight: 700;
        border-bottom: 2px solid rgba(138, 43, 226, 0.5);
        padding-bottom: 10px;
        margin-top: 35px;
        margin-bottom: 25px;
    }
    
    /* Tăng cỡ chữ nội dung, bảng, chú thích */
    p, li, span, div, [data-testid="stCaptionContainer"], [data-testid="stText"], .stMarkdown {
        font-size: 17px !important;
        line-height: 1.7 !important;
    }
    [data-testid="stMetricValue"] { font-size: 30px !important; font-weight: 800 !important; color: #ffffff !important; }
    [data-testid="stMetricLabel"] p, [data-testid="stMetricLabel"] span { color: #f6c85f !important; font-size: 17px !important; font-weight: 700 !important; }

    /* Cải tiến Thanh Tabs (To, rõ, cân đối) */
    div[role="tablist"] {
        background-color: var(--secondary-background-color) !important;
        border-radius: 14px !important;
        padding: 8px !important;
        border: 1px solid rgba(138, 43, 226, 0.4) !important;
        margin-bottom: 30px;
        display: flex;
        gap: 8px;
    }
    button[role="tab"] {
        flex: 1;
        color: var(--text-color) !important;
        opacity: 0.8;
        font-weight: 700 !important;
        font-size: 17px !important;
        border-radius: 10px !important;
        padding: 14px 18px !important;
        border: none !important;
        background: transparent !important;
        transition: all 0.3s ease !important;
    }
    button[role="tab"]:hover {
        opacity: 1 !important;
        background-color: rgba(138, 43, 226, 0.25) !important;
    }
    button[role="tab"][aria-selected="true"] {
        opacity: 1 !important;
        color: #f6c85f !important;
        background: linear-gradient(135deg, rgba(138, 43, 226, 0.5) 0%, rgba(75, 0, 130, 0.8) 100%) !important;
        border: 1px solid rgba(138, 43, 226, 0.9) !important;
        box-shadow: 0 4px 15px rgba(138, 43, 226, 0.4);
    }
    div[data-baseweb="tab-highlight"] { display: none !important; }

    /* Nút bấm */
    [data-testid="stFormSubmitButton"] button, [data-testid="stDownloadButton"] button {
        background: linear-gradient(135deg, #805ad5 0%, #6b46c1 100%);
        color: white !important;
        border-radius: 10px;
        font-weight: 700;
        font-size: 16px;
        padding: 12px 20px;
        border: none;
        box-shadow: 0 4px 15px rgba(107, 70, 193, 0.4);
    }
    [data-testid="stFormSubmitButton"] button:hover, [data-testid="stDownloadButton"] button:hover {
        background: linear-gradient(135deg, #9f7aea 0%, #805ad5 100%);
        transform: translateY(-2px);
    }
    
    .custom-table { width: 100%; border-collapse: collapse; color: var(--text-color); font-size: 16px; }
    .custom-table th { border-bottom: 2px solid rgba(138, 43, 226, 0.5); padding: 12px; text-align: right; color: #f6c85f; font-weight: 700; }
    .custom-table th:first-child { text-align: left; }
    .custom-table td { border-bottom: 1px solid rgba(128, 128, 128, 0.2); padding: 12px; text-align: right; }
    .custom-table td:first-child { text-align: left; font-weight: 600; }
    .footer-text { font-size: 15px; color: var(--text-color); opacity: 0.7; margin-top: 60px; border-top: 1px solid rgba(138, 43, 226, 0.3); padding-top: 25px; text-align: center; }
</style>
""", unsafe_allow_html=True)

def hoverify(text):
    return html.escape(str(text)).replace("\n", "<br>")


def clean_text(text):
    if not isinstance(text, str):
        return text
    return re.sub(r"<br\s*/?>", "\n\n", text, flags=re.IGNORECASE).replace(
        "workbook ", ""
    ).replace("Workbook ", "")


st.markdown(f'<div class="main-title">{hoverify("HỆ THỐNG PHÂN TÍCH CƠ HỘI ĐẦU TƯ CỔ PHIẾU")}</div>', unsafe_allow_html=True)
st.markdown(f'<div class="sub-title">{hoverify("Nền tảng định lượng, định giá chuyên sâu & hỗ trợ quyết định đầu tư thông minh")}</div>', unsafe_allow_html=True)

# Form nhập liệu tối ưu
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
    with st.spinner(f"Hệ thống đang xử lý dữ liệu và chạy mô hình cho {ticker}..."):
        try:
            # 1. Kích hoạt pipeline cào realtime ngầm
            _start_realtime_pipeline()

            # 2. Lấy dữ liệu cơ sở từ DB / Cache
            current_report = st.session_state.get("report_data")
            if (
                analyze_btn
                or not isinstance(current_report, dict)
                or current_report.get("_report_data_revision") != REPORT_DATA_REVISION
            ):
                report_data = load_stock_report_data(ticker)
                report_data["_report_data_revision"] = REPORT_DATA_REVISION
                st.session_state.pop(f"analysis_pdf_{ticker}", None)
                st.session_state["report_data"] = report_data
            
            # 3. CHẠY REFRESH QUOTE VÀ CẬP NHẬT TRỰC TIẾP LẠI VÀO BIẾN REPORT_DATA
            refreshed_data = _refresh_report_quote(ticker)
            if refreshed_data and isinstance(refreshed_data, dict):
                report_data = refreshed_data
            else:
                report_data = st.session_state.get("report_data", {})

            company_name = report_data.get("company_name", ticker)
            st.markdown(f'<div class="main-title" style="font-size: 30px; margin-top: 15px;">{hoverify("BÁO CÁO PHÂN TÍCH:")} {hoverify(company_name.upper())}</div>', unsafe_allow_html=True)
            
            sub_info = (
                f"<b>{hoverify('Mã CK:')}</b> {hoverify(ticker)} &nbsp;|&nbsp; "
                f"<b>{hoverify('Sàn:')}</b> {hoverify(report_data.get('exchange', 'N/A'))} &nbsp;|&nbsp; "
                f"<b>{hoverify('Ngành:')}</b> {hoverify(clean_text(report_data.get('industry', 'N/A')))} &nbsp;|&nbsp; "
                f"<b>{hoverify('Ngày Dữ Liệu:')}</b> {hoverify(report_data.get('data_as_of', 'N/A'))}"
            )
            st.markdown(f'<div class="sub-title">{sub_info}</div>', unsafe_allow_html=True)
            if report_data.get("price_history_error"):
                st.warning(report_data["price_history_error"])
            if report_data.get("company_metadata_error"):
                st.warning(report_data["company_metadata_error"])

            analysis_tech = report_data.get("tech") or {}
            analysis_fund = report_data.get("fund") or {}
            analysis_news = report_data.get("news") or {}
            recommendation_result = get_recommendation(
                analysis_tech, analysis_fund, use_ai=False
            )
            technical_result = get_recommendation(
                analysis_tech, analysis_fund, analysis_news, use_ai=False
            )
            fundamental_result = get_recommendation(
                analysis_tech, analysis_fund, analysis_news, use_ai=False
            )
            valuation_result = get_recommendation(
                analysis_tech, analysis_fund, analysis_news, use_ai=False
            )
            with st.spinner("Đang tạo nhận xét AI cho các tab trong một lượt..."):
                ai_commentaries = call_ai_for_tabs({
                    "general": {
                        "tech": analysis_tech,
                        "fund": analysis_fund,
                        "result": recommendation_result,
                    },
                    "technical": {
                        "tech": analysis_tech,
                        "fund": analysis_fund,
                        "result": technical_result,
                    },
                    "fundamental": {
                        "tech": analysis_tech,
                        "fund": analysis_fund,
                        "result": fundamental_result,
                    },
                    "valuation": {
                        "tech": analysis_tech,
                        "fund": analysis_fund,
                        "result": valuation_result,
                    },
                })
            
            # --- TẢI XUỐNG BÁO CÁO PDF & BCTN ---
            pdf_path = f"outputs/{ticker}_report.pdf"

            col_dl1, col_dl2 = st.columns(2)
            with col_dl1:
                def create_analysis_pdf():
                    pdf_data = report_data.copy()
                    pdf_data.pop("price_history", None)
                    pdf_data.pop("_scoring_data", None)
                    pdf_data = apply_scoring_to_report(
                        pdf_data,
                        pdf_data,
                        use_ai=False,
                    )
                    pdf_ai_summary = ai_commentaries["general"]
                    if not pdf_ai_summary.startswith("[⚠️"):
                        pdf_data["ai_summary"] = pdf_ai_summary
                        pdf_data["ai_generated"] = True
                    generate_pdf(pdf_data, output_path=pdf_path)
                    with open(pdf_path, "rb") as pdf_file:
                        return pdf_file.read()

                st.download_button(
                    "📥 TẠO VÀ TẢI BÁO CÁO PHÂN TÍCH (PDF)",
                    data=create_analysis_pdf,
                    file_name=f"Bao_Cao_Phan_Tich_{ticker}.pdf",
                    mime="application/pdf",
                    key=f"download_analysis_pdf_{ticker}",
                    type="primary",
                    width="stretch",
                )
            
            bctn_year = report_data.get("financial_years", [])[-1] if report_data.get("financial_years") else 2023
            import core.annual_report as ar
            bctn_path = ar.OUTPUT_DIR / f"{ticker}_{bctn_year}.pdf"
            
            with col_dl2:
                if bctn_path.exists():
                    with open(bctn_path, "rb") as f:
                        bctn_bytes = f.read()
                    st.download_button(label=f"📥 TẢI BÁO CÁO THƯỜNG NIÊN ({bctn_year})", data=bctn_bytes, file_name=f"BCTN_{ticker}_{bctn_year}.pdf", mime="application/pdf", width="stretch")
                elif st.button(
                    f"🔎 TÌM BÁO CÁO THƯỜNG NIÊN ({bctn_year})",
                    key=f"find_annual_report_{ticker}_{bctn_year}",
                    width="stretch",
                ):
                    with st.spinner(f"Đang tìm báo cáo thường niên của {ticker}..."):
                        try:
                            found_report = ar.process_report(ticker, bctn_year)
                        except (OSError, RuntimeError, ValueError) as exc:
                            st.error(f"Không tìm được báo cáo thường niên: {exc}")
                        else:
                            if found_report:
                                st.success("Đã tải báo cáo thường niên.")
                                st.rerun()
                            else:
                                st.warning(
                                    f"Không tìm thấy báo cáo thường niên "
                                    f"{ticker} năm {bctn_year}."
                                )

            st.markdown("<hr style='border: 1px solid rgba(138, 43, 226, 0.3); margin: 20px 0;'>", unsafe_allow_html=True)

            # ==========================================
            # HỆ THỐNG 5 TABS CHUYÊN SÂU
            # ==========================================
            tab1, tab2, tab3, tab4, tab5 = st.tabs([
                "🎯 1. Tổng Quan & Khuyến Nghị", 
                "📈 2. Phân Tích Kỹ Thuật", 
                "🏦 3. Phân Tích Cơ Bản", 
                "⚖️ 4. Định Giá & Kịch Bản",
                "📰 5. Tin Tức & Sự Kiện"
            ])

# --- TAB 1: TỔNG QUAN & KHUYẾN NGHỊ ---
            with tab1:
                st.markdown(f'<div class="section-header">🎯 {hoverify("Khuyến Nghị Đầu Tư & Luận Điểm Tổng Quan")}</div>', unsafe_allow_html=True)
                
                # 1. 4 Ô CHỈ SỐ KHUYẾN NGHỊ & ĐỊNH GIÁ MỤC TIÊU
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Giá Hiện Tại", report_data.get("current_price", "N/A"))
                c2.metric("Khuyến Nghị", recommendation_result["recommendation"])
                target_price_range = recommendation_result.get("target_price_range")
                target_price_display = (
                    (
                        f"{target_price_range[0] * 1000:,.0f} – "
                        f"{target_price_range[1] * 1000:,.0f} đ"
                    ).replace(",", ".")
                    if target_price_range
                    else "N/A"
                )
                upside_range = recommendation_result.get("upside_range")
                upside_display = (
                    f"{upside_range[0]:+.1%} đến {upside_range[1]:+.1%}"
                    if upside_range
                    else "N/A"
                )
                c3.metric("Dải giá tham khảo P/E", target_price_display)
                c4.metric("Upside theo dải P/E", upside_display)

                # 2. TỔNG ĐIỂM CHỈ GỒM HAI TRỤ CỘT CƠ BẢN VÀ KỸ THUẬT
                scores_dict = recommendation_result["scores"]
                
                # Điểm thành phần các trụ cột
                total_score = recommendation_result["total_score"]
                total_score_display = (
                    f"{total_score}/100" if total_score is not None else "Chưa đủ dữ liệu"
                )
                fundamental_score_display = (
                    f"{scores_dict['fundamental']}/100"
                    if scores_dict.get("fundamental") is not None
                    else "Chưa đủ dữ liệu"
                )
                technical_score_display = (
                    f"{scores_dict['technical']}/100"
                    if scores_dict.get("technical") is not None
                    else "Chưa đủ dữ liệu"
                )

                st.markdown(f"""
                <div style="margin: 12px 0 18px 0; padding: 12px 16px; background: rgba(255,255,255,0.03); border-radius: 8px; border: 1px solid rgba(255,255,255,0.1);">
                    <div style="font-size: 16px;">
                        <b>📊 Tổng Điểm Định Lượng Tổng Hợp:</b> <span style="font-size: 20px; font-weight: 800; color: #f6c85f;">{total_score_display}</span>
                        <span style="font-size: 13px; opacity: 0.85; margin-left: 10px;">(MUA từ 65/100 · BÁN dưới 45/100)</span>
                    </div>
                    <div style="font-size: 13px; color: #a0aec0; margin-top: 6px;">
                        • <b>Điểm cơ bản:</b> {fundamental_score_display} (50%)<br>
                        • <b>Điểm kỹ thuật:</b> {technical_score_display} (50%)<br>
                        • Tin tức chỉ hiển thị để tham khảo, không ảnh hưởng điểm tổng hoặc khuyến nghị.
                    </div>
                </div>
                """, unsafe_allow_html=True)

                st.markdown(
                    "**Tóm tắt theo quy tắc định lượng:** "
                    + clean_text(recommendation_result["ai_summary"])
                )
                with st.container(border=True):
                    st.markdown("**Nhận định AI tổng quan**")
                    ai_comment = ai_commentaries["general"]
                    if ai_comment.startswith("[⚠️"):
                        st.warning(ai_comment)
                    else:
                        st.markdown(clean_text(ai_comment))

                # 4. HAI CỘT LUẬN ĐIỂM TÍCH CỰC & RỦI RO (ĐÃ FIX CHUẨN HTML THỤT LỀ)
                col_p, col_r = st.columns(2)
                
                with col_p:
                    p_html = f'<div class="summary-box" style="border-left: 4px solid #089981 !important;"><b>✅ Luận Điểm Tích Cực:</b><ul style="margin-top: 8px; padding-left: 20px;">'
                    investment_points = recommendation_result.get("reasons") or []
                    if investment_points:
                        for p in investment_points:
                            p_html += f"<li style='margin-bottom: 6px;'>{hoverify(clean_text(p))}</li>"
                    else:
                        p_html += "<li>Chưa có luận điểm tích cực đủ dữ liệu xác nhận từ hai trụ cột chấm điểm.</li>"
                    p_html += '</ul></div>'
                    st.markdown(p_html, unsafe_allow_html=True)
                    
                with col_r:
                    r_html = f'<div class="summary-box" style="border-left: 4px solid #F23645 !important;"><b>⚠️ Rủi Ro & Thách Thức:</b><ul style="margin-top: 8px; padding-left: 20px;">'
                    key_risks = recommendation_result.get("risks") or []
                    if key_risks:
                        for r in key_risks:
                            r_html += f"<li style='margin-bottom: 6px;'>{hoverify(clean_text(r))}</li>"
                    else:
                        r_html += "<li>Chưa có cảnh báo rủi ro cụ thể được các chỉ báo hiện có xác nhận. Điều này không đồng nghĩa cổ phiếu không có rủi ro.</li>"
                    r_html += '</ul></div>'
                    st.markdown(r_html, unsafe_allow_html=True)
# ==========================================
# ==========================================
            # TAB 2: PHÂN TÍCH KỸ THUẬT (CHỈ GIỮ 1 KHỐI NÀY)
            # ==========================================
            with tab2:
                st.markdown(f'<div class="section-header">📈 {hoverify("Phân Tích Kỹ Thuật & Hệ Thống Chấm Điểm Động Lượng")}</div>', unsafe_allow_html=True)
                technical_factors = technical_result["score_breakdown"][
                    "technical_factors"
                ]
                technical_groups = {}
                for factor in technical_factors:
                    category = factor.get("category", "Khác")
                    technical_groups.setdefault(category, []).append(factor)
                technical_score = technical_result["scores"]["technical"]
                technical_coverage = technical_result["score_breakdown"][
                    "technical_coverage"
                ]

                # 2. Tóm tắt điểm số và giải thích ngưỡng đánh giá
                status_text = (
                    "TÍCH CỰC" if technical_score is not None and technical_score >= 65
                    else "TRUNG LẬP" if technical_score is not None and technical_score >= 45
                    else "THẬN TRỌNG" if technical_score is not None
                    else "CHƯA ĐỦ DỮ LIỆU"
                )
                with st.container(border=True):
                    st.markdown("#### 📊 Tóm tắt tín hiệu kỹ thuật")
                    score_col, status_col, coverage_col = st.columns(3)
                    score_col.metric(
                        "Điểm kỹ thuật",
                        f"{technical_score:.0f}/100" if technical_score is not None else "—",
                    )
                    status_col.metric("Đánh giá", status_text)
                    coverage_col.metric("Độ phủ dữ liệu", f"{technical_coverage}%")
                    st.caption(
                        "Cách đọc: từ 65 là tích cực; 45–64 là trung lập; dưới 45 là thận trọng. "
                        "Trọng số nhóm: xu hướng 45%, động lượng 25%, giá/khối lượng 20%, "
                        "vùng tham chiếu 10%. Chỉ tính điểm tổng khi độ phủ đạt ít nhất 60%. "
                        "Điểm là kết quả quy tắc trên dữ liệu lịch sử, không phải xác suất dự báo "
                        "hay khuyến nghị đầu tư."
                    )
                if technical_score is None:
                    st.warning(
                        "Chưa đủ dữ liệu chỉ báo để tính điểm tổng; hãy xem độ phủ và các chỉ báo còn thiếu bên dưới."
                    )

                # 3. Các ô Metric chỉ số gốc
                c_tk1, c_tk2, c_tk3, c_tk4 = st.columns(4)
                c_tk1.metric("Dải Giá 52 Tuần", report_data.get("price_52w_range", "N/A"))
                c_tk2.metric("KLGD 20 Phiên", report_data.get("avg_volume_20d", "N/A"))
                volume_ratio = report_data["tech"].get("volume_ratio")
                rsi_value = report_data["tech"].get("RSI")
                c_tk3.metric(
                    "KLGD / TB20 phiên",
                    f"{volume_ratio:.2f}x" if volume_ratio is not None else "N/A",
                )
                c_tk4.metric(
                    "RSI (14)",
                    f"{rsi_value:.2f}" if rsi_value is not None else "N/A",
                )

                # 4. Biểu đồ Candlestick & RSI
                try:
                    df_stock = report_data["price_history"].copy()
                    if not df_stock.empty:
                        df_stock['date'] = pd.to_datetime(df_stock['date'])
                        df_stock = df_stock.sort_values('date')
                        df_stock['date_str'] = df_stock['date'].dt.strftime('%d-%m-%Y')
                        df_stock['close'] = pd.to_numeric(df_stock['close'], errors='coerce')
                        df_stock['volume'] = pd.to_numeric(df_stock['volume'], errors='coerce')
                        volume_change = df_stock['close'].diff()
                        volume_colors = [
                            "#089981" if change > 0
                            else "#F23645" if change < 0
                            else "#78909c"
                            for change in volume_change
                        ]
                        df_stock['volume_millions'] = df_stock['volume'] / 1_000_000
                        
                        fig = make_subplots(
                            rows=3, cols=1,
                            shared_xaxes=True,
                            vertical_spacing=0.06,
                            row_heights=[0.58, 0.18, 0.24],
                            subplot_titles=(
                                "Giá và MA20/MA50/MA200",
                                "Khối lượng (triệu cổ phiếu)",
                                "Động lượng RSI (14)",
                            )
                        )
                        
                        fig.add_trace(go.Candlestick(
                            x=df_stock['date_str'], open=df_stock['open'], high=df_stock['high'], low=df_stock['low'], close=df_stock['close'],
                            increasing_line_color='#089981', decreasing_line_color='#F23645', name='Giá'
                        ), row=1, col=1)
                        
                        fig.add_trace(go.Scatter(
                            x=df_stock['date_str'], y=df_stock['MA20'], 
                            line=dict(color='#f59e0b', width=1.5), name='MA20'
                        ), row=1, col=1)
                        
                        fig.add_trace(go.Scatter(
                            x=df_stock['date_str'], y=df_stock['MA50'], 
                            line=dict(color='#3b82f6', width=1.5), name='MA50'
                        ), row=1, col=1)

                        fig.add_trace(go.Scatter(
                            x=df_stock['date_str'], y=df_stock['MA200'],
                            line=dict(color='#ab47bc', width=1.5), name='MA200'
                        ), row=1, col=1)

                        fig.add_trace(go.Bar(
                            x=df_stock['date_str'], y=df_stock['volume_millions'],
                            marker_color=volume_colors,
                            name='Khối lượng',
                            hovertemplate=(
                                "%{x}<br>Khối lượng: %{y:,.2f} triệu CP"
                                "<extra></extra>"
                            ),
                        ), row=2, col=1)
                        
                        fig.add_trace(go.Scatter(
                            x=df_stock['date_str'], y=df_stock['RSI14'],
                            line=dict(color='#e02424', width=2), name='RSI 14'
                        ), row=3, col=1)
                        
                        fig.add_hline(y=70, line_dash="dash", line_color="rgba(242, 54, 69, 0.6)", row=3, col=1)
                        fig.add_hline(y=30, line_dash="dash", line_color="rgba(8, 153, 129, 0.6)", row=3, col=1)
                        
                        fig.update_layout(
                            height=760, 
                            margin=dict(l=20, r=20, t=30, b=30), 
                            paper_bgcolor="rgba(0,0,0,0)", 
                            plot_bgcolor="rgba(0,0,0,0)", 
                            showlegend=True, 
                            legend=dict(orientation="h", y=1.05, x=0),
                            xaxis_rangeslider_visible=False
                        )
                        
                        fig.update_yaxes(title_text="Nghìn đồng", row=1, col=1)
                        fig.update_yaxes(title_text="Triệu CP", row=2, col=1)
                        fig.update_yaxes(range=[0, 100], row=3, col=1)
                        st.caption(
                            "Cột xanh: giá đóng cửa tăng so với phiên trước; "
                            "cột đỏ: giảm; xám: không đổi hoặc chưa có phiên so sánh."
                        )
                        st.plotly_chart(
                            fig,
                            width="stretch",
                            config={"displayModeBar": False},
                            key="tech_chart_single_final",
                            alt="Biểu đồ giá, đường trung bình động, khối lượng xanh đỏ và RSI.",
                        )
                except Exception as e:
                    st.error(f"Lỗi hiển thị biểu đồ kỹ thuật: {e}")

                st.markdown("##### 🤖 Nhận định kỹ thuật")
                with st.container(border=True):
                    ai_comment = ai_commentaries["technical"]
                    if ai_comment.startswith("[⚠️"):
                        st.warning(ai_comment)
                    else:
                        st.markdown(clean_text(ai_comment))

                # 6. Bảng theo nhóm, để tránh phải cuộn ngang qua các quy tắc dài
                st.markdown("##### 📊 Chi tiết điểm từng chỉ báo")
                st.caption(
                    "Điểm chỉ báo nằm trong khoảng 0–100; trọng số cho biết mức ảnh hưởng "
                    "của chỉ báo tới điểm tổng. Dấu — nghĩa là chưa có dữ liệu."
                )
                for category, factors in technical_groups.items():
                    group_weight = sum(factor["weight"] for factor in factors)
                    st.markdown(f"**{category} · {group_weight}% trọng số**")
                    group_table = pd.DataFrame([
                        {
                            "Chỉ báo": f"{factor['label']} ({factor['weight']}%)",
                            "Dữ liệu quan sát": factor["evidence"],
                            "Điểm / 100": (
                                factor["score"]
                                if factor.get("score") is not None
                                else None
                            ),
                            "Nhận xét": factor.get("text") or "Không có cảnh báo nổi bật",
                        }
                        for factor in factors
                    ])
                    st.dataframe(
                        group_table,
                        hide_index=True,
                        width="stretch",
                        column_config={
                            "Chỉ báo": st.column_config.TextColumn(width="medium"),
                            "Dữ liệu quan sát": st.column_config.TextColumn(width="medium"),
                            "Điểm / 100": st.column_config.NumberColumn(
                                format="%.0f",
                                width="small",
                            ),
                            "Nhận xét": st.column_config.TextColumn(width="large"),
                        },
                        alt=f"Điểm các chỉ báo thuộc nhóm {category}.",
                    )

                with st.expander("Xem quy tắc chấm điểm chi tiết"):
                    for category, factors in technical_groups.items():
                        st.markdown(f"**{category}**")
                        for factor in factors:
                            st.markdown(
                                f"- **{factor['label']} ({factor['weight']}%):** "
                                f"{factor['rule']}"
                            )
            # =========================================================================
# --- TAB 3: PHÂN TÍCH CƠ BẢN (GỘP ĐƯỜNG YOY & PHÓNG TO FONT CHỮ SỐ LIỆU BIỂU ĐỒ) ---
            with tab3:
                st.markdown(f'<div class="section-header">🏦 {hoverify("Phân Tích Cơ Bản & Báo Cáo Tài Chính Chi Tiết")}</div>', unsafe_allow_html=True)
                import plotly.graph_objects as go
                
                # CSS TÙY CHỈNH NÚT BẤM CHUYỂN BIỂU ĐỒ KIỂU POPUP MOBILE
                st.markdown("""
                <style>
                div[data-testid="stHorizontalBlock"] div.stRadio > div {
                    flex-direction: row;
                    background-color: #1e1b2e;
                    padding: 4px;
                    border-radius: 8px;
                    border: 1px solid #362f4b;
                }
                div[data-testid="stHorizontalBlock"] div.stRadio label {
                    background-color: transparent;
                    color: #a39cb6;
                    padding: 6px 16px;
                    border-radius: 6px;
                    font-weight: 600;
                    margin-right: 4px;
                    transition: all 0.2s ease;
                }
                div[data-testid="stHorizontalBlock"] div.stRadio label:hover {
                    color: #ffffff;
                }
                </style>
                """, unsafe_allow_html=True)

                # Hàm ép kiểu số an toàn
                def clean_num(val):
                    if isinstance(val, (int, float)):
                        return float(val)
                    if not val or str(val).lower() == 'nan' or str(val) == '-':
                        return 0.0
                    try:
                        clean_str = str(val).replace('%', '').replace('đ', '').strip()
                        if ',' in clean_str and '.' in clean_str:
                            if clean_str.rfind(',') > clean_str.rfind('.'):
                                clean_str = clean_str.replace('.', '').replace(',', '.')
                            else:
                                clean_str = clean_str.replace(',', '')
                        elif ',' in clean_str:
                            decimal_places = len(clean_str.rsplit(',', 1)[-1])
                            clean_str = clean_str.replace(
                                ',', '.' if decimal_places <= 2 else ''
                            )
                        elif '.' in clean_str and len(clean_str.rsplit('.', 1)[-1]) == 3:
                            clean_str = clean_str.replace('.', '')
                        return float(clean_str)
                    except Exception:
                        return 0.0
                
                # 1. Metric tổng quan
                c_cb1, c_cb2, c_cb3, c_cb4 = st.columns(4)
                c_cb1.metric("Vốn Hóa Thị Trường", report_data.get("market_cap", "N/A"))
                financial_ratios = report_data.get("financial_ratios")
                if not isinstance(financial_ratios, dict):
                    financial_ratios = {}
                fundamental_metrics = report_data.get("fundamental_metrics")
                if not isinstance(fundamental_metrics, dict):
                    fundamental_metrics = {}
                beta_value = financial_ratios.get("beta")
                if pd.isna(pd.to_numeric(beta_value, errors="coerce")):
                    beta_value = report_data.get("beta")
                if pd.isna(pd.to_numeric(beta_value, errors="coerce")):
                    beta_value = fundamental_metrics.get("beta", "N/A")
                if not pd.isna(pd.to_numeric(beta_value, errors="coerce")):
                    beta_value = f"{float(beta_value):.2f}"
                c_cb2.metric("Hệ Số Beta", beta_value)
                
                foreign_ownership = report_data.get("foreign_ownership", report_data.get("foreign_ratio", "N/A"))
                shares_out = report_data.get("shares_outstanding", "N/A")
                
                c_cb3.metric("Sở Hữu Nước Ngoài", f"{foreign_ownership}%" if isinstance(foreign_ownership, (int, float)) else str(foreign_ownership))
                c_cb4.metric("CP Lưu Hành", str(shares_out))

                st.markdown("<br>", unsafe_allow_html=True)

                # --- 2. ĐỊNH NGHĨA DANH SÁCH NĂM TĂNG DẦN CHUẨN (2022 -> 2025) ---
                raw_years = report_data.get("financial_years", [2022, 2023, 2024, 2025])
                years = sorted([int(y) for y in raw_years if str(y).isdigit()])
                str_years = [str(y) for y in years]

                # --- 3. BẢNG CHỈ SỐ TÀI CHÍNH GỐC ---
                fin_ratios = report_data.get("financial_ratios_table", [])
                if not isinstance(fin_ratios, list) or not fin_ratios:
                    legacy_ratios = report_data.get("financial_ratios", [])
                    fin_ratios = legacy_ratios if isinstance(legacy_ratios, list) else []
                
                ratio_years = [
                    int(year) for year in report_data.get("financial_years", years)
                    if str(year).isdigit()
                ]
                ratio_section_exists = any(
                    "CHỈ SỐ TÀI CHÍNH" in str(section.get("title", "")).upper()
                    for section in report_data.get("financial_sections", [])
                )

                def render_financial_ratio_chart():
                    st.markdown(
                        "##### 📊 Biểu đồ Chỉ số chính (ROE / ROA / Lợi nhuận ròng)"
                    )
                    ratio_choice = st.radio(
                        "Chọn chỉ tiêu xem biểu đồ (Chỉ số chính)",
                        ["ROE", "ROA", "Lợi nhuận ròng"],
                        key="ratio_chart_radio",
                        horizontal=True,
                        label_visibility="collapsed",
                    )

                    roe_by_year, roa_by_year, profit_by_year = {}, {}, {}
                    for ratio_row in fin_ratios:
                        label = clean_text(ratio_row.get("label", "")).lower()
                        values = ratio_row.get("values", [])
                        source_years = ratio_row.get("years", ratio_years)
                        if len(source_years) != len(values):
                            source_years = ratio_years[-len(values):] if values else []
                        series = {
                            str(year): clean_num(value)
                            for year, value in zip(source_years, values)
                        }
                        if "roe" in label:
                            roe_by_year = series
                        elif "roa" in label:
                            roa_by_year = series

                    financial_chart_data = report_data.get("financial_chart_data", {})
                    chart_net_profit = financial_chart_data.get("net_profit", {})
                    if isinstance(chart_net_profit, dict):
                        profit_by_year = {
                            str(year): clean_num(value)
                            for year, value in chart_net_profit.items()
                        }

                    if not profit_by_year:
                        for section in report_data.get("financial_sections", []):
                            title = str(section.get("title", "")).upper()
                            if "KINH DOANH" not in title and "INCOME" not in title:
                                continue
                            for row in section.get("rows", []):
                                label = clean_text(row.get("label", "")).lower()
                                is_net_profit = any(
                                    key in label
                                    for key in (
                                        "lợi nhuận sau thuế",
                                        "lnst",
                                        "net profit",
                                        "net income",
                                    )
                                )
                                if is_net_profit and "tăng trưởng" not in label and "biên" not in label:
                                    values = row.get("values", [])
                                    section_years = section.get("years", years)
                                    if len(section_years) != len(values):
                                        section_years = years[-len(values):] if values else []
                                    profit_by_year = {
                                        str(year): clean_num(value)
                                        for year, value in zip(section_years, values)
                                    }
                                    break

                    roe_values = [roe_by_year.get(year, 0.0) for year in str_years]
                    roa_values = [roa_by_year.get(year, 0.0) for year in str_years]
                    profit_values = [
                        profit_by_year.get(year, 0.0) for year in str_years
                    ]
                    chart = go.Figure()
                    if ratio_choice == "ROA":
                        values, name, color, suffix = (
                            roa_values, "ROA (%)", "#29b6f6", "%"
                        )
                    elif ratio_choice == "Lợi nhuận ròng":
                        values, name, color, suffix = (
                            profit_values, "Lợi nhuận ròng (Tỷ)", "#00c853", " Tỷ"
                        )
                    else:
                        values, name, color, suffix = (
                            roe_values, "ROE (%)", "#0288d1", "%"
                        )
                    chart.add_trace(go.Bar(
                        x=str_years,
                        y=values,
                        name=name,
                        marker_color=color,
                        text=[
                            f"{value:,.0f}{suffix}" if value else "-"
                            for value in values
                        ],
                        textposition="auto",
                        textfont=dict(
                            size=14, color="#ffffff", family="Arial Black"
                        ),
                    ))
                    chart.update_layout(
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#ffffff", size=13),
                        height=340,
                        margin=dict(l=10, r=10, t=30, b=10),
                        xaxis=dict(
                            type="category",
                            tickfont=dict(size=13, color="#ffffff"),
                        ),
                        yaxis=dict(tickfont=dict(size=12, color="#a39cb6")),
                        legend=dict(
                            orientation="h",
                            yanchor="bottom",
                            y=1.02,
                            xanchor="right",
                            x=1,
                            font=dict(size=13),
                        ),
                    )
                    st.plotly_chart(
                        chart, use_container_width=True, key="financial_ratio_chart"
                    )
                
                if fin_ratios and years:
                    if not ratio_section_exists:
                        st.markdown("### 📊 CHỈ SỐ TÀI CHÍNH & HIỆU QUẢ HOẠT ĐỘNG")
                        r_html = "<div class='summary-box' style='overflow-x: auto;'><table class='custom-table'>"
                        r_html += "<thead><tr><th style='min-width: 250px; text-align: left;'>Chỉ Số Tài Chính</th>"
                        for y in ratio_years:
                            r_html += f"<th style='text-align: right;'>{y}</th>"
                        r_html += "</tr></thead><tbody>"

                        for r_row in fin_ratios:
                            r_html += f"<tr><td style='text-align: left; font-weight: 600;'>{clean_text(r_row.get('label', ''))}</td>"
                            for v in r_row.get("values", []):
                                r_html += f"<td style='text-align: right;'>{v}</td>"
                            r_html += "</tr>"
                        r_html += "</tbody></table></div><br>"
                        st.markdown(r_html, unsafe_allow_html=True)
                        render_financial_ratio_chart()

                # --- 4. HÀM LẤY 3 DÒNG TIỀN THUẦN KHỚP CHÍNH XÁC THEO TỪNG NĂM ---
                def get_clean_cash_flow_rows(tk, year_list):
                    from pathlib import Path
                    import pandas as pd
                    fpath = Path("data/raw") / f"{tk}_cash_flow.csv"
                    clean_cf_rows = []
                    if fpath.exists():
                        try:
                            df = pd.read_csv(fpath)
                            if not df.empty:
                                label_col = next((c for c in ['item', 'name', 'label', 'item_id'] if c in df.columns), None)
                                year_cols = [str(y) for y in year_list if str(y) in df.columns]
                                if label_col and year_cols:
                                    target_items = [
                                        ("Lưu chuyển tiền thuần từ hoạt động kinh doanh", ["kinh doanh"]),
                                        ("Lưu chuyển tiền thuần từ hoạt động đầu tư", ["đầu tư"]),
                                        ("Lưu chuyển tiền thuần từ hoạt động tài chính", ["tài chính"])
                                    ]
                                    for display_name, keywords in target_items:
                                        found_row = None
                                        for _, r in df.iterrows():
                                            lbl = str(r[label_col]).strip().lower()
                                            if "thuần" in lbl and any(k in lbl for k in keywords):
                                                found_row = r
                                                break
                                        if found_row is None:
                                            for _, r in df.iterrows():
                                                lbl = str(r[label_col]).strip().lower()
                                                if "lưu chuyển tiền từ" in lbl and any(k in lbl for k in keywords):
                                                    found_row = r
                                                    break
                                        if found_row is not None:
                                            vals = []
                                            for y in year_cols:
                                                v = found_row.get(y, None)
                                                num_v = clean_num(v)
                                                if abs(num_v) > 1e6:
                                                    num_v = num_v / 1e9
                                                vals.append(num_v)
                                            clean_cf_rows.append({"label": display_name, "values": vals[::-1]})
                        except Exception:
                            pass
                    return clean_cf_rows

                # --- 5. RENDER BẢNG TÀI CHÍNH & BIỂU ĐỒ TRỰC QUAN GỘP YOY ---
                fin_sections = report_data.get("financial_sections", [])

                if fin_sections:
                    for idx_sec, section in enumerate(fin_sections):
                        section_title = clean_text(section.get('title', 'BÁO CÁO TÀI CHÍNH')).upper()
                        st.markdown(f"### 📋 {section_title}")
                        
                        rows = section.get("rows", [])
                        
                        if "TIỀN TỆ" in section_title or "CASH FLOW" in section_title:
                            clean_cf = get_clean_cash_flow_rows(ticker, years)
                            if clean_cf:
                                rows = clean_cf

                        if rows:
                            html_table = "<div class='summary-box' style='overflow-x: auto;'><table class='custom-table'>"
                            html_table += "<thead><tr><th style='min-width: 250px; text-align: left;'>Chỉ Tiêu Tài Chính</th>"
                            for y in years: 
                                html_table += f"<th style='text-align: right;'>{y}</th>"
                            html_table += "</tr></thead><tbody>"
                            
                            for row in rows:
                                label = clean_text(row.get('label', ''))
                                raw_values = row.get("values", [])
                                values = raw_values
                                
                                if not ("TIỀN TỆ" in section_title or "CASH FLOW" in section_title):
                                    sec_years = section.get("years", years)
                                    if len(sec_years) == len(values) and sec_years != years:
                                        val_map = {str(sec_years[idx]): values[idx] for idx in range(len(sec_years))}
                                        values = [val_map.get(str(y), "-") for y in years]

                                is_header = label.isupper() or any(k in label.upper() for k in [
                                    "TỔNG", "LỢI NHUẬN", "TÀI SẢN", "NỢ PHẢI TRẢ", "VỐN CHỦ SỞ HỮU", 
                                    "HOẠT ĐỘNG KINH DOANH", "HOẠT ĐỘNG ĐẦU TƯ", "HOẠT ĐỘNG TÀI CHÍNH", "LƯU CHUYỂN TIỀN THUẦN"
                                ])
                                style_label = "font-weight: 700; color: #f6c85f;" if is_header else ""
                                
                                html_table += f"<tr><td style='text-align: left; {style_label}'>{label}</td>"
                                
                                for i in range(len(years)):
                                    val = values[i] if i < len(years) else "-"
                                    if isinstance(val, (int, float)):
                                        formatted_val = f"{val:,.1f}" if isinstance(val, float) else f"{val:,}"
                                    else:
                                        formatted_val = str(val) if (pd.notna(val) and str(val) != 'nan') else "-"
                                    
                                    html_table += f"<td style='text-align: right; {style_label}'>{formatted_val}</td>"
                                html_table += "</tr>"
                            
                            html_table += "</tbody></table></div><br>"
                            st.markdown(html_table, unsafe_allow_html=True)

                            # --- RENDER BIỂU ĐỒ BÊN DƯỚI BẢNG ---
                            if "CHỈ SỐ TÀI CHÍNH" in section_title:
                                render_financial_ratio_chart()
                            elif "KINH DOANH" in section_title or "INCOME" in section_title:
                                st.markdown("##### 📊 Biểu đồ Báo cáo doanh thu & Lợi nhuận")
                                inc_choice = st.radio(
                                    "Chọn chỉ tiêu xem biểu đồ (KQKD)",
                                    ["Lợi nhuận ST", "Doanh thu thuần"],
                                    key=f"inc_chart_radio_{idx_sec}",
                                    horizontal=True,
                                    label_visibility="collapsed"
                                )

                                rev_vals, net_profit_vals = [0.0]*len(years), [0.0]*len(years)
                                for r in rows:
                                    lbl_l = clean_text(r.get('label', '')).lower()
                                    r_v = r.get('values', [])
                                    if any(k in lbl_l for k in ["doanh thu thuần", "doanh thu hoạt động", "tổng doanh thu"]):
                                        rev_vals = [clean_num(v) for v in r_v]
                                    if any(k in lbl_l for k in ["lợi nhuận sau thuế", "lnst", "net profit", "net income"]):
                                        net_profit_vals = [clean_num(v) for v in r_v]

                                fig_inc = go.Figure()
                                target_vals = net_profit_vals if inc_choice == "Lợi nhuận ST" else rev_vals
                                bar_color = "#00c853" if inc_choice == "Lợi nhuận ST" else "#29b6f6"
                                bar_name = "LNST (Tỷ)" if inc_choice == "Lợi nhuận ST" else "Doanh thu (Tỷ)"

                                growth_yoy = [0.0]
                                for idx in range(1, len(target_vals)):
                                    prev = target_vals[idx-1]
                                    curr = target_vals[idx]
                                    g = ((curr - prev) / abs(prev) * 100) if prev != 0 else 0.0
                                    growth_yoy.append(g)

                                # 1. Cột với Font chữ lớn 14px
                                fig_inc.add_trace(go.Bar(
                                    x=str_years, y=target_vals, name=bar_name,
                                    marker_color=bar_color,
                                    text=[f"{v:,.0f} Tỷ" if v != 0 else "-" for v in target_vals],
                                    textposition="auto",
                                    textfont=dict(size=14, color="#ffffff", family="Arial Black")
                                ))
                                # 2. Đường YOY (%) với Font chữ lớn 14px nổi bật
                                fig_inc.add_trace(go.Scatter(
                                    x=str_years, y=growth_yoy, name="YOY (%)", yaxis="y2",
                                    mode="lines+markers+text", line=dict(color="#ffd600", width=3.5),
                                    marker=dict(size=8, color="#ffd600"),
                                    text=[f"{g:+.1f}%" for g in growth_yoy],
                                    textposition="top center",
                                    textfont=dict(size=14, color="#ffd600", family="Arial Black")
                                ))

                                fig_inc.update_layout(
                                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                                    font=dict(color="#ffffff", size=13), height=350, margin=dict(l=10, r=10, t=30, b=10),
                                    xaxis=dict(type='category', tickfont=dict(size=13, color="#ffffff")),
                                    yaxis=dict(tickfont=dict(size=12, color="#a39cb6")),
                                    yaxis2=dict(overlaying="y", side="right", showgrid=False, title="YOY (%)", tickfont=dict(size=12, color="#ffd600")),
                                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=13))
                                )
                                st.plotly_chart(fig_inc, use_container_width=True)

                            # B. BIỂU ĐỒ BẢNG CÂN ĐỐI KẾ TOÁN (FONT TO)
                            elif "CÂN ĐỐI" in section_title or "BALANCE" in section_title:
                                st.markdown("##### 📊 Biểu đồ Bảng cân đối kế toán")
                                bs_choice = st.radio(
                                    "Chọn chỉ tiêu xem biểu đồ (CĐKT)",
                                    ["Cơ cấu Nợ/Tài sản", "Tổng tài sản", "Nợ phải trả"],
                                    key=f"bs_chart_radio_{idx_sec}",
                                    horizontal=True,
                                    label_visibility="collapsed"
                                )

                                assets_vals, liab_vals = [0.0]*len(years), [0.0]*len(years)
                                for r in rows:
                                    lbl_l = clean_text(r.get('label', '')).lower()
                                    r_v = r.get('values', [])
                                    if "tài sản" in lbl_l and any(k in lbl_l for k in ["tổng", "cộng"]):
                                        assets_vals = [clean_num(v) for v in r_v]
                                    elif "nợ phải trả" in lbl_l or "tổng nợ" in lbl_l:
                                        liab_vals = [clean_num(v) for v in r_v]

                                fig_bs = go.Figure()
                                if bs_choice == "Tổng tài sản":
                                    fig_bs.add_trace(go.Bar(
                                        x=str_years, y=assets_vals, name="Tổng tài sản",
                                        marker_color="#0288d1", text=[f"{v:,.0f} Tỷ" if v != 0 else "-" for v in assets_vals],
                                        textposition="auto", textfont=dict(size=14, color="#ffffff", family="Arial Black")
                                    ))
                                elif bs_choice == "Nợ phải trả":
                                    fig_bs.add_trace(go.Bar(
                                        x=str_years, y=liab_vals, name="Nợ phải trả",
                                        marker_color="#ab47bc", text=[f"{v:,.0f} Tỷ" if v != 0 else "-" for v in liab_vals],
                                        textposition="auto", textfont=dict(size=14, color="#ffffff", family="Arial Black")
                                    ))
                                else:
                                    debt_ratio = [(l/a * 100) if a != 0 else 0.0 for l, a in zip(liab_vals, assets_vals)]
                                    fig_bs.add_trace(go.Bar(x=str_years, y=assets_vals, name="Tổng tài sản", marker_color="#0288d1"))
                                    fig_bs.add_trace(go.Bar(x=str_years, y=liab_vals, name="Nợ phải trả", marker_color="#ab47bc"))
                                    fig_bs.add_trace(go.Scatter(
                                        x=str_years, y=debt_ratio, name="Nợ / TTS (%)", yaxis="y2",
                                        mode="lines+markers+text", line=dict(color="#ffd600", width=3.5),
                                        marker=dict(size=8, color="#ffd600"),
                                        text=[f"{rt:.1f}%" for rt in debt_ratio], textposition="top center",
                                        textfont=dict(size=14, color="#ffd600", family="Arial Black")
                                    ))
                                    fig_bs.update_layout(barmode="group", yaxis2=dict(overlaying="y", side="right", showgrid=False, tickfont=dict(size=12, color="#ffd600")))

                                fig_bs.update_layout(
                                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                                    font=dict(color="#ffffff", size=13), height=350, margin=dict(l=10, r=10, t=30, b=10),
                                    xaxis=dict(type='category', tickfont=dict(size=13, color="#ffffff")),
                                    yaxis=dict(tickfont=dict(size=12, color="#a39cb6")),
                                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=13))
                                )
                                st.plotly_chart(fig_bs, use_container_width=True)

                            # C. BIỂU ĐỒ BÁO CÁO LƯU CHUYỂN TIỀN TỆ (FONT TO)
                            elif "TIỀN TỆ" in section_title or "CASH FLOW" in section_title:
                                st.markdown("##### 📊 Biểu đồ Dòng tiền")
                                cf_choice = st.radio(
                                    "Chọn chỉ tiêu xem biểu đồ (LCTT)",
                                    ["Kinh doanh", "Đầu tư", "Tài chính"],
                                    key=f"cf_chart_radio_{idx_sec}",
                                    horizontal=True,
                                    label_visibility="collapsed"
                                )

                                cf_kd, cf_dt, cf_tc = [0.0]*len(years), [0.0]*len(years), [0.0]*len(years)
                                for r in rows:
                                    lbl_l = clean_text(r.get('label', '')).lower()
                                    r_v = r.get('values', [])
                                    if "kinh doanh" in lbl_l:
                                        cf_kd = [clean_num(v) for v in r_v]
                                    elif "đầu tư" in lbl_l:
                                        cf_dt = [clean_num(v) for v in r_v]
                                    elif "tài chính" in lbl_l:
                                        cf_tc = [clean_num(v) for v in r_v]

                                fig_cf = go.Figure()
                                if cf_choice == "Đầu tư":
                                    fig_cf.add_trace(go.Bar(
                                        x=str_years, y=cf_dt, name="Dòng tiền Đầu tư",
                                        marker_color="#ff9800", text=[f"{v:,.0f} Tỷ" if v != 0 else "-" for v in cf_dt],
                                        textposition="auto", textfont=dict(size=14, color="#ffffff", family="Arial Black")
                                    ))
                                elif cf_choice == "Tài chính":
                                    fig_cf.add_trace(go.Bar(
                                        x=str_years, y=cf_tc, name="Dòng tiền Tài chính",
                                        marker_color="#00e676", text=[f"{v:,.0f} Tỷ" if v != 0 else "-" for v in cf_tc],
                                        textposition="auto", textfont=dict(size=14, color="#ffffff", family="Arial Black")
                                    ))
                                else:
                                    fig_cf.add_trace(go.Bar(
                                        x=str_years, y=cf_kd, name="Dòng tiền Kinh doanh",
                                        marker_color="#00c853", text=[f"{v:,.0f} Tỷ" if v != 0 else "-" for v in cf_kd],
                                        textposition="auto", textfont=dict(size=14, color="#ffffff", family="Arial Black")
                                    ))

                                fig_cf.update_layout(
                                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                                    font=dict(color="#ffffff", size=13), height=350, margin=dict(l=10, r=10, t=30, b=10),
                                    xaxis=dict(type='category', tickfont=dict(size=13, color="#ffffff")),
                                    yaxis=dict(tickfont=dict(size=12, color="#a39cb6")),
                                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=13))
                                )
                                st.plotly_chart(fig_cf, use_container_width=True)

                        else:
                            st.info(f"Không có dữ liệu chi tiết cho mục {section_title}")
                else:
                    financial_error = report_data.get("financial_data_error")
                    if financial_error:
                        st.warning(
                            "Chưa tải được dữ liệu báo cáo tài chính: "
                            f"{financial_error}"
                        )
                    else:
                        st.warning(
                            "Chưa tìm thấy dữ liệu Báo cáo tài chính chi tiết trong bộ lưu trữ."
                        )
                fundamental_score = fundamental_result["scores"].get("fundamental")
                st.markdown("### 🧮 Bảng chi tiết chấm điểm cơ bản")
                if fundamental_score is None:
                    st.info("Chưa đủ dữ liệu chỉ số cơ bản để tính điểm.")
                else:
                    st.metric("Điểm cơ bản", f"{fundamental_score}/100")
                fundamental_factors = fundamental_result["score_breakdown"][
                    "fundamental_factors"
                ]
                st.dataframe(
                    pd.DataFrame([
                        {
                            "Chỉ tiêu": factor["label"],
                            "Dữ liệu": factor["evidence"],
                            "Quy tắc chấm": factor["rule"],
                            "Điểm": (
                                f"{factor['points']:+d}"
                                if factor["points"] is not None
                                else "Chưa tính"
                            ),
                        }
                        for factor in fundamental_factors
                    ]),
                    use_container_width=True,
                    hide_index=True,
                )

                with st.container(border=True):
                    st.markdown("**Đánh giá cơ bản từ AI**")
                    ai_comment = ai_commentaries["fundamental"]
                    if ai_comment.startswith("[⚠️"):
                        st.warning(ai_comment)
                    else:
                        st.markdown(clean_text(ai_comment))
            # --- TAB 4: ĐỊNH GIÁ & KỊCH BẢN ---
            with tab4:
                st.markdown(f'<div class="section-header">⚖️ {hoverify("Mô Hình Định Giá & Kịch Bản Giá Trị Hợp Lý")}</div>', unsafe_allow_html=True)
                valuation_range = valuation_result.get("target_price_range")
                valuation_price_label = (
                    (
                        f"{valuation_range[0] * 1000:,.0f} – "
                        f"{valuation_range[1] * 1000:,.0f} đ"
                    ).replace(",", ".")
                    if valuation_range
                    else "Chưa đủ dữ liệu"
                )
                current_pe = valuation_result.get("valuation_current_pe")
                current_pe_label = (
                    f"{current_pe:.2f} lần"
                    if isinstance(current_pe, (int, float))
                    else "Chưa có dữ liệu"
                )
                historical_pe = valuation_result.get("valuation_reference_pe")
                historical_pe_label = (
                    f"{historical_pe:.2f} lần"
                    if historical_pe is not None
                    else "Chưa đủ dữ liệu"
                )
                historical_pe_count = valuation_result.get(
                    "valuation_history_count", 0
                )
                upside_range = valuation_result.get("upside_range")
                upside_label = (
                    f"{upside_range[0]:+.1%} đến {upside_range[1]:+.1%}"
                    if upside_range
                    else "Chưa đủ dữ liệu"
                )
                normalized_eps = valuation_result.get("valuation_eps")
                normalized_eps_count = valuation_result.get("valuation_eps_count", 0)
                eps_normalized = valuation_result.get("valuation_eps_normalized", False)
                pb_target = valuation_result.get("valuation_pb_target")
                pb_label = (
                    f"{pb_target * 1000:,.0f} đ".replace(",", ".")
                    if pb_target is not None
                    else "Chưa đủ dữ liệu"
                )
                pb_reference = valuation_result.get("valuation_pb_reference")
                pb_count = valuation_result.get("valuation_pb_count", 0)
                pb_reference_label = (
                    f"{pb_reference:.2f} lần ({pb_count} kỳ)"
                    if pb_reference is not None
                    else "Chưa đủ dữ liệu"
                )

                with st.container(border=True):
                    st.markdown("#### Khoảng giá tham khảo theo P/E chuẩn hóa")
                    st.caption(
                        "EPS chuẩn hóa là trung vị EPS dương 3–5 năm gần nhất; dải giá "
                        "dùng Q1–Q3 P/E lịch sử của chính doanh nghiệp, không phải so sánh "
                        "với các công ty cùng ngành."
                    )
                    c_dg1, c_dg2, c_dg3, c_dg4 = st.columns(4)
                    c_dg1.metric("P/E hiện tại", current_pe_label)
                    c_dg2.metric("P/E trung vị lịch sử", historical_pe_label)
                    c_dg2.caption(
                        f"{historical_pe_count} kỳ hợp lệ "
                        f"(P/E dương, tối đa 150; cần tối thiểu 3 kỳ)"
                    )
                    c_dg3.metric("Dải giá P/E (Q1–Q3)", valuation_price_label)
                    c_dg4.metric("Upside toàn dải", upside_label)
                    st.caption(
                        f"{'EPS chuẩn hóa' if eps_normalized else 'EPS gần nhất (chưa chuẩn hóa)'}: "
                        f"{normalized_eps:,.0f} đ/cp "
                        f"({normalized_eps_count} năm EPS dương được tìm thấy). "
                        f"P/B đối chiếu: P/B trung vị {pb_reference_label} × BVPS gần nhất "
                        f"→ {pb_label}; không gộp P/B vào giá P/E. Với BĐS, BVPS có thể "
                        "chưa phản ánh đúng giá trị đất và dự án."
                        if normalized_eps is not None
                        else (
                            f"EPS: chưa đủ dữ liệu; P/B đối chiếu: "
                            f"P/B trung vị {pb_reference_label} → {pb_label}. "
                            "P/B không được gộp vào giá P/E; với BĐS, BVPS có thể chưa "
                            "phản ánh đúng giá trị đất và dự án."
                        )
                    )
                    st.markdown(f"**Cách tính:** {hoverify(valuation_result.get('target_method') or 'Chưa đủ dữ liệu định giá.')}")
                    st.caption(
                        (
                            f"Giá giữa dải theo P/E trung vị: "
                            f"{valuation_result['target_price'] * 1000:,.0f} đ. "
                            "Đây là khoảng tham khảo, không phải giá mục tiêu chắc chắn."
                        ).replace(",", ".")
                        if valuation_result.get("target_price") is not None
                        else "Chưa có đủ dữ liệu để tính giá giữa dải P/E."
                    )
                    if valuation_result.get("valuation_warning"):
                        st.warning(valuation_result["valuation_warning"])

                st.markdown("##### 🤖 Nhận xét diễn giải định giá")
                with st.container(border=True):
                    ai_comment = ai_commentaries["valuation"]
                    if ai_comment.startswith("[⚠️"):
                        st.warning(ai_comment)
                    else:
                        st.markdown(clean_text(ai_comment))

            # --- TAB 5: TIN TỨC & SỰ KIỆN (THẺ HIỂN THỊ SINH ĐỘNG) ---
            with tab5:
                st.markdown(f'<div class="section-header">📰 {hoverify("Tin Tức & Sự Kiện Doanh Nghiệp")}</div>', unsafe_allow_html=True)
                news_feed = report_data.get("news_feed") or {}
                news_list = news_feed.get("headlines") or []
                provider = news_feed.get("source") or "Nguồn tìm kiếm chưa xác định"
                period_labels = {
                    "d": "24 giờ",
                    "w": "7 ngày",
                    "m": "31 ngày",
                    "y": "1 năm",
                }
                period_used = period_labels.get(
                    news_feed.get("period_used"),
                    "chưa xác định",
                )

                if news_feed.get("status") == "error":
                    st.warning(
                        f"Không lấy được tin từ {provider}: "
                        f"{news_feed.get('error') or 'Lỗi không xác định.'}"
                    )
                elif news_feed.get("status") == "empty":
                    st.info(
                        f"{provider} không tìm thấy tin phù hợp trong khoảng đã kiểm tra. "
                        "Không thay bằng tin mẫu hoặc dữ liệu cũ."
                    )
                elif news_list:
                    st.caption(
                        f"Nguồn tìm kiếm: {provider} · Khoảng dữ liệu: {period_used} "
                        f"· Thời điểm truy vấn: {news_feed.get('fetched_at', 'Không rõ')}"
                    )

                if news_list:
                    for news in news_list:
                        with st.container(border=True):
                            st.markdown(f"**{hoverify(news.get('title') or 'Không có tiêu đề')}**")
                            published_at = (
                                news.get("published_at")
                                or news.get("date")
                                or "Không có ngày đăng"
                            )
                            publisher = news.get("source") or "Không rõ nhà xuất bản"
                            relevance = (
                                "Tin trực tiếp về mã"
                                if news.get("relevance") == "direct"
                                else "Tin liên quan"
                            )
                            st.caption(
                                f"Ngày đăng: {hoverify(published_at)} · "
                                f"Nhà xuất bản: {hoverify(publisher)} · {relevance}"
                            )
                            if news.get("summary"):
                                st.write(news["summary"])

                            article_url = news.get("url")
                            if article_url:
                                parsed_url = urlparse(article_url)
                                if parsed_url.scheme in {"http", "https"} and parsed_url.netloc:
                                    st.link_button(
                                        "Mở bài gốc",
                                        article_url,
                                        type="secondary",
                                    )
                                else:
                                    st.caption("Liên kết bài viết không hợp lệ.")
                            else:
                                st.caption("Nguồn không cung cấp liên kết bài gốc.")

                st.caption(
                    "Tin được truy xuất qua công cụ tìm kiếm; nhà xuất bản là nguồn bài viết. "
                    "Tin tìm được chưa đồng nghĩa sự kiện đã được xác minh."
                )

            # --- FOOTER CHUYÊN NGHIỆP ---
            footer_html = (
                f"<b>Chuyên viên phân tích / Mô hình:</b> {report_data.get('analyst_name', 'Hệ thống Quản trị & Đầu tư Thông minh')}<br>"
                f"<i>Ngày xuất báo cáo: {report_data.get('report_date', 'N/A')} · Thông tin chỉ mang tính chất tham khảo đầu tư.</i>"
            )
            st.markdown(f'<div class="footer-text">{footer_html}</div>', unsafe_allow_html=True)

        except Exception as e:
            st.error(f"Đã xảy ra lỗi trong quá trình thực thi: {e}")
            st.code(traceback.format_exc(), language="python")
elif analyze_btn and not ticker_input:
    st.warning("Vui lòng nhập mã cổ phiếu để bắt đầu!")
else:
    # Màn hình chào mừng đẹp mắt, căn giữa, không trống rỗng
    st.markdown(f"""
    <div class="welcome-box" style="margin-top: 40px; text-align: center; padding: 50px 30px !important;">
        <h3 style="color: #f6c85f; margin-bottom: 25px; font-weight: 800; font-size: 28px;">{hoverify('CHÀO MỪNG ĐẾN VỚI HỆ THỐNG PHÂN TÍCH THÔNG MINH')}</h3>
        <p style="color: var(--text-color); font-size: 19px; max-width: 900px; margin: 0 auto 25px auto; line-height: 1.8; font-weight: 600;">
            {hoverify('Hệ thống tự động tích hợp đa nguồn dữ liệu tài chính, kỹ thuật, định giá và tin tức:')}
        </p>
        <div style="display: inline-block; text-align: left; font-size: 18px; line-height: 2.2; font-weight: 700; color: #e2e8f0; margin-bottom: 30px;">
            📈 {hoverify('Biểu đồ kỹ thuật chuyên sâu (MA20, MA50, Volume)')}<br>
            🏦 {hoverify('Báo cáo tài chính toàn diện & Chỉ số cơ bản chuẩn mực')}<br>
            ⚖️ {hoverify('Mô hình định giá đa phương pháp & Kịch bản biên an toàn')}<br>
            🤖 {hoverify('Trợ lý AI tổng hợp nhận định và hỗ trợ ra quyết định đầu tư')}
        </div>
        <p style="color: #f6c85f; font-size: 20px; font-weight: 700;">
            {hoverify('Vui lòng nhập mã cổ phiếu ở khung phía trên để bắt đầu truy xuất báo cáo!')}
        </p>
    </div>
    """, unsafe_allow_html=True)