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
  const styleProps = {};
  const context = {
    document: {
      documentElement: {
        dataset: {theme: initial, defaultTheme: pageDefault},
        style: {
          setProperty: (k, v) => { styleProps[k] = v; },
          removeProperty: k => { delete styleProps[k]; }
        }
      },
      querySelectorAll: () => [],
      addEventListener: () => {}
    },
    localStorage: {
      getItem: () => { if (blocked) throw Error('blocked'); return null; },
      setItem: (key, value) => { if (blocked) throw Error('blocked'); writes.push([key, value]); },
      removeItem: () => { if (blocked) throw Error('blocked'); }
    },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    addEventListener: (name, listener) => { listeners[name] = listener; },
    dispatchEvent: event => events.push(event)
  };
  context.window = context;
  vm.runInNewContext(source, context);
  return {theme: context.UITheme, listeners, events, writes, styleProps};
}
const app = setup();
assert.equal(app.theme.registry.length, 13);
assert.equal(new Set(app.theme.THEMES).size, 13);
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
// accent customization
const before = app.events.length;
assert.ok(app.theme.setAccent('#ffdd00'));
assert.equal(app.theme.accent(), '#ffdd00');
assert.equal(app.styleProps['--accent'], '#ffdd00');
assert.ok(app.styleProps['--accent-soft'].startsWith('rgba(255,221,0'));
assert.equal(app.styleProps['--on-accent'], '#0a0a0a');
assert.ok(app.writes.some(([k]) => k === 'ui-theme-custom'));
assert.equal(app.events[before].type, 'nova:themecustom');
assert.ok(app.theme.setAccent('#123456'));
assert.equal(app.styleProps['--on-accent'], '#ffffff');
assert.equal(app.theme.setAccent('not-a-color'), false);
app.theme.clearAccent();
assert.equal(app.theme.accent(), null);
assert.equal(app.styleProps['--accent'], undefined);
app.listeners.storage({key: 'ui-theme-custom',
  newValue: JSON.stringify({accent: '#00ff00', vars: {'--accent': '#00ff00'}})});
assert.equal(app.theme.accent(), '#00ff00');
assert.equal(app.styleProps['--accent'], '#00ff00');
app.listeners.storage({key: 'ui-theme-custom', newValue: null});
assert.equal(app.theme.accent(), null);
assert.equal(app.styleProps['--accent'], undefined);
console.log('Theme registry, events, storage synchronization, blocked storage and accent customization: passed');
