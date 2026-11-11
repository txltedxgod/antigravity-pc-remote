import asyncio
import base64
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

import account_manager
import antigravity_bridge
import network_guard
import server
import tunnel_manager
import win32_controls


def test_account_manager_re_import_and_path_traversal():
    assert account_manager.extract_code_from_string("http://127.0.0.1:8765/api/antigravity/auth/callback?code=abc%2F123&scope=email") == "abc/123"
    assert account_manager.switch_account_profile("../../server/server")["status"] == "error"
    assert account_manager.delete_account_profile("../pinned_conversations")["status"] == "error"
    path, email = account_manager._resolve_account_path("User.Test+1@Example.COM")
    assert path is not None and email == "user.test+1@example.com"


def test_oauth_state_single_use_and_redirect_allowlist():
    url = account_manager.get_google_oauth_url("https://evil.example/steal")
    assert "state=" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8765%2Fapi%2Fantigravity%2Fauth%2Fcallback" in url
    state = url.split("state=")[1].split("&")[0]
    assert account_manager.verify_and_consume_oauth_state(state) is True
    assert account_manager.verify_and_consume_oauth_state(state) is False
    assert account_manager.verify_and_consume_oauth_state("forged-state") is False
    assert account_manager.validate_redirect_uri("https://evil.com/cb") == "http://127.0.0.1:8765/api/antigravity/auth/callback"


def test_win32_command_injection_and_bounds():
    res_kill = win32_controls.kill_application("notepad.exe & calc.exe")
    assert res_kill["status"] == "error"

    res_url = win32_controls.open_url('https://example.com/" & calc.exe & "')
    assert res_url["status"] == "error"

    res_ping = win32_controls.get_network_ping("-t 127.0.0.1")
    assert res_ping["status"] == "error"

    with patch.object(win32_controls, "send_vk") as mock_vk:
        win32_controls.volume_up(999999)
        assert mock_vk.call_count == 50

    with patch.object(win32_controls.user32, "SendInput", return_value=1) as mock_send:
        assert win32_controls.type_unicode_text("Hi 🚀") is True
        # "Hi " (3 UTF-16 code units) + "🚀" (2 surrogate UTF-16 code units) = 5 * 2 events
        assert mock_send.call_count == 10


def test_antigravity_bridge_conv_validation_and_history(tmp_path):
    assert antigravity_bridge.is_valid_conv_id("50165b0b-b830-4e4e-8e77-8de349b8afcf") is True
    assert antigravity_bridge.is_valid_conv_id("../secret") is False
    assert antigravity_bridge.is_valid_conv_id("abc';alert(1);//") is False

    bad = antigravity_bridge.parse_conversation_history("../../server")
    assert bad["status"] == "error"

    conv_id = "11111111-2222-3333-4444-555555555555"
    logs_dir = tmp_path / conv_id / ".system_generated" / "logs"
    logs_dir.mkdir(parents=True)
    compact = logs_dir / "transcript.jsonl"
    full = logs_dir / "transcript_full.jsonl"
    compact.write_text(
        json.dumps({"type": "USER_INPUT", "content": "<USER_REQUEST>hello</USER_REQUEST>"}) + "\n" +
        json.dumps({"type": "PLANNER_RESPONSE", "content": "trunc...", "truncated_fields": ["content"]}) + "\n",
        encoding="utf-8"
    )
    full.write_text(
        json.dumps({"type": "USER_INPUT", "content": "<USER_REQUEST>hello</USER_REQUEST>"}) + "\n" +
        json.dumps({"type": "PLANNER_RESPONSE", "content": "FULL RESPONSE TEXT"}) + "\n",
        encoding="utf-8"
    )

    with patch.object(antigravity_bridge, "BRAIN_DIR", tmp_path):
        parsed = antigravity_bridge.parse_conversation_history(conv_id, limit=20)
        assert parsed["status"] == "ok"
        assert len(parsed["messages"]) == 2
        assert parsed["messages"][1]["text"] == "FULL RESPONSE TEXT"


