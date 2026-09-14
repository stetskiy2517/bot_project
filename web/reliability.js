(() => {
  let csrf = "";
  let userId = null;
  let offset = 0;
  let statusPromise = null;
  const PREFIX = "secretary-draft:";
  const MAX_AGE = 24 * 60 * 60 * 1000;
  let inMemoryDraft = null;

  function rememberStatus(data) {
    if (!data || !data.user) return;
    csrf = data.csrf_token || csrf;
    if (Number.isFinite(data.server_time_ms)) offset = data.server_time_ms - Date.now();
    if (userId !== null && userId !== data.user.id) inMemoryDraft = null;
    userId = data.user.id;
  }

  function newId() {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return `${Math.trunc(Date.now() + offset)}-${Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("")}`;
  }

  async function status() {
    if (!statusPromise) {
      statusPromise = fetch("/api/status", {cache: "no-store", credentials: "same-origin"})
        .then(async response => {
          const data = await response.json();
          if (!response.ok) throw Object.assign(Error(data.error || "request_failed"), {status: response.status, data});
          rememberStatus(data);
          return data;
        }).finally(() => {statusPromise = null;});
    }
    return statusPromise;
  }

  async function request(path, options = {}) {
    const {requestId, ...init} = options;
    const writes = !["GET", "HEAD", "OPTIONS"].includes((init.method || "GET").toUpperCase());
    if (writes && !csrf) await status();
    if (writes && init.body === undefined) init.body = "{}";
    const headers = {...(init.headers || {})};
    if (!(init.body instanceof FormData)) headers["Content-Type"] ||= "application/json";
    if (writes) {
      headers["X-CSRF-Token"] = csrf;
      headers["X-Request-ID"] = requestId || newId();
    }
    const response = await fetch(path, {...init, headers, credentials: "same-origin", cache: "no-store"});
    const data = await response.json().catch(() => ({}));
    if (path === "/api/status" && response.ok) rememberStatus(data);
    if (!response.ok) {
      if (response.status === 401) csrf = "";
      throw Object.assign(Error(data.message || data.error || "request_failed"), {status: response.status, data});
    }
    return data;
  }

  function draft() {
    if (userId === null) return null;
    let value = inMemoryDraft;
    try {
      const stored = localStorage.getItem(PREFIX + userId);
      if (stored) value = JSON.parse(stored);
    } catch (_) { /* Storage may be unavailable in private browsing. */ }
    if (!value || typeof value.text !== "string" || value.text.length > 10000 ||
        !Number.isFinite(value.updatedAt) || Date.now() - value.updatedAt > MAX_AGE ||
        value.userId !== userId) return null;
    return value;
  }

  function saveDraft(text, id = null, pending = false, context = null) {
    if (userId === null || text.length > 10000) return;
    const value = {text, id, pending, context, userId, updatedAt: Date.now()};
    inMemoryDraft = value;
    try {localStorage.setItem(PREFIX + userId, JSON.stringify(value));} catch (_) {}
  }

  function clearDraft() {
    inMemoryDraft = null;
    try {if (userId !== null) localStorage.removeItem(PREFIX + userId);} catch (_) {}
  }

  function clearAll() {
    clearDraft();
    try {
      for (const key of Object.keys(localStorage)) if (key.startsWith(PREFIX)) localStorage.removeItem(key);
    } catch (_) {}
    userId = null;
    csrf = "";
  }
  window.PlannerRequests = {request, status, newId, draft, saveDraft, clearDraft, clearAll};
})();
