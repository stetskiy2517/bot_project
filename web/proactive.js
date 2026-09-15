(() => {
  const root = document.getElementById("assistantSettings");
  const settingsPanel = document.getElementById("settingsPanel");
  if (!root || !settingsPanel || typeof api !== "function") return;

  const section = document.createElement("details");
  section.className = "assistant-section settings-group";
  section.innerHTML = `
    <summary class="settings-group-summary-ready">
      <span class="settings-group-title">Проактивный помощник</span>
      <span class="settings-group-meta">ИИ</span>
      <svg class="settings-group-chevron" viewBox="0 0 24 24" aria-hidden="true">
        <path d="m6 9 6 6 6-6" />
      </svg>
    </summary>
    <p class="settings-help">Дополнительная ИИ-функция. Базовые календарь и напоминания работают без неё. При доступном ИИ секретарь может анализировать привычки и сам выбирать между напоминанием и блоком времени.</p>
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
  let aiAccess = true;

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

  function setAccess(enabled, requiresEntitlement) {
    aiAccess = Boolean(enabled);
    reminderToggle.disabled = !aiAccess;
    calendarToggle.disabled = !aiAccess;
    save.disabled = !aiAccess;
    if (!aiAccess) {
      reminderToggle.checked = false;
      calendarToggle.checked = false;
      state.textContent = requiresEntitlement
        ? "ИИ-функции доступны дополнительно. Календарь, повторяющиеся события и обычные напоминания продолжают работать бесплатно."
        : "ИИ-функции сейчас отключены. Календарь и обычные напоминания продолжают работать без ИИ.";
    }
  }

  async function load() {
    if (loading) return;
    loading = true;
    try {
      const data = await api("/api/assistant");
      setAccess(data.access?.enabled !== false, Boolean(data.access?.requires_entitlement));
      if (!aiAccess) return;
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
    if (!aiAccess) return;
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
      save.disabled = !aiAccess;
    }
  });

  new MutationObserver(() => {
    if (settingsPanel.classList.contains("open")) load();
  }).observe(settingsPanel, {attributes: true, attributeFilter: ["class"]});
})();
