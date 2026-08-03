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
