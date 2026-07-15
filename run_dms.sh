#!/bin/bash
# Script to run the Driver Monitoring System (DMS)

# Navigate to the script's directory
cd "$(dirname "$0")"

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

# Run the program
echo "[INFO] Starting Driver Monitoring System..."
if command -v libcamerify >/dev/null 2>&1; then
    echo "[INFO] Phat hien libcamerify. Dang chay ung dung qua libcamerify..."
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
