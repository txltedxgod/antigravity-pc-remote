"""
Antigravity Remote Account & Quota Manager.
Enables seamless multi-account profile switching and remote Google OAuth login
for Antigravity IDE without physical access to the laptop.
"""

import ctypes
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from ctypes import wintypes
from pathlib import Path

logger = logging.getLogger("AccountManager")

advapi32 = ctypes.windll.advapi32

class CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ('Flags', wintypes.DWORD),
        ('Type', wintypes.DWORD),
        ('TargetName', wintypes.LPWSTR),
        ('Comment', wintypes.LPWSTR),
        ('LastWritten', wintypes.FILETIME),
        ('CredentialBlobSize', wintypes.DWORD),
        ('CredentialBlob', ctypes.c_char_p),
        ('Persist', wintypes.DWORD),
        ('AttributeCount', wintypes.DWORD),
        ('Attributes', ctypes.c_void_p),
        ('TargetAlias', wintypes.LPWSTR),
        ('UserName', wintypes.LPWSTR),
    ]

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
TARGET_NAME = "gemini:antigravity"

def _load_google_credentials():
    """Dynamically loads Antigravity Google OAuth Client ID and Secret."""
    cid = os.environ.get("AGY_GOOGLE_CLIENT_ID", "")
    csec = os.environ.get("AGY_GOOGLE_CLIENT_SECRET", "")
    if cid and csec:
        return cid, csec

    cred_file = Path(__file__).resolve().parent / ".credentials.json"
    if cred_file.exists():
        try:
            with open(cred_file, "r", encoding="utf-8") as f:
                d = json.load(f)
                return d.get("client_id", ""), d.get("client_secret", "")
        except Exception:
            pass

    # Dynamic fallback: extract from installed Antigravity binary
    local_app = os.environ.get("LOCALAPPDATA", "")
    ls_path = Path(local_app) / r"Programs\Antigravity\resources\app\extensions\antigravity\bin\language_server.exe"
    if ls_path.exists():
        try:
            with open(ls_path, "rb") as bf:
                data = bf.read()
            m_sec = re.findall(rb"GOCSPX-[A-Za-z0-9_-]+", data)
            m_id = re.findall(rb"\d+-[a-z0-9]+\.apps\.googleusercontent\.com", data)
            if m_sec and m_id:
                return m_id[0].decode(), m_sec[0].decode()
        except Exception:
            pass

    return "", ""

GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET = _load_google_credentials()
GOOGLE_SCOPES = (
    "email profile openid "
    "https://www.googleapis.com/auth/aicode "
    "https://www.googleapis.com/auth/cclog "
    "https://www.googleapis.com/auth/cloud-platform "
    "https://www.googleapis.com/auth/experimentsandconfigs "
    "https://www.googleapis.com/auth/userinfo.email "
    "https://www.googleapis.com/auth/userinfo.profile"
)

ACCOUNTS_DIR = Path.home() / ".gemini" / "antigravity" / "accounts"
ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_REDIRECT_URIS = {
    "http://127.0.0.1:8765/api/antigravity/auth/callback",
    "http://localhost:8765/api/antigravity/auth/callback",
}
_EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,63}$")
_oauth_states = {}
_oauth_state_lock = threading.Lock()


def _resolve_account_path(email: str):
    """Validates email format and returns a safe profile Path strictly inside ACCOUNTS_DIR."""
    safe_name = (email or "").strip().lower()
    if not _EMAIL_RE.match(safe_name):
        return None, ""
    candidate = (ACCOUNTS_DIR / f"{safe_name}.json").resolve()
    if not candidate.is_relative_to(ACCOUNTS_DIR.resolve()):
        return None, ""
    return candidate, safe_name


def validate_redirect_uri(redirect_uri: str) -> str:
    """Restricts OAuth redirect_uri to known local callback endpoints."""
    uri = (redirect_uri or "").strip()
    if uri in ALLOWED_REDIRECT_URIS:
        return uri
    return "http://127.0.0.1:8765/api/antigravity/auth/callback"


def verify_and_consume_oauth_state(state: str) -> bool:
    """Validates and consumes a single-use OAuth CSRF state nonce."""
    if not state or not isinstance(state, str):
        return False
    now = time.time()
    with _oauth_state_lock:
        # Prune expired states (> 15 min)
        expired = [k for k, ts in _oauth_states.items() if now - ts > 900]
        for k in expired:
            _oauth_states.pop(k, None)
        ts = _oauth_states.pop(state, None)
        return ts is not None and (now - ts <= 900)

