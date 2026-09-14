(() => {
  const topbar = document.querySelector(".topbar");
  const accountButton = document.getElementById("accountBtn");
  if (!topbar || !accountButton || document.getElementById("lifeWheelBtn")) return;

  const googleColorHex = {
    "1": "#7986cb", "2": "#33b679", "3": "#8e24aa", "4": "#e67c73",
    "5": "#f6c026", "6": "#f5511d", "7": "#039be5", "8": "#616161",
    "9": "#3f51b5", "10": "#0b8043", "11": "#d60000",
  };

  const style = document.createElement("style");
  style.textContent = `
    .topbar { gap: 8px; }
    .life-wheel-button {
      pointer-events:auto;width:44px;height:44px;border-radius:50%;background:rgba(255,255,255,.9);
      border:1px solid var(--line);display:grid;place-items:center;box-shadow:0 5px 18px rgba(0,0,0,.05);
      cursor:pointer;color:#111;
    }
    .life-wheel-button svg {width:21px;height:21px;stroke:currentColor;stroke-width:1.55;fill:none;stroke-linecap:round;stroke-linejoin:round}
    .life-wheel-overlay {
      position:fixed;inset:0;height:var(--app-height,100dvh);z-index:55;background:rgba(247,247,245,.9);
      backdrop-filter:blur(22px);display:none;align-items:flex-end;justify-content:center;padding:12px;
    }
    .life-wheel-overlay.open {display:flex}
    .life-wheel-sheet {
      width:min(620px,100%);max-height:min(92dvh,calc(var(--app-height,100dvh) - 24px));overflow:auto;
      background:#fff;border:1px solid var(--line);border-radius:28px;padding:10px 18px calc(env(safe-area-inset-bottom) + 20px);
      box-shadow:0 22px 70px rgba(0,0,0,.12);
    }
    .life-wheel-head {display:flex;align-items:center;justify-content:space-between;margin-bottom:4px}
    .life-wheel-head h2 {font-size:22px;letter-spacing:-.55px;margin:0}
    .life-wheel-close {width:34px;height:34px;border-radius:50%;background:#f1f1ef;display:grid;place-items:center;cursor:pointer}
    .life-wheel-close svg {width:16px;height:16px;stroke:#555;stroke-width:1.8}
    .life-wheel-intro {margin:5px 0 14px;color:#777773;font-size:13px;line-height:1.45}
    .life-wheel-periods {display:flex;gap:6px;background:#f1f1ef;padding:4px;border-radius:13px;margin-bottom:12px}
    .life-wheel-period {flex:1;padding:8px 10px;border-radius:10px;background:transparent;color:#666;cursor:pointer;font-size:13px}
    .life-wheel-period.active {background:#fff;color:#111;box-shadow:0 1px 5px rgba(0,0,0,.07);font-weight:600}
    .life-wheel-chart-wrap {min-height:330px;display:grid;place-items:center;padding:3px 0 5px}
    .life-wheel-chart {width:min(100%,420px);height:auto;overflow:visible}
    .life-wheel-grid {fill:none;stroke:#dededb;stroke-width:1}
    .life-wheel-axis {stroke:#e6e6e3;stroke-width:1}
    .life-wheel-shape {fill:rgba(17,17,17,.13);stroke:#111;stroke-width:2;stroke-linejoin:round}
    .life-wheel-point {fill:#fff;stroke:#111;stroke-width:1.7}
    .life-wheel-label {fill:#555;font-size:11px;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",Arial,sans-serif;text-anchor:middle}
    .life-wheel-score {fill:#111;font-size:10px;font-weight:600;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",Arial,sans-serif;text-anchor:middle}
    .life-wheel-list {display:grid;gap:1px;border:1px solid #ececea;border-radius:16px;overflow:hidden;background:#ececea;margin-top:8px}
    .life-wheel-row {display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;background:#fff;padding:12px 13px}
    .life-wheel-row-main {display:flex;align-items:center;gap:9px;min-width:0}
    .life-wheel-dot {width:11px;height:11px;border-radius:50%;flex:0 0 auto;background:#aaa}
    .life-wheel-row-name {font-size:14px;font-weight:600}
    .life-wheel-row-meta {font-size:12px;color:#92928e;margin-top:2px}
    .life-wheel-row-score {font-size:16px;font-weight:650;font-variant-numeric:tabular-nums}
    .life-wheel-note {margin:12px 2px 0;color:#888884;font-size:12px;line-height:1.45}
    .life-wheel-empty,.life-wheel-error {padding:46px 18px;text-align:center;color:#777773;font-size:14px;line-height:1.5}
    .life-wheel-retry {margin-top:12px;border-radius:12px;padding:10px 14px;background:#111;color:#fff;cursor:pointer}
    @media (min-width:760px) {.life-wheel-overlay{align-items:center}.life-wheel-sheet{max-height:min(860px,calc(100dvh - 48px))}}
  `;
  document.head.appendChild(style);

  const button = document.createElement("button");
  button.id = "lifeWheelBtn";
  button.type = "button";
  button.className = "life-wheel-button";
  button.setAttribute("aria-label", "Колесо жизни");
  button.title = "Колесо жизни";
  button.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">
    <circle cx="12" cy="12" r="8.2"/><path d="M12 3.8v16.4M3.8 12h16.4M6.2 6.2l11.6 11.6M17.8 6.2 6.2 17.8"/>
  </svg>`;
  topbar.insertBefore(button, accountButton);

  const overlay = document.createElement("div");
  overlay.id = "lifeWheelPanel";
  overlay.className = "life-wheel-overlay";
  overlay.innerHTML = `
    <section class="life-wheel-sheet" role="dialog" aria-modal="true" aria-labelledby="lifeWheelTitle">
      <div class="handle"></div>
      <div class="life-wheel-head">
        <h2 id="lifeWheelTitle">Колесо жизни</h2>
        <button id="closeLifeWheel" class="life-wheel-close" type="button" aria-label="Закрыть">
          <svg viewBox="0 0 20 20"><path d="m5 5 10 10M15 5 5 15"/></svg>
        </button>
      </div>
      <p class="life-wheel-intro">Показывает, каким сферам ты фактически уделял время. Это не оценка качества жизни.</p>
      <div class="life-wheel-periods" role="group" aria-label="Период диаграммы">
        <button class="life-wheel-period" type="button" data-days="7">7 дней</button>
        <button class="life-wheel-period active" type="button" data-days="30">30 дней</button>
        <button class="life-wheel-period" type="button" data-days="90">90 дней</button>
      </div>
      <div id="lifeWheelContent"><div class="life-wheel-empty">Загружаю данные…</div></div>
    </section>`;
  document.body.appendChild(overlay);

  const content = document.getElementById("lifeWheelContent");
  const closeButton = document.getElementById("closeLifeWheel");
  let days = 30;
  let requestVersion = 0;

  function point(cx, cy, radius, index, count) {
    const angle = -Math.PI / 2 + index * Math.PI * 2 / count;
    return [cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius];
  }

  function polygonPoints(cx, cy, radius, values) {
    return values.map((value, index) => {
      const [x, y] = point(cx, cy, radius * Math.max(0, Math.min(10, value)) / 10, index, values.length);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
  }

  function chart(categories) {
    const size = 360;
    const center = size / 2;
    const radius = 116;
    const count = categories.length;
    const grid = [2, 4, 6, 8, 10].map(level =>
      `<polygon class="life-wheel-grid" points="${polygonPoints(center, center, radius, Array(count).fill(level))}"/>`
    ).join("");
    const axes = categories.map((_, index) => {
      const [x, y] = point(center, center, radius, index, count);
      return `<line class="life-wheel-axis" x1="${center}" y1="${center}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}"/>`;
    }).join("");
    const values = categories.map(item => Number(item.score) || 0);
    const shape = polygonPoints(center, center, radius, values);
    const points = values.map((value, index) => {
      const [x, y] = point(center, center, radius * value / 10, index, count);
      return `<circle class="life-wheel-point" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.4"/>`;
    }).join("");
    const labels = categories.map((item, index) => {
      const [x, y] = point(center, center, radius + 35, index, count);
      const scoreY = y + (y < center ? 12 : -9);
      return `<text class="life-wheel-label" x="${x.toFixed(1)}" y="${y.toFixed(1)}">${item.label}</text>` +
        `<text class="life-wheel-score" x="${x.toFixed(1)}" y="${scoreY.toFixed(1)}">${Number(item.score).toFixed(1)}</text>`;
    }).join("");
    return `<div class="life-wheel-chart-wrap"><svg class="life-wheel-chart" viewBox="0 0 ${size} ${size}" role="img" aria-label="Диаграмма колеса жизни">${grid}${axes}<polygon class="life-wheel-shape" points="${shape}"/>${points}${labels}</svg></div>`;
  }

  function categoryRow(item) {
    const color = googleColorHex[String(item.color_id || "")] || "#a8a8a4";
    const activity = item.all_day_days
      ? `${item.events} событий · ${item.active_days} активных дней · ${item.all_day_days} дней целиком`
      : `${item.events} событий · ${item.active_days} активных дней · ${item.hours} ч`;
    return `<div class="life-wheel-row">
      <div class="life-wheel-row-main"><span class="life-wheel-dot" style="background:${color}"></span><div>
        <div class="life-wheel-row-name">${item.label}</div><div class="life-wheel-row-meta">${activity}</div>
      </div></div><div class="life-wheel-row-score">${Number(item.score).toFixed(1)}</div>
    </div>`;
  }

  function render(data) {
    const categories = Array.isArray(data.categories) ? data.categories : [];
    if (!categories.length || !(data.totals?.events > 0)) {
      content.innerHTML = `<div class="life-wheel-empty">За выбранный период нет категоризированных календарных дел. Когда появятся события, здесь соберётся диаграмма.</div>
        <p class="life-wheel-note">Пока учитываются события календаря. Напоминания подключим после общей категоризации.</p>`;
      return;
    }
    content.innerHTML = chart(categories) +
      `<div class="life-wheel-list">${categories.map(categoryRow).join("")}</div>` +
      `<p class="life-wheel-note">${data.metric_help || ""}<br>Сейчас учитываются события календаря. Напоминания пока не входят в расчёт.</p>`;
  }

  async function load() {
    const version = ++requestVersion;
    content.innerHTML = `<div class="life-wheel-empty">Собираю диаграмму…</div>`;
    try {
      const data = await api(`/api/assistant/life-wheel?days=${days}`);
      if (version !== requestVersion || !overlay.classList.contains("open")) return;
      render(data);
    } catch (error) {
      if (version !== requestVersion) return;
      const message = error?.message === "unauthorized" ? "Нужно войти в аккаунт." : "Не удалось построить диаграмму.";
      content.innerHTML = `<div class="life-wheel-error">${message}<br><button id="lifeWheelRetry" class="life-wheel-retry" type="button">Повторить</button></div>`;
      document.getElementById("lifeWheelRetry")?.addEventListener("click", load);
    }
  }

  function open() {
    settingsPanel.classList.remove("open");
    clearChatIdleTimer();
    overlay.classList.add("open");
    load();
  }

  function close() {
    requestVersion += 1;
    overlay.classList.remove("open");
    armChatIdleTimer();
  }

  button.addEventListener("click", open);
  closeButton.addEventListener("click", close);
  overlay.addEventListener("pointerdown", event => { if (event.target === overlay) close(); });
  document.addEventListener("keydown", event => { if (event.key === "Escape" && overlay.classList.contains("open")) close(); });
  overlay.querySelectorAll(".life-wheel-period").forEach(periodButton => {
    periodButton.addEventListener("click", () => {
      const nextDays = Number(periodButton.dataset.days);
      if (![7, 30, 90].includes(nextDays) || nextDays === days) return;
      days = nextDays;
      overlay.querySelectorAll(".life-wheel-period").forEach(item => item.classList.toggle("active", item === periodButton));
      load();
    });
  });
})();