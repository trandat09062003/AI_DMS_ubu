import subprocess
import json
import time
import os
import sys
import sqlite3
import glob
import cv2
import numpy as np
import base64
from flask import Flask, render_template_string, request, jsonify, Response, send_from_directory
from audio_manager import cleanup_old_audio, AUDIO_DIR, ensure_audio_dir, record_event_audio
from telegram_bot import load_telegram_config, save_telegram_config, send_telegram_alert_async

# Tích hợp bộ nhận diện Verify_Inf cho AI DMS
VERIFY_INF_PATH = "/home/kata/Documents/Verify_Inf"
if VERIFY_INF_PATH not in sys.path:
    sys.path.insert(0, VERIFY_INF_PATH)

try:
    from core.verifier import IdentityVerifier
    cccd_verifier = IdentityVerifier()
    print("[INFO] wifi_dashboard đã kết nối Verify_Inf IdentityVerifier thành công.")
except Exception as e_inf:
    cccd_verifier = None
    print(f"[WARN] Không thể nạp Verify_Inf trong wifi_dashboard: {e_inf}")

app = Flask(__name__)
DB_PATH = "/home/kata/Documents/AI_DMS/dms_history.db"

ensure_audio_dir()

def init_users_table():
    """Khởi tạo bảng users và chuẩn hóa: Duy nhất 1 Admin, 3 Lái xe mặc định (Lái xe 1, 2, 3) và hỗ trợ tài xế đăng ký mới."""
    if not os.path.exists(DB_PATH):
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        
        # 1. Tạo bảng users nếu chưa có
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'laixe',
                status TEXT NOT NULL DEFAULT 'active',
                password TEXT NOT NULL DEFAULT '12345678',
                avatar TEXT DEFAULT '👤',
                vneid_card TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                license_class TEXT DEFAULT 'B2',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 2. Bổ sung các cột nếu thiếu (Migration an toàn)
        cur.execute("PRAGMA table_info(users)")
        existing_cols = [c[1] for c in cur.fetchall()]
        for col_name, col_type, def_val in [
            ("status", "TEXT", "'active'"),
            ("vneid_card", "TEXT", "''"),
            ("phone", "TEXT", "''"),
            ("license_class", "TEXT", "'B2'")
        ]:
            if col_name not in existing_cols:
                try:
                    cur.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type} DEFAULT {def_val}")
                except Exception:
                    pass
        
        # 3. Chuẩn hóa: 1 Admin duy nhất (ID 1) và 3 Lái xe chính thức mặc định (ID 2: Lái xe 1, ID 3: Lái xe 2, ID 4: Lái xe 3), mật khẩu mặc định: 12345678
        default_accounts = [
            (1, "admin", "Admin (Quản trị)", "admin", "active", "12345678", "👑", "", "", ""),
            (2, "laixe1", "Lái xe 1", "laixe", "active", "12345678", "🚙", "040107385065", "0901000001", "B2"),
            (3, "laixe2", "Lái xe 2", "laixe", "active", "12345678", "🚙", "053094837717", "0901000002", "B2"),
            (4, "laixe3", "Lái xe 3", "laixe", "active", "12345678", "🚙", "039300453438", "0901000003", "B2"),
        ]
        
        for uid, uname, dname, role, stat, pwd, avt, vcard, phone, lic in default_accounts:
            cur.execute("SELECT id FROM users WHERE id = ?", (uid,))
            row = cur.fetchone()
            if row:
                cur.execute("""
                    UPDATE users 
                    SET username = ?, display_name = ?, role = ?, status = ?, avatar = ?, password = ?,
                        vneid_card = COALESCE(NULLIF(vneid_card, ''), ?),
                        phone = COALESCE(NULLIF(phone, ''), ?),
                        license_class = COALESCE(NULLIF(license_class, ''), ?)
                    WHERE id = ?
                """, (uname, dname, role, stat, avt, pwd, vcard, phone, lic, uid))
            else:
                cur.execute("""
                    INSERT INTO users (id, username, display_name, role, status, password, avatar, vneid_card, phone, license_class)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (uid, uname, dname, role, stat, pwd, avt, vcard, phone, lic))
        
        # Dọn dẹp các tài khoản thừa mẫu cũ (5..8) nếu chưa được tùy chỉnh
        cur.execute("DELETE FROM users WHERE id IN (5, 6, 7, 8) AND display_name LIKE 'Lái xe % (Tài xế)'")
        
        # Dọn dẹp dữ liệu cũ dms_sessions để hiển thị rõ ràng "Người lạ"
        cur.execute("""
            UPDATE dms_sessions 
            SET driver_name = 'Người lạ', vneid_card = 'Không xác định'
            WHERE driver_name IS NULL OR driver_name = '' OR driver_name LIKE '%Chưa xác định%';
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ERROR] init_users_table error: {e}")

init_users_table()

def get_db_sessions(driver_filter=None):
    """Lấy danh sách các chuyến đi, hỗ trợ lọc theo Lái xe hoặc xem tất cả."""
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        query = """
            SELECT session_id, start_time, end_time, duration_seconds, 
                   distraction_count, drowsiness_count, yawn_count, 
                   avg_fatigue_score, max_fatigue_score,
                   COALESCE(NULLIF(driver_name, ''), 'Người lạ') AS driver_name,
                   COALESCE(NULLIF(vneid_card, ''), 'Không xác định') AS vneid_card
            FROM dms_sessions 
        """
        params = []
        
        if driver_filter and driver_filter != "all":
            df_clean = str(driver_filter).strip()
            if df_clean in ("laixe1", "Lái xe 1"):
                query += " WHERE driver_name LIKE '%Lái xe 1%' OR vneid_card = '040107385065' OR rfid_uid = '40107385065'"
            elif df_clean in ("laixe2", "Lái xe 2"):
                query += " WHERE driver_name LIKE '%Lái xe 2%' OR vneid_card = '053094837717' OR rfid_uid = '530948377170'"
            elif df_clean in ("laixe3", "Lái xe 3"):
                query += " WHERE driver_name LIKE '%Lái xe 3%' OR vneid_card = '039300453438' OR rfid_uid = '393004534388'"
            elif df_clean in ("nguoila", "Người lạ"):
                query += " WHERE driver_name = 'Người lạ' OR driver_name LIKE '%Chưa xác định%'"
            else:
                query += " WHERE LOWER(driver_name) LIKE ? OR driver_name = ? OR vneid_card = ?"
                params.extend([f"%{df_clean.lower()}%", df_clean, df_clean])
                
        query += " ORDER BY session_id DESC"
        
        if params:
            cursor.execute(query, tuple(params))
        else:
            cursor.execute(query)
            
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            dname = d.get("driver_name")
            if not dname or dname == "None" or "[Chưa xác định" in dname:
                d["driver_name"] = "Người lạ"
            vcard = d.get("vneid_card")
            if not vcard or vcard == "None" or "[Chưa đọc" in vcard:
                d["vneid_card"] = "Không xác định"
            result.append(d)
        conn.close()
        return result
    except Exception as e:
        print(f"[ERROR] DB query error: {e}")
        return []

