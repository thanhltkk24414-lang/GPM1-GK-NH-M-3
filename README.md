# Hệ Thống Phân Tích Cơ Hội Đầu Tư Cổ Phiếu 📈

Một ứng dụng web được xây dựng bằng **Streamlit** giúp nhà đầu tư tra cứu thông tin chứng khoán một cách tự động và trực quan.

## ✨ Tính Năng Nổi Bật
- **Phân tích kỹ thuật (Technical Analysis):** Hiển thị biểu đồ giá lịch sử, khối lượng giao dịch và các chỉ số (MA, MACD, RSI).
- **Phân tích cơ bản (Fundamental Analysis):** Truy xuất thông tin doanh nghiệp, chỉ số tài chính cơ bản.
- **Định giá tham khảo:** Dùng trung vị EPS dương tối đa 5 năm (cần ít nhất 3 năm để chuẩn hóa) nhân với Q1–Q3 của tối thiểu 3 kỳ P/E lịch sử; nếu thiếu EPS lịch sử, dùng EPS gần nhất nhưng gắn cảnh báo và độ tin cậy thấp. P/B trung vị × BVPS cần tối thiểu 3 kỳ và được trình bày riêng, không gộp thành giá tùy ý. BVPS kế toán có thể chưa phản ánh đầy đủ giá trị đất/dự án của doanh nghiệp bất động sản. Khuyến nghị MUA/BÁN chỉ được phát khi toàn dải giá đáp ứng ngưỡng tương ứng.
- **Tải Báo Cáo Thường Niên:** Chỉ tìm và tải PDF khi người dùng bấm nút; báo cáo đã lưu có thể tải trực tiếp.
- **Báo cáo phân tích PDF:** Chỉ kết xuất khi người dùng yêu cầu để tránh làm chậm lúc tra cứu mã.
- **Cơ chế "Lazy Loading" thông minh:** Không cần phải cào trước toàn bộ dữ liệu thị trường. Khi người dùng tra cứu một mã cổ phiếu bất kỳ, hệ thống mới tự động kết nối API, tạo Database, tải dữ liệu về và tính toán ngay lập tức. Điều này giúp mã nguồn cực kỳ nhẹ.
- **Lưu dữ liệu theo mã:** Lịch sử giá được lưu trong `data/market_data.db`; báo cáo tài chính thô được lưu riêng trong `data/raw/` theo mã cổ phiếu. Lần tra cứu đầu tiên hoặc khi dữ liệu còn thiếu có thể lâu hơn; các lần sau dùng dữ liệu đã lưu, trong khi giá realtime và tin tức vẫn được cập nhật.
- **Thông tin doanh nghiệp:** Sàn giao dịch lấy từ `data/symbols.csv`; ngành lấy từ cache `data/processed/company_metadata.csv` hoặc được tra cứu và lưu lại theo mã khi chưa có. Nếu nguồn hồ sơ không trả được thông tin, dashboard sẽ báo lỗi thay vì âm thầm bỏ qua.

## 🚀 Hướng Dẫn Cài Đặt & Chạy Ứng Dụng

### Yêu cầu
- **Python 3.10 – 3.13** (khuyến nghị 3.11+; đã kiểm thử trên 3.13). Khi cài trên Windows nhớ tick **"Add Python to PATH"**.
- **Git** (hoặc tải ZIP từ GitHub rồi giải nén).
- Kết nối Internet (lấy dữ liệu từ Vietcap/vnstock, tin tức, Gemini).
- *(Tùy chọn)* Khóa API Gemini để có phần nhận xét AI — xem Bước 4.

### Bước 1: Tải mã nguồn về máy
```bash
git clone https://github.com/thanhltkk24414-lang/GPM1-GK-NH-M-3.git
cd GPM1-GK-NH-M-3
```

### Bước 2: Tạo môi trường ảo (khuyến nghị)
**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```
Nếu PowerShell báo lỗi chặn script, chạy một lần: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. Dùng Command Prompt thì kích hoạt bằng `.venv\Scripts\activate.bat`.

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```
Sau khi kích hoạt, đầu dòng lệnh sẽ có `(.venv)`.

### Bước 3: Cài đặt thư viện
```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```
- File `requirements.txt` có dòng `--extra-index-url https://vnstocks.com/api/simple` để cài `vnstock`/`vnai`; cần có Internet.
- **PDF trên Linux/Ubuntu:** cài thêm thư viện hệ thống cho WeasyPrint:
  `sudo apt install $(cat packages.txt)`. Trên Windows không cần — ứng dụng tự dùng xhtml2pdf/ReportLab.
- **OCR báo cáo thường niên dạng scan (tùy chọn):** `pip install -r requirements-ocr.txt`. Không cài vẫn chạy bình thường, chỉ không đọc được PDF scan.

