"""
Antigravity IDE Bridge.
Communicates directly with Antigravity 2.0 Electron instance via Chrome DevTools Protocol (CDP)
and Win32 focus fallback to inject prompts, attach screenshot files, and monitor truthful agent status.
"""

import asyncio
from collections import deque
import ctypes
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time

import aiohttp

logger = logging.getLogger("AntigravityBridge")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

DEFAULT_CDP_PORT = 60382
ANTIGRAVITY_EXE = r"C:\Users\dev\AppData\Local\Programs\antigravity\Antigravity.exe"
DEVTOOLS_PORT_FILE = os.path.expandvars(r"%APPDATA%\Antigravity\DevToolsActivePort")
BRAIN_DIR = Path(os.path.expandvars(r"%USERPROFILE%\.gemini\antigravity\brain"))
ANTIGRAVITY_DIR = BRAIN_DIR.parent
_CONV_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def is_valid_conv_id(conv_id: str) -> bool:
    """Validates conversation IDs to prevent path traversal and template injection."""
    return bool(conv_id and isinstance(conv_id, str) and _CONV_ID_RE.match(conv_id))


async def _cdp_call(ws, req_id: int, method: str, params=None, timeout: float = 3.0):
    """Sends a CDP request and waits safely for the matching response ID with timeout and close handling."""
    payload = {"id": req_id, "method": method}
    if params is not None:
        payload["params"] = params
    await ws.send_str(json.dumps(payload))

    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError(f"CDP timeout waiting for id={req_id} ({method})")
        msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
        if msg.type == aiohttp.WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
            except Exception:
                continue
            if data.get("id") == req_id:
                return data
        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
            raise ConnectionError(f"CDP WebSocket closed while waiting for id={req_id}")

def get_active_cdp_port(fallback=DEFAULT_CDP_PORT):
    """Dynamically retrieves the active DevTools port from Electron's DevToolsActivePort file."""
    try:
        if os.path.exists(DEVTOOLS_PORT_FILE):
            with open(DEVTOOLS_PORT_FILE, "r", encoding="utf-8") as f:
                first_line = f.readline().strip()
                if first_line.isdigit():
                    return int(first_line)
    except Exception as e:
        logger.debug(f"Could not read DevToolsActivePort file: {e}")
    return fallback

def get_current_desktop_name():
    """Returns the Win32 desktop name of the current process thread (e.g. 'Default')."""
    try:
        hdesk = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
        if hdesk:
            buf = ctypes.create_unicode_buffer(256)
            if user32.GetUserObjectInformationW(hdesk, 2, buf, 512, None):
                return buf.value
    except Exception:
        pass
    return ""

def has_antigravity_window_on_default_desktop():
    """Checks whether a visible Antigravity main window exists on the user's Default desktop."""
    try:
        hdesk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
        if not hdesk:
            return False
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        found = [False]
        def enum_cb(hwnd, lp):
            if user32.IsWindowVisible(hwnd):
                cls_buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls_buf, 256)
                if cls_buf.value == "Chrome_WidgetWin_1":
                    txt_buf = ctypes.create_unicode_buffer(512)
                    user32.GetWindowTextW(hwnd, txt_buf, 512)
                    if "antigravity" in txt_buf.value.lower() or "launcher" in txt_buf.value.lower():
                        found[0] = True
                        return False
            return True
        user32.EnumDesktopWindows(hdesk, WNDENUMPROC(enum_cb), 0)
        user32.CloseDesktop(hdesk)
        return found[0]
    except Exception:
        return False

def is_antigravity_running():
    """
    Checks whether the real Antigravity Electron IDE main process is active.
    Explicitly ignores orphaned sidecar_sdk / register_loader.js workers and crashpad helpers.
    """
    try:
        import psutil
        for p in psutil.process_iter(['name', 'cmdline']):
            name = (p.info.get('name') or '').lower()
            if name == "antigravity.exe":
                cmd = " ".join(p.info.get('cmdline') or []).lower()
                if any(ignore in cmd for ignore in ("sidecar_sdk", "register_loader.js", "main.mjs", "--type=", "crashpad")):
                    continue
                return True
    except Exception:
        try:
            no_win = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            out = subprocess.check_output(["tasklist"], creationflags=no_win).decode("utf-8", errors="ignore")
            if "antigravity.exe" in out.lower():
                return True
        except Exception:
            pass
    return False

def _is_game_mode_silent() -> bool:
    try:
        import win32_controls
        return win32_controls.is_game_mode_active()
    except Exception:
        return False

def launch_antigravity(force=False, background=None):
    """Launches Antigravity desktop executable (minimized/unfocused if Game Mode is active)."""
    if not force and is_antigravity_running():
        return True
    if os.path.exists(ANTIGRAVITY_EXE):
        if background is None:
            background = _is_game_mode_silent()
        cur_desk = get_current_desktop_name()
        if cur_desk and cur_desk.lower() != "default":
            logger.warning(f"Launching Antigravity from non-Default desktop '{cur_desk}' — window may not appear on Default desktop!")
        logger.info(f"Launching Antigravity executable (force={force}, background={background}, desktop={cur_desk}): {ANTIGRAVITY_EXE}")
        exe_path = Path(ANTIGRAVITY_EXE)
        try:
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 7 if background else 1  # 7=SW_SHOWMINNOACTIVE (zero focus steal while gaming!), 1=SW_SHOWNORMAL
            subprocess.Popen(
                [str(exe_path)],
                cwd=str(exe_path.parent),
                creationflags=flags,
                startupinfo=si,
                close_fds=True
            )
            return True
        except Exception as e:
            logger.error(f"Error launching Antigravity: {e}")
    return False

def ensure_antigravity_running():
    """Legacy alias for backward compatibility."""
    return launch_antigravity()

async def wait_for_chat_ui_ready(port=None, timeout=18):
    """Polls Antigravity CDP until the chat input element is mounted and interactive."""
    start_time = time.time()
    check_js = """
    (() => {
        const el = document.querySelector('[aria-label="Message input"]') ||
                   document.querySelector('[contenteditable="true"]') ||
                   document.querySelector('textarea');
        return !!el;
    })()
    """
    while time.time() - start_time < timeout:
        target = await find_antigravity_target(port)
        if target and target.get("webSocketDebuggerUrl"):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(target["webSocketDebuggerUrl"], timeout=aiohttp.ClientTimeout(total=2.5)) as ws:
                        reply = await _cdp_call(
                            ws, 901, "Runtime.evaluate",
                            {"expression": check_js, "returnByValue": True},
                            timeout=1.5
                        )
                        if reply.get("result", {}).get("result", {}).get("value") is True:
                            return True
            except Exception:
                pass
        await asyncio.sleep(0.8)
    return False

async def ensure_antigravity_ready(timeout=30):
    """
    Ensures Antigravity is up and responds via DevTools HTTP endpoint.
    If not running, automatically launches it and waits until CDP & UI are ready.
    """
    target = await find_antigravity_target()
    if target:
        return True, "already_running"

    logger.info("Antigravity DevTools not reachable. Launching Antigravity...")
    launch_antigravity(force=True)

    start_time = time.time()
    while time.time() - start_time < timeout:
        await asyncio.sleep(1.0)
        target = await find_antigravity_target()
        if target:
            elapsed = time.time() - start_time
            logger.info(f"Antigravity CDP ready after {elapsed:.1f}s! Waiting for UI hydration...")
            await wait_for_chat_ui_ready(timeout=12)
            return True, "launched"

    logger.warning("Antigravity launch timed out waiting for DevTools endpoint.")
    return False, "timeout"


