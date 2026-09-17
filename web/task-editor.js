(() => {
  "use strict";

  if (window.PlannerTaskEditor) return;
  const app = document.getElementById("app");
  if (!app) return;

  const categories = {
    work: "Работа",
    health: "Здоровье",
    rest: "Отдых",
    travel: "Поездки",
    family: "Семья",
    personal: "Личное",
    other: "Прочее",
  };
  const priorities = {high: "Высокий", normal: "Обычный", low: "Низкий"};
  const repeats = {none: "Не повторять", daily: "Каждый день", weekly: "Каждую неделю", monthly: "Каждый месяц"};

  let activeResolve = null;
  let snackbarResolve = null;
  let snackbarTimer = null;

  const style = document.createElement("style");
  style.id = "taskEditorStyles";
  style.textContent = `
    .task-editor-backdrop{position:absolute;z-index:210;inset:0;display:flex;align-items:flex-end;background:rgba(0,0,0,.18);opacity:0;visibility:hidden;pointer-events:none;transition:opacity .18s ease,visibility 0s linear .18s}
    .task-editor-backdrop.open{opacity:1;visibility:visible;pointer-events:auto;transition-delay:0s}
    .task-editor-sheet{width:100%;max-height:91%;overflow:auto;padding:16px 16px calc(env(safe-area-inset-bottom) + 18px);border-radius:24px 24px 0 0;background:#fff;box-shadow:0 -12px 40px rgba(0,0,0,.12);transform:translateY(18px);transition:transform .18s ease}
    .task-editor-backdrop.open .task-editor-sheet{transform:translateY(0)}
    .task-editor-handle{width:38px;height:4px;border-radius:99px;background:#d2d2cf;margin:0 auto 14px}
    .task-editor-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}
    .task-editor-title{margin:0;font-size:20px;line-height:1.2;font-weight:700}
    .task-editor-close{width:34px;height:34px;border-radius:50%;background:#efefec;color:#444;font-size:20px;cursor:pointer}
    .task-editor-grid{display:grid;gap:12px}
    .task-editor-field{display:grid;gap:6px;color:#6f6f6a;font-size:12px}
    .task-editor-field input,.task-editor-field select{width:100%;min-width:0;min-height:44px;padding:10px 11px;border:1px solid #dededb;border-radius:13px;background:#fff;color:#171717;font:inherit;font-size:15px;box-sizing:border-box}
    .task-editor-two{display:grid;grid-template-columns:1fr 1fr;gap:9px}
    .task-editor-check{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:44px;padding:0 2px;color:#333;font-size:14px}
    .task-editor-check input{width:20px;height:20px}
    .task-editor-help{margin-top:-5px;color:#969691;font-size:11px;line-height:1.35}
    .task-editor-error{min-height:18px;color:#9b3c3c;font-size:12px;line-height:1.35}
    .task-editor-actions{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:3px}
    .task-editor-actions.single{grid-template-columns:1fr}
    .task-editor-button{min-height:46px;padding:10px 13px;border-radius:14px;font-size:14px;font-weight:650;cursor:pointer}
    .task-editor-button.primary{background:#171717;color:#fff}.task-editor-button.secondary{background:#ededeb;color:#2e2e2b}.task-editor-button.danger{background:#f3dfdf;color:#8a2f2f}.task-editor-button:disabled{opacity:.52}
    .task-plan-list{display:grid;gap:1px;margin:4px 0 14px;border:1px solid #e8e8e5;border-radius:15px;overflow:hidden;background:#e8e8e5}
    .task-plan-row{display:grid;gap:4px;padding:11px 13px;background:#fff}.task-plan-name{font-size:14px;font-weight:650}.task-plan-meta{font-size:12px;color:#888883}
    .task-editor-copy{margin:0 0 14px;color:#555550;font-size:14px;line-height:1.45}
    .planner-snackbar{position:absolute;z-index:260;left:50%;bottom:calc(env(safe-area-inset-bottom) + 86px);width:min(430px,calc(100% - 24px));display:flex;align-items:center;gap:10px;padding:11px 12px 11px 14px;border-radius:15px;background:rgba(27,27,27,.96);color:#fff;box-shadow:0 12px 36px rgba(0,0,0,.18);transform:translate(-50%,16px);opacity:0;pointer-events:none;transition:opacity .18s ease,transform .18s ease}
    .planner-snackbar.show{opacity:1;transform:translate(-50%,0);pointer-events:auto}.planner-snackbar-text{min-width:0;flex:1;font-size:13px;line-height:1.35}.planner-snackbar-action{flex:0 0 auto;padding:8px 10px;border-radius:10px;background:#3b3b3b;color:#fff;font-weight:700;cursor:pointer}
    @media(min-width:760px){.task-editor-sheet{max-width:520px;margin:0 auto 18px;border-radius:22px}.planner-snackbar{bottom:28px}}
    @media(max-width:420px){.task-editor-two{grid-template-columns:1fr}}
  `;
  document.head.appendChild(style);

  const backdrop = document.createElement("div");
  backdrop.id = "taskEditorBackdrop";
  backdrop.className = "task-editor-backdrop";
  app.appendChild(backdrop);

  const snackbar = document.createElement("div");
  snackbar.id = "plannerSnackbar";
  snackbar.className = "planner-snackbar";
  snackbar.innerHTML = '<div class="planner-snackbar-text"></div><button class="planner-snackbar-action" type="button">Отменить</button>';
  app.appendChild(snackbar);

  const esc = value => String(value ?? "").replace(/[&<>\"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[char]);
  const options = (values, selected) => Object.entries(values).map(([value, label]) => `<option value="${value}"${value === selected ? " selected" : ""}>${label}</option>`).join("");

  function localValue(value) {
    if (!value) return "";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "";
    const pad = number => String(number).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function close(result = null) {
    backdrop.classList.remove("open");
    const resolve = activeResolve;
    activeResolve = null;
    setTimeout(() => {
      if (!backdrop.classList.contains("open")) backdrop.replaceChildren();
    }, 180);
    if (resolve) resolve(result);
  }

  function show(html, label, resolve) {
    if (activeResolve) close(null);
    activeResolve = resolve || null;
    backdrop.innerHTML = `<section class="task-editor-sheet" role="dialog" aria-modal="true" aria-label="${esc(label)}"><div class="task-editor-handle"></div>${html}</section>`;
    backdrop.classList.add("open");
  }

  function openTask({task = null, parent = null} = {}) {
    const editing = Boolean(task);
    const subtask = Boolean(parent);
    const draft = task || {};
    const title = editing ? "Изменить задачу" : subtask ? "Новая подзадача" : "Новая задача";
    const estimate = draft.estimate_minutes ?? (subtask ? "" : 60);
    const dueAt = localValue(draft.due_at);
    const category = draft.category || parent?.category || "other";
    const priority = draft.priority || parent?.priority || "normal";
    const repeat = draft.repeat_rule || "none";
    const flexible = editing ? Boolean(draft.flexible) : !subtask;

    return new Promise(resolve => {
      show(`
        <div class="task-editor-head"><h2 class="task-editor-title">${title}</h2><button class="task-editor-close" type="button" data-task-editor-cancel aria-label="Закрыть">×</button></div>
        <div class="task-editor-grid">
          <label class="task-editor-field">Название<input id="taskEditTitle" maxlength="300" autocomplete="off" value="${esc(draft.title || "")}" placeholder="Что нужно сделать?" /></label>
          <div class="task-editor-two">
            <label class="task-editor-field">Срок<input id="taskEditDue" type="datetime-local" value="${esc(dueAt)}" /></label>
            <label class="task-editor-field">Длительность, мин<input id="taskEditEstimate" type="number" min="5" step="5" inputmode="numeric" value="${esc(estimate)}" placeholder="Например, 30" /></label>
          </div>
          <div class="task-editor-two">
            <label class="task-editor-field">Категория<select id="taskEditCategory">${options(categories, category)}</select></label>
            <label class="task-editor-field">Приоритет<select id="taskEditPriority">${options(priorities, priority)}</select></label>
          </div>
          <label class="task-editor-field">Повтор<select id="taskEditRepeat">${options(repeats, repeat)}</select></label>
          <label class="task-editor-check"><span>Можно автоматически поставить в календарь</span><input id="taskEditFlexible" type="checkbox"${flexible ? " checked" : ""}${subtask ? " disabled" : ""} /></label>
          <div class="task-editor-help">Для «Распланировать» нужны срок и длительность. Задачи с уведомлением создаются командой «напомни…» и отмечаются колокольчиком.</div>
          <div id="taskEditError" class="task-editor-error" aria-live="polite"></div>
          <div class="task-editor-actions"><button class="task-editor-button secondary" type="button" data-task-editor-cancel>Отмена</button><button class="task-editor-button primary" type="button" data-task-editor-save>Сохранить</button></div>
        </div>`, title, resolve);

      const input = backdrop.querySelector("#taskEditTitle");
      requestAnimationFrame(() => input?.focus());
      backdrop.querySelector("[data-task-editor-save]").onclick = () => {
        const error = backdrop.querySelector("#taskEditError");
        const taskTitle = input?.value.trim() || "";
        const estimateRaw = backdrop.querySelector("#taskEditEstimate")?.value.trim() || "";
        const dueRaw = backdrop.querySelector("#taskEditDue")?.value.trim() || "";
        if (!taskTitle) { error.textContent = "Введите название задачи."; return; }
        const estimateMinutes = estimateRaw ? Number(estimateRaw) : null;
        if (estimateMinutes !== null && (!Number.isFinite(estimateMinutes) || estimateMinutes < 5)) {
          error.textContent = "Длительность — минимум 5 минут.";
          return;
        }
        let dueAtIso = null;
        if (dueRaw) {
          const parsed = new Date(dueRaw);
          if (!Number.isFinite(parsed.getTime())) { error.textContent = "Проверьте дату и время срока."; return; }
          dueAtIso = parsed.toISOString();
        }
        close({
          title: taskTitle,
          estimate_minutes: estimateMinutes,
          due_at: dueAtIso,
          category: backdrop.querySelector("#taskEditCategory")?.value || "other",
          priority: backdrop.querySelector("#taskEditPriority")?.value || "normal",
          repeat_rule: (backdrop.querySelector("#taskEditRepeat")?.value || "none") === "none" ? null : backdrop.querySelector("#taskEditRepeat").value,
          flexible: subtask ? false : Boolean(backdrop.querySelector("#taskEditFlexible")?.checked),
          ...(subtask ? {parent_task_id: Number(parent.task_id)} : {}),
        });
      };
    });
  }

  function confirmAction({title = "Подтвердить", text = "", confirmLabel = "Продолжить", danger = false} = {}) {
    return new Promise(resolve => {
      show(`
        <div class="task-editor-head"><h2 class="task-editor-title">${esc(title)}</h2><button class="task-editor-close" type="button" data-task-editor-cancel aria-label="Закрыть">×</button></div>
        <p class="task-editor-copy">${esc(text)}</p>
        <div class="task-editor-actions"><button class="task-editor-button secondary" type="button" data-task-editor-cancel>Отмена</button><button class="task-editor-button ${danger ? "danger" : "primary"}" type="button" data-task-confirm>${esc(confirmLabel)}</button></div>`, title, resolve);
      backdrop.querySelector("[data-task-confirm]").onclick = () => close(true);
    });
  }

  function confirmPlan(proposals = []) {
    return new Promise(resolve => {
      const rows = proposals.map(item => {
        const when = new Date(item.start);
        const human = Number.isFinite(when.getTime()) ? when.toLocaleString("ru-RU", {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}) : String(item.start || "");
        return `<div class="task-plan-row"><div class="task-plan-name">${esc(item.title || "Задача")}</div><div class="task-plan-meta">${esc(human)} · ${Number(item.estimate_minutes || 0)} мин</div></div>`;
      }).join("");
      show(`
        <div class="task-editor-head"><h2 class="task-editor-title">План задач</h2><button class="task-editor-close" type="button" data-task-editor-cancel aria-label="Закрыть">×</button></div>
        <p class="task-editor-copy">Нашёл свободные окна. Проверьте план перед добавлением в календарь.</p>
        <div class="task-plan-list">${rows}</div>
        <div class="task-editor-actions"><button class="task-editor-button secondary" type="button" data-task-editor-cancel>Не сейчас</button><button class="task-editor-button primary" type="button" data-task-confirm>Добавить в календарь</button></div>`, "План задач", resolve);
      backdrop.querySelector("[data-task-confirm]").onclick = () => close(true);
    });
  }

  function offerUndo(message, timeout = 5500) {
    if (snackbarResolve) {
      clearTimeout(snackbarTimer);
      const previous = snackbarResolve;
      snackbarResolve = null;
      previous(false);
    }
    snackbar.querySelector(".planner-snackbar-text").textContent = String(message || "Изменение выполнено");
    snackbar.classList.add("show");
    return new Promise(resolve => {
      snackbarResolve = resolve;
      const finish = undone => {
        if (snackbarResolve !== resolve) return;
        clearTimeout(snackbarTimer);
        snackbarResolve = null;
        snackbar.classList.remove("show");
        resolve(undone);
      };
      snackbar.querySelector(".planner-snackbar-action").onclick = () => finish(true);
      snackbarTimer = setTimeout(() => finish(false), timeout);
    });
  }

  backdrop.addEventListener("click", event => {
    if (event.target === backdrop || event.target.closest("[data-task-editor-cancel]")) close(null);
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && backdrop.classList.contains("open")) {
      event.preventDefault();
      close(null);
    }
  });

  window.PlannerTaskEditor = {openTask, confirmAction, confirmPlan, offerUndo, close};
})();
