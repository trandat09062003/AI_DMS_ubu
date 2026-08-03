import os
import time
import glob
import subprocess
import threading

AUDIO_DIR = "/home/kata/Documents/AI_DMS/audio_logs"
recorder_thread = None

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

def start_continuous_recording(chunk_duration_sec=60):
    """
    Kích hoạt luồng Ghi Âm Khoang Lái Liên Tục (Real-time Continuous Recording):
    - Tự động bật ngay khi thiết bị khởi động.
    - Ghi âm liên tục từng block 60 giây và tự cập nhật lên Dashboard.
    - Tự động xoay vòng bộ nhớ (Ring Buffer) xóa file cũ.
    """
    global recorder_thread
    if recorder_thread and recorder_thread.is_alive():
        return

    def record_loop():
        print(f"[AUDIO REALTIME] Đã bật ghi âm khoang lái liên tục (block {chunk_duration_sec}s)...")
        ensure_audio_dir()
        while True:
            try:
                cleanup_old_audio()
                device_name = find_usb_audio_device()
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                filename = f"{timestamp}_cabin_realtime.wav"
                filepath = os.path.join(AUDIO_DIR, filename)
                
                cmd = [
                    "arecord",
                    "-D", device_name,
                    "-f", "S16_LE",
                    "-r", "48000",
                    "-c", "1",
                    "-d", str(chunk_duration_sec),
                    filepath
                ]
                subprocess.run(cmd, capture_output=True)
                if os.path.exists(filepath):
                    os.chmod(filepath, 0o666)
            except Exception as e:
                print(f"[AUDIO REALTIME ERROR] {e}")
                time.sleep(2)

    recorder_thread = threading.Thread(target=record_loop, daemon=True)
    recorder_thread.start()

if __name__ == "__main__":
    ensure_audio_dir()
    start_continuous_recording()
