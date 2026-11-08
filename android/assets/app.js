/**
 * Antigravity PC Remote 2.0 - Client Application Logic
 * Real-time AI chat stream, smart agent telemetry, PC hardware controls.
 */

// Global Configuration & State
let config = {
  host: localStorage.getItem("pc_remote_host") || (window.location.hostname && window.location.hostname !== "pcremote.local" ? window.location.hostname : "10.0.0.85"),
  port: localStorage.getItem("pc_remote_port") || (window.location.port ? window.location.port : "8765"),
  mac: localStorage.getItem("pc_remote_mac") || "A8-3B-76-76-20-8F"
};

let isConnected = false;
let activeTab = "tabAntigravity";
let currentConvId = "";
let lastMessageCount = 0;
let isSwitchingConv = false;
let isSendingPrompt = false;

let chatPollTimer = null;
let statusPollTimer = null;
let cloudUrl = localStorage.getItem("pc_remote_cloud_url") || "";

// Auto-Discovery of Live Cloudflare Tunnel URL from GitHub Repository
let isDiscoveringCloud = false;
async function discoverCloudUrl(force = false) {
  if (isDiscoveringCloud) return cloudUrl;
  isDiscoveringCloud = true;
  try {
    const res = await fetch(`https://api.github.com/repos/txltedxgod/antigravity-pc-remote/contents/server/tunnel_url.txt?t=${Date.now()}`, {
      headers: { "Accept": "application/vnd.github.v3+json" }
    });
    if (!res.ok) return cloudUrl;
    const data = await res.json();
    if (data && data.content) {
      const decoded = atob(data.content.replace(/\s/g, "")).trim();
      if (decoded.startsWith("https://") && decoded.includes("trycloudflare.com")) {
        cloudUrl = decoded;
        localStorage.setItem("pc_remote_cloud_url", decoded);
        const disp = document.getElementById("cloudUrlDisplay");
        if (disp) disp.value = decoded;
        return decoded;
      }
    }
  } catch (e) {
    console.error("Cloud discovery error:", e);
  } finally {
    isDiscoveringCloud = false;
  }
  return cloudUrl;
}

