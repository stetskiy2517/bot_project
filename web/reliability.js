(() => {
  let csrf = "";
  let userId = null;
  let offset = 0;
  let statusPromise = null;
  const PREFIX = "secretary-draft:";
  const MAX_AGE = 24 * 60 * 60 * 1000;
  let inMemoryDraft = null;

  function rememberStatus(data) {
    if (!data || !data.user) return;
    csrf = data.csrf_token || csrf;
    if (Number.isFinite(data.server_time_ms)) offset = data.server_time_ms - Date.now();
    if (userId !== null && userId !== data.user.id) inMemoryDraft = null;
    userId = data.user.id;
  }

  function newId() {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return `${Math.trunc(Date.now() + offset)}-${Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("")}`;
  }

  async function status() {
    if (!statusPromise) {
      statusPromise = fetch("/api/status", {cache: "no-store", credentials: "same-origin"})
        .then(async response => {
          const data = await response.json();
          if (!response.ok) throw Object.assign(Error(data.error || "request_failed"), {status: response.status, data});
          rememberStatus(data);
          return data;
        }).finally(() => {statusPromise = null;});
    }
    return statusPromise;
  }

  async function request(path, options = {}) {
    const {requestId, ...init} = options;
    const writes = !["GET", "HEAD", "OPTIONS"].includes((init.method || "GET").toUpperCase());
    if (writes && !csrf) await status();
    if (writes && init.body === undefined) init.body = "{}";
    const headers = {...(init.headers || {})};
    if (!(init.body instanceof FormData)) headers["Content-Type"] ||= "application/json";
    if (writes) {
      headers["X-CSRF-Token"] = csrf;
      headers["X-Request-ID"] = requestId || newId();
    }
    const response = await fetch(path, {...init, headers, credentials: "same-origin", cache: "no-store"});
    const data = await response.json().catch(() => ({}));
    if (path === "/api/status" && response.ok) rememberStatus(data);
    if (!response.ok) {
      if (response.status === 401) csrf = "";
      throw Object.assign(Error(data.message || data.error || "request_failed"), {status: response.status, data});
    }
    return data;
  }

  function draft() {
    if (userId === null) return null;
    let value = inMemoryDraft;
    try {
      const stored = localStorage.getItem(PREFIX + userId);
      if (stored) value = JSON.parse(stored);
    } catch (_) { /* Storage may be unavailable in private browsing. */ }
    if (!value || typeof value.text !== "string" || value.text.length > 10000 ||
        !Number.isFinite(value.updatedAt) || Date.now() - value.updatedAt > MAX_AGE ||
        value.userId !== userId) return null;
    return value;
  }

  function saveDraft(text, id = null, pending = false, context = null) {
    if (userId === null || text.length > 10000) return;
    const value = {text, id, pending, context, userId, updatedAt: Date.now()};
    inMemoryDraft = value;
    try {localStorage.setItem(PREFIX + userId, JSON.stringify(value));} catch (_) {}
  }

  function clearDraft() {
    inMemoryDraft = null;
    try {if (userId !== null) localStorage.removeItem(PREFIX + userId);} catch (_) {}
  }

  function clearAll() {
    clearDraft();
    try {
      for (const key of Object.keys(localStorage)) if (key.startsWith(PREFIX)) localStorage.removeItem(key);
    } catch (_) {}
    userId = null;
    csrf = "";
  }
  window.PlannerRequests = {request, status, newId, draft, saveDraft, clearDraft, clearAll};
})();

