"""
Network Self-Healing Guard for Antigravity PC Remote.
Monitors real internet/DNS reachability and Cloudflare Tunnel health,
automatically runs a non-admin Windows recovery ladder (DNS flush, Wi-Fi reconnect,
DHCP renew, saved-profile failover) and restores Cloudflare Tunnel + Telegram alerts.
"""

import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

import tunnel_manager

logger = logging.getLogger("NetworkGuard")

SERVER_DIR = Path(__file__).resolve().parent
LAST_PROFILE_FILE = SERVER_DIR / "last_wifi_profile.txt"

_UNSAFE_WIFI_CHARS_RE = re.compile(r'[\x00-\x1f"&|;<>`^\\]')
_CREATION_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

_state_lock = threading.Lock()
_reconnect_lock = threading.Lock()

_running = False
_stop_event = threading.Event()
_auto_guard_enabled = True
_is_reconnecting = False
_consecutive_net_failures = 0
_consecutive_tunnel_failures = 0
_was_offline = False
_last_reconnect_ts = 0.0
_reconnect_attempts = 0
_last_event = "Мониторинг активен"
_last_recovery_ts = None

_last_conn_cache = {
    "online": True,
    "ip_reachable": True,
    "dns_ok": True,
    "latency_ms": None,
    "wifi_profile": None,
    "wifi_interface": "Wi-Fi",
    "lan_ip": None,
    "vpn_active": False,
    "checked_at": 0.0,
}

# Tunables
CHECK_INTERVAL_SEC = 15.0
FAILURES_BEFORE_RECOVERY = 3        # ~45s of sustained outage avoids false positives
TUNNEL_FAILURES_BEFORE_RESTART = 3  # ~3 min of verified dead tunnel edge while PC is online
AUTO_COOLDOWN_SEC = 60.0            # Minimum pause between full auto-recovery cycles


def _decode_win_bytes(raw: bytes) -> str:
    if not raw:
        return ""
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _run_cmd(args: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
    try:
        res = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            creationflags=_CREATION_FLAGS,
        )
        return (
            res.returncode,
            _decode_win_bytes(res.stdout).strip(),
            _decode_win_bytes(res.stderr).strip(),
        )
    except Exception as e:
        return -1, "", str(e)


def _read_last_good_profile() -> str | None:
    try:
        if LAST_PROFILE_FILE.exists():
            val = LAST_PROFILE_FILE.read_text(encoding="utf-8").strip()
            if val and not _UNSAFE_WIFI_CHARS_RE.search(val):
                return val
    except Exception:
        pass
    return None


def _save_last_good_profile(profile_name: str):
    if not profile_name or _UNSAFE_WIFI_CHARS_RE.search(profile_name):
        return
    try:
        current = _read_last_good_profile()
        if current != profile_name:
            LAST_PROFILE_FILE.write_text(profile_name, encoding="utf-8")
    except Exception:
        pass


def validate_wifi_profile_name(profile_name: str, allowed_profiles: list[str] | None = None) -> tuple[bool, str]:
    """Validates a Wi-Fi profile against unsafe characters and the PC's saved profile allowlist."""
    name = (profile_name or "").strip()
    if not name or len(name) > 64:
        return False, "Некорректное имя Wi-Fi профиля"
    if _UNSAFE_WIFI_CHARS_RE.search(name):
        return False, "Недопустимые символы в имени Wi-Fi профиля"
    profiles = allowed_profiles if allowed_profiles is not None else list_wifi_profiles()
    if profiles and name not in profiles:
        return False, f"Профиль '{name}' не найден среди сохранённых сетей на ПК"
    if not profiles:
        return False, "На ПК не найдено сохранённых Wi-Fi профилей"
    return True, name