// Helpers: Intelligent URL Resolver (LAN IP vs Global Cloudflare Tunnel)
function getApiUrl(path) {
  let host = (config.host || "").trim();
  
  if (host.startsWith("http://") || host.startsWith("https://")) {
    const clean = host.replace(/\/+$/, "");
    if (clean.includes("trycloudflare.com") || clean.includes("ngrok") || clean.includes("loca.lt")) {
      return `${clean}${path}`;
    }
    if (config.port && config.port !== "80" && config.port !== "443" && !clean.includes(":")) {
      return `${clean}:${config.port}${path}`;
    }
    return `${clean}${path}`;
  }

  if (host.includes("trycloudflare.com") || host.includes("ngrok") || host.includes("loca.lt")) {
    return `https://${host}${path}`;
  }

  const port = config.port ? `:${config.port}` : ":8765";
  return `http://${host}${port}${path}`;
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

// Native or DOM Toast notification
function showToast(message, duration = 2400) {
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

// Generic Fetch Wrapper with Smart Cloudflare Auto-Fallback
async function apiCall(path, method = "GET", body = null) {
  const url = getApiUrl(path);
  const opts = {
    method,
    headers: { "Content-Type": "application/json" }
  };
  if (body) {
    opts.body = JSON.stringify(body);
  }

  const isLocalHost = !config.host.includes("trycloudflare.com");
  const timeoutMs = isLocalHost ? 3000 : 8000;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  opts.signal = controller.signal;

  try {
    const res = await fetch(url, opts);
    clearTimeout(timeoutId);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    clearTimeout(timeoutId);

    // Intelligent Auto-Fallback: if local Wi-Fi drops or unreachable, seamlessly retry via Cloudflare
    if (isLocalHost) {
      let targetCloud = cloudUrl;
      if (!targetCloud) {
        targetCloud = await discoverCloudUrl();
      }

      if (targetCloud) {
        try {
          const cloudBase = targetCloud.replace(/\/+$/, "");
          const fallbackUrl = `${cloudBase}${path}`;
          const retryRes = await fetch(fallbackUrl, {
            method,
            headers: { "Content-Type": "application/json" },
            body: body ? JSON.stringify(body) : undefined
          });
          if (retryRes.ok) {
            config.host = targetCloud;
            localStorage.setItem("pc_remote_host", targetCloud);
            showToast("🌐 Подключено через интернет (Cloudflare)!");
            return await retryRes.json();
          }
        } catch (e) {}
      }

      // If stored cloudUrl also failed, force discover fresh URL from GitHub:
      const freshCloud = await discoverCloudUrl(true);
      if (freshCloud && freshCloud !== targetCloud) {
        try {
          const freshBase = freshCloud.replace(/\/+$/, "");
          const freshRes = await fetch(`${freshBase}${path}`, {
            method,
            headers: { "Content-Type": "application/json" },
            body: body ? JSON.stringify(body) : undefined
          });
          if (freshRes.ok) {
            config.host = freshCloud;
            localStorage.setItem("pc_remote_host", freshCloud);
            showToast("🌐 Найден новый Cloudflare туннель!");
            return await freshRes.json();
          }
        } catch (e2) {}
      }
    }
    throw err;
  }
}

// --- LIGHTWEIGHT CLIENT-SIDE MARKDOWN RENDERER ---
function escapeHtml(str) {
  if (!str) return "";
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderMarkdown(md) {
  if (!md) return "";

  // 1. Extract code blocks first to protect formatting
  const codeBlocks = [];
  let text = md.replace(/```([a-zA-Z0-9_\-\.]*)\n([\s\S]*?)```/g, (match, lang, code) => {
    const id = `__CODE_BLOCK_${codeBlocks.length}__`;
    codeBlocks.push({ lang: lang || "code", code: code.trimEnd() });
    return id;
  });

  // 2. Escape HTML
  text = escapeHtml(text);

  // 3. Inline code
  text = text.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');

  // 4. Headers
  text = text.replace(/^### (.*$)/gim, '<h4 class="md-h4">$1</h4>');
  text = text.replace(/^## (.*$)/gim, '<h3 class="md-h3">$1</h3>');
  text = text.replace(/^# (.*$)/gim, '<h3 class="md-h3">$1</h3>');

  // 5. Bold & Italic
  text = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/\*(.*?)\*/g, '<em>$1</em>');

  // 6. Blockquotes
  text = text.replace(/^>\s?(.*$)/gim, '<blockquote>$1</blockquote>');

  // 7. Unordered lists
  text = text.replace(/^\s*[-*]\s+(.*$)/gim, '<li>$1</li>');
  text = text.replace(/(<li>.*<\/li>)/gim, '<ul>$1</ul>');

  // 8. Numbered lists
  text = text.replace(/^\s*\d+\.\s+(.*$)/gim, '<li>$1</li>');

  // 9. Paragraphs and line breaks
  text = text.replace(/\n\n+/g, '</p><p>');
  text = text.replace(/\n/g, '<br>');
  text = `<p>${text}</p>`;

  // Clean empty paragraphs
  text = text.replace(/<p>\s*<\/p>/g, '');

  // 10. Restore code blocks with 1-click Copy button
  codeBlocks.forEach((item, index) => {
    const placeholder = `__CODE_BLOCK_${index}__`;
    const escapedCode = escapeHtml(item.code);
    const htmlBlock = `
      <div class="code-block">
        <div class="code-header">
          <span class="code-lang">${escapeHtml(item.lang)}</span>
          <button class="btn-copy-code" data-code="${encodeURIComponent(item.code)}">📋 Скопировать</button>
        </div>
        <pre><code>${escapedCode}</code></pre>
      </div>
    `;
    text = text.replace(placeholder, htmlBlock);
  });

  return text;
}

// Copy Code Handler
document.addEventListener("click", (e) => {
  if (e.target && e.target.classList.contains("btn-copy-code")) {
    const code = decodeURIComponent(e.target.getAttribute("data-code") || "");
    if (!code) return;
    haptic(25);
    
    // Copy to clipboard
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(code).then(() => {
        showToast("Код скопирован в буфер!");
      });
    } else {
      const ta = document.createElement("textarea");
      ta.value = code;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
      showToast("Код скопирован!");
    }
  }
});

// --- CHAT LOGIC ---
async function fetchChat(autoScroll = true) {
  const feed = document.getElementById("chatFeed");
  const emptyState = document.getElementById("chatEmptyState");
  const activityBanner = document.getElementById("agentActivityBanner");
  const activityText = document.getElementById("agentActivityText");
  const statusPill = document.getElementById("agentStatusPill");
  const statusLabel = document.getElementById("agentStatusLabel");
  if (!feed) return;

  try {
    const params = currentConvId ? `?conversation_id=${encodeURIComponent(currentConvId)}` : "";
    const res = await apiCall(`/api/antigravity/chat${params}`);
    if (!res || res.status !== "ok") return;

    if (res.conversation_id && !currentConvId) {
      currentConvId = res.conversation_id;
    }

    // Update Agent Live Status
    const agentStatus = res.agent_status || "idle";
    const currentAction = res.current_action || "";

    if (statusPill && statusLabel) {
      statusPill.className = "agent-live-pill " + agentStatus;
      if (agentStatus === "executing") {
        statusLabel.textContent = "Действие...";
      } else if (agentStatus === "thinking") {
        statusLabel.textContent = "Думает...";
      } else if (agentStatus === "idle") {
        statusLabel.textContent = "Онлайн";
      } else {
        statusLabel.textContent = "Офлайн";
      }
    }

    // Update Activity Banner
    if (activityBanner && activityText) {
      if (agentStatus === "executing" || agentStatus === "thinking") {
        activityBanner.style.display = "flex";
        activityText.textContent = currentAction ? `⚙️ ${currentAction}` : "Агент выполняет задачу...";
      } else {
        activityBanner.style.display = "none";
      }
    }

    // Render Messages if count changed or empty
    const messages = res.messages || [];
    if (messages.length === 0) {
      if (emptyState) emptyState.style.display = "flex";
      feed.querySelectorAll(".chat-msg").forEach(m => m.remove());
      lastMessageCount = 0;
      return;
    }

    if (emptyState) emptyState.style.display = "none";

    // Only re-render if count or last message changed
    if (messages.length !== lastMessageCount || isSendingPrompt) {
      lastMessageCount = messages.length;
      isSendingPrompt = false;

      // Keep emptyState element, remove old bubbles
      feed.querySelectorAll(".chat-msg").forEach(m => m.remove());

      messages.forEach(msg => {
        const bubble = document.createElement("div");
        bubble.className = `chat-msg msg-${msg.role}`;
        
        let timeStr = "";
        if (msg.created_at) {
          try {
            const d = new Date(msg.created_at);
            timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
          } catch (e) {}
        }

        if (msg.role === "user") {
          bubble.innerHTML = `
            <div class="msg-bubble-user">${escapeHtml(msg.text)}</div>
            <div class="msg-meta">${timeStr}</div>
          `;
        } else {
          bubble.innerHTML = `
            <div class="msg-bubble-assistant">${renderMarkdown(msg.text)}</div>
            <div class="msg-meta">
              <span>Antigravity</span>
              ${timeStr ? `<span>• ${timeStr}</span>` : ""}
            </div>
          `;
        }
        feed.appendChild(bubble);
      });

      if (autoScroll) {
        setTimeout(() => {
          feed.scrollTop = feed.scrollHeight;
        }, 30);
      }
    }
  } catch (err) {
    // Network fail or PC sleep
  }
}

// Send Prompt to Antigravity
async function sendPrompt() {
  const input = document.getElementById("promptInput");
  const chkFocus = document.getElementById("chkFocusWindow");
  const btn = document.getElementById("btnSendPrompt");
  if (!input || !btn) return;

  const text = input.value.trim();
  if (!text) {
    showToast("Введи текст сообщения");
    return;
  }

  haptic(35);
  btn.disabled = true;
  isSendingPrompt = true;

  // Optimistic UI: append user message to feed immediately
  const feed = document.getElementById("chatFeed");
  const emptyState = document.getElementById("chatEmptyState");
  if (emptyState) emptyState.style.display = "none";

  if (feed) {
    const bubble = document.createElement("div");
    bubble.className = "chat-msg msg-user";
    const nowStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    bubble.innerHTML = `
      <div class="msg-bubble-user">${escapeHtml(text)}</div>
      <div class="msg-meta">${nowStr}</div>
    `;
    feed.appendChild(bubble);
    feed.scrollTop = feed.scrollHeight;
  }

  // Clear input
  input.value = "";
  input.style.height = "auto";

  // Show activity banner right away
  const activityBanner = document.getElementById("agentActivityBanner");
  const activityText = document.getElementById("agentActivityText");
  if (activityBanner && activityText) {
    activityBanner.style.display = "flex";
    activityText.textContent = "Передано в Antigravity. Ожидание ответа...";
  }

  try {
    const res = await apiCall("/api/antigravity/send", "POST", {
      text: text,
      focus: chkFocus ? chkFocus.checked : false,
      conversation_id: currentConvId || null
    });

    if (res.status === "ok") {
      showToast("✨ Запрос отправлен в IDE!");
      // Rapid poll to catch thinking & responses
      for (let i = 1; i <= 6; i++) {
        setTimeout(() => fetchChat(true), i * 800);
      }
    } else {
      showToast("Ошибка: " + (res.message || "Не удалось отправить"));
    }
  } catch (e) {
    showToast("Ошибка связи с сервером Antigravity");
  } finally {
    btn.disabled = false;
  }
}

// Load Conversations Dropdown
async function loadConversations() {
  const select = document.getElementById("conversationSelect");
  if (!select || isSwitchingConv) return;

  try {
    const res = await apiCall("/api/antigravity/conversations");
    if (!res || !res.conversations || res.conversations.length === 0) return;

    const currentVal = select.value || currentConvId;
    const items = res.conversations;

    select.innerHTML = "";
    
    // Auto active option
    const optCurrent = document.createElement("option");
    optCurrent.value = "";
    optCurrent.textContent = "● Текущий активный чат";
    select.appendChild(optCurrent);

    items.forEach(c => {
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.textContent = c.title + (c.isCurrent ? " (активен)" : "");
      if (c.isCurrent && !currentVal) {
        opt.selected = true;
        currentConvId = c.id;
      } else if (currentVal === c.id) {
        opt.selected = true;
      }
      select.appendChild(opt);
    });
  } catch (e) {}
}

// --- TELEMETRY & STATUS ---
async function pollStatus() {
  try {
    const pcStatus = await apiCall("/api/pc/status");
    setConnectionState(true);

    if (pcStatus.mac_address) {
      config.mac = pcStatus.mac_address;
      localStorage.setItem("pc_remote_mac", pcStatus.mac_address);
    }

    // Telemetry Gauges
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

    // SteamGhost status badge
    const ghostDesc = document.getElementById("ghostStatusDesc");
    if (ghostDesc) {
      if (pcStatus.steamghost_running) {
        ghostDesc.textContent = "● РАБОТАЕТ (Idle)";
        ghostDesc.style.color = "var(--success)";
      } else {
        ghostDesc.textContent = "Фоновый idle игр";
        ghostDesc.style.color = "var(--text-muted)";
      }
    }

    // Cloudflare Tunnel URL auto-detection
    if (pcStatus.cloud_url) {
      cloudUrl = pcStatus.cloud_url;
      localStorage.setItem("pc_remote_cloud_url", pcStatus.cloud_url);
      const box = document.getElementById("cloudTunnelInfoBox");
      const disp = document.getElementById("cloudUrlDisplay");
      if (box && disp) {
        box.style.display = "block";
        disp.value = pcStatus.cloud_url;
      }
    }

    // Antigravity Bridge Status
    const agRes = await apiCall("/api/antigravity/status");
    if (agRes && agRes.connected) {
      loadConversations();
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
    badge.className = "connection-pill online";
    text.textContent = `${config.host}:${config.port}`;
  } else {
    badge.className = "connection-pill offline";
    text.textContent = "Офлайн";
  }
}

// PC Action Trigger
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

// Active Windows & Process Manager
async function fetchActiveWindows() {
  const container = document.getElementById("activeWindowsList");
  if (!container) return;

  try {
    const res = await apiCall("/api/pc/windows");
    if (!res || !res.windows) return;

    if (res.windows.length === 0) {
      container.innerHTML = '<div style="text-align: center; color: var(--text-muted); font-size: 12px; padding: 12px 0;">Нет активных видимых окон</div>';
      return;
    }

    container.innerHTML = "";
    res.windows.forEach(w => {
      const item = document.createElement("div");
      item.className = "window-item";

      let icon = "🪟";
      const p = (w.process_name || "").toLowerCase();
      const t = (w.title || "").toLowerCase();
      if (p.includes("ghost") || t.includes("ghost")) icon = "👻";
      else if (p.includes("cs2") || t.includes("counter-strike")) icon = "🔫";
      else if (p.includes("dota") || t.includes("dota")) icon = "🛡️";
      else if (p.includes("taxi")) icon = "🚖";
      else if (p.includes("rust")) icon = "🎯";
      else if (p.includes("banana")) icon = "🍌";
      else if (p.includes("chrome") || p.includes("firefox") || p.includes("edge")) icon = "🌐";
      else if (p.includes("telegram")) icon = "💬";
      else if (p.includes("discord")) icon = "🎙️";
      else if (p.includes("steam")) icon = "🎮";
      else if (p.includes("spotify")) icon = "🎵";
      else if (p.includes("terminal") || p.includes("cmd") || p.includes("powershell")) icon = "💻";

      item.innerHTML = `
        <div class="window-item-left">
          <div class="window-icon-badge">${icon}</div>
          <div class="window-info">
            <span class="window-title" title="${escapeHtml(w.title)}">${escapeHtml(w.title)}</span>
            <span class="window-pname">${escapeHtml(w.process_name || "pid: " + w.pid)}</span>
          </div>
        </div>
        <div class="window-actions">
          <button class="btn-window-action btn-window-focus" title="Вывести на экран">Фокус</button>
          <button class="btn-window-action btn-window-kill" title="Закрыть программу">Закрыть</button>
        </div>
      `;

      item.querySelector(".btn-window-focus").addEventListener("click", () => {
        triggerAction("focus_window", { hwnd: w.hwnd });
        showToast(`🎯 Фокус: ${w.title.slice(0, 20)}`);
      });

      item.querySelector(".btn-window-kill").addEventListener("click", () => {
        triggerAction("close_window", { hwnd: w.hwnd, force: true });
        showToast(`🛑 Закрыто: ${w.process_name || w.title.slice(0, 15)}`);
        setTimeout(fetchActiveWindows, 600);
      });

      container.appendChild(item);
    });
  } catch (e) {
    if (container) {
      container.innerHTML = '<div style="text-align: center; color: var(--text-muted); font-size: 12px; padding: 12px 0;">Ошибка получения списка окон</div>';
    }
  }
}

// Clipboard Functions
async function refreshClipboard() {
  haptic(20);
  try {
    const res = await apiCall("/api/clipboard");
    const box = document.getElementById("pcClipboardView");
    if (box) {
      box.textContent = res.text || "(Буфер обмена ПК пуст)";
    }
    showToast("Буфер ПК прочитан");
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
      showToast("Скопировано в буфер ПК!");
      input.value = "";
      refreshClipboard();
    }
  } catch (e) {
    showToast("Ошибка отправки в буфер ПК");
  }
}

// --- EVENT LISTENERS SETUP ---
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
      activeTab = targetId;
      const targetPane = document.getElementById(targetId);
      if (targetPane) targetPane.classList.add("active");

      if (targetId === "tabAntigravity") {
        fetchChat(true);
      } else if (targetId === "tabControls" || targetId === "tabApps") {
        fetchActiveWindows();
      }
    });
  });

  // Prompt Auto-resize Textarea
  const promptInput = document.getElementById("promptInput");
  if (promptInput) {
    promptInput.addEventListener("input", () => {
      promptInput.style.height = "auto";
      promptInput.style.height = Math.min(promptInput.scrollHeight, 100) + "px";
    });

    promptInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendPrompt();
      }
    });
  }

  // Send Button
  const btnSend = document.getElementById("btnSendPrompt");
  if (btnSend) btnSend.addEventListener("click", sendPrompt);

  // Quick Chips
  document.querySelectorAll(".chip[data-prompt]").forEach(chip => {
    chip.addEventListener("click", () => {
      haptic(15);
      const text = chip.getAttribute("data-prompt");
      if (promptInput) {
        promptInput.value = text;
        promptInput.focus();
        promptInput.style.height = "auto";
      }
    });
  });

  // Conversation Selector
  const convSelect = document.getElementById("conversationSelect");
  if (convSelect) {
    convSelect.addEventListener("change", async () => {
      haptic(20);
      const val = convSelect.value;
      currentConvId = val;
      isSwitchingConv = true;
      lastMessageCount = 0;
      showToast("Переключаю диалог...");
      try {
        if (val) {
          await apiCall("/api/antigravity/conversation/switch", "POST", { id: val });
        }
        await fetchChat(true);
      } catch (e) {
        showToast("Ошибка переключения чата");
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
      lastMessageCount = 0;
      currentConvId = "";
      showToast("Создаю новый диалог в IDE...");
      try {
        await apiCall("/api/antigravity/conversation/new", "POST");
        showToast("✨ Новый диалог создан!");
        setTimeout(() => {
          loadConversations();
          fetchChat(true);
        }, 1200);
      } catch (e) {
        showToast("Ошибка создания чата");
      } finally {
        isSwitchingConv = false;
      }
    });
  }

  // Wake-on-LAN Button
  const btnWol = document.getElementById("btnWakeOnLan");
  if (btnWol) {
    btnWol.addEventListener("click", () => {
      haptic(40);
      let sent = false;
      if (window.AndroidBridge && typeof window.AndroidBridge.sendWakeOnLan === "function") {
        sent = window.AndroidBridge.sendWakeOnLan(config.mac || "A83B7676208F");
      }
      showToast("📡 Пакет WoL отправлен! Проверяем ПК...");
      for (let i = 1; i <= 8; i++) {
        setTimeout(pollStatus, i * 1200);
      }
    });
  }

  // Connect via Cloudflare from offline banner
  const btnConnectCloud = document.getElementById("btnConnectCloud");
  if (btnConnectCloud) {
    btnConnectCloud.addEventListener("click", async () => {
      haptic(30);
      showToast("🔍 Ищу ПК через Cloudflare...");
      const url = await discoverCloudUrl(true);
      if (url) {
        config.host = url;
        localStorage.setItem("pc_remote_host", url);
        showToast("🌐 Найдено! Подключаюсь...");
        pollStatus();
        fetchChat(true);
      } else {
        showToast("Не удалось обнаружить Cloudflare URL в сети");
      }
    });
  }

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

  const btnRestartPc = document.getElementById("btnRestartPc");
  if (btnRestartPc) btnRestartPc.addEventListener("click", () => {
    if (confirm("⚠️ Перезагрузить ПК через 5 секунд? У вас будет возможность отменить.")) {
      triggerAction("restart");
      showToast("⏳ Перезагрузка через 5 сек! Нажмите «Отмена ребута», если нужно остановить.");
    }
  });

  const btnCancelRestart = document.getElementById("btnCancelRestart");
  if (btnCancelRestart) btnCancelRestart.addEventListener("click", () => {
    triggerAction("cancel_restart");
  });

  // Browser Search
  const webSearchInput = document.getElementById("webSearchInput");
  const btnOpenWebSearch = document.getElementById("btnOpenWebSearch");
  const handleOpenWeb = () => {
    if (!webSearchInput) return;
    const q = webSearchInput.value.trim();
    if (!q) {
      showToast("Введите URL или поисковый запрос");
      return;
    }
    triggerAction("open_url", { text: q });
    showToast(`🌐 Открываю на ПК: ${q}`);
  };

  if (btnOpenWebSearch) btnOpenWebSearch.addEventListener("click", handleOpenWeb);
  if (webSearchInput) {
    webSearchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleOpenWeb();
      }
    });
  }

  // Window Hotkeys Pad
  const btnHotCloseActive = document.getElementById("btnHotCloseActive");
  if (btnHotCloseActive) {
    btnHotCloseActive.addEventListener("click", () => {
      triggerAction("close_active");
      setTimeout(fetchActiveWindows, 700);
    });
  }

  const hotkeysMap = {
    btnHotMinimizeAll: "win_d",
    btnHotAltTab: "alt_tab",
    btnHotCloseTab: "ctrl_w",
    btnHotFullscreen: "f11",
    btnHotRefresh: "f5",
    btnHotEsc: "esc",
    btnHotEnter: "enter",
    btnHotSpace: "space",
  };

  Object.entries(hotkeysMap).forEach(([btnId, key]) => {
    const el = document.getElementById(btnId);
    if (el) {
      el.addEventListener("click", () => triggerAction("hotkey", { key }));
    }
  });

  // Windows List Refresh
  const btnRefWin = document.getElementById("btnRefreshWindows");
  if (btnRefWin) {
    btnRefWin.addEventListener("click", () => {
      haptic(20);
      fetchActiveWindows();
      showToast("Список окон обновлён");
    });
  }

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
      const box = document.getElementById("cloudTunnelInfoBox");
      const disp = document.getElementById("cloudUrlDisplay");
      if (box && disp && cloudUrl) {
        box.style.display = "block";
        disp.value = cloudUrl;
      }
      modal.classList.add("open");
    });
  }

  const btnUseCloud = document.getElementById("btnUseCloudUrl");
  if (btnUseCloud) {
    btnUseCloud.addEventListener("click", () => {
      haptic(25);
      const disp = document.getElementById("cloudUrlDisplay");
      if (disp && hostInput && disp.value) {
        hostInput.value = disp.value;
        showToast("Cloudflare URL вставлен в поле!");
      }
    });
  }

  const btnAutoDiscover = document.getElementById("btnAutoDiscoverCloud");
  if (btnAutoDiscover) {
    btnAutoDiscover.addEventListener("click", async () => {
      haptic(25);
      showToast("🔍 Поиск Cloudflare URL на GitHub...");
      const url = await discoverCloudUrl(true);
      if (url) {
        const disp = document.getElementById("cloudUrlDisplay");
        if (disp) disp.value = url;
        if (hostInput) hostInput.value = url;
        showToast("✅ URL найден и вставлен в поле!");
      } else {
        showToast("URL не найден в репозитории");
      }
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
      fetchChat(true);
    });
  }
}

// Initializer
window.addEventListener("DOMContentLoaded", () => {
  setupEvents();
  pollStatus();
  fetchChat(true);
  fetchActiveWindows();

  // Regular Polling intervals
  statusPollTimer = setInterval(pollStatus, 3000);
  chatPollTimer = setInterval(() => {
    if (activeTab === "tabAntigravity") {
      fetchChat(false);
    }
  }, 1800);

  setInterval(() => {
    if (activeTab === "tabControls") {
      fetchActiveWindows();
    }
  }, 4000);
});
