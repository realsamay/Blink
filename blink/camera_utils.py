# Camera detection utilities
import os

def get_camera_device(preferred_index=0):
    path = f'/dev/video{preferred_index}'
    return path if os.path.exists(path) else '/dev/video0'