@pytest.mark.asyncio
async def test_server_no_auth_and_static_root(tmp_path):
    app = server.create_app()
    app.on_startup.clear()
    app.on_shutdown.clear()

    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    dl_file = allowed_root / "report.txt"
    dl_file.write_text("download content", encoding="utf-8")

    async with TestClient(TestServer(app)) as client:
        # 1. /api/ping works cleanly without auth token
        r_ping = await client.get("/api/ping")
        assert r_ping.status == 200
        d_ping = await r_ping.json()
        assert d_ping["status"] == "ok"

        # 2. Protected endpoints work directly without Bearer token (no 401/429 gates)
        with patch.object(server, "ALLOWED_FS_ROOTS", {"Allowed": allowed_root}):
            r_list = await client.get("/api/fs/list")
            assert r_list.status == 200

            r_dl = await client.get("/api/fs/download", params={"path": str(dl_file)})
            assert r_dl.status == 200
            assert await r_dl.text() == "download content"

        # 3. Web root serves index.html (never directory listing) and /download/apk works directly
        r_root = await client.get("/")
        assert r_root.status == 200
        root_html = await r_root.text()
        assert "Antigravity PC Remote" in root_html
        assert "Index of /" not in root_html

        fake_apk = tmp_path / "PCRemote.apk"
        fake_apk.write_bytes(b"PK\x03\x04fakeapk")
        with patch.object(server, "APK_PATH", fake_apk):
            r_apk_dl = await client.get("/download/apk", headers={"CF-Connecting-IP": "1.2.3.4"})
            assert r_apk_dl.status == 200
            assert await r_apk_dl.read() == b"PK\x03\x04fakeapk"


@pytest.mark.asyncio
async def test_server_cors_fs_sandbox_validation_and_queue(tmp_path):
    app = server.create_app()
    # Avoid touching real IDE/network on startup/shutdown in tests
    app.on_startup.clear()
    app.on_shutdown.clear()

    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    safe_file = allowed_root / "notes.txt"
    safe_file.write_text("hello world", encoding="utf-8")
    forbidden_cred = allowed_root / ".credentials.json"
    forbidden_cred.write_text('{"client_secret":"secret"}', encoding="utf-8")

    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("top secret", encoding="utf-8")

    async with TestClient(TestServer(app)) as client:
        # 1. CORS & Security Headers
        r_evil = await client.get("/api/ping", headers={"Origin": "https://evil.example"})
        assert r_evil.status == 200
        assert "Access-Control-Allow-Origin" not in r_evil.headers
        assert r_evil.headers.get("X-Content-Type-Options") == "nosniff"
        assert r_evil.headers.get("X-Frame-Options") == "DENY"

        r_ok = await client.get("/api/ping", headers={"Origin": "https://pcremote.local"})
        assert r_ok.headers.get("Access-Control-Allow-Origin") == "https://pcremote.local"

        # 2. FS Path Traversal & Secret Blocking
        with patch.object(server, "ALLOWED_FS_ROOTS", {"Allowed": allowed_root}):
            r_out = await client.get("/api/fs/read", params={"path": str(outside_file)})
            assert r_out.status == 403

            r_dl_out = await client.get("/api/fs/download", params={"path": str(outside_file)})
            assert r_dl_out.status == 403

            r_cred = await client.get("/api/fs/read", params={"path": str(forbidden_cred)})
            assert r_cred.status == 403

            r_list = await client.get("/api/fs/list", params={"path": str(allowed_root)})
            list_data = await r_list.json()
            names = [it["name"] for it in list_data["items"]]
            assert "notes.txt" in names
            assert ".credentials.json" not in names

            r_read_ok = await client.get("/api/fs/read", params={"path": str(safe_file)})
            assert r_read_ok.status == 200
            assert (await r_read_ok.json())["content"] == "hello world"

            # Upload traversal / ADS / forbidden name blocked
            b64_data = base64.b64encode(b"safe payload").decode("ascii")
            r_up_bad = await client.post("/api/fs/upload", json={
                "filename": "test:stream.txt",
                "folder": str(allowed_root),
                "data": b64_data
            })
            assert r_up_bad.status == 400

            r_up_out = await client.post("/api/fs/upload", json={
                "filename": "ok.txt",
                "folder": str(tmp_path),
                "data": b64_data
            })
            assert r_up_out.status == 403

            r_up_ok = await client.post("/api/fs/upload", json={
                "filename": "ok.txt",
                "folder": str(allowed_root),
                "data": b64_data
            })
            assert r_up_ok.status == 200
            assert (allowed_root / "ok.txt").read_bytes() == b"safe payload"

        # 3. Invalid numeric params return 400 instead of 500
        r_bad_steps = await client.post("/api/pc/action", json={"action": "volume_up", "steps": "abc"})
        assert r_bad_steps.status == 400
        r_bad_mouse = await client.post("/api/pc/mouse", json={"action": "move", "dx": "nan"})
        assert r_bad_mouse.status == 400
        r_bad_pid = await client.post("/api/pc/process/kill", json={"pid": "xyz"})
        assert r_bad_pid.status == 400

        # 4. Invalid image payload rejected
        r_bad_img = await client.post("/api/antigravity/send", json={
            "text": "test",
            "image": "data:image/png;base64," + base64.b64encode(b"NOT_AN_IMAGE_EXE").decode("ascii")
        })
        assert r_bad_img.status == 400

        # 5. OAuth Callback State & HTML Escaping
        r_cb_bad = await client.get("/api/antigravity/auth/callback", params={"code": "123", "state": "invalid"})
        assert r_cb_bad.status == 400

        auth_url_res = await (await client.get("/api/antigravity/auth/url")).json()
        valid_state = auth_url_res["url"].split("state=")[1].split("&")[0]
        with patch.object(account_manager, "exchange_auth_code_for_account", return_value={"status": "ok", "email": "<script>xss</script>@test.com"}):
            r_cb_ok = await client.get("/api/antigravity/auth/callback", params={"code": "123", "state": valid_state})
            assert r_cb_ok.status == 200
            html_body = await r_cb_ok.text()
            assert "<script>xss</script>" not in html_body
            assert "&lt;script&gt;xss&lt;/script&gt;@test.com" in html_body

        # 6. Queue Endpoints
        mock_queue = [{"index": 0, "text": "First queued", "has_media": False, "can_edit": True, "can_delete": True, "can_send_now": True}]
        with patch.object(antigravity_bridge, "get_queued_messages_cdp", new=AsyncMock(return_value=mock_queue)):
            r_q = await client.get("/api/antigravity/queue", params={"conversation_id": "50165b0b-b830-4e4e-8e77-8de349b8afcf"})
            assert r_q.status == 200
            assert (await r_q.json())["queued_messages"] == mock_queue

        r_q_bad_idx = await client.post("/api/antigravity/queue/delete", json={"index": "bad"})
        assert r_q_bad_idx.status == 400

        with patch.object(antigravity_bridge, "queue_action_cdp", new=AsyncMock(return_value={"status": "ok", "action": "edit", "queued_messages": []})):
            r_q_edit = await client.post("/api/antigravity/queue/edit", json={
                "index": 0,
                "text": "First queued",
                "new_text": "Updated queued",
                "conversation_id": "50165b0b-b830-4e4e-8e77-8de349b8afcf"
            })
            assert r_q_edit.status == 200
            assert (await r_q_edit.json())["status"] == "ok"


