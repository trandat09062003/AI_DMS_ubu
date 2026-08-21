# 🚗 AI Driver Monitoring System (AI_DMS)
### Hệ Thống Giám Sát Tài Xế & Cảnh Báo An Toàn Thời Gian Thực Bằng Trí Tuệ Nhân Tạo

[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Ubuntu%20%7C%20Raspberry%20Pi-orange.svg)](https://ubuntu.com/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**AI_DMS** là giải pháp IoT nhúng kết hợp Trí tuệ Nhân tạo (Computer Vision & Deep Learning) nhằm giám sát hành vi, phát hiện tức thì các dấu hiệu nguy hiểm của người lái xe như **buồn ngủ, vi giấc ngủ (microsleep), ngáp liên tục, mất tập trung hoặc cúi/ngoảnh mặt**.

Hệ thống được thiết kế để vận hành độc lập trên xe (**Headless - không bắt buộc cần màn hình lớn**), tự động quản lý kết nối mạng, phát âm thanh cảnh báo bằng **giọng nói Tiếng Việt**, hiển thị trạng thái qua **màn hình phụ OLED mini**, ghi âm khoang lái, gửi cảnh báo tức thì về điện thoại qua **Telegram Bot**, và cung cấp trang **Web Dashboard** quản lý hành trình trên tên miền cố định **`http://wifi.local`**.

---

## 📑 Mục Lục
1. [Tính Năng Nổi Bật](#-tính-năng-nổi-bật)
2. [Cấu Trúc Thư Mục](#-cấu-trúc-thư-mục)
3. [Danh Mục Linh Kiện & Sơ Đồ Đấu Nối Phần Cứng](#-danh-mục-linh-kiện--sơ-đồ-đấu-nối-phần-cứng)
4. [Hướng Dẫn Cài Đặt Từng Bước (Dành Cho Người Mới)](#-hướng-dẫn-cài-đặt-từng-bước-dành-cho-người-mới)
5. [Kiểm Tra & Chẩn Đoán Phần Cứng](#-kiểm-tra--chẩn-đoán-phần-cứng)
6. [Cấu Hình Cảnh Báo Telegram Bot](#-cấu-hình-cảnh-báo-telegram-bot)
7. [Vận Hành & Khởi Chạy Hệ Thống](#-vận-hành--khởi-chạy-hệ-thống)
8. [Hướng Dẫn Sử Dụng Web Dashboard (`http://wifi.local`)](#-hướng-dẫn-sử-dụng-web-dashboard-httpwifilocal)
9. [Xử Lý Lỗi Thường Gặp (FAQ & Troubleshooting)](#-xử-lý-lỗi-thường-gặp-faq--troubleshooting)

---

## 🌟 Tính Năng Nổi Bật

### 1. Nhận Diện Trạng Thái Tài Xế Bằng AI (Real-Time AI Detection)
* **MediaPipe Face Mesh 468 Điểm**: Trích xuất chính xác các mốc tọa độ mắt, miệng và góc xoay đầu 3 chiều (Pitch, Yaw, Roll).
* **Mô hình học sâu chuỗi thời gian PyTorch LSTM**: Phân biệt chuẩn xác giữa nháy mắt tự nhiên và nhắm mắt buồn ngủ theo thời gian thực.
* **Cơ chế hiệu chuẩn tự động (Auto Calibration)**: Tự động học kích thước mắt/miệng đặc thù của từng người lái xe trong 3-5 giây đầu tiên khi lên xe.
* **Đa tầng phát hiện nguy hiểm**:
  * 😴 **Buồn ngủ nhẹ (Level 1)**: Mắt nhắm liên tục $\ge 1.5$ giây.
  * 🚨 **Ngủ gật nguy hiểm / Vi giấc ngủ (Level 2 & 3)**: Mắt nhắm liên tục $\ge 3.0$ giây.
  * 🥱 **Ngáp liên tục**: Độ mở miệng (MAR) vượt ngưỡng an toàn trong thời gian dài.
  * 📱 **Mất tập trung**: Nghiêng đầu, cúi mặt nhìn điện thoại hoặc ngoảnh mặt khỏi hướng lái quá 2 giây.

### 2. Hệ Thống Cảnh Báo Đa Tầng (Multi-Modal Alerts)
* 🗣️ **Giọng nói Tiếng Việt tự nhiên**: Phát nhắc nhở qua loa Jack 3.5mm kết nối mạch khuếch đại TDA2050 (ví dụ: *"Cảnh báo: Bạn đang có dấu hiệu buồn ngủ, vui lòng tập trung lái xe!"*).
* 🔊 **Còi Bíp (Buzzer)**: Kích hoạt âm thanh tần số cao báo động thức giấc.
* 📳 **Động cơ rung (Vibration Motor)**: Gắn trên vô lăng hoặc ghế lái để cảnh báo xúc giác khi có nguy hiểm cấp cao.
* 🖥️ **Màn hình OLED mini 0.96 inch (SSD1306)**: Hiển thị liên tục chỉ số EAR/MAR, FPS, mức độ mệt mỏi (Fatigue Score) và trạng thái hệ thống.

### 3. Cảnh Báo Tức Thời Qua Telegram Bot
* Tự động chụp ảnh khuôn mặt vi phạm tại thời điểm nguy hiểm.
* Tự động thu âm đoạn âm thanh 10 giây trong cabin bằng micro trên camera USB.
* Gửi ngay lập tức ảnh chụp + file ghi âm + thông tin vi phạm (thời gian, loại lỗi) về nhóm/kênh Telegram của chủ xe hoặc người quản lý đội xe.

### 4. Tự Động Quản Lý Mạng & Web Portal Không Cần Màn Hình (Headless)
* **Auto-Hotspot thông minh**:
  * Khi ở nhà / văn phòng: Tự động kết nối Wi-Fi quen thuộc đã lưu.
  * Khi lên xe ô tô / mất sóng: Tự động chuyển sang phát trạm Wi-Fi **`AI_DMS_Hotspot`** (Mật khẩu: `1234567890`) trong vòng 3 giây.
* **Tên miền cố định qua mDNS**: Bất kể IP máy thay đổi thế nào, luôn truy cập được Web Dashboard qua địa chỉ duy nhất: **`http://wifi.local`** *(hoặc `http://dms.local`)*.
* **Web Dashboard Dark-Mode**:
  * Xem nhật ký các chuyến đi (Driving Sessions), tổng thời gian lái, số lần vi phạm.
  * Tính điểm mệt mỏi (Fatigue Score) và đánh giá chuyến đi: 🟢 An toàn | 🟡 Cảnh báo | 🔴 Nguy hiểm.
  * Xuất báo cáo dữ liệu định dạng `.csv`.
  * Quét và kết nối mạng Wi-Fi xung quanh trực tiếp trên giao diện Web.
  * Cấu hình Telegram Bot và kiểm tra thử phần cứng (Test Loa, Còi, Rung, OLED, Ghi âm) ngay trên Web.

---

## 📂 Cấu Trúc Thư Mục

```text
AI_DMS/
├── drowsiness_detector.py # Chương trình AI nhận diện & điều khiển trung tâm
├── audio_manager.py       # Quản lý âm thanh Jack 3.5mm, TDA2050 & ghi âm Micro USB
├── oled_manager.py        # Driver I2C điều khiển màn hình OLED SSD1306 128x64
├── telegram_bot.py        # Xử lý gửi tin nhắn, ảnh và file ghi âm qua Telegram
├── wifi_dashboard.py      # Web Dashboard quản lý hành trình & cấu hình (Cổng 80)
├── mdns_publisher.py      # Dịch vụ định danh tên miền cố định wifi.local
├── setup_autohotspot.sh   # Script cài đặt tính năng tự động phát Wi-Fi Hotspot
├── run_dms.sh             # Script khởi chạy toàn bộ hệ thống với 1 lệnh
├── view_history.py        # Công cụ dòng lệnh xem & xuất dữ liệu lịch sử SQLite
│
├── lstm_model.py          # Kiến trúc mạng học sâu LSTM (PyTorch)
├── lstm_drowsiness.pth    # Trọng số mô hình LSTM đã huấn luyện
├── train_lstm.py          # Script thu thập dữ liệu & huấn luyện lại mô hình
│
├── audio_prompts/         # Thư mục chứa âm thanh còi bíp và giọng nói Tiếng Việt
├── camera_config.json     # Cấu hình Camera USB (Độ phân giải 1280x720, MJPG, 30 FPS)
├── calibration_config.json# Ngưỡng mắt/miệng sau khi hiệu chuẩn
├── telegram_config.json.example # File mẫu hướng dẫn cài đặt Telegram Bot
│
├── test_hardware.py       # Công cụ kiểm tra toàn diện phần cứng
├── test_speaker.py        # Kiểm tra riêng ngõ ra âm thanh & giọng nói
├── test_oled.py           # Kiểm tra riêng màn hình OLED 128x64
├── test_camera.py         # Kiểm tra riêng độ phân giải & FPS Camera USB
│
├── requirements.txt       # Danh sách thư viện Python cần thiết
├── summary.txt            # Tóm tắt kiến trúc kỹ thuật & sơ đồ chân
└── README.md              # Tài liệu hướng dẫn sử dụng chi tiết
```

---

## 🔌 Danh Mục Linh Kiện & Sơ Đồ Đấu Nối Phần Cứng

### 1. Danh Sách Linh Kiện Cần Chuẩn Bị
1. **Bo mạch máy tính**: Raspberry Pi 4 Model B (hoặc Raspberry Pi 3B+, Mini PC chạy Ubuntu).
2. **Nguồn cấp**: Củ nguồn Type-C chuẩn 5V - 3A hoặc 3.5A.
3. **Thẻ nhớ**: MicroSD dung lượng từ 32GB Class 10 trở lên.
4. **Camera & Micro**: Camera USB (Webcam UVC) có tích hợp micro.
5. **Màn hình OLED**: Module OLED 0.96 inch giao tiếp I2C (Chip SSD1306, 128x64 điểm ảnh).
6. **Còi Bíp**: Module Buzzer 5V chủ động (Active Buzzer Module).
7. **Động cơ rung**: Module rung 5V / 3.3V (Vibration Motor Module).
8. **Mạch khuếch đại âm thanh & Loa**: Mạch TDA2050 (hoặc PAM8403) kết nối loa qua ngõ Jack 3.5mm.
9. **Nút bấm hiệu chuẩn (Tùy chọn)**: Nút nhấn nhả 2 chân (Push button).
10. **Dây cắm**: Bộ dây Dupont cắm testboard (Cái - Cái và Đực - Cái).

---

### 2. Bảng Sơ Đồ Đấu Nối Chân GPIO (Raspberry Pi 40-Pin Header)

| Thiết Bị / Module | Chân Trên Module | Nối Vào Chân Raspberry Pi | Vị Trí Chân Vật Lý (Pin No.) | Ghi Chú |
| :--- | :--- | :--- | :--- | :--- |
| **Màn Hình OLED** | **VCC** | Nguồn 3.3V | **Pin 1** (hoặc Pin 17) | Cấp nguồn 3.3V cho màn hình |
| *(SSD1306 I2C)* | **GND** | Nối Đất (Ground) | **Pin 9** (hoặc Pin 6, 14) | Nối cực âm |
| | **SDA** | **GPIO 2** (I2C1 SDA) | **Pin 3** | Chân truyền tín hiệu dữ liệu I2C |
| | **SCL** | **GPIO 3** (I2C1 SCL) | **Pin 5** | Chân truyền xung nhịp clock I2C |
| **Còi Bíp (Buzzer)** | **VCC / (+)** | Nguồn 5V | **Pin 2** (hoặc Pin 4) | Cấp nguồn 5V cho còi |
| | **GND / (-)** | Nối Đất (Ground) | **Pin 14** (hoặc Pin 20) | Nối cực âm |
| | **I/O (Signal)** | **GPIO 27** | **Pin 13** | Tín hiệu điều khiển đóng ngắt |
| **Động Cơ Rung** | **VCC** | Nguồn 5V / 3.3V | **Pin 4** (hoặc Pin 17) | Cấp nguồn cho motor rung |
| | **GND** | Nối Đất (Ground) | **Pin 20** (hoặc Pin 25) | Nối cực âm |
| | **IN (Signal)** | **GPIO 17** | **Pin 11** | Tín hiệu kích hoạt rung |
| **Nút Bấm Hiệu Chuẩn**| **Chân 1** | **GPIO 22** | **Pin 15** | Kéo lên nội trở (Pull-up) |
| *(Calibrate Button)*| **Chân 2** | Nối Đất (Ground) | **Pin 14** (hoặc Pin 9) | Khi bấm nút chân 1 nối GND |
| **Ngõ Ra Loa** | **Jack 3.5mm** | Cổng Jack 3.5mm Pi | Cổng tròn 3.5mm | Xuất âm thanh ra mạch TDA2050 |
| **Camera & Micro** | **Cáp USB** | Cổng USB 3.0/2.0 | Cổng USB trên Pi | Cắm trực tiếp cổng USB |

---

## 🛠️ Hướng Dẫn Cài Đặt Từng Bước (Dành Cho Người Mới)

### Bước 1: Chuẩn Bị Môi Trường Hệ Điều Hành
* Cài đặt hệ điều hành **Ubuntu 22.04 / 20.04 LTS (64-bit)** hoặc **Raspberry Pi OS (64-bit)** lên thẻ nhớ.
* Cấp nguồn cho thiết bị, mở ứng dụng **Terminal** (Dòng lệnh).

### Bước 2: Tải Mã Nguồn Về Máy
Nhập các lệnh sau vào Terminal và nhấn Enter:
```bash
cd ~
git clone https://github.com/trandat09062003/AI_DMS_ubu.git AI_DMS
cd AI_DMS
```

### Bước 3: Cài Đặt Các Gói Phụ Thuộc Của Hệ Thống Linux
Hệ thống cần các thư viện xử lý âm thanh ALSA, xử lý hình ảnh OpenCV, giao thức mạng mDNS và quét mã:
```bash
sudo apt update
sudo apt install -y python3-pip python3-venv libgl1 libglib2.0-0 portaudio19-dev \
                    libasound2-dev libzbar0 tesseract-ocr alsa-utils pulseaudio \
                    network-manager avahi-daemon i2c-tools
```

### Bước 4: Tạo Môi Trường Ảo Python & Cài Thư Viện
Tạo một môi trường Python độc lập để đảm bảo không bị xung đột phiên bản:
```bash
# 1. Khởi tạo môi trường ảo venv
python3 -m venv venv

# 2. Kích hoạt môi trường ảo
source venv/bin/activate

# 3. Cập nhật pip và cài đặt toàn bộ gói cần thiết
pip install --upgrade pip
pip install -r requirements.txt
```

### Bước 5: Kích Hoạt Giao Tiếp I2C Cho Màn Hình OLED (Trên Raspberry Pi)
Nếu bạn đang dùng Raspberry Pi, hãy bật cổng I2C:
```bash
sudo raspi-config
```
* Chọn mục **Interface Options** $\rightarrow$ chọn **I2C** $\rightarrow$ chọn **Yes** để kích hoạt $\rightarrow$ chọn **Finish**.
* Kiểm tra xem màn hình OLED đã được nhận diện chưa:
```bash
sudo i2cdetect -y 1
```
*(Nếu nhìn thấy số `3c` trong bảng hiển thị là màn hình đã nối dây thành công).*

---

## 🔍 Kiểm Tra & Chẩn Đoán Phần Cứng

Trước khi chạy hệ thống chính thức, bạn có thể chạy các công cụ kiểm tra tự động để đảm bảo mọi linh kiện hoạt động 100%:

### 1. Kiểm tra Loa & Giọng nói AI Tiếng Việt
```bash
source venv/bin/activate
python3 test_speaker.py
```
*Chương trình sẽ tự động tăng âm lượng, phát các tiếng còi bíp và giọng nói hướng dẫn tiếng Việt ra loa.*

### 2. Kiểm tra Màn hình OLED mini 128x64
```bash
python3 test_oled.py
```
*Màn hình OLED sẽ lần lượt hiển thị các biểu tượng và các trạng thái cảnh báo mẫu trong vòng 15 giây.*

### 3. Kiểm tra Camera USB & Micro Thu Âm
```bash
python3 test_camera.py
```
*Chương trình sẽ kiểm tra độ phân giải, tốc độ khung hình (FPS) và tự động chụp 1 ảnh mẫu để xác nhận camera hoạt động mượt mà.*

### 4. Kiểm tra Toàn Diện Toàn Bộ Phần Cứng (OLED + Loa + Còi + Rung + Màn hình)
```bash
python3 test_hardware.py
```

---

## 📲 Cấu Hình Cảnh Báo Telegram Bot

Hệ thống hỗ trợ gửi ngay ảnh chụp khuôn mặt và file ghi âm cabin khi phát hiện ngủ gật về Telegram. Để cài đặt:

### Cách 1: Cấu hình nhanh trực tiếp qua Web Dashboard (Khuyên dùng)
1. Mở trình duyệt truy cập: **`http://wifi.local`** (hoặc `http://10.42.0.1`).
2. Chọn tab **Cấu Hình Telegram**.
3. Điền **Bot Token** và **Chat ID** của bạn $\rightarrow$ Gạt nút bật **Kích hoạt cảnh báo Telegram** $\rightarrow$ Nhấn **Lưu Cấu Hình & Gửi Tin Test**.

### Cách 2: Cấu hình qua tệp tin
1. Tạo tệp cấu hình từ file mẫu:
   ```bash
   cp telegram_config.json.example telegram_config.json
   nano telegram_config.json
   ```
2. Điền thông tin của bạn vào file:
   ```json
   {
       "enabled": true,
       "bot_token": "1234567890:AAEexampleTokenHere...",
       "chat_id": "-100123456789"
   }
   ```
3. Nhấn `Ctrl + O` rồi `Enter` để lưu, sau đó nhấn `Ctrl + X` để thoát.

> 💡 **Mẹo lấy Bot Token & Chat ID trong 60 giây**:
> * **Bot Token**: Nhắn tin với [@BotFather](https://t.me/BotFather) trên Telegram $\rightarrow$ Gõ lệnh `/newbot` $\rightarrow$ Đặt tên cho bot và nhận mã Token.
> * **Chat ID**: Nhắn tin với [@userinfobot](https://t.me/userinfobot) để lấy Chat ID cá nhân của bạn, hoặc thêm Bot vào nhóm chung và lấy ID của nhóm.

---

## 🚀 Vận Hành & Khởi Chạy Hệ Thống

### 1. Cài Đặt Tự Động Phát Wi-Fi Hotspot & Tên Miền Cố Định
Chạy lệnh cài đặt dịch vụ mạng một lần duy nhất:
```bash
sudo chmod +x setup_autohotspot.sh run_dms.sh
sudo ./setup_autohotspot.sh
```

### 2. Khởi Chạy Ứng Dụng AI_DMS
Chạy toàn bộ hệ thống bằng script điều khiển tích hợp:
```bash
./run_dms.sh
```
*Script sẽ tự động khởi tạo ngõ ra âm thanh, kích hoạt dịch vụ tên miền `wifi.local`, khởi chạy Web Dashboard ở nền và mở chương trình nhận diện AI.*

---

### 3. Cài Đặt Hệ Thống Tự Chạy Khi Bật Nguồn (Tùy Chọn Chuyên Nghiệp)
Để hệ thống tự động khởi động mỗi khi bạn nổ máy cấp nguồn cho xe:
```bash
sudo nano /etc/systemd/system/aidms.service
```
Dán nội dung sau vào file:
```ini
[Unit]
Description=AI Driver Monitoring System (AI_DMS)
After=network.target sound.target

[Service]
Type=simple
User=kata
WorkingDirectory=/home/kata/Documents/AI_DMS
ExecStart=/bin/bash /home/kata/Documents/AI_DMS/run_dms.sh
Restart=always
RestartSec=5
Environment=DISPLAY=:0

[Install]
WantedBy=multi-user.target
```
*(Thay `kata` và đường dẫn thư mục tương ứng với tên người dùng trên máy của bạn)*.

Kích hoạt dịch vụ:
```bash
sudo systemctl daemon-reload
sudo systemctl enable aidms.service
sudo systemctl start aidms.service
```

---

## 🌐 Hướng Dẫn Sử Dụng Web Dashboard (`http://wifi.local`)

Hệ thống cung cấp giao diện Web trực quan, tối ưu cho cả điện thoại di động và máy tính:

1. **Kết nối**:
   * Dùng điện thoại / laptop quét Wi-Fi và kết nối vào trạm: **`AI_DMS_Hotspot`**
   * Mật khẩu: **`1234567890`**
2. **Truy cập**:
   * Mở trình duyệt Web (Chrome, Safari, Firefox) và truy cập: **`http://wifi.local`** *(hoặc `http://dms.local`, hoặc địa chỉ IP `http://10.42.0.1`)*.

### Các Chức Năng Chính Trên Web:
* 📊 **Tab Chuyến Đi (Driving Sessions)**:
  * Xem danh sách tất cả các hành trình đã ghi nhận.
  * Thống kê thời gian lái xe, số lần nhắm mắt ngủ gật, số lần ngáp, số lần quay đầu mất tập trung.
  * Điểm số mệt mỏi (Fatigue Score) kèm đánh giá an toàn trực quan.
  * Tải báo cáo toàn diện dạng file `.csv` về máy.
* 📶 **Tab Quản Lý Wi-Fi**:
  * Quét các mạng Wi-Fi lân cận và nhập mật khẩu để kết nối trực tiếp chỉ với 1 thao tác mà không cần gắn bàn phím chuột vào Pi.
* 🤖 **Tab Cấu Hình Telegram**:
  * Thiết lập và bật/tắt nhận cảnh báo tức thời qua điện thoại.
* 🧪 **Tab Kiểm Tra Phần Cứng (Hardware Test)**:
  * Bấm nút để test trực tiếp: Kêu còi Bíp, Rung motor, Phát giọng nói Tiếng Việt, Test màn hình OLED, Ghi âm micro 10 giây.

---

## ❓ Xử Lý Lỗi Thường Gặp (FAQ & Troubleshooting)

<details>
<summary><b>1. Không truy cập được trang web http://wifi.local?</b></summary>

* **Khắc phục**: 
  1. Kiểm tra xem điện thoại của bạn đã kết nối vào Wi-Fi `AI_DMS_Hotspot` chưa.
  2. Một số dòng máy Android/Windows cũ có thể chưa hỗ trợ mDNS, bạn có thể truy cập trực tiếp bằng địa chỉ IP: **`http://10.42.0.1`** (hoặc gõ lệnh `hostname -I` trong terminal để lấy IP hiện tại).
</details>

<details>
<summary><b>2. Màn hình OLED không sáng hoặc báo lỗi không tìm thấy?</b></summary>

* **Khắc phục**:
  1. Kiểm tra thứ tự 4 chân dây nối: VCC (3.3V), GND (Nối đất), SDA (Pin 3), SCL (Pin 5).
  2. Chạy lệnh `sudo i2cdetect -y 1` để kiểm tra địa chỉ phần cứng (chuẩn là `0x3C`).
  3. Đảm bảo đã bật giao tiếp I2C trong `sudo raspi-config`.
</details>

<details>
<summary><b>3. Loa không phát ra âm thanh hoặc âm thanh bị nhỏ/rè?</b></summary>

* **Khắc phục**:
  1. Chạy lệnh kiểm tra loa: `python3 test_speaker.py`.
  2. Mở trình điều khiển âm lượng hệ thống bằng lệnh `alsamixer` và kiểm tra kênh `Headphones` / `Master` không bị Mute (nhấn phím `M` để bật tiếng) và tăng âm lượng lên $90\%$.
  3. Đảm bảo giắc cắm 3.5mm đã cắm chặt vào cổng tai nghe trên Raspberry Pi.
</details>

<details>
<summary><b>4. Camera báo lỗi "Device or resource busy" hoặc không mở được khung hình?</b></summary>

* **Khắc phục**:
  1. Kiểm tra xem có tiến trình nào khác đang sử dụng camera hay không bằng lệnh: `sudo fuser /dev/video*`.
  2. Rút cáp USB camera và cắm lại vào cổng USB 3.0 (màu xanh dương).
  3. Chạy `python3 test_camera.py` để kiểm tra nhận diện thiết bị.
</details>

<details>
<summary><b>5. Telegram Bot không gửi được tin nhắn hoặc hình ảnh?</b></summary>

* **Khắc phục**:
  1. Đảm bảo thiết bị đã được kết nối Internet (qua Wi-Fi hoặc 4G).
  2. Kiểm tra lại `bot_token` và `chat_id` trong tab Cấu hình Telegram trên Dashboard `http://wifi.local` và bấm nút **Gửi Thử Tin Nhắn Test**.
</details>

---

## 📄 Bản Quyền & Giấy Phép (License)

Dự án được phát hành theo giấy phép mã nguồn mở **MIT License**. Mọi đóng góp, đề xuất nâng cấp hoặc báo cáo lỗi xin vui lòng tạo Issue hoặc Pull Request trên GitHub Repository.
