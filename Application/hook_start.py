"""Quiet entry point used by ojiisansubscreen.dll (no package installation)."""
import ctypes
import hashlib
import os
from pathlib import Path
import sys
import traceback

from app_paths import APP_ROOT
root = APP_ROOT
os.chdir(root)
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(root / ".dependencies"))
log = open(root / "subscreen-launch.log", "a", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = log
mutex = None
try:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    name = "Local\\OjiisanSubscreen-" + hashlib.sha256(str(root).lower().encode()).hexdigest()[:24]
    mutex = kernel.CreateMutexW(None, False, name)
    if not mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        print("Subscreen is already running for this application directory.")
    else:
        print(f"Starting subscreen PID {os.getpid()}: {sys.argv[1:]}")
        from OjiisanSubscreen import main
        main()
        print("Subscreen closed.")
except Exception:
    traceback.print_exc()
finally:
    if mutex:
        kernel.CloseHandle(mutex)
