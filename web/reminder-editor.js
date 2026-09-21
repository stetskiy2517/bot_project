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
    .reminder-edit-backdrop{position:absolute;z-index:180;inset:0;background:#fff;opacity:0;visibility:hidden;pointer-events:none;transform:translateX(24px);transition:opacity .18s ease,transform .2s cubic-bezier(.22,.8,.24,1),visibility 0s linear .2s;overflow:hidden}
    .reminder-edit-backdrop.open{opacity:1;visibility:visible;pointer-events:auto;transform:translateX(0);transition-delay:0s}
    .reminder-edit-sheet{width:100%;height:100%;max-height:none;overflow:auto;overscroll-behavior:contain;padding:0 16px calc(env(safe-area-inset-bottom) + 20px);border-radius:0;background:#fff;box-shadow:none;box-sizing:border-box}
    .reminder-edit-head{position:sticky;z-index:3;top:0;display:grid;grid-template-columns:44px 1fr 44px;align-items:center;gap:8px;margin:0 -16px 18px;padding:calc(env(safe-area-inset-top) + 8px) 12px 10px;background:rgba(255,255,255,.96);backdrop-filter:blur(18px);border-bottom:1px solid #eeeeeb}
    .reminder-edit-back{display:inline-flex;width:44px;height:44px;align-items:center;justify-content:center;border-radius:50%;background:#efefec;color:#30302e;font-size:20px;cursor:pointer}
    .reminder-edit-head-spacer{width:44px;height:44px}.task-notification-edit-title{margin:0;text-align:center;font-size:19px;font-weight:700}
    .reminder-edit-content{display:grid;gap:14px;max-width:680px;margin:0 auto}
    .reminder-edit-section{display:grid;gap:11px;padding:14px;border:1px solid #e8e8e5;border-radius:18px;background:#fff}
    .reminder-edit-section-title{font-size:12px;font-weight:700;color:#777772;text-transform:uppercase;letter-spacing:.035em}
    .reminder-edit-field{display:grid;gap:7px;color:#696965;font-size:12px}.reminder-edit-field input,.reminder-edit-field select{width:100%;min-width:0;min-height:46px;padding:11px 12px;border:1px solid #dededb;border-radius:13px;background:#fff;color:#151515;font:inherit;font-size:15px;box-sizing:border-box;outline:0}
    .reminder-edit-field input:focus,.reminder-edit-field select:focus{border-color:#aaa9a5;box-shadow:0 0 0 3px rgba(0,0,0,.04)}
    .reminder-edit-two{display:grid;grid-template-columns:1fr 1fr;gap:9px}.reminder-edit-help{color:#92928e;font-size:11px;line-height:1.4}
    .reminder-edit-actions{position:sticky;bottom:0;display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:2px -16px -20px;padding:12px 16px calc(env(safe-area-inset-bottom) + 14px);background:linear-gradient(to bottom,rgba(255,255,255,.84),#fff 24%);backdrop-filter:blur(18px)}
    .reminder-edit-button{min-height:48px;border-radius:14px;font-weight:650;cursor:pointer}.reminder-edit-button.cancel{background:#eeeeeb;color:#30302e}.reminder-edit-button.save{background:#171717;color:#fff}.reminder-edit-button:disabled{opacity:.55}.reminder-edit-error{min-height:18px;color:#9a3e3e;font-size:12px;text-align:center}
    @media(min-width:760px){.reminder-edit-sheet{padding-left:24px;padding-right:24px}.reminder-edit-head{margin-left:-24px;margin-right:-24px;padding-left:20px;padding-right:20px}}
    @media(max-width:420px){.reminder-edit-two{grid-template-columns:1fr}}
    @media(prefers-reduced-motion:reduce){.reminder-edit-backdrop{transition:none!important}}
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

  function localParts(value) {
    if (!value) return {date: "", time: ""};
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return {date: "", time: ""};
    const pad = value => String(value).padStart(2, "0");
    return {
      date: `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}`,
      time: `${pad(date.getHours())}:${pad(date.getMinutes())}`,
    };
  }

  function closeEditor() {
    backdrop.classList.remove("open");
    delete backdrop.dataset.originalReminderLocal;
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

    const remind = localParts(item.scheduled_at || item.remind_at);
    const originalLocal = remind.date && remind.time ? `${remind.date}T${remind.time}` : "";
    backdrop.dataset.originalReminderLocal = originalLocal;
    backdrop.innerHTML = `
      <section class="reminder-edit-sheet" role="dialog" aria-modal="true" aria-label="Изменить задачу с уведомлением">
        <div class="reminder-edit-head">
          <button class="reminder-edit-back" type="button" data-reminder-edit-cancel aria-label="Назад">←</button>
          <h2 class="task-notification-edit-title">Задача с уведомлением</h2>
          <span class="reminder-edit-head-spacer" aria-hidden="true"></span>
        </div>
        <div class="reminder-edit-content">
          <section class="reminder-edit-section">
            <div class="reminder-edit-section-title">Содержание</div>
            <label class="reminder-edit-field">Название / текст задачи<input id="reminderEditText" maxlength="500" autocomplete="off" /></label>
          </section>
          <section class="reminder-edit-section">
            <div class="reminder-edit-section-title">Уведомление</div>
            <div class="reminder-edit-two">
              <label class="reminder-edit-field">Дата<input id="reminderEditDate" type="date" value="${remind.date}" /></label>
              <label class="reminder-edit-field">Время<input id="reminderEditTime" type="time" value="${remind.time}" /></label>
            </div>
            <label class="reminder-edit-field">Повтор<select id="reminderEditRepeat">${repeatOptions()}</select></label>
          </section>
          <section class="reminder-edit-section">
            <div class="reminder-edit-section-title">Категория</div>
            <label class="reminder-edit-field">Категория<select id="reminderEditCategory">${categoryOptions(item)}</select></label>
            <div class="reminder-edit-help">«Авто» использует ту же категоризацию, что календарь и Колесо жизни.</div>
          </section>
          <div id="reminderEditError" class="reminder-edit-error" aria-live="polite"></div>
          <div class="reminder-edit-actions"><button class="reminder-edit-button cancel" type="button" data-reminder-edit-cancel>Отмена</button><button class="reminder-edit-button save" type="button" data-reminder-edit-save="${Number(item.id)}">Сохранить</button></div>
        </div>
      </section>`;

    const textInput = backdrop.querySelector("#reminderEditText");
    const dateInput = backdrop.querySelector("#reminderEditDate");
    const timeInput = backdrop.querySelector("#reminderEditTime");
    const category = backdrop.querySelector("#reminderEditCategory");
    const repeat = backdrop.querySelector("#reminderEditRepeat");
    textInput.value = item.text || "";
    category.value = item.category_source === "manual" ? item.category : "auto";
    repeat.value = item.repeat_rule || "none";
    if (!repeat.value) repeat.value = "none";
    backdrop.classList.add("open");
    if (focusTime) requestAnimationFrame(() => timeInput.focus());
  }

  async function saveEditor(reminderId) {
    const save = backdrop.querySelector("[data-reminder-edit-save]");
    const error = backdrop.querySelector("#reminderEditError");
    const text = backdrop.querySelector("#reminderEditText")?.value.trim() || "";
    const dateRaw = backdrop.querySelector("#reminderEditDate")?.value.trim() || "";
    const timeRaw = backdrop.querySelector("#reminderEditTime")?.value.trim() || "";
    const category = backdrop.querySelector("#reminderEditCategory")?.value || "auto";
    const repeatRule = backdrop.querySelector("#reminderEditRepeat")?.value || "none";
    if (!text) { error.textContent = "Нужен текст задачи."; return; }
    if (!dateRaw || !timeRaw) { error.textContent = "Выберите дату и время уведомления."; return; }
    const at = new Date(`${dateRaw}T${timeRaw}`);
    if (!Number.isFinite(at.getTime())) { error.textContent = "Проверьте дату и время."; return; }
    const [year, month, day] = dateRaw.split("-").map(Number);
    const [hour, minute] = timeRaw.split(":").map(Number);
    if (
      at.getFullYear() !== year ||
      at.getMonth() + 1 !== month ||
      at.getDate() !== day ||
      at.getHours() !== hour ||
      at.getMinutes() !== minute
    ) {
      error.textContent = "Такого местного времени нет из-за смены часового пояса.";
      return;
    }
    const localValue = `${dateRaw}T${timeRaw}`;
    const timeChanged = localValue !== String(backdrop.dataset.originalReminderLocal || "");
    if (timeChanged && at.getTime() <= Date.now()) {
      error.textContent = "Новое время уведомления должно быть в будущем.";
      return;
    }

    save.disabled = true;
    error.textContent = "";
    try {
      const update = {text, category, repeat_rule: repeatRule};
      if (timeChanged) update.remind_at = at.toISOString();
      const payload = await request(`/api/mobile/reminders/${Number(reminderId)}/details`, {
        method: "PATCH",
        body: JSON.stringify(update),
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
