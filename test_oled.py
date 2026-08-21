#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI_DMS OLED Display Test Tool
Kiểm tra trực quan màn hình OLED I2C 128x64 (SSD1306 / SH1106).
"""

import sys
import time
from oled_manager import get_oled_manager

def main():
    print("==================================================")
    print("        AI_DMS - KIEM TRA MAN HINH OLED (I2C)     ")
    print("==================================================")
    
    oled = get_oled_manager()
    if not oled.is_available():
        print("[ERROR] Khong tim thay man hinh OLED I2C tai dia chi 0x3C!")
        print("Goi y kiem tra:")
        print("1. Kiem tra day noi I2C: VCC (3.3V/5V), GND, SDA (GPIO 2 - Pin 3), SCL (GPIO 3 - Pin 5)")
        print("2. Kiem tra xem I2C da duoc bat trong raspi-config chua.")
        sys.exit(1)

    print("[INFO] Da ket noi voi man hinh OLED I2C thanh cong!")
    oled.start()

    try:
        print("\n[1/5] Kiem tra man hinh cho: QUET THE VNEID / CCCD (3 giay)...")
        oled.update_data(auth_state="VNEID_REQ", alarm_level=0)
        time.sleep(3)

        print("[2/5] Kiem tra man hinh cho: XAC THUC KHUON MAT (3 giay)...")
        oled.update_data(auth_state="FACE_REQ", alarm_level=0)
        time.sleep(3)

        print("[3/5] Kiem tra man hinh che do: DANG LAI XE AN TOAN (3 giay)...")
        oled.update_data(auth_state="MONITORING", alarm_level=0, driver_name="Nguyen Van A", ear=0.32, mar=0.12, fps=30.0, fatigue_score=0)
        time.sleep(3)

        print("[4/5] Kiem tra man hinh: CANH BAO CAP 1 (Mệt nhẹ / Mắt nhắm) (3 giay)...")
        oled.update_data(auth_state="MONITORING", alarm_level=1, driver_name="Nguyen Van A", ear=0.18, mar=0.55, fps=28.5, fatigue_score=30)
        time.sleep(3)

        print("[5/5] Kiem tra man hinh: BAO DONG CAP 2 & 3 (NGUY HIEM / NGU GAT) (4 giay)...")
        oled.update_data(auth_state="MONITORING", alarm_level=2, driver_name="Nguyen Van A", ear=0.10, mar=0.20, fps=28.0, fatigue_score=85)
        time.sleep(2)
        oled.update_data(auth_state="MONITORING", alarm_level=3, driver_name="Nguyen Van A", ear=0.08, mar=0.15, fps=28.0, fatigue_score=95)
        time.sleep(2)

        print("\n[INFO] Hoan tat kiem tra OLED! Man hinh tro ve che do san sang.")
        oled.update_data(auth_state="INIT", alarm_level=0, driver_name="Ready", ear=0.0, mar=0.0, fps=0.0, fatigue_score=0)
        time.sleep(2)

    except KeyboardInterrupt:
        print("\n[INFO] Dung kiem tra boi nguoi dung.")
    finally:
        oled.stop()
        print("==================================================")
        print("       DA HOAN TAT KIEM TRA MAN HINH OLED        ")
        print("==================================================")

if __name__ == "__main__":
    main()