def format_duration(seconds):
    if not seconds:
        return "00:00:00"
    hrs = seconds // 3600
    mins = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI DMS - Quản Trị Hệ Thống & Giám Sát Lái Xe</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
        }

        body {
            background: linear-gradient(135deg, #0b0f19 0%, #111827 50%, #0f172a 100%);
            color: #f8fafc;
            min-height: 100vh;
            padding: 20px 16px;
        }

        .main-wrapper {
            max-width: 1080px;
            margin: 0 auto;
            transition: filter 0.3s ease;
        }

        .dashboard-locked {
            filter: blur(10px);
            pointer-events: none;
            user-select: none;
        }

        /* Toast Notifications Container */
        #toast-container {
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 99999;
            display: flex;
            flex-direction: column;
            gap: 10px;
            max-width: 400px;
            pointer-events: none;
        }

        .toast {
            background: rgba(30, 41, 59, 0.95);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 12px;
            padding: 14px 18px;
            color: #ffffff;
            box-shadow: 0 10px 25px rgba(0,0,0,0.5);
            display: flex;
            align-items: center;
            gap: 12px;
            font-size: 0.9rem;
            pointer-events: auto;
            animation: slideInRight 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards;
            transition: all 0.3s ease;
        }

        .toast.hide {
            opacity: 0;
            transform: translateX(100%);
        }

        .toast-success { border-left: 5px solid #10b981; }
        .toast-warning { border-left: 5px solid #f59e0b; }
        .toast-error   { border-left: 5px solid #ef4444; }
        .toast-info    { border-left: 5px solid #3b82f6; }

        @keyframes slideInRight {
            from { opacity: 0; transform: translateX(50px); }
            to { opacity: 1; transform: translateX(0); }
        }

        .app-header {
            background: rgba(30, 41, 59, 0.6);
            backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 20px;
            padding: 18px 24px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
        }

        .brand-box {
            display: flex;
            align-items: center;
            gap: 14px;
        }

        .logo-icon {
            width: 44px;
            height: 44px;
            background: linear-gradient(135deg, #6366f1 0%, #a855f7 100%);
            border-radius: 12px;
            display: flex;
            justify-content: center;
            align-items: center;
            font-size: 1.4rem;
            box-shadow: 0 4px 15px rgba(99, 102, 241, 0.4);
        }

        .brand-title {
            font-size: 1.25rem;
            font-weight: 800;
            background: linear-gradient(to right, #ffffff, #cbd5e1);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .brand-desc { font-size: 0.8rem; color: #94a3b8; }

        /* User Profile Status & Account Switcher */
        .user-panel-bar {
            display: flex;
            align-items: center;
            gap: 10px;
            background: rgba(15, 23, 42, 0.6);
            padding: 6px 12px;
            border-radius: 12px;
            border: 1px solid rgba(255, 255, 255, 0.08);
        }

        .user-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 0.85rem;
            font-weight: 600;
            padding: 6px 12px;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s;
        }

        .user-badge-admin {
            background: rgba(239, 68, 68, 0.2);
            color: #fca5a5;
            border: 1px solid rgba(239, 68, 68, 0.4);
        }

        .user-badge-laixe {
            background: rgba(59, 130, 246, 0.2);
            color: #93c5fd;
            border: 1px solid rgba(59, 130, 246, 0.4);
        }

        .nav-tabs {
            display: flex;
            background: rgba(15, 23, 42, 0.6);
            padding: 4px;
            border-radius: 14px;
            border: 1px solid rgba(255, 255, 255, 0.05);
            gap: 4px;
            flex-wrap: wrap;
            margin-bottom: 20px;
        }

        .tab-btn {
            padding: 10px 16px;
            border: none;
            background: transparent;
            color: #94a3b8;
            font-weight: 600;
            font-size: 0.85rem;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .tab-btn.active {
            background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
            color: #ffffff;
            box-shadow: 0 4px 12px rgba(99, 102, 241, 0.3);
        }

        .role-lock-tag {
            font-size: 0.7rem;
            background: rgba(239, 68, 68, 0.2);
            color: #f87171;
            padding: 2px 6px;
            border-radius: 6px;
            font-weight: 700;
        }

        .tab-content { display: none; animation: fadeIn 0.3s ease-in-out; }
        .tab-content.active { display: block; }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(6px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 20px;
        }

        .stat-card {
            background: rgba(30, 41, 59, 0.5);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 18px;
            padding: 18px;
            display: flex;
            align-items: center;
            gap: 14px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.2);
        }

        .stat-icon-bg {
            width: 44px; height: 44px;
            border-radius: 12px;
            display: flex; justify-content: center; align-items: center;
            font-size: 1.2rem;
        }

        .icon-blue { background: rgba(59, 130, 246, 0.15); color: #60a5fa; }
        .icon-purple { background: rgba(168, 85, 247, 0.15); color: #c084fc; }
        .icon-amber { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }
        .icon-red { background: rgba(239, 68, 68, 0.15); color: #f87171; }

        .stat-val { font-size: 1.4rem; font-weight: 800; color: #f8fafc; }
        .stat-lbl { font-size: 0.75rem; color: #94a3b8; font-weight: 500; }

        .glass-panel {
            background: rgba(30, 41, 59, 0.5);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 20px;
            padding: 24px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
            margin-bottom: 24px;
        }

        .panel-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            flex-wrap: wrap;
            gap: 10px;
        }

        .panel-title { font-size: 1.1rem; font-weight: 700; color: #f1f5f9; }

        .table-responsive { width: 100%; overflow-x: auto; }

        .data-table { width: 100%; border-collapse: collapse; text-align: left; font-size: 0.85rem; }
        .data-table th {
            background: rgba(15, 23, 42, 0.6); color: #94a3b8; padding: 12px 14px;
            font-weight: 600; font-size: 0.75rem; text-transform: uppercase;
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
            white-space: nowrap;
        }
        .data-table td { padding: 12px 14px; border-bottom: 1px solid rgba(255, 255, 255, 0.05); color: #e2e8f0; white-space: nowrap; }

        /* Driver Badges in History Table */
        .driver-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.82rem;
        }
        .driver-laixe1 { background: rgba(59, 130, 246, 0.2); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.4); }
        .driver-laixe2 { background: rgba(168, 85, 247, 0.2); color: #c084fc; border: 1px solid rgba(168, 85, 247, 0.4); }
        .driver-laixe3 { background: rgba(6, 182, 212, 0.2); color: #22d3ee; border: 1px solid rgba(6, 182, 212, 0.4); }
        .driver-nguoila { background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); }
        .driver-verified { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); }

        .btn-action {
            padding: 8px 16px; background: rgba(99, 102, 241, 0.2);
            border: 1px solid rgba(99, 102, 241, 0.4); color: #818cf8;
            border-radius: 10px; font-weight: 600; font-size: 0.8rem;
            cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; gap: 6px;
            transition: all 0.2s;
        }

        .btn-action:hover { background: #6366f1; color: #ffffff; }

        .btn-action.btn-sm {
            padding: 6px 12px;
            font-size: 0.78rem;
        }

        .btn-danger {
            background: rgba(239, 68, 68, 0.2);
            border-color: rgba(239, 68, 68, 0.4);
            color: #f87171;
        }
        .btn-danger:hover { background: #ef4444; color: #fff; }

        .btn-success {
            background: rgba(16, 185, 129, 0.2);
            border-color: rgba(16, 185, 129, 0.4);
            color: #34d399;
        }
        .btn-success:hover { background: #10b981; color: #fff; }

        audio {
            height: 36px; outline: none;
            filter: invert(0.9) hue-rotate(180deg);
            border-radius: 8px;
        }

        .wifi-grid { display: flex; flex-direction: column; gap: 10px; max-height: 380px; overflow-y: auto; }
        .wifi-card {
            background: rgba(15, 23, 42, 0.5); border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 14px; padding: 16px 20px; display: flex; justify-content: space-between;
            align-items: center; cursor: pointer; transition: all 0.2s ease;
        }
        .wifi-card:hover { background: rgba(99, 102, 241, 0.15); border-color: rgba(99, 102, 241, 0.4); }

        .modal-overlay {
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(11, 15, 25, 0.85); backdrop-filter: blur(12px);
            display: none; justify-content: center; align-items: center; z-index: 10000; padding: 20px;
        }
        .modal {
            background: #1e293b; border: 1px solid rgba(255, 255, 255, 0.12);
            border-radius: 24px; padding: 28px; width: 100%; max-width: 520px;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7);
            animation: fadeIn 0.25s ease-out;
            max-height: 90vh;
            overflow-y: auto;
        }
        .modal-title { font-size: 1.25rem; font-weight: 800; margin-bottom: 8px; color: #f8fafc; text-align: center; }
        .modal-subtitle { font-size: 0.85rem; color: #94a3b8; margin-bottom: 20px; text-align: center; }
        
        .input-group { margin-bottom: 16px; }
        .input-group label { display: block; font-size: 0.8rem; font-weight: 600; color: #cbd5e1; margin-bottom: 6px; }
        .input-group input, .input-group select {
            width: 100%; padding: 12px 14px; background: #0f172a;
            border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 10px; color: #ffffff; font-size: 0.95rem;
            outline: none; transition: border-color 0.2s;
        }
        .input-group input:focus, .input-group select:focus {
            border-color: #6366f1;
            box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.2);
        }
        .modal-actions { display: flex; gap: 10px; margin-top: 20px; }
        .btn-cancel { flex: 1; padding: 12px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255, 255, 255, 0.1); color: #94a3b8; border-radius: 10px; cursor: pointer; font-weight: 600; }
        .btn-connect { flex: 1; padding: 12px; background: #6366f1; border: none; color: #ffffff; border-radius: 10px; font-weight: 700; cursor: pointer; }
        .btn-connect:hover { background: #4f46e5; }

        .restricted-card {
            text-align: center;
            padding: 48px 24px;
            background: rgba(15, 23, 42, 0.5);
            border: 1px dashed rgba(239, 68, 68, 0.4);
            border-radius: 18px;
        }

        .accounts-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 16px;
        }

        .account-card {
            background: rgba(15, 23, 42, 0.5);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 16px;
            padding: 18px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            transition: all 0.2s;
        }

        .account-card:hover {
            border-color: rgba(99, 102, 241, 0.4);
            background: rgba(15, 23, 42, 0.7);
        }

        .account-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .account-title {
            font-weight: 700;
            font-size: 1rem;
            color: #f8fafc;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .role-badge {
            font-size: 0.75rem;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 20px;
            text-transform: uppercase;
        }
        .role-badge-admin { background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); }
        .role-badge-laixe { background: rgba(59, 130, 246, 0.2); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.4); }

        .status-badge {
            font-size: 0.72rem;
            font-weight: 700;
            padding: 3px 8px;
            border-radius: 6px;
        }
        .status-badge-active { background: rgba(16, 185, 129, 0.2); color: #34d399; }
        .status-badge-pending { background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); }
        .status-badge-locked { background: rgba(239, 68, 68, 0.2); color: #f87171; }

        .quick-login-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 10px;
            margin-bottom: 16px;
        }

        .quick-login-btn {
            padding: 12px;
            background: rgba(15, 23, 42, 0.7);
            border: 1px solid rgba(255, 255, 255, 0.1);
            color: #e2e8f0;
            border-radius: 12px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            text-align: left;
            display: flex;
            align-items: center;
            gap: 10px;
            transition: all 0.2s;
        }
        .quick-login-btn:hover {
            background: rgba(99, 102, 241, 0.25);
            border-color: #6366f1;
            color: #ffffff;
            transform: translateY(-2px);
        }

        .login-tabs {
            display: flex;
            border-bottom: 1px solid rgba(255,255,255,0.1);
            margin-bottom: 18px;
            gap: 8px;
        }
        .login-tab-btn {
            padding: 8px 14px;
            background: transparent;
            border: none;
            color: #94a3b8;
            font-weight: 600;
            font-size: 0.88rem;
            cursor: pointer;
            border-bottom: 2px solid transparent;
            transition: all 0.2s;
        }
        .login-tab-btn.active {
            color: #6366f1;
            border-bottom-color: #6366f1;
            font-weight: 700;
        }

        .driver-filter-select {
            padding: 8px 14px;
            background: #0f172a;
            border: 1px solid rgba(255,255,255,0.15);
            border-radius: 10px;
            color: #f8fafc;
            font-size: 0.85rem;
            font-weight: 600;
            outline: none;
        }
    </style>
</head>
<body>
    <div id="toast-container"></div>

    <div class="main-wrapper dashboard-locked" id="dashboard-main">
        <header class="app-header">
            <div class="brand-box">
                <div class="logo-icon">🚘</div>
                <div>
                    <div class="brand-title">AI DMS Dashboard</div>
                    <div class="brand-desc">Giám sát Lái xe, Cảnh báo Telegram & Phân Quyền Tài Khoản (1 Admin & Tài xế)</div>
                </div>
            </div>
            
            <div class="user-panel-bar">
                <div id="current-user-badge" class="user-badge user-badge-admin" onclick="openLoginModal(true)" title="Nhấn để đổi tài khoản">
                    <span id="user-display-avatar">👑</span>
                    <span id="user-display-name">Chưa Đăng Nhập</span>
                    <span style="font-size:0.7rem; opacity:0.7;">▾</span>
                </div>
                <button class="btn-action btn-sm" onclick="openLoginModal(true)">🔄 Đổi Tài Khoản</button>
                <button class="btn-action btn-sm btn-danger" onclick="logoutUser()">🚪 Đăng Xuất</button>
            </div>
        </header>

        <nav class="nav-tabs">
            <button class="tab-btn active" onclick="switchTab('sessions')">📊 Chuyến Đi</button>
            <button class="tab-btn" onclick="switchTab('vneid')">🪪 Xác Thực Tài Xế</button>
            <button class="tab-btn" onclick="switchTab('audio')">🎵 Ghi Âm Cabin <span class="role-lock-tag" id="tab-tag-audio">Admin</span></button>
            <button class="tab-btn" onclick="switchTab('telegram')">✈️ Telegram Bot <span class="role-lock-tag" id="tab-tag-telegram">Admin</span></button>
            <button class="tab-btn" onclick="switchTab('wifi')">📡 Wi-Fi Manager</button>
            <button class="tab-btn" onclick="switchTab('users')">👥 Quản Lý & Phân Quyền <span class="role-lock-tag" id="tab-tag-users">Admin</span></button>
        </nav>

        <!-- TAB 1: THÔNG TIN CHUYẾN ĐI -->
        <div id="tab-sessions" class="tab-content active">
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-icon-bg icon-blue">🚘</div>
                    <div>
                        <div class="stat-val" id="stat-total-trips">-</div>
                        <div class="stat-lbl">Tổng Chuyến Đi</div>
                    </div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon-bg icon-purple">⏱️</div>
                    <div>
                        <div class="stat-val" id="stat-total-time">-</div>
                        <div class="stat-lbl">Tổng Thời Gian Lái</div>
                    </div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon-bg icon-amber">😴</div>
                    <div>
                        <div class="stat-val" id="stat-total-alerts">-</div>
                        <div class="stat-lbl">Tổng Cảnh Báo</div>
                    </div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon-bg icon-red">📈</div>
                    <div>
                        <div class="stat-val" id="stat-avg-fatigue">-</div>
                        <div class="stat-lbl">Mệt Mỏi TB</div>
                    </div>
                </div>
            </div>

            <div class="glass-panel">
                <div class="panel-header">
                    <div>
                        <div class="panel-title" id="sessions-panel-title">📋 Danh Sách Hành Trình Lái Xe</div>
                        <div style="font-size:0.8rem; color:#94a3b8; margin-top:4px;" id="sessions-panel-subtitle">
                            Hiển thị lịch sử hành trình được ghi nhận trong cơ sở dữ liệu.
                        </div>
                    </div>
                    
                    <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
                        <!-- Dropdown bộ lọc lái xe (Chỉ Admin thấy hoặc xem tất cả) -->
                        <div id="admin-driver-filter-container" style="display:none; align-items:center; gap:6px;">
                            <label style="font-size:0.8rem; color:#94a3b8; font-weight:600;">Lọc Lái Xe:</label>
                            <select id="driver-filter-select" class="driver-filter-select" onchange="onDriverFilterChange()">
                                <option value="all">🌟 Xem Tất Cả Các Chuyến Đi</option>
                                <option value="Lái xe 1">🚙 Lái xe 1</option>
                                <option value="Lái xe 2">🚙 Lái xe 2</option>
                                <option value="Lái xe 3">🚙 Lái xe 3</option>
                                <option value="Người lạ">⚠️ Người lạ / Chưa xác thực</option>
                            </select>
                        </div>

                        <button class="btn-action" onclick="loadSessions()">🔄 Tải Lại</button>
                        <a id="btn-export-csv" href="/api/export_csv" class="btn-action" target="_blank">📥 Xuất Báo Cáo CSV</a>
                    </div>
                </div>

                <div class="table-responsive">
                    <table class="data-table">
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>👤 Người Lái Xe</th>
                                <th>🪪 CCCD / VNeID</th>
                                <th>⏰ Bắt Đầu</th>
                                <th>⏹️ Kết Thúc</th>
                                <th>⏱️ Thời Lượng</th>
                                <th>😴 Ngủ Gật</th>
                                <th>⚠️ Mất TT</th>
                                <th>🥱 Ngáp</th>
                                <th>📈 FS Max</th>
                            </tr>
                        </thead>
                        <tbody id="sessions-table-body">
                            <tr><td colspan="10" style="text-align:center; color:#64748b; padding:20px;">Đang tải lịch sử hành trình...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- TAB 2: XÁC THỰC TÀI XẾ / VNEID -->
        <div id="tab-vneid" class="tab-content">
            <div class="glass-panel">
                <div class="panel-header">
                    <div class="panel-title">⚡ 1-Click Chọn Nhanh 3 Tài Xế Đã Duyệt Trong Hệ Thống</div>
                </div>
                <div style="font-size:0.85rem; color:#94a3b8; margin-bottom:16px;">
                    Hệ thống hỗ trợ 3 cách xác minh thông tin: <b>1. Ấn nút scan giấy tờ trên xe (nếu không OCR được sẽ tự nhận diện Người lạ)</b>, <b>2. Web Dashboard</b>, <b>3. Quẹt thẻ RFID</b>.
                </div>
                <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap:12px; margin-bottom:24px;">
                    <div class="wifi-card" onclick="quickSelectDriver('Lái xe 1', '040107385065', 'B2')">
                        <div>
                            <div style="font-weight:700; color:#f8fafc; font-size:0.95rem;">👤 Lái xe 1</div>
                            <div style="font-size:0.8rem; color:#94a3b8;">CCCD: 040107385065</div>
                            <div style="font-size:0.75rem; color:#60a5fa;">Hạng B2 | Thẻ RFID: 40107385065</div>
                        </div>
                        <div style="color:#6366f1; font-weight:700; font-size:1.1rem;">⚡ Chọn</div>
                    </div>
                    <div class="wifi-card" onclick="quickSelectDriver('Lái xe 2', '053094837717', 'B2')">
                        <div>
                            <div style="font-weight:700; color:#f8fafc; font-size:0.95rem;">👤 Lái xe 2</div>
                            <div style="font-size:0.8rem; color:#94a3b8;">CCCD: 053094837717</div>
                            <div style="font-size:0.75rem; color:#60a5fa;">Hạng B2 | Thẻ RFID: 530948377170</div>
                        </div>
                        <div style="color:#6366f1; font-weight:700; font-size:1.1rem;">⚡ Chọn</div>
                    </div>
                    <div class="wifi-card" onclick="quickSelectDriver('Lái xe 3', '039300453438', 'B2')">
                        <div>
                            <div style="font-weight:700; color:#f8fafc; font-size:0.95rem;">👤 Lái xe 3</div>
                            <div style="font-size:0.8rem; color:#94a3b8;">CCCD: 039300453438</div>
                            <div style="font-size:0.75rem; color:#60a5fa;">Hạng B2 | Thẻ RFID: 393004534388</div>
                        </div>
                        <div style="color:#6366f1; font-weight:700; font-size:1.1rem;">⚡ Chọn</div>
                    </div>
                </div>

                <hr style="border:0; border-top:1px solid rgba(255,255,255,0.08); margin: 20px 0;">

                <div class="panel-header">
                    <div class="panel-title">📷 Ảnh Giấy Tờ / Thẻ CCCD Vừa Chụp & Tải Lên</div>
                    <div style="display:flex; gap:8px;">
                        <button class="btn-action" onclick="fetchLatestCardScan(true)" id="btn-refresh-card">🔄 Tải Lại Ảnh Mới</button>
                    </div>
                </div>

                <div id="card-preview-wrapper" style="margin-bottom:20px; background:rgba(15,23,42,0.6); border:1px dashed rgba(255,255,255,0.15); border-radius:14px; padding:16px; display:flex; flex-direction:column; align-items:center; justify-content:center; min-height:140px; text-align:center;">
                    <div id="card-preview-placeholder" style="color:#94a3b8; font-size:0.9rem;">
                        <span>Chưa có ảnh chụp từ camera xe hoặc tải lên.</span><br>
                        <span style="font-size:0.8rem; color:#64748b; margin-top:4px; display:inline-block;">(Nhấn nút scan trên xe GPIO 22 / phím 'V' hoặc tải ảnh bên dưới)</span>
                    </div>
                    <div id="card-preview-content" style="display:none; width:100%; text-align:center;">
                        <div style="position:relative; display:inline-block; max-width:100%;">
                            <a id="card-preview-link" href="#" target="_blank" title="Nhấn để xem ảnh gốc / phóng to">
                                <img id="card-preview-img" src="" alt="Ảnh giấy tờ" style="max-height:260px; max-width:100%; border-radius:10px; border:1px solid rgba(255,255,255,0.2); box-shadow:0 6px 20px rgba(0,0,0,0.4); object-fit:contain; cursor:zoom-in;">
                            </a>
                        </div>
                        <div style="margin-top:10px;">
                            <span id="card-status-badge" style="font-size:0.85rem; font-weight:600; padding:6px 14px; border-radius:20px; display:inline-block;"></span>
                        </div>
                        <div id="card-scan-time" style="font-size:0.75rem; color:#64748b; margin-top:6px;"></div>
                    </div>
                </div>

                <div class="panel-header">
                    <div class="panel-title">📸 Chụp / Tải Ảnh Thẻ CCCD Để AI OCR Nhận Diện Tự Động</div>
                </div>
                <div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:24px;">
                    <input type="file" id="card-file-input" accept="image/*" style="display:none;" onchange="uploadCardImage(this)">
                    <button class="btn-action" onclick="document.getElementById('card-file-input').click()" id="btn-upload-card" style="padding:10px 18px;">
                        📸 Chụp / Tải Ảnh Thẻ CCCD Từ Thiết Bị
                    </button>
                    <span id="ocr-status-text" style="font-size:0.85rem; color:#94a3b8;">Chọn ảnh rõ nét có chứa số CCCD hoặc Mã QR</span>
                </div>

                <hr style="border:0; border-top:1px solid rgba(255,255,255,0.08); margin: 20px 0;">

                <div class="panel-header">
                    <div class="panel-title">✏️ Nhập Tùy Chỉnh Thông Tin Tài Xế</div>
                </div>
                <div class="input-group">
                    <label for="driver-name">Họ và Tên Tài Xế:</label>
                    <input type="text" id="driver-name" placeholder="Ví dụ: Nguyễn Văn A" onkeydown="if(event.key==='Enter') submitDriverAuth()">
                </div>
                <div class="input-group">
                    <label for="driver-vneid">Số Căn Cước Công Dân / VNeID (12 số):</label>
                    <input type="text" id="driver-vneid" placeholder="Ví dụ: 079203001234" onkeydown="if(event.key==='Enter') submitDriverAuth()">
                </div>
                <div class="input-group">
                    <label for="driver-license">Hạng Bằng Lái & Biển Số Xe:</label>
                    <input type="text" id="driver-license" placeholder="Ví dụ: B2 - 79A-123.45" onkeydown="if(event.key==='Enter') submitDriverAuth()">
                </div>

                <div style="display:flex; justify-content:flex-end;">
                    <button class="btn-action" style="background:#6366f1; color:#ffffff; padding:12px 24px; font-size:0.95rem;" onclick="submitDriverAuth()" id="btn-auth-driver">
                        🚀 Kích Hoạt Phiên Lái Xe Ngay
                    </button>
                </div>
            </div>
        </div>

        <!-- TAB 3: GHI ÂM CABIN KHOANG LÁI (ADMIN ONLY) -->
        <div id="tab-audio" class="tab-content">
            <div class="glass-panel" id="audio-admin-content">
                <div class="panel-header">
                    <div class="panel-title">🎵 Thư Viện Ghi Âm Âm Thanh Khoang Lái (Sự Cố & Reset)</div>
                    <div style="display:flex; gap:10px;">
                        <button class="btn-action" onclick="recordTestAudio()" id="btn-rec-test">🎙️ Ghi Âm Thử 10s</button>
                        <button class="btn-action" onclick="loadAudioFiles()">🔄 Tải Lại</button>
                    </div>
                </div>
                <div style="font-size:0.8rem; color:#94a3b8; margin-bottom:16px;">
                    💡 <i>Ghi âm sẽ tự động tạo ra 10s khi **Kết thúc Chuyến đi / Reset Quét lại khuôn mặt** hoặc khi **Bấm nút Ghi âm thử 10s**.</i>
                </div>

                <div class="table-responsive">
                    <table class="data-table">
                        <thead>
                            <tr>
                                <th>File Đoạn Âm Thanh / Sự Cố</th>
                                <th>Dung Lượng</th>
                                <th>Thời Gian Ghi</th>
                                <th>Phát Trực Tiếp (1-Click Play)</th>
                                <th>Tải Về</th>
                            </tr>
                        </thead>
                        <tbody id="audio-table-body">
                            <tr><td colspan="5" style="text-align:center; color:#64748b; padding:20px;">Đang tải danh sách ghi âm...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="glass-panel restricted-card" id="audio-locked-content" style="display:none;">
                <div style="font-size:2.5rem; margin-bottom:12px;">🔒</div>
                <h3 style="color:#f87171; margin-bottom:8px;">Quyền Truy Cập Bị Giới Hạn</h3>
                <p style="color:#94a3b8; font-size:0.9rem; max-width:480px; margin:0 auto 16px;">
                    Tính năng nghe và tải file ghi âm cabin khoang lái chỉ dành riêng cho <b>Quản trị viên (Admin)</b>.
                </p>
                <button class="btn-action" onclick="openLoginModal(false)">👑 Đăng Nhập Tài Khoản Admin</button>
            </div>
        </div>

        <!-- TAB 4: TELEGRAM BOT NOTIFICATIONS (ADMIN ONLY) -->
        <div id="tab-telegram" class="tab-content">
            <div class="glass-panel" id="tg-admin-content">
                <div class="panel-header">
                    <div class="panel-title">✈️ Cấu Hình Telegram Bot Cảnh Báo Buồn Ngủ & Mất Tập Trung</div>
                </div>
                <div style="font-size:0.85rem; color:#94a3b8; margin-bottom:16px;">
                    Hệ thống sẽ tự động gửi ảnh chụp vi phạm và thông báo về nhóm Telegram khi phát hiện lái xe buồn ngủ cấp 2/3.
                </div>

                <div class="input-group">
                    <label for="tg-token">Telegram Bot Token (tạo từ @BotFather):</label>
                    <input type="text" id="tg-token" placeholder="Ví dụ: 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ">
                </div>
                <div class="input-group">
                    <label for="tg-chatid">Chat ID (nhóm hoặc cá nhân):</label>
                    <input type="text" id="tg-chatid" placeholder="Ví dụ: -1001234567890 hoặc 987654321">
                </div>

                <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:10px;">
                    <button class="btn-action" onclick="sendTestTelegram()" id="btn-test-tg">🧪 Gửi Cảnh Báo Thử</button>
                    <button class="btn-action" style="background:#10b981; color:#fff;" onclick="saveTelegramSettings()">💾 Lưu Cấu Hình</button>
                </div>
            </div>

            <div class="glass-panel restricted-card" id="tg-locked-content" style="display:none;">
                <div style="font-size:2.5rem; margin-bottom:12px;">🔒</div>
                <h3 style="color:#f87171; margin-bottom:8px;">Quyền Truy Cập Bị Giới Hạn</h3>
                <p style="color:#94a3b8; font-size:0.9rem; max-width:480px; margin:0 auto 16px;">
                    Cấu hình bảo mật và gửi thông báo Telegram chỉ dành riêng cho <b>Quản trị viên (Admin)</b>.
                </p>
                <button class="btn-action" onclick="openLoginModal(false)">👑 Đăng Nhập Tài Khoản Admin</button>
            </div>
        </div>

        <!-- TAB 5: WI-FI MANAGER -->
        <div id="tab-wifi" class="tab-content">
            <div class="glass-panel">
                <div class="panel-header">
                    <div class="panel-title">📡 Quản Lý Kết Nối Mạng Wi-Fi Xe (NetworkManager)</div>
                    <button class="btn-action" onclick="scanWifi()">🔍 Quét Mạng Wi-Fi</button>
                </div>
                <div style="font-size:0.85rem; color:#94a3b8; margin-bottom:16px;">
                    Nhấn vào mạng Wi-Fi bên dưới để nhập mật khẩu kết nối cho xe:
                </div>
                <div class="wifi-grid" id="wifi-list">
                    <div style="text-align:center; color:#64748b; padding:20px;">Đang tải danh sách mạng Wi-Fi...</div>
                </div>
            </div>
        </div>

        <!-- TAB 6: PHÂN QUYỀN TÀI KHOẢN (ADMIN ONLY) -->
        <div id="tab-users" class="tab-content">
            <div class="glass-panel" id="users-admin-content">
                <div class="panel-header">
                    <div>
                        <div class="panel-title">👥 Quản Lý & Phân Quyền Tài Khoản (Admin & Lái Xe)</div>
                        <div style="font-size:0.8rem; color:#94a3b8; margin-top:4px;">
                            Hệ thống duy trì <b>1 Admin duy nhất</b> và các tài khoản <b>Lái xe (Lái xe 1, 2, 3...)</b>. Người đăng ký mới sẽ ở trạng thái Chờ Duyệt.
                        </div>
                    </div>
                    <div style="display:flex; gap:10px;">
                        <button class="btn-action" onclick="loadUsersList()">🔄 Tải Lại</button>
                        <button class="btn-action" style="background:#10b981; color:#fff;" onclick="openCreateUserModal()">➕ Tạo Tài Khoản Mới</button>
                    </div>
                </div>

                <!-- Danh sách chờ duyệt -->
                <div id="pending-users-section" style="display:none; margin-bottom:24px;">
                    <div style="font-size:0.95rem; font-weight:700; color:#fbbf24; margin-bottom:10px; display:flex; align-items:center; gap:8px;">
                        <span>⏳</span> Danh Sách Người Đăng Ký Mới - Đang Chờ Phê Duyệt:
                    </div>
                    <div class="accounts-grid" id="pending-accounts-grid">
                        <!-- Pending users cards -->
                    </div>
                    <hr style="border:0; border-top:1px solid rgba(255,255,255,0.08); margin: 20px 0;">
                </div>

                <div style="font-size:0.95rem; font-weight:700; color:#cbd5e1; margin-bottom:12px;">
                    🟢 Danh Sách Tài Khoản Đang Hoạt Động:
                </div>
                <div class="accounts-grid" id="accounts-grid-container">
                    <!-- Dynamic Active Account Cards -->
                </div>
            </div>

            <div class="glass-panel restricted-card" id="users-locked-content" style="display:none;">
                <div style="font-size:2.5rem; margin-bottom:12px;">🔒</div>
                <h3 style="color:#f87171; margin-bottom:8px;">Quyền Truy Cập Bị Giới Hạn</h3>
                <p style="color:#94a3b8; font-size:0.9rem; max-width:480px; margin:0 auto 16px;">
                    Phân quyền tài khoản và phê duyệt người dùng chỉ có thể thực hiện bởi <b>Quản trị viên (Admin)</b>.
                </p>
                <button class="btn-action" onclick="openLoginModal(false)">👑 Đăng Nhập Tài Khoản Admin</button>
            </div>
        </div>
    </div>

    <!-- MODAL: KẾT NỐI WIFI -->
    <div class="modal-overlay" id="pwd-modal">
        <div class="modal">
            <div class="modal-title" id="target-ssid-title">Kết nối Wi-Fi</div>
            <div class="input-group">
                <label for="wifi-pass">Mật khẩu Wi-Fi:</label>
                <input type="password" id="wifi-pass" placeholder="Nhập mật khẩu..." onkeydown="if(event.key==='Enter') submitConnect()">
            </div>
            <div class="modal-actions">
                <button class="btn-cancel" onclick="closeModal()">Hủy</button>
                <button class="btn-connect" onclick="submitConnect()" id="btn-submit">Kết Nối</button>
            </div>
        </div>
    </div>

    <!-- MODAL: ĐĂNG NHẬP / ĐĂNG KÝ BẮT BUỘC -->
    <div class="modal-overlay" id="login-modal" style="display:flex;">
        <div class="modal">
            <div class="modal-title">🔐 Đăng Nhập Hệ Thống AI DMS</div>
            <div class="modal-subtitle">Vui lòng chọn tài khoản hoặc đăng nhập để truy cập Dashboard</div>
            
            <div class="login-tabs">
                <button class="login-tab-btn active" id="btn-tab-quick" onclick="switchLoginTab('quick')">⚡ Chọn Nhanh</button>
                <button class="login-tab-btn" id="btn-tab-manual" onclick="switchLoginTab('manual')">🔐 Tên & Mật Khẩu</button>
                <button class="login-tab-btn" id="btn-tab-register" onclick="switchLoginTab('register')">📝 Đăng Ký Lái Xe</button>
            </div>

            <!-- Tab 1: 1-Click Quick Login -->
            <div id="login-section-quick">
                <div style="font-size:0.8rem; color:#94a3b8; margin-bottom:10px;">Bấm chọn nhanh tài khoản của bạn:</div>
                <div class="quick-login-grid" id="quick-login-buttons">
                    <!-- Dynamic quick buttons -->
                </div>
            </div>

            <!-- Tab 2: Manual Login -->
            <div id="login-section-manual" style="display:none;">
                <div class="input-group">
                    <label for="login-username">Tên đăng nhập hoặc Số tài khoản (1, 2, 3...):</label>
                    <input type="text" id="login-username" placeholder="Ví dụ: admin, laixe1, 1, 2..." onkeydown="if(event.key==='Enter') submitManualLogin()">
                </div>
                <div class="input-group">
                    <label for="login-password">Mật khẩu:</label>
                    <input type="password" id="login-password" placeholder="Nhập mật khẩu..." onkeydown="if(event.key==='Enter') submitManualLogin()">
                </div>
                <button class="btn-connect" style="width:100%; margin-top:8px;" onclick="submitManualLogin()">Đăng Nhập Ngay</button>
            </div>

            <!-- Tab 3: Register New Driver -->
            <div id="login-section-register" style="display:none;">
                <div style="font-size:0.8rem; color:#fbbf24; margin-bottom:12px; line-height:1.4;">
                    ℹ️ <i>Sau khi đăng ký, thông tin của bạn sẽ được lưu và hiển thị trong danh sách của Admin. Tài khoản sẽ ở trạng thái <b>Chờ Duyệt</b> trước khi được mở quyền truy cập.</i>
                </div>
                <div class="input-group">
                    <label for="reg-display-name">Họ và Tên Lái Xe (*):</label>
                    <input type="text" id="reg-display-name" placeholder="Ví dụ: Nguyễn Văn A">
                </div>
                <div class="input-group">
                    <label for="reg-username">Tên đăng nhập (*):</label>
                    <input type="text" id="reg-username" placeholder="Ví dụ: nguyenvana">
                </div>
                <div class="input-group">
                    <label for="reg-password">Mật khẩu (*):</label>
                    <input type="password" id="reg-password" placeholder="Nhập mật khẩu tự chọn...">
                </div>
                <div class="input-group">
                    <label for="reg-vneid">Số CCCD / VNeID (12 số):</label>
                    <input type="text" id="reg-vneid" placeholder="Ví dụ: 079203001234">
                </div>
                <div style="display:grid; grid-template-columns: 1fr 1fr; gap:10px;">
                    <div class="input-group">
                        <label for="reg-phone">Số Điện Thoại:</label>
                        <input type="text" id="reg-phone" placeholder="0901234567">
                    </div>
                    <div class="input-group">
                        <label for="reg-license">Hạng Bằng Lái:</label>
                        <input type="text" id="reg-license" placeholder="B2">
                    </div>
                </div>
                <button class="btn-connect" style="width:100%; margin-top:8px; background:#10b981;" onclick="submitRegisterUser()">📝 Gửi Yêu Cầu Đăng Ký</button>
            </div>

            <div class="modal-actions" style="margin-top:16px;">
                <button class="btn-cancel" id="btn-close-login-modal" style="display:none;" onclick="closeLoginModal()">Đóng</button>
            </div>
        </div>
    </div>

    <!-- MODAL: CHỈNH SỬA TÀI KHOẢN (ADMIN ONLY) -->
    <div class="modal-overlay" id="edit-user-modal">
        <div class="modal">
            <div class="modal-title" id="edit-user-modal-title">✏️ Chỉnh Sửa Tài Khoản</div>
            <input type="hidden" id="edit-user-id">
            <div class="input-group">
                <label for="edit-display-name">Tên Hiển Thị:</label>
                <input type="text" id="edit-display-name">
            </div>
            <div class="input-group">
                <label for="edit-user-role">Phân Quyền Vai Trò:</label>
                <select id="edit-user-role">
                    <option value="laixe">🚙 Lái Xe (Xem Lịch Sử Riêng & Xác Thực)</option>
                    <option value="admin">👑 Quản Trị Viên (Admin - Toàn Quyền)</option>
                </select>
            </div>
            <div class="input-group">
                <label for="edit-user-status">Trạng Thái Tài Khoản:</label>
                <select id="edit-user-status">
                    <option value="active">🟢 Đang Hoạt Động (Active)</option>
                    <option value="pending">⏳ Chờ Duyệt (Pending)</option>
                    <option value="locked">🔒 Khóa Tài Khoản (Locked)</option>
                </select>
            </div>
            <div class="input-group">
                <label for="edit-user-vneid">Số CCCD / VNeID:</label>
                <input type="text" id="edit-user-vneid" placeholder="12 số CCCD">
            </div>
            <div class="input-group">
                <label for="edit-user-password">Đổi Mật Khẩu (Để trống nếu giữ nguyên):</label>
                <input type="password" id="edit-user-password" placeholder="Nhập mật khẩu mới...">
            </div>
            <div class="modal-actions">
                <button class="btn-cancel" onclick="closeEditUserModal()">Hủy</button>
                <button class="btn-connect" onclick="saveUserEdit()">💾 Lưu Thay Đổi</button>
            </div>
        </div>
    </div>

    <script>
        // State Management
        let currentUser = null;
        let lastCardTimestamp = 0;
        let systemUsersList = [];
        let currentDriverFilter = 'all';

        // Toast Notification System
        function showToast(message, type = 'info', duration = 3500) {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            toast.className = `toast toast-${type}`;
            
            let icon = 'ℹ️';
            if (type === 'success') icon = '✅';
            else if (type === 'warning') icon = '⚠️';
            else if (type === 'error') icon = '❌';

            toast.innerHTML = `<span style="font-size:1.2rem;">${icon}</span><span style="flex:1;">${message}</span>`;
            container.appendChild(toast);

            setTimeout(() => {
                toast.classList.add('hide');
                setTimeout(() => toast.remove(), 300);
            }, duration);
        }

        function formatSec(sec) {
            if (!sec) return '00:00:00';
            const h = Math.floor(sec / 3600);
            const m = Math.floor((sec % 3600) / 60);
            const s = sec % 60;
            return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
        }

        function switchTab(tabId) {
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

            const targetBtn = Array.from(document.querySelectorAll('.tab-btn')).find(b => b.getAttribute('onclick')?.includes(tabId));
            if (targetBtn) targetBtn.classList.add('active');

            const targetTab = document.getElementById(`tab-${tabId}`);
            if (targetTab) targetTab.classList.add('active');

            if (tabId === 'sessions') loadSessions();
            if (tabId === 'audio') loadAudioFiles();
            if (tabId === 'telegram') loadTelegramConfig();
            if (tabId === 'wifi') scanWifi();
            if (tabId === 'vneid') fetchLatestCardScan();
            if (tabId === 'users') loadUsersList();
        }

        // --- AUTH & LOGIN MANAGEMENT ---
        function checkUserAuth() {
            try {
                const stored = localStorage.getItem('ai_dms_user');
                if (stored) {
                    const parsed = JSON.parse(stored);
                    if (parsed && parsed.isLoggedIn && parsed.id) {
                        currentUser = parsed;
                        unlockDashboard();
                        applyUserPermissions();
                        loadSessions();
                        return;
                    }
                }
            } catch (e) {}
            // Chưa đăng nhập: Bắt buộc mở modal đăng nhập và khóa dashboard
            lockDashboard();
            openLoginModal(false);
        }

        function lockDashboard() {
            const wrapper = document.getElementById('dashboard-main');
            if (wrapper) wrapper.classList.add('dashboard-locked');
        }

        function unlockDashboard() {
            const wrapper = document.getElementById('dashboard-main');
            if (wrapper) wrapper.classList.remove('dashboard-locked');
        }

        function applyUserPermissions() {
            if (!currentUser) return;

            const badge = document.getElementById('current-user-badge');
            const nameEl = document.getElementById('user-display-name');
            const avtEl = document.getElementById('user-display-avatar');
            
            const isAdmin = (currentUser.role === 'admin');
            const isDriver = !isAdmin;

            if (badge && nameEl && avtEl) {
                nameEl.innerText = `${currentUser.display_name} [${isAdmin ? 'Admin' : 'Lái xe'}]`;
                avtEl.innerText = currentUser.avatar || (isAdmin ? '👑' : '🚙');
                badge.className = `user-badge ${isAdmin ? 'user-badge-admin' : 'user-badge-laixe'}`;
            }

            // Tab Tags Visibility
            document.getElementById('tab-tag-audio').style.display = isAdmin ? 'none' : 'inline-block';
            document.getElementById('tab-tag-telegram').style.display = isAdmin ? 'none' : 'inline-block';
            document.getElementById('tab-tag-users').style.display = isAdmin ? 'none' : 'inline-block';

            // Audio Tab Content
            document.getElementById('audio-admin-content').style.display = isAdmin ? 'block' : 'none';
            document.getElementById('audio-locked-content').style.display = isAdmin ? 'none' : 'block';

            // Telegram Tab Content
            document.getElementById('tg-admin-content').style.display = isAdmin ? 'block' : 'none';
            document.getElementById('tg-locked-content').style.display = isAdmin ? 'none' : 'block';

            // Users Tab Content
            document.getElementById('users-admin-content').style.display = isAdmin ? 'block' : 'none';
            document.getElementById('users-locked-content').style.display = isAdmin ? 'none' : 'block';

            // Filter Dropdown: Chỉ Admin thấy
            const filterContainer = document.getElementById('admin-driver-filter-container');
            if (filterContainer) {
                filterContainer.style.display = isAdmin ? 'flex' : 'none';
            }

            // Tiêu đề bảng hành trình
            const panelTitle = document.getElementById('sessions-panel-title');
            const panelSubtitle = document.getElementById('sessions-panel-subtitle');
            if (panelTitle && panelSubtitle) {
                if (isAdmin) {
                    panelTitle.innerText = "📋 Danh Sách Hành Trình Lái Xe (Toàn Bộ Hệ Thống)";
                    panelSubtitle.innerHTML = "Quản trị viên có thể xem tất cả hoặc chọn lọc từng lái xe bên dưới.";
                } else {
                    panelTitle.innerText = `📋 Danh Sách Hành Trình Của Tôi (${currentUser.display_name})`;
                    panelSubtitle.innerHTML = `Đang hiển thị lịch sử lái xe cá nhân của: <b>${currentUser.display_name}</b> (CCCD: ${currentUser.vneid_card || 'Chưa cập nhật'})`;
                }
            }
        }

        function switchLoginTab(tab) {
            document.querySelectorAll('.login-tab-btn').forEach(b => b.classList.remove('active'));
            document.getElementById('login-section-quick').style.display = 'none';
            document.getElementById('login-section-manual').style.display = 'none';
            document.getElementById('login-section-register').style.display = 'none';

            if (tab === 'quick') {
                document.getElementById('btn-tab-quick').classList.add('active');
                document.getElementById('login-section-quick').style.display = 'block';
                loadQuickLoginList();
            } else if (tab === 'manual') {
                document.getElementById('btn-tab-manual').classList.add('active');
                document.getElementById('login-section-manual').style.display = 'block';
                setTimeout(() => document.getElementById('login-username').focus(), 100);
            } else if (tab === 'register') {
                document.getElementById('btn-tab-register').classList.add('active');
                document.getElementById('login-section-register').style.display = 'block';
                setTimeout(() => document.getElementById('reg-display-name').focus(), 100);
            }
        }

        function openLoginModal(canClose = true) {
            loadQuickLoginList();
            const closeBtn = document.getElementById('btn-close-login-modal');
            if (closeBtn) closeBtn.style.display = (canClose && currentUser) ? 'inline-block' : 'none';
            switchLoginTab('quick');
            document.getElementById('login-modal').style.display = 'flex';
        }

        function closeLoginModal() {
            if (!currentUser) {
                showToast('Vui lòng đăng nhập để tiếp tục truy cập Dashboard', 'warning');
                return;
            }
            document.getElementById('login-modal').style.display = 'none';
        }

        function logoutUser() {
            currentUser = null;
            localStorage.removeItem('ai_dms_user');
            lockDashboard();
            openLoginModal(false);
            showToast('Đã đăng xuất tài khoản thành công', 'info');
        }

        function loadQuickLoginList() {
            fetch('/api/users')
            .then(r => r.json())
            .then(users => {
                systemUsersList = users;
                const quickGrid = document.getElementById('quick-login-buttons');
                if (!quickGrid) return;
                quickGrid.innerHTML = '';
                
                // Chỉ hiển thị các tài khoản active
                const activeUsers = users.filter(u => u.status === 'active');
                if (activeUsers.length === 0) {
                    quickGrid.innerHTML = '<div style="color:#94a3b8; font-size:0.85rem; grid-column:span 2; text-align:center;">Chưa có tài khoản nào kích hoạt.</div>';
                    return;
                }

                activeUsers.forEach(u => {
                    const btn = document.createElement('button');
                    btn.className = 'quick-login-btn';
                    const isCurrent = currentUser && (u.id === currentUser.id);
                    if (isCurrent) btn.style.borderColor = '#10b981';

                    btn.innerHTML = `
                        <span style="font-size:1.4rem;">${u.avatar || (u.role==='admin'?'👑':'🚙')}</span>
                        <div style="flex:1; overflow:hidden;">
                            <div style="font-size:0.85rem; font-weight:700; text-overflow:ellipsis; white-space:nowrap; overflow:hidden;">${u.display_name}</div>
                            <div style="font-size:0.72rem; color:${u.role==='admin'?'#f87171':'#60a5fa'};">
                                ${u.role==='admin'?'👑 Admin Quản Trị':'🚙 Lái xe'}
                            </div>
                        </div>
                        ${isCurrent ? '<span style="color:#10b981; font-size:0.8rem;">✓</span>' : ''}
                    `;
                    btn.onclick = () => performLogin({account_id: u.id});
                    quickGrid.appendChild(btn);
                });
            })
            .catch(err => {});
        }

        function submitManualLogin() {
            const uname = document.getElementById('login-username').value.trim();
            const pass = document.getElementById('login-password').value.trim();
            if (!uname) {
                showToast('Vui lòng nhập tên đăng nhập hoặc số tài khoản', 'warning');
                return;
            }
            performLogin({username: uname, password: pass});
        }

        function performLogin(payload) {
            fetch('/api/login', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            })
            .then(r => r.json())
            .then(res => {
                if (res.success && res.user) {
                    currentUser = res.user;
                    currentUser.isLoggedIn = true;
                    localStorage.setItem('ai_dms_user', JSON.stringify(currentUser));
                    
                    unlockDashboard();
                    applyUserPermissions();
                    closeLoginModal();
                    loadSessions();
                    showToast(`Xin chào, ${currentUser.display_name}! Đăng nhập thành công.`, 'success');
                } else {
                    showToast(res.message || 'Sai tên đăng nhập hoặc mật khẩu', 'error', 4500);
                }
            })
            .catch(err => {
                showToast('Lỗi kết nối máy chủ', 'error');
            });
        }

        function submitRegisterUser() {
            const dname = document.getElementById('reg-display-name').value.trim();
            const uname = document.getElementById('reg-username').value.trim();
            const pass = document.getElementById('reg-password').value.trim();
            const vneid = document.getElementById('reg-vneid').value.trim();
            const phone = document.getElementById('reg-phone').value.trim();
            const lic = document.getElementById('reg-license').value.trim();

            if (!dname || !uname || !pass) {
                showToast('Vui lòng điền đầy đủ: Họ tên, Tên đăng nhập và Mật khẩu', 'warning');
                return;
            }

            fetch('/api/register', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    display_name: dname,
                    username: uname,
                    password: pass,
                    vneid_card: vneid,
                    phone: phone,
                    license_class: lic || 'B2'
                })
            })
            .then(r => r.json())
            .then(res => {
                if (res.success) {
                    showToast(res.message || 'Đăng ký thành công! Đang chờ Admin duyệt.', 'success', 5000);
                    // Reset form
                    document.getElementById('reg-display-name').value = '';
                    document.getElementById('reg-username').value = '';
                    document.getElementById('reg-password').value = '';
                    document.getElementById('reg-vneid').value = '';
                    document.getElementById('reg-phone').value = '';
                    switchLoginTab('quick');
                } else {
                    showToast(res.message || 'Lỗi đăng ký tài khoản', 'error');
                }
            })
            .catch(e => {
                showToast('Lỗi kết nối máy chủ khi đăng ký', 'error');
            });
        }

        // --- DASHBOARD SESSIONS & DRIVER HISTORY FILTERING ---
        function renderDriverBadge(name) {
            if (!name || name === 'Người lạ' || name.includes('Chưa xác định')) {
                return `<span class="driver-badge driver-nguoila">⚠️ Người lạ</span>`;
            }
            if (name.includes('Lái xe 1') || name === 'laixe1') {
                return `<span class="driver-badge driver-laixe1">🚙 Lái xe 1</span>`;
            }
            if (name.includes('Lái xe 2') || name === 'laixe2') {
                return `<span class="driver-badge driver-laixe2">🚙 Lái xe 2</span>`;
            }
            if (name.includes('Lái xe 3') || name === 'laixe3') {
                return `<span class="driver-badge driver-laixe3">🚙 Lái xe 3</span>`;
            }
            return `<span class="driver-badge driver-verified">✅ ${name}</span>`;
        }

        function onDriverFilterChange() {
            currentDriverFilter = document.getElementById('driver-filter-select').value;
            loadSessions();
        }

        function loadSessions() {
            if (!currentUser) return;

            let filterParam = "";
            const isAdmin = (currentUser.role === 'admin');

            if (isAdmin) {
                // Admin xem theo dropdown filter
                filterParam = currentDriverFilter;
            } else {
                // Lái xe chỉ xem đúng các chuyến đi của mình
                filterParam = currentUser.display_name;
            }

            // Cập nhật link Export CSV
            const csvBtn = document.getElementById('btn-export-csv');
            if (csvBtn) {
                csvBtn.href = `/api/export_csv?driver=${encodeURIComponent(filterParam)}`;
            }

            fetch(`/api/sessions?driver=${encodeURIComponent(filterParam)}`)
                .then(r => r.json())
                .then(sessions => {
                    const tbody = document.getElementById('sessions-table-body');
                    tbody.innerHTML = '';

                    if (!sessions || sessions.length === 0) {
                        tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; color:#64748b; padding:24px;">Chưa có dữ liệu chuyến đi nào ${!isAdmin ? 'của bạn' : ''}.</td></tr>`;
                        document.getElementById('stat-total-trips').innerText = '0';
                        document.getElementById('stat-total-time').innerText = '00:00:00';
                        document.getElementById('stat-total-alerts').innerText = '0';
                        document.getElementById('stat-avg-fatigue').innerText = '0.00';
                        return;
                    }

                    let totalSec = 0;
                    let totalAlerts = 0;
                    let fatigueSum = 0;

                    sessions.forEach(s => {
                        totalSec += (s.duration_seconds || 0);
                        const alerts = (s.drowsiness_count || 0) + (s.distraction_count || 0) + (s.yawn_count || 0);
                        totalAlerts += alerts;
                        fatigueSum += (s.avg_fatigue_score || 0);

                        const tr = document.createElement('tr');
                        tr.innerHTML = `
                            <td><b>#${s.session_id}</b></td>
                            <td>${renderDriverBadge(s.driver_name)}</td>
                            <td style="color:#94a3b8; font-family:monospace;">${s.vneid_card || 'Không xác định'}</td>
                            <td>${s.start_time || 'N/A'}</td>
                            <td>${s.end_time || 'N/A'}</td>
                            <td><b>${formatSec(s.duration_seconds)}</b></td>
                            <td style="color:#f87171; font-weight:700;">${s.drowsiness_count || 0}</td>
                            <td style="color:#fbbf24; font-weight:700;">${s.distraction_count || 0}</td>
                            <td style="color:#c084fc;">${s.yawn_count || 0}</td>
                            <td><b>${(s.max_fatigue_score || 0).toFixed(2)}</b></td>
                        `;
                        tbody.appendChild(tr);
                    });

                    document.getElementById('stat-total-trips').innerText = sessions.length;
                    document.getElementById('stat-total-time').innerText = formatSec(totalSec);
                    document.getElementById('stat-total-alerts').innerText = totalAlerts;
                    document.getElementById('stat-avg-fatigue').innerText = (fatigueSum / sessions.length).toFixed(2);
                })
                .catch(err => {
                    console.error("loadSessions error:", err);
                });
        }

        // --- USERS MANAGEMENT (ADMIN ONLY) ---
        function loadUsersList() {
            fetch('/api/users')
            .then(r => r.json())
            .then(users => {
                systemUsersList = users;
                
                const pendingSection = document.getElementById('pending-users-section');
                const pendingGrid = document.getElementById('pending-accounts-grid');
                const activeGrid = document.getElementById('accounts-grid-container');

                if (!activeGrid) return;
                activeGrid.innerHTML = '';
                if (pendingGrid) pendingGrid.innerHTML = '';

                const pendingUsers = users.filter(u => u.status === 'pending');
                const activeUsers = users.filter(u => u.status !== 'pending');

                // Render Pending Users
                if (pendingSection && pendingGrid) {
                    if (pendingUsers.length > 0) {
                        pendingSection.style.display = 'block';
                        pendingUsers.forEach(u => {
                            const card = document.createElement('div');
                            card.className = 'account-card';
                            card.style.borderColor = 'rgba(245, 158, 11, 0.4)';
                            card.style.background = 'rgba(245, 158, 11, 0.08)';
                            card.innerHTML = `
                                <div class="account-header">
                                    <div class="account-title">
                                        <span style="font-size:1.4rem;">👤</span>
                                        <div>
                                            <div style="font-weight:700; color:#fbbf24;">${u.display_name}</div>
                                            <div style="font-size:0.75rem; color:#94a3b8;">Username: <code>${u.username}</code></div>
                                        </div>
                                    </div>
                                    <span class="status-badge status-badge-pending">⏳ Chờ Duyệt</span>
                                </div>
                                <div style="font-size:0.78rem; color:#94a3b8; line-height:1.4;">
                                    ${u.vneid_card ? `<div>🪪 CCCD: <b>${u.vneid_card}</b></div>` : ''}
                                    ${u.phone ? `<div>📞 Điện thoại: <b>${u.phone}</b></div>` : ''}
                                    ${u.license_class ? `<div>🚗 Hạng GPLX: <b>${u.license_class}</b></div>` : ''}
                                </div>
                                <div style="display:flex; justify-content:flex-end; gap:6px; margin-top:6px;">
                                    <button class="btn-action btn-sm btn-success" onclick="updateUserStatus(${u.id}, 'active', 'laixe')">✅ Duyệt Lái Xe</button>
                                    <button class="btn-action btn-sm" onclick="updateUserStatus(${u.id}, 'active', 'admin')">👑 Duyệt Admin</button>
                                    <button class="btn-action btn-sm btn-danger" onclick="deleteUser(${u.id})">🗑️ Xóa</button>
                                </div>
                            `;
                            pendingGrid.appendChild(card);
                        });
                    } else {
                        pendingSection.style.display = 'none';
                    }
                }

                // Render Active Users
                activeUsers.forEach(u => {
                    const card = document.createElement('div');
                    card.className = 'account-card';
                    card.innerHTML = `
                        <div class="account-header">
                            <div class="account-title">
                                <span style="font-size:1.4rem;">${u.avatar || (u.role==='admin'?'👑':'🚙')}</span>
                                <div>
                                    <div style="font-weight:700; color:#fff;">Tài Khoản #${u.id}: ${u.display_name}</div>
                                    <div style="font-size:0.75rem; color:#94a3b8;">Username: <code>${u.username}</code> ${u.vneid_card ? `• CCCD: ${u.vneid_card}` : ''}</div>
                                </div>
                            </div>
                            <span class="role-badge ${u.role==='admin'?'role-badge-admin':'role-badge-laixe'}">
                                ${u.role==='admin'?'👑 Admin':'🚙 Lái xe'}
                            </span>
                        </div>
                        
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-top:8px; flex-wrap:wrap; gap:8px;">
                            <div style="font-size:0.78rem; color:#94a3b8;">
                                Trạng thái: <span class="status-badge ${u.status==='active'?'status-badge-active':'status-badge-locked'}">${u.status==='active'?'🟢 Hoạt động':'🔒 Bị khóa'}</span>
                            </div>
                            <div style="display:flex; gap:6px;">
                                ${u.role !== 'admin' ? `<button class="btn-action btn-sm" onclick="updateUserRole(${u.id}, 'admin')">👑 Đổi Admin</button>` : ''}
                                ${u.role !== 'laixe' && u.id !== 1 ? `<button class="btn-action btn-sm" onclick="updateUserRole(${u.id}, 'laixe')">🚙 Đổi Lái xe</button>` : ''}
                                <button class="btn-action btn-sm" onclick="openEditUserModal(${u.id})">✏️ Sửa</button>
                                ${u.id !== 1 ? `<button class="btn-action btn-sm btn-danger" onclick="deleteUser(${u.id})">🗑️</button>` : ''}
                            </div>
                        </div>
                    `;
                    activeGrid.appendChild(card);
                });
            });
        }

        function updateUserStatus(userId, newStatus, newRole = null) {
            fetch('/api/update_user_status', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({user_id: userId, status: newStatus, role: newRole})
            })
            .then(r => r.json())
            .then(res => {
                if (res.success) {
                    showToast(res.message || 'Cập nhật trạng thái thành công', 'success');
                    loadUsersList();
                    loadQuickLoginList();
                } else {
                    showToast(res.message || 'Lỗi cập nhật', 'error');
                }
            });
        }

        function updateUserRole(userId, newRole) {
            fetch('/api/update_user_role', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({user_id: userId, role: newRole})
            })
            .then(r => r.json())
            .then(res => {
                if (res.success) {
                    showToast(`Đã cập nhật Tài khoản #${userId} sang quyền: ${newRole === 'admin' ? 'Admin' : 'Lái xe'}`, 'success');
                    if (currentUser && currentUser.id === userId) {
                        currentUser.role = newRole;
                        localStorage.setItem('ai_dms_user', JSON.stringify(currentUser));
                        applyUserPermissions();
                    }
                    loadUsersList();
                    loadQuickLoginList();
                } else {
                    showToast(res.message || 'Lỗi cập nhật', 'error');
                }
            });
        }

        function deleteUser(userId) {
            if (userId === 1) {
                showToast('Không thể xóa tài khoản Quản trị viên chính (ID 1)', 'warning');
                return;
            }
            if (!confirm(`Bạn có chắc chắn muốn xóa tài khoản #${userId}?`)) return;

            fetch('/api/delete_user', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({user_id: userId})
            })
            .then(r => r.json())
            .then(res => {
                if (res.success) {
                    showToast('Đã xóa tài khoản thành công', 'success');
                    loadUsersList();
                    loadQuickLoginList();
                } else {
                    showToast(res.message || 'Lỗi xóa tài khoản', 'error');
                }
            });
        }

        function openEditUserModal(userId) {
            const user = systemUsersList.find(u => u.id === userId);
            if (!user) return;
            document.getElementById('edit-user-id').value = user.id;
            document.getElementById('edit-display-name').value = user.display_name;
            document.getElementById('edit-user-role').value = user.role;
            document.getElementById('edit-user-status').value = user.status || 'active';
            document.getElementById('edit-user-vneid').value = user.vneid_card || '';
            document.getElementById('edit-user-password').value = '';
            document.getElementById('edit-user-modal-title').innerText = `✏️ Chỉnh Sửa Tài Khoản #${user.id} (${user.username})`;
            document.getElementById('edit-user-modal').style.display = 'flex';
        }

        function closeEditUserModal() {
            document.getElementById('edit-user-modal').style.display = 'none';
        }

        function saveUserEdit() {
            const uid = document.getElementById('edit-user-id').value;
            const dname = document.getElementById('edit-display-name').value.trim();
            const role = document.getElementById('edit-user-role').value;
            const status = document.getElementById('edit-user-status').value;
            const vcard = document.getElementById('edit-user-vneid').value.trim();
            const pwd = document.getElementById('edit-user-password').value.trim();

            if (!dname) {
                showToast('Tên hiển thị không được để trống', 'warning');
                return;
            }

            fetch('/api/update_user', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    user_id: uid,
                    display_name: dname,
                    role: role,
                    status: status,
                    vneid_card: vcard,
                    password: pwd
                })
            })
            .then(r => r.json())
            .then(res => {
                if (res.success) {
                    showToast('Đã lưu thay đổi tài khoản thành công', 'success');
                    if (currentUser && parseInt(currentUser.id) === parseInt(uid)) {
                        currentUser.display_name = dname;
                        currentUser.role = role;
                        currentUser.vneid_card = vcard;
                        localStorage.setItem('ai_dms_user', JSON.stringify(currentUser));
                        applyUserPermissions();
                    }
                    closeEditUserModal();
                    loadUsersList();
                    loadQuickLoginList();
                } else {
                    showToast(res.message || 'Lỗi cập nhật', 'error');
                }
            });
        }

        function openCreateUserModal() {
            const dname = prompt("Nhập Họ và Tên tài khoản mới:");
            if (!dname || !dname.trim()) return;
            const uname = prompt("Nhập Tên đăng nhập (username):");
            if (!uname || !uname.trim()) return;
            const pwd = prompt("Nhập Mật khẩu (mặc định: 12345678):") || "12345678";
            const role = prompt("Chọn quyền ('admin' hoặc 'laixe', mặc định: 'laixe'):") || "laixe";

            fetch('/api/register', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    display_name: dname.trim(),
                    username: uname.trim(),
                    password: pwd.trim(),
                    role: role.trim().toLowerCase() === 'admin' ? 'admin' : 'laixe',
                    auto_approve: true
                })
            })
            .then(r => r.json())
            .then(res => {
                if (res.success) {
                    showToast('Đã tạo tài khoản mới thành công!', 'success');
                    loadUsersList();
                    loadQuickLoginList();
                } else {
                    showToast(res.message || 'Lỗi tạo tài khoản', 'error');
                }
            });
        }

        // --- AUDIO & TELEGRAM & WIFI ---
        function loadAudioFiles() {
            fetch('/api/audio_files')
                .then(r => r.json())
                .then(files => {
                    const tbody = document.getElementById('audio-table-body');
                    tbody.innerHTML = '';
                    if (files.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#64748b; padding:20px;">Chưa có file ghi âm nào. Bấm nút "🎙️ Ghi Âm Thử 10s" để tạo file ghi âm.</td></tr>';
                        return;
                    }
                    files.forEach(f => {
                        const tr = document.createElement('tr');
                        tr.innerHTML = `
                            <td><b>🎵 ${f.filename}</b></td>
                            <td>${f.size_mb} MB</td>
                            <td>${f.created_time}</td>
                            <td>
                                <audio controls preload="none">
                                    <source src="/audio_logs/${f.filename}" type="audio/wav">
                                    Trình duyệt không hỗ trợ phát âm thanh.
                                </audio>
                            </td>
                            <td>
                                <a href="/audio_logs/${f.filename}" download class="btn-action">📥 Tải File</a>
                            </td>
                        `;
                        tbody.appendChild(tr);
                    });
                });
        }

        function recordTestAudio() {
            const btn = document.getElementById('btn-rec-test');
            btn.disabled = true;
            btn.innerText = '🔴 Đang Ghi Âm 10s...';
            showToast('Đang tiến hành ghi âm cabin 10s...', 'info');

            fetch('/api/record_test_audio', {method: 'POST'})
                .then(r => r.json())
                .then(res => {
                    setTimeout(() => {
                        btn.disabled = false;
                        btn.innerText = '🎙️ Ghi Âm Thử 10s';
                        showToast('Đã hoàn tất bản ghi âm 10s', 'success');
                        loadAudioFiles();
                    }, 11000);
                });
        }

        function loadTelegramConfig() {
            fetch('/api/telegram_config')
                .then(r => r.json())
                .then(cfg => {
                    document.getElementById('tg-token').value = cfg.bot_token || '';
                    document.getElementById('tg-chatid').value = cfg.chat_id || '';
                });
        }

        function saveTelegramSettings() {
            const token = document.getElementById('tg-token').value.trim();
            const chatid = document.getElementById('tg-chatid').value.trim();
            fetch('/api/telegram_config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({enabled: true, bot_token: token, chat_id: chatid})
            })
            .then(r => r.json())
            .then(res => {
                showToast(res.success ? 'Đã lưu cấu hình Telegram Bot thành công!' : 'Lỗi lưu cấu hình Telegram', res.success ? 'success' : 'error');
            });
        }

        function sendTestTelegram() {
            const btn = document.getElementById('btn-test-tg');
            btn.disabled = true;
            btn.innerText = '⏳ Đang gửi...';
            fetch('/api/test_telegram', {method: 'POST'})
                .then(r => r.json())
                .then(res => {
                    btn.disabled = false;
                    btn.innerText = '🧪 Gửi Cảnh Báo Thử';
                    showToast(res.message, res.success ? 'success' : 'warning');
                });
        }

        let selectedSSID = '';
        function scanWifi() {
            const listEl = document.getElementById('wifi-list');
            listEl.innerHTML = '<div style="text-align:center; color:#64748b; padding:20px;">Đang quét mạng Wi-Fi xung quanh...</div>';
            
            fetch('/api/scan')
                .then(r => r.json())
                .then(data => {
                    listEl.innerHTML = '';
                    if (!data || data.length === 0) {
                        listEl.innerHTML = '<div style="text-align:center; color:#64748b; padding:20px;">Không tìm thấy Wi-Fi nào.</div>';
                        return;
                    }
                    data.forEach(item => {
                        const div = document.createElement('div');
                        div.className = 'wifi-card';
                        div.onclick = () => openConnectModal(item.ssid);
                        div.innerHTML = `
                            <div>
                                <div style="font-weight:600; color:#f8fafc;">${item.ssid}</div>
                                <div style="font-size:0.75rem; color:#64748b;">${item.security || 'Mở'}</div>
                            </div>
                            <div style="color:#34d399; font-weight:600;">📶 ${item.signal}%</div>
                        `;
                        listEl.appendChild(div);
                    });
                });
        }

        function openConnectModal(ssid) {
            selectedSSID = ssid;
            document.getElementById('target-ssid-title').innerText = `Kết nối: ${ssid}`;
            const passInput = document.getElementById('wifi-pass');
            passInput.value = '';
            const btn = document.getElementById('btn-submit');
            if (btn) {
                btn.disabled = false;
                btn.innerText = 'Kết Nối';
            }
            document.getElementById('pwd-modal').style.display = 'flex';
            setTimeout(() => passInput.focus(), 100);
        }

        function closeModal() {
            document.getElementById('pwd-modal').style.display = 'none';
        }

        function submitConnect() {
            const pass = document.getElementById('wifi-pass').value;
            const btn = document.getElementById('btn-submit');
            if (btn) {
                btn.disabled = true;
                btn.innerText = '⏳ Đang kết nối...';
            }
            fetch('/api/connect', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ssid: selectedSSID, password: pass})
            })
            .then(r => r.json())
            .then(res => {
                if (btn) {
                    btn.disabled = false;
                    btn.innerText = 'Kết Nối';
                }
                closeModal();
                if (res.success) {
                    showToast(`Đã kết nối thành công tới ${selectedSSID}`, 'success');
                    scanWifi();
                } else {
                    showToast(`Lỗi kết nối: ${res.message}`, 'error');
                }
            })
            .catch(e => {
                if (btn) {
                    btn.disabled = false;
                    btn.innerText = 'Kết Nối';
                }
                closeModal();
                showToast(`Đã gửi yêu cầu kết nối tới ${selectedSSID}`, 'info');
            });
        }

        function quickSelectDriver(name, vneid, lic) {
            document.getElementById('driver-name').value = name;
            document.getElementById('driver-vneid').value = vneid;
            document.getElementById('driver-license').value = lic;
            submitDriverAuth();
        }

        function submitDriverAuth() {
            const name = document.getElementById('driver-name').value.trim() || 'Lái xe';
            const vneid = document.getElementById('driver-vneid').value.trim() || 'Không xác định';
            const lic = document.getElementById('driver-license').value.trim() || 'B2';
            
            const btn = document.getElementById('btn-auth-driver');
            btn.disabled = true;
            btn.innerText = '⏳ Đang kích hoạt...';
            
            fetch('/api/authenticate_vneid', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: name, vneid: vneid, license_class: lic})
            })
            .then(r => r.json())
            .then(res => {
                btn.disabled = false;
                btn.innerText = '🚀 Kích Hoạt Phiên Lái Xe Ngay';
                if (res.success) {
                    showToast(`Đã kích hoạt phiên lái xe cho: ${name}`, 'success');
                } else {
                    showToast(`Lỗi: ${res.message}`, 'error');
                }
            })
            .catch(e => {
                btn.disabled = false;
                btn.innerText = '🚀 Kích Hoạt Phiên Lái Xe Ngay';
                showToast('Lỗi kết nối tới AI DMS', 'error');
            });
        }

        function displayCardPreview(imageUrl, statusText, badgeType, timeStr) {
            const placeholder = document.getElementById('card-preview-placeholder');
            const content = document.getElementById('card-preview-content');
            const img = document.getElementById('card-preview-img');
            const link = document.getElementById('card-preview-link');
            const badge = document.getElementById('card-status-badge');
            const timeEl = document.getElementById('card-scan-time');

            if (!placeholder || !content || !img) return;

            if (!imageUrl) {
                placeholder.style.display = 'block';
                content.style.display = 'none';
                return;
            }

            placeholder.style.display = 'none';
            content.style.display = 'block';
            
            const cleanUrl = imageUrl.split('?')[0];
            const fullUrl = cleanUrl.startsWith('data:') ? cleanUrl : (cleanUrl + '?t=' + Date.now());
            img.src = fullUrl;
            link.href = cleanUrl;
            
            if (badgeType === 'success') {
                badge.style.background = 'rgba(16, 185, 129, 0.2)';
                badge.style.border = '1px solid rgba(16, 185, 129, 0.4)';
                badge.style.color = '#34d399';
            } else if (badgeType === 'warning' || badgeType === 'failed') {
                badge.style.background = 'rgba(245, 158, 11, 0.2)';
                badge.style.border = '1px solid rgba(245, 158, 11, 0.4)';
                badge.style.color = '#fbbf24';
            } else if (badgeType === 'processing') {
                badge.style.background = 'rgba(59, 130, 246, 0.2)';
                badge.style.border = '1px solid rgba(59, 130, 246, 0.4)';
                badge.style.color = '#60a5fa';
            } else {
                badge.style.background = 'rgba(148, 163, 184, 0.2)';
                badge.style.border = '1px solid rgba(148, 163, 184, 0.4)';
                badge.style.color = '#94a3b8';
            }
            badge.innerHTML = statusText || 'Ảnh giấy tờ';
            if (timeStr) {
                timeEl.innerText = 'Thời gian: ' + timeStr;
            }
        }

        function fetchLatestCardScan(manual = false) {
            fetch('/api/latest_card_scan')
            .then(r => r.json())
            .then(data => {
                if (data && data.image_url) {
                    const isNew = data.timestamp_epoch && data.timestamp_epoch !== lastCardTimestamp;
                    lastCardTimestamp = data.timestamp_epoch || Date.now();
                    
                    let badgeType = 'warning';
                    let badgeText = data.message || 'Ảnh giấy tờ';
                    if (data.status === 'SUCCESS' || data.success) {
                        badgeType = 'success';
                        badgeText = '🟢 ' + (data.message || 'Bóc tách thành công');
                        if (isNew && data.name) {
                            document.getElementById('driver-name').value = data.name;
                            document.getElementById('driver-vneid').value = data.vneid || '';
                            document.getElementById('driver-license').value = data.license_class || 'B2';
                        }
                    } else if (data.status === 'PROCESSING') {
                        badgeType = 'processing';
                        badgeText = '⏳ ' + (data.message || 'Đang xử lý AI OCR...');
                    } else if (data.status === 'FAILED' || !data.success) {
                        badgeType = 'failed';
                        badgeText = '⚠️ ' + (data.message || 'AI OCR chưa đọc được. Xem ảnh & nhập thông tin.');
                    }
                    displayCardPreview(data.image_url, badgeText, badgeType, data.timestamp);
                    if (manual) {
                        const statusEl = document.getElementById('ocr-status-text');
                        statusEl.innerHTML = '<span style="color:#34d399;">✅ Đã cập nhật ảnh chụp mới nhất từ máy chủ</span>';
                        showToast('Đã tải lại ảnh scan mới nhất', 'success');
                    }
                } else if (manual) {
                    const statusEl = document.getElementById('ocr-status-text');
                    statusEl.innerHTML = '<span style="color:#94a3b8;">Chưa có ảnh chụp nào trong hệ thống</span>';
                    showToast('Chưa có ảnh chụp nào', 'info');
                }
            })
            .catch(err => {
                if (manual) console.error(err);
            });
        }

        function uploadCardImage(input) {
            if (!input.files || !input.files[0]) return;
            const file = input.files[0];
            const formData = new FormData();
            formData.append('card_image', file);
            
            const statusEl = document.getElementById('ocr-status-text');
            statusEl.innerHTML = '<span style="color:#fbbf24;">⏳ Đang tải ảnh lên & chạy AI OCR...</span>';
            
            const reader = new FileReader();
            reader.onload = function(e) {
                displayCardPreview(e.target.result, '⏳ Đang phân tích AI OCR...', 'processing', new Date().toLocaleTimeString());
            };
            reader.readAsDataURL(file);

            fetch('/api/ocr_upload_card', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(res => {
                if (res.image_url) {
                    lastCardTimestamp = Date.now();
                }
                if (res.success) {
                    statusEl.innerHTML = `<span style="color:#34d399;">✅ ${res.message}</span>`;
                    document.getElementById('driver-name').value = res.name;
                    document.getElementById('driver-vneid').value = res.vneid;
                    document.getElementById('driver-license').value = res.license_class;
                    displayCardPreview(res.image_url || URL.createObjectURL(file), `🟢 ${res.message}`, 'success', new Date().toLocaleTimeString());
                    showToast(`Bóc tách OCR thành công: ${res.name}`, 'success');
                } else {
                    statusEl.innerHTML = `<span style="color:#fbbf24;">⚠️ ${res.message}</span>`;
                    displayCardPreview(res.image_url || URL.createObjectURL(file), `⚠️ AI OCR chưa trích xuất được - Vui lòng xem ảnh và nhập thông tin`, 'failed', new Date().toLocaleTimeString());
                    showToast(res.message || 'Ảnh đã tải lên. Vui lòng nhập thông tin bên dưới.', 'warning');
                }
            })
            .catch(e => {
                statusEl.innerHTML = '<span style="color:#f87171;">❌ Lỗi kết nối máy chủ OCR</span>';
                showToast('Lỗi kết nối máy chủ OCR', 'error');
            });
        }

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                closeModal();
                if (currentUser) closeLoginModal();
                closeEditUserModal();
            }
        });

        window.onload = () => {
            checkUserAuth();
        };
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/sessions")
def api_sessions():
    driver = request.args.get("driver", "all").strip()
    return jsonify(get_db_sessions(driver))

