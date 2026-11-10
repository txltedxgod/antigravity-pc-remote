"""
PC Remote Host Server - High Performance & Ultra-Lightweight.
Provides LAN endpoints for remote PC controls, hardware telemetry,
bidirectional clipboard synchronization, and direct Antigravity prompt injection.
"""

import asyncio
import json
import logging
import os
import socket
import sys
import time
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

log_file = SERVER_DIR / "server.log"
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s [%(name)s]: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding='utf-8')
    ]
)
logger = logging.getLogger("PCRemoteServer")

# Paths
ROOT_DIR = SERVER_DIR.parent
MOBILE_APP_DIR = ROOT_DIR / "mobile_app"
DESKTOP_DIR = Path(r"C:\Users\dev\Desktop")
APK_PATH = DESKTOP_DIR / "PCRemote.apk"

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

# CORS Response Helper
def json_response(data, status=200):
    return web.Response(
        text=json.dumps(data, ensure_ascii=False),
        status=status,
        content_type="application/json",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Cache-Control": "no-cache, no-store, must-revalidate"
        }
    )

async def handle_options(request):
    return web.Response(
        status=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        }
    )

# --- API HANDLERS ---

async def handle_ping(request):
    return json_response({
        "status": "ok",
        "service": "PCRemoteServer",
        "version": "2.0.0",
        "host": socket.gethostname(),
        "cloud_url": tunnel_manager.get_current_tunnel_url()
    })

async def handle_pc_status(request):
    mem = win32_controls.get_memory_stats()
    cpu = win32_controls.get_cpu_stats()
    win_title = win32_controls.get_foreground_window_title()

    # Battery stats for laptop remote management
    battery_info = None
    try:
        import psutil
        b = psutil.sensors_battery()
        if b is not None:
            battery_info = {
                "percent": int(b.percent),
                "power_plugged": bool(b.power_plugged),
                "secs_left": int(b.secsleft) if b.secsleft and b.secsleft > 0 else None
            }
    except Exception:
        pass

    # Check SteamGhost status
    steamghost_running = False
    try:
        import subprocess
        out = subprocess.check_output("tasklist", shell=True).decode("utf-8", errors="ignore")
        steamghost_running = "SteamGhost" in out or "SteamGhost.Runner" in out
    except Exception:
        pass

    return json_response({
        "status": "ok",
        "cpu_pct": cpu,
        "memory": mem,
        "battery": battery_info,
        "disks": win32_controls.get_disk_stats(),
        "keep_awake": win32_controls.get_keep_awake(),
        "uptime": win32_controls.get_system_uptime(),
        "brightness": win32_controls.get_display_brightness(),
        "active_window": win_title,
        "steamghost_running": steamghost_running,
        "lan_ip": get_lan_ip(),
        "mac_address": win32_controls.get_mac_address(),
        "cloud_url": tunnel_manager.get_current_tunnel_url()
    })

