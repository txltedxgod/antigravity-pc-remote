"""
Win32 System Controls and Hardware Telemetry for PC Remote.
Provides native Windows operations without external heavy dependencies:
- Volume and Media key simulation
- Screen off, Lock PC, Sleep
- Application launchers (SteamGhost, Steam, Telegram, etc.)
- CPU & RAM telemetry
- Bidirectional clipboard access
"""

import contextlib
import ctypes
import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.parse
from ctypes import wintypes
from pathlib import Path
import win32clipboard
import win32con

logger = logging.getLogger("Win32Controls")
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
powrprof = ctypes.windll.powrprof
shell32 = ctypes.windll.shell32

_cpu_lock = threading.Lock()
_SAFE_EXE_RE = re.compile(r"^[a-zA-Z0-9._-]{1,64}\.exe$")
_SAFE_HOST_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.:-]{0,252}$")

# --- GAME MODE ENGINE (NON-INTRUSIVE BACKGROUND CONTROL) ---
GAME_MODE_FILE = Path(__file__).resolve().parent / "game_mode_config.json"
KNOWN_GAME_EXES = {
    "cs2.exe", "csgo.exe", "dota2.exe", "valorant.exe", "valorant-win64-shipping.exe",
    "r5apex.exe", "gta5.exe", "playgtav.exe", "cyberpunk2077.exe", "eldenring.exe",
    "rustclient.exe", "fortniteclient-win64-shipping.exe", "overwatch.exe", "tslgame.exe",
    "escapefromtarkov.exe", "worldoftanks.exe", "witcher3.exe", "bg3.exe", "bg3_dx11.exe",
    "deadlock.exe", "project8.exe", "helldivers2.exe", "palworld-win64-shipping.exe",
    "rocketleague.exe", "league of legends.exe", "minecraft.windows.exe", "hl2.exe",
    "forzahorizon5.exe", "rdr2.exe", "starfield.exe", "warframe.x64.exe", "destiny2.exe",
    "pubg.exe", "bf2042.exe", "cod.exe", "rainbow six.exe", "rainbowsix.exe"
}
NON_GAME_FULLSCREEN_EXES = {
    "explorer.exe", "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "brave.exe",
    "antigravity.exe", "code.exe", "dwm.exe", "shellexperiencehost.exe", "searchhost.exe",
    "taskmgr.exe", "applicationframehost.exe", "lockapp.exe", "logonui.exe"
}
_DISRUPTIVE_GAME_HOTKEYS = {"win_d", "alt_tab", "alt_f4", "ctrl_w", "win", "f11"}

_game_mode_lock = threading.Lock()
_game_mode_setting = "auto"  # "auto" | "on" | "off"
_game_detect_cache = {"ts": 0.0, "is_game": False, "game_name": None}
_last_priority_mode = None  # None | "gaming" | "normal"

def _load_game_mode_setting():
    global _game_mode_setting
    try:
        if GAME_MODE_FILE.exists():
            data = json.loads(GAME_MODE_FILE.read_text(encoding="utf-8"))
            mode = str(data.get("mode", "auto")).lower().strip()
            if mode in ("auto", "on", "off"):
                _game_mode_setting = mode
    except Exception:
        pass
    return _game_mode_setting

_load_game_mode_setting()

