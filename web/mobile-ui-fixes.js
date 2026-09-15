(() => {
  "use strict";

  const app = document.getElementById("app");
  const settingsPanel = document.getElementById("settingsPanel");
  if (!app || !settingsPanel) return;

  let chatGraceUntil = 0;
  let restoringChat = false;

  function chatButton() {
    return document.querySelector('#mobileBottomNav [data-view="chat"]');
  }

  function keepChatFor(ms = 12000) {
    chatGraceUntil = Math.max(chatGraceUntil, Date.now() + ms);
  }

  document.addEventListener("planner-ready", () => {
    const hadChat = app.classList.contains("chat-active");
    if (!hadChat) return;
    keepChatFor();
    queueMicrotask(() => {
      const button = chatButton();
      if (button && !button.classList.contains("active")) button.click();
    });
  }, true);

  document.addEventListener("planner-result", () => keepChatFor(), true);

  const appObserver = new MutationObserver(() => {
    const active = app.classList.contains("chat-active");
    if (active) {
      keepChatFor();
      restoringChat = false;
      return;
    }
    if (Date.now() >= chatGraceUntil || restoringChat) return;
    const button = chatButton();
    if (!button) return;
    restoringChat = true;
    queueMicrotask(() => {
      try {
        if (Date.now() < chatGraceUntil) button.click();
      } finally {
        restoringChat = false;
      }
    });
  });
  appObserver.observe(app, {attributes: true, attributeFilter: ["class"]});

  document.getElementById("mobileBottomNav")?.addEventListener("pointerdown", event => {
    const button = event.target.closest("[data-view]");
    if (button && button.dataset.view !== "chat") chatGraceUntil = 0;
  }, true);

  function setSummaryLabel(summary, label) {
    if (!summary) return;
    const textNode = Array.from(summary.childNodes).find(node => node.nodeType === Node.TEXT_NODE);
    if (textNode) textNode.textContent = label + " ";
    else summary.prepend(document.createTextNode(label + " "));
  }

  function normalizeSettingsLabels() {
    const root = document.getElementById("assistantSettings");
    if (!root) return;
    const duplicates = Array.from(root.querySelectorAll("details")).filter(details => {
      const summary = details.querySelector(":scope > summary");
      return summary?.textContent?.trim().startsWith("Отмена и данные");
    });
    if (duplicates.length <= 1) return;
    for (const details of duplicates) {
      if (details.querySelector("#undoNoteAction")) continue;
      setSummaryLabel(details.querySelector(":scope > summary"), "Данные аккаунта");
    }
  }

  function stabilizeAssistantDetails() {
    const root = document.getElementById("assistantSettings");
    if (!root) return;
    normalizeSettingsLabels();
    for (const details of root.querySelectorAll("details")) {
      if (details.dataset.mobileStable === "1") continue;
      details.dataset.mobileStable = "1";
      const summary = details.querySelector(":scope > summary");
      if (!summary) continue;
      summary.addEventListener("pointerdown", () => {
        if (details.open) delete details.dataset.mobilePinned;
        else details.dataset.mobilePinned = "1";
      }, true);
      details.addEventListener("toggle", () => {
        if (details.dataset.mobilePinned === "1" && !details.open) {
          queueMicrotask(() => {
            if (details.dataset.mobilePinned === "1") details.open = true;
          });
        }
      });
    }
  }

  stabilizeAssistantDetails();
  const settingsObserver = new MutationObserver(stabilizeAssistantDetails);
  settingsObserver.observe(settingsPanel, {childList: true, subtree: true});
  document.addEventListener("planner-ready", stabilizeAssistantDetails);
})();

