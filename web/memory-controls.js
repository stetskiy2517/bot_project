(() => {
  "use strict";

  let installed = false;
  let activeTab = "personal";
  const kindLabels = {
    fact: "Факт", preference: "Предпочтение", habit: "Привычка",
    relationship: "Связь", goal: "Цель", observation: "Наблюдение",
  };

  function api(path, options) {
    if (typeof window.api !== "function") throw new Error("API недоступен");
    return window.api(path, options);
  }

  function textValue(value) {
    if (typeof value === "string") return value;
    try { return JSON.stringify(value, null, 2); } catch (_) { return String(value ?? ""); }
  }

  function sourceLabel(memory) {
    const labels = {
      note: "заметка", reminder: "напоминание", calendar_event: "календарь",
      voice_transcript: "голос/чат", email: "почта", user_correction: "исправлено пользователем",
    };
    return labels[memory.source_type] || memory.source_type || "источник не указан";
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
  }

  function hideSettingsForMemory() {
    const panel = document.getElementById("settingsPanel");
    if (!panel?.classList.contains("open")) return;
    panel.classList.remove("open");
  }

  function restoreSettingsAfterMemory() {
    const panel = document.getElementById("settingsPanel");
    if (!panel) return;
    window.PlannerSettingsThemes?.back?.();
    panel.classList.add("open");
  }

  function screen() {
    return document.getElementById("memoryScreen");
  }

  function openScreen() {
    const root = screen();
    if (!root) return;
    hideSettingsForMemory();
    root.classList.add("open");
    root.setAttribute("aria-hidden", "false");
    load();
  }

  function closeScreen() {
    const root = screen();
    if (!root) return;
    root.classList.remove("open");
    root.setAttribute("aria-hidden", "true");
    restoreSettingsAfterMemory();
  }

  function setTab(name) {
    activeTab = name === "work" ? "work" : "personal";
    document.querySelectorAll(".memory-tab").forEach(button => {
      button.classList.toggle("active", button.dataset.memoryTab === activeTab);
    });
    document.querySelectorAll(".memory-pane").forEach(pane => {
      pane.hidden = pane.dataset.memoryPane !== activeTab;
    });
  }

  async function loadPersonal() {
    const box = document.getElementById("memoryPersonalList");
    const state = document.getElementById("memoryState");
    box.innerHTML = '<p class="memory-empty">Загружаю…</p>';
    const data = await api("/api/memory/controls");
    const memories = data.memories || [];
    box.replaceChildren();
    if (!memories.length) {
      box.innerHTML = '<p class="memory-empty">Пока нет устойчивых фактов. Память будет наполняться по мере работы с секретарём.</p>';
    }
    for (const memory of memories) {
      const card = document.createElement("article");
      card.className = "memory-card";
      card.innerHTML = `
        <div class="memory-card-kicker">${escapeHtml(kindLabels[memory.kind] || memory.kind)} · ${Math.round((memory.confidence || 0) * 100)}%</div>
        <div class="memory-card-value">${escapeHtml(textValue(memory.value))}</div>
        <div class="memory-card-source">Источник: ${escapeHtml(sourceLabel(memory))}${memory.evidence ? " · " + escapeHtml(memory.evidence) : ""}</div>
        <div class="memory-card-actions">
          <button type="button" data-memory-edit="${memory.memory_id}">Исправить</button>
          <button type="button" class="danger" data-memory-forget="${memory.memory_id}">Забыть</button>
        </div>`;
      box.appendChild(card);
    }
    const summary = data.feedback_summary || {};
    state.textContent = `Фактов в активной памяти: ${memories.length}. Оценено предложений: ${(summary.useful || 0) + (summary.dismiss || 0) + (summary.never || 0)}.`;
  }

  function companyName(companies, id) {
    return companies.find(item => Number(item.company_id) === Number(id))?.name || "";
  }

  function contactName(contacts, id) {
    return contacts.find(item => Number(item.contact_id) === Number(id))?.full_name || "";
  }

  async function loadWork() {
    const box = document.getElementById("memoryWorkContent");
    box.innerHTML = '<p class="memory-empty">Загружаю…</p>';
    const data = await api("/api/memory/work-context");
    const companies = data.companies || [];
    const contacts = data.contacts || [];
    const interactions = data.interactions || [];
    const commitments = data.commitments || [];

    const companyRows = companies.length ? companies.map(item => `
      <div class="memory-list-row">
        <div><strong>${escapeHtml(item.name)}</strong>${item.industry ? `<span>${escapeHtml(item.industry)}</span>` : ""}</div>
        <button type="button" data-memory-edit-company="${item.company_id}">Изменить</button>
      </div>`).join("") : '<p class="memory-empty">Компаний пока нет.</p>';

    const contactRows = contacts.length ? contacts.map(item => `
      <div class="memory-list-row">
        <div><strong>${escapeHtml(item.full_name)}</strong>
          <span>${escapeHtml([item.position, companyName(companies, item.company_id)].filter(Boolean).join(" · "))}</span>
        </div>
        <button type="button" data-memory-edit-contact="${item.contact_id}">Изменить</button>
      </div>`).join("") : '<p class="memory-empty">Контактов пока нет.</p>';

    const commitmentRows = commitments.length ? commitments.map(item => `
      <div class="memory-list-row">
        <div><strong>${escapeHtml(item.title)}</strong>
          <span>${escapeHtml([companyName(companies, item.company_id), contactName(contacts, item.contact_id), item.due_at ? new Date(item.due_at).toLocaleString("ru-RU") : ""].filter(Boolean).join(" · "))}</span>
        </div>
        <button type="button" data-memory-done="${item.commitment_id}">Готово</button>
      </div>`).join("") : '<p class="memory-empty">Открытых договорённостей пока нет.</p>';

    const interactionRows = interactions.length ? interactions.slice(0, 30).map(item => `
      <div class="memory-list-row">
        <div><strong>${escapeHtml(item.summary)}</strong>
          <span>${escapeHtml([item.interaction_type, companyName(companies, item.company_id), contactName(contacts, item.contact_id)].filter(Boolean).join(" · "))}</span>
        </div>
      </div>`).join("") : '<p class="memory-empty">История взаимодействий пока пустая.</p>';

    box.innerHTML = `
      <section class="memory-section">
        <div class="memory-section-head"><h3>Поиск</h3></div>
        <div class="memory-search"><input id="memoryWorkSearch" type="search" placeholder="Компания, человек, договорённость…" autocomplete="off"><button type="button" data-memory-search>Найти</button></div>
        <div id="memorySearchResults"></div>
      </section>
      <section class="memory-section">
        <div class="memory-section-head"><h3>Компании</h3><button type="button" data-memory-add="company">Добавить</button></div>
        <div class="memory-list">${companyRows}</div>
      </section>
      <section class="memory-section">
        <div class="memory-section-head"><h3>Контакты</h3><button type="button" data-memory-add="contact">Добавить</button></div>
        <div class="memory-list">${contactRows}</div>
      </section>
      <section class="memory-section">
        <div class="memory-section-head"><h3>Договорённости</h3><button type="button" data-memory-add="commitment">Добавить</button></div>
        <div class="memory-list">${commitmentRows}</div>
      </section>
      <section class="memory-section">
        <div class="memory-section-head"><h3>История</h3><button type="button" data-memory-add="interaction">Добавить</button></div>
        <div class="memory-list">${interactionRows}</div>
      </section>`;
  }

  async function load() {
    const status = document.getElementById("memoryScreenStatus");
    status.textContent = "";
    try {
      if (activeTab === "personal") await loadPersonal();
      else await loadWork();
    } catch (error) {
      status.textContent = String(error.message || error);
    }
  }

  async function editMemory(id) {
    const data = await api("/api/memory/controls");
    const memory = (data.memories || []).find(item => Number(item.memory_id) === Number(id));
    if (!memory) return;
    const next = prompt("Что секретарь должен помнить вместо этого?", textValue(memory.value));
    if (next == null || !next.trim()) return;
    await api(`/api/memory/${id}`, {method: "PATCH", body: JSON.stringify({value: next.trim()})});
    await loadPersonal();
  }

  async function forgetMemory(id) {
    if (!confirm("Убрать этот факт из активной памяти?")) return;
    await api(`/api/memory/${id}`, {method: "DELETE"});
    await loadPersonal();
  }

  async function addWorkItem(type) {
    if (type === "company") {
      const name = prompt("Название компании");
      if (!name?.trim()) return;
      await api("/api/memory/companies", {method: "POST", body: JSON.stringify({name: name.trim()})});
    } else if (type === "contact") {
      const fullName = prompt("Имя контакта");
      if (!fullName?.trim()) return;
      await api("/api/memory/contacts", {method: "POST", body: JSON.stringify({full_name: fullName.trim()})});
    } else if (type === "commitment") {
      const title = prompt("Что нужно не забыть по клиенту?");
      if (!title?.trim()) return;
      await api("/api/memory/commitments", {method: "POST", body: JSON.stringify({title: title.trim()})});
    } else if (type === "interaction") {
      const summary = prompt("Кратко: что произошло?");
      if (!summary?.trim()) return;
      await api("/api/memory/interactions", {
        method: "POST",
        body: JSON.stringify({interaction_type: "note", summary: summary.trim()}),
      });
    }
    await loadWork();
  }


  async function searchWork() {
    const input = document.getElementById("memoryWorkSearch");
    const results = document.getElementById("memorySearchResults");
    const query = input?.value?.trim();
    if (!query || query.length < 2) return;
    const data = await api("/api/memory/search?q=" + encodeURIComponent(query));
    const lines = [];
    for (const item of data.companies || []) lines.push("Компания: " + item.name);
    for (const item of data.contacts || []) lines.push("Контакт: " + item.full_name + (item.position ? " · " + item.position : ""));
    for (const item of data.commitments || []) lines.push("Договорённость: " + item.title);
    for (const item of data.interactions || []) lines.push("История: " + item.summary);
    results.innerHTML = lines.length ? '<div class="memory-search-result">' + lines.map(line => "<p>" + escapeHtml(line) + "</p>").join("") + "</div>" : '<p class="memory-empty">Ничего не найдено.</p>';
  }

  async function editCompany(id) {
    const data = await api("/api/memory/work-context");
    const item = (data.companies || []).find(row => Number(row.company_id) === Number(id));
    if (!item) return;
    const name = prompt("Название компании", item.name || "");
    if (!name?.trim()) return;
    const industry = prompt("Отрасль", item.industry || "");
    if (industry == null) return;
    await api("/api/memory/companies/" + id, {method:"PATCH", body:JSON.stringify({name:name.trim(), industry:industry.trim()})});
    await loadWork();
  }

  async function editContact(id) {
    const data = await api("/api/memory/work-context");
    const item = (data.contacts || []).find(row => Number(row.contact_id) === Number(id));
    if (!item) return;
    const name = prompt("Имя контакта", item.full_name || "");
    if (!name?.trim()) return;
    const position = prompt("Должность", item.position || "");
    if (position == null) return;
    await api("/api/memory/contacts/" + id, {method:"PATCH", body:JSON.stringify({full_name:name.trim(), position:position.trim()})});
    await loadWork();
  }

  async function completeCommitment(id) {
    await api("/api/memory/commitments/" + id, {method:"PATCH", body:JSON.stringify({status:"done"})});
    await loadWork();
  }

  function placeSettingsEntry() {
    const settingsRoot = document.querySelector("#settingsPanel .sheet");
    if (!settingsRoot) return null;

    let entry = document.getElementById("openMemoryScreen");
    if (!entry) {
      entry = document.createElement("button");
      entry.id = "openMemoryScreen";
      entry.className = "memory-settings-entry settings-theme-link";
      entry.type = "button";
      entry.innerHTML = '<span class="settings-theme-link-title">Память</span><svg class="settings-theme-link-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 7 7-7 7"></path></svg>';
      entry.addEventListener("click", openScreen);
    }

    const themesMenu = document.getElementById("settingsThemes");
    if (!themesMenu) return null;
    if (entry.parentElement !== themesMenu) themesMenu.appendChild(entry);
    return entry;
  }

  function install() {
    const settingsRoot = document.querySelector("#settingsPanel .sheet");
    if (!settingsRoot) return;
    placeSettingsEntry();
    if (installed) return;
    installed = true;

    let root = document.getElementById("memoryScreen");
    if (!root) {
      root = document.createElement("div");
      root.id = "memoryScreen";
      root.className = "memory-screen";
      root.setAttribute("aria-hidden", "true");
      root.innerHTML = `
        <div class="memory-screen-shell">
          <header class="memory-screen-head">
            <button id="closeMemoryScreen" class="memory-back" type="button" aria-label="Назад">‹</button>
            <div><h2>Память</h2><p>Единая память секретаря</p></div>
          </header>
          <div class="memory-tabs" role="tablist">
            <button class="memory-tab active" type="button" data-memory-tab="personal">О тебе</button>
            <button class="memory-tab" type="button" data-memory-tab="work">Работа</button>
          </div>
          <div class="memory-scroll">
            <section class="memory-pane" data-memory-pane="personal">
              <div id="memoryPersonalList"></div>
              <p id="memoryState" class="memory-state"></p>
            </section>
            <section class="memory-pane" data-memory-pane="work" hidden>
              <div id="memoryWorkContent"></div>
            </section>
            <p id="memoryScreenStatus" class="memory-state" role="status"></p>
          </div>
        </div>`;
      document.body.appendChild(root);
    }
    document.getElementById("closeMemoryScreen")?.addEventListener("click", closeScreen);

    root.addEventListener("click", event => {
      const tab = event.target.closest("[data-memory-tab]");
      if (tab) {
        setTab(tab.dataset.memoryTab);
        return load();
      }
      const edit = event.target.closest("[data-memory-edit]");
      if (edit) return editMemory(edit.dataset.memoryEdit).catch(error => document.getElementById("memoryScreenStatus").textContent = error.message);
      const forget = event.target.closest("[data-memory-forget]");
      if (forget) return forgetMemory(forget.dataset.memoryForget).catch(error => document.getElementById("memoryScreenStatus").textContent = error.message);
      const add = event.target.closest("[data-memory-add]");
      if (add) return addWorkItem(add.dataset.memoryAdd).catch(error => document.getElementById("memoryScreenStatus").textContent = error.message);
      const search = event.target.closest("[data-memory-search]");
      if (search) return searchWork().catch(error => document.getElementById("memoryScreenStatus").textContent = error.message);
      const company = event.target.closest("[data-memory-edit-company]");
      if (company) return editCompany(company.dataset.memoryEditCompany).catch(error => document.getElementById("memoryScreenStatus").textContent = error.message);
      const contact = event.target.closest("[data-memory-edit-contact]");
      if (contact) return editContact(contact.dataset.memoryEditContact).catch(error => document.getElementById("memoryScreenStatus").textContent = error.message);
      const done = event.target.closest("[data-memory-done]");
      if (done) return completeCommitment(done.dataset.memoryDone).catch(error => document.getElementById("memoryScreenStatus").textContent = error.message);
    });

    const style = document.createElement("style");
    style.textContent = `
      .memory-settings-entry{width:100%;min-height:52px;padding:11px 14px;background:#fff;display:flex;align-items:center;gap:12px;text-align:left;color:#111;cursor:pointer}
      .memory-screen{position:fixed;inset:0;z-index:140;background:#f7f7f5;display:none}.memory-screen.open{display:block}.memory-screen-shell{width:min(720px,100%);height:100%;margin:auto;display:flex;flex-direction:column;background:#f7f7f5}
      .memory-screen-head{display:flex;align-items:center;gap:12px;padding:calc(env(safe-area-inset-top) + 14px) 16px 12px;border-bottom:1px solid #e6e6e3;background:#fff}.memory-screen-head h2{margin:0;font-size:22px}.memory-screen-head p{margin:2px 0 0;color:#8a8a8a;font-size:12px}.memory-back{width:40px;height:40px;border-radius:50%;background:#f1f1ef;font-size:30px;line-height:1;color:#333;cursor:pointer}
      .memory-tabs{display:flex;gap:4px;padding:10px 16px;background:#fff;border-bottom:1px solid #e6e6e3}.memory-tab{flex:1;min-height:40px;border-radius:11px;background:#f1f1ef;color:#666;font-weight:600;cursor:pointer}.memory-tab.active{background:#111;color:#fff}
      .memory-scroll{flex:1;overflow:auto;padding:14px 16px calc(env(safe-area-inset-bottom) + 24px)}.memory-card,.memory-section{background:#fff;border:1px solid #e6e6e3;border-radius:16px;padding:14px;margin-bottom:10px}.memory-card-kicker{font-size:11px;color:#898985;text-transform:uppercase}.memory-card-value{margin-top:6px;font-size:14px;line-height:1.45;white-space:pre-wrap}.memory-card-source,.memory-state,.memory-empty{color:#8a8a8a;font-size:12px;line-height:1.4}.memory-card-source{margin-top:6px}.memory-card-actions{display:flex;gap:7px;margin-top:10px}.memory-card-actions button,.memory-section-head button{padding:8px 10px;border-radius:10px;background:#efefed;color:#333;font-size:12px;font-weight:600;cursor:pointer}.memory-card-actions .danger{color:#8a2d2d}
      .memory-section-head{display:flex;align-items:center;justify-content:space-between;gap:10px}.memory-section-head h3{margin:0;font-size:15px}.memory-list{margin-top:8px}.memory-search{display:flex;gap:8px;margin-top:10px}.memory-search input{flex:1;min-width:0;padding:10px 12px;border:1px solid #dededb;border-radius:10px;background:#fff}.memory-search button,.memory-list-row>button{padding:7px 9px;border-radius:9px;background:#efefed;color:#333;font-size:12px;cursor:pointer}.memory-search-result{margin-top:8px}.memory-search-result p{margin:7px 0;font-size:13px}.memory-list-row{padding:10px 0;border-top:1px solid #efefed;display:flex;align-items:center;justify-content:space-between;gap:10px}.memory-list-row:first-child{border-top:0}.memory-list-row strong{display:block;font-size:14px}.memory-list-row span{display:block;margin-top:3px;color:#8a8a8a;font-size:12px}
    `;
    document.head.appendChild(style);
  }

  document.addEventListener("planner-ready", () => {
    install();
    requestAnimationFrame(placeSettingsEntry);
  });
  document.getElementById("settingsPanel")?.addEventListener("planner-settings-view", () => requestAnimationFrame(placeSettingsEntry));
  const settingsObserver = new MutationObserver(() => placeSettingsEntry());
  const settingsPanel = document.getElementById("settingsPanel");
  if (settingsPanel) settingsObserver.observe(settingsPanel, {childList: true, subtree: true});
  if (document.readyState !== "loading") setTimeout(install, 0);
})();