async def handle_pc_action(request):
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    action = (body.get("action") or "").lower().strip()
    logger.info(f"PC Action requested: {action}")

    if action == "volume_up":
        steps = int(body.get("steps", 2))
        win32_controls.volume_up(steps)
        return json_response({"status": "ok", "action": action})

    elif action == "volume_down":
        steps = int(body.get("steps", 2))
        win32_controls.volume_down(steps)
        return json_response({"status": "ok", "action": action})

    elif action == "volume_mute":
        win32_controls.volume_mute()
        return json_response({"status": "ok", "action": action})

    elif action == "play_pause":
        win32_controls.media_play_pause()
        return json_response({"status": "ok", "action": action})

    elif action == "media_next":
        win32_controls.media_next()
        return json_response({"status": "ok", "action": action})

    elif action == "media_prev":
        win32_controls.media_prev()
        return json_response({"status": "ok", "action": action})

    elif action == "media_stop":
        win32_controls.media_stop()
        return json_response({"status": "ok", "action": action})

    elif action == "lock":
        success = win32_controls.lock_workstation()
        return json_response({"status": "ok" if success else "error", "action": action})

    elif action in ("wake", "wake_screen"):
        win32_controls.wake_screen()
        return json_response({"status": "ok", "action": action})

    elif action == "screen_off":
        win32_controls.screen_off()
        return json_response({"status": "ok", "action": action})

    elif action == "sleep":
        success = win32_controls.sleep_pc()
        return json_response({"status": "ok" if success else "error", "action": action})

    elif action == "launch":
        app_name = body.get("app", "")
        res = win32_controls.launch_application(app_name)
        return json_response(res)

    elif action in ("kill", "close", "stop"):
        app_name = body.get("app", "")
        res = win32_controls.kill_application(app_name)
        return json_response(res)

    elif action in ("open_url", "search", "google"):
        target = body.get("url") or body.get("query") or body.get("text", "")
        res = win32_controls.open_url(target)
        return json_response(res)

    elif action in ("restart", "reboot"):
        res = win32_controls.restart_pc()
        return json_response(res)

    elif action in ("shutdown", "poweroff", "turn_off"):
        res = win32_controls.shutdown_pc()
        return json_response(res)

    elif action in ("cancel_restart", "cancel_reboot", "abort_restart", "cancel_shutdown", "cancel_power"):
        res = win32_controls.cancel_restart_pc()
        return json_response(res)

    elif action in ("hotkey", "key"):
        key_name = body.get("key") or body.get("name", "")
        success = win32_controls.trigger_hotkey(key_name)
        return json_response({"status": "ok" if success else "error", "key": key_name})

    elif action == "close_active":
        success = win32_controls.close_active_window()
        return json_response({"status": "ok" if success else "error", "action": action})

    elif action == "focus_window":
        hwnd = body.get("hwnd")
        success = win32_controls.focus_window(hwnd)
        return json_response({"status": "ok" if success else "error", "hwnd": hwnd})

    elif action == "close_window":
        hwnd = body.get("hwnd")
        force = bool(body.get("force", False))
        success = win32_controls.close_window(hwnd, force=force)
        return json_response({"status": "ok" if success else "error", "hwnd": hwnd})

    return json_response({"status": "error", "message": f"Unknown action: {action}"}, status=400)

async def handle_pc_cmd(request):
    """Executes a PowerShell / shell command on the host PC and returns output."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    cmd = (body.get("command") or "").strip()
    if not cmd:
        return json_response({"status": "error", "message": "Command is empty"}, status=400)

    try:
        import subprocess
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15.0
        )
        output = proc.stdout or ""
        if proc.stderr:
            output += ("\n[STDERR]\n" + proc.stderr if output else proc.stderr)
        return json_response({
            "status": "ok",
            "exit_code": proc.returncode,
            "output": output or "(Команда выполнена успешно, вывод пуст)"
        })
    except subprocess.TimeoutExpired:
        return json_response({"status": "error", "message": "Таймаут выполнения команды (15 сек)"}, status=504)
    except Exception as e:
        return json_response({"status": "error", "message": str(e)}, status=500)

async def handle_pc_windows(request):
    windows = win32_controls.get_active_windows()
    return json_response({"status": "ok", "windows": windows, "count": len(windows)})

async def handle_pc_mouse(request):
    """Handles virtual mouse movements, clicks, and scroll."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    act = body.get("action", "move")
    if act == "move":
        dx = int(body.get("dx", 0))
        dy = int(body.get("dy", 0))
        win32_controls.mouse_move_relative(dx, dy)
    elif act == "click":
        btn = body.get("button", "left")
        win32_controls.mouse_click(btn)
    elif act == "scroll":
        delta = int(body.get("delta", 0))
        win32_controls.mouse_scroll(delta)
    return json_response({"status": "ok", "action": act})

