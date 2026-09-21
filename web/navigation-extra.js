(() => {
  "use strict";
  let installed = false;
  let navigationEnabled = false;
  let originBusy = false;
  let originEditing = false;
  let optimizationBusy = false;
  let activeOriginRequest = null;
  let activeOptimizationRequest = null;
  let bufferSaveTimer = null;
  let bufferSaveChain = Promise.resolve();

  function api(path, options) {
    if (typeof window.api !== "function") throw new Error("API недоступен");
    return window.api(path, options);
  }

  function hideNavigationDialogs() {
    activeOriginRequest = null;
    activeOptimizationRequest = null;
    originEditing = false;
    document.getElementById("navigationOriginQuestion")?.setAttribute("hidden", "");
    document.getElementById("navigationOptimizationQuestion")?.setAttribute("hidden", "");
  }

  function setFieldDisabled(id, disabled) {
    const element = document.getElementById(id);
    if (element) element.disabled = disabled;
  }

  function decorateMasterSwitch() {
    const control = document.getElementById("navigationEnabled");
    if (!control) return;
    const label = control.closest("label");
    const textNode = label && Array.from(label.childNodes).find(node => node.nodeType === Node.TEXT_NODE && node.textContent.trim());
    if (textNode) textNode.textContent = "Навигация ";
    const enabledOption = control.querySelector('option[value="true"]');
    const disabledOption = control.querySelector('option[value="false"]');
    if (enabledOption) enabledOption.textContent = "Включена";
    if (disabledOption) disabledOption.textContent = "Выключена";
    if (label && !document.getElementById("navigationMasterHelp")) {
      const help = document.createElement("span");
      help.id = "navigationMasterHelp";
      help.className = "settings-help";
      help.style.display = "block";
      help.style.marginTop = "6px";
      label.append(help);
    }
  }

  function applyNavigationUiState(enabled) {
    const selected = enabled === true;
    decorateMasterSwitch();
    for (const id of [
      "homeAddress",
      "officeAddress",
      "defaultPlace",
      "travelMode",
      "arrivalBuffer",
      "testNavigation",
      "parkingBufferMinutes",
      "walkingBufferMinutes",
      "openNextRoute",
    ]) setFieldDisabled(id, !selected);

    const advanced = document.getElementById("navigationAdvancedGroup");
    if (advanced) advanced.hidden = !selected;
    const meta = document.getElementById("navigationMeta");
    if (meta) meta.textContent = selected ? "включена" : "выключена";
    const help = document.getElementById("navigationMasterHelp");
    if (help) {
      help.textContent = selected
        ? "Используются места событий и, при разрешении браузера, текущая геопозиция. Дорога может добавляться в календарь автоматически."
        : "Геопозиция не запрашивается, места не используются для маршрутов и автотрансферы не создаются.";
    }
    if (!selected) {
      const status = document.getElementById("navigationStatus");
      if (status) status.textContent = "Навигация выключена. Календарь работает без геолокации и автоматических трансферов.";
    }
  }

  function setNavigationActive(enabled, {notify = true} = {}) {
    navigationEnabled = enabled === true;
    document.documentElement.dataset.navigationEnabled = String(navigationEnabled);
    if (!navigationEnabled) hideNavigationDialogs();
    if (notify) {
      document.dispatchEvent(new CustomEvent("planner-navigation-setting", {
        detail: {enabled: navigationEnabled},
      }));
    }
  }

  function syncMasterFromSavedSettings() {
    const control = document.getElementById("navigationEnabled");
    if (!control) return;
    const enabled = control.value === "true";
    applyNavigationUiState(enabled);
    setNavigationActive(enabled);
  }

  function onMasterChange(event) {
    const enabled = event.currentTarget.value === "true";
    applyNavigationUiState(enabled);
    if (!enabled) setNavigationActive(false);
  }

  async function load() {
    const state = document.getElementById("navigationExtraState");
    if (!navigationEnabled) {
      if (state) state.textContent = "Навигация выключена.";
      return;
    }
    try {
      const data = await api("/api/navigation/buffers");
      document.getElementById("parkingBufferMinutes").value = data.parking_buffer_minutes ?? 0;
      document.getElementById("walkingBufferMinutes").value = data.walking_buffer_minutes ?? 0;
      state.textContent = `Итоговый запас до события: ${data.arrival_buffer_minutes || 0} мин.`;
    } catch (error) {
      state.textContent = error.message;
    }
  }

  async function save() {
    if (!navigationEnabled) return;
    const parking = Number(document.getElementById("parkingBufferMinutes").value);
    const walking = Number(document.getElementById("walkingBufferMinutes").value);
    const data = await api("/api/navigation/buffers", {
      method: "PUT",
      body: JSON.stringify({parking_buffer_minutes: parking, walking_buffer_minutes: walking}),
    });
    document.getElementById("navigationExtraState").textContent =
      `Сохранено автоматически. Итоговый запас: ${data.preferences.arrival_buffer_minutes || 0} мин.`;
  }

  function scheduleBufferSave(delay = 350) {
    clearTimeout(bufferSaveTimer);
    bufferSaveTimer = setTimeout(() => {
      bufferSaveTimer = null;
      bufferSaveChain = bufferSaveChain
        .catch(() => {})
        .then(() => save())
        .catch((error) => {
          const state = document.getElementById("navigationExtraState");
          if (state) state.textContent = error.message;
        });
    }, delay);
  }

  async function openNextRoute() {
    if (!navigationEnabled) return;
    const popup = window.open("about:blank", "_blank");
    if (popup) {
      try { popup.opener = null; } catch (_) {}
    }
    try {
      const data = await api("/api/navigation/next-route");
      if (popup) popup.location.replace(data.url);
      else window.location.assign(data.url);
      document.getElementById("navigationExtraState").textContent = `Маршрут: ${data.title} → ${data.destination}`;
    } catch (error) {
      try { popup?.close(); } catch (_) {}
      throw error;
    }
  }

  function ensureOriginCard() {
    let card = document.getElementById("navigationOriginQuestion");
    if (card) return card;
    card = document.createElement("div");
    card.id = "navigationOriginQuestion";
    card.hidden = true;
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-live", "polite");
    card.style.cssText = [
      "position:fixed", "left:50%", "bottom:max(18px, env(safe-area-inset-bottom))",
      "transform:translateX(-50%)", "z-index:1200", "width:min(92vw,460px)",
      "max-height:calc(100dvh - 36px)", "overflow-y:auto", "box-sizing:border-box",
      "padding:16px", "border-radius:18px", "background:var(--surface,#fff)",
      "box-shadow:0 12px 40px rgba(0,0,0,.18)", "border:1px solid rgba(0,0,0,.12)"
    ].join(";");
    card.innerHTML = `
      <div id="navigationOriginTitle" style="font-weight:700;margin-bottom:6px"></div>
      <div id="navigationOriginText" style="font-size:14px;opacity:.72;margin-bottom:12px"></div>
      <div id="navigationOriginButtons" style="display:flex;gap:8px;flex-wrap:wrap">
        <button type="button" class="action" data-origin="home">Дом</button>
        <button type="button" class="action" data-origin="office">Офис</button>
        <button type="button" class="action" data-origin="other">Другое</button>
        <button type="button" class="action" data-origin="none">Без трансфера</button>
      </div>
      <div id="navigationOriginAddressRow" hidden style="margin-top:10px;display:flex;gap:8px">
        <input id="navigationOriginAddress" type="text" maxlength="500" placeholder="Адрес или место" style="flex:1;min-width:0">
        <button id="navigationOriginAddressSubmit" type="button" class="action">Готово</button>
      </div>
      <div id="navigationOriginState" style="font-size:13px;opacity:.72;margin-top:8px"></div>`;
    document.body.append(card);
    card.querySelectorAll("[data-origin]").forEach((button) => {
      button.addEventListener("click", () => chooseOrigin(button.dataset.origin));
    });
    card.querySelector("#navigationOriginAddressSubmit").addEventListener("click", submitAddress);
    const input = card.querySelector("#navigationOriginAddress");
    input.addEventListener("input", () => { originEditing = true; });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") submitAddress();
    });
    return card;
  }

  function originDialogOpen() {
    const card = document.getElementById("navigationOriginQuestion");
    return Boolean(activeOriginRequest && card && !card.hidden);
  }

  function ensureOptimizationCard() {
    let card = document.getElementById("navigationOptimizationQuestion");
    if (card) return card;
    card = document.createElement("div");
    card.id = "navigationOptimizationQuestion";
    card.hidden = true;
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-live", "assertive");
    card.style.cssText = [
      "position:fixed", "left:50%", "bottom:max(18px, env(safe-area-inset-bottom))",
      "transform:translateX(-50%)", "z-index:1210", "width:min(92vw,520px)",
      "max-height:calc(100dvh - 36px)", "overflow-y:auto", "box-sizing:border-box",
      "padding:16px", "border-radius:18px", "background:var(--surface,#fff)",
      "box-shadow:0 14px 46px rgba(0,0,0,.22)", "border:1px solid rgba(0,0,0,.14)"
    ].join(";");
    card.innerHTML = `
      <div style="font-weight:700;margin-bottom:6px">Не хватает времени на дорогу</div>
      <div id="navigationOptimizationText" style="font-size:14px;line-height:1.45;opacity:.78;margin-bottom:12px"></div>
      <div style="display:grid;gap:8px">
        <button id="navigationShortenPrevious" type="button" class="action"></button>
        <button id="navigationShiftTarget" type="button" class="action"></button>
        <button id="navigationIgnoreTransfer" type="button" class="action">Оставить как есть — без трансфера</button>
      </div>
      <div id="navigationOptimizationState" style="font-size:13px;opacity:.72;margin-top:9px"></div>`;
    document.body.append(card);
    card.querySelector("#navigationShortenPrevious").addEventListener("click", () => sendOptimization("shorten_previous"));
    card.querySelector("#navigationShiftTarget").addEventListener("click", () => sendOptimization("shift_target"));
    card.querySelector("#navigationIgnoreTransfer").addEventListener("click", () => sendOptimization("ignore_transfer"));
    return card;
  }

  function showAddressInput(choice) {
    const card = ensureOriginCard();
    const row = card.querySelector("#navigationOriginAddressRow");
    const previousChoice = row.dataset.choice || "";
    const wasHidden = row.hidden;
    row.hidden = false;
    row.style.display = "flex";
    row.dataset.choice = choice;
    originEditing = true;
    const input = card.querySelector("#navigationOriginAddress");
    input.placeholder = choice === "home" ? "Адрес дома" : choice === "office" ? "Адрес офиса" : "Откуда поедете?";
    if (wasHidden || previousChoice !== choice) input.value = "";
    input.focus();
  }

  async function chooseOrigin(choice) {
    if (!navigationEnabled || !activeOriginRequest || originBusy) return;
    if (choice === "none") {
      originEditing = false;
      await sendOrigin("other", activeOriginRequest.destination, true);
      return;
    }
    if (choice === "other") {
      showAddressInput(choice);
      return;
    }
    if (choice === "home" && !activeOriginRequest.home_available) {
      showAddressInput(choice);
      return;
    }
    if (choice === "office" && !activeOriginRequest.office_available) {
      showAddressInput(choice);
      return;
    }
    originEditing = false;
    await sendOrigin(choice, "");
  }

  async function submitAddress() {
    if (!navigationEnabled || !activeOriginRequest || originBusy) return;
    const card = ensureOriginCard();
    const row = card.querySelector("#navigationOriginAddressRow");
    const choice = row.dataset.choice || "other";
    const address = card.querySelector("#navigationOriginAddress").value.trim();
    if (!address) {
      card.querySelector("#navigationOriginState").textContent = "Укажи адрес или место.";
      return;
    }
    await sendOrigin(choice, address, false);
  }

  async function sendOrigin(choice, address, skipTransfer = false) {
    const request = activeOriginRequest;
    if (!navigationEnabled || !request) return;
    const card = ensureOriginCard();
    const state = card.querySelector("#navigationOriginState");
    originBusy = true;
    state.textContent = skipTransfer ? "Оставляю без трансфера…" : "Считаю маршрут…";
    try {
      const data = await api("/api/navigation/origin-request", {
        method: "POST",
        body: JSON.stringify({event_id: request.event_id, choice, address}),
      });
      if (skipTransfer) {
        state.textContent = "Оставил событие без трансфера.";
      } else if (data.status === "same_location") {
        state.textContent = "Трансфер не нужен: место отправления и событие совпадают.";
      } else if (data.status === "optimization_required") {
        state.textContent = `Не хватает ${data.missing_minutes || 0} мин. Предлагаю оптимизацию расписания.`;
      } else if (data.status === "created") {
        state.textContent = "Маршрут добавлен.";
      } else {
        state.textContent = "Маршрут не создан.";
      }
      originEditing = false;
      activeOriginRequest = null;
      const row = card.querySelector("#navigationOriginAddressRow");
      row.hidden = true;
      row.style.display = "none";
      setTimeout(() => {
        card.hidden = true;
        state.textContent = "";
        pollOptimizationRequest();
        pollOriginRequest();
      }, data.status === "optimization_required" ? 600 : 1800);
    } catch (error) {
      state.textContent = error.message;
      const row = card.querySelector("#navigationOriginAddressRow");
      originEditing = !row.hidden;
    } finally {
      originBusy = false;
    }
  }

  function renderOptimizationRequest(request) {
    const card = ensureOptimizationCard();
    const missing = request.missing_minutes || 0;
    const available = request.available_minutes || 0;
    card.querySelector("#navigationOptimizationText").textContent =
      `После «${request.previous_title}» доступно ${available} мин, а дорога до «${request.title}» с запасом требует ${request.required_minutes || 0} мин. Не хватает ${missing} мин.`;

    const shorten = card.querySelector("#navigationShortenPrevious");
    shorten.textContent = `Сократить «${request.previous_title}» на ${missing} мин`;
    shorten.disabled = !request.shorten?.available;
    shorten.title = request.shorten?.available ? "" : "Предыдущее событие станет слишком коротким";

    const shift = card.querySelector("#navigationShiftTarget");
    shift.textContent = `Сдвинуть «${request.title}» на ${missing} мин`;
    shift.disabled = !request.shift?.available;
    shift.title = request.shift?.available ? "" :
      (request.shift?.conflict_title ? `Сдвиг конфликтует с «${request.shift.conflict_title}»` : "Сдвиг сейчас недоступен");

    card.querySelector("#navigationOptimizationState").textContent = "";
    card.hidden = false;
  }

  async function sendOptimization(action) {
    if (!navigationEnabled || !activeOptimizationRequest || optimizationBusy) return;
    const card = ensureOptimizationCard();
    const state = card.querySelector("#navigationOptimizationState");
    optimizationBusy = true;
    state.textContent = "Применяю…";
    card.querySelectorAll("button").forEach((button) => { button.disabled = true; });
    try {
      const data = await api("/api/navigation/optimization-request", {
        method: "POST",
        body: JSON.stringify({event_id: activeOptimizationRequest.event_id, action}),
      });
      state.textContent = data.message || "Готово.";
      activeOptimizationRequest = null;
      setTimeout(() => {
        card.hidden = true;
        state.textContent = "";
        pollOptimizationRequest();
        pollOriginRequest();
      }, 1600);
    } catch (error) {
      state.textContent = error.message;
      renderOptimizationRequest(activeOptimizationRequest);
      state.textContent = error.message;
    } finally {
      optimizationBusy = false;
    }
  }

  async function pollOptimizationRequest() {
    if (!navigationEnabled || optimizationBusy || originBusy || originEditing || originDialogOpen() || document.hidden) return;
    try {
      const data = await api("/api/navigation/optimization-request");
      const request = data.request;
      const card = ensureOptimizationCard();
      if (!request) {
        if (!activeOptimizationRequest) card.hidden = true;
        return;
      }
      activeOptimizationRequest = request;
      const originCard = ensureOriginCard();
      originCard.hidden = true;
      renderOptimizationRequest(request);
    } catch (_error) {
      // Navigation optimization must never disturb the main planner UI.
    }
  }

  async function pollOriginRequest() {
    if (!navigationEnabled || originBusy || originEditing || optimizationBusy || activeOptimizationRequest || document.hidden) return;
    try {
      const data = await api("/api/navigation/origin-request");
      const request = data.request;
      const card = ensureOriginCard();
      if (!request) {
        if (!activeOriginRequest) card.hidden = true;
        return;
      }
      if (activeOriginRequest?.event_id === request.event_id && !card.hidden) return;
      activeOriginRequest = request;
      card.querySelector("#navigationOriginTitle").textContent = `Откуда поедете на «${request.title}»?`;
      card.querySelector("#navigationOriginText").textContent = `Место события: ${request.destination}. Выберите точку старта.`;
      const row = card.querySelector("#navigationOriginAddressRow");
      row.hidden = true;
      row.style.display = "none";
      row.dataset.choice = "";
      card.querySelector("#navigationOriginState").textContent = "";
      card.hidden = false;
    } catch (_error) {
      // Polling is optional and must never disturb the main planner UI.
    }
  }

  function install() {
    if (installed) return;
    const root = document.getElementById("assistantSettings") || document.querySelector("#settingsPanel .sheet");
    if (!root) return;
    installed = true;
    decorateMasterSwitch();
    document.getElementById("navigationEnabled")?.addEventListener("change", onMasterChange);

    const section = document.createElement("details");
    section.id = "navigationAdvancedGroup";
    section.className = "assistant-section settings-group";
    section.innerHTML = `
      <summary class="settings-group-summary-ready">
        <span class="settings-group-title">Дорога до события</span>
        <span class="settings-group-meta">навигация</span>
      </summary>
      <p class="settings-help">Общий запас из основных настроек сохраняется. Дополнительно можно учесть парковку и время от парковки/остановки до двери.</p>
      <div class="grid">
        <label class="field">Парковка, минут<input id="parkingBufferMinutes" type="number" min="0" max="180" step="5" value="0"></label>
        <label class="field">Дойти до места, минут<input id="walkingBufferMinutes" type="number" min="0" max="180" step="5" value="0"></label>
      </div>
      <div class="assistant-actions">
        <button id="openNextRoute" class="action" type="button">Открыть маршрут к следующей встрече</button>
      </div>
      <p id="navigationExtraState" class="settings-help" role="status"></p>`;
    root.append(section);
    section.addEventListener("toggle", () => { if (section.open) load(); });
    for (const id of ["parkingBufferMinutes", "walkingBufferMinutes"]) {
      const input = document.getElementById(id);
      input?.addEventListener("input", () => scheduleBufferSave(400));
      input?.addEventListener("change", () => scheduleBufferSave(0));
    }
    section.querySelector("#openNextRoute").onclick = () => openNextRoute().catch((error) => {
      document.getElementById("navigationExtraState").textContent = error.message;
    });
    ensureOriginCard();
    ensureOptimizationCard();
    applyNavigationUiState(document.getElementById("navigationEnabled")?.value === "true");
    pollOptimizationRequest();
    pollOriginRequest();
    setInterval(() => {
      pollOptimizationRequest();
      pollOriginRequest();
    }, 5000);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && navigationEnabled) {
        pollOptimizationRequest();
        pollOriginRequest();
      }
    });
  }

  document.addEventListener("planner-ready", () => {
    install();
    syncMasterFromSavedSettings();
  });
  if (document.readyState !== "loading") {
    setTimeout(() => {
      install();
      const control = document.getElementById("navigationEnabled");
      if (control) {
        applyNavigationUiState(control.value === "true");
        if (document.documentElement.dataset.navigationEnabled === "true") {
          setNavigationActive(true, {notify: false});
        }
      }
    }, 0);
  }
})();