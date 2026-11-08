/* ==========================================================================
   Visuals — additional dynamic visual components:
   timeline, stat_grid, callout, gauge, sparkline, keyvalue, badge, quote,
   diff, heatmap, tagcloud.

   These make responses feel rich and dynamic instead of plain text.
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { el, esc, fmtNum, inline } = Nova;

  // ---------- Timeline ----------
  function timeline(data) {
    const events = (data && data.events) || [];
    if (!events.length) return null;
    const wrap = el("div", { class: "nova-timeline" });
    if (data.title) wrap.append(el("div", { class: "nova-section-title" }, data.title));
    const list = el("ol", { class: "nova-timeline-list" });
    events.forEach((ev) => {
      const item = el("li", { class: "nova-timeline-item" });
      item.append(el("span", { class: "nova-timeline-dot", "aria-hidden": "true" }, ev.icon || ""));
      const body = el("div", { class: "nova-timeline-body" });
      if (ev.date) body.append(el("div", { class: "nova-timeline-date" }, ev.date));
      if (ev.title) body.append(el("div", { class: "nova-timeline-title", html: inline(ev.title) }));
      if (ev.description) body.append(el("div", { class: "nova-timeline-desc", html: inline(ev.description) }));
      item.append(body);
      list.append(item);
    });
    wrap.append(list);
    return wrap;
  }

  // ---------- Stat Grid ----------
  function statGrid(data) {
    const stats = (data && data.stats) || [];
    if (!stats.length) return null;
    const wrap = el("div", { class: "nova-stat-grid-wrap" });
    if (data.title) wrap.append(el("div", { class: "nova-section-title" }, data.title));
    const grid = el("div", { class: "nova-metric-grid" });
    stats.forEach((s) => {
      const card = el("div", { class: "nova-metric" });
      const head = el("div", { class: "nova-metric-head" });
      if (s.icon) head.append(el("span", { class: "nova-metric-ico" }, s.icon));
      if (s.label) head.append(el("span", { class: "nova-metric-label", html: inline(s.label) }));
      card.append(head);
      const val = el("div", { class: "nova-metric-value" }, fmtNum(s.value, s.unit));
      if (s.delta != null) {
        const d = el("span", { class: "nova-metric-delta " + (s.delta >= 0 ? "up" : "down") },
          (s.delta >= 0 ? "▲ " : "▼ ") + Math.abs(s.delta) + (s.delta_unit || "%"));
        val.append(d);
      }
      card.append(val);
      // Percentages get a proportional bar so shares are readable at a glance.
      const n = Number(s.value);
      if (s.unit === "%" && isFinite(n)) {
        const bar = el("div", { class: "nova-metric-bar" });
        bar.append(el("i", { style: { width: Math.max(0, Math.min(100, n)) + "%" } }));
        card.append(bar);
      }
      if (s.description) card.append(el("div", { class: "nova-metric-desc", html: inline(s.description) }));
      grid.append(card);
    });
    wrap.append(grid);
    return wrap;
  }

  // ---------- Callout ----------
  const CALLOUT_ICONS = { info: "ℹ", tip: "💡", important: "⚠", quote: "❝" };
  function callout(data) {
    if (!data || !data.message) return null;
    const variant = (data.variant || "info").toLowerCase();
    const wrap = el("div", { class: "nova-callout nova-callout-" + variant, role: "note" });
    wrap.append(el("span", { class: "nova-callout-ico", "aria-hidden": "true" }, data.icon || CALLOUT_ICONS[variant] || "ℹ"));
    const body = el("div", { class: "nova-callout-body" });
    if (data.title) body.append(el("div", { class: "nova-callout-title", html: inline(data.title) }));
    body.append(el("div", { class: "nova-callout-msg", html: inline(data.message) }));
    wrap.append(body);
    return wrap;
  }

  // ---------- Gauge (SVG circular) ----------
  function gauge(data) {
    if (!data || data.value == null) return null;
    const max = data.max != null ? data.max : 100;
    const val = Math.max(0, Math.min(max, Number(data.value)));
    const pct = max > 0 ? val / max : 0;
    const r = 52;
    const circ = 2 * Math.PI * r;
    const offset = circ * (1 - pct);
    const color = data.color || "var(--accent)";
    const wrap = el("div", { class: "nova-gauge-wrap" });
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "nova-gauge");
    svg.setAttribute("viewBox", "0 0 140 140");
    svg.setAttribute("width", "140");
    svg.setAttribute("height", "140");
    svg.innerHTML =
      `<circle cx="70" cy="70" r="${r}" fill="none" stroke="var(--surface-3)" stroke-width="10"/>` +
      `<circle cx="70" cy="70" r="${r}" fill="none" stroke="${color}" stroke-width="10" stroke-linecap="round"` +
      ` stroke-dasharray="${circ}" stroke-dashoffset="${offset}"` +
      ` transform="rotate(-90 70 70)" style="transition:stroke-dashoffset .6s ease"/>` +
      `<text x="70" y="76" text-anchor="middle" font-size="22" font-weight="700" fill="var(--text-1)">${fmtNum(val)}${data.unit || "%"}</text>`;
    wrap.append(svg);
    if (data.label) wrap.append(el("div", { class: "nova-gauge-label" }, data.label));
    return wrap;
  }

  // ---------- Sparkline (SVG) ----------
  function sparkline(data) {
    const arr = (data && data.data) || [];
    if (!arr.length) return null;
    const type = (data.type || "line").toLowerCase();
    const w = 200, h = 48, pad = 4;
    const max = Math.max(...arr), min = Math.min(...arr);
    const range = max - min || 1;
    const stepX = (w - pad * 2) / Math.max(1, arr.length - 1);
    const pts = arr.map((v, i) => {
      const x = pad + i * stepX;
      const y = h - pad - ((v - min) / range) * (h - pad * 2);
      return [x, y];
    });
    const color = data.color || "var(--accent)";
    const wrap = el("div", { class: "nova-sparkline-wrap" });
    if (data.label) wrap.append(el("div", { class: "nova-sparkline-label" }, data.label));
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("width", "100%");
    svg.setAttribute("height", h);
    svg.setAttribute("preserveAspectRatio", "none");
    if (type === "bar") {
      const bw = stepX * 0.7;
      pts.forEach(([x, y]) => {
        const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        rect.setAttribute("x", x - bw / 2);
        rect.setAttribute("y", y);
        rect.setAttribute("width", bw);
        rect.setAttribute("height", h - pad - y);
        rect.setAttribute("fill", color);
        rect.setAttribute("opacity", "0.8");
        svg.append(rect);
      });
    } else {
      const pathD = pts.map((p, i) => (i === 0 ? "M" : "L") + p[0] + " " + p[1]).join(" ");
      if (type === "area") {
        const areaD = pathD + ` L ${pts[pts.length - 1][0]} ${h - pad} L ${pts[0][0]} ${h - pad} Z`;
        const area = document.createElementNS("http://www.w3.org/2000/svg", "path");
        area.setAttribute("d", areaD);
        area.setAttribute("fill", color);
        area.setAttribute("opacity", "0.15");
        svg.append(area);
      }
      const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
      line.setAttribute("d", pathD);
      line.setAttribute("fill", "none");
      line.setAttribute("stroke", color);
      line.setAttribute("stroke-width", "2");
      line.setAttribute("stroke-linecap", "round");
      line.setAttribute("stroke-linejoin", "round");
      svg.append(line);
      pts.forEach(([x, y]) => {
        const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        dot.setAttribute("cx", x);
        dot.setAttribute("cy", y);
        dot.setAttribute("r", 2);
        dot.setAttribute("fill", color);
        svg.append(dot);
      });
    }
    wrap.append(svg);
    return wrap;
  }

  // ---------- Key-Value ----------
  function keyvalue(data) {
    const pairs = (data && data.pairs) || [];
    if (!pairs.length) return null;
    const wrap = el("div", { class: "nova-kv-wrap" });
    if (data.title) wrap.append(el("div", { class: "nova-section-title" }, data.title));
    const grid = el("div", { class: "nova-kv-grid" });
    pairs.forEach((p) => {
      const row = el("div", { class: "nova-kv-row" + (p.highlight ? " highlight" : "") });
      row.append(el("div", { class: "nova-kv-key" }, p.key || ""));
      row.append(el("div", { class: "nova-kv-val", html: inline(p.value != null ? p.value : "") }));
      grid.append(row);
    });
    wrap.append(grid);
    return wrap;
  }

  // ---------- Badge ----------
  function badge(data) {
    const badges = (data && data.badges) || [];
    if (!badges.length) return null;
    const wrap = el("div", { class: "nova-badge-wrap" });
    if (data.title) wrap.append(el("div", { class: "nova-section-title" }, data.title));
    const row = el("div", { class: "nova-badge-row" });
    badges.forEach((b) => {
      const variant = b.variant || "default";
      const pill = el("span", { class: "nova-badge nova-badge-" + variant });
      if (b.icon) pill.append(el("span", { class: "nova-badge-ico" }, b.icon));
      pill.append(el("span", {}, b.label || ""));
      row.append(pill);
    });
    wrap.append(row);
    return wrap;
  }

  // ---------- Quote ----------
  function quote(data) {
    if (!data || !data.text) return null;
    const wrap = el("div", { class: "nova-quote" });
    if (data.image) {
      wrap.append(el("img", { class: "nova-quote-img", src: data.image, alt: data.author || "" }));
    }
    wrap.append(el("div", { class: "nova-quote-text", html: "“" + inline(data.text) + "”" }));
    if (data.author) {
      const attr = el("div", { class: "nova-quote-author" });
      attr.append(el("span", {}, "— " + data.author));
      if (data.source) attr.append(el("span", { class: "nova-quote-source" }, ", " + data.source));
      wrap.append(attr);
    }
    return wrap;
  }

  // ---------- Diff ----------
  function diff(data) {
    if (!data || !data.before == null || data.after == null) return null;
    const wrap = el("div", { class: "nova-diff-wrap" });
    if (data.title) wrap.append(el("div", { class: "nova-section-title" }, data.title));
    const grid = el("div", { class: "nova-diff-grid" });
    const before = el("div", { class: "nova-diff-col nova-diff-before" });
    before.append(el("div", { class: "nova-diff-head" }, "Before"));
    const pre1 = el("pre", {}, data.before);
    before.append(pre1);
    const after = el("div", { class: "nova-diff-col nova-diff-after" });
    after.append(el("div", { class: "nova-diff-head" }, "After"));
    const pre2 = el("pre", {}, data.after);
    after.append(pre2);
    grid.append(before, after);
    wrap.append(grid);
    return wrap;
  }

  // ---------- Heatmap ----------
  function heatmap(data) {
    const grid = (data && data.data) || [];
    if (!grid.length) return null;
    const wrap = el("div", { class: "nova-heatmap-wrap" });
    if (data.title) wrap.append(el("div", { class: "nova-section-title" }, data.title));
    const table = el("div", { class: "nova-heatmap" });
    const xLabels = data.x_labels || [];
    const yLabels = data.y_labels || [];
    grid.forEach((row, yi) => {
      const r = el("div", { class: "nova-heatmap-row" });
      if (yLabels[yi]) r.append(el("div", { class: "nova-heatmap-label" }, yLabels[yi]));
      row.forEach((val, xi) => {
        const v = Math.max(0, Math.min(1, Number(val) || 0));
        const cell = el("div", {
          class: "nova-heatmap-cell",
          title: (xLabels[xi] || "") + ": " + v.toFixed(2),
          style: { background: `rgba(99,102,241,${0.1 + v * 0.9})` },
        }, v.toFixed(1));
        r.append(cell);
      });
      table.append(r);
    });
    if (xLabels.length) {
      const head = el("div", { class: "nova-heatmap-row" });
      head.append(el("div", { class: "nova-heatmap-label" }, ""));
      xLabels.forEach((l) => head.append(el("div", { class: "nova-heatmap-xlabel" }, l)));
      table.prepend(head);
    }
    wrap.append(table);
    return wrap;
  }

  // ---------- Tag Cloud ----------
  function tagcloud(data) {
    const tags = (data && data.tags) || [];
    if (!tags.length) return null;
    const wrap = el("div", { class: "nova-tagcloud-wrap" });
    if (data.title) wrap.append(el("div", { class: "nova-section-title" }, data.title));
    const cloud = el("div", { class: "nova-tagcloud" });
    tags.forEach((t) => {
      const w = t.weight != null ? t.weight : 1;
      const size = 12 + Math.max(0, Math.min(3, w)) * 6;
      const tag = el("span", {
        class: "nova-tag",
        style: { fontSize: size + "px", color: t.color || "var(--accent)", opacity: 0.4 + Math.min(1, w) * 0.6 },
      }, t.label || "");
      cloud.append(tag);
    });
    wrap.append(cloud);
    return wrap;
  }

  // ---------- Divider ----------
  function divider(data) {
    const wrap = el("div", { class: "nova-divider-wrap" });
    if (data && data.label) {
      wrap.append(el("div", { class: "nova-divider-label" }, data.label));
    } else {
      wrap.append(el("hr", { class: "nova-divider" }));
    }
    return wrap;
  }

  // ---------- Register all ----------
  Nova.ComponentRegistry.registerComponent("timeline", timeline);
  Nova.ComponentRegistry.registerComponent("stat_grid", statGrid);
  Nova.ComponentRegistry.registerComponent("callout", callout);
  Nova.ComponentRegistry.registerComponent("gauge", gauge);
  Nova.ComponentRegistry.registerComponent("sparkline", sparkline);
  Nova.ComponentRegistry.registerComponent("keyvalue", keyvalue);
  Nova.ComponentRegistry.registerComponent("badge", badge);
  Nova.ComponentRegistry.registerComponent("quote", quote);
  Nova.ComponentRegistry.registerComponent("diff", diff);
  Nova.ComponentRegistry.registerComponent("heatmap", heatmap);
  Nova.ComponentRegistry.registerComponent("tagcloud", tagcloud);
  Nova.ComponentRegistry.registerComponent("divider", divider);

  Nova.Visuals = { timeline, statGrid, callout, gauge, sparkline, keyvalue, badge, quote, diff, heatmap, tagcloud, divider };
})();
