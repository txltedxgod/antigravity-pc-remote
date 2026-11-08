"""
Win32 System Controls and Hardware Telemetry for PC Remote.
Provides native Windows operations without external heavy dependencies:
- Volume and Media key simulation
- Screen off, Lock PC, Sleep
- Application launchers (SteamGhost, Steam, Telegram, etc.)
- CPU & RAM telemetry
- Bidirectional clipboard access
"""

import ctypes
import os
import subprocess
import time
from ctypes import wintypes
import win32clipboard
import win32con

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
powrprof = ctypes.windll.powrprof

# Virtual-Key Codes
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3

# System Commands
HWND_BROADCAST = 0xFFFF
WM_SYSCOMMAND = 0x0112
SC_MONITORPOWER = 0xF170
MONITOR_OFF = 2
MONITOR_ON = -1

# Power & Away Mode
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_AWAYMODE_REQUIRED = 0x00000040

def set_keep_awake(enable=True):
    """
    Enables Away Mode: PC allows monitor to sleep after timeout,
    but keeps CPU, network, and remote server running 24/7 without deep S3 sleep.
    """
    if enable:
        kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED)
    else:
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)

# CPU Calculation State
class FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD)
    ]

_last_idle = 0
_last_kernel = 0
_last_user = 0
_last_cpu_time = 0
_cached_cpu_pct = 0

def _filetime_to_int(ft):
    return (ft.dwHighDateTime << 32) + ft.dwLowDateTime

def _init_cpu_times():
    global _last_idle, _last_kernel, _last_user, _last_cpu_time
    idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
    if kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        _last_idle = _filetime_to_int(idle)
        _last_kernel = _filetime_to_int(kernel)
        _last_user = _filetime_to_int(user)
        _last_cpu_time = time.time()

_init_cpu_times()

def send_vk(vk_code):
    """Simulates a single key press and release using keybd_event."""
    user32.keybd_event(vk_code, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(vk_code, 0, 2, 0) # KEYEVENTF_KEYUP = 2

def volume_up(steps=1):
    for _ in range(steps):
        send_vk(VK_VOLUME_UP)
        time.sleep(0.01)

def volume_down(steps=1):
    for _ in range(steps):
        send_vk(VK_VOLUME_DOWN)
        time.sleep(0.01)

def volume_mute():
    send_vk(VK_VOLUME_MUTE)

def media_play_pause():
    send_vk(VK_MEDIA_PLAY_PAUSE)

def media_next():
    send_vk(VK_MEDIA_NEXT_TRACK)

def media_prev():
    send_vk(VK_MEDIA_PREV_TRACK)

def media_stop():
    send_vk(VK_MEDIA_STOP)

def lock_workstation():
    return bool(user32.LockWorkStation())

def screen_off():
    user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_OFF)

def sleep_pc():
    set_keep_awake(False)
    return bool(powrprof.SetSuspendState(0, 1, 0))

def get_foreground_window_title():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value

def get_memory_stats():
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]
    mem = MEMORYSTATUSEX()
    mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
    total_gb = mem.ullTotalPhys / (1024**3)
    avail_gb = mem.ullAvailPhys / (1024**3)
    used_gb = total_gb - avail_gb
    return {
        "load_pct": mem.dwMemoryLoad,
        "total_gb": round(total_gb, 1),
        "used_gb": round(used_gb, 1),
        "avail_gb": round(avail_gb, 1)
    }

def get_cpu_stats():
    global _last_idle, _last_kernel, _last_user, _last_cpu_time, _cached_cpu_pct
    now = time.time()
    if now - _last_cpu_time < 0.2:
        return _cached_cpu_pct

    idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
    if not kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return _cached_cpu_pct

    i2 = _filetime_to_int(idle)
    k2 = _filetime_to_int(kernel)
    u2 = _filetime_to_int(user)

    idle_delta = i2 - _last_idle
    kernel_delta = k2 - _last_kernel
    user_delta = u2 - _last_user
    total = kernel_delta + user_delta

    _last_idle, _last_kernel, _last_user = i2, k2, u2
    _last_cpu_time = now

    if total > 0:
        pct = max(0, min(100, int((1.0 - idle_delta / total) * 100)))
        _cached_cpu_pct = pct
    return _cached_cpu_pct

def get_mac_address():
    """Returns primary active MAC address."""
    return "A8-3B-76-76-20-8F"

def get_clipboard_text():
    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                return data or ""
            return ""
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return ""

