(() => {
  "use strict";

  const app = document.getElementById("app");
  const nav = document.getElementById("mobileBottomNav");
  if (!app || !nav || window.__plannerSwipeNavigation) return;
  window.__plannerSwipeNavigation = true;

  const VIEW_ORDER = ["home", "chat", "today", "more"];
  const SWIPE_MIN_X = 64;
  const WHEEL_MIN_X = 90;
  const DIRECTION_RATIO = 1.25;
  const WHEEL_RESET_MS = 180;
  let gesture = null;
  let libraryDocumentOpen = false;
  let suppressClickUntil = 0;
  let wheelX = 0;
  let wheelTimer = null;

  function modalOpen() {
    return Boolean(
      document.getElementById("login")?.classList.contains("open") ||
      document.getElementById("settingsPanel")?.classList.contains("open") ||
      document.querySelector(".reminder-snooze-backdrop.open")
    );
  }

  function activeBackSheet() {
    return document.querySelector(
      "#mobileSheetBackdrop.open, #noteWindowBackdrop.open, #reminderEditBackdrop.open"
    );
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

  function closeTopSheet() {
    const backdrop = activeBackSheet();
    if (!backdrop) return false;
    backdrop.dispatchEvent(new MouseEvent("click", {bubbles: true}));
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
    if (modalOpen() || event.touches.length !== 1 || blockedGestureTarget(event.target)) {
      gesture = null;
      return;
    }
    const touch = event.touches[0];
    gesture = {x: touch.clientX, y: touch.clientY};
  }, {capture: true, passive: true});

  document.addEventListener("touchend", event => {
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

  document.addEventListener("wheel", event => {
    if (modalOpen() || blockedGestureTarget(event.target)) return;
    if (Math.abs(event.deltaX) <= Math.abs(event.deltaY) * 1.1) return;

    if (libraryDocumentOpen && app.classList.contains("chat-active")) return;

    event.preventDefault();
    wheelX += event.deltaX;
    resetWheelSoon();
    if (Math.abs(wheelX) < WHEEL_MIN_X) return;

    if (topSheetOpen()) {
      if (wheelX < 0 && closeTopSheet()) suppressNextClick();
      wheelX = 0;
      return;
    }

    if (app.classList.contains("library-active")) {
      wheelX = 0;
      return;
    }

    const changed = wheelX > 0 ? switchView(1) : switchView(-1);
    if (changed) suppressNextClick();
    wheelX = 0;
  }, {capture: true, passive: false});
})();
