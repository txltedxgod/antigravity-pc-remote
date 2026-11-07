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
from ctypes import wintypes

logger = logging.getLogger("AntigravityBridge")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

DEFAULT_CDP_PORT = 60382
ANTIGRAVITY_EXE = r"C:\Users\dev\AppData\Local\Programs\antigravity\Antigravity.exe"

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

async def find_antigravity_target(port=DEFAULT_CDP_PORT):
    """Finds the active Antigravity conversation tab via DevTools HTTP endpoints."""
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

async def get_conversations_list(port=DEFAULT_CDP_PORT):
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

async def switch_conversation(conv_id, port=DEFAULT_CDP_PORT):
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

async def create_new_conversation(port=DEFAULT_CDP_PORT):
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
                    const btn = document.querySelector('button[aria-label="New Conversation"]');
                    if (btn) {
                        btn.click();
                        return { success: true, method: 'btn_click' };
                    } else {
                        window.location.href = '/';
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
                await asyncio.sleep(1.2)
                return {"status": "ok", "message": "New conversation initiated"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def get_antigravity_status(port=DEFAULT_CDP_PORT):
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

async def send_prompt_to_antigravity(prompt_text, conv_id=None, focus_window=False, port=DEFAULT_CDP_PORT):
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
