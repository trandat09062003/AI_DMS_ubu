import subprocess
import time
import os
import sys

def get_wlan0_ip():
    try:
        res = subprocess.run(["ip", "-4", "addr", "show", "wlan0"], capture_output=True, text=True)
        for line in res.stdout.split("\n"):
            if "inet " in line:
                return line.strip().split()[1].split("/")[0]
    except Exception:
        pass
    return None

def main():
    domains = ["wifi.local", "dms.local", "setup.local", "aidms.local"]
    current_ip = None
    proc_list = []

    print("[INFO] Start mDNS Domain Publisher for AI DMS (wifi.local, dms.local)...")

    while True:
        ip = get_wlan0_ip()
        if ip and ip != current_ip:
            print(f"[mDNS] IP thay doi thanh: {ip}. Dang dang ky domain: {domains}...")
            current_ip = ip
            
            # Kill existing processes
            for p in proc_list:
                try:
                    p.terminate()
                except Exception:
                    pass
            proc_list = []
            
            # Publish new IP for all domain aliases
            for domain in domains:
                try:
                    p = subprocess.Popen(["avahi-publish", "-a", "-R", domain, ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    proc_list.append(p)
                except Exception as e:
                    print(f"[WARN] Loi publish {domain}: {e}")
                    
        time.sleep(4)

if __name__ == "__main__":
    main()
