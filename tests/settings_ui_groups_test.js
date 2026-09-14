const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(path.join(__dirname, "..", "web", "reliability.js"), "utf8");

assert.match(source, /calendarSettingsGroup/);
assert.match(source, /notificationsGroup/);
assert.match(source, /Обзоры и тихие часы/);
assert.match(source, /Избранные команды/);
assert.match(source, /Отмена и данные/);
assert.match(source, /settings-group-summary-ready/);
assert.match(source, /MutationObserver/);

console.log("settings UI grouping tests passed");
