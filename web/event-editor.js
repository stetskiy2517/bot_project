(() => {
  "use strict";

  const backdrop = document.getElementById("mobileSheetBackdrop");
  if (!backdrop || window.PlannerEventEditor) return;

  const defaultCategories = {
    work: "Работа",
    health: "Здоровье",
    rest: "Отдых",
    travel: "Поездки",
    family: "Семья",
    personal: "Личное",
    other: "Прочее",
  };
  let categories = {...defaultCategories};
  const recurrenceLabels = {
    none: "Не повторяется",
    daily: "Каждый день",
    weekdays: "По будням",
    weekends: "По выходным",
    weekly: "Каждую неделю",
    monthly: "Каждый месяц",
    yearly: "Каждый год",
    custom: "Свой повтор",
  };
  const reminderLabels = {
    default: "По умолчанию",
    none: "Без напоминания",
    "10": "За 10 минут",
    "15": "За 15 минут",
    "30": "За 30 минут",
    "60": "За 1 час",
    "1440": "За 1 день",
    custom: "Несколько / другое",
  };
  const scopeLabels = {
    this: "Только это",
    future: "Это и будущие",
    series: "Вся серия",
  };

  let current = null;
  let busy = false;

  const style = document.createElement("style");
  style.id = "eventEditorStyles";
  style.textContent = `
    .event-detail-sheet { max-height:92%; overflow:auto; }
    .event-detail-head { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; margin-bottom:14px; }
    .event-detail-title { margin:0; font-size:21px; line-height:1.18; font-weight:700; overflow-wrap:anywhere; }
    .event-detail-close { flex:0 0 auto; width:36px; height:36px; border-radius:50%; background:#efefec; color:#333; font-size:20px; cursor:pointer; }
    .event-detail-meta { display:grid; gap:1px; margin:0 0 14px; border:1px solid #e8e8e5; border-radius:16px; overflow:hidden; background:#e8e8e5; }
    .event-detail-row { display:grid; grid-template-columns:92px minmax(0,1fr); gap:12px; padding:11px 13px; background:#fff; align-items:start; }
    .event-detail-label { color:#8c8c87; font-size:12px; }
    .event-detail-value { color:#171717; font-size:14px; line-height:1.35; overflow-wrap:anywhere; }
    .event-detail-note { margin:0 0 14px; padding:11px 13px; border-radius:14px; background:#f1f1ee; color:#65655f; font-size:12px; line-height:1.4; }
    .event-detail-actions { display:grid; grid-template-columns:1fr 1fr; gap:9px; }
    .event-detail-actions.single { grid-template-columns:1fr; }
    .event-detail-button { min-height:46px; padding:10px 13px; border-radius:14px; font-size:14px; font-weight:650; cursor:pointer; }
    .event-detail-button.primary { background:#171717; color:#fff; }
    .event-detail-button.secondary { background:#ededeb; color:#2e2e2b; }
    .event-detail-button.danger { background:#f3dfdf; color:#8a2f2f; }
    .event-detail-button:disabled { opacity:.52; cursor:default; }
    .event-edit-grid { display:grid; gap:12px; }
    .event-edit-field { display:grid; gap:6px; color:#74746f; font-size:12px; }
    .event-edit-field input, .event-edit-field select, .event-edit-field textarea {
      width:100%; min-width:0; min-height:44px; box-sizing:border-box; padding:10px 11px;
      border:1px solid #dededb; border-radius:13px; background:#fff; color:#171717; font:inherit; font-size:15px;
    }
    .event-edit-field textarea { min-height:68px; resize:vertical; }
    .event-edit-two { display:grid; grid-template-columns:1fr 1fr; gap:9px; }
    .event-edit-check { display:flex; align-items:center; justify-content:space-between; gap:12px; min-height:44px; padding:0 2px; color:#333; font-size:14px; }
    .event-edit-check input { width:20px; height:20px; }
    .event-edit-help { margin-top:-5px; color:#969691; font-size:11px; line-height:1.35; }
    .event-edit-error { min-height:18px; color:#9b3c3c; font-size:12px; line-height:1.35; }
    .event-conflict { padding:13px; margin-bottom:12px; border-radius:15px; background:#f6eeee; }
    .event-conflict-title { font-size:14px; font-weight:650; color:#713939; }
    .event-conflict-meta { margin-top:5px; color:#7b6161; font-size:12px; line-height:1.35; }
    .event-conflict-options { display:grid; gap:8px; margin:12px 0 14px; }
    .event-conflict-option { min-height:44px; border-radius:13px; background:#eeeeeb; color:#252522; font-weight:600; cursor:pointer; }
    .event-delete-copy { margin:0 0 14px; color:#555550; font-size:14px; line-height:1.45; }
    @media (min-width:760px) { .event-detail-sheet { max-width:520px; margin:0 auto 18px; border-radius:22px; } }
  `;
  document.head.appendChild(style);

  function request(path, options = {}) {
    if (window.PlannerRequests?.request) return window.PlannerRequests.request(path, options);
    return fetch(path, {credentials: "same-origin", cache: "no-store", ...options}).then(async response => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw Object.assign(Error(data.message || data.error || `HTTP ${response.status}`), {status: response.status, data});
      return data;
    });
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function syncCategories(item) {
    const options = Array.isArray(item?.category_options) ? item.category_options : [];
    if (options.length) {
      categories = Object.fromEntries(
        options.filter(option => option?.key).map(option => [String(option.key), String(option.label || option.key)])
      );
    } else {
      categories = {...defaultCategories};
    }
  }

  function categoryOptions(selected) {
    const missing = selected && !Object.prototype.hasOwnProperty.call(categories, selected);
    const placeholder = missing ? '<option value="" selected disabled>Выбери категорию</option>' : "";
    return placeholder + Object.entries(categories)
      .map(([key, label]) => `<option value="${escapeHtml(key)}"${key === selected ? " selected" : ""}>${escapeHtml(label)}</option>`)
      .join("");
  }

  function showSheet(html) {
    backdrop.innerHTML = `<section class="mobile-sheet event-detail-sheet" role="dialog" aria-modal="true" aria-label="Событие"><div class="mobile-sheet-handle"></div>${html}</section>`;
    backdrop.classList.add("open");
  }

  function close() {
    backdrop.classList.remove("open");
    setTimeout(() => {
      if (!backdrop.classList.contains("open")) backdrop.replaceChildren();
    }, 180);
  }

  function refreshToday() {
    const refresh = document.querySelector("[data-refresh-today]");
    if (refresh) refresh.click();
    document.dispatchEvent(new Event("planner-calendar-changed"));
  }

  function formatMoment(value, item) {
    if (!value) return "—";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("ru-RU", {
      timeZone: item.timezone || undefined,
      day: "2-digit",
      month: "long",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  function formatAllDay(item) {
    const start = item.start_date || "";
    const end = item.end_date || start;
    if (!start) return "Весь день";
    const human = value => {
      const [year, month, day] = value.split("-").map(Number);
      const date = new Date(Date.UTC(year, month - 1, day, 12));
      return new Intl.DateTimeFormat("ru-RU", {day: "2-digit", month: "long", year: "numeric", timeZone: "UTC"}).format(date);
    };
    return start === end ? `${human(start)} · весь день` : `${human(start)} — ${human(end)} · весь день`;
  }

  function scopeOptions(selected = "this") {
    return Object.entries(scopeLabels).map(([value, label]) => `<option value="${value}"${value === selected ? " selected" : ""}>${label}</option>`).join("");
  }

  function detailRow(label, value) {
    if (!value) return "";
    return `<div class="event-detail-row"><div class="event-detail-label">${escapeHtml(label)}</div><div class="event-detail-value">${escapeHtml(value)}</div></div>`;
  }

  function renderDetail(item) {
    current = item;
    syncCategories(item);
    const when = item.all_day ? formatAllDay(item) : `${formatMoment(item.start, item)} — ${formatMoment(item.end, item)}`;
    const attendees = (item.attendees || []).join(", ");
    const note = item.managed ? `<div class="event-detail-note">${escapeHtml(item.managed_note || "Системное событие управляется автоматически.")}</div>` : "";
    const actions = item.editable ? `
      <div class="event-detail-actions">
        <button class="event-detail-button secondary" type="button" data-event-delete>Удалить</button>
        <button class="event-detail-button primary" type="button" data-event-edit>Изменить</button>
      </div>` : `
      <div class="event-detail-actions single"><button class="event-detail-button secondary" type="button" data-event-close>Назад</button></div>`;
    showSheet(`
      <div class="event-detail-head"><h2 class="event-detail-title">${escapeHtml(item.title)}</h2><button class="event-detail-close" type="button" data-event-close aria-label="Закрыть">×</button></div>
      <div class="event-detail-meta">
        ${detailRow("Когда", when)}
        ${detailRow("Место", item.location)}
        ${detailRow("Категория", categories[item.category] || item.category_label || "Без категории")}
        ${detailRow("Повтор", recurrenceLabels[item.recurrence] || recurrenceLabels.custom)}
        ${detailRow("Напоминание", reminderLabels[item.reminder] || reminderLabels.custom)}
        ${detailRow("Участники", attendees)}
      </div>
      ${note}${actions}`);
  }

  function initialDraft(item) {
    return {
      scope: "this",
      title: item.title || "",
      location: item.location || "",
      category: item.category || "",
      all_day: Boolean(item.all_day),
      start: String(item.start || "").slice(0, 16),
      end: String(item.end || "").slice(0, 16),
      start_date: item.start_date || String(item.start || "").slice(0, 10),
      end_date: item.end_date || item.start_date || String(item.start || "").slice(0, 10),
      recurrence: item.recurrence || "none",
      reminder: item.reminder || "default",
      attendees: (item.attendees || []).join(", "),
    };
  }

  function recurrenceOptions(value) {
    const keys = ["none", "daily", "weekdays", "weekends", "weekly", "monthly", "yearly"];
    if (value === "custom") keys.push("custom");
    return keys.map(key => `<option value="${key}"${key === value ? " selected" : ""}>${escapeHtml(recurrenceLabels[key])}</option>`).join("");
  }

  function reminderOptions(value) {
    const keys = ["default", "none", "10", "15", "30", "60", "1440"];
    if (value === "custom") keys.push("custom");
    return keys.map(key => `<option value="${key}"${key === value ? " selected" : ""}>${escapeHtml(reminderLabels[key])}</option>`).join("");
  }

  function renderEditor(item, draft = initialDraft(item), errorText = "") {
    current = item;
    syncCategories(item);
    const recurringScope = item.recurring_instance ? `
      <label class="event-edit-field">Изменить
        <select id="eventEditScope">${scopeOptions(draft.scope)}</select>
      </label>` : "";
    showSheet(`
      <div class="event-detail-head"><h2 class="event-detail-title">Изменить событие</h2><button class="event-detail-close" type="button" data-event-edit-cancel aria-label="Отмена">×</button></div>
      <div class="event-edit-grid">
        ${recurringScope}
        <label class="event-edit-field">Название<input id="eventEditTitle" maxlength="200" autocomplete="off" value="${escapeHtml(draft.title)}" /></label>
        <label class="event-edit-field">Место<input id="eventEditLocation" maxlength="500" autocomplete="off" value="${escapeHtml(draft.location)}" /></label>
        <label class="event-edit-field">Категория<select id="eventEditCategory">${categoryOptions(draft.category)}</select></label>
        <label class="event-edit-check"><span>Весь день</span><input id="eventEditAllDay" type="checkbox"${draft.all_day ? " checked" : ""} /></label>
        <div id="eventTimedFields" class="event-edit-two">
          <label class="event-edit-field">Начало<input id="eventEditStart" type="datetime-local" value="${escapeHtml(draft.start)}" /></label>
          <label class="event-edit-field">Окончание<input id="eventEditEnd" type="datetime-local" value="${escapeHtml(draft.end)}" /></label>
        </div>
        <div id="eventAllDayFields" class="event-edit-two">
          <label class="event-edit-field">Первый день<input id="eventEditStartDate" type="date" value="${escapeHtml(draft.start_date)}" /></label>
          <label class="event-edit-field">Последний день<input id="eventEditEndDate" type="date" value="${escapeHtml(draft.end_date)}" /></label>
        </div>
        <label class="event-edit-field">Повтор<select id="eventEditRecurrence">${recurrenceOptions(draft.recurrence)}</select></label>
        <div id="eventRecurrenceHelp" class="event-edit-help"></div>
        <label class="event-edit-field">Напоминание<select id="eventEditReminder">${reminderOptions(draft.reminder)}</select></label>
        <label class="event-edit-field">Участники<textarea id="eventEditAttendees" placeholder="email@example.com, второй@example.com">${escapeHtml(draft.attendees)}</textarea></label>
        <div id="eventEditError" class="event-edit-error">${escapeHtml(errorText)}</div>
        <div class="event-detail-actions">
          <button class="event-detail-button secondary" type="button" data-event-edit-cancel>Отмена</button>
          <button class="event-detail-button primary" type="button" data-event-save>Сохранить</button>
        </div>
      </div>`);
    syncEditorState(item);
  }

  function syncEditorState(item) {
    const allDay = backdrop.querySelector("#eventEditAllDay");
    const timed = backdrop.querySelector("#eventTimedFields");
    const allDayFields = backdrop.querySelector("#eventAllDayFields");
    const scope = backdrop.querySelector("#eventEditScope");
    const recurrence = backdrop.querySelector("#eventEditRecurrence");
    const help = backdrop.querySelector("#eventRecurrenceHelp");
    if (!allDay || !timed || !allDayFields || !recurrence) return;

    const update = () => {
      const seriesScope = item.recurring_instance && scope?.value === "series";
      if (seriesScope) {
        allDay.checked = Boolean(item.all_day);
        allDay.disabled = true;
      } else {
        allDay.disabled = false;
      }
      timed.hidden = allDay.checked;
      allDayFields.hidden = !allDay.checked;
      const singleInstance = item.recurring_instance && (scope?.value || "this") === "this";
      recurrence.disabled = singleInstance;
      if (help) {
        help.textContent = singleInstance
          ? "Повтор можно менять для будущих событий или всей серии."
          : (draftCustomWarning(recurrence.value));
      }
    };
    allDay.addEventListener("change", update);
    scope?.addEventListener("change", update);
    recurrence.addEventListener("change", update);
    update();
  }

  function draftCustomWarning(value) {
    if (value === "custom") return "Сложное правило повтора сохранится без изменений.";
    return "";
  }

  function collectDraft(item) {
    const value = id => backdrop.querySelector(id)?.value ?? "";
    const allDay = Boolean(backdrop.querySelector("#eventEditAllDay")?.checked);
    return {
      scope: item.recurring_instance ? value("#eventEditScope") || "this" : "this",
      title: value("#eventEditTitle").trim(),
      location: value("#eventEditLocation").trim(),
      category: value("#eventEditCategory"),
      all_day: allDay,
      start: value("#eventEditStart"),
      end: value("#eventEditEnd"),
      start_date: value("#eventEditStartDate"),
      end_date: value("#eventEditEndDate"),
      recurrence: value("#eventEditRecurrence") || item.recurrence || "none",
      reminder: value("#eventEditReminder") || item.reminder || "default",
      attendees: value("#eventEditAttendees"),
    };
  }

  function payloadFromDraft(draft, allowConflict = false) {
    const attendeeList = String(draft.attendees || "")
      .split(/[\s,;]+/)
      .map(value => value.trim())
      .filter(Boolean);
    return {
      scope: draft.scope,
      title: draft.title,
      location: draft.location,
      category: draft.category,
      all_day: Boolean(draft.all_day),
      start: draft.start,
      end: draft.end,
      start_date: draft.start_date,
      end_date: draft.end_date,
      recurrence: draft.recurrence,
      reminder: draft.reminder,
      attendees: attendeeList,
      allow_conflict: Boolean(allowConflict),
    };
  }

  function setBusy(value) {
    busy = value;
    backdrop.querySelectorAll("button, input, select, textarea").forEach(control => {
      if (control.matches("[data-event-close], [data-event-edit-cancel]")) return;
      control.disabled = value;
    });
  }

  async function submitDraft(item, draft, allowConflict = false) {
    if (busy) return;
    const error = backdrop.querySelector("#eventEditError");
    if (error) error.textContent = "";
    setBusy(true);
    try {
      await request(`/api/mobile/events/${encodeURIComponent(item.id)}`, {
        method: "PATCH",
        body: JSON.stringify(payloadFromDraft(draft, allowConflict)),
      });
      close();
      refreshToday();
    } catch (requestError) {
      if (requestError?.status === 409 && requestError?.data?.error === "calendar_conflict") {
        renderConflict(item, draft, requestError.data);
        return;
      }
      renderEditor(item, draft, requestError?.message || "Не удалось сохранить событие.");
    } finally {
      busy = false;
    }
  }

  function conflictTime(conflict, item) {
    if (!conflict?.start) return "";
    return conflict.all_day ? "весь день" : formatMoment(conflict.start, item);
  }

  function renderConflict(item, draft, data) {
    const conflict = data.conflict || {};
    const alternatives = Array.isArray(data.alternatives) ? data.alternatives : [];
    showSheet(`
      <div class="event-detail-head"><h2 class="event-detail-title">Время занято</h2><button class="event-detail-close" type="button" data-conflict-back aria-label="Назад">×</button></div>
      <div class="event-conflict">
        <div class="event-conflict-title">${escapeHtml(conflict.title || "Другое событие")}</div>
        <div class="event-conflict-meta">${escapeHtml(conflictTime(conflict, item))}</div>
      </div>
      ${alternatives.length ? `<div class="event-detail-label">Свободные варианты</div><div class="event-conflict-options">${alternatives.map((slot, index) => `<button class="event-conflict-option" type="button" data-conflict-alternative="${index}">${escapeHtml(formatMoment(slot.start, item))} — ${escapeHtml(new Intl.DateTimeFormat("ru-RU", {timeZone: item.timezone || undefined, hour: "2-digit", minute: "2-digit"}).format(new Date(slot.end)))}</button>`).join("")}</div>` : '<div class="event-detail-note">Ближайших свободных вариантов не найдено.</div>'}
      <div class="event-detail-actions">
        <button class="event-detail-button secondary" type="button" data-conflict-back>Назад</button>
        <button class="event-detail-button primary" type="button" data-conflict-force>Оставить время</button>
      </div>`);
    backdrop.querySelectorAll("[data-conflict-alternative]").forEach(button => {
      button.addEventListener("click", () => {
        const slot = alternatives[Number(button.dataset.conflictAlternative)];
        if (!slot) return;
        const next = {...draft, all_day: false, start: String(slot.start).slice(0, 16), end: String(slot.end).slice(0, 16)};
        submitDraft(item, next, false);
      });
    });
    backdrop.querySelector("[data-conflict-force]")?.addEventListener("click", () => submitDraft(item, draft, true));
    backdrop.querySelectorAll("[data-conflict-back]").forEach(button => button.addEventListener("click", () => renderEditor(item, draft)));
  }

  function renderDelete(item) {
    const scope = item.recurring_instance ? `
      <label class="event-edit-field">Удалить
        <select id="eventDeleteScope">${scopeOptions("this")}</select>
      </label>` : "";
    showSheet(`
      <div class="event-detail-head"><h2 class="event-detail-title">Удалить событие?</h2><button class="event-detail-close" type="button" data-event-delete-cancel aria-label="Отмена">×</button></div>
      <p class="event-delete-copy">«${escapeHtml(item.title)}» будет удалено из календаря.${item.recurring_instance ? " Выбери, какую часть серии удалить." : ""}</p>
      <div class="event-edit-grid">${scope}<div id="eventDeleteError" class="event-edit-error"></div>
        <div class="event-detail-actions">
          <button class="event-detail-button secondary" type="button" data-event-delete-cancel>Отмена</button>
          <button class="event-detail-button danger" type="button" data-event-delete-confirm>Удалить</button>
        </div>
      </div>`);
  }

  async function confirmDelete(item) {
    if (busy) return;
    const scope = item.recurring_instance ? (backdrop.querySelector("#eventDeleteScope")?.value || "this") : "this";
    const error = backdrop.querySelector("#eventDeleteError");
    if (error) error.textContent = "";
    setBusy(true);
    try {
      await request(`/api/mobile/events/${encodeURIComponent(item.id)}`, {
        method: "DELETE",
        body: JSON.stringify({scope}),
      });
      close();
      refreshToday();
    } catch (requestError) {
      if (error) error.textContent = requestError?.message || "Не удалось удалить событие.";
      setBusy(false);
    }
  }

  async function open(eventId) {
    if (!eventId) return;
    showSheet('<div class="mobile-loading">Загружаю событие…</div>');
    try {
      const payload = await request(`/api/mobile/events/${encodeURIComponent(eventId)}`);
      renderDetail(payload.event);
    } catch (error) {
      showSheet(`<div class="event-detail-head"><h2 class="event-detail-title">Событие</h2><button class="event-detail-close" type="button" data-event-close>×</button></div><div class="mobile-error">${escapeHtml(error?.message || "Не удалось загрузить событие.")}</div>`);
    }
  }

  backdrop.addEventListener("click", event => {
    if (event.target === backdrop) {
      close();
      return;
    }
    if (event.target.closest("[data-event-close]")) {
      close();
      return;
    }
    if (event.target.closest("[data-event-edit]") && current) {
      renderEditor(current);
      return;
    }
    if (event.target.closest("[data-event-delete]") && current) {
      renderDelete(current);
      return;
    }
    if (event.target.closest("[data-event-edit-cancel]") && current) {
      renderDetail(current);
      return;
    }
    if (event.target.closest("[data-event-delete-cancel]") && current) {
      renderDetail(current);
      return;
    }
    if (event.target.closest("[data-event-save]") && current) {
      const draft = collectDraft(current);
      submitDraft(current, draft, false);
      return;
    }
    if (event.target.closest("[data-event-delete-confirm]") && current) {
      confirmDelete(current);
    }
  });

  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && backdrop.classList.contains("open") && current) close();
  });

  window.PlannerEventEditor = {open, close};
})();