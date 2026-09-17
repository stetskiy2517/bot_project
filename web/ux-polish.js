(() => {
  "use strict";

  if (window.__plannerUxPolish) return;
  window.__plannerUxPolish = true;
  const CLIENT_BUILD = "prebeta-v14";

  const style = document.createElement("style");
  style.id = "uxPolishStyles";
  style.textContent = `
    .library-empty.polished-empty,.mobile-empty.polished-empty{display:grid;gap:7px;align-content:center}
    .library-empty.polished-empty strong,.mobile-empty.polished-empty strong{color:#555550;font-size:15px;font-weight:650}
    .library-empty.polished-empty span,.mobile-empty.polished-empty span{color:#92928e;font-size:12px;line-height:1.45}
  `;
  document.head.appendChild(style);

  function polishSettings() {
    const accountBody = document.querySelector("#settingsTheme-account .settings-theme-body");
    const diagnostics = document.getElementById("diagnosticsGroup");
    if (accountBody && diagnostics && diagnostics.parentElement !== accountBody) accountBody.appendChild(diagnostics);

    // Keep the existing settings groups as real accordions inside each theme.
    // Turning <details> into permanently open visual sections leaves their
    // <summary> elements in the accessibility tree with broken hit targets.
    document.querySelectorAll("#settingsPanel .settings-theme-body > details.settings-group").forEach(group => {
      group.classList.remove("settings-theme-flat-group");
      delete group.dataset.flatSettingsBound;
    });

    const version = document.getElementById("diagVersion");
    if (version && (!version.textContent?.trim() || ["—", "неизвестна"].includes(version.textContent.trim()))) {
      version.textContent = CLIENT_BUILD;
    }
  }

  function setEmpty(node, title, text) {
    if (!node || node.classList.contains("polished-empty")) return;
    node.classList.add("polished-empty");
    node.replaceChildren();
    const strong = document.createElement("strong");
    strong.textContent = title;
    const span = document.createElement("span");
    span.textContent = text;
    node.append(strong, span);
  }

  function polishEmptyStates() {
    document.querySelectorAll(".library-empty").forEach(node => {
      const text = node.textContent || "";
      if (/замет/i.test(text)) {
        setEmpty(node, "Заметок пока нет", "Сохраните первую заметку голосом или обычным сообщением в чате.");
      } else if (/напомин/i.test(text)) {
        setEmpty(node, "Задач с уведомлением пока нет", "Скажите секретарю: «напомни завтра…» — задача появится в общем списке задач.");
      }
    });

    document.querySelectorAll(".mobile-empty").forEach(node => {
      const text = node.textContent || "";
      if (/критичных задач|задач нет/i.test(text)) {
        setEmpty(node, "Срочных задач нет", "Можно добавить новую задачу или спокойно заняться планом на день.");
      } else if (/событий нет/i.test(text)) {
        setEmpty(node, "В календаре свободно", "Добавьте встречу кнопкой «+» или скажите секретарю голосом.");
      }
    });
  }

  let queued = false;
  function schedule() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      polishSettings();
      polishEmptyStates();
    });
  }

  new MutationObserver(schedule).observe(document.body, {childList: true, subtree: true});
  document.addEventListener("planner-ready", schedule);
  document.addEventListener("planner-settings-changed", schedule);
  schedule();
})();
