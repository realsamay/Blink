# PAM timeout guard
import time

DEFAULT_TIMEOUT_SEC = 5.0

def check_timeout(start_time, max_seconds=DEFAULT_TIMEOUT_SEC):
    return (time.time() - start_time) > max_seconds
