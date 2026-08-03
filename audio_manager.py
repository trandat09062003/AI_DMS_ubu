import os
import time
import glob
import subprocess
import threading

AUDIO_DIR = "/home/kata/Documents/AI_DMS/audio_logs"

def ensure_audio_dir():
    if not os.path.exists(AUDIO_DIR):
        os.makedirs(AUDIO_DIR, exist_ok=True)
    try:
        os.chmod(AUDIO_DIR, 0o777)
    except Exception:
        pass

def find_usb_audio_device():
    """
    Tự động tìm kiếm chỉ số ALSA Card của USB Camera
    """
    try:
        res = subprocess.run(["arecord", "-l"], capture_output=True, text=True)
        for line in res.stdout.split("\n"):
            if "card " in line and ("Camera" in line or "USB" in line):
                parts = line.split(":")
                card_num = parts[0].replace("card", "").strip()
                return f"hw:{card_num},0"
    except Exception:
        pass
    return "hw:2,0"

def cleanup_old_audio(max_size_mb=1000, max_days=3):
    """
    Tự động dọn dẹp các file ghi âm cũ để KHÔNG BAO GIỜ bị tràn bộ nhớ SD Card:
    1. Xóa các file cũ hơn max_days (mặc định 3 ngày).
    2. Nếu tổng dung lượng thư mục > max_size_mb (mặc định 1000MB ~ 1GB), xóa các file cũ nhất.
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
        "-r", "48000",
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
