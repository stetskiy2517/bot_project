(() => {
  "use strict";

  const app = document.getElementById("app");
  if (!app) return;

  let loading = false;
  let initialized = false;
  let observer = null;
  let reviewContext = null;
  let reviewRefreshedAt = 0;

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function api(path, options = {}) {
    if (typeof window.api === "function") return window.api(path, options);
    return fetch(path, {headers: {"Content-Type": "application/json"}, ...options}).then(async response => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.message || data.error || `HTTP ${response.status}`);
      return data;
    });
  }

  function mainActionLabel(item) {
    if (item.action_type === "email_action") return "Выполнить";
    if (item.action_type === "task") return "Открыть";
    if (item.action_type === "navigation") return "Решить";
    return "Понятно";
  }

  async function refreshReviewContext() {
    if (reviewContext && Date.now() - reviewRefreshedAt < 60000) return reviewContext;
    try {
      const today = await api("/api/mobile/today");
      reviewContext = today?.review || null;
      reviewRefreshedAt = Date.now();
    } catch (_error) {
      // Attention remains usable even if the daily briefing cannot be refreshed.
    }
    return reviewContext;
  }

  function render(items, review = null) {
    const content = document.getElementById("mobileTodayContent");
    if (!content) return;
    content.querySelector(".mobile-attention-card")?.remove();

    const normalized = Array.isArray(items) ? items : [];
    const aiReview = review?.presentation === "ai";
    const card = document.createElement("div");
    card.className = `mobile-card mobile-attention-card${normalized.length ? "" : " is-empty"}`;
    card.innerHTML = `
      <div class="mobile-card-title">
        <span>Требует внимания</span>
        <span class="mobile-attention-count">${normalized.length}</span>
      </div>
      <div class="mobile-attention-list">
        ${normalized.length ? normalized.slice(0, 5).map(item => {
          const showAiBadge = aiReview && item.source_type === "daily_review";
          return `
          <div class="mobile-attention-item${item.unseen ? "" : " seen"}" data-attention-id="${Number(item.attention_id)}">
            <span class="mobile-attention-dot ${esc(item.priority || "normal")}"></span>
            <div class="mobile-attention-main">
              <div class="mobile-attention-heading">
                <div class="mobile-attention-title">${esc(item.title)}</div>
                ${showAiBadge ? '<span class="mobile-attention-ai-badge">AI-сводка</span>' : ""}
              </div>
              ${item.body ? `<div class="mobile-attention-body">${esc(item.body)}</div>` : ""}
              <div class="mobile-attention-actions">
                <button type="button" data-attention-action="primary">${mainActionLabel(item)}</button>
                <button type="button" class="secondary" data-attention-action="dismiss">Скрыть</button>
              </div>
            </div>
          </div>`;
        }).join("") : `
          <div class="mobile-attention-empty">
            Сейчас ничего не требует внимания.
          </div>`}
      </div>`;
    content.prepend(card);
  }

  async function load({force = false} = {}) {
    if (loading || !app.classList.contains("mobile-view-today")) return;
    const content = document.getElementById("mobileTodayContent");
    if (!content) return;
    loading = true;
    try {
      await refreshReviewContext();
      const data = await api("/api/mobile/attention");
      render(data.items || [], reviewContext);
      focusDeepLink();
    } catch (_error) {
      // The Today screen must remain usable if the attention subsystem is temporarily unavailable.
    } finally {
      loading = false;
    }
  }

  async function markSeen(id) {
    try {
      await api(`/api/mobile/attention/${id}/seen`, {method: "POST", body: "{}"});
    } catch (_error) {}
  }

  async function dismiss(id) {
    await api(`/api/mobile/attention/${id}`, {method: "DELETE"});
    await load({force: true});
  }

  async function applyEmailAction(item, id) {
    if (!item.action || typeof item.action !== "object") return;
    const result = await api("/api/email/action", {
      method: "POST",
      body: JSON.stringify(item.action),
    });
    if (result?.created || result?.already_present || result?.ok) await dismiss(id);
  }

  function openTask(item) {
    const taskId = Number(item.action?.task_id || 0);
    if (!taskId) return false;
    const button = document.querySelector(`[data-task-id="${taskId}"]`);
    if (!button) return false;
    button.click();
    return true;
  }

  function openNavigation() {
    // navigation-extra.js already treats visibilitychange as an immediate refresh trigger.
    // Reuse that path instead of duplicating its origin/optimization state machine here.
    document.dispatchEvent(new Event("visibilitychange"));
    return true;
  }

  async function handlePrimary(item, id, button) {
    button.disabled = true;
    await markSeen(id);
    try {
      if (item.action_type === "email_action") {
        await applyEmailAction(item, id);
      } else if (item.action_type === "task") {
        if (!openTask(item)) throw new Error("Задача не показана в текущем списке");
      } else if (item.action_type === "navigation") {
        openNavigation();
      } else {
        await dismiss(id);
      }
    } finally {
      button.disabled = false;
    }
  }

  async function onClick(event) {
    const button = event.target.closest("[data-attention-action]");
    if (!button) return;
    const row = button.closest(".mobile-attention-item");
    const id = Number(row?.dataset.attentionId || 0);
    if (!id) return;
    const data = await api("/api/mobile/attention");
    const item = (data.items || []).find(candidate => Number(candidate.attention_id) === id);
    if (!item) {
      await load({force: true});
      return;
    }
    try {
      if (button.dataset.attentionAction === "dismiss") await dismiss(id);
      else await handlePrimary(item, id, button);
    } catch (error) {
      button.disabled = false;
      window.alert(error?.message || "Не удалось выполнить действие");
    }
  }

  function focusDeepLink() {
    const params = new URLSearchParams(location.search);
    const id = Number(params.get("attention") || 0);
    if (!id) return;
    const row = document.querySelector(`.mobile-attention-item[data-attention-id="${id}"]`);
    row?.scrollIntoView({behavior: "smooth", block: "center"});
  }

  function openTodayFromDeepLink() {
    const params = new URLSearchParams(location.search);
    if (params.get("view") !== "today") return;
    const button = document.querySelector('#mobileBottomNav [data-view="today"]');
    if (button && !app.classList.contains("mobile-view-today")) button.click();
  }

  function install() {
    if (initialized) return;
    const nav = document.getElementById("mobileBottomNav");
    const today = document.getElementById("mobileTodayScreen");
    if (!nav || !today) {
      setTimeout(install, 50);
      return;
    }
    initialized = true;
    document.addEventListener("click", onClick);
    observer = new MutationObserver(() => {
      if (app.classList.contains("mobile-view-today")) {
        setTimeout(() => load(), 0);
      }
    });
    observer.observe(app, {attributes: true, attributeFilter: ["class"]});

    const contentObserver = new MutationObserver(() => {
      if (app.classList.contains("mobile-view-today") && !document.querySelector(".mobile-attention-card")) {
        setTimeout(() => load(), 0);
      }
    });
    contentObserver.observe(today, {childList: true, subtree: true});

    openTodayFromDeepLink();
    if (app.classList.contains("mobile-view-today")) load({force: true});
  }

  document.addEventListener("planner-ready", install, {once: true});
  document.addEventListener("DOMContentLoaded", install, {once: true});
  if (document.readyState !== "loading") setTimeout(install, 0);
})();
