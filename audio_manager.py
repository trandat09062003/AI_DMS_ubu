import os
import time
import glob
import subprocess
import threading

AUDIO_DIR = "/home/kata/Documents/AI_DMS/audio_logs"

def ensure_audio_dir():
    if not os.path.exists(AUDIO_DIR):
        os.makedirs(AUDIO_DIR, exist_ok=True)

def cleanup_old_audio(max_size_mb=1000, max_days=3):
    """
    Tự động dọn dẹp các file ghi âm cũ để KHÔNG BAO GIỜ bị tràn bộ nhớ SD Card:
    1. Xóa các file cũ hơn max_days (mặc định 3 ngày).
    2. Nếu tổng dung lượng thư mục > max_size_mb (mặc định 1000MB ~ 1GB), xóa các file cũ nhất cho đến khi < max_size_mb.
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
                    print(f"[AUDIO CLEANUP] Da xoa file qua cu (> {max_days} ngay): {f}")
                except Exception:
                    pass

        # 2. Xóa theo tổng dung lượng
        files = glob.glob(os.path.join(AUDIO_DIR, "*.wav")) + glob.glob(os.path.join(AUDIO_DIR, "*.mp3"))
        files.sort(key=lambda x: os.path.getmtime(x)) # Cũ nhất xếp trước
        
        total_bytes = sum(os.path.getsize(f) for f in files)
        max_bytes = max_size_mb * 1024 * 1024
        
        while total_bytes > max_bytes and files:
            oldest_file = files.pop(0)
            file_size = os.path.getsize(oldest_file)
            try:
                os.remove(oldest_file)
                total_bytes -= file_size
                print(f"[AUDIO CLEANUP] Xoa file cu de giam dung luong bộ nho (<{max_size_mb}MB): {oldest_file}")
            except Exception:
                pass
    except Exception as e:
        print(f"[AUDIO CLEANUP ERROR] {e}")

def record_event_audio(event_name="drowsiness", duration_sec=10):
    """
    Ghi âm một đoạn sự cố (ví dụ 10s) khi phát hiện cảnh báo và tự động nén.
    """
    ensure_audio_dir()
    # Chạy dọn dẹp bộ nhớ trước
    cleanup_old_audio()
    
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}_{event_name}_{duration_sec}s.wav"
    filepath = os.path.join(AUDIO_DIR, filename)
    
    cmd = [
        "arecord",
        "-D", "plughw:2,0",
        "-f", "S16_LE",
        "-r", "48000",
        "-c", "1",
        "-d", str(duration_sec),
        filepath
    ]
    
    def run_record():
        try:
            subprocess.run(cmd, capture_output=True, timeout=duration_sec + 5)
            print(f"[AUDIO] Da ghi am xong su co khoang lai: {filepath}")
        except Exception as e:
            print(f"[AUDIO ERROR] Ghi am loi: {e}")
            
    thread = threading.Thread(target=run_record, daemon=True)
    thread.start()
    return filename

if __name__ == "__main__":
    ensure_audio_dir()
    cleanup_old_audio()
