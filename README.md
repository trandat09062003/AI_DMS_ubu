# Driver Monitoring System (DMS) - AI Detection

Hệ thống giám sát trạng thái tài xế (phát hiện buồn ngủ, ngáp, mất tập trung) thời gian thực bằng trí tuệ nhân tạo. Dự án kết hợp mô hình trích xuất đặc trưng khuôn mặt (MediaPipe Face Mesh) và mô hình học sâu tuần hoàn LSTM (PyTorch) nhằm phân tích chuỗi hành vi thời gian thực trong 60 giây và đưa ra dự báo sớm trạng thái buồn ngủ/vi ngủ (Microsleep).

> **Lưu ý quan trọng**: Hệ thống được phát triển và tối ưu hóa chuyên biệt cho môi trường **Ubuntu / Linux** và **Raspberry Pi** (không hỗ trợ hệ điều hành Windows).

---

## Các tính năng chính

- **Phân tích đặc trưng sinh học thời gian thực**:
  - **EAR (Eye Aspect Ratio)**: Đo lường độ mở mắt động theo trạng thái sinh lý của tài xế.
  - **MAR (Mouth Aspect Ratio)**: Đánh giá biên độ mở miệng để phát hiện hành vi ngáp.
  - **Head Pose Estimation (SolvePnP)**: Tính toán góc cúi/ngửa (Pitch), nghiêng (Roll) và quay đầu (Yaw) dưới dạng tọa độ 3D.
- **Tính toán chỉ số PERCLOS**: Đánh giá phần trăm thời gian nhắm mắt tích lũy (5 giây gần nhất) để nhận diện trạng thái mệt mỏi khách quan.
- **Dự báo vi ngủ sớm bằng LSTM**: Sử dụng chuỗi trượt 60 giây để dự đoán sớm nguy cơ buồn ngủ trước khi xảy ra sự cố.
- **Cơ chế phản hồi cảnh báo khẩn cấp (Safety Overrides)**:
  - Báo động tức thì nếu nhắm mắt liên tục > 1.0 giây.
  - Báo động tức thì nếu lệch đầu, gục đầu quá góc quy định > 1.0 giây.
  - Cảnh báo mất dấu khuôn mặt (Face Lost) nếu tài xế lệch khỏi khung hình > 1.5 giây.
- **Tích hợp phần cứng (Raspberry Pi GPIO)**: Tự động phát hiện và kích hoạt mô-tơ rung (GPIO 17) và còi chíp (GPIO 27) tương thích theo từng cấp độ nguy hiểm.
- **Lịch sử hoạt động**: Tự động lưu trữ thông số trạng thái mỗi giây vào cơ sở dữ liệu SQLite (`dms_history.db`) phục vụ giám sát và phân tích hành trình.

---

## Cấu trúc thư mục dự án

```text
├── drowsiness_detector.py # Chương trình nhận diện và chạy Dashboard chính
├── lstm_model.py          # Kiến trúc mạng LSTM (PyTorch)
├── train_lstm.py          # Kịch bản huấn luyện mô hình LSTM
├── lstm_drowsiness.pth    # Trọng số mô hình đã được huấn luyện sẵn
├── run_dms.sh             # Script Bash tự động thiết lập môi trường và chạy ứng dụng
├── install_remote.sh      # Script cài đặt AnyDesk trên Ubuntu
├── requirements.txt       # Danh sách các thư viện Python cần thiết
└── README.md              # Hướng dẫn sử dụng
```

---

## Hướng dẫn cài đặt và sử dụng trên Ubuntu / Linux

### 1. Chuẩn bị phần cứng và Camera
- Đảm bảo camera USB Webcam hoặc CSI Camera đã được kết nối và hệ thống nhận diện thiết bị tại `/dev/video*`.
- Bạn có thể kiểm tra danh sách thiết bị video bằng lệnh:
  ```bash
  ls -l /dev/video*
  ```

### 2. Khởi chạy nhanh bằng Script
Dự án cung cấp sẵn tệp shell script tự động tạo môi trường ảo Python (`venv`), cài đặt các thư viện cần thiết và chạy ứng dụng:
```bash
# Cấp quyền thực thi cho script (nếu chưa có)
chmod +x run_dms.sh

# Chạy chương trình
./run_dms.sh
```

