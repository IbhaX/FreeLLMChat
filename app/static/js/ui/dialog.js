(function () {
  'use strict';
  const UI = window.NovaUI = window.NovaUI || {};
  let sequence = 0;

  /** @typedef {{value: string, label: string}} Choice */
  /** @typedef {{name: string, label: string, type?: string, value?: string | boolean, required?: boolean, options?: Choice[]}} Field */
  /** @typedef {{title: string, message?: string, fields?: Field[], confirmLabel?: string, danger?: boolean}} FormOptions */

  /** @param {FormOptions} options @returns {Promise<Record<string, string | boolean> | null>} */
  function form(options) {
    const fields = options.fields || [];
    const allowedTypes = ['text', 'password', 'email', 'url', 'number', 'textarea', 'select', 'checkbox', 'multiselect'];
    const names = new Set();
    for (const field of fields) {
      if (!field.name || names.has(field.name) || !allowedTypes.includes(field.type || 'text')) {
        return Promise.reject(new TypeError('Invalid dialog field'));
      }
      names.add(field.name);
    }
    return new Promise(resolve => {
      const trigger = document.activeElement;
      const dialog = document.createElement('dialog');
      dialog.className = 'nova-dialog';
      const heading = document.createElement('h2');
      heading.id = 'nova-dialog-title-' + (++sequence);
      heading.textContent = options.title;
      dialog.setAttribute('aria-labelledby', heading.id);
      const body = document.createElement('form');
      body.append(heading);
      if (options.message) {
        const message = document.createElement('p');
        message.id = 'nova-dialog-message-' + sequence;
        message.textContent = options.message;
        dialog.setAttribute('aria-describedby', message.id);
        body.append(message);
      }
      const controls = new Map();
      for (const field of fields) {
        const label = document.createElement('label');
        label.className = 'nova-dialog-field';
        const caption = document.createElement('span');
        caption.textContent = field.label;
        const type = field.type || 'text';
        const input = document.createElement(
          type === 'textarea' ? 'textarea'
            : type === 'select' ? 'select'
            : type === 'multiselect' ? 'div' : 'input');
        if (input.tagName === 'INPUT') input.type = type;
        caption.id = 'nova-dialog-field-' + sequence + '-' + controls.size;
        input.setAttribute('aria-labelledby', caption.id);
        input.name = field.name;
        input.className = type === 'checkbox' ? ''
          : type === 'multiselect' ? 'nova-dialog-ms' : 'ui-input';
        input.required = Boolean(field.required);
        if (type === 'select') {
          for (const choice of field.options || []) {
            const option = document.createElement('option');
            option.value = choice.value;
            option.textContent = choice.label;
            input.append(option);
          }
        }
        if (type === 'multiselect') {
          const picked = new Set(Array.isArray(field.value) ? field.value : []);
          let allCb = null;
          const rest = [];
          for (const choice of field.options || []) {
            const item = document.createElement('label');
            item.className = 'nova-dialog-msi'
              + (choice.selectAll ? ' nova-dialog-msall' : '');
            const cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.value = choice.value;
            cb.checked = picked.has(choice.value);
            const text = document.createElement('span');
            text.textContent = choice.label;
            if (choice.title) item.title = choice.title;
            item.append(cb, text);
            input.append(item);
            if (choice.selectAll) allCb = cb; else rest.push(cb);
          }
          /* An option flagged selectAll drives the rest: toggling it
             checks/clears every item; it mirrors the set (checked /
             indeterminate) as items are toggled individually. */
          if (allCb && rest.length) {
            const sync = () => {
              const on = rest.filter(c => c.checked).length;
              allCb.checked = on === rest.length;
              allCb.indeterminate = on > 0 && on < rest.length;
            };
            allCb.addEventListener('change', () => {
              rest.forEach(c => { c.checked = allCb.checked; });
              allCb.indeterminate = false;
            });
            rest.forEach(c => c.addEventListener('change', sync));
            sync();
          }
        }
        if (type === 'checkbox') input.checked = Boolean(field.value);
        else if (type !== 'multiselect') input.value = field.value == null ? '' : String(field.value);
        label.append(caption, input);
        body.append(label);
        controls.set(field.name, input);
      }
      const actions = document.createElement('div');
      actions.className = 'nova-dialog-actions';
      const cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.className = 'ui-btn';
      cancel.textContent = 'Cancel';
      const submit = document.createElement('button');
      submit.type = 'submit';
      submit.className = 'ui-btn ' + (options.danger ? 'ui-btn-danger' : 'ui-btn-primary');
      submit.textContent = options.confirmLabel || 'Save';
      actions.append(cancel, submit);
      body.append(actions);
      dialog.append(body);
      dialog.addEventListener('keydown', event => {
        if (event.key !== 'Tab') return;
        const focusable = Array.from(dialog.querySelectorAll('input, select, textarea, button, a[href], [tabindex]'))
          .filter(control => !control.disabled && control.tabIndex >= 0
            && control.getClientRects().length > 0);
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      });
      let result = null;
      const dismiss = () => dialog.close();
      cancel.onclick = dismiss;
      body.onsubmit = event => {
        event.preventDefault();
        if (!body.reportValidity()) return;
        result = Object.fromEntries(Array.from(controls, ([name, control]) => [
          name,
          control.classList && control.classList.contains('nova-dialog-ms')
            ? Array.from(control.querySelectorAll('input:checked'), c => c.value)
            : control.type === 'checkbox' ? control.checked : control.value
        ]));
        dialog.close();
      };
      dialog.addEventListener('close', () => {
        window.removeEventListener('pagehide', dismiss);
        dialog.remove();
        if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
        resolve(result);
      }, {once: true});
      window.addEventListener('pagehide', dismiss, {once: true});
      document.body.append(dialog);
      dialog.showModal();
      const first = controls.values().next().value;
      (first || cancel).focus();
    });
  }

  /** @param {string} message @param {{title?: string, confirmLabel?: string, danger?: boolean}} [options] @returns {Promise<boolean>} */
  async function confirm(message, options = {}) {
    return await form({...options, title: options.title || 'Confirm', message,
      confirmLabel: options.confirmLabel || 'Confirm'}) !== null;
  }

  /** @param {string} message @param {{title?: string, value?: string, required?: boolean}} [options] @returns {Promise<string | null>} */
  async function prompt(message, options = {}) {
    const result = await form({title: options.title || 'Enter a value', fields: [
      {name: 'value', label: message, value: options.value || '', required: options.required}
    ]});
    return result === null ? null : String(result.value);
  }

  UI.Dialog = Object.freeze({form, confirm, prompt});
})();
