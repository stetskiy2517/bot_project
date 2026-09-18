const CACHE = "personal-secretary-v17-navigation-promoted";
const STATIC = [
  "/",
  "/manifest.webmanifest",
  "/icon.svg",
  "/reliability.js",
  "/reminders.js",
  "/library.js",
  "/task-editor.js",
  "/tasks.js",
  "/tasks-unified.js",
  "/task-swipe.js",
  "/voice-gesture.js",
  "/location.js",
  "/assistant.js",
  "/life-wheel.js",
  "/proactive.js",
  "/mobile-ui.css",
  "/mobile-ui-overlays.css",
  "/attention-center.css",
  "/mobile-ui.js",
  "/mobile-ui-fixes.js",
  "/swipe-navigation.js",
  "/event-editor.js",
  "/file-ingest.js",
  "/attention-center.js",
  "/reminder-editor.js",
  "/settings-themes.js",
  "/prebeta-polish.js",
  "/ux-polish.js",
];

async function refreshStaticCache() {
  const cache = await caches.open(CACHE);
  await Promise.all(
    STATIC.map(async (path) => {
      try {
        const response = await fetch(path, { cache: "reload" });
        if (response.ok) await cache.put(path, response);
      } catch (_error) {
        // One optional asset must not prevent the new worker from installing.
      }
    }),
  );
}

self.addEventListener("install", (event) => {
  event.waitUntil(refreshStaticCache());
});

self.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))),
    ),
  );
  self.clients.claim();
});

self.addEventListener("push", (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (_error) {
    payload = { body: event.data ? event.data.text() : "" };
  }

  // New Apple platforms can display this payload declaratively even if the
  // Service Worker cannot run. Browsers without Declarative Web Push still
  // receive the same JSON here and display it imperatively.
  const notification =
    payload && payload.web_push === 8030 && payload.notification
      ? payload.notification
      : payload;
  const notificationData = notification.data || {};
  const title = notification.title || "Напоминание";
  const body = notification.body || "У тебя есть напоминание.";
  const tag = notification.tag || `reminder-${Date.now()}`;
  const url = notification.navigate || notificationData.url || payload.url || "/";
  const reminderId = notificationData.reminder_id || payload.reminder_id || null;
  const actionUrls = notificationData.action_urls || payload.action_urls || {};
  const actions = Array.isArray(notification.actions)
    ? notification.actions.slice(0, 2).map((item) => ({
        action: String(item.action || ""),
        title: String(item.title || ""),
        ...(item.navigate ? { navigate: item.navigate } : {}),
      }))
    : [];

  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      tag,
      data: { url, reminderId, actionUrls },
      ...(actions.length ? { actions } : {}),
    }),
  );
});

function notificationTargetUrl(notification, action) {
  const data = notification?.data || {};
  const actionUrls = data.actionUrls || data.action_urls || {};
  const target = action && actionUrls[action] ? actionUrls[action] : data.url || "/";
  try {
    const url = new URL(target, self.location.origin);
    return url.origin === self.location.origin ? url.href : self.location.origin + "/";
  } catch (_error) {
    return new URL("/", self.location.origin).href;
  }
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = notificationTargetUrl(event.notification, event.action || "");
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client) {
          if ("navigate" in client) client.navigate(targetUrl).catch(() => {});
          return client.focus();
        }
      }
      return self.clients.openWindow ? self.clients.openWindow(targetUrl) : undefined;
    }),
  );
});

async function networkFirst(request) {
  const cache = await caches.open(CACHE);
  try {
    const response = await fetch(request, {cache: "no-store"});
    if (response.ok) {
      cache.put(request, response.clone()).catch(() => {});
    }
    return response;
  } catch (error) {
    const cached = await cache.match(request, { ignoreSearch: true });
    if (cached) return cached;
    if (request.mode === "navigate") {
      const shell = await cache.match("/");
      if (shell) return shell;
    }
    throw error;
  }
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (
    event.request.method !== "GET" ||
    url.origin !== self.location.origin ||
    url.pathname.startsWith("/api/") ||
    url.pathname === "/oauth2callback"
  ) {
    return;
  }
  event.respondWith(networkFirst(event.request));
});
