"""
Antigravity IDE Bridge.
Communicates directly with Antigravity 2.0 Electron instance via Chrome DevTools Protocol (CDP)
and Win32 focus fallback to inject prompts and monitor agent status.
"""

import asyncio
import aiohttp
import json
import logging
import ctypes
import os
import re
from pathlib import Path
from ctypes import wintypes

logger = logging.getLogger("AntigravityBridge")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

DEFAULT_CDP_PORT = 60382
ANTIGRAVITY_EXE = r"C:\Users\dev\AppData\Local\Programs\antigravity\Antigravity.exe"
DEVTOOLS_PORT_FILE = os.path.expandvars(r"%APPDATA%\Antigravity\DevToolsActivePort")
BRAIN_DIR = Path(os.path.expandvars(r"%USERPROFILE%\.gemini\antigravity\brain"))

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

async def get_conversations_list(port=None):
    """Retrieves all active and recent conversations from Antigravity sidebar."""
    target = await find_antigravity_target(port)
    if not target:
        return {"status": "error", "message": "Antigravity not found", "conversations": []}

    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return {"status": "error", "message": "No CDP WebSocket URL", "conversations": []}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=4.0)) as ws:
                js = """
                (() => {
                    const links = Array.from(document.querySelectorAll('a[href*="/c/"]')).map(a => {
                        const row = a.closest('div[class*="group"]') || a.parentElement;
                        const text = row ? row.innerText.split('\\n')[0].trim() : '';
                        const href = a.getAttribute('href') || '';
                        const parts = href.split('/c/');
                        const id = parts.length > 1 ? parts[1].split('?')[0] : '';
                        return {
                            id: id,
                            title: text || 'Диалог ' + id.substring(0, 8),
                            isCurrent: window.location.href.includes(id)
                        };
                    }).filter(x => x.id && x.title);
                    return {
                        currentUrl: window.location.href,
                        conversations: links
                    };
                })()
                """
                await ws.send_str(json.dumps({
                    "id": 201,
                    "method": "Runtime.evaluate",
                    "params": {"expression": js, "returnByValue": True}
                }))
                reply = await ws.receive_json()
                data = reply.get("result", {}).get("result", {}).get("value", {})
                return {
                    "status": "ok",
                    "current_url": data.get("currentUrl", ""),
                    "conversations": data.get("conversations", [])
                }
    except Exception as e:
        logger.error(f"Error fetching conversations: {e}")
        return {"status": "error", "message": str(e), "conversations": []}

async def switch_conversation(conv_id, port=None):
    """Navigates Antigravity UI to the specified conversation ID."""
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
                        window.location.href = '/c/{conv_id}';
                        return {{ success: true, method: 'location' }};
                    }}
                }})()
                """
                await ws.send_str(json.dumps({
                    "id": 202,
                    "method": "Runtime.evaluate",
                    "params": {"expression": js, "returnByValue": True}
                }))
                reply = await ws.receive_json()
                await asyncio.sleep(1.2)
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
                reply = await ws.receive_json()
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
                # Active page in Antigravity has no /c/ -> explicitly in new blank conversation
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
                    messages.append({
                        "id": f"msg_u_{abs_index}",
                        "role": "user",
                        "text": clean_text,
                        "created_at": step.get("created_at", "")
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
            if ltype == "PLANNER_RESPONSE" and not last.get("content"):
                if last.get("tool_calls"):
                    agent_status = "executing"
                    tool = last["tool_calls"][0]
                    current_action = tool.get("args", {}).get("toolSummary", tool.get("name", "Команда"))
                else:
                    agent_status = "thinking"
                    current_action = "Размышляет..."
            elif ltype == "GENERIC":
                agent_status = "executing"
                current_action = "Обрабатывает результат..."

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

async def get_conversation_chat(conv_id=None, limit=50, port=None):
    """Asynchronously retrieves chat messages and real-time status for the conversation."""
    if not conv_id or conv_id == "active":
        conv_id = await get_active_conversation_id(port)
    if not conv_id or conv_id == "new":
        return {
            "status": "ok",
            "conversation_id": "new",
            "agent_status": "idle",
            "current_action": "",
            "messages": []
        }
    return parse_conversation_history(conv_id, limit=limit)

async def send_prompt_to_antigravity(prompt_text, conv_id=None, focus_window=False, port=None):
    """
    Injects the given prompt text directly into Antigravity's active chat canvas
    and triggers prompt submission. Supports switching conversation or starting a new one.
    """
    if not prompt_text or not prompt_text.strip():
        return {"status": "error", "message": "Prompt text is empty"}

    # Handle conversation switching or new chat creation first if requested
    if conv_id == "new":
        logger.info("Creating new conversation before sending prompt...")
        await create_new_conversation(port)
        await asyncio.sleep(1.0)
    elif conv_id:
        logger.info(f"Switching to conversation {conv_id} before sending prompt...")
        await switch_conversation(conv_id, port)
        await asyncio.sleep(1.0)

    prompt_text = prompt_text.strip()
    target = await find_antigravity_target(port)
    if not target:
        # Try to launch Antigravity and wait up to 12s
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
            async with session.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=5.0)) as ws:
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
                reply = await ws.receive_json()
                found = reply.get("result", {}).get("result", {}).get("value", {}).get("found", False)
                if not found:
                    return {
                        "status": "error",
                        "message": "Message input element not found in Antigravity chat canvas."
                    }

                # Step 2: Insert text via CDP Input.insertText (clean native IME injection)
                await ws.send_str(json.dumps({
                    "id": 102,
                    "method": "Input.insertText",
                    "params": {"text": prompt_text}
                }))
                await ws.receive_json()

                # Step 3: Trigger Enter key event to submit
                await asyncio.sleep(0.05)
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
                await ws.receive_json()

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
                await ws.receive_json()

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
                await ws.receive_json()

                return {
                    "status": "ok",
                    "message": "Prompt successfully delivered to Antigravity!",
                    "target_title": target.get("title", "")
                }
    except Exception as e:
        logger.error(f"CDP injection failed: {e}", exc_info=True)
        return {"status": "error", "message": f"CDP error: {str(e)}"}