async def find_antigravity_target(port=None):
    """Finds the active Antigravity conversation tab via DevTools HTTP endpoints."""
    if port is None:
        port = get_active_cdp_port()
    url = f"http://127.0.0.1:{port}/json/list"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=2.0)) as resp:
                if resp.status == 200:
                    pages = await resp.json()
                    # Priority 1: Match page with conversation ID in URL
                    for p in pages:
                        p_url = p.get("url", "")
                        if "/c/" in p_url and "antigravity" in p.get("title", "").lower():
                            return p
                    # Priority 2: Any Antigravity title (skip data:text/html frames)
                    for p in pages:
                        p_url = p.get("url", "")
                        if "antigravity" in p.get("title", "").lower() and p.get("type") == "page" and not p_url.startswith("data:"):
                            return p
                    # Priority 3: First regular page
                    for p in pages:
                        if p.get("type") == "page" and not p.get("url", "").startswith("data:"):
                            return p
    except Exception as e:
        logger.debug(f"Failed to query CDP on port {port}: {e}")
    return None

def focus_antigravity_window():
    """Brings the Antigravity desktop window to foreground on user desktop (skipped during Game Mode)."""
    if _is_game_mode_silent():
        logger.info("[GameMode] Skipped focus_antigravity_window to keep game fullscreen")
        return False
    prev_desk = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    hdesk = None
    try:
        hdesk = user32.OpenDesktopW("default", 0, False, 0x01FF)
        if not hdesk:
            return False
        user32.SetThreadDesktop(hdesk)

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        target_hwnd = [0]

        def enum_cb(hwnd, lp):
            if user32.IsWindowVisible(hwnd):
                cls_buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls_buf, 256)
                if cls_buf.value == "Chrome_WidgetWin_1":
                    txt_buf = ctypes.create_unicode_buffer(512)
                    user32.GetWindowTextW(hwnd, txt_buf, 512)
                    if "Antigravity" in txt_buf.value or "Launcher" in txt_buf.value:
                        target_hwnd[0] = hwnd
                        return False
            return True

        user32.EnumDesktopWindows(hdesk, WNDENUMPROC(enum_cb), 0)
        hwnd = target_hwnd[0]
        if hwnd:
            user32.ShowWindow(hwnd, 9) # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return True
    except Exception as e:
        logger.error(f"Error focusing window: {e}")
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
    return False

_conv_cache = {
    "timestamp": 0,
    "items": []
}

def load_official_conversation_titles():
    """Reads official human-readable conversation titles from Antigravity's conversation_summaries.db."""
    db_path = ANTIGRAVITY_DIR / "conversation_summaries.db"
    if not db_path.exists():
        return {}
    titles = {}
    try:
        uri = f"file:{db_path.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as con:
            cur = con.cursor()
            rows = cur.execute("SELECT conversation_id, title, preview FROM conversation_summaries").fetchall()
            for cid, title, preview in rows:
                clean_t = (title or preview or "").strip()
                if clean_t:
                    titles[cid] = clean_t
    except Exception as e:
        logger.debug(f"Failed to read conversation_summaries.db: {e}")
    return titles

def get_all_brain_conversations(cache_ttl=4.0):
    """Scans BRAIN_DIR for all conversation folders, extracts clean title and timestamps."""
    global _conv_cache
    now = time.time()
    if _conv_cache["items"] and (now - _conv_cache["timestamp"] < cache_ttl):
        return [dict(x) for x in _conv_cache["items"]]

    db_titles = load_official_conversation_titles()
    convs = []
    try:
        if BRAIN_DIR.exists():
            for d in BRAIN_DIR.iterdir():
                if not d.is_dir() or d.name.startswith("."):
                    continue
                log_path = d / ".system_generated" / "logs" / "transcript.jsonl"
                if not log_path.exists():
                    continue
                try:
                    mtime = log_path.stat().st_mtime
                except Exception:
                    mtime = d.stat().st_mtime

                # 1. First priority: official title from Antigravity summaries database
                title = db_titles.get(d.name, "")

                # 2. Fallback: parse first user input cleanly
                if not title:
                    try:
                        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                            for line in f:
                                if '"type":"USER_INPUT"' in line:
                                    obj = json.loads(line)
                                    raw = obj.get("content", "")
                                    if isinstance(raw, list):
                                        parts = [p.get("text", "") for p in raw if isinstance(p, dict)]
                                        raw = " ".join(parts)
                                    if "<USER_REQUEST>" in raw:
                                        m = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", raw, re.DOTALL)
                                        if m:
                                            raw = m.group(1)
                                    title = clean_user_text(raw).replace("\n", " ").strip()
                                    title = re.sub(r"\s+", " ", title)[:60]
                                    if title:
                                        break
                    except Exception:
                        pass

                if not title:
                    title = f"Диалог {d.name[:8]}"

                convs.append({
                    "id": d.name,
                    "title": title,
                    "mtime": mtime
                })
        convs.sort(key=lambda x: x["mtime"], reverse=True)
        _conv_cache["timestamp"] = now
        _conv_cache["items"] = convs
    except Exception as e:
        logger.error(f"Error scanning brain conversations: {e}")

    return [dict(x) for x in _conv_cache["items"]]

PINNED_FILE = Path(__file__).resolve().parent.parent / "pinned_conversations.json"

_pinned_cache = {
    "timestamp": 0,
    "ids": set()
}

def load_local_pinned_ids():
    if PINNED_FILE.exists():
        try:
            with open(PINNED_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()

def save_local_pinned_ids(pinned_set):
    try:
        with open(PINNED_FILE, "w", encoding="utf-8") as f:
            json.dump(list(pinned_set), f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save pinned ids: {e}")

async def get_pinned_conversation_ids(port=None):
    """Fetches pinned conversation IDs from live Antigravity CDP DOM merged with local persistence."""
    global _pinned_cache
    now = time.time()
    if _pinned_cache["ids"] and (now - _pinned_cache["timestamp"] < 5.0):
        return set(_pinned_cache["ids"])

    local_pinned = load_local_pinned_ids()
    cdp_pinned = set()

    try:
        target = await find_antigravity_target(port)
        if target:
            ws_url = target.get("webSocketDebuggerUrl")
            if ws_url:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=2.0)) as ws:
                        js = """
                        (() => {
                            const pinned = [];
                            document.querySelectorAll('[data-pinned="true"]').forEach(el => {
                                const id = el.getAttribute('data-cascade-id') || el.getAttribute('data-conversation-id');
                                if (id && !pinned.includes(id)) {
                                    pinned.push(id);
                                }
                            });
                            return pinned;
                        })()
                        """
                        r = await _cdp_call(ws, 501, "Runtime.evaluate", {"expression": js, "returnByValue": True}, timeout=1.5)
                        val = r.get("result", {}).get("result", {}).get("value", [])
                        if isinstance(val, list):
                            cdp_pinned = set(val)
    except Exception as e:
        logger.debug(f"Failed to get pinned IDs via CDP: {e}")

    merged = local_pinned | cdp_pinned
    if merged and merged != local_pinned:
        save_local_pinned_ids(merged)

    _pinned_cache["timestamp"] = now
    _pinned_cache["ids"] = set(merged)
    return merged

