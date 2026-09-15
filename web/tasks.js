(() => {
  "use strict";
  const categoryLabels = {
    work: "Работа", health: "Здоровье", rest: "Отдых", travel: "Поездки",
    family: "Семья", personal: "Личное", other: "Прочее",
  };
  const priorityLabels = {high: "Высокий", normal: "Обычный", low: "Низкий"};
  let installed = false;
  let taskMode = false;

  function api(path, options) {
    if (typeof window.api !== "function") throw new Error("API недоступен");
    return window.api(path, options);
  }

  function formatDate(value) {
    if (!value) return "без срока";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "без срока";
    return date.toLocaleString("ru-RU", {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"});
  }

  function notify(text) {
    if (typeof window.msg === "function") window.msg(text);
    else console.info(text);
  }

  function taskCard(task) {
    const card = document.createElement("div");
    card.className = "planner-task-card" + (task.status === "done" ? " completed" : "");
    const title = document.createElement("div");
    title.className = "planner-task-title";
    title.textContent = task.title;
    const meta = document.createElement("div");
    meta.className = "planner-task-meta";
    const parts = [
      categoryLabels[task.category] || "Прочее",
      priorityLabels[task.priority] || "Обычный",
      formatDate(task.due_at),
    ];
    if (task.estimate_minutes) parts.push(`${task.estimate_minutes} мин`);
    if (task.calendar_event_id) parts.push("в календаре");
    meta.textContent = parts.join(" · ");
    const actions = document.createElement("div");
    actions.className = "planner-task-actions";

    const action = (label, handler, className = "") => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "planner-task-action " + className;
      button.textContent = label;
      button.onclick = async (event) => {
        event.stopPropagation();
        button.disabled = true;
        try { await handler(); } finally { button.disabled = false; }
      };
      return button;
    };

    if (task.status !== "done") {
      actions.append(action("Выполнено", async () => {
        await api(`/api/tasks/${task.task_id}`, {method: "PATCH", body: JSON.stringify({status: "done"})});
        await renderTasks();
      }));
      actions.append(action("Изменить", async () => editTask(task)));
    } else {
      actions.append(action("Вернуть", async () => {
        await api(`/api/tasks/${task.task_id}`, {method: "PATCH", body: JSON.stringify({status: "open"})});
        await renderTasks();
      }));
    }
    actions.append(action("Удалить", async () => {
      if (!confirm(`Удалить задачу «${task.title}»?`)) return;
      await api(`/api/tasks/${task.task_id}`, {method: "DELETE"});
      await renderTasks();
    }, "danger"));
    card.append(title, meta, actions);
    return card;
  }

  async function createTask() {
    const title = prompt("Название задачи");
    if (!title?.trim()) return;
    const estimateRaw = prompt("Сколько времени нужно, минут? Можно оставить пустым.", "60");
    const estimate = estimateRaw?.trim() ? Number(estimateRaw) : null;
    const dueRaw = prompt("Срок. Формат: 2026-09-18T18:00. Можно оставить пустым.");
    let dueAt = null;
    if (dueRaw?.trim()) {
      const local = new Date(dueRaw.trim());
      if (!Number.isFinite(local.getTime())) throw new Error("Некорректный срок задачи");
      dueAt = local.toISOString();
    }
    await api("/api/tasks", {
      method: "POST",
      body: JSON.stringify({title: title.trim(), estimate_minutes: estimate, due_at: dueAt, flexible: true}),
    });
    await renderTasks();
  }

  async function editTask(task) {
    const title = prompt("Название задачи", task.title);
    if (!title?.trim()) return;
    const estimateRaw = prompt("Длительность, минут", task.estimate_minutes || "");
    const estimate = estimateRaw?.trim() ? Number(estimateRaw) : null;
    const priority = prompt("Приоритет: high / normal / low", task.priority || "normal") || task.priority;
    const category = prompt("Категория: work / health / rest / travel / family / personal / other", task.category || "other") || task.category;
    await api(`/api/tasks/${task.task_id}`, {
      method: "PATCH",
      body: JSON.stringify({title: title.trim(), estimate_minutes: estimate, priority, category}),
    });
    await renderTasks();
  }

  async function planTasks() {
    const preview = await api("/api/tasks/schedule/preview");
    const proposals = preview.proposals || [];
    if (!proposals.length) {
      notify("Не нашёл задач, которые можно безопасно поставить в календарь. Для автопланирования нужны срок и оценка длительности.");
      return;
    }
    const lines = proposals.map((item, index) => {
      const start = new Date(item.start).toLocaleString("ru-RU", {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"});
      return `${index + 1}. ${item.title} — ${start}, ${item.estimate_minutes} мин`;
    });
    const approved = confirm(`Предлагаю поставить в календарь:\n\n${lines.join("\n")}\n\nПрименить этот план?`);
    if (!approved) return;
    const result = await api("/api/tasks/schedule/apply", {
      method: "POST",
      body: JSON.stringify({proposals}),
    });
    if (result.errors?.length) {
      notify(`В календарь добавлено: ${result.applied_count}. Часть окон успела измениться — обнови план.`);
    } else {
      notify(`Готово. В календарь добавлено задач: ${result.applied_count}.`);
    }
    await renderTasks();
  }

  async function renderTasks() {
    if (!taskMode) return;
    const list = document.getElementById("libraryList");
    if (!list) return;
    list.innerHTML = '<div class="library-loading">Загружаю задачи…</div>';
    try {
      const data = await api("/api/tasks?status=all");
      const tasks = data.tasks || [];
      list.replaceChildren();
      const toolbar = document.createElement("div");
      toolbar.className = "planner-task-toolbar";
      const create = document.createElement("button");
      create.type = "button";
      create.className = "planner-task-primary";
      create.textContent = "+ Задача";
      create.onclick = () => createTask().catch((error) => notify(error.message));
      const plan = document.createElement("button");
      plan.type = "button";
      plan.className = "planner-task-primary secondary";
      plan.textContent = "Распланировать";
      plan.onclick = () => planTasks().catch((error) => notify(error.message));
      const summary = document.createElement("div");
      summary.className = "planner-task-summary";
      summary.textContent = `Открыто ${data.summary?.open || 0} · просрочено ${data.summary?.overdue || 0}`;
      toolbar.append(create, plan, summary);
      list.append(toolbar);
      if (!tasks.length) {
        const empty = document.createElement("div");
        empty.className = "library-empty";
        empty.textContent = "Задач пока нет.";
        list.append(empty);
        return;
      }
      for (const task of tasks) list.append(taskCard(task));
    } catch (error) {
      list.innerHTML = `<div class="library-error">${String(error.message || error)}</div>`;
    }
  }

  function install() {
    if (installed) return;
    const notes = document.getElementById("libraryNotesTab");
    const reminders = document.getElementById("libraryRemindersTab");
    const tabs = notes?.parentElement;
    if (!notes || !reminders || !tabs) return;
    installed = true;
    tabs.style.gridTemplateColumns = "1fr 1fr 1fr";
    const button = document.createElement("button");
    button.id = "libraryTasksTab";
    button.className = "library-tab";
    button.type = "button";
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", "false");
    button.textContent = "Задачи";
    tabs.append(button);

    button.addEventListener("click", () => {
      taskMode = true;
      notes.classList.remove("active");
      reminders.classList.remove("active");
      button.classList.add("active");
      notes.setAttribute("aria-selected", "false");
      reminders.setAttribute("aria-selected", "false");
      button.setAttribute("aria-selected", "true");
      renderTasks();
    });
    for (const other of [notes, reminders]) {
      other.addEventListener("click", () => {
        taskMode = false;
        button.classList.remove("active");
        button.setAttribute("aria-selected", "false");
      });
    }

    const style = document.createElement("style");
    style.textContent = `
      .planner-task-toolbar{display:grid;grid-template-columns:auto auto 1fr;gap:8px;align-items:center;margin:0 0 12px}
      .planner-task-primary{min-height:40px;padding:0 12px;border-radius:12px;background:#111;color:#fff;font-weight:650;cursor:pointer}
      .planner-task-primary.secondary{background:#ececea;color:#222}
      .planner-task-summary{text-align:right;color:#888883;font-size:12px}
      .planner-task-card{padding:15px 16px;margin:0 0 10px;border:1px solid #e5e5e2;border-radius:18px;background:#fff;box-shadow:0 3px 14px rgba(0,0,0,.035)}
      .planner-task-card.completed .planner-task-title{text-decoration:line-through;color:#777773}
      .planner-task-title{font-size:15px;font-weight:650;line-height:1.35;overflow-wrap:anywhere}
      .planner-task-meta{margin-top:7px;color:#8d8d88;font-size:12px;line-height:1.35}
      .planner-task-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:11px}
      .planner-task-action{min-height:34px;padding:0 10px;border-radius:10px;background:#efefed;color:#333;font-size:12px;font-weight:600;cursor:pointer}
      .planner-task-action.danger{background:#f2dddd;color:#8a2d2d}
      @media(max-width:520px){.planner-task-toolbar{grid-template-columns:1fr 1fr}.planner-task-summary{grid-column:1/-1;text-align:left}}
    `;
    document.head.append(style);
  }

  document.addEventListener("planner-ready", install);
  document.addEventListener("planner-library-changed", () => { if (taskMode) renderTasks(); });
  if (document.readyState !== "loading") setTimeout(install, 0);
})();
