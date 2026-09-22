(() => {
  const REMINDER_POLL_MS = 15000;
  const CHAT_CLEAR_DELAY_MS = 400;
  const PUSH_SELF_TEST_KEY = "personal-secretary-push-self-test-v2";

  let reminderPollBusy = false;
  let reminderPollController = null;
  let reminderPageActive = true;
  let chatClearTimer = null;
  let pushConfig = null;
  let pushManager = null;
  let pushSubscription = null;
  let pushSetupReady = false;
  let pushSetupError = null;
  let reminderEnablePromptShown = false;

  const isIos =
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const isStandalone =
    window.matchMedia?.("(display-mode: standalone)").matches ||
    window.navigator.standalone === true;
  const notificationsSupported = "Notification" in window;
  const serviceWorkerSupported = "serviceWorker" in navigator;
  const declarativePushSupported = Boolean(window.pushManager?.subscribe);
  const classicPushSupported = serviceWorkerSupported && "PushManager" in window;
  const pushSupported =
    notificationsSupported && (declarativePushSupported || classicPushSupported);

  function installTransientChatCleanup() {
    const appNode = document.getElementById("app");
    const chatNode = document.getElementById("chat");
    if (!appNode || !chatNode || !window.MutationObserver) return;

    let wasActive = appNode.classList.contains("chat-active");
    const observer = new MutationObserver(() => {
      const isActive = appNode.classList.contains("chat-active");
      if (wasActive && !isActive) {
        if (chatClearTimer) window.clearTimeout(chatClearTimer);
        chatClearTimer = window.setTimeout(() => {
          if (!appNode.classList.contains("chat-active")) chatNode.replaceChildren();
          chatClearTimer = null;
        }, CHAT_CLEAR_DELAY_MS);
      } else if (!wasActive && isActive && chatClearTimer) {
        window.clearTimeout(chatClearTimer);
        chatClearTimer = null;
      }
      wasActive = isActive;
    });
    observer.observe(appNode, { attributes: true, attributeFilter: ["class"] });
  }

  function base64UrlToUint8Array(value) {
    const padding = "=".repeat((4 - (value.length % 4)) % 4);
    const base64 = (value + padding).replace(/-/g, "+").replace(/_/g, "/");
    const raw = atob(base64);
    return Uint8Array.from(raw, (char) => char.charCodeAt(0));
  }

  function setPushStatus(value) {
    const status = document.getElementById("pushNotificationStatus");
    if (status) status.textContent = value;
  }

  function readablePushError(error) {
    const name = String(error?.name || "");
    const message = String(error?.message || "").trim();
    if (name === "NotAllowedError") {
      if (Notification.permission === "granted") {
        return "Разрешение есть, но браузер не создал push-подписку. Нажми «Подключить push» ещё раз.";
      }
      return "Браузер не дал создать push-подписку. Разреши уведомления и повтори.";
    }
    if (name === "InvalidStateError") {
      return "Старая push-подписка несовместима. Перезапусти приложение и подключи уведомления снова.";
    }
    if (name === "AbortError") {
      return "Сервис push временно недоступен. Повтори подключение через несколько секунд.";
    }
    return message || name || "Не удалось подключить push-уведомления.";
  }

  async function syncSubscription(subscription) {
    if (!subscription) return;
    await api("/api/push/subscriptions", {
      method: "POST",
      body: JSON.stringify(subscription.toJSON()),
    });
  }

  async function removeSubscriptionFromServer(subscription) {
    if (!subscription) return;
    try {
      await api("/api/push/subscriptions", {
        method: "DELETE",
        body: JSON.stringify({ endpoint: subscription.endpoint }),
      });
    } catch (error) {
      if (error.message !== "unauthorized")
        console.warn("Could not remove Push subscription from server", error);
    }
  }

  async function preparePushEnvironment() {
    if (!pushSupported || (isIos && !isStandalone)) {
      pushSetupReady = true;
      updatePushUi();
      return;
    }

    setPushStatus("Готовлю push-уведомления…");
    try {
      // Fetch everything that may require network access BEFORE the user taps the
      // subscribe button. Safari requires pushManager.subscribe() to be called
      // directly from the user gesture; awaiting network/Service Worker setup in
      // the click handler loses that gesture and leaves permission granted but
      // the PushSubscription missing.
      pushConfig = await api("/api/push/config");

      let registration = null;
      if (serviceWorkerSupported) {
        registration = await navigator.serviceWorker.register("/sw.js");
      }

      if (declarativePushSupported) {
        pushManager = window.pushManager;
      } else {
        registration = registration || (await navigator.serviceWorker.ready);
        if (!registration.active) registration = await navigator.serviceWorker.ready;
        pushManager = registration.pushManager;
      }

      pushSubscription = await pushManager.getSubscription();
      if (pushSubscription) await syncSubscription(pushSubscription);
      pushSetupError = null;
    } catch (error) {
      pushSetupError = readablePushError(error);
      console.warn("Push preparation failed", error);
    } finally {
      pushSetupReady = true;
      updatePushUi();
    }
  }

  function pushStatusText() {
    if (!pushSupported) return "Этот браузер не поддерживает push-уведомления.";
    if (isIos && !isStandalone)
      return "На iPhone добавь сайт на экран «Домой» и открой его как приложение.";
    if (Notification.permission === "denied")
      return "Уведомления запрещены в настройках устройства или браузера.";
    if (pushSetupError) return `Ошибка push: ${pushSetupError}`;
    if (!pushSetupReady) return "Готовлю push-уведомления…";
    if (pushSubscription) return "Включены на этом устройстве.";
    if (Notification.permission === "granted")
      return "Разрешение выдано. Осталось подключить push на этом устройстве.";
    return "Выключены на этом устройстве.";
  }

  async function runPushTest({ announce = true } = {}) {
    if (!pushSubscription) throw Error("Push-подписка не создана.");
    setPushStatus("Проверяю доставку push…");
    try {
      const result = await api("/api/push/test", { method: "POST" });
      setPushStatus("Тестовый push отправлен. Он должен появиться на экране сейчас.");
      if (announce) {
        showChat();
        msg("Тестовый push отправлен. Если системного уведомления нет — напиши мне.", "assistant");
        armChatIdleTimer();
      }
      try {
        localStorage.setItem(PUSH_SELF_TEST_KEY, "ok");
      } catch (_error) {}
      return result;
    } catch (error) {
      pushSetupError = error.message;
      setPushStatus(`Ошибка push: ${error.message}`);
      if (announce) {
        showChat();
        msg(`Push не прошёл: ${error.message}`, "assistant");
        armChatIdleTimer();
      }
      throw error;
    }
  }

  function ensurePushSettingsUi() {
    if (document.getElementById("pushNotificationSettings")) return;
    const actions = document.querySelector("#settingsPanel .sheet-actions");
    if (!actions) return;

    const group = document.createElement("details");
    group.id = "notificationsGroup";
    group.className = "settings-group";

    const summary = document.createElement("summary");
    summary.innerHTML = `
      <span class="settings-group-title">На устройстве</span>
      <span id="notificationsMeta" class="settings-group-meta"></span>
      <svg class="settings-group-chevron" viewBox="0 0 24 24" aria-hidden="true">
        <path d="m6 9 6 6 6-6"></path>
      </svg>`;
    group.appendChild(summary);

    const grid = document.createElement("div");
    grid.id = "pushNotificationSettings";
    grid.className = "grid";

    const field = document.createElement("div");
    field.className = "field";

    const status = document.createElement("span");
    status.id = "pushNotificationStatus";

    const button = document.createElement("button");
    button.id = "pushNotificationToggle";
    button.className = "action primary";
    button.type = "button";
    button.style.width = "100%";

    const testButton = document.createElement("button");
    testButton.id = "pushNotificationTest";
    testButton.className = "action";
    testButton.type = "button";
    testButton.style.width = "100%";
    testButton.style.marginTop = "8px";
    testButton.textContent = "Проверить уведомления";

    button.addEventListener("click", () => {
      if (button.disabled) return;
      button.disabled = true;
      pushSetupError = null;

      if (pushSubscription) {
        disablePushNotifications()
          .catch((error) => {
            pushSetupError = readablePushError(error);
          })
          .finally(updatePushUi);
        return;
      }

      if (!pushManager || !pushConfig) {
        pushSetupError = "Push ещё не готов. Закрой настройки, открой снова и повтори.";
        updatePushUi();
        return;
      }

      // IMPORTANT: do not await Notification.requestPermission(), fetch(), or
      // navigator.serviceWorker.ready here. Safari requires subscribe() itself to
      // happen in this click task. subscribe() will show the permission prompt.
      let subscribePromise;
      try {
        subscribePromise = pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: base64UrlToUint8Array(pushConfig.public_key),
        });
        setPushStatus("Подключаю push…");
      } catch (error) {
        pushSetupError = readablePushError(error);
        updatePushUi();
        return;
      }

      Promise.resolve(subscribePromise)
        .then(async (subscription) => {
          await syncSubscription(subscription);
          pushSubscription = subscription;
          pushSetupError = null;
          updatePushUi();
          try {
            await runPushTest({ announce: false });
          } catch (_error) {}
        })
        .catch((error) => {
          pushSetupError = readablePushError(error);
          console.warn("Push subscribe failed", error);
          updatePushUi();
        });
    });

    testButton.addEventListener("click", async () => {
      testButton.disabled = true;
      try {
        await runPushTest({ announce: true });
      } catch (_error) {
      } finally {
        testButton.disabled = false;
      }
    });

    field.append(status, button, testButton);
    grid.appendChild(field);
    group.appendChild(grid);
    actions.parentNode.insertBefore(group, actions);
    updatePushUi();
  }

  function updatePushUi() {
    const status = document.getElementById("pushNotificationStatus");
    const button = document.getElementById("pushNotificationToggle");
    const testButton = document.getElementById("pushNotificationTest");
    if (!status || !button) return;

    status.textContent = pushStatusText();
    if (testButton) testButton.hidden = !pushSubscription;

    if (!pushSupported) {
      button.textContent = "Недоступно";
      button.disabled = true;
      return;
    }
    if (isIos && !isStandalone) {
      button.textContent = "Добавь на экран Домой";
      button.disabled = true;
      return;
    }
    if (Notification.permission === "denied") {
      button.textContent = "Разреши в настройках устройства";
      button.disabled = true;
      return;
    }
    if (!pushSetupReady) {
      button.textContent = "Готовлю…";
      button.disabled = true;
      return;
    }
    if (!pushManager || !pushConfig) {
      button.textContent = "Повторить подготовку";
      button.disabled = false;
      return;
    }
    button.textContent = pushSubscription
      ? "Отключить уведомления"
      : Notification.permission === "granted"
        ? "Подключить push"
        : "Включить уведомления";
    button.disabled = false;
  }

  async function disablePushNotifications() {
    const subscription = pushSubscription;
    if (!subscription) return;
    await removeSubscriptionFromServer(subscription);
    try {
      await subscription.unsubscribe();
    } catch (_error) {}
    pushSubscription = null;
    try {
      localStorage.removeItem(PUSH_SELF_TEST_KEY);
    } catch (_error) {}
  }

  function reminderPushActionUrls(reminderId) {
    const id = Number(reminderId);
    const complete = new URL(`/?push_action=complete&reminder_id=${id}`, window.location.origin).href;
    const reschedule = new URL(`/?push_action=reschedule&reminder_id=${id}`, window.location.origin).href;
    return { complete, reschedule };
  }

  function clearPushActionUrl() {
    const url = new URL(window.location.href);
    url.searchParams.delete("push_action");
    url.searchParams.delete("reminder_id");
    const query = url.searchParams.toString();
    window.history.replaceState({}, "", `${url.pathname}${query ? `?${query}` : ""}${url.hash}`);
  }

  function showPushActionFeedback(message) {
    const toast = document.querySelector(".library-toast");
    if (toast) {
      toast.textContent = message;
      toast.classList.add("show");
      window.setTimeout(() => toast.classList.remove("show"), 1800);
      return;
    }
    showChat();
    msg(message, "assistant");
    armChatIdleTimer();
  }

  function wait(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  async function openReminderSnoozeFromPush(reminderId) {
    const deadline = Date.now() + 5000;
    let libraryOpenButton = null;
    let remindersTab = null;

    while (Date.now() < deadline) {
      libraryOpenButton = document.getElementById("libraryOpenBtn");
      remindersTab = document.getElementById("libraryRemindersTab");
      if (libraryOpenButton && remindersTab) break;
      await wait(80);
    }
    if (!libraryOpenButton || !remindersTab) throw new Error("library_not_ready");

    const appNode = document.getElementById("app");
    if (!appNode?.classList.contains("library-active")) libraryOpenButton.click();
    remindersTab.click();

    while (Date.now() < deadline) {
      const row = document.querySelector(`.reminder-swipe-row[data-id="${Number(reminderId)}"]`);
      const action = row?.querySelector('[data-action="reschedule"]');
      if (action) {
        action.click();
        clearPushActionUrl();
        return;
      }
      await wait(100);
    }
    throw new Error("reminder_not_found");
  }

  async function handlePushActionFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const action = params.get("push_action");
    const reminderId = Number(params.get("reminder_id"));
    if (!Number.isInteger(reminderId) || reminderId <= 0) return;
    if (action !== "complete" && action !== "reschedule") return;

    try {
      if (action === "complete") {
        const payload = await api(`/api/library/reminders/${reminderId}/complete`, { method: "POST" });
        clearPushActionUrl();
        showPushActionFeedback(
          payload?.reminder?.text
            ? `Выполнено · ${payload.reminder.text}`
            : "Напоминание отмечено выполненным",
        );
        return;
      }
      await openReminderSnoozeFromPush(reminderId);
    } catch (error) {
      if (error.message !== "unauthorized") {
        console.warn("Push reminder action failed", error);
        clearPushActionUrl();
        showPushActionFeedback("Не удалось выполнить действие с напоминанием");
      }
    }
  }

  async function showSystemNotification(reminder) {
    if (!notificationsSupported || Notification.permission !== "granted") return;
    try {
      const registration = await navigator.serviceWorker.ready;
      const actionUrls = reminderPushActionUrls(reminder.id);
      await registration.showNotification("Напоминание", {
        body: reminder.text,
        icon: "/icon.svg",
        tag: `reminder-${reminder.id}`,
        navigate: new URL("/", window.location.origin).href,
        data: { url: "/", reminderId: reminder.id, actionUrls },
        actions: [
          { action: "complete", title: "Выполнено", navigate: actionUrls.complete },
          { action: "reschedule", title: "Отложить", navigate: actionUrls.reschedule },
        ],
      });
    } catch (error) {
      console.warn("Local reminder notification failed", error);
    }
  }

  async function pollDueReminders() {
    if (reminderPollBusy || !reminderPageActive || document.hidden) return;
    reminderPollBusy = true;
    const controller = new AbortController();
    reminderPollController = controller;
    try {
      const result = await api("/api/reminders/due", { signal: controller.signal });
      if (!reminderPageActive || document.hidden) return;
      const reminders = result.reminders || [];
      if (!reminders.length) return;
      showChat();
      for (const reminder of reminders) {
        msg(reminder.message || `Напоминание · ${reminder.text}`, "assistant");
        await showSystemNotification(reminder);
      }
      armChatIdleTimer();
    } catch (error) {
      if (error?.name !== "AbortError" && error.message !== "unauthorized")
        console.warn("Reminder poll failed", error);
    } finally {
      if (reminderPollController === controller) reminderPollController = null;
      reminderPollBusy = false;
    }
  }

  function appendEnablePushPrompt() {
    if (reminderEnablePromptShown || pushSubscription) return;
    reminderEnablePromptShown = true;
    const chatNode = document.getElementById("chat");
    if (!chatNode) return;

    const item = document.createElement("div");
    item.className = "msg assistant";
    const text = document.createElement("div");

    if (isIos && !isStandalone) {
      text.textContent =
        "Чтобы напоминание пришло на экран iPhone, добавь приложение на экран «Домой», открой его и включи уведомления.";
      item.appendChild(text);
    } else if (!pushSupported) {
      text.textContent = "Этот браузер не умеет получать push-уведомления.";
      item.appendChild(text);
    } else if (Notification.permission === "denied") {
      text.textContent = "Уведомления запрещены. Разреши их в настройках устройства или браузера.";
      item.appendChild(text);
    } else {
      text.textContent = "Чтобы напоминание пришло на экран, включи уведомления в настройках приложения.";
      item.appendChild(text);
    }

    chatNode.appendChild(item);
    chatNode.scrollTop = chatNode.scrollHeight;
  }

  function watchReminderCreation() {
    const chatNode = document.getElementById("chat");
    if (!chatNode || !window.MutationObserver) return;
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        for (const node of mutation.addedNodes) {
          if (!(node instanceof HTMLElement)) continue;
          const value = (node.textContent || "").trim();
          if (node.classList.contains("assistant") && /^Напоминание · «/.test(value)) {
            window.setTimeout(appendEnablePushPrompt, 0);
            return;
          }
        }
      }
    });
    observer.observe(chatNode, { childList: true });
  }

  function wrapLogout() {
    const logout = document.getElementById("logout");
    if (!logout || typeof logout.onclick !== "function") return;
    const original = logout.onclick;
    logout.onclick = async function (event) {
      if (event) event.preventDefault();
      const subscription = pushSubscription;
      if (subscription) {
        await removeSubscriptionFromServer(subscription);
        try {
          await subscription.unsubscribe();
        } catch (_error) {}
        pushSubscription = null;
      }
      return original.call(this, event);
    };
  }

  async function maybeRunOneTimeSelfTest() {
    if (!pushSubscription || Notification.permission !== "granted") return;
    let done = false;
    try {
      done = localStorage.getItem(PUSH_SELF_TEST_KEY) === "ok";
    } catch (_error) {}
    if (done) return;
    try {
      await runPushTest({ announce: false });
    } catch (_error) {}
  }

  window.addEventListener("pagehide", () => {
    reminderPageActive = false;
    reminderPollController?.abort();
    reminderPollController = null;
  });
  window.addEventListener("pageshow", () => {
    reminderPageActive = true;
    window.setTimeout(pollDueReminders, 0);
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      reminderPollController?.abort();
      reminderPollController = null;
      return;
    }
    window.setTimeout(pollDueReminders, 0);
  });

  installTransientChatCleanup();
  ensurePushSettingsUi();
  watchReminderCreation();
  wrapLogout();
  preparePushEnvironment().then(() => {
    if (pushSubscription) window.setTimeout(maybeRunOneTimeSelfTest, 500);
  });
  window.setInterval(pollDueReminders, REMINDER_POLL_MS);
  window.setTimeout(pollDueReminders, 1500);
  window.setTimeout(handlePushActionFromUrl, 350);
})();
