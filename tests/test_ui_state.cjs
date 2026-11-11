'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');
const start = source.indexOf('function showLogin()');
const end = source.indexOf('function showDashboard(', start);
assert.ok(start >= 0 && end > start, 'Expected the actual login state transition');
const elements = {
  '#details-dialog': {closed: false, close() { this.closed = true; }},
  '#shell': {hidden: false},
  '#login-screen': {hidden: true}
};
const context = {elements, $: selector => elements[selector]};
vm.runInNewContext(`
  let signedIn = true, settingsLoaded = true, rulesLoaded = true;
  let selected = new Set([42]);
  ${source.slice(start, end)}
  showLogin();
  globalThis.result = {signedIn, settingsLoaded, rulesLoaded, selected: selected.size};
`, context);
assert.equal(context.result.signedIn, false);
assert.equal(context.result.settingsLoaded, false, 'A new session must reload settings');
assert.equal(context.result.rulesLoaded, false, 'A new session must reload rules');
assert.equal(context.result.selected, 0);
assert.equal(elements['#details-dialog'].closed, true);
assert.equal(elements['#shell'].hidden, true);
assert.equal(elements['#login-screen'].hidden, false);
console.log('Login transition clears cached form state and hides prior dashboard data.');