### 3. Cài đặt thủ công (Không dùng Script)
Nếu muốn tự cài đặt từng bước:
```bash
# 1. Cập nhật hệ thống và cài đặt môi trường ảo
sudo apt update
sudo apt install -y python3-pip python3-venv

# 2. Tạo và kích hoạt môi trường ảo
python3 -m venv venv
source venv/bin/activate

# 3. Cài đặt các thư viện phụ thuộc
pip install --upgrade pip
pip install -r requirements.txt

# 4. Chạy chương trình
python3 drowsiness_detector.py
```

*(Nếu muốn tự huấn luyện lại mô hình LSTM từ đầu, hãy chạy: `python3 train_lstm.py`)*

### 4. Các tham số cấu hình tùy chọn (Command-line Arguments)
Bạn có thể tùy chọn truyền thêm các tham số khi chạy thông qua script `./run_dms.sh` hoặc trực tiếp qua lệnh `python3 drowsiness_detector.py`:
- `--scale <giá_trị>`: Tỉ lệ kích thước cửa sổ hiển thị (mặc định là `1.0`). Ví dụ truyền `--scale 0.5` để thu nhỏ một nửa hoặc `--scale 0.7` để thu nhỏ còn 70%. Việc này giúp giao diện hiển thị gọn gàng hơn trên các màn hình nhỏ của Raspberry Pi và giúp hệ thống chạy nhẹ hơn.
- `--camera <chỉ_số>`: Chỉ định chỉ số camera sử dụng (ví dụ: `--camera 0`).
- `--enhance`: Bắt buộc kích hoạt chế độ tăng cường độ tương phản CLAHE.
- `--no-enhance`: Vô hiệu hóa chế độ tự động tăng cường độ tương phản.
- `--show-enhanced`: Hiển thị khung hình camera sau khi đã được tăng cường CLAHE lên dashboard.

### 5. Thiết lập khởi động cùng hệ thống (Autostart)
Để ứng dụng tự động kích hoạt sau khi khởi động desktop Ubuntu:
1. Tạo một file cấu hình autostart tại đường dẫn `~/.config/autostart/ai_dms.desktop`.
2. Ghi nội dung cấu hình trỏ đường dẫn thực thi tới file `run_dms.sh`.

---

## Hướng dẫn cấu hình và chạy trên Raspberry Pi

### 1. Sơ đồ kết nối GPIO (Cảnh báo vật lý)
- **Động cơ rung**: Kết nối cực điều khiển qua transistor tới chân **GPIO 17** (BCM 17 / Physical Pin 11).
- **Còi chíp (Buzzer)**: Kết nối cực điều khiển qua transistor tới chân **GPIO 27** (BCM 27 / Physical Pin 13).

*Lưu ý: Mặc định tính năng điều khiển GPIO sẽ tự động kích hoạt nếu thư viện `RPi.GPIO` được cài đặt thành công trên hệ thống.*

### 2. Cấu hình Camera trên Raspberry Pi OS / Ubuntu
Nếu bạn sử dụng **Raspberry Pi Camera Module 3** hoặc các dòng camera CSI:
1. Thêm cấu hình cảm biến vào `/boot/firmware/config.txt` (ví dụ: `dtoverlay=imx708`).
2. Khởi động lại thiết bị.
3. Chạy chương trình thông qua công cụ hỗ trợ tương thích `libcamerify`:
   ```bash
   libcamerify python3 drowsiness_detector.py
   ```

---

## Hướng dẫn kiểm thử (Testing & Calibration)

1. **Hiệu chuẩn (Calibration)**: Khi ứng dụng bắt đầu mở camera, tài xế cần ngồi thẳng lưng, nhìn thẳng vào camera trong khoảng **3 giây đầu tiên (100 frames)** để hệ thống thiết lập baseline sinh học chuẩn (EAR, MAR, Head Pose gốc).
2. **Hiệu chuẩn lại**: Nhấn phím **`r`** trên bàn phím bất cứ lúc nào nếu thay đổi tư thế ngồi hoặc vị trí camera.
3. **Thoát chương trình**: Nhấn phím **`q`** tại màn hình hiển thị Dashboard của camera để giải phóng camera và đóng ứng dụng an toàn.
