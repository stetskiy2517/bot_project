const assert = require('node:assert/strict');
const fs = require('node:fs');

const source = fs.readFileSync('web/email.js', 'utf8');

assert.match(source, /<form id="emailImapForm"/, 'IMAP credentials must be submitted through a form');
assert.match(source, /id="connectEmailImap"[^>]*type="submit"/, 'Connect button must submit the form');
assert.match(source, /emailImapForm"\)\.onsubmit\s*=\s*\(event\)/, 'Form submit handler must be installed');
assert.match(source, /event\.preventDefault\(\);\s*connectImap\(\);/, 'Enter submit must call connectImap');
assert.match(source, /Проверяю адрес и пароль приложения/, 'User must see an in-progress state');
assert.match(source, /Подключено: \$\{email\}/, 'User must see an explicit success state');
assert.match(source, /Не удалось подключить почту:/, 'User must see a useful error state');
assert.match(source, /aria-live="polite"/, 'Connection state must be announced accessibly');

console.log('Email connection client regression test passed');
