(() => {
  "use strict";

  const app = document.getElementById("app");
  const nav = document.getElementById("mobileBottomNav");
  const chat = document.getElementById("chat");
  if (!app || !nav || window.__plannerSwipeNavigation) return;
  window.__plannerSwipeNavigation = true;

  const VIEW_ORDER = ["home", "chat", "today", "more"];
  const SWIPE_MIN_X = 64;
  const SWIPE_DOWN_MIN_Y = 72;
  const SHEET_TOP_GRAB_ZONE = 88;
  const WHEEL_MIN_X = 90;
  const DIRECTION_RATIO = 1.25;
  const WHEEL_RESET_MS = 180;
  const CHAT_HISTORY_MAX_MESSAGES = 14;
  const CHAT_HISTORY_KEY_PREFIX = "personal-secretary-chat-history-v1";
  const SHEET_HANDLE_SELECTOR = ".handle, [class*='-handle']";
  const SHEET_ROOT_SELECTOR = [
    "#settingsPanel.open",
    "#lifeWheelPanel.open",
    "#mobileSheetBackdrop.open",
    "#noteWindowBackdrop.open",
    "#reminderEditBackdrop.open",
    "#taskEditorBackdrop.open",
    ".panel.open",
    "[class*='backdrop'].open",
    "[class*='overlay'].open",
  ].join(", ");
  const SHEET_CLOSE_SELECTOR = [
    "[data-note-close]",
    "[data-note-editor-cancel]",
    "[data-reminder-edit-cancel]",
    "[data-task-editor-cancel]",
    "#closeLifeWheel",
    "#closeSettings",
    ".event-detail-close",
    "[aria-label='Закрыть']",
    "[aria-label='Отмена']",
    ".mobile-action-button.secondary",
  ].join(", ");

  let gesture = null;
  let sheetGesture = null;
  let libraryDocumentOpen = false;
  let suppressClickUntil = 0;
  let wheelX = 0;
  let wheelTimer = null;
  let chatHistoryKey = null;
  let volatileChatHistory = [];

  function modalOpen() {
    return Boolean(
      document.getElementById("login")?.classList.contains("open") ||
      document.getElementById("settingsPanel")?.classList.contains("open") ||
      document.getElementById("lifeWheelPanel")?.classList.contains("open") ||
      document.querySelector(".reminder-snooze-backdrop.open")
    );
  }

  function activeBackSheet() {
    const candidates = Array.from(document.querySelectorAll(
      "#mobileSheetBackdrop.open, #noteWindowBackdrop.open, #reminderEditBackdrop.open, #taskEditorBackdrop.open"
    )).filter(node => {
      const style = getComputedStyle(node);
      return style.display !== "none" && style.visibility !== "hidden" && style.pointerEvents !== "none";
    });
    let top = null;
    let topZ = Number.NEGATIVE_INFINITY;
    for (const node of candidates) {
      const parsed = Number.parseInt(getComputedStyle(node).zIndex, 10);
      const zIndex = Number.isFinite(parsed) ? parsed : 0;
      if (!top || zIndex >= topZ) {
        top = node;
        topZ = zIndex;
      }
    }
    return top;
  }

  function topSheetOpen() {
    return Boolean(activeBackSheet());
  }

  function blockedGestureTarget(target) {
    return Boolean(target?.closest(
      "input, textarea, select, #mobileBottomNav, .record-button, .composer-voice-button, .library-swipe-row, .reminder-action"
    ));
  }

  function activeView() {
    const active = nav.querySelector(".mobile-nav-button.active[data-view]");
    return active?.dataset.view || "home";
  }

  function switchView(direction) {
    const current = activeView();
    const index = VIEW_ORDER.indexOf(current);
    if (index < 0) return false;
    const nextIndex = index + direction;
    if (nextIndex < 0 || nextIndex >= VIEW_ORDER.length) return false;
    const button = nav.querySelector(`[data-view="${VIEW_ORDER[nextIndex]}"]`);
    if (!button) return false;
    button.click();
    return true;
  }

  function closeSheetRoot(root) {
    if (!root) return false;
    const closeControl = root.querySelector(SHEET_CLOSE_SELECTOR);
    if (closeControl && !closeControl.disabled) {
      closeControl.click();
      return true;
    }

    try {
      root.dispatchEvent(new PointerEvent("pointerdown", {bubbles: true, cancelable: true}));
    } catch (_error) {
      root.dispatchEvent(new Event("pointerdown", {bubbles: true, cancelable: true}));
    }
    if (!root.classList.contains("open")) return true;
    root.dispatchEvent(new MouseEvent("click", {bubbles: true, cancelable: true}));
    return true;
  }

  function closeTopSheet() {
    return closeSheetRoot(activeBackSheet());
  }

  function handledSheetFromTarget(target) {
    let node = target?.nodeType === 1 ? target : target?.parentElement;
    while (node && node !== document.body) {
      const handle = Array.from(node.children || []).find(child => child.matches?.(SHEET_HANDLE_SELECTOR));
      if (handle) return {sheet: node, handle};
      node = node.parentElement;
    }
    return null;
  }

  function sheetDismissTarget(target, clientY) {
    if (!target?.closest) return null;
    const handled = handledSheetFromTarget(target);
    if (!handled) return null;
    const {sheet} = handled;
    const root = sheet.closest(SHEET_ROOT_SELECTOR);
    if (!root) return null;

    const onHandle = Boolean(target.closest(SHEET_HANDLE_SELECTOR));
    const rect = sheet.getBoundingClientRect();
    if (!onHandle && clientY > rect.top + SHEET_TOP_GRAB_ZONE) return null;
    if (!onHandle && Number(sheet.scrollTop || 0) > 2) return null;
    return {root, sheet};
  }

  function openEventSheet(row) {
    const backdrop = document.getElementById("mobileSheetBackdrop");
    if (!backdrop || backdrop.classList.contains("open")) return false;

    const title = row.querySelector(".mobile-row-title")?.textContent?.trim() || "Событие";
    const subtitle = row.querySelector(".mobile-row-subtitle")?.textContent?.trim() || "";

    const sheet = document.createElement("section");
    sheet.className = "mobile-sheet";
    sheet.setAttribute("role", "dialog");
    sheet.setAttribute("aria-modal", "true");
    sheet.setAttribute("aria-label", "Событие");

    const handle = document.createElement("div");
    handle.className = "mobile-sheet-handle";

    const heading = document.createElement("h2");
    heading.className = "mobile-sheet-title";
    heading.textContent = "Событие";

    const card = document.createElement("div");
    card.className = "mobile-card";
    const eventTitle = document.createElement("div");
    eventTitle.className = "mobile-row-title";
    eventTitle.textContent = title;
    card.appendChild(eventTitle);
    if (subtitle) {
      const eventMeta = document.createElement("div");
      eventMeta.className = "mobile-row-subtitle";
      eventMeta.textContent = subtitle;
      card.appendChild(eventMeta);
    }

    const actions = document.createElement("div");
    actions.className = "mobile-sheet-actions";
    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.className = "mobile-action-button secondary";
    closeButton.textContent = "Назад";
    closeButton.addEventListener("click", closeTopSheet);
    actions.appendChild(closeButton);

    sheet.append(handle, heading, card, actions);
    backdrop.replaceChildren(sheet);
    backdrop.classList.add("open");
    return true;
  }

  function openEventDetails(row) {
    const eventId = row?.dataset?.eventId;
    if (eventId && window.PlannerEventEditor?.open) {
      window.PlannerEventEditor.open(eventId);
      return true;
    }
    return openEventSheet(row);
  }

  function suppressNextClick() {
    suppressClickUntil = performance.now() + 400;
  }

  function resetWheelSoon() {
    clearTimeout(wheelTimer);
    wheelTimer = setTimeout(() => {
      wheelX = 0;
      wheelTimer = null;
    }, WHEEL_RESET_MS);
  }

  function chatIdentityToken() {
    const email = document.getElementById("accountEmail")?.textContent?.trim().toLocaleLowerCase("en-US");
    if (!email || email === "google calendar") return null;
    let hash = 2166136261;
    for (let index = 0; index < email.length; index += 1) {
      hash ^= email.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(36);
  }

  function normalizeStoredHistory(value) {
    if (!Array.isArray(value)) return [];
    return value
      .filter(item => item && (item.role === "user" || item.role === "assistant") && typeof item.text === "string")
      .map(item => ({role: item.role, text: item.text.slice(0, 10000)}))
      .filter(item => item.text.trim())
      .slice(-CHAT_HISTORY_MAX_MESSAGES);
  }

  function readStoredChatHistory() {
    if (!chatHistoryKey) return volatileChatHistory.slice(-CHAT_HISTORY_MAX_MESSAGES);
    try {
      const parsed = JSON.parse(localStorage.getItem(chatHistoryKey) || "[]");
      const history = normalizeStoredHistory(parsed);
      if (history.length) volatileChatHistory = history;
      return history.length ? history : volatileChatHistory.slice(-CHAT_HISTORY_MAX_MESSAGES);
    } catch (_error) {
      return volatileChatHistory.slice(-CHAT_HISTORY_MAX_MESSAGES);
    }
  }

  function chatEntriesFromDom() {
    if (!chat) return [];
    const nodes = Array.from(chat.children).filter(node => node.classList?.contains("msg"));
    if (nodes.length > CHAT_HISTORY_MAX_MESSAGES) {
      nodes.slice(0, -CHAT_HISTORY_MAX_MESSAGES).forEach(node => node.remove());
    }
    return nodes.slice(-CHAT_HISTORY_MAX_MESSAGES).map(node => ({
      role: node.classList.contains("user") ? "user" : "assistant",
      text: String(node.textContent || "").slice(0, 10000),
    })).filter(item => item.text.trim());
  }

  function persistChatHistory() {
    const history = chatEntriesFromDom();
    if (!history.length) return;
    volatileChatHistory = history;
    if (!chatHistoryKey) return;
    try {
      localStorage.setItem(chatHistoryKey, JSON.stringify(history));
    } catch (_error) {}
  }

  function restoreChatHistory() {
    if (!chat || chatEntriesFromDom().length) return;
    const history = readStoredChatHistory();
    if (!history.length) return;
    const fragment = document.createDocumentFragment();
    for (const item of history) {
      const node = document.createElement("div");
      node.className = `msg ${item.role}`;
      node.textContent = item.text;
      fragment.appendChild(node);
    }
    chat.appendChild(fragment);
    chat.scrollTop = chat.scrollHeight;
  }

  function initializeChatHistoryIdentity() {
    const token = chatIdentityToken();
    if (!token) return false;
    const nextKey = `${CHAT_HISTORY_KEY_PREFIX}:${token}`;
    if (chatHistoryKey === nextKey) {
      if (app.classList.contains("chat-active")) restoreChatHistory();
      return true;
    }

    if (chatHistoryKey && chatHistoryKey !== nextKey) {
      volatileChatHistory = [];
      chat?.replaceChildren();
    }
    chatHistoryKey = nextKey;

    const existing = chatEntriesFromDom();
    if (existing.length) {
      volatileChatHistory = existing;
      try {
        localStorage.setItem(chatHistoryKey, JSON.stringify(existing));
      } catch (_error) {}
    } else if (app.classList.contains("chat-active")) {
      restoreChatHistory();
    } else {
      const saved = readStoredChatHistory();
      if (saved.length) volatileChatHistory = saved;
    }
    return true;
  }

  if (chat && window.MutationObserver) {
    const chatObserver = new MutationObserver(() => persistChatHistory());
    chatObserver.observe(chat, {childList: true});
  }

  document.addEventListener("planner-ready", () => {
    initializeChatHistoryIdentity();
    if (app.classList.contains("chat-active")) restoreChatHistory();
  });
  window.setTimeout(initializeChatHistoryIdentity, 0);

  document.addEventListener("planner-library-open", () => {
    libraryDocumentOpen = true;
  });

  document.getElementById("libraryOpenBtn")?.addEventListener("click", () => {
    if (!app.classList.contains("chat-active")) libraryDocumentOpen = false;
  });

  const appObserver = new MutationObserver(() => {
    if (!app.classList.contains("chat-active") && !app.classList.contains("library-active")) {
      libraryDocumentOpen = false;
    }
    if (app.classList.contains("chat-active")) {
      initializeChatHistoryIdentity();
      restoreChatHistory();
    }
  });
  appObserver.observe(app, {attributes: true, attributeFilter: ["class"]});

  document.addEventListener("click", event => {
    if (performance.now() < suppressClickUntil) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }

    const eventRow = event.target.closest?.("[data-event-id]");
    if (
      eventRow &&
      app.classList.contains("mobile-view-today") &&
      !app.classList.contains("library-active") &&
      !modalOpen() &&
      !topSheetOpen() &&
      openEventDetails(eventRow)
    ) {
      event.preventDefault();
      event.stopPropagation();
    }
  }, true);

  document.addEventListener("touchstart", event => {
    sheetGesture = null;
    if (event.touches.length !== 1) {
      gesture = null;
      return;
    }

    const touch = event.touches[0];
    if (!blockedGestureTarget(event.target)) {
      const dismissTarget = sheetDismissTarget(event.target, touch.clientY);
      if (dismissTarget) {
        sheetGesture = {x: touch.clientX, y: touch.clientY, ...dismissTarget};
        gesture = null;
        return;
      }
    }

    if (modalOpen() || blockedGestureTarget(event.target)) {
      gesture = null;
      return;
    }
    gesture = {x: touch.clientX, y: touch.clientY};
  }, {capture: true, passive: true});

  document.addEventListener("touchmove", event => {
    if (!sheetGesture || event.touches.length !== 1) return;
    const touch = event.touches[0];
    const dx = touch.clientX - sheetGesture.x;
    const dy = touch.clientY - sheetGesture.y;
    if (dy > 8 && dy > Math.abs(dx) * 1.1) event.preventDefault();
  }, {capture: true, passive: false});

  document.addEventListener("touchend", event => {
    if (sheetGesture) {
      const start = sheetGesture;
      sheetGesture = null;
      gesture = null;
      if (event.changedTouches.length !== 1) return;
      const touch = event.changedTouches[0];
      const dx = touch.clientX - start.x;
      const dy = touch.clientY - start.y;
      if (dy >= SWIPE_DOWN_MIN_Y && dy > Math.abs(dx) * 1.1 && closeSheetRoot(start.root)) {
        suppressNextClick();
        event.stopPropagation();
        event.preventDefault();
      }
      return;
    }

    if (!gesture || event.changedTouches.length !== 1) {
      gesture = null;
      return;
    }
    const start = gesture;
    gesture = null;
    const touch = event.changedTouches[0];
    const dx = touch.clientX - start.x;
    const dy = touch.clientY - start.y;

    if (Math.abs(dx) < SWIPE_MIN_X || Math.abs(dx) < Math.abs(dy) * DIRECTION_RATIO) return;

    if (libraryDocumentOpen && app.classList.contains("chat-active")) return;

    if (topSheetOpen()) {
      if (dx > 0 && closeTopSheet()) {
        suppressNextClick();
        event.stopPropagation();
        event.preventDefault();
      }
      return;
    }

    if (app.classList.contains("library-active")) return;

    const changed = dx < 0 ? switchView(1) : switchView(-1);
    if (changed) {
      suppressNextClick();
      event.stopPropagation();
      event.preventDefault();
    }
  }, {capture: true, passive: false});

  document.addEventListener("touchcancel", () => {
    gesture = null;
    sheetGesture = null;
  }, {capture: true, passive: true});

  document.addEventListener("wheel", event => {
    if (modalOpen() || blockedGestureTarget(event.target)) return;
    if (Math.abs(event.deltaX) <= Math.abs(event.deltaY) * 1.1) return;

    if (libraryDocumentOpen && app.classList.contains("chat-active")) return;

    event.preventDefault();
    wheelX += event.deltaX;
    resetWheelSoon();
    if (Math.abs(wheelX) < WHEEL_MIN_X) return;

    if (topSheetOpen()) {
      if (wheelX < 0) closeTopSheet();
      wheelX = 0;
      return;
    }

    if (app.classList.contains("library-active")) {
      wheelX = 0;
      return;
    }

    const changed = wheelX > 0 ? switchView(1) : switchView(-1);
    wheelX = 0;
    if (!changed) return;
  }, {capture: true, passive: false});
})();