def set_game_mode(mode: str):
    """Sets Game Mode preference: 'auto', 'on', or 'off'."""
    global _game_mode_setting
    clean = (mode or "auto").lower().strip()
    if clean not in ("auto", "on", "off"):
        return {"status": "error", "message": "Invalid game mode. Expected: auto, on, off"}
    with _game_mode_lock:
        _game_mode_setting = clean
        try:
            GAME_MODE_FILE.write_text(json.dumps({"mode": clean}), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Could not save game mode config: {e}")
    status = get_game_mode_status(force_refresh=True)
    return {"status": "ok", **status}

def _apply_gaming_process_priorities(gaming_active: bool):
    """Lowers background server/tunnel/IDE priority during gaming to prevent FPS micro-stutters."""
    global _last_priority_mode
    target_mode = "gaming" if gaming_active else "normal"
    if _last_priority_mode == target_mode:
        return
    _last_priority_mode = target_mode
    try:
        import psutil
        target_prio = psutil.BELOW_NORMAL_PRIORITY_CLASS if gaming_active else psutil.NORMAL_PRIORITY_CLASS
        my_pid = os.getpid()
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                pid = p.info['pid']
                name = (p.info.get('name') or '').lower()
                if pid == my_pid or name == "cloudflared.exe":
                    p.nice(target_prio)
                elif name == "antigravity.exe":
                    cmd = " ".join(p.info.get('cmdline') or []).lower()
                    if "--type=renderer" in cmd or "--type=gpu-process" in cmd:
                        p.nice(target_prio)
            except Exception:
                pass
        logger.info(f"[GameMode] Adjusted background priorities -> {target_mode}")
    except Exception as e:
        logger.debug(f"Could not adjust process priorities: {e}")

def detect_running_game(force_refresh: bool = False):
    """Detects whether a fullscreen D3D game or known game executable is currently running in foreground."""
    now = time.time()
    with _game_mode_lock:
        if not force_refresh and (now - _game_detect_cache["ts"] < 3.0):
            return _game_detect_cache["is_game"], _game_detect_cache["game_name"]

    is_game = False
    game_name = None
    try:
        import psutil
        # 1. Check Windows Shell Fullscreen / Presentation notification state
        q_state = ctypes.c_int(0)
        try:
            shell32.SHQueryUserNotificationState(ctypes.byref(q_state))
        except Exception:
            pass
        is_d3d_fullscreen = (q_state.value == 3)  # QUNS_RUNNING_D3D_FULL_SCREEN

        # 2. Check active foreground window process on Default desktop
        fg_exe = None
        with _interactive_desktop():
            hwnd = user32.GetForegroundWindow()
            if hwnd:
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value > 0:
                    try:
                        fg_exe = psutil.Process(pid.value).name()
                    except Exception:
                        fg_exe = None

        fg_low = (fg_exe or "").lower()
        if fg_low in KNOWN_GAME_EXES:
            is_game = True
            game_name = fg_exe
        elif is_d3d_fullscreen and fg_low and fg_low not in NON_GAME_FULLSCREEN_EXES:
            is_game = True
            game_name = fg_exe
        elif is_d3d_fullscreen:
            # Fallback: verify if any known game exe is active
            for p in psutil.process_iter(['name']):
                pname = (p.info.get('name') or '')
                if pname.lower() in KNOWN_GAME_EXES:
                    is_game = True
                    game_name = pname
                    break
    except Exception as e:
        logger.debug(f"detect_running_game error: {e}")

    with _game_mode_lock:
        _game_detect_cache.update({"ts": now, "is_game": is_game, "game_name": game_name})
    return is_game, game_name

def get_game_mode_status(force_refresh: bool = False):
    """Returns full Game Mode state and synchronizes background priority throttling."""
    mode = _game_mode_setting
    detected, game_name = detect_running_game(force_refresh=force_refresh) if mode != "off" else (False, None)
    active = (mode == "on") or (mode == "auto" and detected)
    _apply_gaming_process_priorities(active)
    return {
        "mode": mode,
        "active": active,
        "detected_game": game_name
    }

def is_game_mode_active() -> bool:
    """Fast check returning True if Game Mode shield is currently active."""
    return get_game_mode_status()["active"]


@contextlib.contextmanager
def _interactive_desktop():
    """Temporarily binds current thread to interactive desktop and guarantees handle cleanup."""
    prev_desk = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    hdesk = user32.OpenDesktopW('default', 0, False, 0x01FF)
    try:
        if hdesk:
            user32.SetThreadDesktop(hdesk)
        yield hdesk
    finally:
        if prev_desk:
            try:
                user32.SetThreadDesktop(prev_desk)
            except Exception:
                pass
        if hdesk:
            try:
                user32.CloseDesktop(hdesk)
            except Exception:
                pass

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

_keep_awake_state = True

def set_keep_awake(enable=True):
    """
    Enables Away Mode: PC allows monitor to sleep after timeout,
    but keeps CPU, network, and remote server running 24/7 without deep S3 sleep.
    """
    global _keep_awake_state
    _keep_awake_state = bool(enable)
    if enable:
        kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED)
    else:
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    return _keep_awake_state

def get_keep_awake():
    return _keep_awake_state

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

def trigger_hotkey(name, force=False):
    """Executes a defined keyboard shortcut on the interactive desktop (respecting Game Mode)."""
    name = (name or "").lower().strip()
    if not force and is_game_mode_active() and name in _DISRUPTIVE_GAME_HOTKEYS:
        logger.info(f"[GameMode] Blocked disruptive hotkey '{name}'")
        return False
    keys = VK_MAPPING.get(name)
    if not keys:
        return False
    with _interactive_desktop():
        for k in keys:
            user32.keybd_event(k, 0, 0, 0)
            time.sleep(0.02)
        time.sleep(0.04)
        for k in reversed(keys):
            user32.keybd_event(k, 0, 2, 0)
            time.sleep(0.02)
    return True

def close_active_window(force=False):
    """Closes the current foreground window via WM_CLOSE or Alt+F4 (blocked during Game Mode unless forced)."""
    if not force and is_game_mode_active():
        logger.info("[GameMode] Blocked close_active_window to protect fullscreen game")
        return False
    with _interactive_desktop():
        hwnd = user32.GetForegroundWindow()
        if hwnd:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
            return True
    return trigger_hotkey("alt_f4", force=force)