### Bước 4: Cấu hình khóa API (file `.env`)
File `.env` **không có trong repo** (bị `.gitignore`), nên bạn phải tự tạo ở thư mục gốc (cùng cấp với `app.py`):
```env
# Nhận xét AI - lấy miễn phí tại https://aistudio.google.com/apikey
GEMINI_API_KEY=your_gemini_key

# Tùy chọn: AI dự phòng nếu Gemini lỗi
# ANTHROPIC_API_KEY=...
# OPENAI_API_KEY=...

# Tùy chọn: chỉ cần nếu muốn chạy thêm collector DNSE
# DNSE_API_KEY=...
# DNSE_API_SECRET=...

# Tùy chọn: khóa vnstock (nếu có) để tăng giới hạn truy vấn
# VNSTOCK_API_KEY=...
```
**Cách lấy `GEMINI_API_KEY` (miễn phí):**
1. Mở https://aistudio.google.com/apikey và đăng nhập bằng tài khoản Google.
2. Bấm **Create API key** (chọn một project có sẵn hoặc tạo project mới).
3. Sao chép chuỗi key (bắt đầu bằng `AIza...`) và dán vào `.env` sau dấu `=`, không có dấu cách hay dấu ngoặc kép.
4. Lưu file rồi chạy lại `streamlit run app.py`.

- **Không có khóa nào vẫn chạy được**: dữ liệu giá, kỹ thuật, cơ bản, định giá và chấm điểm đều hoạt động; chỉ phần nhận xét AI sẽ báo thiếu khóa/không hiển thị.
- Tuyệt đối **không commit** file `.env` hay chia sẻ khóa công khai.

### Bước 5: Khởi chạy giao diện Web
```bash
streamlit run app.py
```
Trình duyệt tự mở `http://localhost:8501` (nếu không, hãy mở tay). Nhập mã cổ phiếu (VD: FPT, HPG, VCB) và hệ thống sẽ tự tải dữ liệu, tính toán, hiển thị báo cáo.

- **Lần đầu tra cứu mỗi mã sẽ chậm** (vài chục giây đến vài phút) vì phải tải giá và báo cáo tài chính; các lần sau dùng dữ liệu đã lưu.
- Muốn đổi cổng: `streamlit run app.py --server.port 8502`. Dừng ứng dụng bằng `Ctrl + C` trong terminal.
- Thư mục `data/`, `reports/`, `annual_reports/`, `outputs/` được tự tạo/ghi khi chạy; `data/market_data.db` và `data/symbols.csv` đã có sẵn trong repo.

### Xử lý sự cố thường gặp
| Hiện tượng | Cách xử lý |
|---|---|
| `streamlit` / `pip` không nhận lệnh | Chưa kích hoạt `.venv` hoặc Python chưa vào PATH; dùng `python -m pip ...` và `python -m streamlit run app.py` |
| Lỗi khi cài `vnstock`/`vnai` | Kiểm tra Internet/VPN/proxy; chạy lại `pip install -r requirements.txt` |
| Lỗi cài `weasyprint` hoặc thiếu thư viện khi xuất PDF | Windows: bỏ qua, ứng dụng tự dùng xhtml2pdf; Linux: cài `packages.txt` như Bước 3 |
| Không có nhận xét AI | Kiểm tra `GEMINI_API_KEY` trong `.env`, tên file phải đúng là `.env` (không phải `.env.txt`), rồi khởi động lại Streamlit |
| Báo lỗi hồ sơ doanh nghiệp / không tải được dữ liệu | Thường do mạng hoặc nguồn tạm giới hạn truy cập; chờ ít phút rồi tra cứu lại |
| `database is locked` | Chỉ chạy **một** tiến trình Streamlit; đóng các cửa sổ chạy trùng |
| Cổng 8501 đang bận | Dùng `--server.port` khác |

### Dữ liệu giá trên dashboard
- Khi mở báo cáo một mã, dashboard khởi động realtime pipeline nền một lần cho tiến trình Streamlit. WebSocket Vietcap ghi dữ liệu mới vào `data/market_data.db`; nguồn và thời điểm giá đang hiển thị được ghi ngay dưới báo cáo.
- Trong phiên giao dịch Việt Nam (thứ Hai–thứ Sáu, 09:00–11:30 và 13:00–15:00), dashboard ưu tiên quote realtime còn mới; nếu chưa có quote trong cache, hệ thống lấy snapshot bảng giá Vietcap.
- Ngoài khung giờ giao dịch, dashboard dùng giá đóng cửa lịch sử gần nhất và không yêu cầu quote realtime. Dashboard kiểm tra/cập nhật quote mỗi 2 phút khi đang mở báo cáo.
- Không chạy thêm `python -m stock_bot.data_pipeline.main` đồng thời với dashboard; Streamlit đã tự khởi động pipeline khi tra cứu mã và chạy hai tiến trình sẽ tạo kết nối dữ liệu trùng lặp.
- Nhận xét Gemini cho các tab dashboard được gọi tự động trong một yêu cầu chung và dùng cache trong một giờ; lỗi từ Gemini không bị cache để có thể thử lại. PDF dùng chung nhận xét tổng quan để tránh gọi trùng model.
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
