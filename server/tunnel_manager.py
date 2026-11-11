import atexit
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
import subprocess
import threading
from pathlib import Path

# Ensure utf-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SERVER_DIR = Path(__file__).resolve().parent
CLOUDFLARED_EXE = SERVER_DIR / "cloudflared.exe"
URL_CACHE_FILE = SERVER_DIR / "tunnel_url.txt"
BEACON_FILE = SERVER_DIR / ".tunnel_beacon.json"
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

_tunnel_process = None
_current_url = None
_url_started_at = None
_local_port = 8765
_stopping = False
_lock = threading.Lock()
_restart_lock = threading.Lock()
_restart_event = threading.Event()
_tunnel_thread = None
_restart_count = 0
_notify_reason = "startup"
_last_notified_url = None
_last_published_url = None
_cached_gh_token = None
_last_stalled_notify_ts = 0.0
_last_error = None


def stop_tunnel():
    """Stops the background Cloudflare tunnel process cleanly on shutdown."""
    global _stopping, _tunnel_process
    _stopping = True
    _restart_event.set()
    proc = _tunnel_process
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


atexit.register(stop_tunnel)


def _get_gh_token(force_refresh: bool = False) -> str:
    global _cached_gh_token
    if _cached_gh_token and not force_refresh:
        return _cached_gh_token
    try:
        res = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=_NO_WINDOW,
        )
        tok = (res.stdout or "").strip() if res.returncode == 0 else ""
        if tok:
            _cached_gh_token = tok
            return tok
    except Exception:
        pass
    return ""


