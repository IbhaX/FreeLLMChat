(function () {
  'use strict';
  const UI = window.NovaUI = window.NovaUI || {};

  /** @param {'empty' | 'loading' | 'error'} kind @param {{title: string, detail?: string, action?: {label: string, run: () => void}}} options @returns {HTMLElement} */
  function create(kind, options) {
    const section = document.createElement('section');
    section.className = 'nova-state nova-state-' + kind;
    if (kind === 'error') section.setAttribute('role', 'alert');
    if (kind === 'loading') {
      section.setAttribute('role', 'status');
      section.setAttribute('aria-busy', 'true');
    }
    const heading = document.createElement('h3');
    heading.textContent = options.title;
    section.append(heading);
    if (options.detail) {
      const detail = document.createElement('p');
      detail.textContent = options.detail;
      section.append(detail);
    }
    if (kind === 'loading') {
      const skeleton = document.createElement('div');
      skeleton.className = 'ui-skeleton';
      skeleton.setAttribute('aria-hidden', 'true');
      section.append(skeleton);
    }
    if (options.action) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'ui-btn';
      button.textContent = options.action.label;
      button.onclick = options.action.run;
      section.append(button);
    }
    if (window.NovaMotion) NovaMotion.enter(section, {y: 10});
    return section;
  }

  UI.States = Object.freeze({
    empty: options => create('empty', options),
    loading: options => create('loading', options),
    error: options => create('error', options)
  });
})();