def list_wifi_profiles() -> list[str]:
    """Returns saved Windows Wi-Fi profiles (works without Admin or Location permissions)."""
    rc, out, _ = _run_cmd(["netsh", "wlan", "show", "profiles"], timeout=5.0)
    if rc != 0 or not out:
        return []
    profiles: list[str] = []
    for line in out.splitlines():
        if ":" in line:
            left, right = line.split(":", 1)
            left_l = left.lower()
            if "profile" in left_l or "профил" in left_l:
                name = right.strip()
                if name and "<none>" not in name.lower() and not _UNSAFE_WIFI_CHARS_RE.search(name):
                    if name not in profiles:
                        profiles.append(name)
    # Put last known good profile first for priority visibility
    last_good = _read_last_good_profile()
    if last_good and last_good in profiles:
        profiles.remove(last_good)
        profiles.insert(0, last_good)
    return profiles


def get_active_connection_info() -> dict:
    """Queries Windows connection profiles & local interface states without Location/Admin elevation."""
    info = {
        "wifi_profile": None,
        "wifi_interface": "Wi-Fi",
        "lan_ip": None,
        "vpn_active": False,
        "ipv4_connectivity": None,
    }

    try:
        import psutil
        if_addrs = psutil.net_if_addrs()
        if_stats = psutil.net_if_stats()
        for iface, addrs in if_addrs.items():
            st = if_stats.get(iface)
            is_up = bool(st and st.isup)
            iface_l = iface.lower()
            for a in addrs:
                if a.family == socket.AF_INET and a.address and not a.address.startswith("127.") and not a.address.startswith("169.254."):
                    if "wi-fi" in iface_l or "wlan" in iface_l:
                        info["wifi_interface"] = iface
                        if is_up:
                            info["lan_ip"] = a.address
                    elif is_up and any(v in iface_l for v in ("nord", "wireguard", "openvpn", "wg", "tailscale", "vpn")):
                        info["vpn_active"] = True
                    elif is_up and not info["lan_ip"] and "vethernet" not in iface_l and "loopback" not in iface_l:
                        info["lan_ip"] = a.address
    except Exception:
        pass

    ps_script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        "Get-NetConnectionProfile | Select-Object Name,InterfaceAlias,IPv4Connectivity | ConvertTo-Json -Compress"
    )
    rc, out, _ = _run_cmd(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        timeout=5.0,
    )
    if rc == 0 and out:
        try:
            parsed = json.loads(out)
            items = parsed if isinstance(parsed, list) else ([parsed] if isinstance(parsed, dict) else [])
            best = None
            for item in items:
                alias = str(item.get("InterfaceAlias") or "").lower()
                if "wi-fi" in alias or "wlan" in alias:
                    best = item
                    break
            if not best and items:
                best = items[0]
            if best:
                name = str(best.get("Name") or "").strip()
                if name and not _UNSAFE_WIFI_CHARS_RE.search(name):
                    info["wifi_profile"] = name
                if best.get("InterfaceAlias"):
                    info["wifi_interface"] = str(best["InterfaceAlias"])
                info["ipv4_connectivity"] = best.get("IPv4Connectivity")
        except Exception:
            pass

    return info


def check_connectivity() -> dict:
    """Fast pure-socket TCP + DNS reachability probe (runs in ~20ms when online, <=3s when offline)."""
    ip_ok = False
    dns_ok = False
    best_ms = None

    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        t0 = time.time()
        try:
            with socket.create_connection((host, port), timeout=2.5):
                elapsed = round((time.time() - t0) * 1000, 1)
                ip_ok = True
                best_ms = elapsed if best_ms is None else min(best_ms, elapsed)
                break
        except OSError:
            continue

    if ip_ok:
        for domain in ("cloudflare.com", "google.com"):
            try:
                socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
                dns_ok = True
                break
            except OSError:
                continue

    online = bool(ip_ok and dns_ok)
    return {
        "online": online,
        "ip_reachable": ip_ok,
        "dns_ok": dns_ok,
        "latency_ms": best_ms,
    }


def _wait_for_online(max_wait_sec: float = 12.0, step_sec: float = 2.0) -> bool:
    deadline = time.time() + max_wait_sec
    while time.time() < deadline and not _stop_event.is_set():
        res = check_connectivity()
        if res["online"]:
            return True
        time.sleep(step_sec)
    return check_connectivity()["online"]