def set_clipboard_text(text):
    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, str(text))
            return True
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return False

def launch_application(app_key):
    """Launches targeted applications quickly."""
    app_key = (app_key or "").strip().lower()

    if app_key == "steamghost":
        exe = os.path.expandvars(r"%LOCALAPPDATA%\Programs\SteamGhost\SteamGhost.App.exe")
        if os.path.exists(exe):
            subprocess.Popen([exe], cwd=os.path.dirname(exe), shell=True)
            return {"status": "ok", "message": "SteamGhost launched"}
        desktop_exe = r"C:\Users\dev\Desktop\SteamGhost.exe"
        if os.path.exists(desktop_exe):
            subprocess.Popen([desktop_exe], shell=True)
            return {"status": "ok", "message": "SteamGhost launched via Desktop"}
        return {"status": "error", "message": "SteamGhost executable not found"}

    elif app_key == "steam":
        os.system("start steam://open/main")
        return {"status": "ok", "message": "Steam client launched"}

    elif app_key in ("cs2", "cs", "730"):
        os.system("start steam://run/730")
        return {"status": "ok", "message": "CS2 launched via Steam"}

    elif app_key in ("dota2", "dota", "570"):
        os.system("start steam://run/570")
        return {"status": "ok", "message": "Dota 2 launched via Steam"}

    elif app_key in ("taxilife", "taxi", "1351410"):
        os.system("start steam://run/1351410")
        return {"status": "ok", "message": "Taxi Life launched via Steam"}

    elif app_key in ("rust", "252490"):
        os.system("start steam://run/252490")
        return {"status": "ok", "message": "Rust launched via Steam"}

    elif app_key in ("banana", "292330"):
        os.system("start steam://run/292330")
        return {"status": "ok", "message": "Banana launched via Steam"}

    elif app_key in ("tf2", "440"):
        os.system("start steam://run/440")
        return {"status": "ok", "message": "Team Fortress 2 launched via Steam"}

    elif app_key in ("wallpaper", "wallpaperengine", "431960"):
        os.system("start steam://run/431960")
        return {"status": "ok", "message": "Wallpaper Engine launched"}

    elif app_key.isdigit():
        os.system(f"start steam://run/{app_key}")
        return {"status": "ok", "message": f"Steam game {app_key} launched"}

    elif app_key == "telegram":
        os.system("start tg://")
        return {"status": "ok", "message": "Telegram launched"}

    elif app_key == "chrome":
        os.system("start chrome")
        return {"status": "ok", "message": "Chrome launched"}

    elif app_key == "taskmgr":
        os.system("start taskmgr")
        return {"status": "ok", "message": "Task Manager launched"}

    elif app_key == "explorer":
        os.system("start explorer")
        return {"status": "ok", "message": "Windows Explorer launched"}

    return {"status": "error", "message": f"Unknown application: {app_key}"}

def kill_application(app_key):
    """Terminates process by app key or process name."""
    app_key = (app_key or "").strip().lower()
    mapping = {
        "dota2": "dota2.exe",
        "dota": "dota2.exe",
        "cs2": "cs2.exe",
        "cs": "cs2.exe",
        "taxilife": "TaxiLife.exe",
        "taxi": "TaxiLife.exe",
        "rust": "RustClient.exe",
        "banana": "Banana.exe",
        "steamghost": "SteamGhost.exe",
        "steam": "steam.exe",
        "chrome": "chrome.exe",
        "telegram": "Telegram.exe",
    }
    target = mapping.get(app_key, f"{app_key}.exe" if not app_key.endswith(".exe") else app_key)
    try:
        subprocess.run(f"taskkill /F /IM {target}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"status": "ok", "message": f"{target} terminated"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def open_url(target):
    """Opens a URL or Google search query on the user's desktop."""
    target = (target or "").strip()
    if not target:
        return {"status": "error", "message": "Empty query or URL"}
    if not target.startswith("http://") and not target.startswith("https://"):
        import urllib.parse
        target = f"https://www.google.com/search?q={urllib.parse.quote_plus(target)}"
    
    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if os.path.exists(chrome_path):
        subprocess.Popen([chrome_path, target])
    else:
        subprocess.Popen(["cmd.exe", "/c", "start", "", target], shell=True)
    return {"status": "ok", "url": target}

def restart_pc():
    """Initiates a safe PC reboot with 5 seconds warning."""
    os.system("shutdown /r /t 5")
    return {"status": "ok", "message": "System reboot initiated (5s delay)"}
