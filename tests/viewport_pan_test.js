const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class FakeStyle {
  constructor() {
    this.values = new Map();
  }

  setProperty(name, value) {
    this.values.set(name, String(value));
  }

  getPropertyValue(name) {
    return this.values.get(name) || "";
  }
}

class FakeClassList {
  constructor() {
    this.values = new Set();
  }

  toggle(name, force) {
    if (force === false) {
      this.values.delete(name);
      return false;
    }
    if (force === true) {
      this.values.add(name);
      return true;
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

class FakeElement {
  constructor(rectTop = 0) {
    this.rectTop = rectTop;
    this.style = new FakeStyle();
    this.textContent = "";
    this.id = "";
  }

  matches(selector) {
    return selector.includes("input");
  }

  getBoundingClientRect() {
    return {top: this.rectTop};
  }
}

const source = fs.readFileSync(
  path.join(__dirname, "..", "web", "voice_gesture.js"),
  "utf8",
);
const marker = "\n})();\n\n(() => {";
const markerIndex = source.indexOf(marker);
assert.ok(markerIndex > 0, "viewport module boundary not found");
const viewportSource = source.slice(0, markerIndex + "\n})();".length);

const root = {
  clientHeight: 844,
  style: new FakeStyle(),
  classList: new FakeClassList(),
};
const body = new FakeElement(-84);
const activeInput = new FakeElement();
activeInput.id = "message";
const head = {appendChild() {}};
const document = {
  documentElement: root,
  body,
  activeElement: activeInput,
  head,
  createElement() {
    return {id: "", textContent: ""};
  },
  getElementById() {
    return null;
  },
  addEventListener() {},
};
const visualViewport = {
  height: 500,
  offsetTop: 0,
  addEventListener() {},
};
const window = {
  innerHeight: 844,
  visualViewport,
  addEventListener() {},
};
const context = {
  document,
  window,
  HTMLElement: FakeElement,
  requestAnimationFrame(callback) {
    callback();
  },
  setTimeout(callback) {
    callback();
    return 1;
  },
  clearTimeout() {},
};

vm.createContext(context);
vm.runInContext(viewportSource, context, {filename: "viewport-module.js"});

assert.equal(root.style.getPropertyValue("--stable-app-height"), "844px");
assert.equal(root.style.getPropertyValue("--keyboard-inset"), "344px");
assert.equal(root.style.getPropertyValue("--viewport-pan"), "84px");
assert.equal(root.style.getPropertyValue("--voice-keyboard-lift"), "151px");
assert.equal(root.classList.contains("composer-keyboard-open"), true);

console.log("viewport pan tests passed");
