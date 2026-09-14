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

  toggle(name, force) {
    if (force === true) {
      this.values.add(name);
      return true;
    }
    if (force === false) {
      this.values.delete(name);
      return false;
    }
    if (this.values.has(name)) {
      this.values.delete(name);
      return false;
    }
    this.values.add(name);
    return true;
  }

  contains(name) {
    return this.values.has(name);
  }
}

class FakeStyle {
  constructor() {
    this.values = new Map();
  }

  setProperty(name, value) {
    this.values.set(name, String(value));
  }

  removeProperty(name) {
    this.values.delete(name);
  }

  getPropertyValue(name) {
    return this.values.get(name) || "";
  }
}

class FakeElement {
  constructor() {
    this.classList = new FakeClassList();
    this.listeners = new Map();
    this.attributes = new Map();
    this.style = new FakeStyle();
    this.children = [];
    this.disabled = false;
    this.title = "";
    this.textContent = "";
    this.innerHTML = "";
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

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  getBoundingClientRect() {
    return { left: 100, top: 240, width: 148, height: 148 };
  }

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
  const documentElement = { clientHeight: 800, style: new FakeStyle() };

  const document = {
    head,
    body,
    documentElement,
    activeElement: null,
    createElement() {
      return new FakeElement();
    },
    querySelector(selector) {
      return selector === ".voice-sub" ? voiceSub : null;
    },
    getElementById() {
      return null;
    },
    addEventListener() {},
  };
  const window = {
    innerHeight: 800,
    visualViewport: null,
    addEventListener() {},
  };

  const context = {
    console,
    document,
    window,
    HTMLElement: FakeElement,
    requestAnimationFrame(callback) {
      callback();
    },
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
  const trash = body.children[0];
  return { context, mainButton, chatButton, voiceCaption, voiceSub, trash };
}

function emulateBasePointerDown(context, button, pointerId = 7) {
  context.voicePressActive = true;
  context.voicePointerId = pointerId;
  context.voiceSourceButton = button;
  context.voiceRecordingStartedAt = 1000;
  context.voiceChunks = ["audio"];
}

(function testTrashAppearsAndButtonFollowsFinger() {
  const { context, mainButton, trash } = loadGestureScript();
  assert.ok(trash);

  mainButton.dispatch("pointerdown", pointerEvent({ clientY: 220 }));
  emulateBasePointerDown(context, mainButton);
  context.recorder = createRecorder(context);

  mainButton.dispatch("pointermove", pointerEvent({ clientY: 190 }));

  assert.equal(trash.classList.contains("visible"), true);
  assert.equal(trash.classList.contains("armed"), false);
  assert.equal(mainButton.classList.contains("voice-gesture-dragging"), true);
  assert.equal(mainButton.style.getPropertyValue("--voice-drag-y"), "-24px");
  assert.notEqual(mainButton.style.getPropertyValue("--voice-drag-scale"), "");
})();

(function testSwipeUpCancelsAndDoesNotSend() {
  const { context, mainButton, voiceCaption, voiceSub, trash } = loadGestureScript();
  assert.match(voiceSub.textContent, /Потяни вверх/);

  mainButton.dispatch("pointerdown", pointerEvent({ clientY: 220 }));
  emulateBasePointerDown(context, mainButton);
  context.recorder = createRecorder(context);

  mainButton.dispatch("pointermove", pointerEvent({ clientY: 140 }));
  assert.equal(mainButton.classList.contains("voice-cancel-armed"), true);
  assert.equal(trash.classList.contains("armed"), true);
  assert.equal(voiceCaption.textContent, "Отпусти — запись удалится");

  const release = pointerEvent({ clientY: 140 });
  mainButton.dispatch("pointerup", release);

  assert.equal(release.defaultPrevented, true);
  assert.equal(release.immediateStopped, true);
  assert.equal(mainButton.classList.contains("voice-cancel-drop"), true);
  assert.equal(trash.classList.contains("consume"), true);
  assert.equal(context.sentVoiceCount, 0);
  assert.equal(context.voiceRecordingStartedAt, 0);
  assert.equal(context.voiceChunks.length, 0);
  assert.equal(context.recorder.state, "inactive");
})();

(function testSmallMovementStillUsesNormalReleaseFlow() {
  const { context, mainButton, trash } = loadGestureScript();
  mainButton.dispatch("pointerdown", pointerEvent({ clientY: 220 }));
  emulateBasePointerDown(context, mainButton);
  context.recorder = createRecorder(context);

  mainButton.dispatch("pointermove", pointerEvent({ clientY: 180 }));
  assert.equal(trash.classList.contains("visible"), true);
  const release = pointerEvent({ clientY: 180 });
  mainButton.dispatch("pointerup", release);

  assert.equal(release.defaultPrevented, false);
  assert.equal(release.immediateStopped, false);
  assert.equal(mainButton.classList.contains("voice-gesture-dragging"), false);
  assert.equal(trash.classList.contains("visible"), false);
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