def get_active_windows():
    """Returns a list of visible top-level windows on the interactive desktop without spawning tasklist."""
    proc_map = {}
    try:
        import psutil
        for p in psutil.process_iter(['pid', 'name']):
            try:
                proc_map[p.info['pid']] = p.info['name'] or ""
            except Exception:
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

    with _interactive_desktop() as hdesk:
        if hdesk:
            user32.EnumDesktopWindows(hdesk, WNDENUMPROC(enum_cb), 0)
        else:
            user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
    return windows

def focus_window(hwnd, force=False):
    """Brings the specified window to the foreground (blocked in Game Mode unless forced)."""
    if not force and is_game_mode_active():
        logger.info(f"[GameMode] Blocked focus_window({hwnd}) to avoid Alt-Tabbing game")
        return False
    try:
        hwnd = int(hwnd)
        with _interactive_desktop():
            user32.ShowWindow(hwnd, 9)
            return bool(user32.SetForegroundWindow(hwnd))
    except Exception:
        return False

def close_window(hwnd, force=False):
    """Closes or terminates a specific window."""
    try:
        hwnd = int(hwnd)
        with _interactive_desktop():
            if force:
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value > 0:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid.value)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=4,
                        creationflags=_NO_WINDOW
                    )
                    return True
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
            return True
    except Exception:
        return False

def volume_up(steps=1):
    count = max(1, min(50, int(steps)))
    for _ in range(count):
        send_vk(VK_VOLUME_UP)
        time.sleep(0.01)

