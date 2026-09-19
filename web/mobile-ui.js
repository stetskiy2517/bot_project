(() => {
  "use strict";

  const app = document.getElementById("app");
  const workspace = app?.querySelector(".workspace");
  const login = document.getElementById("login");
  const messageInput = document.getElementById("message");
  const settingsPanel = document.getElementById("settingsPanel");
  if (!app || !workspace || document.getElementById("mobileBottomNav")) return;

  app.classList.add("mobile-shell");
  let currentView = "home";
  let todayPayload = null;
  let routePayload = null;
  let loadingToday = false;
  let toastTimer = null;

  const icon = name => {
    const paths = {
      home: '<path d="M4 11.5 12 5l8 6.5V20h-5v-5H9v5H4z"/>',
      chat: '<path d="M5 5h14v10H9l-4 4z"/>',
      today: '<rect x="4" y="5.5" width="16" height="14" rx="2"/><path d="M8 3v5M16 3v5M4 10h16"/>',
      tasks: '<path d="M9 6h11M9 12h11M9 18h11"/><path d="m4 6 1.5 1.5L7.5 5M4 12l1.5 1.5L7.5 11M4 18l1.5 1.5 2-2.5"/>',
      plus: '<path d="M12 5v14M5 12h14"/>',
      refresh: '<path d="M19 7V3l-3 3a7 7 0 1 0 2 8"/>',
      plan: '<path d="M7 3v3M17 3v3M4.5 9h15"/><rect x="4.5" y="5.5" width="15" height="14" rx="3"/><path d="m9 14 1.8 1.8L15 12"/>',
    };
    return `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || ""}</svg>`;
  };

  const todayScreen = document.createElement("section");
  todayScreen.id = "mobileTodayScreen";
  todayScreen.className = "mobile-screen";
  todayScreen.setAttribute("aria-label", "Сегодня");
  todayScreen.innerHTML = `
    <div class="mobile-screen-head">
      <div><h1 class="mobile-screen-title">Сегодня</h1><div id="mobileTodayDate" class="mobile-screen-subtitle"></div></div>
      <button id="mobileTodayAdd" class="mobile-icon-button" type="button" aria-label="Добавить">${icon("plus")}</button>
    </div>
    <div id="mobileTodayContent"><div class="mobile-loading">Собираю день…</div></div>`;
  workspace.appendChild(todayScreen);

  const nav = document.createElement("nav");
  nav.id = "mobileBottomNav";
  nav.className = "mobile-bottom-nav";
  nav.hidden = true;
  nav.setAttribute("aria-label", "Основная навигация");
  nav.innerHTML = [
    ["home", "Главная", "home"], ["chat", "Чат", "chat"], ["today", "Сегодня", "today"], ["tasks", "Задачи", "tasks"],
  ].map(([view, label, iconName]) => `
    <button class="mobile-nav-button${view === "home" ? " active" : ""}" type="button" data-view="${view}" aria-label="${label}">
      ${icon(iconName)}<span class="mobile-nav-label">${label}</span>
    </button>`).join("");
  app.appendChild(nav);

  const sheetBackdrop = document.createElement("div");
  sheetBackdrop.id = "mobileSheetBackdrop";
  sheetBackdrop.className = "mobile-sheet-backdrop";
  app.appendChild(sheetBackdrop);

  const toast = document.createElement("div");
  toast.className = "mobile-toast";
  app.appendChild(toast);

  function showToast(text) {
    clearTimeout(toastTimer);
    toast.textContent = String(text || "");
    toast.classList.add("show");
    toastTimer = setTimeout(() => toast.classList.remove("show"), 2200);
  }

  function friendly(error) {
    return window.PlannerPolish?.friendlyError?.(error) || error?.message || "Не удалось выполнить действие";
  }

  function request(path, options = {}) {
    if (typeof window.api === "function") return window.api(path, options);
    return fetch(path, {headers: {"Content-Type": "application/json"}, ...options}).then(async response => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.message || data.error || `HTTP ${response.status}`);
      return data;
    });
  }

  function setActiveNav(view) {
    nav.querySelectorAll(".mobile-nav-button").forEach(button => {
      const active = button.dataset.view === view;
      button.classList.toggle("active", active);
      button.setAttribute("aria-current", active ? "page" : "false");
    });
  }

  function setView(view, {fromObserver = false} = {}) {
    if (!["home", "chat", "today"].includes(view)) view = "home";
    currentView = view;
    app.classList.remove("mobile-view-home", "mobile-view-chat", "mobile-view-today", "mobile-view-more");
    app.classList.add(`mobile-view-${view}`);
    setActiveNav(view);
    if (view === "chat") {
      if (!app.classList.contains("chat-active")) app.classList.add("chat-active");
      requestAnimationFrame(() => {
        const chat = document.getElementById("chat");
        if (chat) chat.scrollTop = chat.scrollHeight;
      });
    } else if (app.classList.contains("chat-active") && !fromObserver) {
      app.classList.remove("chat-active");
    }
    if (view === "today") loadToday();
  }

  function formatDate(value, options = {}) {
    if (!value) return "";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "";
    return date.toLocaleString("ru-RU", options);
  }

  function escapeHtml(value) {
    return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  }

  function eventRow(event) {
    const time = event.all_day ? "Весь день" : formatDate(event.starts_at, {hour: "2-digit", minute: "2-digit"});
    const subtitle = [time, event.location].filter(Boolean).join(" · ");
    const accent = event.is_travel ? "travel" : "personal";
    return `<button class="mobile-row" type="button" data-event-id="${escapeHtml(event.id || "")}">
      <span class="mobile-row-accent ${accent}"></span><span class="mobile-row-main"><span class="mobile-row-title">${escapeHtml(event.title)}</span><span class="mobile-row-subtitle">${escapeHtml(subtitle)}</span></span><span class="mobile-row-chevron">›</span>
    </button>`;
  }

  function taskMeta(task) {
    const parts = [];
    if (task.overdue) parts.push("Просрочено");
    else if (task.due_at) parts.push("до " + formatDate(task.due_at, {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"}));
    if (task.estimate_minutes) parts.push(`${task.estimate_minutes} мин`);
    if (task.calendar_event_id) parts.push("в календаре");
    return parts.join(" · ") || "Без срока";
  }

  function taskRow(task, index) {
    const accent = task.overdue ? "overdue" : task.priority === "high" ? "high" : task.category;
    return `<button class="mobile-row" type="button" data-task-id="${task.task_id}">
      <span class="mobile-row-accent ${escapeHtml(accent)}"></span><span class="mobile-row-main"><span class="mobile-row-title">${index + 1}. ${escapeHtml(task.title)}</span><span class="mobile-row-subtitle">${escapeHtml(taskMeta(task))}</span></span><span class="mobile-row-chevron">›</span>
    </button>`;
  }

  function compactReview(text) {
    const lines = String(text || "").split("\n").map(line => line.trim()).filter(Boolean);
    return lines.slice(1, 7).join("\n");
  }

  function renderToday(data, route) {
    const content = document.getElementById("mobileTodayContent");
    if (!content) return;
    const date = new Date(`${data.date}T12:00:00`);
    document.getElementById("mobileTodayDate").textContent = date.toLocaleDateString("ru-RU", {weekday: "long", day: "numeric", month: "long"});
    const summary = data.task_summary || {};
    const upcoming = (data.events || []).filter(item => item.is_travel || !item.starts_at || new Date(item.starts_at).getTime() >= Date.now()).slice(0, 5);
    const tasks = (data.tasks || []).slice(0, 3);
    const reviewText = compactReview(data.review?.text);
    const reviewNeedsToggle = reviewText.length > 150 || reviewText.split("\n").length > 3;
    const routeCard = route ? `
      <div class="mobile-card mobile-route-card">
        <div class="mobile-route-icon">↗</div><div><div class="mobile-route-title">${escapeHtml(route.title || "Следующая встреча")}</div><div class="mobile-route-meta">${escapeHtml(route.destination || "Маршрут готов")}</div></div>
        <button class="mobile-action-button" type="button" data-open-route>Маршрут</button>
      </div>` : "";

    const tasksCard = `
      <div class="mobile-card mobile-tasks-card${tasks.length ? "" : " is-empty"}">
        <div class="mobile-card-title">
          <span>Главные задачи</span>
          <button class="mobile-icon-button mobile-card-icon-button" type="button" data-plan-tasks aria-label="Распланировать задачи" title="Распланировать">${icon("plan")}</button>
        </div>
        ${tasks.length
          ? `<div class="mobile-list">${tasks.map(taskRow).join("")}</div>`
          : '<div class="mobile-today-empty">Срочных задач нет</div>'}
      </div>`;

    const dayCard = upcoming.length
      ? `<div class="mobile-card mobile-day-card"><div class="mobile-card-title"><span>День</span><span class="mobile-card-meta">${data.calendar_ok ? "календарь" : "календарь недоступен"}</span></div><div class="mobile-list">${upcoming.map(eventRow).join("")}</div></div>`
      : `<div class="mobile-card mobile-day-card is-empty"><div class="mobile-card-title"><span>День</span><span class="mobile-card-meta">${data.calendar_ok ? "календарь" : "недоступен"}</span></div><div class="mobile-day-empty"><strong>${data.calendar_ok ? "Календарь свободен" : "Календарь недоступен"}</strong><span>${data.calendar_ok ? "На сегодня событий нет" : "События не удалось загрузить"}</span></div></div>`;

    const reviewCard = reviewText ? `
      <div class="mobile-card mobile-review-card">
        <div class="mobile-card-title"><span>Сводка секретаря</span><button class="mobile-icon-button mobile-card-icon-button" type="button" data-refresh-today aria-label="Обновить">${icon("refresh")}</button></div>
        <div class="mobile-review-text">${escapeHtml(reviewText)}</div>
        ${reviewNeedsToggle ? '<button class="mobile-review-toggle" type="button" data-toggle-review aria-expanded="false">Подробнее</button>' : ""}
      </div>` : "";

    content.innerHTML = `
      <div class="mobile-summary-strip" aria-label="Сводка дня">
        <div class="mobile-summary-item"><div class="mobile-summary-number">${Number(summary.open || 0)}</div><div class="mobile-summary-label">задач</div></div>
        <div class="mobile-summary-item"><div class="mobile-summary-number">${Number(summary.overdue || 0)}</div><div class="mobile-summary-label">просрочено</div></div>
        <div class="mobile-summary-item"><div class="mobile-summary-number">${(data.events || []).filter(item => !item.is_travel).length}</div><div class="mobile-summary-label">событий</div></div>
      </div>
      ${tasksCard}
      ${routeCard}
      ${dayCard}
      ${reviewCard}`;
  }

  async function loadToday(force = false) {
    if (loadingToday || (todayPayload && !force)) {
      if (todayPayload) renderToday(todayPayload, routePayload);
      return;
    }
    loadingToday = true;
    const content = document.getElementById("mobileTodayContent");
    if (content) content.innerHTML = '<div class="mobile-loading">Собираю день…</div>';
    try {
      const [todayResult, routeResult] = await Promise.allSettled([request("/api/mobile/today"), request("/api/navigation/next-route")]);
      if (todayResult.status !== "fulfilled") throw todayResult.reason;
      todayPayload = todayResult.value;
      routePayload = routeResult.status === "fulfilled" ? routeResult.value : null;
      renderToday(todayPayload, routePayload);
    } catch (error) {
      if (content) content.innerHTML = `<div class="mobile-error">${escapeHtml(friendly(error))}</div>`;
    } finally {
      loadingToday = false;
    }
  }

  function openSheet(html) {
    sheetBackdrop.innerHTML = `<section class="mobile-sheet" role="dialog" aria-modal="true"><div class="mobile-sheet-handle"></div>${html}</section>`;
    sheetBackdrop.classList.add("open");
  }

  function closeSheet() {
    sheetBackdrop.classList.remove("open");
    sheetBackdrop.innerHTML = "";
  }

  function quickAdd() {
    openSheet(`
      <h2 class="mobile-sheet-title">Что добавить?</h2>
      <div class="mobile-more-group">
        <button class="mobile-more-row" type="button" data-quick="event"><span class="mobile-more-icon">□</span><span><span class="mobile-more-title">Событие</span><span class="mobile-more-meta">В календарь</span></span><span class="mobile-row-chevron">›</span></button>
        <button class="mobile-more-row" type="button" data-quick="task"><span class="mobile-more-icon">✓</span><span><span class="mobile-more-title">Задачу</span><span class="mobile-more-meta">Со сроком и длительностью</span></span><span class="mobile-row-chevron">›</span></button>
        <button class="mobile-more-row" type="button" data-quick="reminder"><span class="mobile-more-icon">!</span><span><span class="mobile-more-title">Напоминание</span><span class="mobile-more-meta">Разовое или повторяющееся</span></span><span class="mobile-row-chevron">›</span></button>
        <button class="mobile-more-row" type="button" data-quick="note"><span class="mobile-more-icon">≡</span><span><span class="mobile-more-title">Заметку</span><span class="mobile-more-meta">Сохранить мысль</span></span><span class="mobile-row-chevron">›</span></button>
      </div>`);
  }

  function startChat(prefix = "") {
    closeSheet();
    setView("chat");
    if (messageInput) {
      messageInput.value = prefix;
      messageInput.dispatchEvent(new Event("input", {bubbles: true}));
      requestAnimationFrame(() => {
        messageInput.focus();
        messageInput.setSelectionRange(messageInput.value.length, messageInput.value.length);
      });
    }
  }

  function localInputValue(value) {
    if (!value) return "";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "";
    const pad = number => String(number).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function openTask(taskId) {
    const task = (todayPayload?.tasks || []).find(item => Number(item.task_id) === Number(taskId));
    if (!task) return;
    openSheet(`
      <h2 class="mobile-sheet-title">Задача</h2>
      <label class="mobile-sheet-field">Название<input id="mobileTaskTitle" value="${escapeHtml(task.title)}" maxlength="300" /></label>
      <label class="mobile-sheet-field">Срок<input id="mobileTaskDue" type="datetime-local" value="${localInputValue(task.due_at)}" /></label>
      <label class="mobile-sheet-field">Длительность, минут<input id="mobileTaskEstimate" type="number" min="5" max="720" value="${task.estimate_minutes || ""}" /></label>
      <label class="mobile-sheet-field">Приоритет<select id="mobileTaskPriority"><option value="high"${task.priority === "high" ? " selected" : ""}>Высокий</option><option value="normal"${task.priority === "normal" ? " selected" : ""}>Обычный</option><option value="low"${task.priority === "low" ? " selected" : ""}>Низкий</option></select></label>
      <label class="mobile-sheet-field">Категория<select id="mobileTaskCategory">${[["work","Работа"],["health","Здоровье"],["rest","Отдых"],["travel","Поездки"],["family","Семья"],["personal","Личное"],["other","Прочее"]].map(([value,label]) => `<option value="${value}"${task.category === value ? " selected" : ""}>${label}</option>`).join("")}</select></label>
      <div class="mobile-sheet-actions two"><button class="mobile-action-button secondary" type="button" data-task-schedule="${task.task_id}">${task.calendar_event_id ? "Уже в календаре" : "В календарь"}</button><button class="mobile-action-button" type="button" data-task-save="${task.task_id}">Сохранить</button></div>
      <div class="mobile-sheet-actions"><button class="mobile-action-button secondary" type="button" data-task-done="${task.task_id}">Отметить выполненной</button></div>`);
    if (task.calendar_event_id) sheetBackdrop.querySelector("[data-task-schedule]").disabled = true;
  }

  async function saveTask(taskId) {
    const title = document.getElementById("mobileTaskTitle")?.value.trim();
    const dueRaw = document.getElementById("mobileTaskDue")?.value;
    const estimateRaw = document.getElementById("mobileTaskEstimate")?.value;
    if (!title) return showToast("Нужно название задачи");
    const changes = {
      title,
      due_at: dueRaw ? new Date(dueRaw).toISOString() : null,
      estimate_minutes: estimateRaw ? Number(estimateRaw) : null,
      priority: document.getElementById("mobileTaskPriority")?.value || "normal",
      category: document.getElementById("mobileTaskCategory")?.value || "other",
    };
    await request(`/api/tasks/${taskId}`, {method: "PATCH", body: JSON.stringify(changes)});
    closeSheet();
    todayPayload = null;
    await loadToday(true);
    showToast("Задача сохранена");
  }

  async function completeTask(taskId) {
    await request(`/api/tasks/${taskId}`, {method: "PATCH", body: JSON.stringify({status: "done"})});
    closeSheet();
    todayPayload = null;
    await loadToday(true);
    showToast("Задача выполнена");
  }

  async function approvePlan(proposals) {
    if (!window.PlannerTaskEditor?.confirmPlan) {
      showToast("Редактор плана ещё загружается");
      return false;
    }
    return window.PlannerTaskEditor.confirmPlan(proposals);
  }

  async function scheduleTask(taskId) {
    const preview = await request("/api/tasks/schedule/preview");
    const proposal = (preview.proposals || []).find(item => Number(item.task_id) === Number(taskId));
    if (!proposal) return showToast("Нужны срок, длительность и свободное окно");
    if (!(await approvePlan([proposal]))) return;
    const result = await request("/api/tasks/schedule/apply", {method: "POST", body: JSON.stringify({proposals: [proposal]})});
    if (!result.applied_count) throw new Error(result.errors?.[0]?.error || "Окно уже занято");
    closeSheet();
    todayPayload = null;
    await loadToday(true);
    showToast("Задача добавлена в календарь");
  }

  async function planTasks() {
    const preview = await request("/api/tasks/schedule/preview");
    const proposals = preview.proposals || [];
    if (!proposals.length) return showToast("Нет задач, которые можно безопасно распланировать");
    if (!(await approvePlan(proposals))) return;
    const result = await request("/api/tasks/schedule/apply", {method: "POST", body: JSON.stringify({proposals})});
    todayPayload = null;
    await loadToday(true);
    showToast(`В календарь добавлено: ${result.applied_count || 0}`);
  }

  async function openRoute() {
    let route = routePayload;
    if (!route) {
      try { route = await request("/api/navigation/next-route"); }
      catch (error) { return showToast(friendly(error)); }
    }
    if (route?.url) window.open(route.url, "_blank", "noopener,noreferrer");
  }

  function closeSettingsForNavigation() {
    if (!settingsPanel?.classList.contains("open")) return;
    const close = document.getElementById("closeSettings");
    if (close) close.click();
    else settingsPanel.classList.remove("open");
  }

  function openLibrarySection(name, {fromSettings = false} = {}) {
    const library = document.getElementById("libraryOpenBtn");
    if (!library) {
      if (name === "tasks") setActiveNav(currentView);
      return showToast("Раздел ещё загружается");
    }
    if (fromSettings) closeSettingsForNavigation();

    const open = () => {
      if (name === "saved") {
        const notes = document.getElementById("libraryNotesTab");
        if (!notes) return showToast("Раздел ещё загружается");
        notes.click();
        library.click();
        return;
      }
      library.click();
      requestAnimationFrame(() => document.getElementById("libraryTasksTab")?.click());
    };
    if (fromSettings) requestAnimationFrame(open);
    else open();
  }

  function openTasksModule() {
    setActiveNav("tasks");
    openLibrarySection("tasks");
  }

  function installSettingsShortcuts() {
    const root = document.getElementById("settingsThemes");
    if (!root) return;

    root.querySelector('[data-settings-utility="saved"]')?.remove();

    const shortcuts = [
      ["route", "Маршрут", "К следующей встрече"],
    ];
    for (const [name, title, note] of shortcuts) {
      if (root.querySelector(`[data-settings-utility="${name}"]`)) continue;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "settings-theme-link";
      button.dataset.settingsUtility = name;
      button.innerHTML = `
        <span class="settings-theme-link-main">
          <span class="settings-theme-link-title">${title}</span>
          <span class="settings-theme-link-note">${note}</span>
        </span>
        <svg class="settings-theme-link-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 7 7-7 7" /></svg>`;
      root.appendChild(button);
    }
  }

  nav.addEventListener("click", event => {
    const button = event.target.closest(".mobile-nav-button");
    if (!button) return;
    if (button.dataset.view === "tasks") return openTasksModule();
    setView(button.dataset.view);
  });

  todayScreen.addEventListener("click", event => {
    const task = event.target.closest("[data-task-id]");
    if (task) return openTask(task.dataset.taskId);
    if (event.target.closest("[data-open-route]")) return openRoute();
    if (event.target.closest("[data-plan-tasks]")) return planTasks().catch(error => showToast(friendly(error)));
    const reviewToggle = event.target.closest("[data-toggle-review]");
    if (reviewToggle) {
      const card = reviewToggle.closest(".mobile-review-card");
      const expanded = card?.classList.toggle("expanded") || false;
      reviewToggle.textContent = expanded ? "Свернуть" : "Подробнее";
      reviewToggle.setAttribute("aria-expanded", String(expanded));
      return;
    }
    if (event.target.closest("[data-refresh-today]")) {
      todayPayload = null;
      routePayload = null;
      return loadToday(true);
    }
  });

  settingsPanel?.addEventListener("click", event => {
    const shortcut = event.target.closest("[data-settings-utility]");
    if (!shortcut) return;
    const name = shortcut.dataset.settingsUtility;
    if (name === "route") {
      closeSettingsForNavigation();
      return requestAnimationFrame(() => openRoute());
    }
  });

  const settingsShortcutObserver = new MutationObserver(installSettingsShortcuts);
  if (settingsPanel) settingsShortcutObserver.observe(settingsPanel, {childList: true, subtree: true});
  document.addEventListener("planner-settings-changed", installSettingsShortcuts);
  document.getElementById("accountBtn")?.addEventListener("click", () => requestAnimationFrame(installSettingsShortcuts));

  document.getElementById("mobileTodayAdd").addEventListener("click", quickAdd);
  sheetBackdrop.addEventListener("click", event => {
    if (event.target === sheetBackdrop) return closeSheet();
    const quick = event.target.closest("[data-quick]");
    if (quick) {
      const prefix = {event: "Добавь в календарь ", task: "Добавь задачу ", reminder: "Напомни мне ", note: "Сохрани заметку: "}[quick.dataset.quick] || "";
      return startChat(prefix);
    }
    const save = event.target.closest("[data-task-save]");
    if (save) return saveTask(Number(save.dataset.taskSave)).catch(error => showToast(friendly(error)));
    const done = event.target.closest("[data-task-done]");
    if (done) return completeTask(Number(done.dataset.taskDone)).catch(error => showToast(friendly(error)));
    const schedule = event.target.closest("[data-task-schedule]");
    if (schedule && !schedule.disabled) return scheduleTask(Number(schedule.dataset.taskSchedule)).catch(error => showToast(friendly(error)));
  });

  document.addEventListener("planner-ready", () => {
    nav.hidden = false;
    const title = app.querySelector(".voice-title");
    const sub = app.querySelector(".voice-sub");
    if (title) title.textContent = "Слушаю вас…";
    if (sub) sub.textContent = "Удерживай кнопку. Отпусти — отправлю.";
    setView(currentView);
  });

  document.addEventListener("planner-result", () => {
    setView("chat");
    todayPayload = null;
    routePayload = null;
  });
  document.addEventListener("planner-library-changed", () => { todayPayload = null; });

  let libraryWasActive = app.classList.contains("library-active");
  const observer = new MutationObserver(() => {
    const chatActive = app.classList.contains("chat-active");
    const libraryActive = app.classList.contains("library-active");
    if (chatActive && currentView === "home") setView("chat", {fromObserver: true});
    else if (!chatActive && currentView === "chat") setView("home", {fromObserver: true});
    if (libraryWasActive && !libraryActive && nav.querySelector('[data-view="tasks"]')?.classList.contains("active")) {
      setActiveNav(currentView);
    }
    libraryWasActive = libraryActive;
  });
  observer.observe(app, {attributes: true, attributeFilter: ["class"]});

  if (!login?.classList.contains("open")) {
    setTimeout(() => {
      if (document.getElementById("accountName")?.textContent !== "Пользователь") nav.hidden = false;
    }, 700);
  }
  installSettingsShortcuts();
  setView("home");
})();
