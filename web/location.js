(() => {
  "use strict";

  if (!("geolocation" in navigator)) return;

  let lastSentAt = 0;
  const minRefreshMs = 5 * 60 * 1000;

  async function sendPosition(position) {
    const now = Date.now();
    if (now - lastSentAt < minRefreshMs) return;
    lastSentAt = now;
    try {
      await fetch("/api/location", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        credentials: "same-origin",
        body: JSON.stringify({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracy: position.coords.accuracy,
        }),
      });
    } catch (_) {
      lastSentAt = 0;
    }
  }

  function refreshLocation() {
    navigator.geolocation.getCurrentPosition(sendPosition, () => {}, {
      enableHighAccuracy: false,
      maximumAge: 5 * 60 * 1000,
      timeout: 7000,
    });
  }

  async function start() {
    if (!navigator.permissions?.query) {
      document.addEventListener("pointerdown", refreshLocation, {once: true});
      return;
    }
    try {
      const permission = await navigator.permissions.query({name: "geolocation"});
      if (permission.state === "granted") refreshLocation();
      else if (permission.state === "prompt") document.addEventListener("pointerdown", refreshLocation, {once: true});
      permission.addEventListener?.("change", () => {
        if (permission.state === "granted") refreshLocation();
      });
    } catch (_) {}
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refreshLocation();
  });
  start();
})();
