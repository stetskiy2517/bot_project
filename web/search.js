(() => {
  "use strict";
  const labels = {note:"Заметки", task:"Задачи", memory:"О тебе", company:"Компании", contact:"Контакты", interaction:"История", commitment:"Договорённости"};
  const api = (path, options) => window.PlannerRequests.request(path, options);
  const dialog = document.createElement("dialog");
  dialog.id = "globalSearchDialog";
  dialog.className = "secretary-record-dialog";
  dialog.setAttribute("aria-label", "Общий поиск");
  dialog.innerHTML = '<button type="button" data-search-close aria-label="Закрыть">×</button><h2>Поиск</h2><form id="globalSearchForm"><label>Заметки, задачи и память<input id="globalSearchQuery" type="search" maxlength="300" required autocomplete="off"></label><button type="submit">Найти</button></form><p id="globalSearchStatus" role="status"></p><div id="globalSearchResults"></div>';
  document.body.appendChild(dialog);
  const style = document.createElement("style");
  style.textContent = `.secretary-record-dialog{width:min(500px,calc(100vw - 24px));max-height:85dvh;box-sizing:border-box;border:1px solid #ddd;border-radius:20px;padding:20px;color:#222;background:#fff;overflow:auto}.secretary-record-dialog::backdrop{background:#0006}.secretary-record-dialog h2{margin:0 32px 16px 0;font-size:20px}.secretary-record-dialog button{padding:10px 12px;border-radius:10px;background:#eee;color:#222;cursor:pointer;margin:4px 4px 4px 0}.secretary-record-dialog label{display:block;font-size:14px;margin:12px 0}.secretary-record-dialog input{box-sizing:border-box;width:100%;padding:10px;border:1px solid #ccc;border-radius:10px;font-size:16px;margin-top:5px}.secretary-record-dialog pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}.secretary-search-result{display:block;width:100%;text-align:left;overflow-wrap:anywhere}.secretary-search-result span{display:block;font-size:12px;color:#666;margin-top:5px}.secretary-record-dialog [data-search-close],.secretary-record-dialog [data-record-close]{float:right}.memory-list-row .memory-card-actions{flex-wrap:wrap}.memory-more{padding:10px;border-radius:10px;margin-top:8px}.memory-list-row{flex-wrap:wrap}`;
  document.head.appendChild(style);
  style.textContent += '#globalSearchOpen{position:absolute;top:calc(env(safe-area-inset-top,0px) + 14px);right:122px}.app.mobile-shell #globalSearchOpen{top:calc(env(safe-area-inset-top,0px) + 10px);right:118px;width:44px;height:44px}';
  let generation = 0;
  let query = "";
  const box = dialog.querySelector("#globalSearchResults");
  const status = dialog.querySelector("#globalSearchStatus");

  async function openResult(item) {
    await api("/api/search/open", {method:"POST", body:JSON.stringify({kind:item.kind,id:item.id})});
    dialog.close();
    if (item.kind === "note") return window.PlannerNotes.open(item.id);
    return window.PlannerMemory.openEntity(item.kind, item.id);
  }

  function resultButton(item) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secretary-search-result";
    const title = document.createElement("strong"); title.textContent = item.title;
    const summary = document.createElement("span"); summary.textContent = item.snippet || "";
    const date = document.createElement("span"); date.textContent = item.date ? new Date(item.date).toLocaleString("ru-RU") : "";
    button.append(title, summary, date);
    button.onclick = () => openResult(item).catch(error => {status.textContent = error.message; if (!dialog.open) dialog.showModal();});
    return button;
  }

  function renderGroup(kind, page, section=null) {
    if (!section) {
      section = document.createElement("section");
      const title = document.createElement("h3"); title.textContent = labels[kind] || kind;
      section.appendChild(title); box.appendChild(section);
    }
    section.querySelector("[data-search-more]")?.remove();
    for (const item of page.items) section.appendChild(resultButton(item));
    if (page.next_cursor) {
      const more = document.createElement("button"); more.type = "button"; more.textContent = "Показать ещё"; more.dataset.searchMore = kind;
      const selectedQuery = query;
      more.onclick = async () => {
        more.disabled = true;
        try {
          const data = await api(`/api/search?q=${encodeURIComponent(selectedQuery)}&kind=${kind}&before=${page.next_cursor}`);
          if (query === selectedQuery && section.isConnected) renderGroup(kind, data.groups[kind], section);
        } catch(error) {status.textContent = error.message; more.disabled=false;}
      };
      section.appendChild(more);
    }
  }

  dialog.querySelector("#globalSearchForm").onsubmit = async event => {
    event.preventDefault();
    query = dialog.querySelector("#globalSearchQuery").value.trim();
    if (!query) return;
    const selected = ++generation;
    box.replaceChildren(); status.textContent = "Ищу…";
    try {
      const data = await api("/api/search?q="+encodeURIComponent(query));
      if (selected !== generation) return;
      let found = false;
      for (const [kind, page] of Object.entries(data.groups)) if (page.items.length) {renderGroup(kind,page); found=true;}
      if (data.related?.length) {
        const section = document.createElement("section"); const title = document.createElement("h3"); title.textContent = "Связанные записи"; section.appendChild(title);
        for (const item of data.related) section.appendChild(resultButton(item)); box.appendChild(section); found=true;
      }
      status.textContent = found ? "Результаты по тексту и связанные записи показаны отдельно." : "Ничего не найдено.";
    } catch(error) {if (selected === generation) status.textContent = error.message;}
  };
  dialog.querySelector("[data-search-close]").onclick = () => dialog.close();
  dialog.addEventListener("close", () => {generation++;});
  function open() {if (!dialog.open) dialog.showModal(); dialog.querySelector("input").focus();}
  const button = document.createElement("button");
  button.id="globalSearchOpen"; button.className="account-button"; button.type="button"; button.setAttribute("aria-label","Общий поиск"); button.title="Общий поиск";
  button.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/></svg>';
  button.onclick=open;
  document.querySelector(".topbar")?.prepend(button);
  window.PlannerSearch={open,openResult};
  document.addEventListener("keydown", event => {if ((event.ctrlKey || event.metaKey) && event.key === "k") {event.preventDefault();open();}});
})();
