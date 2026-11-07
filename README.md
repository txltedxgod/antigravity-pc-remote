# Antigravity PC Remote & IDE Bridge 📱⚡

[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![Android SDK 34](https://img.shields.io/badge/Android-SDK%2034-3DDC84?style=flat&logo=android&logoColor=white)](https://developer.android.com/)
[![Windows 11](https://img.shields.io/badge/Windows-11%20Win32-0078D4?style=flat&logo=windows&logoColor=white)](https://microsoft.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An ultra-lightweight, high-performance Windows PC remote control server and companion Android mobile application with **direct prompt injection into Google Antigravity 2.0 / Antigravity IDE** via the Chrome DevTools Protocol (CDP).

Control your PC, manage media and system power, monitor hardware telemetry in real time, and converse with your Antigravity agent right from your smartphone.

---

## 🌟 Key Features

### 1. 🤖 Antigravity 2.0 IDE Direct Injection
- **Live Prompt Delivery**: Injects text directly into the chat input (`[aria-label="Message input"]`) and triggers submission via CDP `Input.insertText` and `Input.dispatchKeyEvent`.
- **Dynamic Conversation Switching**: Browse all active/recent Antigravity sessions in a dropdown and switch conversations on the fly.
- **One-Tap New Chat**: Creates clean, fresh conversations in Antigravity (`button[aria-label="New Conversation"]`) straight from your phone.
- **Auto-Launch Antigravity**: If the Antigravity desktop app isn't running when you send a prompt, the server automatically launches it, waits for DevTools to bind, and delivers the message.
- **Optional PC Window Focus**: Bring the Antigravity window to the foreground on the PC display when sending.

### 2. 🎛️ Win32 Hardware & Multimedia Controls
- **System Volume**: `-5%`, `+5%`, `Mute` via native Win32 virtual keys (`VK_VOLUME_DOWN`, `VK_VOLUME_UP`, `VK_VOLUME_MUTE`).
- **Media Playback**: `Play/Pause`, `Next`, `Previous`, `Stop`.
- **Power & Display**:
  - 🔒 **Lock Workstation**: Instant lock via `LockWorkStation()`.
  - 🖥️ **Screen Off**: Puts monitors into power-saving standby via `SC_MONITORPOWER`.
  - 🌙 **Sleep Mode**: Suspends workstation via `SetSuspendState()`.

### 3. 🚀 1-Tap Application Launchers
- 👻 **SteamGhost**: Headless background game idle runner.
- 🎮 **Steam**: Native client URI (`steam://open/main`).
- 💬 **Telegram**: Desktop messenger (`tg://`).
- 🌐 **Chrome**: Google Chrome browser.
- ⚙️ **Task Manager**: Windows task manager (`taskmgr`).
- 📁 **File Explorer**: Windows Explorer.

### 4. 📊 Live Telemetry & Clipboard Bridge
- **Hardware Telemetry**: Real-time CPU % (via `GetSystemTimes`) and RAM utilization (GB used/total via `GlobalMemoryStatusEx`).
- **Active Window**: Detects and displays the current foreground window title on the PC.
- **Bidirectional Clipboard**: Read current PC clipboard text on phone or send text/links from phone straight to PC clipboard.

### 5. ⚡ 24/7 Availability & Sleep Management
- **Windows Away Mode**: Keeps CPU, Wi-Fi, and server running in low-power mode (`ES_AWAYMODE_REQUIRED`) while allowing screens to sleep.
- **Native Wake-on-LAN (WoL)**: If the PC is suspended, tap **"⚡ Wake PC (Wake-on-LAN)"** in the app to broadcast a magic packet to the PC's MAC address (`A8:3B:76:76:20:8F`) and resume it in seconds.
- **Silent Windows Startup**: Auto-launches silently in the background on user login via Windows Startup (`shell:startup`).

---

## 🏛️ Architecture

```
┌──────────────────────────────────────┐                ┌──────────────────────────────────────┐
│           Android Mobile App         │                │            Windows 11 Host           │
│   (WebView + Native Java Bridge)     │    LAN/HTTP    │      (aiohttp Server on :8765)       │
│                                      │ ─────────────► │                                      │
│  • Antigravity Chat & Selector       │                │  • /api/antigravity/send             │
│  • Media & Volume Controls           │                │    ├── Chrome DevTools WS (:60382)   │
│  • System Power & App Launchers      │                │    └── Win32 Focus / Paste Fallback  │
│  • Live Telemetry Gauges             │                │  • /api/antigravity/conversations    │
│  • Bidirectional Clipboard Sync      │                │  • /api/pc/action (Win32 keybd_event)│
│  • Native Wake-on-LAN Magic Packet   │ ──(WoL UDP 9)─►│  • /api/pc/status (RAM / CPU stats)  │
└──────────────────────────────────────┘                │  • /api/clipboard (win32clipboard)   │
                                                        └──────────────────────────────────────┘
```

---

## 🚀 Quick Start

### 1. Requirements
- **Host**: Windows 10/11 with Python 3.10+ (`aiohttp`, `pywin32`).
- **Client**: Any Android phone (Android 7.0+) or any mobile web browser.

### 2. Run the Host Server
```bash
cd server
python server.py
```
Or double-click `Start PC Remote Server.bat` on Windows.

### 3. Connect from Mobile
- **Instant Web Access**: Open `http://<YOUR_PC_LAN_IP>:8765/` in your phone browser.
- **Install Android APK**: Download the standalone signed APK directly from `http://<YOUR_PC_LAN_IP>:8765/download/apk`.

---

## 📦 Building the Android APK

The repository includes a self-contained zero-Gradle build script `build_apk.py` that compiles, dexes, aligns, and signs standalone Android APKs in ~5 seconds using the Android SDK command-line tools:

```bash
python build_apk.py
```
**Artifacts Generated**:
- `android/dist/PCRemote.apk` (~95 KB, signed with v2/v3 signatures).

---

## 🔒 Security
- Bound to local network interfaces by default.
- Strict cross-origin headers configured for local intranet IP ranges.
- Zero external third-party telemetry or cloud dependencies.

---

## 📄 License
MIT License. Free for personal and commercial open-source use.