(() => {
  "use strict";

  const app = document.getElementById("app");
  const form = document.getElementById("composer");
  const backing = document.getElementById("message");
  const root = document.documentElement;
  if (!app || !form || !backing || backing.tagName !== "INPUT") return;

  const style = document.createElement("style");
  style.id = "mobileComposerFix";
  style.textContent = `
    #composer textarea#message {
      flex: 1;
      min-width: 0;
      min-height: 36px;
      max-height: min(40dvh, 220px);
      margin: 0;
      border: 0;
      outline: 0;
      resize: none;
      overflow-y: hidden;
      background: transparent;
      padding: 8px 2px;
      color: #111;
      font: inherit;
      font-size: 16px;
      line-height: 1.35;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    @media (max-width: 759px), (pointer: coarse) {
      .app.mobile-shell.mobile-view-chat .composer-wrap {
        bottom: calc(env(safe-area-inset-bottom) + var(--mobile-nav-height)) !important;
      }
      :root.composer-keyboard-open .app.mobile-shell.mobile-view-chat .composer-wrap {
        bottom: var(--keyboard-inset, 0px) !important;
      }
      .app.mobile-shell.mobile-view-chat .chat-shell {
        bottom: calc(
          env(safe-area-inset-bottom) + var(--mobile-nav-height) + var(--mobile-composer-height, 58px)
        ) !important;
      }
      :root.composer-keyboard-open .app.mobile-shell.mobile-view-chat .chat-shell {
        bottom: calc(var(--keyboard-inset, 0px) + var(--mobile-composer-height, 58px)) !important;
      }
      .app.mobile-shell.mobile-view-chat .chat {
        padding-bottom: 16px !important;
      }
    }
  `;
  document.head.appendChild(style);

  const nativeValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
  const initialValue = nativeValue?.get ? nativeValue.get.call(backing) : backing.value;
  backing.id = "messageBacking";
  backing.setAttribute("aria-hidden", "true");
  backing.tabIndex = -1;
  backing.style.display = "none";

  const editor = document.createElement("textarea");
  editor.id = "message";
  editor.rows = 1;
  editor.autocomplete = backing.autocomplete || "off";
  editor.placeholder = backing.placeholder || "Сообщение…";
  editor.setAttribute("aria-label", backing.getAttribute("aria-label") || "Сообщение");
  editor.value = initialValue || "";
  backing.parentNode.insertBefore(editor, backing);

  let sizingFrame = 0;
  function syncComposerHeight() {
    sizingFrame = 0;
    const wrap = form.closest(".composer-wrap");
    if (!wrap) return;
    const height = Math.max(52, Math.ceil(wrap.getBoundingClientRect().height || 0));
    root.style.setProperty("--mobile-composer-height", height + "px");
    const chat = document.getElementById("chat");
    if (chat) chat.scrollTop = chat.scrollHeight;
  }

  function autosize() {
    editor.style.height = "0px";
    const viewportHeight = Math.max(240, Number(window.visualViewport?.height || window.innerHeight || 0));
    const limit = Math.max(96, Math.min(220, Math.round(viewportHeight * 0.4)));
    const wanted = Math.max(36, editor.scrollHeight || 36);
    const height = Math.min(wanted, limit);
    editor.style.height = height + "px";
    editor.style.overflowY = wanted > limit ? "auto" : "hidden";
    if (sizingFrame) cancelAnimationFrame(sizingFrame);
    sizingFrame = requestAnimationFrame(syncComposerHeight);
  }

  if (nativeValue?.get && nativeValue?.set) {
    Object.defineProperty(backing, "value", {
      configurable: true,
      get() {
        return nativeValue.get.call(backing);
      },
      set(value) {
        const text = String(value ?? "");
        nativeValue.set.call(backing, text);
        if (editor.value !== text) editor.value = text;
        autosize();
      },
    });
  }

  const nativeFocus = backing.focus.bind(backing);
  const nativeBlur = backing.blur.bind(backing);
  backing.focus = options => editor.focus(options);
  backing.blur = () => editor.blur();
  backing.dataset.nativeFocusAvailable = String(Boolean(nativeFocus && nativeBlur));

  editor.addEventListener("input", () => {
    if (nativeValue?.set) nativeValue.set.call(backing, editor.value);
    else backing.value = editor.value;
    backing.dispatchEvent(new Event("input", {bubbles: true}));
    autosize();
  });
  editor.addEventListener("focus", () => {
    backing.dispatchEvent(new Event("focus"));
    scheduleKeyboardSync();
  });
  editor.addEventListener("blur", () => {
    backing.dispatchEvent(new Event("blur"));
    setTimeout(scheduleKeyboardSync, 80);
  });
  editor.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  let stableHeight = Math.max(
    window.innerHeight || 0,
    Number(window.visualViewport?.height || 0),
    root.clientHeight || 0,
  );
  let keyboardTimer = 0;

  function fieldFocused() {
    return document.activeElement === editor;
  }

  function visibleViewportHeight() {
    const values = [
      Number(window.innerHeight || 0),
      Number(window.visualViewport?.height || 0),
    ].filter(value => Number.isFinite(value) && value > 0);
    return values.length ? Math.min(...values) : stableHeight;
  }

  function scheduleKeyboardSync() {
    if (keyboardTimer) clearTimeout(keyboardTimer);
    const sync = () => {
      const focused = fieldFocused();
      const visibleHeight = visibleViewportHeight();
      if (!focused) {
        stableHeight = Math.max(
          stableHeight,
          window.innerHeight || 0,
          Number(window.visualViewport?.height || 0),
          root.clientHeight || 0,
        );
      }
      const loss = Math.max(0, stableHeight - visibleHeight);
      const existing = Math.max(0, Number.parseFloat(root.style.getPropertyValue("--keyboard-inset")) || 0);
      const open = focused && (loss > 100 || existing > 100);
      const inset = open ? Math.max(loss, existing) : 0;
      root.style.setProperty("--keyboard-inset", Math.round(inset) + "px");
      root.classList.toggle("composer-keyboard-open", open);
      autosize();
    };
    sync();
    requestAnimationFrame(sync);
    keyboardTimer = setTimeout(sync, 120);
  }

  window.addEventListener("resize", scheduleKeyboardSync, {passive: true});
  window.visualViewport?.addEventListener("resize", scheduleKeyboardSync, {passive: true});
  window.visualViewport?.addEventListener("scroll", scheduleKeyboardSync, {passive: true});
  document.addEventListener("focusin", scheduleKeyboardSync);
  document.addEventListener("focusout", () => setTimeout(scheduleKeyboardSync, 80));

  const PENDING_LOCATION_KEY = "secretary-pending-calendar-location";
  let pendingLocation = null;
  try {
    const stored = JSON.parse(sessionStorage.getItem(PENDING_LOCATION_KEY) || "null");
    if (stored && stored.event_id && Date.now() - Number(stored.saved_at || 0) < 30 * 60 * 1000) {
      pendingLocation = stored;
    } else {
      sessionStorage.removeItem(PENDING_LOCATION_KEY);
    }
  } catch (_) {
    sessionStorage.removeItem(PENDING_LOCATION_KEY);
  }

  function appendChatMessage(text, role = "assistant") {
    const chat = document.getElementById("chat");
    if (!chat) return null;
    const row = document.createElement("div");
    row.className = "msg " + role;
    row.textContent = String(text || "");
    chat.appendChild(row);
    chat.scrollTop = chat.scrollHeight;
    return row;
  }

  function rememberPendingLocation(value) {
    pendingLocation = value ? {...value, saved_at: Date.now()} : null;
    try {
      if (pendingLocation) sessionStorage.setItem(PENDING_LOCATION_KEY, JSON.stringify(pendingLocation));
      else sessionStorage.removeItem(PENDING_LOCATION_KEY);
    } catch (_) {}
  }

  function createdEventTitle(result) {
    const replies = Array.isArray(result?.replies) ? result.replies : [];
    for (let index = replies.length - 1; index >= 0; index -= 1) {
      const match = String(replies[index] || "").match(/Событие\s+«([^»]{1,200})»\s+(?:добавлено|создано)/i);
      if (match) return match[1].trim();
    }
    return "";
  }

  async function requestMissingLocation(result) {
    if (pendingLocation || typeof window.api !== "function") return;
    const title = createdEventTitle(result);
    if (!title) return;
    try {
      const data = await window.api(`/api/mobile/calendar-location?title=${encodeURIComponent(title)}`);
      if (!data?.request?.event_id) return;
      rememberPendingLocation(data.request);
      appendChatMessage(
        `Где будет «${data.request.title || title}»? Напиши адрес или место. Если дорога не нужна — «без трансфера».`,
      );
      editor.focus();
    } catch (_) {}
  }

  document.addEventListener("planner-result", event => {
    requestMissingLocation(event.detail).catch(() => {});
  });

  const originalSubmit = form.onsubmit;
  const skipLocation = new Set([
    "без адреса",
    "без места",
    "без геолокации",
    "без локации",
    "без трансфера",
    "дорога не нужна",
    "не нужна дорога",
    "онлайн",
  ]);

  form.onsubmit = async event => {
    if (!pendingLocation) {
      if (typeof originalSubmit === "function") return originalSubmit.call(form, event);
      event.preventDefault();
      return window.submitPlannerText?.(backing.value.trim());
    }

    event.preventDefault();
    const text = editor.value.trim();
    if (!text || typeof window.api !== "function") return;
    appendChatMessage(text, "user");
    backing.value = "";
    const normalized = text.toLowerCase().replaceAll("ё", "е").replace(/\s+/g, " ").trim();
    const current = pendingLocation;
    try {
      const result = await window.api(`/api/mobile/calendar-location/${encodeURIComponent(current.event_id)}`, {
        method: "POST",
        body: JSON.stringify(skipLocation.has(normalized) ? {skip: true} : {location: text}),
      });
      rememberPendingLocation(null);
      if (result.skipped) {
        appendChatMessage("Ок. Оставил событие без места и маршрута.");
      } else {
        appendChatMessage(`Место добавлено: ${result.location}. Если нужна дорога, помощник уточнит точку выезда.`);
      }
      document.dispatchEvent(new Event("planner-library-changed"));
    } catch (error) {
      appendChatMessage(error.message || "Не удалось сохранить место события.");
      backing.value = text;
      editor.focus();
    }
  };

  autosize();
  scheduleKeyboardSync();
  if (pendingLocation) {
    appendChatMessage(
      `Где будет «${pendingLocation.title || "событие"}»? Напиши адрес или место. Если дорога не нужна — «без трансфера».`,
    );
  }
})();