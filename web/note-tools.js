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
  let noteBackTouch = null;
  let noteWheelX = 0;
  let noteWheelTimer = null;
  let noteSearchOpen = false;

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
    .note-window-button:disabled,.notes-header-icon:disabled{opacity:.5;cursor:default}
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
    .notes-header-actions{display:flex;align-items:center;gap:6px}
    .notes-header-actions[hidden]{display:none!important}
    .notes-header-icon{position:relative;width:36px;height:36px;flex:0 0 36px;display:grid;place-items:center;border:0;border-radius:12px;background:#e9e9e6;color:#252524;cursor:pointer;transition:transform .16s ease,background .16s ease,color .16s ease}
    .notes-header-icon:active{transform:scale(.94)}
    .notes-header-icon.primary{background:#2d2d2c;color:#fff}
    .notes-header-icon.active::after{content:"";position:absolute;right:5px;top:5px;width:5px;height:5px;border-radius:50%;background:#2d2d2c;box-shadow:0 0 0 2px #e9e9e6}
    .notes-header-icon svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
    .notes-search-shell{position:relative;width:36px;height:36px;flex:0 0 36px;z-index:9}
    .notes-search-input{position:absolute;z-index:8;right:42px;top:0;width:0;height:36px;opacity:0;pointer-events:none;transform:translateX(9px);padding:0;border:1px solid #dededb;border-radius:12px;background:#fff;color:#222;font:inherit;font-size:16px;outline:0;box-shadow:0 5px 18px rgba(0,0,0,.07);transition:width .24s cubic-bezier(.22,.8,.24,1),opacity .16s ease,transform .24s cubic-bezier(.22,.8,.24,1),padding .24s ease}
    .notes-search-shell.open .notes-search-input{width:clamp(132px,46vw,190px);opacity:1;pointer-events:auto;transform:translateX(0);padding:0 12px}
    .notes-search-input:focus{border-color:#c9c9c5}
    #libraryScreen .library-nav.note-search-open .library-tabs{opacity:0;pointer-events:none;transform:translateX(-8px)}
    .notes-search-status{display:none;margin:6px 2px 0;color:#858580;font-size:11px;line-height:1.35}
    .notes-search-status.visible{display:block}
    .note-card-badges{display:flex;flex-wrap:wrap;gap:5px;margin-top:9px}
    .note-card-badge{display:inline-flex;align-items:center;min-height:22px;padding:2px 7px;border-radius:999px;background:#f0f0ed;color:#666660;font-size:10px;line-height:1.1}
    .note-card-badge.pin{background:#ece8dc;color:#695f3c}
    .note-search-answer{padding:12px 13px;margin-bottom:12px;border-radius:14px;background:#ececea;color:#4a4a46;font-size:13px;line-height:1.45;white-space:pre-wrap}
    .note-search-results{display:grid;gap:8px}
    .note-search-result{display:block;width:100%;padding:12px 13px;border:1px solid #e5e5e2;border-radius:14px;background:#fff;text-align:left;color:#222;cursor:pointer}
    .note-search-result-title{font-size:13px;font-weight:700}
    .note-search-result-preview{margin-top:4px;color:#777772;font-size:11px;line-height:1.35;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}
    @media (max-width:420px){.notes-header-icon{width:34px;height:34px;flex-basis:34px;border-radius:11px}.notes-search-shell{width:34px;height:34px;flex-basis:34px}.notes-search-input{right:40px;height:34px}.note-window-actions.three{grid-template-columns:1fr 1fr}.note-window-actions.three .danger{grid-column:1/-1}}
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

    root.addEventListener("wheel", event => {
      if (!root.classList.contains("open")) return;
      if (Math.abs(event.deltaX) <= Math.abs(event.deltaY) * 1.1) return;
      noteWheelX += event.deltaX;
      if (noteWheelTimer) clearTimeout(noteWheelTimer);
      noteWheelTimer = setTimeout(() => { noteWheelX = 0; }, 180);
      if (Math.abs(noteWheelX) < 90) return;
      const shouldClose = noteWheelX < 0;
      noteWheelX = 0;
      if (!shouldClose) return;
      close();
      event.preventDefault();
      event.stopPropagation();
    }, {passive: false});

    document.body.appendChild(root);
    return root;
  }

  function bindWindowBackGesture(windowElement) {
    if (!windowElement || windowElement.dataset.noteBackGestureBound === "1") return;
    windowElement.dataset.noteBackGestureBound = "1";
    windowElement.addEventListener("touchstart", event => {
      if (event.touches.length !== 1 || event.target.closest?.("input, textarea, select, button")) {
        noteBackTouch = null;
        return;
      }
      const touch = event.touches[0];
      noteBackTouch = {x: touch.clientX, y: touch.clientY};
    }, {passive: true});
    windowElement.addEventListener("touchend", event => {
      const start = noteBackTouch;
      noteBackTouch = null;
      if (!start || event.changedTouches.length !== 1) return;
      const touch = event.changedTouches[0];
      const dx = touch.clientX - start.x;
      const dy = touch.clientY - start.y;
      if (dx < 64 || dx < Math.abs(dy) * 1.25) return;
      close();
      event.preventDefault();
      event.stopPropagation();
    }, {passive: false});
    windowElement.addEventListener("touchcancel", () => {
      noteBackTouch = null;
    }, {passive: true});
  }

  function show(html) {
    const root = modal();
    root.innerHTML = `<section class="note-window" role="dialog" aria-modal="true" aria-label="Заметка"><div class="note-window-handle"></div>${html}</section>`;
    bindWindowBackGesture(root.querySelector(".note-window"));
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
    if (document.getElementById("notesHeaderActions")) return;
    const nav = document.querySelector("#libraryScreen .library-nav");
    const host = document.getElementById("libraryNavActions");
    if (!nav || !host) return;

    const root = document.createElement("div");
    root.id = "notesHeaderActions";
    root.className = "notes-header-actions";
    root.innerHTML = `
      <div class="notes-search-shell">
        <input id="notesLibrarySearch" class="notes-search-input" type="search" maxlength="500" autocomplete="off" placeholder="Поиск по заметкам" aria-label="Поиск по заметкам">
        <button id="notesSearchBtn" class="notes-header-icon" type="button" aria-label="Поиск по заметкам" aria-expanded="false">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="5.7"></circle><path d="m15 15 4.5 4.5"></path></svg>
        </button>
      </div>
      <button id="notesSemanticSearch" class="notes-header-icon" type="button" aria-label="Поиск по смыслу" title="Поиск по смыслу">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l1.1 3.2L16 7.3l-2.9 1.2L12 12l-1.1-3.5L8 7.3l2.9-1.1L12 3Z"></path><path d="M18 13l.8 2.2L21 16l-2.2.8L18 19l-.8-2.2L15 16l2.2-.8L18 13Z"></path></svg>
      </button>
      <button id="notesCreate" class="notes-header-icon primary" type="button" aria-label="Новая заметка" title="Новая заметка">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"></path></svg>
      </button>`;
    host.appendChild(root);

    const wrap = document.createElement("div");
    wrap.id = "notesProductToolbarWrap";
    wrap.innerHTML = `<div id="notesSearchStatus" class="notes-search-status" role="status"></div>`;
    nav.insertAdjacentElement("afterend", wrap);

    const shell = root.querySelector(".notes-search-shell");
    const input = root.querySelector("#notesLibrarySearch");
    const searchButton = root.querySelector("#notesSearchBtn");
    const setSearchOpen = open => {
      noteSearchOpen = Boolean(open);
      shell.classList.toggle("open", noteSearchOpen);
      searchButton.setAttribute("aria-expanded", String(noteSearchOpen));
      nav.classList.toggle("note-search-open", noteSearchOpen);
      if (noteSearchOpen) {
        requestAnimationFrame(() => {
          input.focus();
          if (input.value) input.select();
        });
      } else {
        input.blur();
      }
    };

    searchButton.addEventListener("click", () => setSearchOpen(!noteSearchOpen));
    document.getElementById("notesCreate")?.addEventListener("click", create);
    input.addEventListener("input", () => {
      searchButton.classList.toggle("active", Boolean(input.value.trim()));
      filterRows();
    });
    input.addEventListener("search", () => {
      searchButton.classList.toggle("active", Boolean(input.value.trim()));
      filterRows();
    });
    input.addEventListener("keydown", event => {
      if (event.key === "Enter") {
        event.preventDefault();
        semanticSearch();
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setSearchOpen(false);
      }
    });
    document.getElementById("notesSemanticSearch")?.addEventListener("click", () => {
      setSearchOpen(true);
      semanticSearch();
    });
    document.querySelector("#libraryScreen .library-tabs")?.addEventListener("click", () => setTimeout(syncToolbar, 0));
    syncToolbar();
  }

  function syncToolbar() {
    const toolbar = document.getElementById("notesHeaderActions");
    if (!toolbar) return;
    const visible = notesActive() && libraryOpen();
    toolbar.hidden = !visible;
    if (!visible) {
      noteSearchOpen = false;
      toolbar.querySelector(".notes-search-shell")?.classList.remove("open");
      toolbar.querySelector("#notesSearchBtn")?.setAttribute("aria-expanded", "false");
      toolbar.querySelector("#notesLibrarySearch")?.blur();
      document.querySelector("#libraryScreen .library-nav")?.classList.remove("note-search-open");
    }
    if (visible) setTimeout(() => decorateRows(), 50);
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

  window.PlannerNotes = {open, close, create, semanticSearch, refresh: decorateRows};
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setTimeout(install, 0), {once: true});
  else setTimeout(install, 0);
})();