def _set_event(msg: str):
    global _last_event
    with _state_lock:
        _last_event = msg
    logger.info(f"[NetworkGuard] {msg}")


def _connect_wlan_profile(profile_name: str, interface_name: str = "Wi-Fi", disconnect_first: bool = True) -> bool:
    iface = interface_name or "Wi-Fi"
    if disconnect_first:
        _run_cmd(["netsh", "wlan", "disconnect", f"interface={iface}"], timeout=5.0)
        time.sleep(1.8)
    rc, _, err = _run_cmd(
        ["netsh", "wlan", "connect", f"name={profile_name}", f"interface={iface}"],
        timeout=8.0,
    )
    if rc != 0:
        logger.warning(f"netsh wlan connect '{profile_name}' failed (rc={rc}): {err}")
    return rc == 0


def _renew_dhcp_and_dns(interface_name: str = "Wi-Fi"):
    _run_cmd(["ipconfig", "/flushdns"], timeout=5.0)
    if interface_name:
        _run_cmd(["ipconfig", "/renew", interface_name], timeout=15.0)
    else:
        _run_cmd(["ipconfig", "/renew"], timeout=15.0)


def reconnect_network(reason: str = "manual", target_profile: str | None = None) -> dict:
    """
    Runs a non-admin Windows network recovery pipeline:
    1. If target_profile is set: validates allowlist -> switches Wi-Fi -> DHCP/DNS refresh -> restarts Cloudflare tunnel.
    2. Otherwise: DNS flush -> reconnect current/last-good Wi-Fi + DHCP renew -> fallback through saved profiles ->
       restore primary if none succeeded -> restart Cloudflare tunnel upon recovery.
    """
    global _is_reconnecting, _last_reconnect_ts, _reconnect_attempts, _consecutive_net_failures
    global _consecutive_tunnel_failures, _was_offline, _last_recovery_ts

    profiles = list_wifi_profiles()
    if target_profile is not None:
        ok, clean_or_err = validate_wifi_profile_name(target_profile, allowed_profiles=profiles)
        if not ok:
            return {"status": "error", "message": clean_or_err}
        target_profile = clean_or_err

    if not _reconnect_lock.acquire(blocking=False):
        return {"status": "busy", "message": "Восстановление сети уже выполняется"}

    try:
        with _state_lock:
            _is_reconnecting = True
            _last_reconnect_ts = time.time()
            _reconnect_attempts += 1

        conn_info = get_active_connection_info()
        iface = conn_info.get("wifi_interface") or "Wi-Fi"
        active_profile = conn_info.get("wifi_profile")
        last_good = _read_last_good_profile()
        primary_profile = target_profile or active_profile or last_good or (profiles[0] if profiles else None)

        # Target switch path (explicit profile switch)
        if target_profile:
            _set_event(f"Переключение Wi-Fi на '{target_profile}'...")
            _run_cmd(["ipconfig", "/flushdns"], timeout=5.0)
            _connect_wlan_profile(target_profile, iface, disconnect_first=True)
            online = _wait_for_online(max_wait_sec=12.0, step_sec=2.0)
            if not online:
                _set_event(f"Обновление DHCP на '{target_profile}'...")
                _renew_dhcp_and_dns(iface)
                online = _wait_for_online(max_wait_sec=8.0, step_sec=2.0)

            recovered_by = target_profile if online else None
        else:
            # Stage 1: Soft DNS flush + wait briefly if IP is alive but DNS broke
            _set_event("Шаг 1/3: Сброс DNS кэша (ipconfig /flushdns)...")
            _run_cmd(["ipconfig", "/flushdns"], timeout=5.0)
            pre_check = check_connectivity()
            online = pre_check["online"]
            recovered_by = primary_profile if online else None

            # Wait briefly to avoid disconnecting Wi-Fi if manual trigger only wanted refresh?
            # For explicit 'manual' reconnect, always refresh Wi-Fi + tunnel cleanly if requested
            if reason == "manual" or not online:
                if primary_profile:
                    _set_event(f"Шаг 2/3: Переподключение Wi-Fi '{primary_profile}' + DHCP...")
                    _connect_wlan_profile(primary_profile, iface, disconnect_first=True)
                    online = _wait_for_online(max_wait_sec=10.0, step_sec=2.0)
                    if not online:
                        _renew_dhcp_and_dns(iface)
                        online = _wait_for_online(max_wait_sec=8.0, step_sec=2.0)
                    if online:
                        recovered_by = primary_profile
                else:
                    _set_event("Шаг 2/3: Обновление DHCP/DNS (Wi-Fi профили не найдены)...")
                    _renew_dhcp_and_dns(iface)
                    online = _wait_for_online(max_wait_sec=8.0, step_sec=2.0)
                    if online:
                        recovered_by = active_profile

            # Stage 3: Auto-failover to backup saved Wi-Fi profiles (e.g. mobile hotspot / 2nd router)
            if not online and profiles:
                candidates = [p for p in profiles if p != primary_profile][:4]
                for backup in candidates:
                    if _stop_event.is_set():
                        break
                    _set_event(f"Шаг 3/3: Пробую резервный Wi-Fi '{backup}'...")
                    _connect_wlan_profile(backup, iface, disconnect_first=True)
                    if _wait_for_online(max_wait_sec=10.0, step_sec=2.0):
                        online = True
                        recovered_by = backup
                        break

                # If no backup profile brought internet back, re-arm primary Wi-Fi so Windows picks it up once router returns
                if not online and primary_profile:
                    _set_event(f"Все сети офлайн — возвращаю ожидание на '{primary_profile}'...")
                    _connect_wlan_profile(primary_profile, iface, disconnect_first=False)

        # Finalize state & restart Cloudflare Tunnel if internet is live
        final_conn = get_active_connection_info()
        if online:
            final_profile = recovered_by or final_conn.get("wifi_profile") or primary_profile
            if final_profile:
                _save_last_good_profile(final_profile)
            with _state_lock:
                _consecutive_net_failures = 0
                _consecutive_tunnel_failures = 0
                _was_offline = False
                _last_recovery_ts = time.time()
                _last_conn_cache.update({
                    "online": True,
                    "ip_reachable": True,
                    "dns_ok": True,
                    "wifi_profile": final_profile,
                    "wifi_interface": final_conn.get("wifi_interface") or iface,
                    "lan_ip": final_conn.get("lan_ip"),
                    "vpn_active": bool(final_conn.get("vpn_active")),
                    "checked_at": time.time(),
                })
            detail_msg = f"Сеть: {final_profile or 'LAN/Ethernet'} (режим: {reason})"
            _set_event(f"Интернет восстановлен ({final_profile or 'OK'}), перезапуск Cloudflare...")
            tunnel_manager.restart_tunnel(reason="network_recovered", detail=detail_msg)
            return {
                "status": "ok",
                "online": True,
                "profile": final_profile,
                "message": f"Сеть восстановлена ({final_profile or 'онлайн'}), туннель обновляется",
            }
        else:
            with _state_lock:
                _last_conn_cache.update({
                    "online": False,
                    "wifi_profile": final_conn.get("wifi_profile") or primary_profile,
                    "wifi_interface": final_conn.get("wifi_interface") or iface,
                    "lan_ip": final_conn.get("lan_ip"),
                    "vpn_active": bool(final_conn.get("vpn_active")),
                    "checked_at": time.time(),
                })
            _set_event("Не удалось восстановить интернет — повтор в следующем цикле")
            return {
                "status": "error",
                "online": False,
                "profile": final_conn.get("wifi_profile") or primary_profile,
                "message": "Интернет пока недоступен; сторож продолжит попытки",
            }
    finally:
        with _state_lock:
            _is_reconnecting = False
        _reconnect_lock.release()


