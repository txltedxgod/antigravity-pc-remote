"""
Antigravity IDE Bridge.
Communicates directly with Antigravity 2.0 Electron instance via Chrome DevTools Protocol (CDP)
and Win32 focus fallback to inject prompts, attach screenshot files, and monitor truthful agent status.
"""

import asyncio
import aiohttp
import json
import logging
import ctypes
import os
import re
import sqlite3
from pathlib import Path
from ctypes import wintypes

logger = logging.getLogger("AntigravityBridge")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

DEFAULT_CDP_PORT = 60382
ANTIGRAVITY_EXE = r"C:\Users\dev\AppData\Local\Programs\antigravity\Antigravity.exe"
DEVTOOLS_PORT_FILE = os.path.expandvars(r"%APPDATA%\Antigravity\DevToolsActivePort")
BRAIN_DIR = Path(os.path.expandvars(r"%USERPROFILE%\.gemini\antigravity\brain"))
ANTIGRAVITY_DIR = BRAIN_DIR.parent

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

def ensure_antigravity_running():
    """Launches Antigravity desktop executable if not currently running."""
    try:
        import subprocess
        out = subprocess.check_output("tasklist", shell=True).decode("utf-8", errors="ignore")
        if "antigravity.exe" in out.lower() or "antigravity" in out.lower():
            return True
        if os.path.exists(ANTIGRAVITY_EXE):
            logger.info("Antigravity not running. Launching automatically...")
            subprocess.Popen([ANTIGRAVITY_EXE], shell=True)
            return True
    except Exception as e:
        logger.error(f"Error checking/launching Antigravity: {e}")
    return False

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
                    # Priority 2: Any Antigravity title
                    for p in pages:
                        if "antigravity" in p.get("title", "").lower() and p.get("type") == "page":
                            return p
                    # Priority 3: First page
                    if pages:
                        return pages[0]
    except Exception as e:
        logger.debug(f"Failed to query CDP on port {port}: {e}")
    return None

def focus_antigravity_window():
    """Brings the Antigravity desktop window to foreground on user desktop."""
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
    return False

import time

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
                    async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=1.2)) as ws:
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
                        await ws.send_str(json.dumps({
                            "id": 501,
                            "method": "Runtime.evaluate",
                            "params": {"expression": js, "returnByValue": True}
                        }))
                        while True:
                            r = await ws.receive_json()
                            if r.get("id") == 501:
                                val = r.get("result", {}).get("result", {}).get("value", [])
                                if isinstance(val, list):
                                    cdp_pinned = set(val)
                                break
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
    if not conv_id:
        return {"status": "error", "message": "Missing conversation ID"}

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
                    async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=1.2)) as ws:
                        js = f"""
                        (() => {{
                            const row = document.querySelector('[data-cascade-id="{conv_id}"]');
                            if (!row) return {{ found: false }};
                            const btn = row.querySelector('button[aria-label="Pin conversation"], button[aria-label="Unpin conversation"]');
                            if (btn) {{
                                btn.click();
                                return {{ found: true, clicked: true }};
                            }}
                            return {{ found: true, clicked: false }};
                        }})()
                        """
                        await ws.send_str(json.dumps({
                            "id": 502,
                            "method": "Runtime.evaluate",
                            "params": {"expression": js, "returnByValue": True}
                        }))
                        while True:
                            r = await ws.receive_json()
                            if r.get("id") == 502:
                                res = r.get("result", {}).get("result", {}).get("value", {})
                                cdp_triggered = res.get("clicked", False)
                                break
    except Exception as e:
        logger.debug(f"CDP pin toggle error: {e}")

    return {
        "status": "ok",
        "id": conv_id,
        "isPinned": new_state,
        "cdp_synced": cdp_triggered
    }

async def get_conversations_list(port=None):
    """Retrieves all active and historical conversations from brain/, marking pinned ones."""
    active_id = await get_active_conversation_id(port)
    pinned_ids = await get_pinned_conversation_ids(port)
    convs = get_all_brain_conversations()
    for c in convs:
        c["isCurrent"] = (c["id"] == active_id)
        c["isPinned"] = (c["id"] in pinned_ids)

    return {
        "status": "ok",
        "active_id": active_id,
        "total": len(convs),
        "pinned_count": len([c for c in convs if c.get("isPinned")]),
        "conversations": convs
    }

