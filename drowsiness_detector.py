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
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False

# Cấu hình cổng vật lý trên Raspberry Pi (nếu chạy trên Ubuntu Pi)
MOTOR_PIN = 17  # Chân GPIO 17 điều khiển động cơ rung
BUZZER_PIN = 27 # Chân GPIO 27 điều khiển còi chíp vật lý

import sqlite3
from collections import deque
from lstm_model import DrowsinessLSTM


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
alarm_level = 0  # 0: bình thường, 1: mệt nhẹ/hiệu chuẩn (beep chậm), 2: mệt vừa (beep chậm + rung), 3: nguy hiểm (beep nhanh + rung)

def sleep_and_check(duration):
    """Ngủ trong thời gian ngắn và phản ứng lập tức nếu alarm_level chuyển về 0"""
    global alarm_level
    steps = int(duration / 0.05)
    for _ in range(steps):
        if alarm_level == 0:
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
        if alarm_level == 0:
            if GPIO_AVAILABLE:
                try:
                    GPIO.output(MOTOR_PIN, GPIO.LOW)
                    GPIO.output(BUZZER_PIN, GPIO.LOW)
                except:
                    pass
            return True
        time.sleep(rem)
    return alarm_level == 0

def alarm_worker():
    global alarm_level
    while True:
        # 1. Điều khiển cổng vật lý trên Raspberry Pi nếu có GPIO
        if GPIO_AVAILABLE:
            try:
                if alarm_level == 0:
                    GPIO.output(MOTOR_PIN, GPIO.LOW)
                    GPIO.output(BUZZER_PIN, GPIO.LOW)
                    time.sleep(0.05)
                elif alarm_level == 1:
                    GPIO.output(MOTOR_PIN, GPIO.LOW)
                    GPIO.output(BUZZER_PIN, GPIO.HIGH)
                    if sleep_and_check(0.1): continue
                    GPIO.output(BUZZER_PIN, GPIO.LOW)
                    if sleep_and_check(0.9): continue
                elif alarm_level == 2:
                    GPIO.output(MOTOR_PIN, GPIO.HIGH)
                    GPIO.output(BUZZER_PIN, GPIO.HIGH)
                    if sleep_and_check(0.2): continue
                    GPIO.output(BUZZER_PIN, GPIO.LOW)
                    if sleep_and_check(0.3): continue
                elif alarm_level == 3:
                    GPIO.output(MOTOR_PIN, GPIO.HIGH)
                    GPIO.output(BUZZER_PIN, GPIO.HIGH)
                    if sleep_and_check(0.1): continue
                    GPIO.output(BUZZER_PIN, GPIO.LOW)
                    if sleep_and_check(0.1): continue
            except:
                time.sleep(0.05)
        # 2. Chế độ PC thường (không có phần cứng còi chíp/rung)
        else:
            time.sleep(0.05)

