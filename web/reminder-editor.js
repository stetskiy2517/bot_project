(() => {
  "use strict";

  const app = document.getElementById("app");
  const list = document.getElementById("libraryList");
  if (!app || !list || window.__plannerReminderEditor) return;
  window.__plannerReminderEditor = true;

  const labels = {
    work: "Работа", health: "Здоровье", rest: "Отдых", travel: "Поездки",
    family: "Семья", personal: "Личное", other: "Прочее",
  };
  const weekdays = [
    ["weekly:0", "Каждый понедельник"], ["weekly:1", "Каждый вторник"],
    ["weekly:2", "Каждую среду"], ["weekly:3", "Каждый четверг"],
    ["weekly:4", "Каждую пятницу"], ["weekly:5", "Каждую субботу"],
    ["weekly:6", "Каждое воскресенье"],
  ];

  let details = new Map();
  let refreshTimer = null;
  let loading = false;

  const style = document.createElement("style");
  style.id = "reminderEditorStyles";
  style.textContent = `
    .reminder-action.edit{background:#e8e8e5;color:#31312f}.reminder-category-chip{display:inline-flex;align-items:center;min-height:20px;padding:2px 7px;border-radius:999px;background:#efefec;color:#666662;font-size:10px;line-height:1.2;white-space:nowrap}
    .reminder-edit-backdrop{position:absolute;z-index:180;inset:0;display:flex;align-items:flex-end;background:rgba(0,0,0,.18);opacity:0;visibility:hidden;pointer-events:none;transition:opacity .18s ease,visibility 0s linear .18s}.reminder-edit-backdrop.open{opacity:1;visibility:visible;pointer-events:auto;transition-delay:0s}
    .reminder-edit-sheet{width:100%;max-height:88%;overflow:auto;padding:16px 16px calc(env(safe-area-inset-bottom) + 18px);border-radius:24px 24px 0 0;background:#fff;box-shadow:0 -12px 40px rgba(0,0,0,.12);transform:translateY(18px);transition:transform .18s ease}.reminder-edit-backdrop.open .reminder-edit-sheet{transform:translateY(0)}
    .reminder-edit-handle{width:38px;height:4px;border-radius:99px;background:#d2d2cf;margin:0 auto 14px}.task-notification-edit-title{margin:0 0 16px;font-size:18px;font-weight:650}
    .reminder-edit-field{display:grid;gap:7px;margin:0 0 13px;color:#696965;font-size:12px}.reminder-edit-field input,.reminder-edit-field select{width:100%;min-width:0;min-height:44px;padding:10px 12px;border:1px solid #dededb;border-radius:13px;background:#fff;color:#151515;font:inherit;font-size:15px;box-sizing:border-box}
    .reminder-edit-help{margin:-4px 0 14px;color:#92928e;font-size:11px;line-height:1.35}.reminder-edit-actions{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:17px}.reminder-edit-button{min-height:44px;border-radius:13px;font-weight:650;cursor:pointer}.reminder-edit-button.cancel{background:#eeeeeb;color:#30302e}.reminder-edit-button.save{background:#171717;color:#fff}.reminder-edit-button:disabled{opacity:.55}.reminder-edit-error{min-height:18px;margin-top:10px;color:#9a3e3e;font-size:12px;text-align:center}
    @media(min-width:760px){.reminder-edit-sheet{max-width:500px;margin:0 auto 18px;border-radius:22px}}
  `;
  document.head.appendChild(style);

  const backdrop = document.createElement("div");
  backdrop.id = "reminderEditBackdrop";
  backdrop.className = "reminder-edit-backdrop";
  app.appendChild(backdrop);

  function request(path, options = {}) {
    if (window.PlannerRequests?.request) return window.PlannerRequests.request(path, options);
    return fetch(path, {credentials: "same-origin", cache: "no-store", ...options}).then(async response => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.message || data.error || `HTTP ${response.status}`);
      return data;
    });
  }

  function localDateTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "";
    const pad = value => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function closeEditor() {
    backdrop.classList.remove("open");
    setTimeout(() => { if (!backdrop.classList.contains("open")) backdrop.replaceChildren(); }, 190);
  }

  function categoryOptions(item) {
    const autoLabel = labels[item.category] || labels.other;
    return [`<option value="auto">Авто · ${autoLabel}</option>`, ...Object.entries(labels).map(([value, label]) => `<option value="${value}">${label}</option>`)].join("");
  }

  function repeatOptions() {
    return [
      ["none", "Не повторять"], ["daily", "Каждый день"], ["weekdays", "По будням"],
      ["weekends", "По выходным"], ["weekly", "Каждую неделю"], ...weekdays,
    ].map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
  }

  async function openEditor(reminderId, {focusTime = false} = {}) {
    let item = details.get(Number(reminderId));
    if (!item) {
      const payload = await request(`/api/mobile/reminders/${Number(reminderId)}/details`);
      item = payload.reminder;
      details.set(Number(item.id), item);
    }

    backdrop.innerHTML = `
      <section class="reminder-edit-sheet" role="dialog" aria-modal="true" aria-label="Изменить задачу с уведомлением">
        <div class="reminder-edit-handle"></div>
        <h2 class="task-notification-edit-title">Задача с уведомлением</h2>
        <label class="reminder-edit-field">Текст<input id="reminderEditText" maxlength="500" autocomplete="off" /></label>
        <label class="reminder-edit-field">Когда напомнить<input id="reminderEditAt" type="datetime-local" /></label>
        <label class="reminder-edit-field">Категория<select id="reminderEditCategory">${categoryOptions(item)}</select></label>
        <div class="reminder-edit-help">«Авто» использует ту же категоризацию, что календарь и Колесо жизни.</div>
        <label class="reminder-edit-field">Повтор<select id="reminderEditRepeat">${repeatOptions()}</select></label>
        <div class="reminder-edit-help">Дата, время, повтор и категория меняются в одном месте.</div>
        <div class="reminder-edit-actions"><button class="reminder-edit-button cancel" type="button" data-reminder-edit-cancel>Отмена</button><button class="reminder-edit-button save" type="button" data-reminder-edit-save="${Number(item.id)}">Сохранить</button></div>
        <div id="reminderEditError" class="reminder-edit-error" aria-live="polite"></div>
      </section>`;

    const textInput = backdrop.querySelector("#reminderEditText");
    const atInput = backdrop.querySelector("#reminderEditAt");
    const category = backdrop.querySelector("#reminderEditCategory");
    const repeat = backdrop.querySelector("#reminderEditRepeat");
    textInput.value = item.text || "";
    atInput.value = localDateTime(item.remind_at);
    category.value = item.category_source === "manual" ? item.category : "auto";
    repeat.value = item.repeat_rule || "none";
    if (!repeat.value) repeat.value = "none";
    backdrop.classList.add("open");
    requestAnimationFrame(() => (focusTime ? atInput : textInput).focus());
  }

  async function saveEditor(reminderId) {
    const save = backdrop.querySelector("[data-reminder-edit-save]");
    const error = backdrop.querySelector("#reminderEditError");
    const text = backdrop.querySelector("#reminderEditText")?.value.trim() || "";
    const atRaw = backdrop.querySelector("#reminderEditAt")?.value.trim() || "";
    const category = backdrop.querySelector("#reminderEditCategory")?.value || "auto";
    const repeatRule = backdrop.querySelector("#reminderEditRepeat")?.value || "none";
    if (!text) { error.textContent = "Нужен текст задачи."; return; }
    if (!atRaw) { error.textContent = "Выберите дату и время уведомления."; return; }
    const at = new Date(atRaw);
    if (!Number.isFinite(at.getTime())) { error.textContent = "Проверьте дату и время."; return; }
    if (at.getTime() <= Date.now()) { error.textContent = "Время уведомления должно быть в будущем."; return; }

    save.disabled = true;
    error.textContent = "";
    try {
      const payload = await request(`/api/mobile/reminders/${Number(reminderId)}/details`, {
        method: "PATCH",
        body: JSON.stringify({text, remind_at: at.toISOString(), category, repeat_rule: repeatRule}),
      });
      details.set(Number(reminderId), payload.reminder);
      closeEditor();
      document.dispatchEvent(new Event("planner-library-changed"));
      scheduleRefresh();
    } catch (requestError) {
      error.textContent = window.PlannerPolish?.friendlyError?.(requestError) || requestError?.message || "Не удалось сохранить задачу.";
      save.disabled = false;
    }
  }

  function decorateRows() {
    for (const row of list.querySelectorAll(".reminder-swipe-row[data-id]")) {
      const id = Number(row.dataset.id || 0);
      if (!id) continue;
      const item = details.get(id);
      const manageSide = row.querySelector(".reminder-actions.manage-side");
      if (manageSide && !manageSide.querySelector("[data-reminder-edit]")) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "reminder-action edit";
        button.dataset.reminderEdit = String(id);
        button.textContent = "Изменить";
        manageSide.appendChild(button);
        row.dataset.leftWidth = String(Number(row.dataset.leftWidth || 176) + 88);
      }
      const meta = row.querySelector(".reminder-card-meta");
      if (meta && item) {
        let chip = meta.querySelector(".reminder-category-chip");
        if (!chip) { chip = document.createElement("span"); chip.className = "reminder-category-chip"; meta.appendChild(chip); }
        chip.textContent = item.category_label || labels[item.category] || labels.other;
        chip.title = item.category_source === "manual" ? "Категория выбрана вручную" : "Категория определена автоматически";
      }
    }
  }

  async function refreshDetails() {
    if (loading || !app.classList.contains("library-active")) return;
    loading = true;
    try {
      const payload = await request("/api/mobile/reminders/details");
      details = new Map((payload.items || []).map(item => [Number(item.id), item]));
      decorateRows();
    } catch (_) {
      // Base list remains usable if enrichment is temporarily unavailable.
    } finally { loading = false; }
  }

  function scheduleRefresh() { clearTimeout(refreshTimer); refreshTimer = setTimeout(refreshDetails, 50); }
  function mutationAddsReminderRow(mutation) {
    return Array.from(mutation.addedNodes || []).some(node => node.nodeType === 1 && (node.matches?.(".reminder-swipe-row") || node.querySelector?.(".reminder-swipe-row")));
  }

  new MutationObserver(mutations => {
    if (!mutations.some(mutationAddsReminderRow)) return;
    decorateRows();
    scheduleRefresh();
  }).observe(list, {childList: true, subtree: true});

  new MutationObserver(() => { if (app.classList.contains("library-active")) scheduleRefresh(); })
    .observe(app, {attributes: true, attributeFilter: ["class"]});

  list.addEventListener("click", event => {
    const edit = event.target.closest("[data-reminder-edit]");
    if (!edit) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    openEditor(Number(edit.dataset.reminderEdit)).catch(() => {});
  }, true);

  backdrop.addEventListener("click", event => {
    if (event.target === backdrop || event.target.closest("[data-reminder-edit-cancel]")) { closeEditor(); return; }
    const save = event.target.closest("[data-reminder-edit-save]");
    if (save) saveEditor(Number(save.dataset.reminderEditSave));
  });

  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && backdrop.classList.contains("open")) closeEditor();
  });

  window.PlannerReminderEditor = {open: openEditor, close: closeEditor, refresh: scheduleRefresh};
})();
