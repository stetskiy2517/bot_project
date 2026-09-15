(() => {
  "use strict";
  let installed = false;

  function api(path, options) {
    if (typeof window.api !== "function") throw new Error("API недоступен");
    return window.api(path, options);
  }

  async function load() {
    const state = document.getElementById("navigationExtraState");
    try {
      const data = await api("/api/navigation/buffers");
      document.getElementById("parkingBufferMinutes").value = data.parking_buffer_minutes ?? 0;
      document.getElementById("walkingBufferMinutes").value = data.walking_buffer_minutes ?? 0;
      state.textContent = `Итоговый запас до события: ${data.arrival_buffer_minutes || 0} мин.`;
    } catch (error) {
      state.textContent = error.message;
    }
  }

  async function save() {
    const parking = Number(document.getElementById("parkingBufferMinutes").value);
    const walking = Number(document.getElementById("walkingBufferMinutes").value);
    const data = await api("/api/navigation/buffers", {
      method: "PUT",
      body: JSON.stringify({parking_buffer_minutes: parking, walking_buffer_minutes: walking}),
    });
    document.getElementById("navigationExtraState").textContent =
      `Сохранено. Итоговый запас: ${data.preferences.arrival_buffer_minutes || 0} мин.`;
  }

  async function openNextRoute() {
    const data = await api("/api/navigation/next-route");
    window.open(data.url, "_blank", "noopener,noreferrer");
    document.getElementById("navigationExtraState").textContent = `Маршрут: ${data.title} → ${data.destination}`;
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
        <span class="settings-group-title">Дорога до события</span>
        <span class="settings-group-meta">навигация</span>
      </summary>
      <p class="settings-help">Общий запас из основных настроек сохраняется. Дополнительно можно учесть парковку и время от парковки/остановки до двери.</p>
      <div class="grid">
        <label class="field">Парковка, минут<input id="parkingBufferMinutes" type="number" min="0" max="180" step="5" value="0"></label>
        <label class="field">Дойти до места, минут<input id="walkingBufferMinutes" type="number" min="0" max="180" step="5" value="0"></label>
      </div>
      <div class="assistant-actions">
        <button id="saveNavigationExtra" class="action" type="button">Сохранить</button>
        <button id="openNextRoute" class="action" type="button">Открыть маршрут к следующей встрече</button>
      </div>
      <p id="navigationExtraState" class="settings-help" role="status"></p>`;
    root.append(section);
    section.addEventListener("toggle", () => { if (section.open) load(); });
    section.querySelector("#saveNavigationExtra").onclick = () => save().catch((error) => {
      document.getElementById("navigationExtraState").textContent = error.message;
    });
    section.querySelector("#openNextRoute").onclick = () => openNextRoute().catch((error) => {
      document.getElementById("navigationExtraState").textContent = error.message;
    });
  }

  document.addEventListener("planner-ready", install);
  if (document.readyState !== "loading") setTimeout(install, 0);
})();
