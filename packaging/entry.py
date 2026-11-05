"""Private, isolated runtime entry point; no global Python installation required."""
import ctypes
import os
from pathlib import Path
import sys
import traceback

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))

# Separate from the widget's file lock so a second launch cannot replace the PID.
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
kernel.CreateMutexW.restype = ctypes.c_void_p
kernel.CloseHandle.argtypes = [ctypes.c_void_p]
mutex = kernel.CreateMutexW(None, False, "Local\\CodexQuotaWidgetRuntime")
if not mutex:
    raise ctypes.WinError(ctypes.get_last_error())
if ctypes.get_last_error() == 183:
    kernel.CloseHandle(mutex)
    sys.exit(0)

pid_file = root / "runtime.pid"
try:
    pid_file.write_text(str(os.getpid()), encoding="ascii")
    import quota_widget
    quota_widget.main()
except Exception:
    (root / "runtime-error.log").write_text(traceback.format_exc(), encoding="utf-8")
    sys.exit(1)
finally:
    pid_file.unlink(missing_ok=True)
    kernel.CloseHandle(mutex)
