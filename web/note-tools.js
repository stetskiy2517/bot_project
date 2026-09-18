(() => {
  "use strict";

  if (window.PlannerNotes) return;

  const MAX_TITLE = 120;
  const MAX_TEXT = 5000;
  const CATEGORIES = {
    work: "Работа",
    health: "Здоровье",
    rest: "Отдых",
    travel: "Поездки",
    family: "Семья",
    personal: "Личное",
    other: "Прочее",
  };

  let current = null;
  let openGeneration = 0;
  let searchBusy = false;
  let decorating = false;

  const style = document.createElement("style");
  style.id = "notesProductStyles";
  style.textContent = `
    .note-window-backdrop{position:fixed;z-index:240;inset:0;display:flex;align-items:flex-end;background:rgba(0,0,0,.18);opacity:0;visibility:hidden;pointer-events:none;transition:opacity .18s ease,visibility 0s linear .18s}
    .note-window-backdrop.open{opacity:1;visibility:visible;pointer-events:auto;transition-delay:0s}
    .note-window{width:100%;max-height:92vh;overflow:auto;padding:10px 16px calc(env(safe-area-inset-bottom) + 18px);border-radius:24px 24px 0 0;background:#f7f7f5;box-shadow:0 -16px 46px rgba(0,0,0,.14);transform:translateY(18px);transition:transform .18s ease}
    .note-window-backdrop.open .note-window{transform:translateY(0)}
    .note-window-handle{width:42px;height:4px;margin:1px auto 14px;border-radius:999px;background:#d0d0cc}
    .note-window-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:14px}
    .note-window-title{min-width:0;margin:0;font-size:21px;line-height:1.2;font-weight:700;overflow-wrap:anywhere}
    .note-window-close{flex:0 0 auto;width:36px;height:36px;border:0;border-radius:50%;background:#e9e9e6;color:#333;font-size:20px;cursor:pointer}
    .note-window-body{white-space:pre-wrap;overflow-wrap:anywhere;padding:14px;border:1px solid #e4e4e1;border-radius:16px;background:#fff;color:#252522;font-size:14px;line-height:1.55}
    .note-window-meta{display:flex;flex-wrap:wrap;gap:6px;margin:11px 0 14px}
    .note-window-chip{display:inline-flex;align-items:center;min-height:27px;padding:4px 9px;border-radius:999px;background:#ececea;color:#5b5b56;font-size:11px}
    .note-window-chip.pin{background:#ece8dc;color:#695f3c}
    .note-window-checklist{display:grid;gap:6px;margin:14px 0}
    .note-window-check{display:flex;align-items:flex-start;gap:9px;padding:9px 11px;border-radius:12px;background:#efefec;color:#343431;font-size:13px;line-height:1.35}
    .note-window-check input{width:18px;height:18px;flex:0 0 auto;margin:0}
    .note-window-check.done span{text-decoration:line-through;color:#989893}
    .note-window-dates{margin:10px 2px 0;color:#999994;font-size:10px;line-height:1.35}
    .note-window-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px}
    .note-window-actions.three{grid-template-columns:1fr 1fr 1fr}
    .note-window-button{min-height:44px;padding:9px 10px;border:0;border-radius:13px;background:#e8e8e5;color:#292926;font-size:13px;font-weight:650;cursor:pointer}
    .note-window-button.primary{background:#171717;color:#fff}
    .note-window-button.danger{background:#f2dddd;color:#842f2f}
    .note-window-button:disabled,.notes-toolbar-button:disabled{opacity:.5;cursor:default}
    .note-editor-grid{display:grid;gap:11px}
    .note-editor-field{display:grid;gap:6px;color:#767671;font-size:11px}
    .note-editor-field input,.note-editor-field select,.note-editor-field textarea{width:100%;min-width:0;box-sizing:border-box;padding:10px 11px;border:1px solid #dededb;border-radius:13px;background:#fff;color:#171717;font:inherit;font-size:14px;outline:none}
    .note-editor-field input,.note-editor-field select{min-height:43px}
    .note-editor-field textarea{min-height:180px;resize:vertical;line-height:1.45}
    .note-editor-field textarea.note-checklist-editor{min-height:100px}
    .note-editor-check{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:42px;padding:0 2px;color:#393936;font-size:13px}
    .note-editor-check input{width:20px;height:20px}
    .note-editor-help{margin-top:-4px;color:#9a9a95;font-size:10px;line-height:1.35}
    .note-editor-error{min-height:17px;color:#923d3d;font-size:11px;line-height:1.35}
    .notes-product-toolbar{display:none;margin-top:10px;gap:8px;align-items:center}
    .notes-product-toolbar.visible{display:grid;grid-template-columns:minmax(0,1fr) auto auto}
    .notes-search-input{min-width:0;height:42px;padding:0 12px;border:1px solid #dededb;border-radius:12px;background:#fff;color:#171717;font:inherit;font-size:14px;outline:none}
    .notes-toolbar-button{height:42px;padding:0 12px;border:0;border-radius:12px;background:#e8e8e5;color:#292926;font-size:12px;font-weight:650;cursor:pointer;white-space:nowrap}
    .notes-toolbar-button.primary{width:42px;padding:0;background:#171717;color:#fff;font-size:21px;font-weight:400}
    .notes-search-status{display:none;margin:7px 2px 0;color:#858580;font-size:11px;line-height:1.35}
    .notes-search-status.visible{display:block}
    .note-card-badges{display:flex;flex-wrap:wrap;gap:5px;margin-top:9px}
    .note-card-badge{display:inline-flex;align-items:center;min-height:22px;padding:2px 7px;border-radius:999px;background:#f0f0ed;color:#666660;font-size:10px;line-height:1.1}
    .note-card-badge.pin{background:#ece8dc;color:#695f3c}
    .note-search-answer{padding:12px 13px;margin-bottom:12px;border-radius:14px;background:#ececea;color:#4a4a46;font-size:13px;line-height:1.45;white-space:pre-wrap}
    .note-search-results{display:grid;gap:8px}
    .note-search-result{display:block;width:100%;padding:12px 13px;border:1px solid #e5e5e2;border-radius:14px;background:#fff;text-align:left;color:#222;cursor:pointer}
    .note-search-result-title{font-size:13px;font-weight:700}
    .note-search-result-preview{margin-top:4px;color:#777772;font-size:11px;line-height:1.35;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}
    @media (max-width:420px){.notes-product-toolbar.visible{grid-template-columns:minmax(0,1fr) 42px}.notes-toolbar-button.semantic{grid-column:1/-1;grid-row:2;width:max-content;justify-self:end}.note-window-actions.three{grid-template-columns:1fr 1fr}.note-window-actions.three .danger{grid-column:1/-1}}
    @media (min-width:760px){.note-window{max-width:560px;margin:0 auto 18px;border-radius:22px}}
  `;
  document.head.appendChild(style);

  function api(path, options = {}) {
    if (window.PlannerRequests?.request) return window.PlannerRequests.request(path, options);
    if (typeof window.api === "function") return window.api(path, options);
    return fetch(path, {credentials: "same-origin", cache: "no-store", ...options}).then(async response => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw Object.assign(new Error(data.message || data.error || `HTTP ${response.status}`), {status: response.status, data});
      return data;
    });
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function modal() {
    let root = document.getElementById("noteWindowBackdrop");
    if (root) return root;
    root = document.createElement("div");
    root.id = "noteWindowBackdrop";
    root.className = "note-window-backdrop";
    document.body.appendChild(root);
    return root;
  }

  function show(html) {
    const root = modal();
    root.innerHTML = `<section class="note-window" role="dialog" aria-modal="true" aria-label="Заметка"><div class="note-window-handle"></div>${html}</section>`;
    root.classList.add("open");
  }

  function close() {
    const root = modal();
    openGeneration += 1;
    root.classList.remove("open");
    current = null;
    setTimeout(() => {
      if (!root.classList.contains("open")) root.replaceChildren();
    }, 180);
  }

  function categoryOptions(selected = "other") {
    return Object.entries(CATEGORIES)
      .map(([key, label]) => `<option value="${key}"${key === selected ? " selected" : ""}>${label}</option>`)
      .join("");
  }

  function tagsFromInput(value) {
    const seen = new Set();
    const tags = [];
    String(value || "").split(/[,;\n]+/).forEach(raw => {
      const tag = raw.trim().replace(/^#+/, "").slice(0, 40);
      const key = tag.toLocaleLowerCase("ru-RU");
      if (tag && !seen.has(key) && tags.length < 20) {
        seen.add(key);
        tags.push(tag);
      }
    });
    return tags;
  }

  function checklistToText(items) {
    return (Array.isArray(items) ? items : []).map(item => `${item.done ? "[x]" : "[ ]"} ${item.text || ""}`.trim()).join("\n");
  }

  function checklistFromText(value) {
    return String(value || "").split("\n").map(line => line.trim()).filter(Boolean).slice(0, 100).map(line => {
      const match = line.match(/^\[([xхXХ ])\]\s*(.*)$/);
      if (!match) return {text: line.slice(0, 300), done: false};
      return {text: String(match[2] || "").trim().slice(0, 300), done: !/^\s*$/.test(match[1])};
    }).filter(item => item.text);
  }

  function humanDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "";
    return new Intl.DateTimeFormat("ru-RU", {day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit"}).format(date);
  }

  function renderChecklist(note) {
    const items = Array.isArray(note.checklist) ? note.checklist : [];
    if (!items.length) return "";
    return `<div class="note-window-checklist">${items.map((item, index) => `<label class="note-window-check${item.done ? " done" : ""}"><input type="checkbox" data-note-check="${index}"${item.done ? " checked" : ""}><span>${escapeHtml(item.text)}</span></label>`).join("")}</div>`;
  }

  function renderDetail(note) {
    current = note;
    const chips = [];
    if (note.pinned) chips.push('<span class="note-window-chip pin">Закреплено</span>');
    chips.push(`<span class="note-window-chip">${escapeHtml(CATEGORIES[note.category] || CATEGORIES.other)}</span>`);
    for (const tag of note.tags || []) chips.push(`<span class="note-window-chip">#${escapeHtml(tag)}</span>`);
    const created = humanDate(note.created_at);
    const updated = humanDate(note.updated_at);
    show(`
      <div class="note-window-head"><h2 class="note-window-title">${escapeHtml(note.title || "Без названия")}</h2><button class="note-window-close" type="button" data-note-close aria-label="Закрыть">×</button></div>
      <div class="note-window-body">${escapeHtml(note.text || "")}</div>
      <div class="note-window-meta">${chips.join("")}</div>
      ${renderChecklist(note)}
      <div class="note-window-dates">${created ? `Создано: ${escapeHtml(created)}` : ""}${created && updated ? " · " : ""}${updated ? `Изменено: ${escapeHtml(updated)}` : ""}</div>
      <div class="note-window-actions three"><button class="note-window-button" type="button" data-note-pin>${note.pinned ? "Открепить" : "Закрепить"}</button><button class="note-window-button primary" type="button" data-note-edit>Изменить</button><button class="note-window-button danger" type="button" data-note-delete>Удалить</button></div>`);
  }

  function blankNote() {
    return {note_id: null, title: "", text: "", category: "other", pinned: false, tags: [], checklist: []};
  }

  function renderEditor(note, creating = false, error = "") {
    current = creating ? null : note;
    const value = note || blankNote();
    show(`
      <div class="note-window-head"><h2 class="note-window-title">${creating ? "Новая заметка" : "Изменить заметку"}</h2><button class="note-window-close" type="button" data-note-editor-cancel aria-label="Отмена">×</button></div>
      <div class="note-editor-grid" data-note-editor-mode="${creating ? "create" : "edit"}" data-note-editor-id="${value.note_id || ""}">
        <label class="note-editor-field">Название<input id="noteEditTitle" maxlength="${MAX_TITLE}" autocomplete="off" placeholder="Можно оставить пустым" value="${escapeHtml(value.title || "")}"></label>
        <label class="note-editor-field">Текст<textarea id="noteEditText" maxlength="${MAX_TEXT}" placeholder="Что сохранить?">${escapeHtml(value.text || "")}</textarea></label>
        <label class="note-editor-field">Категория<select id="noteEditCategory">${categoryOptions(value.category || "other")}</select></label>
        <label class="note-editor-field">Теги<input id="noteEditTags" maxlength="500" autocomplete="off" placeholder="например: машина, покупки" value="${escapeHtml((value.tags || []).join(", "))}"></label>
        <label class="note-editor-check"><span>Закрепить сверху</span><input id="noteEditPinned" type="checkbox"${value.pinned ? " checked" : ""}></label>
        <label class="note-editor-field">Чек-лист<textarea id="noteEditChecklist" class="note-checklist-editor" placeholder="[ ] Купить молоко\n[x] Позвонить">${escapeHtml(checklistToText(value.checklist))}</textarea></label>
        <div class="note-editor-help">Каждый пункт с новой строки. [x] означает выполнено, [ ] означает ещё нет.</div>
        <div id="noteEditError" class="note-editor-error">${escapeHtml(error)}</div>
        <div class="note-window-actions"><button class="note-window-button" type="button" data-note-editor-cancel>Отмена</button><button class="note-window-button primary" type="button" data-note-save>${creating ? "Создать" : "Сохранить"}</button></div>
      </div>`);
    requestAnimationFrame(() => modal().querySelector("#noteEditText")?.focus());
  }

  function editorDraft() {
    const root = modal();
    const value = id => root.querySelector(id)?.value ?? "";
    return {
      title: value("#noteEditTitle").trim(),
      text: value("#noteEditText").trim(),
      category: value("#noteEditCategory") || "other",
      tags: tagsFromInput(value("#noteEditTags")),
      pinned: Boolean(root.querySelector("#noteEditPinned")?.checked),
      checklist: checklistFromText(value("#noteEditChecklist")),
    };
  }

  function signalChanged() {
    document.dispatchEvent(new Event("planner-library-changed"));
    document.dispatchEvent(new Event("planner-note-changed"));
    setTimeout(() => decorateRows(), 120);
  }

  async function open(noteId) {
    const id = Number(noteId || 0);
    if (!id) return;
    const generation = ++openGeneration;
    show('<div class="note-window-body">Загружаю заметку…</div>');
    try {
      const data = await api(`/api/note-tools/${id}`);
      if (generation !== openGeneration || !modal().classList.contains("open")) return;
      renderDetail(data.note);
    } catch (error) {
      if (generation !== openGeneration || !modal().classList.contains("open")) return;
      show(`<div class="note-window-head"><h2 class="note-window-title">Заметка</h2><button class="note-window-close" type="button" data-note-close>×</button></div><div class="note-window-body">${escapeHtml(error?.message || "Не удалось загрузить заметку.")}</div>`);
    }
  }

  function create() {
    openGeneration += 1;
    renderEditor(blankNote(), true);
  }

  async function saveEditor() {
    const root = modal();
    const editor = root.querySelector("[data-note-editor-mode]");
    if (!editor) return;
    const draft = editorDraft();
    const error = root.querySelector("#noteEditError");
    if (!draft.text) {
      if (error) error.textContent = "Добавь текст заметки.";
      return;
    }
    root.querySelectorAll("button,input,select,textarea").forEach(control => control.disabled = true);
    try {
      let saved;
      if (editor.dataset.noteEditorMode === "create") {
        saved = (await api("/api/note-tools", {method: "POST", body: JSON.stringify(draft)})).note;
      } else {
        const id = Number(editor.dataset.noteEditorId || 0);
        await api(`/api/note-tools/${id}`, {method: "PATCH", body: JSON.stringify({title: draft.title || current?.title || "Заметка", text: draft.text})});
        saved = (await api(`/api/note-tools/${id}/metadata`, {method: "PUT", body: JSON.stringify({pinned: draft.pinned, tags: draft.tags, checklist: draft.checklist, category: draft.category})})).note;
      }
      signalChanged();
      renderDetail(saved);
    } catch (err) {
      renderEditor(current || draft, !editor.dataset.noteEditorId, err?.message || "Не удалось сохранить заметку.");
    }
  }

  async function updateMetadata(note, patch) {
    const payload = {
      pinned: patch.pinned ?? Boolean(note.pinned),
      tags: patch.tags ?? (note.tags || []),
      checklist: patch.checklist ?? (note.checklist || []),
      category: patch.category ?? (note.category || "other"),
    };
    const data = await api(`/api/note-tools/${note.note_id}/metadata`, {method: "PUT", body: JSON.stringify(payload)});
    signalChanged();
    renderDetail(data.note);
  }

  function renderDelete(note) {
    current = note;
    show(`<div class="note-window-head"><h2 class="note-window-title">Удалить заметку?</h2><button class="note-window-close" type="button" data-note-delete-cancel aria-label="Отмена">×</button></div><div class="note-window-body">${escapeHtml(note.title || "Без названия")}</div><div id="noteDeleteError" class="note-editor-error"></div><div class="note-window-actions"><button class="note-window-button" type="button" data-note-delete-cancel>Отмена</button><button class="note-window-button danger" type="button" data-note-delete-confirm>Удалить</button></div>`);
  }

  async function confirmDelete() {
    if (!current) return;
    const button = modal().querySelector("[data-note-delete-confirm]");
    if (button) button.disabled = true;
    try {
      await api(`/api/library/notes/${current.note_id}`, {method: "DELETE"});
      close();
      signalChanged();
    } catch (error) {
      const target = modal().querySelector("#noteDeleteError");
      if (target) target.textContent = error?.message || "Не удалось удалить заметку.";
      if (button) button.disabled = false;
    }
  }

  function notesActive() {
    return document.getElementById("libraryNotesTab")?.getAttribute("aria-selected") === "true";
  }

  function libraryOpen() {
    return document.getElementById("app")?.classList.contains("library-active");
  }

  function installToolbar() {
    if (document.getElementById("notesProductToolbarWrap")) return;
    const tabs = document.querySelector("#libraryScreen .library-tabs");
    if (!tabs) return;
    const wrap = document.createElement("div");
    wrap.id = "notesProductToolbarWrap";
    wrap.innerHTML = `<div id="notesProductToolbar" class="notes-product-toolbar"><input id="notesLibrarySearch" class="notes-search-input" type="search" maxlength="500" autocomplete="off" placeholder="Поиск по заметкам"><button id="notesSemanticSearch" class="notes-toolbar-button semantic" type="button">По смыслу</button><button id="notesCreate" class="notes-toolbar-button primary" type="button" aria-label="Новая заметка">+</button></div><div id="notesSearchStatus" class="notes-search-status" role="status"></div>`;
    tabs.insertAdjacentElement("afterend", wrap);
    document.getElementById("notesCreate")?.addEventListener("click", create);
    document.getElementById("notesLibrarySearch")?.addEventListener("input", filterRows);
    document.getElementById("notesLibrarySearch")?.addEventListener("keydown", event => {
      if (event.key === "Enter") {
        event.preventDefault();
        semanticSearch();
      }
    });
    document.getElementById("notesSemanticSearch")?.addEventListener("click", semanticSearch);
    document.getElementById("libraryNotesTab")?.addEventListener("click", () => setTimeout(syncToolbar, 0));
    document.getElementById("libraryRemindersTab")?.addEventListener("click", () => setTimeout(syncToolbar, 0));
    syncToolbar();
  }

  function syncToolbar() {
    const toolbar = document.getElementById("notesProductToolbar");
    if (!toolbar) return;
    toolbar.classList.toggle("visible", notesActive() && libraryOpen());
    if (notesActive() && libraryOpen()) setTimeout(() => decorateRows(), 50);
  }

  function noteRows() {
    return Array.from(document.querySelectorAll("#libraryList .note-swipe-row[data-id]"));
  }

  function filterRows() {
    const input = document.getElementById("notesLibrarySearch");
    const query = String(input?.value || "").trim().toLocaleLowerCase("ru-RU");
    let visible = 0;
    for (const row of noteRows()) {
      const haystack = String(row.dataset.noteSearch || row.textContent || "").toLocaleLowerCase("ru-RU");
      const showRow = !query || query.split(/\s+/).every(token => haystack.includes(token));
      row.hidden = !showRow;
      if (showRow) visible += 1;
    }
    const status = document.getElementById("notesSearchStatus");
    if (!status) return;
    status.classList.toggle("visible", Boolean(query));
    status.textContent = query ? (visible ? `Найдено: ${visible}` : "По словам ничего не найдено. Попробуй поиск по смыслу.") : "";
  }

  async function decorateRows() {
    if (decorating || !notesActive() || !libraryOpen()) return;
    const rows = noteRows();
    if (!rows.length) return;
    decorating = true;
    try {
      const data = await api("/api/note-tools");
      const byId = new Map(rows.map(row => [Number(row.dataset.id), row]));
      for (const note of data.notes || []) {
        const row = byId.get(Number(note.note_id));
        if (!row) continue;
        row.dataset.noteSearch = [note.title, note.text, CATEGORIES[note.category] || "", ...(note.tags || [])].join(" ");
        const card = row.querySelector(".library-card");
        if (!card) continue;
        card.querySelector(".note-card-badges")?.remove();
        const badges = document.createElement("div");
        badges.className = "note-card-badges";
        if (note.pinned) {
          const pin = document.createElement("span");
          pin.className = "note-card-badge pin";
          pin.textContent = "Закреплено";
          badges.appendChild(pin);
        }
        const category = document.createElement("span");
        category.className = "note-card-badge";
        category.textContent = CATEGORIES[note.category] || CATEGORIES.other;
        badges.appendChild(category);
        for (const tag of (note.tags || []).slice(0, 3)) {
          const chip = document.createElement("span");
          chip.className = "note-card-badge";
          chip.textContent = `#${tag}`;
          badges.appendChild(chip);
        }
        const meta = card.querySelector(".library-card-meta");
        if (meta) card.insertBefore(badges, meta);
        else card.appendChild(badges);
      }
      filterRows();
    } catch (_error) {
    } finally {
      decorating = false;
    }
  }

  async function semanticSearch() {
    if (searchBusy) return;
    const input = document.getElementById("notesLibrarySearch");
    const query = String(input?.value || "").trim();
    const status = document.getElementById("notesSearchStatus");
    if (query.length < 2) {
      if (status) {
        status.textContent = "Напиши хотя бы два символа для поиска по смыслу.";
        status.classList.add("visible");
      }
      input?.focus();
      return;
    }
    searchBusy = true;
    const button = document.getElementById("notesSemanticSearch");
    if (button) button.disabled = true;
    try {
      const data = await api("/api/note-tools/search", {method: "POST", body: JSON.stringify({query})});
      const notes = Array.isArray(data.notes) ? data.notes : [];
      show(`<div class="note-window-head"><h2 class="note-window-title">Поиск по заметкам</h2><button class="note-window-close" type="button" data-note-close>×</button></div><div class="note-search-answer">${escapeHtml(data.answer || "Не нашёл.")}</div><div class="note-search-results">${notes.length ? notes.map(note => `<button class="note-search-result" type="button" data-note-result="${Number(note.note_id)}"><div class="note-search-result-title">${escapeHtml(note.title || "Без названия")}</div><div class="note-search-result-preview">${escapeHtml(note.text || "")}</div></button>`).join("") : '<div class="note-window-body">Подходящих заметок нет.</div>'}</div>`);
    } catch (error) {
      if (status) {
        status.textContent = error?.message || "Не удалось выполнить поиск.";
        status.classList.add("visible");
      }
    } finally {
      searchBusy = false;
      if (button) button.disabled = false;
    }
  }

  function install() {
    installToolbar();
    const list = document.getElementById("libraryList");
    const app = document.getElementById("app");
    if (list && !list.dataset.notesProductObserved) {
      list.dataset.notesProductObserved = "1";
      new MutationObserver(() => setTimeout(() => decorateRows(), 30)).observe(list, {childList: true});
    }
    if (app && !app.dataset.notesProductObserved) {
      app.dataset.notesProductObserved = "1";
      new MutationObserver(syncToolbar).observe(app, {attributes: true, attributeFilter: ["class"]});
    }
    syncToolbar();
  }

  document.addEventListener("click", event => {
    const card = event.target.closest?.("#libraryList .library-card[data-type='note']");
    if (!card || !libraryOpen()) return;
    const row = card.closest(".library-swipe-row");
    if (row && Math.abs(Number(row.dataset.offset || 0)) > 1) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    open(Number(card.dataset.id));
  }, true);

  document.addEventListener("click", event => {
    const root = modal();
    if (!root.classList.contains("open")) return;
    if (event.target === root || event.target.closest?.("[data-note-close]")) {
      close();
      return;
    }
    if (event.target.closest?.("[data-note-edit]") && current) {
      renderEditor(current, false);
      return;
    }
    if (event.target.closest?.("[data-note-editor-cancel]")) {
      if (current) renderDetail(current); else close();
      return;
    }
    if (event.target.closest?.("[data-note-save]")) {
      saveEditor();
      return;
    }
    if (event.target.closest?.("[data-note-pin]") && current) {
      updateMetadata(current, {pinned: !current.pinned}).catch(() => renderDetail(current));
      return;
    }
    if (event.target.closest?.("[data-note-delete]") && current) {
      renderDelete(current);
      return;
    }
    if (event.target.closest?.("[data-note-delete-cancel]") && current) {
      renderDetail(current);
      return;
    }
    if (event.target.closest?.("[data-note-delete-confirm]")) {
      confirmDelete();
      return;
    }
    const check = event.target.closest?.("[data-note-check]");
    if (check && current) {
      const checklist = (current.checklist || []).map(item => ({...item}));
      const index = Number(check.dataset.noteCheck);
      if (checklist[index]) {
        checklist[index].done = Boolean(check.checked);
        updateMetadata(current, {checklist}).catch(() => renderDetail(current));
      }
      return;
    }
    const result = event.target.closest?.("[data-note-result]");
    if (result) open(Number(result.dataset.noteResult));
  });

  document.addEventListener("planner-library-changed", () => setTimeout(() => decorateRows(), 80));
  document.addEventListener("planner-ready", () => setTimeout(install, 0));
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && modal().classList.contains("open")) close();
  });

  window.PlannerNotes = {open, create, semanticSearch, refresh: decorateRows};
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setTimeout(install, 0), {once: true});
  else setTimeout(install, 0);
})();