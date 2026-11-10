/* ==========================================================================
   MarkdownRenderer — ChatGPT-style markdown rendering.

   - Uses marked + DOMPurify + highlight.js when CDNs are loaded.
   - Robust custom fallback (no external deps) so content always renders.
   - Renders inline + display math via KaTeX when available.
   - upgrade(root): post-processes a rendered DOM subtree — converts bare
     <pre><code> blocks into full CodeRenderer blocks, bare <table> elements
     into interactive TableRenderer tables, and decodes legacy fenced langs
     (table/cards/carousel/json/markdown) into structured components.

   Design goal: clean conversational typography. The bubble is the container.
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { sanitize, esc, normalizeTableRows, parseMdTable } = Nova;

  let _markedReady = false;

  function ensureMarked() {
    if (_markedReady || !window.marked) return false;
    try {
      const opts = { breaks: true, gfm: true };
      if (window.hljs) {
        opts.highlight = (code, lang) => {
          try {
            return window.hljs.getLanguage(lang)
              ? window.hljs.highlight(code, { language: lang }).value
              : window.hljs.highlightAuto(code).value;
          } catch (_) {
            return esc(code);
          }
        };
      }
      window.marked.setOptions(opts);
      _markedReady = true;
    } catch (_) {
      _markedReady = false;
    }
    return _markedReady;
  }

  // Math placeholders: printable sentinels that survive DOMPurify/marked.
  const MD = "@@NOVA_MATH_DISPLAY_";
  const MI = "@@NOVA_MATH_INLINE_";

  function renderMath(text) {
    const math = [];
    const stash = (tex, display) => {
      const i = math.length;
      math.push({ tex, display });
      return (display ? MD : MI) + i + "@@";
    };
    let out = text
      .replace(/\$\$([\s\S]+?)\$\$/g, (m, t) => stash(t, true))
      .replace(/\\\[([\s\S]+?)\\\]/g, (m, t) => stash(t, true))
      .replace(/\\\(([\s\S]+?)\\\)/g, (m, t) => stash(t, false))
      .replace(/\$([^\s$][^$\n]*?)\$/g, (m, t) => stash(t, false));
    return { text: out, math };
  }

  function restoreMath(html, math) {
    if (!math.length) return html;
    const render = (tex, display) => {
      if (window.katex) {
        try {
          return window.katex.renderToString(tex, {
            displayMode: display,
            throwOnError: false,
            output: "html",
          });
        } catch (_) {
          return esc(tex);
        }
      }
      return display
        ? `<div class="nova-math-fallback">${esc(tex)}</div>`
        : `<code class="ic">${esc(tex)}</code>`;
    };
    return html
      .replace(new RegExp(MD + "(\\d+)@@", "g"), (m, i) => render(math[+i].tex, true))
      .replace(new RegExp(MI + "(\\d+)@@", "g"), (m, i) => render(math[+i].tex, false));
  }

  // ---- custom fallback markdown (no marked) ----
  function fallbackMd(src) {
    let html = esc(src);
    html = html.replace(
      /```(\w*)\n?([\s\S]*?)```/g,
      (m, lang, code) =>
        `<div class="codeblock"><div class="codehead"><span>${lang || "code"}</span></div><pre><code>${code.replace(/^\n/, "")}</code></pre></div>`
    );
    // Tables go through TableRenderer (single source of truth for columns,
    // alignment, CSV/copy) instead of a hand-rolled table here.
    html = html.replace(/((?:^\|[^\n]+\|\s*$\n?){2,})/gm, (block) => {
      const parsed = parseMdTable(block);
      return parsed ? Nova.TableRenderer.render(parsed).outerHTML : block;
    });
    html = html.replace(/`([^`\n]+)`/g, '<code class="ic">$1</code>');
    html = html.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
    html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
    html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
    html = html.replace(/^# (.+)$/gm, "<h1>$1</h1>");
    html = html.replace(/^> (.+)$/gm, "<blockquote>$1</blockquote>");
    html = html.replace(/(?<!\w)_([^_\n]+)_(?!\w)/g, "<i>$1</i>");
    html = html.replace(/(?:^|\n)- ([^\n]+)/g, "\n<ul><li>$1</li></ul>");
    html = html.replace(/(?:^|\n)(\d+)\. ([^\n]+)/g, "\n<ol><li>$2</li></ol>");
    html = html.replace(/<\/ul>\n<ul>/g, "");
    html = html.replace(/<\/ol>\n<ol>/g, "");
    html = html.replace(
      /\[([^\]]+)\]\((https?:[^)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>'
    );
    html = html.split(/\n{2,}/).map((p) => {
      const t = p.trim();
      if (!t) return "";
      if (/^<(h\d|ul|ol|div|blockquote|table)/.test(t)) return t;
      return `<p>${p.replace(/\n/g, "<br>")}</p>`;
    }).join("");
    return html;
  }

  function render(text) {
    if (text == null) return "";
    if (typeof text !== "string") text = String(text);
    // Join table rows the model wrapped across lines before any parsing —
    // otherwise neither marked nor the fallback can see them as tables.
    text = normalizeTableRows(text);
    const { text: stashed, math } = renderMath(text);
    let html;
    if (window.marked && window.DOMPurify && ensureMarked()) {
      try {
        html = window.marked.parse(stashed, { breaks: true });
      } catch (_) {
        html = fallbackMd(stashed);
      }
    } else {
      html = fallbackMd(stashed);
    }
    html = sanitize(html);
    html = restoreMath(html, math);
    return html;
  }

  // ---------- DOM upgrade pass ----------
  // Called after innerHTML is set. Converts bare marked output into full
  // interactive components and restores legacy fenced-lang support.

  function specialFence(lang, code) {
    const L = (lang || "").toLowerCase();
    const txt = code.trim();
    // No language tag: guess from content — markdown table -> TableRenderer,
    // JSON object/array -> JsonViewer. Otherwise it's a normal code block.
    if (!L) {
      if (parseMdTable(txt)) return Nova.TableRenderer.render(parseMdTable(txt));
      if (/^[[{]/.test(txt)) {
        try {
          return Nova.JsonViewer.render({ data: JSON.parse(txt) });
        } catch (_) {
          return null;
        }
      }
      return null;
    }
    if (L === "table") {
      try {
        const j = JSON.parse(txt);
        const items = Array.isArray(j) ? j : j && (j.items || j.rows || null);
        if (items && items.length && typeof items[0] === "object")
          return Nova.TableRenderer.render({ rows: items });
      } catch (_) {}
      const parsed = parseMdTable(txt);
      if (parsed) return Nova.TableRenderer.render(parsed);
      return null; // fall through to plain code block
    }
    if (L === "json") {
      try {
        const j = JSON.parse(txt);
        const items = Array.isArray(j) ? j : j && (j.items || j.cards || null);
        if (items && items.length && typeof items[0] === "object") {
          const keys = [...new Set(items.flatMap((o) => Object.keys(o || {})))];
          if (keys.some((k) => /title|name|body|description/i.test(k)))
            return cardGrid(items);
        }
        return Nova.JsonViewer.render({ data: j });
      } catch (_) {
        return null;
      }
    }
    if (L === "cards" || L === "carousel") {
      try {
        const j = JSON.parse(txt);
        const items = Array.isArray(j) ? j : j && (j.items || j.cards || null);
        if (items && items.length && typeof items[0] === "object")
          return L === "carousel" ? carousel(items) : cardGrid(items);
      } catch (_) {
        return null;
      }
    }
    if (L === "markdown" || L === "md") {
      // The model fenced a whole markdown document — render it as markdown
      // rather than as a code block (common LLM quirk).
      const wrap = Nova.el("div", { class: "nova-prose" });
      wrap.innerHTML = render(txt);
      upgrade(wrap);
      return wrap;
    }
    if (L === "chart" || L === "nova-chart") {
      try {
        const j = JSON.parse(txt);
        return Nova.ChartRenderer.render(j);
      } catch (_) {
        return null;
      }
    }
    return null;
  }

  function cardGrid(items) {
    const { el } = Nova;
    const grid = el("div", {
      class: "nova-card-grid",
      style: {
        display: "grid",
        gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))",
        gap: "10px",
        margin: "10px 0",
      },
    });
    items.forEach((it) => {
      const card = el("div", { class: "ui-card ui-card-pad" });
      if (it.icon) card.append(el("div", { style: { fontSize: "18px", marginBottom: "5px" } }, it.icon));
      card.append(el("b", { style: { display: "block", fontSize: "12.5px", marginBottom: "3px" } }, it.title || it.name || ""));
      card.append(el("div", { class: "t-xs tx-2" }, it.body || it.description || ""));
      if (it.badge) card.append(el("span", { class: "pill info", style: { marginTop: "6px" } }, it.badge));
      grid.append(card);
    });
    return grid;
  }

  function carousel(items) {
    const { el } = Nova;
    const wrap = el("div", { class: "rich-wrap" });
    const head = el("div", { class: "codehead" });
    head.append(el("span", {}, `carousel · ${items.length} items`));
    const nav = el("span");
    [["‹", -1], ["›", 1]].forEach(([g, d]) => {
      const b = el("button", { class: "car-btn" }, g);
      b.onclick = () => track.scrollBy({ left: 220 * d, behavior: "smooth" });
      nav.append(b);
    });
    head.append(nav);
    wrap.append(head);
    const track = el("div", {
      class: "carousel",
      style: { display: "flex", gap: "10px", overflowX: "auto", scrollSnapType: "x mandatory", padding: "10px 2px" },
    });
    items.forEach((it) => {
      const card = el("div", {
        class: "ui-card ui-card-pad",
        style: { minWidth: "200px", flex: "none", scrollSnapAlign: "start" },
      });
      if (it.icon) card.append(el("div", { style: { fontSize: "20px", marginBottom: "6px" } }, it.icon));
      card.append(el("b", { style: { display: "block", fontSize: "13px", marginBottom: "4px" } }, it.title || it.name || ""));
      card.append(el("div", { class: "t-xs tx-2" }, it.body || it.description || ""));
      if (it.badge) card.append(el("span", { class: "pill info", style: { marginTop: "6px" } }, it.badge));
      track.append(card);
    });
    wrap.append(track);
    return wrap;
  }

  // Extract language from marked's <code class="language-x"> or hljs class.
  function langFromCode(codeEl) {
    const m = (codeEl.className || "").match(/language-([\w-]+)/);
    return m ? m[1] : "";
  }

  // Upgrade all bare <pre><code> and <table> elements inside root.
  function upgrade(root) {
    if (!root || !root.querySelectorAll) return root;

    // 1) fenced code -> CodeRenderer (or special fence renderer)
    root.querySelectorAll("pre").forEach((pre) => {
      if (pre.closest(".nova-codeblock, .codeblock")) return;
      const codeEl = pre.querySelector("code");
      if (!codeEl) return;
      const lang = langFromCode(codeEl);
      const raw = codeEl.textContent || "";
      let node = null;
      try {
        node = specialFence(lang, raw);
      } catch (_) {}
      if (!node) node = Nova.CodeRenderer.render({ language: lang, code: raw });
      pre.parentNode && pre.parentNode.replaceChild(node, pre);
    });

    // 2) bare <table> -> interactive TableRenderer (skip already-upgraded)
    root.querySelectorAll("table").forEach((table) => {
      if (table.classList.contains("nova-table") || table.closest(".nova-table-wrap")) return;
      // DOM textContent is already entity-decoded exactly once; TableRenderer
      // renders with textContent, so passing it straight through never
      // double-encodes "&amp;" -> "&amp;amp;". (Do NOT round-trip through
      // parseMdTable here: its entity decoding is for markdown pipe tables.)
      const headCells = [...table.querySelectorAll("thead th")];
      const bodyTrs = [...table.querySelectorAll("tbody tr")];
      let columns = headCells.map((th) => th.textContent.trim());
      let rowEls = bodyTrs;
      if (!bodyTrs.length) {
        // No thead/tbody (hand-written HTML): first row of th (or td) is the header.
        const allTrs = [...table.querySelectorAll("tr")];
        const first = allTrs[0];
        if (first && first.querySelector("th")) {
          columns = [...first.querySelectorAll("th")].map((th) => th.textContent.trim());
          rowEls = allTrs.slice(1);
        } else {
          rowEls = allTrs;
        }
      }
      const rows = rowEls.map((tr) =>
        [...tr.querySelectorAll("td")].map((td) => td.textContent.trim())
      );
      if (!columns.length && !rows.length) return;
      const node = Nova.TableRenderer.render({ columns, rows });
      table.parentNode && table.parentNode.replaceChild(node, table);
    });
    return root;
  }

  // Response handler: render markdown into a host element (with upgrade).
  function responseHandler(plan, host) {
    const text = plan.text != null ? plan.text : plan.prose || "";
    host.innerHTML = render(text);
    upgrade(host);
    return host;
  }

  Nova.MarkdownRenderer = { render, upgrade, responseHandler, specialFence };
  Nova.ComponentRegistry.registerResponse("general", responseHandler);
})();
