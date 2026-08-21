#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI_DMS OLED Display Manager (SSD1306 / SH1106 128x64 I2C)
Driver màn hình OLED hiển thị trạng thái hệ thống, cảnh báo buồn ngủ,
thông số EAR/MAR, FPS, IP mạng và thông tin xác thực tài xế.
"""

import os
import time
import fcntl
import threading
import subprocess
from PIL import Image, ImageDraw, ImageFont

I2C_SLAVE = 0x0703
DEFAULT_I2C_BUS = "/dev/i2c-1"
DEFAULT_I2C_ADDR = 0x3C

class SSD1306OLED:
    """Pure-Python I2C Driver cho màn hình SSD1306 / SH1106 128x64 qua Linux /dev/i2c-*"""
    def __init__(self, bus_path=DEFAULT_I2C_BUS, addr=DEFAULT_I2C_ADDR, width=128, height=64):
        self.bus_path = bus_path
        self.addr = addr
        self.width = width
        self.height = height
        self.fd = None
        self.available = False
        self._init_device()

    def _init_device(self):
        try:
            if not os.path.exists(self.bus_path):
                # Thử tìm bus khác nếu bus-1 không có
                for b in [1, 0, 10, 20, 21, 22]:
                    p = f"/dev/i2c-{b}"
                    if os.path.exists(p):
                        self.bus_path = p
                        break

            if not os.path.exists(self.bus_path):
                self.available = False
                return

            self.fd = os.open(self.bus_path, os.O_RDWR)
            fcntl.ioctl(self.fd, I2C_SLAVE, self.addr)

            # Khởi tạo SSD1306 chuẩn
            init_cmds = [
                0xAE,        # Display OFF
                0xD5, 0x80,  # Set display clock divide ratio/oscillator frequency
                0xA8, 0x3F,  # Multiplex ratio: 64 (0x3F)
                0xD3, 0x00,  # Display offset: 0
                0x40 | 0x00, # Start line: 0
                0x8D, 0x14,  # Enable charge pump
                0x20, 0x00,  # Memory addressing mode: Horizontal
                0xA1,        # Segment remap (column 127 mapped to SEG0)
                0xC8,        # COM Output scan direction (remapped)
                0xDA, 0x12,  # COM pins hardware configuration
                0x81, 0xCF,  # Contrast control
                0xD9, 0xF1,  # Pre-charge period
                0xDB, 0x40,  # VCOMH deselect level
                0xA4,        # Entire display ON (output follows RAM)
                0xA6,        # Set Normal display
                0x2E,        # Deactivate scroll
                0xAF         # Display ON
            ]
            for c in init_cmds:
                self._send_cmd(c)

            self.available = True
            self.clear()
        except Exception as e:
            self.available = False
            if self.fd:
                try:
                    os.close(self.fd)
                except Exception:
                    pass
                self.fd = None

    def _send_cmd(self, cmd):
        if not self.fd:
            return
        if isinstance(cmd, int):
            cmd = [cmd]
        for c in cmd:
            os.write(self.fd, bytes([0x00, c]))

    def _send_data(self, data):
        if not self.fd:
            return
        chunk_size = 32
        for i in range(0, len(data), chunk_size):
            chunk = data[i:i + chunk_size]
            os.write(self.fd, bytes([0x40]) + bytes(chunk))

    def display(self, image):
        """Nhận vào PIL Image (chế độ '1' - 1 bit monochrome 128x64) và xuất ra OLED"""
        if not self.available or not self.fd:
            return

        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height))
        if image.mode != '1':
            image = image.convert('1')

        # Đặt địa chỉ cột và trang
        self._send_cmd([0x21, 0, self.width - 1, 0x22, 0, (self.height // 8) - 1])

        # Chuyển đổi ma trận ảnh sang byte buffer dạng page
        pages = self.height // 8
        buf = bytearray(self.width * pages)
        pix = image.load()
        for p in range(pages):
            p_offset = p * 8
            for x in range(self.width):
                byte = 0
                for bit in range(8):
                    y = p_offset + bit
                    if pix[x, y]:
                        byte |= (1 << bit)
                buf[p * self.width + x] = byte

        self._send_data(buf)

    def clear(self):
        """Xóa trắng màn hình (tất cả pixel tắt)"""
        if not self.available or not self.fd:
            return
        self._send_cmd([0x21, 0, self.width - 1, 0x22, 0, (self.height // 8) - 1])
        blank = [0x00] * (self.width * (self.height // 8))
        self._send_data(blank)

    def close(self):
        if self.fd:
            try:
                self.clear()
                self._send_cmd(0xAE) # Turn off display
                os.close(self.fd)
            except Exception:
                pass
            self.fd = None
            self.available = False


class OLEDManager:
    """Quản lý luồng cập nhật giao diện OLED thời gian thực cho AI DMS"""
    def __init__(self):
        self.driver = SSD1306OLED()
        self.lock = threading.Lock()
        self.running = False
        self.thread = None

        # Trạng thái hiển thị
        self.state = {
            "auth_state": "INIT",      # "VNEID_REQ", "FACE_REQ", "MONITORING", "RECALIBRATING"
            "alarm_level": 0,          # 0, 1, 2, 3, 4, 5
            "status_text": "KHOI DONG",
            "ear": 0.0,
            "mar": 0.0,
            "fps": 0.0,
            "fatigue_score": 0,
            "driver_name": "",
            "ip_addr": "10.42.0.1",
            "wifi_mode": "HOTSPOT",
            "flash_state": False,
            "cpu_temp": "--"
        }

        # Khởi tạo font vẽ
        self.font = ImageFont.load_default()
        self._last_ip_check = 0
        self._ip_cached = "10.42.0.1"
        self._temp_cached = "--"

    def is_available(self):
        return self.driver.available

    def start(self):
        if not self.driver.available:
            print("[OLED] Khong tim thay thiet bi OLED I2C 0x3C, bo qua.")
            return

        print("[OLED] Da tim thay man hinh OLED I2C 0x3C. Khoi chay giao dien OLED...")
        self.running = True
        self.thread = threading.Thread(target=self._render_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self.driver.close()

    def update_data(self, **kwargs):
        with self.lock:
            for k, v in kwargs.items():
                if k in self.state:
                    self.state[k] = v

    def _get_system_info(self):
        now = time.time()
        if now - self._last_ip_check > 5.0:
            self._last_ip_check = now
            # Get IP
            try:
                res = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=1.0)
                ips = res.stdout.strip().split()
                if ips:
                    self._ip_cached = ips[0]
                else:
                    self._ip_cached = "No IP"
            except Exception:
                pass

            # Get CPU Temp
            try:
                if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
                    with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                        t = int(f.read().strip()) / 1000.0
                        self._temp_cached = f"{t:.0f}C"
            except Exception:
                pass

        return self._ip_cached, self._temp_cached

    def _render_loop(self):
        flash_counter = 0
        while self.running:
            try:
                ip, temp = self._get_system_info()
                with self.lock:
                    st = self.state.copy()

                flash_counter += 1
                flash = (flash_counter % 4) < 2

                # Vẽ khung hình 128x64
                img = Image.new('1', (128, 64), 0)
                draw = ImageDraw.Draw(img)

                # ================= 1. HEADER (0 -> 13) =================
                draw.rectangle((0, 0, 127, 12), fill=1)
                header_text = "AI DMS"
                auth = st["auth_state"]
                lvl = st["alarm_level"]

                if lvl >= 2:
                    header_status = "! CANH BAO !"
                elif auth == "VNEID_REQ":
                    header_status = "QUET VNEID"
                elif auth == "FACE_REQ":
                    header_status = "XAC THUC MAT"
                elif auth == "MONITORING":
                    header_status = "DANG LAI XE"
                else:
                    header_status = "READY"

                draw.text((2, 1), header_text, fill=0, font=self.font)
                draw.text((50, 1), header_status, fill=0, font=self.font)

                # ================= 2. BODY (14 -> 50) =================
                if auth == "VNEID_REQ":
                    # Màn hình yêu cầu VNeID / CCCD
                    draw.rectangle((2, 16, 125, 48), outline=1)
                    if flash:
                        draw.text((10, 20), ">> QUET THE <<", fill=1, font=self.font)
                        draw.text((8, 33), "VNeID / CCCD QR", fill=1, font=self.font)
                    else:
                        draw.text((14, 20), "[ DUA THE ]", fill=1, font=self.font)
                        draw.text((12, 33), "VAO CAMERA...", fill=1, font=self.font)

                elif auth == "FACE_REQ":
                    # Màn hình yêu cầu căn chỉnh khuôn mặt
                    draw.rectangle((2, 16, 125, 48), outline=1)
                    if flash:
                        draw.text((12, 20), ">> NHIN VAO <<", fill=1, font=self.font)
                        draw.text((18, 33), "CAMERA 2S...", fill=1, font=self.font)
                    else:
                        draw.text((10, 20), "[ XAC THUC ]", fill=1, font=self.font)
                        draw.text((14, 33), "KHUON MAT...", fill=1, font=self.font)

                elif lvl >= 2:
                    # Màn hình BÁO ĐỘNG ĐỎ / NGUY HIỂM
                    if flash:
                        draw.rectangle((0, 14, 127, 49), fill=1)
                        msg = "NGUY HIEM!" if lvl == 2 else "BAO DONG CAP 3!"
                        draw.text((15, 20), msg, fill=0, font=self.font)
                        draw.text((10, 34), "TAP TRUNG LAI XE!", fill=0, font=self.font)
                    else:
                        draw.rectangle((2, 16, 125, 48), outline=1)
                        draw.text((15, 20), "!! NGU GAT !!", fill=1, font=self.font)
                        draw.text((10, 34), f"ALARM LVL {lvl}", fill=1, font=self.font)

                elif lvl == 1:
                    # Cảnh báo vàng: Mệt nhẹ / Ngáp / Mắt nhắm 1.5s
                    draw.text((2, 16), "! CHU Y:", fill=1, font=self.font)
                    draw.text((55, 16), "Nham mat/Ngap", fill=1, font=self.font)
                    draw.text((2, 28), f"EAR:{st['ear']:.2f} MAR:{st['mar']:.2f}", fill=1, font=self.font)
                    draw.text((2, 39), f"FPS:{st['fps']:.0f} Met:{st['fatigue_score']}%", fill=1, font=self.font)

                else:
                    # Trạng thái an toàn bình thường (MONITORING SAFE)
                    driver = st["driver_name"]
                    if not driver:
                        driver = "Tai xe"
                    if len(driver) > 14:
                        driver = driver[:13] + "."

                    draw.text((2, 16), f"TX: {driver}", fill=1, font=self.font)
                    draw.text((2, 27), f"EAR:{st['ear']:.2f}  MAR:{st['mar']:.2f}", fill=1, font=self.font)
                    draw.text((2, 38), f"FPS:{st['fps']:.0f}  Met:{st['fatigue_score']}%", fill=1, font=self.font)

                # ================= 3. FOOTER (51 -> 63) =================
                draw.line((0, 50, 127, 50), fill=1)
                draw.text((2, 53), f"IP:{ip}", fill=1, font=self.font)
                draw.text((95, 53), f"{temp}", fill=1, font=self.font)

                # Gửi ra màn hình OLED
                self.driver.display(img)

                time.sleep(0.1) # 10 FPS
            except Exception as e:
                time.sleep(0.5)


# Global singleton instance
oled_instance = None

def get_oled_manager():
    global oled_instance
    if oled_instance is None:
        oled_instance = OLEDManager()
    return oled_instance
