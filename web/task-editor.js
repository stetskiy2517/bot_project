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
    .task-editor-backdrop{position:absolute;z-index:210;inset:0;background:#fff;opacity:0;visibility:hidden;pointer-events:none;transform:translateX(24px);transition:opacity .18s ease,transform .2s cubic-bezier(.22,.8,.24,1),visibility 0s linear .2s;overflow:hidden}
    .task-editor-backdrop.open{opacity:1;visibility:visible;pointer-events:auto;transform:translateX(0);transition-delay:0s}
    .task-editor-backdrop.compact{display:flex;align-items:flex-end;background:rgba(0,0,0,.18);transform:none}
    .task-editor-sheet{width:100%;height:100%;max-height:none;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;padding:0 16px calc(env(safe-area-inset-bottom) + 20px);border-radius:0;background:#fff;box-shadow:none;box-sizing:border-box}
    .task-editor-handle{width:38px;height:4px;border-radius:99px;background:#d2d2cf;margin:0 auto 14px}
    .task-editor-head{position:sticky;z-index:3;top:0;display:grid;grid-template-columns:44px 1fr 44px;align-items:center;gap:8px;margin:0 -16px 18px;padding:calc(env(safe-area-inset-top) + 8px) 12px 10px;background:rgba(255,255,255,.96);backdrop-filter:blur(18px);border-bottom:1px solid #eeeeeb}
    .task-editor-title{margin:0;text-align:center;font-size:19px;line-height:1.2;font-weight:700}
    .task-editor-close{display:inline-flex;width:44px;height:44px;align-items:center;justify-content:center;border-radius:50%;background:#efefec;color:#30302e;font-size:20px;cursor:pointer}
    .task-editor-head-spacer{width:44px;height:44px}
    .task-editor-grid{display:grid;gap:16px;max-width:680px;margin:0 auto}
    .task-editor-section{display:grid;gap:11px;padding:14px;border:1px solid #e8e8e5;border-radius:18px;background:#fff}
    .task-editor-section-title{font-size:12px;font-weight:700;color:#777772;text-transform:uppercase;letter-spacing:.035em}
    .task-editor-field{display:grid;gap:6px;min-width:0;max-width:100%;color:#6f6f6a;font-size:12px}
    .task-editor-field input,.task-editor-field select,.task-editor-field textarea{display:block;width:100%;min-width:0;max-width:100%;min-height:46px;padding:11px 12px;border:1px solid #dededb;border-radius:13px;background:#fff;color:#171717;font:inherit;font-size:15px;box-sizing:border-box;outline:0}
    .task-editor-field input[type="date"],.task-editor-field input[type="time"]{inline-size:100%;min-inline-size:0;max-inline-size:100%;overflow:hidden}
    .task-editor-field textarea{min-height:118px;resize:vertical;line-height:1.45}
    .task-editor-field input:focus,.task-editor-field select:focus,.task-editor-field textarea:focus{border-color:#aaa9a5;box-shadow:0 0 0 3px rgba(0,0,0,.04)}
    .task-editor-two{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:9px;min-width:0}
    .task-editor-three{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,.8fr) minmax(0,.8fr);gap:9px;min-width:0}
    .task-editor-two>*,.task-editor-three>*{min-width:0;max-width:100%}
    .task-editor-check{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:46px;padding:2px;color:#333;font-size:14px}
    .task-editor-check input{width:22px;height:22px}
    .task-editor-help{color:#969691;font-size:11px;line-height:1.4}
    .task-editor-scheduled{padding:10px 12px;border-radius:12px;background:#f1f1ee;color:#62625e;font-size:12px;line-height:1.4}
    .task-editor-error{min-height:18px;color:#9b3c3c;font-size:12px;line-height:1.35;text-align:center}
    .task-editor-actions{position:sticky;bottom:0;display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:4px -16px -20px;padding:12px 16px calc(env(safe-area-inset-bottom) + 14px);background:linear-gradient(to bottom,rgba(255,255,255,.84),#fff 24%);backdrop-filter:blur(18px)}
    .task-editor-actions.single{grid-template-columns:1fr}
    .task-editor-button{min-height:48px;padding:10px 13px;border-radius:14px;font-size:14px;font-weight:650;cursor:pointer}
    .task-editor-button.primary{background:#171717;color:#fff}.task-editor-button.secondary{background:#ededeb;color:#2e2e2b}.task-editor-button.danger{background:#f3dfdf;color:#8a2f2f}.task-editor-button:disabled{opacity:.52}
    .task-plan-list{display:grid;gap:1px;margin:4px 0 14px;border:1px solid #e8e8e5;border-radius:15px;overflow:hidden;background:#e8e8e5}
    .task-plan-row{display:grid;gap:4px;padding:11px 13px;background:#fff}.task-plan-name{font-size:14px;font-weight:650}.task-plan-meta{font-size:12px;color:#888883}
    .task-editor-copy{margin:0 0 14px;color:#555550;font-size:14px;line-height:1.45}
    .task-confirm-sheet{height:auto;min-height:0;max-height:70%;position:relative;padding:16px 16px calc(env(safe-area-inset-bottom) + 18px);border-radius:24px 24px 0 0;box-shadow:0 -12px 40px rgba(0,0,0,.12)}
    .task-confirm-sheet .task-editor-head{position:static;display:flex;justify-content:space-between;margin:0 0 14px;padding:0;background:transparent;border:0;backdrop-filter:none}.task-confirm-sheet .task-editor-title{text-align:left}
    .task-confirm-sheet .task-editor-actions{position:static;margin:14px 0 0;padding:0;background:transparent;backdrop-filter:none}
    .planner-snackbar{position:absolute;z-index:260;left:50%;bottom:calc(env(safe-area-inset-bottom) + 86px);width:min(430px,calc(100% - 24px));display:flex;align-items:center;gap:10px;padding:11px 12px 11px 14px;border-radius:15px;background:rgba(27,27,27,.96);color:#fff;box-shadow:0 12px 36px rgba(0,0,0,.18);transform:translate(-50%,16px);opacity:0;pointer-events:none;transition:opacity .18s ease,transform .18s ease}
    .planner-snackbar.show{opacity:1;transform:translate(-50%,0);pointer-events:auto}.planner-snackbar-text{min-width:0;flex:1;font-size:13px;line-height:1.35}.planner-snackbar-action{flex:0 0 auto;padding:8px 10px;border-radius:10px;background:#3b3b3b;color:#fff;font-weight:700;cursor:pointer}
    @media(min-width:760px){.task-editor-sheet{padding-left:24px;padding-right:24px}.task-editor-head{margin-left:-24px;margin-right:-24px;padding-left:20px;padding-right:20px}.planner-snackbar{bottom:28px}}
    @media(max-width:520px){.task-editor-two,.task-editor-three{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.task-editor-three .task-editor-field:first-child{grid-column:1/-1}}
    @media(max-width:360px){.task-editor-two,.task-editor-three{grid-template-columns:minmax(0,1fr)}.task-editor-three .task-editor-field:first-child{grid-column:auto}}
    @media(prefers-reduced-motion:reduce){.task-editor-backdrop{transition:none!important}}
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

  function localParts(value) {
    if (!value) return {date: "", time: ""};
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return {date: "", time: ""};
    const pad = number => String(number).padStart(2, "0");
    return {
      date: `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}`,
      time: `${pad(date.getHours())}:${pad(date.getMinutes())}`,
    };
  }

  function dueIso(dateRaw, timeRaw) {
    if (!dateRaw && !timeRaw) return null;
    if (!dateRaw) throw new Error("Выберите дату срока.");
    const time = timeRaw || "23:59";
    const parsed = new Date(`${dateRaw}T${time}`);
    if (!Number.isFinite(parsed.getTime())) throw new Error("Проверьте дату и время срока.");
    return parsed.toISOString();
  }

  function close(result = null) {
    backdrop.classList.remove("open");
    const resolve = activeResolve;
    activeResolve = null;
    setTimeout(() => {
      if (!backdrop.classList.contains("open")) {
        backdrop.classList.remove("compact");
        backdrop.replaceChildren();
      }
    }, 180);
    if (resolve) resolve(result);
  }

  function show(html, label, resolve, {compact = false} = {}) {
    if (activeResolve) close(null);
    activeResolve = resolve || null;
    backdrop.classList.toggle("compact", compact);
    const handle = compact ? '<div class="task-editor-handle"></div>' : "";
    backdrop.innerHTML = `<section class="task-editor-sheet${compact ? " task-confirm-sheet" : ""}" role="dialog" aria-modal="true" aria-label="${esc(label)}">${handle}${html}</section>`;
    backdrop.classList.add("open");
  }

  function openTask({task = null, parent = null} = {}) {
    const editing = Boolean(task);
    const subtask = Boolean(parent);
    const draft = task || {};
    const title = editing ? "Задача" : subtask ? "Новая подзадача" : "Новая задача";
    const estimate = draft.estimate_minutes ?? (subtask ? "" : 60);
    const due = localParts(draft.due_at);
    const category = draft.category || parent?.category || "other";
    const priority = draft.priority || parent?.priority || "normal";
    const repeat = draft.repeat_rule || "none";
    const flexible = editing ? Boolean(draft.flexible) : !subtask;
    const status = draft.status || "open";

    return new Promise(resolve => {
      show(`
        <div class="task-editor-head">
          <button class="task-editor-close" type="button" data-task-editor-cancel aria-label="Назад">←</button>
          <h2 class="task-editor-title">${title}</h2>
          <span class="task-editor-head-spacer" aria-hidden="true"></span>
        </div>
        <div class="task-editor-grid">
          <section class="task-editor-section">
            <div class="task-editor-section-title">Содержание</div>
            <label class="task-editor-field">Название
              <input id="taskEditTitle" maxlength="300" autocomplete="off" value="${esc(draft.title || "")}" placeholder="Что нужно сделать?" />
            </label>
            <label class="task-editor-field">Описание
              <textarea id="taskEditDescription" maxlength="4000" placeholder="Детали, ссылки, комментарии…">${esc(draft.description || "")}</textarea>
            </label>
          </section>

          <section class="task-editor-section">
            <div class="task-editor-section-title">Срок и время</div>
            <div class="task-editor-three">
              <label class="task-editor-field">Дата срока
                <input id="taskEditDueDate" type="date" value="${esc(due.date)}" />
              </label>
              <label class="task-editor-field">Время
                <input id="taskEditDueTime" type="time" value="${esc(due.time)}" />
              </label>
              <label class="task-editor-field">Длительность, мин
                <input id="taskEditEstimate" type="number" min="5" max="720" step="5" inputmode="numeric" value="${esc(estimate)}" placeholder="30" />
              </label>
            </div>
            <div class="task-editor-help">Если время срока не указано, задача считается актуальной до 23:59 выбранной даты.</div>
            ${draft.scheduled_start ? `<div class="task-editor-scheduled">В календаре уже выделено время: ${esc(new Date(draft.scheduled_start).toLocaleString("ru-RU", {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}))}</div>` : ""}
          </section>

          <section class="task-editor-section">
            <div class="task-editor-section-title">Параметры</div>
            <div class="task-editor-two">
              <label class="task-editor-field">Категория
                <select id="taskEditCategory">${options(categories, category)}</select>
              </label>
              <label class="task-editor-field">Приоритет
                <select id="taskEditPriority">${options(priorities, priority)}</select>
              </label>
            </div>
            <div class="task-editor-two">
              <label class="task-editor-field">Повтор
                <select id="taskEditRepeat">${options(repeats, repeat)}</select>
              </label>
              ${editing ? `<label class="task-editor-field">Статус
                <select id="taskEditStatus">
                  <option value="open"${status === "open" ? " selected" : ""}>Открыта</option>
                  <option value="done"${status === "done" ? " selected" : ""}>Выполнена</option>
                </select>
              </label>` : '<div></div>'}
            </div>
            <label class="task-editor-check">
              <span>Можно автоматически поставить в свободное окно календаря</span>
              <input id="taskEditFlexible" type="checkbox"${flexible ? " checked" : ""}${subtask ? " disabled" : ""} />
            </label>
            <div class="task-editor-help">Автопланирование использует срок, длительность, рабочие часы и занятость календаря.</div>
          </section>

          <div id="taskEditError" class="task-editor-error" aria-live="polite"></div>
          <div class="task-editor-actions">
            <button class="task-editor-button secondary" type="button" data-task-editor-cancel>Отмена</button>
            <button class="task-editor-button primary" type="button" data-task-editor-save>Сохранить</button>
          </div>
        </div>`, title, resolve);

      const input = backdrop.querySelector("#taskEditTitle");
      if (!editing) requestAnimationFrame(() => input?.focus());
      backdrop.querySelector("[data-task-editor-save]").onclick = () => {
        const error = backdrop.querySelector("#taskEditError");
        const taskTitle = input?.value.trim() || "";
        const description = backdrop.querySelector("#taskEditDescription")?.value.trim() || "";
        const estimateRaw = backdrop.querySelector("#taskEditEstimate")?.value.trim() || "";
        const dueDateRaw = backdrop.querySelector("#taskEditDueDate")?.value.trim() || "";
        const dueTimeRaw = backdrop.querySelector("#taskEditDueTime")?.value.trim() || "";
        if (!taskTitle) { error.textContent = "Введите название задачи."; return; }
        const estimateMinutes = estimateRaw ? Number(estimateRaw) : null;
        if (estimateMinutes !== null && (!Number.isFinite(estimateMinutes) || estimateMinutes < 5 || estimateMinutes > 720)) {
          error.textContent = "Длительность — от 5 минут до 12 часов.";
          return;
        }
        let dueAtIso = null;
        try {
          dueAtIso = dueIso(dueDateRaw, dueTimeRaw);
        } catch (dueError) {
          error.textContent = dueError.message || "Проверьте срок задачи.";
          return;
        }
        const payload = {
          title: taskTitle,
          description,
          estimate_minutes: estimateMinutes,
          due_at: dueAtIso,
          category: backdrop.querySelector("#taskEditCategory")?.value || "other",
          priority: backdrop.querySelector("#taskEditPriority")?.value || "normal",
          repeat_rule: (backdrop.querySelector("#taskEditRepeat")?.value || "none") === "none" ? null : backdrop.querySelector("#taskEditRepeat").value,
          flexible: subtask ? false : Boolean(backdrop.querySelector("#taskEditFlexible")?.checked),
          ...(subtask ? {parent_task_id: Number(parent.task_id)} : {}),
        };
        if (editing) payload.status = backdrop.querySelector("#taskEditStatus")?.value || status;
        close(payload);
      };
    });
  }

  function confirmAction({title = "Подтвердить", text = "", confirmLabel = "Продолжить", danger = false} = {}) {
    return new Promise(resolve => {
      show(`
        <div class="task-editor-head"><h2 class="task-editor-title">${esc(title)}</h2><button class="task-editor-close" type="button" data-task-editor-cancel aria-label="Закрыть">×</button></div>
        <p class="task-editor-copy">${esc(text)}</p>
        <div class="task-editor-actions"><button class="task-editor-button secondary" type="button" data-task-editor-cancel>Отмена</button><button class="task-editor-button ${danger ? "danger" : "primary"}" type="button" data-task-confirm>${esc(confirmLabel)}</button></div>`, title, resolve, {compact: true});
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
        <div class="task-editor-actions"><button class="task-editor-button secondary" type="button" data-task-editor-cancel>Не сейчас</button><button class="task-editor-button primary" type="button" data-task-confirm>Добавить в календарь</button></div>`, "План задач", resolve, {compact: true});
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
