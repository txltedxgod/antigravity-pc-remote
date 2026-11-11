@echo off
title Fix Antigravity & PC Remote Desktop Session
echo Closing stuck/hidden Antigravity and pythonw processes...
taskkill /F /IM Antigravity.exe >nul 2>&1
taskkill /F /IM pythonw.exe >nul 2>&1
taskkill /F /IM cloudflared.exe >nul 2>&1
timeout /t 2 /nobreak >nul
echo Starting PC Remote Watchdog on your visible Default desktop...
start "" wscript.exe "C:\Users\dev\.gemini\antigravity\scratch\pc_remote\server\start_server.vbs"
echo Launching Antigravity IDE...
start "" "C:\Users\dev\AppData\Local\Programs\antigravity\Antigravity.exe"
echo Done! Antigravity will appear in a few seconds.
timeout /t 3 /nobreak >nul
