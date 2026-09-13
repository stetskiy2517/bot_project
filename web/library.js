(() => {
  "use strict";

  const app = document.getElementById("app");
  const topbar = app?.querySelector(".topbar");
  const chat = document.getElementById("chat");
  const chatLabel = app?.querySelector(".chat-label");
  const chatCollapseButton = document.getElementById("chatCollapseBtn");
  const login = document.getElementById("login");
  const settingsPanel = document.getElementById("settingsPanel");
  if (!app || !topbar || !chat || document.getElementById("libraryScreen")) return;

  const style = document.createElement("style");
  style.textContent = `
    .topbar, .workspace, .composer-wrap {
      transition: transform .36s cubic-bezier(.22,.8,.24,1);
    }
    .app.library-active > .topbar,
    .app.library-active > .workspace,
    .app.library-active > .composer-wrap {
      transform: translateX(-100%);
      pointer-events: none;
    }
    .topbar {
      justify-content: space-between !important;
    }
    .library-open-button,
    .library-back-button {
      width: 44px;
      height: 44px;
      border-radius: 50%;
      display: grid;
      place-items: center;
      cursor: pointer;
      color: #111;
      background: rgba(255,255,255,.9);
      border: 1px solid var(--line);
      box-shadow: 0 5px 18px rgba(0,0,0,.05);
      flex: 0 0 auto;
    }
    .library-open-button {
      pointer-events: auto;
    }
    .library-open-button svg,
    .library-back-button svg {
      width: 20px;
      height: 20px;
      fill: none;
      stroke: currentColor;
      stroke-width: 1.8;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .library-screen {
      position: absolute;
      z-index: 40;
      inset: 0;
      display: flex;
      flex-direction: column;
      min-width: 0;
      background: var(--bg);
      transform: translateX(100%);
      visibility: hidden;
      pointer-events: none;
      transition:
        transform .36s cubic-bezier(.22,.8,.24,1),
        visibility 0s linear .36s;
      touch-action: pan-y;
    }
    .app.library-active .library-screen {
      transform: translateX(0);
      visibility: visible;
      pointer-events: auto;
      transition-delay: 0s;
    }
    .library-head {
      flex: 0 0 auto;
      padding: calc(env(safe-area-inset-top) + 14px) 16px 12px;
      background: rgba(247,247,245,.92);
      backdrop-filter: blur(18px);
      border-bottom: 1px solid rgba(230,230,227,.8);
    }
    .library-nav {
      height: 44px;
      display: grid;
      grid-template-columns: 44px minmax(0,1fr) 44px;
      align-items: center;
      gap: 10px;
    }
    .library-title {
      margin: 0;
      text-align: center;
      font-size: 17px;
      font-weight: 650;
      letter-spacing: -.25px;
    }
    .library-nav-spacer {
      width: 44px;
      height: 44px;
    }
    .library-tabs {
      margin-top: 14px;
      padding: 4px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 4px;
      border-radius: 14px;
      background: #eaeaE7;
    }
    .library-tab {
      min-width: 0;
      padding: 9px 10px;
      border-radius: 11px;
      background: transparent;
      color: #6f6f6b;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .library-tab.active {
      background: #fff;
      color: #111;
      box-shadow: 0 1px 5px rgba(0,0,0,.07);
    }
    .library-list {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      overscroll-behavior: contain;
      -webkit-overflow-scrolling: touch;
      padding: 12px 12px calc(env(safe-area-inset-bottom) + 24px);
    }
    .library-card {
      width: 100%;
      display: block;
      text-align: left;
      padding: 15px 16px;
      margin: 0 0 10px;
      border-radius: 18px;
      border: 1px solid #e5e5e2;
      background: #fff;
      color: #111;
      cursor: pointer;
      box-shadow: 0 3px 14px rgba(0,0,0,.035);
    }
    .library-card:active {
      transform: scale(.992);
    }
    .library-card.delivered {
      opacity: .62;
    }
    .library-card-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
    }
    .library-card-title {
      min-width: 0;
      font-size: 15px;
      font-weight: 650;
      line-height: 1.3;
      overflow-wrap: anywhere;
    }
    .library-card-status {
      flex: 0 0 auto;
      padding: 4px 7px;
      border-radius: 999px;
      background: #f0f0ed;
      color: #777773;
      font-size: 10px;
      font-weight: 650;
      text-transform: uppercase;
      letter-spacing: .04em;
    }
    .library-card-status.pending {
      background: #ededeb;
      color: #333;
    }
    .library-card-preview {
      margin-top: 7px;
      color: #656561;
      font-size: 13px;
      line-height: 1.4;
      white-space: pre-line;
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 3;
      overflow: hidden;
    }
    .library-card-meta {
      margin-top: 9px;
      color: #9a9a96;
      font-size: 11px;
      line-height: 1.3;
    }
    .library-empty,
    .library-loading,
    .library-error {
      padding: 54px 22px;
      text-align: center;
      color: #92928e;
      font-size: 14px;
      line-height: 1.45;
    }
    .library-error {
      color: #6e5c5c;
    }
    @media (min-width: 760px) {
      .library-screen {
        border-radius: 32px;
        overflow: hidden;
      }
    }
  `;
  document.head.appendChild(style);

  const openButton = document.createElement("button");
  openButton.id = "libraryOpenBtn";
  openButton.className = "library-open-button";
  openButton.type = "button";
  openButton.setAttribute("aria-label", "Заметки и напоминания");
  openButton.title = "Заметки и напоминания";
  openButton.innerHTML = `
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4.5 6.5h5l1.7 2h8.3v9.8a1.7 1.7 0 0 1-1.7 1.7H6.2a1.7 1.7 0 0 1-1.7-1.7z" />
      <path d="M4.5 8.5V6.2a1.7 1.7 0 0 1 1.7-1.7h3.1" />
    </svg>`;
  topbar.prepend(openButton);

  const screen = document.createElement("section");
  screen.id = "libraryScreen";
  screen.className = "library-screen";
  screen.setAttribute("aria-label", "Заметки и напоминания");
  screen.innerHTML = `
    <div class="library-head">
      <div class="library-nav">
        <button id="libraryBackBtn" class="library-back-button" type="button" aria-label="Назад">
          <svg viewBox="0 0 24 24"><path d="m15 5-7 7 7 7" /></svg>
        </button>
        <h2 class="library-title">Сохранённое</h2>
        <span class="library-nav-spacer" aria-hidden="true"></span>
      </div>
      <div class="library-tabs" role="tablist" aria-label="Сохранённые данные">
        <button id="libraryNotesTab" class="library-tab active" type="button" role="tab" aria-selected="true">Заметки</button>
        <button id="libraryRemindersTab" class="library-tab" type="button" role="tab" aria-selected="false">Напоминания</button>
      </div>
    </div>
    <div id="libraryList" class="library-list" aria-live="polite"></div>`;
  app.appendChild(screen);

  const backButton = document.getElementById("libraryBackBtn");
  const notesTab = document.getElementById("libraryNotesTab");
  const remindersTab = document.getElementById("libraryRemindersTab");
  const list = document.getElementById("libraryList");

  let activeTab = "notes";
  let data = { notes: [], reminders: [], timezone: "Europe/Moscow" };
  let loading = false;
  let touchStart = null;
  let suppressClickUntil = 0;
  let wheelX = 0;
  let wheelTimer = null;

  function modalOpen() {
    return Boolean(login?.classList.contains("open") || settingsPanel?.classList.contains("open"));
  }

  async function request(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.body && !(options.body instanceof FormData) && !headers["Content-Type"])
      headers["Content-Type"] = "application/json";
    const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
    if (response.status === 401) {
      login?.classList.add("open");
      throw new Error("unauthorized");
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.message || payload.error || "request_failed");
    return payload;
  }

  function formatDate(value, withYear = true) {
    if (!value) return "";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "";
    try {
      return new Intl.DateTimeFormat("ru-RU", {
        timeZone: data.timezone || "Europe/Moscow",
        day: "2-digit",
        month: "2-digit",
        ...(withYear ? { year: "numeric" } : {}),
        hour: "2-digit",
        minute: "2-digit",
      }).format(parsed);
    } catch (_error) {
      return parsed.toLocaleString("ru-RU");
    }
  }

  function shortPreview(value, limit = 260) {
    const text = String(value || "").trim();
    if (text.length <= limit) return text;
    return text.slice(0, limit - 1).trimEnd() + "…";
  }

  function setTab(tab) {
    activeTab = tab === "reminders" ? "reminders" : "notes";
    const notesActive = activeTab === "notes";
    notesTab.classList.toggle("active", notesActive);
    remindersTab.classList.toggle("active", !notesActive);
    notesTab.setAttribute("aria-selected", String(notesActive));
    remindersTab.setAttribute("aria-selected", String(!notesActive));
    render();
  }

  function noteCard(note) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "library-card";
    button.dataset.type = "note";
    button.dataset.id = String(note.id);

    const head = document.createElement("div");
    head.className = "library-card-head";
    const title = document.createElement("div");
    title.className = "library-card-title";
    title.textContent = note.title || "Без названия";
    head.appendChild(title);

    const preview = document.createElement("div");
    preview.className = "library-card-preview";
    preview.textContent = shortPreview(note.text);

    const meta = document.createElement("div");
    meta.className = "library-card-meta";
    meta.textContent = formatDate(note.updated_at || note.created_at);

    button.append(head, preview, meta);
    return button;
  }

  function reminderCard(reminder) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "library-card" + (reminder.status === "delivered" ? " delivered" : "");
    button.dataset.type = "reminder";
    button.dataset.id = String(reminder.id);

    const head = document.createElement("div");
    head.className = "library-card-head";
    const title = document.createElement("div");
    title.className = "library-card-title";
    title.textContent = reminder.text || "Напоминание";
    const status = document.createElement("span");
    status.className = "library-card-status " + (reminder.status === "pending" ? "pending" : "");
    status.textContent =
      reminder.status === "delivered"
        ? "Выполнено"
        : reminder.status === "delivering"
          ? "Отправляется"
          : "Активно";
    head.append(title, status);

    const meta = document.createElement("div");
    meta.className = "library-card-meta";
    meta.textContent = formatDate(reminder.remind_at);

    button.append(head, meta);
    return button;
  }

  function render() {
    if (loading) {
      list.innerHTML = '<div class="library-loading">Загружаю…</div>';
      return;
    }
    list.replaceChildren();
    const items = activeTab === "notes" ? data.notes : data.reminders;
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "library-empty";
      empty.textContent = activeTab === "notes" ? "Сохранённых заметок пока нет." : "Сохранённых напоминаний пока нет.";
      list.appendChild(empty);
      return;
    }
    const fragment = document.createDocumentFragment();
    items.forEach((item) => fragment.appendChild(activeTab === "notes" ? noteCard(item) : reminderCard(item)));
    list.appendChild(fragment);
  }

  function updateTabLabels() {
    notesTab.textContent = `Заметки · ${data.notes.length}`;
    remindersTab.textContent = `Напоминания · ${data.reminders.length}`;
  }

  async function loadLibrary() {
    if (loading) return;
    loading = true;
    render();
    try {
      const payload = await request("/api/library");
      data = {
        notes: Array.isArray(payload.notes) ? payload.notes : [],
        reminders: Array.isArray(payload.reminders) ? payload.reminders : [],
        timezone: payload.timezone || "Europe/Moscow",
      };
      updateTabLabels();
    } catch (error) {
      if (error.message !== "unauthorized") {
        list.innerHTML = '<div class="library-error">Не удалось загрузить сохранённые данные.</div>';
      }
      return;
    } finally {
      loading = false;
    }
    render();
  }

  function openLibrary() {
    if (modalOpen()) return;
    if (typeof window.clearChatIdleTimer === "function") window.clearChatIdleTimer();
    app.classList.add("library-active");
    loadLibrary();
  }

  function closeLibrary() {
    app.classList.remove("library-active");
    if (typeof window.armChatIdleTimer === "function") window.armChatIdleTimer();
  }

  function closeChatToMain() {
    if (!app.classList.contains("chat-active") || !chatCollapseButton) return false;
    chatCollapseButton.click();
    const closed = !app.classList.contains("chat-active");
    if (closed && chatLabel) chatLabel.textContent = "Чат";
    return closed;
  }

  function showDocumentInChat(payload) {
    app.classList.remove("library-active");
    chat.replaceChildren();
    app.classList.add("chat-active");
    if (chatLabel) chatLabel.textContent = payload.label || "Чат";
    const item = document.createElement("div");
    item.className = "msg assistant";
    item.textContent = payload.chat_text || "Открыто.";
    chat.appendChild(item);
    chat.scrollTop = chat.scrollHeight;
    if (typeof window.clearChatIdleTimer === "function") window.clearChatIdleTimer();
    if (typeof window.armChatIdleTimer === "function") window.armChatIdleTimer();
  }

  async function openItem(type, id) {
    try {
      const payload = await request("/api/library/open", {
        method: "POST",
        body: JSON.stringify({ type, id: Number(id) }),
      });
      showDocumentInChat(payload);
    } catch (error) {
      if (error.message !== "unauthorized") {
        list.innerHTML = '<div class="library-error">Не удалось открыть выбранный элемент.</div>';
      }
    }
  }

  openButton.addEventListener("click", openLibrary);
  backButton.addEventListener("click", closeLibrary);
  notesTab.addEventListener("click", () => setTab("notes"));
  remindersTab.addEventListener("click", () => setTab("reminders"));
  list.addEventListener("click", (event) => {
    if (performance.now() < suppressClickUntil) return;
    const card = event.target.closest(".library-card");
    if (!card) return;
    openItem(card.dataset.type, card.dataset.id);
  });

  app.addEventListener(
    "touchstart",
    (event) => {
      if (modalOpen() || event.touches.length !== 1) {
        touchStart = null;
        return;
      }
      if (event.target.closest("input, select, textarea, .record-button, .composer-voice-button")) {
        touchStart = null;
        return;
      }
      const touch = event.touches[0];
      touchStart = { x: touch.clientX, y: touch.clientY };
    },
    { passive: true },
  );

  app.addEventListener(
    "touchend",
    (event) => {
      if (!touchStart || event.changedTouches.length !== 1) {
        touchStart = null;
        return;
      }
      const touch = event.changedTouches[0];
      const dx = touch.clientX - touchStart.x;
      const dy = touch.clientY - touchStart.y;
      touchStart = null;
      if (Math.abs(dx) < 64 || Math.abs(dx) < Math.abs(dy) * 1.25) return;
      suppressClickUntil = performance.now() + 350;
      if (dx < 0 && !app.classList.contains("library-active")) openLibrary();
      else if (dx > 0 && app.classList.contains("library-active")) closeLibrary();
      else if (dx > 0 && app.classList.contains("chat-active")) closeChatToMain();
    },
    { passive: true },
  );

  app.addEventListener(
    "wheel",
    (event) => {
      if (modalOpen() || Math.abs(event.deltaX) <= Math.abs(event.deltaY) * 1.05) return;
      event.preventDefault();
      wheelX += event.deltaX;
      clearTimeout(wheelTimer);
      wheelTimer = setTimeout(() => {
        wheelX = 0;
      }, 180);
      if (Math.abs(wheelX) < 85) return;
      if (wheelX > 0 && !app.classList.contains("library-active")) openLibrary();
      else if (wheelX < 0 && app.classList.contains("library-active")) closeLibrary();
      else if (wheelX < 0 && app.classList.contains("chat-active")) closeChatToMain();
      wheelX = 0;
    },
    { passive: false },
  );

  document.addEventListener("keydown", (event) => {
    if (modalOpen() || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.target.matches("input, textarea, select")) return;
    if (event.key === "ArrowRight" && !app.classList.contains("library-active")) openLibrary();
    if (event.key === "ArrowLeft" && app.classList.contains("library-active")) closeLibrary();
    else if (event.key === "ArrowLeft" && app.classList.contains("chat-active")) closeChatToMain();
  });
})();