def _publish_url_to_beacon(url: str) -> bool:
    """Publishes active tunnel URL into the private Secret GitHub Gist without git commits or CMD flashes."""
    if not BEACON_FILE.exists() or not url:
        return False
    try:
        beacon = json.loads(BEACON_FILE.read_text(encoding="utf-8"))
        gist_id = str(beacon.get("gist_id") or "").strip()
        if not gist_id:
            return False
    except Exception as e:
        print(f"[Tunnel] Beacon config read error: {e}")
        return False

    api_endpoint = f"https://api.github.com/gists/{gist_id}"
    payload = json.dumps({"files": {"tunnel_url.txt": {"content": url}}}).encode("utf-8")

    for attempt in range(3):
        if _stopping:
            return False
        tok = _get_gh_token(force_refresh=(attempt > 0))
        if not tok:
            return False
        try:
            req = urllib.request.Request(
                api_endpoint,
                data=payload,
                method="PATCH",
                headers={
                    "Authorization": f"Bearer {tok}",
                    "Accept": "application/vnd.github+json",
                    "Content-Type": "application/json",
                    "User-Agent": "PCRemoteServer/2.0",
                },
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                if 200 <= resp.status < 300:
                    print(f"[Tunnel] Published URL to private Secret Gist beacon ({gist_id[:8]}...)")
                    return True
        except urllib.error.HTTPError as he:
            print(f"[Tunnel] Beacon publish HTTP {he.code} (attempt {attempt + 1}/3)")
            if he.code == 401:
                _get_gh_token(force_refresh=True)
        except Exception as e:
            print(f"[Tunnel] Beacon publish note (attempt {attempt + 1}/3): {e}")
        time.sleep(3.0)
    return False


def _notify_telegram(url, reason="startup", detail=None):
    global _last_stalled_notify_ts
    try:
        actions_py = Path(r"C:\Users\dev\.gemini\antigravity\scratch\tg_assistant\actions.py")
        if actions_py.exists():
            now = time.time()
            # Throttle repeated tunnel_stalled alerts so Telegram never floods or triggers link-preview loops
            if reason == "tunnel_stalled" and (now - _last_stalled_notify_ts) < 300.0:
                print("[Tunnel] Skipped duplicate tunnel_stalled Telegram alert (cooldown active)")
                return
            if reason == "tunnel_stalled":
                _last_stalled_notify_ts = now

            if reason == "network_recovered":
                header = "🔄🟢 Связь ноутбука и туннель восстановлены!"
            elif reason == "tunnel_stalled":
                header = "🔄🚇 Cloudflare-туннель перезапущен (был недоступен)!"
            elif reason == "manual_restart":
                header = "🔄🌐 Cloudflare-туннель перезапущен вручную!"
            else:
                header = "🟢 PC Remote онлайн на ПК!"

            detail_block = f"\n📡 {detail}\n" if detail else "\n"
            msg = (
                f"{header}\n"
                f"{detail_block}"
                f"🌐 Активный адрес (откроется прямо в браузере):\n{url}\n\n"
                f"📱 Или открой APK — туннель работает без токенов."
            )
            py_bin = Path(sys.executable).with_name("python.exe")
            py_cmd = str(py_bin) if py_bin.exists() else "python"
            subprocess.run(
                [py_cmd, str(actions_py), "tg_send", "--to", "@trasherlvx", "--text", msg, "--account", "seocry"],
                timeout=25,
                capture_output=True,
                creationflags=_NO_WINDOW
            )
            print(f"[Tunnel] Sent Telegram notification ({reason}) with active tunnel URL!")
    except Exception as e:
        print(f"[Tunnel] Telegram notification note: {e}")


def get_current_tunnel_url():
    global _current_url
    with _lock:
        if _current_url:
            return _current_url
    if URL_CACHE_FILE.exists():
        try:
            return URL_CACHE_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return None


def get_tunnel_status():
    """Returns diagnostic status of the Cloudflare tunnel process."""
    with _lock:
        proc = _tunnel_process
        url = _current_url
        started_at = _url_started_at
        restarts = _restart_count
        last_err = _last_error
    if not url and URL_CACHE_FILE.exists():
        try:
            url = URL_CACHE_FILE.read_text(encoding="utf-8").strip() or None
        except Exception:
            pass
    return {
        "url": url,
        "running": bool(proc is not None and proc.poll() is None),
        "started_at": started_at,
        "restarts": restarts,
        "last_error": last_err,
    }


def _is_local_origin_alive(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2.0):
            return True
    except OSError:
        return False


def check_tunnel_reachable(timeout=10.0):
    """Performs an external end-to-end GET against <cloud_url>/api/ping after DNS warm-up."""
    with _lock:
        url = _current_url
        started_at = _url_started_at
        proc = _tunnel_process
        port = _local_port
    if not _is_local_origin_alive(port):
        return False, "local_server_down"
    if proc is None or proc.poll() is not None:
        return False, "process_not_running"
    if not url:
        return False, "no_url"
    if started_at and (time.time() - started_at) < 60.0:
        return True, "warming_up"
    try:
        req = urllib.request.Request(
            f"{url}/api/ping",
            headers={"User-Agent": "PCRemoteHealthCheck/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                return True, "ok"
            return False, f"http_{resp.status}"
    except Exception as e:
        return False, str(e)


def restart_tunnel(reason="manual_restart", detail=None):
    """Forces termination and immediate respawn of cloudflared.exe with a fresh URL."""
    global _current_url, _url_started_at, _restart_count, _notify_reason, _last_error
    if _stopping or not CLOUDFLARED_EXE.exists():
        return False
    with _restart_lock:
        with _lock:
            _notify_reason = reason
            _current_url = None
            _url_started_at = None
            _restart_count += 1
            _last_error = f"restarting ({reason})"
            if detail:
                _notify_detail_holder[0] = detail
            else:
                _notify_detail_holder[0] = None
            proc = _tunnel_process
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        _restart_event.set()
        if _tunnel_thread is None:
            start_tunnel(_local_port)
        return True


_notify_detail_holder = [None]


def start_tunnel(local_port=8765):
    global _tunnel_process, _current_url, _tunnel_thread, _local_port, _stopping
    _local_port = local_port
    _stopping = False
    if not CLOUDFLARED_EXE.exists():
        print(f"[Tunnel] cloudflared.exe not found at {CLOUDFLARED_EXE}")
        return None
    if _tunnel_thread is not None and _tunnel_thread.is_alive():
        return _tunnel_thread

    def runner():
        global _current_url, _url_started_at, _tunnel_process, _notify_reason, _last_notified_url, _last_published_url, _last_error
        cmd = [
            str(CLOUDFLARED_EXE),
            "tunnel",
            "--url", f"http://127.0.0.1:{local_port}",
            "--no-autoupdate"
        ]

        while not _stopping:
            _restart_event.clear()
            proc = None
            try:
                print(f"[Tunnel] Launching Cloudflare Tunnel on port {local_port}...")
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                )
                with _lock:
                    _tunnel_process = proc
                    _last_error = None

                for line in iter(proc.stdout.readline, ""):
                    if _stopping:
                        break
                    # Look for trycloudflare.com URL, excluding Cloudflare API endpoint
                    m = re.search(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com", line)
                    if m:
                        url = m.group(0)
                        if "api.trycloudflare.com" in url:
                            continue
                        with _lock:
                            _current_url = url
                            _url_started_at = time.time()
                            _last_error = None
                            reason_snapshot = _notify_reason
                            detail_snapshot = _notify_detail_holder[0]
                            should_notify = (url != _last_notified_url) or (reason_snapshot != "startup")
                            should_publish = (url != _last_published_url)
                            if should_notify:
                                _last_notified_url = url
                                _notify_reason = "startup"
                                _notify_detail_holder[0] = None
                            if should_publish:
                                _last_published_url = url
                        try:
                            URL_CACHE_FILE.write_text(url, encoding="utf-8")
                            if should_publish:
                                threading.Thread(
                                    target=_publish_url_to_beacon,
                                    args=(url,),
                                    daemon=True
                                ).start()
                            if should_notify:
                                threading.Thread(
                                    target=_notify_telegram,
                                    args=(url, reason_snapshot, detail_snapshot),
                                    daemon=True
                                ).start()
                        except Exception:
                            pass
                        print(f"🔥 [Tunnel] Online! Global HTTPS URL: {url}")

                code = proc.wait()
                with _lock:
                    if not _stopping:
                        _last_error = f"exited_code_{code}"
            except Exception as e:
                with _lock:
                    _last_error = str(e)
                print(f"[Tunnel] Error in tunnel loop: {e}")
            finally:
                if proc is not None and proc.poll() is None:
                    try:
                        proc.terminate()
                        proc.wait(timeout=2)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass

            if _stopping:
                break
            if _restart_event.is_set():
                print("[Tunnel] Immediate tunnel restart triggered...")
                _restart_event.clear()
                time.sleep(0.5)
            else:
                print("[Tunnel] Tunnel process exited. Reconnecting in 5s...")
                _restart_event.wait(timeout=5)

    t = threading.Thread(target=runner, daemon=True, name="CloudflareTunnelThread")
    _tunnel_thread = t
    t.start()
    return t

if __name__ == "__main__":
    start_tunnel(8765)
    print("Waiting for Cloudflare Tunnel URL...")
    for _ in range(30):
        time.sleep(1)
        url = get_current_tunnel_url()
        if url:
            print(f"\n=======================================================")
            print(f"✅ READY! Cloudflare Tunnel URL: {url}")
            print(f"=======================================================")
            break
