#!/bin/bash
# Script to run the Driver Monitoring System (DMS)

# Navigate to the script's directory
cd "$(dirname "$0")"

# Thêm thời gian chờ để đảm bảo giao diện đồ họa (DISPLAY) và thiết bị camera đã sẵn sàng (Đặc biệt hữu ích khi chạy Autostart trên Pi)
echo "===================================================="
echo "[INFO] Dang kiem tra va cho he thong khoi dong hoan tat..."

# 1. Chờ DISPLAY hoặc WAYLAND_DISPLAY sẵn sàng (tránh lỗi cv2.imshow khi chạy autostart)
for i in {1..30}; do
    if [ -n "$DISPLAY" ] || [ -n "$WAYLAND_DISPLAY" ]; then
        echo "[INFO] Da phat hien giao dien do hoa (DISPLAY=$DISPLAY, WAYLAND_DISPLAY=$WAYLAND_DISPLAY)."
        break
    fi
    echo "[WAIT] Dang cho giao dien do hoa khoi dong... ($i/30)"
    sleep 1
done

# 2. Chờ thiết bị camera /dev/video0 sẵn sàng
for i in {1..15}; do
    if [ -e "/dev/video0" ]; then
        echo "[INFO] Da phat hien thiet bi camera /dev/video0."
        break
    fi
    echo "[WAIT] Dang cho thiet bi camera /dev/video0 san sang... ($i/15)"
    sleep 1
done

# Chờ thêm 2 giây để các driver được load hoàn tất hoàn toàn
sleep 2
echo "===================================================="

# Check if setup was already successfully completed
if [ ! -f ".setup_done" ]; then
    # Check if venv exists
    if [ ! -d "venv" ]; then
        echo "[INFO] Creating virtual environment..."
        python3 -m venv venv
        if [ $? -ne 0 ]; then
            echo "[ERROR] Failed to create virtual environment. Please check your Python installation."
            exit 1
        fi
    fi

    # Install/Update requirements
    echo "[INFO] Installing / updating dependencies..."
    venv/bin/pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to install dependencies."
        exit 1
    fi

    # Tự động cài đặt thư viện GPIO phù hợp
    if grep -q "Raspberry Pi" /proc/device-tree/model 2>/dev/null; then
        if grep -q "Raspberry Pi 5" /proc/device-tree/model 2>/dev/null; then
            echo "[INFO] Phat hien chay tren Raspberry Pi 5. Dang tu dong cai dat rpi-lgpio..."
            venv/bin/pip install rpi-lgpio
        else
            echo "[INFO] Phat hien chay tren Raspberry Pi. Dang tu dong cai dat RPi.GPIO..."
            venv/bin/pip install RPi.GPIO
        fi

        # Kiểm tra và cài đặt v4l-utils và libcamera-tools
        echo "[INFO] Dang kiem tra cac cong cu he thong tren Raspberry Pi..."
        if ! command -v v4l2-ctl >/dev/null 2>&1 || ! command -v libcamerify >/dev/null 2>&1; then
            echo "[INFO] Thieu cong cu v4l2-ctl hoac libcamerify. Dang thu tu dong cai dat v4l-utils va libcamera-tools..."
            sudo apt update && sudo apt install -y v4l-utils libcamera-tools
            if [ $? -ne 0 ]; then
                echo "[WARN] Khong the tu dong cai dat v4l-utils hoac libcamera-tools."
                echo "       Vui long tu cai dat bang tay: sudo apt update && sudo apt install -y v4l-utils libcamera-tools"
            fi
        else
            echo "[INFO] Cac cong cu v4l2-ctl va libcamerify da san sang."
        fi
    fi

    touch .setup_done
fi

# Hiển thị các thiết bị video đang kết nối để chẩn đoán
echo "===================================================="
echo "[DIAGNOSTIC] Cac thiet bi camera phat hien tren he thong:"
if command -v v4l2-ctl >/dev/null 2>&1; then
    v4l2-ctl --list-devices
else
    ls -l /dev/video* 2>/dev/null || echo "Khong tim thay thiet bi /dev/video nao."
fi
echo "===================================================="

# Kiểm tra camera là USB Webcam (uvcvideo) hay CSI Camera
IS_USB_CAM=false
if command -v v4l2-ctl >/dev/null 2>&1; then
    if v4l2-ctl -d /dev/video0 --info 2>/dev/null | grep -iq "uvcvideo\|USB"; then
        IS_USB_CAM=true
    fi
