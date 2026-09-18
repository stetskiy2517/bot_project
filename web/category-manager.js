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
    .category-manager{display:grid;gap:8px}
    .category-manager-row{display:grid;grid-template-columns:14px minmax(0,1fr) minmax(118px,.65fr) 34px;gap:8px;align-items:center}
    .category-manager-dot{width:11px;height:11px;border-radius:50%;background:#aaa}
    .category-manager-row input,.category-manager-row select,.category-manager-add input,.category-manager-add select{width:100%;min-width:0;border:1px solid #dededb;border-radius:10px;background:#fff;padding:9px 10px;font:inherit;color:#111}
    .category-manager-delete{width:34px;height:34px;border-radius:10px;background:#f3f3f1;color:#777;cursor:pointer;font-size:18px;line-height:1}
    .category-manager-delete.confirm{background:#fee9e7;color:#b42318;font-size:12px;font-weight:650}
    .category-manager-add{display:grid;grid-template-columns:minmax(0,1fr) minmax(118px,.65fr) auto;gap:8px;margin-top:4px}
    .category-manager-add button{border-radius:10px;padding:9px 12px;background:#111;color:#fff;cursor:pointer;font-weight:600}
    .category-manager-help{font-size:12px;line-height:1.4;color:#888884;margin:3px 0 0}
    .category-manager-error{font-size:12px;color:#b42318;min-height:16px}
    @media(max-width:520px){.category-manager-row{grid-template-columns:12px minmax(0,1fr) 34px}.category-manager-row select{grid-column:2/3}.category-manager-add{grid-template-columns:1fr auto}.category-manager-add select{grid-column:1/2}}
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
    return `<select data-category-color="${escapeHtml(category.key)}" aria-label="Цвет категории ${escapeHtml(category.label)}">` +
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
      ${categories.map(item => `<div class="category-manager-row" data-category-row="${escapeHtml(item.key)}">
        <span class="category-manager-dot" style="background:${colorHex[String(item.color_id || "")] || "#aaa"}"></span>
        <input data-category-label="${escapeHtml(item.key)}" maxlength="40" value="${escapeHtml(item.label)}" aria-label="Название категории" />
        ${colorSelect(item)}
        <button class="category-manager-delete" type="button" data-category-delete="${escapeHtml(item.key)}" aria-label="Удалить категорию">×</button>
      </div>`).join("")}
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
      select.addEventListener("change", () => patchCategory(select.dataset.categoryColor, {color_id: select.value || null}));
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
