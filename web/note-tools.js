(() => {
  "use strict";
  let installed = false;

  function api(path, options) {
    if (typeof window.api !== "function") throw new Error("API недоступен");
    return window.api(path, options);
  }

  function notify(text) {
    if (typeof window.msg === "function") window.msg(text);
  }

  function button(label, handler, className = "") {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `note-tool-action ${className}`.trim();
    item.textContent = label;
    item.onclick = async () => {
      item.disabled = true;
      try { await handler(); } catch (error) { notify(error.message); }
      finally { item.disabled = false; }
    };
    return item;
  }

  async function openTools(noteId) {
    const chat = document.getElementById("chat");
    if (!chat || chat.querySelector(`[data-note-tools="${noteId}"]`)) return;
    const {note} = await api(`/api/note-tools/${noteId}`);
    const panel = document.createElement("div");
    panel.className = "msg assistant note-tool-panel";
    panel.dataset.noteTools = String(noteId);

    const meta = document.createElement("div");
    meta.className = "note-tool-meta";
    meta.textContent = `${note.pinned ? "Закреплено · " : ""}${(note.tags || []).length ? "#" + note.tags.join(" #") : "Без тегов"}`;
    const actions = document.createElement("div");
    actions.className = "note-tool-actions";

    actions.append(
      button("Изменить", async () => {
        const title = prompt("Название заметки", note.title || "");
        if (title == null) return;
        const text = prompt("Текст заметки", note.text || "");
        if (text == null) return;
        const data = await api(`/api/note-tools/${noteId}`, {
          method: "PATCH", body: JSON.stringify({title, text}),
        });
        notify(`Заметка обновлена · ${data.note.title}`);
        document.dispatchEvent(new Event("planner-library-changed"));
      }),
      button(note.pinned ? "Открепить" : "Закрепить", async () => {
        await api(`/api/note-tools/${noteId}/metadata`, {
          method: "PUT",
          body: JSON.stringify({pinned: !note.pinned, tags: note.tags || [], checklist: note.checklist || []}),
        });
        panel.remove();
        await openTools(noteId);
      }),
      button("Теги", async () => {
        const raw = prompt("Теги через запятую", (note.tags || []).join(", "));
        if (raw == null) return;
        const tags = raw.split(",").map((item) => item.trim()).filter(Boolean);
        await api(`/api/note-tools/${noteId}/metadata`, {
          method: "PUT",
          body: JSON.stringify({pinned: Boolean(note.pinned), tags, checklist: note.checklist || []}),
        });
        panel.remove();
        await openTools(noteId);
      }),
      button("В задачу", async () => {
        const estimateRaw = prompt("Сколько времени выделить на задачу, минут? Можно пусто.", "60");
        const dueRaw = prompt("Срок задачи в формате 2026-09-20T18:00. Можно пусто.");
        let dueAt = null;
        if (dueRaw?.trim()) {
          const date = new Date(dueRaw.trim());
          if (!Number.isFinite(date.getTime())) throw new Error("Некорректный срок");
          dueAt = date.toISOString();
        }
        await api(`/api/note-tools/${noteId}/to-task`, {
          method: "POST",
          body: JSON.stringify({
            title: note.title,
            estimate_minutes: estimateRaw?.trim() ? Number(estimateRaw) : null,
            due_at: dueAt,
            flexible: true,
          }),
        });
        notify(`Создана задача · ${note.title}`);
        document.dispatchEvent(new Event("planner-library-changed"));
      }),
    );

    const checklistWrap = document.createElement("details");
    checklistWrap.className = "note-checklist-tools";
    const summary = document.createElement("summary");
    summary.textContent = `Чек-лист · ${(note.checklist || []).length}`;
    checklistWrap.append(summary);
    const list = document.createElement("div");
    list.className = "note-checklist-list";
    const checklist = Array.isArray(note.checklist) ? note.checklist.map((item) => ({...item})) : [];

    function renderChecklist() {
      list.replaceChildren();
      checklist.forEach((item, index) => {
        const row = document.createElement("label");
        row.className = "note-checklist-row";
        const check = document.createElement("input");
        check.type = "checkbox";
        check.checked = Boolean(item.done);
        check.onchange = () => { checklist[index].done = check.checked; };
        const text = document.createElement("span");
        text.textContent = item.text;
        row.append(check, text);
        list.append(row);
      });
    }
    renderChecklist();
    const checklistActions = document.createElement("div");
    checklistActions.className = "note-tool-actions";
    checklistActions.append(
      button("Добавить пункт", async () => {
        const text = prompt("Новый пункт чек-листа");
        if (!text?.trim()) return;
        checklist.push({text: text.trim(), done: false});
        renderChecklist();
      }),
      button("Сохранить чек-лист", async () => {
        await api(`/api/note-tools/${noteId}/metadata`, {
          method: "PUT",
          body: JSON.stringify({pinned: Boolean(note.pinned), tags: note.tags || [], checklist}),
        });
        notify("Чек-лист сохранён");
      }),
    );
    checklistWrap.append(list, checklistActions);
    panel.append(meta, actions, checklistWrap);
    chat.append(panel);
    chat.scrollTop = chat.scrollHeight;
  }

  async function semanticSearch() {
    const input = document.getElementById("noteSemanticQuery");
    const state = document.getElementById("noteSemanticState");
    const query = input.value.trim();
    if (!query) return;
    state.textContent = "Ищу по заметкам…";
    try {
      const data = await api("/api/note-tools/search", {
        method: "POST", body: JSON.stringify({query}),
      });
      const lines = [data.answer || ""];
      for (const note of data.notes || []) lines.push(`• ${note.title}`);
      state.textContent = lines.filter(Boolean).join("\n");
    } catch (error) {
      state.textContent = error.message;
    }
  }

  function install() {
    if (installed) return;
    const root = document.getElementById("assistantSettings") || document.querySelector("#settingsPanel .sheet");
    if (!root) return;
    installed = true;
    const section = document.createElement("details");
    section.className = "assistant-section settings-group";
    section.innerHTML = `
      <summary class="settings-group-summary-ready">
        <span class="settings-group-title">Поиск по заметкам</span>
        <span class="settings-group-meta">ИИ</span>
      </summary>
      <p class="settings-help">Обычный поиск работает по словам. При доступном ИИ можно искать по смыслу, например: «где я записывал размеры шин?»</p>
      <div class="note-semantic-search">
        <input id="noteSemanticQuery" type="text" maxlength="500" placeholder="Что найти в заметках?">
        <button id="noteSemanticSearch" class="action" type="button">Найти</button>
      </div>
      <p id="noteSemanticState" class="settings-help note-semantic-state" role="status"></p>`;
    root.append(section);
    section.querySelector("#noteSemanticSearch").onclick = semanticSearch;
    section.querySelector("#noteSemanticQuery").addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); semanticSearch(); }
    });
    const style = document.createElement("style");
    style.textContent = `
      .note-tool-actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
      .note-tool-action{min-height:34px;padding:0 10px;border-radius:10px;background:#efefed;color:#333;font-size:12px;font-weight:600;cursor:pointer}
      .note-tool-meta{color:#888;font-size:11px;margin-bottom:5px}
      .note-checklist-tools{margin-top:10px}.note-checklist-tools summary{cursor:pointer;font-size:13px;font-weight:650}
      .note-checklist-list{display:grid;gap:5px;margin:8px 0}.note-checklist-row{display:flex;gap:8px;align-items:flex-start;font-size:13px}
      .note-semantic-search{display:grid;grid-template-columns:1fr auto;gap:8px}.note-semantic-search input{min-width:0;padding:10px;border:1px solid #ddd;border-radius:10px;font:inherit}
      .note-semantic-state{white-space:pre-line}
    `;
    document.head.append(style);
  }

  document.addEventListener("planner-library-open", (event) => {
    if (event.detail?.type === "note") openTools(Number(event.detail.id)).catch((error) => notify(error.message));
  });
  document.addEventListener("planner-ready", install);
  if (document.readyState !== "loading") setTimeout(install, 0);
})();
