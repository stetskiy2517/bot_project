(() => {
  "use strict";

  const categoryLabels = {
    work: "Работа", health: "Здоровье", rest: "Отдых", travel: "Поездки",
    family: "Семья", personal: "Личное", other: "Прочее",
  };
  const priorityLabels = {high: "Высокий", normal: "Обычный", low: "Низкий"};
  const repeatLabels = {daily: "ежедневно", weekly: "еженедельно", monthly: "ежемесячно"};

  let installed = false;
  let taskMode = false;
  let taskQuery = "";
  let taskCategory = "";
  let taskPriority = "";
  let searchTimer = null;
  let feedbackTimer = null;

  function api(path, options) {
    if (typeof window.api !== "function") throw new Error("API недоступен");
    return window.api(path, options);
  }

  function editor() {
    if (!window.PlannerTaskEditor) throw new Error("Редактор задач ещё загружается. Повторите через секунду.");
    return window.PlannerTaskEditor;
  }

  function formatDate(value) {
    if (!value) return "без срока";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "без срока";
    return date.toLocaleString("ru-RU", {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"});
  }

  function notify(text) {
    const message = String(text || "").trim();
    if (!message) return;
    const list = document.getElementById("libraryList");
    if (taskMode && list) {
      let box = document.getElementById("plannerTaskFeedback");
      if (!box) {
        box = document.createElement("div");
        box.id = "plannerTaskFeedback";
        box.className = "planner-task-feedback";
        const toolbar = list.querySelector(".planner-task-toolbar");
        if (toolbar) toolbar.insertAdjacentElement("afterend", box);
        else list.prepend(box);
      }
      box.textContent = message;
      box.hidden = false;
      clearTimeout(feedbackTimer);
      feedbackTimer = setTimeout(() => document.getElementById("plannerTaskFeedback")?.remove(), 6500);
      return;
    }
    if (typeof window.msg === "function") window.msg(message);
  }

  async function createTask() {
    const payload = await editor().openTask();
    if (!payload) return;
    await api("/api/tasks", {method: "POST", body: JSON.stringify(payload)});
    await renderTasks();
  }

  async function createSubtask(parent) {
    const payload = await editor().openTask({parent});
    if (!payload) return;
    await api("/api/tasks", {method: "POST", body: JSON.stringify(payload)});
    await renderTasks();
  }

  async function editTask(task) {
    const payload = await editor().openTask({task});
    if (!payload) return;
    await api(`/api/tasks/${task.task_id}`, {method: "PATCH", body: JSON.stringify(payload)});
    await renderTasks();
  }

  async function deleteTask(task, card) {
    const row = card?.closest(".planner-task-swipe-row") || card;
    if (row) row.hidden = true;
    const undone = await editor().offerUndo(`Задача «${task.title}» удалена`);
    if (undone) {
      if (row) row.hidden = false;
      return;
    }
    try {
      const result = await api(`/api/tasks/${task.task_id}`, {method: "DELETE"});
      if (result.detached_subtasks) notify(`Подзадачи сохранены отдельно: ${result.detached_subtasks}.`);
      await renderTasks();
    } catch (error) {
      if (row) row.hidden = false;
      throw error;
    }
  }

  function taskCard(task, depth = 0) {
    const card = document.createElement("div");
    card.className = "planner-task-card" + (task.status === "done" ? " completed" : "") + (depth ? " subtask" : "");
    card.dataset.taskId = String(task.task_id);
    card.style.setProperty("--task-depth", String(Math.min(depth, 3)));
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", `Открыть задачу «${task.title}»`);

    const title = document.createElement("div");
    title.className = "planner-task-title";
    title.textContent = task.title;

    const description = document.createElement("div");
    description.className = "planner-task-description";
    description.textContent = String(task.description || "").trim();
    description.hidden = !description.textContent;

    const meta = document.createElement("div");
    meta.className = "planner-task-meta";
    const parts = [categoryLabels[task.category] || "Прочее", priorityLabels[task.priority] || "Обычный", formatDate(task.due_at)];
    if (task.estimate_minutes) parts.push(`${task.estimate_minutes} мин`);
    if (task.repeat_rule && repeatLabels[task.repeat_rule]) parts.push(repeatLabels[task.repeat_rule]);
    if (task.calendar_event_id) parts.push("в календаре");
    meta.textContent = parts.join(" · ");

    const actions = document.createElement("div");
    actions.className = "planner-task-actions";
    const action = (label, handler, className = "") => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `planner-task-action ${className}`.trim();
      button.textContent = label;
      button.onclick = async event => {
        event.stopPropagation();
        button.disabled = true;
        try { await handler(); }
        catch (error) { notify(error?.message || String(error)); }
        finally { button.disabled = false; }
      };
      return button;
    };

    if (task.status !== "done") {
      actions.append(action("Выполнено", async () => {
        const result = await api(`/api/tasks/${task.task_id}`, {method: "PATCH", body: JSON.stringify({status: "done"})});
        if (result.next_task) notify("Следующая повторяющаяся задача создана.");
        await renderTasks();
      }));
      actions.append(action("Изменить", async () => editTask(task)));
      if (!depth) actions.append(action("+ Подзадача", async () => createSubtask(task)));
    } else {
      actions.append(action("Вернуть", async () => {
        await api(`/api/tasks/${task.task_id}`, {method: "PATCH", body: JSON.stringify({status: "open"})});
        await renderTasks();
      }));
    }
    actions.append(action("Удалить", async () => deleteTask(task, card), "danger"));

    const openEditor = event => {
      if (event?.target?.closest?.("button, input, select, textarea, a")) return;
      editTask(task).catch(error => notify(error?.message || String(error)));
    };
    card.addEventListener("click", openEditor);
    card.addEventListener("keydown", event => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      openEditor(event);
    });

    card.append(title, description, meta, actions);
    return card;
  }

  function skippedSummary(skipped) {
    const counts = {};
    for (const item of skipped || []) {
      const reason = String(item?.reason || "unknown");
      counts[reason] = (counts[reason] || 0) + 1;
    }
    const parts = [];
    if (counts.missing_estimate) parts.push(`без длительности: ${counts.missing_estimate}`);
    if (counts.missing_deadline) parts.push(`без срока: ${counts.missing_deadline}`);
    if (counts.overdue) parts.push(`просрочено: ${counts.overdue}`);
    if (counts.no_slot_before_deadline) parts.push(`нет свободного окна до срока: ${counts.no_slot_before_deadline}`);
    if (counts.fixed) parts.push(`фиксированные: ${counts.fixed}`);
    if (counts.already_scheduled) parts.push(`уже в календаре: ${counts.already_scheduled}`);
    if (!parts.length) return "Не нашёл задач, которые можно безопасно поставить в календарь.";
    const needsData = Boolean(counts.missing_estimate || counts.missing_deadline);
    const hint = needsData ? " Откройте задачу касанием и добавьте срок и длительность." : "";
    return `Не удалось распланировать: ${parts.join(" · ")}.${hint}`;
  }

  async function planTasks() {
    const preview = await api("/api/tasks/schedule/preview");
    const proposals = preview.proposals || [];
    if (!proposals.length) {
      notify(skippedSummary(preview.skipped));
      return;
    }
    const approved = await editor().confirmPlan(proposals);
    if (!approved) return;
    const result = await api("/api/tasks/schedule/apply", {method: "POST", body: JSON.stringify({proposals})});
    await renderTasks();
    if (result.errors?.length) notify(`В календарь добавлено: ${result.applied_count}. Часть окон успела измениться — обновите план.`);
    else notify(`Готово. В календарь добавлено задач: ${result.applied_count}.`);
  }

  function appendTaskTree(list, tasks) {
    const byParent = new Map();
    const ids = new Set(tasks.map(task => Number(task.task_id)));
    for (const task of tasks) {
      const parent = Number(task.parent_task_id || 0);
      if (!byParent.has(parent)) byParent.set(parent, []);
      byParent.get(parent).push(task);
    }
    const visited = new Set();
    const renderBranch = (task, depth) => {
      const id = Number(task.task_id);
      if (visited.has(id)) return;
      visited.add(id);
      list.append(taskCard(task, depth));
      for (const child of byParent.get(id) || []) renderBranch(child, depth + 1);
    };
    const roots = tasks.filter(task => !task.parent_task_id || !ids.has(Number(task.parent_task_id)));
    for (const root of roots) renderBranch(root, 0);
    for (const task of tasks) renderBranch(task, task.parent_task_id ? 1 : 0);
  }

  function filterRow() {
    const filters = document.createElement("div");
    filters.className = "planner-task-filters";
    const search = document.createElement("input");
    search.type = "search";
    search.placeholder = "Поиск по задачам";
    search.value = taskQuery;
    search.className = "planner-task-filter";
    search.oninput = () => {
      taskQuery = search.value.trim();
      clearTimeout(searchTimer);
      searchTimer = setTimeout(renderTasks, 350);
    };
    const category = document.createElement("select");
    category.className = "planner-task-filter";
    category.innerHTML = '<option value="">Все категории</option>' + Object.entries(categoryLabels).map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
    category.value = taskCategory;
    category.onchange = () => { taskCategory = category.value; renderTasks(); };
    const priority = document.createElement("select");
    priority.className = "planner-task-filter";
    priority.innerHTML = '<option value="">Все приоритеты</option>' + Object.entries(priorityLabels).map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
    priority.value = taskPriority;
    priority.onchange = () => { taskPriority = priority.value; renderTasks(); };
    filters.append(search, category, priority);
    return filters;
  }

  function emptyState(filtered) {
    const empty = document.createElement("div");
    empty.className = "library-empty planner-task-empty";
    if (filtered) {
      empty.innerHTML = '<strong>Ничего не найдено</strong><span>Измените поиск или сбросьте фильтры.</span>';
    } else {
      empty.innerHTML = '<strong>Задач пока нет</strong><span>Добавьте первую или скажите секретарю: «напомни завтра…».</span>';
    }
    return empty;
  }

  async function renderTasks() {
    if (!taskMode) return;
    const list = document.getElementById("libraryList");
    if (!list) return;
    list.innerHTML = '<div class="library-loading">Загружаю задачи…</div>';
    try {
      const params = new URLSearchParams({status: "all"});
      if (taskQuery) params.set("q", taskQuery);
      if (taskCategory) params.set("category", taskCategory);
      if (taskPriority) params.set("priority", taskPriority);
      const data = await api(`/api/tasks?${params.toString()}`);
      const tasks = data.tasks || [];
      list.replaceChildren();

      const toolbar = document.createElement("div");
      toolbar.className = "planner-task-toolbar";
      const create = document.createElement("button");
      create.type = "button";
      create.className = "planner-task-primary";
      create.textContent = "+ Задача";
      create.onclick = () => createTask().catch(error => notify(error?.message));
      const plan = document.createElement("button");
      plan.type = "button";
      plan.className = "planner-task-primary secondary";
      plan.textContent = "Распланировать";
      plan.onclick = () => planTasks().catch(error => notify(error?.message));
      const summary = document.createElement("div");
      summary.className = "planner-task-summary";
      summary.textContent = `Открыто ${data.summary?.open || 0} · просрочено ${data.summary?.overdue || 0}`;
      toolbar.append(create, plan, summary);
      list.append(toolbar, filterRow());

      if (!tasks.length) {
        list.append(emptyState(Boolean(taskQuery || taskCategory || taskPriority)));
        document.dispatchEvent(new Event("planner-task-rendered"));
        return;
      }
      appendTaskTree(list, tasks);
      document.dispatchEvent(new Event("planner-task-rendered"));
    } catch (error) {
      const friendly = window.PlannerPolish?.friendlyError?.(error) || "Не удалось загрузить задачи. Проверьте соединение и попробуйте ещё раз.";
      list.innerHTML = `<div class="library-error">${friendly}</div>`;
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
    tabs.insertBefore(button, notes);

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
      .planner-task-toolbar{display:grid;grid-template-columns:auto auto 1fr;gap:8px;align-items:center;margin:0 0 10px}
      .planner-task-primary{min-height:40px;padding:0 12px;border-radius:12px;background:#2d2d2c;color:#fff;font-weight:650;cursor:pointer}.planner-task-primary.secondary{background:#ececea;color:#222}
      .planner-task-summary{text-align:right;color:#888883;font-size:12px}.planner-task-feedback{margin:0 0 10px;padding:10px 12px;border:1px solid #dddcd8;border-radius:12px;background:#f0f0ed;color:#353533;font-size:12px;line-height:1.4}
      .planner-task-filters{display:grid;grid-template-columns:minmax(0,1.4fr) 1fr 1fr;gap:7px;margin:0 0 12px}.planner-task-filter{min-width:0;min-height:38px;padding:0 9px;border:1px solid #dededb;border-radius:11px;background:#fff;color:#333;font:inherit;font-size:12px;outline:0}
      .planner-task-card{padding:15px 16px;margin:0 0 10px;border:1px solid #e5e5e2;border-radius:18px;background:#fff;box-shadow:0 3px 14px rgba(0,0,0,.035)}.planner-task-card.subtask{margin-left:calc(min(var(--task-depth),3) * 18px);padding:12px 14px;border-radius:15px;background:#fafaf8}
      .planner-task-card{cursor:pointer}.planner-task-card:focus-visible{outline:2px solid #7b7b76;outline-offset:2px}.planner-task-card.completed .planner-task-title{text-decoration:line-through;color:#777773}.planner-task-title{font-size:15px;font-weight:650;line-height:1.35;overflow-wrap:anywhere}.planner-task-description{margin-top:7px;color:#5f5f5b;font-size:13px;line-height:1.4;white-space:pre-line;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.planner-task-description[hidden]{display:none}.planner-task-meta{margin-top:7px;color:#8d8d88;font-size:12px;line-height:1.35}
      .planner-task-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:11px}.planner-task-action{min-height:34px;padding:0 10px;border-radius:10px;background:#efefed;color:#333;font-size:12px;font-weight:600;cursor:pointer}.planner-task-action.danger{background:#f2dddd;color:#8a2d2d}
      .planner-task-empty{display:grid;gap:7px}.planner-task-empty strong{color:#555550;font-size:16px}.planner-task-empty span{font-size:13px}
      @media(max-width:520px){.planner-task-toolbar{grid-template-columns:1fr 1fr}.planner-task-summary{grid-column:1/-1;text-align:left}.planner-task-filters{grid-template-columns:1fr 1fr}.planner-task-filter:first-child{grid-column:1/-1}}
    `;
    document.head.append(style);
  }

  document.addEventListener("planner-ready", install);
  document.addEventListener("planner-library-changed", () => { if (taskMode) renderTasks(); });
  if (document.readyState !== "loading") setTimeout(install, 0);
})();
