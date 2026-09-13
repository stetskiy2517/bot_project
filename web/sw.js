const CACHE = "personal-secretary-v6";
const STATIC = ["/", "/manifest.webmanifest", "/icon.svg", "/reminders.js", "/library.js"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(STATIC)));
  self.skipWaiting();
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

  event.waitUntil(
    self.registration.showNotification(title, {
      body,
      tag,
      data: { url, reminderId },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = event.notification.data?.url || "/";
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

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (
    event.request.method !== "GET" ||
    url.pathname.startsWith("/api/") ||
    url.pathname === "/oauth2callback"
  ) {
    return;
  }
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
});
