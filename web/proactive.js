(() => {
  const root = document.getElementById("assistantSettings");
  const settingsPanel = document.getElementById("settingsPanel");
  if (!root || !settingsPanel || typeof api !== "function") return;

  const section = document.createElement("details");
  section.className = "assistant-section";
  section.innerHTML = `
    <summary>Проактивный помощник</summary>
    <p class="settings-help">Секретарь сам определяет, нужна ли по привычке точечная подсказка или блок времени. Напоминания и события календаря включаются отдельно. Автодействие выполняется только при точном расписании и высокой уверенности.</p>
    <label class="field">Автоматические напоминания <input id="proactiveRemindersEnabled" type="checkbox"></label>
    <label class="field">Автоматические события календаря <input id="proactiveCalendarEnabled" type="checkbox"></label>
    <button class="action" type="button" id="saveProactiveActions">Сохранить</button>
    <p id="proactiveState" class="settings-help"></p>`;
  root.insertBefore(section, root.lastElementChild);

  const reminderToggle = document.getElementById("proactiveRemindersEnabled");
  const calendarToggle = document.getElementById("proactiveCalendarEnabled");
  const save = document.getElementById("saveProactiveActions");
  const state = document.getElementById("proactiveState");
  let loading = false;

  function describe(action) {
    if (!action) return "Автоматических действий пока не было.";
    const noun = action.action_type === "calendar_event" ? "событие календаря" : "напоминание";
    const labels = {
      created: `создано ${noun}`,
      covered: `${noun} уже было`,
      not_actionable: "автодействие не определено",
      failed: "ошибка создания",
    };
    return `Последнее решение: ${labels[action.status] || action.status}. ${action.reason || ""}`.trim();
  }

  async function load() {
    if (loading) return;
    loading = true;
    try {
      const data = await api("/api/assistant");
      reminderToggle.checked = Boolean(data.preferences?.proactive_reminders_enabled);
      calendarToggle.checked = Boolean(data.preferences?.proactive_calendar_events_enabled);
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
        body: JSON.stringify({
          proactive_reminders_enabled: reminderToggle.checked,
          proactive_calendar_events_enabled: calendarToggle.checked,
        }),
      });
      if (reminderToggle.checked || calendarToggle.checked) {
        const enabled = [];
        if (reminderToggle.checked) enabled.push("напоминания");
        if (calendarToggle.checked) enabled.push("события календаря");
        state.textContent = `Включено: ${enabled.join(" и ")}. Секретарь будет действовать только при точном расписании и высокой уверенности.`;
      } else {
        state.textContent = "Выключено. Новые автоматические действия создаваться не будут.";
      }
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
