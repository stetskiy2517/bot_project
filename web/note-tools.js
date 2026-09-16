(() => {
  "use strict";

  if (window.PlannerNotes) return;

  const CATEGORIES = {
    work: "Работа",
    health: "Здоровье",
    rest: "Отдых",
    travel: "Поездки",
    family: "Семья",
    personal: "Личное",
    other: "Прочее",
  };
  const MAX_TITLE = 160;
  const MAX_TEXT = 20000;

  let current = null;
  let metadataLoading = false;
  let metadataQueued = false;
  let lastDecoratedSignature = "";
  let toolbarInstalled = false;
  let searchBusy = false;

  const style = document.createElement("style");
  style.id = "notesProductStyles";
  style.textContent = `
    .notes-product-toolbar{display:none;margin-top:10px;gap:8px;align-items:center}
    .notes-product-toolbar.visible{display:grid;grid-template-columns:minmax(0,1fr) auto auto}
    .notes-search-input{min-width:0;height:42px;padding:0 12px;border:1px solid #dededb;border-radius:12px;background:#fff;color:#171717;font:inherit;font-size:14px;outline:none}
    .notes-search-input:focus{border-color:#b9b9b4;box-shadow:0 0 0 3px rgba(0,0,0,.035)}
    .notes-toolbar-button{height:42px;padding:0 12px;border-radius:12px;background:#ececea;color:#292926;font-size:12px;font-weight:650;cursor:pointer;white-space:nowrap}
    .notes-toolbar-button.primary{width:42px;padding:0;background:#171717;color:#fff;font-size:21px;font-weight:400}
    .notes-search-status{display:none;margin:7px 2px 0;color:#858580;font-size:11px;line-height:1.35}
    .notes-search-status.visible{display:block}
    .note-card-badges{display:flex;flex-wrap:wrap;gap:5px;margin-top:9px}
    .note-card-badge{display:inline-flex;align-items:center;min-height:22px;padding:2px 7px;border-radius:999px;background:#f0f0ed;color:#666660;font-size:10px;line-height:1.1}
    .note-card-badge.pin{background:#ece8dc;color:#695f3c}
    .note-product-sheet{max-height:92%;overflow:auto}
    .note-product-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:14px}
    .note-product-title{min-width:0;margin:0;font-size:21px;line-height:1.2;font-weight:700;overflow-wrap:anywhere}
    .note-product-close{flex:0 0 auto;width:36px;height:36px;border-radius:50%;background:#efefec;color:#333;font-size:20px;cursor:pointer}
    .note-product-body{white-space:pre-wrap;overflow-wrap:anywhere;padding:14px;border:1px solid #e7e7e4;border-radius:16px;background:#fff;color:#252522;font-size:14px;line-height:1.55}
    .note-product-meta{display:flex;flex-wrap:wrap;gap:6px;margin:11px 0 14px}
    .note-product-chip{display:inline-flex;align-items:center;min-height:27px;padding:4px 9px;border-radius:999px;background:#eeeeeb;color:#5b5b56;font-size:11px}
    .note-product-chip.pin{background:#ece8dc;color:#695f3c}
    .note-product-dates{margin:10px 2px 0;color:#999994;font-size:10px;line-height:1.35}
    .note-product-checklist{display:grid;gap:6px;margin:14px 0}
    .note-check-row{display:flex;align-items:flex-start;gap:9px;padding:9px 11px;border-radius:12px;background:#f5f5f2;color:#343431;font-size:13px;line-height:1.35}
    .note-check-row input{width:18px;height:18px;flex:0 0 auto;margin:0}
    .note-check-row.done span{text-decoration:line-through;color:#989893}
    .note-product-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px}
    .note-product-actions.three{grid-template-columns:1fr 1fr 1fr}
    .note-product-button{min-height:44px;padding:9px 10px;border-radius:13px;background:#ececea;color:#292926;font-size:13px;font-weight:650;cursor:pointer}
    .note-product-button.primary{background:#171717;color:#fff}
    .note-product-button.danger{background:#f2dddd;color:#842f2f}
    .note-product-button:disabled{opacity:.5;cursor:default}
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
    .note-search-answer{padding:12px 13px;margin-bottom:12px;border-radius:14px;background:#f1f1ee;color:#4a4a46;font-size:13px;line-height:1.45;white-space:pre-wrap}
    .note-search-results{display:grid;gap:8px}
    .note-search-result{display:block;width:100%;padding:12px 13px;border:1px solid #e5e5e2;border-radius:14px;background:#fff;text-align:left;color:#222;cursor:pointer}
    .note-search-result-title{font-size:13px;font-weight:700}
    .note-search-result-preview{margin-top:4px;color:#777772;font-size:11px;line-height:1.35;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden}
    .note-delete-copy{margin:0 0 14px;color:#555550;font-size:14px;line-height:1.45}
    @media (max-width:420px){
      .notes-product-toolbar.visible{grid-template-columns:minmax(0,1fr) 42px}
      .notes-toolbar-button.semantic{grid-column:1 / -1;grid-row:2;width:max-content;justify-self:end}
      .note-product-actions.three{grid-template-columns:1fr 1fr}.note-product-actions.three .danger{grid-column:1 / -1}
    }
    @media (min-width:760px){.note-product-sheet{max-width:560px;margin:0 auto 18px;border-radius:22px}}
  `;
  document.head.appendChild(style);

  function api(path, options = {}) {
    if (window.PlannerRequests?.request) return window.PlannerRequests.request(path, options);
    if (typeof window.api === "function") return window.api(path, options);
    return fetch(path, {credentials: "same-origin", cache: "no-store", ...options}).then(async response => {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw Object.assign(Error(data.message || data.error || `HTTP ${response.status}`), {status: response.status, data});
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

  function backdrop() {
    return document.getElementById("mobileSheetBackdrop");
  }

  function showSheet(html) {
    const root = backdrop();
    if (!root) throw new Error("Экран заметки ещё загружается");
    root.innerHTML = `<section class="mobile-sheet note-product-sheet" role="dialog" aria-modal="true" aria-label="Заметка"><div class="mobile-sheet-handle"></div>${html}</section>`;
    root.classList.add("open");
  }

  function closeSheet() {
    const root = backdrop();
    if (!root) return;
    root.classList.remove("open");
    current = null;
    setTimeout(() => {
      if (!root.classList.contains("open")) root.replaceChildren();
    }, 180);
  }

  function humanDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "";
    return new Intl.DateTimeFormat("ru-RU", {
      day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
    }).format(date);
  }

  function categoryOptions(selected = "other") {
    return Object.entries(CATEGORIES)
      .map(([key, label]) => `<option value="${key}"${selected === key ? " selected" : ""}>${label}</option>`)
      .join("");
  }

  function tagsFromInput(value) {
    const result = [];
    const seen = new Set();
    String(value || "").split(/[,;\n]+/).forEach(raw => {
      const tag = raw.trim().replace(/^#+/, "").slice(0, 40);
      const key = tag.toLocaleLowerCase("ru-RU");
      if (tag && !seen.has(key) && result.length < 20) {
        result.push(tag);
        seen.add(key);
      }
    });
    return result;
  }

  function checklistToText(items) {
    return (Array.isArray(items) ? items : [])
      .map(item => `${item.done ? "[x]" : "[ ]"} ${item.text || ""}`.trim())
      .join("\n");
  }

  function checklistFromText(value) {
    return String(value || "").split("\n").map(line => line.trim()).filter(Boolean).slice(0, 100).map(line => {
      const match = line.match(/^\[([xхXХ ])\]\s*(.*)$/);
      if (!match) return {text: line.slice(0, 300), done: false};
      return {
        text: String(match[2] || "").trim().slice(0, 300),
        done: !/^\s*$/.test(match[1]),
      };
    }).filter(item => item.text);
  }

  function chips(note) {
    const parts = [];
    if (note.pinned) parts.push('<span class="note-product-chip pin">Закреплено</span>');
    parts.push(`<span class="note-product-chip">${escapeHtml(CATEGORIES[note.category] || CATEGORIES.other)}</span>`);
    for (const tag of note.tags || []) parts.push(`<span class="note-product-chip">#${escapeHtml(tag)}</span>`);
    return parts.join("");
  }

  function renderChecklist(note) {
    const items = Array.isArray(note.checklist) ? note.checklist : [];
    if (!items.length) return "";
    return `<div class="note-product-checklist">${items.map((item, index) => `
      <label class="note-check-row${item.done ? " done" : ""}">
        <input type="checkbox" data-note-check="${index}"${item.done ? " checked" : ""}>
        <span>${escapeHtml(item.text)}</span>
      </label>`).join("")}</div>`;
  }

  function renderDetail(note) {
    current = note;
    const created = humanDate(note.created_at);
    const updated = humanDate(note.updated_at);
    showSheet(`
      <div class="note-product-head">
        <h2 class="note-product-title">${escapeHtml(note.title || "Без названия")}</h2>
        <button class="note-product-close" type="button" data-note-close aria-label="Закрыть">×</button>
      </div>
      <div class="note-product-body">${escapeHtml(note.text || "")}</div>
      <div class="note-product-meta">${chips(note)}</div>
      ${renderChecklist(note)}
      <div class="note-product-dates">${created ? `Создано: ${escapeHtml(created)}` : ""}${created && updated ? " · " : ""}${updated ? `Изменено: ${escapeHtml(updated)}` : ""}</div>
      <div class="note-product-actions three">
        <button class="note-product-button" type="button" data-note-pin>${note.pinned ? "Открепить" : "Закрепить"}</button>
        <button class="note-product-button primary" type="button" data-note-edit>Изменить</button>
        <button class="note-product-button danger" type="button" data-note-delete>Удалить</button>
      </div>`);
  }

  function blankNote() {
    return {
      note_id: null,
      title: "",
      text: "",
      category: "other",
      pinned: false,
      tags: [],
      checklist: [],
    };
  }

  function renderEditor(note, {creating = false, error = ""} = {}) {
    current = creating ? null : note;
    const value = note || blankNote();
    showSheet(`
      <div class="note-product-head">
        <h2 class="note-product-title">${creating ? "Новая заметка" : "Изменить заметку"}</h2>
        <button class="note-product-close" type="button" data-note-editor-cancel aria-label="Отмена">×</button>
      </div>
      <div class="note-editor-grid" data-note-editor-mode="${creating ? "create" : "edit"}" data-note-editor-id="${value.note_id || ""}">
        <label class="note-editor-field">Название
          <input id="noteEditTitle" maxlength="${MAX_TITLE}" autocomplete="off" placeholder="Можно оставить пустым" value="${escapeHtml(value.title || "")}">
        </label>
        <label class="note-editor-field">Текст
          <textarea id="noteEditText" maxlength="${MAX_TEXT}" placeholder="Что сохранить?">${escapeHtml(value.text || "")}</textarea>
        </label>
        <label class="note-editor-field">Категория
          <select id="noteEditCategory">${categoryOptions(value.category || "other")}</select>
        </label>
        <label class="note-editor-field">Теги
          <input id="noteEditTags" maxlength="500" autocomplete="off" placeholder="например: машина, покупки" value="${escapeHtml((value.tags || []).join(", "))}">
        </label>
        <label class="note-editor-check"><span>Закрепить сверху</span><input id="noteEditPinned" type="checkbox"${value.pinned ? " checked" : ""}></label>
        <label class="note-editor-field">Чек-лист
          <textarea id="noteEditChecklist" class="note-checklist-editor" placeholder="[ ] Купить молоко\n[x] Позвонить">${escapeHtml(checklistToText(value.checklist))}</textarea>
        </label>
        <div class="note-editor-help">Каждый пункт — с новой строки. `[x]` означает выполнено, `[ ]` — ещё нет.</div>
        <div id="noteEditError" class="note-editor-error">${escapeHtml(error)}</div>
        <div class="note-product-actions">
          <button class="note-product-button" type="button" data-note-editor-cancel>Отмена</button>
          <button class="note-product-button primary" type="button" data-note-save>${creating ? "Создать" : "Сохранить"}</button>
        </div>
      </div>`);
    requestAnimationFrame(() => backdrop()?.querySelector("#noteEditText")?.focus());
  }

  function collectEditor() {
    const root = backdrop();
    const read = id => root?.querySelector(id)?.value ?? "";
    return {
      title: read("#noteEditTitle").trim(),
      text: read("#noteEditText").trim(),
      category: read("#noteEditCategory") || "other",
      tags: tagsFromInput(read("#noteEditTags")),
      pinned: Boolean(root?.querySelector("#noteEditPinned")?.checked),
      checklist: checklistFromText(read("#noteEditChecklist")),
    };
  }

  function setEditorBusy(value) {
    const root = backdrop();
    root?.querySelectorAll("button,input,select,textarea").forEach(control => {
      if (control.matches("[data-note-editor-cancel]")) return;
      control.disabled = value;
    });
  }

  function editorError(text) {
    const target = backdrop()?.querySelector("#noteEditError");
    if (target) target.textContent = text || "";
  }

  function signalChanged() {
    lastDecoratedSignature = "";
    document.dispatchEvent(new Event("planner-library-changed"));
    document.dispatchEvent(new Event("planner-note-changed"));
    scheduleDecoration(true);
  }

  async function saveEditor() {
    const editor = backdrop()?.querySelector("[data-note-editor-mode]");
    if (!editor) return;
    const creating = editor.dataset.noteEditorMode === "create";
    const noteId = Number(editor.dataset.noteEditorId || 0);
    const draft = collectEditor();
    if (!draft.text) {
      editorError("Добавь текст заметки.");
      return;
    }
    setEditorBusy(true);
    editorError("");
    try {
      let saved;
      if (creating) {
        const result = await api("/api/note-tools", {
          method: "POST",
          body: JSON.stringify(draft),
        });
        saved = result.note;
      } else {
        const content = await api(`/api/note-tools/${noteId}`, {
          method: "PATCH",
          body: JSON.stringify({title: draft.title || current?.title || "Заметка", text: draft.text}),
        });
        const metadata = await api(`/api/note-tools/${noteId}/metadata`, {
          method: "PUT",
          body: JSON.stringify({
            pinned: draft.pinned,
            tags: draft.tags,
            checklist: draft.checklist,
            category: draft.category,
          }),
        });
        saved = metadata.note || content.note;
      }
      signalChanged();
      renderDetail(saved);
    } catch (error) {
      editorError(error?.message || "Не удалось сохранить заметку.");
    } finally {
      setEditorBusy(false);
    }
  }

  async function togglePinned(note) {
    const result = await api(`/api/note-tools/${note.note_id}/metadata`, {
      method: "PUT",
      body: JSON.stringify({
        pinned: !note.pinned,
        tags: note.tags || [],
        checklist: note.checklist || [],
        category: note.category || "other",
      }),
    });
    signalChanged();
    renderDetail(result.note);
  }

  async function toggleChecklist(note, index, checked) {
    const checklist = (note.checklist || []).map(item => ({...item}));
    if (!checklist[index]) return;
    checklist[index].done = Boolean(checked);
    const result = await api(`/api/note-tools/${note.note_id}/metadata`, {
      method: "PUT",
      body: JSON.stringify({
        pinned: Boolean(note.pinned),
        tags: note.tags || [],
        checklist,
        category: note.category || "other",
      }),
    });
    current = result.note;
    signalChanged();
    renderDetail(result.note);
  }

  function renderDelete(note) {
    current = note;
    showSheet(`
      <div class="note-product-head">
        <h2 class="note-product-title">Удалить заметку?</h2>
        <button class="note-product-close" type="button" data-note-delete-cancel aria-label="Отмена">×</button>
      </div>
      <p class="note-delete-copy">«${escapeHtml(note.title || "Без названия") }» будет удалена. Это действие можно отменить через историю действий, если она ещё доступна.</p>
      <div id="noteDeleteError" class="note-editor-error"></div>
      <div class="note-product-actions">
        <button class="note-product-button" type="button" data-note-delete-cancel>Отмена</button>
        <button class="note-product-button danger" type="button" data-note-delete-confirm>Удалить</button>
      </div>`);
  }

  async function confirmDelete(note) {
    const button = backdrop()?.querySelector("[data-note-delete-confirm]");
    if (button) button.disabled = true;
    try {
      await api(`/api/library/notes/${note.note_id}`, {method: "DELETE"});
      closeSheet();
      signalChanged();
    } catch (error) {
      const target = backdrop()?.querySelector("#noteDeleteError");
      if (target) target.textContent = error?.message || "Не удалось удалить заметку.";
      if (button) button.disabled = false;
    }
  }

  async function open(noteId) {
    const id = Number(noteId || 0);
    if (!id) return;
    showSheet('<div class="mobile-loading">Загружаю заметку…</div>');
    try {
      const {note} = await api(`/api/note-tools/${id}`);
      renderDetail(note);
    } catch (error) {
      showSheet(`
        <div class="note-product-head"><h2 class="note-product-title">Заметка</h2><button class="note-product-close" type="button" data-note-close>×</button></div>
        <div class="mobile-error">${escapeHtml(error?.message || "Не удалось загрузить заметку.")}</div>`);
    }
  }

  function create() {
    renderEditor(blankNote(), {creating: true});
  }

  function toolbar() {
    return document.getElementById("notesProductToolbar");
  }

  function notesActive() {
    return document.getElementById("libraryNotesTab")?.getAttribute("aria-selected") === "true";
  }

  function libraryOpen() {
    return document.getElementById("app")?.classList.contains("library-active");
  }

  function installToolbar() {
    if (toolbarInstalled) return true;
    const tabs = document.querySelector("#libraryScreen .library-tabs");
    if (!tabs) return false;
    const wrapper = document.createElement("div");
    wrapper.id = "notesProductToolbarWrap";
    wrapper.innerHTML = `
      <div id="notesProductToolbar" class="notes-product-toolbar">
        <input id="notesLibrarySearch" class="notes-search-input" type="search" maxlength="500" autocomplete="off" placeholder="Поиск по заметкам">
        <button id="notesSemanticSearch" class="notes-toolbar-button semantic" type="button">По смыслу</button>
        <button id="notesCreate" class="notes-toolbar-button primary" type="button" aria-label="Новая заметка">+</button>
      </div>
      <div id="notesSearchStatus" class="notes-search-status" role="status"></div>`;
    tabs.insertAdjacentElement("afterend", wrapper);
    toolbarInstalled = true;

    document.getElementById("notesCreate")?.addEventListener("click", create);
    document.getElementById("notesLibrarySearch")?.addEventListener("input", filterNoteCards);
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
    return true;
  }

  function syncToolbar() {
    const visible = notesActive() && libraryOpen();
    toolbar()?.classList.toggle("visible", Boolean(visible));
    const status = document.getElementById("notesSearchStatus");
    if (status && !visible) status.classList.remove("visible");
    if (visible) scheduleDecoration(false);
  }

  function noteRows() {
    return Array.from(document.querySelectorAll("#libraryList .note-swipe-row[data-id]"));
  }

  function rowSignature() {
    return noteRows().map(row => row.dataset.id).join(",");
  }

  function filterNoteCards() {
    if (!notesActive()) return;
    const input = document.getElementById("notesLibrarySearch");
    const query = String(input?.value || "").trim().toLocaleLowerCase("ru-RU");
    let visible = 0;
    noteRows().forEach(row => {
      const haystack = String(row.dataset.noteSearch || row.textContent || "").toLocaleLowerCase("ru-RU");
      const show = !query || query.split(/\s+/).every(token => haystack.includes(token));
      row.hidden = !show;
      if (show) visible += 1;
    });
    const status = document.getElementById("notesSearchStatus");
    if (!status) return;
    if (query) {
      status.textContent = visible ? `Найдено: ${visible}` : "По словам ничего не найдено. Попробуй поиск по смыслу.";
      status.classList.add("visible");
    } else {
      status.textContent = "";
      status.classList.remove("visible");
    }
  }

  function decorateRow(row, note) {
    row.dataset.noteSearch = [
      note.title,
      note.text,
      CATEGORIES[note.category] || "",
      ...(note.tags || []),
    ].join(" ");
    const card = row.querySelector(".library-card");
    if (!card) return;
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
      const item = document.createElement("span");
      item.className = "note-card-badge";
      item.textContent = `#${tag}`;
      badges.appendChild(item);
    }
    const meta = card.querySelector(".library-card-meta");
    if (meta) card.insertBefore(badges, meta);
    else card.appendChild(badges);
  }

  async function decorateNotes(force = false) {
    if (!notesActive() || !libraryOpen() || metadataLoading) return;
    const rows = noteRows();
    if (!rows.length) return;
    const signature = rowSignature();
    if (!force && signature && signature === lastDecoratedSignature) {
      filterNoteCards();
      return;
    }
    metadataLoading = true;
    try {
      const {notes} = await api("/api/note-tools");
      const list = document.getElementById("libraryList");
      const map = new Map(rows.map(row => [Number(row.dataset.id), row]));
      for (const note of notes || []) {
        const row = map.get(Number(note.note_id));
        if (!row) continue;
        decorateRow(row, note);
        list?.appendChild(row);
      }
      lastDecoratedSignature = rowSignature();
      filterNoteCards();
    } catch (_) {
      // The base library still remains usable if metadata decoration fails.
    } finally {
      metadataLoading = false;
    }
  }

  function scheduleDecoration(force = false) {
    if (force) lastDecoratedSignature = "";
    if (metadataQueued) return;
    metadataQueued = true;
    setTimeout(() => {
      metadataQueued = false;
      decorateNotes(force);
    }, 80);
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
    if (status) {
      status.textContent = "Ищу по смыслу…";
      status.classList.add("visible");
    }
    try {
      const data = await api("/api/note-tools/search", {
        method: "POST",
        body: JSON.stringify({query}),
      });
      const notes = Array.isArray(data.notes) ? data.notes : [];
      showSheet(`
        <div class="note-product-head">
          <h2 class="note-product-title">Поиск по заметкам</h2>
          <button class="note-product-close" type="button" data-note-close aria-label="Закрыть">×</button>
        </div>
        <div class="note-search-answer">${escapeHtml(data.answer || (notes.length ? "Найдены совпадения." : "Не нашёл."))}</div>
        <div class="note-search-results">${notes.length ? notes.map(note => `
          <button class="note-search-result" type="button" data-note-result="${Number(note.note_id)}">
            <div class="note-search-result-title">${escapeHtml(note.title || "Без названия")}</div>
            <div class="note-search-result-preview">${escapeHtml(note.text || "")}</div>
          </button>`).join("") : '<div class="mobile-empty">Подходящих заметок нет.</div>'}</div>`);
      if (status) {
        status.textContent = data.ai_used ? "Поиск выполнен по смыслу." : "Показаны совпадения по словам.";
      }
    } catch (error) {
      if (status) status.textContent = error?.message || "Не удалось выполнить поиск.";
    } finally {
      searchBusy = false;
      if (button) button.disabled = false;
    }
  }

  function install() {
    if (!installToolbar()) return;
    const list = document.getElementById("libraryList");
    const app = document.getElementById("app");
    if (list && !list.dataset.notesProductObserved) {
      list.dataset.notesProductObserved = "1";
      new MutationObserver(() => {
        if (notesActive() && libraryOpen()) scheduleDecoration(false);
      }).observe(list, {childList: true, subtree: false});
    }
    if (app && !app.dataset.notesProductObserved) {
      app.dataset.notesProductObserved = "1";
      new MutationObserver(syncToolbar).observe(app, {attributes: true, attributeFilter: ["class"]});
    }
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
    const root = backdrop();
    if (!root?.classList.contains("open")) return;
    if (event.target === root || event.target.closest?.("[data-note-close]")) {
      closeSheet();
      return;
    }
    if (event.target.closest?.("[data-note-edit]") && current) {
      renderEditor(current, {creating: false});
      return;
    }
    if (event.target.closest?.("[data-note-editor-cancel]")) {
      if (current) renderDetail(current); else closeSheet();
      return;
    }
    if (event.target.closest?.("[data-note-save]")) {
      saveEditor();
      return;
    }
    if (event.target.closest?.("[data-note-pin]") && current) {
      togglePinned(current).catch(error => {
        showSheet(`<div class="mobile-error">${escapeHtml(error?.message || "Не удалось изменить закрепление.")}</div>`);
      });
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
    if (event.target.closest?.("[data-note-delete-confirm]") && current) {
      confirmDelete(current);
      return;
    }
    const check = event.target.closest?.("[data-note-check]");
    if (check && current) {
      toggleChecklist(current, Number(check.dataset.noteCheck), check.checked).catch(() => renderDetail(current));
      return;
    }
    const result = event.target.closest?.("[data-note-result]");
    if (result) {
      open(Number(result.dataset.noteResult));
    }
  });

  document.addEventListener("planner-library-changed", () => scheduleDecoration(true));
  document.addEventListener("planner-ready", () => setTimeout(install, 0));
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && backdrop()?.classList.contains("open") && backdrop()?.querySelector(".note-product-sheet")) closeSheet();
  });

  window.PlannerNotes = {open, create, semanticSearch, refresh: () => scheduleDecoration(true)};
  if (document.readyState !== "loading") setTimeout(install, 0);
  else document.addEventListener("DOMContentLoaded", () => setTimeout(install, 0), {once: true});
})();
