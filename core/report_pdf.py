# core/report_pdf.py
import os
import datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

def register_vietnamese_font():
    """Đăng ký font tiếng Việt Unicode từ dự án (assets/fonts) hoặc hệ thống"""
    # 1. Ưu tiên font trong thư mục assets/fonts/ của dự án
    project_font = "assets/fonts/arial.ttf"
    project_font_bold = "assets/fonts/arialbd.ttf"
    
    if os.path.exists(project_font):
        try:
            pdfmetrics.registerFont(TTFont('VietnameseFont', project_font))
            bold_font = project_font_bold if os.path.exists(project_font_bold) else project_font
            pdfmetrics.registerFont(TTFont('VietnameseFont-Bold', bold_font))
            return 'VietnameseFont', 'VietnameseFont-Bold'
        except Exception:
            pass

    # 2. Dự phòng lấy font hệ thống OS
    font_paths = [
        "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\segoeui.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    ]
    font_bold_paths = [
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "C:\\Windows\\Fonts\\segoeuib.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    ]
    
    for fp, fbp in zip(font_paths, font_bold_paths):
        if os.path.exists(fp):
            try:
                pdfmetrics.registerFont(TTFont('VietnameseFont', fp))
                bold_path = fbp if os.path.exists(fbp) else fp
                pdfmetrics.registerFont(TTFont('VietnameseFont-Bold', bold_path))
                return 'VietnameseFont', 'VietnameseFont-Bold'
            except Exception:
                pass
                
    return "Helvetica", "Helvetica-Bold"

class NumberedCanvas(canvas.Canvas):
    """Canvas vẽ Header & Footer chuyên nghiệp hỗ trợ đếm tổng số trang"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        # Dải màu trang trí phía trên Header
        self.setFillColor(colors.HexColor('#0F172A'))
        self.rect(0, 782, 612, 10, fill=True, stroke=False)
        self.setFillColor(colors.HexColor('#2563EB'))
        self.rect(0, 778, 612, 4, fill=True, stroke=False)
        
        # Dùng font hỗ trợ tiếng Việt cho Footer
        registered_fonts = pdfmetrics.getRegisteredFontNames()
        font_to_use = 'VietnameseFont' if 'VietnameseFont' in registered_fonts else 'Helvetica'
        
        self.setFont(font_to_use, 8)
        self.setFillColor(colors.HexColor('#64748B'))
        today_str = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
        self.drawString(36, 20, f"Báo cáo tự động được tạo lúc: {today_str} | Hệ thống Phân tích Đầu tư Chứng khoán")
        self.drawRightString(576, 20, f"Trang {self._pageNumber} / {page_count}")
        self.restoreState()

def generate_pdf(data_dict: dict, output_path: str = "outputs/stock_report.pdf") -> str:
    """
    Hàm nhận dictionary dữ liệu và xuất ra file PDF báo cáo chuẩn tiếng Việt
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    font_name, font_bold = register_vietnamese_font()
    
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=45,
        bottomMargin=40
    )
    
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName=font_bold,
        fontSize=22,
        leading=26,
        textColor=colors.HexColor('#0F172A'),
        spaceAfter=4
    )
    
    subtitle_style = ParagraphStyle(
        'SubTitle',
        parent=styles['Normal'],
        fontName=font_name,
        fontSize=11,
        leading=15,
        textColor=colors.HexColor('#475569'),
        spaceAfter=15
    )
    
    heading_style = ParagraphStyle(
        'SectionHeader',
        parent=styles['Heading2'],
        fontName=font_bold,
        fontSize=13,
        leading=17,
        textColor=colors.HexColor('#1E3A8A'),
        spaceBefore=14,
        spaceAfter=8
    )
    
    normal_style = ParagraphStyle(
        'NormalText',
        parent=styles['Normal'],
        fontName=font_name,
        fontSize=10,
        leading=15,
        textColor=colors.HexColor('#334155')
    )
    
    table_header_style = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontName=font_bold,
        fontSize=9,
        leading=12,
        textColor=colors.whitesmoke
    )
    
    table_cell_style = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName=font_name,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#1E293B')
    )

    story = []

    # 1. Header Báo cáo
    symbol = data_dict.get("symbol", "N/A")
    company_name = data_dict.get("company_name", "N/A")
    
    story.append(Paragraph(f"BÁO CÁO PHÂN TÍCH CỔ PHIẾU: {symbol}", title_style))
    story.append(Paragraph(f"<b>Doanh nghiệp:</b> {company_name}", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#E2E8F0'), spaceAfter=12))

    # 2. Thẻ Khuyến nghị & Tổng quan (Key KPI Cards)
    scoring = data_dict.get("scoring", {})
    rec = scoring.get("recommendation", "N/A")
    score = scoring.get("total_score", "N/A")
    price = data_dict.get("price", "N/A")
    change = data_dict.get("change_percent", "")
    price_str = f"{price:,} VND" if isinstance(price, (int, float)) else str(price)
    
    rec_color = '#16A34A' if 'MUA' in str(rec).upper() else ('#DC2626' if 'BÁN' in str(rec).upper() else '#D97706')

    kpi_card_data = [
        [
            Paragraph(f"<font color='#64748B' size=8>GIÁ HIỆN TẠI</font><br/><font size=14><b>{price_str}</b></font><br/><font color='#16A34A' size=9>{change}</font>", normal_style),
            Paragraph(f"<font color='#64748B' size=8>ĐIỂM ĐÁNH GIÁ</font><br/><font size=14><b>{score} / 10</b></font><br/><font color='#2563EB' size=9>Tổng hợp AI</font>", normal_style),
            Paragraph(f"<font color='#64748B' size=8>KHUYẾN NGHỊ ĐẦU TƯ</font><br/><font size=13 color='{rec_color}'><b>{rec}</b></font><br/><font color='#64748B' size=8>Trung & Dài hạn</font>", normal_style)
        ]
    ]
    
    t_kpi = Table(kpi_card_data, colWidths=[180, 180, 180])
    t_kpi.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8FAFC')),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('TOPPADDING', (0,0), (-1,-1), 10),
        ('BOTTOMPADDING', (0,0), (-1,-1), 10),
        ('LEFTPADDING', (0,0), (-1,-1), 12),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
    ]))
    story.append(t_kpi)
    story.append(Spacer(1, 15))

    # 3. Chỉ số Tài chính Cơ bản
    story.append(Paragraph("1. Chỉ số Tài chính Cốt lõi", heading_style))
    fin = data_dict.get("financials", {})
    fin_rows = [
        [Paragraph("Chỉ số tài chính", table_header_style), Paragraph("Giá trị thực tế", table_header_style)],
        [Paragraph("P/E (Chỉ số Định giá)", table_cell_style), Paragraph(str(fin.get("pe", "N/A")), table_cell_style)],
        [Paragraph("P/B (Chỉ số Giá / Giá trị sổ sách)", table_cell_style), Paragraph(str(fin.get("pb", "N/A")), table_cell_style)],
        [Paragraph("ROE (Tỷ suất LN trên Vốn chủ sở hữu)", table_cell_style), Paragraph(str(fin.get("roe", "N/A")), table_cell_style)],
        [Paragraph("Tăng trưởng Doanh thu", table_cell_style), Paragraph(str(fin.get("revenue_growth", "N/A")), table_cell_style)]
    ]
    t_fin = Table(fin_rows, colWidths=[300, 240])
    t_fin.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E293B')),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F8FAFC')]),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(t_fin)
    story.append(Spacer(1, 15))

    # 4. Tín hiệu Kỹ thuật
    story.append(Paragraph("2. Tín hiệu Kỹ thuật", heading_style))
    tech = data_dict.get("tech_signals", {})
    ma20_val = tech.get('ma20', 0)
    ma20_str = f"{ma20_val:,} VND" if isinstance(ma20_val, (int, float)) else str(ma20_val)
    
    tech_rows = [
        [Paragraph("Chỉ báo kỹ thuật", table_header_style), Paragraph("Giá trị / Tín hiệu", table_header_style)],
        [Paragraph("RSI (14) - Chỉ số sức mạnh tương đối", table_cell_style), Paragraph(str(tech.get("rsi", "N/A")), table_cell_style)],
        [Paragraph("MA20 - Đường trung bình động 20 ngày", table_cell_style), Paragraph(ma20_str, table_cell_style)],
        [Paragraph("Tín hiệu MACD", table_cell_style), Paragraph(str(tech.get("macd_signal", "N/A")), table_cell_style)]
    ]
    t_tech = Table(tech_rows, colWidths=[300, 240])
    t_tech.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E293B')),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F8FAFC')]),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
    ]))
    story.append(t_tech)
    story.append(Spacer(1, 15))

    # 5. Tóm tắt & Nhận định
    story.append(Paragraph("3. Tóm tắt Nhận định & Đánh giá", heading_style))
    summary_text = data_dict.get("summary", "Chưa có tóm tắt phân tích.")
    story.append(Paragraph(summary_text, normal_style))

    # Build PDF
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"✅ Đã xuất PDF thành công tại: {output_path}")
    return output_path


if __name__ == "__main__":
    # Thử kết nối dữ liệu từ mock_data
    try:
        from mock_data import MOCK_STOCK_DATA
        generate_pdf(MOCK_STOCK_DATA)
    except ImportError:
        sample_vietnamese_data = {
            "symbol": "FPT",
            "company_name": "Công ty Cổ phần FPT",
            "price": 135000,
            "change_percent": "+1.5%",
            "tech_signals": {
                "rsi": 58.4,
                "ma20": 132000,
                "macd_signal": "MUA TÍCH LŨY"
            },
            "financials": {
                "pe": 18.2,
                "pb": 4.1,
                "roe": "25.5%",
                "revenue_growth": "16.8%"
            },
            "scoring": {
                "total_score": 8.5,
                "recommendation": "KHUYẾN NGHỊ MUA"
            },
            "summary": "FPT tiếp tục duy trì tốc độ tăng trưởng ổn định nhờ sự bứt phá mạnh mẽ ở mảng xuất khẩu phần mềm và dịch vụ chuyển đổi số toàn cầu. Các chỉ số tài chính lành mạnh với ROE vượt mốc 25%."
        }
        generate_pdf(sample_vietnamese_data)