async def handle_pc_type(request):
    """Types arbitrary text directly into the foreground window."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    text = body.get("text", "")
    if text:
        win32_controls.type_unicode_text(text)
    return json_response({"status": "ok", "length": len(text)})

async def handle_get_brightness(request):
    """Gets current monitor brightness."""
    b = win32_controls.get_display_brightness()
    return json_response({"status": "ok", "brightness": b})

async def handle_set_brightness(request):
    """Sets monitor brightness."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)
    level = int(body.get("level", 50))
    res = win32_controls.set_display_brightness(level)
    return json_response(res)

async def handle_pc_processes(request):
    """Lists top memory/CPU processes."""
    try:
        limit = int(request.query.get("limit", 15))
    except Exception:
        limit = 15
    procs = win32_controls.get_top_processes(limit=limit)
    return json_response({"status": "ok", "processes": procs})

async def handle_pc_kill_process(request):
    """Kills a process by PID."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)
    pid = body.get("pid")
    if not pid:
        return json_response({"status": "error", "message": "PID is required"}, status=400)
    res = win32_controls.kill_process_by_pid(int(pid))
    return json_response(res)

ALLOWED_FS_ROOTS = {
    "Desktop": Path.home() / "Desktop",
    "Downloads": Path.home() / "Downloads",
    "Documents": Path.home() / "Documents",
    "Scratch Projects": Path.home() / ".gemini" / "antigravity" / "scratch",
    "PC Remote App": Path.home() / ".gemini" / "antigravity" / "scratch" / "pc_remote"
}

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

    target_path = Path(req_path)
    if not target_path.exists():
        return json_response({"status": "error", "message": "Path not found"}, status=404)

    if not target_path.is_dir():
        return json_response({"status": "error", "message": "Path is not a directory"}, status=400)

    items = []
    try:
        for entry in target_path.iterdir():
            try:
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
        parent = str(target_path.parent) if target_path.parent != target_path else ""
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
    """Downloads a file from the host PC."""
    req_path = request.query.get("path", "").strip()
    if not req_path:
        return web.Response(text="Path is required", status=400)
    target_path = Path(req_path)
    if not target_path.exists() or not target_path.is_file():
        return web.Response(text="File not found", status=404)
    return web.FileResponse(
        target_path,
        headers={
            "Content-Disposition": f'attachment; filename="{target_path.name}"'
        }
    )

async def handle_fs_read(request):
    """Reads preview content of a text or code file."""
    req_path = request.query.get("path", "").strip()
    if not req_path:
        return json_response({"status": "error", "message": "Path is required"}, status=400)
    target_path = Path(req_path)
    if not target_path.exists() or not target_path.is_file():
        return json_response({"status": "error", "message": "File not found"}, status=404)
    try:
        with open(target_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(50000)
        return json_response({"status": "ok", "name": target_path.name, "path": str(target_path), "content": content})
    except Exception as e:
        return json_response({"status": "error", "message": str(e)}, status=500)

async def handle_fs_upload(request):
    """Uploads a file (base64 encoded) from mobile client to host PC."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    filename = body.get("filename", "").strip()
    folder = body.get("folder", "").strip()
    data_b64 = body.get("data", "")

    if not filename or not data_b64:
        return json_response({"status": "error", "message": "Filename and data are required"}, status=400)

    import base64
    if "," in data_b64:
        data_b64 = data_b64.split(",", 1)[1]

    try:
        raw_bytes = base64.b64decode(data_b64)
    except Exception as e:
        return json_response({"status": "error", "message": f"Base64 decode failed: {e}"}, status=400)

    clean_name = Path(filename).name
    if not clean_name:
        clean_name = f"upload_{int(time.time())}.bin"

    target_dir = Path(folder) if folder else Path.home() / "Downloads"
    if not target_dir.exists() or not target_dir.is_dir():
        target_dir = Path.home() / "Downloads"

    target_file = target_dir / clean_name
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
    disks = win32_controls.get_disk_stats()
    return json_response({"status": "ok", "disks": disks})

