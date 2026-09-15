(() => {
  let csrfToken = "";
  let searchTimer = null;

  const byId = (id) => document.getElementById(id);
  const usersBody = byId("usersBody");
  const auditBody = byId("auditBody");
  const userStatus = byId("userStatus");

  function requestId() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  async function api(path, options = {}) {
    const config = { ...options, headers: { ...(options.headers || {}) } };
    if (config.method && !["GET", "HEAD", "OPTIONS"].includes(config.method.toUpperCase())) {
      config.headers["X-CSRF-Token"] = csrfToken;
      config.headers["X-Request-ID"] = requestId();
      config.headers["Content-Type"] = "application/json";
    }
    const response = await fetch(path, config);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.message || payload.error || `HTTP ${response.status}`);
    }
    return payload;
  }

  function formatDate(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
  }

  function cell(text, className = "") {
    const td = document.createElement("td");
    td.textContent = text ?? "—";
    if (className) td.className = className;
    return td;
  }

  function aiLabel(feature) {
    if (feature.enabled) {
      if (feature.explicit?.expires_at) return `Включён до ${formatDate(feature.explicit.expires_at)}`;
      return feature.explicit ? "Включён вручную" : "Включён по beta";
    }
    return feature.explicit?.enabled === false ? "Выключен вручную" : "Недоступен";
  }

  function renderUsers(users) {
    usersBody.replaceChildren();
    if (!users.length) {
      const row = document.createElement("tr");
      const empty = cell("Ничего не найдено", "muted");
      empty.colSpan = 6;
      row.append(empty);
      usersBody.append(row);
      return;
    }

    for (const user of users) {
      const row = document.createElement("tr");
      row.append(cell(String(user.id)));

      const identity = document.createElement("td");
      const name = document.createElement("div");
      name.textContent = user.name || "Без имени";
      const email = document.createElement("div");
      email.className = "muted";
      email.textContent = user.email;
      identity.append(name, email);
      row.append(identity);

      row.append(cell(formatDate(user.created_at)));
      const role = document.createElement("td");
      const rolePill = document.createElement("span");
      rolePill.className = "pill";
      rolePill.textContent = user.role;
      role.append(rolePill);
      row.append(role);

      const feature = user.features.ai;
      const aiCell = document.createElement("td");
      const aiPill = document.createElement("span");
      aiPill.className = `pill ${feature.enabled ? "on" : "off"}`;
      aiPill.textContent = aiLabel(feature);
      aiCell.append(aiPill);
      row.append(aiCell);

      const action = document.createElement("td");
      const button = document.createElement("button");
      button.type = "button";
      button.className = feature.enabled ? "toggle secondary" : "toggle";
      button.textContent = feature.enabled ? "Выключить" : "Включить";
      button.addEventListener("click", async () => {
        button.disabled = true;
        userStatus.textContent = "Сохраняю…";
        userStatus.classList.remove("error");
        try {
          await api(`/api/admin/users/${user.id}/features/ai`, {
            method: "PATCH",
            body: JSON.stringify({ enabled: !feature.enabled, expires_at: null }),
          });
          await Promise.all([loadUsers(), loadOverview(), loadAudit()]);
          userStatus.textContent = "Доступ обновлён.";
        } catch (error) {
          userStatus.textContent = `Ошибка: ${error.message}`;
          userStatus.classList.add("error");
          button.disabled = false;
        }
      });
      action.append(button);
      row.append(action);
      usersBody.append(row);
    }
  }

  function renderAudit(events) {
    auditBody.replaceChildren();
    if (!events.length) {
      const row = document.createElement("tr");
      const empty = cell("Журнал пока пуст", "muted");
      empty.colSpan = 5;
      row.append(empty);
      auditBody.append(row);
      return;
    }
    for (const event of events) {
      const row = document.createElement("tr");
      row.append(cell(formatDate(event.created_at)));
      row.append(cell(event.admin_email || `#${event.admin_user_id}`));
      row.append(cell(event.action));
      row.append(cell(event.target_email || (event.target_user_id ? `#${event.target_user_id}` : "—")));
      const details = event.details || {};
      row.append(cell(details.feature ? `${details.feature}: ${details.enabled ? "ON" : "OFF"}` : "—", "muted"));
      auditBody.append(row);
    }
  }

  async function loadOverview() {
    const payload = await api("/api/admin/overview");
    csrfToken = payload.csrf_token || csrfToken;
    const overview = payload.overview || {};
    byId("totalUsers").textContent = overview.users ?? "—";
    byId("managedAi").textContent = overview.managed_ai ?? "—";
    byId("aiMode").textContent = overview.ai_mode ?? "—";
    byId("auditCount").textContent = overview.audit_events ?? "—";
  }

  async function loadUsers() {
    const query = byId("userSearch").value.trim();
    const payload = await api(`/api/admin/users?q=${encodeURIComponent(query)}`);
    renderUsers(payload.users || []);
  }

  async function loadAudit() {
    const payload = await api("/api/admin/audit?limit=100");
    renderAudit(payload.events || []);
  }

  async function start() {
    try {
      await loadOverview();
      await Promise.all([loadUsers(), loadAudit()]);
      userStatus.textContent = "";
    } catch (error) {
      userStatus.textContent = `Не удалось загрузить админку: ${error.message}`;
      userStatus.classList.add("error");
    }
  }

  byId("userSearch").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadUsers().catch((error) => {
      userStatus.textContent = `Ошибка поиска: ${error.message}`;
      userStatus.classList.add("error");
    }), 250);
  });

  start();
})();