def test_network_guard_profile_validation_and_recovery(tmp_path):
    network_guard._stop_event.clear()
    profiles = ["HomeNet_5G", "A55 пользователя Yul", "ReserveWiFi"]

    # 1. Profile validation rejects command injection & unlisted names, allows Cyrillic
    assert network_guard.validate_wifi_profile_name("HomeNet_5G & calc.exe", profiles)[0] is False
    assert network_guard.validate_wifi_profile_name('HomeNet";whoami', profiles)[0] is False
    assert network_guard.validate_wifi_profile_name("UnknownSSID", profiles)[0] is False
    ok, clean = network_guard.validate_wifi_profile_name("A55 пользователя Yul", profiles)
    assert ok is True and clean == "A55 пользователя Yul"

    # 2. Failover escalation: primary dead -> backup Wi-Fi recovers -> tunnel restarted + profile saved
    calls = []
    fake_now = [1000.0]

    def fake_sleep(sec):
        fake_now[0] += float(sec)

    def fake_run_cmd(args, timeout=8.0):
        calls.append(args)
        return (0, "", "")

    def fake_check():
        # Stay offline until Stage 3 connects to the backup profile "A55 пользователя Yul"
        switched_to_backup = any(
            c[:3] == ["netsh", "wlan", "connect"] and "name=A55 пользователя Yul" in c
            for c in calls
        )
        if switched_to_backup:
            return {"online": True, "ip_reachable": True, "dns_ok": True, "latency_ms": 22.0}
        return {"online": False, "ip_reachable": False, "dns_ok": False, "latency_ms": None}

    last_file = tmp_path / "last_wifi_profile.txt"
    with patch.object(network_guard, "LAST_PROFILE_FILE", last_file), \
         patch.object(network_guard, "list_wifi_profiles", return_value=profiles), \
         patch.object(network_guard, "get_active_connection_info", return_value={"wifi_profile": "HomeNet_5G", "wifi_interface": "Wi-Fi", "lan_ip": "10.0.0.85", "vpn_active": False}), \
         patch.object(network_guard, "_run_cmd", side_effect=fake_run_cmd), \
         patch.object(network_guard, "check_connectivity", side_effect=fake_check), \
         patch.object(network_guard.time, "time", side_effect=lambda: fake_now[0]), \
         patch.object(network_guard.time, "sleep", side_effect=fake_sleep), \
         patch.object(tunnel_manager, "restart_tunnel", return_value=True) as mock_restart:
        res = network_guard.reconnect_network(reason="auto")
        assert res["status"] == "ok"
        assert res["online"] is True
        assert res["profile"] == "A55 пользователя Yul"
        mock_restart.assert_called_once()
        assert mock_restart.call_args.kwargs.get("reason") == "network_recovered"
        assert last_file.read_text(encoding="utf-8") == "A55 пользователя Yul"
        assert any(c[:3] == ["netsh", "wlan", "connect"] and "name=HomeNet_5G" in c for c in calls)
        assert any(c[:3] == ["netsh", "wlan", "connect"] and "name=A55 пользователя Yul" in c for c in calls)