async def handle_pc_ping(request):
    """Measures network ping and internet connectivity."""
    host = request.query.get("host", "8.8.8.8")
    res = win32_controls.get_network_ping(host)
    return json_response(res)

async def handle_pc_speak(request):
    """Speaks text through host PC speakers via SAPI."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)
    text = body.get("text", "")
    res = win32_controls.speak_text(text)
    return json_response(res)

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
    state = win32_controls.set_keep_awake(enable)
    return json_response({"status": "ok", "keep_awake": state})


async def handle_get_clipboard(request):
    text = win32_controls.get_clipboard_text()
    return json_response({"status": "ok", "text": text})

async def handle_set_clipboard(request):
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    text = body.get("text", "")
    success = win32_controls.set_clipboard_text(text)
    return json_response({"status": "ok" if success else "error", "length": len(text)})

async def handle_antigravity_status(request):
    status = await antigravity_bridge.get_antigravity_status()
    return json_response(status)

async def handle_antigravity_launch(request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    win32_controls.wake_screen()
    if body.get("restart"):
        account_manager.stop_antigravity_gracefully()
    ready, status = await antigravity_bridge.ensure_antigravity_ready(timeout=30)
    conv_id = (body.get("conversation_id") or "").strip()
    if ready and conv_id and conv_id != "new":
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
    try:
        limit = int(request.query.get("limit", 40))
    except Exception:
        limit = 40
    res = await antigravity_bridge.get_conversation_chat(conv_id=conv_id, limit=limit)
    return json_response(res)

_antigravity_send_lock = asyncio.Lock()

async def handle_antigravity_send(request):
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    prompt = body.get("text", "")
    image_data = body.get("image", None)
    focus_win = bool(body.get("focus", False))
    conv_id = body.get("conversation_id", None)

    image_path = None
    if image_data:
        try:
            uploads_dir = SERVER_DIR.parent / "uploads"
            uploads_dir.mkdir(parents=True, exist_ok=True)
            import base64
            import time

            if "," in image_data:
                header, b64_str = image_data.split(",", 1)
                ext = "png"
                if "jpeg" in header or "jpg" in header:
                    ext = "jpg"
                elif "webp" in header:
                    ext = "webp"
            else:
                b64_str = image_data
                ext = "png"

            img_bytes = base64.b64decode(b64_str)
            filename = f"screenshot_{int(time.time() * 1000)}.{ext}"
            saved_file = uploads_dir / filename
            with open(saved_file, "wb") as f:
                f.write(img_bytes)
            image_path = str(saved_file.resolve())
            logger.info(f"Saved uploaded screenshot to: {image_path} ({len(img_bytes)} bytes)")
        except Exception as e:
            logger.error(f"Failed to decode uploaded image: {e}")

    logger.info(f"Queueing/injecting prompt to Antigravity ({len(prompt)} chars, img: {bool(image_path)}, conv: {conv_id})...")

    # Wake screen so UI render loop is active
    win32_controls.wake_screen()

    async with _antigravity_send_lock:
        res = await antigravity_bridge.send_prompt_to_antigravity(
            prompt, 
            image_path=image_path, 
            conv_id=conv_id, 
            focus_window=focus_win
        )
        await asyncio.sleep(0.3)
        return json_response(res)

async def handle_pc_screenshot(request):
    data, src = await antigravity_bridge.capture_pc_screenshot()
    if not data:
        return json_response({"status": "error", "message": "Failed to capture PC screenshot"}, status=500)
    return json_response({"status": "ok", "image": data, "source": src})

async def handle_pc_telemetry(request):
    data = antigravity_bridge.get_pc_telemetry()
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
    res = account_manager.list_saved_accounts()
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
    res = account_manager.delete_account_profile(email)
    return json_response(res)

async def handle_antigravity_auth_url(request):
    """Returns Google OAuth authorization URL for mobile/remote login."""
    redirect_uri = f"http://127.0.0.1:8765/api/antigravity/auth/callback"
    url = account_manager.get_google_oauth_url(redirect_uri)
    return json_response({"status": "ok", "url": url, "redirect_uri": redirect_uri})

async def handle_antigravity_auth_callback(request):
    """Handles Google OAuth redirect after mobile login."""
    code = request.query.get("code")
    error = request.query.get("error")
    if error:
        return web.Response(text=f"OAuth Error: {error}", status=400)
    if not code:
        return web.Response(text="No authorization code received", status=400)

    redirect_uri = f"http://127.0.0.1:8765/api/antigravity/auth/callback"
    res = account_manager.exchange_auth_code_for_account(code, redirect_uri)
    email = res.get("email", "Google Account")

    html = f"""<!DOCTYPE html>
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
    <div class="email-pill">{email}</div>
    <p style="margin-top: 20px; font-size: 13px; color: #71717A;">
      Лимиты и квоты обновлены. Antigravity автоматически перезапущен с новым профилем. Можете закрыть эту страницу и вернуться в приложение.
    </p>
  </div>
