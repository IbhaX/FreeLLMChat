/* ==========================================================================
   TextbookRenderer — renders a ResearchDocument (see app/services/research_doc.py)
   into the illustrated-textbook view. Pure vanilla JS, no framework, no CDN —
   charts are inline SVG so the same markup prints identically into the PDF.

   window.NovaTextbook.render(doc, host) -> builds cover, TOC, chapters,
   glossary, quiz, flashcards, references and wires the interactions.
   ========================================================================== */
(function () {
  "use strict";

  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.prototype.slice.call((r || document).querySelectorAll(s));

  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      if (k === "class") e.className = attrs[k];
      else if (k === "html") e.innerHTML = attrs[k];
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), attrs[k]);
      else if (attrs[k] != null) e.setAttribute(k, attrs[k]);
    }
    kids.flat().forEach((k) => {
      if (k == null) return;
      e.append(k.nodeType ? k : document.createTextNode(String(k)));
    });
    return e;
  }
  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

  /* Minimal inline markdown: **bold**, *italic*, `code`, [label](url). */
  function md(s) {
    let t = esc(s);
    t = t.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
         .replace(/\*([^*]+)\*/g, "<i>$1</i>")
         .replace(/`([^`]+)`/g, "<code>$1</code>")
         .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
           '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return t;
  }

  const PALETTE = {
    navy: "#243B64", sky: "#79C9ED", mint: "#A9DCC8",
    yellow: "#FFE49A", coral: "#F4A58C", lavender: "#C6B6EB",
  };
  const CHART_COLORS = ["#243B64", "#79C9ED", "#F4A58C", "#A9DCC8", "#C6B6EB", "#E9BC4E"];

  const CALLOUTS = {
    key_concept:   { c: "c-sky",    ico: "💡", lab: "Key concept" },
    highlight_box: { c: "c-yellow", ico: "⭐", lab: "Highlight" },
    key_takeaway:  { c: "c-mint",   ico: "✔", lab: "Remember this" },
    remember_this: { c: "c-mint",   ico: "✔", lab: "Remember this" },
    did_you_know:  { c: "c-lav",    ico: "🔍", lab: "Did you know?" },
    important_note:{ c: "c-coral",  ico: "❗", lab: "Important" },
    warning_box:   { c: "c-coral",  ico: "⚠", lab: "Warning" },
    real_world:    { c: "c-mint",   ico: "🌍", lab: "Real-world connection" },
    lets_understand:{ c: "c-navy",  ico: "🧩", lab: "Let's understand" },
    think_about:   { c: "c-lav",    ico: "🤔", lab: "Think about it" },
    quick_check:   { c: "c-yellow", ico: "✎", lab: "Quick check" },
    case_study:    { c: "c-coral",  ico: "📖", lab: "Case study" },
  };

  /* ---------------- block renderers ---------------- */

  function callout(b) {
    const d = b.data || {};
    const cfg = CALLOUTS[b.type] || CALLOUTS.highlight_box;
    const card = el("div", { class: "tb-card " + cfg.c });
    card.append(el("div", { class: "ico" }, cfg.ico));
    const ct = el("div", { class: "ct" });
    ct.append(el("div", { class: "lab" }, cfg.lab));
    if (d.title) ct.append(el("div", { class: "tt2", html: md(d.title) }));
    const text = d.text || d.question || d.answer || "";
    if (b.type === "quick_check") {
      card.classList.add("tb-qc");
      if (d.question) ct.append(el("div", { class: "tx", html: md(d.question) }));
      if (d.answer) {
        ct.append(el("button", {
          class: "rev", type: "button",
          onclick: (e) => {
            const a = $(".ans", card); a.classList.toggle("show");
            e.currentTarget.textContent = a.classList.contains("show") ? "Hide answer" : "Reveal answer";
          },
        }, "Reveal answer"));
        ct.append(el("div", { class: "ans", html: md(d.answer) }));
      }
    } else if (b.type === "lets_understand" && Array.isArray(d.steps) && d.steps.length) {
      if (text) ct.append(el("div", { class: "tx", html: md(text) }));
      ct.append(el("ol", { class: "steps" }, d.steps.map((s) => el("li", { html: md(s) }))));
    } else if (text) {
      ct.append(el("div", { class: "tx", html: md(text) }));
    }
    card.append(ct);
    return card;
  }

  function table(b, comparison) {
    const d = b.data || {};
    const rows = Array.isArray(d.rows) ? d.rows : [];
    if (!rows.length) return null;
    const cols = Array.isArray(d.columns) && d.columns.length
      ? d.columns : Object.keys(rows[0] || {});
    const wrap = el("div", { class: "tb-tblwrap" });
    if (d.title) wrap.append(el("div", { class: "cap-top", html: md(d.title) }));
    const t = el("table", { class: "tb-table" });
    t.append(el("tr", null, cols.map((c) => el("th", { html: md(c) }))));
    rows.forEach((r) => {
      const cells = Array.isArray(r) ? r : cols.map((c) => r[c]);
      t.append(el("tr", null, cells.map((c) => el("td", { html: md(c) }))));
    });
    wrap.append(t);
    return wrap;
  }

  /* Inline SVG chart — bar, line, pie/donut, area, scatter. */
  function chart(b) {
    const d = b.data || {};
    const rows = Array.isArray(d.data) ? d.data : [];
    if (!rows.length) return null;
    const type = (d.chart_type || "bar").toLowerCase();
    const first = rows[0] || {};
    const keys = Object.keys(first);
    const labelKey = keys.find((k) => typeof first[k] !== "number") || keys[0];
    const series = keys.filter((k) => k !== labelKey)
      .map((k) => ({ name: k, vals: rows.map((r) => Number(r[k]) || 0) }));
    if (!series.length) return null;
    const labels = rows.map((r) => String(r[labelKey] ?? ""));

    const W = 640, H = 240, padL = 42, padB = 34, padT = 12, padR = 12;
    const iw = W - padL - padR, ih = H - padT - padB;
    const all = series.flatMap((s) => s.vals);
    const vmax = Math.max(1, ...all.map((v) => Math.abs(v))) * 1.08;
    const svg = [`<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" font-family="Nunito,Inter,sans-serif">`];
    const fmt = (v) => Math.abs(v) >= 1000 ? `${Math.round(v / 100) / 10}k` : `${+v.toFixed(2)}`;
    [0, 0.25, 0.5, 0.75, 1].forEach((f) => {
      const y = padT + ih * (1 - f);
      svg.push(`<line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="rgba(36,59,100,.12)" stroke-width="1"/>`);
      svg.push(`<text x="${padL - 6}" y="${y + 4}" font-size="9.5" fill="#5A6478" text-anchor="end">${fmt(vmax * f)}</text>`);
    });

    const legend = [];
    if (type === "pie" || type === "donut") {
      const cx = 140, cy = 128, R = 96, r0 = type === "donut" ? 52 : 0;
      const total = series[0].vals.reduce((a, v) => a + v, 0) || 1;
      let ang = -Math.PI / 2;
      series[0].vals.forEach((v, i) => {
        const a2 = ang + (v / total) * Math.PI * 2;
        const large = a2 - ang > Math.PI ? 1 : 0;
        const x1 = cx + R * Math.cos(ang), y1 = cy + R * Math.sin(ang);
        const x2 = cx + R * Math.cos(a2), y2 = cy + R * Math.sin(a2);
        const inner = r0
          ? `L ${cx + r0 * Math.cos(a2)} ${cy + r0 * Math.sin(a2)} A ${r0} ${r0} 0 ${large} 0 ${cx + r0 * Math.cos(ang)} ${cy + r0 * Math.sin(ang)} Z`
          : "Z";
        svg.push(`<path d="M ${cx} ${cy} L ${x1} ${y1} A ${R} ${R} 0 ${large} 1 ${x2} ${y2} ${inner}" fill="${CHART_COLORS[i % 6]}"/>`);
        legend.push({ c: CHART_COLORS[i % 6], n: `${labels[i]} (${fmt(v)})` });
        ang = a2;
      });
      legend.forEach((l, i) => {
        svg.push(`<rect x="270" y="${60 + i * 20}" width="10" height="10" rx="3" fill="${l.c}"/>`);
        svg.push(`<text x="286" y="${69 + i * 20}" font-size="10.5" fill="#273247">${esc(l.n)}</text>`);
      });
    } else if (type === "line" || type === "area" || type === "scatter") {
      const n = labels.length || 1;
      const px = (i) => padL + (n === 1 ? iw / 2 : (iw * i) / (n - 1));
      const py = (v) => padT + ih * (1 - v / vmax);
      series.forEach((s, si) => {
        const col = CHART_COLORS[si % 6];
        const pts = s.vals.map((v, i) => `${px(i)},${py(v)}`).join(" ");
        if (type === "area")
          svg.push(`<polygon points="${padL},${py(0)} ${pts} ${px(s.vals.length - 1)},${py(0)}" fill="${col}33"/>`);
        if (type !== "scatter")
          svg.push(`<polyline points="${pts}" fill="none" stroke="${col}" stroke-width="2.5" stroke-linecap="round"/>`);
        s.vals.forEach((v, i) =>
          svg.push(`<circle cx="${px(i)}" cy="${py(v)}" r="4" fill="${col}"/>`));
        legend.push({ c: col, n: s.name });
      });
      labels.forEach((l, i) => svg.push(
        `<text x="${px(i)}" y="${H - 14}" font-size="9.5" fill="#5A6478" text-anchor="middle">${esc(l)}</text>`));
    } else { /* bar */
      const n = labels.length || 1, groups = series.length;
      const gw = iw / n, bw = Math.min(34, (gw * 0.7) / groups);
      labels.forEach((l, i) => {
        series.forEach((s, si) => {
          const col = CHART_COLORS[si % 6];
          const h = (Math.abs(s.vals[i]) / vmax) * ih;
          const x = padL + gw * i + (gw - bw * groups) / 2 + bw * si;
          svg.push(`<rect x="${x}" y="${padT + ih - h}" width="${bw - 3}" height="${h}" rx="4" fill="${col}"/>`);
        });
        svg.push(`<text x="${padL + gw * i + gw / 2}" y="${H - 14}" font-size="9.5" fill="#5A6478" text-anchor="middle">${esc(l)}</text>`);
      });
      series.forEach((s, si) => legend.push({ c: CHART_COLORS[si % 6], n: s.name }));
    }
    svg.push("</svg>");

    const wrap = el("div", { class: "tb-chart" });
    if (d.title) wrap.append(el("div", { class: "cap-top", html: md(d.title) }));
    wrap.append(el("div", { html: svg.join("") }));
    if (legend.length > 1 || type === "pie" || type === "donut") {
      wrap.append(el("div", { class: "legend" },
        legend.map((l) => el("span", { html: `<i style="background:${l.c}"></i>${esc(l.n)}` }))));
    }
    return wrap;
  }

  function stats(b) {
    const d = b.data || {};
    const list = Array.isArray(d.stats) ? d.stats : [];
    if (!list.length) return null;
    const g = el("div", { class: "tb-stats" });
    if (d.title) g.append(el("div", { class: "cap-top", html: md(d.title) }));
    list.forEach((s) => {
      const c = el("div", { class: "tb-stat" });
      c.append(el("div", { class: "v", html: md(`${s.value ?? ""}${s.unit || ""}`) }));
      if (s.label) c.append(el("div", { class: "l", html: md(s.label) }));
      if (s.delta != null) c.append(el("div", { class: "d", html: md(s.delta) }));
      g.append(c);
    });
    return g;
  }

  function timeline(b) {
    const d = b.data || {};
    const evs = Array.isArray(d.events) ? d.events : [];
    if (!evs.length) return null;
    const wrap = el("div", null);
    if (d.title) wrap.append(el("div", { class: "cap-top", style: "font:800 12.5px var(--tb-disp);color:var(--tb-navy);margin-bottom:10px", html: md(d.title) }));
    const tl = el("div", { class: "tb-tl" });
    evs.forEach((e2) => {
      const ev = el("div", { class: "ev" });
      if (e2.date) ev.append(el("div", { class: "dt" }, e2.date));
      if (e2.title) ev.append(el("div", { class: "et", html: md(e2.title) }));
      if (e2.description) ev.append(el("div", { class: "ed", html: md(e2.description) }));
      tl.append(ev);
    });
    wrap.append(tl);
    return wrap;
  }

  function flowchart(b) {
    const d = b.data || {};
    const steps = Array.isArray(d.steps) ? d.steps : [];
    if (!steps.length) return null;
    const wrap = el("div", { class: "tb-flow" });
    if (d.title) wrap.append(el("div", { class: "cap-top", html: md(d.title) }));
    steps.forEach((s, i) => {
      wrap.append(el("div", { class: "st" },
        el("div", { class: "n" }, i + 1), el("div", { class: "box", html: md(s) })));
      if (i < steps.length - 1) wrap.append(el("div", { class: "arr" }, "↓"));
    });
    return wrap;
  }

  function illustration(b, lightbox) {
    const d = b.data || {};
    const fig = el("figure", { class: "tb-illus" });
    const art = el("div", { class: "art" });
    if (d.url) {
      art.append(el("img", {
        class: "art-img", src: d.url, alt: d.caption || "illustration",
        loading: "lazy",
        onclick: () => lightbox(d.url),
      }));
    } else {
      /* Decorative generated plate: soft gradient + geometry. Honest
         placeholder — labeled so it never pretends to be a real image. */
      const hues = [["#79C9ED", "#243B64"], ["#A9DCC8", "#3D8A6D"],
        ["#FFE49A", "#E9BC4E"], ["#C6B6EB", "#6D54A8"], ["#F4A58C", "#B45B42"]];
      const seed = (d.description || d.caption || "").length % hues.length;
      const [c1, c2] = hues[seed];
      art.innerHTML = `<svg viewBox="0 0 640 190" xmlns="http://www.w3.org/2000/svg">
        <defs><linearGradient id="g${seed}" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="${c1}"/><stop offset="1" stop-color="${c2}"/></linearGradient></defs>
        <rect width="640" height="190" fill="url(#g${seed})" opacity=".35"/>
        <circle cx="120" cy="95" r="58" fill="${c1}" opacity=".5"/>
        <circle cx="120" cy="95" r="34" fill="${c2}" opacity=".45"/>
        <rect x="420" y="48" width="120" height="96" rx="18" fill="${c2}" opacity=".4" transform="rotate(8 480 96)"/>
        <circle cx="540" cy="140" r="30" fill="${c1}" opacity=".55"/>
        <path d="M 240 140 Q 300 60 360 130 T 480 120" stroke="${c2}" stroke-width="4" fill="none" opacity=".6"/>
      </svg>`;
    }
    fig.append(art);
    if (d.caption || d.description)
      fig.append(el("figcaption", { html: md(d.caption || d.description) }));
    return fig;
  }

  function gallery(b, lightbox) {
    const d = b.data || {};
    const imgs = Array.isArray(d.images) ? d.images : [];
    if (!imgs.length) return null;
    const g = el("div", { class: "tb-gal" });
    imgs.forEach((im) => {
      const f = el("figure", null);
      if (im.url) f.append(el("img", {
        src: im.url, alt: im.caption || "", loading: "lazy",
        onclick: () => lightbox(im.url),
      }));
      if (im.caption) f.append(el("figcaption", { html: md(im.caption) }));
      g.append(f);
    });
    return g;
  }

  function infographic(b) {
    const d = b.data || {};
    const cells = Array.isArray(d.cells) ? d.cells
      : Array.isArray(d.items) ? d.items : [];
    if (!cells.length) return null;
    const pal = ["c-sky", "c-mint", "c-yellow", "c-coral", "c-lav"];
    const g = el("div", { class: "tb-info" });
    cells.forEach((c, i) => {
      g.append(el("div", { class: "cell " + pal[i % pal.length], style: "border:1px solid var(--tb-line)" },
        el("div", { class: "bi" }, c.icon || "•"),
        el("div", { class: "bt", html: md(c.title || "") }),
        el("div", { class: "bx", html: md(c.text || "") })));
    });
    return g;
  }

  function diagram(b) {
    const d = b.data || {};
    const wrap = el("div", { class: "tb-diag" });
    if (d.title) wrap.append(el("div", { class: "cap-top", html: md(d.title) }));
    if (d.description) wrap.append(el("div", { class: "dd", html: md(d.description) }));
    const lbs = Array.isArray(d.labels) ? d.labels : [];
    if (lbs.length)
      wrap.append(el("div", { class: "lbs" }, lbs.map((l) => el("span", { html: md(l) }))));
    return wrap;
  }

  function block(b, lightbox) {
    const d = (b && b.data) || {};
    switch (b && b.type) {
      case "chapter_intro": case "section":
        if (b.type === "section") {
          const s = el("div", { class: "tb-sec" });
          if (d.title) s.append(el("div", { class: "sec-t", html: md(d.title) }));
          if (d.text) s.append(el("p", { class: "tb-p", html: md(d.text) }));
          return s;
        }
        return el("p", { class: "tb-p", html: md(d.text || "") });
      case "heading":
        return el("div", { class: `tb-h l${Math.min(4, d.level || 3)}`, html: md(d.text || "") });
      case "paragraph":
        return el("p", { class: "tb-p", html: md(d.text || "") });
      case "list": {
        const items = Array.isArray(d.items) ? d.items : [];
        if (!items.length) return null;
        const li = items.map((i) => el("li", { html: md(i) }));
        return el(d.ordered ? "ol" : "ul", { class: "tb-list" }, li);
      }
      case "table": return table(b);
      case "comparison_table": return table(b, true);
      case "chart": return chart(b);
      case "statistics_card": return stats(b);
      case "timeline": return timeline(b);
      case "flowchart": return flowchart(b);
      case "diagram": return diagram(b);
      case "image_illustration": return illustration(b, lightbox);
      case "image_gallery": return gallery(b, lightbox);
      case "infographic": return infographic(b);
      case "quote_card": {
        const q = el("div", { class: "tb-quote" });
        q.append(el("div", { class: "q", html: "“" + md(d.text || "") + "”" }));
        if (d.author) q.append(el("div", { class: "a" }, d.author));
        return q;
      }
      case "formula_card": {
        const f = el("div", { class: "tb-formula" });
        f.append(el("div", { class: "f", html: md(d.expression || "") }));
        if (d.label) f.append(el("div", { class: "l", html: md(d.label) }));
        return f;
      }
      case "code_block": {
        const c = el("div", { class: "tb-code" });
        c.append(el("div", { class: "hd" },
          el("span", null, d.language || "code"),
          el("span", null, d.filename || "")));
        c.append(el("pre", null, el("code", null, d.code || "")));
        return c;
      }
      case "vocabulary_card": {
        return el("div", { class: "tb-vocab" },
          el("div", { class: "term", html: md(d.term || "") }),
          el("div", { class: "def", html: md(d.definition || "") }));
      }
      case "summary": {
        const pts = Array.isArray(d.points) ? d.points : [];
        const s = el("div", { class: "tb-summ" });
        s.append(el("div", { class: "cap-top" }, d.title || "Summary"));
        if (d.text) s.append(el("p", { class: "tb-p", html: md(d.text) }));
        if (pts.length) s.append(el("ul", { class: "tb-list" }, pts.map((p) => el("li", { html: md(p) }))));
        return s;
      }
      default:
        if (CALLOUTS[b.type]) return callout(b);
        if (d.text) return el("p", { class: "tb-p", html: md(d.text) });
        return null;
    }
  }

  /* ---------------- document assembly ---------------- */

  /* One chapter — same rich output as render(): banner, colored panel,
   * callouts, charts, tables, illustrations. Exported for progressive
   * per-chapter rendering during composition. */
  function renderChapter(ch, host, lightbox) {
    const lb = lightbox || ((url) => {
      let box = $(".tb-lb");
      if (!box) { box = el("div", { class: "tb-lb", onclick: () => box.classList.remove("on") }); document.body.append(box); }
      box.innerHTML = ""; box.append(el("img", { src: url }));
      box.classList.add("on");
    });
    const color = PALETTE[ch.color] ? "c-" + ch.color
      : "c-" + ["navy", "sky", "mint", "yellow", "coral", "lavender"][(ch.number - 1) % 6];
    const sec = el("section", { class: "tb-ch " + color, id: ch.id });
    sec.append(el("div", { class: "tb-ch-banner" },
      el("div", { class: "num" }, "Chapter " + ch.number),
      el("div", { class: "tt" }, el("h2", null, ch.title))));
    if (ch.intro) sec.append(el("p", { class: "tb-ch-intro", html: md(ch.intro) }));
    (ch.blocks || []).forEach((b) => {
      const n = block(b, lb);
      if (n) sec.append(n);
      (b.blocks || []).forEach((nb) => { const nn = block(nb, lb); if (nn) sec.append(nn); });
    });
    if (host) host.append(sec);
    return sec;
  }

  function render(doc, host, opts) {
    opts = opts || {};
    host.innerHTML = "";
    const lightbox = (url) => {
      let lb = $(".tb-lb");
      if (!lb) { lb = el("div", { class: "tb-lb", onclick: () => lb.classList.remove("on") }); document.body.append(lb); }
      lb.innerHTML = ""; lb.append(el("img", { src: url }));
      lb.classList.add("on");
    };

    /* cover */
    const cov = doc.cover || {};
    const cover = el("div", { class: "tb-cover" },
      el("div", { class: "deco d1" }), el("div", { class: "deco d2" }),
      el("div", { class: "deco d3" }), el("div", { class: "deco sq" }),
      el("div", { class: "tb-kicker" }, "iChat · Illustrated research"),
      el("h1", null, cov.title || doc.title || "Research report"),
      cov.subtitle || doc.subtitle ? el("div", { class: "sub", html: md(cov.subtitle || doc.subtitle) }) : null,
      el("div", { class: "meta" },
        doc.topic || cov.topic ? el("span", null, cov.topic || doc.topic) : null,
        el("span", null, (cov.level || doc.level || "introductory") + " level"),
        el("span", null, `${cov.reading_minutes || doc.reading_minutes || "≈10"} min read`)));
    host.append(cover);

    /* in-book TOC */
    const toc = doc.toc && doc.toc.length ? doc.toc
      : (doc.chapters || []).map((c, i) => ({ id: c.id, title: c.title, number: c.number || i + 1, summary: c.intro }));
    if (toc.length) {
      const tp = el("div", { class: "tb-tocpage" });
      tp.append(el("h2", null, "Contents"));
      toc.forEach((t) => {
        tp.append(el("a", { href: "#" + t.id },
          el("span", { class: "n" }, String(t.number || "")),
          el("span", { class: "t" }, t.title),
          t.summary ? el("span", { class: "s" }, t.summary) : null));
      });
      host.append(tp);
    }

    /* chapters */
    (doc.chapters || []).forEach((ch) => renderChapter(ch, host, lightbox));

    /* closing sections */
    if (doc.glossary && doc.glossary.length) {
      host.append(el("div", { class: "tb-sechead", id: "glossary" }, "Glossary"));
      host.append(el("div", { class: "tb-gloss" },
        doc.glossary.map((g) => el("div", { class: "ge" },
          el("div", { class: "t", html: md(g.term) }),
          el("div", { class: "d", html: md(g.definition) })))));
    }
    if (doc.flashcards && doc.flashcards.length) {
      host.append(el("div", { class: "tb-sechead" }, "Flashcards"));
      host.append(el("div", { class: "tb-flash" },
        doc.flashcards.map((f) => {
          const fc = el("div", { class: "tb-fc", onclick: () => fc.classList.toggle("flip") });
          fc.append(el("div", { class: "in" },
            el("div", { class: "fa", html: md(f.front) }),
            el("div", { class: "fb", html: md(f.back) })));
          return fc;
        })));
    }
    if (doc.quiz && doc.quiz.length) {
      host.append(el("div", { class: "tb-sechead", id: "quiz" }, "Knowledge check"));
      const qz = el("div", { class: "tb-quiz" });
      doc.quiz.forEach((q, qi) => {
        const box = el("div", { class: "qz" });
        box.append(el("div", { class: "qq" }, `${qi + 1}. ${q.question}`));
        (q.options || []).forEach((op, oi) => {
          box.append(el("button", {
            class: "qo", type: "button",
            onclick: (e) => {
              $$(".qo", box).forEach((o) => o.disabled = true);
              e.currentTarget.classList.add(oi === q.answer_index ? "ok" : "no");
              if (oi !== q.answer_index) $$(".qo", box)[q.answer_index]?.classList.add("ok");
              $(".qe", box)?.classList.add("show");
            },
          }, op));
        });
        if (q.explanation) box.append(el("div", { class: "qe", html: md(q.explanation) }));
        qz.append(box);
      });
      host.append(qz);
    }
    if (doc.references && doc.references.length) {
      host.append(el("div", { class: "tb-sechead", id: "references" }, "References"));
      host.append(el("ol", { class: "tb-refs" },
        doc.references.map((r) => {
          const li = el("li", null);
          if (r.url) li.append(el("a", { href: r.url, target: "_blank", rel: "noopener" }, r.title || r.url));
          else li.append(el("span", null, r.title || ""));
          if (r.source) li.append(el("span", { class: "src" }, " · " + r.source));
          return li;
        })));
    }
    host.append(el("div", { class: "tb-end" },
      el("div", { class: "mark" }, "✦"),
      el("div", { class: "cap-top" }, "End of report"),
      el("div", { style: "font-size:12px;margin-top:6px" }, "Generated by iChat research")));

    return { toc };
  }

  /* TOC rail + scroll-spy + progress — screen only. */
  function chrome(doc, renderRes) {
    const rail = $(".tb-rail .tb-toc");
    if (rail) {
      rail.innerHTML = "";
      renderRes.toc.forEach((t) => {
        const a = el("a", { href: "#" + t.id },
          el("span", { class: "n" }, String(t.number || "")), t.title);
        rail.append(a);
      });
      const links = $$("a", rail);
      const secs = renderRes.toc.map((t) => document.getElementById(t.id)).filter(Boolean);
      const spy = () => {
        let cur = 0;
        secs.forEach((s, i) => { if (s.getBoundingClientRect().top < 140) cur = i; });
        links.forEach((l, i) => l.classList.toggle("on", i === cur));
      };
      window.addEventListener("scroll", spy, { passive: true });
      spy();
    }
    const bar = $(".tb-progress");
    if (bar) {
      const prog = () => {
        const h = document.documentElement;
        bar.style.width = `${(h.scrollTop / Math.max(1, h.scrollHeight - h.clientHeight)) * 100}%`;
      };
      window.addEventListener("scroll", prog, { passive: true });
      prog();
    }
  }

  window.NovaTextbook = { render, chrome, renderChapter };
})();
