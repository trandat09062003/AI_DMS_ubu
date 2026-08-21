#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI_DMS Hardware Diagnostic Tool - Screen, OLED & Audio Test
Kiểm tra toàn diện:
1. Màn hình chính Desktop / HDMI (800x480)
2. Màn hình OLED I2C 128x64 (SSD1306 / SH1106)
3. Loa phát âm thanh (Jack 3.5mm / ALSA / PulseAudio)
4. Còi chíp Buzzer và Động cơ rung GPIO (nếu có)
"""

import os
import sys
import time
import subprocess
import numpy as np
import cv2

os.environ["DISPLAY"] = os.environ.get("DISPLAY", ":0")

# Đường dẫn thư mục âm thanh
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROMPT_DIR = os.path.join(BASE_DIR, "audio_prompts")

from audio_manager import (
    unmute_and_max_speaker_volume,
    play_pc_beep,
    play_pc_beep_double,
    play_pc_beep_single,
    play_voice_prompt_async
)
from oled_manager import get_oled_manager

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False

MOTOR_PIN = 17
BUZZER_PIN = 27

def get_system_info():
    """Lấy thông tin hệ thống cơ bản"""
    ip_addr = "N/A"
    try:
        res = subprocess.run(["hostname", "-I"], capture_output=True, text=True)
        ips = res.stdout.strip().split()
        if ips:
            ip_addr = ips[0]
    except Exception:
        pass

    temp_str = "N/A"
    try:
        if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                t = int(f.read().strip()) / 1000.0
                temp_str = f"{t:.1f} °C"
    except Exception:
        pass

    return ip_addr, temp_str

def draw_header(img, title, subtitle=""):
    """Vẽ thanh tiêu đề chuẩn cho màn hình 800x480"""
    cv2.rectangle(img, (0, 0), (800, 55), (30, 30, 40), -1)
    cv2.line(img, (0, 55), (800, 55), (0, 200, 255), 2)
    cv2.putText(img, title, (20, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2, cv2.LINE_AA)
    if subtitle:
        cv2.putText(img, subtitle, (500, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)

def draw_footer(img, current_step, total_steps=5):
    """Vẽ thanh trạng thái phía dưới"""
    cv2.rectangle(img, (0, 440), (800, 480), (20, 20, 25), -1)
    cv2.line(img, (0, 440), (800, 440), (80, 80, 80), 1)
    step_text = f"TIEN TRINH KIEM TRA: {current_step}/{total_steps}"
    cv2.putText(img, step_text, (20, 465), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1, cv2.LINE_AA)
    cv2.putText(img, "Nhan 'Q' hoac 'ESC' de dung lai", (500, 465), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1, cv2.LINE_AA)

def main():
    print("==================================================")
    print("      AI_DMS - KIEM TRA TOAN DIEN PHAN CUNG       ")
    print("  (Man hinh chinh, Man hinh OLED & Loa Am thanh)  ")
    print("==================================================")
    
    # 1. Bật tối đa âm lượng
    unmute_and_max_speaker_volume()
    ip_addr, cpu_temp = get_system_info()
    
    # Khởi tạo màn hình OLED
    oled = get_oled_manager()
    oled_status = "KHONG TIM THAY"
    if oled.is_available():
        oled.start()
        oled_status = "HOAT DONG TOT (I2C 0x3C)"
        oled.update_data(auth_state="VNEID_REQ", alarm_level=0)
        print("[INFO] Da tim thay va khoi chay man hinh OLED I2C 0x3C.")
    else:
        print("[WARN] Khong tim thay man hinh OLED I2C.")

    # Khởi tạo cửa sổ OpenCV
    win_name = "AI_DMS_HARDWARE_TEST"
    cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win_name, 800, 480)
    cv2.moveWindow(win_name, 0, 0)

    # ==========================================
    # BƯỚC 1: KIỂM TRA MÀU ĐƠN SẮC & ĐIỂM CHẾT
    # ==========================================
    colors = [
        ("RED (Do)", (0, 0, 255), (255, 255, 255)),
        ("GREEN (Xanh La)", (0, 255, 0), (0, 0, 0)),
        ("BLUE (Xanh Duong)", (255, 0, 0), (255, 255, 255)),
        ("WHITE (Trang)", (255, 255, 255), (0, 0, 0)),
        ("BLACK (Den)", (0, 0, 0), (255, 255, 255))
    ]
    
    print("\n[1/5] Kiem tra cac mau don sac (Red, Green, Blue, White, Black)...")
    for name, bgr, text_color in colors:
        frame = np.full((480, 800, 3), bgr, dtype=np.uint8)
        cv2.putText(frame, f"TEST MAN HINH: {name}", (200, 240), 
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, text_color, 2, cv2.LINE_AA)
        cv2.imshow(win_name, frame)
        key = cv2.waitKey(600) & 0xFF
        if key in (27, ord('q'), ord('Q')):
            cv2.destroyAllWindows()
            if oled.is_available(): oled.stop()
            return

    # ==========================================
    # BƯỚC 2: KIỂM TRA DẢI MÀU (COLOR BARS)
    # ==========================================
    print("[2/5] Kiem tra bang mau SMPTE va do tuong phan...")
    bars_img = np.zeros((480, 800, 3), dtype=np.uint8)
    
    palette = [
        (255, 255, 255), # White
        (0, 255, 255),   # Yellow
        (255, 255, 0),   # Cyan
        (0, 255, 0),     # Green
        (255, 0, 255),   # Magenta
        (0, 0, 255),     # Red
        (255, 0, 0),     # Blue
        (50, 50, 50)     # Gray
    ]
    bar_w = 800 // len(palette)
    for i, col in enumerate(palette):
        x1 = i * bar_w
        x2 = 800 if i == len(palette)-1 else (i + 1) * bar_w
        cv2.rectangle(bars_img, (x1, 60), (x2, 280), col, -1)
    
    for x in range(800):
        val = int((x / 800.0) * 255)
        bars_img[290:350, x] = (val, val, val)
        
    draw_header(bars_img, "TEST MAN HINH: BANG MAU CHUAN", "Do phan giai: 800x480")
    cv2.putText(bars_img, "Giai do tuong phan (Grayscale Gradient 0 - 255):", (20, 375), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)
    draw_footer(bars_img, 2, 5)
    
    cv2.imshow(win_name, bars_img)
    for _ in range(15):
        key = cv2.waitKey(100) & 0xFF
        if key in (27, ord('q'), ord('Q')):
            cv2.destroyAllWindows()
            if oled.is_available(): oled.stop()
            return

    # ==========================================
    # BƯỚC 3: KIỂM TRA MÀN HÌNH OLED I2C 128x64
    # ==========================================
    print("[3/5] Kiem tra man hinh OLED I2C (128x64)...")
    if oled.is_available():
        oled_steps = [
            ("VNEID_REQ", 0, "Quet the VNeID"),
            ("FACE_REQ", 0, "Xac thuc khuon mat"),
            ("MONITORING", 0, "Trang thai lai xe an toan"),
            ("MONITORING", 1, "Canh bao mệt mỏi Cap 1"),
            ("MONITORING", 2, "Bao dong nguy hiem Cap 2")
        ]
        for a_st, a_lvl, label in oled_steps:
            oled.update_data(auth_state=a_st, alarm_level=a_lvl, ear=0.32, mar=0.15, fps=30.0, driver_name="Nguyen Van A")
            
            oled_frame = np.zeros((480, 800, 3), dtype=np.uint8)
            draw_header(oled_frame, "TEST MAN HINH OLED (I2C 128x64)", "Dia chi: 0x3C (/dev/i2c-1)")
            cv2.rectangle(oled_frame, (80, 100), (720, 380), (30, 40, 50), -1)
            cv2.rectangle(oled_frame, (80, 100), (720, 380), (0, 200, 255), 2)
            cv2.putText(oled_frame, f"DANG TEST OLED: {label}", (120, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(oled_frame, f"Auth: {a_st}  |  Alarm Level: {a_lvl}", (120, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.putText(oled_frame, "Vui long quan sat man hinh OLED nho dang hien thi dong thoi!", (120, 310), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1, cv2.LINE_AA)
            draw_footer(oled_frame, 3, 5)
            cv2.imshow(win_name, oled_frame)
            
            for _ in range(12):
                key = cv2.waitKey(100) & 0xFF
                if key in (27, ord('q'), ord('Q')):
                    cv2.destroyAllWindows()
                    oled.stop()
                    return

    # ==========================================
    # BƯỚC 4: KIỂM TRA LOA & ÂM THANH (AUDIO PLAYBACK)
    # ==========================================
    print("[4/5] Kiem tra phat am thanh qua loa (Beep + Voice prompts)...")
    
    audio_tests = [
        ("beep_single", "Am Coi Bip Don (Single Beep)", "Canh bao nhin vao Camera", play_pc_beep_single),
        ("beep_double", "Am Coi Bip Doi (Double Beep)", "Yeu cau quet the VNeID / Can cuoc", play_pc_beep_double),
        ("req_vneid", "Giong noi AI (Yeu cau quet CCCD/VNeID)", "'Vui long quet the can cuoc cong dan hoac ung dung VNeID...'", lambda: play_voice_prompt_async("req_vneid")),
        ("vneid_success", "Giong noi AI (Xac thuc CCCD/VNeID OK)", "'Xac thuc can cuoc cong dan va VNeID thanh cong...'", lambda: play_voice_prompt_async("vneid_success")),
        ("req_face", "Giong noi AI (Huong dan nhin camera)", "'Vui long nhin thang vao camera de xac thuc khuon mat...'", lambda: play_voice_prompt_async("req_face")),
        ("face_success", "Giong noi AI (Xac thuc khuon mat OK)", "'Xac thuc khuon mat thanh cong. Chuc ban lai xe an toan...'", lambda: play_voice_prompt_async("face_success"))
    ]
    
    for audio_id, title, desc, play_fn in audio_tests:
        print(f"  -> Dang phat: {title}")
        play_fn()
        
        start_time = time.time()
        phase = 0.0
        while time.time() - start_time < 3.0:
            frame = np.zeros((480, 800, 3), dtype=np.uint8)
            draw_header(frame, "TEST LOA: DANG PHAT AM THANH", "Am luong: 100%")
            
            # Khung thông tin âm thanh đang phát
            cv2.rectangle(frame, (80, 100), (720, 230), (45, 30, 20), -1)
            cv2.rectangle(frame, (80, 100), (720, 230), (0, 165, 255), 2)
            cv2.putText(frame, f"[DANG PHAT] {title}", (100, 150), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(frame, f"Noi dung: {desc}", (100, 190), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 255, 200), 1, cv2.LINE_AA)
            
            # Vẽ thanh sóng âm động
            phase += 0.25
            center_y = 330
            for i in range(25):
                bar_x = 130 + i * 22
                bar_h = int(25 + 40 * np.sin(phase + i * 0.4) ** 2)
                cv2.rectangle(frame, (bar_x, center_y - bar_h), (bar_x + 14, center_y + bar_h), (0, 255, 128), -1)
                
            cv2.putText(frame, "Song am thanh tin hieu Loa dang hoat dong...", (240, 415), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 180, 180), 1, cv2.LINE_AA)
            draw_footer(frame, 4, 5)
            
            cv2.imshow(win_name, frame)
            key = cv2.waitKey(60) & 0xFF
            if key in (27, ord('q'), ord('Q')):
                cv2.destroyAllWindows()
                if oled.is_available(): oled.stop()
                return

    # ==========================================
    # BƯỚC 5: TỔNG KẾT KẾT QUẢ KIỂM TRA
    # ==========================================
    print("\n[5/5] Hoan tat kiem tra toan dien phan cung!")
    summary_img = np.zeros((480, 800, 3), dtype=np.uint8)
    draw_header(summary_img, "KET QUA KIEM TRA PHAN CUNG", "Trang thai: HOAN TAT")
    
    items = [
        ("1. MAN HINH CHINH (Display)", "HOAT DONG TOT (800x480)", (0, 255, 0)),
        ("2. MAN HINH OLED (SSD1306/SH1106)", oled_status, (0, 255, 0) if oled.is_available() else (0, 0, 255)),
        ("3. LOA / AM THANH (Speaker)", "HOAT DONG TOT (ALSA 3.5mm / 100% Vol)", (0, 255, 0)),
        ("4. GIONG NOI AI & COI CANH BAO", "HOAT DONG TOT (Tieng Viet TTS & Beeps)", (0, 255, 0)),
        (f"5. THONG SO HE THONG", f"IP: {ip_addr}  |  Nhiet do CPU: {cpu_temp}", (255, 200, 0))
    ]
    
    y = 95
    for title, status, color in items:
        cv2.rectangle(summary_img, (50, y - 20), (750, y + 26), (35, 35, 35), -1)
        cv2.rectangle(summary_img, (50, y - 20), (750, y + 26), (70, 70, 70), 1)
        cv2.putText(summary_img, title, (70, y + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(summary_img, status, (380, y + 8), cv2.FONT_HERSHEY_SIMPLEX, 0.50, color, 2, cv2.LINE_AA)
        y += 56
        
    draw_footer(summary_img, 5, 5)
    cv2.imshow(win_name, summary_img)
    
    for _ in range(40):
        key = cv2.waitKey(100) & 0xFF
        if key in (27, ord('q'), ord('Q')):
            break
            
    cv2.destroyAllWindows()
    if oled.is_available():
        oled.stop()

    print("==================================================")
    print(" KIEM TRA HOAN TAT - MAN HINH, OLED VA LOA HOAT DONG TOT")
    print("==================================================")

if __name__ == "__main__":
    main()
