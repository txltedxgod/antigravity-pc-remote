/**
 * PC Remote & Antigravity - Mobile Client Logic
 */

// State
let config = {
  host: localStorage.getItem("pc_remote_host") || (window.location.hostname && window.location.hostname !== "pcremote.local" ? window.location.hostname : "10.0.0.85"),
  port: localStorage.getItem("pc_remote_port") || (window.location.port ? window.location.port : "8765"),
  mac: localStorage.getItem("pc_remote_mac") || "A8-3B-76-76-20-8F"
};

let isConnected = false;
let statusPollTimer = null;

function getApiUrl(path) {
  return `http://${config.host}:${config.port}${path}`;
}

// Haptic feedback via native Android Bridge or browser vibration API
function haptic(ms = 20) {
  try {
    if (window.AndroidBridge && typeof window.AndroidBridge.vibrate === "function") {
      window.AndroidBridge.vibrate(ms);
    } else if (navigator.vibrate) {
      navigator.vibrate(ms);
    }
  } catch (e) {}
}

// Toast notification
function showToast(message, duration = 2500) {
  try {
    if (window.AndroidBridge && typeof window.AndroidBridge.showToast === "function") {
      window.AndroidBridge.showToast(message);
    }
  } catch (e) {}

  const container = document.getElementById("toastContainer");
  if (!container) return;
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = message;
  container.appendChild(t);

  setTimeout(() => t.classList.add("show"), 20);
  setTimeout(() => {
    t.classList.remove("show");
    setTimeout(() => t.remove(), 250);
  }, duration);
}

// Generic API Request
async function apiCall(path, method = "GET", body = null) {
  const url = getApiUrl(path);
  const opts = {
    method,
    headers: { "Content-Type": "application/json" }
  };
  if (body) {
    opts.body = JSON.stringify(body);
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 4000);
  opts.signal = controller.signal;

  try {
    const res = await fetch(url, opts);
    clearTimeout(timeoutId);
    return await res.json();
  } catch (err) {
    clearTimeout(timeoutId);
    throw err;
  }
}

// Poll status
let isSwitchingConv = false;

async function loadConversations() {
  const select = document.getElementById("conversationSelect");
  if (!select || isSwitchingConv) return;

  try {
    const res = await apiCall("/api/antigravity/conversations");
    if (!res || !res.conversations || res.conversations.length === 0) return;

    // Check if options changed
    const currentVal = select.value;
    const items = res.conversations;

    // Build options
    select.innerHTML = "";
    
    // Default current option
    const optCurrent = document.createElement("option");
    optCurrent.value = "";
    optCurrent.textContent = "● Авто (Текущий активный)";
    select.appendChild(optCurrent);

    items.forEach(c => {
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.textContent = c.title + (c.isCurrent ? " (активен)" : "");
      if (c.isCurrent && !currentVal) {
        opt.selected = true;
      } else if (currentVal === c.id) {
        opt.selected = true;
      }
      select.appendChild(opt);
    });
  } catch (e) {}
}

async function pollStatus() {
  try {
    const pcStatus = await apiCall("/api/pc/status");
    setConnectionState(true);

    if (pcStatus.mac_address) {
      config.mac = pcStatus.mac_address;
      localStorage.setItem("pc_remote_mac", pcStatus.mac_address);
    }

    // Update Telemetry
    const cpuStat = document.getElementById("cpuStat");
    const cpuFill = document.getElementById("cpuFill");
    if (cpuStat && cpuFill) {
      const cpu = pcStatus.cpu_pct || 0;
      cpuStat.textContent = `${cpu}%`;
      cpuFill.style.width = `${cpu}%`;
    }

    const ramStat = document.getElementById("ramStat");
    const ramFill = document.getElementById("ramFill");
    if (ramStat && ramFill && pcStatus.memory) {
      const mem = pcStatus.memory;
      ramStat.textContent = `${mem.used_gb} / ${mem.total_gb} ГБ`;
      ramFill.style.width = `${mem.load_pct}%`;
    }

    const activeWin = document.getElementById("activeWindowTitle");
    if (activeWin) {
      activeWin.textContent = pcStatus.active_window || "—";
    }

    // Update Antigravity Status
    const agRes = await apiCall("/api/antigravity/status");
    const agIndicator = document.getElementById("agIndicator");
    const agBadge = document.getElementById("agTagBadge");

    if (agRes && agRes.connected) {
      if (agIndicator) agIndicator.style.backgroundColor = "var(--success)";
      if (agBadge) agBadge.textContent = "ONLINE";
      loadConversations();
    } else {
      if (agIndicator) agIndicator.style.backgroundColor = "var(--text-muted)";
      if (agBadge) agBadge.textContent = "IDLE";
    }

  } catch (err) {
    setConnectionState(false);
  }
}