(() => {
  const sheet = document.querySelector("#settingsPanel .sheet");
  if (!sheet) return;

  const style = document.createElement("style");
  style.id = "settingsGroupsPolish";
  style.textContent = `
    #settingsPanel .settings-group {
      margin-top: 12px;
    }
    #settingsPanel #calendarSettingsGroup {
      margin-top: 18px;
    }
    #settingsPanel .settings-group.assistant-section {
      padding: 0 !important;
      border: 1px solid #ececea !important;
      border-radius: 16px !important;
      overflow: hidden !important;
      background: #fff !important;
    }
    #settingsPanel .settings-group.assistant-section > summary {
      min-height: 48px !important;
      padding: 13px 14px !important;
      display: flex !important;
      align-items: center !important;
      gap: 10px !important;
      border: 0;
      cursor: pointer;
      font-weight: 400 !important;
    }
    #settingsPanel .settings-group.assistant-section[open] > summary {
      border-bottom: 1px solid #ececea !important;
    }
    #settingsPanel .settings-group.assistant-section .field {
      margin: 0 !important;
    }
    #settingsPanel .settings-group.assistant-section > .settings-help,
    #settingsPanel .settings-group.assistant-section > #assistantTemplates,
    #settingsPanel .settings-group.assistant-section > form,
    #settingsPanel .settings-group.assistant-section > .assistant-actions,
    #settingsPanel .settings-group.assistant-section > .action {
      margin-left: 14px;
      margin-right: 14px;
    }
    #settingsPanel .settings-group.assistant-section > form,
    #settingsPanel .settings-group.assistant-section > .assistant-actions {
      margin-bottom: 14px;
    }
    #settingsPanel .settings-group.assistant-section > .action {
      width: calc(100% - 28px);
    }
    #settingsPanel .settings-group.assistant-section > #assistantTemplates:empty {
      display: none;
    }
    #settingsPanel .settings-group-summary-ready .settings-group-title {
      min-width: 0;
    }
    #settingsPanel .settings-group-meta {
      max-width: 46%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      text-align: right;
    }
  `;
  document.head.appendChild(style);

  function chevron() {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "settings-group-chevron");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "m6 9 6 6 6-6");
    svg.appendChild(path);
    return svg;
  }

  function decorateSummary(details, titleText, metaText = "", metaId = "") {
    if (!details) return null;
    details.classList.add("settings-group");
    let summary = details.querySelector(":scope > summary");
    if (!summary) {
      summary = document.createElement("summary");
      details.prepend(summary);
    }
    if (!summary.classList.contains("settings-group-summary-ready")) {
      summary.replaceChildren();
      const title = document.createElement("span");
      title.className = "settings-group-title";
      title.textContent = titleText;
      const meta = document.createElement("span");
      meta.className = "settings-group-meta";
      if (metaId) meta.id = metaId;
      meta.textContent = metaText;
      summary.append(title, meta, chevron());
      summary.classList.add("settings-group-summary-ready");
      return meta;
    }
    const title = summary.querySelector(".settings-group-title");
    if (title) title.textContent = titleText;
    let meta = summary.querySelector(".settings-group-meta");
    if (!meta) {
      meta = document.createElement("span");
      meta.className = "settings-group-meta";
      summary.insertBefore(meta, summary.querySelector(".settings-group-chevron"));
    }
    if (metaId) meta.id = metaId;
    meta.textContent = metaText;
    return meta;
  }

  function calendarMeta() {
    const start = document.getElementById("workStart");
    const end = document.getElementById("workEnd");
    const meta = document.getElementById("calendarSettingsMeta");
    if (!meta) return;
    meta.textContent = start?.value && end?.value ? `${start.value}–${end.value}` : "График";
  }

  function wrapCalendar() {
    if (document.getElementById("calendarSettingsGroup")) {
      calendarMeta();
      return;
    }
    const workStart = document.getElementById("workStart");
    const grid = workStart?.closest(".grid");
    if (!grid) return;
    const heading = grid.previousElementSibling?.classList.contains("section-title")
      ? grid.previousElementSibling
      : null;
    const group = document.createElement("details");
    group.id = "calendarSettingsGroup";
    group.className = "settings-group";
    const summary = document.createElement("summary");
    group.appendChild(summary);
    const anchor = heading || grid;
    anchor.parentNode.insertBefore(group, anchor);
    group.appendChild(grid);
    if (heading) heading.remove();
    decorateSummary(group, "Календарь", "График", "calendarSettingsMeta");
    for (const id of ["workStart", "workEnd"]) {
      const input = document.getElementById(id);
      input?.addEventListener("change", calendarMeta);
      input?.addEventListener("input", calendarMeta);
    }
    calendarMeta();
  }

  function assistantGroupConfig(details) {
    if (details.querySelector("#assistantDeliveryFields")) {
      return ["Обзоры и тихие часы", "Расписание", "assistantDeliveryMeta"];
    }
    if (details.querySelector("#templateForm") || details.querySelector("#assistantTemplates")) {
      return ["Избранные команды", "Шаблоны", "assistantTemplatesMeta"];
    }
    if (details.querySelector("#undoNoteAction") || details.querySelector("#exportAccountData") || details.querySelector("#eraseAccountData")) {
      return ["Отмена и данные", "Данные аккаунта", "assistantDataMeta"];
    }
    return null;
  }

  function updateAssistantMeta() {
    const deliveryMeta = document.getElementById("assistantDeliveryMeta");
    const deliveryFields = document.getElementById("assistantDeliveryFields");
    if (deliveryMeta && deliveryFields) {
      const enabled = Array.from(deliveryFields.querySelectorAll('input[type="checkbox"]')).some(input => input.checked);
      deliveryMeta.textContent = enabled ? "Включено" : "Выключено";
    }
    const templatesMeta = document.getElementById("assistantTemplatesMeta");
    const templates = document.getElementById("assistantTemplates");
    if (templatesMeta && templates) {
      const count = templates.querySelectorAll(".assistant-template").length;
      templatesMeta.textContent = count ? `${count} команд` : "Нет команд";
    }
  }

  function decorateAssistantGroups() {
    const root = document.getElementById("assistantSettings");
    if (!root) return;
    const groups = Array.from(root.querySelectorAll("details.assistant-section"));
    groups.forEach(details => {
      const config = assistantGroupConfig(details);
      if (!config) return;
      decorateSummary(details, config[0], config[1], config[2]);
    });
    const fields = document.getElementById("assistantDeliveryFields");
    if (fields && !fields.dataset.settingsMetaBound) {
      fields.dataset.settingsMetaBound = "1";
      fields.addEventListener("change", updateAssistantMeta);
    }
    const templates = document.getElementById("assistantTemplates");
    if (templates && !templates.dataset.settingsMetaObserved) {
      templates.dataset.settingsMetaObserved = "1";
      new MutationObserver(updateAssistantMeta).observe(templates, {childList: true});
    }
    updateAssistantMeta();
  }

  function enhance() {
    wrapCalendar();
    decorateAssistantGroups();
  }

  let queued = false;
  const observer = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      enhance();
    });
  });
  observer.observe(sheet, {childList: true, subtree: true});
  document.addEventListener("planner-ready", () => requestAnimationFrame(() => {
    calendarMeta();
    updateAssistantMeta();
  }));
  enhance();
})();