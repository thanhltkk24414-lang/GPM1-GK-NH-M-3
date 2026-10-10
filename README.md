# Hệ Thống Phân Tích Cơ Hội Đầu Tư Cổ Phiếu 📈

Một ứng dụng web được xây dựng bằng **Streamlit** giúp nhà đầu tư tra cứu thông tin chứng khoán một cách tự động và trực quan.

## ✨ Tính Năng Nổi Bật
- **Phân tích kỹ thuật (Technical Analysis):** Hiển thị biểu đồ giá lịch sử, khối lượng giao dịch và các chỉ số (MA, MACD, RSI).
- **Phân tích cơ bản (Fundamental Analysis):** Truy xuất thông tin doanh nghiệp, chỉ số tài chính cơ bản.
- **Tải Báo Cáo Thường Niên tự động:** Hệ thống tự động tìm kiếm, tải về và hiển thị Báo Cáo Thường Niên (PDF) của doanh nghiệp theo năm mới nhất.
- **Cơ chế "Lazy Loading" thông minh:** Không cần phải cào trước toàn bộ dữ liệu thị trường. Khi người dùng tra cứu một mã cổ phiếu bất kỳ, hệ thống mới tự động kết nối API, tạo Database, tải dữ liệu về và tính toán ngay lập tức. Điều này giúp mã nguồn cực kỳ nhẹ.

## 🚀 Hướng Dẫn Cài Đặt & Chạy Ứng Dụng

Yêu cầu: Máy tính của bạn cần cài đặt sẵn **Python** (phiên bản 3.8 trở lên).

### Bước 1: Tải mã nguồn về máy
Mở Terminal (hoặc Command Prompt/PowerShell) và gõ lệnh:
```bash
git clone https://github.com/thanhltkk24414-lang/GPM1-GK-NH-M-3.git
cd GPM1-GK-NH-M-3
```

### Bước 2: Cài đặt các thư viện cần thiết (Bắt buộc)
Chạy lệnh sau để cài đặt các thư viện lõi (Streamlit, Pandas, VNStock...):
```bash
pip install -r requirements.txt
```
*(Lưu ý: Nếu máy tính của bạn dùng Mac/Linux hoặc cài đặt nhiều phiên bản Python, bạn có thể cần dùng `pip3 install -r requirements.txt`)*

### Bước 3: Khởi chạy giao diện Web
Gõ lệnh sau để khởi động server Streamlit:
```bash
streamlit run app.py
```
Sau vài giây, trình duyệt web của bạn sẽ tự động mở trang web tại địa chỉ `http://localhost:8501`. 
Bạn chỉ cần nhập mã cổ phiếu (VD: FPT, HPG, VCB) vào ô tìm kiếm và hệ thống sẽ tự động làm phần việc còn lại!

### Dữ liệu giá trên dashboard
- Khi mở báo cáo một mã, dashboard khởi động realtime pipeline nền một lần cho tiến trình Streamlit. WebSocket Vietcap ghi dữ liệu mới vào `data/market_data.db`; nguồn và thời điểm giá đang hiển thị được ghi ngay dưới báo cáo.
- Trong phiên giao dịch Việt Nam (thứ Hai–thứ Sáu, 09:00–11:30 và 13:00–15:00), dashboard ưu tiên quote realtime còn mới; nếu chưa có quote trong cache, hệ thống lấy snapshot bảng giá Vietcap.
- Ngoài khung giờ giao dịch, dashboard dùng giá đóng cửa lịch sử gần nhất và không yêu cầu quote realtime. Dashboard kiểm tra/cập nhật quote mỗi 2 phút khi đang mở báo cáo.
- Không chạy thêm `python -m stock_bot.data_pipeline.main` đồng thời với dashboard; Streamlit đã tự khởi động pipeline khi tra cứu mã và chạy hai tiến trình sẽ tạo kết nối dữ liệu trùng lặp.
- Trên Windows nếu WeasyPrint thiếu thư viện hệ thống, PDF dùng xhtml2pdf; biểu đồ SVG được chuyển sang PNG để vẫn hiển thị trong báo cáo.
- Để hiển thị sàn giao dịch, đặt CSV danh sách mã tại `data/symbols.csv` (hoặc `data/symbol.csv`) với các cột `symbol` và `exchange`.

## 📁 Cấu Trúc Mã Nguồn Cơ Bản
- `app.py`: File chạy chính của giao diện Streamlit.
- `core/`: Chứa các module xử lý dữ liệu lõi (market_data.py, annual_report.py...).
- `stock_bot/`: Bot tự động cào dữ liệu từ các nguồn tài chính.
- `data/` (Tự động sinh ra): Thư mục chứa cơ sở dữ liệu SQLite `market_data.db` sau khi ứng dụng chạy.
- `annual_reports/` (Tự động sinh ra): Thư mục lưu các file PDF báo cáo tải về.

---
*Dự án phục vụ mục đích nghiên cứu và phân tích dữ liệu thị trường chứng khoán Việt Nam.*