async def switch_conversation(conv_id, port=None):
    """Navigates Antigravity UI to the specified conversation ID while preserving project context."""
    if not conv_id:
        return {"status": "error", "message": "Missing conversation ID"}

    target = await find_antigravity_target(port)
    if not target:
        return {"status": "error", "message": "Antigravity not found"}

    ws_url = target.get("webSocketDebuggerUrl")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=4.0)) as ws:
                js = f"""
                (() => {{
                    const link = document.querySelector('a[href*="{conv_id}"]');
                    if (link) {{
                        link.click();
                        return {{ success: true, method: 'click' }};
                    }} else {{
                        const currentSearch = window.location.search || '';
                        window.location.href = '/c/{conv_id}' + currentSearch;
                        return {{ success: true, method: 'location' }};
                    }}
                }})()
                """
                await ws.send_str(json.dumps({
                    "id": 202,
                    "method": "Runtime.evaluate",
                    "params": {"expression": js, "returnByValue": True}
                }))
                while True:
                    reply = await ws.receive_json()
                    if reply.get("id") == 202:
                        break
                await asyncio.sleep(0.8)
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
                await ws.send_str(json.dumps({
                    "id": 203,
                    "method": "Runtime.evaluate",
                    "params": {"expression": js, "returnByValue": True}
                }))
                while True:
                    reply = await ws.receive_json()
                    if reply.get("id") == 203:
                        break
                await asyncio.sleep(1.0)
                return {"status": "ok", "message": "New conversation initiated"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def get_antigravity_status(port=None):
    """Returns the current connection status, active conversation title, and URL."""
    target = await find_antigravity_target(port)
    if not target:
        return {
            "connected": False,
            "title": "Not Connected",
            "url": "",
            "message": "Antigravity DevTools not reachable"
        }
    return {
        "connected": True,
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
    logs_dir = BRAIN_DIR / conv_id / ".system_generated" / "logs"
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
        with open(compact_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        
        recent_lines = all_lines[-500:] if len(all_lines) > 500 else all_lines
        start_index = len(all_lines) - len(recent_lines)

        parsed_steps = []
        for line in recent_lines:
            line = line.strip()
            if not line:
                continue
            try:
                parsed_steps.append(json.loads(line))
            except Exception:
                continue

        full_cache = {}
        def get_full_step(idx):
            if idx in full_cache:
                return full_cache[idx]
            if not full_path.exists():
                return None
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as ff:
                    for line_idx, line in enumerate(ff):
                        if line_idx == idx:
                            obj = json.loads(line.strip())
                            full_cache[idx] = obj
                            return obj
            except Exception:
                pass
            return None

        messages = []
        agent_status = "idle"
        current_action = ""

        for i, step in enumerate(parsed_steps):
            abs_index = start_index + i
            step_type = step.get("type")
            trunc = step.get("truncated_fields", [])

            if step_type == "USER_INPUT":
                raw_content = step.get("content", "")
                if "content" in trunc:
                    fstep = get_full_step(abs_index)
                    if fstep:
                        raw_content = fstep.get("content", raw_content)
                
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
                if "content" in trunc:
                    fstep = get_full_step(abs_index)
                    if fstep:
                        content = fstep.get("content", content)

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
            last = parsed_steps[-1]
            ltype = last.get("type")
            lstatus = last.get("status")

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

async def check_agent_active_status_cdp(conv_id=None, target=None, port=None):
    """
    Directly queries Antigravity DOM via Chrome DevTools Protocol:
    1. If conv_id provided: checks if the conversation row has button[aria-label="Stop execution"]
    2. Or if active canvas has button[aria-label="Stop execution"]
    Returns (is_running: bool, current_action: str) based on actual live DOM.
    """
    try:
        if not target:
            target = await find_antigravity_target(port)
        if not target:
            return False, ""
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            return False, ""
        cid = conv_id or ""
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=1.2)) as ws:
                js = f"""
                (() => {{
                    const convId = '{cid}';
                    if (convId) {{
                        const row = document.querySelector(`[data-cascade-id="${{convId}}"], [data-conversation-id="${{convId}}"]`);
                        if (row) {{
                            const stopInRow = row.querySelector('button[aria-label="Stop execution"], [aria-label*="Stop"]');
                            if (stopInRow) return {{ isRunning: true, source: 'sidebar_row' }};
                        }}
                    }}
                    const canvasStop = document.querySelector('button[aria-label="Stop execution"], button[title="Stop execution"]');
                    if (canvasStop) return {{ isRunning: true, source: 'canvas' }};

                    return {{ isRunning: false, source: 'none' }};
                }})()
                """
                await ws.send_str(json.dumps({
                    "id": 401,
                    "method": "Runtime.evaluate",
                    "params": {"expression": js, "returnByValue": True}
                }))
                while True:
                    r = await ws.receive_json()
                    if r.get("id") == 401:
                        val = r.get("result", {}).get("result", {}).get("value", {})
                        return val.get("isRunning", False), ""
    except Exception as e:
        logger.debug(f"CDP status check failed: {e}")
        return False, ""

async def get_conversation_chat(conv_id=None, limit=50, port=None):
    """Asynchronously retrieves chat messages and truthful real-time status for the conversation."""
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
            "messages": []
        }

    chat_data = parse_conversation_history(conv_id, limit=limit)
    is_live_running, _ = await check_agent_active_status_cdp(conv_id=conv_id, target=target, port=port)
    if is_live_running:
        if chat_data.get("agent_status") == "idle":
            chat_data["agent_status"] = "executing"
            if not chat_data.get("current_action"):
                chat_data["current_action"] = "Выполняет задачу..."
    else:
        chat_data["agent_status"] = "idle"
        chat_data["current_action"] = ""

    return chat_data