def volume_down(steps=1):
    count = max(1, min(50, int(steps)))
    for _ in range(count):
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
    """Wakes the monitor and graphics display stack from sleep (skipped when actively gaming)."""
    if is_game_mode_active():
        return True
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
    with _cpu_lock:
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
    """Terminates process by app key or strictly validated executable name."""
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
        subprocess.run(
            ["taskkill", "/F", "/IM", "SteamGhost.exe", "/IM", "SteamGhost.App.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            creationflags=_NO_WINDOW
        )
        return {"status": "ok", "message": "SteamGhost terminated"}

    target = mapping.get(app_key)
    if not target:
        candidate = f"{app_key}.exe" if not app_key.endswith(".exe") else app_key
        if not _SAFE_EXE_RE.match(candidate):
            return {"status": "error", "message": "Invalid application name"}
        target = candidate
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            creationflags=_NO_WINDOW
        )
        return {"status": "ok", "message": f"{target} terminated"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def run_interactive(cmd_str):
    """Executes a command directly on the active user's interactive desktop (WinSta0\\default)."""
    task_name = f"PCRemoteRun_{int(time.time() * 1000) % 100000}"
    try:
        subprocess.run(
            ['schtasks', '/create', '/tn', task_name, '/tr', f'cmd.exe /c {cmd_str}', '/sc', 'once', '/st', '23:59', '/f', '/it'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, creationflags=_NO_WINDOW
        )
        subprocess.run(
            ['schtasks', '/run', '/tn', task_name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, creationflags=_NO_WINDOW
        )
        time.sleep(0.4)
        subprocess.run(
            ['schtasks', '/delete', '/tn', task_name, '/f'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3, creationflags=_NO_WINDOW
        )
        return True
    except Exception:
        try:
            subprocess.Popen(cmd_str, shell=True, creationflags=_NO_WINDOW)
            return True
        except Exception:
            return False

def open_url(target, force=False):
    """Opens a validated HTTP(S) URL or Google search query in Google Chrome without shell interpolation."""
    target = (target or "").strip()
    if not target:
        return {"status": "error", "message": "Empty query or URL"}
    if not target.startswith("http://") and not target.startswith("https://"):
        target = f"https://www.google.com/search?q={urllib.parse.quote_plus(target)}"

    # Strictly validate URL scheme, netloc, and reject control/quote/shell characters
    if any(ch in target for ch in ('"', "'", "\r", "\n", "\x00", "`")):
        return {"status": "error", "message": "Invalid characters in URL"}
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"status": "error", "message": "Invalid URL"}

    if not force and is_game_mode_active():
        return {
            "status": "blocked",
            "blocked_by_game_mode": True,
            "message": "🎮 Игровой режим: открытие браузера на ПК заблокировано чтобы не свернуть игру"
        }

    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    try:
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        if os.path.exists(chrome_path):
            subprocess.Popen([chrome_path, "--profile-directory=Default", target], creationflags=flags, close_fds=True)
        else:
            os.startfile(target)
    except Exception as e:
        return {"status": "error", "message": str(e)}

    # Bring Chrome window to foreground
    try:
        time.sleep(1)
        with _interactive_desktop() as hdesk:
            if hdesk:
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
    subprocess.run(["shutdown", "/r", "/t", "5"], timeout=4, creationflags=_NO_WINDOW)
    return {"status": "ok", "message": "System reboot initiated (5s delay)"}

def shutdown_pc():
    """Initiates a safe PC shutdown with 5 seconds warning."""
    subprocess.run(["shutdown", "/s", "/t", "5"], timeout=4, creationflags=_NO_WINDOW)
    return {"status": "ok", "message": "System shutdown initiated (5s delay)"}

def cancel_restart_pc():
    """Aborts a pending system reboot or shutdown."""
    subprocess.run(["shutdown", "/a"], timeout=4, creationflags=_NO_WINDOW)
    return {"status": "ok", "message": "Power action aborted"}

# --- VIRTUAL MOUSE & TOUCHPAD ---
def mouse_move_relative(dx: int, dy: int):
    """Moves mouse cursor relatively by clamped dx, dy."""
    try:
        cdx = max(-5000, min(5000, int(dx)))
        cdy = max(-5000, min(5000, int(dy)))
        user32.mouse_event(0x0001, cdx, cdy, 0, 0)
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
    """Scrolls mouse wheel by clamped delta (e.g. +120 or -120)."""
    try:
        cdelta = max(-2400, min(2400, int(delta)))
        user32.mouse_event(0x0800, 0, 0, cdelta, 0)
        return True
    except Exception as e:
        logger.error(f"Error scrolling mouse: {e}")
        return False

# --- VIRTUAL UNICODE KEYBOARD TYPING ---
def type_unicode_text(text: str):
    """Types arbitrary Unicode text (including surrogate pairs) directly into the foreground window."""
    try:
        text = str(text or "")[:4000]
        if not text:
            return True

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

        utf16_bytes = text.encode("utf-16-le")
        for idx in range(0, len(utf16_bytes), 2):
            code = int.from_bytes(utf16_bytes[idx:idx + 2], "little")
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
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True, timeout=3.0, creationflags=_NO_WINDOW)
        return {"status": "ok", "brightness": val}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def get_display_brightness():
    """Gets laptop monitor current brightness."""
    try:
        cmd = "(Get-WmiObject -Namespace root/wmi -Class WmiMonitorBrightness).CurrentBrightness"
        res = subprocess.check_output(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], timeout=3.0, text=True, creationflags=_NO_WINDOW)
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

def speak_text(text: str, force: bool = False):
    """Speaks text asynchronously using Windows SAPI with proper COM initialization (muted in Game Mode)."""
    if not force and is_game_mode_active():
        return {
            "status": "blocked",
            "blocked_by_game_mode": True,
            "message": "🎮 Игровой режим: озвучка приглушена чтобы не мешать звуку игры"
        }
    com_inited = False
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        com_inited = True
        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        clean_text = str(text or "").strip()[:500]
        if not clean_text:
            return {"status": "error", "message": "Text is empty"}
        # SVSFlagsAsync = 1
        speaker.Speak(clean_text, 1)
        return {"status": "ok", "text": clean_text}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        if com_inited:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass

def get_disk_stats():
    """Returns disk partitions, usage, total, used, free GB and percentage."""
    import psutil
    disks = []
    for p in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(p.mountpoint)
            disks.append({
                "device": p.device,
                "mountpoint": p.mountpoint,
                "total_gb": round(usage.total / (1024**3), 1),
                "used_gb": round(usage.used / (1024**3), 1),
                "free_gb": round(usage.free / (1024**3), 1),
                "percent": usage.percent
            })
        except Exception:
            pass
    return disks

def get_network_ping(host: str = "8.8.8.8"):
    """Measures network ping and online latency to a strictly validated host."""
    clean_host = (host or "8.8.8.8").strip()
    if not _SAFE_HOST_RE.match(clean_host):
        return {"status": "error", "latency_ms": None, "online": False, "message": "Invalid host", "host": clean_host}
    t0 = time.time()
    try:
        res = subprocess.run(
            ["ping", "-n", "1", "-w", "1000", clean_host],
            capture_output=True, text=True, timeout=2.5,
            creationflags=_NO_WINDOW
        )
        elapsed = round((time.time() - t0) * 1000, 1)
        m = re.search(r"время[=<](\d+)мс|time[=<](\d+)ms", res.stdout, re.IGNORECASE)
        ms = int(m.group(1) or m.group(2)) if m else int(elapsed)
        return {"status": "ok", "latency_ms": ms, "online": (res.returncode == 0), "host": clean_host}
    except Exception as e:
        return {"status": "error", "latency_ms": None, "online": False, "message": str(e), "host": clean_host}

