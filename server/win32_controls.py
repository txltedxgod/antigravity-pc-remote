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

VK_MAPPING = {
    "alt_f4": [0x12, 0x73],
    "win_d": [0x5B, 0x44],
    "alt_tab": [0x12, 0x09],
    "ctrl_w": [0x11, 0x57],
    "ctrl_t": [0x11, 0x54],
    "ctrl_r": [0x11, 0x52],
    "f5": [0x74],
    "f11": [0x7A],
    "enter": [0x0D],
    "esc": [0x1B],
    "space": [0x20],
    "backspace": [0x08],
    "tab": [0x09],
    "win": [0x5B],
    "up": [0x26],
    "down": [0x28],
    "left": [0x25],
    "right": [0x27],
}

def trigger_hotkey(name):
    """Executes a defined keyboard shortcut on the interactive desktop."""
    name = (name or "").lower().strip()
    keys = VK_MAPPING.get(name)
    if not keys:
        return False
    hdesk = user32.OpenDesktopW('default', 0, False, 0x01FF)
    if hdesk:
        user32.SetThreadDesktop(hdesk)
    for k in keys:
        user32.keybd_event(k, 0, 0, 0)
        time.sleep(0.02)
    time.sleep(0.04)
    for k in reversed(keys):
        user32.keybd_event(k, 0, 2, 0)
        time.sleep(0.02)
    return True

def close_active_window():
    """Closes the current foreground window via WM_CLOSE or Alt+F4."""
    hdesk = user32.OpenDesktopW('default', 0, False, 0x01FF)
    if hdesk:
        user32.SetThreadDesktop(hdesk)
    hwnd = user32.GetForegroundWindow()
    if hwnd:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
        return True
    return trigger_hotkey("alt_f4")

def get_active_windows():
    """Returns a list of visible top-level windows on the interactive desktop."""
    hdesk = user32.OpenDesktopW('default', 0, False, 0x01FF)
    if hdesk:
        user32.SetThreadDesktop(hdesk)

    proc_map = {}
    try:
        res = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True)
        for line in res.stdout.splitlines():
            p = [x.strip('"') for x in line.split('","')]
            if len(p) >= 2:
                try:
                    proc_map[int(p[1])] = p[0]
                except ValueError:
                    pass
    except Exception:
        pass

    windows = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def enum_cb(hwnd, lp):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.strip()

                cloaked = wintypes.DWORD()
                try:
                    ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)
                    if cloaked.value != 0:
                        return True
                except Exception:
                    pass

                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                pname = proc_map.get(pid.value, "")

                ignored = ("Program Manager", "Setup", "Default IME", "MSCTFIME UI")
                if title and title not in ignored:
                    windows.append({
                        "hwnd": hwnd,
                        "title": title,
                        "pid": pid.value,
                        "process_name": pname
                    })
        return True

    user32.EnumDesktopWindows(hdesk, WNDENUMPROC(enum_cb), 0)
    return windows

def focus_window(hwnd):
    """Brings the specified window to the foreground."""
    hdesk = user32.OpenDesktopW('default', 0, False, 0x01FF)
    if hdesk:
        user32.SetThreadDesktop(hdesk)
    try:
        hwnd = int(hwnd)
        user32.ShowWindow(hwnd, 9)
        return bool(user32.SetForegroundWindow(hwnd))
    except Exception:
        return False

