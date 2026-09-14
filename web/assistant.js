(() => {
  "use strict";
  const root = document.getElementById("app");
  const panel = document.createElement("dialog");
  panel.id = "assistantDialog";
  panel.setAttribute("aria-labelledby", "assistantTitle");
  panel.innerHTML = `
    <header class="assistant-header"><h2 id="assistantTitle">Помощник</h2><button type="button" id="assistantClose" aria-label="Закрыть">×</button></header>
    <nav class="assistant-tabs" aria-label="Раздел помощника">
      <button type="button" data-page="day">День</button>
      <button type="button" data-page="templates">Избранное</button>
      <button type="button" data-page="notifications">Уведомления</button>
      <button type="button" data-page="privacy">Данные</button>
    </nav>
    <p id="assistantStatus" role="status" aria-live="polite"></p>
    <section id="assistantContent"></section>`;
  document.body.appendChild(panel);

  const style = document.createElement("style");
  style.textContent = `
    #assistantDialog { width:min(520px,calc(100vw - 24px));max-height:88dvh;padding:18px;border:1px solid #deded9;border-radius:22px;background:#fafaf8;color:#171717;overflow:auto;box-sizing:border-box; }
    #assistantDialog::backdrop { background:rgba(0,0,0,.28); }
    .assistant-header,.assistant-row,.assistant-tabs,.assistant-controls {display:flex;align-items:center;gap:8px;flex-wrap:wrap;}
    .assistant-header {justify-content:space-between;}
    .assistant-header h2 {font-size:21px;margin:0 0 10px;}
    .assistant-open,#assistantDialog button,.draft-actions button,.undo-action,.slot-action {
      border:1px solid #d9d9d5;border-radius:11px;background:#fff;color:#171717;padding:9px 12px;cursor:pointer;font:inherit;
    }
    #assistantDialog button:disabled,.draft-actions button:disabled {opacity:.5;cursor:wait;}
    .assistant-open {margin-right:8px;font-size:13px;pointer-events:auto;}
    .assistant-tabs {margin:10px 0;}
    .assistant-tabs button[aria-current=page] {background:#171717;color:#fff;}
    #assistantContent {font-size:14px;}
    #assistantContent form {display:grid;gap:12px;margin:12px 0;}
    #assistantContent label {display:grid;gap:6px;}
    #assistantContent input:not([type=checkbox]),#assistantContent select {font:inherit;width:100%;min-height:40px;padding:8px;box-sizing:border-box;border:1px solid #d9d9d5;border-radius:9px;background:#fff;color:#171717;}
    #assistantContent .check {display:flex;align-items:center;gap:9px;}
    #assistantContent .check input {width:18px;height:18px;}
    #assistantContent p,#assistantStatus {line-height:1.45;white-space:pre-line;}
    #assistantStatus {min-height:1em;font-size:13px;}
    .assistant-item {padding:12px 0;border-bottom:1px solid #e4e4df;}
    .assistant-item h3 {font-size:15px;margin:0 0 7px;overflow-wrap:anywhere;}
    .assistant-item p {margin:6px 0;overflow-wrap:anywhere;}
    .assistant-controls {margin:8px 0;}
    .assistant-danger {color:#9f1d1d!important;border-color:#cda2a2!important;}
    .voice-shell,.chat-shell {bottom:var(--assistant-composer-height,calc(env(safe-area-inset-bottom) + 82px));}
    .draft-panel {font-size:13px;max-width:760px;margin:0 auto 8px;padding:9px 11px;border:1px solid #d9d9d5;border-radius:12px;background:#fff;overflow-wrap:anywhere;}
    .draft-panel p {margin:0 0 7px;max-height:54px;overflow:auto;}
    .draft-actions {display:flex;gap:7px;flex-wrap:wrap;}
    .undo-action {font-size:12px;margin:0 14px 8px;max-width:calc(100% - 28px);}
    .slot-actions {display:flex;gap:6px;flex-wrap:wrap;margin:7px 0 14px;}
    .slot-action {font-size:13px;}
    .assistant-note {font-size:12px;color:#666;}
  `;
  document.head.appendChild(style);

  const openButton = document.createElement("button");
  openButton.type = "button";
  openButton.className = "assistant-open";
  openButton.textContent = "Помощник";
  document.querySelector(".topbar").prepend(openButton);

  const content = panel.querySelector("#assistantContent");
  const status = panel.querySelector("#assistantStatus");
  let page = "day";
  let loadVersion = 0;
  let busy = false;
  let pendingUndo = null;
  let retrying = false;
  let reminderItems = [];

  function node(tag, text = "", className = "") {
    const item = document.createElement(tag);
    item.textContent = text;
    if (className) item.className = className;
    return item;
  }

  function button(label, action, className = "") {
    const item = node("button", label, className);
    item.type = "button";
    item.addEventListener("click", () => run(action, item));
    return item;
  }

  async function run(action, source = null) {
    if (busy) return;
    busy = true;
    if (source) source.disabled = true;
    status.textContent = "";
    try { await action(); }
    catch (error) { status.textContent = error.message === "unauthorized" ? "Войди через Google заново." : error.message; }
    finally { busy = false; if (source?.isConnected) source.disabled = false; }
  }

  function field(form, label, type, value = "", options = null) {
    const wrapper = node("label", label);
    const input = document.createElement(options ? "select" : "input");
    if (options) {
      for (const [id, text] of options) {
        const option = node("option", text); option.value = id; input.append(option);
      }
    } else { input.type = type; }
    if (type === "checkbox") {
      wrapper.className = "check";
      input.checked = Boolean(value);
      wrapper.prepend(input);
    } else {
      input.value = String(value ?? "");
      wrapper.append(input);
    }
    form.append(wrapper);
    return input;
  }

  function post(path, values = {}, method = "POST") {
    return api(path, { method, body: JSON.stringify(values) });
  }

  function showResult(result) {
    panel.close();
    showChat();
    presentResult(result);
    armChatIdleTimer();
  }

  function renderReminder(item, timezone) {
    const row = node("div", "", "assistant-item");
    row.append(node("h3", item.text));
    const stamp = new Intl.DateTimeFormat("ru", {dateStyle:"short", timeStyle:"short", timeZone:timezone});
    row.append(node("p", stamp.format(new Date(item.remind_at)), "assistant-note"));
    const controls = node("div", "", "assistant-controls");
    controls.append(
      button("Выполнено", async () => {
        await post(`/api/library/reminders/${item.id}/complete`, {completed:true});
        await loadDay(true);
      }),
      button("Перенести", async () => {
        const form = node("form");
        const date = field(form, `Новые дата и время · ${timezone}`, "datetime-local");
        date.required = true;
        const save = node("button", "Сохранить перенос"); save.type = "submit";
        form.append(save);
        form.onsubmit = (event) => {
          event.preventDefault();
          run(async () => {
            await post(`/api/assistant/reminders/${item.id}/reschedule`, {local_time:date.value});
            await loadDay(true);
          }, save);
        };
        row.append(form);
        date.focus();
      }),
      button("Оставить", async () => { status.textContent = "Оставлено без изменений."; }),
    );
    row.append(controls);
    return row;
  }

  async function loadDay(evening = false) {
    const version = ++loadVersion;
    content.replaceChildren(node("p", "Проверяю день…"));
    const result = await api("/api/assistant/overview" + (evening ? "?mode=evening" : ""));
    if (version !== loadVersion || page !== "day") return;
    const controls = node("div", "", "assistant-controls");
    controls.append(button("Обзор дня", () => loadDay(false)), button("Вечерний разбор", () => loadDay(true)));
    content.replaceChildren(controls, node("p", result.text));
    for (const item of result.reminders || []) content.append(renderReminder(item, result.timezone));
    const states = {accepted:"Принято Push-сервисом, прочтение неизвестно", failed:"Отправка не подтверждена", attempted:"Попытка начата, результат неизвестен"};
    for (const item of result.deliveries || []) {
      content.append(node("p", states[item.status] || "Статус отправки неизвестен", "assistant-note"));
    }
  }

  async function loadTemplates(editing = null) {
    const version = ++loadVersion;
    const result = await api("/api/templates");
    if (version !== loadVersion || page !== "templates") return;
    content.replaceChildren(node("p", "Сохраняются только заданные тобой шаблоны. Дату и время указывай при запуске.", "assistant-note"));
    for (const item of result.templates || []) {
      const row = node("div", "", "assistant-item");
      row.append(node("h3", item.name), node("p", `${item.title} · ${item.duration_minutes} мин`));
      const date = document.createElement("input");
      date.placeholder = "Например: завтра в 15";
      date.setAttribute("aria-label", `Дата и время: ${item.name}`);
      date.maxLength = 200;
      row.append(date);
      const controls = node("div", "", "assistant-controls");
      controls.append(
        button("Использовать", async () => showResult(await post(`/api/templates/${item.id}/use`, {date:date.value}))),
        button("Изменить", () => loadTemplates(item)),
        button("Удалить", async () => {
          if (!confirm(`Удалить шаблон «${item.name}»?`)) return;
          await post(`/api/templates/${item.id}`, {}, "DELETE");
          await loadTemplates();
        }),
      );
      row.append(controls); content.append(row);
    }
    const form = node("form");
    form.append(node("h3", editing ? "Изменить шаблон" : "Новый шаблон"));
    const name = field(form, "Имя шаблона", "text", editing?.name); name.maxLength = 80; name.required = true;
    const title = field(form, "Название события", "text", editing?.title); title.maxLength = 200; title.required = true;
    const duration = field(form, "Длительность, минут", "number", editing?.duration_minutes || 60); duration.min=5; duration.max=720;
    const category = field(form, "Категория", "text", editing?.category || "work", Object.entries(categoryNames));
    const save = node("button", "Сохранить шаблон"); save.type = "submit";
    form.append(save);
    form.onsubmit = (event) => {
      event.preventDefault();
      run(async () => {
        await post("/api/templates" + (editing ? `/${editing.id}` : ""), {
          name:name.value, title:title.value, duration_minutes:Number(duration.value), category:category.value,
        }, editing ? "PATCH" : "POST");
        await loadTemplates();
        status.textContent = "Шаблон сохранён.";
      }, save);
    };
    content.append(form);
  }

  async function loadNotifications() {
    const version = ++loadVersion;
    const [prefs, library] = await Promise.all([api("/api/assistant/preferences"), api("/api/library")]);
    if (version !== loadVersion || page !== "notifications") return;
    content.replaceChildren(node("p", `Часовой пояс: ${library.timezone}. Push нужно включить в основных настройках. Это не системный будильник.`, "assistant-note"));
    const form = node("form");
    const morning = field(form, "Утренний обзор", "checkbox", prefs.morning_enabled);
    const morningTime = field(form, "Утром в", "time", prefs.morning_time);
    const evening = field(form, "Вечерний разбор", "checkbox", prefs.evening_enabled);
    const eveningTime = field(form, "Вечером в", "time", prefs.evening_time);
    const quiet = field(form, "Тихие часы", "checkbox", prefs.quiet_enabled);
    const quietStart = field(form, "Не уведомлять с", "time", prefs.quiet_start);
    const quietEnd = field(form, "До", "time", prefs.quiet_end);
    const save = node("button", "Сохранить уведомления"); save.type = "submit"; form.append(save);
    form.onsubmit = (event) => {
      event.preventDefault();
      run(async () => {
        await post("/api/assistant/preferences", {
          morning_enabled:morning.checked, morning_time:morningTime.value,
          evening_enabled:evening.checked, evening_time:eveningTime.value,
          quiet_enabled:quiet.checked, quiet_start:quietStart.value, quiet_end:quietEnd.value,
        });
        status.textContent = "Настройки сохранены.";
      }, save);
    };
    content.append(form);
    reminderItems = (library.reminders || []).filter(item => item.status !== "completed");
    content.append(node("h3", "Повторные уведомления"));
    content.append(node("p", "По умолчанию выключены. От 0 до 3 повторов после первого уведомления. Выполнение останавливает повторы; календарное повторение самого напоминания не меняется.", "assistant-note"));
    if (!reminderItems.length) { content.append(node("p", "Нет активных напоминаний.")); return; }
    const alertForm = node("form");
    const chosen = field(alertForm, "Напоминание", "text", reminderItems[0].id,
      reminderItems.map(item => [item.id, item.text.slice(0,100)]));
    const count = field(alertForm, "Число повторов: 0 — выключено", "number", 0); count.min=0; count.max=3;
    const interval = field(alertForm, "Интервал, минут", "number", 15); interval.min=5; interval.max=180;
    let policyVersion = 0;
    async function loadPolicy() {
      const current = ++policyVersion;
      const id = chosen.value;
      const result = await api(`/api/library/reminders/${id}/alerts`);
      if (current !== policyVersion || !chosen.isConnected) return;
      count.value = result.max_repeats; interval.value = result.interval_minutes;
    }
    chosen.onchange = () => run(loadPolicy);
    const alertSave = node("button", "Сохранить повторы"); alertSave.type = "submit"; alertForm.append(alertSave);
    alertForm.onsubmit = (event) => {
      event.preventDefault();
      run(async () => {
        await post(`/api/library/reminders/${chosen.value}/alerts`, {max_repeats:Number(count.value), interval_minutes:Number(interval.value)});
        status.textContent = "Повторы сохранены. Доставка Push не означает, что уведомление прочитано.";
      }, alertSave);
    };
    content.append(alertForm); await loadPolicy();
  }

  async function loadPrivacy() {
    const version = ++loadVersion;
    const notice = await api("/api/privacy");
    if (version !== loadVersion || page !== "privacy") return;
    content.replaceChildren(node("p", "Экспорт содержит твои локальные записи и настройки. Токены и Push-ключи в файл не попадают."));
    content.append(button("Выгрузить данные", async () => {
      const response = await SecretaryRequests.request("/api/privacy/export");
      if (!response.ok) throw Error("Не удалось выгрузить данные.");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href=url; link.download="personal-secretary-export.json"; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      status.textContent = "Файл выгрузки передан браузеру.";
    }));
    content.append(node("p", notice.message));
    content.append(button("Удалить локальный аккаунт", async () => {
      const ticket = await post("/api/privacy/delete-ticket");
      const form = node("form");
      const confirmation = field(form, `Введи: ${ticket.confirmation}`, "text");
      confirmation.autocomplete = "off";
      const remove = node("button", "Подтверждаю безвозвратное удаление", "assistant-danger");
      remove.type = "submit"; form.append(remove);
      form.onsubmit = (event) => {
        event.preventDefault();
        run(async () => {
          const result = await post("/api/privacy/delete", {ticket:ticket.ticket, confirmation:confirmation.value});
          accountRemoved = true;
          clearTimeout(refreshTimer);
          clearTimeout(undoTimer);
          pendingUndo = null;
          undoButton.hidden = true;
          await SecretaryRequests.signOutDevice();
          if (!panel.open) panel.showModal();
          status.textContent = result.message;
          content.replaceChildren(node("p", result.message), button("Перейти ко входу", async () => location.reload()));
          panel.querySelectorAll(".assistant-tabs button").forEach(item => { item.disabled = true; });
        }, remove);
      };
      content.append(form); confirmation.focus();
    }, "assistant-danger"));
  }

  async function loadPage(next) {
    page=next; loadVersion++;
    status.textContent="";
    panel.querySelectorAll("[data-page]").forEach(item => item.setAttribute("aria-current", item.dataset.page===page ? "page" : "false"));
    if (page==="day") return loadDay();
    if (page==="templates") return loadTemplates();
    if (page==="notifications") return loadNotifications();
    return loadPrivacy();
  }
  panel.querySelectorAll("[data-page]").forEach(item => {
    item.onclick=() => run(() => loadPage(item.dataset.page), item);
  });
  openButton.onclick=() => {
    if (sendingVoice || sendingChat || voicePressActive) return;
    panel.showModal(); clearChatIdleTimer(); run(() => loadPage("day"));
  };
  panel.querySelector("#assistantClose").onclick=() => panel.close();
  panel.addEventListener("close", armChatIdleTimer);

  const draftPanel=node("div", "", "draft-panel");
  draftPanel.id="requestDraft"; draftPanel.hidden=true;
  draftPanel.setAttribute("role","status");
  const undoButton=node("button", "Отменить последнее действие", "undo-action");
  undoButton.type="button"; undoButton.hidden=true;
  const composerWrap = root.querySelector(".composer-wrap");
  composerWrap.prepend(undoButton);
  composerWrap.prepend(draftPanel);
  function fitComposer() {
    root.style.setProperty("--assistant-composer-height", `${Math.ceil(composerWrap.getBoundingClientRect().height) + 8}px`);
  }
  if (window.ResizeObserver) new ResizeObserver(fitComposer).observe(composerWrap);
  window.addEventListener("resize", fitComposer);
  fitComposer();

  function renderDraft() {
    const draft=SecretaryRequests.readDraft();
    draftPanel.hidden=!draft;
    draftPanel.replaceChildren();
    if (!draft) return;
    draftPanel.append(node("p", draft.kind==="text" ? `Не подтверждено: ${draft.text}` : "Голосовой запрос: результат ещё не подтверждён."));
    const controls=node("div", "", "draft-actions");
    const retry=node("button", "Проверить / повторить"); retry.type="button"; retry.disabled=retrying;
    const discard=node("button", "Убрать"); discard.type="button"; discard.disabled=retrying;
    retry.onclick=() => retryDraft(draft);
    discard.onclick=() => {
      if (confirm("Убрать черновик? Это не отменяет уже выполненную на сервере команду.")) {
        SecretaryRequests.discardDraft(draft.key); armChatIdleTimer();
      }
    };
    controls.append(retry,discard); draftPanel.append(controls);
  }

  async function retryDraft(draft) {
    if (retrying || sendingVoice || sendingChat || voicePressActive) return;
    retrying=true; sendingChat=true; clearChatIdleTimer(); renderDraft();
    try {
      let response=await SecretaryRequests.request(`/api/requests/${encodeURIComponent(draft.key)}`);
      let result=await response.json();
      if (response.status===404 && result.error==="request_not_found") {
        let options={method:"POST",requestKey:draft.key}, path="/api/chat";
        if (draft.kind==="text") {
          options.headers={"Content-Type":"application/json"};
          options.body=JSON.stringify({message:draft.text});
        } else {
          const blob=SecretaryRequests.currentVoice(draft.key);
          if (!blob) throw Error("Запись звука не хранится после закрытия страницы. Запрос не найден на сервере; убери черновик и запиши заново.");
          path="/api/voice";
          const form=new FormData();
          form.append("audio",blob,"voice."+voiceExtension(blob.type||""));
          form.append("duration_ms",String(draft.durationMs));
          options.body=form;
        }
        response=await SecretaryRequests.request(path,options); result=await response.json();
      }
      if (!response.ok) {
        // A terminal validation error can be discarded; unknown outcomes remain check-only.
        if (response.status===400 && !result.check_only) SecretaryRequests.discardDraft(draft.key);
        throw Error(result.message||"Результат пока не подтверждён. Проверь сохранённые записи.");
      }
      SecretaryRequests.discardDraft(draft.key);
      showChat(); presentResult(result);
    } catch(error) { showChat(); msg(error.message,"assistant"); }
    finally {retrying=false;sendingChat=false;renderDraft();armChatIdleTimer();}
  }

  let undoTimer=null;
  let accountRemoved=false;
  async function refreshUndo() {
    if (accountRemoved) return;
    try {
      const result=await api("/api/undo");
      pendingUndo=result.action;
      undoButton.hidden=!pendingUndo;
      if (pendingUndo) {
        undoButton.textContent=`Отменить: ${pendingUndo.label}`;
        clearTimeout(undoTimer);
        undoTimer=setTimeout(() => {undoButton.hidden=true;pendingUndo=null;},
          Math.max(0, pendingUndo.expires_at*1000-Date.now()));
      }
    } catch (_) {undoButton.hidden=true;pendingUndo=null;}
  }
  undoButton.onclick=async () => {
    if (!pendingUndo || undoButton.disabled) return;
    undoButton.disabled=true;
    try {
      await post(`/api/undo/${pendingUndo.id}`);
      showChat();msg("Локальное действие отменено.");
      window.dispatchEvent(new CustomEvent("secretary:library-refresh"));
      await refreshUndo();
    } catch(error) {showChat();msg(error.message);}
    finally {undoButton.disabled=false;armChatIdleTimer();}
  };

  window.addEventListener("secretary:result",(event) => {
    document.querySelectorAll(".slot-actions").forEach(item => item.remove());
    const actions=event.detail?.actions||[];
    if (!actions.length) return;
    const row=node("div","","slot-actions");
    for (const action of actions.slice(0,3)) {
      const item=node("button",action.label,"slot-action");item.type="button";
      item.onclick=() => {
        if (sendingChat || sendingVoice || voicePressActive || SecretaryRequests.readDraft()) return;
        messageInput.value=action.command;
        document.getElementById("composer").requestSubmit();
        row.remove();
      };
      row.append(item);
    }
    chat.append(row);
  });
  window.addEventListener("secretary:draft",renderDraft);
  window.addEventListener("secretary:retry-needed",renderDraft);
  window.addEventListener("secretary:session",() => {renderDraft();refreshUndo();});
  window.addEventListener("secretary:session-ended",() => {
    draftPanel.hidden=true;undoButton.hidden=true;pendingUndo=null;
    if (!accountRemoved) {content.replaceChildren();panel.close();}
  });
  let refreshTimer=null;
  window.addEventListener("secretary:mutation",() => {
    clearTimeout(refreshTimer);
    if (!accountRemoved) refreshTimer=setTimeout(refreshUndo,150);
  });
  window.addEventListener("secretary:draft-storage-error",() => {
    showChat();msg("Черновик сохранён только до закрытия этой страницы: хранилище браузера недоступно.");
  });
  window.addEventListener("storage",(event) => {
    if (event.key?.startsWith("secretary.draft.")) renderDraft();
  });
  renderDraft();
})();
