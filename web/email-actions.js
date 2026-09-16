(() => {
  "use strict";
  let installed = false;

  function api(path, options) {
    if (typeof window.api !== "function") throw new Error("API недоступен");
    return window.api(path, options);
  }

  function labelType(value) {
    return {task: "Задача", reminder: "Напоминание", calendar_event: "Календарь"}[value] || value;
  }

  function notify(text) {
    const state = document.getElementById("emailPlanState");
    if (state) state.textContent = text;
    if (typeof window.msg === "function") window.msg(text);
  }

  function formatWhen(value, timezoneName = "") {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    try {
      return date.toLocaleString("ru-RU", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        ...(timezoneName ? {timeZone: timezoneName} : {}),
      });
    } catch (_) {
      return date.toLocaleString("ru-RU");
    }
  }

  function actionButtonState(action, button) {
    if (action.auto_created) {
      button.textContent = "Добавлено автоматически ✓";
      button.disabled = true;
      return true;
    }
    if (action.already_in_calendar || action.applied) {
      button.textContent = "Уже в календаре ✓";
      button.disabled = true;
      return true;
    }
    if (action.ready === false || action.attachment_event?.ready === false) {
      button.textContent = "Нужна проверка";
      button.disabled = true;
      return true;
    }
    return false;
  }

  async function applyAction(action, button, options = {}) {
    if (action.ready === false || action.attachment_event?.ready === false || button.disabled) {
      notify("Сначала проверь данные: ИИ не уверен в этом событии.");
      return;
    }
    const event = action.attachment_event;
    const when = event?.start ? formatWhen(event.start, event.start_timezone) : (action.due_at ? formatWhen(action.due_at) : "без точного времени");
    const route = event?.location ? `\n${event.location}` : "";
    if (options.confirm !== false && !confirm(`${labelType(action.action_type)}: «${action.title}»\n${when}${route}\n\nСоздать?`)) return;
    button.disabled = true;
    const oldText = button.textContent;
    button.textContent = "Добавляю…";
    try {
      const result = await api("/api/email/action", {method: "POST", body: JSON.stringify(action)});
      button.textContent = result.already_present ? "Уже в календаре ✓" : "Добавлено ✓";
      button.dataset.applied = "1";
      action.applied = true;
      if (result.already_present) action.already_in_calendar = true;
      document.dispatchEvent(new Event("planner-library-changed"));
      notify(result.already_present ? `Уже было в календаре: ${action.title}` : `Создано: ${action.title}`);
    } catch (error) {
      notify(error.message);
      button.disabled = false;
      button.textContent = oldText;
    }
  }

  function renderWarnings(target, warnings) {
    const items = (warnings || []).filter(Boolean);
    if (!items.length) return;
    const box = document.createElement("div");
    box.className = "email-plan-warnings";
    for (const warning of items) {
      const line = document.createElement("div");
      line.textContent = `⚠ ${warning}`;
      box.append(line);
    }
    target.append(box);
  }

  function renderAttachmentStatus(target, info) {
    if (!info || info.enabled !== true) return;
    const status = document.createElement("div");
    status.className = "email-plan-attachment-status settings-help";
    const detected = Number(info.detected || 0);
    const supported = Number(info.supported_found || 0);
    const analyzed = Number(info.analyzed || 0);
    status.textContent = `Вложения: найдено ${detected} · поддерживается ${supported} · разобрано ${analyzed}`;
    target.append(status);
  }

  function eventMeta(action) {
    const event = action.attachment_event || {};
    const start = formatWhen(event.start || action.due_at, event.start_timezone);
    const end = formatWhen(event.end, event.end_timezone);
    const lines = [];
    if (start && end) lines.push(`${start} — ${end}`);
    else if (start) lines.push(start);
    if (event.start_location && event.end_location) lines.push(`${event.start_location} → ${event.end_location}`);
    else if (event.location) lines.push(event.location);
    if (action.source?.attachment) lines.push(`Вложение: ${action.source.attachment}`);
    if (Number.isFinite(Number(action.confidence))) lines.push(`Уверенность: ${Math.round(Number(action.confidence) * 100)}%`);
    return lines.join("\n");
  }

  function makeActionButton(action, *, chat = false) {
    const button = document.createElement("button");
    button.className = "action primary";
    button.type = "button";
    if (!actionButtonState(action, button)) {
      button.textContent = action.action_type === "calendar_event" ? "Добавить в календарь" : "Подтвердить и создать";
      button.onclick = () => applyAction(action, button, {confirm: !chat});
    }
    return button;
  }

  function renderChatPlan(plan) {
    const chat = document.getElementById("chat");
    if (!chat || !plan || !Array.isArray(plan.actions)) return;
    const attachmentActions = plan.actions.filter((action) => action && action.attachment_event);
    for (const action of attachmentActions) {
      const card = document.createElement("div");
      card.className = "msg assistant email-chat-action-card";

      const title = document.createElement("div");
      title.className = "email-chat-action-title";
      title.textContent = action.title || action.attachment_event?.title || "Событие";
      card.append(title);

      const meta = document.createElement("div");
      meta.className = "email-chat-action-meta";
      meta.textContent = eventMeta(action);
      card.append(meta);

      if (action.auto_created) {
        const status = document.createElement("div");
        status.className = "email-chat-auto-status";
        status.textContent = "Билет прошёл порог 99% и добавлен автоматически.";
        card.append(status);
      } else if (action.already_in_calendar) {
        const status = document.createElement("div");
        status.className = "email-chat-auto-status";
        status.textContent = "Это событие уже есть в календаре — дубль не создаю.";
        card.append(status);
      } else if (action.auto_create_error) {
        const status = document.createElement("div");
        status.className = "email-plan-warnings";
        status.textContent = action.auto_create_error;
        card.append(status);
      }

      renderWarnings(card, action.warnings);
      const controls = document.createElement("div");
      controls.className = "email-chat-action-controls";
      controls.append(makeActionButton(action, {chat: true}));
      card.append(controls);
      chat.append(card);
    }
    if (attachmentActions.length) chat.scrollTop = chat.scrollHeight;
  }

  function renderPlan(plan) {
    const target = document.getElementById("emailPlanResults");
    target.replaceChildren();
    const summary = document.createElement("p");
    summary.className = "settings-help";
    summary.textContent = plan.summary || "Ничего важного для планирования не нашёл.";
    target.append(summary);

    const attachmentInfo = plan.attachment_analysis || {};
    renderAttachmentStatus(target, attachmentInfo);
    renderWarnings(target, attachmentInfo.warnings);

    for (const action of plan.actions || []) {
      const card = document.createElement("div");
      card.className = "email-plan-card";
      if (action.attachment_event) card.classList.add("email-plan-attachment-card");
      const title = document.createElement("strong");
      title.textContent = `${labelType(action.action_type)} · ${action.title}`;
      const meta = document.createElement("div");
      meta.className = "settings-help";
      const parts = [];
      if (action.due_at) parts.push(formatWhen(action.due_at));
      if (action.duration_minutes) parts.push(`${action.duration_minutes} мин`);
      if (action.source?.subject) parts.push(`Письмо: ${action.source.subject}`);
      if (action.source?.attachment) parts.push(`Вложение: ${action.source.attachment}`);
      if (Number.isFinite(action.confidence)) parts.push(`уверенность ${Math.round(action.confidence * 100)}%`);
      meta.textContent = parts.join(" · ");

      const event = action.attachment_event;
      const eventDetails = document.createElement("div");
      eventDetails.className = "email-plan-event-details settings-help";
      if (event) {
        const details = [];
        if (event.location) details.push(event.location);
        if (event.start && event.end) details.push(`${formatWhen(event.start)} → ${formatWhen(event.end)}`);
        eventDetails.textContent = details.join("\n");
      }

      const reason = document.createElement("div");
      reason.className = "settings-help";
      reason.textContent = action.reason || "";
      card.append(title, meta);
      if (eventDetails.textContent) card.append(eventDetails);
      card.append(reason);
      renderWarnings(card, action.warnings);
      card.append(makeActionButton(action, {chat: false}));
      target.append(card);
    }
    if (plan.draft_reply) {
      const draft = document.createElement("div");
      draft.className = "email-plan-draft";
      const heading = document.createElement("strong");
      heading.textContent = "Черновик ответа";
      const text = document.createElement("textarea");
      text.value = plan.draft_reply;
      text.rows = 6;
      text.setAttribute("aria-label", "Черновик ответа на письмо");
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "action";
      copy.textContent = "Копировать";
      copy.onclick = async () => {
        await navigator.clipboard.writeText(text.value);
        notify("Черновик скопирован. Письмо не отправлялось.");
      };
      const warning = document.createElement("p");
      warning.className = "settings-help";
      warning.textContent = "Это только текст черновика. Секретарь не умеет отправлять его без твоего отдельного действия.";
      draft.append(heading, text, copy, warning);
      target.append(draft);
    }
  }

  async function analyze() {
    const button = document.getElementById("emailPlanButton");
    button.disabled = true;
    notify("Разбираю последние письма и поддерживаемые вложения…");
    try {
      const plan = await api("/api/email/plan", {
        method: "POST",
        body: JSON.stringify({request: "Что из последних писем и вложений влияет на мои ближайшие планы и требует действия?"}),
      });
      renderPlan(plan);
      const created = Number(plan.auto_calendar?.created || 0);
      if (created) notify(`Автоматически добавлено билетов: ${created}. Остальные предложения можно подтвердить вручную.`);
      else notify((plan.actions || []).length ? `Найдено предложений: ${plan.actions.length}.` : "Действий, которые можно уверенно предложить, не нашёл.");
    } catch (error) {
      notify(error.message);
    } finally {
      button.disabled = false;
    }
  }

  function installStyles() {
    if (document.getElementById("emailActionStyles")) return;
    const style = document.createElement("style");
    style.id = "emailActionStyles";
    style.textContent = `
      .email-plan-divider{border:0;border-top:1px solid #e5e5e2;margin:16px 0}
      .email-plan-card,.email-plan-draft{padding:12px;margin:9px 0;border:1px solid #e4e4e1;border-radius:14px;background:#fff;display:grid;gap:7px}
      .email-plan-attachment-card{border-style:solid}
      .email-plan-attachment-status{padding:8px 10px;border-radius:10px;background:#f5f5f2;margin:6px 0}
      .email-plan-event-details{white-space:pre-line;font-weight:600}
      .email-plan-warnings{display:grid;gap:4px;font-size:12px;line-height:1.35;color:#6b5b28}
      .email-plan-draft textarea{width:100%;box-sizing:border-box;padding:10px;border:1px solid #ddd;border-radius:10px;font:inherit;resize:vertical}
      .email-chat-action-card{max-width:min(92%,560px)!important;width:min(92%,560px);background:#fff!important;border:1px solid #dfdfdc;border-radius:17px!important;display:grid;gap:7px}
      .email-chat-action-title{font-weight:680}
      .email-chat-action-meta{color:#6f6f6b;font-size:12px;line-height:1.45;white-space:pre-line}
      .email-chat-auto-status{font-size:12px;line-height:1.4;color:#4d6a52}
      .email-chat-action-controls{display:flex;gap:8px;margin-top:3px}
      .email-chat-action-controls .action{padding:9px 12px;font-size:12px}
    `;
    document.head.append(style);
  }

  function install() {
    installStyles();
    if (installed) return;
    const group = document.getElementById("emailGroup");
    if (!group) return;
    installed = true;
    const block = document.createElement("div");
    block.className = "email-plan-block";
    block.innerHTML = `
      <hr class="email-plan-divider" />
      <div class="field">
        <strong>Почта → планирование</strong>
        <span class="settings-help">ИИ анализирует текст письма и поддерживаемые вложения. Транспортные билеты с уверенностью 99%+ и проверенными датами/часовыми поясами добавляются автоматически; остальные действия требуют подтверждения.</span>
      </div>
      <button id="emailPlanButton" class="action" type="button">Разобрать последние письма</button>
      <p id="emailPlanState" class="settings-help" role="status" aria-live="polite"></p>
      <div id="emailPlanResults"></div>`;
    group.append(block);
    document.getElementById("emailPlanButton").onclick = analyze;
  }

  document.addEventListener("planner-result", (event) => {
    const plan = event.detail?.email_plan;
    if (plan) renderChatPlan(plan);
  });
  document.addEventListener("planner-ready", () => setTimeout(install, 0));
  const observer = new MutationObserver(() => install());
  observer.observe(document.documentElement, {childList: true, subtree: true});
  if (document.readyState !== "loading") setTimeout(install, 50);
})();
