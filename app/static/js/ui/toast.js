(function () {
  'use strict';
  const UI = window.NovaUI = window.NovaUI || {};
  const active = new Set();
  let region = null;

  /** @param {string} message @param {{tone?: string, duration?: number}} [options] @returns {() => void} */
  function show(message, options = {}) {
    if (!region || !region.isConnected) {
      region = document.createElement('div');
      region.className = 'nova-toasts';
      region.setAttribute('aria-label', 'Notifications');
      document.body.append(region);
    }
    while (active.size >= 3) active.values().next().value();
    const toast = document.createElement('div');
    const tone = ['success', 'error', 'info'].includes(options.tone) ? options.tone : 'info';
    toast.className = 'nova-toast nova-toast-' + tone;
    toast.setAttribute('role', tone === 'error' ? 'alert' : 'status');
    const text = document.createElement('span');
    text.textContent = message;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'ui-btn ui-btn-ghost ui-btn-icon';
    button.setAttribute('aria-label', 'Dismiss notification');
    button.append(UI.Icons.create('close'));
    toast.append(text, button);
    region.append(toast);
    let timer;
    const dismiss = () => {
      clearTimeout(timer);
      toast.remove();
      active.delete(dismiss);
      if (!active.size && region) {region.remove();region = null;}
    };
    button.onclick = dismiss;
    active.add(dismiss);
    const duration = Number.isFinite(options.duration) ? Math.max(0, Math.min(30000, options.duration)) : 5000;
    if (duration > 0) timer = setTimeout(dismiss, duration);
    return dismiss;
  }

  window.addEventListener('pagehide', () => Array.from(active).forEach(dismiss => dismiss()));
  UI.Toast = Object.freeze({show});
})();
