'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../app/static/js/theme.js'), 'utf8');
function setup(initial = 'dark', blocked = false, pageDefault = initial) {
  const listeners = {};
  const events = [];
  const writes = [];
  const context = {
    document: {
      documentElement: {dataset: {theme: initial, defaultTheme: pageDefault}},
      querySelectorAll: () => [],
      addEventListener: () => {}
    },
    localStorage: {
      getItem: () => { if (blocked) throw Error('blocked'); return null; },
      setItem: (key, value) => { if (blocked) throw Error('blocked'); writes.push([key, value]); }
    },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    addEventListener: (name, listener) => { listeners[name] = listener; },
    dispatchEvent: event => events.push(event)
  };
  context.window = context;
  vm.runInNewContext(source, context);
  return {theme: context.UITheme, listeners, events, writes};
}
const app = setup();
assert.equal(app.theme.registry.length, 8);
assert.equal(new Set(app.theme.THEMES).size, 8);
assert.ok(Object.isFrozen(app.theme.registry));
app.theme.set('stage');
assert.equal(app.theme.get(), 'stage');
assert.equal(app.events.length, 1);
assert.equal(app.events[0].detail.scheme, 'dark');
app.theme.set('stage');
assert.equal(app.events.length, 1);
app.listeners.storage({key: 'ui-theme', newValue: 'apple'});
assert.equal(app.theme.get(), 'apple');
assert.equal(app.writes.length, 2);
assert.equal(app.events[1].detail.scheme, 'light');
const roleplay = setup('stage', true);
assert.doesNotThrow(() => roleplay.theme.set('light'));
roleplay.listeners.storage({key: 'ui-theme', newValue: null});
assert.equal(roleplay.theme.get(), 'stage');
const restoredRoleplay = setup('light', false, 'stage');
restoredRoleplay.listeners.storage({key: 'ui-theme', newValue: null});
assert.equal(restoredRoleplay.theme.get(), 'stage');
const invalid = setup('missing', false, 'stage');
assert.equal(invalid.theme.get(), 'stage');
invalid.theme.set('__proto__');
assert.equal(invalid.theme.get(), 'stage');
console.log('Theme registry, events, storage synchronization and blocked storage: passed');
