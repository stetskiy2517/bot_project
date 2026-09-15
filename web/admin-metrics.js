(() => {
  "use strict";

  async function request(path) {
    const response = await fetch(path, {credentials: "same-origin"});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.message || payload.error || `HTTP ${response.status}`);
    return payload;
  }

  function valueCard(label, value) {
    const card = document.createElement("div");
    card.className = "card";
    const small = document.createElement("small");
    small.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = String(value ?? "—");
    card.append(small, strong);
    return card;
  }

  function healthRow(label, value, ok = true) {
    const row = document.createElement("div");
    row.className = "admin-health-row";
    const name = document.createElement("span");
    name.textContent = label;
    const pill = document.createElement("span");
    pill.className = `pill ${ok ? "on" : "off"}`;
    pill.textContent = value;
    row.append(name, pill);
    return row;
  }

  function render(data) {
    const metrics = data.metrics || {};
    const health = data.health || {};
    const shell = document.querySelector(".shell");
    if (!shell || document.getElementById("productMetricsPanel")) return;

    const cards = document.createElement("section");
    cards.id = "productMetricsPanel";
    cards.className = "cards admin-extra-cards";
    cards.setAttribute("aria-label", "Продуктовые метрики");
    cards.append(
      valueCard("Заметок", metrics.objects?.notes),
      valueCard("Напоминаний", metrics.objects?.reminders),
      valueCard("Задач", metrics.objects?.tasks),
      valueCard("AI memory events · 7д", metrics.activity?.memory_events_7d),
      valueCard("Новые задачи · 7д", metrics.activity?.tasks_7d),
      valueCard("Новые заметки · 7д", metrics.activity?.notes_7d),
      valueCard("Новые напоминания · 7д", metrics.activity?.reminders_7d),
      valueCard("Proactive actions", metrics.objects?.proactive_actions),
    );

    const panel = document.createElement("section");
    panel.className = "panel";
    panel.innerHTML = `<div class="panel-head"><div><h2>Система и интеграции</h2><div class="status">Только агрегаты и техническое состояние. Содержимое писем, заметок и календарей здесь не показывается.</div></div></div>`;
    const grid = document.createElement("div");
    grid.className = "admin-health-grid";
    const ai = health.ai || {};
    const aiAvailable = typeof ai.available === "boolean"
      ? ai.available
      : Boolean(ai.enabled && ai.configured);
    const aiLabel = aiAvailable
      ? [ai.provider || "включён", ai.model].filter(Boolean).join(" · ")
      : "недоступен";
    grid.append(
      healthRow("ИИ", aiLabel, aiAvailable),
      healthRow("Speech · AssemblyAI", health.speech?.providers?.assemblyai ? "настроен" : "нет", Boolean(health.speech?.providers?.assemblyai)),
      healthRow("Speech · Yandex", health.speech?.providers?.yandex ? "настроен" : "нет", Boolean(health.speech?.providers?.yandex)),
      healthRow("Навигация", health.navigation?.configured ? (health.navigation?.provider || "настроена") : "нет", Boolean(health.navigation?.configured)),
      healthRow("Google Calendar", `${metrics.integrations?.google_calendar_tokens || 0} подключений`, true),
      healthRow("Почта", `${metrics.integrations?.email_accounts || 0} ящиков`, true),
      healthRow("Push", `${metrics.integrations?.push_subscriptions || 0} подписок`, true),
      healthRow("Навигация включена", `${metrics.integrations?.navigation_enabled || 0} пользователей`, true),
    );
    const identity = document.createElement("div");
    identity.className = "status admin-identity-line";
    identity.textContent = "Способы входа: " + Object.entries(metrics.identities || {}).map(([provider, count]) => `${provider}: ${count}`).join(" · ");
    panel.append(grid, identity);

    const reference = document.querySelector(".shell > .panel");
    shell.insertBefore(cards, reference || null);
    shell.insertBefore(panel, reference || null);

    const style = document.createElement("style");
    style.textContent = `
      .admin-extra-cards{grid-template-columns:repeat(4,minmax(0,1fr))}
      .admin-health-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
      .admin-health-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 12px;border:1px solid #eee;border-radius:12px}
      .admin-identity-line{margin-top:12px}
      @media(max-width:760px){.admin-extra-cards,.admin-health-grid{grid-template-columns:1fr 1fr}}
    `;
    document.head.append(style);
  }

  async function load() {
    try { render(await request("/api/admin/metrics")); }
    catch (error) { console.error("Admin metrics unavailable", error); }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", load);
  else load();
})();
