"""
PC Remote Host Server - High Performance & Ultra-Lightweight.
Provides LAN endpoints for remote PC controls, hardware telemetry,
bidirectional clipboard synchronization, and direct Antigravity prompt injection.
"""

import asyncio
import base64
import html
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from aiohttp import web

# Add local directory to path
SERVER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SERVER_DIR))

# Ensure UTF-8 output on Windows terminal
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import win32_controls
import antigravity_bridge
import tunnel_manager
import battery_guard
import account_manager
import network_guard

log_file = SERVER_DIR / "server.log"
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s [%(name)s]: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
    ]
)
logger = logging.getLogger("PCRemoteServer")

# Paths
ROOT_DIR = SERVER_DIR.parent
MOBILE_APP_DIR = ROOT_DIR / "mobile_app"
DESKTOP_DIR = Path(r"C:\Users\dev\Desktop")
APK_PATH = DESKTOP_DIR / "PCRemote.apk"

_BRIGHTNESS_CACHE = {"value": None, "ts": 0.0}
_LAN_ORIGIN_RE = re.compile(
    r"^https?://(localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?::\d{1,5})?$",
    re.IGNORECASE
)
_TRYCF_ORIGIN_RE = re.compile(r"^https://[a-z0-9-]+\.trycloudflare\.com$", re.IGNORECASE)
_FORBIDDEN_FS_NAMES = {".credentials.json", ".auth_token", ".env", "debug.keystore", ".tunnel_beacon.json"}
_FORBIDDEN_FS_SUFFIXES = {".session", ".session-journal", ".pem", ".key"}
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _is_allowed_origin(origin: str) -> bool:
    if not origin:
        return False
    if origin.lower() == "https://pcremote.local":
        return True
    return bool(_LAN_ORIGIN_RE.match(origin) or _TRYCF_ORIGIN_RE.match(origin))


def _apply_security_headers(request, response):
    origin = request.headers.get("Origin", "").strip()
    if _is_allowed_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@web.middleware
async def security_headers_middleware(request, handler):
    if request.method == "OPTIONS":
        resp = web.Response(status=204)
        return _apply_security_headers(request, resp)
    resp = await handler(request)
    return _apply_security_headers(request, resp)


def _parse_int(val, default=None, min_val=None, max_val=None):
    try:
        num = int(val)
    except (TypeError, ValueError):
        return default, False
    if min_val is not None:
        num = max(min_val, num)
    if max_val is not None:
        num = min(max_val, num)
    return num, True


