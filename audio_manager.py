#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI_DMS Audio Manager
Quản lý phát âm thanh loa (Jack 3.5mm / ALSA / PulseAudio), giọng nói thông báo Tiếng Việt
và ghi âm cabin khoang lái bằng Microphone trên USB Camera.
"""

import os
import time
import glob
import wave
import math
import struct
import subprocess
import threading
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR = os.path.join(BASE_DIR, "audio_logs")
PROMPT_DIR = os.path.join(BASE_DIR, "audio_prompts")
BEEP_FILE = os.path.join(PROMPT_DIR, "dms_beep.wav")

_pygame_initialized = False
_sound_cache = {}
_sound_lock = threading.Lock()

_voice_lock = threading.Lock()
_is_voice_playing = False

def is_voice_prompt_playing():
    """Kiểm tra xem hệ thống có đang phát giọng nói AI hay không"""
    global _is_voice_playing
    return _is_voice_playing

def unmute_and_max_speaker_volume(vol_pct="90%"):
    """
    Bật âm lượng chuẩn 85-90% cho Jack 3.5mm (Headphones ALSA / PulseAudio) 
    kết nối mạch khuếch đại TDA2050, tránh bị rè/vỡ tiếng do clipping.
    """
    try:
        # 1. Bật mixer ALSA cho Jack 3.5mm (bcm2835 Headphones) và các card ALSA khác
        for c in ["0", "Headphones", "default", "1", "2", "3"]:
            subprocess.run(["amixer", "-c", str(c), "sset", "Headphone", vol_pct, "unmute"], capture_output=True)
            subprocess.run(["amixer", "-c", str(c), "sset", "Master", vol_pct, "unmute"], capture_output=True)
            subprocess.run(["amixer", "-c", str(c), "sset", "Speaker", vol_pct, "unmute"], capture_output=True)
            subprocess.run(["amixer", "-c", str(c), "sset", "PCM", vol_pct, "unmute"], capture_output=True)
        subprocess.run(["amixer", "sset", "Master", vol_pct, "unmute"], capture_output=True)
        subprocess.run(["amixer", "sset", "Headphone", vol_pct, "unmute"], capture_output=True)
        
        # 2. Đặt Default Sink cho PulseAudio / PipeWire sang Jack 3.5mm (bcm2835) nếu có
        res = subprocess.run(["pactl", "list", "sinks", "short"], capture_output=True, text=True)
        for line in res.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                sink_id = parts[0]
                sink_name = parts[1]
                subprocess.run(["pactl", "set-sink-mute", sink_id, "0"], capture_output=True)
                subprocess.run(["pactl", "set-sink-volume", sink_id, vol_pct], capture_output=True)
                if "bcm2835" in sink_name or "Headphone" in sink_name or "analog" in sink_name:
                    subprocess.run(["pactl", "set-default-sink", sink_name], capture_output=True)
                    
        subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"], capture_output=True)
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", vol_pct], capture_output=True)
    except Exception:
        pass

# Tự động tăng âm lượng khi nạp module
unmute_and_max_speaker_volume()

def _create_synthetic_beeps_if_needed():
    """Tự động tạo các file âm thanh beep chuẩn PCM 16-bit 44.1kHz Stereo nếu chưa tồn tại"""
    os.makedirs(PROMPT_DIR, exist_ok=True)
    sample_rate = 44100

    def make_beep_file(filename, freq, duration, double=False, interval=0.08):
        path = os.path.join(PROMPT_DIR, filename)
        if os.path.exists(path):
            return
        b = bytearray()
        def add_tone(d, f):
            ns = int(sample_rate * d)
            for i in range(ns):
                s = int(14000 * math.sin(2 * math.pi * f * (i / sample_rate)))
                sb = struct.pack('<h', s)
                b.extend(sb)
                b.extend(sb)
        def add_sil(d):
            ns = int(sample_rate * d)
            for _ in range(ns):
                b.extend(b'\x00\x00\x00\x00')
        if double:
            add_tone(duration, freq)
            add_sil(interval)
            add_tone(duration, freq)
        else:
            add_tone(duration, freq)
        try:
            with wave.open(path, 'wb') as f:
                f.setnchannels(2)
                f.setsampwidth(2)
                f.setframerate(sample_rate)
                f.writeframes(b)
        except Exception:
            pass

    # 1. Beep cảnh báo chung (0.08s)
    make_beep_file("dms_beep.wav", 1200.0, 0.08)
    # 2. Beep đôi (0.09s + 0.08s + 0.09s)
    make_beep_file("beep_double.wav", 1000.0, 0.09, double=True, interval=0.08)
    # 3. Beep đơn (0.12s)
    make_beep_file("beep_single.wav", 950.0, 0.12)

_create_synthetic_beeps_if_needed()

def _play_wav_native(file_path):
    """Phát file WAV bằng PulseAudio (paplay) hoặc ALSA (aplay) trực tiếp với chất lượng cao nhất, không rè"""
    if not os.path.exists(file_path):
        return False
    if shutil.which("paplay"):
        ret = subprocess.run(["paplay", file_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if ret.returncode == 0:
            return True
    if shutil.which("aplay"):
        ret = subprocess.run(["aplay", "-q", "-D", "default", file_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if ret.returncode == 0:
            return True
    return False

ENABLE_SPEAKER_BEEP = False  # Đặt False để tắt tiếng còi bíp thô trên loa, chỉ dùng giọng nói tiếng Việt chuẩn

def play_pc_beep():
    """Phát âm thanh còi bíp cảnh báo mệt mỏi/buồn ngủ (chạy không nghẽn)"""
    if not ENABLE_SPEAKER_BEEP:
        return
    def _play():
        if _is_voice_playing:
            return
        _play_wav_native(BEEP_FILE)
    
    threading.Thread(target=_play, daemon=True).start()

def play_pc_beep_double():
    """Phát 2 âm còi bíp liên tiếp (Yêu cầu xác thực VNeID)"""
    if not ENABLE_SPEAKER_BEEP:
        return
    def _play():
        if _is_voice_playing:
            return
        p = os.path.join(PROMPT_DIR, "beep_double.wav")
        _play_wav_native(p)
            
    threading.Thread(target=_play, daemon=True).start()

def play_pc_beep_single():
    """Phát 1 âm còi bíp ngắt quãng (Yêu cầu xác thực khuôn mặt)"""
    if not ENABLE_SPEAKER_BEEP:
        return
    def _play():
        if _is_voice_playing:
            return
        p = os.path.join(PROMPT_DIR, "beep_single.wav")
        _play_wav_native(p)
            
    threading.Thread(target=_play, daemon=True).start()

def play_voice_prompt_sync(prompt_name):
    """
    Phát âm thanh giọng nói hướng dẫn tiếng Việt đồng bộ (xếp hàng chờ tuần tự, âm thanh trong trẻo, không rè)
    """
    global _is_voice_playing
    wav_path = os.path.join(PROMPT_DIR, f"{prompt_name}.wav")
    mp3_path = os.path.join(PROMPT_DIR, f"{prompt_name}.mp3")
    
    target_path = None
    if os.path.exists(wav_path):
        target_path = wav_path
    elif os.path.exists(mp3_path):
        target_path = mp3_path
        
    if not target_path:
        return
        
    with _voice_lock:
        _is_voice_playing = True
        try:
            # Ưu tiên phát trực tiếp file WAV PCM qua paplay / aplay
            if target_path.endswith(".wav"):
                if _play_wav_native(target_path):
                    return
            
            # Fallback nếu là mp3 hoặc không có paplay
            if shutil.which("gst-launch-1.0"):
                subprocess.run([
                    "gst-launch-1.0", "-q",
                    "filesrc", f"location={target_path}",
                    "!", "mpegaudioparse",
                    "!", "mpg123audiodec",
                    "!", "audioconvert",
                    "!", "audioresample",
                    "!", "autoaudiosink"
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        finally:
            _is_voice_playing = False

def play_voice_prompt_async(prompt_name):
    """
    Phát âm thanh giọng nói hướng dẫn tiếng Việt trong luồng ngầm không gây block camera
    (Tự động xếp hàng qua _voice_lock nếu có nhiều thông báo liên tiếp)
    """
    threading.Thread(target=play_voice_prompt_sync, args=(prompt_name,), daemon=True).start()

def ensure_audio_dir():
    if not os.path.exists(AUDIO_DIR):
        os.makedirs(AUDIO_DIR, exist_ok=True)
    try:
        os.chmod(AUDIO_DIR, 0o777)
    except Exception:
        pass

def unmute_and_boost_mic(card_num="3"):
    """Tự động bật công tắc Micro và tăng âm lượng lên 100% (+24dB) trong ALSA Mixer cho card microphone"""
    try:
        for c in [card_num, "3", "2", "1", "0"]:
            if c:
                subprocess.run(["amixer", "-c", str(c), "sset", "Mic", "100%", "cap", "unmute"], capture_output=True)
                subprocess.run(["amixer", "-c", str(c), "sset", "Capture", "100%", "cap", "unmute"], capture_output=True)
        subprocess.run(["amixer", "sset", "Capture", "100%", "cap", "unmute"], capture_output=True)
    except Exception:
        pass

def find_usb_audio_device():
    """
    Tự động tìm kiếm chỉ số ALSA Card của USB Camera / USB Microphone
    """
    card_num = None
    try:
        res = subprocess.run(["arecord", "-l"], capture_output=True, text=True)
        for line in res.stdout.split("\n"):
            if "card " in line and ("Camera" in line or "USB" in line):
                parts = line.split(":")
                card_num = parts[0].replace("card", "").strip()
                break
    except Exception:
        pass

    if not card_num:
        card_num = "3"

    unmute_and_boost_mic(card_num)
    return f"plughw:{card_num},0"

def cleanup_old_audio(max_size_mb=1000, max_days=3):
    """
    Tự động dọn dẹp các file ghi âm cũ để KHÔNG BAO GIỜ bị tràn bộ nhớ SD Card
    """
    ensure_audio_dir()
    try:
        now = time.time()
        files = glob.glob(os.path.join(AUDIO_DIR, "*.wav")) + glob.glob(os.path.join(AUDIO_DIR, "*.mp3"))
        
        # 1. Xóa theo số ngày
        for f in files:
            file_age_days = (now - os.path.getmtime(f)) / 86400.0
            if file_age_days > max_days:
                try:
                    os.remove(f)
                except Exception:
                    pass

        # 2. Xóa theo tổng dung lượng
        files = glob.glob(os.path.join(AUDIO_DIR, "*.wav")) + glob.glob(os.path.join(AUDIO_DIR, "*.mp3"))
        files.sort(key=lambda x: os.path.getmtime(x))
        
        total_bytes = sum(os.path.getsize(f) for f in files)
        max_bytes = max_size_mb * 1024 * 1024
        
        while total_bytes > max_bytes and files:
            oldest_file = files.pop(0)
            file_size = os.path.getsize(oldest_file)
            try:
                os.remove(oldest_file)
                total_bytes -= file_size
            except Exception:
                pass
    except Exception as e:
        print(f"[AUDIO CLEANUP ERROR] {e}")

def record_event_audio(event_name="session_end", duration_sec=10):
    """
    Ghi âm 1 đoạn âm thanh khoang lái (10 giây) khi kết thúc tiến trình / reset quét lại khuôn mặt / bấm test.
    """
    ensure_audio_dir()
    cleanup_old_audio()
    
    device_name = find_usb_audio_device()
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{event_name}_{duration_sec}s.wav"
    filepath = os.path.join(AUDIO_DIR, filename)
    
    cmd = [
        "arecord",
        "-D", device_name,
        "-f", "S16_LE",
        "-r", "44100",
        "-c", "1",
        "-d", str(duration_sec),
        filepath
    ]
    
    def run_record():
        try:
            print(f"[AUDIO] Dang ghi am khoang lai ({event_name} - {duration_sec}s)...")
            subprocess.run(cmd, capture_output=True, timeout=duration_sec + 5)
            if os.path.exists(filepath):
                os.chmod(filepath, 0o666)
                print(f"[AUDIO SUCCESS] Da luu file ghi am khoang lai: {filepath}")
        except Exception as e:
            print(f"[AUDIO ERROR] Ghi am loi: {e}")
            
    thread = threading.Thread(target=run_record, daemon=True)
    thread.start()
    return filename

if __name__ == "__main__":
    ensure_audio_dir()
    cleanup_old_audio()
    print("Testing Audio Manager...")
    play_pc_beep_double()
    time.sleep(1)
    play_pc_beep()
    time.sleep(1)
    play_voice_prompt_async("face_success")
    time.sleep(2)
    print("Audio test finished.")
