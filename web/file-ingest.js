(() => {
  "use strict";

  const app = document.getElementById("app");
  const composer = document.getElementById("composer");
  const messageInput = document.getElementById("message");
  const chat = document.getElementById("chat");
  const chatVoice = document.getElementById("chatVoiceBtn");
  if (!app || !composer || !messageInput || !chat || document.getElementById("fileAttachBtn")) return;

  const style = document.createElement("style");
  style.id = "fileIngestStyles";
  style.textContent = `
    .app.mobile-shell.mobile-view-home .composer-wrap,
    .app.mobile-shell.mobile-view-chat .composer-wrap { display: block; }
    .app.mobile-shell.mobile-view-home .voice-shell {
      bottom: calc(env(safe-area-inset-bottom) + var(--mobile-nav-height) + 62px);
      padding-bottom: 10px;
    }
    .app.mobile-shell.mobile-view-home .composer-voice-button { display: none; }
    .file-attach-button {
      width: 44px;
      height: 44px;
      border-radius: 50%;
      display: grid;
      place-items: center;
      flex: 0 0 auto;
      background: #efefed;
      color: #202020;
      cursor: pointer;
    }
    .file-attach-button:disabled { opacity: .45; cursor: default; }
    .file-attach-button svg {
      width: 18px;
      height: 18px;
      fill: none;
      stroke: currentColor;
      stroke-width: 1.8;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .file-analysis-card {
      max-width: 100% !important;
      width: min(100%, 560px);
      background: #fff !important;
      border: 1px solid #dfdfdc;
      border-radius: 17px !important;
    }
    .file-analysis-title { font-weight: 680; margin-bottom: 5px; }
    .file-analysis-meta { color: #6f6f6b; font-size: 12px; line-height: 1.45; white-space: pre-line; }
    .file-analysis-warning { margin-top: 7px; color: #8a5d43; font-size: 12px; line-height: 1.4; }
    .file-analysis-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
    .file-analysis-actions .action { flex: 1 1 150px; padding: 9px 12px; font-size: 12px; }
  `;
  document.head.appendChild(style);

  const attachButton = document.createElement("button");
  attachButton.id = "fileAttachBtn";
  attachButton.type = "button";
  attachButton.className = "file-attach-button";
  attachButton.setAttribute("aria-label", "Прикрепить файл");
  attachButton.title = "Прикрепить файл";
  attachButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8.5 12.5 6.7-6.7a3 3 0 0 1 4.2 4.2l-8.8 8.8a5 5 0 0 1-7.1-7.1l8.4-8.4"/><path d="m6.4 14.6 8-8"/></svg>';

  const fileInput = document.createElement("input");
  fileInput.id = "fileAttachInput";
  fileInput.type = "file";
  fileInput.hidden = true;
  fileInput.accept = ".pdf,.txt,.doc,.docx,.epub,.ppt,.pptx,.xlsx,.jpg,.jpeg,.png,.tif,.tiff,.bmp,image/jpeg,image/png,image/tiff,image/bmp,application/pdf";

  composer.insertBefore(attachButton, chatVoice || composer.querySelector(".send-button"));
  composer.appendChild(fileInput);

  function addMessage(text, who = "assistant") {
    const item = document.createElement("div");
    item.className = `msg ${who}`;
    item.textContent = String(text || "");
    chat.appendChild(item);
    chat.scrollTop = chat.scrollHeight;
    return item;
  }

  function openChat() {
    const tab = document.querySelector('#mobileBottomNav [data-view="chat"]');
    if (tab) tab.click();
    else app.classList.add("chat-active");
    requestAnimationFrame(() => { chat.scrollTop = chat.scrollHeight; });
  }

  function formatMoment(value, timezoneName) {
    if (!value) return "время не определено";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return String(value);
    try {
      return date.toLocaleString("ru-RU", {
        day: "2-digit", month: "2-digit", year: "numeric",
        hour: "2-digit", minute: "2-digit",
        ...(timezoneName ? {timeZone: timezoneName} : {}),
      });
    } catch (_) {
      return date.toLocaleString("ru-RU", {day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit"});
    }
  }

  function eventMeta(event) {
    const start = formatMoment(event.start, event.start_timezone);
    const end = formatMoment(event.end, event.end_timezone);
    const zones = event.start_timezone || event.end_timezone
      ? [event.start_timezone, event.end_timezone].filter(Boolean).join(" → ")
      : "";
    return [
      `${start} — ${end}`,
      zones,
      event.location || "",
      `Уверенность: ${Math.round(Number(event.confidence || 0) * 100)}%`,
    ].filter(Boolean).join("\n");
  }

  function taskMeta(task) {
    const parts = [];
    if (task.due_at) parts.push("Срок: " + formatMoment(task.due_at, task.due_timezone));
    if (task.category && task.category !== "other") parts.push("Категория: " + task.category);
    if (task.priority && task.priority !== "normal") parts.push("Приоритет: " + task.priority);
    if (task.estimate_minutes) parts.push(`${task.estimate_minutes} мин`);
    parts.push(`Уверенность: ${Math.round(Number(task.confidence || 0) * 100)}%`);
    return parts.join("\n");
  }

  async function applyTask(task, button, draftId = null, index = -1) {
    if (!task.ready || button.disabled) return;
    button.disabled = true;
    const oldText = button.textContent;
    button.textContent = "Добавляю…";
    try {
      const result = await PlannerRequests.request("/api/files/task", {
        method: "POST",
        requestId: PlannerRequests.newId(),
        body: JSON.stringify({
          task,
          draft_id: draftId,
          index,
        }),
      });
      task.imported = {...(task.imported || {}), task: true};
      button.textContent = "Добавлено ✓";
      button.disabled = true;
      document.dispatchEvent(new CustomEvent("planner-library-changed", {detail: {type: "task", item: result.task}}));
    } catch (error) {
      button.disabled = false;
      button.textContent = oldText;
      addMessage("Не удалось добавить задачу: " + (error.message || "ошибка"), "assistant");
    }
  }

  async function applyTaskToCalendar(task, button, draftId = null, index = -1) {
    if (!task.ready || !task.due_at || button.disabled) return;
    button.disabled = true;
    const oldText = button.textContent;
    button.textContent = "Добавляю…";
    try {
      const result = await PlannerRequests.request("/api/files/calendar", {
        method: "POST",
        requestId: PlannerRequests.newId(),
        body: JSON.stringify({task, draft_id: draftId, index}),
      });
      task.imported = {...(task.imported || {}), calendar: true};
      button.textContent = "В календаре ✓";
      button.disabled = true;
      document.dispatchEvent(new CustomEvent("planner-library-changed", {
        detail: {type: "calendar_event", item: result.event},
      }));
    } catch (error) {
      button.disabled = false;
      button.textContent = oldText;
      addMessage("Не удалось добавить в календарь: " + (error.message || "ошибка"), "assistant");
    }
  }

  function renderTask(task, draftId = null, index = -1) {
    const card = document.createElement("div");
    card.className = "msg assistant file-analysis-card";
    if (draftId) card.dataset.importDraftId = draftId;

    const title = document.createElement("div");
    title.className = "file-analysis-title";
    title.textContent = task.title || "Задача";
    card.appendChild(title);

    if (task.description) {
      const description = document.createElement("div");
      description.className = "file-analysis-meta";
      description.textContent = task.description;
      card.appendChild(description);
    }

    const meta = document.createElement("div");
    meta.className = "file-analysis-meta";
    meta.textContent = taskMeta(task);
    card.appendChild(meta);

    const warnings = Array.isArray(task.warnings) ? task.warnings.filter(Boolean) : [];
    if (warnings.length) {
      const warning = document.createElement("div");
      warning.className = "file-analysis-warning";
      warning.textContent = warnings.join(" ");
      card.appendChild(warning);
    }

    const actions = document.createElement("div");
    actions.className = "file-analysis-actions";
    const add = document.createElement("button");
    add.type = "button";
    add.className = "action primary";
    const taskImported = Boolean(task.imported?.task);
    add.textContent = taskImported ? "Добавлено ✓" : (task.ready ? "Добавить задачу" : "Нужна ручная проверка");
    add.disabled = taskImported || !task.ready;
    add.addEventListener("click", () => applyTask(task, add, draftId, index));
    actions.appendChild(add);

    if (task.ready && task.due_at) {
      const calendar = document.createElement("button");
      calendar.type = "button";
      calendar.className = "action";
      const calendarImported = Boolean(task.imported?.calendar);
      calendar.textContent = calendarImported ? "В календаре ✓" : "Добавить в календарь";
      calendar.disabled = calendarImported;
      calendar.addEventListener("click", () => applyTaskToCalendar(task, calendar, draftId, index));
      actions.appendChild(calendar);
    }
    card.appendChild(actions);

    chat.appendChild(card);
  }

  async function applyEvent(event, button, draftId = null, index = -1) {
    if (!event.ready || button.disabled) return;
    button.disabled = true;
    const oldText = button.textContent;
    button.textContent = "Добавляю…";
    try {
      const result = await PlannerRequests.request("/api/files/calendar", {
        method: "POST",
        requestId: PlannerRequests.newId(),
        body: JSON.stringify({event, draft_id: draftId, index}),
      });
      event.imported = {...(event.imported || {}), calendar: true};
      button.textContent = "Добавлено ✓";
      button.disabled = true;
      document.dispatchEvent(new CustomEvent("planner-library-changed", {detail: {type: "calendar_event", item: result.event}}));
    } catch (error) {
      button.disabled = false;
      button.textContent = oldText;
      addMessage("Не удалось добавить событие: " + (error.message || "ошибка"), "assistant");
    }
  }

  function renderEvent(event, draftId = null, index = -1) {
    const card = document.createElement("div");
    card.className = "msg assistant file-analysis-card";
    if (draftId) card.dataset.importDraftId = draftId;

    const title = document.createElement("div");
    title.className = "file-analysis-title";
    title.textContent = event.title || "Событие";
    card.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "file-analysis-meta";
    meta.textContent = eventMeta(event);
    card.appendChild(meta);

    const warnings = Array.isArray(event.warnings) ? event.warnings.filter(Boolean) : [];
    if (warnings.length) {
      const warning = document.createElement("div");
      warning.className = "file-analysis-warning";
      warning.textContent = warnings.join(" ");
      card.appendChild(warning);
    }

    const actions = document.createElement("div");
    actions.className = "file-analysis-actions";
    const add = document.createElement("button");
    add.type = "button";
    add.className = "action primary";
    const calendarImported = Boolean(event.imported?.calendar);
    add.textContent = calendarImported ? "В календаре ✓" : (event.ready ? "Добавить в календарь" : "Нужна ручная проверка");
    add.disabled = calendarImported || !event.ready;
    add.addEventListener("click", () => applyEvent(event, add, draftId, index));
    actions.appendChild(add);
    card.appendChild(actions);

    chat.appendChild(card);
  }

  function renderAnalysis(result, {restored = false} = {}) {
    const draftId = result?.draft_id || null;
    if (draftId) {
      const exists = [...chat.querySelectorAll("[data-import-draft-id]")].some(
        node => node.dataset.importDraftId === draftId
      );
      if (exists) return;
    }
    if (restored) {
      addMessage(`Черновик импорта${result.source_name ? " · " + result.source_name : ""}`, "assistant");
    }
    if (result.summary) addMessage(result.summary, "assistant");
    const warnings = Array.isArray(result.warnings) ? result.warnings.filter(Boolean) : [];
    if (warnings.length) addMessage("Проверь: " + warnings.join(" "), "assistant");

    const tasks = Array.isArray(result.tasks) ? result.tasks : [];
    const events = Array.isArray(result.events) ? result.events : [];

    if (!tasks.length && !events.length) {
      addMessage("В файле не нашёл задач или событий, которые можно добавить.", "assistant");
      return;
    }

    tasks.forEach((task, index) => renderTask(task, draftId, index));
    events.forEach((event, index) => renderEvent(event, draftId, index));
    chat.scrollTop = chat.scrollHeight;
  }

  async function analyzeFile(file) {
    if (!file || !file.name) return;
    const isImage = String(file.type || "").startsWith("image/") || /\.(?:jpe?g|png|tiff?|bmp)$/i.test(file.name);
    const maxBytes = (isImage ? 15 : 20) * 1024 * 1024;
    if (file.size > maxBytes) {
      openChat();
      addMessage(`Файл слишком большой. Максимум ${isImage ? 15 : 20} МБ.`, "assistant");
      return;
    }
    openChat();
    addMessage(`📎 ${file.name}`, "user");
    const loading = addMessage("Разбираю файл и ищу задачи, даты и события…", "assistant");
    attachButton.disabled = true;
    try {
      await PlannerRequests.status();
      const form = new FormData();
      form.append("file", file, file.name);
      const result = await PlannerRequests.request("/api/files/analyze", {
        method: "POST",
        requestId: PlannerRequests.newId(),
        body: form,
      });
      loading.remove();
      renderAnalysis(result);
    } catch (error) {
      loading.textContent = "Не удалось разобрать файл: " + (error.message || "ошибка");
    } finally {
      attachButton.disabled = false;
      fileInput.value = "";
      chat.scrollTop = chat.scrollHeight;
    }
  }

  function draftHasPending(result) {
    const tasks = Array.isArray(result?.tasks) ? result.tasks : [];
    const events = Array.isArray(result?.events) ? result.events : [];
    const taskPending = tasks.some(task => {
      if (!task?.ready) return false;
      if (!task.imported?.task) return true;
      return Boolean(task.due_at) && !task.imported?.calendar;
    });
    const eventPending = events.some(event => event?.ready && !event.imported?.calendar);
    return taskPending || eventPending;
  }

  async function restoreImportDrafts() {
    try {
      const payload = await PlannerRequests.request("/api/files/drafts");
      const drafts = Array.isArray(payload?.drafts) ? payload.drafts : [];
      const pending = drafts
        .map(draft => ({...draft.result, draft_id: draft.draft_id}))
        .filter(draftHasPending)
        .reverse();
      pending.forEach(result => renderAnalysis(result, {restored: true}));
    } catch (_) {
      // Import history is optional; the rest of chat must stay usable.
    }
  }

  attachButton.addEventListener("click", () => fileInput.click());
  restoreImportDrafts();
  fileInput.addEventListener("change", () => analyzeFile(fileInput.files?.[0]));
})();
