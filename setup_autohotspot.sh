#!/bin/bash
# ==============================================================================
# Script Tự Động Cấu Hình Auto-Hotspot cho Raspberry Pi / Ubuntu (NetworkManager)
# ==============================================================================
# Cơ chế hoạt động:
# 1. Ưu tiên tự động kết nối với các mạng Wi-Fi đã lưu (Nhà, Công ty, Điện thoại...).
# 2. Nếu đi ra ngoài không có mạng Wi-Fi quen thuộc, Pi tự động phát Hotspot.
# 3. Tên Hotspot mặc định: AI_DMS_Hotspot | Mật khẩu: 1234567890 (IP: 10.42.0.1)
# ==============================================================================

HOTSPOT_SSID="${1:-AI_DMS_Hotspot}"
HOTSPOT_PASS="${2:-1234567890}"

echo "===================================================="
echo "[INFO] Dang cau hinh Auto Hotspot cho he thong AI DMS..."
echo "[INFO] Hotspot SSID: $HOTSPOT_SSID"
echo "[INFO] Hotspot Password: $HOTSPOT_PASS"
echo "===================================================="

# 1. Kiểm tra xem profile Hotspot đã tồn tại chưa, nếu có thì xóa cấu hình cũ
if sudo nmcli connection show "$HOTSPOT_SSID" >/dev/null 2>&1; then
    echo "[INFO] Xoa profile Hotspot cu..."
    sudo nmcli connection delete "$HOTSPOT_SSID"
fi

# 2. Tạo profile Wi-Fi Hotspot (AP Mode) chuẩn NetworkManager
echo "[INFO] Dang tao profile Hotspot moi..."
sudo nmcli connection add type wifi ifname wlan0 mode ap con-name "$HOTSPOT_SSID" ssid "$HOTSPOT_SSID" autoconnect yes
sudo nmcli connection modify "$HOTSPOT_SSID" 802-11-wireless-security.key-mgmt wpa-psk
sudo nmcli connection modify "$HOTSPOT_SSID" 802-11-wireless-security.psk "$HOTSPOT_PASS"
sudo nmcli connection modify "$HOTSPOT_SSID" ipv4.method shared
sudo nmcli connection modify "$HOTSPOT_SSID" connection.autoconnect-priority -10

# 3. Tăng độ ưu tiên autoconnect của tất cả các mạng Wi-Fi Client đã lưu lên 10 (để ưu tiên kết nối Wi-Fi nhà hơn Hotspot)
echo "[INFO] Dang thiet lap do uu tien ket noi Wi-Fi..."
saved_cons=$(nmcli -g NAME,TYPE connection show | grep ":wifi$" | cut -d':' -f1)
while IFS= read -r con_name; do
    if [ -n "$con_name" ] && [ "$con_name" != "$HOTSPOT_SSID" ]; then
        echo "  -> Dat autoconnect-priority=10 cho Wifi: '$con_name'"
        sudo nmcli connection modify "$con_name" connection.autoconnect-priority 10 autoconnect yes 2>/dev/null || true
    fi
done <<< "$saved_cons"

# 4. Tạo script NetworkManager Dispatcher tự động chuyển đổi thông minh
echo "[INFO] Dang tao script chuyen doi tu dong /etc/NetworkManager/dispatcher.d/99-autohotspot.sh..."

sudo bash -c "cat << 'EOF' > /etc/NetworkManager/dispatcher.d/99-autohotspot.sh
#!/bin/bash
IFACE=\$1
ACTION=\$2

HOTSPOT_NAME=\"$HOTSPOT_SSID\"

if [ \"\$IFACE\" = \"wlan0\" ] && [ \"\$ACTION\" = \"down\" ]; then
    sleep 3
    # Kiểm tra xem có đang kết nối Wi-Fi nào không
    CURRENT_CON=\$(nmcli -t -f GENERAL.CONNECTION dev show wlan0 2>/dev/null | cut -d':' -f2)
    if [ -z \"\$CURRENT_CON\" ] || [ \"\$CURRENT_CON\" = \"--\" ]; then
        echo \"[AutoHotspot] Khong tim thay Wi-Fi quen thuoc, dang phat Hotspot \$HOTSPOT_NAME...\"
        nmcli connection up \"\$HOTSPOT_NAME\" 2>/dev/null || true
    fi
fi
EOF"

sudo chmod +x /etc/NetworkManager/dispatcher.d/99-autohotspot.sh

echo "===================================================="
echo "[SUCCESS] DA CAU HINH AUTO HOTSPOT THANH CONG!"
echo "----------------------------------------------------"
echo "Chế độ hoạt động:"
echo "1. Khi có Wi-Fi quen thuộc (như VIETSET_TECH): Pi tự động kết nối Wi-Fi."
echo "2. Khi mang Pi đi xa / không có Wi-Fi: Pi tự động phát Wi-Fi:"
echo "   - Tên Wi-Fi: $HOTSPOT_SSID"
echo "   - Mật khẩu: $HOTSPOT_PASS"
echo "   - Địa chỉ IP của Pi khi bạn truy cập vào Hotspot: 10.42.0.1"
echo "===================================================="
