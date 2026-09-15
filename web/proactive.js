(() => {
  const root = document.getElementById("assistantSettings");
  const settingsPanel = document.getElementById("settingsPanel");
  if (!root || !settingsPanel || typeof api !== "function") return;

  const section = document.createElement("details");
  section.className = "assistant-section";
  section.innerHTML = `
    <summary>Проактивный помощник</summary>
    <p class="settings-help">Если включить, секретарь может сам создать повторяющееся напоминание только по явно указанной привычке с точным временем и высокой уверенностью. Календарь, заметки и другие действия автоматически не меняются.</p>
    <label class="field">Автоматические напоминания <input id="proactiveRemindersEnabled" type="checkbox"></label>
    <button class="action" type="button" id="saveProactiveReminders">Сохранить</button>
    <p id="proactiveState" class="settings-help"></p>`;
  root.insertBefore(section, root.lastElementChild);

  const toggle = document.getElementById("proactiveRemindersEnabled");
  const save = document.getElementById("saveProactiveReminders");
  const state = document.getElementById("proactiveState");
  let loading = false;

  function describe(action) {
    if (!action) return "Автоматических действий пока не было.";
    const labels = {created: "создано напоминание", covered: "напоминание уже было", not_actionable: "действие не требовалось", failed: "ошибка создания"};
    return `Последнее решение: ${labels[action.status] || action.status}. ${action.reason || ""}`.trim();
  }

  async function load() {
    if (loading) return;
    loading = true;
    try {
      const data = await api("/api/assistant");
      toggle.checked = Boolean(data.preferences?.proactive_reminders_enabled);
      state.textContent = describe(data.proactive?.last_action);
    } catch (error) {
      state.textContent = error.message;
    } finally {
      loading = false;
    }
  }

  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      await api("/api/assistant/preferences", {
        method: "POST",
        body: JSON.stringify({proactive_reminders_enabled: toggle.checked}),
      });
      state.textContent = toggle.checked
        ? "Включено. Секретарь будет проверять только безопасные привычки с точным расписанием."
        : "Выключено. Новые автоматические напоминания создаваться не будут.";
    } catch (error) {
      state.textContent = error.message;
    } finally {
      save.disabled = false;
    }
  });

  new MutationObserver(() => {
    if (settingsPanel.classList.contains("open")) load();
  }).observe(settingsPanel, {attributes: true, attributeFilter: ["class"]});
})();