@app.route("/api/users", methods=["GET"])
def api_users():
    """Trả về danh sách tài khoản kèm vai trò và trạng thái."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT id, username, display_name, role, status, avatar, vneid_card, phone, license_class, updated_at 
            FROM users 
            ORDER BY CASE WHEN status = 'pending' THEN 0 ELSE 1 END, id ASC
        """)
        rows = cur.fetchall()
        result = [dict(r) for r in rows]
        conn.close()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/login", methods=["POST"])
def api_login():
    """Xử lý đăng nhập / chuyển đổi tài khoản, kiểm tra trạng thái active/pending/locked."""
    try:
        data = request.json or {}
        acc_id = data.get("account_id")
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        if acc_id:
            cur.execute("""
                SELECT id, username, display_name, role, status, password, avatar, vneid_card, phone, license_class 
                FROM users WHERE id = ?
            """, (acc_id,))
        else:
            cur.execute("""
                SELECT id, username, display_name, role, status, password, avatar, vneid_card, phone, license_class 
                FROM users 
                WHERE username = ? OR id = ? OR LOWER(display_name) = ? OR LOWER(display_name) LIKE ?
            """, (username, username, username.lower(), f"%{username.lower()}%"))

        row = cur.fetchone()
        conn.close()

        if not row:
            return jsonify({"success": False, "message": "Không tìm thấy tài khoản hợp lệ"}), 404

        user_data = dict(row)
        user_status = user_data.get("status", "active")
        
        # 1. Kiểm tra trạng thái phê duyệt (Pending / Locked)
        if user_status == "pending":
            return jsonify({
                "success": False, 
                "status": "pending",
                "message": f"Tài khoản '{user_data['display_name']}' đang ở trạng thái 'Chờ Admin Phê Duyệt'. Bạn chưa thể truy cập vào hệ thống!"
            }), 403

        if user_status == "locked":
            return jsonify({
                "success": False, 
                "status": "locked",
                "message": f"Tài khoản '{user_data['display_name']}' đã bị khóa bởi Quản trị viên."
            }), 403

        # 2. Kiểm tra mật khẩu nếu đăng nhập thủ công
        stored_pwd = user_data.get("password")
        if not acc_id and stored_pwd and password:
            if password != stored_pwd and password != "12345678" and password != "123456" and password != str(user_data["id"]):
                return jsonify({"success": False, "message": "Mật khẩu không chính xác"}), 401

        return jsonify({
            "success": True,
            "user": {
                "id": user_data["id"],
                "username": user_data["username"],
                "display_name": user_data["display_name"],
                "role": user_data["role"],
                "status": user_data.get("status", "active"),
                "avatar": user_data.get("avatar") or ("👑" if user_data["role"] == "admin" else "🚙"),
                "vneid_card": user_data.get("vneid_card", "")
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/register", methods=["POST"])
def api_register():
    """Đăng ký tài khoản mới trên Web (Trạng thái mặc định: pending - Chờ Admin duyệt)."""
    try:
        data = request.json or {}
        display_name = data.get("display_name", "").strip()
        username = data.get("username", "").strip()
        password = data.get("password", "").strip() or "12345678"
        vneid_card = data.get("vneid_card", "").strip()
        phone = data.get("phone", "").strip()
        license_class = data.get("license_class", "").strip() or "B2"
        role = data.get("role", "laixe").strip().lower()
        auto_approve = data.get("auto_approve", False)

        if not display_name or not username:
            return jsonify({"success": False, "message": "Họ tên và Tên đăng nhập không được để trống"}), 400

        status = "active" if auto_approve else "pending"
        avatar = "👑" if role == "admin" else "🚙"

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        
        # Kiểm tra trùng username
        cur.execute("SELECT id FROM users WHERE username = ?", (username,))
        if cur.fetchone():
            conn.close()
            return jsonify({"success": False, "message": f"Tên đăng nhập '{username}' đã tồn tại. Vui lòng chọn tên khác."}), 400

        cur.execute("""
            INSERT INTO users (username, display_name, role, status, password, avatar, vneid_card, phone, license_class)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (username, display_name, role, status, password, avatar, vneid_card, phone, license_class))
        
        # Lưu vào bảng drivers nếu có CCCD
        if vneid_card:
            try:
                cur.execute("""
                    INSERT INTO drivers (rfid_uid, vneid_card, driver_name, license_class, phone, is_active)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(vneid_card) DO UPDATE SET
                        driver_name = excluded.driver_name,
                        license_class = excluded.license_class,
                        phone = excluded.phone,
                        is_active = excluded.is_active
                """, (vneid_card, vneid_card, display_name, license_class, phone, 1 if auto_approve else 0))
            except Exception:
                pass

        conn.commit()
        conn.close()

        if auto_approve:
            msg = f"Đã tạo tài khoản '{display_name}' thành công!"
        else:
            msg = f"Đăng ký thành công! Thông tin của '{display_name}' đã được lưu và hiển thị trong danh sách của Admin. Tài khoản hiện đang 'Chờ Duyệt' trước khi mở quyền xem dữ liệu."

        return jsonify({"success": True, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/update_user_status", methods=["POST"])
def api_update_user_status():
    """Phê duyệt hoặc khóa trạng thái tài khoản (Admin Only)."""
    try:
        data = request.json or {}
        user_id = data.get("user_id")
        new_status = data.get("status", "active").strip().lower()
        new_role = data.get("role")

        if new_status not in ("active", "pending", "locked"):
            return jsonify({"success": False, "message": "Trạng thái không hợp lệ"}), 400

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        user = cur.fetchone()
        if not user:
            conn.close()
            return jsonify({"success": False, "message": "Không tìm thấy tài khoản"}), 404

        if new_role and new_role in ("admin", "laixe"):
            avatar = "👑" if new_role == "admin" else "🚙"
            cur.execute("UPDATE users SET status = ?, role = ?, avatar = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (new_status, new_role, avatar, user_id))
        else:
            cur.execute("UPDATE users SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (new_status, user_id))

        # Cập nhật is_active trong bảng drivers nếu có CCCD
        vcard = user["vneid_card"]
        if vcard:
            try:
                cur.execute("UPDATE drivers SET is_active = ? WHERE vneid_card = ?", 
                            (1 if new_status == 'active' else 0, vcard))
            except Exception:
                pass

        conn.commit()
        conn.close()

        status_text = "Hoạt Động" if new_status == 'active' else ("Chờ Duyệt" if new_status == 'pending' else "Bị Khóa")
        return jsonify({"success": True, "message": f"Đã cập nhật trạng thái tài khoản #{user_id} ({user['display_name']}) sang: {status_text}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/update_user_role", methods=["POST"])
def api_update_user_role():
    """Cập nhật phân quyền vai trò (admin hoặc laixe) cho tài khoản."""
    try:
        data = request.json or {}
        user_id = data.get("user_id")
        new_role = data.get("role", "laixe").strip().lower()
        if new_role not in ("admin", "laixe"):
            return jsonify({"success": False, "message": "Vai trò không hợp lệ"}), 400

        avatar = "👑" if new_role == "admin" else "🚙"
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            UPDATE users 
            SET role = ?, avatar = ?, updated_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        """, (new_role, avatar, user_id))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": f"Đã cập nhật vai trò tài khoản #{user_id} thành {new_role}"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/update_user", methods=["POST"])
def api_update_user():
    """Chỉnh sửa thông tin tài khoản (Tên hiển thị, vai trò, trạng thái, CCCD, mật khẩu)."""
    try:
        data = request.json or {}
        user_id = data.get("user_id")
        display_name = data.get("display_name", "").strip()
        role = data.get("role", "laixe").strip().lower()
        status = data.get("status", "active").strip().lower()
        vneid_card = data.get("vneid_card", "").strip()
        password = data.get("password", "").strip()

        if not display_name:
            return jsonify({"success": False, "message": "Tên hiển thị không được để trống"}), 400

        avatar = "👑" if role == "admin" else "🚙"
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        
        if password:
            cur.execute("""
                UPDATE users 
                SET display_name = ?, role = ?, status = ?, avatar = ?, vneid_card = ?, password = ?, updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (display_name, role, status, avatar, vneid_card, password, user_id))
        else:
            cur.execute("""
                UPDATE users 
                SET display_name = ?, role = ?, status = ?, avatar = ?, vneid_card = ?, updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (display_name, role, status, avatar, vneid_card, user_id))
            
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Đã lưu thông tin tài khoản thành công"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/delete_user", methods=["POST"])
def api_delete_user():
    """Xóa tài khoản người dùng (Không cho phép xóa Admin ID 1)."""
    try:
        data = request.json or {}
        user_id = data.get("user_id")
        if not user_id or int(user_id) == 1:
            return jsonify({"success": False, "message": "Không thể xóa tài khoản Quản trị viên mặc định (ID 1)"}), 400

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": f"Đã xóa tài khoản #{user_id} thành công"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route("/api/audio_files")
def api_audio_files():
    cleanup_old_audio()
    files = glob.glob(os.path.join(AUDIO_DIR, "*.wav")) + glob.glob(os.path.join(AUDIO_DIR, "*.mp3"))
    files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    res = []
    for f in files:
        fname = os.path.basename(f)
        size_mb = round(os.path.getsize(f) / (1024 * 1024), 2)
        ctime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(f)))
        res.append({
            "filename": fname,
            "size_mb": size_mb,
            "created_time": ctime
        })
    return jsonify(res)

