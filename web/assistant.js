(() => {
  const sheet = document.querySelector("#settingsPanel .sheet");
  if (!sheet) return;
  const sections = document.createElement("div");
  sections.id = "assistantSettings";
  sections.innerHTML = `
    <details class="assistant-section"><summary>Обзоры и тихие часы</summary>
      <p class="settings-help">Обзоры выключены по умолчанию. Время — в часовом поясе аккаунта. Push не гарантирует показ при выключенном устройстве.</p>
      <div id="assistantDeliveryFields" class="grid"></div>
      <div class="assistant-actions">
        <button class="action" type="button" id="morningReview">Обзор дня</button>
        <button class="action" type="button" id="eveningReview">Вечерний разбор</button>
      </div>
      <p id="reviewDeliveryState" class="settings-help"></p>
    </details>
    <details class="assistant-section"><summary>Избранные команды</summary>
      <p class="settings-help">Только сохранённые тобой шаблоны встреч. Дата и время задаются отдельно. Приглашения шаблоном не отправляются.</p>
      <div id="assistantTemplates"></div>
      <form id="templateForm">
        <label class="field">Имя команды<input name="name" required minlength="2" maxlength="50" placeholder="Обычный созвон"></label>
        <label class="field">Название встречи<input name="title" required maxlength="200"></label>
        <label class="field">Длительность, минут<input name="duration_minutes" type="number" min="1" max="720" value="60" required></label>
        <label class="field">Категория<select name="category"></select></label>
        <button class="action" type="submit">Сохранить шаблон</button>
        <button class="action" type="button" id="cancelTemplateEdit">Новый шаблон</button>
      </form>
    </details>
    <details class="assistant-section"><summary>Отмена и данные</summary>
      <p class="settings-help">Можно отменить последнее изменение локальной заметки в течение 10 минут, пока она не изменилась снова. Встречи и удаление аккаунта не отменяются.</p>
      <button class="action" type="button" id="undoNoteAction" disabled>Нет действия для отмены</button>
      <p id="privacyNotice" class="settings-help"></p>
      <button class="action" type="button" id="exportAccount">Выгрузить данные JSON</button>
      <button class="action" type="button" id="reauthAccount">Подтвердить вход</button>
      <button class="action danger" type="button" id="eraseAccount">Удалить локальные данные</button>
    </details>
    <p id="assistantSettingsStatus" class="settings-help" role="status" aria-live="polite"></p>`;
  sheet.insertBefore(sections, sheet.querySelector(".sheet-actions"));
  const style = document.createElement("style");
  style.textContent = `.assistant-section {padding:14px 0;border-top:1px solid #ddd}
    .assistant-section summary {cursor:pointer;font-weight:600;padding:5px 0}
    .assistant-section .field {margin:10px 0}
    .assistant-actions {display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}
    .assistant-template {margin:10px 0;overflow-wrap:anywhere}
    .assistant-section button,.msg button {margin:4px 4px 4px 0}
    .assistant-repeat input {max-width:140px}
    .assistant-section form {display:grid;gap:6px}`;
  document.head.appendChild(style);
  const status = document.getElementById("assistantSettingsStatus");
  const form = document.getElementById("templateForm");
  let editingId = null;
  let undo = null;
  let loading = false;
  let deliverySaveTimer = null;
  let deliverySaveChain = Promise.resolve();
  let deliverySaveRevision = 0;

  function button(label, action) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "action";
    item.textContent = label;
    item.onclick = async () => {
      item.disabled = true;
      try {await action();} catch (error) {status.textContent = error.message; window.msg?.(error.message);}
      finally {item.disabled = false;}
    };
    return item;
  }
  const labels = {
    morning_enabled: "Утренний обзор", morning_time: "Время утром",
    evening_enabled: "Вечерний обзор", evening_time: "Время вечером",
    quiet_enabled: "Тихие часы", quiet_start: "Начало тишины", quiet_end: "Конец тишины",
  };
  const fields = document.getElementById("assistantDeliveryFields");
  for (const [key, text] of Object.entries(labels)) {
    const label = document.createElement("label");
    label.className = "field";
    label.textContent = text;
    const input = document.createElement("input");
    input.name = key;
    input.type = key.endsWith("_enabled") ? "checkbox" : "time";
    label.appendChild(input);
    fields.appendChild(label);
  }
  for (const [value, label] of Object.entries(categoryNames)) {
    form.elements.category.add(new Option(label, value));
  }

  function renderTemplates(templates) {
    const box = document.getElementById("assistantTemplates");
    box.replaceChildren();
    for (const template of templates) {
      const row = document.createElement("div");
      row.className = "assistant-template";
      const title = document.createElement("div");
      title.textContent = `${template.name} → ${template.title}, ${template.duration_minutes} мин`;
      row.append(title, button("Ввести", () => {
        settingsPanel.classList.remove("open");
        showChat();
        messageInput.value = template.name + " ";
        messageInput.dispatchEvent(new Event("input"));
        messageInput.focus();
      }), button("Изменить", () => {
        editingId = template.id;
        for (const name of ["name", "title", "duration_minutes", "category"]) form.elements[name].value = template[name];
      }), button("Удалить", async () => {
        await api(`/api/assistant/templates/${template.id}`, {method: "DELETE"});
        await load();
      }));
      box.appendChild(row);
    }
  }

  async function load() {
    if (loading) return;
    loading = true;
    try {
      const data = await api("/api/assistant");
      for (const input of fields.querySelectorAll("input")) {
        if (input.type === "checkbox") input.checked = Boolean(data.preferences[input.name]);
        else input.value = data.preferences[input.name];
      }
      renderTemplates(data.templates || []);
      undo = data.undo;
      const undoButton = document.getElementById("undoNoteAction");
      undoButton.disabled = !undo;
      undoButton.textContent = undo ? undo.label : "Нет действия для отмены";
      document.getElementById("privacyNotice").textContent =
        `${data.privacy.notice} Настроенный срок хранения: ${data.privacy.backup_retention_days} дней.`;
      const recent = (data.reviews || [])[0];
      const phase = {accepted: "принят push-сервисом, показ не подтверждён", failed: "не отправлен", missed: "пропущен", sending: "результат отправки неизвестен"};
      document.getElementById("reviewDeliveryState").textContent =
        recent ? `Последний обзор ${recent.day}: ${phase[recent.phase] || recent.phase}.` : "Автоматических отправок пока нет.";
      status.textContent = "";
    } catch (error) {status.textContent = error.message;}
    finally {loading = false;}
  }

  function deliveryValues() {
    const values = {};
    for (const input of fields.querySelectorAll("input")) {
      values[input.name] = input.type === "checkbox" ? input.checked : input.value;
    }
    return values;
  }

  function scheduleDeliverySave(delay = 250) {
    clearTimeout(deliverySaveTimer);
    const revision = ++deliverySaveRevision;
    status.textContent = "Сохраняю настройки…";
    deliverySaveTimer = setTimeout(() => {
      deliverySaveTimer = null;
      const values = deliveryValues();
      deliverySaveChain = deliverySaveChain
        .catch(() => {})
        .then(async () => {
          await api("/api/assistant/preferences", {method: "POST", body: JSON.stringify(values)});
          if (revision === deliverySaveRevision) {
            status.textContent = "Настройки уведомлений сохранены автоматически.";
          }
        })
        .catch((error) => {
          if (revision === deliverySaveRevision) status.textContent = error.message;
        });
    }, delay);
  }

  fields.addEventListener("change", () => scheduleDeliverySave(0));
  fields.addEventListener("input", (event) => {
    if (event.target?.type !== "checkbox") scheduleDeliverySave(350);
  });

  async function showReview(kind) {
    const review = await api(`/api/assistant/review?kind=${encodeURIComponent(kind)}`);
    settingsPanel.classList.remove("open");
    app.classList.remove("library-active");
    showChat();
    msg(review.text);
    if (kind === "evening") {
      for (const reminder of review.reminders || []) {
        const row = msg(reminder.text);
        const date = document.createElement("input");
        date.type = "datetime-local";
        date.setAttribute("aria-label", "Новое время напоминания");
        row.append(button("Выполнено", async () => {
          await api(`/api/library/reminders/${reminder.id}/complete`, {method: "POST"});
          row.textContent = "Выполнено · " + reminder.text;
          document.dispatchEvent(new Event("planner-library-changed"));
        }), date, button("Перенести", async () => {
          const value = new Date(date.value);
          if (!date.value || !Number.isFinite(value.getTime()) || value <= new Date()) throw Error("Выбери будущее время.");
          await api(`/api/library/reminders/${reminder.id}/reschedule`, {method: "POST", body: JSON.stringify({remind_at: value.toISOString()})});
          row.textContent = "Перенесено · " + reminder.text;
          document.dispatchEvent(new Event("planner-library-changed"));
        }), button("Оставить", () => {row.textContent = "Оставлено без изменений · " + reminder.text;}));
      }
    }
    armChatIdleTimer();
  }
  for (const kind of ["morning", "evening"]) {
    document.getElementById(kind + "Review").onclick = async () => {
      try {await showReview(kind);} catch (error) {status.textContent = error.message;}
    };
  }

  form.onsubmit = async event => {
    event.preventDefault();
    const save = form.querySelector('[type="submit"]');
    save.disabled = true;
    try {
      const data = Object.fromEntries(new FormData(form));
      data.duration_minutes = Number(data.duration_minutes);
      await api("/api/assistant/templates" + (editingId ? "/" + editingId : ""),
        {method: editingId ? "PUT" : "POST", body: JSON.stringify(data)});
      editingId = null;
      form.reset();
      await load();
    } catch (error) {status.textContent = error.message;} finally {save.disabled = false;}
  };
  document.getElementById("cancelTemplateEdit").onclick = () => {editingId = null; form.reset();};

  async function undoLatest(action) {
    await api(`/api/assistant/undo/${action.id}`, {method: "POST"});
    msg("Изменение заметки отменено.");
    document.dispatchEvent(new Event("planner-library-changed"));
    await load();
  }
  document.getElementById("undoNoteAction").onclick = async function () {
    if (!undo) return;
    this.disabled = true;
    try {await undoLatest(undo);} catch (error) {status.textContent = error.message;}
  };

  document.getElementById("exportAccount").onclick = async function () {
    this.disabled = true;
    try {
      const data = await api("/api/privacy/export");
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: "application/json"}));
      const link = document.createElement("a");
      link.href = url;
      link.download = "personal-secretary-export.json";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {status.textContent = error.message;} finally {this.disabled = false;}
  };
  document.getElementById("reauthAccount").onclick = async () => {
    try {location.href = (await api("/api/google/login")).url;} catch (error) {status.textContent = error.message;}
  };
  document.getElementById("eraseAccount").onclick = async function () {
    this.disabled = true;
    try {
      const data = await api("/api/privacy/challenge", {method: "POST"});
      const confirmation = prompt(data.policy.notice + "\n\nДля удаления напиши: УДАЛИТЬ МОИ ДАННЫЕ");
      if (confirmation !== "УДАЛИТЬ МОИ ДАННЫЕ") return;
      await api("/api/privacy/erase", {method: "POST", body: JSON.stringify({challenge: data.challenge, confirmation})});
      PlannerRequests.clearAll();
      pendingVoice = null;
      const registration = await navigator.serviceWorker?.getRegistration();
      const subscription = await registration?.pushManager.getSubscription();
      if (subscription) await subscription.unsubscribe();
      location.replace("/");
    } catch (error) {status.textContent = error.message;} finally {this.disabled = false;}
  };

  document.addEventListener("planner-library-open", event => {
    if (event.detail.type !== "reminder") return;
    const id = event.detail.id;
    const row = msg("");
    const details = document.createElement("details");
    details.className = "assistant-repeat";
    const summary = document.createElement("summary");
    summary.textContent = "Повторные уведомления";
    details.appendChild(summary);
    row.appendChild(details);
    details.addEventListener("toggle", async () => {
      if (!details.open || details.dataset.loaded) return;
      details.dataset.loaded = "true";
      try {
        const {policy} = await api(`/api/assistant/reminders/${id}/notifications`);
        const explanation = document.createElement("p");
        explanation.textContent = "Это повтор доставки, не новое событие. 0 — выключено. Выполнение останавливает повторы. Не будильник.";
        const interval = document.createElement("input");
        interval.type = "number"; interval.min = "5"; interval.max = "1440"; interval.value = policy.interval_minutes;
        interval.setAttribute("aria-label", "Интервал повторов в минутах");
        const count = document.createElement("input");
        count.type = "number"; count.min = "0"; count.max = "5"; count.value = policy.max_repeats;
        count.setAttribute("aria-label", "Число повторов, максимум 5");
        details.append(explanation, document.createTextNode("Минут: "), interval,
          document.createTextNode(" Повторов: "), count, button("Сохранить", async () => {
            await api(`/api/assistant/reminders/${id}/notifications`, {method: "POST",
              body: JSON.stringify({interval_minutes: Number(interval.value), max_repeats: Number(count.value)})});
            explanation.textContent = "Сохранено. Тихие часы задаются в настройках аккаунта.";
          }));
      } catch (error) {details.appendChild(document.createTextNode(error.message));}
    });
  });
  new MutationObserver(() => {if (settingsPanel.classList.contains("open")) load();})
    .observe(settingsPanel, {attributes: true, attributeFilter: ["class"]});
  document.addEventListener("planner-ready", () => {
    const kind = new URL(location.href).searchParams.get("review");
    if (["morning", "evening"].includes(kind)) showReview(kind).catch(error => {status.textContent = error.message;});
  }, {once: true});
})();