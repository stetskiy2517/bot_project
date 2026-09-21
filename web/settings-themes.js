(() => {
  "use strict";

  const panel = document.getElementById("settingsPanel");
  const sheet = panel?.querySelector(".sheet");
  const assistantRoot = document.getElementById("assistantSettings");
  const sheetHead = sheet?.querySelector(".sheet-head");
  const sheetTitle = sheetHead?.querySelector("h2");
  const closeButton = document.getElementById("closeSettings");
  if (!panel || !sheet || !assistantRoot || !sheetHead || !sheetTitle) return;

  const themes = [
    {
      key: "planning",
      title: "Планирование",
      note: "Календарь, маршруты и категории",
    },
    {
      key: "notifications",
      title: "Уведомления",
      note: "Push, обзоры и тихие часы",
    },
    {
      key: "assistant",
      title: "Ассистент",
      note: "ИИ, память и быстрые команды",
    },
    {
      key: "integrations",
      title: "Интеграции",
      note: "Почта и внешние сервисы",
    },
    {
      key: "account",
      title: "Аккаунт и данные",
      note: "Экспорт, удаление и выход",
    },
  ];

  let activeTheme = "";
  let touchStart = null;
  let wheelX = 0;
  let wheelTimer = null;

  function definitionFor(key) {
    return themes.find(item => item.key === key) || null;
  }

  function ensureBackButton() {
    let button = document.getElementById("settingsThemeBack");
    if (button) return button;

    button = document.createElement("button");
    button.id = "settingsThemeBack";
    button.className = "settings-theme-back";
    button.type = "button";
    button.setAttribute("aria-label", "Назад в аккаунт");
    button.title = "Назад";
    button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 5-7 7 7 7" /></svg>';
    button.hidden = true;
    button.addEventListener("click", () => showHome());
    sheetHead.insertBefore(button, sheetTitle);
    return button;
  }

  const backButton = ensureBackButton();

  function ensureRoot() {
    let root = document.getElementById("settingsThemes");
    if (root) {
      if (root.parentElement !== assistantRoot) assistantRoot.prepend(root);
      return root;
    }

    root = document.createElement("div");
    root.id = "settingsThemes";
    root.className = "settings-themes-menu";
    root.setAttribute("aria-label", "Разделы аккаунта");
    assistantRoot.prepend(root);
    return root;
  }

  function ensureMenuButton(definition) {
    const root = ensureRoot();
    let button = root.querySelector(`[data-settings-open="${definition.key}"]`);
    if (button) return button;

    button = document.createElement("button");
    button.type = "button";
    button.className = "settings-theme-link";
    button.dataset.settingsOpen = definition.key;
    button.setAttribute("aria-controls", `settingsTheme-${definition.key}`);
    button.innerHTML = `
      <span class="settings-theme-link-main">
        <span class="settings-theme-link-title">${definition.title}</span>
        <span class="settings-theme-link-note">${definition.note}</span>
      </span>
      <svg class="settings-theme-link-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 7 7-7 7" /></svg>`;
    button.addEventListener("click", () => openTheme(definition.key));
    root.appendChild(button);
    return button;
  }

  function ensureTheme(definition) {
    let section = document.getElementById(`settingsTheme-${definition.key}`);
    if (section) return section;

    section = document.createElement("section");
    section.id = `settingsTheme-${definition.key}`;
    section.className = "settings-theme-screen";
    section.dataset.settingsTheme = definition.key;
    section.hidden = true;
    section.innerHTML = '<div class="settings-theme-body"></div>';
    assistantRoot.appendChild(section);
    return section;
  }

  function themeFor(group) {
    const id = group.id || "";
    if (["calendarSettingsGroup", "navigationGroup", "categoryColorsGroup"].includes(id)) {
      return "planning";
    }
    if (id === "notificationsGroup" || group.querySelector("#assistantDeliveryFields")) {
      return "notifications";
    }
    if (id === "emailGroup") return "integrations";
    if (
      group.querySelector("#proactiveRemindersEnabled") ||
      group.querySelector("#assistantMemoryList") ||
      group.querySelector("#templateForm") ||
      group.querySelector("#assistantTemplates")
    ) {
      return "assistant";
    }
    if (
      group.querySelector("#undoNoteAction") ||
      group.querySelector("#exportAccount") ||
      group.querySelector("#eraseAccount")
    ) {
      return "account";
    }
    return "";
  }

  function themeBody(key) {
    return document.querySelector(`#settingsTheme-${key} .settings-theme-body`);
  }

  function placeLegacyCalendar() {
    const workStart = document.getElementById("workStart");
    const planning = themeBody("planning");
    if (!workStart || !planning) return;
    if (workStart.closest("details.settings-group")) return;

    const grid = workStart.closest(".grid");
    if (!grid) return;
    const title = grid.previousElementSibling?.classList.contains("section-title")
      ? grid.previousElementSibling
      : null;
    if (title && title.parentElement !== planning) planning.prepend(title);
    if (grid.parentElement !== planning) {
      if (title && title.parentElement === planning) title.insertAdjacentElement("afterend", grid);
      else planning.prepend(grid);
    }
  }

  function placeActions() {
    const logout = document.getElementById("logout");
    const account = themeBody("account");

    if (logout && account) {
      let actions = document.getElementById("accountThemeActions");
      if (!actions) {
        actions = document.createElement("div");
        actions.id = "accountThemeActions";
        actions.className = "settings-theme-actions";
        account.appendChild(actions);
      }
      if (logout.parentElement !== actions) actions.appendChild(logout);
    }

    const oldActions = sheet.querySelector(":scope > .sheet-actions");
    if (oldActions) oldActions.hidden = oldActions.children.length === 0;
  }

  function sectionAvailable(definition) {
    const section = document.getElementById(`settingsTheme-${definition.key}`);
    const body = section?.querySelector(".settings-theme-body");
    if (!body) return false;
    return Boolean(body.children.length);
  }

  function updateAvailability() {
    for (const definition of themes) {
      const available = sectionAvailable(definition);
      const section = document.getElementById(`settingsTheme-${definition.key}`);
      const button = document.querySelector(`[data-settings-open="${definition.key}"]`);
      if (button) button.hidden = !available;
      if (section) section.hidden = !available || activeTheme !== definition.key;
    }
  }

  function openTheme(key, {focus = true} = {}) {
    const definition = definitionFor(key);
    if (!definition || !sectionAvailable(definition)) return false;

    activeTheme = key;
    ensureRoot().hidden = true;
    sheet.classList.add("settings-detail-open");
    sheet.dataset.settingsView = key;
    sheetTitle.textContent = definition.title;
    backButton.hidden = false;
    updateAvailability();
    sheet.scrollTop = 0;
    if (focus) requestAnimationFrame(() => backButton.focus({preventScroll: true}));
    panel.dispatchEvent(new CustomEvent("planner-settings-view", {detail: {view: key}}));
    return true;
  }

  function showHome({focus = false} = {}) {
    activeTheme = "";
    const root = ensureRoot();
    root.hidden = false;
    sheet.classList.remove("settings-detail-open");
    delete sheet.dataset.settingsView;
    sheetTitle.textContent = "Аккаунт";
    backButton.hidden = true;
    updateAvailability();
    sheet.scrollTop = 0;
    if (focus) closeButton?.focus({preventScroll: true});
    panel.dispatchEvent(new CustomEvent("planner-settings-view", {detail: {view: "home"}}));
  }

  function organize() {
    themes.forEach(definition => {
      ensureMenuButton(definition);
      ensureTheme(definition);
    });

    const groups = Array.from(sheet.querySelectorAll("details.settings-group"));
    for (const group of groups) {
      const key = themeFor(group);
      const body = key ? themeBody(key) : null;
      if (body && group.parentElement !== body) body.appendChild(group);
    }

    placeLegacyCalendar();
    placeActions();
    updateAvailability();
  }

  function blockedSwipeTarget(target) {
    return Boolean(target?.closest("input, textarea, select, button, summary, label"));
  }

  panel.addEventListener("touchstart", event => {
    if (!activeTheme || event.touches.length !== 1 || blockedSwipeTarget(event.target)) {
      touchStart = null;
      return;
    }
    const touch = event.touches[0];
    touchStart = {x: touch.clientX, y: touch.clientY};
  }, {passive: true});

  panel.addEventListener("touchend", event => {
    if (!activeTheme || !touchStart || event.changedTouches.length !== 1) {
      touchStart = null;
      return;
    }
    const start = touchStart;
    touchStart = null;
    const touch = event.changedTouches[0];
    const dx = touch.clientX - start.x;
    const dy = touch.clientY - start.y;
    if (dx > 64 && Math.abs(dx) > Math.abs(dy) * 1.25) {
      showHome();
      event.preventDefault();
    }
  }, {passive: false});

  panel.addEventListener("wheel", event => {
    if (!activeTheme || blockedSwipeTarget(event.target)) return;
    if (Math.abs(event.deltaX) <= Math.abs(event.deltaY) * 1.1) return;
    event.preventDefault();
    wheelX += event.deltaX;
    clearTimeout(wheelTimer);
    wheelTimer = setTimeout(() => {
      wheelX = 0;
      wheelTimer = null;
    }, 180);
    if (wheelX <= -90) {
      wheelX = 0;
      showHome();
    }
  }, {passive: false});

  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && panel.classList.contains("open") && activeTheme) {
      event.preventDefault();
      event.stopPropagation();
      showHome();
    }
  }, true);

  const style = document.createElement("style");
  style.id = "settingsThemesStyle";
  style.textContent = `
    #settingsPanel .sheet-head h2{flex:1;min-width:0}
    #settingsPanel .settings-theme-back{width:34px;height:34px;border-radius:50%;background:#f1f1ef;display:grid;place-items:center;cursor:pointer;flex:0 0 auto;margin-right:8px}
    #settingsPanel .settings-theme-back[hidden]{display:none}
    #settingsPanel .settings-theme-back svg{width:18px;height:18px;fill:none;stroke:#555;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round}
    #settingsPanel .settings-themes-menu{display:grid;gap:1px;margin-top:18px;border:1px solid #ececea;border-radius:16px;overflow:hidden;background:#ececea}
    #settingsPanel .settings-themes-menu[hidden]{display:none}
    #settingsPanel .settings-theme-link{width:100%;min-height:62px;padding:12px 14px;background:#fff;display:flex;align-items:center;gap:12px;text-align:left;cursor:pointer}
    #settingsPanel .settings-theme-link[hidden]{display:none}
    #settingsPanel .settings-theme-link-main{display:grid;gap:3px;min-width:0;flex:1}
    #settingsPanel .settings-theme-link-title{font-size:15px;font-weight:600;color:#111}
    #settingsPanel .settings-theme-link-note{font-size:12px;color:#92928e;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    #settingsPanel .settings-theme-link-chevron{width:18px;height:18px;fill:none;stroke:#8d8d88;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;flex:0 0 auto}
    #settingsPanel .settings-theme-screen[hidden]{display:none}
    #settingsPanel .settings-theme-body{display:grid;gap:8px;margin-top:8px}
    #settingsPanel .settings-theme-body>.settings-group{margin-top:0!important}
    #settingsPanel .settings-theme-body>.section-title{margin-top:8px}
    #settingsPanel .settings-theme-actions{display:grid;gap:8px;margin-top:2px}
    #settingsPanel .settings-theme-actions>.action{width:100%}
    #settingsPanel .sheet-actions[hidden]{display:none}
    #settingsPanel .sheet.settings-detail-open>.account-card,
    #settingsPanel .sheet.settings-detail-open>#onboardingNotice{display:none!important}
    @media(max-width:430px){#settingsPanel .settings-theme-link{min-height:60px}}
  `;
  document.head.appendChild(style);

  let queued = false;
  const observer = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      organize();
    });
  });
  observer.observe(sheet, {childList: true, subtree: true, attributes: true, attributeFilter: ["class"]});

  const panelObserver = new MutationObserver(() => {
    if (!panel.classList.contains("open") && activeTheme) showHome();
    if (panel.classList.contains("open") && !activeTheme) {
      sheetTitle.textContent = "Аккаунт";
      backButton.hidden = true;
    }
  });
  panelObserver.observe(panel, {attributes: true, attributeFilter: ["class"]});

  document.addEventListener("planner-ready", () => requestAnimationFrame(organize));

  window.PlannerSettingsThemes = {
    open: openTheme,
    back: showHome,
    get active() { return activeTheme || "home"; },
  };

  organize();
  showHome();
})();
