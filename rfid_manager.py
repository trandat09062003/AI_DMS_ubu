#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI_DMS RFID RC522 Manager Module
Quản lý đầu đọc thẻ RFID RC522 (qua chuẩn SPI), kết nối CSDL SQLite dms_history.db,
tra cứu thông tin tài xế (Họ tên, CCCD/VNeID, Hạng bằng lái) và hỗ trợ chạy ngầm non-blocking.
"""

import os
import time
import sqlite3
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "dms_history.db")

DEFAULT_APPROVED_DRIVERS = [
    {"uid": "40107385065", "name": "Lái xe 1", "vneid": "040107385065", "license_class": "B2", "phone": "0901000001"},
    {"uid": "530948377170", "name": "Lái xe 2", "vneid": "053094837717", "license_class": "B2", "phone": "0901000002"},
    {"uid": "393004534388", "name": "Lái xe 3", "vneid": "039300453438", "license_class": "B2", "phone": "0901000003"},
]

def init_rfid_database(db_path=DEFAULT_DB_PATH):
    """Khởi tạo cấu trúc bảng drivers, tự động nạp 3 tài xế đã duyệt và mở rộng dms_sessions nếu chưa có."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Tạo bảng lưu trữ thông tin tài xế và thẻ RFID
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS drivers (
                driver_id INTEGER PRIMARY KEY AUTOINCREMENT,
                rfid_uid TEXT UNIQUE NOT NULL,
                vneid_card TEXT UNIQUE NOT NULL,
                driver_name TEXT NOT NULL,
                license_class TEXT DEFAULT 'B2',
                phone TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1
            )
        """)
        
        # Nạp 3 tài xế mặc định đã duyệt vào cơ sở dữ liệu nếu chưa có
        for d in DEFAULT_APPROVED_DRIVERS:
            cursor.execute("""
                INSERT INTO drivers (rfid_uid, vneid_card, driver_name, license_class, phone, is_active)
                VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT(rfid_uid) DO UPDATE SET
                    driver_name = excluded.driver_name,
                    vneid_card = excluded.vneid_card,
                    license_class = excluded.license_class,
                    is_active = 1
            """, (d["uid"], d["vneid"], d["name"], d["license_class"], d["phone"]))

        # Thêm cột rfid_uid vào bảng dms_sessions nếu chưa tồn tại
        try:
            cursor.execute("ALTER TABLE dms_sessions ADD COLUMN rfid_uid TEXT")
        except Exception:
            pass
            
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[RFID DB ERROR] Không thể khởi tạo CSDL: {e}")
        return False


def get_driver_by_uid(uid_str, db_path=DEFAULT_DB_PATH):
    """
    Tra cứu thông tin tài xế từ mã UID thẻ RFID.
    Trả về dict thông tin tài xế nếu tìm thấy và thẻ đang active.
    """
    if not uid_str:
        return {"success": False, "reason": "EMPTY_UID"}
        
    uid_clean = str(uid_str).strip()
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT driver_name, vneid_card, license_class, phone, is_active 
            FROM drivers 
            WHERE rfid_uid = ?
        """, (uid_clean,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            driver_name, vneid_card, license_class, phone, is_active = row
            if is_active == 1:
                return {
                    "success": True,
                    "uid": uid_clean,
                    "name": driver_name,
                    "vneid": vneid_card,
                    "license_class": license_class or "B2",
                    "phone": phone or "",
                    "method": "RFID_RC522"
                }
            else:
                return {
                    "success": False,
                    "reason": "CARD_BLOCKED",
                    "uid": uid_clean,
                    "name": driver_name
                }
        else:
            return {
                "success": False,
                "reason": "NOT_FOUND",
                "uid": uid_clean
            }
    except Exception as e:
        return {"success": False, "reason": f"DB_ERROR: {e}", "uid": uid_clean}


def register_driver(rfid_uid, vneid_card, driver_name, license_class="B2", phone="", db_path=DEFAULT_DB_PATH):
    """Đăng ký mới hoặc cập nhật thông tin tài xế gắn với thẻ RFID."""
    init_rfid_database(db_path)
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO drivers (rfid_uid, vneid_card, driver_name, license_class, phone, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(rfid_uid) DO UPDATE SET
                vneid_card = excluded.vneid_card,
                driver_name = excluded.driver_name,
                license_class = excluded.license_class,
                phone = excluded.phone,
                is_active = 1
        """, (str(rfid_uid).strip(), str(vneid_card).strip(), str(driver_name).strip(), license_class.strip(), phone.strip()))
        conn.commit()
        conn.close()
        return True, "Thành công"
    except Exception as e:
        return False, str(e)


def delete_driver_by_uid(rfid_uid, db_path=DEFAULT_DB_PATH):
    """Xóa tài xế theo UID thẻ."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM drivers WHERE rfid_uid = ?", (str(rfid_uid).strip(),))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"[RFID ERROR] Lỗi khi xóa tài xế: {e}")
        return False


