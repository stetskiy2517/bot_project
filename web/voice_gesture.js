(() => {
  const CANCEL_SWIPE_PX = 72;
  const CANCEL_FEEDBACK_MS = 700;

  let gesture = null;
  let cancelToastTimer = null;

  const style = document.createElement("style");
  style.textContent = `
    .record-button.voice-cancel-armed,
    .composer-voice-button.voice-cancel-armed {
      transform: translateY(-8px) scale(0.94);
      opacity: 0.72;
    }
    .voice-cancel-toast {
      position: fixed;
      z-index: 45;
      left: 50%;
      bottom: calc(env(safe-area-inset-bottom) + 78px);
      transform: translateX(-50%);
      padding: 9px 13px;
      border-radius: 999px;
      background: rgba(17, 17, 17, 0.9);
      color: #fff;
      font-size: 13px;
      font-weight: 600;
      white-space: nowrap;
      pointer-events: none;
      opacity: 0;
      transition: opacity 0.12s ease;
    }
    .voice-cancel-toast.visible {
      opacity: 1;
    }
  `;
  document.head.appendChild(style);

  const toast = document.createElement("div");
  toast.className = "voice-cancel-toast";
  toast.setAttribute("role", "status");
  toast.setAttribute("aria-live", "polite");
  document.body.appendChild(toast);

  const voiceSub = document.querySelector(".voice-sub");
  if (voiceSub) voiceSub.textContent = "Отпусти — отправить · Потяни вверх — отменить";

  function showToast(text, persistent = false) {
    if (cancelToastTimer) {
      clearTimeout(cancelToastTimer);
      cancelToastTimer = null;
    }
    toast.textContent = text;
    toast.classList.add("visible");
    if (!persistent) {
      cancelToastTimer = setTimeout(() => {
        toast.classList.remove("visible");
        cancelToastTimer = null;
      }, CANCEL_FEEDBACK_MS);
    }
  }

  function hideToast() {
    if (cancelToastTimer) {
      clearTimeout(cancelToastTimer);
      cancelToastTimer = null;
    }
    toast.classList.remove("visible");
  }

  function restoreSourceButton(button) {
    if (!button) return;
    button.classList.remove("voice-cancel-armed");
    button.setAttribute(
      "aria-label",
      button === chatVoiceBtn
        ? "Нажать и удерживать для голосового ввода"
        : "Нажать и удерживать для записи",
    );
    if (button === chatVoiceBtn) button.title = "Нажми и удерживай для записи";
  }

  function showCancelledFeedback(sourceButton) {
    restoreSourceButton(sourceButton);
    voiceCaption.textContent = "Запись отменена";
    showToast("Запись отменена");
    setTimeout(() => {
      if (!voicePressActive && !sendingVoice) resetVoiceButton();
    }, CANCEL_FEEDBACK_MS);
    armChatIdleTimer();
  }

  function isCurrentGesture(event) {
    return Boolean(
      gesture &&
        voicePressActive &&
        voiceSourceButton === gesture.button &&
        (gesture.pointerId === null || event.pointerId === gesture.pointerId),
    );
  }

  function beginGesture(event) {
    if (sendingVoice || voicePressActive || event.currentTarget.disabled) return;
    if (event.pointerType === "mouse" && event.button !== 0) return;
    gesture = {
      pointerId: event.pointerId,
      button: event.currentTarget,
      startY: event.clientY,
      cancelArmed: false,
    };
    hideToast();
  }

  function moveGesture(event) {
    if (!isCurrentGesture(event) || gesture.cancelArmed) return;
    const upwardDistance = gesture.startY - event.clientY;
    if (upwardDistance < CANCEL_SWIPE_PX) return;

    gesture.cancelArmed = true;
    const button = gesture.button;
    button.classList.add("voice-cancel-armed");
    button.setAttribute("aria-label", "Отпустить, чтобы отменить запись");
    if (button === chatVoiceBtn) button.title = "Отпусти — запись удалится";
    voiceCaption.textContent = "Отпусти — запись удалится";
    showToast("Отпусти — запись удалится", true);
  }

  function discardCurrentRecording(event, force = false) {
    if (!gesture) return false;
    const matchesPointer =
      gesture.pointerId === null ||
      event.pointerId === undefined ||
      event.pointerId === gesture.pointerId;
    if (!matchesPointer) return false;
    if (!force && !gesture.cancelArmed) {
      gesture = null;
      hideToast();
      return false;
    }

    event.preventDefault();
    event.stopImmediatePropagation();

    const sourceButton = gesture.button;
    gesture = null;
    voicePressActive = false;
    voicePointerId = null;
    voiceRecordingStartedAt = 0;
    voiceChunks = [];
    hideToast();

    if (recorder && recorder.state === "recording") {
      const activeRecorder = recorder;
      activeRecorder.addEventListener(
        "stop",
        () => showCancelledFeedback(sourceButton),
        { once: true },
      );
      activeRecorder.stop();
      return true;
    }

    stopTracks();
    voiceSourceButton = null;
    resetVoiceButton();
    showCancelledFeedback(sourceButton);
    return true;
  }

  function cancelGesture(event) {
    if (!gesture) return;
    discardCurrentRecording(event, true);
  }

  voiceButtons.forEach((button) => {
    button.addEventListener("pointerdown", beginGesture, true);
    button.addEventListener("pointermove", moveGesture, true);
    button.addEventListener("pointerup", (event) => discardCurrentRecording(event), true);
    button.addEventListener("pointercancel", cancelGesture, true);
  });
})();
