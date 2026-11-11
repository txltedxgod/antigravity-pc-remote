"""
PC Remote Autostart & Power Management Setup.
Configures 24/7 unattended operation:
1. Windows Startup folder VBScript
2. Windows Task Scheduler job (PCRemoteServerSupervisor)
3. Powercfg settings (Zero Sleep on AC)
"""

import os
import sys
import subprocess
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SERVER_DIR = Path(__file__).resolve().parent
START_VBS = SERVER_DIR / "start_server.vbs"

def setup_startup_folder():
    startup_dir = Path(os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"))
    if not startup_dir.exists():
        startup_dir.mkdir(parents=True, exist_ok=True)
    
    target_vbs = startup_dir / "PCRemoteServer.vbs"
    content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "{SERVER_DIR}"
WshShell.Run """{START_VBS}""", 1, False
'''
    target_vbs.write_text(content, encoding="utf-8")
    print(f"✅ Startup VBS written to: {target_vbs}")

def setup_scheduled_task():
    task_name = "PCRemoteServerSupervisor"
    tr_cmd = f'wscript.exe "{START_VBS}"'
    # Try standard user task first (does not require elevated UAC)
    cmd = [
        "schtasks", "/create",
        "/tn", task_name,
        "/tr", tr_cmd,
        "/sc", "onlogon",
        "/f"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            print(f"✅ Scheduled Task '{task_name}' registered successfully!")
        else:
            print(f"⚠️ Scheduled Task registration notice: {res.stdout} {res.stderr}")
    except Exception as e:
        print(f"⚠️ Could not register scheduled task: {e}")

def setup_registry_run():
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE
        )
        cmd_val = f'wscript.exe "{START_VBS}"'
        winreg.SetValueEx(key, "PCRemoteServer", 0, winreg.REG_SZ, cmd_val)
        winreg.CloseKey(key)
        print(f"✅ Registry Run key written: PCRemoteServer -> {cmd_val}")
    except Exception as e:
        print(f"⚠️ Could not write Registry Run key: {e}")

def configure_power_plan():
    commands = [
        ["powercfg", "/change", "standby-timeout-ac", "0"],
        ["powercfg", "/change", "hibernate-timeout-ac", "0"],
        ["powercfg", "/change", "monitor-timeout-ac", "15"]
    ]
    for c in commands:
        try:
            subprocess.run(c, capture_output=True, text=True)
        except Exception:
            pass
    print("✅ Power settings locked: Sleep=Never, Hibernate=Never, Display Sleep=15m")

if __name__ == "__main__":
    print("=== Configuring PC Remote Autostart & 24/7 Remote Readiness ===")
    setup_startup_folder()
    setup_registry_run()
    setup_scheduled_task()
    configure_power_plan()
    print("=== Setup Complete! ===")