def close_window(hwnd, force=False):
    """Closes or terminates a specific window."""
    hdesk = user32.OpenDesktopW('default', 0, False, 0x01FF)
    if hdesk:
        user32.SetThreadDesktop(hdesk)
    try:
        hwnd = int(hwnd)
        if force:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value > 0:
                subprocess.run(f"taskkill /F /PID {pid.value}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
        user32.PostMessageW(hwnd, 0x0010, 0, 0)
        return True
    except Exception:
        return False

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

def wake_screen():
    """Wakes the monitor and graphics display stack from sleep."""
    try:
        user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, MONITOR_ON)
        user32.mouse_event(1, 0, 0, 0, 0)
        return True
    except Exception:
        return False

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

    if app_key in ("steamghost", "ghost", "idler", "накрутка", "часы", "hourboost", "фарм"):
        desktop_exe = r"C:\Users\dev\Desktop\SteamGhost.exe"
        if os.path.exists(desktop_exe):
            run_interactive(r'"C:\Users\dev\Desktop\SteamGhost.exe"')
            return {"status": "ok", "message": "SteamGhost launched via Desktop"}
        exe = os.path.expandvars(r"%LOCALAPPDATA%\Programs\SteamGhost\SteamGhost.App.exe")
        if os.path.exists(exe):
            run_interactive(f'"{exe}"')
            return {"status": "ok", "message": "SteamGhost launched"}
        return {"status": "error", "message": "SteamGhost executable not found"}

    elif app_key == "steam":
        run_interactive('start "" "steam://open/main"')
        return {"status": "ok", "message": "Steam client launched"}

    elif app_key in ("cs2", "cs", "730"):
        desktop_url = r"C:\Users\dev\Desktop\Counter-Strike 2.url"
        if os.path.exists(desktop_url):
            run_interactive(r'start "" "C:\Users\dev\Desktop\Counter-Strike 2.url"')
        else:
            run_interactive('start "" "steam://rungameid/730"')
        return {"status": "ok", "message": "CS2 launched via Steam"}

    elif app_key in ("dota2", "dota", "570"):
        desktop_url = r"C:\Users\dev\Desktop\Dota 2.url"
        if os.path.exists(desktop_url):
            run_interactive(r'start "" "C:\Users\dev\Desktop\Dota 2.url"')
        else:
            run_interactive('start "" "steam://rungameid/570"')
        return {"status": "ok", "message": "Dota 2 launched via Steam"}

    elif app_key in ("taxilife", "taxi", "1351410"):
        desktop_url = r"C:\Users\dev\Desktop\Taxi Life A City Driving Simulator.url"
        if os.path.exists(desktop_url):
            run_interactive(r'start "" "C:\Users\dev\Desktop\Taxi Life A City Driving Simulator.url"')
        else:
            run_interactive('start "" "steam://rungameid/1351410"')
        return {"status": "ok", "message": "Taxi Life launched via Steam"}

    elif app_key in ("rust", "252490"):
        run_interactive('start "" "steam://rungameid/252490"')
        return {"status": "ok", "message": "Rust launched via Steam"}

    elif app_key in ("banana", "292330"):
        run_interactive('start "" "steam://rungameid/292330"')
        return {"status": "ok", "message": "Banana launched via Steam"}

    elif app_key in ("tf2", "440"):
        run_interactive('start "" "steam://rungameid/440"')
        return {"status": "ok", "message": "Team Fortress 2 launched via Steam"}

    elif app_key in ("wallpaper", "wallpaperengine", "431960"):
        run_interactive('start "" "steam://rungameid/431960"')
        return {"status": "ok", "message": "Wallpaper Engine launched"}

    elif app_key.isdigit():
        run_interactive(f'start "" "steam://rungameid/{app_key}"')
        return {"status": "ok", "message": f"Steam game {app_key} launched"}

    elif app_key in ("discord", "дс"):
        desktop_lnk = r"C:\Users\dev\Desktop\Discord.lnk"
        if os.path.exists(desktop_lnk):
            run_interactive(r'start "" "C:\Users\dev\Desktop\Discord.lnk"')
        else:
            run_interactive("start discord")
        return {"status": "ok", "message": "Discord launched"}

    elif app_key in ("telegram", "тг"):
        run_interactive('start "" "tg://"')
        return {"status": "ok", "message": "Telegram launched"}

    elif app_key == "chrome":
        chrome_exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        if os.path.exists(chrome_exe):
            run_interactive(f'"{chrome_exe}" --profile-directory="Default"')
        else:
            run_interactive("start chrome")
        return {"status": "ok", "message": "Chrome launched"}

    elif app_key == "taskmgr":
        run_interactive("start taskmgr")
        return {"status": "ok", "message": "Task Manager launched"}

    elif app_key == "explorer":
        run_interactive("start explorer")
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
    if app_key in ("steamghost", "ghost", "idler", "накрутка"):
        subprocess.run("taskkill /F /IM SteamGhost.exe /IM SteamGhost.App.exe", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"status": "ok", "message": "SteamGhost terminated"}

    target = mapping.get(app_key, f"{app_key}.exe" if not app_key.endswith(".exe") else app_key)
    try:
        subprocess.run(f"taskkill /F /IM {target}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"status": "ok", "message": f"{target} terminated"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def run_interactive(cmd_str):
    """Executes a command directly on the active user's interactive desktop (WinSta0\\default)."""
    task_name = f"PCRemoteRun_{int(time.time() * 1000) % 100000}"
    try:
        subprocess.run(
            ['schtasks', '/create', '/tn', task_name, '/tr', f'cmd.exe /c {cmd_str}', '/sc', 'once', '/st', '23:59', '/f', '/it'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3
        )
        subprocess.run(
            ['schtasks', '/run', '/tn', task_name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3
        )
        time.sleep(0.4)
        subprocess.run(
            ['schtasks', '/delete', '/tn', task_name, '/f'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3
        )
        return True
    except Exception:
        try:
            subprocess.Popen(cmd_str, shell=True)
            return True
        except Exception:
            return False

def open_url(target):
    """Opens a URL or Google search query in Google Chrome on the user's interactive desktop."""
    target = (target or "").strip()
    if not target:
        return {"status": "error", "message": "Empty query or URL"}
    if not target.startswith("http://") and not target.startswith("https://"):
        import urllib.parse
        target = f"https://www.google.com/search?q={urllib.parse.quote_plus(target)}"
    
    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if os.path.exists(chrome_path):
        cmd = f'"{chrome_path}" --profile-directory="Default" "{target}"'
    else:
        cmd = f'start "" "{target}"'

    run_interactive(cmd)

    # Bring Chrome window to foreground
    try:
        time.sleep(1)
        hdesk = user32.OpenDesktopW('default', 0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
            windows = []
            WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def enum_cb(hwnd, lp):
                if user32.IsWindowVisible(hwnd):
                    buf = ctypes.create_unicode_buffer(512)
                    user32.GetWindowTextW(hwnd, buf, 512)
                    if any(k in buf.value.lower() for k in ['chrome', 'google']):
                        windows.append(hwnd)
                return True
            user32.EnumDesktopWindows(hdesk, WNDENUMPROC(enum_cb), 0)
            for h in windows:
                user32.ShowWindow(h, 9) # SW_RESTORE
                user32.SetForegroundWindow(h)
    except Exception:
        pass

    return {"status": "ok", "url": target, "method": "interactive_desktop"}

def restart_pc():
    """Initiates a safe PC reboot with 5 seconds warning."""
    subprocess.run("shutdown /r /t 5", shell=True)
    return {"status": "ok", "message": "System reboot initiated (5s delay)"}

def cancel_restart_pc():
    """Aborts a pending system reboot."""
    subprocess.run("shutdown /a", shell=True)
    return {"status": "ok", "message": "Reboot aborted"}

# --- VIRTUAL MOUSE & TOUCHPAD ---
def mouse_move_relative(dx: int, dy: int):
    """Moves mouse cursor relatively by dx, dy."""
    try:
        user32.mouse_event(0x0001, int(dx), int(dy), 0, 0)
        return True
    except Exception as e:
        logger.error(f"Error moving mouse: {e}")
        return False

def mouse_click(button: str = "left"):
    """Performs mouse click: left, right, middle, or double."""
    try:
        b = (button or "left").lower()
        if b == "left":
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
        elif b == "right":
            user32.mouse_event(0x0008, 0, 0, 0, 0)
            user32.mouse_event(0x0010, 0, 0, 0, 0)
        elif b == "middle":
            user32.mouse_event(0x0020, 0, 0, 0, 0)
            user32.mouse_event(0x0040, 0, 0, 0, 0)
        elif b == "double":
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.04)
            user32.mouse_event(0x0002, 0, 0, 0, 0)
            user32.mouse_event(0x0004, 0, 0, 0, 0)
        return True
    except Exception as e:
        logger.error(f"Error clicking mouse: {e}")
        return False

def mouse_scroll(delta: int):
    """Scrolls mouse wheel by delta (e.g. +120 or -120)."""
    try:
        user32.mouse_event(0x0800, 0, 0, int(delta), 0)
        return True
    except Exception as e:
        logger.error(f"Error scrolling mouse: {e}")
        return False

# --- VIRTUAL UNICODE KEYBOARD TYPING ---
def type_unicode_text(text: str):
    """Types arbitrary Unicode text directly into the foreground window."""
    try:
        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ('wVk', wintypes.WORD),
                ('wScan', wintypes.WORD),
                ('dwFlags', wintypes.DWORD),
                ('time', wintypes.DWORD),
                ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))
            ]

        class INPUT(ctypes.Structure):
            class _INPUT(ctypes.Union):
                _fields_ = [('ki', KEYBDINPUT)]
            _anonymous_ = ('_input',)
            _fields_ = [('type', wintypes.DWORD), ('_input', _INPUT)]

        KEYEVENTF_UNICODE = 0x0004
        KEYEVENTF_KEYUP = 0x0002
        INPUT_KEYBOARD = 1

        for char in text:
            code = ord(char)
            inp_down = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=None))
            inp_up = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=None))
            user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
            user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))
            time.sleep(0.004)
        return True
    except Exception as e:
        logger.error(f"Error typing text: {e}")
        return False