def set_auto_guard(enabled: bool) -> bool:
    global _auto_guard_enabled
    with _state_lock:
        _auto_guard_enabled = bool(enabled)
    _set_event(f"Авто-восстановление сети: {'ВКЛ' if _auto_guard_enabled else 'ВЫКЛ'}")
    return _auto_guard_enabled


def get_quick_status() -> dict:
    """Fast non-blocking summary for /api/pc/status polling."""
    with _state_lock:
        return {
            "internet_online": _last_conn_cache.get("online", True),
            "wifi_profile": _last_conn_cache.get("wifi_profile") or _read_last_good_profile(),
            "network_guard_enabled": _auto_guard_enabled,
            "network_reconnecting": _is_reconnecting,
            "network_last_event": _last_event,
        }


def get_network_snapshot() -> dict:
    """Full live network & tunnel diagnostic snapshot for the Network control card."""
    probe = check_connectivity()
    conn_info = get_active_connection_info()
    profiles = list_wifi_profiles()

    if probe["online"] and conn_info.get("wifi_profile"):
        _save_last_good_profile(conn_info["wifi_profile"])

    with _state_lock:
        _last_conn_cache.update({
            "online": probe["online"],
            "ip_reachable": probe["ip_reachable"],
            "dns_ok": probe["dns_ok"],
            "latency_ms": probe["latency_ms"],
            "wifi_profile": conn_info.get("wifi_profile") or _read_last_good_profile(),
            "wifi_interface": conn_info.get("wifi_interface") or "Wi-Fi",
            "lan_ip": conn_info.get("lan_ip"),
            "vpn_active": bool(conn_info.get("vpn_active")),
            "checked_at": time.time(),
        })
        snapshot = {
            "status": "ok",
            **dict(_last_conn_cache),
            "profiles": profiles,
            "auto_guard": _auto_guard_enabled,
            "is_reconnecting": _is_reconnecting,
            "reconnect_attempts": _reconnect_attempts,
            "last_event": _last_event,
            "last_recovery_ts": _last_recovery_ts,
            "tunnel": tunnel_manager.get_tunnel_status(),
        }
    return snapshot


