#!/usr/bin/env python3
import time
import sys

MOTOR_PIN = 17  # GPIO 17
BUZZER_PIN = 27 # GPIO 27

try:
    import RPi.GPIO as GPIO
    print("[INFO] Import RPi.GPIO thanh cong.")
except ImportError:
    print("[ERROR] Khong the import RPi.GPIO. Vui long kiem tra xem thu vien da duoc cai dat hay chua.")
    print("Goi y: Chay script trong venv bang cach dung venv/bin/python3")
    sys.exit(1)

def main():
    print("=== CHUONG TRINH KIEM TRA DONG CO RUNG VA COI CHIP ===")
    print(f"Cấu hình chân BCM: Motor Pin = {MOTOR_PIN}, Buzzer Pin = {BUZZER_PIN}")
    
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(MOTOR_PIN, GPIO.OUT)
        GPIO.setup(BUZZER_PIN, GPIO.OUT)
        
        # Đảm bảo tắt lúc đầu
        GPIO.output(MOTOR_PIN, GPIO.LOW)
        GPIO.output(BUZZER_PIN, GPIO.LOW)
        
        print("\n[INFO] Bat dau kich hoat coi keu va dong co rung trong 10 giay...")
        GPIO.output(MOTOR_PIN, GPIO.HIGH)
        GPIO.output(BUZZER_PIN, GPIO.HIGH)
        
        # Cho keu va rung trong 10 giay
        for i in range(10, 0, -1):
            print(f"Dang hoat dong... Con lai {i} giay", end="\r")
            time.sleep(1)
        print("\nHet thoi gian 10 giay!")
        
    except KeyboardInterrupt:
        print("\n[INFO] Da ngat kiem tra boi nguoi dung.")
    except Exception as e:
        print(f"\n[ERROR] Co loi xay ra: {e}")
    finally:
        # Tat thiet bi va don dep
        try:
            GPIO.output(MOTOR_PIN, GPIO.LOW)
            GPIO.output(BUZZER_PIN, GPIO.LOW)
            GPIO.cleanup()
            print("[INFO] Da tat thiet bi va don dep GPIO thanh cong.")
        except Exception as e:
            print(f"[WARN] Khong the don dep GPIO: {e}")

if __name__ == "__main__":
    main()