async def toggle_conversation_pin(conv_id, pinned=None, port=None):
    """Toggles or sets the pinned state for a conversation in PC Remote and triggers CDP toggle if available."""
    if not is_valid_conv_id(conv_id):
        return {"status": "error", "message": "Invalid conversation ID"}

    current_pins = load_local_pinned_ids()
    if pinned is None:
        new_state = (conv_id not in current_pins)
    else:
        new_state = bool(pinned)

    if new_state:
        current_pins.add(conv_id)
    else:
        current_pins.discard(conv_id)

    save_local_pinned_ids(current_pins)
    global _pinned_cache
    _pinned_cache["timestamp"] = time.time()
    _pinned_cache["ids"] = set(current_pins)

    # Attempt to click pin button in Antigravity DOM if visible
    cdp_triggered = False
    try:
        target = await find_antigravity_target(port)
        if target:
            ws_url = target.get("webSocketDebuggerUrl")
            if ws_url:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=2.0)) as ws:
                        conv_json = json.dumps(conv_id)
                        js = f"""
                        (() => {{
                            const cid = {conv_json};
                            const row = document.querySelector(`[data-cascade-id="${{cid}}"]`);
                            if (!row) return {{ found: false }};
                            const btn = row.querySelector('button[aria-label="Pin conversation"], button[aria-label="Unpin conversation"]');
                            if (btn) {{
                                btn.click();
                                return {{ found: true, clicked: true }};
                            }}
                            return {{ found: true, clicked: false }};
                        }})()
                        """
                        r = await _cdp_call(ws, 502, "Runtime.evaluate", {"expression": js, "returnByValue": True}, timeout=1.5)
                        res = r.get("result", {}).get("result", {}).get("value", {})
                        cdp_triggered = bool(res.get("clicked", False))
    except Exception as e:
        logger.debug(f"CDP pin toggle error: {e}")

    return {
        "status": "ok",
        "id": conv_id,
        "isPinned": new_state,
        "cdp_synced": cdp_triggered
    }

async def get_conversations_list(port=None):
    """Retrieves all active and historical conversations from brain/, marking pinned and currently running ones."""
    cache_ttl = 20.0 if _is_game_mode_silent() else 4.0
    active_id = await get_active_conversation_id(port)
    pinned_ids = await get_pinned_conversation_ids(port)
    target = await find_antigravity_target(port)
    running_ids = await get_running_conversation_ids_cdp(target=target, port=port)
    convs = get_all_brain_conversations(cache_ttl=cache_ttl)
    for c in convs:
        c["isCurrent"] = (c["id"] == active_id)
        c["isPinned"] = (c["id"] in pinned_ids)
        c["isRunning"] = (c["id"] in running_ids)

    return {
        "status": "ok",
        "active_id": active_id,
        "total": len(convs),
        "pinned_count": len([c for c in convs if c.get("isPinned")]),
        "running_count": len(running_ids),
        "conversations": convs
    }