def read_active_credential():
    """Reads current active Antigravity credential from Windows Credential Manager."""
    PCREDENTIAL = ctypes.POINTER(CREDENTIAL)
    pcred = PCREDENTIAL()
    if advapi32.CredReadW(TARGET_NAME, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        try:
            c = pcred.contents
            blob = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
            data = json.loads(blob.decode('utf-8'))
            return data
        except Exception as e:
            logger.error(f"Error parsing {TARGET_NAME}: {e}")
            return None
        finally:
            advapi32.CredFree(pcred)
    return None

def write_active_credential(data: dict):
    """Writes credential data into Windows Credential Manager under gemini:antigravity."""
    try:
        raw_bytes = json.dumps(data).encode('utf-8')
        cred = CREDENTIAL()
        cred.Flags = 0
        cred.Type = CRED_TYPE_GENERIC
        cred.TargetName = TARGET_NAME
        cred.Comment = "Antigravity OAuth Credential"
        cred.CredentialBlobSize = len(raw_bytes)
        cred.CredentialBlob = raw_bytes
        cred.Persist = CRED_PERSIST_LOCAL_MACHINE
        cred.AttributeCount = 0
        cred.Attributes = None
        cred.TargetAlias = None
        cred.UserName = "antigravity"

        ok = advapi32.CredWriteW(ctypes.byref(cred), 0)
        return bool(ok)
    except Exception as e:
        logger.error(f"Error writing {TARGET_NAME}: {e}")
        return False

def update_app_storage_email(email: str):
    """Updates lastLoginUsername in Antigravity app_storage.json."""
    storage_path = Path(os.path.expandvars(r"%APPDATA%\Antigravity\app_storage.json"))
    if storage_path.exists():
        try:
            with open(storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["agy.onboarding.lastLoginUsername"] = email
            with open(storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            logger.warning(f"Could not update app_storage.json: {e}")
    return False

def stop_antigravity_gracefully():
    """Closes Antigravity cleanly first, force-kills only if hung, and clears stale DevTools port file."""
    no_win = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        subprocess.run(["taskkill", "/IM", "Antigravity.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, creationflags=no_win)
        # Wait briefly for clean flush
        for _ in range(5):
            time.sleep(0.5)
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq Antigravity.exe"],
                timeout=4,
                creationflags=no_win
            ).decode("utf-8", errors="ignore")
            if "antigravity.exe" not in out.lower():
                break
        else:
            subprocess.run(["taskkill", "/F", "/IM", "Antigravity.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, creationflags=no_win)
            time.sleep(1.0)
    except Exception as e:
        logger.debug(f"stop_antigravity_gracefully note: {e}")

    # Remove stale DevToolsActivePort so subsequent CDP probe never hits a dead port
    port_file = Path(os.path.expandvars(r"%APPDATA%\Antigravity\DevToolsActivePort"))
    if port_file.exists():
        try:
            port_file.unlink()
        except Exception:
            pass

def restart_antigravity():
    """Gracefully terminates and relaunches Antigravity desktop executable with SW_SHOWNORMAL."""
    try:
        stop_antigravity_gracefully()
        exe = Path(os.path.expandvars(r"%LOCALAPPDATA%\Programs\antigravity\Antigravity.exe"))
        if exe.exists():
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 1  # SW_SHOWNORMAL
            subprocess.Popen(
                [str(exe)],
                cwd=str(exe.parent),
                creationflags=flags,
                startupinfo=si,
                close_fds=True
            )
            return True
    except Exception as e:
        logger.error(f"Failed to restart Antigravity: {e}")
    return False

def save_account_profile(email: str, data: dict, label: str = ""):
    """Saves an account profile into the accounts directory."""
    profile_path, safe_name = _resolve_account_path(email)
    if not profile_path:
        return False
    save_data = {
        "email": safe_name,
        "label": label or safe_name,
        "saved_at": int(time.time()),
        "credential": data
    }
    try:
        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save profile {profile_path}: {e}")
        return False

def list_saved_accounts():
    """Lists all saved accounts and identifies the currently active one."""
    active_data = read_active_credential()
    active_email = (active_data.get("id_token_email") or "").strip().lower() if active_data else ""

    # Auto-save active account if not saved yet
    if active_email:
        p_path = ACCOUNTS_DIR / f"{active_email}.json"
        if not p_path.exists():
            save_account_profile(active_email, active_data, label="Основной")

    accounts = []
    for f in ACCOUNTS_DIR.glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                p = json.load(fp)
            email = p.get("email") or f.stem
            is_active = (email.lower() == active_email.lower())
            accounts.append({
                "email": email,
                "label": p.get("label", email),
                "is_active": is_active,
                "saved_at": p.get("saved_at", 0)
            })
        except Exception:
            pass

    accounts.sort(key=lambda x: (not x["is_active"], x["email"]))
    return {
        "status": "ok",
        "active_account": active_email,
        "accounts": accounts,
        "count": len(accounts)
    }

def refresh_google_token(refresh_token: str):
    """Refreshes access token with Google OAuth servers using Antigravity client credentials."""
    post_data = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=post_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        logger.error(f"Error refreshing Google token: {e}")
        return None

def switch_account_profile(target_email: str, restart: bool = True):
    """
    Switches active Antigravity account to target_email:
    1. Backs up current active credential
    2. Loads target profile
    3. Refreshes token if needed
    4. Writes to Windows Credential Manager (gemini:antigravity)
    5. Updates app_storage.json
    6. Optionally restarts Antigravity IDE
    """
    profile_path, target_email = _resolve_account_path(target_email)
    if not profile_path or not profile_path.exists():
        return {"status": "error", "message": f"Профиль {target_email or '(некорректный email)'} не найден"}

    # Backup current active
    current = read_active_credential()
    if current and current.get("id_token_email"):
        save_account_profile(current["id_token_email"], current)

    try:
        with open(profile_path, "r", encoding="utf-8") as f:
            profile = json.load(f)
        cred = profile.get("credential", {})
        tok = cred.get("token", {})
        rt = tok.get("refresh_token")

        # Refresh token to ensure valid session
        if rt:
            fresh = refresh_google_token(rt)
            if fresh and "access_token" in fresh:
                tok["access_token"] = fresh["access_token"]
                if "expires_in" in fresh:
                    tok["expires_in"] = fresh["expires_in"]
                cred["token"] = tok

        # Write to Windows Credential Manager
        ok = write_active_credential(cred)
        if not ok:
            return {"status": "error", "message": "Сбой записи в Windows Credential Manager"}

        update_app_storage_email(target_email)
        restarted = restart_antigravity() if restart else False

        logger.info(f"Successfully switched Antigravity account to {target_email} (Restarted: {restarted})")
        return {
            "status": "ok",
            "message": f"Аккаунт успешно переключен на {target_email}!",
            "active_account": target_email,
            "restarted": restarted
        }
    except Exception as e:
        logger.error(f"Error switching account: {e}")
        return {"status": "error", "message": str(e)}

def delete_account_profile(email: str):
    """Deletes a saved account profile."""
    profile_path, safe_name = _resolve_account_path(email)
    if not profile_path:
        return {"status": "error", "message": "Некорректный email"}
    if profile_path.exists():
        profile_path.unlink()
        return {"status": "ok", "message": f"Профиль {safe_name} удален"}
    return {"status": "error", "message": "Профиль не найден"}

def get_google_oauth_url(redirect_uri: str = "http://127.0.0.1:8765/api/antigravity/auth/callback"):
    """Builds authorization URL with a CSRF state token for adding a Google account to Antigravity."""
    safe_redirect = validate_redirect_uri(redirect_uri)
    state = secrets.token_urlsafe(24)
    with _oauth_state_lock:
        _oauth_states[state] = time.time()
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": safe_redirect,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",
        "prompt": "consent select_account",
        "state": state,
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urllib.parse.urlencode(params)}"

def extract_code_from_string(text: str) -> str:
    """Extracts raw OAuth authorization code from strings, full URLs, or query params."""
    text = (text or "").strip()
    if "code=" in text:
        m = re.search(r'code=([^& ]+)', text)
        if m:
            return urllib.parse.unquote(m.group(1))
    return text

def exchange_auth_code_for_account(code: str, redirect_uri: str = "http://127.0.0.1:8765/api/antigravity/auth/callback"):
    """Exchanges authorization code for Google tokens and saves as active account."""
    clean_code = extract_code_from_string(code)
    safe_redirect = validate_redirect_uri(redirect_uri)
    post_data = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": clean_code,
        "redirect_uri": safe_redirect
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=post_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            token_res = json.loads(r.read().decode())
    except Exception as e:
        return {"status": "error", "message": f"Google Token Exchange Error: {e}"}

    access_token = token_res.get("access_token")
    if not access_token:
        return {"status": "error", "message": "Google did not return access_token"}

    # Ensure ISO expiry string exists for Antigravity's Go runtime
    if "expiry" not in token_res:
        from datetime import datetime, timezone, timedelta
        exp = datetime.now(timezone.utc) + timedelta(seconds=token_res.get("expires_in", 3600))
        token_res["expiry"] = exp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    # Fetch user email from Google UserInfo
    email = "google_user@gmail.com"
    try:
        user_req = urllib.request.Request(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        with urllib.request.urlopen(user_req, timeout=8) as ur:
            uinfo = json.loads(ur.read().decode())
            email = uinfo.get("email", email)
    except Exception as e:
        logger.warning(f"Could not fetch userinfo: {e}")

    cred_blob = {
        "token": token_res,
        "auth_method": "consumer",
        "id_token_email": email,
        "writer_id": ""
    }

    # Save to vault
    save_account_profile(email, cred_blob, label=email)
    # Write active
    write_active_credential(cred_blob)
    update_app_storage_email(email)
    # Restart
    restart_antigravity()

    return {
        "status": "ok",
        "email": email,
        "message": f"Аккаунт {email} успешно подключен и активирован в Antigravity!"
    }

def open_oauth_on_pc(redirect_uri: str = "http://127.0.0.1:8765/api/antigravity/auth/callback"):
    """Opens Google OAuth sign-in directly in the default browser on the PC."""
    url = get_google_oauth_url(redirect_uri)
    import webbrowser
    webbrowser.open(url)
    return {"status": "ok", "url": url}
