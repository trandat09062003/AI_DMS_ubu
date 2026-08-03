import cv2
import numpy as np
import subprocess
import time

def set_camera_controls(exposure_val, gain_val):
    try:
        print(f"Setting exposure={exposure_val}, gain={gain_val}...")
        # auto_exposure=1 is Manual Mode in V4L2 (or sometimes 1 is manual, 0 is auto)
        # Let's try setting auto_exposure to manual mode (1) or auto mode (0)
        # According to v4l2-ctl -l: auto_exposure 0x009a0901 (menu): min=0 max=1 default=1 value=0 (Auto Mode)
        # Value 1 is probably Manual Mode.
        subprocess.run([
            "v4l2-ctl", "-d", "/dev/v4l-subdev0",
            "-c", "auto_exposure=1",
            "-c", "gain_automatic=0",
            "-c", f"exposure={exposure_val}",
            "-c", f"analogue_gain={gain_val}"
        ], capture_output=True)
    except Exception as e:
        print(f"Error setting controls: {e}")

def main():
    # Set high exposure and gain first
    set_camera_controls(500, 800)
    
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Cannot open /dev/video0")
        return
        
    cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('G', 'B', '1', '0'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    
    # Read a few frames to apply settings and clear buffer
    for i in range(10):
        ret, frame = cap.read()
        time.sleep(0.1)
        
    ret, frame = cap.read()
    if not ret or frame is None:
        print("Failed to capture frame")
        cap.release()
        return
        
    print(f"Captured size: {frame.size}, shape: {frame.shape}")
    
    # Convert GB10 to 8-bit grayscale / BGR
    try:
        raw_16 = np.frombuffer(frame.tobytes(), dtype=np.uint16).reshape((480, 640))
        # Let's save both raw shift and different offsets to see where the bits are
        for shift in [0, 2, 4, 6, 8]:
            img_8 = (raw_16 >> shift).astype(np.uint8)
            img_bgr = cv2.cvtColor(img_8, cv2.COLOR_BayerGB2BGR)
            filename = f"test_capture_shift_{shift}.jpg"
            cv2.imwrite(filename, img_bgr)
            print(f"Saved {filename} (mean: {np.mean(img_8):.2f})")
    except Exception as e:
        print(f"Error converting: {e}")
        
    cap.release()

if __name__ == "__main__":
    main()