async def switch_conversation(conv_id, port=None):
    """Navigates Antigravity UI to the specified conversation ID while preserving project context."""
    if not is_valid_conv_id(conv_id):
        return {"status": "error", "message": "Invalid conversation ID"}

    target = await find_antigravity_target(port)
    if not target:
        return {"status": "error", "message": "Antigravity not found"}

    current_url = target.get("url", "")
    if f"/c/{conv_id}" in current_url:
        logger.info(f"Target is already on conversation {conv_id}, no switch needed.")
        return {"status": "ok", "message": f"Already on {conv_id}"}

    ws_url = target.get("webSocketDebuggerUrl")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=4.0)) as ws:
                conv_json = json.dumps(conv_id)
                js = f"""
                (() => {{
                    const cid = {conv_json};
                    const link = document.querySelector(`a[href*="${{cid}}"]`);
                    if (link) {{
                        link.click();
                        return {{ success: true, method: 'click' }};
                    }} else {{
                        const currentSearch = window.location.search || '';
                        window.location.href = '/c/' + encodeURIComponent(cid) + currentSearch;
                        return {{ success: true, method: 'location' }};
                    }}
                }})()
                """
                try:
                    await _cdp_call(ws, 202, "Runtime.evaluate", {"expression": js, "returnByValue": True}, timeout=2.0)
                except Exception:
                    pass
                await asyncio.sleep(0.5)
                return {"status": "ok", "message": f"Switched to {conv_id}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def create_new_conversation(port=None):
    """Triggers 'New Conversation' in Antigravity to create a fresh chat session."""
    target = await find_antigravity_target(port)
    if not target:
        return {"status": "error", "message": "Antigravity not found"}

    ws_url = target.get("webSocketDebuggerUrl")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=4.0)) as ws:
                js = """
                (() => {
                    const btn = document.querySelector('[data-testid="new-conversation-button"]') ||
                                document.querySelector('a[href="/"]') ||
                                Array.from(document.querySelectorAll('a, button')).find(x => x.textContent.trim() === 'New Conversation');
                    if (btn) {
                        btn.click();
                        return { success: true, method: 'btn_click' };
                    } else {
                        const search = window.location.search || '';
                        window.location.href = '/' + search;
                        return { success: true, method: 'root_nav' };
                    }
                })()
                """
                await _cdp_call(ws, 203, "Runtime.evaluate", {"expression": js, "returnByValue": True}, timeout=2.5)
                await asyncio.sleep(1.0)
                return {"status": "ok", "message": "New conversation initiated"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def get_antigravity_status(port=None):
    """Returns the current connection status, active conversation title, and desktop visibility."""
    running = is_antigravity_running()
    win_vis = has_antigravity_window_on_default_desktop()
    cur_desk = get_current_desktop_name()
    target = await find_antigravity_target(port)
    if not target:
        return {
            "connected": False,
            "antigravity_running": running,
            "window_visible": win_vis,
            "desktop": cur_desk,
            "title": "Not Connected",
            "url": "",
            "message": "Antigravity DevTools not reachable"
        }
    return {
        "connected": True,
        "antigravity_running": running,
        "window_visible": win_vis,
        "desktop": cur_desk,
        "title": target.get("title", "Active Conversation"),
        "url": target.get("url", ""),
        "id": target.get("id", "")
    }

def clean_user_text(raw):
    """Cleans internal XML markup from user requests for mobile display."""
    if not raw:
        return ""
    if isinstance(raw, list):
        parts = []
        for p in raw:
            if isinstance(p, dict) and "text" in p:
                parts.append(p["text"])
        raw = " ".join(parts)

    if not isinstance(raw, str):
        return str(raw)

    m = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", raw, re.DOTALL)
    if m:
        return m.group(1).strip()

    if raw.startswith("<CONTEXT_SUMMARY>") or raw.startswith("<system>") or "<identity>" in raw:
        return ""

    cleaned = re.sub(r"<ADDITIONAL_METADATA>.*?</ADDITIONAL_METADATA>", "", raw, flags=re.DOTALL)
    return cleaned.strip()

async def get_active_conversation_id(port=None):
    """Detects active conversation ID via CDP target URL or latest modified directory in brain."""
    try:
        target = await find_antigravity_target(port)
        if target:
            url = target.get("url", "")
            if "/c/" in url:
                parts = url.split("/c/")[1].split("?")[0].strip("/")
                if parts:
                    return parts
            else:
                return "new"
    except Exception:
        pass

    try:
        if BRAIN_DIR.exists():
            dirs = [d for d in BRAIN_DIR.iterdir() if d.is_dir()]
            def get_mtime(p):
                t = p / ".system_generated" / "logs" / "transcript.jsonl"
                if t.exists():
                    return t.stat().st_mtime
                return p.stat().st_mtime
            dirs.sort(key=get_mtime, reverse=True)
            for d in dirs:
                if (d / ".system_generated" / "logs" / "transcript.jsonl").exists():
                    return d.name
    except Exception:
        pass
    return None

def parse_conversation_history(conv_id, limit=50):
    """Parses transcript.jsonl into clean user/assistant messages and live agent status."""
    if not is_valid_conv_id(conv_id):
        return {
            "status": "error",
            "message": "Invalid conversation ID",
            "conversation_id": conv_id,
            "agent_status": "error",
            "current_action": "",
            "messages": []
        }

    base_dir = BRAIN_DIR.resolve()
    conv_dir = (base_dir / conv_id).resolve()
    if not conv_dir.is_relative_to(base_dir):
        return {
            "status": "error",
            "message": "Invalid conversation path",
            "conversation_id": conv_id,
            "agent_status": "error",
            "current_action": "",
            "messages": []
        }

    logs_dir = conv_dir / ".system_generated" / "logs"
    compact_path = logs_dir / "transcript.jsonl"
    full_path = logs_dir / "transcript_full.jsonl"

    if not compact_path.exists():
        return {
            "status": "error",
            "message": f"Conversation logs not found for {conv_id}",
            "conversation_id": conv_id,
            "agent_status": "unknown",
            "current_action": "",
            "messages": []
        }

    try:
        recent_lines = deque(maxlen=500)
        total_lines = 0
        with open(compact_path, "r", encoding="utf-8", errors="replace") as f:
            for total_lines, line in enumerate(f, 1):
                recent_lines.append(line)

        start_index = total_lines - len(recent_lines)

        parsed_steps = []
        needed_full_indices = set()
        for idx_offset, line in enumerate(recent_lines):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except Exception:
                continue
            abs_idx = start_index + idx_offset
            parsed_steps.append((abs_idx, obj))
            trunc = obj.get("truncated_fields", [])
            if obj.get("type") in ("USER_INPUT", "PLANNER_RESPONSE") and "content" in trunc:
                needed_full_indices.add(abs_idx)

        # Load any truncated full steps in a single streaming pass over transcript_full.jsonl
        full_cache = {}
        if needed_full_indices and full_path.exists():
            max_needed = max(needed_full_indices)
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as ff:
                    for line_idx, line in enumerate(ff):
                        if line_idx > max_needed:
                            break
                        if line_idx in needed_full_indices:
                            try:
                                full_cache[line_idx] = json.loads(line.strip())
                            except Exception:
                                pass
            except Exception:
                pass

        messages = []
        agent_status = "idle"
        current_action = ""

        for abs_index, step in parsed_steps:
            step_type = step.get("type")
            trunc = step.get("truncated_fields", [])

            if step_type == "USER_INPUT":
                raw_content = step.get("content", "")
                if "content" in trunc and abs_index in full_cache:
                    raw_content = full_cache[abs_index].get("content", raw_content)

                clean_text = clean_user_text(raw_content)
                if clean_text:
                    media_list = step.get("media", [])
                    messages.append({
                        "id": f"msg_u_{abs_index}",
                        "role": "user",
                        "text": clean_text,
                        "created_at": step.get("created_at", ""),
                        "has_image": len(media_list) > 0
                    })

            elif step_type == "PLANNER_RESPONSE":
                content = step.get("content", "")
                if "content" in trunc and abs_index in full_cache:
                    content = full_cache[abs_index].get("content", content)

                tool_calls = step.get("tool_calls", [])
                if content and content.strip():
                    messages.append({
                        "id": f"msg_a_{abs_index}",
                        "role": "assistant",
                        "text": content.strip(),
                        "created_at": step.get("created_at", "")
                    })
                    agent_status = "idle"
                    current_action = ""
                elif tool_calls:
                    tool = tool_calls[0]
                    summary = tool.get("args", {}).get("toolSummary", tool.get("name", "Команда"))
                    agent_status = "executing"
                    current_action = summary
                elif step.get("thinking"):
                    agent_status = "thinking"
                    current_action = "Размышляет..."

        if parsed_steps:
            _, last = parsed_steps[-1]
            ltype = last.get("type")

            # Check if this last step is stale (> 3 minutes ago)
            is_stale = False
            last_created = last.get("created_at")
            if last_created:
                try:
                    import datetime
                    dt = datetime.datetime.fromisoformat(last_created.replace("Z", "+00:00"))
                    now = datetime.datetime.now(datetime.timezone.utc)
                    if (now - dt).total_seconds() > 180:
                        is_stale = True
                except Exception:
                    pass

            if is_stale:
                agent_status = "idle"
                current_action = ""
            elif ltype == "PLANNER_RESPONSE":
                tool_calls = last.get("tool_calls", [])
                if tool_calls:
                    agent_status = "executing"
                    tool = tool_calls[0]
                    current_action = tool.get("args", {}).get("toolSummary", tool.get("name", "Команда"))
                elif last.get("thinking") and not last.get("content"):
                    agent_status = "thinking"
                    current_action = "Размышляет..."
                else:
                    agent_status = "idle"
                    current_action = ""
            elif ltype == "GENERIC":
                agent_status = "thinking"
                current_action = "Размышляет над ответом..."
            elif ltype == "USER_INPUT":
                agent_status = "thinking"
                current_action = "Размышляет..."

        if limit and len(messages) > limit:
            messages = messages[-limit:]

        return {
            "status": "ok",
            "conversation_id": conv_id,
            "agent_status": agent_status,
            "current_action": current_action,
            "total_messages": len(messages),
            "messages": messages
        }
    except Exception as e:
        logger.error(f"Error parsing transcript: {e}", exc_info=True)
        return {
            "status": "error",
            "message": str(e),
            "conversation_id": conv_id,
            "agent_status": "error",
            "current_action": "",
            "messages": []
        }

async def get_running_conversation_ids_cdp(target=None, port=None) -> set:
    """
    Directly queries Antigravity DOM via Chrome DevTools Protocol to find
    all conversation IDs that currently have an active Stop button.
    """
    try:
        if not target:
            target = await find_antigravity_target(port)
        if not target:
            return set()
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            return set()
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=2.0)) as ws:
                js = """
                (() => {
                    const running = new Set();
                    const stopBtns = document.querySelectorAll('button[aria-label*="Stop" i], button[title*="Stop" i]');
                    for (const btn of stopBtns) {
                        const row = btn.closest('[data-cascade-id], [data-conversation-id]');
                        if (row) {
                            const cid = row.getAttribute('data-cascade-id') || row.getAttribute('data-conversation-id');
                            if (cid) running.add(cid);
                        } else {
                            const m = (window.location.href || '').match(/\\/c\\/([a-zA-Z0-9_-]+)/);
                            if (m && m[1]) running.add(m[1]);
                        }
                    }
                    return Array.from(running);
                })()
                """
                r = await _cdp_call(ws, 405, "Runtime.evaluate", {"expression": js, "returnByValue": True}, timeout=1.5)
                val = r.get("result", {}).get("result", {}).get("value", [])
                return set(val) if isinstance(val, list) else set()
    except Exception as e:
        logger.debug(f"CDP running conversations check failed: {e}")
        return set()


