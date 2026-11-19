(function () {
  'use strict';
  const UI = window.NovaUI = window.NovaUI || {};
  const paths = Object.freeze({
    menu: ['M4 6h16M4 12h16M4 18h16'],
    close: ['m6 6 12 12M6 18 18 6'],
    chat: ['M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-3 3V11.5A8.5 8.5 0 0 1 9.5 3h3a8.5 8.5 0 0 1 8.5 8.5Z'],
    search: ['M21 21l-5-5', 'M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z'],
    plus: ['M12 5v14M5 12h14'],
    check: ['m5 12 4 4L19 6'],
    chevron: ['m9 5 7 7-7 7'],
    copy: ['M9 9h12v12H9z', 'M15 9V3H3v12h6'],
    stop: ['M5 5h14v14H5z'],
    retry: ['M3 11a9 9 0 1 1 3 8', 'M3 4v7h7'],
    send: ['m22 2-7 20-4-9-9-4Z', 'm22 2-11 11'],
    settings: ['M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Z', 'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1Z'],
    info: ['M12 16v-5M12 7h.01', 'M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z'],
    warning: ['m12 3 10 18H2Z', 'M12 9v5M12 17h.01']
  });
  let sequence = 0;

  /** @param {string} name @param {{size?: number, label?: string}} [options] @returns {SVGSVGElement} */
  function create(name, options = {}) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    const size = Number.isFinite(options.size) ? Math.min(64, Math.max(12, options.size)) : 18;
    for (const [key, value] of Object.entries({viewBox: '0 0 24 24', width: size, height: size,
      fill: 'none', stroke: 'currentColor', 'stroke-width': 1.7,
      'stroke-linecap': 'round', 'stroke-linejoin': 'round', focusable: 'false'})) {
      svg.setAttribute(key, String(value));
    }
    if (options.label) {
      const title = document.createElementNS(svg.namespaceURI, 'title');
      title.id = 'nova-icon-title-' + (++sequence);
      title.textContent = options.label;
      svg.setAttribute('role', 'img');
      svg.setAttribute('aria-labelledby', title.id);
      svg.append(title);
    } else svg.setAttribute('aria-hidden', 'true');
    for (const d of Object.hasOwn(paths, name) ? paths[name] : paths.info) {
      const path = document.createElementNS(svg.namespaceURI, 'path');
      path.setAttribute('d', d);
      svg.append(path);
    }
    return svg;
  }

  UI.Icons = Object.freeze({create});
})();
