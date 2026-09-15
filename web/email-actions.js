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

  function formatWhen(value) {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("ru-RU");
  }

  async function applyAction(action, button) {
    if (action.ready === false) {
      notify("Сначала проверь данные: ИИ не уверен в этом событии.");
      return;
    }
    const event = action.attachment_event;
    const when = event?.start ? formatWhen(event.start) : (action.due_at ? formatWhen(action.due_at) : "без точного времени");
    const route = event?.location ? `\n${event.location}` : "";
    if (!confirm(`${labelType(action.action_type)}: «${action.title}»\n${when}${route}\n\nСоздать?`)) return;
    button.disabled = true;
    try {
      await api("/api/email/action", {method: "POST", body: JSON.stringify(action)});
      button.textContent = "Создано";
      button.dataset.applied = "1";
      document.dispatchEvent(new Event("planner-library-changed"));
      notify(`Создано: ${action.title}`);
    } catch (error) {
      notify(error.message);
      button.disabled = false;
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

  function renderPlan(plan) {
    const target = document.getElementById("emailPlanResults");
    target.replaceChildren();
    const summary = document.createElement("p");
    summary.className = "settings-help";
    summary.textContent = plan.summary || "Ничего важного для планирования не нашёл.";
    target.append(summary);

    const attachmentInfo = plan.attachment_analysis || {};
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
      const button = document.createElement("button");
      button.className = "action";
      button.type = "button";
      button.textContent = action.ready === false ? "Нужна проверка" : "Подтвердить и создать";
      button.disabled = action.ready === false;
      button.onclick = () => applyAction(action, button);
      card.append(title, meta);
      if (eventDetails.textContent) card.append(eventDetails);
      card.append(reason);
      renderWarnings(card, action.warnings);
      card.append(button);
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
    notify("Разбираю последние письма и поддерживаемые вложения. Ничего не создаю и не отправляю…");
    try {
      const plan = await api("/api/email/plan", {
        method: "POST",
        body: JSON.stringify({request: "Что из последних писем и вложений влияет на мои ближайшие планы и требует действия?"}),
      });
      renderPlan(plan);
      notify((plan.actions || []).length ? `Найдено предложений: ${plan.actions.length}.` : "Действий, которые можно уверенно предложить, не нашёл.");
    } catch (error) {
      notify(error.message);
    } finally {
      button.disabled = false;
    }
  }

  function install() {
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
        <span class="settings-help">ИИ анализирует текст письма и поддерживаемые вложения (например PDF-билет), затем предлагает задачу, напоминание, событие или черновик ответа. Ничего не создаётся без твоего подтверждения.</span>
      </div>
      <button id="emailPlanButton" class="action" type="button">Разобрать последние письма</button>
      <p id="emailPlanState" class="settings-help" role="status" aria-live="polite"></p>
      <div id="emailPlanResults"></div>`;
    group.append(block);
    document.getElementById("emailPlanButton").onclick = analyze;
    const style = document.createElement("style");
    style.textContent = `
      .email-plan-divider{border:0;border-top:1px solid #e5e5e2;margin:16px 0}
      .email-plan-card,.email-plan-draft{padding:12px;margin:9px 0;border:1px solid #e4e4e1;border-radius:14px;background:#fff;display:grid;gap:7px}
      .email-plan-attachment-card{border-style:solid}
      .email-plan-event-details{white-space:pre-line;font-weight:600}
      .email-plan-warnings{display:grid;gap:4px;font-size:12px;line-height:1.35;color:#6b5b28}
      .email-plan-draft textarea{width:100%;box-sizing:border-box;padding:10px;border:1px solid #ddd;border-radius:10px;font:inherit;resize:vertical}
    `;
    document.head.append(style);
  }

  document.addEventListener("planner-ready", () => setTimeout(install, 0));
  const observer = new MutationObserver(() => install());
  observer.observe(document.documentElement, {childList: true, subtree: true});
  if (document.readyState !== "loading") setTimeout(install, 50);
})();
