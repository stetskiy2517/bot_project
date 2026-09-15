(() => {
  "use strict";

  if (!("geolocation" in navigator)) return;

  const minRefreshMs = 2 * 60 * 1000;
  const minMoveMeters = 100;
  let navigationEnabled = false;
  let watchId = null;
  let lastSentAt = 0;
  let lastLatitude = null;
  let lastLongitude = null;
  let lastAccuracy = null;
  let permissionStatus = null;
  let permissionLoading = false;
  let promptBound = false;

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
    if (!navigationEnabled) return false;
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
    if (!navigationEnabled || !shouldSend(position)) return;
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
      if (!navigationEnabled) return;
      lastSentAt = sentAt;
      lastLatitude = position.coords.latitude;
      lastLongitude = position.coords.longitude;
      lastAccuracy = Number(position.coords.accuracy || 0) || null;
    } catch (_) {}
  }

  function stopWatching() {
    if (watchId !== null) {
      navigator.geolocation.clearWatch(watchId);
      watchId = null;
    }
  }

  function startWatching() {
    if (!navigationEnabled || watchId !== null || document.visibilityState !== "visible") return;
    watchId = navigator.geolocation.watchPosition(sendPosition, () => {}, {
      enableHighAccuracy: true,
      maximumAge: 60 * 1000,
      timeout: 10000,
    });
  }

  function removePromptStart() {
    if (!promptBound) return;
    document.removeEventListener("pointerdown", promptStart, true);
    promptBound = false;
  }

  function promptStart() {
    removePromptStart();
    if (navigationEnabled) startWatching();
  }

  function bindPromptStart() {
    if (!navigationEnabled || promptBound) return;
    promptBound = true;
    document.addEventListener("pointerdown", promptStart, {once: true, capture: true});
  }

  function handlePermissionState() {
    if (!navigationEnabled) {
      stopWatching();
      removePromptStart();
      return;
    }
    if (!permissionStatus) {
      bindPromptStart();
      return;
    }
    if (permissionStatus.state === "granted") {
      removePromptStart();
      startWatching();
    } else if (permissionStatus.state === "prompt") {
      stopWatching();
      bindPromptStart();
    } else {
      stopWatching();
      removePromptStart();
    }
  }

  async function preparePermission() {
    if (!navigationEnabled || permissionLoading || permissionStatus) {
      handlePermissionState();
      return;
    }
    if (!navigator.permissions?.query) {
      handlePermissionState();
      return;
    }
    permissionLoading = true;
    try {
      permissionStatus = await navigator.permissions.query({name: "geolocation"});
      permissionStatus.addEventListener?.("change", handlePermissionState);
    } catch (_) {
      permissionStatus = null;
    } finally {
      permissionLoading = false;
    }
    handlePermissionState();
  }

  function setNavigationEnabled(enabled) {
    const next = enabled === true;
    navigationEnabled = next;
    document.documentElement.dataset.navigationEnabled = String(next);
    if (!next) {
      stopWatching();
      removePromptStart();
      return;
    }
    preparePermission();
  }

  async function loadNavigationState() {
    try {
      const status = await window.PlannerRequests.request("/api/status");
      setNavigationEnabled(status?.navigation?.enabled === true);
    } catch (_) {
      setNavigationEnabled(false);
    }
  }

  document.addEventListener("planner-navigation-setting", (event) => {
    setNavigationEnabled(event.detail?.enabled === true);
  });

  document.addEventListener("planner-ready", () => {
    const control = document.getElementById("navigationEnabled");
    if (control) setNavigationEnabled(control.value === "true");
  });

  document.getElementById("navigationEnabled")?.addEventListener("change", (event) => {
    if (event.currentTarget.value === "false") setNavigationEnabled(false);
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && navigationEnabled) preparePermission();
    else stopWatching();
  });

  loadNavigationState();
})();
