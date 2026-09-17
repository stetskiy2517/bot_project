(() => {
  "use strict";

  const panel = document.getElementById("settingsPanel");
  const sheet = panel?.querySelector(".sheet");
  if (!panel || !sheet) return;

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

  function ensureRoot() {
    const assistantRoot = document.getElementById("assistantSettings");
    let root = document.getElementById("settingsThemes");
    if (root) {
      if (assistantRoot && root.parentElement !== assistantRoot) assistantRoot.prepend(root);
      return root;
    }

    root = document.createElement("div");
    root.id = "settingsThemes";
    root.className = "settings-themes";
    if (assistantRoot) {
      assistantRoot.prepend(root);
      return root;
    }
    const onboarding = document.getElementById("onboardingNotice");
    if (onboarding) onboarding.insertAdjacentElement("afterend", root);
    else sheet.querySelector(".account-card")?.insertAdjacentElement("afterend", root);
    return root;
  }

  function ensureTheme(definition) {
    const root = ensureRoot();
    let section = document.getElementById(`settingsTheme-${definition.key}`);
    if (section) return section;

    section = document.createElement("section");
    section.id = `settingsTheme-${definition.key}`;
    section.className = "settings-theme";
    section.dataset.settingsTheme = definition.key;
    section.innerHTML = `
      <div class="settings-theme-head">
        <div class="settings-theme-title">${definition.title}</div>
        <div class="settings-theme-note">${definition.note}</div>
      </div>
      <div class="settings-theme-body"></div>`;
    root.appendChild(section);
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

  function placeActions() {
    const save = document.getElementById("saveSettings");
    const logout = document.getElementById("logout");
    const planning = themeBody("planning");
    const account = themeBody("account");

    if (save && planning) {
      let actions = document.getElementById("planningThemeActions");
      if (!actions) {
        actions = document.createElement("div");
        actions.id = "planningThemeActions";
        actions.className = "settings-theme-actions";
        planning.appendChild(actions);
      }
      if (save.parentElement !== actions) actions.appendChild(save);
      save.textContent = "Сохранить планирование";
    }

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

  function updateVisibility() {
    for (const definition of themes) {
      const section = document.getElementById(`settingsTheme-${definition.key}`);
      const body = section?.querySelector(".settings-theme-body");
      if (!section || !body) continue;
      const hasGroup = Boolean(body.querySelector(":scope > details.settings-group"));
      const hasAction = Boolean(body.querySelector(":scope > .settings-theme-actions"));
      section.hidden = !hasGroup && !hasAction;
    }
  }

  function organize() {
    themes.forEach(ensureTheme);
    const groups = Array.from(sheet.querySelectorAll("details.settings-group"));
    for (const group of groups) {
      const key = themeFor(group);
      const body = key ? themeBody(key) : null;
      if (body && group.parentElement !== body) body.appendChild(group);
    }
    placeActions();
    updateVisibility();
  }

  const style = document.createElement("style");
  style.id = "settingsThemesStyle";
  style.textContent = `
    #settingsPanel .settings-themes{margin-top:18px}
    #settingsPanel .settings-theme{margin:0 0 22px}
    #settingsPanel .settings-theme[hidden]{display:none}
    #settingsPanel .settings-theme-head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin:0 2px 8px}
    #settingsPanel .settings-theme-title{font-size:12px;font-weight:700;color:#686864;text-transform:uppercase;letter-spacing:.07em}
    #settingsPanel .settings-theme-note{min-width:0;color:#aaa9a5;font-size:11px;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    #settingsPanel .settings-theme-body{display:grid;gap:8px}
    #settingsPanel .settings-theme-body>.settings-group{margin-top:0!important}
    #settingsPanel .settings-theme-actions{display:grid;gap:8px;margin-top:2px}
    #settingsPanel .settings-theme-actions>.action{width:100%}
    #settingsPanel .sheet-actions[hidden]{display:none}
    @media(max-width:430px){#settingsPanel .settings-theme-note{max-width:52%}}
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

  document.addEventListener("planner-ready", () => requestAnimationFrame(organize));
  organize();
})();
