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

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s [%(name)s]: %(message)s",
    datefmt="%H:%M:%S"
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

    return json_response({"status": "error", "message": f"Unknown action: {action}"}, status=400)

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

async def handle_antigravity_chat(request):
    conv_id = request.query.get("conversation_id", None)
    try:
        limit = int(request.query.get("limit", 40))
    except Exception:
        limit = 40
    res = await antigravity_bridge.get_conversation_chat(conv_id=conv_id, limit=limit)
    return json_response(res)

async def handle_antigravity_send(request):
    try:
        body = await request.json()
    except Exception:
        return json_response({"status": "error", "message": "Invalid JSON"}, status=400)

    prompt = body.get("text", "")
    focus_win = bool(body.get("focus", False))
    conv_id = body.get("conversation_id", None)
    logger.info(f"Injecting prompt to Antigravity ({len(prompt)} chars, conv: {conv_id})...")

    res = await antigravity_bridge.send_prompt_to_antigravity(prompt, conv_id=conv_id, focus_window=focus_win)
    return json_response(res)

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
    app.router.add_post("/api/pc/action", handle_pc_action)
    app.router.add_get("/api/clipboard", handle_get_clipboard)
    app.router.add_post("/api/clipboard", handle_set_clipboard)
    app.router.add_get("/api/antigravity/status", handle_antigravity_status)
    app.router.add_get("/api/antigravity/conversations", handle_antigravity_conversations)
    app.router.add_post("/api/antigravity/conversation/switch", handle_antigravity_switch_conversation)
    app.router.add_post("/api/antigravity/conversation/new", handle_antigravity_new_conversation)
    app.router.add_get("/api/antigravity/chat", handle_antigravity_chat)
    app.router.add_post("/api/antigravity/send", handle_antigravity_send)
    app.router.add_get("/download/apk", handle_download_apk)

    # Web Dashboard Static Fallback
    if MOBILE_APP_DIR.exists():
        app.router.add_static("/", MOBILE_APP_DIR, show_index=True)

    return app

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

    web.run_app(app, host="0.0.0.0", port=port, print=None)

if __name__ == "__main__":
    port = 8765
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    run_server(port)