async def get_queued_messages_cdp(conv_id=None, target=None, port=None) -> list:
    """Extracts queued messages directly from Antigravity's [data-testid='queued-messages-card'] DOM."""
    try:
        if not target:
            target = await find_antigravity_target(port)
        if not target:
            return []
        url = target.get("url", "")
        if conv_id and conv_id not in ("active", "new") and f"/c/{conv_id}" not in url:
            return []
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            return []
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=2.0)) as ws:
                js = """
                (() => {
                    const card = document.querySelector('[data-testid="queued-messages-card"]');
                    if (!card) return [];
                    const decorators = Array.from(card.querySelectorAll('[data-testid="queued-decorators"]'));
                    return decorators.map((dec, idx) => {
                        const row = (dec.parentElement && dec.parentElement.parentElement) || dec.closest('div');
                        const left = row ? row.firstElementChild : null;
                        let text = '';
                        let hasMedia = false;
                        if (left) {
                            hasMedia = !!left.querySelector('img, video, [data-tooltip-id]');
                            const textSpan = left.querySelector('span.text-sm');
                            text = (textSpan ? textSpan.textContent : left.innerText || '').trim();
                            if (text === 'Empty message') text = '';
                        }
                        return {
                            index: idx,
                            text,
                            has_media: hasMedia,
                            can_edit: !!dec.querySelector('button[aria-label="Edit"]'),
                            can_delete: !!dec.querySelector('button[aria-label="Delete"]'),
                            can_send_now: !!dec.querySelector('button[aria-label="Send Now"]')
                        };
                    });
                })()
                """
                r = await _cdp_call(ws, 406, "Runtime.evaluate", {"expression": js, "returnByValue": True}, timeout=1.5)
                val = r.get("result", {}).get("result", {}).get("value", [])
                return val if isinstance(val, list) else []
    except Exception as e:
        logger.debug(f"CDP queued messages check failed: {e}")
        return []


