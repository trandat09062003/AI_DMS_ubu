# Driver Monitoring System (DMS) - AI Detection & Smart IoT Control

Hệ thống giám sát trạng thái tài xế (phát hiện buồn ngủ, ngáp, mất tập trung) thời gian thực bằng Trí tuệ Nhân tạo (AI) kết hợp Hệ thống Quản lý Hành trình & Cấu hình IoT Không Cần Màn Hình (Headless). 

Dự án kết hợp mô hình trích xuất đặc trưng khuôn mặt (**MediaPipe Face Mesh**), mạng học sâu chuỗi thời gian **LSTM (PyTorch)**, cùng giao diện Web Dashboard quản lý hành trình trên tên miền cố định **`http://wifi.local`**.

> **Lưu ý quan trọng**: Hệ thống được phát triển và tối ưu hóa chuyên biệt cho môi trường **Ubuntu / Linux** và **Raspberry Pi** (không hỗ trợ Windows).

---

## 🌟 Các Tính Năng Nổi Bật Mới Cập Nhật

### 1. Tối Ưu Hóa Tối Đa Cho Camera USB (UVC Webcams)
* **Triệt tiêu độ trễ/Lag khung hình (`CAP_PROP_BUFFERSIZE = 1`)**: Xóa sạch hàng đợi bộ nhớ đệm V4L2 giúp xử lý khung hình thời gian thực (Real-time).
* **Định dạng & Độ phân giải chuẩn hóa (MJPG @ 1280x720 - 30 FPS)**: Đặt thứ tự cấu hình `FOURCC` chuẩn giúp nhận 30 FPS mượt mà mà không làm nóng CPU như 1080p.
* **Tự động bỏ qua Raw Bayer của Camera Cáp (CSI)**: Khi bật chế độ `usb` trong `camera_config.json`, hệ thống sẽ tự động bỏ qua kiểm tra cổng Raw Bayer CSI giúp tránh khóa thiết bị `/dev/video0`.
* **Tự động Fallback độ phân giải**: Tự động linh hoạt chuyển đổi giữa `1280x720` và `640x480` nếu camera USB không đáp ứng được cấu hình cao.

### 2. Tích Hợp Ghi Âm Khoang Lái Bằng Micro Có Sẵn Trên USB Camera
* Tự động phát hiện thiết bị thu âm tích hợp sẵn trên USB Camera (ALSA Sound Card `hw:2,0`).
* Hỗ trợ ghi âm bằng chứng cabin khoang lái khi phát hiện báo động nguy hiểm (Level 2 / Level 3).

