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
    <p class="settings-help">Секретарь сам выполняет только безопасные действия с высокой уверенностью. Остальные сигналы попадают в «Требует внимания» на экране «Сегодня» без дополнительных ИИ-запросов.</p>
    <label class="field">Автоматические напоминания <input id="proactiveRemindersEnabled" type="checkbox"></label>
    <label class="field">Автоматические события календаря <input id="proactiveCalendarEnabled" type="checkbox"></label>
    <label class="field">Важные push-уведомления <input id="attentionPushEnabled" type="checkbox"><span class="settings-help">Только новые важные сигналы: действия из почты, просроченные задачи и конфликты маршрута. Учитываются тихие часы и дедупликация.</span></label>
    <p id="proactiveState" class="settings-help"></p>`;
  root.insertBefore(section, root.lastElementChild);

  const reminderToggle = document.getElementById("proactiveRemindersEnabled");
  const calendarToggle = document.getElementById("proactiveCalendarEnabled");
  const pushToggle = document.getElementById("attentionPushEnabled");
  const state = document.getElementById("proactiveState");
  let loading = false;
  let aiAccess = true;
  let saveTimer = null;
  let saveChain = Promise.resolve();

  function describe(action) {
    if (!action) return "Автоматических ИИ-действий пока не было.";
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
    if (!aiAccess) {
      reminderToggle.checked = false;
      calendarToggle.checked = false;
      state.textContent = requiresEntitlement
        ? "ИИ-автодействия доступны дополнительно. Центр внимания и важные push могут работать без них."
        : "ИИ-автодействия сейчас отключены. Центр внимания и важные push продолжают работать без ИИ.";
    }
  }

  async function load() {
    if (loading) return;
    loading = true;
    try {
      const data = await api("/api/assistant");
      setAccess(data.access?.enabled !== false, Boolean(data.access?.requires_entitlement));
      pushToggle.checked = Boolean(data.preferences?.attention_push_enabled);
      if (aiAccess) {
        reminderToggle.checked = Boolean(data.preferences?.proactive_reminders_enabled);
        calendarToggle.checked = Boolean(data.preferences?.proactive_calendar_events_enabled);
        state.textContent = describe(data.proactive?.last_action);
      }
    } catch (error) {
      state.textContent = error.message;
    } finally {
      loading = false;
    }
  }

  function scheduleSave(delay = 150) {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      saveTimer = null;
      const values = {
        proactive_reminders_enabled: aiAccess && reminderToggle.checked,
        proactive_calendar_events_enabled: aiAccess && calendarToggle.checked,
        attention_push_enabled: pushToggle.checked,
      };
      saveChain = saveChain
        .catch(() => {})
        .then(async () => {
          await api("/api/assistant/preferences", {
            method: "POST",
            body: JSON.stringify(values),
          });
          const enabled = [];
          if (values.proactive_reminders_enabled) enabled.push("автонапоминания");
          if (values.proactive_calendar_events_enabled) enabled.push("автособытия");
          if (values.attention_push_enabled) enabled.push("важные push");
          state.textContent = enabled.length
            ? `Включено: ${enabled.join(", ")}. Сохранено автоматически.`
            : "Автоматические действия и важные push выключены. Сохранено автоматически.";
        })
        .catch((error) => { state.textContent = error.message; });
    }, delay);
  }

  for (const toggle of [reminderToggle, calendarToggle, pushToggle]) {
    toggle.addEventListener("change", () => scheduleSave(0));
  }

  new MutationObserver(() => {
    if (settingsPanel.classList.contains("open")) load();
  }).observe(settingsPanel, {attributes: true, attributeFilter: ["class"]});
})();