def get_lan_ip():
    """Detects primary local LAN IP."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def json_response(data, status=200):
    return web.Response(
        text=json.dumps(data, ensure_ascii=False),
        status=status,
        content_type="application/json",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate"
        }
    )


async def handle_options(request):
    return web.Response(status=204)

# --- API HANDLERS ---

async def handle_ping(request):
    return json_response({
        "status": "ok",
        "service": "PCRemoteServer",
        "version": "2.0.0",
        "host": socket.gethostname(),
        "cloud_url": tunnel_manager.get_current_tunnel_url()
    })

def _collect_pc_status_sync():
    mem = win32_controls.get_memory_stats()
    cpu = win32_controls.get_cpu_stats()
    win_title = win32_controls.get_foreground_window_title()

    battery_info = None
    steamghost_running = False
    try:
        import psutil
        b = psutil.sensors_battery()
        if b is not None:
            battery_info = {
                "percent": int(b.percent),
                "power_plugged": bool(b.power_plugged),
                "secs_left": int(b.secsleft) if b.secsleft and b.secsleft > 0 else None
            }
        for p in psutil.process_iter(["name"]):
            pname = (p.info.get("name") or "").lower()
            if "steamghost" in pname:
                steamghost_running = True
                break
    except Exception:
        pass

    game_mode = win32_controls.get_game_mode_status()
    brightness_ttl = 120.0 if game_mode["active"] else 45.0
    now = time.time()
    if (not game_mode["active"] and (now - _BRIGHTNESS_CACHE["ts"] > brightness_ttl or _BRIGHTNESS_CACHE["value"] is None)) or _BRIGHTNESS_CACHE["value"] is None:
        _BRIGHTNESS_CACHE["value"] = win32_controls.get_display_brightness()
        _BRIGHTNESS_CACHE["ts"] = now

    return {
        "status": "ok",
        "cpu_pct": cpu,
        "memory": mem,
        "battery": battery_info,
        "disks": win32_controls.get_disk_stats(),
        "keep_awake": win32_controls.get_keep_awake(),
        "game_mode": game_mode,
        "uptime": win32_controls.get_system_uptime(),
        "brightness": _BRIGHTNESS_CACHE["value"],
        "active_window": win_title,
        "steamghost_running": steamghost_running,
        "lan_ip": get_lan_ip(),
        "mac_address": win32_controls.get_mac_address(),
        "cloud_url": tunnel_manager.get_current_tunnel_url(),
        "network": network_guard.get_quick_status()
    }


async def handle_pc_status(request):
    data = await asyncio.to_thread(_collect_pc_status_sync)
    return json_response(data)

def _game_mode_block_resp(msg: str):
    return json_response({
        "status": "blocked",
        "blocked_by_game_mode": True,
        "message": msg
    })

async def handle_pc_action(request):
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    action = (body.get("action") or "").lower().strip()
    force = bool(body.get("force", False))
    logger.info(f"PC Action requested: {action}")

    if action == "volume_up":
        steps, ok = _parse_int(body.get("steps", 2), default=2, min_val=1, max_val=50)
        if not ok:
            return json_response({"status": "error", "message": "Invalid steps"}, status=400)
        await asyncio.to_thread(win32_controls.volume_up, steps)
        return json_response({"status": "ok", "action": action})

    elif action == "volume_down":
        steps, ok = _parse_int(body.get("steps", 2), default=2, min_val=1, max_val=50)
        if not ok:
            return json_response({"status": "error", "message": "Invalid steps"}, status=400)
        await asyncio.to_thread(win32_controls.volume_down, steps)
        return json_response({"status": "ok", "action": action})

    elif action == "volume_mute":
        await asyncio.to_thread(win32_controls.volume_mute)
        return json_response({"status": "ok", "action": action})

    elif action == "play_pause":
        await asyncio.to_thread(win32_controls.media_play_pause)
        return json_response({"status": "ok", "action": action})

    elif action == "media_next":
        await asyncio.to_thread(win32_controls.media_next)
        return json_response({"status": "ok", "action": action})

    elif action == "media_prev":
        await asyncio.to_thread(win32_controls.media_prev)
        return json_response({"status": "ok", "action": action})

    elif action == "media_stop":
        await asyncio.to_thread(win32_controls.media_stop)
        return json_response({"status": "ok", "action": action})

    elif action == "lock":
        success = await asyncio.to_thread(win32_controls.lock_workstation)
        return json_response({"status": "ok" if success else "error", "action": action})

    elif action in ("wake", "wake_screen"):
        await asyncio.to_thread(win32_controls.wake_screen)
        return json_response({"status": "ok", "action": action})

    elif action == "screen_off":
        await asyncio.to_thread(win32_controls.screen_off)
        return json_response({"status": "ok", "action": action})

    elif action == "sleep":
        success = await asyncio.to_thread(win32_controls.sleep_pc)
        return json_response({"status": "ok" if success else "error", "action": action})

    elif action == "launch":
        if not force and await asyncio.to_thread(win32_controls.is_game_mode_active):
            return _game_mode_block_resp("🎮 Игровой режим: запуск приложения заблокирован, чтобы не свернуть игру")
        app_name = body.get("app", "")
        res = await asyncio.to_thread(win32_controls.launch_application, app_name)
        return json_response(res)

    elif action in ("kill", "close", "stop"):
        app_name = body.get("app", "")
        res = await asyncio.to_thread(win32_controls.kill_application, app_name)
        return json_response(res)

    elif action in ("open_url", "search", "google"):
        target = body.get("url") or body.get("query") or body.get("text", "")
        res = await asyncio.to_thread(win32_controls.open_url, target, force=force)
        return json_response(res)

    elif action in ("restart", "reboot"):
        res = await asyncio.to_thread(win32_controls.restart_pc)
        return json_response(res)

    elif action in ("shutdown", "poweroff", "turn_off"):
        res = await asyncio.to_thread(win32_controls.shutdown_pc)
        return json_response(res)

    elif action in ("cancel_restart", "cancel_reboot", "abort_restart", "cancel_shutdown", "cancel_power"):
        res = await asyncio.to_thread(win32_controls.cancel_restart_pc)
        return json_response(res)

    elif action in ("hotkey", "key"):
        key_name = body.get("key") or body.get("name", "")
        success = await asyncio.to_thread(win32_controls.trigger_hotkey, key_name, force=force)
        if not success and not force and await asyncio.to_thread(win32_controls.is_game_mode_active):
            return _game_mode_block_resp(f"🎮 Игровой режим: горячая клавиша '{key_name}' заблокирована, чтобы не свернуть игру")
        return json_response({"status": "ok" if success else "error", "key": key_name})

    elif action == "close_active":
        success = await asyncio.to_thread(win32_controls.close_active_window, force=force)
        if not success and not force and await asyncio.to_thread(win32_controls.is_game_mode_active):
            return _game_mode_block_resp("🎮 Игровой режим: закрытие активного окна заблокировано, чтобы не закрыть игру")
        return json_response({"status": "ok" if success else "error", "action": action})

    elif action == "focus_window":
        hwnd, ok = _parse_int(body.get("hwnd"))
        if not ok:
            return json_response({"status": "error", "message": "Invalid hwnd"}, status=400)
        success = await asyncio.to_thread(win32_controls.focus_window, hwnd, force=force)
        if not success and not force and await asyncio.to_thread(win32_controls.is_game_mode_active):
            return _game_mode_block_resp("🎮 Игровой режим: переключение окна заблокировано, чтобы не свернуть игру")
        return json_response({"status": "ok" if success else "error", "hwnd": hwnd})

    elif action == "close_window":
        hwnd, ok = _parse_int(body.get("hwnd"))
        if not ok:
            return json_response({"status": "error", "message": "Invalid hwnd"}, status=400)
        success = await asyncio.to_thread(win32_controls.close_window, hwnd, force=force)
        return json_response({"status": "ok" if success else "error", "hwnd": hwnd})

    return json_response({"status": "error", "message": f"Unknown action: {action}"}, status=400)

def _run_ps_cmd_sync(cmd: str) -> tuple[int, str, str]:
    no_win = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    res = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True,
        timeout=15.0,
        creationflags=no_win
    )
    stdout_txt = (res.stdout or b"").decode("utf-8", errors="replace")
    stderr_txt = (res.stderr or b"").decode("utf-8", errors="replace")
    return res.returncode, stdout_txt, stderr_txt


async def handle_pc_cmd(request):
    """Executes a PowerShell command asynchronously without spawning a visible console window."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    cmd = (body.get("command") or "").strip()
    if not cmd:
        return json_response({"status": "error", "message": "Command is empty"}, status=400)

    try:
        rc, stdout_txt, stderr_txt = await asyncio.to_thread(_run_ps_cmd_sync, cmd)
        output = stdout_txt
        if stderr_txt:
            output += ("\n[STDERR]\n" + stderr_txt if output else stderr_txt)
        return json_response({
            "status": "ok",
            "exit_code": rc,
            "output": output or "(Команда выполнена успешно, вывод пуст)"
        })
    except subprocess.TimeoutExpired:
        return json_response({"status": "error", "message": "Таймаут выполнения команды (15 сек)"}, status=504)
    except Exception as e:
        return json_response({"status": "error", "message": str(e)}, status=500)

async def handle_pc_windows(request):
    windows = await asyncio.to_thread(win32_controls.get_active_windows)
    return json_response({"status": "ok", "windows": windows, "count": len(windows)})

async def handle_pc_mouse(request):
    """Handles virtual mouse movements, clicks, and scroll (blocked during Game Mode unless forced)."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    force = bool(body.get("force", False))
    act = (body.get("action") or "move").lower().strip()
    dx = dy = delta = 0
    btn = "left"

    if act == "move":
        dx, ok_x = _parse_int(body.get("dx", 0), 0, -5000, 5000)
        dy, ok_y = _parse_int(body.get("dy", 0), 0, -5000, 5000)
        if not ok_x or not ok_y:
            return json_response({"status": "error", "message": "Invalid dx/dy"}, status=400)
    elif act == "click":
        btn = body.get("button", "left")
    elif act == "scroll":
        delta, ok_d = _parse_int(body.get("delta", 0), 0, -2400, 2400)
        if not ok_d:
            return json_response({"status": "error", "message": "Invalid delta"}, status=400)
    else:
        return json_response({"status": "error", "message": "Invalid mouse action"}, status=400)

    if not force and await asyncio.to_thread(win32_controls.is_game_mode_active):
        return _game_mode_block_resp("🎮 Игровой режим: тачпад заблокирован, чтобы не сбить прицел в игре")

    if act == "move":
        await asyncio.to_thread(win32_controls.mouse_move_relative, dx, dy)
    elif act == "click":
        await asyncio.to_thread(win32_controls.mouse_click, btn)
    elif act == "scroll":
        await asyncio.to_thread(win32_controls.mouse_scroll, delta)
    return json_response({"status": "ok", "action": act})

async def handle_pc_type(request):
    """Types arbitrary text directly into the foreground window (blocked during Game Mode unless forced)."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    force = bool(body.get("force", False))
    if not force and await asyncio.to_thread(win32_controls.is_game_mode_active):
        return _game_mode_block_resp("🎮 Игровой режим: удалённый ввод заблокирован, чтобы не мешать игре")

    text = str(body.get("text") or "")[:4000]
    if text:
        await asyncio.to_thread(win32_controls.type_unicode_text, text)
    return json_response({"status": "ok", "length": len(text)})

