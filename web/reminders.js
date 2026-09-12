(() => {
  const REMINDER_POLL_MS = 15000;
  const CHAT_CLEAR_DELAY_MS = 400;
  const PUSH_SELF_TEST_KEY = "personal-secretary-push-self-test-v1";
  let reminderPollBusy = false;
  let chatClearTimer = null;
  let pushConfig = null;
  let pushSubscription = null;
  let reminderEnablePromptShown = false;

  const isIos =
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const isStandalone =
    window.matchMedia?.("(display-mode: standalone)").matches ||
    window.navigator.standalone === true;
  const pushSupported =
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window;

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

  async function getRegistration() {
    return navigator.serviceWorker.ready;
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

  function setPushStatus(value) {
    const status = document.getElementById("pushNotificationStatus");
    if (status) status.textContent = value;
  }

  function pushStatusText() {
    if (!pushSupported) return "Этот браузер не поддерживает push-уведомления.";
    if (isIos && !isStandalone)
      return "На iPhone сначала добавь сайт на экран «Домой» и открой его как приложение.";
    if (Notification.permission === "denied")
      return "Уведомления запрещены в настройках устройства или браузера.";
    if (pushSubscription) return "Включены на этом устройстве.";
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
        msg("Тестовый push отправлен. Если системного уведомления нет — напиши мне, разберём следующий уровень.", "assistant");
        armChatIdleTimer();
      }
      try {
        localStorage.setItem(PUSH_SELF_TEST_KEY, "ok");
      } catch (_error) {}
      return result;
    } catch (error) {
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

    const title = document.createElement("div");
    title.className = "section-title";
    title.textContent = "Уведомления";

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
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        if (pushSubscription) await disablePushNotifications();
        else await enablePushNotifications(true);
      } catch (error) {
        setPushStatus(`Ошибка push: ${error.message}`);
      } finally {
        updatePushUi();
      }
    });

    const testButton = document.createElement("button");
    testButton.id = "pushNotificationTest";
    testButton.className = "action";
    testButton.type = "button";
    testButton.style.width = "100%";
    testButton.style.marginTop = "8px";
    testButton.textContent = "Проверить уведомления";
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
    actions.parentNode.insertBefore(title, actions);
    actions.parentNode.insertBefore(grid, actions);
    updatePushUi();
  }

  function updatePushUi() {
    const status = document.getElementById("pushNotificationStatus");
    const button = document.getElementById("pushNotificationToggle");
    const testButton = document.getElementById("pushNotificationTest");
    if (!status || !button) return;

    if (!status.textContent.startsWith("Ошибка push:") && !status.textContent.startsWith("Тестовый push"))
      status.textContent = pushStatusText();
    button.disabled = false;
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
    button.textContent = pushSubscription
      ? "Отключить уведомления"
      : "Включить уведомления";
  }

  async function enablePushNotifications(fromUserGesture = false) {
    if (!pushSupported) throw Error("push_unsupported");
    if (isIos && !isStandalone) {
      updatePushUi();
      return false;
    }

    if (Notification.permission !== "granted") {
      if (!fromUserGesture) return false;
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        updatePushUi();
        return false;
      }
    }

    if (!pushConfig) pushConfig = await api("/api/push/config");
    const registration = await getRegistration();
    let subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: base64UrlToUint8Array(pushConfig.public_key),
      });
    }
    await syncSubscription(subscription);
    pushSubscription = subscription;
    updatePushUi();

    // Permission alone is not enough. Verify the full server -> push service -> PWA
    // path immediately so a broken iPhone subscription is visible at setup time.
    try {
      await runPushTest({ announce: false });
    } catch (_error) {}
    return true;
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
    updatePushUi();
  }

  async function showSystemNotification(reminder) {
    if (!pushSupported || Notification.permission !== "granted") return;
    try {
      const registration = await getRegistration();
      await registration.showNotification("Напоминание", {
        body: reminder.text,
        icon: "/icon.svg",
        tag: `reminder-${reminder.id}`,
        data: { url: "/", reminderId: reminder.id },
      });
    } catch (error) {
      console.warn("Local reminder notification failed", error);
    }
  }

  async function pollDueReminders() {
    if (reminderPollBusy) return;
    reminderPollBusy = true;
    try {
      const result = await api("/api/reminders/due");
      const reminders = result.reminders || [];
      if (!reminders.length) return;
      showChat();
      for (const reminder of reminders) {
        msg(reminder.message || `Напоминание · ${reminder.text}`, "assistant");
        await showSystemNotification(reminder);
      }
      armChatIdleTimer();
    } catch (error) {
      if (error.message !== "unauthorized")
        console.warn("Reminder poll failed", error);
    } finally {
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
      text.textContent = "Уведомления запрещены. Разреши их для приложения в настройках устройства или браузера.";
      item.appendChild(text);
    } else {
      text.textContent = "Чтобы напоминание пришло на экран, включи уведомления на этом устройстве.";
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Включить уведомления";
      button.style.cssText =
        "margin-top:10px;border:0;border-radius:10px;padding:9px 12px;background:#111;color:#fff;font:inherit;cursor:pointer";
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const enabled = await enablePushNotifications(true);
          if (enabled) {
            button.textContent = "Уведомления включены";
            msg("Уведомления включены на этом устройстве. Отправил тестовый push для проверки.", "assistant");
          } else {
            button.disabled = false;
          }
        } catch (error) {
          console.warn("Push enable failed", error);
          button.disabled = false;
          button.textContent = "Не удалось включить — попробуй ещё раз";
        }
      });
      item.append(text, button);
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

  async function initPushNotifications() {
    ensurePushSettingsUi();
    if (!pushSupported || (isIos && !isStandalone)) {
      updatePushUi();
      return;
    }
    try {
      const registration = await getRegistration();
      pushSubscription = await registration.pushManager.getSubscription();
      if (pushSubscription) {
        await syncSubscription(pushSubscription);
      } else if (Notification.permission === "granted") {
        await enablePushNotifications(false);
      }

      let selfTestDone = false;
      try {
        selfTestDone = localStorage.getItem(PUSH_SELF_TEST_KEY) === "ok";
      } catch (_error) {}
      if (pushSubscription && Notification.permission === "granted" && !selfTestDone) {
        window.setTimeout(() => runPushTest({ announce: false }).catch(() => {}), 500);
      }
    } catch (error) {
      if (error.message !== "unauthorized") {
        console.warn("Push initialization failed", error);
        setPushStatus(`Ошибка push: ${error.message}`);
      }
    }
    updatePushUi();
  }

  installTransientChatCleanup();
  ensurePushSettingsUi();
  watchReminderCreation();
  wrapLogout();
  initPushNotifications();
  window.setInterval(pollDueReminders, REMINDER_POLL_MS);
  window.setTimeout(pollDueReminders, 1500);
})();
