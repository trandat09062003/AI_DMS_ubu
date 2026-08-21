import subprocess
import time
import os
import sys

def get_all_ips():
    ips = set()
    try:
        res = subprocess.run(["hostname", "-I"], capture_output=True, text=True)
        for part in res.stdout.strip().split():
            if "." in part and not part.startswith("127."):
                ips.add(part)
    except Exception:
        pass
    try:
        res = subprocess.run(["ip", "-4", "addr", "show"], capture_output=True, text=True)
        for line in res.stdout.split("\n"):
            if "inet " in line:
                ip = line.strip().split()[1].split("/")[0]
                if not ip.startswith("127."):
                    ips.add(ip)
    except Exception:
        pass
    return sorted(list(ips))

def main():
    domains = ["dms.local", "wifi.local", "setup.local", "aidms.local"]
    current_ips = []
    proc_list = []

    print("[INFO] Start All-Interface mDNS Domain Publisher for AI DMS (dms.local)...")

    while True:
        ips = get_all_ips()
        if ips != current_ips:
            print(f"[mDNS] Danh sach IP thay doi thanh: {ips}. Dang dang ky domain {domains}...")
            current_ips = ips
            
            # Kill existing processes
            for p in proc_list:
                try:
                    p.terminate()
                except Exception:
                    pass
            proc_list = []
            
            # Publish all domains on all active IP addresses
            for ip in ips:
                for domain in domains:
                    try:
                        p = subprocess.Popen(["avahi-publish", "-a", "-R", domain, ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        proc_list.append(p)
                    except Exception as e:
                        print(f"[WARN] Loi publish {domain} ({ip}): {e}")
                        
        time.sleep(4)

if __name__ == "__main__":
    main()