function setConnectionState(online) {
  isConnected = online;
  const badge = document.getElementById("connBadge");
  const text = document.getElementById("connText");
  const offlineCard = document.getElementById("offlineCard");

  if (offlineCard) {
    offlineCard.style.display = online ? "none" : "flex";
  }

  if (!badge || !text) return;

  if (online) {
    badge.className = "connection-badge online";
    text.textContent = `${config.host}:${config.port}`;
  } else {
    badge.className = "connection-badge offline";
    text.textContent = "Нет связи";
  }
}

// Send Prompt to Antigravity
async function sendPrompt() {
  const input = document.getElementById("promptInput");
  const chkFocus = document.getElementById("chkFocusWindow");
  const btn = document.getElementById("btnSendPrompt");
  const select = document.getElementById("conversationSelect");
  if (!input) return;

  const text = input.value.trim();
  if (!text) {
    showToast("Введи текст сообщения");
    return;
  }

  const convId = select ? select.value : "";

  haptic(35);
  btn.disabled = true;
  btn.innerHTML = `<span>Отправка...</span>`;

  try {
    const res = await apiCall("/api/antigravity/send", "POST", {
      text: text,
      focus: chkFocus ? chkFocus.checked : false,
      conversation_id: convId || null
    });

    if (res.status === "ok") {
      showToast("✨ Сообщение отправлено в Antigravity!");
      input.value = "";
    } else {
      showToast("Ошибка: " + (res.message || "Не удалось отправить"));
    }
  } catch (e) {
    showToast("Ошибка сети при отправке в IDE");
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<span>Отправить в IDE</span>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <line x1="22" y1="2" x2="11" y2="13"></line>
        <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
      </svg>`;
  }
}

// Trigger PC Action
async function triggerAction(action, extra = {}) {
  haptic(25);
  try {
    const body = Object.assign({ action }, extra);
    const res = await apiCall("/api/pc/action", "POST", body);
    if (res.status === "ok") {
      showToast(`Выполнено: ${action}`);
    } else {
      showToast(`Ошибка: ${res.message || action}`);
    }
  } catch (e) {
    showToast(`Ошибка связи с ПК`);
  }
}

// Clipboard Actions
async function refreshClipboard() {
  haptic(20);
  try {
    const res = await apiCall("/api/clipboard");
    const box = document.getElementById("pcClipboardView");
    if (box) {
      box.textContent = res.text || "(Буфер обмена пуст)";
    }
    showToast("Буфер ПК обновлен");
  } catch (e) {
    showToast("Не удалось прочитать буфер ПК");
  }
}

async function sendClipboardText() {
  const input = document.getElementById("clipInput");
  if (!input) return;
  const text = input.value;
  if (!text) return;
  haptic(30);

  try {
    const res = await apiCall("/api/clipboard", "POST", { text });
    if (res.status === "ok") {
      showToast("Текст скопирован в буфер ПК!");
      input.value = "";
      refreshClipboard();
    }
  } catch (e) {
    showToast("Ошибка отправки в буфер ПК");
  }
}

// Setup Event Listeners
function setupEvents() {
  // Navigation Tabs
  const navItems = document.querySelectorAll(".nav-item");
  navItems.forEach(item => {
    item.addEventListener("click", () => {
      haptic(15);
      navItems.forEach(n => n.classList.remove("active"));
      document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));

      item.classList.add("active");
      const targetId = item.getAttribute("data-tab");
      const targetPane = document.getElementById(targetId);
      if (targetPane) targetPane.classList.add("active");
    });
  });

  // Wake-on-LAN Button
  const btnWol = document.getElementById("btnWakeOnLan");
  if (btnWol) {
    btnWol.addEventListener("click", () => {
      haptic(40);
      let sent = false;
      if (window.AndroidBridge && typeof window.AndroidBridge.sendWakeOnLan === "function") {
        sent = window.AndroidBridge.sendWakeOnLan(config.mac || "A83B7676208F");
      }
      if (sent) {
        showToast("⚡ Magic Packet отправлен! Будим ПК...");
      } else {
        showToast("📡 Пакет WoL передан в сеть! Проверяем ПК...");
      }
      // Rapid retry polling
      for (let i = 1; i <= 8; i++) {
        setTimeout(pollStatus, i * 1200);
      }
    });
  }

  // Conversation selector
  const convSelect = document.getElementById("conversationSelect");
  if (convSelect) {
    convSelect.addEventListener("change", async () => {
      haptic(20);
      const val = convSelect.value;
      if (!val) return;
      isSwitchingConv = true;
      showToast("Переключаю диалог в IDE...");
      try {
        await apiCall("/api/antigravity/conversation/switch", "POST", { id: val });
        showToast("✨ Диалог переключен!");
      } catch (e) {
        showToast("Ошибка переключения диалога");
      } finally {
        isSwitchingConv = false;
      }
    });
  }

  // New Chat Button
  const btnNewChat = document.getElementById("btnNewChat");
  if (btnNewChat) {
    btnNewChat.addEventListener("click", async () => {
      haptic(30);
      isSwitchingConv = true;
      showToast("Создаю новый чат в IDE...");
      try {
        await apiCall("/api/antigravity/conversation/new", "POST");
        showToast("✨ Новый чат создан!");
        setTimeout(loadConversations, 1500);
      } catch (e) {
        showToast("Ошибка создания нового чата");
      } finally {
        isSwitchingConv = false;
      }
    });
  }

  // Antigravity Prompt Send
  const btnSend = document.getElementById("btnSendPrompt");
  if (btnSend) btnSend.addEventListener("click", sendPrompt);

  // Quick prompt chips
  document.querySelectorAll(".chip[data-prompt]").forEach(chip => {
    chip.addEventListener("click", () => {
      haptic(15);
      const text = chip.getAttribute("data-prompt");
      const input = document.getElementById("promptInput");
      if (input) {
        input.value = text;
        input.focus();
      }
    });
  });

  // Volume Buttons
  const btnVolDown = document.getElementById("btnVolDown");
  if (btnVolDown) btnVolDown.addEventListener("click", () => triggerAction("volume_down", { steps: 3 }));

  const btnVolUp = document.getElementById("btnVolUp");
  if (btnVolUp) btnVolUp.addEventListener("click", () => triggerAction("volume_up", { steps: 3 }));

  const btnVolMute = document.getElementById("btnVolMute");
  if (btnVolMute) btnVolMute.addEventListener("click", () => triggerAction("volume_mute"));

  // Media Buttons
  const btnMediaPrev = document.getElementById("btnMediaPrev");
  if (btnMediaPrev) btnMediaPrev.addEventListener("click", () => triggerAction("media_prev"));

  const btnMediaPlay = document.getElementById("btnMediaPlay");
  if (btnMediaPlay) btnMediaPlay.addEventListener("click", () => triggerAction("play_pause"));

  const btnMediaNext = document.getElementById("btnMediaNext");
  if (btnMediaNext) btnMediaNext.addEventListener("click", () => triggerAction("media_next"));

  // System Buttons
  const btnLockPc = document.getElementById("btnLockPc");
  if (btnLockPc) btnLockPc.addEventListener("click", () => triggerAction("lock"));

  const btnScreenOff = document.getElementById("btnScreenOff");
  if (btnScreenOff) btnScreenOff.addEventListener("click", () => triggerAction("screen_off"));

  const btnSleepPc = document.getElementById("btnSleepPc");
  if (btnSleepPc) btnSleepPc.addEventListener("click", () => {
    if (confirm("Перевести компьютер в спящий режим?")) {
      triggerAction("sleep");
    }
  });

  // App Cards
  document.querySelectorAll(".app-card[data-app]").forEach(card => {
    card.addEventListener("click", () => {
      const app = card.getAttribute("data-app");
      triggerAction("launch", { app });
    });
  });

  // Clipboard
  const btnRefreshClip = document.getElementById("btnRefreshClip");
  if (btnRefreshClip) btnRefreshClip.addEventListener("click", refreshClipboard);

  const btnSendClip = document.getElementById("btnSendClip");
  if (btnSendClip) btnSendClip.addEventListener("click", sendClipboardText);

  // Settings Modal
  const modal = document.getElementById("settingsModal");
  const btnSettings = document.getElementById("btnSettings");
  const btnCancel = document.getElementById("btnCancelSettings");
  const btnSave = document.getElementById("btnSaveSettings");
  const hostInput = document.getElementById("serverHostInput");
  const portInput = document.getElementById("serverPortInput");

  if (btnSettings && modal) {
    btnSettings.addEventListener("click", () => {
      haptic(20);
      if (hostInput) hostInput.value = config.host;
      if (portInput) portInput.value = config.port;
      modal.classList.add("open");
    });
  }

  if (btnCancel && modal) {
    btnCancel.addEventListener("click", () => {
      haptic(15);
      modal.classList.remove("open");
    });
  }

  if (btnSave && modal) {
    btnSave.addEventListener("click", () => {
      haptic(25);
      const newHost = hostInput ? hostInput.value.trim() : "";
      const newPort = portInput ? portInput.value.trim() : "";
      if (newHost) {
        config.host = newHost;
        localStorage.setItem("pc_remote_host", newHost);
      }
      if (newPort) {
        config.port = newPort;
        localStorage.setItem("pc_remote_port", newPort);
      }
      modal.classList.remove("open");
      showToast("Настройки сохранены. Проверка связи...");
      pollStatus();
    });
  }
}

// Init
window.addEventListener("DOMContentLoaded", () => {
  setupEvents();
  pollStatus();
  statusPollTimer = setInterval(pollStatus, 2500);
});
