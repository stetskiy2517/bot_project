(() => {
  "use strict";

  if (window.__plannerUnifiedTasks) return;
  window.__plannerUnifiedTasks = true;

  const repeatLabels = {
    daily: "ежедневно",
    weekly: "еженедельно",
    weekdays: "по будням",
    weekends: "по выходным",
  };

  let installed = false;
  let syncTimer = null;
  let syncing = false;

  function api(path, options) {
    if (typeof window.api !== "function") throw new Error("API недоступен");
    return window.api(path, options);
  }

  function taskTabActive() {
    const tab = document.getElementById("libraryTasksTab");
    return Boolean(tab && (tab.classList.contains("active") || tab.getAttribute("aria-selected") === "true"));
  }

  function formatDate(value) {
    if (!value) return "без времени";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "без времени";
    return date.toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function promptDateTime(value) {
    const date = new Date(value || Date.now() + 60 * 60 * 1000);
    if (!Number.isFinite(date.getTime())) return "";
    const pad = number => String(number).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function repeatLabel(rule) {
    const value = String(rule || "").trim();
    if (!value) return "";
    if (repeatLabels[value]) return repeatLabels[value];
    if (value.startsWith("weekly:")) return "еженедельно";
    return "повтор";
  }

  function notify(text) {
    const list = document.getElementById("libraryList");
    const message = String(text || "").trim();
    if (!list || !message) return;
    let box = document.getElementById("plannerUnifiedTaskFeedback");
    if (!box) {
      box = document.createElement("div");
      box.id = "plannerUnifiedTaskFeedback";
      box.className = "planner-task-feedback";
      const toolbar = list.querySelector(".planner-task-toolbar");
      if (toolbar) toolbar.insertAdjacentElement("afterend", box);
      else list.prepend(box);
    }
    box.textContent = message;
  }

  function currentFilters() {
    const filters = document.querySelector("#libraryList .planner-task-filters");
    if (!filters) return {query: "", category: "", priority: ""};
    const controls = filters.querySelectorAll("input, select");
    return {
      query: String(controls[0]?.value || "").trim().toLocaleLowerCase("ru-RU"),
      category: String(controls[1]?.value || "").trim(),
      priority: String(controls[2]?.value || "").trim(),
    };
  }

  function mergeReminderData(baseItems, detailItems) {
    const details = new Map((detailItems || []).map(item => [Number(item.id), item]));
    return (baseItems || []).map(item => ({...item, ...(details.get(Number(item.id)) || {})}));
  }

  function visibleReminder(item, filters) {
    if (filters.query && !String(item.text || "").toLocaleLowerCase("ru-RU").includes(filters.query)) return false;
    if (filters.category && String(item.category || "") !== filters.category) return false;
    // Lightweight tasks with a notification do not have a priority. A priority filter
    // therefore intentionally shows only planner tasks with an explicit priority.
    if (filters.priority) return false;
    return true;
  }

  function action(label, name, reminderId, className = "") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `planner-task-action ${className}`.trim();
    button.dataset.unifiedReminderAction = name;
    button.dataset.reminderId = String(reminderId);
    button.textContent = label;
    return button;
  }

  function reminderCard(item) {
    const reminderId = Number(item.id);
    const completed = item.status === "completed";
    const card = document.createElement("div");
    card.className = "planner-task-card planner-reminder-task" + (completed ? " completed" : "");
    card.dataset.reminderId = String(reminderId);

    const title = document.createElement("div");
    title.className = "planner-task-title";
    title.textContent = item.text || "Без названия";

    const meta = document.createElement("div");
    meta.className = "planner-task-meta";
    const parts = [];
    if (item.category_label) parts.push(item.category_label);
    parts.push(`Уведомление ${formatDate(item.remind_at)}`);
    const repeat = repeatLabel(item.repeat_rule);
    if (repeat) parts.push(repeat);
    if (item.status === "delivered") parts.push("уведомление отправлено");
    meta.textContent = parts.join(" · ");

    const actions = document.createElement("div");
    actions.className = "planner-task-actions";
    if (completed) {
      actions.append(action("Вернуть", "reopen", reminderId));
    } else {
      actions.append(action("Выполнено", "complete", reminderId));
      const edit = document.createElement("button");
      edit.type = "button";
      edit.className = "planner-task-action";
      edit.setAttribute("data-reminder-edit", String(reminderId));
      edit.textContent = "Изменить";
      actions.append(edit, action("Перенести", "reschedule", reminderId));
    }
    actions.append(action("Удалить", "delete", reminderId, "danger"));
    card.append(title, meta, actions);
    return card;
  }

  async function loadReminders() {
    const [library, detailResult] = await Promise.all([
      api("/api/library"),
      api("/api/mobile/reminders/details").catch(() => ({items: []})),
    ]);
    return mergeReminderData(library.reminders || [], detailResult.items || []);
  }

  async function syncReminderTasks() {
    if (syncing || !taskTabActive()) return;
    const list = document.getElementById("libraryList");
    if (!list || !list.querySelector(".planner-task-toolbar")) return;
    syncing = true;
    try {
      const reminders = (await loadReminders()).filter(item => visibleReminder(item, currentFilters()));
      if (!taskTabActive()) return;

      list.querySelectorAll(".planner-reminder-task, .planner-task-reminder-summary").forEach(node => node.remove());
      if (reminders.length) {
        const empty = list.querySelector(".library-empty");
        if (empty && empty.textContent?.includes("Задач")) empty.remove();
      }

      const summary = document.createElement("div");
      summary.className = "planner-task-reminder-summary";
      summary.textContent = `С уведомлением ${reminders.filter(item => item.status !== "completed").length}`;
      const filters = list.querySelector(".planner-task-filters");
      if (filters) filters.insertAdjacentElement("afterend", summary);
      for (const reminder of reminders) list.append(reminderCard(reminder));
    } catch (error) {
      notify(error?.message || "Не удалось загрузить задачи с уведомлением.");
    } finally {
      syncing = false;
    }
  }

  function scheduleSync(delay = 60) {
    clearTimeout(syncTimer);
    syncTimer = setTimeout(syncReminderTasks, delay);
  }

  function refreshTaskView() {
    const tab = document.getElementById("libraryTasksTab");
    if (tab && taskTabActive()) tab.click();
  }

  async function runReminderAction(button) {
    const reminderId = Number(button.dataset.reminderId || 0);
    const actionName = button.dataset.unifiedReminderAction;
    if (!reminderId || !actionName) return;
    button.disabled = true;
    try {
      if (actionName === "complete" || actionName === "reopen") {
        await api(`/api/library/reminders/${reminderId}/complete`, {
          method: "POST",
          body: JSON.stringify({completed: actionName === "complete"}),
        });
      } else if (actionName === "reschedule") {
        const raw = prompt("Новое время", promptDateTime(Date.now() + 60 * 60 * 1000));
        if (!raw?.trim()) return;
        const parsed = new Date(raw.trim());
        if (!Number.isFinite(parsed.getTime())) throw new Error("Некорректная дата или время");
        if (parsed.getTime() <= Date.now()) throw new Error("Новое время должно быть в будущем");
        await api(`/api/library/reminders/${reminderId}/reschedule`, {
          method: "POST",
          body: JSON.stringify({remind_at: parsed.toISOString()}),
        });
      } else if (actionName === "delete") {
        const title = button.closest(".planner-reminder-task")?.querySelector(".planner-task-title")?.textContent || "эту задачу";
        if (!confirm(`Удалить задачу «${title}»?`)) return;
        await api(`/api/library/reminders/${reminderId}`, {method: "DELETE"});
      }
      document.dispatchEvent(new Event("planner-library-changed"));
      setTimeout(refreshTaskView, 20);
    } catch (error) {
      notify(error?.message || "Не удалось изменить задачу.");
    } finally {
      button.disabled = false;
    }
  }

  function mutationNeedsSync(mutations) {
    return mutations.some(mutation => Array.from(mutation.addedNodes || []).some(node =>
      node.nodeType === 1 && (
        node.matches?.(".planner-task-toolbar, .planner-task-filters") ||
        node.querySelector?.(".planner-task-toolbar, .planner-task-filters")
      )
    ));
  }

  function ensureNotificationRepeatEditor(backdrop) {
    const sheet = backdrop.querySelector(".reminder-edit-sheet");
    const actions = backdrop.querySelector(".reminder-edit-actions");
    const save = backdrop.querySelector("[data-reminder-edit-save]");
    const reminderId = Number(save?.dataset.reminderEditSave || 0);
    if (!sheet || !actions || !reminderId || sheet.querySelector(".unified-notification-repeat")) return;

    const details = document.createElement("details");
    details.className = "unified-notification-repeat";
    const summary = document.createElement("summary");
    summary.textContent = "Повторные уведомления";
    const body = document.createElement("div");
    body.className = "unified-notification-repeat-body";
    details.append(summary, body);
    actions.insertAdjacentElement("beforebegin", details);

    details.addEventListener("toggle", async () => {
      if (!details.open || details.dataset.loaded === "true") return;
      details.dataset.loaded = "true";
      body.textContent = "Загружаю…";
      try {
        const payload = await api(`/api/assistant/reminders/${reminderId}/notifications`);
        const policy = payload.policy || {};
        body.replaceChildren();

        const explanation = document.createElement("p");
        explanation.className = "reminder-edit-help unified-notification-repeat-status";
        explanation.textContent = "Повтор доставки, если задача ещё не выполнена. 0 повторов — выключено.";

        const intervalLabel = document.createElement("label");
        intervalLabel.className = "reminder-edit-field";
        intervalLabel.textContent = "Интервал, минут";
        const interval = document.createElement("input");
        interval.type = "number";
        interval.min = "5";
        interval.max = "1440";
        interval.value = String(policy.interval_minutes ?? 30);
        interval.setAttribute("aria-label", "Интервал повторов в минутах");
        intervalLabel.appendChild(interval);

        const countLabel = document.createElement("label");
        countLabel.className = "reminder-edit-field";
        countLabel.textContent = "Количество повторов";
        const count = document.createElement("input");
        count.type = "number";
        count.min = "0";
        count.max = "5";
        count.value = String(policy.max_repeats ?? 0);
        count.setAttribute("aria-label", "Число повторов, максимум 5");
        countLabel.appendChild(count);

        const saveRepeats = document.createElement("button");
        saveRepeats.type = "button";
        saveRepeats.className = "reminder-edit-button cancel unified-notification-repeat-save";
        saveRepeats.textContent = "Сохранить повторы";
        saveRepeats.addEventListener("click", async () => {
          saveRepeats.disabled = true;
          try {
            await api(`/api/assistant/reminders/${reminderId}/notifications`, {
              method: "POST",
              body: JSON.stringify({
                interval_minutes: Number(interval.value),
                max_repeats: Number(count.value),
              }),
            });
            explanation.textContent = "Сохранено. Тихие часы задаются в настройках уведомлений.";
          } catch (error) {
            explanation.textContent = error?.message || "Не удалось сохранить повторные уведомления.";
          } finally {
            saveRepeats.disabled = false;
          }
        });
        body.append(explanation, intervalLabel, countLabel, saveRepeats);
      } catch (error) {
        body.textContent = error?.message || "Не удалось загрузить повторные уведомления.";
      }
    });
  }

  function relabelReminderEditor() {
    const backdrop = document.getElementById("reminderEditBackdrop");
    if (!backdrop?.classList.contains("open")) return;
    const title = backdrop.querySelector(".reminder-edit-title");
    const sheet = backdrop.querySelector(".reminder-edit-sheet");
    if (title) title.textContent = "Задача с уведомлением";
    if (sheet) sheet.setAttribute("aria-label", "Изменить задачу с уведомлением");
    ensureNotificationRepeatEditor(backdrop);
  }

  function install() {
    if (installed) return true;
    const tabs = document.getElementById("libraryTabs");
    const reminders = document.getElementById("libraryRemindersTab");
    const tasks = document.getElementById("libraryTasksTab");
    const list = document.getElementById("libraryList");
    if (!tabs || !reminders || !tasks || !list) return false;
    installed = true;

    reminders.hidden = true;
    reminders.style.display = "none";
    reminders.setAttribute("aria-hidden", "true");
    reminders.tabIndex = -1;
    tabs.style.gridTemplateColumns = "repeat(2, minmax(0, 1fr))";

    const style = document.createElement("style");
    style.id = "unifiedTaskReminderStyles";
    style.textContent = `
      .planner-task-reminder-summary{margin:-3px 0 10px;color:#8d8d88;font-size:12px}
      .planner-reminder-task{border-color:#ddddda}
      #libraryRemindersTab{display:none!important}
      .unified-notification-repeat{margin:2px 0 14px;padding:11px 0;border-top:1px solid #ecece8;border-bottom:1px solid #ecece8}
      .unified-notification-repeat summary{cursor:pointer;font-size:13px;font-weight:650;color:#4a4a47}
      .unified-notification-repeat-body{padding-top:12px}
      .unified-notification-repeat .reminder-edit-field{margin-bottom:10px}
      .unified-notification-repeat-save{width:100%;margin-top:2px}
    `;
    document.head.appendChild(style);

    tasks.addEventListener("click", () => scheduleSync(90));
    list.addEventListener("click", event => {
      const button = event.target.closest("[data-unified-reminder-action]");
      if (!button) return;
      event.preventDefault();
      event.stopPropagation();
      runReminderAction(button);
    });

    const listObserver = new MutationObserver(mutations => {
      if (taskTabActive() && mutationNeedsSync(mutations)) scheduleSync(80);
    });
    listObserver.observe(list, {childList: true, subtree: true});

    const editor = document.getElementById("reminderEditBackdrop");
    if (editor) {
      const editorObserver = new MutationObserver(relabelReminderEditor);
      editorObserver.observe(editor, {childList: true, subtree: true, attributes: true, attributeFilter: ["class"]});
    }

    document.addEventListener("planner-library-changed", () => {
      if (taskTabActive()) setTimeout(refreshTaskView, 30);
    });
    if (taskTabActive()) scheduleSync(50);
    return true;
  }

  function installSoon() {
    if (install()) return;
    setTimeout(installSoon, 100);
  }

  document.addEventListener("planner-ready", installSoon);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", installSoon, {once: true});
  } else {
    installSoon();
  }
})();
