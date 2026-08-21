import subprocess
import json
import time
import os
import sqlite3
import glob
from flask import Flask, render_template_string, request, jsonify, Response, send_from_directory
from audio_manager import cleanup_old_audio, AUDIO_DIR, ensure_audio_dir, record_event_audio
from telegram_bot import load_telegram_config, save_telegram_config, send_telegram_alert_async

app = Flask(__name__)
DB_PATH = "/home/kata/Documents/AI_DMS/dms_history.db"

ensure_audio_dir()

def get_db_sessions():
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT session_id, start_time, end_time, duration_seconds, 
                   distraction_count, drowsiness_count, yawn_count, 
                   avg_fatigue_score, max_fatigue_score,
                   COALESCE(driver_name, 'Nguyễn Văn A') AS driver_name,
                   COALESCE(vneid_card, '012345678910') AS vneid_card
            FROM dms_sessions 
            ORDER BY session_id DESC
        """)
        rows = cursor.fetchall()
        result = [dict(row) for row in rows]
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
    <title>AI DMS - Local Control & Telegram Alert Dashboard</title>
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
            padding: 24px 16px;
        }

        .main-wrapper {
            max-width: 1000px;
            margin: 0 auto;
        }

        .app-header {
            background: rgba(30, 41, 59, 0.6);
            backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 20px;
            padding: 20px 24px;
            margin-bottom: 24px;
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

        .nav-tabs {
            display: flex;
            background: rgba(15, 23, 42, 0.6);
            padding: 4px;
            border-radius: 14px;
            border: 1px solid rgba(255, 255, 255, 0.05);
            gap: 4px;
            flex-wrap: wrap;
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
            margin-bottom: 24px;
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
        }
        .data-table td { padding: 12px 14px; border-bottom: 1px solid rgba(255, 255, 255, 0.05); color: #e2e8f0; }

        .btn-action {
            padding: 8px 16px; background: rgba(99, 102, 241, 0.2);
            border: 1px solid rgba(99, 102, 241, 0.4); color: #818cf8;
            border-radius: 10px; font-weight: 600; font-size: 0.8rem;
            cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; gap: 6px;
            transition: all 0.2s;
        }

        .btn-action:hover { background: #6366f1; color: #ffffff; }

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
            background: rgba(15, 23, 42, 0.8); backdrop-filter: blur(8px);
            display: none; justify-content: center; align-items: center; z-index: 100; padding: 20px;
        }
        .modal {
            background: #1e293b; border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 20px; padding: 24px; width: 100%; max-width: 400px;
        }
        .modal-title { font-size: 1.1rem; font-weight: 700; margin-bottom: 16px; color: #f8fafc; }
        .input-group { margin-bottom: 20px; }
        .input-group label { display: block; font-size: 0.8rem; color: #94a3b8; margin-bottom: 6px; }
        .input-group input {
            width: 100%; padding: 12px 14px; background: #0f172a;
            border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 10px; color: #ffffff; font-size: 0.95rem;
        }
        .modal-actions { display: flex; gap: 10px; }
        .btn-cancel { flex: 1; padding: 10px; background: transparent; border: 1px solid rgba(255, 255, 255, 0.1); color: #94a3b8; border-radius: 10px; cursor: pointer; }
        .btn-connect { flex: 1; padding: 10px; background: #6366f1; border: none; color: #ffffff; border-radius: 10px; font-weight: 600; cursor: pointer; }
    </style>
</head>
<body>
    <div class="main-wrapper">
        <header class="app-header">
            <div class="brand-box">
                <div class="logo-icon">🚘</div>
                <div>
                    <div class="brand-title">AI DMS Dashboard</div>
                    <div class="brand-desc">Giám sát Lái xe, Cảnh báo Telegram & Ghi Âm Cabin</div>
                </div>
            </div>
            <nav class="nav-tabs">
                <button class="tab-btn active" onclick="switchTab('sessions')">📊 Chuyến Đi</button>
                <button class="tab-btn" onclick="switchTab('vneid')">🪪 Xác Thực Tài Xế</button>
                <button class="tab-btn" onclick="switchTab('audio')">🎵 Ghi Âm Cabin</button>
                <button class="tab-btn" onclick="switchTab('telegram')">✈️ Telegram Bot</button>
                <button class="tab-btn" onclick="switchTab('wifi')">📡 Wi-Fi Manager</button>
            </nav>
        </header>

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
                    <div class="panel-title">📋 Danh Sách Hành Trình Lái Xe (dms_history.db)</div>
                    <a href="/api/export_csv" class="btn-action" target="_blank">📥 Xuất Báo Cáo CSV</a>
                </div>

                <div class="table-responsive">
                    <table class="data-table">
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Bắt Đầu</th>
                                <th>Kết Thúc</th>
                                <th>Thời Lượng</th>
                                <th>Ngủ Gật</th>
                                <th>Mất TT</th>
                                <th>Ngáp</th>
                                <th>Mệt Mỏi Max</th>
                            </tr>
                        </thead>
                        <tbody id="sessions-table-body">
                            <tr><td colspan="8" style="text-align:center; color:#64748b; padding:20px;">Đang tải lịch sử hành trình...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- TAB 2: XÁC THỰC TÀI XẾ / VNEID -->
        <div id="tab-vneid" class="tab-content">
            <div class="glass-panel">
                <div class="panel-header">
                    <div class="panel-title">⚡ 1-Click Chọn Nhanh Tài Xế Thường Dùng</div>
                </div>
                <div style="font-size:0.85rem; color:#94a3b8; margin-bottom:16px;">
                    Chỉ cần chạm 1 nút trên điện thoại để kích hoạt phiên lái xe và chuyển sang bước quét mặt ngay lập tức:
                </div>
                <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap:12px; margin-bottom:24px;">
                    <div class="wifi-card" onclick="quickSelectDriver('Nguyễn Văn A', '079203001234', 'B2 - 79A-123.45')">
                        <div>
                            <div style="font-weight:700; color:#f8fafc; font-size:0.95rem;">👤 Nguyễn Văn A</div>
                            <div style="font-size:0.8rem; color:#94a3b8;">CCCD: 079203001234</div>
                            <div style="font-size:0.75rem; color:#60a5fa;">Hạng B2 - 79A-123.45</div>
                        </div>
                        <div style="color:#6366f1; font-weight:700; font-size:1.1rem;">⚡ Chọn</div>
                    </div>
                    <div class="wifi-card" onclick="quickSelectDriver('Trần Thị Bích', '001201004567', 'B1 - 29A-678.90')">
                        <div>
                            <div style="font-weight:700; color:#f8fafc; font-size:0.95rem;">👤 Trần Thị Bích</div>
                            <div style="font-size:0.8rem; color:#94a3b8;">CCCD: 001201004567</div>
                            <div style="font-size:0.75rem; color:#60a5fa;">Hạng B1 - 29A-678.90</div>
                        </div>
                        <div style="color:#6366f1; font-weight:700; font-size:1.1rem;">⚡ Chọn</div>
                    </div>
                    <div class="wifi-card" onclick="quickSelectDriver('Lê Hoàng Nam', '048099008899', 'C - 51D-999.88')">
                        <div>
                            <div style="font-weight:700; color:#f8fafc; font-size:0.95rem;">👤 Lê Hoàng Nam</div>
                            <div style="font-size:0.8rem; color:#94a3b8;">CCCD: 048099008899</div>
                            <div style="font-size:0.75rem; color:#60a5fa;">Hạng C - 51D-999.88</div>
                        </div>
                        <div style="color:#6366f1; font-weight:700; font-size:1.1rem;">⚡ Chọn</div>
                    </div>
                </div>

                <hr style="border:0; border-top:1px solid rgba(255,255,255,0.08); margin: 20px 0;">

                <div class="panel-header">
                    <div class="panel-title">📷 Chụp / Tải Ảnh Thẻ CCCD Để AI OCR Nhận Diện Tự Động</div>
                </div>
                <div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:24px;">
                    <input type="file" id="card-file-input" accept="image/*" style="display:none;" onchange="uploadCardImage(this)">
                    <button class="btn-action" onclick="document.getElementById('card-file-input').click()" id="btn-upload-card" style="padding:10px 18px;">
                        📸 Chụp / Tải Ảnh Thẻ CCCD Từ Điện Thoại
                    </button>
                    <span id="ocr-status-text" style="font-size:0.85rem; color:#94a3b8;">Chọn ảnh rõ nét có chứa số CCCD hoặc Mã QR</span>
                </div>

                <hr style="border:0; border-top:1px solid rgba(255,255,255,0.08); margin: 20px 0;">

                <div class="panel-header">
                    <div class="panel-title">✏️ Nhập Tùy Chỉnh Thông Tin Tài Xế</div>
                </div>
                <div class="input-group">
                    <label for="driver-name">Họ và Tên Tài Xế:</label>
                    <input type="text" id="driver-name" placeholder="Ví dụ: Nguyễn Văn A">
                </div>
                <div class="input-group">
                    <label for="driver-vneid">Số Căn Cước Công Dân / VNeID (12 số):</label>
                    <input type="text" id="driver-vneid" placeholder="Ví dụ: 079203001234">
                </div>
                <div class="input-group">
                    <label for="driver-license">Hạng Bằng Lái & Biển Số Xe:</label>
                    <input type="text" id="driver-license" placeholder="Ví dụ: B2 - 79A-123.45">
                </div>

                <div style="display:flex; justify-content:flex-end;">
                    <button class="btn-action" style="background:#6366f1; color:#ffffff; padding:12px 24px; font-size:0.95rem;" onclick="submitDriverAuth()" id="btn-auth-driver">
                        🚀 Kích Hoạt Phiên Lái Xe Ngay
                    </button>
                </div>
            </div>
        </div>

        <!-- TAB 3: GHI ÂM CABIN KHOANG LÁI -->
        <div id="tab-audio" class="tab-content">
            <div class="glass-panel">
                <div class="panel-header">
                    <div class="panel-title">🎵 Thư Viện Ghi Âm Âm Thanh Khoang Lái (Sự Cố & Reset)</div>
                    <div style="display:flex; gap:10px;">
                        <button class="btn-action" onclick="recordTestAudio()" id="btn-rec-test">🎙️ Ghi Âm Thử 10s</button>
                        <button class="btn-action" onclick="loadAudioFiles()">🔄 Tải Lại</button>
                    </div>
                </div>
                <div style="font-size:0.8rem; color:#94a3b8; margin-bottom:16px;">
                    💡 <i>Ghi âm sẽ tự động tạo ra 10s khi **Kết thúc Chuyến đi / Reset Quét lại khuôn mặt** hoặc khi **Bấm nút Ghi âm thử 10s**. Tự động xóa file cũ (>3 ngày / >1GB) để KHÔNG BAO GIỜ tràn bộ nhớ!</i>
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
        </div>

        <!-- TAB 3: TELEGRAM BOT NOTIFICATIONS -->
        <div id="tab-telegram" class="tab-content">
            <div class="glass-panel">
                <div class="panel-header">
                    <div class="panel-title">✈️ Cấu Hình Thông Báo Cảnh Báo Telegram Bot</div>
                    <button class="btn-action" onclick="sendTestTelegram()" id="btn-test-tg">🧪 Gửi Cảnh Báo Thử</button>
                </div>
                <div style="font-size:0.85rem; color:#94a3b8; margin-bottom:20px;">
                    Nhập <b>Bot Token</b> và <b>Chat ID</b> Telegram của bạn để hệ thống tự động gửi ảnh chụp camera và cảnh báo khi tài xế ngủ gật!
                </div>

                <div class="input-group">
                    <label for="tg-token">Telegram Bot Token:</label>
                    <input type="text" id="tg-token" placeholder="Ví dụ: 123456789:ABCdefGhIJKlmNoPQrsTUVwxyZ...">
                </div>

                <div class="input-group">
                    <label for="tg-chatid">Telegram Chat ID (ID người nhận):</label>
                    <input type="text" id="tg-chatid" placeholder="Ví dụ: 987654321">
                </div>

                <div style="display:flex; justify-content:flex-end;">
                    <button class="btn-action" onclick="saveTelegramSettings()">💾 Lưu Cấu Hình Telegram</button>
                </div>
            </div>
        </div>

        <!-- TAB 4: QUẢN LÝ WI-FI -->
        <div id="tab-wifi" class="tab-content">
            <div class="glass-panel">
                <div class="panel-header">
                    <div class="panel-title">📡 Cấu Hình Kết Nối Wi-Fi Cho Pi</div>
                    <button class="btn-action" onclick="scanWifi()">🔄 Quét Lại Wi-Fi</button>
                </div>
                <div id="wifi-list" class="wifi-grid">
                    <div style="text-align:center; color:#64748b; padding:20px;">Nhấp "Quét Lại" để tìm Wi-Fi xung quanh...</div>
                </div>
            </div>
        </div>
    </div>

    <!-- Modal Nhập Mật Khẩu -->
    <div id="pwd-modal" class="modal-overlay">
        <div class="modal">
            <div class="modal-title" id="target-ssid-title">Kết nối Wi-Fi</div>
            <div class="input-group">
                <label for="wifi-pass">Mật khẩu Wi-Fi:</label>
                <input type="password" id="wifi-pass" placeholder="Nhập mật khẩu...">
            </div>
            <div class="modal-actions">
                <button class="btn-cancel" onclick="closeModal()">Hủy</button>
                <button class="btn-connect" onclick="submitConnect()" id="btn-submit">Kết nối</button>
            </div>
        </div>
    </div>

    <script>
        function switchTab(tabName) {
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));

            const tabMap = {
                'sessions': { btnIdx: 0, contentId: 'tab-sessions', init: loadSessions },
                'vneid': { btnIdx: 1, contentId: 'tab-vneid', init: () => {} },
                'audio': { btnIdx: 2, contentId: 'tab-audio', init: loadAudioFiles },
                'telegram': { btnIdx: 3, contentId: 'tab-telegram', init: loadTelegramConfig },
                'wifi': { btnIdx: 4, contentId: 'tab-wifi', init: scanWifi }
            };

            const target = tabMap[tabName] || tabMap['sessions'];
            document.querySelectorAll('.tab-btn')[target.btnIdx].classList.add('active');
            document.getElementById(target.contentId).classList.add('active');
            target.init();
        }

        function formatSec(seconds) {
            if (!seconds) return '00:00:00';
            const h = Math.floor(seconds / 3600).toString().padStart(2, '0');
            const m = Math.floor((seconds % 3600) / 60).toString().padStart(2, '0');
            const s = (seconds % 60).toString().padStart(2, '0');
            return `${h}:${m}:${s}`;
        }

        function loadSessions() {
            fetch('/api/sessions')
                .then(r => r.json())
                .then(sessions => {
                    const tbody = document.getElementById('sessions-table-body');
                    tbody.innerHTML = '';

                    if (sessions.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#64748b; padding:20px;">Chưa có dữ liệu chuyến đi nào.</td></tr>';
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
                            <td>${s.start_time || 'N/A'}</td>
                            <td>${s.end_time || 'N/A'}</td>
                            <td>${formatSec(s.duration_seconds)}</td>
                            <td style="color:#f87171; font-weight:600;">${s.drowsiness_count || 0}</td>
                            <td style="color:#fbbf24; font-weight:600;">${s.distraction_count || 0}</td>
                            <td style="color:#c084fc;">${s.yawn_count || 0}</td>
                            <td>${(s.max_fatigue_score || 0).toFixed(2)}</td>
                        `;
                        tbody.appendChild(tr);
                    });

                    document.getElementById('stat-total-trips').innerText = sessions.length;
                    document.getElementById('stat-total-time').innerText = formatSec(totalSec);
                    document.getElementById('stat-total-alerts').innerText = totalAlerts;
                    document.getElementById('stat-avg-fatigue').innerText = (fatigueSum / sessions.length).toFixed(2);
                });
        }

        function loadAudioFiles() {
            fetch('/api/audio_files')
                .then(r => r.json())
                .then(files => {
                    const tbody = document.getElementById('audio-table-body');
                    tbody.innerHTML = '';
                    if (files.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#64748b; padding:20px;">Chưa có file ghi âm nào. Bấm nút "🎙️ Ghi Âm Thử 10s" hoặc Reset Quét lại trên AI DMS để tạo file ghi âm.</td></tr>';
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
            fetch('/api/record_test_audio', {method: 'POST'})
                .then(r => r.json())
                .then(res => {
                    setTimeout(() => {
                        btn.disabled = false;
                        btn.innerText = '🎙️ Ghi Âm Thử 10s';
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
                alert(res.success ? 'Đã lưu cấu hình Telegram Bot!' : 'Lỗi lưu cấu hình');
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
                    alert(res.message);
                });
        }

        let selectedSSID = '';
        function scanWifi() {
            const listEl = document.getElementById('wifi-list');
            listEl.innerHTML = '<div style="text-align:center; color:#64748b; padding:20px;">Đang quét mạng Wi-Fi...</div>';
            
            fetch('/api/scan')
                .then(r => r.json())
                .then(data => {
                    listEl.innerHTML = '';
                    if (data.length === 0) {
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
            document.getElementById('wifi-pass').value = '';
            document.getElementById('pwd-modal').style.display = 'flex';
        }

        function closeModal() {
            document.getElementById('pwd-modal').style.display = 'none';
        }

        function submitConnect() {
            const pass = document.getElementById('wifi-pass').value;
            fetch('/api/connect', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ssid: selectedSSID, password: pass})
            })
            .then(r => r.json())
            .then(res => {
                closeModal();
                alert(res.success ? `Đã kết nối thành công tới ${selectedSSID}` : `Lỗi: ${res.message}`);
            });
        }

        function quickSelectDriver(name, vneid, lic) {
            document.getElementById('driver-name').value = name;
            document.getElementById('driver-vneid').value = vneid;
            document.getElementById('driver-license').value = lic;
            submitDriverAuth();
        }

        function submitDriverAuth() {
            const name = document.getElementById('driver-name').value.trim() || 'Nguyễn Văn A';
            const vneid = document.getElementById('driver-vneid').value.trim() || '079203001234';
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
                    alert(`✅ ${res.message}\n\nHệ thống AI DMS trên xe đã kích hoạt và chuyển sang bước quét khuôn mặt!`);
                } else {
                    alert(`❌ Lỗi: ${res.message}`);
                }
            })
            .catch(e => {
                btn.disabled = false;
                btn.innerText = '🚀 Kích Hoạt Phiên Lái Xe Ngay';
                alert('Lỗi kết nối tới AI DMS');
            });
        }

        function uploadCardImage(input) {
            if (!input.files || !input.files[0]) return;
            const file = input.files[0];
            const formData = new FormData();
            formData.append('card_image', file);
            
            const statusEl = document.getElementById('ocr-status-text');
            statusEl.innerHTML = '<span style="color:#fbbf24;">⏳ Đang chạy AI OCR đọc chữ & mã QR...</span>';
            
            fetch('/api/ocr_upload_card', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(res => {
                if (res.success) {
                    statusEl.innerHTML = `<span style="color:#34d399;">✅ ${res.message}</span>`;
                    document.getElementById('driver-name').value = res.name;
                    document.getElementById('driver-vneid').value = res.vneid;
                    document.getElementById('driver-license').value = res.license_class;
                    alert(`✅ ${res.message}\n\nHệ thống đã tự động kích hoạt phiên lái xe trên AI DMS!`);
                } else {
                    statusEl.innerHTML = `<span style="color:#f87171;">❌ ${res.message}</span>`;
                    alert(`❌ ${res.message}`);
                }
            })
            .catch(e => {
                statusEl.innerHTML = '<span style="color:#f87171;">❌ Lỗi kết nối máy chủ OCR</span>';
            });
        }

        window.onload = () => loadSessions();
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/sessions")
def api_sessions():
    return jsonify(get_db_sessions())

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
    try:
        filename = record_event_audio("manual_test", 10)
        return jsonify({"success": True, "filename": filename})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route("/api/telegram_config", methods=["GET", "POST"])
def api_telegram_config():
    if request.method == "POST":
        data = request.json or {}
        save_telegram_config(data)
        return jsonify({"success": True})
    return jsonify(load_telegram_config())

@app.route("/api/test_telegram", methods=["POST"])
def api_test_telegram():
    try:
        send_telegram_alert_async("🧪 Đây là tin nhắn cảnh báo thử nghiệm từ hệ thống AI DMS!")
        return jsonify({"success": True, "message": "Đã gửi tin nhắn cảnh báo thử nghiệm tới Telegram!"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route("/api/authenticate_vneid", methods=["POST"])
def api_authenticate_vneid():
    try:
        data = request.json or {}
        name = data.get("name", "").strip() or "Nguyễn Văn A"
        vneid = data.get("vneid", "").strip() or "012345678910"
        license_cls = data.get("license_class", "").strip() or "B2 - 79A-123.45"
        
        vneid_payload = {
            "name": name,
            "vneid": vneid,
            "license_class": license_cls,
            "timestamp": time.time()
        }
        # Ghi nguyên tử để tiến trình camera không đọc phải JSON đang ghi dở.
        trigger_tmp_path = "/tmp/dms_vneid_trigger.json.tmp"
        with open(trigger_tmp_path, "w") as f:
            json.dump(vneid_payload, f)
        os.replace(trigger_tmp_path, "/tmp/dms_vneid_trigger.json")
            
        return jsonify({"success": True, "message": f"Đã nhận xác thực của {name}; thông tin phiên sẽ được gửi sau khi quét mặt hoàn tất."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route("/audio_logs/<path:filename>")
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename)

@app.route("/api/export_csv")
def api_export_csv():
    sessions = get_db_sessions()
    if not sessions:
        return "No session data found", 404
    output = []
    headers = list(sessions[0].keys())
    output.append(",".join(headers))
    for s in sessions:
        row = [str(s[h]) if s[h] is not None else "" for h in headers]
        output.append(",".join(row))
    csv_data = "\n".join(output)
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=dms_sessions_report.csv"}
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
