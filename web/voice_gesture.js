(() => {
  const CANCEL_SWIPE_PX = 72;
  const TRASH_REVEAL_PX = 12;
  const CANCEL_FEEDBACK_MS = 700;
  const DROP_ANIMATION_MS = 180;

  let gesture = null;
  let cancelToastTimer = null;

  const style = document.createElement("style");
  style.textContent = `
    .record-button.voice-gesture-dragging,
    .composer-voice-button.voice-gesture-dragging {
      position: relative;
      z-index: 47;
      transition: none !important;
      transform: translateY(var(--voice-drag-y, 0px)) scale(var(--voice-drag-scale, 1)) !important;
      opacity: var(--voice-drag-opacity, 1);
    }
    .record-button.voice-cancel-armed,
    .composer-voice-button.voice-cancel-armed {
      box-shadow: 0 0 0 8px rgba(17,17,17,.08), 0 12px 34px rgba(0,0,0,.12);
    }
    .record-button.voice-cancel-drop,
    .composer-voice-button.voice-cancel-drop {
      z-index: 47;
      transition: transform ${DROP_ANIMATION_MS}ms cubic-bezier(.55,.02,.86,.48), opacity ${DROP_ANIMATION_MS}ms ease !important;
      transform: translateY(var(--voice-drop-y, -96px)) scale(.18) !important;
      opacity: 0 !important;
    }
    .voice-trash-target {
      position: fixed;
      z-index: 46;
      width: 54px;
      height: 54px;
      display: grid;
      place-items: center;
      border-radius: 18px;
      background: rgba(255,255,255,.96);
      border: 1px solid rgba(17,17,17,.09);
      box-shadow: 0 8px 28px rgba(0,0,0,.10);
      color: #222;
      pointer-events: none;
      opacity: 0;
      visibility: hidden;
      transform: translate(-50%, 10px) scale(.82);
      transition: opacity .12s ease, transform .16s cubic-bezier(.22,.8,.24,1), visibility 0s linear .16s;
    }
    .voice-trash-target.visible {
      opacity: 1;
      visibility: visible;
      transform: translate(-50%, 0) scale(1);
      transition-delay: 0s;
    }
    .voice-trash-target.armed {
      transform: translate(-50%, 0) scale(1.08);
      background: #111;
      color: #fff;
    }
    .voice-trash-target.consume {
      transform: translate(-50%, 0) scale(.92);
    }
    .voice-trash-target svg {
      width: 25px;
      height: 25px;
      fill: none;
      stroke: currentColor;
      stroke-width: 1.8;
      stroke-linecap: round;
      stroke-linejoin: round;
      overflow: visible;
    }
    .voice-trash-target .voice-trash-lid {
      transform-origin: 12px 7px;
      transition: transform .14s cubic-bezier(.22,.8,.24,1);
    }
    .voice-trash-target.armed .voice-trash-lid {
      transform: translateY(-2px) rotate(-12deg);
    }
    .voice-trash-target.consume .voice-trash-lid {
      transform: translateY(0) rotate(0deg);
    }
    .voice-cancel-toast {
      position: fixed;
      z-index: 48;
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

  const trash = document.createElement("div");
  trash.className = "voice-trash-target";
  trash.setAttribute("aria-hidden", "true");
  trash.innerHTML = `
    <svg viewBox="0 0 24 24">
      <g class="voice-trash-lid">
        <path d="M8.5 6V4.8c0-.8.6-1.3 1.4-1.3h4.2c.8 0 1.4.5 1.4 1.3V6" />
        <path d="M5 6h14" />
      </g>
      <path d="M7 8.2l.7 10.1c.1 1.2.8 1.7 1.9 1.7h4.8c1.1 0 1.8-.5 1.9-1.7L17 8.2" />
      <path d="M10 10.2v6.4M14 10.2v6.4" />
    </svg>`;
  document.body.appendChild(trash);

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

  function positionTrash(button) {
    if (!button || !button.getBoundingClientRect) return -96;
    const rect = button.getBoundingClientRect();
    const targetTop = Math.max(12, rect.top - 74);
    const centerX = rect.left + rect.width / 2;
    const targetCenterY = targetTop + 27;
    const buttonCenterY = rect.top + rect.height / 2;
    trash.style.left = `${centerX}px`;
    trash.style.top = `${targetTop}px`;
    return targetCenterY - buttonCenterY;
  }

  function hideTrash() {
    trash.classList.remove("visible", "armed", "consume");
  }

  function clearButtonMotion(button) {
    if (!button) return;
    button.classList.remove("voice-gesture-dragging", "voice-cancel-armed", "voice-cancel-drop");
    button.style.removeProperty("--voice-drag-y");
    button.style.removeProperty("--voice-drag-scale");
    button.style.removeProperty("--voice-drag-opacity");
    button.style.removeProperty("--voice-drop-y");
  }

  function updateDragVisual(button, upwardDistance, cancelArmed) {
    if (!button) return;
    const effectiveDistance = cancelArmed
      ? Math.max(CANCEL_SWIPE_PX, upwardDistance)
      : Math.max(0, upwardDistance);

    if (effectiveDistance < TRASH_REVEAL_PX) {
      button.classList.remove("voice-gesture-dragging");
      button.style.removeProperty("--voice-drag-y");
      button.style.removeProperty("--voice-drag-scale");
      button.style.removeProperty("--voice-drag-opacity");
      hideTrash();
      return;
    }

    const progress = Math.min(1, effectiveDistance / CANCEL_SWIPE_PX);
    const visualLift = Math.min(58, effectiveDistance * 0.8);
    const scale = 1 - progress * 0.22;
    const opacity = 1 - progress * 0.16;
    button.classList.add("voice-gesture-dragging");
    button.style.setProperty("--voice-drag-y", `${-visualLift}px`);
    button.style.setProperty("--voice-drag-scale", scale.toFixed(3));
    button.style.setProperty("--voice-drag-opacity", opacity.toFixed(3));
    trash.classList.add("visible");
    trash.classList.toggle("armed", cancelArmed);
    positionTrash(button);
  }

  function restoreSourceButton(button) {
    if (!button) return;
    clearButtonMotion(button);
    hideTrash();
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
    clearButtonMotion(event.currentTarget);
    hideTrash();
    gesture = {
      pointerId: event.pointerId,
      button: event.currentTarget,
      startY: event.clientY,
      cancelArmed: false,
      dropOffsetY: positionTrash(event.currentTarget),
    };
    hideToast();
  }

  function armCancellation() {
    if (!gesture || gesture.cancelArmed) return;
    gesture.cancelArmed = true;
    const button = gesture.button;
    button.classList.add("voice-cancel-armed");
    trash.classList.add("visible", "armed");
    button.setAttribute("aria-label", "Отпустить, чтобы отменить запись");
    if (button === chatVoiceBtn) button.title = "Отпусти — запись удалится";
    voiceCaption.textContent = "Отпусти — запись удалится";
    showToast("Отпусти — запись удалится", true);
  }

  function moveGesture(event) {
    if (!isCurrentGesture(event)) return;
    const upwardDistance = gesture.startY - event.clientY;
    if (!gesture.cancelArmed && upwardDistance >= CANCEL_SWIPE_PX) armCancellation();
    updateDragVisual(gesture.button, upwardDistance, gesture.cancelArmed);
  }

  function playDropAnimation(button, dropOffsetY) {
    if (!button) return;
    button.style.setProperty("--voice-drop-y", `${dropOffsetY || -96}px`);
    button.classList.remove("voice-gesture-dragging");
    button.classList.add("voice-cancel-drop");
    trash.classList.add("visible", "armed", "consume");
  }

  function discardCurrentRecording(event, force = false) {
    if (!gesture) return false;
    const matchesPointer =
      gesture.pointerId === null ||
      event.pointerId === undefined ||
      event.pointerId === gesture.pointerId;
    if (!matchesPointer) return false;
    if (!force && !gesture.cancelArmed) {
      const sourceButton = gesture.button;
      gesture = null;
      clearButtonMotion(sourceButton);
      hideTrash();
      hideToast();
      return false;
    }

    event.preventDefault();
    event.stopImmediatePropagation();

    const sourceButton = gesture.button;
    const dropOffsetY = gesture.dropOffsetY;
    gesture = null;
    voicePressActive = false;
    voicePointerId = null;
    voiceRecordingStartedAt = 0;
    voiceChunks = [];
    hideToast();
    playDropAnimation(sourceButton, dropOffsetY);

    const finish = () => {
      setTimeout(() => showCancelledFeedback(sourceButton), DROP_ANIMATION_MS);
    };

    if (recorder && recorder.state === "recording") {
      const activeRecorder = recorder;
      activeRecorder.addEventListener("stop", finish, { once: true });
      activeRecorder.stop();
      return true;
    }

    stopTracks();
    voiceSourceButton = null;
    resetVoiceButton();
    finish();
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
