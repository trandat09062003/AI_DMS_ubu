import os
import json
import requests
import threading
import cv2
import time

CONFIG_PATH = "/home/kata/Documents/AI_DMS/telegram_config.json"

def load_telegram_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "enabled": True,
        "bot_token": "",
        "chat_id": ""
    }

def save_telegram_config(config):
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=4)
        return True
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}")
        return False

def send_telegram_alert_async(message, frame=None, audio_file=None):
    def _send():
        config = load_telegram_config()
        if not config.get("enabled", True):
            return

        bot_token = config.get("bot_token", "").strip()
        chat_id = config.get("chat_id", "").strip()
        
        if not bot_token or not chat_id:
            print("[TELEGRAM WARN] Chưa cấu hình Telegram Bot Token hoặc Chat ID.")
            return

        api_base = f"https://api.telegram.org/bot{bot_token}"
        
        try:
            # 1. Gửi ảnh nếu có frame
            if frame is not None:
                tmp_img = "/tmp/dms_alert_frame.jpg"
                cv2.imwrite(tmp_img, frame)
                with open(tmp_img, "rb") as photo:
                    requests.post(
                        f"{api_base}/sendPhoto",
                        data={"chat_id": chat_id, "caption": f"🚨 [AI DMS ALERT] 🚨\n{message}"},
                        files={"photo": photo},
                        timeout=10
                    )
                if os.path.exists(tmp_img):
                    try:
                        os.remove(tmp_img)
                    except Exception:
                        pass
            else:
                requests.post(
                    f"{api_base}/sendMessage",
                    data={"chat_id": chat_id, "text": f"🚨 [AI DMS ALERT] 🚨\n{message}"},
                    timeout=10
                )

            # 2. Gửi file ghi âm âm thanh khoang lái nếu có
            if audio_file and os.path.exists(audio_file):
                with open(audio_file, "rb") as voice:
                    requests.post(
                        f"{api_base}/sendAudio",
                        data={"chat_id": chat_id, "caption": "🎵 Âm thanh khoang lái lúc xảy ra sự cố:"},
                        files={"audio": voice},
                        timeout=15
                    )
            print(f"[TELEGRAM SUCCESS] Đã gửi thông báo tới Telegram Chat ID: {chat_id}")
        except Exception as e:
            print(f"[TELEGRAM ERROR] {e}")

    t = threading.Thread(target=_send, daemon=True)
    t.start()
