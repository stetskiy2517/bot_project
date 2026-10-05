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

  async function saveReviewedItem(draftId, kind, index, item) {
    if (!draftId) return item;
    const result = await PlannerRequests.request(`/api/files/drafts/${encodeURIComponent(draftId)}/item`, {
      method: "PATCH",
      requestId: PlannerRequests.newId(),
      body: JSON.stringify({kind, index, item}),
    });
    return result.item || item;
  }

  async function reviewTask(task, card, draftId, index) {
    if (!window.PlannerTaskEditor?.openTask) {
      addMessage("Редактор задачи ещё загружается. Попробуйте ещё раз.", "assistant");
      return;
    }
    const edited = await window.PlannerTaskEditor.openTask({
      task: {
        ...task,
        status: "open",
        flexible: true,
      },
    });
    if (!edited) return;
    const reviewed = {
      ...task,
      ...edited,
      confidence: 1,
      ready: true,
      warnings: [],
    };
    const saved = await saveReviewedItem(draftId, "task", index, reviewed);
    Object.assign(task, saved, {ready: true, warnings: []});
    card.remove();
    renderTask(task, draftId, index);
    chat.scrollTop = chat.scrollHeight;
  }

  async function reviewEvent(event, card, draftId, index) {
    if (!window.PlannerEventEditor?.openDraft) {
      addMessage("Редактор события ещё загружается. Попробуйте ещё раз.", "assistant");
      return;
    }
    const edited = await window.PlannerEventEditor.openDraft(event);
    if (!edited) return;
    const reviewed = {
      ...event,
      ...edited,
      confidence: 1,
      ready: true,
      warnings: [],
    };
    const saved = await saveReviewedItem(draftId, "event", index, reviewed);
    Object.assign(event, saved, {ready: true, warnings: []});
    card.remove();
    renderEvent(event, draftId, index);
    chat.scrollTop = chat.scrollHeight;
  }

  async function createImportedTask(task, draftId = null, index = -1) {
    const existingId = Number(task.imported?.task || 0);
    if (existingId > 0) return {task_id: existingId, title: task.title};

    if (task.imported?.task === true) {
      const search = await PlannerRequests.request(`/api/tasks?status=all&q=${encodeURIComponent(task.title || "")}`);
      const exact = (search.tasks || []).find(item => {
        if (String(item.title || "").trim() !== String(task.title || "").trim()) return false;
        const left = task.due_at ? new Date(task.due_at).getTime() : null;
        const right = item.due_at ? new Date(item.due_at).getTime() : null;
        return left === right;
      });
      if (exact?.task_id) {
        task.imported = {...(task.imported || {}), task: Number(exact.task_id)};
        if (draftId) {
          await PlannerRequests.request(`/api/files/drafts/${encodeURIComponent(draftId)}/mark`, {
            method: "POST",
            requestId: PlannerRequests.newId(),
            body: JSON.stringify({kind: "task", index, target: "task", value: Number(exact.task_id)}),
          });
        }
        return exact;
      }
    }

    const result = await PlannerRequests.request("/api/files/task", {
      method: "POST",
      requestId: PlannerRequests.newId(),
      body: JSON.stringify({task, draft_id: draftId, index}),
    });
    task.imported = {...(task.imported || {}), task: Number(result.task.task_id)};
    document.dispatchEvent(new CustomEvent("planner-library-changed", {detail: {type: "task", item: result.task}}));
    return result.task;
  }

  async function editTaskForCalendar(task, draftId, index) {
    if (!window.PlannerTaskEditor?.openTask) throw new Error("Редактор задачи ещё загружается.");
    const edited = await window.PlannerTaskEditor.openTask({
      task: {...task, status: "open", flexible: true},
    });
    if (!edited) return null;
    const reviewed = {
      ...task,
      ...edited,
      confidence: 1,
      ready: true,
      warnings: [],
    };
    const saved = await saveReviewedItem(draftId, "task", index, reviewed);
    Object.assign(task, saved, {ready: true, warnings: []});
    return task;
  }

  async function applyTask(task, button, draftId = null, index = -1) {
    if (!task.ready || button.disabled) return;
    button.disabled = true;
    const oldText = button.textContent;
    button.textContent = "Добавляю…";
    try {
      await createImportedTask(task, draftId, index);
      button.textContent = "Добавлено ✓";
      button.disabled = true;
    } catch (error) {
      button.disabled = false;
      button.textContent = oldText;
      addMessage("Не удалось добавить задачу: " + (error.message || "ошибка"), "assistant");
    }
  }

  function scheduleSkipText(skipped = []) {
    const reason = skipped?.[0]?.reason;
    if (reason === "missing_estimate") return "Укажи длительность задачи.";
    if (reason === "missing_deadline") return "Укажи срок задачи.";
    if (reason === "overdue") return "Срок задачи уже прошёл.";
    if (reason === "already_scheduled") return "Для этой задачи уже выделено время в календаре.";
    if (reason === "fixed") return "Разреши планирование задачи в свободное окно.";
    if (reason === "no_slot_before_deadline") return "До срока нет свободного окна. Измени время вручную или перенеси срок.";
    return "Не удалось подобрать свободное окно для задачи.";
  }

  async function applyTaskToCalendar(task, button, taskButton, draftId = null, index = -1) {
    if (!task.ready || !task.due_at || button.disabled) return;
    button.disabled = true;
    const oldText = button.textContent;
    button.textContent = "Подбираю время…";
    try {
      if (!task.estimate_minutes) {
        const edited = await editTaskForCalendar(task, draftId, index);
        if (!edited) {
          button.disabled = false;
          button.textContent = oldText;
          return;
        }
        if (!task.due_at || !task.estimate_minutes) throw new Error("Для календаря нужны срок и длительность.");
      }

      const createdTask = await createImportedTask(task, draftId, index);
      if (taskButton) {
        taskButton.textContent = "Добавлено ✓";
        taskButton.disabled = true;
      }

      const preview = await PlannerRequests.request(`/api/tasks/${Number(createdTask.task_id)}/schedule/preview`);
      const proposal = (preview.proposals || []).find(item => Number(item.task_id) === Number(createdTask.task_id));
      if (!proposal) throw new Error(scheduleSkipText(preview.skipped || []));
      if (!window.PlannerTaskEditor?.confirmPlan) throw new Error("Редактор плана ещё загружается.");

      const approved = await window.PlannerTaskEditor.confirmPlan([proposal]);
      if (!approved) {
        button.disabled = false;
        button.textContent = oldText;
        return;
      }

      const result = await PlannerRequests.request("/api/tasks/schedule/apply", {
        method: "POST",
        requestId: PlannerRequests.newId(),
        body: JSON.stringify({proposals: approved}),
      });
      if (!result.applied_count) {
        throw new Error(result.errors?.[0]?.error || "Не удалось записать задачу в календарь.");
      }

      const applied = result.applied[0];
      task.imported = {
        ...(task.imported || {}),
        task: Number(createdTask.task_id),
        calendar: applied?.calendar_event_id || true,
      };
      if (draftId) {
        await PlannerRequests.request(`/api/files/drafts/${encodeURIComponent(draftId)}/mark`, {
          method: "POST",
          requestId: PlannerRequests.newId(),
          body: JSON.stringify({
            kind: "task",
            index,
            target: "calendar",
            value: applied?.calendar_event_id || true,
          }),
        });
      }
      button.textContent = "В календаре ✓";
      button.disabled = true;
      document.dispatchEvent(new CustomEvent("planner-library-changed", {
        detail: {type: "task", item: applied},
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
    add.textContent = taskImported ? "Добавлено ✓" : (task.ready ? "Добавить задачу" : "Проверить и исправить");
    add.disabled = taskImported;
    add.addEventListener("click", () => {
      if (!task.ready) return reviewTask(task, card, draftId, index).catch(error => addMessage("Не удалось сохранить исправления: " + (error.message || "ошибка"), "assistant"));
      return applyTask(task, add, draftId, index);
    });
    actions.appendChild(add);

    if (task.ready && task.due_at) {
      const calendar = document.createElement("button");
      calendar.type = "button";
      calendar.className = "action";
      const calendarImported = Boolean(task.imported?.calendar);
      calendar.textContent = calendarImported ? "В календаре ✓" : "Добавить в календарь";
      calendar.disabled = calendarImported;
      calendar.addEventListener("click", () => applyTaskToCalendar(task, calendar, add, draftId, index));
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
    add.textContent = calendarImported ? "В календаре ✓" : (event.ready ? "Добавить в календарь" : "Проверить и исправить");
    add.disabled = calendarImported;
    add.addEventListener("click", () => {
      if (!event.ready) return reviewEvent(event, card, draftId, index).catch(error => addMessage("Не удалось сохранить исправления: " + (error.message || "ошибка"), "assistant"));
      return applyEvent(event, add, draftId, index);
    });
    actions.appendChild(add);
    card.appendChild(actions);

    chat.appendChild(card);
  }

  function renderAnalysis(result, {restored = false, replaceExisting = false} = {}) {
    const draftId = result?.draft_id || null;
    if (draftId) {
      const existing = [...chat.querySelectorAll("[data-import-draft-id]")].filter(
        node => node.dataset.importDraftId === draftId
      );
      if (replaceExisting) existing.forEach(node => node.remove());
      else if (existing.length) return;
    }
    if (restored) {
      const summary = addMessage(`Черновик импорта${result.source_name ? " · " + result.source_name : ""}`, "assistant");
      if (draftId) summary.dataset.importDraftSummary = draftId;
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
      if (!task?.ready) return true;
      if (!task.imported?.task) return true;
      return Boolean(task.due_at) && !task.imported?.calendar;
    });
    const eventPending = events.some(event => !event?.ready || !event.imported?.calendar);
    return taskPending || eventPending;
  }

  let restorePromise = null;

  async function restoreImportDrafts({replaceExisting = false} = {}) {
    if (restorePromise) return restorePromise;
    restorePromise = (async () => {
      try {
        const payload = await PlannerRequests.request("/api/files/drafts");
        const drafts = Array.isArray(payload?.drafts) ? payload.drafts : [];
        const pending = drafts
          .map(draft => ({...draft.result, draft_id: draft.draft_id}))
          .filter(draftHasPending)
          .reverse();

        if (replaceExisting) {
          const pendingIds = new Set(pending.map(item => item.draft_id).filter(Boolean));
          chat.querySelectorAll("[data-import-draft-summary]").forEach(node => {
            if (pendingIds.has(node.dataset.importDraftSummary)) node.remove();
          });
        }

        pending.forEach(result => renderAnalysis(result, {restored: true, replaceExisting}));
      } catch (_) {
        // Import history is optional; the rest of chat must stay usable.
      } finally {
        restorePromise = null;
      }
    })();
    return restorePromise;
  }

  attachButton.addEventListener("click", () => fileInput.click());
  restoreImportDrafts();
  document.addEventListener("planner-view-changed", event => {
    // Existing import cards stay in the chat DOM while switching views.
    // Re-fetch only to recover drafts that are actually missing; never remove
    // and append them again, otherwise stale imports jump below newer messages.
    if (event.detail?.view === "chat") restoreImportDrafts();
  });
  fileInput.addEventListener("change", () => analyzeFile(fileInput.files?.[0]));
})();
