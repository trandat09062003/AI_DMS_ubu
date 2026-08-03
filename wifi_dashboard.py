import subprocess
import json
import time
import os
import sqlite3
import glob
from flask import Flask, render_template_string, request, jsonify, Response, send_from_directory
from audio_manager import cleanup_old_audio, AUDIO_DIR, ensure_audio_dir, start_continuous_recording

app = Flask(__name__)
DB_PATH = "/home/kata/Documents/AI_DMS/dms_history.db"

ensure_audio_dir()
# Tự động kích hoạt ghi âm khoang lái liên tục (Real-time) ngay khi thiết bị khởi động
start_continuous_recording(chunk_duration_sec=60)

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
                   avg_fatigue_score, max_fatigue_score 
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
    <title>AI DMS - Local Control & Realtime Audio Dashboard</title>
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

        .rec-pulse {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 14px;
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid rgba(239, 68, 68, 0.4);
            color: #f87171;
            border-radius: 20px;
            font-size: 0.8rem;
            font-weight: 700;
        }

        .pulse-dot {
            width: 10px;
            height: 10px;
            background: #ef4444;
            border-radius: 50%;
            animation: blink 1.2s infinite;
        }

        @keyframes blink {
            0% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.3; transform: scale(0.85); }
            100% { opacity: 1; transform: scale(1); }
        }

        .btn-action {
            padding: 8px 16px; background: rgba(99, 102, 241, 0.2);
            border: 1px solid rgba(99, 102, 241, 0.4); color: #818cf8;
            border-radius: 10px; font-weight: 600; font-size: 0.8rem;
            cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; gap: 6px;
            transition: all 0.2s;
        }

        .btn-action:hover { background: #6366f1; color: #ffffff; }

        audio {
            height: 36px;
            outline: none;
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
                    <div class="brand-desc">Giám sát Lái xe & Ghi Âm Khoang Lái Thời Gian Thực</div>
                </div>
            </div>
            <nav class="nav-tabs">
                <button class="tab-btn active" onclick="switchTab('sessions')">📊 Chuyến Đi</button>
                <button class="tab-btn" onclick="switchTab('audio')">🎵 Ghi Âm Cabin</button>
                <button class="tab-btn" onclick="switchTab('wifi')">📡 Wi-Fi Manager</button>
            </nav>
        </header>

        <!-- TAB 1: THÔNG TIN CHUYẾN ĐỊ -->
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

        <!-- TAB 2: GHI ÂM CABIN KHOANG LÁI (REALTIME) -->
        <div id="tab-audio" class="tab-content">
            <div class="glass-panel">
                <div class="panel-header">
                    <div class="panel-title">🎵 Âm Thanh Khoang Lái Thời Gian Thực (Real-time Recording)</div>
                    <div style="display:flex; align-items:center; gap:12px;">
                        <div class="rec-pulse"><div class="pulse-dot"></div> ĐANG GHI ÂM REALTIME</div>
                        <button class="btn-action" onclick="loadAudioFiles()">🔄 Tải Lại</button>
                    </div>
                </div>
                <div style="font-size:0.8rem; color:#94a3b8; margin-bottom:16px;">
                    💡 <i>Micro tự động ghi âm khoang lái liên tục từng đoạn 60s ngay từ khi bật thiết bị. Tự động xoay vòng xóa file cũ (>3 ngày / >1GB) để KHÔNG BAO GIỜ bị tràn bộ nhớ!</i>
                </div>

                <div class="table-responsive">
                    <table class="data-table">
                        <thead>
                            <tr>
                                <th>File Đoạn Âm Thanh</th>
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

        <!-- TAB 3: QUẢN LÝ WI-FI -->
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
        let audioTimer = null;

        function switchTab(tabName) {
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
            
            if (audioTimer) { clearInterval(audioTimer); audioTimer = null; }

            if (tabName === 'sessions') {
                document.querySelectorAll('.tab-btn')[0].classList.add('active');
                document.getElementById('tab-sessions').classList.add('active');
                loadSessions();
            } else if (tabName === 'audio') {
                document.querySelectorAll('.tab-btn')[1].classList.add('active');
                document.getElementById('tab-audio').classList.add('active');
                loadAudioFiles();
                // Tự động làm mới danh sách file mỗi 10 giây để hiển thị đoạn ghi âm mới nhất
                audioTimer = setInterval(loadAudioFiles, 10000);
            } else {
                document.querySelectorAll('.tab-btn')[2].classList.add('active');
                document.getElementById('tab-wifi').classList.add('active');
                scanWifi();
            }
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
                        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#64748b; padding:20px;">Đang ghi âm đoạn đầu tiên... Vui lòng đợi trong giây lát.</td></tr>';
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
