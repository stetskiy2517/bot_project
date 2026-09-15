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