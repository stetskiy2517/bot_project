(() => {
  const esc = (value) => String(value ?? "").replace(/[&<>\"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[char]);

  function setConnectionStatus(message, state = "") {
    const status = document.getElementById("emailConnectionStatus");
    if (!status) return;
    status.textContent = message;
    status.dataset.state = state;
  }

  async function loadAccounts() {
    const target = document.getElementById("emailAccounts");
    const meta = document.getElementById("emailMeta");
    if (!target || typeof window.api !== "function") return;
    try {
      const data = await window.api("/api/email/accounts");
      const accounts = Array.isArray(data.accounts) ? data.accounts : [];
      meta.textContent = accounts.length ? `${accounts.length} подключено` : "Не подключена";
      target.innerHTML = accounts.length
        ? accounts.map((item) => `
          <div class="field" data-email-account="${item.account_id}">
            <div><strong>${esc(item.display_name || item.provider)}</strong><br><span class="settings-help">${esc(item.email)}</span></div>
            <button class="action" type="button" data-email-remove="${item.account_id}">Отключить</button>
          </div>`).join("")
        : '<div class="field"><span class="settings-help">Подключи ящик. Сейчас модуль только читает письма и ничего не отправляет.</span></div>';
      target.querySelectorAll("[data-email-remove]").forEach((button) => {
        button.onclick = async () => {
          if (!confirm("Отключить этот почтовый ящик?")) return;
          await window.api(`/api/email/accounts/${button.dataset.emailRemove}`, {method: "DELETE"});
          setConnectionStatus("Почтовый ящик отключён.", "success");
          await loadAccounts();
        };
      });
    } catch (error) {
      meta.textContent = "Ошибка";
      target.innerHTML = '<div class="field"><span class="settings-help">Не удалось загрузить подключения почты.</span></div>';
    }
  }

  async function connectGmail() {
    setConnectionStatus("Открываю авторизацию Google…", "pending");
    try {
      const data = await window.api("/api/email/google/connect");
      if (!data.url) throw new Error("Google не вернул ссылку авторизации");
      location.href = data.url;
    } catch (error) {
      setConnectionStatus("Не удалось начать подключение Gmail: " + error.message, "error");
    }
  }

  async function connectImap() {
    const provider = document.getElementById("emailProvider").value;
    const email = document.getElementById("emailAddress").value.trim();
    const appPassword = document.getElementById("emailAppPassword").value;
    if (!email || !appPassword) {
      setConnectionStatus("Укажи email и пароль приложения.", "error");
      return;
    }
    const button = document.getElementById("connectEmailImap");
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "Проверяю…";
    setConnectionStatus("Проверяю адрес и пароль приложения…", "pending");
    try {
      await window.api("/api/email/accounts/imap", {
        method: "POST",
        body: JSON.stringify({provider, email, app_password: appPassword}),
      });
      document.getElementById("emailAppPassword").value = "";
      setConnectionStatus(`Подключено: ${email}. Почту уже можно проверять через чат или голосом.`, "success");
      await loadAccounts();
    } catch (error) {
      setConnectionStatus("Не удалось подключить почту: " + error.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  }

  function applyOAuthResult() {
    const url = new URL(location.href);
    const result = url.searchParams.get("email");
    if (!result) return;
    if (result === "connected") {
      setConnectionStatus("Gmail подключён. Почту уже можно проверять через чат или голосом.", "success");
    } else if (result === "stale") {
      setConnectionStatus("Сессия подключения Gmail устарела. Запусти подключение ещё раз.", "error");
    } else if (result === "error") {
      setConnectionStatus("Не удалось подключить Gmail. Попробуй ещё раз.", "error");
    }
    url.searchParams.delete("email");
    history.replaceState(null, "", url.pathname + url.search + url.hash);
  }

  function install() {
    if (document.getElementById("emailGroup")) {
      loadAccounts();
      return;
    }
    const anchor = document.getElementById("categoryColorsGroup");
    if (!anchor) return;
    const group = document.createElement("details");
    group.id = "emailGroup";
    group.className = "settings-group";
    group.innerHTML = `
      <summary>
        <span class="settings-group-title">Почта</span>
        <span id="emailMeta" class="settings-group-meta">Не подключена</span>
        <svg class="settings-group-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
      </summary>
      <div id="emailAccounts" class="grid"></div>
      <div class="grid" style="margin-top:10px">
        <div class="field">
          <button id="connectGmail" class="action" type="button">Подключить Gmail</button>
          <span class="settings-help">Доступ только на чтение.</span>
        </div>
      </div>
      <form id="emailImapForm" class="grid" style="margin-top:10px">
        <label class="field">Провайдер
          <select id="emailProvider"><option value="yandex">Яндекс</option><option value="mailru">Mail.ru</option></select>
        </label>
        <label class="field">Email<input id="emailAddress" type="email" autocomplete="email" placeholder="name@example.ru" required /></label>
        <label class="field">Пароль приложения<input id="emailAppPassword" type="password" autocomplete="new-password" placeholder="Не основной пароль" required /></label>
        <div class="field"><button id="connectEmailImap" class="action" type="submit">Подключить ящик</button></div>
        <p id="emailConnectionStatus" class="settings-help" role="status" aria-live="polite"></p>
      </form>
      <p class="settings-help">Пароль приложения шифруется на сервере. Отправка писем в этой версии отключена.</p>`;
    anchor.parentNode.insertBefore(group, anchor);
    document.getElementById("connectGmail").onclick = connectGmail;
    document.getElementById("emailImapForm").onsubmit = (event) => {
      event.preventDefault();
      connectImap();
    };
    applyOAuthResult();
    loadAccounts();
  }

  document.addEventListener("planner-ready", install);
  if (document.readyState !== "loading") setTimeout(install, 0);
})();