async def handle_get_brightness(request):
    """Gets current monitor brightness."""
    b = await asyncio.to_thread(win32_controls.get_display_brightness)
    return json_response({"status": "ok", "brightness": b})

async def handle_set_brightness(request):
    """Sets monitor brightness."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)
    level, ok = _parse_int(body.get("level", 50), 50, 0, 100)
    if not ok:
        return json_response({"status": "error", "message": "Invalid brightness level"}, status=400)
    res = await asyncio.to_thread(win32_controls.set_display_brightness, level)
    _BRIGHTNESS_CACHE["value"] = res.get("brightness", level)
    _BRIGHTNESS_CACHE["ts"] = time.time()
    return json_response(res)

async def handle_pc_processes(request):
    """Lists top memory/CPU processes."""
    limit, _ = _parse_int(request.query.get("limit", 15), 15, 1, 100)
    procs = await asyncio.to_thread(win32_controls.get_top_processes, limit)
    return json_response({"status": "ok", "processes": procs})

async def handle_pc_kill_process(request):
    """Kills a process by PID."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)
    pid, ok = _parse_int(body.get("pid"), None, 1, 2**31 - 1)
    if not ok or not pid:
        return json_response({"status": "error", "message": "Valid PID is required"}, status=400)
    res = await asyncio.to_thread(win32_controls.kill_process_by_pid, pid)
    return json_response(res)

ALLOWED_FS_ROOTS = {
    "Desktop": Path.home() / "Desktop",
    "Downloads": Path.home() / "Downloads",
    "Documents": Path.home() / "Documents",
    "Scratch Projects": Path.home() / ".gemini" / "antigravity" / "scratch",
    "PC Remote App": Path.home() / ".gemini" / "antigravity" / "scratch" / "pc_remote"
}


def _is_forbidden_fs_path(resolved: Path) -> bool:
    name_low = resolved.name.lower()
    if name_low in _FORBIDDEN_FS_NAMES or resolved.suffix.lower() in _FORBIDDEN_FS_SUFFIXES:
        return True
    parts_low = {p.lower() for p in resolved.parts}
    if ".ssh" in parts_low:
        return True
    accounts_dir = (Path.home() / ".gemini" / "antigravity" / "accounts").resolve()
    if resolved == accounts_dir or resolved.is_relative_to(accounts_dir):
        return True
    return False


def _resolve_safe_fs_path(req_path: str):
    """Resolves a requested path and verifies it falls strictly inside ALLOWED_FS_ROOTS and is not sensitive."""
    raw = (req_path or "").strip()
    if not raw:
        return None, "Path is required", 400
    try:
        resolved = Path(raw).resolve()
    except Exception:
        return None, "Invalid path", 400

    allowed = False
    for root in ALLOWED_FS_ROOTS.values():
        try:
            if resolved == root.resolve() or resolved.is_relative_to(root.resolve()):
                allowed = True
                break
        except Exception:
            continue

    if not allowed or _is_forbidden_fs_path(resolved):
        return None, "Access denied: path outside allowed roots", 403
    return resolved, None, 200


def _sanitize_upload_filename(filename: str) -> str:
    """Strips directory components and rejects unsafe Windows filenames / NTFS streams."""
    raw_name = os.path.basename((filename or "").replace("\\", "/")).strip().strip(".")
    if not raw_name or ":" in raw_name or any(ord(c) < 32 for c in raw_name):
        return ""
    raw_name = re.sub(r'[<>:"/\\|?*]', "_", raw_name)
    stem = Path(raw_name).stem.upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        raw_name = f"file_{raw_name}"
    if raw_name.lower() in _FORBIDDEN_FS_NAMES or Path(raw_name).suffix.lower() in _FORBIDDEN_FS_SUFFIXES:
        return ""
    return raw_name[:180]


def _safe_content_disposition(filename: str) -> str:
    clean = re.sub(r'[\r\n";\\]', "_", filename or "download.bin")
    return f'attachment; filename="{clean}"'


async def handle_fs_list(request):
    """Lists directory contents or predefined root folders."""
    req_path = request.query.get("path", "").strip()
    if not req_path:
        roots = []
        for name, p in ALLOWED_FS_ROOTS.items():
            if p.exists():
                roots.append({
                    "name": name,
                    "path": str(p),
                    "is_dir": True,
                    "size": 0,
                    "mtime": int(p.stat().st_mtime)
                })
        return json_response({"status": "ok", "current": "", "is_root": True, "items": roots})

    target_path, err, code = _resolve_safe_fs_path(req_path)
    if err:
        return json_response({"status": "error", "message": err}, status=code)
    if not target_path.exists():
        return json_response({"status": "error", "message": "Path not found"}, status=404)
    if not target_path.is_dir():
        return json_response({"status": "error", "message": "Path is not a directory"}, status=400)

    items = []
    try:
        for entry in target_path.iterdir():
            try:
                resolved_entry = entry.resolve()
                if _is_forbidden_fs_path(resolved_entry):
                    continue
                is_dir = entry.is_dir()
                stat = entry.stat()
                items.append({
                    "name": entry.name,
                    "path": str(entry),
                    "is_dir": is_dir,
                    "size": stat.st_size if not is_dir else 0,
                    "mtime": int(stat.st_mtime)
                })
            except Exception:
                continue
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        parent = ""
        if target_path.parent != target_path:
            p_res, p_err, _ = _resolve_safe_fs_path(str(target_path.parent))
            if not p_err and p_res:
                parent = str(p_res)
        return json_response({
            "status": "ok",
            "current": str(target_path),
            "parent": parent,
            "is_root": False,
            "items": items[:200]
        })
    except Exception as e:
        return json_response({"status": "error", "message": str(e)}, status=500)

async def handle_fs_download(request):
    """Downloads a validated file within ALLOWED_FS_ROOTS from the host PC."""
    req_path = request.query.get("path", "").strip()
    target_path, err, code = _resolve_safe_fs_path(req_path)
    if err:
        return web.Response(text=err, status=code)
    if not target_path.exists() or not target_path.is_file():
        return web.Response(text="File not found", status=404)
    return web.FileResponse(
        target_path,
        headers={
            "Content-Disposition": _safe_content_disposition(target_path.name)
        }
    )