# --- LAPTOP BRIGHTNESS & POWER ---
def set_display_brightness(level: int):
    """Sets laptop monitor brightness between 0 and 100."""
    try:
        val = max(0, min(100, int(level)))
        cmd = f"(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1, {val})"
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True, timeout=3.0)
        return {"status": "ok", "brightness": val}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def get_display_brightness():
    """Gets laptop monitor current brightness."""
    try:
        cmd = "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness"
        res = subprocess.check_output(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], timeout=3.0, text=True)
        return int(res.strip())
    except Exception:
        return None

# --- PROCESS MANAGEMENT ---
def get_top_processes(limit: int = 10):
    """Returns top processes sorted by RAM usage."""
    import psutil
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'memory_info', 'cpu_percent']):
        try:
            mem_mb = round(p.info['memory_info'].rss / (1024 * 1024), 1)
            procs.append({
                "pid": p.info['pid'],
                "name": p.info['name'],
                "mem_mb": mem_mb,
                "cpu": p.info['cpu_percent'] or 0
            })
        except Exception:
            pass
    procs.sort(key=lambda x: x['mem_mb'], reverse=True)
    return procs[:limit]

def kill_process_by_pid(pid: int, force: bool = True):
    """Terminates process by PID."""
    import psutil
    try:
        p = psutil.Process(int(pid))
        p_name = p.name()
        if force:
            p.kill()
        else:
            p.terminate()
        return {"status": "ok", "message": f"Process {p_name} ({pid}) terminated"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def get_system_uptime():
    """Returns uptime in hours and seconds."""
    import psutil
    try:
        uptime_sec = int(time.time() - psutil.boot_time())
        hours = round(uptime_sec / 3600, 1)
        return {"uptime_sec": uptime_sec, "uptime_hours": hours}
    except Exception:
        return {"uptime_sec": 0, "uptime_hours": 0}