async def queue_action_cdp(action: str, index: int, expected_text: str = "", new_text: str = "", conv_id: str = None, port=None):
    """Performs delete, send_now, or edit on a queued message inside Antigravity's QueuedMessagesCard via CDP."""
    action = (action or "").strip().lower()
    if action not in ("delete", "send_now", "edit"):
        return {"status": "error", "message": "Invalid queue action"}
    if conv_id and conv_id not in ("active", "new") and not is_valid_conv_id(conv_id):
        return {"status": "error", "message": "Invalid conversation ID"}
    if action == "edit" and not (new_text or "").strip():
        return {"status": "error", "message": "Edited message text cannot be empty"}

    ready, _ = await ensure_antigravity_ready(timeout=15)
    if not ready:
        return {"status": "error", "message": "Antigravity DevTools не отвечает"}

    target = await find_antigravity_target(port)
    if not target:
        return {"status": "error", "message": "Antigravity target not found"}

    if conv_id and conv_id not in ("active", "new") and f"/c/{conv_id}" not in target.get("url", ""):
        await switch_conversation(conv_id, port)
        await asyncio.sleep(0.7)
        target = await find_antigravity_target(port) or target

    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return {"status": "error", "message": "No webSocketDebuggerUrl available"}

    aria_map = {
        "delete": "Delete",
        "send_now": "Send Now",
        "edit": "Edit",
    }
    btn_label = aria_map[action]

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=8.0)) as ws:
                click_js = f"""
                (() => {{
                    const card = document.querySelector('[data-testid="queued-messages-card"]');
                    if (!card) return {{ ok: false, error: 'Очередь сообщений пуста' }};
                    const decorators = Array.from(card.querySelectorAll('[data-testid="queued-decorators"]'));
                    if (!decorators.length) return {{ ok: false, error: 'Очередь сообщений пуста' }};

                    const reqIndex = {int(index)};
                    const expected = {json.dumps((expected_text or '').strip())};
                    const btnLabel = {json.dumps(btn_label)};

                    const getText = (dec) => {{
                        const row = (dec.parentElement && dec.parentElement.parentElement) || dec.closest('div');
                        const left = row ? row.firstElementChild : null;
                        if (!left) return '';
                        const sp = left.querySelector('span.text-sm');
                        let t = (sp ? sp.textContent : left.innerText || '').trim();
                        return t === 'Empty message' ? '' : t;
                    }};

                    let chosenIdx = -1;
                    if (reqIndex >= 0 && reqIndex < decorators.length) {{
                        const candidateText = getText(decorators[reqIndex]);
                        if (!expected || candidateText === expected) {{
                            chosenIdx = reqIndex;
                        }}
                    }}
                    if (chosenIdx === -1 && expected) {{
                        chosenIdx = decorators.findIndex(d => getText(d) === expected);
                    }}
                    if (chosenIdx === -1 && !expected && reqIndex >= 0 && reqIndex < decorators.length) {{
                        chosenIdx = reqIndex;
                    }}
                    if (chosenIdx === -1) {{
                        return {{ ok: false, error: 'Сообщение уже отправлено или удалено из очереди' }};
                    }}

                    const targetDec = decorators[chosenIdx];
                    let btn = targetDec.querySelector(`button[aria-label="${{btnLabel}}"]`);
                    let fallbackDeleted = false;
                    if (!btn && btnLabel === 'Edit') {{
                        btn = targetDec.querySelector('button[aria-label="Delete"]');
                        fallbackDeleted = !!btn;
                    }}
                    if (!btn) {{
                        return {{ ok: false, error: `Кнопка ${{btnLabel}} недоступна` }};
                    }}
                    btn.click();
                    return {{ ok: true, index: chosenIdx, fallbackDeleted }};
                }})()
                """
                r = await _cdp_call(ws, 701, "Runtime.evaluate", {"expression": click_js, "returnByValue": True}, timeout=2.5)
                click_res = r.get("result", {}).get("result", {}).get("value", {})
                if not click_res.get("ok"):
                    return {"status": "error", "message": click_res.get("error", "Queue item action failed")}

                if action == "edit":
                    await asyncio.sleep(0.22)
                    clean_new = (new_text or "").strip()
                    # Focus composer and select-all existing content restored by Edit before replacing
                    clear_js = """
                    (() => {
                        const el = document.querySelector('[aria-label="Message input"]') ||
                                   document.querySelector('[contenteditable="true"]') ||
                                   document.querySelector('textarea');
                        if (!el) return { found: false };
                        el.focus();
                        el.click();
                        try {
                            document.execCommand('selectAll', false, null);
                        } catch (e) {}
                        return { found: true };
                    })()
                    """
                    rc = await _cdp_call(ws, 702, "Runtime.evaluate", {"expression": clear_js, "returnByValue": True}, timeout=2.0)
                    if not rc.get("result", {}).get("result", {}).get("value", {}).get("found"):
                        return {"status": "error", "message": "Message input not found during edit"}

                    if not click_res.get("fallbackDeleted"):
                        await _cdp_call(ws, 703, "Input.dispatchKeyEvent", {
                            "type": "keyDown", "windowsVirtualKeyCode": 8, "key": "Backspace", "code": "Backspace"
                        }, timeout=2.0)
                        await _cdp_call(ws, 704, "Input.dispatchKeyEvent", {
                            "type": "keyUp", "windowsVirtualKeyCode": 8, "key": "Backspace", "code": "Backspace"
                        }, timeout=2.0)

                    await _cdp_call(ws, 705, "Input.insertText", {"text": clean_new}, timeout=2.5)
                    await asyncio.sleep(0.08)
                    await _cdp_call(ws, 706, "Input.dispatchKeyEvent", {
                        "type": "keyDown", "windowsVirtualKeyCode": 13, "key": "Enter", "code": "Enter", "text": "\r"
                    }, timeout=2.0)
                    await _cdp_call(ws, 707, "Input.dispatchKeyEvent", {
                        "type": "keyUp", "windowsVirtualKeyCode": 13, "key": "Enter", "code": "Enter"
                    }, timeout=2.0)
                    submit_js = """
                    (() => {
                        const btns = Array.from(document.querySelectorAll('button'));
                        const sendBtn = btns.find(b =>
                            (b.getAttribute('aria-label') || '').toLowerCase().includes('send') ||
                            (b.getAttribute('title') || '').toLowerCase().includes('send')
                        );
                        if (sendBtn && !sendBtn.disabled) { sendBtn.click(); return true; }
                        return false;
                    })()
                    """
                    await _cdp_call(ws, 708, "Runtime.evaluate", {"expression": submit_js, "returnByValue": True}, timeout=2.0)

                await asyncio.sleep(0.28)
                updated_queue = await get_queued_messages_cdp(conv_id=conv_id, target=target, port=port)
                return {
                    "status": "ok",
                    "action": action,
                    "queued_messages": updated_queue
                }
    except Exception as e:
        logger.error(f"Queue action '{action}' failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


async def check_agent_active_status_cdp(conv_id=None, target=None, port=None):
    """
    Directly queries Antigravity DOM via Chrome DevTools Protocol:
    Checks if the specified conversation ID has an active Stop button.
    Returns (is_running: bool, current_action: str) strictly scoped to conv_id.
    """
    if not conv_id or conv_id == "new":
        return False, ""
    running_ids = await get_running_conversation_ids_cdp(target=target, port=port)
    return (conv_id in running_ids), ""

async def get_conversation_chat(conv_id=None, limit=50, port=None):
    """Asynchronously retrieves chat messages, queued messages, and truthful real-time status strictly for the requested conversation."""
    target = await find_antigravity_target(port)
    if not conv_id or conv_id == "active":
        if target:
            url = target.get("url", "")
            if "/c/" in url:
                conv_id = url.split("/c/")[1].split("?")[0].strip("/")
            else:
                conv_id = "new"
        if not conv_id:
            conv_id = await get_active_conversation_id(port)

    if not conv_id or conv_id == "new":
        return {
            "status": "ok",
            "conversation_id": "new",
            "agent_status": "idle",
            "current_action": "",
            "messages": [],
            "queued_messages": []
        }

    chat_data = await asyncio.to_thread(parse_conversation_history, conv_id, limit)
    running_ids = await get_running_conversation_ids_cdp(target=target, port=port)
    queued_messages = await get_queued_messages_cdp(conv_id=conv_id, target=target, port=port)
    is_live_running = (conv_id in running_ids)

    if is_live_running:
        if chat_data.get("agent_status") == "idle":
            chat_data["agent_status"] = "executing"
            if not chat_data.get("current_action"):
                chat_data["current_action"] = "Выполняет задачу..."
    else:
        # If this dialog does NOT have a live stop button, it is genuinely idle
        chat_data["agent_status"] = "idle"
        chat_data["current_action"] = ""

    chat_data["queued_messages"] = queued_messages
    return chat_data

async def attach_file_to_composer(ws, file_path):
    """Sets files on the Antigravity hidden file input via CDP DOM.setFileInputFiles."""
    try:
        await _cdp_call(ws, 301, "DOM.enable", timeout=2.5)
        r_doc = await _cdp_call(ws, 302, "DOM.getDocument", timeout=2.5)
        root_node_id = r_doc.get("result", {}).get("root", {}).get("nodeId")
        if not root_node_id:
            logger.warning("Could not get root nodeId from DOM")
            return False

        r_q = await _cdp_call(
            ws, 303, "DOM.querySelector",
            {"nodeId": root_node_id, "selector": 'input[type="file"]'},
            timeout=2.5
        )
        file_node_id = r_q.get("result", {}).get("nodeId")
        if not file_node_id:
            logger.warning("Could not find input[type='file'] in Antigravity DOM")
            return False

        abs_path = str(Path(file_path).resolve())
        await _cdp_call(
            ws, 304, "DOM.setFileInputFiles",
            {"files": [abs_path], "nodeId": file_node_id},
            timeout=3.0
        )
        logger.info(f"Successfully attached file via CDP: {abs_path}")

        # Wait 350ms for React/Chromium to render attachment chip
        await asyncio.sleep(0.35)
        return True
    except Exception as e:
        logger.error(f"Error attaching file via CDP: {e}", exc_info=True)
        return False

async def send_prompt_to_antigravity(prompt_text, image_path=None, conv_id=None, focus_window=False, port=None):
    """
    Injects the given prompt text (and optional image file) directly into Antigravity's active chat canvas
    and triggers prompt submission. Supports switching conversation or starting a new one.
    Automatically launches Antigravity if it is not currently running.
    """
    prompt_text = (prompt_text or "").strip()
    if not prompt_text and not image_path:
        return {"status": "error", "message": "Prompt text and image are both empty"}
    if conv_id and conv_id not in ("active", "new") and not is_valid_conv_id(conv_id):
        return {"status": "error", "message": "Invalid conversation ID"}

    # 1. Ensure Antigravity is running and connected via DevTools first!
    ready, launch_status = await ensure_antigravity_ready(timeout=25)
    if not ready:
        return {
            "status": "error",
            "message": "Antigravity не удалось запустить или подключиться по DevTools CDP."
        }

    target = await find_antigravity_target(port)
    if not target:
        return {
            "status": "error",
            "message": "Antigravity DevTools endpoint not found after ready check."
        }

    current_url = target.get("url", "")

    # 2. Handle conversation switching or new chat creation only if genuinely required
    if conv_id == "new":
        logger.info("Creating new conversation before sending prompt...")
        await create_new_conversation(port)
        await asyncio.sleep(0.8)
        target = await find_antigravity_target(port) or target
    elif conv_id:
        if f"/c/{conv_id}" in current_url:
            logger.info(f"Antigravity is ALREADY on conversation {conv_id}. Fast path!")
        else:
            logger.info(f"Switching conversation from {current_url} to {conv_id} before sending prompt...")
            await switch_conversation(conv_id, port)
            await asyncio.sleep(0.8)
            target = await find_antigravity_target(port) or target

    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return {"status": "error", "message": "No webSocketDebuggerUrl available for Antigravity"}

    if focus_window and not _is_game_mode_silent():
        focus_antigravity_window()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=12.0)) as ws:
                # If image is attached, set it first
                if image_path and os.path.exists(image_path):
                    logger.info(f"Attaching image to Antigravity composer: {image_path}")
                    attached = await attach_file_to_composer(ws, image_path)
                    if not attached:
                        logger.warning("Image attachment failed via CDP, will attempt text prompt...")

                # Step 1: Locate and focus the Message input element with retry
                focus_js = """
                (() => {
                    let el = document.querySelector('[aria-label="Message input"]');
                    if (!el) {
                        el = document.querySelector('[contenteditable="true"]') ||
                             document.querySelector('textarea');
                    }
                    if (el) {
                        el.focus();
                        el.click();
                        return { found: true };
                    }
                    return { found: false };
                })()
                """
                found = False
                for _ in range(8):
                    try:
                        reply = await _cdp_call(
                            ws, 101, "Runtime.evaluate",
                            {"expression": focus_js, "returnByValue": True},
                            timeout=2.0
                        )
                        found = bool(reply.get("result", {}).get("result", {}).get("value", {}).get("found", False))
                    except Exception:
                        found = False
                    if found:
                        break
                    await asyncio.sleep(0.25)

                if not found:
                    return {
                        "status": "error",
                        "message": "Message input element not found in Antigravity chat canvas."
                    }

                # Step 2: Insert text via CDP Input.insertText (if provided)
                if prompt_text:
                    await _cdp_call(ws, 102, "Input.insertText", {"text": prompt_text}, timeout=3.0)

                # Step 3: Trigger Enter key event to submit
                await asyncio.sleep(0.08)
                await _cdp_call(ws, 103, "Input.dispatchKeyEvent", {
                    "type": "keyDown",
                    "windowsVirtualKeyCode": 13,
                    "key": "Enter",
                    "code": "Enter",
                    "text": "\r"
                }, timeout=2.0)

                await _cdp_call(ws, 104, "Input.dispatchKeyEvent", {
                    "type": "keyUp",
                    "windowsVirtualKeyCode": 13,
                    "key": "Enter",
                    "code": "Enter"
                }, timeout=2.0)

                # Step 4: Click Send button if still present
                click_send_js = """
                (() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const sendBtn = btns.find(b => 
                        (b.getAttribute('aria-label') || '').toLowerCase().includes('send') ||
                        (b.getAttribute('title') || '').toLowerCase().includes('send')
                    );
                    if (sendBtn && !sendBtn.disabled) {
                        sendBtn.click();
                        return true;
                    }
                    return false;
                })()
                """
                await _cdp_call(ws, 105, "Runtime.evaluate", {"expression": click_send_js, "returnByValue": True}, timeout=2.0)

                await asyncio.sleep(0.2)
                queued = await get_queued_messages_cdp(conv_id=conv_id, target=target, port=port)
                return {
                    "status": "ok",
                    "message": "Prompt successfully delivered to Antigravity!",
                    "target_title": target.get("title", ""),
                    "image_attached": bool(image_path),
                    "queued_messages": queued
                }
    except Exception as e:
        logger.error(f"CDP injection failed: {e}", exc_info=True)
        return {"status": "error", "message": f"CDP error: {str(e)}"}

