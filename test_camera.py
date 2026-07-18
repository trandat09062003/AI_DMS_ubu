import cv2
import numpy as np
import os
import sys

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Testing camera and printing diagnostics...")
    parser.add_argument("--camera", type=int, default=None, help="Chi so camera su dung (mac dinh thu tu dong tu 0-10)")
    parser.add_argument("--mono", action="store_true", help="Che do giai ma don sac (Monochrome) cho camera Raw Bayer")
    parser.add_argument("--enhance", action="store_true", help="Bat buoc bat tang cuong do tuong phan CLAHE")
    parser.add_argument("--no-enhance", action="store_true", help="Vo hieu hoa tu dong tang cuong do tuong phan")
    args = parser.parse_args()

    print("Testing camera and printing diagnostics...")
    cap = None
    raw_bayer_mode = False
    raw_bayer_format = None
    camera_idx_found = -1
    
    # Load camera configuration if available
    camera_config = None
    config_path = "camera_config.json"
    import json
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                camera_config = json.load(f)
            print(f"Loaded camera configuration: {camera_config}")
        except Exception as e:
            print(f"Failed to load {config_path}: {e}")

    bayer_pattern = "GB"  # Default
    camera_flip_code = None
    if camera_config is not None:
        bayer_pattern = camera_config.get("bayer_pattern", "GB")
        camera_flip_code = camera_config.get("flip_code", None)
    
    # Quét danh sách camera (Nếu truyền --camera sẽ chỉ quét camera đó, nếu không sẽ quét tự động từ 0-10)
    camera_indices = [args.camera] if args.camera is not None else list(range(11))
    
    for camera_idx in camera_indices:
        try:
            print(f"Trying camera index {camera_idx}...")
            temp_cap = cv2.VideoCapture(camera_idx)
            if temp_cap.isOpened():
                ret = False
                try:
                    ret, _ = temp_cap.read()
                except:
                    pass
                if ret:
                    cap = temp_cap
                    camera_idx_found = camera_idx
                    print(f"Success opening camera index {camera_idx} in normal mode.")
                    break
                else:
                    print(f"Index {camera_idx} opened but could not read frame. Trying raw Bayer...")
                    temp_cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
                    
                    # GB10
                    temp_cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('G', 'B', '1', '0'))
                    temp_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    temp_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    ret_bayer = False
                    try:
                        ret_bayer, frame_bayer = temp_cap.read()
                    except:
                        pass
                    if ret_bayer and frame_bayer is not None and frame_bayer.size == 614400:
                        cap = temp_cap
                        camera_idx_found = camera_idx
                        raw_bayer_mode = True
                        raw_bayer_format = 'GB10'
                        print(f"Success opening camera index {camera_idx} in raw Bayer GB10.")
                        break
                        
                    # pGAA
                    temp_cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('p', 'G', 'A', 'A'))
                    temp_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    temp_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    ret_bayer = False
                    try:
                        ret_bayer, frame_bayer = temp_cap.read()
                    except:
                        pass
                    if ret_bayer and frame_bayer is not None and frame_bayer.size == 384000:
                        cap = temp_cap
                        camera_idx_found = camera_idx
                        raw_bayer_mode = True
                        raw_bayer_format = 'pGAA'
                        print(f"Success opening camera index {camera_idx} in raw Bayer pGAA.")
                        break
                    temp_cap.release()
            else:
                temp_cap.release()
        except Exception as e:
            print(f"Error testing index {camera_idx}: {e}")
            
    if cap is None:
        print("Error: No camera found!")
        sys.exit(1)
        
    # Read a few frames to let auto-exposure adjust
    print("Reading 30 frames to stabilize exposure...")
    frame = None
    for i in range(30):
        if raw_bayer_mode:
            ret, raw_frame = cap.read()
            if ret and raw_frame is not None:
                bayer_bgr_code = getattr(cv2, f"COLOR_Bayer{bayer_pattern}2BGR", cv2.COLOR_BayerGB2BGR)
                bayer_gray_code = getattr(cv2, f"COLOR_Bayer{bayer_pattern}2GRAY", cv2.COLOR_BayerGB2GRAY)
                
                if raw_bayer_format == 'GB10':
                    raw_16 = np.frombuffer(raw_frame.tobytes(), dtype=np.uint16).reshape((480, 640))
                    img_8 = (raw_16 >> 2).astype(np.uint8)
                    if args.mono:
                        img_gray = cv2.cvtColor(img_8, bayer_gray_code)
                        frame = cv2.cvtColor(img_gray, cv2.COLOR_GRAY2BGR)
                    else:
                        frame = cv2.cvtColor(img_8, bayer_bgr_code)
                elif raw_bayer_format == 'pGAA':
                    raw_bytes = np.frombuffer(raw_frame.tobytes(), dtype=np.uint8)
                    img_8 = raw_bytes.reshape(-1, 5)[:, :4].reshape((480, 640))
                    if args.mono:
                        img_gray = cv2.cvtColor(img_8, bayer_gray_code)
                        frame = cv2.cvtColor(img_gray, cv2.COLOR_GRAY2BGR)
                    else:
                        frame = cv2.cvtColor(img_8, bayer_bgr_code)
        else:
            ret, frame = cap.read()
            
        if frame is not None:
            if camera_flip_code is not None:
                frame = cv2.flip(frame, camera_flip_code)
            if len(frame.shape) == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            elif len(frame.shape) == 3 and frame.shape[2] == 1:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            
    if frame is None:
        print("Error: Could not read frame from camera.")
        sys.exit(1)
        
    print(f"Captured frame shape: {frame.shape}")
    print(f"Min pixel val: {np.min(frame)}, Max pixel val: {np.max(frame)}, Mean: {np.mean(frame)}")
    
    # Save raw capture
    cv2.imwrite("test_capture_raw.jpg", frame)
    print("Saved test_capture_raw.jpg")
    
    # Auto contrast enhancement logic
    is_mono = False
    if frame is not None and len(frame.shape) == 3:
        small_frame = cv2.resize(frame, (64, 48))
        b, g, r = cv2.split(small_frame)
        diff_bg = np.max(np.abs(b.astype(np.int16) - g.astype(np.int16)))
        diff_gr = np.max(np.abs(g.astype(np.int16) - r.astype(np.int16)))
        if diff_bg < 5 and diff_gr < 5:
            is_mono = True
            
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    contrast = gray.std()
    
    should_enhance = args.enhance or (
        not args.no_enhance and (is_mono or contrast < 35.0)
    )
    
    if should_enhance:
        print(f"Applying CLAHE contrast enhancement (is_mono: {is_mono}, contrast: {contrast:.2f})")
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
        cl = clahe.apply(l)
        enhanced = cv2.cvtColor(cv2.merge((cl, a, b)), cv2.COLOR_LAB2BGR)
    else:
        print(f"No enhancement applied (is_mono: {is_mono}, contrast: {contrast:.2f})")
        enhanced = frame.copy()
        
    cv2.imwrite("test_capture_enhanced.jpg", enhanced)
    print("Saved test_capture_enhanced.jpg")
    
    # Try face mesh
    try:
        import mediapipe.python.solutions.face_mesh as mp_face_mesh
        face_mesh = mp_face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=False,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )
        
        # Test on raw
        res_raw = face_mesh.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        detected_raw = res_raw.multi_face_landmarks is not None
        print(f"Face detected on RAW frame: {detected_raw}")
        
        # Test on enhanced
        res_enh = face_mesh.process(cv2.cvtColor(enhanced, cv2.COLOR_BGR2RGB))
        detected_enh = res_enh.multi_face_landmarks is not None
        print(f"Face detected on ENHANCED frame: {detected_enh}")
    except Exception as e:
        print(f"Error testing MediaPipe: {e}")
        
    cap.release()
    print("Done testing.")

if __name__ == "__main__":
    main()