async def attach_file_to_composer(ws, file_path):
    """Sets files on the Antigravity hidden file input via CDP DOM.setFileInputFiles."""
    try:
        # Step 1: Enable DOM
        await ws.send_str(json.dumps({"id": 301, "method": "DOM.enable"}))
        while True:
            r = await ws.receive_json()
            if r.get("id") == 301:
                break
        
        # Step 2: Get Document
        await ws.send_str(json.dumps({"id": 302, "method": "DOM.getDocument"}))
        root_node_id = None
        while True:
            r = await ws.receive_json()
            if r.get("id") == 302:
                root_node_id = r.get("result", {}).get("root", {}).get("nodeId")
                break
        
        if not root_node_id:
            logger.warning("Could not get root nodeId from DOM")
            return False
            
        # Step 3: Query file input
        await ws.send_str(json.dumps({
            "id": 303,
            "method": "DOM.querySelector",
            "params": {"nodeId": root_node_id, "selector": 'input[type="file"]'}
        }))
        file_node_id = None
        while True:
            r = await ws.receive_json()
            if r.get("id") == 303:
                file_node_id = r.get("result", {}).get("nodeId")
                break
                
        if not file_node_id:
            logger.warning("Could not find input[type='file'] in Antigravity DOM")
            return False

        # Step 4: Set file path
        abs_path = str(Path(file_path).resolve())
        await ws.send_str(json.dumps({
            "id": 304,
            "method": "DOM.setFileInputFiles",
            "params": {"files": [abs_path], "nodeId": file_node_id}
        }))
        while True:
            r = await ws.receive_json()
            if r.get("id") == 304:
                logger.info(f"Successfully attached file via CDP: {abs_path}")
                break
        
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
    """
    prompt_text = (prompt_text or "").strip()
    if not prompt_text and not image_path:
        return {"status": "error", "message": "Prompt text and image are both empty"}

    # Handle conversation switching or new chat creation first if requested
    if conv_id == "new":
        logger.info("Creating new conversation before sending prompt...")
        await create_new_conversation(port)
        await asyncio.sleep(1.0)
    elif conv_id:
        logger.info(f"Switching to conversation {conv_id} before sending prompt...")
        await switch_conversation(conv_id, port)
        await asyncio.sleep(1.0)

    target = await find_antigravity_target(port)
    if not target:
        ensure_antigravity_running()
        for attempt in range(12):
            await asyncio.sleep(1.0)
            target = await find_antigravity_target(port)
            if target:
                logger.info(f"Antigravity became ready after {attempt + 1}s!")
                break

    if not target:
        return {
            "status": "error",
            "message": "Antigravity DevTools endpoint not found. Make sure Antigravity is running."
        }

    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return {"status": "error", "message": "No webSocketDebuggerUrl available for Antigravity"}

    if focus_window:
        focus_antigravity_window()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=8.0)) as ws:
                # If image is attached, set it first
                if image_path and os.path.exists(image_path):
                    logger.info(f"Attaching image to Antigravity composer: {image_path}")
                    attached = await attach_file_to_composer(ws, image_path)
                    if not attached:
                        logger.warning("Image attachment failed via CDP, will attempt text prompt...")

                # Step 1: Locate and focus the Message input element
                focus_js = """
                (() => {
                    const el = document.querySelector('[aria-label="Message input"]');
                    if (el) {
                        el.focus();
                        return { found: true };
                    }
                    return { found: false };
                })()
                """
                await ws.send_str(json.dumps({
                    "id": 101,
                    "method": "Runtime.evaluate",
                    "params": {"expression": focus_js, "returnByValue": True}
                }))
                while True:
                    reply = await ws.receive_json()
                    if reply.get("id") == 101:
                        found = reply.get("result", {}).get("result", {}).get("value", {}).get("found", False)
                        break

                if not found:
                    return {
                        "status": "error",
                        "message": "Message input element not found in Antigravity chat canvas."
                    }

                # Step 2: Insert text via CDP Input.insertText (if provided)
                if prompt_text:
                    await ws.send_str(json.dumps({
                        "id": 102,
                        "method": "Input.insertText",
                        "params": {"text": prompt_text}
                    }))
                    while True:
                        r = await ws.receive_json()
                        if r.get("id") == 102:
                            break

                # Step 3: Trigger Enter key event to submit
                await asyncio.sleep(0.08)
                await ws.send_str(json.dumps({
                    "id": 103,
                    "method": "Input.dispatchKeyEvent",
                    "params": {
                        "type": "keyDown",
                        "windowsVirtualKeyCode": 13,
                        "key": "Enter",
                        "code": "Enter",
                        "text": "\r"
                    }
                }))
                while True:
                    r = await ws.receive_json()
                    if r.get("id") == 103:
                        break

                await ws.send_str(json.dumps({
                    "id": 104,
                    "method": "Input.dispatchKeyEvent",
                    "params": {
                        "type": "keyUp",
                        "windowsVirtualKeyCode": 13,
                        "key": "Enter",
                        "code": "Enter"
                    }
                }))
                while True:
                    r = await ws.receive_json()
                    if r.get("id") == 104:
                        break

                # Step 4: Click Send button if still present
                click_send_js = """
                (() => {
                    const btns = Array.from(document.querySelectorAll('button'));
                    const sendBtn = btns.find(b => 
                        (b.getAttribute('aria-label') || '').toLowerCase().includes('send') ||
                        (b.getAttribute('title') || '').toLowerCase().includes('send')
                    );
                    if (sendBtn) {
                        sendBtn.click();
                        return true;
                    }
                    return false;
                })()
                """
                await ws.send_str(json.dumps({
                    "id": 105,
                    "method": "Runtime.evaluate",
                    "params": {"expression": click_send_js, "returnByValue": True}
                }))
                while True:
                    r = await ws.receive_json()
                    if r.get("id") == 105:
                        break

                return {
                    "status": "ok",
                    "message": "Prompt successfully delivered to Antigravity!",
                    "target_title": target.get("title", ""),
                    "image_attached": bool(image_path)
                }
    except Exception as e:
        logger.error(f"CDP injection failed: {e}", exc_info=True)
        return {"status": "error", "message": f"CDP error: {str(e)}"}

async def stop_agent_execution(conv_id=None, port=None):
    """Triggers the Stop button in Antigravity via CDP to immediately abort execution."""
    target = await find_antigravity_target(port)
    if not target:
        return {"status": "error", "message": "Antigravity not found"}

    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return {"status": "error", "message": "No debugger URL"}

    cid = conv_id or ""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=2.0)) as ws:
                js = f"""
                (() => {{
                    const convId = '{cid}';

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

                    // 2. Check the active canvas stop button
                    const canvasStop = document.querySelector('button[aria-label="Stop execution"], button[title="Stop execution"]');
                    if (canvasStop) {{
                        canvasStop.click();
                        return {{ clicked: true, method: 'canvas', convId }};
                    }}

                    // 3. Any stop button in the document
                    const allStops = Array.from(document.querySelectorAll('button[aria-label="Stop execution"], [aria-label*="Stop"]'));
                    if (allStops.length > 0) {{
                        allStops[allStops.length - 1].click();
                        return {{ clicked: true, method: 'fallback_any', convId }};
                    }}

                    return {{ clicked: false, message: "No active stop button found in Antigravity" }};
                }})()
                """
                await ws.send_str(json.dumps({
                    "id": 801,
                    "method": "Runtime.evaluate",
                    "params": {"expression": js, "returnByValue": True}
                }))
                while True:
                    r = await ws.receive_json()
                    if r.get("id") == 801:
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
            mon = sct.monitors[1]
            sct_img = sct.grab(mon)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            img.thumbnail((1280, 720), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=70)
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
                    async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=2.0)) as ws:
                        await ws.send_str(json.dumps({
                            "id": 901,
                            "method": "Page.captureScreenshot",
                            "params": {"format": "jpeg", "quality": 70}
                        }))
                        while True:
                            r = await ws.receive_json()
                            if r.get("id") == 901:
                                raw_b64 = r.get("result", {}).get("data", "")
                                return f"data:image/jpeg;base64,{raw_b64}", "cdp_workspace"
    except Exception as e:
        logger.debug(f"CDP capture fallback failed: {e}")

    return None, "failed"

def get_pc_telemetry():
    """Gathers real-time CPU, RAM, disk space, and GPU metrics."""
    import psutil
    import subprocess

    cpu_pct = psutil.cpu_percent(interval=None)
    v = psutil.virtual_memory()
    d = psutil.disk_usage("C:\\")

    gpu_info = None
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
            timeout=0.8,
            text=True
        ).strip()
        if out:
            parts = [p.strip() for p in out.split(",")]
            if len(parts) >= 5:
                gpu_info = {
                    "name": parts[0],
                    "temp_c": int(parts[1]),
                    "percent": int(parts[2]),
                    "vram_used_mb": int(parts[3]),
                    "vram_total_mb": int(parts[4])
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
        "gpu": gpu_info
    }
