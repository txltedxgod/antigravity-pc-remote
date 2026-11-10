"""
Battery Guard for Antigravity PC Remote
Monitors laptop AC power and battery level.
Sends emergency Telegram notifications to @trasherlvx if power is disconnected.
"""

import time
import threading
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("BatteryGuard")

TG_ASSISTANT_DIR = Path(r"C:\Users\dev\.gemini\antigravity\scratch\tg_assistant")
ACTIONS_PY = TG_ASSISTANT_DIR / "actions.py"

_alerted_unplugged = False
_alerted_20 = False
_alerted_10 = False
_running = False

def send_telegram_alert(text: str):
    """Sends a notification to Telegram @trasherlvx using seocry session."""
    if not ACTIONS_PY.exists():
        logger.warning(f"actions.py not found at {ACTIONS_PY}")
        return
    try:
        cmd = [
            "python", str(ACTIONS_PY), "tg_send",
            "--to", "@trasherlvx",
            "--text", text,
            "--account", "seocry"
        ]
        subprocess.Popen(cmd, cwd=str(TG_ASSISTANT_DIR), creationflags=0x08000000) # DETACHED
        logger.info(f"Telegram battery alert dispatched: {text[:60]}...")
    except Exception as e:
        logger.error(f"Failed to dispatch Telegram battery alert: {e}")

def battery_guard_loop():
    global _alerted_unplugged, _alerted_20, _alerted_10
    import psutil

    logger.info("Battery Guard monitoring thread started.")
    while _running:
        try:
            b = psutil.sensors_battery()
            if b is not None:
                percent = int(b.percent)
                plugged = bool(b.power_plugged)
                secs_left = b.secsleft if b.secsleft and b.secsleft > 0 else 0
                mins_left = round(secs_left / 60) if secs_left else None

                if not plugged:
                    # 1. Unplugged alert
                    if not _alerted_unplugged:
                        _alerted_unplugged = True
                        mins_txt = f" (~{mins_left} мин работы)" if mins_left else ""
                        send_telegram_alert(f"⚠️ ВНИМАНИЕ: Ноутбук отключен от сети 220V!\nЗаряд аккумулятора: {percent}%{mins_txt}.\nПроверь питание!")

                    # 2. Critical <= 20%
                    if percent <= 20 and not _alerted_20:
                        _alerted_20 = True
                        send_telegram_alert(f"🚨 НИЗКИЙ ЗАРЯД: Аккумулятор ноутбука {percent}%! Ноутбук скоро уснет или выключится.")

                    # 3. Emergency <= 10%
                    if percent <= 10 and not _alerted_10:
                        _alerted_10 = True
                        send_telegram_alert(f"🆘 КРИТИЧЕСКИЙ ЗАРЯД ({percent}%): Срочно включи питание ноута!")

                else:
                    # Power restored
                    if _alerted_unplugged or _alerted_20 or _alerted_10:
                        send_telegram_alert(f"⚡ Питание ноутбука восстановлено!\nСнова подключен к сети 220V (Заряд: {percent}%).")
                        _alerted_unplugged = False
                        _alerted_20 = False
                        _alerted_10 = False

        except Exception as e:
            logger.debug(f"Battery guard check error: {e}")

        time.sleep(45)

def start_battery_guard():
    global _running
    if _running:
        return
    _running = True
    t = threading.Thread(target=battery_guard_loop, daemon=True, name="BatteryGuardThread")
    t.start()
