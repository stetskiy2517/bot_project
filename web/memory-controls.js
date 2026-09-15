(() => {
  "use strict";
  let installed = false;
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
      voice_transcript: "голос/чат", user_correction: "исправлено пользователем",
    };
    return labels[memory.source_type] || memory.source_type || "неизвестный источник";
  }

  function actionButton(label, handler, className = "") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `memory-control-action ${className}`.trim();
    button.textContent = label;
    button.onclick = async () => {
      button.disabled = true;
      try { await handler(); } finally { button.disabled = false; }
    };
    return button;
  }

  async function load() {
    const box = document.getElementById("assistantMemoryList");
    const actionsBox = document.getElementById("assistantProactiveFeedback");
    const state = document.getElementById("assistantMemoryState");
    if (!box || !actionsBox) return;
    box.innerHTML = '<p class="settings-help">Загружаю…</p>';
    actionsBox.replaceChildren();
    try {
      const data = await api("/api/memory/controls");
      box.replaceChildren();
      const memories = data.memories || [];
      if (!memories.length) {
        box.innerHTML = '<p class="settings-help">Секретарь пока ничего устойчивого о тебе не запомнил.</p>';
      }
      for (const memory of memories) {
        const card = document.createElement("div");
        card.className = "memory-control-card";
        const head = document.createElement("div");
        head.className = "memory-control-head";
        head.textContent = `${kindLabels[memory.kind] || memory.kind} · ${Math.round((memory.confidence || 0) * 100)}%`;
        const value = document.createElement("div");
        value.className = "memory-control-value";
        value.textContent = textValue(memory.value);
        const source = document.createElement("div");
        source.className = "memory-control-source";
        source.textContent = `Источник: ${sourceLabel(memory)}${memory.evidence ? ` · ${memory.evidence}` : ""}`;
        const controls = document.createElement("div");
        controls.className = "memory-control-actions";
        controls.append(
          actionButton("Исправить", async () => {
            const next = prompt("Что секретарь должен помнить вместо этого?", textValue(memory.value));
            if (next == null || !next.trim()) return;
            await api(`/api/memory/${memory.memory_id}`, {
              method: "PATCH", body: JSON.stringify({value: next.trim()}),
            });
            await load();
          }),
          actionButton("Забыть", async () => {
            if (!confirm("Удалить этот вывод из активной памяти секретаря?")) return;
            await api(`/api/memory/${memory.memory_id}`, {method: "DELETE"});
            await load();
          }, "danger"),
        );
        card.append(head, value, source, controls);
        box.append(card);
      }

      const actions = (data.actions || []).filter((item) => !item.feedback).slice(0, 8);
      if (!actions.length) {
        actionsBox.innerHTML = '<p class="settings-help">Новых предложений для оценки нет.</p>';
      }
      for (const action of actions) {
        const row = document.createElement("div");
        row.className = "memory-feedback-card";
        const description = document.createElement("div");
        description.className = "memory-control-value";
        description.textContent = action.reason || "Решение проактивного помощника";
        const controls = document.createElement("div");
        controls.className = "memory-control-actions";
        const send = async (feedback) => {
          await api(`/api/proactive/${action.action_id}/feedback`, {
            method: "POST", body: JSON.stringify({feedback}),
          });
          await load();
        };
        controls.append(
          actionButton("Полезно", () => send("useful")),
          actionButton("Не надо", () => send("dismiss")),
          actionButton("Больше не предлагать", () => send("never"), "danger"),
        );
        row.append(description, controls);
        actionsBox.append(row);
      }
      const summary = data.feedback_summary || {};
      state.textContent = `Оценено предложений: ${(summary.useful || 0) + (summary.dismiss || 0) + (summary.never || 0)}.`;
    } catch (error) {
      box.innerHTML = `<p class="settings-help">${String(error.message || error)}</p>`;
    }
  }

  function install() {
    if (installed) return;
    const root = document.getElementById("assistantSettings");
    const settingsPanel = document.getElementById("settingsPanel");
    if (!root || !settingsPanel) return;
    installed = true;
    const section = document.createElement("details");
    section.className = "assistant-section settings-group";
    section.innerHTML = `
      <summary class="settings-group-summary-ready">
        <span class="settings-group-title">Память секретаря</span>
        <span class="settings-group-meta">контроль</span>
      </summary>
      <p class="settings-help">Здесь видно, что секретарь считает устойчивым фактом, откуда это взялось и насколько он уверен. Вывод можно исправить или убрать.</p>
      <div id="assistantMemoryList"></div>
      <h4 class="memory-control-subtitle">Оценка предложений</h4>
      <div id="assistantProactiveFeedback"></div>
      <p id="assistantMemoryState" class="settings-help"></p>`;
    root.prepend(section);
    section.addEventListener("toggle", () => { if (section.open) load(); });
    const style = document.createElement("style");
    style.textContent = `
      .memory-control-card,.memory-feedback-card{padding:12px;margin:9px 0;border:1px solid #e4e4e1;border-radius:14px;background:#fff}
      .memory-control-head{font-size:11px;font-weight:650;color:#898985;text-transform:uppercase;letter-spacing:.02em}
      .memory-control-value{margin-top:6px;font-size:14px;line-height:1.4;white-space:pre-wrap;overflow-wrap:anywhere}
      .memory-control-source{margin-top:6px;color:#8d8d88;font-size:11px;line-height:1.35}
      .memory-control-actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px}
      .memory-control-action{min-height:34px;padding:0 10px;border-radius:10px;background:#efefed;color:#333;font-size:12px;font-weight:600;cursor:pointer}
      .memory-control-action.danger{background:#f2dddd;color:#8a2d2d}
      .memory-control-subtitle{margin:18px 0 7px;font-size:13px}
    `;
    document.head.append(style);
  }

  document.addEventListener("planner-ready", install);
  if (document.readyState !== "loading") setTimeout(install, 0);
})();
