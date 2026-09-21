(() => {
  const container = document.getElementById("categoryColors");
  const group = document.getElementById("categoryColorsGroup");
  if (!container || !group || container.dataset.dynamicCategories === "1") return;
  container.dataset.dynamicCategories = "1";

  const colorOptions = [
    ["", "Без цвета"], ["1", "Лавандовый"], ["2", "Зелёный"], ["3", "Фиолетовый"],
    ["4", "Коралловый"], ["5", "Жёлтый"], ["6", "Оранжевый"], ["7", "Голубой"],
    ["8", "Серый"], ["9", "Синий"], ["10", "Тёмно-зелёный"], ["11", "Красный"],
  ];
  const colorHex = {
    "1": "#7986cb", "2": "#33b679", "3": "#8e24aa", "4": "#e67c73",
    "5": "#f6c026", "6": "#f5511d", "7": "#039be5", "8": "#616161",
    "9": "#3f51b5", "10": "#0b8043", "11": "#d60000",
  };

  const style = document.createElement("style");
  style.textContent = `
    #categoryColors{display:block!important;background:#fff!important;border:0!important;border-radius:0!important;overflow:visible!important}
    .category-manager{display:block;background:#fff}
    .category-manager-list{overflow:hidden;background:#fff}
    .category-manager-row{display:grid;grid-template-columns:14px minmax(86px,1fr) minmax(108px,126px) 30px;gap:10px;align-items:center;min-height:58px;padding:8px 10px 8px 14px;background:#fff}
    .category-manager-row+.category-manager-row{border-top:1px solid #ececea}
    .category-manager-dot{width:14px;height:14px;border-radius:50%;background:#d9d9d6;box-shadow:inset 0 0 0 1px rgba(0,0,0,.08)}
    .category-manager-name{width:100%;min-width:0;border:0!important;outline:0;background:transparent!important;border-radius:9px!important;padding:8px 6px!important;font:inherit;font-size:16px!important;font-weight:600;color:#171717}
    .category-manager-name:focus{background:#f5f5f3!important}
    .category-manager-color{width:100%;min-width:0;border:0!important;outline:0;background:#f2f2f0!important;border-radius:10px!important;padding:8px 28px 8px 10px!important;font:inherit;font-size:16px!important;color:#5f5f5b}
    .category-manager-delete{width:44px;height:44px;border-radius:10px;background:transparent;color:#aaa9a5;cursor:pointer;font-size:18px;line-height:1;transition:background .16s ease,color .16s ease}
    .category-manager-delete:hover{background:#f3f3f1;color:#5f5f5b}
    .category-manager-delete.confirm{background:#fee9e7;color:#b42318;font-size:11px;font-weight:650}
    .category-manager-add{display:grid;grid-template-columns:minmax(0,1fr) minmax(112px,132px) auto;gap:8px;align-items:center;padding:14px;border-top:1px solid #ececea;background:#fff}
    .category-manager-add input,.category-manager-add select{width:100%;min-width:0;border:0;outline:0;border-radius:10px;background:#f5f5f3;padding:10px 11px;font:inherit;color:#171717}
    .category-manager-add input::placeholder{color:#9b9b96}
    .category-manager-add button{min-height:44px;border-radius:10px;padding:9px 13px;background:#2d2d2c;color:#fff;cursor:pointer;font-weight:600;white-space:nowrap}
    .category-manager-help{font-size:12px;line-height:1.45;color:#888884;margin:0;padding:0 14px 14px;background:#fff}
    .category-manager-error{font-size:12px;color:#b42318;min-height:0;margin:0;padding:0 14px 12px;background:#fff}
    .category-manager-error:empty{display:none}
    @media(max-width:430px){
      .category-manager-row{grid-template-columns:14px minmax(72px,1fr) minmax(100px,112px) 30px;gap:8px;padding-left:12px}
      .category-manager-name{font-size:16px!important}
      .category-manager-color{font-size:16px!important;padding-left:8px!important}
      .category-manager-add{grid-template-columns:minmax(0,1fr) auto}
      .category-manager-add input{grid-column:1/-1}
      .category-manager-add select{min-width:0}
    }
  `;
  document.head.appendChild(style);

  let categories = [];
  let loadVersion = 0;

  const escapeHtml = value => String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));

  async function requestJson(url, options = {}) {
    if (typeof window.api === "function") return window.api(url, options);
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", ...(options.headers || {})},
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.message || payload.error || "request_failed");
    return payload;
  }

  function colorSelect(category) {
    return `<select class="category-manager-color" data-category-color="${escapeHtml(category.key)}" aria-label="Цвет категории ${escapeHtml(category.label)}">` +
      colorOptions.map(([value, label]) => `<option value="${value}"${String(category.color_id || "") === value ? " selected" : ""}>${label}</option>`).join("") +
      `</select>`;
  }

  function updateMeta() {
    const meta = group.querySelector(".settings-group-meta");
    if (meta) meta.textContent = `${categories.length} ${categories.length === 1 ? "категория" : categories.length < 5 ? "категории" : "категорий"}`;
  }

  function render(message = "") {
    updateMeta();
    container.innerHTML = `<div class="category-manager">
      <div class="category-manager-list">
        ${categories.map(item => `<div class="category-manager-row" data-category-row="${escapeHtml(item.key)}">
          <span class="category-manager-dot" style="background:${colorHex[String(item.color_id || "")] || "#d9d9d6"}"></span>
          <input class="category-manager-name" data-category-label="${escapeHtml(item.key)}" maxlength="40" value="${escapeHtml(item.label)}" aria-label="Название категории ${escapeHtml(item.label)}" />
          ${colorSelect(item)}
          <button class="category-manager-delete" type="button" data-category-delete="${escapeHtml(item.key)}" aria-label="Удалить категорию ${escapeHtml(item.label)}">×</button>
        </div>`).join("")}
      </div>
      <div class="category-manager-add">
        <input id="newCategoryLabel" maxlength="40" placeholder="Новая категория" aria-label="Название новой категории" />
        <select id="newCategoryColor" aria-label="Цвет новой категории">${colorOptions.map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}</select>
        <button id="addCategoryBtn" type="button">Добавить</button>
      </div>
      <p class="category-manager-help">Автокатегоризация остаётся для стандартных смыслов. Свои категории можно назначать событию вручную. Удаление категории не удаляет сами события.</p>
      <div id="categoryManagerError" class="category-manager-error">${escapeHtml(message)}</div>
    </div>`;
    bind();
  }

  function message(value) {
    const node = document.getElementById("categoryManagerError");
    if (node) node.textContent = value || "";
  }

  async function patchCategory(key, patch) {
    try {
      const result = await requestJson(`/api/categories/${encodeURIComponent(key)}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
      categories = result.categories || categories;
      render();
      window.dispatchEvent(new CustomEvent("planner:categories-changed", {detail: {categories}}));
    } catch (error) {
      message(error.message || "Не удалось сохранить категорию");
      await load();
    }
  }

  function bind() {
    container.querySelectorAll("[data-category-label]").forEach(input => {
      input.addEventListener("change", () => patchCategory(input.dataset.categoryLabel, {label: input.value}));
      input.addEventListener("keydown", event => {
        if (event.key === "Enter") { event.preventDefault(); input.blur(); }
      });
    });
    container.querySelectorAll("[data-category-color]").forEach(select => {
      select.addEventListener("change", () => {
        const row = select.closest(".category-manager-row");
        const dot = row?.querySelector(".category-manager-dot");
        if (dot) dot.style.background = colorHex[select.value] || "#d9d9d6";
        patchCategory(select.dataset.categoryColor, {color_id: select.value || null});
      });
    });
    container.querySelectorAll("[data-category-delete]").forEach(button => {
      button.addEventListener("click", async () => {
        if (button.dataset.confirmDelete !== "1") {
          button.dataset.confirmDelete = "1";
          button.classList.add("confirm");
          button.textContent = "ещё";
          setTimeout(() => {
            if (!button.isConnected || button.dataset.confirmDelete !== "1") return;
            delete button.dataset.confirmDelete;
            button.classList.remove("confirm");
            button.textContent = "×";
          }, 2500);
          return;
        }
        try {
          const result = await requestJson(`/api/categories/${encodeURIComponent(button.dataset.categoryDelete)}`, {method: "DELETE"});
          categories = result.categories || [];
          render();
          window.dispatchEvent(new CustomEvent("planner:categories-changed", {detail: {categories}}));
        } catch (error) {
          message(error.message || "Не удалось удалить категорию");
        }
      });
    });
    document.getElementById("addCategoryBtn")?.addEventListener("click", async () => {
      const label = document.getElementById("newCategoryLabel")?.value.trim() || "";
      const colorId = document.getElementById("newCategoryColor")?.value || null;
      if (!label) { message("Напиши название категории"); return; }
      try {
        const result = await requestJson("/api/categories", {
          method: "POST",
          body: JSON.stringify({label, color_id: colorId}),
        });
        categories = result.categories || [];
        render();
        window.dispatchEvent(new CustomEvent("planner:categories-changed", {detail: {categories}}));
      } catch (error) {
        message(error.message || "Не удалось создать категорию");
      }
    });
  }

  async function load() {
    const version = ++loadVersion;
    try {
      const result = await requestJson("/api/categories");
      if (version !== loadVersion) return;
      categories = Array.isArray(result.categories) ? result.categories : [];
      render();
    } catch (error) {
      render(error.message || "Не удалось загрузить категории");
    }
  }

  window.PlannerCategories = {
    list: () => categories.map(item => ({...item})),
    refresh: load,
  };

  document.getElementById("accountBtn")?.addEventListener("click", () => setTimeout(load, 0));
  group.addEventListener("toggle", () => { if (group.open) load(); });
  window.addEventListener("planner:settings-refreshed", load);
  load();
})();
