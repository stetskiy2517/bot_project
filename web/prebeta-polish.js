(() => {
  "use strict";

  if (window.__plannerPrebetaPolish) return;
  window.__plannerPrebetaPolish = true;

  const app = document.getElementById("app");
  if (!app) return;

  const UPDATE_PENDING_KEY = "personal-secretary-update-pending-v1";
  let lastSuccessAt = 0;
  let connectionHideTimer = null;
  let updateReloading = false;
  let updateActivationRequested = false;
  let pendingUpdateRegistration = null;
  let diagnosticsBusy = false;
  let diagnosticsTimer = null;

  const style = document.createElement("style");
  style.id = "prebetaPolishStyles";
  style.textContent = `
    .planner-system-banner{position:absolute;z-index:290;left:50%;bottom:calc(env(safe-area-inset-bottom) + 82px);width:min(480px,calc(100% - 24px));min-height:44px;display:flex;align-items:center;gap:10px;padding:8px 9px 8px 13px;border:1px solid rgba(0,0,0,.07);border-radius:14px;background:rgba(31,31,31,.96);color:#fff;box-shadow:0 10px 34px rgba(0,0,0,.14);transform:translate(-50%,14px);opacity:0;visibility:hidden;pointer-events:none;transition:opacity .18s ease,transform .18s ease,visibility 0s linear .18s}
    .planner-system-banner.show{opacity:1;visibility:visible;transform:translate(-50%,0);pointer-events:none;transition-delay:0s}.planner-system-banner-text{min-width:0;flex:1;font-size:13px;line-height:1.3}.planner-system-banner-action{flex:0 0 auto;min-height:44px;padding:0 12px;border-radius:11px;background:#fff;color:#171717;font-size:12px;font-weight:700;cursor:pointer;pointer-events:auto}.planner-system-banner-action[hidden]{display:none}
    #plannerConnectionBanner.offline{background:rgba(67,54,54,.97)}
    #diagnosticsGroup .diagnostics-grid{display:grid;gap:1px;margin-top:10px;border:1px solid #ececea;border-radius:14px;overflow:hidden;background:#ececea}.diagnostics-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;padding:11px 12px;background:#fff;align-items:center}.diagnostics-label{font-size:13px;color:#555550}.diagnostics-value{max-width:210px;text-align:right;color:#8a8a85;font-size:12px;overflow-wrap:anywhere}.diagnostics-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}.diagnostics-actions button{min-height:44px;border-radius:12px;background:#efefec;color:#272725;font-weight:650;cursor:pointer}.diagnostics-status{min-height:18px;margin-top:8px;color:#888883;font-size:11px;line-height:1.35}
    @media(max-width:430px){.diagnostics-row{grid-template-columns:1fr}.diagnostics-value{text-align:left;max-width:none}}
  `;
  document.head.appendChild(style);

  function makeBanner(id) {
    const banner = document.createElement("div");
    banner.id = id;
    banner.className = "planner-system-banner";
    banner.setAttribute("role", "status");
    banner.setAttribute("aria-live", "polite");
    banner.innerHTML = '<div class="planner-system-banner-text"></div><button class="planner-system-banner-action" type="button"></button>';
    app.appendChild(banner);
    return banner;
  }

  const connectionBanner = makeBanner("plannerConnectionBanner");
  const updateBanner = makeBanner("plannerUpdateBanner");

  function friendlyError(error) {
    const status = Number(error?.status || 0);
    const raw = String(error?.message || error || "").trim();
    if (!navigator.onLine || error instanceof TypeError || /failed to fetch|networkerror|network request/i.test(raw)) {
      return "Нет соединения. Проверьте интернет и попробуйте ещё раз.";
    }
    if (status === 401) return "Сессия истекла. Войдите снова.";
    if (status === 429) return "Слишком много запросов. Подождите немного и повторите.";
    if (status >= 500) return "Сервис временно недоступен. Попробуйте ещё раз чуть позже.";
    if (raw && !/request_failed|http \d+/i.test(raw)) return raw;
    return "Не удалось выполнить действие. Попробуйте ещё раз.";
  }

  function setConnection(message, {offline = false, action = false, temporary = false} = {}) {
    clearTimeout(connectionHideTimer);
    connectionBanner.querySelector(".planner-system-banner-text").textContent = message;
    const button = connectionBanner.querySelector(".planner-system-banner-action");
    button.textContent = "Повторить";
    button.hidden = !action;
    connectionBanner.classList.toggle("offline", offline);
    connectionBanner.classList.add("show");
    if (temporary) connectionHideTimer = setTimeout(() => connectionBanner.classList.remove("show"), 2200);
  }

  function refreshDiagnosticsSoon() {
    if (diagnosticsBusy) return;
    clearTimeout(diagnosticsTimer);
    diagnosticsTimer = setTimeout(() => {
      if (document.getElementById("diagnosticsGroup")?.open) refreshDiagnostics();
    }, 150);
  }

  function markSuccess() {
    const wasOffline = connectionBanner.classList.contains("offline") || !navigator.onLine;
    lastSuccessAt = Date.now();
    if (wasOffline && navigator.onLine) setConnection("Соединение восстановлено", {temporary: true});
    else if (navigator.onLine) connectionBanner.classList.remove("show", "offline");
    refreshDiagnosticsSoon();
  }

  function markNetworkError() {
    setConnection("Нет соединения", {offline: true, action: true});
    refreshDiagnosticsSoon();
  }

  async function retryConnection() {
    const button = connectionBanner.querySelector(".planner-system-banner-action");
    button.disabled = true;
    button.textContent = "Проверяю…";
    try {
      if (!window.PlannerRequests?.status) throw new Error("offline");
      await window.PlannerRequests.status();
      markSuccess();
    } catch (_) {
      markNetworkError();
    } finally {
      button.disabled = false;
      button.textContent = "Повторить";
    }
  }
  connectionBanner.querySelector(".planner-system-banner-action").onclick = retryConnection;

  function wrapRequests() {
    const requests = window.PlannerRequests;
    if (!requests || requests.__polishWrapped) return false;
    requests.__polishWrapped = true;
    for (const name of ["request", "status"]) {
      const original = requests[name];
      if (typeof original !== "function") continue;
      requests[name] = async function(...args) {
        try {
          const result = await original.apply(this, args);
          markSuccess();
          return result;
        } catch (error) {
          const status = Number(error?.status || 0);
          if (!navigator.onLine || error instanceof TypeError || status >= 500) markNetworkError();
          throw error;
        }
      };
    }
    return true;
  }

  function waitForRequests() {
    if (!wrapRequests()) setTimeout(waitForRequests, 80);
  }

  window.addEventListener("offline", markNetworkError);
  window.addEventListener("online", retryConnection);
  if (!navigator.onLine) markNetworkError();
  waitForRequests();

  function activateWaitingWorker(registration, automatic = false) {
    const worker = registration?.waiting;
    if (!worker) return false;
    updateActivationRequested = true;
    if (automatic) updateBanner.querySelector(".planner-system-banner-text").textContent = "Обновление применится при следующем открытии";
    worker.postMessage({type: "SKIP_WAITING"});
    pendingUpdateRegistration = null;
    return true;
  }

  function showUpdate(registration) {
    if (!registration?.waiting || !navigator.serviceWorker?.controller) return;
    pendingUpdateRegistration = registration;
    try { localStorage.setItem(UPDATE_PENDING_KEY, "1"); } catch (_) {}
    updateBanner.querySelector(".planner-system-banner-text").textContent = "Доступно обновление приложения";
    const button = updateBanner.querySelector(".planner-system-banner-action");
    button.hidden = false;
    button.textContent = "Обновить";
    button.onclick = () => activateWaitingWorker(registration, false);
    updateBanner.classList.add("show");
  }

  function activatePendingUpdateOnBackground() {
    if (!pendingUpdateRegistration?.waiting) return;
    activateWaitingWorker(pendingUpdateRegistration, true);
  }

  async function watchServiceWorker() {
    if (!("serviceWorker" in navigator)) return;
    try {
      const registration = await navigator.serviceWorker.getRegistration();
      if (!registration) return;
      showUpdate(registration);
      registration.addEventListener("updatefound", () => {
        const worker = registration.installing;
        worker?.addEventListener("statechange", () => {
          if (worker.state === "installed") showUpdate(registration);
        });
      });
      const check = () => registration.update().catch(() => {});
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "hidden") activatePendingUpdateOnBackground();
        else check();
      });
      window.addEventListener("pagehide", activatePendingUpdateOnBackground);
      setInterval(check, 15 * 60 * 1000);
    } catch (_) {}
  }

  navigator.serviceWorker?.addEventListener("controllerchange", () => {
    if (!updateActivationRequested || updateReloading) return;
    updateReloading = true;
    try { localStorage.removeItem(UPDATE_PENDING_KEY); } catch (_) {}
    if (document.visibilityState === "visible") location.reload();
  });
  setTimeout(watchServiceWorker, 300);

  async function confirmAction(options = {}) {
    if (window.PlannerTaskEditor?.confirmAction) return window.PlannerTaskEditor.confirmAction(options);
    return false;
  }

  function diagnosticsGroup() {
    const root = document.getElementById("assistantSettings");
    if (!root) return null;
    let group = document.getElementById("diagnosticsGroup");
    if (group) return group;
    group = document.createElement("details");
    group.id = "diagnosticsGroup";
    group.className = "settings-group assistant-section";
    group.innerHTML = `
      <summary><span class="settings-group-title">Диагностика</span><span class="settings-group-meta">Статус приложения</span></summary>
      <div class="diagnostics-grid">
        <div class="diagnostics-row"><span class="diagnostics-label">Версия</span><span id="diagVersion" class="diagnostics-value">—</span></div>
        <div class="diagnostics-row"><span class="diagnostics-label">Сеть</span><span id="diagNetwork" class="diagnostics-value">—</span></div>
        <div class="diagnostics-row"><span class="diagnostics-label">Push</span><span id="diagPush" class="diagnostics-value">—</span></div>
        <div class="diagnostics-row"><span class="diagnostics-label">ИИ</span><span id="diagAi" class="diagnostics-value">—</span></div>
        <div class="diagnostics-row"><span class="diagnostics-label">Последняя связь</span><span id="diagLastSync" class="diagnostics-value">—</span></div>
      </div>
      <div class="diagnostics-actions"><button id="refreshDiagnostics" type="button">Обновить</button><button id="copyDiagnostics" type="button">Скопировать</button></div>
      <div id="diagnosticsStatus" class="diagnostics-status" role="status" aria-live="polite"></div>`;
    root.appendChild(group);
    group.addEventListener("toggle", () => { if (group.open) refreshDiagnostics(); });
    group.querySelector("#refreshDiagnostics").onclick = refreshDiagnostics;
    group.querySelector("#copyDiagnostics").onclick = copyDiagnostics;
    document.dispatchEvent(new Event("planner-settings-changed"));
    return group;
  }

  function pushState(subscription) {
    if (!("Notification" in window)) return "Не поддерживается";
    if (Notification.permission === "denied") return "Запрещены";
    if (Notification.permission !== "granted") return "Не включены";
    return subscription ? "Подключены" : "Разрешены, не подключены";
  }

  function formatLastSync() {
    if (!lastSuccessAt) return "Ещё не было";
    return new Date(lastSuccessAt).toLocaleTimeString("ru-RU", {hour: "2-digit", minute: "2-digit", second: "2-digit"});
  }

  async function refreshDiagnostics() {
    const group = diagnosticsGroup();
    if (!group || diagnosticsBusy) return;
    diagnosticsBusy = true;
    const statusEl = group.querySelector("#diagnosticsStatus");
    statusEl.textContent = "Проверяю…";
    try {
      const results = await Promise.allSettled([
        window.PlannerRequests?.status?.(),
        window.PlannerRequests?.request?.("/api/assistant"),
        navigator.serviceWorker?.getRegistration?.().then(reg => reg?.pushManager?.getSubscription?.()),
      ]);
      const statusData = results[0]?.status === "fulfilled" ? results[0].value : null;
      const assistantData = results[1]?.status === "fulfilled" ? results[1].value : null;
      const subscription = results[2]?.status === "fulfilled" ? results[2].value : null;
      group.querySelector("#diagVersion").textContent = String(statusData?.app_version || "неизвестна");
      group.querySelector("#diagNetwork").textContent = navigator.onLine && statusData ? "Онлайн" : navigator.onLine ? "Сервер недоступен" : "Нет сети";
      group.querySelector("#diagPush").textContent = pushState(subscription);
      const ai = assistantData?.ai || {};
      const access = assistantData?.access || {};
      group.querySelector("#diagAi").textContent = ai.configured || ai.enabled
        ? `${ai.provider || "AI"}${ai.model ? ` · ${ai.model}` : ""}${access.allowed === false ? " · нет доступа" : ""}`
        : "Не настроен";
      group.querySelector("#diagLastSync").textContent = formatLastSync();
      statusEl.textContent = statusData ? "Проверка завершена." : "Сервер не ответил. Остальные статусы показаны локально.";
    } catch (error) {
      statusEl.textContent = friendlyError(error);
    } finally {
      diagnosticsBusy = false;
    }
  }

  function diagnosticText() {
    const value = id => document.getElementById(id)?.textContent?.trim() || "—";
    return [
      "Personal Secretary — диагностика",
      `Версия: ${value("diagVersion")}`,
      `Сеть: ${value("diagNetwork")}`,
      `Push: ${value("diagPush")}`,
      `ИИ: ${value("diagAi")}`,
      `Последняя связь: ${value("diagLastSync")}`,
      `Браузер: ${navigator.userAgent}`,
    ].join("\n");
  }

  async function copyDiagnostics() {
    const statusEl = document.getElementById("diagnosticsStatus");
    const text = diagnosticText();
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
      else {
        const area = document.createElement("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        area.remove();
      }
      if (statusEl) statusEl.textContent = "Диагностика скопирована.";
    } catch (_) {
      if (statusEl) statusEl.textContent = "Не удалось скопировать. Попробуйте ещё раз.";
    }
  }

  function installDiagnostics() {
    if (!diagnosticsGroup()) setTimeout(installDiagnostics, 120);
  }
  document.addEventListener("planner-ready", installDiagnostics);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", installDiagnostics, {once: true});
  else installDiagnostics();

  window.PlannerPolish = {
    friendlyError,
    confirmAction,
    refreshDiagnostics,
    get lastSuccessAt() { return lastSuccessAt; },
  };
})();
