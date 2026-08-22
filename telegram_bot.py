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
        
        def _make_req(endpoint, data, files=None):
            nonlocal chat_id, config
            r = requests.post(f"{api_base}/{endpoint}", data=data, files=files, timeout=12)
            try:
                res_json = r.json()
                if not res_json.get("ok") and "parameters" in res_json:
                    new_id = res_json["parameters"].get("migrate_to_chat_id")
                    if new_id:
                        print(f"[TELEGRAM INFO] Group migrated. Updating chat_id to {new_id}")
                        chat_id = str(new_id)
                        config["chat_id"] = chat_id
                        save_telegram_config(config)
                        data["chat_id"] = chat_id
                        r = requests.post(f"{api_base}/{endpoint}", data=data, files=files, timeout=12)
            except Exception:
                pass
            return r

        try:
            # 1. Gửi ảnh nếu có frame
            if frame is not None:
                tmp_img = f"/tmp/dms_alert_frame_{time.time_ns()}.jpg"
                cv2.imwrite(tmp_img, frame)
                try:
                    with open(tmp_img, "rb") as photo:
                        caption_text = f"🚨 [AI DMS ALERT] 🚨\n{message}" if not message.startswith(("🚨", "🆔", "🧪")) else message
                        _make_req(
                            "sendPhoto",
                            data={"chat_id": chat_id, "caption": caption_text},
                            files={"photo": photo}
                        )
                finally:
                    if os.path.exists(tmp_img):
                        try:
                            os.remove(tmp_img)
                        except Exception:
                            pass
            else:
                msg_text = f"🚨 [AI DMS ALERT] 🚨\n{message}" if not message.startswith(("🚨", "🆔", "🧪")) else message
                _make_req(
                    "sendMessage",
                    data={"chat_id": chat_id, "text": msg_text}
                )

            # 2. Gửi file ghi âm âm thanh khoang lái nếu có
            if audio_file and os.path.exists(audio_file):
                with open(audio_file, "rb") as voice:
                    _make_req(
                        "sendAudio",
                        data={"chat_id": chat_id, "caption": "🎵 Âm thanh khoang lái lúc xảy ra sự cố:"},
                        files={"audio": voice}
                    )
            print(f"[TELEGRAM SUCCESS] Đã gửi thông báo tới Telegram Chat ID: {chat_id}")
        except Exception as e:
            print(f"[TELEGRAM ERROR] {e}")

    t = threading.Thread(target=_send, daemon=True)
    t.start()

