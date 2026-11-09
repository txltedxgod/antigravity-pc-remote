import asyncio
import aiohttp
import sys
from pathlib import Path

# Add server dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

import win32_controls
import server

async def test_endpoints():
    app = server.create_app()
    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, "127.0.0.1", 8769)
    await site.start()
    print("Test server started on 127.0.0.1:8769")

    async with aiohttp.ClientSession() as s:
        # 1. Ping
        async with s.get("http://127.0.0.1:8769/api/ping") as r:
            ping_data = await r.json()
            print("Ping:", ping_data)
            assert ping_data["status"] == "ok"

        # 2. PC Status
        async with s.get("http://127.0.0.1:8769/api/pc/status") as r:
            status_data = await r.json()
            print("Status:", status_data)
            assert "cpu_pct" in status_data
            assert "memory" in status_data

        # 3. Clipboard Get / Set
        async with s.post("http://127.0.0.1:8769/api/clipboard", json={"text": "PCRemote_Test_String_123"}) as r:
            clip_res = await r.json()
            print("Clip Set:", clip_res)
            assert clip_res["status"] == "ok"

        async with s.get("http://127.0.0.1:8769/api/clipboard") as r:
            clip_get = await r.json()
            print("Clip Get:", clip_get)
            assert clip_get["text"] == "PCRemote_Test_String_123"

        # 4. Antigravity status
        async with s.get("http://127.0.0.1:8769/api/antigravity/status") as r:
            ag_status = await r.json()
            print("Antigravity Status:", ag_status)
            assert "connected" in ag_status

        # 5. Antigravity Chat Endpoint
        async with s.get("http://127.0.0.1:8769/api/antigravity/chat") as r:
            chat_res = await r.json()
            print("Chat status:", chat_res.get("status"), "Agent status:", chat_res.get("agent_status"))
            assert chat_res["status"] == "ok"
            assert "agent_status" in chat_res
            assert "messages" in chat_res

    await runner.cleanup()
    print("All server endpoint tests passed successfully!")

if __name__ == "__main__":
    asyncio.run(test_endpoints())