async def handle_fs_read(request):
    """Reads preview content of a validated text or code file within ALLOWED_FS_ROOTS."""
    req_path = request.query.get("path", "").strip()
    target_path, err, code = _resolve_safe_fs_path(req_path)
    if err:
        return json_response({"status": "error", "message": err}, status=code)
    if not target_path.exists() or not target_path.is_file():
        return json_response({"status": "error", "message": "File not found"}, status=404)
    try:
        with open(target_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(50000)
        return json_response({"status": "ok", "name": target_path.name, "path": str(target_path), "content": content})
    except Exception as e:
        return json_response({"status": "error", "message": str(e)}, status=500)

async def handle_fs_upload(request):
    """Uploads a file (base64 encoded) from mobile client into an allowed host folder."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    filename = str(body.get("filename") or "").strip()
    folder = str(body.get("folder") or "").strip()
    data_b64 = str(body.get("data") or "")

    if not filename or not data_b64:
        return json_response({"status": "error", "message": "Filename and data are required"}, status=400)

    if "," in data_b64:
        data_b64 = data_b64.split(",", 1)[1]

    if len(data_b64) > 35 * 1024 * 1024:
        return json_response({"status": "error", "message": "File exceeds 25 MB limit"}, status=413)

    try:
        raw_bytes = base64.b64decode(data_b64, validate=True)
    except Exception as e:
        return json_response({"status": "error", "message": f"Base64 decode failed: {e}"}, status=400)

    if len(raw_bytes) > 25 * 1024 * 1024:
        return json_response({"status": "error", "message": "File exceeds 25 MB limit"}, status=413)

    clean_name = _sanitize_upload_filename(filename)
    if not clean_name:
        return json_response({"status": "error", "message": "Invalid or forbidden filename"}, status=400)

    if folder:
        target_dir, err, code = _resolve_safe_fs_path(folder)
        if err:
            return json_response({"status": "error", "message": err}, status=code)
        if not target_dir.exists() or not target_dir.is_dir():
            return json_response({"status": "error", "message": "Target directory not found"}, status=404)
    else:
        target_dir = (Path.home() / "Downloads").resolve()

    target_file = (target_dir / clean_name).resolve()
    if not target_file.is_relative_to(target_dir) or _is_forbidden_fs_path(target_file):
        return json_response({"status": "error", "message": "Invalid target file path"}, status=403)

    try:
        with open(target_file, "wb") as f:
            f.write(raw_bytes)
        logger.info(f"File uploaded successfully: {target_file} ({len(raw_bytes)} bytes)")
        return json_response({
            "status": "ok",
            "message": f"Файл {clean_name} успешно сохранён ({round(len(raw_bytes)/1024, 1)} KB)",
            "path": str(target_file),
            "size": len(raw_bytes)
        })
    except Exception as e:
        return json_response({"status": "error", "message": str(e)}, status=500)

async def handle_pc_disks(request):
    """Returns disk space usage for all local partitions."""
    disks = await asyncio.to_thread(win32_controls.get_disk_stats)
    return json_response({"status": "ok", "disks": disks})

async def handle_pc_ping(request):
    """Measures network ping and internet connectivity."""
    host = request.query.get("host", "8.8.8.8")
    res = await asyncio.to_thread(win32_controls.get_network_ping, host)
    return json_response(res)

async def handle_pc_speak(request):
    """Speaks text through host PC speakers via SAPI."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)
    text = body.get("text", "")
    force = bool(body.get("force", False))
    res = await asyncio.to_thread(win32_controls.speak_text, text, force=force)
    return json_response(res)

async def handle_get_gamemode(request):
    """Returns current Game Mode setting, active state, and detected fullscreen game."""
    res = await asyncio.to_thread(win32_controls.get_game_mode_status, True)
    return json_response({"status": "ok", **res})

async def handle_set_gamemode(request):
    """Sets Game Mode: 'auto', 'on', or 'off'."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)
    mode = body.get("mode", "auto")
    res = await asyncio.to_thread(win32_controls.set_game_mode, mode)
    code = 200 if res.get("status") == "ok" else 400
    return json_response(res, status=code)

async def handle_get_keepawake(request):
    """Gets current keep-awake status."""
    return json_response({"status": "ok", "keep_awake": win32_controls.get_keep_awake()})

async def handle_set_keepawake(request):
    """Enables or disables keep-awake (Away Mode)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if "enable" in body:
        enable = bool(body["enable"])
    else:
        enable = not win32_controls.get_keep_awake()
    state = await asyncio.to_thread(win32_controls.set_keep_awake, enable)
    return json_response({"status": "ok", "keep_awake": state})


async def _run_delayed_network_recovery(reason: str, target_profile: str | None = None):
    await asyncio.sleep(0.35)
    await asyncio.to_thread(network_guard.reconnect_network, reason, target_profile)


async def handle_network_status(request):
    """Returns live connectivity, saved Wi-Fi profiles, and Cloudflare tunnel health."""
    snap = await asyncio.to_thread(network_guard.get_network_snapshot)
    return json_response(snap)


async def handle_network_reconnect(request):
    """Schedules non-blocking full Wi-Fi + DHCP + Cloudflare reconnect after HTTP response is sent."""
    quick = network_guard.get_quick_status()
    if quick.get("network_reconnecting"):
        return json_response({"status": "busy", "message": "Переподключение уже выполняется"})
    asyncio.create_task(_run_delayed_network_recovery("manual", None))
    return json_response({
        "status": "ok",
        "scheduled": True,
        "message": "Переподключаю Wi-Fi, DHCP и Cloudflare-туннель (~10 сек)..."
    })


async def handle_network_switch(request):
    """Switches the laptop to a validated saved Wi-Fi profile."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    profile = (body.get("profile") or "").strip()
    ok, clean_or_err = await asyncio.to_thread(network_guard.validate_wifi_profile_name, profile)
    if not ok:
        return json_response({"status": "error", "message": clean_or_err}, status=400)

    quick = network_guard.get_quick_status()
    if quick.get("network_reconnecting"):
        return json_response({"status": "busy", "message": "Переподключение уже выполняется"})

    asyncio.create_task(_run_delayed_network_recovery("manual_switch", clean_or_err))
    return json_response({
        "status": "ok",
        "scheduled": True,
        "profile": clean_or_err,
        "message": f"Переключаю Wi-Fi на «{clean_or_err}» и обновляю туннель..."
    })


async def handle_network_autoguard(request):
    """Enables or disables automatic network recovery guard."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    if "enable" in body:
        enable = bool(body["enable"])
    else:
        enable = not network_guard.get_quick_status().get("network_guard_enabled", True)
    state = network_guard.set_auto_guard(enable)
    return json_response({"status": "ok", "auto_guard": state})


async def handle_network_restart_tunnel(request):
    """Restarts Cloudflare Tunnel without touching active Wi-Fi connection."""
    async def _delayed():
        await asyncio.sleep(0.25)
        await asyncio.to_thread(tunnel_manager.restart_tunnel, "manual_restart")

    asyncio.create_task(_delayed())
    return json_response({
        "status": "ok",
        "scheduled": True,
        "message": "Перезапускаю Cloudflare-туннель (новая ссылка придёт в Telegram)..."
    })


async def handle_get_clipboard(request):
    text = await asyncio.to_thread(win32_controls.get_clipboard_text)
    return json_response({"status": "ok", "text": text})

async def handle_set_clipboard(request):
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    text = str(body.get("text") or "")[:100000]
    success = await asyncio.to_thread(win32_controls.set_clipboard_text, text)
    return json_response({"status": "ok" if success else "error", "length": len(text)})

async def handle_antigravity_status(request):
    status = await antigravity_bridge.get_antigravity_status()
    return json_response(status)

async def handle_antigravity_launch(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    cur_desk = antigravity_bridge.get_current_desktop_name()
    if cur_desk and cur_desk.lower() != "default" and not antigravity_bridge.is_antigravity_running():
        return json_response({
            "status": "error",
            "antigravity": "isolated_desktop",
            "message": f"⚠️ Сервер запущен в изолированном столе '{cur_desk}'. Перезапустите сервер через start_server.vbs на обычном рабочем столе Windows, чтобы окно Antigravity отображалось на экране."
        })
    await asyncio.to_thread(win32_controls.wake_screen)
    if body.get("restart"):
        await asyncio.to_thread(account_manager.stop_antigravity_gracefully)
    ready, status = await antigravity_bridge.ensure_antigravity_ready(timeout=30)
    conv_id = (body.get("conversation_id") or "").strip()
    if ready and conv_id and conv_id != "new" and antigravity_bridge.is_valid_conv_id(conv_id):
        await asyncio.sleep(1.0)
        await antigravity_bridge.switch_conversation(conv_id)
    return json_response({
        "status": "ok" if ready else "error",
        "antigravity": status,
        "message": "🚀 Antigravity запущен и готов к работе!" if ready else "⚠️ Не удалось дождаться запуска Antigravity CDP"
    })

async def handle_antigravity_conversations(request):
    convs = await antigravity_bridge.get_conversations_list()
    return json_response(convs)

async def handle_antigravity_switch_conversation(request):
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)
    conv_id = body.get("id", "")
    res = await antigravity_bridge.switch_conversation(conv_id)
    return json_response(res)

async def handle_antigravity_new_conversation(request):
    res = await antigravity_bridge.create_new_conversation()
    return json_response(res)

async def handle_antigravity_pin_conversation(request):
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)
    conv_id = body.get("id", "")
    pinned = body.get("pinned", None)
    res = await antigravity_bridge.toggle_conversation_pin(conv_id, pinned)
    return json_response(res)

