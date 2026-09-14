(() => {
  "use strict";

  if (!("geolocation" in navigator)) return;

  const minRefreshMs = 2 * 60 * 1000;
  const minMoveMeters = 100;
  let watchId = null;
  let lastSentAt = 0;
  let lastLatitude = null;
  let lastLongitude = null;
  let lastAccuracy = null;

  function distanceMeters(lat1, lon1, lat2, lon2) {
    const toRad = (value) => value * Math.PI / 180;
    const earthRadius = 6371000;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
    return 2 * earthRadius * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  function shouldSend(position) {
    if (lastLatitude === null || lastLongitude === null) return true;
    const elapsed = Date.now() - lastSentAt;
    const moved = distanceMeters(
      lastLatitude,
      lastLongitude,
      position.coords.latitude,
      position.coords.longitude,
    );
    const accuracy = Number(position.coords.accuracy || 0);
    const accuracyImproved =
      lastAccuracy !== null && accuracy > 0 && accuracy < Math.max(30, lastAccuracy * 0.6);
    return elapsed >= minRefreshMs || moved >= minMoveMeters || accuracyImproved;
  }

  async function sendPosition(position) {
    if (!shouldSend(position)) return;
    const sentAt = Date.now();
    try {
      await window.PlannerRequests.request("/api/location", {
        method: "POST",
        body: JSON.stringify({
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracy: position.coords.accuracy,
        }),
      });
      lastSentAt = sentAt;
      lastLatitude = position.coords.latitude;
      lastLongitude = position.coords.longitude;
      lastAccuracy = Number(position.coords.accuracy || 0) || null;
    } catch (_) {}
  }

  function stopWatching() {
    if (watchId === null) return;
    navigator.geolocation.clearWatch(watchId);
    watchId = null;
  }

  function startWatching() {
    if (watchId !== null || document.visibilityState !== "visible") return;
    watchId = navigator.geolocation.watchPosition(sendPosition, () => {}, {
      enableHighAccuracy: true,
      maximumAge: 60 * 1000,
      timeout: 10000,
    });
  }

  async function start() {
    if (!navigator.permissions?.query) {
      document.addEventListener("pointerdown", startWatching, {once: true});
      return;
    }
    try {
      const permission = await navigator.permissions.query({name: "geolocation"});
      if (permission.state === "granted") startWatching();
      else if (permission.state === "prompt")
        document.addEventListener("pointerdown", startWatching, {once: true});
      permission.addEventListener?.("change", () => {
        if (permission.state === "granted") startWatching();
        else stopWatching();
      });
    } catch (_) {}
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") startWatching();
    else stopWatching();
  });

  start();
})();
