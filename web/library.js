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
    .library-open-button { pointer-events: auto; }
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
      transition: transform .36s cubic-bezier(.22,.8,.24,1), visibility 0s linear .36s;
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
      padding: calc(env(safe-area-inset-top) + 14px) 16px 8px;
      background: rgba(247,247,245,.92);
      backdrop-filter: blur(18px);
      border-bottom: 1px solid rgba(230,230,227,.8);
    }
    .library-nav {
      height: 52px;
      display: grid;
      grid-template-columns: 44px minmax(0,1fr) auto;
      align-items: center;
      gap: 10px;
    }
    .library-tabs {
      min-width: 0;
      height: 52px;
      margin: 0;
      padding: 4px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 4px;
      border-radius: 14px;
      background: #eaeae7;
    }
    .library-nav-actions {
      position: relative;
      min-width: 44px;
      height: 44px;
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 6px;
    }
    .library-nav-actions:empty { width: 44px; }
    .library-tab {
      min-width: 0;
      min-height: 44px;
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
    .library-card:active { transform: scale(.992); }
    .library-card.completed .library-card-title {
      color: #777773;
      text-decoration: line-through;
      text-decoration-thickness: 1.5px;
      text-decoration-color: #8c8c88;
    }
    .library-card.completed .library-card-meta { color: #b0b0ac; }
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
    .reminder-card-meta {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .reminder-repeat-icon {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 14px;
      height: 14px;
      flex: 0 0 14px;
      font-size: 13px;
      font-weight: 600;
      line-height: 1;
    }
    .library-swipe-row {
      position: relative;
      overflow: hidden;
      margin: 0 0 10px;
      border-radius: 18px;
      background: #e9e9e6;
      touch-action: pan-y;
    }
    .library-swipe-row .library-card {
      position: relative;
      z-index: 2;
      margin: 0;
      background: #fff;
      will-change: transform;
      transition: transform .2s cubic-bezier(.22,.8,.24,1);
    }
    .reminder-actions {
      position: absolute;
      z-index: 1;
      inset: 0;
      display: flex;
      align-items: stretch;
      pointer-events: none;
    }
    .reminder-actions.delete-side {
      right: auto;
      width: 88px;
      justify-content: flex-start;
    }
    .reminder-actions.manage-side {
      left: auto;
      width: max-content;
      justify-content: flex-end;
    }
    .reminder-action {
      min-width: 88px;
      padding: 0 12px;
      border: 0;
      border-radius: 0;
      font-size: 12px;
      font-weight: 650;
      cursor: pointer;
      pointer-events: auto;
      touch-action: manipulation;
    }
    .reminder-action.delete { background: #f2dddd; color: #8a2d2d; }
    .reminder-action.complete { background: #e0ece3; color: #315c3b; }
    .reminder-action.reschedule { background: #e4e4e8; color: #494950; }
    .reminder-snooze-backdrop {
      position: absolute;
      z-index: 90;
      inset: 0;
      display: flex;
      align-items: flex-end;
      background: rgba(0,0,0,.16);
      opacity: 0;
      visibility: hidden;
      pointer-events: none;
      transition: opacity .18s ease, visibility 0s linear .18s;
    }
    .reminder-snooze-backdrop.open {
      opacity: 1;
      visibility: visible;
      pointer-events: auto;
      transition-delay: 0s;
    }
    .reminder-snooze-sheet {
      width: 100%;
      padding: 18px 16px calc(env(safe-area-inset-bottom) + 18px);
      border-radius: 24px 24px 0 0;
      background: #fff;
      box-shadow: 0 -12px 40px rgba(0,0,0,.12);
      transform: translateY(20px);
      transition: transform .18s ease;
    }
    .reminder-snooze-backdrop.open .reminder-snooze-sheet { transform: translateY(0); }
    .reminder-snooze-title {
      margin: 0 0 14px;
      font-size: 16px;
      font-weight: 650;
      text-align: center;
    }
    .reminder-snooze-quick {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 8px;
    }
    .reminder-snooze-button {
      min-height: 44px;
      padding: 9px 8px;
      border-radius: 12px;
      background: #f1f1ef;
      color: #222;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
    }
    .reminder-snooze-custom {
      display: none;
      grid-template-columns: minmax(0,1fr) auto;
      gap: 8px;
      margin-top: 10px;
    }
    .reminder-snooze-custom.open { display: grid; }
    .reminder-snooze-custom input {
      min-width: 0;
      padding: 10px 11px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fff;
      color: #111;
      font: inherit;
    }
    .reminder-snooze-save {
      padding: 0 14px;
      border-radius: 12px;
      background: #111;
      color: #fff;
      font-weight: 600;
      cursor: pointer;
    }
    .library-toast {
      position: absolute;
      z-index: 110;
      left: 50%;
      bottom: calc(env(safe-area-inset-bottom) + 24px);
      max-width: calc(100% - 32px);
      transform: translate(-50%, 14px);
      padding: 10px 14px;
      border-radius: 12px;
      background: rgba(25,25,25,.92);
      color: #fff;
      font-size: 13px;
      line-height: 1.35;
      opacity: 0;
      pointer-events: none;
      transition: opacity .18s ease, transform .18s ease;
    }
    .library-toast.show { opacity: 1; transform: translate(-50%, 0); }
    .library-empty,
    .library-loading,
    .library-error {
      padding: 54px 22px;
      text-align: center;
      color: #92928e;
      font-size: 14px;
      line-height: 1.45;
    }
    .library-error { color: #6e5c5c; }
    @media (min-width: 760px) {
      .library-screen { border-radius: 32px; overflow: hidden; }
      .reminder-snooze-sheet {
        max-width: 480px;
        margin: 0 auto 18px;
        border-radius: 22px;
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
        <div class="library-tabs" role="tablist" aria-label="Сохранённые данные">
          <button id="libraryNotesTab" class="library-tab active" type="button" role="tab" aria-selected="true">Заметки</button>
          <button id="libraryRemindersTab" class="library-tab" type="button" role="tab" aria-selected="false">Напоминания</button>
        </div>
        <div id="libraryNavActions" class="library-nav-actions" aria-label="Действия"></div>
      </div>
    </div>
    <div id="libraryList" class="library-list" aria-live="polite"></div>`;
  app.appendChild(screen);

  const snoozeBackdrop = document.createElement("div");
  snoozeBackdrop.className = "reminder-snooze-backdrop";
  snoozeBackdrop.innerHTML = `
    <div class="reminder-snooze-sheet" role="dialog" aria-modal="true" aria-label="Перенести напоминание">
      <h3 class="reminder-snooze-title">Перенести напоминание</h3>
      <div class="reminder-snooze-quick">
        <button type="button" class="reminder-snooze-button" data-snooze="hour">+1 час</button>
        <button type="button" class="reminder-snooze-button" data-snooze="tomorrow">Завтра</button>
        <button type="button" class="reminder-snooze-button" data-snooze="custom">Выбрать</button>
      </div>
      <div class="reminder-snooze-custom">
        <input id="reminderSnoozeInput" type="datetime-local" aria-label="Новая дата и время" />
        <button id="reminderSnoozeSave" class="reminder-snooze-save" type="button">ОК</button>
      </div>
    </div>`;
  app.appendChild(snoozeBackdrop);

  const toast = document.createElement("div");
  toast.className = "library-toast";
  app.appendChild(toast);

  const backButton = document.getElementById("libraryBackBtn");
  const notesTab = document.getElementById("libraryNotesTab");
  const remindersTab = document.getElementById("libraryRemindersTab");
  const list = document.getElementById("libraryList");
  const snoozeCustom = snoozeBackdrop.querySelector(".reminder-snooze-custom");
  const snoozeInput = document.getElementById("reminderSnoozeInput");
  const snoozeSave = document.getElementById("reminderSnoozeSave");

  let activeTab = "notes";
  let data = { notes: [], reminders: [], timezone: "Europe/Moscow" };
  let loading = false;
  let touchStart = null;
  let suppressClickUntil = 0;
  let wheelX = 0;
  let wheelTimer = null;
  let openedLibraryItem = false;
  let snoozeReminderId = null;
  let toastTimer = null;

  function modalOpen() {
    return Boolean(
      login?.classList.contains("open") ||
      settingsPanel?.classList.contains("open") ||
      snoozeBackdrop.classList.contains("open")
    );
  }

  async function request(path, options = {}) {
    return window.api(path, options);
  }

  function showToast(message) {
    clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.add("show");
    toastTimer = setTimeout(() => toast.classList.remove("show"), 1800);
  }

  function tomorrowSameLocalTime(value = new Date()) {
    const result = new Date(value.getTime());
    result.setDate(result.getDate() + 1);
    return result;
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

  function toLocalInputValue(date) {
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function shortPreview(value, limit = 260) {
    const text = String(value || "").trim();
    if (text.length <= limit) return text;
    return text.slice(0, limit - 1).trimEnd() + "…";
  }

  function closeSwipeRows(except = null) {
    list.querySelectorAll(".library-swipe-row").forEach((row) => {
      if (row !== except) setSwipeOffset(row, 0);
    });
  }

  function setSwipeOffset(row, offset, animate = true) {
    if (!row) return;
    const card = row.querySelector(".library-card");
    if (!card) return;
    const leftWidth = Number(row.dataset.leftWidth || 0);
    const bounded = Math.max(-leftWidth, Math.min(88, Number(offset) || 0));
    card.style.transition = animate ? "transform .2s cubic-bezier(.22,.8,.24,1)" : "none";
    card.style.transform = `translateX(${bounded}px)`;
    row.dataset.offset = String(bounded);
  }

  function isolateLibraryActionPointer(button) {
    const stopPointer = (event) => {
      event.stopPropagation();
      if (event.type === "pointerdown" && button.setPointerCapture) {
        try {
          button.setPointerCapture(event.pointerId);
        } catch (_error) {}
      }
    };
    button.addEventListener("pointerdown", stopPointer);
    button.addEventListener("pointerup", stopPointer);
    button.addEventListener("pointercancel", stopPointer);
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
    const row = document.createElement("div");
    row.className = "library-swipe-row note-swipe-row";
    row.dataset.id = String(note.id);
    row.dataset.type = "note";
    row.dataset.leftWidth = "0";
    row.dataset.offset = "0";

    const deleteSide = document.createElement("div");
    deleteSide.className = "reminder-actions delete-side";
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "reminder-action delete";
    deleteButton.dataset.action = "delete-note";
    deleteButton.textContent = "Удалить";
    isolateLibraryActionPointer(deleteButton);
    deleteSide.appendChild(deleteButton);

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
    row.append(deleteSide, button);
    return row;
  }

  function reminderStatusText(status) {
    if (status === "completed") return "Выполнено";
    return "";
  }

  function reminderCard(reminder) {
    const row = document.createElement("div");
    row.className = "library-swipe-row reminder-swipe-row";
    row.dataset.id = String(reminder.id);
    row.dataset.type = "reminder";
    row.dataset.leftWidth = "176";
    row.dataset.offset = "0";

    const deleteSide = document.createElement("div");
    deleteSide.className = "reminder-actions delete-side";
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "reminder-action delete";
    deleteButton.dataset.action = "delete-reminder";
    deleteButton.textContent = "Удалить";
    isolateLibraryActionPointer(deleteButton);
    deleteSide.appendChild(deleteButton);

    const manageSide = document.createElement("div");
    manageSide.className = "reminder-actions manage-side";
    if (reminder.status === "completed") {
      const reopenButton = document.createElement("button");
      reopenButton.type = "button";
      reopenButton.className = "reminder-action complete";
      reopenButton.dataset.action = "reopen";
      reopenButton.textContent = "Вернуть";
      isolateLibraryActionPointer(reopenButton);
      manageSide.appendChild(reopenButton);
    } else {
      const completeButton = document.createElement("button");
      completeButton.type = "button";
      completeButton.className = "reminder-action complete";
      completeButton.dataset.action = "complete";
      completeButton.textContent = "Выполнено";
      isolateLibraryActionPointer(completeButton);
      manageSide.appendChild(completeButton);
    }
    const rescheduleButton = document.createElement("button");
    rescheduleButton.type = "button";
    rescheduleButton.className = "reminder-action reschedule";
    rescheduleButton.dataset.action = "reschedule";
    rescheduleButton.textContent = "Перенести";
    isolateLibraryActionPointer(rescheduleButton);
    manageSide.appendChild(rescheduleButton);

    const button = document.createElement("button");
    button.type = "button";
    button.className = "library-card";
    if (reminder.status === "completed") button.classList.add("completed");
    button.dataset.type = "reminder";
    button.dataset.id = String(reminder.id);

    const head = document.createElement("div");
    head.className = "library-card-head";
    const title = document.createElement("div");
    title.className = "library-card-title";
    title.textContent = reminder.text || "Напоминание";
    head.appendChild(title);

    const accessibilityStatus = reminderStatusText(reminder.status);
    button.setAttribute(
      "aria-label",
      accessibilityStatus ? `${title.textContent}. ${accessibilityStatus}` : title.textContent,
    );

    const meta = document.createElement("div");
    meta.className = "library-card-meta reminder-card-meta";
    const date = document.createElement("span");
    date.textContent = formatDate(reminder.scheduled_at || reminder.remind_at);
    meta.appendChild(date);
    if (reminder.repeat_rule) {
      const repeatIcon = document.createElement("span");
      repeatIcon.className = "reminder-repeat-icon";
      repeatIcon.setAttribute("aria-hidden", "true");
      repeatIcon.textContent = "↻";
      meta.appendChild(repeatIcon);
    }

    button.append(head, meta);
    row.append(deleteSide, manageSide, button);
    return row;
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

  function replaceReminder(reminder) {
    const index = data.reminders.findIndex((item) => Number(item.id) === Number(reminder.id));
    if (index >= 0) data.reminders[index] = reminder;
    updateTabLabels();
    render();
  }

  function removeNote(noteId) {
    data.notes = data.notes.filter((item) => Number(item.id) !== Number(noteId));
    updateTabLabels();
    render();
  }

  function removeReminder(reminderId) {
    data.reminders = data.reminders.filter((item) => Number(item.id) !== Number(reminderId));
    updateTabLabels();
    render();
  }

  function openLibrary() {
    if (modalOpen()) return;
    closeSwipeRows();
    if (typeof window.clearChatIdleTimer === "function") window.clearChatIdleTimer();
    app.classList.add("library-active");
    loadLibrary();
  }

  function closeLibrary() {
    closeSwipeRows();
    app.classList.remove("library-active");
    if (typeof window.armChatIdleTimer === "function") window.armChatIdleTimer();
  }

  function closeChatToMain() {
    if (!app.classList.contains("chat-active") || !chatCollapseButton) return false;
    chatCollapseButton.click();
    const closed = !app.classList.contains("chat-active");
    if (closed) {
      openedLibraryItem = false;
      if (chatLabel) chatLabel.textContent = "Чат";
    }
    return closed;
  }

  function returnToLibraryFromDocument() {
    if (!openedLibraryItem || !app.classList.contains("chat-active")) return false;
    const returnTab = activeTab;
    if (!closeChatToMain()) return false;
    setTab(returnTab);
    openLibrary();
    return true;
  }

  function navigateBackFromChat() {
    if (openedLibraryItem) return returnToLibraryFromDocument();
    return closeChatToMain();
  }

  function showDocumentInChat(payload) {
    setTab(payload.type === "reminder" ? "reminders" : "notes");
    openedLibraryItem = true;
    app.classList.remove("library-active");
    chat.replaceChildren();
    app.classList.add("chat-active");
    if (chatLabel) chatLabel.textContent = payload.label || "Чат";
    const item = document.createElement("div");
    item.className = "msg assistant";
    item.textContent = payload.chat_text || "Открыто.";
    chat.appendChild(item);
    document.dispatchEvent(new CustomEvent("planner-library-open", {detail: payload}));
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
      if (error.message !== "unauthorized") showToast("Не удалось открыть элемент");
    }
  }

  async function deleteNote(noteId) {
    try {
      await request(`/api/library/notes/${Number(noteId)}`, { method: "DELETE" });
      removeNote(noteId);
      showToast("Заметка удалена");
      document.dispatchEvent(new Event("planner-note-changed"));
    } catch (error) {
      if (error.message !== "unauthorized") showToast("Не удалось удалить заметку");
    }
  }

  async function completeReminder(reminderId) {
    try {
      const payload = await request(`/api/library/reminders/${Number(reminderId)}/complete`, { method: "POST" });
      replaceReminder(payload.reminder);
      showToast("Отмечено выполненным");
    } catch (error) {
      if (error.message !== "unauthorized") showToast("Не удалось отметить выполненным");
    }
  }

  async function reopenReminder(reminderId) {
    try {
      const payload = await request(`/api/library/reminders/${Number(reminderId)}/complete`, {
        method: "POST",
        body: JSON.stringify({ completed: false }),
      });
      replaceReminder(payload.reminder);
      showToast("Отметка выполнения снята");
    } catch (error) {
      if (error.message !== "unauthorized") showToast("Не удалось вернуть напоминание");
    }
  }

  async function deleteReminder(reminderId) {
    try {
      await request(`/api/library/reminders/${Number(reminderId)}`, { method: "DELETE" });
      removeReminder(reminderId);
      showToast("Напоминание удалено");
    } catch (error) {
      if (error.message !== "unauthorized") showToast("Не удалось удалить напоминание");
    }
  }

  async function rescheduleReminder(reminderId, remindAt) {
    try {
      const payload = await request(`/api/library/reminders/${Number(reminderId)}/reschedule`, {
        method: "POST",
        body: JSON.stringify({ remind_at: remindAt }),
      });
      replaceReminder(payload.reminder);
      closeSnoozeSheet();
      showToast(`Перенесено на ${formatDate(payload.reminder.scheduled_at || payload.reminder.remind_at, false)}`);
    } catch (error) {
      if (error.message !== "unauthorized") showToast(error.message || "Не удалось перенести напоминание");
    }
  }

  function openSnoozeSheet(reminderId) {
    snoozeReminderId = Number(reminderId);
    snoozeCustom.classList.remove("open");
    snoozeInput.value = toLocalInputValue(new Date(Date.now() + 60 * 60 * 1000));
    snoozeBackdrop.classList.add("open");
  }

  function closeSnoozeSheet() {
    snoozeBackdrop.classList.remove("open");
    snoozeCustom.classList.remove("open");
    snoozeReminderId = null;
  }

  function handleLibraryAction(actionButton, row) {
    const itemId = Number(row?.dataset.id || 0);
    if (!itemId) return;
    const action = actionButton.dataset.action;
    if (action === "delete-note") deleteNote(itemId);
    else if (action === "delete-reminder") deleteReminder(itemId);
    else if (action === "complete") completeReminder(itemId);
    else if (action === "reopen") reopenReminder(itemId);
    else if (action === "reschedule") {
      setSwipeOffset(row, 0);
      openSnoozeSheet(itemId);
    }
  }

  document.addEventListener("planner-library-changed", () => {
    if (app.classList.contains("library-active")) loadLibrary();
  });
  openButton.addEventListener("click", openLibrary);
  backButton.addEventListener("click", closeLibrary);
  notesTab.addEventListener("click", () => setTab("notes"));
  remindersTab.addEventListener("click", () => setTab("reminders"));

  list.addEventListener("click", (event) => {
    if (performance.now() < suppressClickUntil) return;
    const actionButton = event.target.closest(".reminder-action");
    if (actionButton) {
      event.stopPropagation();
      handleLibraryAction(actionButton, actionButton.closest(".library-swipe-row"));
      return;
    }
    const card = event.target.closest(".library-card");
    if (!card) return;
    const row = card.closest(".library-swipe-row");
    if (row && Math.abs(Number(row.dataset.offset || 0)) > 1) {
      setSwipeOffset(row, 0);
      return;
    }
    openItem(card.dataset.type, card.dataset.id);
  });

  snoozeBackdrop.addEventListener("click", (event) => {
    if (event.target === snoozeBackdrop) closeSnoozeSheet();
  });

  snoozeBackdrop.querySelectorAll("[data-snooze]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!snoozeReminderId) return;
      const mode = button.dataset.snooze;
      if (mode === "hour") {
        rescheduleReminder(snoozeReminderId, new Date(Date.now() + 60 * 60 * 1000).toISOString());
      } else if (mode === "tomorrow") {
        rescheduleReminder(snoozeReminderId, tomorrowSameLocalTime().toISOString());
      } else {
        snoozeCustom.classList.add("open");
        snoozeInput.focus();
      }
    });
  });

  snoozeSave.addEventListener("click", () => {
    if (!snoozeReminderId || !snoozeInput.value) return;
    const selected = new Date(snoozeInput.value);
    if (Number.isNaN(selected.getTime())) {
      showToast("Выбери корректное время");
      return;
    }
    rescheduleReminder(snoozeReminderId, selected.toISOString());
  });

  app.addEventListener(
    "touchstart",
    (event) => {
      if (modalOpen() || event.touches.length !== 1) {
        touchStart = null;
        return;
      }
      if (event.target.closest("input, select, textarea, .record-button, .composer-voice-button, .reminder-action")) {
        touchStart = null;
        return;
      }
      const touch = event.touches[0];
      const swipeRow = app.classList.contains("library-active")
        ? event.target.closest(".library-swipe-row")
        : null;
      if (swipeRow) closeSwipeRows(swipeRow);
      touchStart = {
        x: touch.clientX,
        y: touch.clientY,
        row: swipeRow,
        startOffset: swipeRow ? Number(swipeRow.dataset.offset || 0) : 0,
        horizontal: false,
      };
    },
    { passive: true },
  );

  app.addEventListener(
    "touchmove",
    (event) => {
      if (!touchStart?.row || event.touches.length !== 1) return;
      const touch = event.touches[0];
      const dx = touch.clientX - touchStart.x;
      const dy = touch.clientY - touchStart.y;
      if (!touchStart.horizontal) {
        if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
        if (Math.abs(dy) > Math.abs(dx)) return;
        touchStart.horizontal = true;
      }
      if (!touchStart.horizontal) return;
      event.preventDefault();
      setSwipeOffset(touchStart.row, touchStart.startOffset + dx, false);
    },
    { passive: false },
  );

  app.addEventListener(
    "touchend",
    (event) => {
      if (!touchStart || event.changedTouches.length !== 1) {
        touchStart = null;
        return;
      }
      const start = touchStart;
      const touch = event.changedTouches[0];
      const dx = touch.clientX - start.x;
      const dy = touch.clientY - start.y;
      touchStart = null;

      if (start.row) {
        const horizontal = start.horizontal || (Math.abs(dx) >= 38 && Math.abs(dx) >= Math.abs(dy) * 1.15);
        if (!horizontal) {
          setSwipeOffset(start.row, start.startOffset);
          return;
        }
        suppressClickUntil = performance.now() + 350;
        const total = start.startOffset + dx;
        const leftWidth = Number(start.row.dataset.leftWidth || 0);
        if (total < -44 && leftWidth > 0) setSwipeOffset(start.row, -leftWidth);
        else if (total > 44) setSwipeOffset(start.row, 88);
        else setSwipeOffset(start.row, 0);
        return;
      }

      if (Math.abs(dx) < 64 || Math.abs(dx) < Math.abs(dy) * 1.25) return;
      suppressClickUntil = performance.now() + 350;
      if (dx < 0 && !app.classList.contains("library-active")) openLibrary();
      else if (dx > 0 && app.classList.contains("library-active")) closeLibrary();
      else if (dx > 0 && app.classList.contains("chat-active")) navigateBackFromChat();
    },
    { passive: true },
  );

  app.addEventListener(
    "wheel",
    (event) => {
      if (modalOpen() || Math.abs(event.deltaX) <= Math.abs(event.deltaY) * 1.05) return;
      if (event.target.closest(".library-swipe-row") && app.classList.contains("library-active")) return;
      event.preventDefault();
      wheelX += event.deltaX;
      clearTimeout(wheelTimer);
      wheelTimer = setTimeout(() => { wheelX = 0; }, 180);
      if (Math.abs(wheelX) < 85) return;
      if (wheelX > 0 && !app.classList.contains("library-active")) openLibrary();
      else if (wheelX < 0 && app.classList.contains("library-active")) closeLibrary();
      else if (wheelX < 0 && app.classList.contains("chat-active")) navigateBackFromChat();
      wheelX = 0;
    },
    { passive: false },
  );

  document.addEventListener("keydown", (event) => {
    if (modalOpen() || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.target.matches("input, textarea, select")) return;
    if (event.key === "ArrowRight" && !app.classList.contains("library-active")) openLibrary();
    if (event.key === "ArrowLeft" && app.classList.contains("library-active")) closeLibrary();
    else if (event.key === "ArrowLeft" && app.classList.contains("chat-active")) navigateBackFromChat();
  });

  window.PlannerLibrary = {
    open: openLibrary,
    close: closeLibrary,
    setTab,
    tomorrowSameLocalTime,
  };
})();
