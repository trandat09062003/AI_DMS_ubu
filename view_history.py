#!/usr/bin/env python3
"""
Công cụ xem và truy xuất Lịch sử Hành trình Lái xe (DMS History Viewer)
"""

import sqlite3
import argparse
import os
import sys

DB_PATH = "dms_history.db"

def format_duration(seconds):
    if seconds is None:
        return "00:00:00"
    hrs = seconds // 3600
    mins = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"

def list_sessions():
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] Khong tim thay file co so du lieu SQLite: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dms_sessions';")
        if not cursor.fetchone():
            print("[INFO] Chưa có dữ liệu phiên hành trình nào được ghi nhận.")
            conn.close()
            return
            
        cursor.execute("""
            SELECT session_id, 
                   COALESCE(NULLIF(driver_name, ''), 'Người lạ') AS driver_name,
                   COALESCE(NULLIF(vneid_card, ''), 'Không xác định') AS vneid_card,
                   start_time, end_time, duration_seconds, 
                   distraction_count, drowsiness_count, yawn_count, 
                   avg_fatigue_score, max_fatigue_score 
            FROM dms_sessions 
            ORDER BY session_id DESC
        """)
        rows = cursor.fetchall()
        
        if not rows:
            print("[INFO] Bảng lịch sử dms_sessions đang trống.")
            conn.close()
            return

        print("==================================================================================================================================")
        print("                                       LỊCH SỬ TIẾN TRÌNH HÀNH TRÌNH LÁI XE (DMS SESSIONS)")
        print("==================================================================================================================================")
        print(f"{'ID':<4} | {'Nguoi Lai Xe':<16} | {'CCCD/VNeID':<14} | {'Bat Dau':<19} | {'Ket Thuc':<19} | {'Thoi Gian':<9} | {'Mat TT':<6} | {'Ngu':<4} | {'Ngap':<4} | {'FS TB':<6} | {'FS Max':<6}")
        print("-" * 130)

        for row in rows:
            sid, dname, vcard, stime, etime, dur, dis_cnt, drow_cnt, yawn_cnt, avg_f, max_f = row
            dur_str = format_duration(dur)
            stime_str = stime if stime else "N/A"
            etime_str = etime if etime else "N/A"
            dname_str = (dname[:14] + '..') if len(dname) > 16 else dname
            vcard_str = (vcard[:12] + '..') if len(vcard) > 14 else vcard
            avg_f_str = f"{avg_f:.2f}" if avg_f is not None else "0.00"
            max_f_str = f"{max_f:.2f}" if max_f is not None else "0.00"

            print(f"#{sid:<3} | {dname_str:<16} | {vcard_str:<14} | {stime_str:<19} | {etime_str:<19} | {dur_str:<9} | {dis_cnt:<6} | {drow_cnt:<4} | {yawn_cnt:<4} | {avg_f_str:<6} | {max_f_str:<6}")

        print("==================================================================================================================================")
        print(f"Tong so phien hanh trinh da luu: {len(rows)}")

    except Exception as e:
        print(f"[ERROR] Loi khi truy van co so du lieu: {e}")
    finally:
        conn.close()

def export_csv(output_file="dms_sessions_export.csv"):
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] Khong tim thay file co so du lieu SQLite: {DB_PATH}")
        return

    import csv
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT * FROM dms_sessions ORDER BY session_id ASC")
        rows = cursor.fetchall()
        headers = [description[0] for description in cursor.description]
        
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(rows)
            
        print(f"[SUCCESS] Da xuat {len(rows)} phien hanh trinh ra file CSV: {output_file}")
    except Exception as e:
        print(f"[ERROR] Loi khi xuat CSV: {e}")
    finally:
        conn.close()

def main():
    parser = argparse.ArgumentParser(description="Xem Lịch sử Hành trình Lái xe DMS")
    parser.add_argument("--export", action="store_true", help="Xuat lich su hanh trinh ra file CSV")
    parser.add_argument("--out", type=str, default="dms_sessions_export.csv", help="Ten file CSV xuat ra (mac dinh: dms_sessions_export.csv)")
    args = parser.parse_args()

    if args.export:
        export_csv(args.out)
    else:
        list_sessions()

if __name__ == "__main__":
    main()
