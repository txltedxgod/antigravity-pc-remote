import os
import re
import sys
import io
import time
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

_tunnel_process = None
_current_url = None
_lock = threading.Lock()

def _publish_url_to_github(url):
    try:
        repo_dir = SERVER_DIR.parent
        subprocess.run(["git", "add", "server/tunnel_url.txt"], cwd=str(repo_dir), capture_output=True, timeout=5)
        subprocess.run(["git", "commit", "-m", "chore: auto-update cloudflare tunnel url"], cwd=str(repo_dir), capture_output=True, timeout=5)
        subprocess.run(["git", "push", "origin", "main"], cwd=str(repo_dir), capture_output=True, timeout=15)
        print("[Tunnel] Published new tunnel URL to GitHub for mobile auto-discovery!")
    except Exception as e:
        print(f"[Tunnel] Note on auto-publishing URL to GitHub: {e}")

def _notify_telegram(url):
    try:
        actions_py = Path(r"C:\Users\dev\.gemini\antigravity\scratch\tg_assistant\actions.py")
        if actions_py.exists():
            msg = (
                f"🟢 PC Remote онлайн на ПК!\n\n"
                f"🌐 Активный адрес туннеля:\n{url}\n\n"
                f"📱 Приложение на смартфоне подключается к нему автоматически через GitHub.\n"
                f"✈️ Удалённый доступ готов к поездке!"
            )
            subprocess.run(
                ["python", str(actions_py), "tg_send", "--to", "@trasherlvx", "--text", msg, "--account", "seocry"],
                timeout=25,
                capture_output=True
            )
            print("[Tunnel] Sent Telegram notification with active tunnel URL!")
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

def start_tunnel(local_port=8765):
    global _tunnel_process, _current_url
    if not CLOUDFLARED_EXE.exists():
        print(f"[Tunnel] cloudflared.exe not found at {CLOUDFLARED_EXE}")
        return None

    def runner():
        global _current_url, _tunnel_process
        cmd = [
            str(CLOUDFLARED_EXE),
            "tunnel",
            "--url", f"http://127.0.0.1:{local_port}",
            "--no-autoupdate"
        ]
        
        while True:
            try:
                print(f"[Tunnel] Launching Cloudflare Tunnel on port {local_port}...")
                _tunnel_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                )

                for line in iter(_tunnel_process.stdout.readline, ""):
                    # Look for trycloudflare.com URL
                    m = re.search(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com", line)
                    if m:
                        url = m.group(0)
                        with _lock:
                            _current_url = url
                        try:
                            URL_CACHE_FILE.write_text(url, encoding="utf-8")
                            threading.Thread(target=_publish_url_to_github, args=(url,), daemon=True).start()
                            threading.Thread(target=_notify_telegram, args=(url,), daemon=True).start()
                        except Exception:
                            pass
                        print(f"🔥 [Tunnel] Online! Global HTTPS URL: {url}")

                _tunnel_process.wait()
            except Exception as e:
                print(f"[Tunnel] Error in tunnel loop: {e}")

            print("[Tunnel] Tunnel process exited. Reconnecting in 5s...")
            time.sleep(5)

    t = threading.Thread(target=runner, daemon=True)
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
