#!/usr/bin/env python3
from rfid_manager import (init_rfid_database, get_driver_by_uid, register_driver, list_all_drivers, delete_driver_by_uid)

def main():
    init_rfid_database()
    while True:
        print("=" * 60)
        print("     HE THONG QUAN LY THE RFID RC522 - AI_DMS")
        print("=" * 60)
        print("1. Quet the kiem tra")
        print("2. Dang ky the moi")
        print("3. Xem danh sach tai xe")
        print("4. Xoa the")
        print("5. Them tai xe mau")
        print("0. Thoat")
        print("-" * 60)
        c = input("Chon (0-5): ").strip()
        if c == "1":
            uid = input("Nhap UID the kiem tra (hoac quet truc tiep): ").strip()
            if uid:
                res = get_driver_by_uid(uid)
                if res.get("success"):
                    print(f"-> XAC THUC THANH CONG: Ten = {res['name']}, CCCD = {res['vneid']}, Hang = {res['license_class']}")
                else:
                    print(f"-> The {uid} chua co trong danh sach (Ly do: {res.get('reason')})")
        elif c == "2":
            uid = input("Nhap UID the: ").strip()
            name = input("Nhap Ho va Ten tai xe: ").strip()
            vneid = input("Nhap So CCCD (12 so): ").strip()
            lic = input("Hang bang lai (B1/B2/C... mac dinh B2): ").strip() or "B2"
            phone = input("So dien thoai (tuy chon): ").strip()
            ok, msg = register_driver(uid, vneid, name, lic, phone)
            print("-> [THANH CONG] Da luu thong tin tai xe!" if ok else f"-> [THAT BAI] Loi: {msg}")
        elif c == "3":
            drivers = list_all_drivers()
            if not drivers:
                print("-> Chua co tai xe nao.")
            else:
                print(f"{'ID':<4} | {'UID The':<14} | {'Ho va Ten':<20} | {'CCCD/VNeID':<14} | {'Hang':<5} | {'Trang thai'}")
                print("-" * 75)
                for d in drivers:
                    status = "Hoat dong" if d['is_active'] else "Da khoa"
                    print(f"{d['id']:<4} | {d['uid']:<14} | {d['name']:<20} | {d['vneid']:<14} | {d['license_class']:<5} | {status}")
        elif c == "4":
            uid = input("Nhap UID the can xoa: ").strip()
            if uid and delete_driver_by_uid(uid):
                print("-> Da xoa the thanh cong.")
            else:
                print("-> Khong the xoa the.")
        elif c == "5":
            from rfid_manager import DEFAULT_APPROVED_DRIVERS
            for d in DEFAULT_APPROVED_DRIVERS:
                register_driver(d["uid"], d["vneid"], d["name"], d["license_class"], d["phone"])
            print("-> Đã nạp 3 tài xế được duyệt:")
            print("   1. UID: 40107385065  | Tên: Lái xe 1 | CCCD: 040107385065")
            print("   2. UID: 530948377170 | Tên: Lái xe 2 | CCCD: 053094837717")
            print("   3. UID: 393004534388 | Tên: Lái xe 3 | CCCD: 039300453438")
        elif c == "0":
            print("Tam biet!")
            break

if __name__ == '__main__':
    main()