### 3. Tự Động Phát Wi-Fi Hotspot Không Cần Màn Hình (Headless Auto-Hotspot)
* Khởi chạy qua script [setup_autohotspot.sh](file:///home/kata/Documents/AI_DMS/setup_autohotspot.sh).
* **Cơ chế thông minh**:
  * **Tại nhà / Công ty**: Tự động kết nối mạng Wi-Fi đã lưu (Ưu tiên độ ưu tiên kết nối cao `Priority 10`).
  * **Trên xe ô tô / Không có Wi-Fi quen**: Tự động ngắt và phát trạm Wi-Fi Hotspot **`AI_DMS_Hotspot`** (Mật khẩu: `1234567890`) trong vòng 3 giây.

### 4. Trang Web Dashboard Local & Tên Miền Cố Định (`http://wifi.local`)
* **Tên miền cố định tự động cập nhật IP (mDNS Dynamic Resolution)**:
  Bất kể IP của Pi bị thay đổi thế nào (khi ở Hotspot `10.42.0.1` hay Wi-Fi nhà `192.168.1.x`), bạn luôn truy cập được Dashboard qua địa chỉ cố định:
  👉 **`http://wifi.local`** *(hoặc `http://dms.local`)*
* **Giao diện Web Dashboard Hiện Đại (Dark-Mode Glassmorphic)**:
  * **Tab 1: Báo Cáo Chuyến Đi (Driving Sessions)**: Thống kê tổng số chuyến đi, thời gian lái xe, số lần vi phạm (Ngủ gật/Mất tập trung/Ngáp), điểm mệt mỏi (Fatigue Score). Đánh giá tự động trạng thái chuyến đi: 🟢 **An toàn** | 🟡 **Cảnh báo** | 🔴 **Nguy hiểm**. Hỗ trợ xuất file báo cáo CSV.
  * **Tab 2: Quản Lý Wi-Fi (Wi-Fi Manager)**: Quét và chọn kết nối Wi-Fi 1-click trực tiếp từ điện thoại / laptop mà không cần gõ lệnh.

---

## 🛠️ Cấu Trúc Thư Mục Dự Án

```text
AI_DMS/
├── drowsiness_detector.py # Chương trình nhận diện AI & Dashboard chính
├── wifi_dashboard.py      # Web Dashboard Portal & API Báo cáo Chuyến đi (Cổng 80)
├── mdns_publisher.py      # Dịch vụ cập nhật tên miền cố định wifi.local
├── setup_autohotspot.sh   # Script tự động cài đặt Auto-Hotspot NetworkManager
├── camera_config.json     # Cấu hình Camera USB (1280x720 MJPG 30FPS)
├── lstm_model.py          # Kiến trúc mạng LSTM (PyTorch)
├── train_lstm.py          # Kịch bản huấn luyện mô hình LSTM
├── lstm_drowsiness.pth    # Trọng số mô hình đã huấn luyện
├── view_history.py        # Công cụ CLI truy vấn lịch sử CSDL SQLite
├── run_dms.sh             # Script khởi chạy ứng dụng chính
├── install_remote.sh      # Script cài đặt AnyDesk quản lý từ xa
├── requirements.txt       # Danh sách thư viện Python
└── README.md              # Tài liệu hướng dẫn sử dụng
```

---

## 🚀 Hướng Dẫn Cài Đặt & Khởi Chạy

### 1. Khởi chạy Hệ thống Nhận diện AI DMS
```bash
# Cấp quyền thực thi và chạy
chmod +x run_dms.sh
./run_dms.sh
```

### 2. Cài đặt Auto Hotspot & Web Dashboard (`http://wifi.local`)
Để bật tính năng tự động phát Wi-Fi khi lên xe và trang Web Dashboard cố định:
```bash
# Chạy script cài đặt Auto-Hotspot
sudo ./setup_autohotspot.sh
```

Sau khi cài xong, bạn có thể kết nối Wi-Fi **`AI_DMS_Hotspot`** (Pass: `1234567890`) và mở trình duyệt truy cập:
👉 **`http://wifi.local`**

---

## 📊 Quản Lý Lịch Sử Hành Trình (SQLite Database)

Dữ liệu từng chuyến đi được tự động lưu vào cơ sở dữ liệu `dms_history.db`:
* **Xem qua Web Dashboard**: Truy cập `http://wifi.local` ➜ Xem tab **Chuyến Đi**.
* **Xuất báo cáo CSV**: Nhấp nút **Xuất File CSV** trên giao diện Web hoặc chạy lệnh CLI:
  ```bash
  python3 view_history.py --export
  ```

---

## ⚙️ Sơ Đồ Kết Nối Phần Cứng (Raspberry Pi GPIO)

* **Động cơ rung**: Kết nối chân **GPIO 17** (Pin 11).
* **Còi báo động (Buzzer)**: Kết nối chân **GPIO 27** (Pin 13).
* **Nút bấm Quét Lại (Re-calibrate Button)**: Chân **GPIO 22** (Pin 15) và **GND** (Pin 14).

---

## 📝 Giấy Phép & Đóng Góp

Dự án được phát triển bởi **Trần Đạt** (trandat09062003). Tất cả các đóng góp và báo lỗi xin vui lòng gửi qua phần Issues trên GitHub.
