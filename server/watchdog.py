"""
PC Remote Watchdog & Supervisor.
Keeps server.py and cloudflared tunnel running 24/7.
Ensures single-instance watchdog and auto-restarts server if it crashes.
"""

import os
import sys
import time
import socket
import subprocess
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent
SERVER_SCRIPT = SERVER_DIR / "server.py"
LOG_FILE = SERVER_DIR / "watchdog.log"
LOCK_PORT = 28765  # Dedicated local port to ensure only ONE watchdog runs at a time

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 2 * 1024 * 1024:
            backup = LOG_FILE.with_suffix(".log.1")
            try:
                if backup.exists():
                    backup.unlink()
                LOG_FILE.rename(backup)
            except Exception:
                pass
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def acquire_lock():
    """Binds a localhost socket to ensure only one watchdog runs."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        return s
    except Exception:
        return None

def free_port(port=8765):
    """Frees the given TCP listening port by terminating any non-self process bound to it."""
    try:
        import psutil
        for conn in psutil.net_connections(kind='inet'):
            if (
                conn.laddr
                and conn.laddr.port == port
                and conn.status == psutil.CONN_LISTEN
                and conn.pid
                and conn.pid != os.getpid()
            ):
                log(f"Port {port} is occupied by PID {conn.pid}. Terminating...")
                try:
                    p = psutil.Process(conn.pid)
                    p.terminate()
                    try:
                        p.wait(timeout=2)
                    except Exception:
                        p.kill()
                except Exception:
                    pass
    except Exception as e:
        log(f"Error freeing port {port}: {e}")

def kill_existing_server_processes():
    """Terminates orphan PC Remote server.py or cloudflared processes scoped strictly to SERVER_DIR."""
    free_port(8765)
    try:
        import psutil
        current_pid = os.getpid()
        target_script = str(SERVER_SCRIPT).lower()
        target_cf = str(SERVER_DIR / "cloudflared.exe").lower()
        for p in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if p.pid == current_pid:
                    continue
                name = (p.info.get('name') or '').lower()
                cmd = p.info.get('cmdline') or []
                cmd_str = " ".join(cmd).lower()
                if target_script in cmd_str or ("pc_remote" in cmd_str and "server.py" in cmd_str):
                    log(f"Killing orphan PC Remote server process PID {p.pid}...")
                    p.terminate()
                    try:
                        p.wait(timeout=2)
                    except Exception:
                        p.kill()
                elif "cloudflared" in name and (target_cf in cmd_str or "127.0.0.1:8765" in cmd_str):
                    log(f"Killing orphan PC Remote cloudflared process PID {p.pid}...")
                    p.terminate()
            except Exception:
                pass
    except Exception as e:
        log(f"Error checking orphan processes: {e}")

def run_supervisor():
    lock_sock = acquire_lock()
    if not lock_sock:
        log("Another watchdog instance is already active. Exiting duplicate.")
        return

    log("=== PC Remote Watchdog Supervisor Started (Single Instance) ===")
    log(f"Target script: {SERVER_SCRIPT}")

    # Ensure power settings are maintained (silently without console flash)
    no_win = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        subprocess.run(["powercfg", "/change", "standby-timeout-ac", "0"], capture_output=True, creationflags=no_win)
        subprocess.run(["powercfg", "/change", "hibernate-timeout-ac", "0"], capture_output=True, creationflags=no_win)
    except Exception:
        pass

    py_exe = sys.executable

    while True:
        try:
            kill_existing_server_processes()
            time.sleep(1)

            log(f"Launching server: {py_exe} {SERVER_SCRIPT.name}...")
            err_file = open(SERVER_DIR / "server_stdout.log", "w", encoding="utf-8")
            si = subprocess.STARTUPINFO()
            if os.name == "nt":
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 1
            proc = subprocess.Popen(
                [py_exe, str(SERVER_SCRIPT)],
                cwd=str(SERVER_DIR),
                stdout=err_file,
                stderr=subprocess.STDOUT,
                startupinfo=si if os.name == "nt" else None,
                creationflags=no_win
            )

            # Supervise process AND ensure localhost:8765 does not silently stall as a zombie
            start_ts = time.time()
            dead_port_strikes = 0
            while proc.poll() is None:
                time.sleep(25)
                if proc.poll() is not None:
                    break
                if time.time() - start_ts < 30.0:
                    continue
                try:
                    with socket.create_connection(("127.0.0.1", 8765), timeout=3.0):
                        dead_port_strikes = 0
                except OSError:
                    dead_port_strikes += 1
                    log(f"Health probe failed on 127.0.0.1:8765 ({dead_port_strikes}/3)")
                    if dead_port_strikes >= 3:
                        log("Server port 8765 hung silently while process alive; forcing restart...")
                        try:
                            proc.terminate()
                            proc.wait(timeout=3)
                        except Exception:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                        break

            code = proc.wait()
            err_file.close()
            try:
                out_content = (SERVER_DIR / "server_stdout.log").read_text(encoding="utf-8", errors="replace").strip()
                if out_content:
                    log(f"Server output on exit:\n{out_content}")
            except Exception:
                pass
            log(f"Server exited with code {code}. Restarting in 3 seconds...")
        except Exception as e:
            log(f"Error in watchdog loop: {e}")

        time.sleep(3)

if __name__ == "__main__":
    run_supervisor()
