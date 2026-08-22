#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI_DMS - Script kiểm tra hoạt động của Module RFID RC522 (SPI)
Cách dùng:
    venv/bin/python3 test_rfid.py
"""

import os
import sys
import time

def main():
    print("=" * 60)
    print("      AI_DMS - KIỂM TRA ĐẦU ĐỌC THẺ RFID RC522 (SPI)")
    print("=" * 60)
    print("Sơ đồ đấu nối chân chuẩn (Raspberry Pi 40-Pin Header):")
    print("  1. VCC / 3.3V  -> Pin 1  (Nguồn 3.3V - Tuyệt đối KHÔNG cắm 5V)")
    print("  2. RST (Reset) -> Pin 22 (GPIO 25)")
    print("  3. GND         -> Pin 6  (hoặc Pin 9, 14, 20, 25)")
    print("  4. MISO / SDO  -> Pin 21 (GPIO 9 / SPI0_MISO)")
    print("  5. MOSI / SDI  -> Pin 19 (GPIO 10 / SPI0_MOSI)")
    print("  6. SCK / SCLK  -> Pin 23 (GPIO 11 / SPI0_SCLK)")
    print("  7. SDA / NSS   -> Pin 24 (GPIO 8 / SPI0_CE0)")
    print("  8. IRQ         -> Bỏ trống (Không nối)")
    print("=" * 60)

    # 1. Kiểm tra driver SPI
    if not os.path.exists("/dev/spidev0.0"):
        print("\n[LỖI CẤU HÌNH] Chưa bật giao tiếp SPI trên Raspberry Pi!")
        print("-> Hãy chạy lệnh: sudo raspi-config")
        print("-> Chọn: Interface Options -> SPI -> Enable -> Finish, rồi thử lại.")
        return

    # 2. Khởi tạo thư viện SimpleMFRC522
    try:
        import RPi.GPIO as GPIO
        from mfrc522 import SimpleMFRC522, MFRC522
        print("\n[INFO] Đang khởi tạo module RC522 qua SPI...")
        if GPIO.getmode() is None:
            GPIO.setmode(GPIO.BCM)
        reader = SimpleMFRC522()
        if GPIO.getmode() == GPIO.BCM:
            reader.READER = MFRC522(pin_rst=25)
        print("[THÀNH CÔNG] Đã kết nối phần cứng RC522 qua SPI /dev/spidev0.0!")
    except Exception as e:
        print(f"\n[LỖI KẾT NỐI PHẦN CỨNG] Không thể kết nối RC522: {e}")
        print("-> Vui lòng kiểm tra lại dây cắm nguồn 3.3V, GND, RST và các chân SPI.")
        return

    print("\n" + "-" * 60)
    print(">>> SẴN SÀNG QUÉT THẺ! Vui lòng đưa thẻ / móc khóa RFID lại gần đầu đọc...")
    print(">>> Nhấn Ctrl + C để dừng kiểm tra.")
    print("-" * 60 + "\n")

    try:
        count = 0
        last_uid = None
        last_time = 0
        while True:
            card_id, text = reader.read_no_block()
            if card_id is not None:
                uid_str = str(card_id).strip()
                now = time.time()
                if uid_str != last_uid or (now - last_time) > 2.0:
                    count += 1
                    last_uid = uid_str
                    last_time = now
                    print(f"[{count}] [QUẸT THÀNH CÔNG] -> Mã UID Thẻ: {uid_str}")
                    if text and text.strip():
                        print(f"    Dữ liệu ghi trong thẻ: {text.strip()}")
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n[INFO] Đã dừng kiểm tra theo yêu cầu.")
    finally:
        try:
            import RPi.GPIO as GPIO
            GPIO.cleanup()
        except Exception:
            pass

if __name__ == "__main__":
    main()
