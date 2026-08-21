import cv2
import mediapipe as mp
import numpy as np
import torch
# Tối ưu hóa số luồng PyTorch trên Raspberry Pi 4 để giảm tải CPU và tránh overhead quản lý luồng
torch.set_num_threads(1)
torch.set_num_interop_threads(1)
import time
import os
import threading
import multiprocessing as mp
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False

# Cấu hình cổng vật lý trên Raspberry Pi (nếu chạy trên Ubuntu Pi)
MOTOR_PIN = 17  # Chân GPIO 17 điều khiển động cơ rung
BUZZER_PIN = 27 # Chân GPIO 27 điều khiển còi chíp vật lý
BUTTON_PIN = 22 # Chân GPIO 22 (Chân vật lý 15) nối nút bấm quét lại (Push Button)

import sqlite3
from collections import deque
from lstm_model import DrowsinessLSTM
from audio_manager import (
    play_voice_prompt_async,
    play_pc_beep_double,
    play_pc_beep_single,
    play_pc_beep,
    AUDIO_DIR,
    unmute_and_max_speaker_volume,
    is_voice_prompt_playing
)
from oled_manager import get_oled_manager

driver_info = {
    "name": "[Chưa xác định - Xem ảnh đính kèm]",
    "vneid": "[Chưa đọc được - Xem ảnh đính kèm]",
    "license_class": "B2"
}
fatigue_scores_history = []
auth_state = "VNEID_REQ"
vneid_authenticated = False
vneid_prompt_played = False
face_prompt_played = False
vneid_auth_trigger = False
is_processing_vneid = False
alarm_level = 0

# OCR/QR có thể treo với ảnh lỗi hoặc thẻ bị che. Tách nó sang process riêng để
# có thể dừng cứng, thay vì chỉ dùng thread (thread không thể bị hủy an toàn).
VNEID_PROCESSING_TIMEOUT_SECONDS = 3.0



# --- Cấu hình các chỉ số mốc khuôn mặt (Landmarks) ---
# Theo thuyết minh sáng kiến:
# Mắt trái: [33, 160, 158, 133, 153, 144] (tương ứng P1, P2, P3, P4, P5, P6)
# Mắt phải: [362, 385, 387, 263, 373, 380] (tương ứng P1, P2, P3, P4, P5, P6)
LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]

# Môi trong dùng để tính MAR:
# A: 81 -> 178
# B: 82 -> 87
# C: 311 -> 317
# D: 78 -> 308 (Chiều ngang miệng)
LIP_A = (81, 178)
LIP_B = (82, 87)
LIP_C = (311, 317)
LIP_D = (78, 308)

# Các điểm mốc dùng cho Head Pose Estimation (SolvePnP)
# 1. Nose tip (Đầu mũi): 1
# 2. Chin (Cằm): 152
# 3. Left eye outer corner (Góc mắt trái ngoài): 33
# 4. Right eye outer corner (Góc mắt phải ngoài): 263
# 5. Left mouth corner (Khóe miệng trái): 61
# 6. Right mouth corner (Khóe miệng phải): 291
POSE_LANDMARKS = [1, 152, 33, 263, 61, 291]

# Mô hình 3D khuôn mặt chuẩn (đơn vị mm)
MODEL_POINTS = np.array([
    (0.0, 0.0, 0.0),             # Nose tip
    (0.0, -330.0, -65.0),        # Chin
    (-225.0, 170.0, -135.0),     # Left eye outer corner
    (225.0, 170.0, -135.0),      # Right eye outer corner
    (-150.0, -150.0, -125.0),    # Left mouth corner
    (150.0, -150.0, -125.0)      # Right mouth corner
], dtype=np.float32)

# --- Quản lý Âm thanh Cảnh báo bằng Luồng riêng (Threading) ---
# Tránh bị đóng băng khung hình camera khi gọi còi bíp đồng bộ
alarm_level = 0  # 0: bình thường, 1: mệt nhẹ (beep chậm), 2: mệt vừa, 3: nguy hiểm, 4: VNeID req (2 beeps, 3s pause), 5: Face req (1 beep, 3s pause)

def sleep_and_check(duration):
    """Ngủ trong thời gian ngắn và phản ứng lập tức nếu alarm_level thay đổi"""
    global alarm_level
    initial_level = alarm_level
    steps = int(duration / 0.05)
    for _ in range(steps):
        if alarm_level != initial_level:
            if GPIO_AVAILABLE:
                try:
                    GPIO.output(MOTOR_PIN, GPIO.LOW)
                    GPIO.output(BUZZER_PIN, GPIO.LOW)
                except:
                    pass
            return True
        time.sleep(0.05)
    rem = duration % 0.05
    if rem > 0:
        if alarm_level != initial_level:
            if GPIO_AVAILABLE:
                try:
                    GPIO.output(MOTOR_PIN, GPIO.LOW)
                    GPIO.output(BUZZER_PIN, GPIO.LOW)
                except:
                    pass
            return True
        time.sleep(rem)
    return alarm_level != initial_level

def alarm_worker():
    global alarm_level
    while True:
        # Nếu đang phát giọng nói AI hướng dẫn, tạm hoãn còi chíp phần cứng để không bị đè âm thanh
        if is_voice_prompt_playing():
            time.sleep(0.1)
            continue

        # Cập nhật trạng thái phần cứng GPIO (Buzzer/Motor)
        if alarm_level == 0:
            if GPIO_AVAILABLE:
                try:
                    GPIO.output(MOTOR_PIN, GPIO.LOW)
                    GPIO.output(BUZZER_PIN, GPIO.LOW)
                except Exception:
                    pass
            time.sleep(0.05)
        elif alarm_level == 1:
            if GPIO_AVAILABLE:
                try:
                    GPIO.output(MOTOR_PIN, GPIO.LOW)
                    GPIO.output(BUZZER_PIN, GPIO.HIGH)
                except Exception:
                    pass
            if sleep_and_check(0.1): continue
            if GPIO_AVAILABLE:
                try:
                    GPIO.output(BUZZER_PIN, GPIO.LOW)
                except Exception:
                    pass
            if sleep_and_check(0.9): continue
        elif alarm_level == 2:
            if GPIO_AVAILABLE:
                try:
                    GPIO.output(MOTOR_PIN, GPIO.HIGH)
                    GPIO.output(BUZZER_PIN, GPIO.HIGH)
                except Exception:
                    pass
            if sleep_and_check(0.2): continue
            if GPIO_AVAILABLE:
                try:
                    GPIO.output(BUZZER_PIN, GPIO.LOW)
                except Exception:
                    pass
            if sleep_and_check(0.3): continue
        elif alarm_level == 3:
            if GPIO_AVAILABLE:
                try:
                    GPIO.output(MOTOR_PIN, GPIO.HIGH)
                    GPIO.output(BUZZER_PIN, GPIO.HIGH)
                except Exception:
                    pass
            if sleep_and_check(0.1): continue
            if GPIO_AVAILABLE:
                try:
                    GPIO.output(BUZZER_PIN, GPIO.LOW)
                except Exception:
                    pass
            if sleep_and_check(0.1): continue
        elif alarm_level == 4:
            # Yêu cầu VNeID
            if GPIO_AVAILABLE:
                try:
                    GPIO.output(MOTOR_PIN, GPIO.LOW)
                    GPIO.output(BUZZER_PIN, GPIO.HIGH)
                except Exception:
                    pass
            time.sleep(0.1)
            if GPIO_AVAILABLE:
                try:
                    GPIO.output(BUZZER_PIN, GPIO.LOW)
                except Exception:
                    pass
            time.sleep(0.1)
            if GPIO_AVAILABLE:
                try:
                    GPIO.output(BUZZER_PIN, GPIO.HIGH)
                except Exception:
                    pass
            time.sleep(0.1)
            if GPIO_AVAILABLE:
                try:
                    GPIO.output(BUZZER_PIN, GPIO.LOW)
                except Exception:
                    pass
            if sleep_and_check(3.0): continue
        elif alarm_level == 5:
            # Yêu cầu Xác thực khuôn mặt
            if GPIO_AVAILABLE:
                try:
                    GPIO.output(MOTOR_PIN, GPIO.LOW)
                    GPIO.output(BUZZER_PIN, GPIO.HIGH)
                except Exception:
                    pass
            time.sleep(0.15)
            if GPIO_AVAILABLE:
                try:
                    GPIO.output(BUZZER_PIN, GPIO.LOW)
                except Exception:
                    pass
            if sleep_and_check(3.0): continue


def quick_scan_qr(frame):
    """Quét cực nhanh mã QR trên frame gốc trong luồng chính (chỉ mất ~2ms)"""
    if frame is None:
        return None
    try:
        import pyzbar.pyzbar as pyzbar
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        decoded = pyzbar.decode(gray)
        for obj in decoded:
            qr_text = obj.data.decode('utf-8', errors='ignore').strip()
            if qr_text:
                parts = qr_text.split('|')
                vneid_num = parts[0].strip() if len(parts) >= 1 else qr_text
                name_str = parts[2].strip() if len(parts) >= 3 else "Nguyễn Văn A"
                return {"success": True, "vneid": vneid_num, "name": name_str, "method": "QUICK_QR"}
    except Exception:
        pass
    return None

def extract_info_from_ocr_text(text):
    """Bóc tách thông tin Số CCCD/VNeID, Họ tên và Hạng bằng lái từ chuỗi text OCR"""
    info = {}
    if not text:
        return info
        
    import re
    # 1. Tìm số CCCD 12 chữ số
    cccd_match = re.search(r'(?:Số|No\.?|ID[:\s]*|^|\s)([0-9]{12})\b', text, re.IGNORECASE)
    if cccd_match:
        info['vneid'] = cccd_match.group(1).strip()
    else:
        # Fallback CMND 9 chữ số
        cmnd_match = re.search(r'\b([0-9]{9})\b', text)
        if cmnd_match:
            info['vneid'] = cmnd_match.group(1).strip()
            
    # 2. Tìm Họ và tên
    name_match = re.search(r'(?:Họ và tên|Full name|Họ tên|Họ và tên / Full name)[:\s\n]+([A-ZÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ\s]+)', text, re.IGNORECASE)
    if name_match:
        name_cand = name_match.group(1).strip().split('\n')[0].strip()
        if len(name_cand) >= 3 and not any(kw in name_cand.upper() for kw in ['CONG HOA', 'CỘNG HÒA', 'VIET NAM', 'VIỆT NAM', 'CAN CUOC', 'CĂN CƯỚC']):
            info['name'] = name_cand

    # Fallback tìm dòng chữ IN HOA hợp lệ đại diện cho họ tên
    if 'name' not in info:
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        for line in lines:
            # Loại trừ các tiêu đề quốc hiệu
            clean_line = re.sub(r'[^A-ZÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ\s]', '', line).strip()
            words = clean_line.split()
            if 2 <= len(words) <= 5 and clean_line.isupper():
                if not any(kw in clean_line for kw in ['CONG HOA', 'CỘNG HÒA', 'VIET NAM', 'VIỆT NAM', 'CAN CUOC', 'CĂN CƯỚC', 'GIAY PHEP', 'GIẤY PHÉP', 'DOC LAP', 'ĐỘC LẬP', 'HANH PHUC', 'HẠNH PHÚC', 'SO', 'NO']):
                    info['name'] = clean_line
                    break

    # 3. Tìm hạng bằng lái (B1, B2, C, D, E, FC, FE) nếu có
    lic_match = re.search(r'\b(B1|B2|C|D|E|FB2|FC|FD|FE)\b', text, re.IGNORECASE)
    if lic_match:
        info['license_class'] = lic_match.group(1).upper()
        
    return info