</body>
</html>"""
    return web.Response(text=html, content_type="text/html", status=200)

async def handle_antigravity_submit_code(request):
    """Manual submission of Google OAuth auth code."""
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)
    code = body.get("code", "").strip()
    if not code:
        return json_response({"status": "error", "message": "Code is required"}, status=400)
    redirect_uri = body.get("redirect_uri", "http://127.0.0.1:8765/api/antigravity/auth/callback")
    res = account_manager.exchange_auth_code_for_account(code, redirect_uri)
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
    res = account_manager.open_oauth_on_pc()
    return json_response(res)

async def handle_server_logs(request):
    """Returns the last lines of server.log."""
    try:
        limit = int(request.query.get("limit", 40))
    except Exception:
        limit = 40
    log_file = SERVER_DIR / "server.log"
    if not log_file.exists():
        return json_response({"status": "ok", "lines": ["(server.log is empty)"]})
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        recent = [l.rstrip("\r\n") for l in all_lines[-limit:]]
        return json_response({"status": "ok", "lines": recent, "total_lines": len(all_lines)})
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

def create_app():
    app = web.Application()

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
    app.router.add_get("/api/clipboard", handle_get_clipboard)
    app.router.add_post("/api/clipboard", handle_set_clipboard)
    app.router.add_get("/api/antigravity/status", handle_antigravity_status)
    app.router.add_get("/api/antigravity/conversations", handle_antigravity_conversations)
    app.router.add_post("/api/antigravity/conversation/switch", handle_antigravity_switch_conversation)
    app.router.add_post("/api/antigravity/conversation/pin", handle_antigravity_pin_conversation)
    app.router.add_post("/api/antigravity/conversation/new", handle_antigravity_new_conversation)
    app.router.add_get("/api/antigravity/chat", handle_antigravity_chat)
    app.router.add_post("/api/antigravity/send", handle_antigravity_send)
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

    # Web Dashboard Static Fallback
    if MOBILE_APP_DIR.exists():
        app.router.add_static("/", MOBILE_APP_DIR, show_index=True)

    app.on_startup.append(on_server_startup)
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

        if not antigravity_bridge.is_antigravity_running():
            logging.info("[BootAutoLaunch] Antigravity not running on boot. Auto-launching IDE...")
            win32_controls.wake_screen()
            await antigravity_bridge.ensure_antigravity_ready(timeout=35)
    except Exception as e:
        logging.error(f"[BootAutoLaunch] Error: {e}")

async def on_server_startup(app):
    asyncio.create_task(_boot_antigravity_and_resume())

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

    # Start Battery Guard for laptop power monitoring
    battery_guard.start_battery_guard()

    web.run_app(app, host="0.0.0.0", port=port, print=None)

if __name__ == "__main__":
    port = 8765
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    run_server(port)
