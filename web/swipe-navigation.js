(() => {
  "use strict";

  const app = document.getElementById("app");
  const nav = document.getElementById("mobileBottomNav");
  const chat = document.getElementById("chat");
  if (!app || !nav || window.__plannerSwipeNavigation) return;
  window.__plannerSwipeNavigation = true;

  const VIEW_ORDER = ["home", "chat", "today", "tasks"];
  const SWIPE_MIN_X = 64;
  const SWIPE_DOWN_MIN_Y = 72;
  const SHEET_TOP_GRAB_ZONE = 88;
  const SHEET_FAST_DISMISS_MIN_Y = 28;
  const SHEET_FAST_DISMISS_VELOCITY = 0.65;
  const SHEET_SNAP_MS = 180;
  const SHEET_DISMISS_MS = 220;
  const SHEET_EASING = "cubic-bezier(.22,.8,.24,1)";
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
  let suppressClickPoint = null;
  let internalSheetCloseClick = false;
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
    if (root.id === "noteWindowBackdrop" && typeof window.PlannerNotes?.close === "function") {
      window.PlannerNotes.close();
      return true;
    }
    const closeControl = root.querySelector(SHEET_CLOSE_SELECTOR);
    if (closeControl && !closeControl.disabled) {
      internalSheetCloseClick = true;
      try {
        closeControl.click();
      } finally {
        internalSheetCloseClick = false;
      }
      if (!root.classList.contains("open")) return true;
    }

    try {
      root.dispatchEvent(new PointerEvent("pointerdown", {bubbles: true, cancelable: true}));
    } catch (_error) {
      root.dispatchEvent(new Event("pointerdown", {bubbles: true, cancelable: true}));
    }
    if (!root.classList.contains("open")) return true;

    internalSheetCloseClick = true;
    try {
      root.dispatchEvent(new MouseEvent("click", {bubbles: true, cancelable: true}));
    } finally {
      internalSheetCloseClick = false;
    }
    return !root.classList.contains("open");
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

  function parseBackdropColor(value) {
    const match = String(value || "").match(/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,\/]\s*([\d.]+))?\s*\)/i);
    if (!match) return null;
    return {
      r: Number(match[1]),
      g: Number(match[2]),
      b: Number(match[3]),
      a: match[4] === undefined ? 1 : Number(match[4]),
    };
  }

  function backdropColorWithProgress(color, progress) {
    if (!color) return "";
    const fade = 1 - Math.min(1, Math.max(0, progress)) * 0.82;
    return `rgba(${color.r}, ${color.g}, ${color.b}, ${Math.max(0, color.a * fade).toFixed(3)})`;
  }

  function prepareSheetGesture(target, touch) {
    const now = performance.now();
    const rootStyle = getComputedStyle(target.root);
    return {
      x: touch.clientX,
      y: touch.clientY,
      lastY: touch.clientY,
      lastTime: now,
      velocityY: 0,
      dragging: false,
      ...target,
      backdropColor: parseBackdropColor(rootStyle.backgroundColor),
      originalSheetTransform: target.sheet.style.transform,
      originalSheetTransition: target.sheet.style.transition,
      originalSheetWillChange: target.sheet.style.willChange,
      originalRootBackgroundColor: target.root.style.backgroundColor,
      originalRootTransition: target.root.style.transition,
      originalRootWillChange: target.root.style.willChange,
    };
  }

  function restoreSheetInlineStyles(state) {
    if (!state?.sheet || !state?.root) return;
    state.sheet.style.transform = state.originalSheetTransform;
    state.sheet.style.transition = state.originalSheetTransition;
    state.sheet.style.willChange = state.originalSheetWillChange;
    state.root.style.backgroundColor = state.originalRootBackgroundColor;
    state.root.style.transition = state.originalRootTransition;
    state.root.style.willChange = state.originalRootWillChange;
  }

  function updateSheetDrag(state, clientY) {
    const offset = Math.max(0, clientY - state.y);
    const height = Math.max(1, state.sheet.getBoundingClientRect().height);
    const progress = Math.min(1, offset / Math.max(220, height * 0.72));
    const now = performance.now();
    const elapsed = Math.max(1, now - state.lastTime);
    state.velocityY = (clientY - state.lastY) / elapsed;
    state.lastY = clientY;
    state.lastTime = now;
    state.dragging = offset > 0;

    state.sheet.style.transition = "none";
    state.sheet.style.willChange = "transform";
    state.sheet.style.transform = `translate3d(0, ${offset.toFixed(1)}px, 0)`;
    if (state.backdropColor) {
      state.root.style.transition = "none";
      state.root.style.willChange = "background-color";
      state.root.style.backgroundColor = backdropColorWithProgress(state.backdropColor, progress);
    }
  }

  function animateSheetBack(state) {
    if (!state?.sheet || !state?.root) return;
    state.sheet.style.transition = `transform ${SHEET_SNAP_MS}ms ${SHEET_EASING}`;
    state.sheet.style.transform = "translate3d(0, 0, 0)";
    if (state.backdropColor) {
      state.root.style.transition = `background-color ${SHEET_SNAP_MS}ms ease`;
      state.root.style.backgroundColor = backdropColorWithProgress(state.backdropColor, 0);
    }
    window.setTimeout(() => restoreSheetInlineStyles(state), SHEET_SNAP_MS + 40);
  }

  function animateSheetDismiss(state) {
    if (!state?.sheet || !state?.root) return false;
    const rect = state.sheet.getBoundingClientRect();
    const distance = Math.max(rect.height + 40, window.innerHeight - rect.top + 40);
    state.sheet.style.transition = `transform ${SHEET_DISMISS_MS}ms ${SHEET_EASING}`;
    state.sheet.style.willChange = "transform";
    state.sheet.style.transform = `translate3d(0, ${distance.toFixed(1)}px, 0)`;
    if (state.backdropColor) {
      state.root.style.transition = `background-color ${SHEET_DISMISS_MS}ms ease`;
      state.root.style.willChange = "background-color";
      state.root.style.backgroundColor = backdropColorWithProgress(state.backdropColor, 1);
    }

    window.setTimeout(() => {
      closeSheetRoot(state.root);
      window.setTimeout(() => restoreSheetInlineStyles(state), SHEET_DISMISS_MS + 40);
    }, SHEET_DISMISS_MS);
    return true;
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

  function suppressNextClick(touch = null) {
    suppressClickUntil = performance.now() + 400;
    suppressClickPoint = touch
      ? {x: Number(touch.clientX) || 0, y: Number(touch.clientY) || 0}
      : null;
  }

  function shouldSuppressClick(event) {
    if (performance.now() >= suppressClickUntil || internalSheetCloseClick) return false;
    if (!suppressClickPoint) return true;
    const dx = Number(event.clientX || 0) - suppressClickPoint.x;
    const dy = Number(event.clientY || 0) - suppressClickPoint.y;
    return Math.hypot(dx, dy) <= 36;
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
    if (shouldSuppressClick(event)) {
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
        sheetGesture = prepareSheetGesture(dismissTarget, touch);
        gesture = null;
        return;
      }
    }

    if ((modalOpen() && !topSheetOpen()) || blockedGestureTarget(event.target)) {
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
    if (dy > 0 && dy > Math.abs(dx) * 1.1) {
      updateSheetDrag(sheetGesture, touch.clientY);
      if (dy > 8) event.preventDefault();
    }
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
      const vertical = dy > 0 && dy > Math.abs(dx) * 1.1;
      const fastDismiss = dy >= SHEET_FAST_DISMISS_MIN_Y && start.velocityY >= SHEET_FAST_DISMISS_VELOCITY;
      if (vertical && (dy >= SWIPE_DOWN_MIN_Y || fastDismiss) && animateSheetDismiss(start)) {
        suppressNextClick(touch);
        event.stopPropagation();
        event.preventDefault();
      } else if (start.dragging) {
        animateSheetBack(start);
        event.stopPropagation();
        event.preventDefault();
      } else {
        restoreSheetInlineStyles(start);
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

    if (topSheetOpen()) {
      if (dx > 0 && closeTopSheet()) {
        suppressNextClick(touch);
        event.stopPropagation();
        event.preventDefault();
      }
      return;
    }

    if (libraryDocumentOpen && app.classList.contains("chat-active")) return;
    if (app.classList.contains("library-active")) return;

    const changed = dx < 0 ? switchView(1) : switchView(-1);
    if (changed) {
      suppressNextClick(touch);
      event.stopPropagation();
      event.preventDefault();
    }
  }, {capture: true, passive: false});

  document.addEventListener("touchcancel", () => {
    gesture = null;
    if (sheetGesture?.dragging) animateSheetBack(sheetGesture);
    else if (sheetGesture) restoreSheetInlineStyles(sheetGesture);
    sheetGesture = null;
  }, {capture: true, passive: true});

  document.addEventListener("wheel", event => {
    if ((modalOpen() && !topSheetOpen()) || blockedGestureTarget(event.target)) return;
    if (Math.abs(event.deltaX) <= Math.abs(event.deltaY) * 1.1) return;

    event.preventDefault();
    wheelX += event.deltaX;
    resetWheelSoon();
    if (Math.abs(wheelX) < WHEEL_MIN_X) return;

    if (topSheetOpen()) {
      if (wheelX < 0) closeTopSheet();
      wheelX = 0;
      return;
    }

    if (libraryDocumentOpen && app.classList.contains("chat-active")) {
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
