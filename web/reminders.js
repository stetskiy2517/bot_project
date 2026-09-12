(() => {
  const REMINDER_POLL_MS = 15000;
  let reminderPollBusy = false;

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

  window.setInterval(pollDueReminders, REMINDER_POLL_MS);
  window.setTimeout(pollDueReminders, 1500);
})();