async def stop_agent_execution(conv_id=None, port=None):
    """Triggers the Stop button in Antigravity via CDP to immediately abort execution."""
    if conv_id and conv_id not in ("active", "new") and not is_valid_conv_id(conv_id):
        return {"status": "error", "message": "Invalid conversation ID"}
    target = await find_antigravity_target(port)
    if not target:
        return {"status": "error", "message": "Antigravity not found"}

    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return {"status": "error", "message": "No debugger URL"}

    cid = conv_id or ""
    cid_json = json.dumps(cid)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=2.5)) as ws:
                js = f"""
                (() => {{
                    const convId = {cid_json};

                    // 1. If convId is provided, first look for its sidebar row stop button
                    if (convId) {{
                        const row = document.querySelector(`[data-cascade-id="${{convId}}"], [data-conversation-id="${{convId}}"]`);
                        if (row) {{
                            const stopBtn = row.querySelector('button[aria-label="Stop execution"], [aria-label*="Stop"]');
                            if (stopBtn) {{
                                stopBtn.click();
                                return {{ clicked: true, method: 'sidebar_row', convId }};
                            }}
                        }}
                    }}

                    // 2. Check the active canvas stop button (only if canvas is for this convId or convId is empty)
                    const isCanvasForConv = !convId || (window.location.href || '').includes('/c/' + convId);
                    if (isCanvasForConv) {{
                        const canvasStop = document.querySelector('button[aria-label="Stop execution"], button[title="Stop execution"]');
                        if (canvasStop) {{
                            canvasStop.click();
                            return {{ clicked: true, method: 'canvas', convId }};
                        }}
                    }}

                    // 3. Fallback only if convId is NOT specified
                    if (!convId) {{
                        const allStops = Array.from(document.querySelectorAll('button[aria-label="Stop execution"], button[title="Stop execution"]'));
                        if (allStops.length > 0) {{
                            allStops[allStops.length - 1].click();
                            return {{ clicked: true, method: 'fallback_any' }};
                        }}
                    }}

                    return {{ clicked: false, message: "No active stop button found for conversation " + convId }};
                }})()
                """
                r = await _cdp_call(ws, 801, "Runtime.evaluate", {"expression": js, "returnByValue": True}, timeout=2.0)
                res = r.get("result", {}).get("result", {}).get("value", {})
                return {"status": "ok", "result": res}
    except Exception as e:
        logger.error(f"Error stopping agent via CDP: {e}")
        return {"status": "error", "message": str(e)}

async def capture_pc_screenshot():
    """Captures a screenshot of the primary display (or Antigravity workspace fallback if desktop is locked)."""
    # 1. Desktop capture via mss
    try:
        import mss
        from PIL import Image
        import io
        import base64
        with mss.mss() as sct:
            mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            sct_img = sct.grab(mon)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            if img.width > 1920:
                img.thumbnail((1920, 1080), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=82)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{b64}", "desktop"
    except Exception as e:
        logger.debug(f"Desktop mss capture failed: {e}")

    # 2. CDP fallback (captures workspace window even when desktop is locked)
    try:
        target = await find_antigravity_target()
        if target:
            ws_url = target.get("webSocketDebuggerUrl")
            if ws_url:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=2.5)) as ws:
                        r = await _cdp_call(ws, 901, "Page.captureScreenshot", {"format": "jpeg", "quality": 82}, timeout=2.5)
                        raw_b64 = r.get("result", {}).get("data", "")
                        return f"data:image/jpeg;base64,{raw_b64}", "cdp_workspace"
    except Exception as e:
        logger.debug(f"CDP capture fallback failed: {e}")

    return None, "failed"

_gpu_telemetry_cache = {"ts": 0.0, "data": None}

def get_pc_telemetry():
    """Gathers real-time CPU, RAM, disk space, and GPU metrics (throttles nvidia-smi during Game Mode)."""
    import psutil
    import subprocess

    cpu_pct = psutil.cpu_percent(interval=None)
    v = psutil.virtual_memory()
    d = psutil.disk_usage("C:\\")

    now = time.time()
    gpu_ttl = 12.0 if _is_game_mode_silent() else 3.0
    gpu_info = _gpu_telemetry_cache["data"]
    if (now - _gpu_telemetry_cache["ts"]) >= gpu_ttl:
        fresh_gpu = None
        try:
            no_win = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL,
                timeout=0.8,
                text=True,
                creationflags=no_win
            ).strip()
            if out:
                parts = [p.strip() for p in out.split(",")]
                if len(parts) >= 5:
                    fresh_gpu = {
                        "name": parts[0],
                        "temp_c": int(parts[1]),
                        "percent": int(parts[2]),
                        "vram_used_mb": int(parts[3]),
                        "vram_total_mb": int(parts[4])
                    }
        except Exception:
            pass
        _gpu_telemetry_cache["ts"] = now
        if fresh_gpu is not None:
            _gpu_telemetry_cache["data"] = fresh_gpu
            gpu_info = fresh_gpu

    battery_info = None
    try:
        b = psutil.sensors_battery()
        if b is not None:
            battery_info = {
                "percent": int(b.percent),
                "power_plugged": bool(b.power_plugged),
                "secs_left": int(b.secsleft) if b.secsleft and b.secsleft > 0 else None
            }
    except Exception:
        pass

    return {
        "status": "ok",
        "cpu": {
            "percent": cpu_pct,
            "cores": psutil.cpu_count(logical=True)
        },
        "ram": {
            "percent": v.percent,
            "used_gb": round(v.used / (1024**3), 1),
            "total_gb": round(v.total / (1024**3), 1)
        },
        "disk": {
            "free_gb": round(d.free / (1024**3), 1),
            "total_gb": round(d.total / (1024**3), 1),
            "percent": d.percent
        },
        "gpu": gpu_info,
        "battery": battery_info
    }