threading.Thread(target=alarm_worker, daemon=True).start()

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
    """Thiết lập thông số phơi sáng và gain của camera qua v4l2-ctl (chuyên dụng cho Raspberry Pi CSI)"""
    import subprocess
    import os
    dev_path = "/dev/v4l-subdev0"
    if not os.path.exists(dev_path):
        dev_path = "/dev/video0"
    if not os.path.exists(dev_path):
        return
    try:
        subprocess.run([
            "v4l2-ctl", "-d", dev_path,
            "-c", "auto_exposure=1",
            "-c", "gain_automatic=0",
            "-c", f"exposure={exposure_val}",
            "-c", f"analogue_gain={gain_val}"
        ], capture_output=True)
    except Exception as e:
        print(f"[WARN] Khong the cau hinh camera controls: {e}")

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
    
    # Quét danh sách camera (Nếu truyền --camera sẽ chỉ quét camera đó, nếu không sẽ quét tự động từ 0-10)
    camera_indices = [args.camera] if args.camera is not None else list(range(11))
    
    for camera_idx in camera_indices:
        try:
            print(f"[INFO] Dang thu mo camera index {camera_idx}...")
            temp_cap = cv2.VideoCapture(camera_idx)
            if temp_cap.isOpened():
                # Đọc thử 1 frame mặc định
                ret = False
                try:
                    ret, _ = temp_cap.read()
                except:
                    pass
                
                if ret:
                    cap = temp_cap
                    print(f"[SUCCESS] Da mo camera index {camera_idx} thanh cong!")
                    break
                else:
                    # Thử chế độ Raw Bayer (dành cho CSI Camera trên Pi không có libcamerify)
                    print(f"[INFO] Mac dinh camera index {camera_idx} khong doc duoc frame. Dang thu cau hinh che do Raw Bayer...")
                    temp_cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
                    
                    # Thử định dạng GB10 (10-bit Bayer không nén, 614400 bytes cho 640x480)
                    temp_cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('G', 'B', '1', '0'))
                    temp_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    temp_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    ret_bayer = False
                    try:
                        ret_bayer, frame_bayer = temp_cap.read()
                    except:
                        pass
                        
                    if ret_bayer and frame_bayer is not None and frame_bayer.size == 614400:
                        cap = temp_cap
                        raw_bayer_mode = True
                        raw_bayer_format = 'GB10'
                        print(f"[SUCCESS] Da mo camera index {camera_idx} o che do Raw Bayer GB10!")
                        break
                    
                    # Thử định dạng pGAA (10-bit Bayer nén MIPI, 384000 bytes cho 640x480)
                    temp_cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('p', 'G', 'A', 'A'))
                    temp_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    temp_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    ret_bayer = False
                    try:
                        ret_bayer, frame_bayer = temp_cap.read()
                    except:
                        pass
                        
                    if ret_bayer and frame_bayer is not None and frame_bayer.size == 384000:
                        cap = temp_cap
                        raw_bayer_mode = True
                        raw_bayer_format = 'pGAA'
                        print(f"[SUCCESS] Da mo camera index {camera_idx} o che do Raw Bayer pGAA!")
                        break
                    
                    # Nếu thất bại hoàn toàn, giải phóng camera
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
    db_conn.commit()
    print(f"[INFO] Da khoi tao co so du lieu SQLite tai: {db_path}")

    # Khởi tạo GPIO trên Raspberry Pi (nếu có sẵn)
    if GPIO_AVAILABLE:
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(MOTOR_PIN, GPIO.OUT)
            GPIO.setup(BUZZER_PIN, GPIO.OUT)
            GPIO.output(MOTOR_PIN, GPIO.LOW)
            GPIO.output(BUZZER_PIN, GPIO.LOW)
            print("[INFO] Da khoi tao GPIO Raspberry Pi thanh cong.")
        except Exception as e:
            GPIO_AVAILABLE = False
            print(f"[WARN] Khong the khoi tao GPIO: {e}")

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
    
    calibrated = False
    
    last_second_time = time.time()
    lstm_risk = 0.0
    fatigue_score = 0.0
    perclos = 0.0
    blink_rate = 0
    yawn_count = 0
    
    # Tạo giao diện hiển thị (Su dung WINDOW_NORMAL de cho phep keo gian thu nho)
    cv2.namedWindow("DMS - Drowsiness Detection Dashboard", cv2.WINDOW_NORMAL)
    
    sim_time_start = time.time()
    
    while True:
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

        # 4. Hiệu chuẩn (Calibration Stage)
        if detected_face and not calibrated:
            alarm_level = 1  # Còi chậm để báo hiệu người dùng nhìn thẳng camera hiệu chuẩn
            calib_count += 1
            calib_ears.append(ear)
            calib_mars.append(mar)
            calib_pitches.append(pitch)
            calib_yaws.append(yaw)
            calib_rolls.append(roll)
            
            cv2.rectangle(frame, (50, 400), (590, 440), (0, 0, 0), -1)
            progress = int((calib_count / calib_frames) * 520)
            cv2.rectangle(frame, (60, 410), (60 + progress, 430), (0, 255, 255), -1)
            cv2.putText(frame, f"CALIBRATING BASELINE... {calib_count}%", (70, 425), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2)
            
            if calib_count >= calib_frames:
                ear_baseline = np.mean(calib_ears)
                mar_baseline = np.mean(calib_mars)
                pitch_baseline = np.mean(calib_pitches)
                yaw_baseline = np.mean(calib_yaws)
                roll_baseline = np.mean(calib_rolls)
                # Giới hạn ngưỡng nhắm mắt trong khoảng sinh học [0.20, 0.24] để tăng độ chính xác thực tế
                ear_limit = max(0.20, min(0.24, ear_baseline * 0.80))
                calibrated = True
                alarm_level = 0  # Tắt còi khi hiệu chuẩn hoàn tất
                print("====================================================")
                print("[SUCCESS] Hieu chuan hoan tat!")
                print(f"EAR Baseline: {ear_baseline:.3f} | Nguong nham mat (EAR Limit): {ear_limit:.3f}")
                print(f"MAR Baseline: {mar_baseline:.3f}")
                print(f"Pitch Baseline: {pitch_baseline:.3f} | Yaw Baseline: {yaw_baseline:.3f} | Roll Baseline: {roll_baseline:.3f}")
                print("====================================================")
        
        # 5. Phân tích thời gian thực khi đã hiệu chuẩn
        elif detected_face and calibrated:
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
                
            # Đè cảnh báo khẩn cấp tức thì (Overrides)
            if eye_closed_duration >= 1.0:
                status_text = "NGUY HIEM - NHAM MAT!"
                status_color = (0, 0, 255)  # Red
                alarm_level = 3
                fatigue_score = 0.95
            elif head_tilted_duration >= 1.0:
                status_text = "NGUY HIEM - LECH DAU!"
                status_color = (0, 0, 255)  # Red
                alarm_level = 3
                fatigue_score = 0.95

            # Nếu người lái xe trở lại trạng thái bình thường (mắt mở, đầu thẳng, không ngáp)
            # thì bắt buộc dừng còi báo ngay lập tức.
            if not is_eye_closed and not is_head_tilted and not is_yawning:
                alarm_level = 0
                status_text = "TINH TAO"
                status_color = (0, 255, 0)
                
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
                # Đã mất dấu lâu hơn 1.5 giây -> Cảnh báo khẩn cấp ngay lập tức
                status_text = "NGUY HIEM - MAT DAU!"
                status_color = (0, 0, 255)
                alarm_level = 3 # Fast beep bíp dồn dập
                fatigue_score = 1.0 # Force full score trên UI
                lstm_risk = 100.0
                
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
        if calibrated:
            # Tiêu đề chính
            cv2.putText(dashboard, "DRIVER MONITORING SYSTEM", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2, cv2.LINE_AA)
            cv2.line(dashboard, (15, 45), (300, 45), (100, 100, 100), 1)
            
            # Vẽ các thanh tiến trình cho metrics
            draw_bar(dashboard, "EAR (Eye Opening)", ear, 0.35, 20, 80, 280, 15, (255, 255, 0))
            draw_bar(dashboard, "MAR (Mouth Opening)", mar, 0.8, 20, 130, 280, 15, (255, 0, 255))
            
            # Góc đầu
            cv2.putText(dashboard, f"Head Pose angles (deg):", (20, 175), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1, cv2.LINE_AA)
            pitch_dev_disp = abs((pitch - pitch_baseline + 180) % 360 - 180)
            yaw_dev_disp = abs((yaw - yaw_baseline + 180) % 360 - 180)
            roll_dev_disp = abs((roll - roll_baseline + 180) % 360 - 180)
            cv2.putText(dashboard, f"  Pitch (Cui): {pitch:.1f}", (20, 195), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0) if pitch_dev_disp < 25.0 else (0, 0, 255), 1)
            cv2.putText(dashboard, f"  Yaw (Quay): {yaw:.1f}", (20, 215), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0) if yaw_dev_disp < 30.0 else (0, 0, 255), 1)
            cv2.putText(dashboard, f"  Roll (Nghieng): {roll:.1f}", (20, 235), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0) if roll_dev_disp < 25.0 else (0, 0, 255), 1)
            
            # PERCLOS & Blinks
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
                
            draw_bar(dashboard, "PERCLOS (Eye Closed %)", perclos_val, 50.0, 20, 275, 280, 15, (0, 120, 255))
            cv2.putText(dashboard, f"Blink Rate: {br_val} / min", (20, 315), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
            cv2.putText(dashboard, f"Yawn Count: {yc_val} / min", (20, 335), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
            
            # Fatigue Score & LSTM Prediction
            cv2.line(dashboard, (15, 355), (300, 355), (100, 100, 100), 1)
            draw_bar(dashboard, "FATIGUE SCORE (FS)", fatigue_score, 1.0, 20, 385, 280, 18, status_color)
            
            # Khung hiển thị Trạng thái Cảnh báo
            cv2.rectangle(dashboard, (20, 420), (300, 465), status_color, 2)
            cv2.putText(dashboard, status_text, (35, 448), cv2.FONT_HERSHEY_SIMPLEX, 0.55, status_color, 2, cv2.LINE_AA)
            
            # LSTM prediction risk
            # Chỉ hiển thị % khi đã tích lũy đủ 60 giây dữ liệu
            if model_loaded:
                seq_len_current = len(history_window)
                if seq_len_current < 60:
                    lstm_text = f"LSTM Loading: {seq_len_current}/60s"
                    lstm_color = (150, 150, 150)
                else:
                    lstm_text = f"Microsleep Risk: {lstm_risk:.1f}%"
                    lstm_color = (0, 0, 255) if lstm_risk > 70 else (0, 255, 0)
                cv2.putText(frame, lstm_text, (10, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, lstm_color, 2, cv2.LINE_AA)
            
            # Vẽ hướng dẫn phím tắt hiệu chuẩn lại
            cv2.putText(dashboard, "Nhan 'r' de hieu chuan lai", (20, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (120, 120, 120), 1, cv2.LINE_AA)
            
        # 6. Ghép khung hình camera với Dashboard thống kê
        combined_img = np.hstack((frame, dashboard))
        
        if args.scale != 1.0 and args.scale > 0:
            h_new = int(combined_img.shape[0] * args.scale)
            w_new = int(combined_img.shape[1] * args.scale)
            combined_img = cv2.resize(combined_img, (w_new, h_new))
            
        # Hiển thị giao diện chính
        cv2.imshow("DMS - Drowsiness Detection Dashboard", combined_img)
        
        # Nhận phím bấm từ người dùng (q để thoát, r để hiệu chuẩn lại)
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('r'):
            calibrated = False
            calib_count = 0
            calib_ears.clear()
            calib_mars.clear()
            calib_pitches.clear()
            calib_yaws.clear()
            calib_rolls.clear()
            alarm_level = 0  # Reset còi ngay lập tức khi nhấn phím 'r'
            print("[INFO] Yeu cau hieu chuan lai baseline...")

    # Giải phóng tài nguyên
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