@pytest.mark.asyncio
async def test_server_network_endpoints_and_validation():
    app = server.create_app()
    app.on_startup.clear()
    app.on_shutdown.clear()

    mock_snap = {
        "status": "ok",
        "online": True,
        "ip_reachable": True,
        "dns_ok": True,
        "latency_ms": 18.0,
        "wifi_profile": "HomeNet_5G",
        "wifi_interface": "Wi-Fi",
        "lan_ip": "10.0.0.85",
        "vpn_active": False,
        "profiles": ["HomeNet_5G", "PhoneHotspot"],
        "auto_guard": True,
        "is_reconnecting": False,
        "reconnect_attempts": 0,
        "last_event": "Мониторинг активен",
        "tunnel": {"url": "https://demo.trycloudflare.com", "running": True, "restarts": 0}
    }

    async with TestClient(TestServer(app)) as client:
        with patch.object(network_guard, "get_network_snapshot", return_value=mock_snap):
            r_st = await client.get("/api/pc/network/status")
            assert r_st.status == 200
            data_st = await r_st.json()
            assert data_st["wifi_profile"] == "HomeNet_5G"
            assert "PhoneHotspot" in data_st["profiles"]

        # Switching to unknown/injected profile must return 400
        with patch.object(network_guard, "list_wifi_profiles", return_value=["HomeNet_5G", "PhoneHotspot"]):
            r_bad_sw = await client.post("/api/pc/network/switch", json={"profile": "HomeNet_5G & calc.exe"})
            assert r_bad_sw.status == 400

            with patch.object(server, "_run_delayed_network_recovery", new=AsyncMock()):
                r_ok_sw = await client.post("/api/pc/network/switch", json={"profile": "PhoneHotspot"})
                assert r_ok_sw.status == 200
                assert (await r_ok_sw.json())["profile"] == "PhoneHotspot"

                r_rec = await client.post("/api/pc/network/reconnect", json={})
                assert r_rec.status == 200
                assert (await r_rec.json())["scheduled"] is True

        # Autoguard toggle
        r_off = await client.post("/api/pc/network/autoguard", json={"enable": False})
        assert r_off.status == 200 and (await r_off.json())["auto_guard"] is False
        r_on = await client.post("/api/pc/network/autoguard", json={"enable": True})
        assert r_on.status == 200 and (await r_on.json())["auto_guard"] is True

        # Restart tunnel endpoint
        with patch.object(tunnel_manager, "restart_tunnel", return_value=True) as mock_rt:
            r_rt = await client.post("/api/pc/network/restart_tunnel", json={})
            assert r_rt.status == 200
            await asyncio.sleep(0.35)
            mock_rt.assert_called_once_with("manual_restart")


