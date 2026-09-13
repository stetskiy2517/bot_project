const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class FakeClassList {
  constructor() {
    this.values = new Set();
  }

  add(...names) {
    names.forEach((name) => this.values.add(name));
  }

  remove(...names) {
    names.forEach((name) => this.values.delete(name));
  }

  contains(name) {
    return this.values.has(name);
  }
}

class FakeElement {
  constructor() {
    this.classList = new FakeClassList();
    this.listeners = new Map();
    this.attributes = new Map();
    this.style = { setProperty() {} };
    this.disabled = false;
    this.title = "";
    this.textContent = "";
  }

  addEventListener(type, listener, options = false) {
    const config = typeof options === "object" ? options : { capture: Boolean(options) };
    const listeners = this.listeners.get(type) || [];
    listeners.push({ listener, capture: Boolean(config.capture), once: Boolean(config.once) });
    this.listeners.set(type, listeners);
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  appendChild() {}

  dispatch(type, event) {
    const listeners = [...(this.listeners.get(type) || [])].sort(
      (left, right) => Number(right.capture) - Number(left.capture),
    );
    event.currentTarget = this;
    for (const entry of listeners) {
      entry.listener(event);
      if (entry.once) {
        const current = this.listeners.get(type) || [];
        this.listeners.set(type, current.filter((candidate) => candidate !== entry));
      }
      if (event.immediateStopped) break;
    }
  }
}

function pointerEvent({ pointerId = 7, clientY = 200, pointerType = "touch", button = 0 } = {}) {
  return {
    pointerId,
    clientY,
    pointerType,
    button,
    defaultPrevented: false,
    immediateStopped: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
    stopImmediatePropagation() {
      this.immediateStopped = true;
    },
  };
}

function createRecorder(context) {
  const stopListeners = [];
  return {
    state: "recording",
    onstop: () => {
      if (context.voiceRecordingStartedAt >= 400) context.sentVoiceCount += 1;
    },
    addEventListener(type, listener, options = {}) {
      if (type === "stop") stopListeners.push({ listener, once: Boolean(options.once) });
    },
    stop() {
      this.state = "inactive";
      if (this.onstop) this.onstop();
      for (const entry of [...stopListeners]) entry.listener();
    },
  };
}

function loadGestureScript() {
  const mainButton = new FakeElement();
  const chatButton = new FakeElement();
  const voiceCaption = { textContent: "" };
  const voiceSub = { textContent: "" };
  const head = new FakeElement();
  const body = new FakeElement();

  const document = {
    head,
    body,
    createElement() {
      return new FakeElement();
    },
    querySelector(selector) {
      return selector === ".voice-sub" ? voiceSub : null;
    },
  };

  const context = {
    console,
    document,
    setTimeout,
    clearTimeout,
    voiceButtons: [mainButton, chatButton],
    chatVoiceBtn: chatButton,
    voiceCaption,
    voicePressActive: false,
    sendingVoice: false,
    voiceSourceButton: null,
    voicePointerId: null,
    voiceRecordingStartedAt: 0,
    voiceChunks: [],
    recorder: null,
    sentVoiceCount: 0,
    stopTracksCount: 0,
    resetVoiceButtonCount: 0,
    armChatIdleTimerCount: 0,
    stopTracks() {
      this.stopTracksCount += 1;
    },
    resetVoiceButton() {
      this.resetVoiceButtonCount += 1;
    },
    armChatIdleTimer() {
      this.armChatIdleTimerCount += 1;
    },
  };

  vm.createContext(context);
  const source = fs.readFileSync(path.join(__dirname, "..", "web", "voice_gesture.js"), "utf8");
  vm.runInContext(source, context, { filename: "voice_gesture.js" });
  return { context, mainButton, chatButton, voiceCaption, voiceSub };
}

function emulateBasePointerDown(context, button, pointerId = 7) {
  context.voicePressActive = true;
  context.voicePointerId = pointerId;
  context.voiceSourceButton = button;
  context.voiceRecordingStartedAt = 1000;
  context.voiceChunks = ["audio"];
}

(function testSwipeUpCancelsAndDoesNotSend() {
  const { context, mainButton, voiceCaption, voiceSub } = loadGestureScript();
  assert.match(voiceSub.textContent, /Потяни вверх/);

  mainButton.dispatch("pointerdown", pointerEvent({ clientY: 220 }));
  emulateBasePointerDown(context, mainButton);
  context.recorder = createRecorder(context);

  mainButton.dispatch("pointermove", pointerEvent({ clientY: 140 }));
  assert.equal(mainButton.classList.contains("voice-cancel-armed"), true);
  assert.equal(voiceCaption.textContent, "Отпусти — запись удалится");

  const release = pointerEvent({ clientY: 140 });
  mainButton.dispatch("pointerup", release);

  assert.equal(release.defaultPrevented, true);
  assert.equal(release.immediateStopped, true);
  assert.equal(context.sentVoiceCount, 0);
  assert.equal(context.voiceRecordingStartedAt, 0);
  assert.equal(context.voiceChunks.length, 0);
  assert.equal(context.recorder.state, "inactive");
})();

(function testSmallMovementStillUsesNormalReleaseFlow() {
  const { context, mainButton } = loadGestureScript();
  mainButton.dispatch("pointerdown", pointerEvent({ clientY: 220 }));
  emulateBasePointerDown(context, mainButton);
  context.recorder = createRecorder(context);

  mainButton.dispatch("pointermove", pointerEvent({ clientY: 180 }));
  const release = pointerEvent({ clientY: 180 });
  mainButton.dispatch("pointerup", release);

  assert.equal(release.defaultPrevented, false);
  assert.equal(release.immediateStopped, false);
  assert.equal(context.recorder.state, "recording");
  assert.equal(context.voiceRecordingStartedAt, 1000);
})();

(function testPointerCancelAlwaysDiscards() {
  const { context, chatButton } = loadGestureScript();
  chatButton.dispatch("pointerdown", pointerEvent({ clientY: 220 }));
  emulateBasePointerDown(context, chatButton);
  context.recorder = createRecorder(context);

  const cancel = pointerEvent({ clientY: 220 });
  chatButton.dispatch("pointercancel", cancel);

  assert.equal(cancel.defaultPrevented, true);
  assert.equal(cancel.immediateStopped, true);
  assert.equal(context.sentVoiceCount, 0);
  assert.equal(context.voiceRecordingStartedAt, 0);
  assert.equal(context.recorder.state, "inactive");
})();

console.log("voice gesture tests passed");
