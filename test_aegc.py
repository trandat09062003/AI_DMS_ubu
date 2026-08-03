import cv2
import numpy as np
import subprocess
import time
import threading

current_exposure = 250
current_gain = 500
aegc_running = False

def set_camera_controls(exposure_val, gain_val):
    try:
        subprocess.run([
            "v4l2-ctl", "-d", "/dev/v4l-subdev0",
            "-c", "auto_exposure=1",
            "-c", "gain_automatic=0",
            "-c", f"exposure={exposure_val}",
            "-c", f"analogue_gain={gain_val}"
        ], capture_output=True)
    except Exception as e:
        print(f"Error setting controls: {e}")

def aegc_loop(mean_brightness):
    global current_exposure, current_gain
    target = 120.0
    diff = target - mean_brightness
    
    # Proportional control
    if abs(diff) > 8:
        # Scale steps based on difference
        step_exp = int(diff * 1.2)
        step_gain = int(diff * 1.8)
        
        new_exposure = max(4, min(500, current_exposure + step_exp))
        new_gain = max(16, min(1023, current_gain + step_gain))
        
        if new_exposure != current_exposure or new_gain != current_gain:
            current_exposure = new_exposure
            current_gain = new_gain
            # Set values
            set_camera_controls(current_exposure, current_gain)

def main():
    global current_exposure, current_gain
    
    # Initial manual configuration
    set_camera_controls(current_exposure, current_gain)
    
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Cannot open /dev/video0")
        return
        
    cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('G', 'B', '1', '0'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    
    print("Running AEGC test for 5 seconds...")
    start_time = time.time()
    frame_count = 0
    
    while time.time() - start_time < 5.0:
        ret, frame = cap.read()
        if not ret or frame is None:
            print("Failed to capture frame")
            break
            
        frame_count += 1
        
        # Convert GB10 to 8-bit grayscale
        try:
            raw_16 = np.frombuffer(frame.tobytes(), dtype=np.uint16).reshape((480, 640))
            img_8 = (raw_16 >> 2).astype(np.uint8)
            mean_val = np.mean(img_8)
            
            # Apply AEGC every 5 frames to avoid control oscillation and slow down
            if frame_count % 5 == 0:
                aegc_loop(mean_val)
                print(f"Frame {frame_count}: Mean Brightness = {mean_val:.2f} | Exposure = {current_exposure} | Gain = {current_gain}")
                
        except Exception as e:
            print(f"Error: {e}")
            break
            
        time.sleep(0.03)
        
    # Capture final frame and save it
    ret, frame = cap.read()
    if ret and frame is not None:
        raw_16 = np.frombuffer(frame.tobytes(), dtype=np.uint16).reshape((480, 640))
        img_8 = (raw_16 >> 2).astype(np.uint8)
        img_bgr = cv2.cvtColor(img_8, cv2.COLOR_BayerGB2BGR)
        cv2.imwrite("test_capture_aegc.jpg", img_bgr)
        print(f"Saved test_capture_aegc.jpg (final mean: {np.mean(img_8):.2f})")
        
    cap.release()

if __name__ == "__main__":
    main()