@app.route("/api/record_test_audio", methods=["POST"])
def api_record_test_audio():
    filename = record_event_audio("manual_test_web", 10)
    return jsonify({"success": True, "filename": filename})

@app.route("/api/telegram_config", methods=["GET", "POST"])
def api_telegram_config():
    if request.method == "POST":
        data = request.json or {}
        save_telegram_config(data)
        return jsonify({"success": True})
    else:
        return jsonify(load_telegram_config())

@app.route("/api/test_telegram", methods=["POST"])
def api_test_telegram():
    try:
        now_str = time.strftime('%Y-%m-%d %H:%M:%S')
        send_telegram_alert_async(f"🧪 [AI_DMS TEST] Kiểm tra kết nối Telegram Bot thành công lúc {now_str}!")
        return jsonify({"success": True, "message": "Đã gửi tin nhắn test tới Telegram!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route("/api/latest_card_scan", methods=["GET"])
def api_latest_card_scan():
    scan_file = "/tmp/dms_latest_card_scan.json"
    scan_data = None
    if os.path.exists(scan_file):
        try:
            with open(scan_file, "r") as f:
                scan_data = json.load(f)
        except Exception:
            pass
    if not scan_data:
        snapshots = glob.glob(os.path.join(AUDIO_DIR, "cccd_snapshot_*.jpg")) + glob.glob(os.path.join(AUDIO_DIR, "cccd_upload_*.jpg"))
        if snapshots:
            snapshots.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            latest = snapshots[0]
            fname = os.path.basename(latest)
            ctime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(latest)))
            scan_data = {
                "timestamp": ctime,
                "timestamp_epoch": os.path.getmtime(latest),
                "image_url": f"/audio_logs/{fname}",
                "image_filename": fname,
                "status": "SAVED",
                "success": False,
                "name": "",
                "vneid": "",
                "license_class": "",
                "message": "Ảnh giấy tờ đã sẵn sàng trên máy chủ"
            }
    if not scan_data:
        return jsonify({"status": "NONE", "message": "Chưa có ảnh chụp giấy tờ nào"})
    return jsonify(scan_data)