def _network_guard_loop():
    global _consecutive_net_failures, _consecutive_tunnel_failures, _was_offline, _last_recovery_ts

    # Initial startup grace & baseline profile capture
    _stop_event.wait(timeout=12.0)
    try:
        conn_info = get_active_connection_info()
        init_probe = check_connectivity()
        if init_probe["online"] and conn_info.get("wifi_profile"):
            _save_last_good_profile(conn_info["wifi_profile"])
        with _state_lock:
            _last_conn_cache.update({
                "online": init_probe["online"],
                "ip_reachable": init_probe["ip_reachable"],
                "dns_ok": init_probe["dns_ok"],
                "latency_ms": init_probe["latency_ms"],
                "wifi_profile": conn_info.get("wifi_profile") or _read_last_good_profile(),
                "wifi_interface": conn_info.get("wifi_interface") or "Wi-Fi",
                "lan_ip": conn_info.get("lan_ip"),
                "vpn_active": bool(conn_info.get("vpn_active")),
                "checked_at": time.time(),
            })
    except Exception as e:
        logger.debug(f"Initial network snapshot note: {e}")

    tick = 0
    while _running and not _stop_event.is_set():
        tick += 1
        try:
            probe = check_connectivity()
            with _state_lock:
                _last_conn_cache["online"] = probe["online"]
                _last_conn_cache["ip_reachable"] = probe["ip_reachable"]
                _last_conn_cache["dns_ok"] = probe["dns_ok"]
                _last_conn_cache["latency_ms"] = probe["latency_ms"]
                _last_conn_cache["checked_at"] = time.time()
                enabled = _auto_guard_enabled
                reconnecting_now = _is_reconnecting

            if probe["online"]:
                was_down = False
                with _state_lock:
                    _consecutive_net_failures = 0
                    if _was_offline:
                        _was_offline = False
                        was_down = True

                # Refresh active Wi-Fi profile periodically (skipped during Game Mode unless recovering)
                in_game_mode = False
                try:
                    import win32_controls
                    in_game_mode = win32_controls.is_game_mode_active()
                except Exception:
                    pass
                if was_down or (tick % 8 == 0 and not in_game_mode):
                    conn_info = get_active_connection_info()
                    with _state_lock:
                        if conn_info.get("wifi_profile"):
                            _last_conn_cache["wifi_profile"] = conn_info["wifi_profile"]
                        _last_conn_cache["lan_ip"] = conn_info.get("lan_ip")
                        _last_conn_cache["vpn_active"] = bool(conn_info.get("vpn_active"))
                    if conn_info.get("wifi_profile"):
                        _save_last_good_profile(conn_info["wifi_profile"])

                if was_down:
                    with _state_lock:
                        prof = _last_conn_cache.get("wifi_profile") or _read_last_good_profile() or "Wi-Fi"
                if was_down and not reconnecting_now:
                    _set_event(f"Сеть вернулась самостоятельно ({prof}), обновляю туннель...")
                    tunnel_manager.restart_tunnel(reason="network_recovered", detail=f"Сеть: {prof} (авто-восстановление)")
                    with _state_lock:
                        _last_recovery_ts = time.time()
                        _consecutive_tunnel_failures = 0

                # Every ~60s (4 ticks), verify Cloudflare Tunnel responds externally while internet is alive
                if tick % 4 == 0 and not reconnecting_now and enabled:
                    t_ok, t_detail = tunnel_manager.check_tunnel_reachable(timeout=10.0)
                    if t_ok:
                        with _state_lock:
                            _consecutive_tunnel_failures = 0
                    elif t_detail == "local_server_down":
                        with _state_lock:
                            _consecutive_tunnel_failures += 1
                            l_fails = _consecutive_tunnel_failures
                        logger.error(f"Local server origin port is unresponsive ({l_fails}/2); skipping cloudflared restart")
                        if l_fails >= 2:
                            logger.critical("Local server socket died while process remained alive; exiting for watchdog respawn")
                            os._exit(1)
                    else:
                        with _state_lock:
                            _consecutive_tunnel_failures += 1
                            t_fails = _consecutive_tunnel_failures
                        logger.warning(f"Cloudflare tunnel health check failed ({t_fails}/{TUNNEL_FAILURES_BEFORE_RESTART}): {t_detail}")
                        if t_fails >= TUNNEL_FAILURES_BEFORE_RESTART:
                            with _state_lock:
                                _consecutive_tunnel_failures = 0
                            _set_event(f"Туннель залип ({t_detail}), перезапуск cloudflared...")
                            tunnel_manager.restart_tunnel(reason="tunnel_stalled", detail=f"Причина: {t_detail}")
            else:
                with _state_lock:
                    _consecutive_net_failures += 1
                    _consecutive_tunnel_failures = 0
                    fails = _consecutive_net_failures
                    if fails >= FAILURES_BEFORE_RECOVERY:
                        _was_offline = True
                    since_last = time.time() - _last_reconnect_ts

                logger.warning(f"Internet connectivity check failed ({fails}/{FAILURES_BEFORE_RECOVERY})")
                if enabled and fails >= FAILURES_BEFORE_RECOVERY and since_last >= AUTO_COOLDOWN_SEC and not reconnecting_now:
                    reconnect_network(reason="auto")

        except Exception as e:
            logger.debug(f"Network guard loop exception: {e}")

        _stop_event.wait(timeout=CHECK_INTERVAL_SEC)


def start_network_guard():
    global _running
    if _running:
        return
    _stop_event.clear()
    _running = True
    t = threading.Thread(target=_network_guard_loop, daemon=True, name="NetworkGuardThread")
    t.start()
    logger.info("NetworkGuard background monitor started.")


def stop_network_guard():
    global _running
    _running = False
    _stop_event.set()