# --- ANTIGRAVITY QUOTA & SUBSCRIPTION PLAN EXTRACTION ---
_quota_cache = None
_quota_cache_time = 0

def parse_antigravity_quota_text(raw_text):
    data = {
        "status": "ok",
        "plan": "Google AI Pro",
        "user_name": "",
        "user_email": "",
        "credits": 0,
        "gemini": {
            "weekly_percent": 100,
            "weekly_desc": "",
            "five_hour_percent": 100,
            "five_hour_desc": ""
        },
        "claude_gpt": {
            "weekly_percent": 100,
            "five_hour_percent": 100
        },
        "timestamp": int(time.time())
    }

    # Plan
    m_plan = re.search(r'Your Plan:\s*([^\n\r]+)', raw_text, re.IGNORECASE)
    if m_plan:
        plan_line = m_plan.group(1).strip()
        plan_line = re.split(r'You can upgrade|Upgrade', plan_line)[0].strip()
        data["plan"] = plan_line or "Google AI Pro"

    # User & email
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    for idx, l in enumerate(lines):
        if '@' in l and '.' in l and idx > 0:
            data["user_email"] = l
            data["user_name"] = lines[idx - 1]
            break

    # Credits
    m_credits = re.search(r'Available AI Credits:\s*(\d+)', raw_text, re.IGNORECASE)
    if m_credits:
        data["credits"] = int(m_credits.group(1))

    # Gemini Models section
    gemini_part = ""
    if "Gemini Models" in raw_text:
        gemini_part = raw_text.split("Gemini Models", 1)[1]
        if "Claude and GPT" in gemini_part:
            gemini_part = gemini_part.split("Claude and GPT", 1)[0]

    m_gw = re.search(r'Weekly Limit Remaining\s*(?:[^\n\r%]*\n)?\s*(\d+)%', gemini_part, re.IGNORECASE)
    if m_gw:
        data["gemini"]["weekly_percent"] = int(m_gw.group(1))
    m_gw_refresh = re.search(r'refresh in ([^\n\r.]+)', gemini_part, re.IGNORECASE)
    if m_gw_refresh:
        data["gemini"]["weekly_desc"] = f"Обновление через {m_gw_refresh.group(1).strip()}"

    m_g5 = re.search(r'Five Hour Limit Remaining\s*(?:[^\n\r%]*\n)?\s*(\d+)%', gemini_part, re.IGNORECASE)
    if m_g5:
        data["gemini"]["five_hour_percent"] = int(m_g5.group(1))
    all_refreshes = re.findall(r'refresh in ([^\n\r.]+)', gemini_part, re.IGNORECASE)
    if len(all_refreshes) > 1:
        data["gemini"]["five_hour_desc"] = f"Обновление через {all_refreshes[1].strip()}"

    # Claude & GPT section
    if "Claude and GPT" in raw_text:
        cg_part = raw_text.split("Claude and GPT", 1)[1]
        m_cw = re.search(r'Weekly Limit Remaining\s*(?:[^\n\r%]*\n)?\s*(\d+)%', cg_part, re.IGNORECASE)
        if m_cw:
            data["claude_gpt"]["weekly_percent"] = int(m_cw.group(1))
        m_c5 = re.search(r'Five Hour Limit Remaining\s*(?:[^\n\r%]*\n)?\s*(\d+)%', cg_part, re.IGNORECASE)
        if m_c5:
            data["claude_gpt"]["five_hour_percent"] = int(m_c5.group(1))

    return data

def clear_quota_cache():
    """Clears cached quota snapshot (e.g. after switching accounts)."""
    global _quota_cache, _quota_cache_time
    _quota_cache = None
    _quota_cache_time = 0

async def get_antigravity_quota_and_plan(port=None, force=False, force_refresh=False):
    """
    Fetches Antigravity's current Plan (e.g. Google AI Pro) and real-time model quotas
    (Gemini 5-hour limit, weekly limit, Claude/GPT limits, credits) via CDP.
    """
    global _quota_cache, _quota_cache_time
    force = force or force_refresh
    now = time.time()
    if not force and _quota_cache and (now - _quota_cache_time < 25):
        return _quota_cache

    target = await find_antigravity_target(port)
    if not target or not target.get("webSocketDebuggerUrl"):
        if _quota_cache:
            return _quota_cache
        return {"status": "error", "message": "Antigravity DevTools not reachable"}

    ws_url = target["webSocketDebuggerUrl"]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=8)) as ws:
                script = """(async () => {
                    let wasOpen = !!document.querySelector('[role="dialog"]');
                    if (!wasOpen) {
                        const settingsBtn = Array.from(document.querySelectorAll('button')).find(b => b.innerText && b.innerText.trim() === 'Settings');
                        if (!settingsBtn) return { error: 'Settings button not found' };
                        settingsBtn.click();
                        await new Promise(r => setTimeout(r, 200));
                    }
                    
                    const dialog = document.querySelector('[role="dialog"]');
                    if (!dialog) return { error: 'Settings dialog did not open' };
                    
                    const modelsBtn = Array.from(dialog.querySelectorAll('button')).find(b => b.innerText && b.innerText.trim() === 'Models');
                    if (modelsBtn) {
                        modelsBtn.click();
                        await new Promise(r => setTimeout(r, 200));
                    }
                    
                    const dialogText = dialog.innerText || '';
                    
                    if (!wasOpen) {
                        const closeBtn = dialog.querySelector('button.agy-button-icon-lg, button[aria-label="Close"]');
                        if (closeBtn) closeBtn.click();
                        else {
                            document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }));
                        }
                    }
                    
                    return { ok: true, raw: dialogText };
                })()"""
                resp = await _cdp_call(
                    ws, 99, "Runtime.evaluate",
                    {"expression": script, "awaitPromise": True, "returnByValue": True},
                    timeout=5.0
                )
                data = resp.get('result', {}).get('result', {}).get('value', {})
                raw_text = data.get('raw', '')
                if not raw_text:
                    if _quota_cache:
                        return _quota_cache
                    return {"status": "error", "message": data.get("error", "No quota text found")}

                parsed = parse_antigravity_quota_text(raw_text)
                _quota_cache = parsed
                _quota_cache_time = now
                return parsed
    except Exception as e:
        logger.warning(f"Error fetching quota via CDP: {e}")
        if _quota_cache:
            return _quota_cache
        return {"status": "error", "message": str(e)}