def test_tunnel_health_checks_local_origin_first():
    with patch.object(tunnel_manager, "_is_local_origin_alive", return_value=False):
        ok, detail = tunnel_manager.check_tunnel_reachable()
        assert ok is False
        assert detail == "local_server_down"

    # Non-windowed PowerShell helper uses CREATE_NO_WINDOW on Windows
    with patch.object(server.subprocess, "run", return_value=MagicMock(returncode=0, stdout=b"ok", stderr=b"")) as mock_run:
        rc, out, _ = server._run_ps_cmd_sync("Write-Output ok")
        assert rc == 0 and out == "ok"
        assert mock_run.call_args.kwargs.get("creationflags") == server.subprocess.CREATE_NO_WINDOW


def test_secret_gist_beacon_publish_and_fs_protection(tmp_path):
    beacon_file = tmp_path / ".tunnel_beacon.json"
    beacon_file.write_text(json.dumps({
        "gist_id": "abc123deadbeef",
        "api_url": "https://api.github.com/gists/abc123deadbeef"
    }), encoding="utf-8")

    # .tunnel_beacon.json must be blocked from remote FS access
    assert server._is_forbidden_fs_path(beacon_file) is True

    fake_resp = MagicMock()
    fake_resp.status = 200
    fake_resp.__enter__ = lambda s: fake_resp
    fake_resp.__exit__ = lambda s, *a: False

    with patch.object(tunnel_manager, "BEACON_FILE", beacon_file), \
         patch.object(tunnel_manager, "_get_gh_token", return_value="gho_test_token_123"), \
         patch.object(tunnel_manager.urllib.request, "urlopen", return_value=fake_resp) as mock_open:
        ok = tunnel_manager._publish_url_to_beacon("https://live-test-node.trycloudflare.com")
        assert ok is True
        req_arg = mock_open.call_args.args[0]
        assert req_arg.get_method() == "PATCH"
        assert req_arg.full_url == "https://api.github.com/gists/abc123deadbeef"
        sent_body = json.loads(req_arg.data.decode("utf-8"))
        assert sent_body["files"]["tunnel_url.txt"]["content"] == "https://live-test-node.trycloudflare.com"

    # Also verify start_tunnel runner publishes & caches URL cleanly without UnboundLocalError
    import io
    fake_proc = MagicMock()
    fake_proc.stdout = io.StringIO("INF +---+\nINF | https://runner-ok-test.trycloudflare.com |\n")
    fake_proc.poll.return_value = 0
    fake_proc.wait.side_effect = lambda: (setattr(tunnel_manager, "_stopping", True), 0)[1]
    url_file = tmp_path / "tunnel_url.txt"
    with patch.object(tunnel_manager, "URL_CACHE_FILE", url_file), \
         patch.object(tunnel_manager, "_tunnel_thread", None), \
         patch.object(tunnel_manager, "_last_published_url", None), \
         patch.object(tunnel_manager, "_last_notified_url", None), \
         patch.object(tunnel_manager, "_publish_url_to_beacon", return_value=True) as mock_pub, \
         patch.object(tunnel_manager, "_notify_telegram", return_value=None), \
         patch.object(tunnel_manager.subprocess, "Popen", return_value=fake_proc):
        t = tunnel_manager.start_tunnel(8765)
        assert t is not None
        t.join(timeout=3)
        tunnel_manager._stopping = False
        tunnel_manager._tunnel_thread = None
        assert url_file.read_text(encoding="utf-8") == "https://runner-ok-test.trycloudflare.com"
        mock_pub.assert_called_once_with("https://runner-ok-test.trycloudflare.com")


def test_is_antigravity_running_ignores_sidecars_and_helpers():
    import psutil
    fake_sidecar = MagicMock()
    fake_sidecar.info = {
        "name": "Antigravity.exe",
        "cmdline": ["C:\\Antigravity.exe", "--import=file:///sidecar_sdk/register_loader.js", "main.mjs"]
    }
    fake_renderer = MagicMock()
    fake_renderer.info = {
        "name": "Antigravity.exe",
        "cmdline": ["C:\\Antigravity.exe", "--type=renderer"]
    }
    fake_main = MagicMock()
    fake_main.info = {
        "name": "Antigravity.exe",
        "cmdline": ["C:\\Users\\dev\\AppData\\Local\\Programs\\antigravity\\Antigravity.exe"]
    }

    with patch.object(psutil, "process_iter", return_value=[fake_sidecar, fake_renderer]):
        assert antigravity_bridge.is_antigravity_running() is False

    with patch.object(psutil, "process_iter", return_value=[fake_sidecar, fake_renderer, fake_main]):
        assert antigravity_bridge.is_antigravity_running() is True