fi

# Kiểm tra và tự động tạo các file âm thanh giọng nói thông báo nếu chưa có
if [ ! -f "audio_prompts/req_vneid.mp3" ] || [ ! -f "audio_prompts/beep_double.wav" ]; then
    echo "[INFO] Dang tao cac file thong bao am thanh va coi bíp..."
    venv/bin/python3 -c "
import os, wave, struct, math
from gtts import gTTS
os.makedirs('audio_prompts', exist_ok=True)
prompts = {
    'req_vneid.mp3': 'Vui lòng quét thẻ VNeID hoặc căn cước công dân để xác thực người lái.',
    'vneid_success.mp3': 'Xác thực VNeID thành công.',
    'req_face.mp3': 'Vui lòng nhìn thẳng vào camera để xác thực khuôn mặt.',
    'face_success.mp3': 'Xác thực khuôn mặt thành công. Chúc bạn lái xe an toàn.',
    'alert_drowsy.mp3': 'Cảnh báo! Bạn đang có dấu hiệu buồn ngủ, vui lòng tập trung lái xe.',
    'alert_danger.mp3': 'Nguy hiểm! Phát hiện ngủ gật, hãy dừng xe ngay lập tức!',
    'alert_distracted.mp3': 'Cảnh báo! Vui lòng chú ý quan sát phía trước.',
    'alert_yawn.mp3': 'Bạn đang có dấu hiệu mệt mỏi, hãy giữ tỉnh táo.'
}
for fname, text in prompts.items():
    p = os.path.join('audio_prompts', fname)
    if not os.path.exists(p):
        try:
            gTTS(text=text, lang='vi').save(p)
        except Exception:
            pass

sample_rate = 44100
freq = 1000.0
def make_tone_pcm(d):
    ns = int(sample_rate*d)
    b = bytearray()
    for i in range(ns):
        b.extend(struct.pack('<h', int(16000*math.sin(2*math.pi*freq*(i/sample_rate)))))
    return b

def make_sil_pcm(d): return bytearray(int(sample_rate*d)*2)

if not os.path.exists('audio_prompts/beep_double.wav'):
    d = make_tone_pcm(0.10) + make_sil_pcm(0.10) + make_tone_pcm(0.10)
    with wave.open('audio_prompts/beep_double.wav', 'w') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(sample_rate); f.writeframes(d)

if not os.path.exists('audio_prompts/beep_single.wav'):
    d = make_tone_pcm(0.15)
    with wave.open('audio_prompts/beep_single.wav', 'w') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(sample_rate); f.writeframes(d)
"
fi

# Tự động mở âm lượng 100% cho Loa (Jack 3.5mm ALSA / PulseAudio)
echo "[INFO] Dang cau hinh am luong loa 100%..."
amixer -c 0 sset Headphone 100% unmute >/dev/null 2>&1 || true
amixer sset Master 100% unmute >/dev/null 2>&1 || true
pactl set-sink-mute 0 0 >/dev/null 2>&1 || true
pactl set-sink-volume 0 100% >/dev/null 2>&1 || true

# Run the program
echo "[INFO] Starting Driver Monitoring System..."
if [ "$IS_USB_CAM" = true ]; then
    echo "[INFO] Phat hien USB Webcam (UVC). Dang chay truc tiep voi Python..."
    venv/bin/python3 drowsiness_detector.py "$@"
elif command -v libcamerify >/dev/null 2>&1; then
    echo "[INFO] Phat hien Camera CSI & libcamerify. Dang chay ung dung qua libcamerify..."
    libcamerify venv/bin/python3 drowsiness_detector.py "$@"
else
    if grep -q "Raspberry Pi" /proc/device-tree/model 2>/dev/null; then
        echo "[WARN] Canh bao: Khong tim thay 'libcamerify'."
        echo "       Neu ban su dung Raspberry Pi Camera Module (CSI), vui long cai dat de ho tro camera:"
        echo "       sudo apt update && sudo apt install -y libcamera-tools"
        echo "       Sau do chay lai script nay."
        echo "----------------------------------------------------"
    fi
    venv/bin/python3 drowsiness_detector.py "$@"
fi
