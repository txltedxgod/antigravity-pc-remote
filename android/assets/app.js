/**
 * Antigravity PC Remote 3.0 - Client Application Logic
 * ChatGPT Minimalist Monochrome Edition
 * Screenshot pasting/attaching, truthful live status, PC hardware controls.
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
let allConversations = [];
let lastMessageCount = 0;
let isSwitchingConv = false;
let isSendingPrompt = false;
let currentAgentStatus = "idle";
let userIsAwaitingResponse = false;

// Active screenshot attachment
let currentAttachedImage = null; // Base64 dataURL
let currentAttachedFileName = "";

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

// Sleek, Non-Intrusive Floating Toast Notification
let activeToastTimer = null;
function showToast(message, duration = 2000) {
  const container = document.getElementById("toastContainer");
  if (!container) return;

  container.innerHTML = "";
  if (activeToastTimer) clearTimeout(activeToastTimer);

  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = message;
  container.appendChild(t);

  requestAnimationFrame(() => {
    t.classList.add("show");
  });

  activeToastTimer = setTimeout(() => {
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

    // Intelligent Auto-Fallback: if local Wi-Fi drops or unreachable, retry via Cloudflare
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

  text = text.replace(/<p>\s*<\/p>/g, '');

  // 10. Restore code blocks with 1-click Copy button
  codeBlocks.forEach((item, index) => {
    const placeholder = `__CODE_BLOCK_${index}__`;
    const escapedCode = escapeHtml(item.code);
    const htmlBlock = `
      <div class="code-block">
        <div class="code-header" style="display: flex; justify-content: space-between; align-items: center; padding: 4px 8px; background: #1c1c1f; border-bottom: 1px solid #27272a; border-radius: 6px 6px 0 0; font-size: 11px; color: #a1a1aa;">
          <span class="code-lang">${escapeHtml(item.lang)}</span>
          <button class="btn-copy-code" data-code="${encodeURIComponent(item.code)}" style="background: transparent; border: 1px solid #27272a; color: #f4f4f5; font-size: 11px; padding: 2px 6px; border-radius: 4px; cursor: pointer;">Скопировать</button>
        </div>
        <pre style="margin: 0; padding: 10px; background: #141416; border-radius: 0 0 6px 6px; overflow-x: auto;"><code>${escapedCode}</code></pre>
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

// --- SCREENSHOT & IMAGE ATTACHMENT LOGIC ---
function attachBase64Image(dataUrl, fileName = "screenshot.jpg") {
  currentAttachedImage = dataUrl;
  currentAttachedFileName = fileName;

  const previewContainer = document.getElementById("imagePreviewContainer");
  const previewImg = document.getElementById("imagePreviewImg");
  const previewName = document.getElementById("previewFileName");

  if (previewImg) previewImg.src = currentAttachedImage;
  if (previewName) previewName.textContent = currentAttachedFileName;
  if (previewContainer) previewContainer.style.display = "flex";

  updateSendButtonState();
  haptic(30);
}

function attachImageFile(file) {
  if (!file || !file.type || !file.type.startsWith("image/")) {
    showToast("Пожалуйста, прикрепите изображение (PNG, JPG, WEBP)");
    return;
  }
  const reader = new FileReader();
  reader.onload = (e) => {
    attachBase64Image(e.target.result, file.name || "screenshot.png");
    showToast("📸 Скриншот прикреплен!");
  };
  reader.readAsDataURL(file);
}

function clearAttachedImage() {
  currentAttachedImage = null;
  currentAttachedFileName = "";
  const previewContainer = document.getElementById("imagePreviewContainer");
  const previewImg = document.getElementById("imagePreviewImg");
  const fileInput = document.getElementById("imageFileInput");
  if (previewImg) previewImg.src = "";
  if (previewContainer) previewContainer.style.display = "none";
  if (fileInput) fileInput.value = "";
  updateSendButtonState();
}

// 1. Capture PC Screenshot into Composer (+ fallback to CDP workspace)
async function capturePCScreen() {
  haptic(30);
  showToast("📸 Делаю снимок экрана ПК...");
  try {
    const res = await apiCall("/api/pc/screenshot");
    if (res && res.status === "ok" && res.image) {
      const srcName = res.source === "desktop" ? "Рабочий стол ПК" : "Окно Antigravity";
      attachBase64Image(res.image, `pc_${res.source}.jpg`);
      showToast(`📸 Скриншот (${srcName}) готов к отправке!`);
    } else {
      showToast("Не удалось сделать скриншот: " + (res?.message || "ошибка"));
    }
  } catch (e) {
    showToast("Ошибка при захвате экрана: " + e.message);
  }
}

// Fullscreen Image Preview Modal
function openImageModal(imgSrc, title = "Снимок экрана") {
  if (!imgSrc) return;
  haptic(15);
  const modal = document.getElementById("imageModal");
  const modalImg = document.getElementById("imageModalImg");
  const modalTitle = document.getElementById("imageModalTitle");
  if (!modal || !modalImg) return;

  modalImg.src = imgSrc;
  if (modalTitle) modalTitle.textContent = title;
  modal.style.display = "flex";
}

function closeImageModal() {
  const modal = document.getElementById("imageModal");
  const modalImg = document.getElementById("imageModalImg");
  if (modal) modal.style.display = "none";
  if (modalImg) modalImg.src = "";
}

// 2. Emergency Stop Execution
async function stopAgentExecution() {
  haptic(40);
  const btn = document.getElementById("btnStopExecution");
  if (btn) btn.disabled = true;
  showToast("🛑 Остановка выполнения агента...");

  try {
    const res = await apiCall("/api/antigravity/stop", "POST", {
      conversation_id: currentConvId || null
    });
    if (res && res.status === "ok" && res.result && res.result.clicked) {
      userIsAwaitingResponse = false;
      showToast("🛑 Агент успешно остановлен!");
      const activityBanner = document.getElementById("agentActivityBanner");
      if (activityBanner) activityBanner.style.display = "none";
      currentAgentStatus = "idle";
      updateSendButtonState();
      setTimeout(() => fetchChat(false), 500);
    } else {
      showToast("Не удалось остановить: " + (res?.result?.message || res?.message || "кнопка не найдена"));
    }
  } catch (e) {
    showToast("Ошибка остановки: " + e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// 3. Task Completion Push Notification
function notifyTaskCompleted() {
  haptic([80, 50, 100]);
  try {
    if (window.AndroidBridge && typeof window.AndroidBridge.showNotification === "function") {
      window.AndroidBridge.showNotification("Antigravity IDE", "✅ Задача успешно выполнена!");
    } else if ("Notification" in window && Notification.permission === "granted") {
      new Notification("Antigravity IDE", {
        body: "✅ Задача успешно выполнена!",
        icon: "icon-192.png"
      });
    }
  } catch (e) {}
  showToast("🔔 Задача агента завершена!");
}

// 4. Voice Input (Dictation) via Web Speech API
let speechRecognizer = null;
let isVoiceRecording = false;

function initVoiceInput() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) return null;

  const recognizer = new SpeechRecognition();
  recognizer.lang = "ru-RU";
  recognizer.continuous = true;
  recognizer.interimResults = true;

  let baseText = "";

  recognizer.onstart = () => {
    isVoiceRecording = true;
    haptic(40);
    const btn = document.getElementById("btnVoiceInput");
    const input = document.getElementById("promptInput");
    if (btn) btn.classList.add("recording");
    if (input) {
      baseText = input.value.trim() ? input.value.trim() + " " : "";
      input.setAttribute("placeholder", "Слушаю... Говорите в микрофон");
    }
    showToast("🎤 Голосовой ввод включен");
  };

  recognizer.onresult = (event) => {
    const input = document.getElementById("promptInput");
    if (!input) return;

    let transcript = "";
    for (let i = 0; i < event.results.length; ++i) {
      transcript += event.results[i][0].transcript;
    }

    input.value = baseText + transcript;
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 120) + "px";
    updateSendButtonState();
  };

  recognizer.onerror = (event) => {
    console.warn("Speech error:", event.error);
    stopVoiceInput();
    if (event.error === "not-allowed") {
      showToast("Разрешите доступ к микрофону в настройках приложения");
    } else if (event.error !== "no-speech") {
      showToast("Ошибка распознавания: " + event.error);
    }
  };

  recognizer.onend = () => {
    stopVoiceInput();
  };

  return recognizer;
}

function toggleVoiceInput() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    showToast("Голосовой ввод не поддерживается на этом устройстве");
    return;
  }

  if (isVoiceRecording) {
    stopVoiceInput();
  } else {
    startVoiceInput();
  }
}

function startVoiceInput() {
  if (!speechRecognizer) {
    speechRecognizer = initVoiceInput();
  }
  if (speechRecognizer) {
    try {
      speechRecognizer.start();
    } catch (e) {
      try {
        speechRecognizer.stop();
        setTimeout(() => speechRecognizer.start(), 200);
      } catch (err) {}
    }
  }
}

function stopVoiceInput() {
  isVoiceRecording = false;
  const btn = document.getElementById("btnVoiceInput");
  const input = document.getElementById("promptInput");
  if (btn) btn.classList.remove("recording");
  if (input) input.setAttribute("placeholder", "Спросить Antigravity...");
  if (speechRecognizer) {
    try { speechRecognizer.stop(); } catch (e) {}
  }
}

// 5. Hardware Telemetry Gatherer
async function pollTelemetry() {
  try {
    const data = await apiCall("/api/pc/telemetry");
    if (!data || data.status !== "ok") return;

    // CPU
    const cpuStat = document.getElementById("cpuStat");
    const cpuFill = document.getElementById("cpuFill");
    const cpuCores = document.getElementById("cpuCores");
    if (data.cpu) {
      if (cpuStat) cpuStat.textContent = `${data.cpu.percent}%`;
      if (cpuFill) cpuFill.style.width = `${Math.min(data.cpu.percent, 100)}%`;
      if (cpuCores) cpuCores.textContent = `${data.cpu.cores} ядер`;
    }

    // RAM
    const ramStat = document.getElementById("ramStat");
    const ramFill = document.getElementById("ramFill");
    const ramDetail = document.getElementById("ramDetail");
    if (data.ram) {
      if (ramStat) ramStat.textContent = `${data.ram.percent}%`;
      if (ramFill) ramFill.style.width = `${Math.min(data.ram.percent, 100)}%`;
      if (ramDetail) ramDetail.textContent = `${data.ram.used_gb} / ${data.ram.total_gb} ГБ`;
    }

    // GPU
    const gpuStat = document.getElementById("gpuStat");
    const gpuFill = document.getElementById("gpuFill");
    const gpuTemp = document.getElementById("gpuTemp");
    const gpuName = document.getElementById("gpuName");
    const gpuVram = document.getElementById("gpuVram");
    if (data.gpu) {
      if (gpuStat) gpuStat.textContent = `${data.gpu.percent}%`;
      if (gpuFill) gpuFill.style.width = `${Math.min(data.gpu.percent, 100)}%`;
      if (gpuTemp) {
        gpuTemp.textContent = `${data.gpu.temp_c}°C`;
        gpuTemp.style.color = data.gpu.temp_c > 80 ? "#EF4444" : (data.gpu.temp_c > 68 ? "#F59E0B" : "#10B981");
      }
      if (gpuName) gpuName.textContent = data.gpu.name || "NVIDIA RTX 2050";
      if (gpuVram) gpuVram.textContent = `VRAM: ${data.gpu.vram_used_mb} / ${data.gpu.vram_total_mb} МБ`;
    } else {
      if (gpuStat) gpuStat.textContent = "—";
      if (gpuName) gpuName.textContent = "Не обнаружено";
      if (gpuTemp) gpuTemp.textContent = "—";
      if (gpuVram) gpuVram.textContent = "GPU недоступен";
    }

    // DISK C:
    const diskStat = document.getElementById("diskStat");
    const diskFill = document.getElementById("diskFill");
    const diskDetail = document.getElementById("diskDetail");
    if (data.disk) {
      if (diskStat) diskStat.textContent = `${data.disk.percent}%`;
      if (diskFill) diskFill.style.width = `${Math.min(data.disk.percent, 100)}%`;
      if (diskDetail) diskDetail.textContent = `Свободно ${data.disk.free_gb} из ${data.disk.total_gb} ГБ`;
    }
  } catch (e) {
    console.debug("Telemetry error:", e);
  }
}

// 6. Live Screen Snapshot Gatherer for Monitor Tab
let currentMonitorScreenshot = "";
async function loadMonitorScreenshot() {
  const ph = document.getElementById("screenPlaceholder");
  const img = document.getElementById("screenPreviewImg");
  try {
    const span = ph ? ph.querySelector("span") : null;
    if (span) span.textContent = "Захват экрана ПК...";
    const res = await apiCall("/api/pc/screenshot");
    if (res && res.status === "ok" && res.image) {
      currentMonitorScreenshot = res.image;
      if (img) {
        img.src = res.image;
        img.style.display = "block";
      }
      if (ph) ph.style.display = "none";
    } else {
      if (span) span.textContent = "Не удалось захватить экран";
    }
  } catch (e) {
    const span = ph ? ph.querySelector("span") : null;
    if (span) span.textContent = "Ошибка получения снимка";
  }
}

function updateSendButtonState() {
  const input = document.getElementById("promptInput");
  const btn = document.getElementById("btnSendPrompt");
  if (!btn) return;
  const hasText = input && input.value.trim().length > 0;
  const hasImage = !!currentAttachedImage;
  if (hasText || hasImage) {
    btn.classList.add("active");
  } else {
    btn.classList.remove("active");
  }

  // Antigravity Queue Mode: adjust input placeholder and button tooltip
  const isAgentBusy = (currentAgentStatus === "executing" || currentAgentStatus === "thinking");
  if (isAgentBusy) {
    if (input && (!input.value || input.placeholder === "Спросить Antigravity...")) {
      input.placeholder = "Сообщение в очередь...";
    }
    btn.setAttribute("title", "Добавить в очередь");
  } else {
    if (input && input.placeholder === "Сообщение в очередь...") {
      input.placeholder = "Спросить Antigravity...";
    }
    btn.setAttribute("title", "Отправить сообщение");
  }
}

// Dedicated multi-frame smooth scroll to bottom for chat feed
function scrollChatToBottom(force = false) {
  const feed = document.getElementById("chatFeed");
  if (!feed) return;
  const doScroll = () => {
    feed.scrollTop = feed.scrollHeight;
  };
  doScroll();
  requestAnimationFrame(doScroll);
  setTimeout(doScroll, 40);
  setTimeout(doScroll, 120);
  setTimeout(doScroll, 300);
}

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
    if (!res || res.status !== "ok") {
      if (activityBanner && !isSendingPrompt) activityBanner.style.display = "none";
      return;
    }

    if (currentConvId === "new") {
      if (res.conversation_id && res.conversation_id !== "new") {
        currentConvId = res.conversation_id;
        updateActiveConvTitle(currentConvId);
        loadConversations();
      } else {
        if (emptyState) emptyState.style.display = "flex";
        feed.querySelectorAll(".chat-msg").forEach(m => m.remove());
        lastMessageCount = 0;
        if (activityBanner) activityBanner.style.display = "none";
        return;
      }
    } else if (res.conversation_id && !currentConvId) {
      if (res.conversation_id !== "new") {
        currentConvId = res.conversation_id;
        updateActiveConvTitle(currentConvId);
      }
    } else if (currentConvId && currentConvId !== "new") {
      updateActiveConvTitle(currentConvId);
    }

    // Update Agent Live Status & Task Completion Notification
    const agentStatus = res.agent_status || "idle";
    const currentAction = res.current_action || "";

    // Truthful notification: ONLY fire when the user was actively awaiting response for this chat
    if (userIsAwaitingResponse && (currentAgentStatus === "executing" || currentAgentStatus === "thinking") && agentStatus === "idle") {
      userIsAwaitingResponse = false;
      notifyTaskCompleted();
    }

    currentAgentStatus = agentStatus;
    updateSendButtonState();

    if (statusPill && statusLabel) {
      if (agentStatus === "executing") {
        statusPill.style.display = "flex";
        statusPill.className = "agent-live-pill executing";
        statusLabel.textContent = currentAction ? currentAction : "Действие...";
      } else if (agentStatus === "thinking") {
        statusPill.style.display = "flex";
        statusPill.className = "agent-live-pill thinking";
        statusLabel.textContent = "Думает...";
      } else {
        statusPill.style.display = "none";
      }
    }

    // Truthful Activity Banner: only show when genuinely executing / thinking
    if (activityBanner && activityText) {
      if (agentStatus === "executing" || agentStatus === "thinking") {
        activityBanner.style.display = "flex";
        activityText.textContent = currentAction ? currentAction : (agentStatus === "thinking" ? "Размышляет над ответом..." : "Агент выполняет задачу...");
      } else {
        // Absolutely idle: immediately hide banner
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

    const isNearBottom = (feed.scrollHeight - feed.scrollTop - feed.clientHeight) < 140;

    // Only re-render if count or last message changed
    if (messages.length !== lastMessageCount || isSendingPrompt || isSwitchingConv) {
      lastMessageCount = messages.length;
      isSendingPrompt = false;

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
          let imgTag = "";
          if (msg.has_image) {
            imgTag = `<div style="font-size: 11px; color: #a1a1aa; margin-bottom: 4px; display: flex; align-items: center; gap: 4px;"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><circle cx="8.5" cy="8.5" r="1.5"></circle><polyline points="21 15 16 10 5 21"></polyline></svg> Скриншот прикреплен</div>`;
          }
          bubble.innerHTML = `
            <div class="msg-bubble-user">
              ${imgTag}
              <div>${escapeHtml(msg.text)}</div>
            </div>
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

      if (autoScroll || isSwitchingConv || isNearBottom) {
        scrollChatToBottom(true);
      }
    }
  } catch (err) {
    if (activityBanner && !isSendingPrompt) activityBanner.style.display = "none";
  }
}

// Send Prompt to Antigravity (Supports text and screenshots)
async function sendPrompt() {
  const input = document.getElementById("promptInput");
  const btn = document.getElementById("btnSendPrompt");
  if (!input || !btn) return;

  const text = input.value.trim();
  const attachedImg = currentAttachedImage;

  if (!text && !attachedImg) {
    showToast("Введи текст сообщения или прикрепи скриншот");
    return;
  }

  haptic(35);
  btn.disabled = true;
  isSendingPrompt = true;

  const isAgentBusy = (currentAgentStatus === "executing" || currentAgentStatus === "thinking");

  // Optimistic UI: append user message to feed immediately
  const feed = document.getElementById("chatFeed");
  const emptyState = document.getElementById("chatEmptyState");
  if (emptyState) emptyState.style.display = "none";

  if (feed) {
    const bubble = document.createElement("div");
    bubble.className = "chat-msg msg-user";
    const nowStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    let imageHtml = "";
    if (attachedImg) {
      imageHtml = `<img class="msg-attachment-thumb" src="${attachedImg}" alt="Attached Screenshot">`;
    }
    const queuedHtml = isAgentBusy ? `<div class="msg-queued-badge"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg> В очереди</div>` : "";
    bubble.innerHTML = `
      <div class="msg-bubble-user">
        ${queuedHtml}
        ${imageHtml}
        ${text ? `<div>${escapeHtml(text)}</div>` : ""}
      </div>
      <div class="msg-meta">${nowStr}</div>
    `;
    feed.appendChild(bubble);
    scrollChatToBottom(true);
  }

  // Clear input & reset attachment
  input.value = "";
  input.style.height = "auto";
  clearAttachedImage();

  // Show activity banner right away
  const activityBanner = document.getElementById("agentActivityBanner");
  const activityText = document.getElementById("agentActivityText");
  if (activityBanner && activityText) {
    activityBanner.style.display = "flex";
    activityText.textContent = isAgentBusy ? "Добавлено в очередь Antigravity..." : "Передано в Antigravity. Ожидание ответа...";
  }

  try {
    const isNew = (currentConvId === "new");
    const res = await apiCall("/api/antigravity/send", "POST", {
      text: text,
      image: attachedImg,
      focus: false,
      conversation_id: isNew ? "new" : (currentConvId || null)
    });

    if (res.status === "ok") {
      userIsAwaitingResponse = true;
      if (isAgentBusy) {
        showToast("⏳ Сообщение добавлено в очередь Antigravity!");
      } else {
        showToast("✨ Запрос отправлен в IDE!");
      }
      if (isNew) {
        setTimeout(async () => {
          currentConvId = "";
          await loadConversations();
          fetchChat(true);
        }, 2200);
      } else {
        for (let i = 1; i <= 6; i++) {
          setTimeout(() => fetchChat(true), i * 750);
        }
      }
    } else {
      showToast("Ошибка: " + (res.message || "Не удалось отправить"));
      if (activityBanner) activityBanner.style.display = "none";
    }
  } catch (e) {
    showToast("Ошибка связи с сервером Antigravity");
    if (activityBanner) activityBanner.style.display = "none";
  } finally {
    btn.disabled = false;
    updateSendButtonState();
  }
}

// --- CONVERSATION DRAWER & SELECTION ---
function formatConvTime(mtime) {
  if (!mtime) return "";
  try {
    const d = new Date(mtime * 1000);
    const now = new Date();
    const isToday = d.toDateString() === now.toDateString();
    const timeStr = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (isToday) return timeStr;
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    if (d.toDateString() === yesterday.toDateString()) return "Вчера";
    return `${d.getDate().toString().padStart(2, "0")}.${(d.getMonth() + 1).toString().padStart(2, "0")}`;
  } catch (e) {
    return "";
  }
}

function updateActiveConvTitle(convId, directTitle) {
  const titleEl = document.getElementById("activeConvTitle");
  if (!titleEl) return;
  if (!convId || convId === "new") {
    titleEl.textContent = "Новый диалог";
    return;
  }
  let title = directTitle;
  let isPinned = false;
  const match = allConversations.find(c => c.id === convId);
  if (match) {
    if (!title) title = match.title;
    isPinned = !!match.isPinned;
  }

  if (!title) {
    title = `Диалог ${convId.substring(0, 8)}`;
  }

  if (isPinned) {
    titleEl.innerHTML = `
      <svg class="header-pin-icon" width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
        <path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5.2v6h1.6v-6H18v-2l-2-2z"/>
      </svg>
      <span>${escapeHtml(title)}</span>
    `;
  } else {
    titleEl.textContent = title;
  }
}

async function togglePinConversation(e, convId) {
  e.stopPropagation();
  e.preventDefault();
  haptic(25);
  const match = allConversations.find(c => c.id === convId);
  const willBePinned = match ? !match.isPinned : true;
  if (match) match.isPinned = willBePinned;

  renderDrawerList(document.getElementById("drawerSearchInput")?.value || "");
  updateActiveConvTitle(currentConvId);

  try {
    const res = await apiCall("/api/antigravity/conversation/pin", "POST", { id: convId, pinned: willBePinned });
    if (res && res.status === "ok") {
      showToast(willBePinned ? "📌 Диалог закреплён" : "Диалог откреплён");
    }
  } catch (err) {
    showToast("Не удалось изменить статус закрепления");
  }
}

function createDrawerItem(c) {
  const item = document.createElement("button");
  const isActive = (c.id === currentConvId) || (!currentConvId && c.isCurrent);
  const isPinned = !!c.isPinned;
  item.className = "drawer-item" + (isActive ? " active" : "") + (isPinned ? " pinned" : "");
  item.dataset.id = c.id;

  const timeLabel = formatConvTime(c.mtime);
  const safeTitle = escapeHtml(c.title || `Диалог ${c.id.substring(0, 8)}`);

  const pinIconSvg = `
    <svg class="drawer-item-pin-badge" width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
      <path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5.2v6h1.6v-6H18v-2l-2-2z"/>
    </svg>
  `;

  const chatIconSvg = `
    <svg class="drawer-item-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
    </svg>
  `;

  item.innerHTML = `
    <div class="drawer-item-left">
      ${isPinned ? pinIconSvg : chatIconSvg}
      <span class="drawer-item-title">${safeTitle}</span>
    </div>
    <div class="drawer-item-actions">
      ${timeLabel ? `<span class="drawer-item-time">${timeLabel}</span>` : ""}
      <button class="btn-pin-toggle ${isPinned ? "pinned" : ""}" title="${isPinned ? "Открепить" : "Закрепить"}" type="button">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="${isPinned ? "currentColor" : "none"}" stroke="currentColor" stroke-width="2">
          <path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5.2v6h1.6v-6H18v-2l-2-2z"/>
        </svg>
      </button>
      ${isActive ? `<span class="drawer-item-active-dot"></span>` : ""}
    </div>
  `;

  item.addEventListener("click", () => switchConversation(c.id, c.title));

  const pinBtn = item.querySelector(".btn-pin-toggle");
  if (pinBtn) {
    pinBtn.addEventListener("click", (e) => togglePinConversation(e, c.id));
  }

  return item;
}

function renderDrawerList(filter = "") {
  const listEl = document.getElementById("drawerList");
  if (!listEl) return;

  const query = (filter || "").toLowerCase().trim();
  const filtered = query
    ? allConversations.filter(c => (c.title && c.title.toLowerCase().includes(query)) || c.id.toLowerCase().includes(query))
    : allConversations;

  if (filtered.length === 0) {
    listEl.innerHTML = `<div class="loading-state-text">${query ? "Ничего не найдено" : "Нет сохраненных диалогов"}</div>`;
    return;
  }

  listEl.innerHTML = "";

  const pinned = filtered.filter(c => !!c.isPinned);
  const recent = filtered.filter(c => !c.isPinned);

  if (pinned.length > 0) {
    const pinnedHeader = document.createElement("div");
    pinnedHeader.className = "drawer-section-title";
    pinnedHeader.innerHTML = `
      <div class="drawer-section-title-left">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
          <path d="M16 12V4h1V2H7v2h1v8l-2 2v2h5.2v6h1.6v-6H18v-2l-2-2z"/>
        </svg>
        <span>Закреплённые</span>
      </div>
      <span class="drawer-section-badge">${pinned.length}</span>
    `;
    listEl.appendChild(pinnedHeader);

    pinned.forEach(c => {
      listEl.appendChild(createDrawerItem(c));
    });
  }

  if (recent.length > 0) {
    if (pinned.length > 0 || query) {
      const recentHeader = document.createElement("div");
      recentHeader.className = "drawer-section-title";
      recentHeader.innerHTML = `
        <div class="drawer-section-title-left">
          <span>${query ? "Остальные результаты" : "Недавние"}</span>
        </div>
      `;
      listEl.appendChild(recentHeader);
    }

    recent.forEach(c => {
      listEl.appendChild(createDrawerItem(c));
    });
  }
}

async function loadConversations() {
  if (isSwitchingConv) return;
  try {
    const res = await apiCall("/api/antigravity/conversations");
    if (!res || !res.conversations) return;

    allConversations = res.conversations;
    const badge = document.getElementById("drawerCountBadge");
    if (badge) badge.textContent = allConversations.length;

    if (!currentConvId && res.active_id) {
      currentConvId = res.active_id;
    }
    updateActiveConvTitle(currentConvId);

    const searchInput = document.getElementById("drawerSearchInput");
    renderDrawerList(searchInput ? searchInput.value : "");
  } catch (e) {
    console.error("Failed to load conversations:", e);
  }
}

async function switchConversation(convId, optTitle) {
  if (!convId || convId === currentConvId) {
    closeSidebar();
    return;
  }
  haptic(20);
  closeSidebar();

  currentConvId = convId;
  updateActiveConvTitle(convId, optTitle);
  isSwitchingConv = true;
  userIsAwaitingResponse = false;
  lastMessageCount = 0;

  const feed = document.getElementById("chatFeed");
  const emptyState = document.getElementById("chatEmptyState");
  if (feed) feed.querySelectorAll(".chat-msg").forEach(m => m.remove());
  if (emptyState) emptyState.style.display = "none";

  try {
    await apiCall("/api/antigravity/conversation/switch", "POST", { id: convId });
    await fetchChat(true);
    scrollChatToBottom(true);
    renderDrawerList();
  } catch (e) {
    showToast("Ошибка переключения диалога");
  } finally {
    isSwitchingConv = false;
  }
}

async function startNewConversation() {
  haptic(30);
  closeSidebar();

  isSwitchingConv = true;
  userIsAwaitingResponse = false;
  currentConvId = "new";
  lastMessageCount = 0;
  updateActiveConvTitle("new");

  const feed = document.getElementById("chatFeed");
  const emptyState = document.getElementById("chatEmptyState");
  if (feed) feed.querySelectorAll(".chat-msg").forEach(m => m.remove());
  if (emptyState) emptyState.style.display = "flex";

  try {
    await apiCall("/api/antigravity/conversation/new", "POST");
    await loadConversations();
  } catch (e) {
    showToast("Ошибка создания чата");
  } finally {
    isSwitchingConv = false;
  }
}

function openSidebar() {
  haptic(15);
  const drawer = document.getElementById("conversationsDrawer");
  const backdrop = document.getElementById("drawerBackdrop");
  if (drawer) drawer.classList.add("open");
  if (backdrop) backdrop.classList.add("active");
  loadConversations();
  try {
    history.pushState({ drawerOpen: true }, "");
  } catch (e) {}
}

function closeSidebar() {
  const drawer = document.getElementById("conversationsDrawer");
  const backdrop = document.getElementById("drawerBackdrop");
  if (drawer && drawer.classList.contains("open")) {
    drawer.classList.remove("open");
  }
  if (backdrop && backdrop.classList.contains("active")) {
    backdrop.classList.remove("active");
  }
}

window.addEventListener("popstate", () => {
  closeSidebar();
});

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
    badge.className = "connection-pill connected";
    text.textContent = "ПК онлайн";
  } else {
    badge.className = "connection-pill disconnected";
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

      item.innerHTML = `
        <div style="display: flex; align-items: center; gap: 8px; flex: 1; overflow: hidden;">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color: var(--text-secondary); flex-shrink: 0;"><rect x="3" y="3" width="18" height="18" rx="2"></rect><line x1="3" y1="9" x2="21" y2="9"></line></svg>
          <div style="display: flex; flex-direction: column; overflow: hidden;">
            <span class="window-title" title="${escapeHtml(w.title)}">${escapeHtml(w.title)}</span>
            <span style="font-size: 10px; color: var(--text-muted);">${escapeHtml(w.process_name || "pid: " + w.pid)}</span>
          </div>
        </div>
        <div class="window-actions">
          <button class="btn-win-action btn-window-focus" title="Вывести на экран">Фокус</button>
          <button class="btn-win-action btn-window-kill" title="Закрыть окно">Закрыть</button>
        </div>
      `;

      item.querySelector(".btn-window-focus").addEventListener("click", () => {
        triggerAction("focus_window", { hwnd: w.hwnd });
        showToast(`🎯 Фокус: ${w.title.slice(0, 20)}`);
      });

      item.querySelector(".btn-window-kill").addEventListener("click", () => {
        triggerAction("close_window", { hwnd: w.hwnd, force: true });
        showToast(`Закрыто: ${w.process_name || w.title.slice(0, 15)}`);
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
      } else if (targetId === "tabMonitor") {
        pollTelemetry();
        if (!currentMonitorScreenshot) loadMonitorScreenshot();
      }
    });
  });

  // Prompt Textarea & Auto-resize
  const promptInput = document.getElementById("promptInput");
  if (promptInput) {
    promptInput.addEventListener("input", () => {
      promptInput.style.height = "auto";
      promptInput.style.height = Math.min(promptInput.scrollHeight, 120) + "px";
      updateSendButtonState();
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

  // Emergency Stop Execution Button
  const btnStop = document.getElementById("btnStopExecution");
  if (btnStop) btnStop.addEventListener("click", stopAgentExecution);

  // PC Screenshot Capture Button
  const btnCapturePC = document.getElementById("btnCapturePCScreen");
  if (btnCapturePC) btnCapturePC.addEventListener("click", capturePCScreen);

  // Voice Input (Dictation) Button
  const btnVoice = document.getElementById("btnVoiceInput");
  if (btnVoice) btnVoice.addEventListener("click", toggleVoiceInput);

  // Fullscreen Image Modal Events for Chat Composer
  const previewBox = document.getElementById("imagePreviewContainer");
  if (previewBox) {
    previewBox.addEventListener("click", (e) => {
      if (e.target.closest("#btnRemoveAttachment")) return;
      if (currentAttachedImage) {
        openImageModal(currentAttachedImage, currentAttachedFileName || "Снимок экрана ПК");
      }
    });
  }

  const btnCloseModal = document.getElementById("btnCloseImageModal");
  if (btnCloseModal) btnCloseModal.addEventListener("click", closeImageModal);

  const imgModal = document.getElementById("imageModal");
  if (imgModal) {
    imgModal.addEventListener("click", (e) => {
      if (e.target === imgModal || e.target.classList.contains("image-modal-body")) {
        closeImageModal();
      }
    });
  }

  // Live Screen Snapshot in Monitor Tab
  const btnRefreshScreen = document.getElementById("btnRefreshScreenSnapshot");
  if (btnRefreshScreen) {
    btnRefreshScreen.addEventListener("click", () => {
      haptic(25);
      showToast("📸 Обновление снимка экрана...");
      loadMonitorScreenshot();
    });
  }

  const screenBox = document.getElementById("screenPreviewBox");
  if (screenBox) {
    screenBox.addEventListener("click", () => {
      if (currentMonitorScreenshot) {
        openImageModal(currentMonitorScreenshot, "Экран ПК (Live View)");
      } else {
        loadMonitorScreenshot();
      }
    });
  }

  // Chat Feed Image Click Delegation (open modal for sent screenshots)
  const chatFeed = document.getElementById("chatFeed");
  if (chatFeed) {
    chatFeed.addEventListener("click", (e) => {
      const thumb = e.target.closest(".msg-attachment-thumb");
      if (thumb && thumb.src) {
        openImageModal(thumb.src, "Снимок экрана в чате");
      }
    });
  }

  // Refresh Telemetry Button
  const btnRefTelemetry = document.getElementById("btnRefreshTelemetry");
  if (btnRefTelemetry) {
    btnRefTelemetry.addEventListener("click", () => {
      haptic(20);
      pollTelemetry();
      showToast("Телеметрия обновлена");
    });
  }

  // File Attachment & Image Picking
  const btnAttach = document.getElementById("btnAttach");
  const fileInput = document.getElementById("imageFileInput");
  const btnRemoveAttach = document.getElementById("btnRemoveAttachment");

  if (btnAttach && fileInput) {
    btnAttach.addEventListener("click", () => {
      haptic(20);
      fileInput.click();
    });
    fileInput.addEventListener("change", (e) => {
      if (e.target.files && e.target.files[0]) {
        attachImageFile(e.target.files[0]);
      }
    });
  }

  if (btnRemoveAttach) {
    btnRemoveAttach.addEventListener("click", () => {
      haptic(15);
      clearAttachedImage();
    });
  }

  // Clipboard Paste Listener (Ctrl+V / Mobile Paste)
  window.addEventListener("paste", (e) => {
    if (!e.clipboardData || !e.clipboardData.items) return;
    const items = e.clipboardData.items;
    for (let i = 0; i < items.length; i++) {
      if (items[i].type.indexOf("image") !== -1) {
        e.preventDefault();
        const file = items[i].getAsFile();
        if (file) {
          attachImageFile(file);
          break;
        }
      }
    }
  });

  // Quick Chips
  document.querySelectorAll(".chip[data-prompt]").forEach(chip => {
    chip.addEventListener("click", () => {
      haptic(15);
      const text = chip.getAttribute("data-prompt");
      if (promptInput) {
        promptInput.value = text;
        promptInput.focus();
        promptInput.style.height = "auto";
        updateSendButtonState();
      }
    });
  });

  // Sidebar Drawer Events
  const btnToggleSidebar = document.getElementById("btnToggleSidebar");
  const btnOpenSidebar = document.getElementById("btnOpenSidebar");
  const btnCloseDrawer = document.getElementById("btnCloseDrawer");
  const drawerBackdrop = document.getElementById("drawerBackdrop");
  const btnDrawerNewChat = document.getElementById("btnDrawerNewChat");
  const btnNewChat = document.getElementById("btnNewChat");
  const drawerSearchInput = document.getElementById("drawerSearchInput");

  if (btnToggleSidebar) btnToggleSidebar.addEventListener("click", openSidebar);
  if (btnOpenSidebar) btnOpenSidebar.addEventListener("click", openSidebar);
  if (btnCloseDrawer) btnCloseDrawer.addEventListener("click", closeSidebar);
  if (drawerBackdrop) drawerBackdrop.addEventListener("click", closeSidebar);
  if (btnDrawerNewChat) btnDrawerNewChat.addEventListener("click", startNewConversation);
  if (btnNewChat) btnNewChat.addEventListener("click", startNewConversation);

  if (drawerSearchInput) {
    drawerSearchInput.addEventListener("input", (e) => {
      renderDrawerList(e.target.value);
    });
  }

  // Wake-on-LAN Button
  const btnWol = document.getElementById("btnWakeOnLan");
  if (btnWol) {
    btnWol.addEventListener("click", () => {
      haptic(40);
      if (window.AndroidBridge && typeof window.AndroidBridge.sendWakeOnLan === "function") {
        window.AndroidBridge.sendWakeOnLan(config.mac || "A83B7676208F");
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
      modal.classList.add("active");
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
      modal.classList.remove("active");
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
      modal.classList.remove("active");
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
  loadConversations();
  fetchChat(true);
  fetchActiveWindows();
  pollTelemetry();

  // Notification permissions request if supported
  try {
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission().catch(() => {});
    }
  } catch (e) {}

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
    } else if (activeTab === "tabMonitor") {
      pollTelemetry();
    }
  }, 3500);
});
