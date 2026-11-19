(function () {
  'use strict';
  const UI = window.NovaUI = window.NovaUI || {};
  let sequence = 0;

  /** @param {HTMLElement} trigger @param {HTMLElement} menu @returns {{open: (last?: boolean) => void, close: (restore?: boolean) => void, destroy: () => void}} */
  function bind(trigger, menu) {
    const controller = new AbortController();
    const options = {signal: controller.signal};
    const original = {
      hidden: menu.hidden,
      id: menu.getAttribute('id'),
      role: menu.getAttribute('role'),
      expanded: trigger.getAttribute('aria-expanded'),
      controls: trigger.getAttribute('aria-controls'),
      popup: trigger.getAttribute('aria-haspopup')
    };
    if (!menu.id) menu.id = 'nova-menu-' + (++sequence);
    menu.setAttribute('role', 'menu');
    trigger.setAttribute('aria-haspopup', 'menu');
    trigger.setAttribute('aria-controls', menu.id);
    trigger.setAttribute('aria-expanded', 'false');
    menu.hidden = true;
    let opened = false;
    const itemState = new Map();
    const items = () => Array.from(menu.querySelectorAll('button, a[href], [role="menuitem"]'))
      .filter(item => !item.disabled && item.getAttribute('aria-disabled') !== 'true'
        && !item.hidden && item.getClientRects().length > 0);
    const close = (restore = false) => {
      opened = false;
      menu.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      if (restore && trigger.isConnected) trigger.focus();
    };
    const open = (last = false) => {
      opened = true;
      menu.hidden = false;
      trigger.setAttribute('aria-expanded', 'true');
      const available = items();
      available.forEach(item => {
        if (!itemState.has(item)) itemState.set(item, {
          role: item.getAttribute('role'), tabindex: item.getAttribute('tabindex')
        });
        item.setAttribute('role', 'menuitem');
        item.tabIndex = -1;
      });
      const target = last ? available[available.length - 1] : available[0];
      if (target) target.focus();
    };
    trigger.addEventListener('click', () => opened ? close() : open(), options);
    trigger.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        open(event.key === 'ArrowUp');
      }
    }, options);
    menu.addEventListener('keydown', event => {
      const available = items();
      const index = available.indexOf(document.activeElement);
      let next;
      if (event.key === 'ArrowDown') next = (index + 1) % available.length;
      else if (event.key === 'ArrowUp') next = (index - 1 + available.length) % available.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = available.length - 1;
      else if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        close(true);
      } else if (event.key === 'Tab') close(true);
      else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
        const ordered = available.slice(index + 1).concat(available.slice(0, index + 1));
        const match = ordered.find(item => item.textContent.trim().toLowerCase().startsWith(event.key.toLowerCase()));
        if (match) {event.preventDefault();match.focus();}
      }
      if (next !== undefined) {
        event.preventDefault();
        if (available[next]) available[next].focus();
      }
    }, options);
    menu.addEventListener('click', event => {
      if (!(event.target instanceof Element)) return;
      const item = event.target.closest('button, a[href], [role="menuitem"]');
      if (!item) return;
      if (item.disabled || item.getAttribute('aria-disabled') === 'true') {
        event.preventDefault();
        return;
      }
      close(true);
    }, options);
    document.addEventListener('pointerdown', event => {
      if (opened && !menu.contains(event.target) && !trigger.contains(event.target)) close();
    }, {...options, capture: true});
    document.addEventListener('focusin', event => {
      if (opened && !menu.contains(event.target) && !trigger.contains(event.target)) close();
    }, options);
    const restore = (element, attribute, value) => {
      if (value === null) element.removeAttribute(attribute);
      else element.setAttribute(attribute, value);
    };
    return {
      open, close,
      destroy() {
        close();
        controller.abort();
        menu.hidden = original.hidden;
        restore(menu, 'id', original.id);
        restore(menu, 'role', original.role);
        restore(trigger, 'aria-expanded', original.expanded);
        restore(trigger, 'aria-controls', original.controls);
        restore(trigger, 'aria-haspopup', original.popup);
        itemState.forEach((state, item) => {
          restore(item, 'role', state.role);
          restore(item, 'tabindex', state.tabindex);
        });
      }
    };
  }

  UI.Menu = Object.freeze({bind});
})();
