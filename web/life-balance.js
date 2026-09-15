(() => {
  "use strict";
  const labels = {
    work: "Работа", health: "Здоровье", rest: "Отдых", travel: "Поездки",
    family: "Семья", personal: "Личное", other: "Прочее",
  };
  let installed = false;

  function api(path, options) {
    if (typeof window.api !== "function") throw new Error("API недоступен");
    return window.api(path, options);
  }

  async function loadRatings() {
    const host = document.getElementById("lifeBalanceControls");
    if (!host) return;
    try {
      const data = await api("/api/life-balance/ratings");
      const ratings = data.ratings || {};
      host.querySelectorAll("[data-balance-category]").forEach((row) => {
        const value = ratings[row.dataset.balanceCategory] || {};
        row.querySelector('[name="rating"]').value = value.rating ?? "";
        row.querySelector('[name="target"]').value = value.target ?? "";
      });
    } catch (error) {
      document.getElementById("lifeBalanceState").textContent = error.message;
    }
  }

  async function saveRow(row) {
    const category = row.dataset.balanceCategory;
    const ratingRaw = row.querySelector('[name="rating"]').value;
    const targetRaw = row.querySelector('[name="target"]').value;
    const payload = {
      rating: ratingRaw === "" ? null : Number(ratingRaw),
      target: targetRaw === "" ? null : Number(targetRaw),
    };
    await api(`/api/life-balance/ratings/${encodeURIComponent(category)}`, {
      method: "PUT", body: JSON.stringify(payload),
    });
  }

  function install() {
    if (installed) return;
    const sheet = document.querySelector("#lifeWheelPanel .life-wheel-sheet");
    if (!sheet) return;
    installed = true;
    const panel = document.createElement("details");
    panel.className = "life-balance-controls";
    panel.innerHTML = `
      <summary>Моя оценка и желаемый баланс</summary>
      <p class="life-wheel-note">Автоматическая диаграмма показывает фактическую активность. Здесь можно отдельно оценить, насколько тебя устраивает каждая сфера, и задать желаемый уровень.</p>
      <div id="lifeBalanceControls"></div>
      <button id="saveLifeBalance" class="life-wheel-retry" type="button">Сохранить оценки</button>
      <p id="lifeBalanceState" class="life-wheel-note" role="status"></p>`;
    sheet.append(panel);
    const host = panel.querySelector("#lifeBalanceControls");
    for (const [key, label] of Object.entries(labels)) {
      const row = document.createElement("div");
      row.className = "life-balance-row";
      row.dataset.balanceCategory = key;
      row.innerHTML = `
        <span>${label}</span>
        <label>Сейчас <input name="rating" type="number" min="0" max="10" step="0.5" inputmode="decimal"></label>
        <label>Хочу <input name="target" type="number" min="0" max="10" step="0.5" inputmode="decimal"></label>`;
      host.append(row);
    }
    panel.addEventListener("toggle", () => { if (panel.open) loadRatings(); });
    panel.querySelector("#saveLifeBalance").onclick = async () => {
      const state = panel.querySelector("#lifeBalanceState");
      const button = panel.querySelector("#saveLifeBalance");
      button.disabled = true;
      try {
        for (const row of host.querySelectorAll("[data-balance-category]")) await saveRow(row);
        state.textContent = "Сохранено. Автоматическая активность и твоя самооценка теперь учитываются отдельно.";
      } catch (error) {
        state.textContent = error.message;
      } finally {
        button.disabled = false;
      }
    };
    const style = document.createElement("style");
    style.textContent = `
      .life-balance-controls{margin-top:14px;border-top:1px solid #ececea;padding-top:12px}
      .life-balance-controls summary{cursor:pointer;font-size:14px;font-weight:650}
      .life-balance-row{display:grid;grid-template-columns:minmax(90px,1fr) 100px 100px;gap:8px;align-items:center;padding:8px 0;border-bottom:1px solid #f0f0ed;font-size:13px}
      .life-balance-row label{display:grid;gap:3px;color:#888;font-size:10px}
      .life-balance-row input{width:100%;box-sizing:border-box;padding:7px;border:1px solid #ddd;border-radius:9px;font:inherit;color:#111}
      @media(max-width:480px){.life-balance-row{grid-template-columns:1fr 74px 74px}}
    `;
    document.head.append(style);
  }

  const observer = new MutationObserver(install);
  observer.observe(document.documentElement, {childList: true, subtree: true});
  document.addEventListener("planner-ready", () => setTimeout(install, 0));
  if (document.readyState !== "loading") setTimeout(install, 50);
})();
