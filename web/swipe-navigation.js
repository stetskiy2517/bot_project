(() => {
  "use strict";

  const app = document.getElementById("app");
  const nav = document.getElementById("mobileBottomNav");
  if (!app || !nav || window.__plannerSwipeNavigation) return;
  window.__plannerSwipeNavigation = true;

  const VIEW_ORDER = ["home", "chat", "today", "more"];
  const SWIPE_MIN_X = 64;
  const DIRECTION_RATIO = 1.25;
  let gesture = null;
  let libraryDocumentOpen = false;
  let suppressClickUntil = 0;

  function modalOpen() {
    return Boolean(
      document.getElementById("login")?.classList.contains("open") ||
      document.getElementById("settingsPanel")?.classList.contains("open") ||
      document.querySelector(".reminder-snooze-backdrop.open")
    );
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
    const backdrop = document.getElementById("mobileSheetBackdrop");
    if (!backdrop?.classList.contains("open")) return false;
    backdrop.dispatchEvent(new MouseEvent("click", {bubbles: true}));
    return true;
  }

  function suppressNextClick() {
    suppressClickUntil = performance.now() + 400;
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
    if (performance.now() >= suppressClickUntil) return;
    event.preventDefault();
    event.stopPropagation();
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

    // Existing library navigation owns note/reminder document back gestures.
    if (libraryDocumentOpen && app.classList.contains("chat-active")) return;

    // Any nested mobile sheet behaves as a screen: swipe right goes one level back.
    if (dx > 0 && closeTopSheet()) {
      suppressNextClick();
      event.stopPropagation();
      event.preventDefault();
      return;
    }

    // The library itself still owns its card-level gestures and its own back stack.
    if (app.classList.contains("library-active")) return;

    // On top-level screens, horizontal swipes follow the exact order of bottom navigation.
    const changed = dx < 0 ? switchView(1) : switchView(-1);
    if (changed) {
      suppressNextClick();
      event.stopPropagation();
      event.preventDefault();
    }
  }, {capture: true, passive: false});
})();