def test_isolated_desktop_blocks_trapping_ide_launch(tmp_path):
    async def _run():
        pending_file = tmp_path / "pending_switch_resume.json"
        with patch.object(server, "PENDING_SWITCH_FILE", pending_file), \
             patch.object(server.asyncio, "sleep", new_callable=AsyncMock), \
             patch.object(antigravity_bridge, "get_current_desktop_name", return_value="exebox-TEST"), \
             patch.object(antigravity_bridge, "is_antigravity_running", return_value=False), \
             patch.object(antigravity_bridge, "ensure_antigravity_ready", new_callable=AsyncMock) as mock_launch:
            await server._boot_antigravity_and_resume()
            mock_launch.assert_not_awaited()

            fake_req = MagicMock()
            fake_req.json = AsyncMock(return_value={})
            resp = await server.handle_antigravity_launch(fake_req)
            body = json.loads(resp.text)
            assert body["status"] == "error"
            assert body["antigravity"] == "isolated_desktop"
            mock_launch.assert_not_awaited()

    asyncio.run(_run())


def test_game_mode_protects_focus_and_allows_media(tmp_path):
    gm_file = tmp_path / "game_mode_config.json"
    orig_mode = win32_controls._game_mode_setting
    try:
        with patch.object(win32_controls, "GAME_MODE_FILE", gm_file), \
             patch.object(win32_controls, "_apply_gaming_process_priorities"):
            bad = win32_controls.set_game_mode("invalid")
            assert bad["status"] == "error"

            res_on = win32_controls.set_game_mode("on")
            assert res_on["status"] == "ok" and res_on["active"] is True
            assert win32_controls.focus_window(12345) is False
            assert win32_controls.close_active_window() is False
            assert win32_controls.trigger_hotkey("win_d") is False
            assert win32_controls.open_url("https://example.com")["blocked_by_game_mode"] is True
            assert win32_controls.speak_text("hello")["blocked_by_game_mode"] is True
            assert antigravity_bridge.focus_antigravity_window() is False

            # Safe off restores auto/off cleanly
            res_off = win32_controls.set_game_mode("off")
            assert res_off["status"] == "ok" and res_off["active"] is False
    finally:
        win32_controls._game_mode_setting = orig_mode


def test_game_mode_http_endpoints_and_guards(tmp_path):
    async def _run():
        gm_file = tmp_path / "game_mode_config.json"
        orig_mode = win32_controls._game_mode_setting
        with patch.object(win32_controls, "GAME_MODE_FILE", gm_file), \
             patch.object(win32_controls, "_apply_gaming_process_priorities"), \
             patch.object(server, "on_server_startup", new_callable=AsyncMock), \
             patch.object(server, "on_server_shutdown", new_callable=AsyncMock), \
             patch.object(win32_controls, "volume_up") as mock_vol, \
             patch.object(win32_controls, "mouse_click") as mock_click, \
             patch.object(win32_controls, "type_unicode_text") as mock_type:
            app = server.create_app()
            async with TestClient(TestServer(app)) as client:
                r = await client.post("/api/pc/gamemode", json={"mode": "on"})
                assert r.status == 200
                data = await r.json()
                assert data["mode"] == "on" and data["active"] is True

                # Touchpad click & typing blocked unless force=True
                r_mouse = await client.post("/api/pc/mouse", json={"action": "click", "button": "left"})
                assert (await r_mouse.json())["blocked_by_game_mode"] is True
                mock_click.assert_not_called()

                r_mouse_force = await client.post("/api/pc/mouse", json={"action": "click", "button": "left", "force": True})
                assert (await r_mouse_force.json())["status"] == "ok"
                mock_click.assert_called_once_with("left")

                r_type = await client.post("/api/pc/type", json={"text": "wasd"})
                assert (await r_type.json())["blocked_by_game_mode"] is True
                mock_type.assert_not_called()

                # Disruptive hotkey blocked, but volume_up allowed cleanly
                r_hk = await client.post("/api/pc/action", json={"action": "hotkey", "key": "win_d"})
                assert (await r_hk.json())["blocked_by_game_mode"] is True

                r_vol = await client.post("/api/pc/action", json={"action": "volume_up", "steps": 2})
                assert (await r_vol.json())["status"] == "ok"
                mock_vol.assert_called_once_with(2)
        win32_controls._game_mode_setting = orig_mode

    asyncio.run(_run())
