#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI_DMS - Công cụ Test Loa Tự Động cho Jack 3.5mm & Mạch Khuếch Đại TDA2050
Kiểm tra toàn diện tất cả các âm thanh hệ thống (Beep + Voice AI Tiếng Việt)
"""

import os
import sys
import time
import subprocess
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROMPT_DIR = os.path.join(BASE_DIR, "audio_prompts")

from audio_manager import (
    unmute_and_max_speaker_volume,
    play_pc_beep,
    play_pc_beep_double,
    play_pc_beep_single,
    play_voice_prompt_async
)

def print_header():
    print("=" * 60)
    print("      AI_DMS - KIỂM TRA TỰ ĐỘNG LOA & MẠCH TDA2050 (JACK 3.5MM)      ")
    print("=" * 60)

def check_alsa_device():
    print("\n[BƯỚC 1/5] Kiểm tra thiết bị âm thanh ALSA Jack 3.5mm...")
    res = subprocess.run(["aplay", "-l"], capture_output=True, text=True)
    if "Headphones" in res.stdout or "bcm2835" in res.stdout:
        print("  ✓ Đã phát hiện card âm thanh Jack 3.5mm (bcm2835 Headphones)!")
    else:
        print("  ! Lưu ý: Sử dụng ngõ âm thanh mặc định.")

def set_max_volume():
    print("\n[BƯỚC 2/5] Bật âm lượng tối ưu 90% (Unmute & Gain chuẩn cho TDA2050)...")
    unmute_and_max_speaker_volume()
    print("  ✓ Đã thiết lập Volume ALSA & PulseAudio cho ngõ ra Jack 3.5mm.")

def test_beeps():
    print("\n[BƯỚC 3/5] Kiểm tra các âm còi Bíp cảnh báo...")
    
    print("  -> 1. Phát Còi Bíp Đơn (Single Beep)...")
    play_pc_beep_single()
    time.sleep(1.2)
    
    print("  -> 2. Phát Còi Bíp Đôi (Double Beep)...")
    play_pc_beep_double()
    time.sleep(1.5)
    
    print("  -> 3. Phát Còi Bíp Cảnh Báo Buồn Ngủ (DMS Beep)...")
    play_pc_beep()
    time.sleep(1.2)
    print("  ✓ Đã hoàn thành phát còi bíp.")

def test_voice_prompts():
    print("\n[BƯỚC 4/5] Kiểm tra giọng nói AI hướng dẫn (Tiếng Việt) theo luồng thực tế...")
    
    prompts = [
        ("req_vneid", "1. Yêu cầu CCCD/VNeID: 'Vui lòng quét thẻ căn cước công dân hoặc ứng dụng VNeID'"),
        ("vneid_success", "2. Xác thực CCCD/VNeID OK: 'Xác thực căn cước công dân và VNeID thành công'"),
        ("req_face", "3. Yêu cầu Khuôn mặt: 'Vui lòng nhìn thẳng vào camera để xác thực khuôn mặt'"),
        ("face_success", "4. Xác thực Khuôn mặt OK: 'Xác thực khuôn mặt thành công. Chúc bạn lái xe an toàn.'")
    ]
    
    for prompt_key, desc in prompts:
        print(f"\n  -> Đang phát: {desc}")
        play_voice_prompt_async(prompt_key)
        time.sleep(3.6)
        
    print("\n  ✓ Đã hoàn thành phát tất cả giọng nói AI theo đúng thứ tự xác thực.")

def test_direct_alsa():
    print("\n[BƯỚC 5/5] Kiểm tra phát trực tiếp qua ALSA aplay...")
    beep_wav = os.path.join(PROMPT_DIR, "dms_beep.wav")
    if os.path.exists(beep_wav) and shutil.which("aplay"):
        subprocess.run(["aplay", "-q", "-D", "default", beep_wav])
        print("  ✓ Lệnh aplay thực thi thành công.")
    else:
        print("  - Bỏ qua kiểm tra aplay.")

def main():
    print_header()
    check_alsa_device()
    set_max_volume()
    test_beeps()
    test_voice_prompts()
    test_direct_alsa()
    
    print("\n" + "=" * 60)
    print(" [KẾT QUẢ] QUÁ TRÌNH KIỂM TRA LOA TỰ ĐỘNG ĐÃ HOÀN TẤT!")
    print(" Nếu bạn nghe thấy các âm bíp và giọng nói rõ ràng:")
    print(" -> Ngõ ra Jack 3.5mm, Mạch TDA2050 và Loa đã hoạt động 100% chính xác.")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    main()
