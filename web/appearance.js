(() => {
  "use strict";

  const STORAGE_KEY = "personal-secretary:appearance-theme";
  const THEMES = new Set(["auto", "light", "dark"]);
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  let selectedTheme = "auto";
  let saveBusy = false;

  function normalize(value) {
    const theme = String(value || "").trim().toLowerCase();
    return THEMES.has(theme) ? theme : "auto";
  }

  function resolvedTheme(theme = selectedTheme) {
    return theme === "auto" ? (media.matches ? "dark" : "light") : theme;
  }

  function updateMetaColor(resolved) {
    let meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) {
      meta = document.createElement("meta");
      meta.name = "theme-color";
      document.head.appendChild(meta);
    }
    meta.content = resolved === "dark" ? "#111214" : "#f7f7f5";
  }

  function updateSelection() {
    document.querySelectorAll("[data-appearance-choice]").forEach(button => {
      const active = button.dataset.appearanceChoice === selectedTheme;
      button.classList.toggle("active", active);
      button.setAttribute("aria-checked", active ? "true" : "false");
      const mark = button.querySelector(".appearance-choice-mark");
      if (mark) mark.textContent = active ? "✓" : "";
    });
  }

  function apply(theme, {persist = true} = {}) {
    selectedTheme = normalize(theme);
    const resolved = resolvedTheme();
    const root = document.documentElement;
    root.dataset.appearance = selectedTheme;
    root.dataset.colorScheme = resolved;
    root.style.colorScheme = resolved;
    updateMetaColor(resolved);
    if (persist) {
      try { localStorage.setItem(STORAGE_KEY, selectedTheme); } catch (_) {}
    }
    updateSelection();
    document.dispatchEvent(new CustomEvent("planner-appearance-change", {
      detail: {theme: selectedTheme, resolved},
    }));
  }

  function renderSettings() {
    const body = document.querySelector('#settingsTheme-appearance .settings-theme-body');
    if (!body) return false;
    if (!document.getElementById("appearanceThemeControl")) {
      const group = document.createElement("div");
      group.id = "appearanceThemeControl";
      group.className = "appearance-settings";
      group.innerHTML = `
        <div class="appearance-choice-list" role="radiogroup" aria-label="Тема приложения">
          <button type="button" class="appearance-choice" data-appearance-choice="auto" role="radio">
            <span class="appearance-preview appearance-preview-auto" aria-hidden="true"><i></i><i></i></span>
            <span class="appearance-choice-copy"><strong>Авто</strong></span>
            <span class="appearance-choice-mark" aria-hidden="true"></span>
          </button>
          <button type="button" class="appearance-choice" data-appearance-choice="light" role="radio">
            <span class="appearance-preview appearance-preview-light" aria-hidden="true"></span>
            <span class="appearance-choice-copy"><strong>Светлая</strong></span>
            <span class="appearance-choice-mark" aria-hidden="true"></span>
          </button>
          <button type="button" class="appearance-choice" data-appearance-choice="dark" role="radio">
            <span class="appearance-preview appearance-preview-dark" aria-hidden="true"></span>
            <span class="appearance-choice-copy"><strong>Тёмная</strong></span>
            <span class="appearance-choice-mark" aria-hidden="true"></span>
          </button>
        </div>
        <div id="appearanceThemeStatus" class="appearance-status" aria-live="polite"></div>`;
      body.appendChild(group);
      group.querySelectorAll("[data-appearance-choice]").forEach(button => {
        button.addEventListener("click", async () => {
          const next = normalize(button.dataset.appearanceChoice);
          if (saveBusy || next === selectedTheme) return;
          const previous = selectedTheme;
          const status = document.getElementById("appearanceThemeStatus");
          apply(next);
          if (status) status.textContent = "Сохраняю…";
          saveBusy = true;
          try {
            const request = window.PlannerRequests?.request;
            if (!request) throw new Error("settings_unavailable");
            const response = await request("/api/settings", {
              method: "POST",
              body: JSON.stringify({appearance_theme: next}),
            });
            apply(response.appearance_theme || next);
            if (status) status.textContent = "";
          } catch (error) {
            apply(previous);
            if (status) status.textContent = "Не удалось сохранить оформление.";
            console.warn("Appearance setting save failed", error);
          } finally {
            saveBusy = false;
          }
        });
      });
    }
    const menuButton = document.querySelector('[data-settings-open="appearance"]');
    if (menuButton) menuButton.hidden = false;
    updateSelection();
    return true;
  }

  async function syncFromServer() {
    try {
      const status = await window.PlannerRequests?.status?.();
      if (!status) return;
      apply(normalize(status.appearance_theme));
    } catch (_) {
      // Local preference remains usable when status is temporarily unavailable.
    }
  }

  media.addEventListener?.("change", () => {
    if (selectedTheme === "auto") apply("auto", {persist: false});
  });

  document.addEventListener("planner-ready", () => {
    renderSettings();
    syncFromServer();
  });
  document.addEventListener("planner-settings-view", event => {
    if (event.detail?.view === "appearance") renderSettings();
  });

  try {
    selectedTheme = normalize(localStorage.getItem(STORAGE_KEY));
  } catch (_) {
    selectedTheme = normalize(document.documentElement.dataset.appearance);
  }
  apply(selectedTheme, {persist: false});
  requestAnimationFrame(renderSettings);
  window.setTimeout(() => {
    renderSettings();
    syncFromServer();
  }, 0);

  window.PlannerAppearance = {
    apply,
    get theme() { return selectedTheme; },
    get resolved() { return resolvedTheme(); },
  };
})();