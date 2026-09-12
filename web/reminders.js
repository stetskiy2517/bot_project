(() => {
  const REMINDER_POLL_MS = 15000;
  const CHAT_CLEAR_DELAY_MS = 400;
  let reminderPollBusy = false;
  let chatClearTimer = null;

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

  async function pollDueReminders() {
    if (reminderPollBusy) return;
    reminderPollBusy = true;
    try {
      const result = await api("/api/reminders/due");
      const reminders = result.reminders || [];
      if (!reminders.length) return;
      showChat();
      reminders.forEach((reminder) => {
        msg(reminder.message || `Напоминание · ${reminder.text}`, "assistant");
        if ("Notification" in window && Notification.permission === "granted") {
          try {
            new Notification("Напоминание", {
              body: reminder.text,
              icon: "/icon.svg",
            });
          } catch (_error) {}
        }
      });
      armChatIdleTimer();
    } catch (error) {
      if (error.message !== "unauthorized")
        console.warn("Reminder poll failed", error);
    } finally {
      reminderPollBusy = false;
    }
  }

  installTransientChatCleanup();
  window.setInterval(pollDueReminders, REMINDER_POLL_MS);
  window.setTimeout(pollDueReminders, 1500);
})();