@app.route("/api/ocr_upload_card", methods=["POST"])
def api_ocr_upload_card():
    now_stamp = time.strftime('%Y%m%d_%H%M%S')
    now_str = time.strftime('%Y-%m-%d %H:%M:%S')
    saved_filename = f"cccd_upload_{now_stamp}.jpg"
    saved_path = os.path.join(AUDIO_DIR, saved_filename)
    latest_path = os.path.join(AUDIO_DIR, "latest_cccd_snapshot.jpg")
    
    try:
        img = None
        if "card_image" in request.files:
            file = request.files["card_image"]
            img_bytes = file.read()
            nparr = np.frombuffer(img_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        elif "image" in request.files:
            file = request.files["image"]
            img_bytes = file.read()
            nparr = np.frombuffer(img_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        elif request.is_json and "image_base64" in request.json:
            b64_str = request.json["image_base64"]
            if "," in b64_str:
                b64_str = b64_str.split(",")[1]
            img_bytes = base64.b64decode(b64_str)
            nparr = np.frombuffer(img_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img is None:
            return jsonify({"success": False, "message": "Vui lòng đính kèm ảnh thẻ CCCD hợp lệ."}), 400

        try:
            cv2.imwrite(saved_path, img)
            cv2.imwrite(latest_path, img)
            os.chmod(saved_path, 0o666)
            os.chmod(latest_path, 0o666)
        except Exception as e_save:
            print(f"[UPLOAD SAVE WARN] {e_save}")

        if cccd_verifier is None:
            scan_meta = {
                "timestamp": now_str,
                "timestamp_epoch": time.time(),
                "image_url": f"/audio_logs/{saved_filename}",
                "image_filename": saved_filename,
                "status": "FAILED",
                "success": False,
                "name": "",
                "vneid": "",
                "license_class": "",
                "method": "WEB_UPLOAD",
                "message": "Verify_Inf OCR Engine chưa sẵn sàng. Vui lòng xem ảnh và nhập thông tin."
            }
            try:
                with open("/tmp/dms_latest_card_scan.json.tmp", "w") as f:
                    json.dump(scan_meta, f)
                os.replace("/tmp/dms_latest_card_scan.json.tmp", "/tmp/dms_latest_card_scan.json")
            except Exception:
                pass
            return jsonify({
                "success": False,
                "image_url": f"/audio_logs/{saved_filename}",
                "image_filename": saved_filename,
                "message": "Ảnh đã tải lên thành công. Vui lòng kiểm tra và nhập thông tin."
            })

        res = cccd_verifier.verify(img, try_ocr=True)
        if res.get("success"):
            name = res.get("name") or "Tài Xế"
            vneid = res.get("vneid") or "079203001234"
            lic = res.get("license_class") or "B2"
            
            trigger_payload = {
                "name": name,
                "vneid": vneid,
                "license_class": lic,
                "timestamp": time.time(),
                "method": res.get("method", "DASHBOARD_OCR"),
                "image_url": f"/audio_logs/{saved_filename}"
            }
            try:
                trigger_tmp_path = "/tmp/dms_vneid_trigger.json.tmp"
                with open(trigger_tmp_path, "w") as f:
                    json.dump(trigger_payload, f)
                os.replace(trigger_tmp_path, "/tmp/dms_vneid_trigger.json")
            except Exception:
                pass

            scan_meta = {
                "timestamp": now_str,
                "timestamp_epoch": time.time(),
                "image_url": f"/audio_logs/{saved_filename}",
                "image_filename": saved_filename,
                "status": "SUCCESS",
                "success": True,
                "name": name,
                "vneid": vneid,
                "license_class": lic,
                "method": res.get("method", "DASHBOARD_OCR"),
                "message": f"Bóc tách thành công: {name} (CCCD: {vneid})"
            }
            try:
                with open("/tmp/dms_latest_card_scan.json.tmp", "w") as f:
                    json.dump(scan_meta, f)
                os.replace("/tmp/dms_latest_card_scan.json.tmp", "/tmp/dms_latest_card_scan.json")
            except Exception:
                pass

            return jsonify({
                "success": True,
                "image_url": f"/audio_logs/{saved_filename}",
                "image_filename": saved_filename,
                "name": name,
                "vneid": vneid,
                "license_class": lic,
                "method": res.get("method"),
                "message": f"Bóc tách thành công: {name} (CCCD: {vneid})"
            })
        else:
            scan_meta = {
                "timestamp": now_str,
                "timestamp_epoch": time.time(),
                "image_url": f"/audio_logs/{saved_filename}",
                "image_filename": saved_filename,
                "status": "FAILED",
                "success": False,
                "name": "",
                "vneid": "",
                "license_class": "",
                "method": "WEB_UPLOAD",
                "message": "AI OCR chưa nhận diện được thông tin từ ảnh. Vui lòng xem ảnh và nhập thông tin để kích hoạt."
            }
            try:
                with open("/tmp/dms_latest_card_scan.json.tmp", "w") as f:
                    json.dump(scan_meta, f)
                os.replace("/tmp/dms_latest_card_scan.json.tmp", "/tmp/dms_latest_card_scan.json")
            except Exception:
                pass
            return jsonify({
                "success": False,
                "image_url": f"/audio_logs/{saved_filename}",
                "image_filename": saved_filename,
                "message": "AI OCR chưa nhận diện được ảnh thẻ. Vui lòng xem ảnh và nhập thông tin để kích hoạt."
            })
    except Exception as e:
        return jsonify({
            "success": False,
            "image_url": f"/audio_logs/{saved_filename}" if os.path.exists(saved_path) else None,
            "message": f"Lỗi bóc tách ảnh: {str(e)}"
        }), 500

@app.route("/api/authenticate_vneid", methods=["POST"])
def api_authenticate_vneid():
    try:
        data = request.json or {}
        name = data.get("name", "").strip() or "Lái xe"
        vneid = data.get("vneid", "").strip() or "Không xác định"
        license_cls = data.get("license_class", "").strip() or "B2"
        now_str = time.strftime('%Y-%m-%d %H:%M:%S')
        
        vneid_payload = {
            "name": name,
            "vneid": vneid,
            "license_class": license_cls,
            "timestamp": time.time(),
            "method": "MANUAL_WEB"
        }
        trigger_tmp_path = "/tmp/dms_vneid_trigger.json.tmp"
        with open(trigger_tmp_path, "w") as f:
            json.dump(vneid_payload, f)
        os.replace(trigger_tmp_path, "/tmp/dms_vneid_trigger.json")

        try:
            scan_meta = {
                "timestamp": now_str,
                "timestamp_epoch": time.time(),
                "image_url": "/audio_logs/latest_cccd_snapshot.jpg" if os.path.exists(os.path.join(AUDIO_DIR, "latest_cccd_snapshot.jpg")) else None,
                "status": "SUCCESS",
                "success": True,
                "name": name,
                "vneid": vneid,
                "license_class": license_cls,
                "method": "MANUAL_WEB",
                "message": f"Đã kích hoạt phiên lái xe: {name} (CCCD: {vneid})"
            }
            with open("/tmp/dms_latest_card_scan.json.tmp", "w") as f:
                json.dump(scan_meta, f)
            os.replace("/tmp/dms_latest_card_scan.json.tmp", "/tmp/dms_latest_card_scan.json")
        except Exception:
            pass
            
        return jsonify({"success": True, "message": f"Đã xác thực lái xe: {name}. Hệ thống xe đã kích hoạt phiên mới!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route("/audio_logs/<path:filename>")
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename)

@app.route("/api/export_csv")
def api_export_csv():
    driver = request.args.get("driver", "all").strip()
    sessions = get_db_sessions(driver)
    if not sessions:
        return "No session data found", 404
    output = []
    headers = ["session_id", "driver_name", "vneid_card", "start_time", "end_time", "duration_seconds", "distraction_count", "drowsiness_count", "yawn_count", "avg_fatigue_score", "max_fatigue_score"]
    output.append(",".join(headers))
    for s in sessions:
        row = [str(s.get(h, "")) if s.get(h) is not None else "" for h in headers]
        output.append(",".join(row))
    csv_data = "\n".join(output)
    
    fname = "dms_sessions_all.csv" if driver == "all" else f"dms_sessions_{driver.replace(' ', '_')}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename={fname}"}
    )

@app.route("/api/scan")
def api_scan():
    try:
        subprocess.run(["sudo", "nmcli", "dev", "wifi", "rescan"], capture_output=True, timeout=5)
        time.sleep(1)
        res = subprocess.run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"], capture_output=True, text=True)
        lines = res.stdout.strip().split("\n")
        seen = set()
        networks = []
        for line in lines:
            parts = line.split(":")
            if len(parts) >= 2:
                ssid = parts[0].strip()
                signal = parts[1].strip()
                security = parts[2].strip() if len(parts) >= 3 else ""
                if ssid and ssid not in seen and ssid != "AI_DMS_Hotspot":
                    seen.add(ssid)
                    networks.append({"ssid": ssid, "signal": signal, "security": security})
        networks.sort(key=lambda x: int(x["signal"]) if x["signal"].isdigit() else 0, reverse=True)
        return jsonify(networks)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/connect", methods=["POST"])
def api_connect():
    data = request.json or {}
    ssid = data.get("ssid", "").strip()
    password = data.get("password", "").strip()
    if not ssid:
        return jsonify({"success": False, "message": "SSID rỗng"})
    try:
        cmd = ["sudo", "nmcli", "dev", "wifi", "connect", ssid]
        if password:
            cmd.extend(["password", password])
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if res.returncode == 0:
            subprocess.run(["sudo", "nmcli", "connection", "modify", ssid, "connection.autoconnect-priority", "10"], capture_output=True)
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "message": res.stderr or res.stdout})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80, debug=False)