def scan_cccd_or_vneid(frame, deadline=None):
    """
    Nhận diện đa tầng: Mã QR siêu tốc + AI OCR Tiếng Việt (Tesseract vie+eng) đọc trực tiếp chữ trên thẻ
    """
    if frame is None:
        return None

    # --- TẦNG 1: QUÉT MÃ QR (PyZbar & OpenCV) ---
    try:
        import pyzbar.pyzbar as pyzbar
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
        decoded_objects = pyzbar.decode(gray)
        for obj in decoded_objects:
            qr_text = obj.data.decode('utf-8', errors='ignore').strip()
            if qr_text:
                parts = qr_text.split('|')
                vneid_num = parts[0].strip() if len(parts) >= 1 else qr_text
                name_str = parts[2].strip() if len(parts) >= 3 else "Nguyễn Văn A"
                return {
                    "success": True,
                    "vneid": vneid_num,
                    "name": name_str,
                    "method": "PYZBAR_QR"
                }
    except Exception:
        pass

    try:
        qr_detector = cv2.QRCodeDetector()
        qr_text, points, _ = qr_detector.detectAndDecode(frame)
        if qr_text:
            parts = qr_text.split('|')
            vneid_num = parts[0].strip() if len(parts) >= 1 else qr_text
            name_str = parts[2].strip() if len(parts) >= 3 else "Nguyễn Văn A"
            return {
                "success": True,
                "vneid": vneid_num,
                "name": name_str,
                "method": "OPENCV_QR"
            }
    except Exception:
        pass

    # --- TẦNG 2: AI OCR ĐỌC CHỮ TIẾNG VIỆT & CHỮ SỐ TRÊN THẺ (Tesseract vie+eng) ---
    try:
        import pytesseract

        def remaining_seconds():
            return None if deadline is None else deadline - time.monotonic()

        if deadline is not None and remaining_seconds() <= 0:
            return None
        
        # Tiền xử lý ảnh nâng cao (Enhanced Preprocessing)
        h, w = frame.shape[:2]
        proc_img = frame.copy()
        if w < 1000:
            scale_f = 1000.0 / w
            proc_img = cv2.resize(proc_img, (int(w * scale_f), int(h * scale_f)), interpolation=cv2.INTER_CUBIC)
            
        gray_img = cv2.cvtColor(proc_img, cv2.COLOR_BGR2GRAY) if len(proc_img.shape) == 3 else proc_img
        
        # Tăng cường độ tương phản (CLAHE)
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray_img)
        
        # Lọc làm sắc nét chữ
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        sharpened = cv2.filter2D(enhanced, -1, kernel)
        
        # Chạy OCR trên ảnh sắc nét
        ocr_timeout = max(0.1, remaining_seconds()) if deadline is not None else 3
        ocr_text = pytesseract.image_to_string(
            sharpened, lang='vie+eng', config='--psm 6', timeout=ocr_timeout
        )
        info = extract_info_from_ocr_text(ocr_text)
        
        # Nếu chưa tìm thấy đủ số CCCD, thử chạy tiếp trên ảnh nhị phân Otsu
        if 'vneid' not in info and (deadline is None or remaining_seconds() > 0.35):
            _, thresh = cv2.threshold(sharpened, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            ocr_timeout = max(0.1, remaining_seconds()) if deadline is not None else 3
            ocr_text_otsu = pytesseract.image_to_string(
                thresh, lang='vie+eng', config='--psm 6', timeout=ocr_timeout
            )
            info_otsu = extract_info_from_ocr_text(ocr_text_otsu)
            if 'vneid' in info_otsu:
                info['vneid'] = info_otsu['vneid']
            if 'name' not in info and 'name' in info_otsu:
                info['name'] = info_otsu['name']

        if info and ('vneid' in info or 'name' in info):
            vneid_res = info.get('vneid', '079203001234')
            name_res = info.get('name', 'Nguyễn Văn A')
            lic_res = info.get('license_class', 'B2')
            print(f"[AI OCR SUCCESS] Trích xuất thành công từ ảnh: {name_res} ({vneid_res}) - Hạng: {lic_res}")
            return {
                "success": True,
                "vneid": vneid_res,
                "name": name_res,
                "license_class": lic_res,
                "method": "AI_OCR_TESSERACT"
            }
    except Exception as e_ocr:
        print(f"[AI OCR ERROR] {e_ocr}")

    return None


def _scan_vneid_worker(snapshot_frame, result_pipe, timeout_seconds):
    """Worker tách biệt: kết thúc cùng process nếu OCR bị quá thời gian."""
    try:
        result_pipe.send(scan_cccd_or_vneid(
            snapshot_frame, deadline=time.monotonic() + timeout_seconds
        ))
    except Exception as error:
        print(f"[AI OCR ERROR] {error}")
        try:
            result_pipe.send(None)
        except Exception:
            pass
    finally:
        result_pipe.close()


def scan_vneid_with_timeout(snapshot_frame, timeout_seconds=VNEID_PROCESSING_TIMEOUT_SECONDS):
    """Chạy QR/OCR tối đa `timeout_seconds`; hết hạn sẽ hủy process OCR."""
    receive_pipe, send_pipe = mp.Pipe(duplex=False)
    # DMS chạy trên Raspberry Pi/Linux. fork tránh import lại toàn bộ camera/OLED
    # trong worker, nên thời gian 3 giây là dành cho QR/OCR thực tế.
    worker = mp.get_context("fork").Process(
        target=_scan_vneid_worker,
        args=(snapshot_frame, send_pipe, timeout_seconds),
        daemon=True,
    )
    started_at = time.monotonic()
    worker.start()
    send_pipe.close()
    try:
        remaining = max(0.0, timeout_seconds - (time.monotonic() - started_at))
        if receive_pipe.poll(remaining):
            result = receive_pipe.recv()
            worker.join(timeout=0.1)
            return result

        print(f"[AUTH TIMEOUT] QR/OCR vượt quá {timeout_seconds:.0f}s, đã hủy tác vụ.")
        return None
    finally:
        if worker.is_alive():
            worker.terminate()
        worker.join(timeout=0.2)
        receive_pipe.close()

def process_vneid_async(snapshot_frame, verified_driver=None):
    """
    Tạo phiên lái xe sau khi xác thực CCCD/VNeID.

    ``verified_driver`` được truyền từ Web Dashboard khi người dùng đã xác thực
    trên web. Trường hợp đó không OCR lại khung hình camera, mà chuyển thẳng
    sang bước xác thực khuôn mặt.
    """
    global is_processing_vneid, auth_state, vneid_authenticated, driver_info
    global session_start_time, session_start_str, session_distraction_count, session_drowsiness_count, session_yawn_count, current_session_id, fatigue_scores_history
    
    try:
        now_stamp = time.strftime('%Y%m%d_%H%M%S')
        now_str = time.strftime('%Y-%m-%d %H:%M:%S')
        authenticated_from_web = verified_driver is not None

        if authenticated_from_web:
            driver_info["name"] = verified_driver.get("name", driver_info["name"])
            driver_info["vneid"] = verified_driver.get("vneid", driver_info["vneid"])
            driver_info["license_class"] = verified_driver.get("license_class", driver_info["license_class"])
            print(f"[AUTH WEB] Đã xác thực trên web: {driver_info['name']} ({driver_info['vneid']}); chuyển thẳng sang quét mặt.")
        else:
            # Lưu ngay sau khi chụp để không mất bằng chứng nếu OCR hết thời gian.
            cccd_img_path = os.path.join(AUDIO_DIR, f"cccd_snapshot_{now_stamp}.jpg")
            try:
                cv2.imwrite(cccd_img_path, snapshot_frame)
                os.chmod(cccd_img_path, 0o666)
                print(f"[IMAGE SUCCESS] Đã lưu ảnh chụp thẻ CCCD: {cccd_img_path}")
            except Exception as e:
                print(f"[IMAGE ERROR] {e}")

            print(f"[AUTH THREAD] Xử lý QR/OCR tối đa {VNEID_PROCESSING_TIMEOUT_SECONDS:.0f}s...")
            scan_res = scan_vneid_with_timeout(snapshot_frame)
            if scan_res and scan_res.get("success"):
                driver_info["vneid"] = scan_res["vneid"]
                driver_info["name"] = scan_res["name"]
                driver_info["license_class"] = scan_res.get("license_class", driver_info["license_class"])
                print(f"[AUTH THREAD] Nhận diện thành công ({scan_res['method']}): {driver_info['name']} - {driver_info['vneid']}")

        # DMS đã khởi động và camera đã ổn định trước khi hàm này được gọi.
        # Gửi thông tin tài xế ngay trước khi chuyển sang bước quét mặt.
        try:
            from telegram_bot import send_telegram_alert_async
            send_telegram_alert_async(
                "🆔 THÔNG TIN TÀI XẾ ĐÃ ĐƯỢC NHẬN!\n"
                f"👤 Họ tên: {driver_info.get('name', 'Chưa xác định')}\n"
                f"🪪 VNeID/CCCD: {driver_info.get('vneid', 'Chưa xác định')}\n"
                f"🚗 Hạng bằng: {driver_info.get('license_class', 'B2')}\n"
                f"⏰ Thời gian: {now_str}\n"
                "📍 Hệ thống DMS đã sẵn sàng, chuyển sang quét khuôn mặt."
            )
        except Exception as e:
            print(f"[WARN] Không gửi được thông tin tài xế: {e}")

        play_voice_prompt_async("vneid_success")

        session_start_time = time.time()
        session_start_str = now_str
        session_distraction_count = 0
        session_drowsiness_count = 0
        session_yawn_count = 0
        fatigue_scores_history.clear()
        current_session_id = None
        
        try:
            db_conn_tmp = sqlite3.connect("dms_history.db")
            cur = db_conn_tmp.cursor()
            cur.execute("""
                INSERT INTO dms_sessions (start_time, end_time, duration_seconds, distraction_count, drowsiness_count, yawn_count, avg_fatigue_score, max_fatigue_score, driver_name, vneid_card)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (now_str, now_str, 0, 0, 0, 0, 0.0, 0.0, driver_info["name"], driver_info["vneid"]))
            current_session_id = cur.lastrowid
            db_conn_tmp.commit()
            db_conn_tmp.close()
            print(f"[DB SUCCESS] Đã tạo phiên lái xe mới trong SQLite: Session #{current_session_id}")
        except Exception as e_db:
            print(f"[DB ERROR] {e_db}")

        vneid_authenticated = True
        auth_state = "FACE_REQ"
        print("[AUTH THREAD] Đã chuyển sang bước Yêu cầu Xác thực khuôn mặt (FACE_REQ)!")

    except Exception as e:
        print(f"[AUTH THREAD ERROR] {e}")
    finally:
        is_processing_vneid = False

threading.Thread(target=alarm_worker, daemon=True).start()

# Khởi động màn hình hiển thị OLED I2C (128x64)
oled = get_oled_manager()
if oled.is_available():
    oled.start()

# --- Các Hàm Tính Toán Chỉ Số ---

def calculate_ear(eye_pts, landmarks, w, h):
    """Tính toán tỷ lệ mở mắt (Eye Aspect Ratio)"""
    p = []
    for idx in eye_pts:
        p.append(np.array([landmarks[idx].x * w, landmarks[idx].y * h]))
    
    # Khoảng cách dọc
    d_v1 = np.linalg.norm(p[1] - p[5])
    d_v2 = np.linalg.norm(p[2] - p[4])
    # Khoảng cách ngang
    d_h = np.linalg.norm(p[0] - p[3])
    
    ear = (d_v1 + d_v2) / (2.0 * d_h + 1e-6)
    return ear

def calculate_mar(landmarks, w, h):
    """Tính toán tỷ lệ mở miệng (Mouth Aspect Ratio)"""
    def dist(pt1_idx, pt2_idx):
        p1 = np.array([landmarks[pt1_idx].x * w, landmarks[pt1_idx].y * h])
        p2 = np.array([landmarks[pt2_idx].x * w, landmarks[pt2_idx].y * h])
        return np.linalg.norm(p1 - p2)
    
    a = dist(LIP_A[0], LIP_A[1])
    b = dist(LIP_B[0], LIP_B[1])
    c = dist(LIP_C[0], LIP_C[1])
    d = dist(LIP_D[0], LIP_D[1])
    
    mar = (a + b + c) / (2.0 * d + 1e-6)
    return mar

def estimate_head_pose(landmarks, w, h):
    """Ước lượng tư thế đầu (Pitch, Yaw, Roll) sử dụng SolvePnP"""
    image_points = np.array([
        (landmarks[1].x * w, landmarks[1].y * h),      # Nose tip
        (landmarks[152].x * w, landmarks[152].y * h),  # Chin
        (landmarks[33].x * w, landmarks[33].y * h),    # Left eye corner
        (landmarks[263].x * w, landmarks[263].y * h),  # Right eye corner
        (landmarks[61].x * w, landmarks[61].y * h),    # Left mouth corner
        (landmarks[291].x * w, landmarks[291].y * h)   # Right mouth corner
    ], dtype=np.float32)
    
    focal_length = w
    center = (w / 2, h / 2)
    camera_matrix = np.array([
        [focal_length, 0, center[0]],
        [0, focal_length, center[1]],
        [0, 0, 1]
    ], dtype=np.float32)
    dist_coeffs = np.zeros((4, 1))
    
    success, rvec, tvec = cv2.solvePnP(MODEL_POINTS, image_points, camera_matrix, dist_coeffs, flags=cv2.SOLVEPNP_ITERATIVE)
    
    # Chuyển đổi ma trận quay
    R, _ = cv2.Rodrigues(rvec)
    
    # Tính toán góc Euler Pitch, Yaw, Roll từ ma trận quay R
    sy = np.sqrt(R[0,0]*R[0,0] + R[1,0]*R[1,0])
    singular = sy < 1e-6
    if not singular:
        x = np.arctan2(R[2,1], R[2,2])
        y = np.arctan2(-R[2,0], sy)
        z = np.arctan2(R[1,0], R[0,0])
    else:
        x = np.arctan2(-R[1,2], R[1,1])
        y = np.arctan2(-R[2,0], sy)
        z = 0
        
    pitch = (np.degrees(x) + 180) % 360 - 180
    yaw = (np.degrees(y) + 180) % 360 - 180
    roll = (np.degrees(z) + 180) % 360 - 180
    
    return pitch, yaw, roll, rvec, tvec, camera_matrix, dist_coeffs, image_points

# --- Hàm Vẽ Thanh Trượt Đẹp ---
def draw_bar(img, label, val, max_val, x, y, w, h, color):
    cv2.putText(img, f"{label}: {val:.2f}", (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.rectangle(img, (x, y), (x + w, y + h), (50, 50, 50), -1)
    fill_w = int(min(1.0, val / (max_val + 1e-6)) * w)
    cv2.rectangle(img, (x, y), (x + fill_w, y + h), color, -1)

def set_camera_controls(exposure_val, gain_val):
    """Thiết lập thông số phơi sáng và gain tương thích với cả CSI Camera và USB UVC Webcams"""
    import subprocess
    import os
    dev_path = "/dev/v4l-subdev0"
    if not os.path.exists(dev_path):
        dev_path = "/dev/video0"
    if not os.path.exists(dev_path):
        return
    try:
        info = subprocess.run(["v4l2-ctl", "-d", dev_path, "--info"], capture_output=True, text=True).stdout
        if "uvcvideo" in info or "USB" in info:
            # USB Webcams dùng chế độ tự động phơi sáng phần cứng
            pass
        else:
            # Raspberry Pi CSI camera dùng điều khiển subdev
            subprocess.run([
                "v4l2-ctl", "-d", dev_path,
                "-c", "auto_exposure=1",
                "-c", "gain_automatic=0",
                "-c", f"exposure={exposure_val}",
                "-c", f"analogue_gain={gain_val}"
            ], capture_output=True)
    except Exception as e:
        pass

# --- Luồng Ứng Dụng Chính ---

def main():
    global alarm_level, GPIO_AVAILABLE
    
    # Khởi tạo argparse để cấu hình tham số
    import argparse
    parser = argparse.ArgumentParser(description="DMS: HE THONG CANH BAO NGU GAT THOI GIAN THUC (AI)")
    parser.add_argument("--camera", type=int, default=None, help="Chi so camera su dung (mac dinh thu tu dong tu 0-10)")
    parser.add_argument("--mono", action="store_true", help="Che do giai ma don sac (Monochrome) cho camera Raw Bayer")
    parser.add_argument("--enhance", action="store_true", help="Bat buoc bat tang cuong do tuong phan CLAHE cho camera den trang / hong ngoai")
    parser.add_argument("--no-enhance", action="store_true", help="Vo hieu hoa tu dong tang cuong do tuong phan")
    parser.add_argument("--show-enhanced", action="store_true", help="Hien thi khung hinh da tang cuong CLAHE len man hinh Dashboard")
    parser.add_argument("--scale", type=float, default=1.0, help="Ti le thu nho/phong to cua so hien thi (vi du: 0.5 de thu nho mot nua)")
    args = parser.parse_args()

    print("====================================================")
    print("DMS: HE THONG CANH BAO NGU GAT THOI GIAN THUC (AI)")
    print("====================================================")
    
    # Cấu hình camera
    cap = None
    simulated_mode = False
    raw_bayer_mode = False
    raw_bayer_format = None
    
    # Load camera configuration if available
    camera_config = None
    config_path = "camera_config.json"
    import json
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                camera_config = json.load(f)
            print(f"[INFO] Loaded camera configuration: {camera_config}")
        except Exception as e:
            print(f"[WARN] Failed to load {config_path}: {e}")

    bayer_pattern = "GB"  # Default
    camera_flip_code = None
    if camera_config is not None:
        bayer_pattern = camera_config.get("bayer_pattern", "GB")
        camera_flip_code = camera_config.get("flip_code", None)

    
    # Khởi tạo thông số phơi sáng và điều khiển phơi sáng tự động (AEGC)
    current_exposure = 250
    current_gain = 500
    frame_count = 0
    
    def aegc_loop(mean_brightness):
        nonlocal current_exposure, current_gain
        target = 120.0
        diff = target - mean_brightness
        if abs(diff) > 8:
            step_exp = int(diff * 1.2)
            step_gain = int(diff * 1.8)
            new_exposure = max(4, min(500, current_exposure + step_exp))
            new_gain = max(16, min(1023, current_gain + step_gain))
            if new_exposure != current_exposure or new_gain != current_gain:
                current_exposure = new_exposure
                current_gain = new_gain
                set_camera_controls(current_exposure, current_gain)
    
    # Quét danh sách camera (Ưu tiên camera_index trong camera_config.json nếu có, hoặc --camera)
    if args.camera is not None:
        camera_indices = [args.camera]
    elif camera_config is not None and "camera_index" in camera_config:
        pref_idx = camera_config["camera_index"]
        valid_devs = [i for i in range(6) if os.path.exists(f"/dev/video{i}")]
        if not valid_devs:
            valid_devs = list(range(6))
        camera_indices = [pref_idx] + [i for i in valid_devs if i != pref_idx]
    else:
        valid_devs = [i for i in range(6) if os.path.exists(f"/dev/video{i}")]
        camera_indices = valid_devs if valid_devs else list(range(6))
    
    is_usb_config = (camera_config.get("camera_type", "usb") == "usb") if camera_config else True

    for camera_idx in camera_indices:
        try:
            print(f"[INFO] Dang thu mo camera index {camera_idx}...")
            temp_cap = cv2.VideoCapture(camera_idx, cv2.CAP_V4L2)
            if not temp_cap.isOpened():
                temp_cap = cv2.VideoCapture(camera_idx)

            if temp_cap.isOpened():
                # Tối ưu hóa cho USB Camera: Buffer size = 1 triệt tiêu hoàn toàn độ trễ (lag)
                try:
                    temp_cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass

                # Cấu hình định dạng và độ phân giải camera
                fourcc_code = camera_config.get("fourcc", "MJPG") if camera_config else "MJPG"
                req_w = camera_config.get("width", 1280) if camera_config else 1280
                req_h = camera_config.get("height", 720) if camera_config else 720
                req_fps = camera_config.get("fps", 30) if camera_config else 30

                resolutions_to_try = [(req_w, req_h)]
                if (req_w, req_h) != (1280, 720):
                    resolutions_to_try.append((1280, 720))
                if (req_w, req_h) != (640, 480):
                    resolutions_to_try.append((640, 480))

                ret = False
                test_frame = None

                for try_w, try_h in resolutions_to_try:
                    if fourcc_code:
                        temp_cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc_code))
                    temp_cap.set(cv2.CAP_PROP_FRAME_WIDTH, try_w)
                    temp_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, try_h)
                    if req_fps:
                        temp_cap.set(cv2.CAP_PROP_FPS, req_fps)

                    # Đọc thử kèm làm ấm (USB camera cần thời gian cho ISP khởi tạo)
                    for attempt in range(8):
                        try:
                            ret, test_frame = temp_cap.read()
                        except Exception:
                            ret = False
                        if ret and test_frame is not None and test_frame.size > 0:
                            break
                        time.sleep(0.04)

                    if ret and test_frame is not None and test_frame.size > 0:
                        cap = temp_cap
                        h_res, w_res = test_frame.shape[:2]
                        print(f"[SUCCESS] Da mo camera USB/V4L2 index {camera_idx} thanh cong! Do phan gia hoat dong: {w_res}x{h_res}")
                        break

                if cap is not None:
                    break

                # Nếu không đọc được frame thường và KHÔNG PHẢI chế độ USB camera bắt buộc mới thử Raw Bayer
                if not is_usb_config:
                    print(f"[INFO] Camera index {camera_idx} khong doc duoc frame thuong. Dang thu che do Raw Bayer (CSI Cable)...")
                    temp_cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
                    
                    # Thử GB10
                    temp_cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('G', 'B', '1', '0'))
                    temp_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    temp_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    ret_bayer = False
                    try:
                        ret_bayer, frame_bayer = temp_cap.read()
                    except Exception:
                        pass
                        
                    if ret_bayer and frame_bayer is not None and frame_bayer.size == 614400:
                        cap = temp_cap
                        raw_bayer_mode = True
                        raw_bayer_format = 'GB10'
                        print(f"[SUCCESS] Da mo camera index {camera_idx} o che do Raw Bayer GB10!")
                        break
                    
                    # Thử pGAA
                    temp_cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('p', 'G', 'A', 'A'))
                    temp_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    temp_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    ret_bayer = False
                    try:
                        ret_bayer, frame_bayer = temp_cap.read()
                    except Exception:
                        pass
                        
                    if ret_bayer and frame_bayer is not None and frame_bayer.size == 384000:
                        cap = temp_cap
                        raw_bayer_mode = True
                        raw_bayer_format = 'pGAA'
                        print(f"[SUCCESS] Da mo camera index {camera_idx} o che do Raw Bayer pGAA!")
                        break
                
                temp_cap.release()
            else:
                temp_cap.release()
        except Exception as e:
            print(f"[INFO] Loi khi thu mo camera index {camera_idx}: {e}")
            
    if cap is None:
        print("====================================================")
        print("[WARN] CANH BAO: Khong the mo bat ky camera nao (/dev/video)!")
        print("[HUONG DAN CHO RASPBERRY PI / UBUNTU]:")
        print("1. Neu ban dang dung Raspberry Pi Camera Module 3:")
        print("   - Hãy dam bao da them 'dtoverlay=imx708' vao cuoi file '/boot/firmware/config.txt' va reboot.")
        print("   - Chay ung dung qua cong cu tuong thich: 'libcamerify python3 drowsiness_detector.py'")
        print("2. Kiem tra cong ket noi cap va dam bao camera khong bi ung dung khac chiem dung.")
        print("Hệ thong tu dong chuyen sang che do GIAP LAP (Simulation Mode) de ban kiem tra...")
        print("====================================================")
        simulated_mode = True
        
    # Khởi tạo phơi sáng mặc định nếu ở chế độ Raw Bayer
    if not simulated_mode and raw_bayer_mode:
        print(f"[INFO] Thiet lap phoi sang va gain ban dau cho Raw Bayer: exposure={current_exposure}, gain={current_gain}")
        set_camera_controls(current_exposure, current_gain)
        
    # Khởi tạo MediaPipe Face Mesh (Graceful fallback nếu không hỗ trợ)
    mediapipe_available = True
    face_mesh = None
    try:
        import mediapipe.python.solutions.face_mesh as mp_face_mesh
        face_mesh = mp_face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=False,  # Tối ưu hóa FPS cho CPU Raspberry Pi 4 (không cần mống mắt)
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )
    except (AttributeError, ImportError) as e:
        mediapipe_available = False
        print(f"[WARN] MediaPipe solutions khong kha dung tren phien ban Python nay: {e}")
        print("[WARN] Bat buoc chuyen sang che do GIAP LAP (Simulation Mode)!")
        simulated_mode = True
    
    # Tải mô hình dự báo LSTM
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    lstm_model = DrowsinessLSTM().to(device)
    model_loaded = False
    model_path = "lstm_drowsiness.pth"
    
    if os.path.exists(model_path):
        try:
            lstm_model.load_state_dict(torch.load(model_path, map_location=device))
            lstm_model.eval()
            model_loaded = True
            print(f"[INFO] Da tai mo hinh LSTM thanh cong tu {model_path}.")
        except Exception as e:
            print(f"[ERROR] Khong the load trong so LSTM: {e}. Se dung kịch ban thay the.")
    else:
        print("[WARN] File weights lstm_drowsiness.pth khong ton tai. Vui long chay train_lstm.py truoc.")
        print("[WARN] He thong se tu dong dung bo phan tich heuristic neu khong co model.")

    # Khởi tạo Cơ sở dữ liệu SQLite để lưu lịch sử như thiết kế
    db_path = "dms_history.db"
    db_conn = sqlite3.connect(db_path)
    db_cursor = db_conn.cursor()
    # Tối ưu hóa SQLite cho thẻ nhớ SD Card trên Raspberry Pi
    try:
        db_cursor.execute("PRAGMA journal_mode=WAL;")
        db_cursor.execute("PRAGMA synchronous=NORMAL;")
    except:
        pass
    db_cursor.execute("""
        CREATE TABLE IF NOT EXISTS dms_logs (
            timestamp TEXT PRIMARY KEY,
            ear REAL,
            mar REAL,
            pitch REAL,
            yaw REAL,
            roll REAL,
            risk REAL
        )
    """)
    db_cursor.execute("""
        CREATE TABLE IF NOT EXISTS dms_sessions (
            session_id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT,
            end_time TEXT,
            duration_seconds INTEGER,
            distraction_count INTEGER,
            drowsiness_count INTEGER,
            yawn_count INTEGER,
            avg_fatigue_score REAL,
            max_fatigue_score REAL,
            driver_name TEXT,
            vneid_card TEXT
        )
    """)
    # Tự động nâng cấp bảng nếu thiếu cột trong phiên bản cũ
    for col, col_type in [("driver_name", "TEXT"), ("vneid_card", "TEXT")]:
        try:
            db_cursor.execute(f"ALTER TABLE dms_sessions ADD COLUMN {col} {col_type}")
        except Exception:
            pass
    db_conn.commit()
    print(f"[INFO] Da khoi tao co so du lieu SQLite tai: {db_path}")

    # Khởi tạo GPIO trên Raspberry Pi (nếu có sẵn)
    if GPIO_AVAILABLE:
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(MOTOR_PIN, GPIO.OUT)
            GPIO.setup(BUZZER_PIN, GPIO.OUT)
            GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.output(MOTOR_PIN, GPIO.LOW)
            GPIO.output(BUZZER_PIN, GPIO.LOW)
            print("[INFO] Da khoi tao GPIO Raspberry Pi (Motor: 17, Buzzer: 27, Button: 22) thanh cong.")
        except Exception as e:
            GPIO_AVAILABLE = False
            print(f"[WARN] Khong the khoi tao GPIO: {e}")

    # --- Trạng Thái Khởi Động Hệ Thống & Xác Thực Người Lái ---
    # State 1: "VNEID_REQ" -> Yêu cầu xác thực thẻ VNeID/CCCD (Phát còi 2 tiếng liên tiếp, 3s kêu tiếp)
    # State 2: "FACE_REQ"  -> Yêu cầu xác thực khuôn mặt (Phát còi 1 tiếng ngắt quãng mỗi 3s)
    # State 3: "ACTIVE"    -> Đã xác thực thành công, đang tiến hành giám sát lái xe mệt mỏi
    global auth_state, vneid_authenticated, vneid_prompt_played, face_prompt_played, vneid_auth_trigger, is_processing_vneid, alarm_level, driver_info
    auth_state = "VNEID_REQ"
    vneid_authenticated = False
    vneid_prompt_played = False
    face_prompt_played = False
    vneid_auth_trigger = False
    is_processing_vneid = False
    alarm_level = 4
    qr_detector = cv2.QRCodeDetector()


    # Khởi tạo các cấu trúc lưu trữ và cửa sổ trượt
    frame_buffer = []  # Lưu dữ liệu trong 1 giây để tính trung bình
    history_window = deque(maxlen=60)  # Cửa sổ trượt 60 giây chứa [EAR_norm, MAR_norm, Pitch_norm, PERCLOS_norm]
    
    # Bộ đếm chớp mắt & ngáp
    blink_timestamps = deque()
    yawn_timestamps = deque()
    
    eye_previously_closed = False
    mouth_previously_yawning = False
    yawn_start_time = None
    
    # Làm ấm camera tránh phơi sáng xấu ở giây đầu tiên
    warmup_count = 0
    warmup_limit = 45
    
    # Các tham số hiệu chuẩn (Calibration)
    calib_frames = 100
    calib_count = 0
    calib_ears = []
    calib_mars = []
    calib_pitches = []
    calib_yaws = []
    calib_rolls = []
    
    ear_baseline = 0.30
    mar_baseline = 0.20
    pitch_baseline = 0.0
    yaw_baseline = 0.0
    roll_baseline = 0.0
    ear_limit = 0.21
    
    # Hàng đợi lưu trạng thái nhắm mắt từng frame để tính PERCLOS thời gian thực (150 frames ~ 5 giây)
    eye_closed_frames = deque(maxlen=150)
    face_lost_start_time = None
    eye_closed_start_time = None
    head_tilted_start_time = None
    
    # Thông tin tiến trình hành trình lái xe (Driving Session Tracking)
    current_session_id = None
    session_start_time = None
    session_start_str = ""
    session_distraction_count = 0
    session_drowsiness_count = 0
    session_yawn_count = 0
    fatigue_scores_history = []
    
    # Cờ kiểm soát sự kiện liên tục
    eye_closed_1s_logged = False
    eye_closed_3s_logged = False
    distraction_logged = False
    last_db_save_time = time.time()

    def save_session_to_db():
        nonlocal current_session_id, session_start_time, session_start_str
        nonlocal session_distraction_count, session_drowsiness_count, session_yawn_count, fatigue_scores_history
        if session_start_time is None:
            return
        current_time_val = time.time()
        end_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_time_val))
        duration_sec = int(current_time_val - session_start_time)
        avg_fatigue = float(np.mean(fatigue_scores_history)) if fatigue_scores_history else 0.0
        max_fatigue = float(np.max(fatigue_scores_history)) if fatigue_scores_history else 0.0
        
        try:
            if current_session_id is None:
                db_cursor.execute("""
                    INSERT INTO dms_sessions (start_time, end_time, duration_seconds, distraction_count, drowsiness_count, yawn_count, avg_fatigue_score, max_fatigue_score, driver_name, vneid_card)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (session_start_str, end_time_str, duration_sec, session_distraction_count, session_drowsiness_count, session_yawn_count, avg_fatigue, max_fatigue, driver_info["name"], driver_info["vneid"]))
                current_session_id = db_cursor.lastrowid
            else:
                db_cursor.execute("""
                    UPDATE dms_sessions
                    SET end_time = ?, duration_seconds = ?, distraction_count = ?, drowsiness_count = ?, yawn_count = ?, avg_fatigue_score = ?, max_fatigue_score = ?
                    WHERE session_id = ?
                """, (end_time_str, duration_sec, session_distraction_count, session_drowsiness_count, session_yawn_count, avg_fatigue, max_fatigue, current_session_id))
            db_conn.commit()
        except Exception as e:
            print(f"[WARN] Khong the luu tien trinh vao DB: {e}")

    calibrated = False
    
    last_second_time = time.time()
    lstm_risk = 0.0
    fatigue_score = 0.0
    perclos = 0.0
    blink_rate = 0
    yawn_count = 0
    
    recalibrate_requested = False

    # Web dashboard có thể chạy bằng user khác với DMS. Vì /tmp dùng sticky bit,
    # DMS không được phép xóa file tín hiệu của user đó. Lưu mã tín hiệu đã nhận
    # trong file riêng để tránh xử lý lặp mà không cần xóa file nguồn.
    web_auth_ack_path = "/tmp/dms_vneid_consumed_timestamp"
    try:
        with open(web_auth_ack_path, "r") as f:
            last_web_auth_trigger_id = f.read().strip()
    except OSError:
        last_web_auth_trigger_id = ""

    # Quản lý giọng nói cảnh báo thông minh không bị lặp đè
    last_voice_alert_time = 0.0
    VOICE_ALERT_COOLDOWN = 6.0  # Khoảng cách tối thiểu 6s giữa 2 câu cảnh báo giọng nói

    def trigger_voice_alert(prompt_name):
        nonlocal last_voice_alert_time
        now = time.time()
        if (now - last_voice_alert_time) >= VOICE_ALERT_COOLDOWN:
            last_voice_alert_time = now
            play_voice_prompt_async(prompt_name)

    def reset_and_start_authentication(immediate_capture=False):
        """
        Reset toàn diện trạng thái xác thực và hiệu chuẩn khuôn mặt để người dùng quét lại từ đầu
        """
        nonlocal calibrated, calib_count, calib_ears, calib_mars, calib_pitches, calib_yaws, calib_rolls
        nonlocal current_session_id, session_start_time, warmup_count, last_voice_alert_time
        nonlocal eye_closed_1s_logged, eye_closed_3s_logged, distraction_logged
        global auth_state, vneid_authenticated, vneid_prompt_played, face_prompt_played, vneid_auth_trigger, is_processing_vneid, alarm_level
        
        if current_session_id is not None:
            save_session_to_db()
            
        calibrated = False
        calib_count = 0
        calib_ears.clear()
        calib_mars.clear()
        calib_pitches.clear()
        calib_yaws.clear()
        calib_rolls.clear()
        
        eye_closed_1s_logged = False
        eye_closed_3s_logged = False
        distraction_logged = False
        
        auth_state = "VNEID_REQ"
        vneid_authenticated = False
        vneid_prompt_played = False
        face_prompt_played = False
        is_processing_vneid = False
        current_session_id = None
        session_start_time = None
        warmup_count = warmup_limit
        alarm_level = 0
        last_voice_alert_time = 0.0
        
        if immediate_capture:
            vneid_auth_trigger = True
        else:
            vneid_auth_trigger = False

    def restart_face_scan():
        """Quét lại khuôn mặt nhưng giữ nguyên xác thực VNeID/CCCD và phiên lái xe."""
        nonlocal calibrated, calib_count, calib_ears, calib_mars, calib_pitches, calib_yaws, calib_rolls
        global auth_state, face_prompt_played, alarm_level

        # VNeID/CCCD đã được chụp, OCR, gửi xử lý nền và tạo phiên trước đó.
        # Không gọi reset_and_start_authentication() ở đây vì hàm đó quay lại VNEID_REQ.
        calibrated = False
        calib_count = 0
        calib_ears.clear()
        calib_mars.clear()
        calib_pitches.clear()
        calib_yaws.clear()
        calib_rolls.clear()
        face_prompt_played = False
        auth_state = "FACE_REQ"
        alarm_level = 0
        print("[FACE RESCAN] Quét lại khuôn mặt; giữ nguyên VNeID/CCCD và Session hiện tại.")

    def handle_scan_button():
        """Xử lý nút quét theo bước xác thực hiện tại."""
        global auth_state, vneid_auth_trigger, is_processing_vneid

        if auth_state == "VNEID_REQ":
            if is_processing_vneid:
                print("[SCAN BUTTON] Ảnh CCCD/VNeID đang được xử lý nền, bỏ qua lần bấm này.")
                return
            # Chỉ ở bước này nút mới chụp CCCD/VNeID và gửi cho luồng xử lý nền.
            vneid_auth_trigger = True
            print("[SCAN BUTTON] Chụp CCCD/VNeID để xử lý tự động.")
        elif vneid_authenticated:
            # Sau khi VNeID thành công, mọi lần bấm chỉ hiệu chuẩn/quét lại khuôn mặt.
            restart_face_scan()
        else:
            print(f"[SCAN BUTTON] Bỏ qua do trạng thái không hợp lệ: {auth_state}")

    # Tạo giao diện hiển thị (Su dung WINDOW_NORMAL de cho phep keo gian thu nho)
    cv2.namedWindow("DMS - Drowsiness Detection Dashboard", cv2.WINDOW_NORMAL)
    
    def on_mouse_click(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            scale = args.scale if (args.scale != 1.0 and args.scale > 0) else 1.0
            real_x = x / scale
            real_y = y / scale
            
            # w mặc định là 640 nếu chưa đọc frame
            current_w = w if 'w' in locals() else 640
            
            # Kiểm tra nút bấm trên Dashboard
            if (current_w + 15) <= real_x <= (current_w + 305) and 425 <= real_y <= 460:
                print("[GUI BUTTON] Click nút quét.")
                handle_scan_button()

        cv2.setMouseCallback("DMS - Drowsiness Detection Dashboard", on_mouse_click)
    
    sim_time_start = time.time()
    
    while True:
        # Kiểm tra nút bấm vật lý trên Raspberry Pi GPIO (Chân 22 / Pin 15)
        if GPIO_AVAILABLE:
            try:
                if GPIO.input(BUTTON_PIN) == GPIO.LOW:
                    print("[PHYSICAL BUTTON] Đã bấm nút GPIO 22.")
                    handle_scan_button()
                    time.sleep(0.4)  # Chống nảy nút bấm (debounce)
            except Exception:
                pass

        # 1. Đọc frame (từ camera thật hoặc tạo dữ liệu giả lập)
        if not simulated_mode:
            if raw_bayer_mode:
                ret, raw_frame = cap.read()
                if not ret or raw_frame is None:
                    print("[ERROR] Mat ket noi voi camera (Raw mode).")
                    break
                try:
                    # Giải mã raw_frame sang BGR
                    bayer_bgr_code = getattr(cv2, f"COLOR_Bayer{bayer_pattern}2BGR", cv2.COLOR_BayerGB2BGR)
                    bayer_gray_code = getattr(cv2, f"COLOR_Bayer{bayer_pattern}2GRAY", cv2.COLOR_BayerGB2GRAY)

                    if raw_bayer_format == 'GB10':
                        raw_16 = np.frombuffer(raw_frame.tobytes(), dtype=np.uint16).reshape((480, 640))
                        img_8 = (raw_16 >> 2).astype(np.uint8)
                        if args.mono:
                            img_gray = cv2.cvtColor(img_8, bayer_gray_code)
                            frame = cv2.cvtColor(img_gray, cv2.COLOR_GRAY2BGR)
                        else:
                            frame = cv2.cvtColor(img_8, bayer_bgr_code)
                    elif raw_bayer_format == 'pGAA':
                        raw_bytes = np.frombuffer(raw_frame.tobytes(), dtype=np.uint8)
                        img_8 = raw_bytes.reshape(-1, 5)[:, :4].reshape((480, 640))
                        if args.mono:
                            img_gray = cv2.cvtColor(img_8, bayer_gray_code)
                            frame = cv2.cvtColor(img_gray, cv2.COLOR_GRAY2BGR)
                        else:
                            frame = cv2.cvtColor(img_8, bayer_bgr_code)
                    else:
                        frame = np.zeros((480, 640, 3), dtype=np.uint8)
                        
                    # Tự động điều chỉnh phơi sáng động (AEGC) mỗi 5 frames
                    if 'img_8' in locals():
                        frame_count += 1
                        if frame_count % 5 == 0:
                            aegc_loop(np.mean(img_8))
                except Exception as e:
                    print(f"[ERROR] Loi giai ma Bayer: {e}")
                    break
            else:
                ret, frame = cap.read()
                if not ret:
                    print("[ERROR] Mat ket noi voi camera.")
                    break
            # Lật ảnh ngang cho cảm giác gương tự nhiên hoặc dùng cấu hình góc quay
            if camera_flip_code is not None:
                frame = cv2.flip(frame, camera_flip_code)
            else:
                frame = cv2.flip(frame, 1)
            
            # Đảm bảo ảnh luôn ở dạng 3 kênh BGR để tránh lỗi ghép hstack với dashboard hoặc lỗi xử lý MediaPipe/CLAHE
            if frame is not None:
                if len(frame.shape) == 2:
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                elif len(frame.shape) == 3 and frame.shape[2] == 1:
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        else:
            # Tạo frame giả lập màu tối
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            # Tạo hiệu ứng chuyển động tròn đơn giản trên giao diện giả lập để biết chương trình đang chạy
            cv2.circle(frame, (320, 240), int(40 + 10 * np.sin(time.time() * 2)), (30, 30, 30), -1)
            cv2.putText(frame, "SIMULATED CAMERA FEED", (120, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (100, 100, 100), 2)
            cv2.putText(frame, "Connect a webcam to use real detection", (120, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 100, 100), 1)
            
        h, w = frame.shape[:2]

        # Chỉ nhận xác thực sau khi DMS hoàn tất khởi động/làm ấm camera. Khối
        # này vẫn chạy ở mọi trạng thái để không bỏ sót tín hiệu web mới.
        dms_ready = simulated_mode or warmup_count >= warmup_limit
        if dms_ready and os.path.exists("/tmp/dms_vneid_trigger.json") and not is_processing_vneid:
            try:
                import json
                with open("/tmp/dms_vneid_trigger.json", "r") as f:
                    trig_data = json.load(f)

                # Timestamp được Web Dashboard tạo mới cho mỗi lần người dùng bấm.
                # Dùng JSON đầy đủ làm fallback cho các bản dashboard cũ.
                trigger_id = str(trig_data.get("timestamp") or json.dumps(trig_data, sort_keys=True))
                if trigger_id != last_web_auth_trigger_id:
                    # Một lần xác thực mới trên web sẽ kết thúc phiên cũ trước khi
                    # tạo phiên mới và bắt đầu quét khuôn mặt.
                    if auth_state in ("FACE_REQ", "ACTIVE") and current_session_id is not None:
                        save_session_to_db()

                    is_processing_vneid = True
                    last_web_auth_trigger_id = trigger_id
                    try:
                        with open(web_auth_ack_path, "w") as f:
                            f.write(trigger_id)
                    except OSError as e:
                        # Biến trong bộ nhớ vẫn ngăn xử lý lặp cho tới khi DMS khởi động lại.
                        print(f"[AUTH WEB WARN] Không lưu được mốc đã nhận tín hiệu: {e}")
                    threading.Thread(
                        target=process_vneid_async,
                        args=(frame.copy(), trig_data),
                        daemon=True,
                    ).start()
                    print("[AUTH WEB TRIGGER] Đã nhận tín hiệu từ Web Dashboard.")
            except Exception as e:
                is_processing_vneid = False
                print(f"[AUTH WEB ERROR] Không đọc được tín hiệu xác thực từ web: {e}")
        
        # 2. Tạo phần Dashboard Dashboard bên phải (Rộng thêm 320px)
        dashboard = np.zeros((h, 320, 3), dtype=np.uint8)
        
        # 2b. Làm ấm camera trước khi xử lý (Warm up camera)
        if not simulated_mode and warmup_count < warmup_limit:
            warmup_count += 1
            cv2.putText(frame, "STABILIZING CAMERA EXPOSURE...", (80, 220), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(frame, "Please wait...", (260, 260), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
            combined_img = np.hstack((frame, dashboard))
            if args.scale != 1.0 and args.scale > 0:
                h_new = int(combined_img.shape[0] * args.scale)
                w_new = int(combined_img.shape[1] * args.scale)
                combined_img = cv2.resize(combined_img, (w_new, h_new))
            cv2.imshow("DMS - Drowsiness Detection Dashboard", combined_img)
            cv2.waitKey(30)
            continue
        
        # Biến lưu trữ kết quả nhận diện
        detected_face = False
        ear = 0.3
        mar = 0.2
        pitch, yaw, roll = 0.0, 0.0, 0.0
        status_text = "TINH TAO"
        status_color = (0, 255, 0)
        
        # 3. Phân tích hình ảnh
        if not simulated_mode:
            # Tự động phát hiện ảnh đơn sắc (grayscale/monochrome)
            is_mono = False
            if frame is not None and len(frame.shape) == 3:
                # Downsample nhỏ để kiểm tra kênh nhanh tránh ảnh hưởng FPS
                small_frame = cv2.resize(frame, (64, 48))
                b, g, r = cv2.split(small_frame)
                diff_bg = np.max(np.abs(b.astype(np.int16) - g.astype(np.int16)))
                diff_gr = np.max(np.abs(g.astype(np.int16) - r.astype(np.int16)))
                if diff_bg < 5 and diff_gr < 5:
                    is_mono = True
            
            # Tính độ tương phản (độ lệch chuẩn của độ sáng) để tự động kích hoạt khi trời tối/thiếu sáng
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            contrast = gray.std()
            
            # Quyết định có áp dụng tăng cường độ tương phản CLAHE không
            should_enhance = args.enhance or (
                not args.no_enhance and (is_mono or contrast < 35.0)
            )
            
            if should_enhance:
                try:
                    # Chuyển sang không gian màu LAB để áp dụng CLAHE lên kênh độ sáng L (tránh đổi màu nếu là ảnh màu)
                    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
                    l, a, b = cv2.split(lab)
                    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
                    cl = clahe.apply(l)
                    enhanced_bgr = cv2.cvtColor(cv2.merge((cl, a, b)), cv2.COLOR_LAB2BGR)
                    img_rgb_detect = cv2.cvtColor(enhanced_bgr, cv2.COLOR_BGR2RGB)
                    
                    # Nếu cấu hình hiển thị ảnh tăng cường hoặc đang dùng cam đen trắng/hồng ngoại
                    # hiển thị ảnh tăng cường giúp người lái xe theo dõi dễ hơn trên dashboard
                    if args.show_enhanced or is_mono:
                        frame = enhanced_bgr
                except Exception as e:
                    img_rgb_detect = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            else:
                img_rgb_detect = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                
            results = face_mesh.process(img_rgb_detect)
            
            if results.multi_face_landmarks:
                detected_face = True
                face_landmarks = results.multi_face_landmarks[0].landmark
                
                # Tính toán EAR
                ear_l = calculate_ear(LEFT_EYE, face_landmarks, w, h)
                ear_r = calculate_ear(RIGHT_EYE, face_landmarks, w, h)
                ear = (ear_l + ear_r) / 2.0
                
                # Tính toán MAR
                mar = calculate_mar(face_landmarks, w, h)
                
                # Tính toán Head Pose
                try:
                    pitch, yaw, roll, rvec, tvec, cam_matrix, dist_coeffs, img_pts = estimate_head_pose(face_landmarks, w, h)
                    
                    # Vẽ trục tọa độ 3D trên mũi lái xe
                    axis_points = np.array([(100.0, 0.0, 0.0), (0.0, 100.0, 0.0), (0.0, 0.0, 100.0)], dtype=np.float32)
                    proj_pts, _ = cv2.projectPoints(axis_points, rvec, tvec, cam_matrix, dist_coeffs)
                    nose_tip = (int(img_pts[0][0]), int(img_pts[0][1]))
                    cv2.line(frame, nose_tip, (int(proj_pts[0][0][0]), int(proj_pts[0][0][1])), (0, 0, 255), 2)  # Trục X - Đỏ (Pitch)
                    cv2.line(frame, nose_tip, (int(proj_pts[1][0][0]), int(proj_pts[1][0][1])), (0, 255, 0), 2)  # Trục Y - Xanh lá (Yaw)
                    cv2.line(frame, nose_tip, (int(proj_pts[2][0][0]), int(proj_pts[2][0][1])), (255, 0, 0), 2)  # Trục Z - Xanh dương (Roll)
                except Exception as e:
                    pass
                
                # Vẽ điểm mốc mắt và miệng lên frame
                for idx in LEFT_EYE + RIGHT_EYE:
                    pt = face_landmarks[idx]
                    cv2.circle(frame, (int(pt.x * w), int(pt.y * h)), 2, (0, 255, 255), -1)
                for pair in [LIP_A, LIP_B, LIP_C, LIP_D]:
                    pt1 = face_landmarks[pair[0]]
                    pt2 = face_landmarks[pair[1]]
                    cv2.circle(frame, (int(pt1.x * w), int(pt1.y * h)), 2, (255, 0, 255), -1)
                    cv2.circle(frame, (int(pt2.x * w), int(pt2.y * h)), 2, (255, 0, 255), -1)
        else:
            # Chế độ GIẢ LẬP: Tạo dữ liệu thay đổi theo thời gian
            detected_face = True
            sim_elapsed = time.time() - sim_time_start
            
            # Kịch bản giả lập tuần hoàn 60 giây:
            # 0 - 20s: Tỉnh táo (EAR~0.3, MAR~0.15, Pitch~0)
            # 20 - 30s: Bắt đầu ngáp (MAR tăng lên 0.7 trong vài giây)
            # 30 - 45s: Nhắm mắt dài & Cúi đầu (EAR giảm về 0.08, Pitch tăng về 25 độ)
            # 45 - 60s: Hồi phục tỉnh táo
            cycle_time = sim_elapsed % 60.0
            
            if cycle_time < 20.0:
                # Tỉnh táo
                ear = np.random.uniform(0.28, 0.32)
                # Đôi khi chớp mắt nhanh
                if int(cycle_time * 2) % 10 == 0:
                    ear = 0.08
                mar = np.random.uniform(0.12, 0.18)
                pitch = np.random.uniform(-2.0, 2.0)
            elif cycle_time < 30.0:
                # Ngáp
                ear = np.random.uniform(0.25, 0.30)
                # MAR tăng mô phỏng ngáp
                if 22.0 < cycle_time < 26.0:
                    mar = 0.75
                else:
                    mar = np.random.uniform(0.15, 0.25)
                pitch = np.random.uniform(2.0, 8.0)
            elif cycle_time < 45.0:
                # Ngủ gật / Microsleep
                # EAR giảm sâu, mắt nhắm
                ear = np.random.uniform(0.06, 0.12)
                mar = np.random.uniform(0.1, 0.18)
                # Đầu cúi xuống dần
                pitch = np.linspace(5.0, 28.0, 15)[int(cycle_time - 30)] + np.random.uniform(-1.0, 1.0)
            else:
                # Hồi phục
                ear = np.random.uniform(0.26, 0.31)
                mar = np.random.uniform(0.15, 0.20)
                pitch = np.random.uniform(-2.0, 2.0)
                
            yaw = np.random.uniform(-2.0, 2.0)
            roll = np.random.uniform(-2.0, 2.0)
            
            # Vẽ khuôn mặt giả lập cử động mắt/miệng để trực quan hóa
            face_color = (0, 255, 0) if ear > 0.15 else (0, 0, 255)
            # Đầu
            cv2.ellipse(frame, (320, 200), (80, 110), int(roll), 0, 360, face_color, 2)
            # Mắt trái
            eye_h = int(15 * (ear / 0.3))
            cv2.ellipse(frame, (285, 180), (15, max(2, eye_h)), 0, 0, 360, (255, 255, 255), -1)
            cv2.circle(frame, (285, 180), 4, (120, 0, 0), -1)
            # Mắt phải
            cv2.ellipse(frame, (355, 180), (15, max(2, eye_h)), 0, 0, 360, (255, 255, 255), -1)
            cv2.circle(frame, (355, 180), 4, (120, 0, 0), -1)
            # Miệng
            mouth_w = 20
            mouth_h = int(25 * (mar / 0.7))
            cv2.ellipse(frame, (320, 250), (mouth_w, max(2, mouth_h)), 0, 0, 360, (0, 0, 255), -1)

        # 4. Quản lý trạng thái khởi động & xác thực (VNeID -> Telegram & Session -> Face Scan -> Active)
        if auth_state == "VNEID_REQ":
            alarm_level = 4  # 2 âm còi liên tiếp, 3s sau kêu tiếp
            if not vneid_prompt_played:
                play_voice_prompt_async("req_vneid")
                vneid_prompt_played = True
                print("[AUTH] Đã phát âm thanh yêu cầu xác thực thẻ VNeID / CCCD.")

            # 1. Tự động quét mã QR cực nhanh (2ms) trong luồng camera
            if not vneid_auth_trigger and not is_processing_vneid:
                scan_res = quick_scan_qr(frame)
                if scan_res and scan_res.get("success"):
                    driver_info["vneid"] = scan_res["vneid"]
                    driver_info["name"] = scan_res["name"]
                    print(f"[AUTH SCANNER] Đã tự động nhận diện thẻ CCCD ({scan_res['method']}): {driver_info['vneid']} - {driver_info['name']}")
                    vneid_auth_trigger = True

            # Hiển thị giao diện thông báo Yêu cầu VNeID trên camera
            cv2.rectangle(frame, (30, 30), (w - 30, 160), (0, 0, 0), -1)
            cv2.rectangle(frame, (30, 30), (w - 30, 160), (0, 255, 255), 2)
            if is_processing_vneid:
                cv2.putText(frame, "[ CHUP ANH OK - DANG XU LY TOI DA 3 GIAY... ]", (50, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 255, 0), 2, cv2.LINE_AA)
                cv2.putText(frame, "Anh da luu; ket qua se cap nhat len Web Dashboard.", (50, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)
            else:
                cv2.putText(frame, "YEU CAU XAC THUC THE VNeID / CCCD", (50, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2, cv2.LINE_AA)
                cv2.putText(frame, "Dua the CCCD truoc camera hoac bam 'V' de xac thuc", (50, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
                cv2.putText(frame, "[Gio the CCCD / Nhan 'V' / Click nut GUI de xac thuc]", (50, 135), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 0), 1, cv2.LINE_AA)

            # Tín hiệu khi ấn nút bấm (TỨC THÌ 0ms - đNy vào luồng ngầm)
            if vneid_auth_trigger and not is_processing_vneid:
                vneid_auth_trigger = False
                is_processing_vneid = True
                play_pc_beep_single()
                print("[INSTANT CAPTURE] Lập tức chụp ảnh và đẩy vào luồng xử lý ngầm!")
                snapshot_frame = frame.copy()
                threading.Thread(target=process_vneid_async, args=(snapshot_frame,), daemon=True).start()

        elif auth_state == "FACE_REQ":
            alarm_level = 5  # 1 âm còi ngắt quãng mỗi 3s
            if not face_prompt_played:
                play_voice_prompt_async("req_face")
                face_prompt_played = True
                print("[AUTH] Đã phát âm thanh yêu cầu xác thực khuôn mặt.")

            if detected_face and not calibrated:
                calib_count += 1
                calib_ears.append(ear)
                calib_mars.append(mar)
                calib_pitches.append(pitch)
                calib_yaws.append(yaw)
                calib_rolls.append(roll)

                cv2.rectangle(frame, (30, 390), (w - 30, 445), (0, 0, 0), -1)
                progress = int((calib_count / calib_frames) * (w - 80))
                cv2.rectangle(frame, (40, 400), (40 + progress, 435), (0, 255, 255), -1)
                cv2.rectangle(frame, (40, 400), (w - 40, 435), (255, 255, 255), 1)
                cv2.putText(frame, f"DANG QUET XAC THUC KHUON MAT... {int((calib_count/calib_frames)*100)}%", (50, 423), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 0, 0) if progress > 200 else (255, 255, 255), 2)

                if calib_count >= calib_frames:
                    ear_baseline = np.mean(calib_ears)
                    mar_baseline = np.mean(calib_mars)
                    pitch_baseline = np.mean(calib_pitches)
                    yaw_baseline = np.mean(calib_yaws)
                    roll_baseline = np.mean(calib_rolls)
                    ear_limit = max(0.20, min(0.24, ear_baseline * 0.80))
                    calibrated = True
                    alarm_level = 0
                    play_voice_prompt_async("face_success")
                    auth_state = "ACTIVE"
                    print("====================================================")
                    print("[SUCCESS] Xác thực khuôn mặt hoàn tất! Da khoi tao phien va chuyen sang giam sat tinh tao.")
                    print(f"EAR Baseline: {ear_baseline:.3f} | Nguong nham mat (EAR Limit): {ear_limit:.3f}")
                    print(f"MAR Baseline: {mar_baseline:.3f}")
                    print(f"Pitch Baseline: {pitch_baseline:.3f} | Yaw Baseline: {yaw_baseline:.3f} | Roll Baseline: {roll_baseline:.3f}")
                    print("====================================================")
            else:
                cv2.rectangle(frame, (30, 390), (w - 30, 445), (0, 0, 0), -1)
                cv2.putText(frame, "YEU CAU XAC THUC KHUON MAT: Vui long nhin vao camera", (40, 423), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 255), 1, cv2.LINE_AA)

        # 5. Phân tích thời gian thực khi đã xác thực và hiệu chuẩn hoàn tất (ACTIVE)
        elif auth_state == "ACTIVE" and detected_face and calibrated:
            # Reset bộ đếm mất dấu khuôn mặt
            face_lost_start_time = None
            
            # --- Đếm Chớp Mắt & Ngáp dựa trên ngưỡng động đã hiệu chuẩn ---
            is_eye_closed = ear < ear_limit
            if is_eye_closed:
                if eye_closed_start_time is None:
                    eye_closed_start_time = time.time()
                eye_closed_duration = time.time() - eye_closed_start_time
            else:
                eye_closed_start_time = None
                eye_closed_duration = 0.0
                eye_closed_1s_logged = False
                eye_closed_3s_logged = False
                
            if is_eye_closed and not eye_previously_closed:
                # Bắt đầu nhắm mắt
                eye_previously_closed = True
            elif not is_eye_closed and eye_previously_closed:
                # Mở mắt trở lại -> Xác nhận 1 lần chớp mắt
                blink_timestamps.append(time.time())
                eye_previously_closed = False
            
            # Đếm ngáp (MAR > 0.6 duy trì trên 1.5 giây)
            is_yawning = mar > 0.60
            if is_yawning and not mouth_previously_yawning:
                mouth_previously_yawning = True
                yawn_start_time = time.time()
            elif not is_yawning and mouth_previously_yawning:
                if yawn_start_time and (time.time() - yawn_start_time) >= 1.5:
                    yawn_timestamps.append(time.time())
                    session_yawn_count += 1
                    trigger_voice_alert("alert_yawn")
                    try:
                        from telegram_bot import send_telegram_alert_async
                        send_telegram_alert_async("🥱 CẢNH BÁO MỆT MỎI: Tài xế vừa ngáp dài (>1.5s)!", frame)
                    except Exception:
                        pass
                mouth_previously_yawning = False
                yawn_start_time = None
            
            # Lưu vết trạng thái nhắm mắt vào deque để tính PERCLOS thời gian thực không trễ
            eye_closed_frames.append(1 if is_eye_closed else 0)
            
            # --- TÍNH TOÁN CÁC CHỈ SỐ Ở TỪNG FRAME ĐỂ CẢNH BÁO TỨC THÌ ---
            ear_norm_instant = max(0.0, min(1.0, 1.0 - (ear / ear_baseline)))
            mar_norm_instant = max(0.0, min(1.0, (mar - mar_baseline) / (0.60 - mar_baseline + 1e-6)))
            
            # Đo độ lệch tư thế đầu (Forward/Backward/Sideways) so với baseline bằng khoảng cách góc ngắn nhất
            pitch_dev = abs((pitch - pitch_baseline + 180) % 360 - 180)
            yaw_dev = abs((yaw - yaw_baseline + 180) % 360 - 180)
            roll_dev = abs((roll - roll_baseline + 180) % 360 - 180)
            
            # Lệch đầu nghiêm trọng: Pitch lệch > 25 độ (cúi/ngửa), Roll lệch > 25 độ (nghiêng), hoặc Yaw lệch > 30 độ (quay)
            is_head_tilted = pitch_dev > 25.0 or roll_dev > 25.0 or yaw_dev > 30.0
            if is_head_tilted:
                if head_tilted_start_time is None:
                    head_tilted_start_time = time.time()
                head_tilted_duration = time.time() - head_tilted_start_time
            else:
                head_tilted_start_time = None
                head_tilted_duration = 0.0
            
            # pose_norm kết hợp Pitch (cúi/ngửa), Yaw (quay đầu), Roll (nghiêng đầu)
            # Lệch nguy hiểm: Pitch 25 độ, Yaw 30 độ, Roll 25 độ
            pose_norm_instant = max(pitch_dev / 25.0, yaw_dev / 30.0, roll_dev / 25.0)
            pose_norm_instant = max(0.0, min(1.0, pose_norm_instant))
            
            # Tính PERCLOS thời gian thực dựa trên 5 giây gần nhất (~150 frames)
            perclos_instant = np.mean(eye_closed_frames) * 100.0 if eye_closed_frames else 0.0
            perclos = perclos_instant
            perclos_norm_instant = min(perclos_instant / 40.0, 1.0)
            
            # Tần suất chớp mắt & ngáp trong 60 giây gần nhất
            current_time = time.time()
            while blink_timestamps and current_time - blink_timestamps[0] > 60.0:
                blink_timestamps.popleft()
            while yawn_timestamps and current_time - yawn_timestamps[0] > 60.0:
                yawn_timestamps.popleft()
                
            blink_rate = len(blink_timestamps)
            yawn_count = len(yawn_timestamps)
            
            blink_norm = max(0.0, min(1.0, 1.0 - (blink_rate / 15.0)))
            yawn_norm = min(yawn_count / 3.0, 1.0)
            
            # --- Tính Chỉ Số Mệt Mỏi Fatigue Score (FS) tức thì ---
            fatigue_score = (
                0.30 * ear_norm_instant + 
                0.25 * perclos_norm_instant + 
                0.15 * pose_norm_instant + 
                0.10 * mar_norm_instant + 
                0.10 * blink_norm + 
                0.10 * yawn_norm
            )
            fatigue_score = max(0.0, min(1.0, fatigue_score))
            
            # Ghi dữ liệu frame hiện tại vào buffer 1 giây
            frame_buffer.append({
                'ear': ear,
                'mar': mar,
                'pitch': pitch,
                'closed': 1 if is_eye_closed else 0
            })
            
            # --- Xử lý định kỳ mỗi 1.0 giây (Tạo điểm dữ liệu chuỗi thời gian cho LSTM và SQLite) ---
            if current_time - last_second_time >= 1.0:
                last_second_time = current_time
                if frame_buffer:
                    ear_avg = np.mean([f['ear'] for f in frame_buffer])
                    mar_avg = np.mean([f['mar'] for f in frame_buffer])
                    pitch_avg = np.mean([f['pitch'] for f in frame_buffer])
                    closed_ratio = np.mean([f['closed'] for f in frame_buffer])
                    
                    ear_norm_db = max(0.0, min(1.0, 1.0 - (ear_avg / ear_baseline)))
                    mar_norm_db = max(0.0, min(1.0, (mar_avg - mar_baseline) / (0.60 - mar_baseline + 1e-6)))
                    pitch_norm_db = max(0.0, min(1.0, abs((pitch_avg - pitch_baseline + 180) % 360 - 180) / 25.0))
                    
                    history_window.append([ear_norm_db, mar_norm_db, pitch_norm_db, closed_ratio])
                    
                    # Chạy dự báo LSTM mỗi 1.0 giây
                    if model_loaded and len(history_window) == 60:
                        seq_data = np.array(history_window, dtype=np.float32)
                        seq_tensor = torch.from_numpy(seq_data).unsqueeze(0).to(device)
                        with torch.no_grad():
                            prob = lstm_model(seq_tensor).item()
                        lstm_risk = prob * 100.0
                    else:
                        lstm_risk = fatigue_score * 100.0
                    
                    # Lưu trữ lịch sử dữ liệu vào SQLite mỗi 1.0 giây
                    try:
                        timestamp_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_time))
                        db_cursor.execute("""
                            INSERT OR REPLACE INTO dms_logs (timestamp, ear, mar, pitch, yaw, roll, risk)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (timestamp_str, float(ear_avg), float(mar_avg), float(pitch_avg), float(yaw), float(roll), float(lstm_risk)))
                        db_conn.commit()
                    except:
                        pass
                    
                    # Xóa bộ đệm giây cũ
                    frame_buffer.clear()
            
            fatigue_scores_history.append(fatigue_score)
            
            # --- Phân Loại & Kích Hoạt Cảnh Báo Đa Tầng ---
            if fatigue_score < 0.4:
                status_text = "TINH TAO"
                status_color = (0, 255, 0)  # Green
                alarm_level = 0
            elif fatigue_score < 0.6:
                status_text = "MET MOI NHE"
                status_color = (0, 255, 255)  # Yellow
                alarm_level = 1
            elif fatigue_score < 0.8:
                status_text = "DE NGHI NGHI NGOI"
                status_color = (0, 165, 255)  # Orange
                alarm_level = 2
            else:
                status_text = "NGUY HIEM - NGU GAT!"
                status_color = (0, 0, 255)  # Red
                alarm_level = 3
                
            # Đè cảnh báo khẩn cấp tức thì (Overrides & Đếm sự kiện hành trình)
            if eye_closed_duration >= 3.0:
                status_text = "NGUY HIEM - NHAM MAT 3S!"
                status_color = (0, 0, 255)  # Red
                alarm_level = 3
                fatigue_score = 1.0
                trigger_voice_alert("alert_danger")
                if not eye_closed_3s_logged:
                    session_drowsiness_count += 1
                    eye_closed_3s_logged = True
                    aud_file = None
                    try:
                        from audio_manager import record_event_audio
                        aud_file = record_event_audio("drowsiness_3s", 10)
                    except Exception:
                        pass
                    try:
                        from telegram_bot import send_telegram_alert_async
                        send_telegram_alert_async("CẢNH BÁO NGUY HIỂM: Tài xế nhắm mắt liên tục 3s (Microsleep)!", frame)
                    except Exception:
                        pass
            elif eye_closed_duration >= 1.5:
                status_text = "CANH BAO - NHAM MAT 1.5S!"
                status_color = (0, 165, 255)  # Orange
                alarm_level = 2
                fatigue_score = max(fatigue_score, 0.85)
                trigger_voice_alert("alert_drowsy")
                if not eye_closed_1s_logged:
                    eye_closed_1s_logged = True
                    try:
                        from telegram_bot import send_telegram_alert_async
                        send_telegram_alert_async("⚠️ CẢNH BÁO: Tài xế nhắm mắt 1.5s!", frame)
                    except Exception:
                        pass
                
            if head_tilted_duration >= 1.0:
                status_text = "NGUY HIEM - LECH DAU!"
                status_color = (0, 0, 255)  # Red
                alarm_level = 3
                fatigue_score = max(fatigue_score, 0.95)
                
            # Theo dõi đếm sự kiện mất tập trung khi lệch đầu
            if head_tilted_duration >= 1.5:
                trigger_voice_alert("alert_distracted")
                if not distraction_logged:
                    session_distraction_count += 1
                    distraction_logged = True
                    try:
                        from telegram_bot import send_telegram_alert_async
                        send_telegram_alert_async("⚠️ CẢNH BÁO MẤT TẬP TRUNG: Tài xế ngoảnh mặt / lệch đầu rời tầm nhìn đường (>1.5s)!", frame)
                    except Exception:
                        pass
            elif not is_head_tilted and detected_face:
                distraction_logged = False

            # Nếu người lái xe trở lại trạng thái bình thường (mắt mở, đầu thẳng, không ngáp)
            # thì bắt buộc dừng còi báo ngay lập tức.
            if not is_eye_closed and not is_head_tilted and not is_yawning:
                alarm_level = 0
                status_text = "TINH TAO"
                status_color = (0, 255, 0)
                
            # Cập nhật thông tin hành trình vào cơ sở dữ liệu định kỳ mỗi 5 giây
            if current_time - last_db_save_time >= 5.0:
                last_db_save_time = current_time
                save_session_to_db()
                
            # Đèn báo viền đỏ nhấp nháy trên màn hình camera nếu mệt mỏi nặng
            if alarm_level >= 2:
                border_color = (0, 0, 255) if int(time.time() * 5) % 2 == 0 else (0, 0, 0)
                cv2.rectangle(frame, (0, 0), (w, h), border_color, 12)
                
                # Giả lập Cấp 4: Gửi dữ liệu về trung tâm
                if fatigue_score > 0.95:
                    cv2.putText(frame, "[SENDING EMERGENCY CENTRAL SMS]", (10, 40), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2, cv2.LINE_AA)
                                
        # 5b. Xử lý trường hợp mất dấu khuôn mặt (Gục đầu hoặc ngửa đầu ra sau quá giới hạn camera)
        elif not detected_face and calibrated:
            # Theo dõi thời gian mất dấu khuôn mặt
            if face_lost_start_time is None:
                face_lost_start_time = time.time()
                
            elapsed_lost = time.time() - face_lost_start_time
            if elapsed_lost >= 1.5:
                # Đã mất dấu lâu hơn 1.5 giây -> Cảnh báo khẩn cấp ngay lập tức & Đếm 1 lần mất tập trung
                status_text = "NGUY HIEM - MAT DAU!"
                status_color = (0, 0, 255)
                alarm_level = 3 # Báo động nguy hiểm
                fatigue_score = 1.0 # Force full score trên UI
                lstm_risk = 100.0
                fatigue_scores_history.append(fatigue_score)
                trigger_voice_alert("alert_distracted")
                
                if not distraction_logged:
                    session_distraction_count += 1
                    distraction_logged = True
                
                # Vẽ viền đỏ nhấp nháy khẩn cấp
                border_color = (0, 0, 255) if int(time.time() * 5) % 2 == 0 else (0, 0, 0)
                cv2.rectangle(frame, (0, 0), (w, h), border_color, 12)
                cv2.putText(frame, "WARNING: FACE LOST", (130, h // 2), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 3, cv2.LINE_AA)
            else:
                status_text = "DISTRACTED/LOST..."
                status_color = (0, 165, 255) # Orange
                alarm_level = 1
                
                # Giả lập Cấp 4: Gửi dữ liệu về trung tâm
                if fatigue_score > 0.95:
                    cv2.putText(frame, "[SENDING EMERGENCY CENTRAL SMS]", (10, 40), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2, cv2.LINE_AA)
            
        else:
            # Chưa hiệu chuẩn xong và chưa phát hiện khuôn mặt (hiển thị giao diện hướng dẫn)
            alarm_level = 0
            cv2.putText(dashboard, "BASELINING INITIAL STATE...", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            cv2.putText(frame, "PLEASE LOOK STRAIGHT AT CAMERA", (100, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

        # --- Vẽ Dashboard Thống Kê Giao Diện Đẹp ---
        cv2.putText(dashboard, "DRIVER MONITORING SYSTEM", (20, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 255, 255), 2, cv2.LINE_AA)
        cv2.line(dashboard, (15, 30), (305, 30), (100, 100, 100), 1)

        if auth_state == "VNEID_REQ":
            cv2.putText(dashboard, "TRANG THAI: CHO VNeID", (20, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(dashboard, f" Tai xe: {driver_info['name']}", (20, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
            cv2.putText(dashboard, f" VNeID: {driver_info['vneid']}", (20, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 200), 1)
            cv2.putText(dashboard, f" Bang lai: {driver_info['license_class']}", (20, 115), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
            cv2.line(dashboard, (15, 128), (305, 128), (100, 100, 100), 1)
            cv2.putText(dashboard, " AM THANH THONG BAO:", (20, 148), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (255, 255, 0), 1)
            cv2.putText(dashboard, " - Phat tieng giong noi thong bao", (20, 168), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (200, 200, 200), 1)
            cv2.putText(dashboard, " - Coi 2 tieng lien tiep (lap 3s)", (20, 188), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 255), 1)
            
            # Interactive Button
            cv2.rectangle(dashboard, (15, 422), (305, 458), (0, 200, 255), -1)
            cv2.rectangle(dashboard, (15, 422), (305, 458), (255, 255, 255), 1)
            cv2.putText(dashboard, "[ XAC THUC VNeID (Phim V) ]", (24, 444), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 2, cv2.LINE_AA)
            cv2.putText(dashboard, "Click nut tren hoac nhan 'v' de quet card", (15, h - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (140, 140, 140), 1)

        elif auth_state == "FACE_REQ":
            cv2.putText(dashboard, "VNeID: DA XAC THUC OK", (20, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (0, 255, 0), 2, cv2.LINE_AA)
            cv2.putText(dashboard, f" Tai xe: {driver_info['name']}", (20, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
            cv2.putText(dashboard, f" Session ID: #{current_session_id or 'moi'}", (20, 92), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 0), 1)
            cv2.line(dashboard, (15, 105), (305, 105), (100, 100, 100), 1)
            cv2.putText(dashboard, " XAC THUC KHUON MAT:", (20, 125), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 2, cv2.LINE_AA)
            cv2.putText(dashboard, " - Phat tieng giong noi thong bao", (20, 145), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (200, 200, 200), 1)
            cv2.putText(dashboard, " - Coi 1 tieng ngat quang 3s/lan", (20, 165), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 255), 1)
            
            draw_bar(dashboard, "Face Baseline Scan", calib_count / calib_frames, 1.0, 20, 195, 280, 12, (0, 255, 255))
            
            # Button: cho phép bắt đầu lại việc quét mặt, không yêu cầu quét lại VNeID.
            cv2.rectangle(dashboard, (15, 422), (305, 458), (255, 140, 0), -1)
            cv2.rectangle(dashboard, (15, 422), (305, 458), (255, 255, 255), 1)
            cv2.putText(dashboard, "[ QUET LAI KHUON MAT ]", (35, 444), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (0, 0, 0), 2, cv2.LINE_AA)
            cv2.putText(dashboard, "Click nut hoac nhan 'r' de quet lai mat", (15, h - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (140, 140, 140), 1)

        elif auth_state == "ACTIVE":
            # 2. Tiến trình hành trình lái xe (Driving Session Info)
            if session_start_time is not None:
                elapsed_sec = int(time.time() - session_start_time)
                hrs = elapsed_sec // 3600
                mins = (elapsed_sec % 3600) // 60
                secs = elapsed_sec % 60
                time_str = f"{hrs:02d}:{mins:02d}:{secs:02d}"
            else:
                time_str = "00:00:00"
                
            cv2.putText(dashboard, "TIEN TRINH LAI XE (SESSION):", (20, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 0), 1, cv2.LINE_AA)
            cv2.putText(dashboard, f" Thoi gian: {time_str}", (20, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (255, 255, 255), 1, cv2.LINE_AA)
            
            dis_color = (0, 0, 255) if session_distraction_count > 0 else (0, 255, 0)
            drow_color = (0, 0, 255) if session_drowsiness_count > 0 else (0, 255, 0)
            cv2.putText(dashboard, f" Mat tap trung: {session_distraction_count} lan", (20, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.43, dis_color, 1, cv2.LINE_AA)
            cv2.putText(dashboard, f" Ngu ngan / Ngap: {session_drowsiness_count} / {session_yawn_count} lan", (20, 99), cv2.FONT_HERSHEY_SIMPLEX, 0.43, drow_color, 1, cv2.LINE_AA)
            cv2.line(dashboard, (15, 108), (305, 108), (100, 100, 100), 1)
            
            # 3. Chỉ số mốc mắt & miệng
            draw_bar(dashboard, "EAR (Eye Opening)", ear, 0.35, 20, 126, 280, 12, (255, 255, 0))
            draw_bar(dashboard, "MAR (Mouth Opening)", mar, 0.8, 20, 158, 280, 12, (255, 0, 255))
            
            # 4. Góc đầu
            cv2.putText(dashboard, "Head Pose angles (deg):", (20, 185), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (200, 200, 200), 1, cv2.LINE_AA)
            pitch_dev_disp = abs((pitch - pitch_baseline + 180) % 360 - 180)
            yaw_dev_disp = abs((yaw - yaw_baseline + 180) % 360 - 180)
            roll_dev_disp = abs((roll - roll_baseline + 180) % 360 - 180)
            cv2.putText(dashboard, f" Pitch: {pitch:.1f}", (20, 201), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0) if pitch_dev_disp < 25.0 else (0, 0, 255), 1)
            cv2.putText(dashboard, f" Yaw: {yaw:.1f}", (115, 201), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0) if yaw_dev_disp < 30.0 else (0, 0, 255), 1)
            cv2.putText(dashboard, f" Roll: {roll:.1f}", (205, 201), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0) if roll_dev_disp < 25.0 else (0, 0, 255), 1)
            
            # 5. PERCLOS & Blinks
            try:
                perclos_val = perclos
            except NameError:
                perclos_val = 0.0
            try:
                br_val = blink_rate
            except NameError:
                br_val = 15
            try:
                yc_val = yawn_count
            except NameError:
                yc_val = 0
                
            draw_bar(dashboard, "PERCLOS (Eye Closed %)", perclos_val, 50.0, 20, 226, 280, 12, (0, 120, 255))
            cv2.putText(dashboard, f"Blink: {br_val}/min | Yawn: {yc_val}/min", (20, 254), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1)
            cv2.line(dashboard, (15, 264), (305, 264), (100, 100, 100), 1)
            
            # 6. Fatigue Score & Status Box
            draw_bar(dashboard, "FATIGUE SCORE (FS)", fatigue_score, 1.0, 20, 282, 280, 15, status_color)
            
            cv2.rectangle(dashboard, (15, 312), (305, 355), status_color, 2)
            cv2.putText(dashboard, status_text, (25, 338), cv2.FONT_HERSHEY_SIMPLEX, 0.50, status_color, 2, cv2.LINE_AA)
            
            # 7. SQLite Session log status & LSTM Prediction
            if current_session_id is not None:
                cv2.putText(dashboard, f"SQLite Logged (Session #{current_session_id})", (20, 368), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1, cv2.LINE_AA)
            
            if model_loaded:
                seq_len_current = len(history_window)
                if seq_len_current < 60:
                    lstm_text = f"LSTM Loading: {seq_len_current}/60s"
                    lstm_color = (150, 150, 150)
                else:
                    lstm_text = f"Microsleep Risk: {lstm_risk:.1f}%"
                    lstm_color = (0, 0, 255) if lstm_risk > 70 else (0, 255, 0)
                cv2.putText(frame, lstm_text, (10, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, lstm_color, 2, cv2.LINE_AA)
            
            # 8. Nút bấm Hiệu chuẩn lại (Interactive UI Button)
            btn_bg = (255, 140, 0) if not recalibrate_requested else (0, 255, 255)
            cv2.rectangle(dashboard, (15, 422), (305, 458), btn_bg, -1)
            cv2.rectangle(dashboard, (15, 422), (305, 458), (255, 255, 255), 1)
            cv2.putText(dashboard, "[ RE-CALIBRATE / QUET LAI ]", (28, 444), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 2, cv2.LINE_AA)
            
            # Hướng dẫn phím bấm
            cv2.putText(dashboard, "Click nut tren hoac nhan 'r' de quet lai", (15, h - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (140, 140, 140), 1, cv2.LINE_AA)
            
        # 6. Ghép khung hình camera với Dashboard thống kê
        combined_img = np.hstack((frame, dashboard))
        
        if args.scale != 1.0 and args.scale > 0:
            h_new = int(combined_img.shape[0] * args.scale)
            w_new = int(combined_img.shape[1] * args.scale)
            combined_img = cv2.resize(combined_img, (w_new, h_new))
            
        # Hiển thị giao diện chính
        cv2.imshow("DMS - Drowsiness Detection Dashboard", combined_img)

        # Cập nhật màn hình OLED I2C (128x64)
        if oled.is_available():
            oled_auth = auth_state if auth_state != "ACTIVE" else "MONITORING"
            oled.update_data(
                auth_state=oled_auth,
                alarm_level=alarm_level,
                ear=ear if 'ear' in locals() else 0.0,
                mar=mar if 'mar' in locals() else 0.0,
                fps=30.0,
                fatigue_score=int(fatigue_score * 100) if 'fatigue_score' in locals() else 0,
                driver_name=driver_info.get("name", ""),
                status_text=status_text if 'status_text' in locals() else "BINH THUONG"
            )
        
        # v/chạm nút: chụp VNeID ở bước đầu; r/chạm nút: quét lại mặt sau khi VNeID thành công.
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key in (ord('v'), ord('V'), ord('c'), 32, 13):
            print("[KEYBOARD TRIGGER] Nhận lệnh quét.")
            handle_scan_button()
        elif key == ord('r') or recalibrate_requested:
            recalibrate_requested = False
            try:
                from audio_manager import record_event_audio
                record_event_audio("reset_recalibrate", 10)
            except Exception:
                pass
            if vneid_authenticated:
                print("[INFO] Nhấn 'r': Yêu cầu quét lại khuôn mặt.")
                restart_face_scan()
            else:
                print("[INFO] Nhấn 'r' khi chưa xác thực VNeID: chụp CCCD/VNeID.")
                handle_scan_button()

    # Lưu và in báo cáo kết thúc hành trình
    if current_session_id is not None or session_start_time is not None:
        save_session_to_db()
        print("====================================================")
        print("[SESSION REPORT] THONG KE TIEN TRINH HÀNH TRÌNH LAI XE:")
        if current_session_id:
            print(f"- Ma phien (Session ID)  : #{current_session_id}")
        print(f"- Thoi gian bat dau      : {session_start_str}")
        if session_start_time:
            elapsed_sec = int(time.time() - session_start_time)
            hrs = elapsed_sec // 3600
            mins = (elapsed_sec % 3600) // 60
            secs = elapsed_sec % 60
            print(f"- Tong thoi gian di      : {hrs:02d}:{mins:02d}:{secs:02d} ({elapsed_sec} giây)")
        print(f"- So lan mat tap trung   : {session_distraction_count} lan")
        print(f"- So lan ngu ngan (3s)   : {session_drowsiness_count} lan")
        print(f"- So lan ngap            : {session_yawn_count} lan")
        if fatigue_scores_history:
            print(f"- Diem met moi trung binh: {np.mean(fatigue_scores_history):.2f}")
            print(f"- Diem met moi cao nhat  : {np.max(fatigue_scores_history):.2f}")
        print("====================================================")

    # Giải phóng tài nguyên
    if oled.is_available():
        oled.stop()
    if cap is not None:
        cap.release()
    cv2.destroyAllWindows()
    if GPIO_AVAILABLE:
        try:
            GPIO.cleanup()
            print("[INFO] Da giai phong chan GPIO Raspberry Pi.")
        except:
            pass
    try:
        db_conn.close()
        print("[INFO] Da dong ket noi SQLite database.")
    except:
        pass
    print("[INFO] Da dung va dong ung dung.")

if __name__ == "__main__":
    main()
