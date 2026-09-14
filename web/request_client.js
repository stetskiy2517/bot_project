(() => {
  "use strict";
  let security = null;
  let loading = null;
  let voiceDraft = null;
  let memoryDraft = null;
  const PREFIX = "secretary.draft.";
  const TTL = 86400000;

  function publish(name, detail) {
    window.dispatchEvent(new CustomEvent(name, { detail }));
  }

  function acceptStatus(data) {
    if (!data?.user?.id || !data.csrf_token) return;
    security = {
      userId: data.user.id,
      csrf: data.csrf_token,
      offset: Number(data.server_time_ms || Date.now()) - Date.now(),
    };
    publish("secretary:session", { userId: security.userId });
  }

  async function bootstrap() {
    if (security) return security;
    if (!loading) {
      loading = fetch("/api/status", { credentials: "same-origin", cache: "no-store" })
        .then(async (response) => {
          if (!response.ok) {
            const error = Error(response.status === 401 ? "unauthorized" : "Не удалось подключиться к серверу.");
            error.status = response.status;
            throw error;
          }
          acceptStatus(await response.json());
          if (!security) throw Error("Обнови страницу приложения.");
          return security;
        }).finally(() => { loading = null; });
    }
    return loading;
  }

  function newKey() {
    if (!security || !window.crypto?.getRandomValues) throw Error("Не удалось подготовить безопасный запрос.");
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    const random = Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
    return `${Math.round(Date.now() + security.offset)}.${random}`;
  }

  async function request(path, options = {}) {
    const url = new URL(path, location.origin);
    if (url.origin !== location.origin) throw Error("Внешний адрес запроса запрещён.");
    const { requestKey, ...fetchOptions } = options;
    const method = (options.method || "GET").toUpperCase();
    const headers = new Headers(options.headers || {});
    if (["POST", "PUT", "PATCH", "DELETE"].includes(method) || url.pathname === "/api/reminders/due") {
      const active = await bootstrap();
      headers.set("X-CSRF-Token", active.csrf);
      if (method !== "GET") headers.set("Idempotency-Key", requestKey || newKey());
    }
    const response = await fetch(url.pathname + url.search, {
      ...fetchOptions, method, headers, credentials: "same-origin", cache: "no-store",
    });
    if (response.status === 401) {
      security = null;
      voiceDraft = null;
      publish("secretary:session-ended");
    } else if (url.pathname === "/api/status" && response.ok) {
      acceptStatus(await response.clone().json());
    }
    if (response.ok && ["POST", "PATCH", "DELETE", "PUT"].includes(method)) {
      publish("secretary:mutation", {path:url.pathname});
    }
    return response;
  }

  function readDraft() {
    if (!security) return null;
    let stored;
    try { stored = JSON.parse(localStorage.getItem(PREFIX + security.userId) || "null") || memoryDraft; }
    catch (_) { stored = memoryDraft; }
    if (!stored || stored.owner !== security.userId) return null;
    if (Date.now() - stored.savedAt > TTL || typeof stored.key !== "string") {
      discardDraft();
      return null;
    }
    return stored;
  }

  function writeDraft(draft) {
    memoryDraft = draft;
    try { localStorage.setItem(PREFIX + draft.owner, JSON.stringify(draft)); }
    catch (_) {
      publish("secretary:draft-storage-error", { message: "Черновик не удалось сохранить на устройстве." });
    }
    publish("secretary:draft", draft);
    return draft;
  }

  async function recordText(text) {
    const active = await bootstrap();
    if (readDraft()) throw Error("Сначала разбери предыдущий черновик.");
    if (typeof text !== "string" || !text.trim() || text.length > 10000)
      throw Error("Сообщение должно содержать от 1 до 10 000 символов.");
    return writeDraft({
      owner: active.userId, kind: "text", text,
      key: newKey(), savedAt: Date.now(),
    });
  }

  async function recordVoice(blob, durationMs) {
    const active = await bootstrap();
    if (readDraft()) throw Error("Сначала разбери предыдущий черновик.");
    const draft = {
      owner: active.userId, kind: "voice", key: newKey(), savedAt: Date.now(),
      durationMs: Math.round(durationMs),
    };
    voiceDraft = { key: draft.key, owner: active.userId, blob };
    return writeDraft(draft);
  }

  function discardDraft(key = null) {
    if (!security) return;
    const stored = readRawDraft();
    if (key && stored && stored.key !== key) return;
    try { localStorage.removeItem(PREFIX + security.userId); } catch (_) { /* Storage can be unavailable. */ }
    if (!key || memoryDraft?.key === key) memoryDraft = null;
    if (!key || voiceDraft?.key === key) voiceDraft = null;
    publish("secretary:draft", null);
  }

  function readRawDraft() {
    try { return JSON.parse(localStorage.getItem(PREFIX + security?.userId) || "null") || memoryDraft; }
    catch (_) { return memoryDraft; }
  }

  function currentVoice(key) {
    return voiceDraft?.key === key && voiceDraft.owner === security?.userId ? voiceDraft.blob : null;
  }

  async function signOutDevice() {
    try {
      const registration = await navigator.serviceWorker?.getRegistration();
      const subscription = await registration?.pushManager?.getSubscription();
      if (subscription) {
        try {
          await request("/api/push/subscriptions", {
            method: "DELETE", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ endpoint: subscription.endpoint }),
          });
        } finally {
          await subscription.unsubscribe();
        }
      }
    } catch (_) { publish("secretary:push-unsubscribe-failed"); }
    discardDraft();
    try {
      for (const key of Object.keys(localStorage)) {
        if (key.startsWith(PREFIX)) localStorage.removeItem(key);
      }
    } catch (_) { /* The in-memory audio has already been cleared. */ }
    voiceDraft = null;
    memoryDraft = null;
  }

  window.addEventListener("storage", (event) => {
    if (event.key !== PREFIX + security?.userId) return;
    memoryDraft = null;
    let current = null;
    try { current = event.newValue ? JSON.parse(event.newValue) : null; } catch (_) {}
    if (!current || current.key !== voiceDraft?.key) voiceDraft = null;
  });

  window.SecretaryRequests = {
    request, bootstrap, newKey, recordText, recordVoice, readDraft,
    discardDraft, currentVoice, signOutDevice,
  };
})();