async def handle_antigravity_chat(request):
    conv_id = request.query.get("conversation_id", None)
    limit, _ = _parse_int(request.query.get("limit", 40), 40, 1, 200)
    res = await antigravity_bridge.get_conversation_chat(conv_id=conv_id, limit=limit)
    return json_response(res)

_antigravity_send_lock = asyncio.Lock()


def _detect_image_ext(img_bytes: bytes):
    """Validates image magic bytes and returns safe extension or None."""
    if img_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if img_bytes.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(img_bytes) >= 12 and img_bytes[:4] == b"RIFF" and img_bytes[8:12] == b"WEBP":
        return "webp"
    return None


def _prune_old_uploads(uploads_dir: Path, keep_last: int = 50):
    try:
        files = sorted(
            (f for f in uploads_dir.iterdir() if f.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        for old_file in files[keep_last:]:
            try:
                old_file.unlink()
            except Exception:
                pass
    except Exception:
        pass


async def handle_antigravity_send(request):
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    prompt = str(body.get("text") or "")
    image_data = body.get("image", None)
    focus_win = bool(body.get("focus", False))
    conv_id = body.get("conversation_id", None)

    image_path = None
    if image_data:
        try:
            uploads_dir = SERVER_DIR.parent / "uploads"
            uploads_dir.mkdir(parents=True, exist_ok=True)
            b64_str = str(image_data)
            if "," in b64_str:
                _, b64_str = b64_str.split(",", 1)
            if len(b64_str) > 14 * 1024 * 1024:
                return json_response({"status": "error", "message": "Image exceeds 10 MB limit"}, status=413)
            img_bytes = base64.b64decode(b64_str, validate=True)
            if len(img_bytes) > 10 * 1024 * 1024:
                return json_response({"status": "error", "message": "Image exceeds 10 MB limit"}, status=413)
            ext = _detect_image_ext(img_bytes)
            if not ext:
                return json_response({"status": "error", "message": "Unsupported or invalid image format"}, status=400)

            filename = f"screenshot_{int(time.time() * 1000)}.{ext}"
            saved_file = uploads_dir / filename
            with open(saved_file, "wb") as f:
                f.write(img_bytes)
            image_path = str(saved_file.resolve())
            _prune_old_uploads(uploads_dir, keep_last=50)
            logger.info(f"Saved uploaded screenshot to: {image_path} ({len(img_bytes)} bytes)")
        except Exception as e:
            logger.error(f"Failed to decode uploaded image: {e}")
            return json_response({"status": "error", "message": "Invalid image payload"}, status=400)

    logger.info(f"Queueing/injecting prompt to Antigravity ({len(prompt)} chars, img: {bool(image_path)}, conv: {conv_id})...")

    # Wake screen so UI render loop is active
    await asyncio.to_thread(win32_controls.wake_screen)

    async with _antigravity_send_lock:
        res = await antigravity_bridge.send_prompt_to_antigravity(
            prompt,
            image_path=image_path,
            conv_id=conv_id,
            focus_window=focus_win
        )
        await asyncio.sleep(0.25)
        return json_response(res)


async def handle_antigravity_queue(request):
    """Returns currently queued messages from Antigravity's QueuedMessagesCard."""
    conv_id = request.query.get("conversation_id", None)
    queued = await antigravity_bridge.get_queued_messages_cdp(conv_id=conv_id)
    return json_response({"status": "ok", "queued_messages": queued})


async def _handle_queue_mutation(request, action: str):
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    idx, ok_idx = _parse_int(body.get("index"), None, 0, 500)
    if not ok_idx:
        return json_response({"status": "error", "message": "Valid queue index is required"}, status=400)

    expected_text = str(body.get("text") or body.get("old_text") or "")
    new_text = str(body.get("new_text") or "")
    conv_id = body.get("conversation_id") or None

    async with _antigravity_send_lock:
        res = await antigravity_bridge.queue_action_cdp(
            action=action,
            index=idx,
            expected_text=expected_text,
            new_text=new_text,
            conv_id=conv_id
        )
        return json_response(res, status=200 if res.get("status") == "ok" else 400)


async def handle_antigravity_queue_delete(request):
    return await _handle_queue_mutation(request, "delete")


async def handle_antigravity_queue_send_now(request):
    return await _handle_queue_mutation(request, "send_now")


async def handle_antigravity_queue_edit(request):
    return await _handle_queue_mutation(request, "edit")


async def handle_pc_screenshot(request):
    data, src = await antigravity_bridge.capture_pc_screenshot()
    if not data:
        return json_response({"status": "error", "message": "Failed to capture PC screenshot"}, status=500)
    return json_response({"status": "ok", "image": data, "source": src})

async def handle_pc_telemetry(request):
    data = await asyncio.to_thread(antigravity_bridge.get_pc_telemetry)
    return json_response(data)

async def handle_antigravity_stop(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    conv_id = body.get("conversation_id")
    res = await antigravity_bridge.stop_agent_execution(conv_id=conv_id)
    return json_response(res)

async def handle_antigravity_accounts(request):
    """Lists saved and active Antigravity accounts."""
    res = await asyncio.to_thread(account_manager.list_saved_accounts)
    return json_response(res)

PENDING_SWITCH_FILE = SERVER_DIR / "pending_switch_resume.json"
_switch_status = {
    "in_progress": False,
    "step": "idle",
    "email": "",
    "conv_id": "",
    "auto_continue": False,
    "message": "",
    "updated_at": 0
}

def _update_switch_status(in_progress, step, email="", conv_id="", auto_continue=False, message=""):
    _switch_status.update({
        "in_progress": in_progress,
        "step": step,
        "email": email,
        "conv_id": conv_id,
        "auto_continue": auto_continue,
        "message": message,
        "updated_at": int(time.time())
    })

async def _restart_and_resume_task(email: str, conv_id: str, auto_continue: bool, do_restart: bool = True):
    """
    Background workflow after switching Google account:
    1. Gracefully restarts Antigravity IDE (or launches it if needed)
    2. Waits until CDP + React chat UI is fully mounted
    3. Restores the exact conversation tab (`conv_id`)
    4. Sends seamless continuation prompt if `auto_continue` is enabled
    """
    try:
        if do_restart:
            _update_switch_status(True, "restarting_ide", email, conv_id, auto_continue, "Перезапуск Antigravity с новым профилем...")
            logging.info(f"[SwitchResume] Gracefully stopping and relaunching Antigravity for {email}...")
            await asyncio.to_thread(account_manager.stop_antigravity_gracefully)
            antigravity_bridge.launch_antigravity(force=True)
            await asyncio.sleep(2.5)

        _update_switch_status(True, "waiting_cdp", email, conv_id, auto_continue, "Ожидание готовности IDE...")
        ready, _ = await antigravity_bridge.ensure_antigravity_ready(timeout=40)
        if not ready:
            _update_switch_status(False, "error", email, conv_id, auto_continue, "Таймаут запуска Antigravity")
            return

        # Give Electron extension host & workspace a moment to hydrate
        await asyncio.sleep(2.5)
        antigravity_bridge.clear_quota_cache()

        if conv_id and conv_id != "new":
            _update_switch_status(True, "restoring_chat", email, conv_id, auto_continue, "Восстанавливаем активный диалог...")
            logging.info(f"[SwitchResume] Restoring active conversation {conv_id}...")
            await antigravity_bridge.switch_conversation(conv_id)
            await antigravity_bridge.wait_for_chat_ui_ready(timeout=18)
            await asyncio.sleep(1.0)

            if auto_continue:
                _update_switch_status(True, "continuing_task", email, conv_id, auto_continue, "Отправляем сигнал продолжения задачи...")
                resume_prompt = (
                    f"Аккаунт Antigravity успешно переключён на {email}. "
                    "Продолжай выполнение текущей задачи автономно с того места, где мы остановились."
                )
                send_res = await antigravity_bridge.send_prompt_to_antigravity(resume_prompt, conv_id=conv_id)
                logging.info(f"[SwitchResume] Auto-continue result: {send_res}")

        if PENDING_SWITCH_FILE.exists():
            try:
                PENDING_SWITCH_FILE.unlink()
            except Exception:
                pass

        _update_switch_status(False, "done", email, conv_id, auto_continue, f"Профиль {email} активен, работа продолжается!")
    except Exception as e:
        logging.error(f"[SwitchResume] Error during resume workflow: {e}", exc_info=True)
        _update_switch_status(False, "error", email, conv_id, auto_continue, str(e))

async def handle_antigravity_switch_status(request):
    """Returns real-time status of account switch / mid-task resume workflow."""
    return json_response({"status": "ok", **_switch_status})

async def handle_antigravity_switch_account(request):
    """
    Switches active Antigravity account to requested email:
    preserves active conversation, cleanly restarts IDE with new token,
    reopens the exact chat tab, and resumes interrupted task seamlessly.
    """
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)
    email = body.get("email", "").strip()
    if not email:
        return json_response({"status": "error", "message": "Email is required"}, status=400)

    conv_id = (body.get("conversation_id") or body.get("conv_id") or "").strip()
    if not conv_id:
        conv_id = await antigravity_bridge.get_active_conversation_id() or ""
    auto_continue = bool(body.get("auto_continue", True))

    _update_switch_status(True, "switching_credentials", email, conv_id, auto_continue, f"Переключаем токен на {email}...")
    res = await asyncio.to_thread(account_manager.switch_account_profile, email, False)
    if res.get("status") != "ok":
        _update_switch_status(False, "error", email, conv_id, auto_continue, res.get("message", "Ошибка переключения"))
        return json_response(res)

    antigravity_bridge.clear_quota_cache()

    # Persist switch-resume marker on disk in case server itself restarts
    try:
        PENDING_SWITCH_FILE.write_text(json.dumps({
            "email": email,
            "conv_id": conv_id,
            "auto_continue": auto_continue,
            "timestamp": time.time()
        }), encoding="utf-8")
    except Exception:
        pass

    asyncio.create_task(_restart_and_resume_task(email, conv_id, auto_continue, do_restart=True))

    res["restarting"] = True
    res["conv_id"] = conv_id
    res["auto_continue"] = auto_continue
    res["message"] = f"Переключаем на {email}... Диалог и задача продолжат работу автоматически!"
    return json_response(res)

async def handle_antigravity_delete_account(request):
    """Deletes an account profile from vault."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)
    email = body.get("email", "").strip()
    res = await asyncio.to_thread(account_manager.delete_account_profile, email)
    return json_response(res)

async def handle_antigravity_auth_url(request):
    """Returns Google OAuth authorization URL for mobile/remote login."""
    redirect_uri = "http://127.0.0.1:8765/api/antigravity/auth/callback"
    url = account_manager.get_google_oauth_url(redirect_uri)
    return json_response({"status": "ok", "url": url, "redirect_uri": redirect_uri})

async def handle_antigravity_auth_callback(request):
    """Handles Google OAuth redirect after browser login with state verification and HTML escaping."""
    code = request.query.get("code")
    error = request.query.get("error")
    state = request.query.get("state", "")
    if error:
        return web.Response(text=f"OAuth Error: {error}", status=400)
    if not code:
        return web.Response(text="No authorization code received", status=400)
    if not account_manager.verify_and_consume_oauth_state(state):
        return web.Response(text="Invalid or expired OAuth state parameter", status=400)

    redirect_uri = "http://127.0.0.1:8765/api/antigravity/auth/callback"
    res = await asyncio.to_thread(account_manager.exchange_auth_code_for_account, code, redirect_uri)
    safe_email = html.escape(str(res.get("email", "Google Account")))

    page_html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Аккаунт подключен</title>
  <style>
    body {{ background: #09090B; color: #FAFAFA; font-family: system-ui, -apple-system, sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; text-align: center; padding: 20px; }}
    .card {{ background: #18181B; border: 1px solid #27272A; border-radius: 16px; padding: 32px 24px; max-width: 420px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }}
    h1 {{ font-size: 20px; color: #10B981; margin: 12px 0 6px; }}
    p {{ color: #A1A1AA; font-size: 14px; margin: 6px 0 16px; line-height: 1.5; }}
    .email-pill {{ background: #27272A; border: 1px solid #3F3F46; padding: 8px 16px; border-radius: 9999px; font-weight: 600; color: #F4F4F5; display: inline-block; font-size: 14px; }}
  </style>
</head>
<body>
  <div class="card">
    <div style="font-size: 48px;">🎉</div>
    <h1>Аккаунт успешно подключен!</h1>
    <p>Новый аккаунт установлен в Antigravity на ноутбуке:</p>
    <div class="email-pill">{safe_email}</div>
    <p style="margin-top: 20px; font-size: 13px; color: #71717A;">
      Лимиты и квоты обновлены. Antigravity автоматически перезапущен с новым профилем. Можете закрыть эту страницу и вернуться в приложение.
    </p>
  </div>
</body>
</html>"""
    return web.Response(text=page_html, content_type="text/html", status=200)

async def handle_antigravity_submit_code(request):
    """Manual submission of Google OAuth auth code."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)
    code = str(body.get("code") or "").strip()
    if not code:
        return json_response({"status": "error", "message": "Code is required"}, status=400)
    redirect_uri = account_manager.validate_redirect_uri(body.get("redirect_uri", ""))
    res = await asyncio.to_thread(account_manager.exchange_auth_code_for_account, code, redirect_uri)
    return json_response(res)

async def handle_antigravity_quota(request):
    """Returns current Antigravity plan (e.g. Google AI Pro) and real-time model limits."""
    force = (request.query.get("force", "").lower() in ("1", "true")) or (request.query.get("refresh", "").lower() in ("1", "true"))
    res = await antigravity_bridge.get_antigravity_quota_and_plan(force=force)
    if isinstance(res, dict):
        response_data = dict(res)
        response_data["quota"] = dict(res)
        return json_response(response_data)
    return json_response(res)

async def handle_antigravity_auth_open_on_pc(request):
    """Opens Google OAuth sign-in directly in the browser on the PC."""
    res = await asyncio.to_thread(account_manager.open_oauth_on_pc)
    return json_response(res)


def _read_log_tail(path: Path, limit: int):
    """Reads the last `limit` lines from a potentially large log file without loading all lines into memory."""
    size = path.stat().st_size
    read_bytes = min(size, max(65536, limit * 512))
    with open(path, "rb") as f:
        if size > read_bytes:
            f.seek(size - read_bytes)
        raw = f.read().decode("utf-8", errors="replace")
    lines = raw.splitlines()
    if size > read_bytes and lines:
        lines = lines[1:]
    return lines[-limit:]


async def handle_server_logs(request):
    """Returns the last lines of server.log efficiently."""
    limit, _ = _parse_int(request.query.get("limit", 40), 40, 1, 500)
    if not log_file.exists():
        return json_response({"status": "ok", "lines": ["(server.log is empty)"]})
    try:
        recent = await asyncio.to_thread(_read_log_tail, log_file, limit)
        return json_response({"status": "ok", "lines": recent})
    except Exception as e:
        return json_response({"status": "error", "message": str(e)}, status=500)

async def handle_download_apk(request):
    if not APK_PATH.exists():
        return web.Response(text="APK not found. Please build it first.", status=404)
    return web.FileResponse(
        APK_PATH,
        headers={
            "Content-Disposition": 'attachment; filename="PCRemote.apk"',
            "Content-Type": "application/vnd.android.package-archive"
        }
    )


async def handle_index(request):
    """Serves the web dashboard index.html at root '/' instead of directory listing."""
    index_file = MOBILE_APP_DIR / "index.html"
    if not index_file.exists():
        return web.Response(text="index.html not found", status=404)
    return web.FileResponse(
        index_file,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )

def create_app():
    app = web.Application(
        middlewares=[security_headers_middleware],
        client_max_size=36 * 1024 * 1024
    )

    # CORS Options Preflights
    app.router.add_route("OPTIONS", "/{tail:.*}", handle_options)

    # API Routes
    app.router.add_get("/api/ping", handle_ping)
    app.router.add_get("/api/pc/status", handle_pc_status)
    app.router.add_get("/api/pc/telemetry", handle_pc_telemetry)
    app.router.add_get("/api/pc/screenshot", handle_pc_screenshot)
    app.router.add_get("/api/pc/windows", handle_pc_windows)
    app.router.add_post("/api/pc/action", handle_pc_action)
    app.router.add_post("/api/pc/cmd", handle_pc_cmd)
    app.router.add_post("/api/pc/mouse", handle_pc_mouse)
    app.router.add_post("/api/pc/type", handle_pc_type)
    app.router.add_get("/api/pc/brightness", handle_get_brightness)
    app.router.add_post("/api/pc/brightness", handle_set_brightness)
    app.router.add_get("/api/pc/processes", handle_pc_processes)
    app.router.add_post("/api/pc/process/kill", handle_pc_kill_process)
    app.router.add_get("/api/fs/list", handle_fs_list)
    app.router.add_get("/api/fs/download", handle_fs_download)
    app.router.add_get("/api/fs/read", handle_fs_read)
    app.router.add_post("/api/fs/upload", handle_fs_upload)
    app.router.add_get("/api/pc/disks", handle_pc_disks)
    app.router.add_get("/api/pc/ping", handle_pc_ping)
    app.router.add_post("/api/pc/speak", handle_pc_speak)
    app.router.add_get("/api/pc/keepawake", handle_get_keepawake)
    app.router.add_post("/api/pc/keepawake", handle_set_keepawake)
    app.router.add_get("/api/pc/gamemode", handle_get_gamemode)
    app.router.add_post("/api/pc/gamemode", handle_set_gamemode)
    app.router.add_get("/api/pc/network/status", handle_network_status)
    app.router.add_post("/api/pc/network/reconnect", handle_network_reconnect)
    app.router.add_post("/api/pc/network/switch", handle_network_switch)
    app.router.add_post("/api/pc/network/autoguard", handle_network_autoguard)
    app.router.add_post("/api/pc/network/restart_tunnel", handle_network_restart_tunnel)
    app.router.add_get("/api/clipboard", handle_get_clipboard)
    app.router.add_post("/api/clipboard", handle_set_clipboard)
    app.router.add_get("/api/antigravity/status", handle_antigravity_status)
    app.router.add_get("/api/antigravity/conversations", handle_antigravity_conversations)
    app.router.add_post("/api/antigravity/conversation/switch", handle_antigravity_switch_conversation)
    app.router.add_post("/api/antigravity/conversation/pin", handle_antigravity_pin_conversation)
    app.router.add_post("/api/antigravity/conversation/new", handle_antigravity_new_conversation)
    app.router.add_get("/api/antigravity/chat", handle_antigravity_chat)
    app.router.add_post("/api/antigravity/send", handle_antigravity_send)
    app.router.add_get("/api/antigravity/queue", handle_antigravity_queue)
    app.router.add_post("/api/antigravity/queue/delete", handle_antigravity_queue_delete)
    app.router.add_post("/api/antigravity/queue/send_now", handle_antigravity_queue_send_now)
    app.router.add_post("/api/antigravity/queue/edit", handle_antigravity_queue_edit)
    app.router.add_post("/api/antigravity/stop", handle_antigravity_stop)
    app.router.add_post("/api/antigravity/launch", handle_antigravity_launch)
    app.router.add_get("/api/antigravity/accounts", handle_antigravity_accounts)
    app.router.add_post("/api/antigravity/account/switch", handle_antigravity_switch_account)
    app.router.add_get("/api/antigravity/account/switch_status", handle_antigravity_switch_status)
    app.router.add_post("/api/antigravity/account/delete", handle_antigravity_delete_account)
    app.router.add_get("/api/antigravity/auth/url", handle_antigravity_auth_url)
    app.router.add_get("/api/antigravity/auth/callback", handle_antigravity_auth_callback)
    app.router.add_post("/api/antigravity/account/code", handle_antigravity_submit_code)
    app.router.add_get("/api/antigravity/quota", handle_antigravity_quota)
    app.router.add_post("/api/antigravity/auth/open_on_pc", handle_antigravity_auth_open_on_pc)
    app.router.add_get("/api/server/logs", handle_server_logs)
    app.router.add_get("/download/apk", handle_download_apk)

    # Web Dashboard Static Fallback (explicit '/' handler; never expose directory listings)
    if MOBILE_APP_DIR.exists():
        app.router.add_get("/", handle_index)
        app.router.add_static("/", MOBILE_APP_DIR, show_index=False)

    app.on_startup.append(on_server_startup)
    app.on_shutdown.append(on_server_shutdown)
    return app

async def _boot_antigravity_and_resume():
    """
    Runs once when server starts (e.g. after PC reboot):
    1. Checks if a pending account switch / resume marker exists
    2. Auto-launches Antigravity IDE if it isn't running yet
    """
    await asyncio.sleep(4.0)
    try:
        if PENDING_SWITCH_FILE.exists():
            try:
                data = json.loads(PENDING_SWITCH_FILE.read_text(encoding="utf-8"))
                if time.time() - data.get("timestamp", 0) < 300:
                    logging.info(f"[BootResume] Found pending switch resume for {data.get('email')}")
                    await _restart_and_resume_task(
                        data.get("email", ""),
                        data.get("conv_id", ""),
                        bool(data.get("auto_continue", True)),
                        do_restart=False
                    )
                    return
                else:
                    PENDING_SWITCH_FILE.unlink()
            except Exception as e:
                logging.warning(f"[BootResume] Error reading marker: {e}")

        cur_desk = antigravity_bridge.get_current_desktop_name()
        if cur_desk and cur_desk.lower() != "default":
            logging.warning(f"[BootAutoLaunch] Skipping auto-launch because server is running on non-Default desktop '{cur_desk}'.")
            return

        if not antigravity_bridge.is_antigravity_running():
            logging.info("[BootAutoLaunch] Antigravity not running on boot. Auto-launching IDE...")
            win32_controls.wake_screen()
            await antigravity_bridge.ensure_antigravity_ready(timeout=35)
    except Exception as e:
        logging.error(f"[BootAutoLaunch] Error: {e}")

async def on_server_startup(app):
    asyncio.create_task(_boot_antigravity_and_resume())


async def on_server_shutdown(app):
    network_guard.stop_network_guard()
    tunnel_manager.stop_tunnel()

def run_server(port=8765):
    ip = get_lan_ip()
    app = create_app()

    print("=" * 60)
    print(">>  PC REMOTE HOST SERVER & ANTIGRAVITY BRIDGE")
    print("=" * 60)
    print(f">>  LAN Web Access:      http://{ip}:{port}/")
    print(f">>  Download Mobile APK:  http://{ip}:{port}/download/apk")
    print(f">>  Localhost Access:    http://127.0.0.1:{port}/")
    print(f">>  Antigravity CDP:     ws://127.0.0.1:60382/...")
    print("=" * 60)
    print("Server running. Press Ctrl+C to terminate.")

    # Enable Away Mode so PC doesn't deep sleep while serving
    win32_controls.set_keep_awake(True)

    # Start Cloudflare Tunnel for worldwide access
    tunnel_manager.start_tunnel(port)

    # Start Network Self-Healing Guard (auto Wi-Fi/DNS/Tunnel recovery)
    network_guard.start_network_guard()

    # Start Battery Guard for laptop power monitoring
    battery_guard.start_battery_guard()

    # Use SelectorEventLoop on Windows to avoid Python 3.14 ProactorEventLoop AcceptEx WinError 64 socket stalls
    loop = asyncio.SelectorEventLoop() if os.name == "nt" and hasattr(asyncio, "SelectorEventLoop") else None
    web.run_app(app, host="0.0.0.0", port=port, print=None, loop=loop)

if __name__ == "__main__":
    port = 8765
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    run_server(port)