def list_all_drivers(db_path=DEFAULT_DB_PATH):
    """Lấy danh sách tất cả tài xế đã đăng ký thẻ."""
    init_rfid_database(db_path)
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT driver_id, rfid_uid, vneid_card, driver_name, license_class, phone, created_at, is_active
            FROM drivers
            ORDER BY driver_id DESC
        """)
        rows = cursor.fetchall()
        conn.close()
        drivers = []
        for r in rows:
            drivers.append({
                "id": r[0],
                "uid": r[1],
                "vneid": r[2],
                "name": r[3],
                "license_class": r[4],
                "phone": r[5],
                "created_at": r[6],
                "is_active": bool(r[7])
            })
        return drivers
    except Exception as e:
        print(f"[RFID ERROR] Lỗi khi lấy danh sách tài xế: {e}")
        return []


class RFIDManager:
    """
    Class quản lý vòng lặp đọc thẻ RFID RC522 trong luồng chạy nền (Background Thread).
    An toàn luồng (Thread-safe), không chặn camera, có bộ lọc chống quét lặp lại (Debounce/Cooldown).
    """
    def __init__(self, db_path=DEFAULT_DB_PATH, on_card_detected_callback=None, cooldown_seconds=3.0):
        self.db_path = db_path
        self.callback = on_card_detected_callback
        self.cooldown_seconds = cooldown_seconds
        
        self.is_running = False
        self.thread = None
        self.reader = None
        self.hardware_available = False
        
        self.last_scanned_uid = None
        self.last_scanned_time = 0.0
        
        # Khởi tạo Database
        init_rfid_database(self.db_path)
        self._init_hardware()

    def _init_hardware(self):
        """Khởi tạo module RC522 qua SPI."""
        try:
            import RPi.GPIO as GPIO
            from mfrc522 import SimpleMFRC522, MFRC522
            
            # Đảm bảo GPIO mode thống nhất là BCM để đồng bộ với Motor, Buzzer, Button
            if GPIO.getmode() is None:
                GPIO.setmode(GPIO.BCM)
                
            self.reader = SimpleMFRC522()
            # Nếu chạy BCM, chỉ định đúng chân vật lý Pin 22 -> GPIO 25
            if GPIO.getmode() == GPIO.BCM:
                self.reader.READER = MFRC522(pin_rst=25)
                
            self.hardware_available = True
            print("[RFID RC522] Đã kết nối phần cứng RC522 qua SPI thành công.")
        except Exception as e:
            self.hardware_available = False
            self.reader = None
            print(f"[RFID RC522] Cảnh báo: Không thể kết nối phần cứng RC522 ({e}). Tiếp tục ở chế độ dự phòng.")

    def _worker_loop(self):
        print("[RFID RC522] Bắt đầu luồng quét thẻ nền (Non-blocking)...")
        while self.is_running:
            if not self.hardware_available or self.reader is None:
                time.sleep(1.0)
                continue
                
            try:
                # Đọc ID thẻ (non-blocking để không bị treo luồng)
                card_id, _ = self.reader.read_no_block()
                if card_id is not None:
                    uid_str = str(card_id).strip()
                    now = time.time()
                    
                    # Kiểm tra cooldown để tránh 1 lần quẹt bị kích hoạt nhiều lần
                    if uid_str != self.last_scanned_uid or (now - self.last_scanned_time) > self.cooldown_seconds:
                        self.last_scanned_uid = uid_str
                        self.last_scanned_time = now
                        print(f"\n[RFID RC522] >>> Đã quẹt thẻ! UID = {uid_str}")
                        
                        # Tra cứu thông tin từ CSDL
                        driver_data = get_driver_by_uid(uid_str, self.db_path)
                        
                        if self.callback:
                            try:
                                self.callback(driver_data)
                            except Exception as cb_err:
                                print(f"[RFID CALLBACK ERROR] {cb_err}")
                
                time.sleep(0.08)  # Nghỉ 80ms giữa các lần thăm dò để tối ưu CPU
            except Exception as e:
                # Tránh dừng luồng khi xảy ra lỗi SPI tạm thời
                time.sleep(0.2)

    def start(self):
        """Bắt đầu chạy luồng quét thẻ nền."""
        if not self.is_running:
            self.is_running = True
            self.thread = threading.Thread(target=self._worker_loop, daemon=True, name="RFID-Thread")
            self.thread.start()

    def stop(self):
        """Dừng luồng quét thẻ an toàn."""
        self.is_running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
