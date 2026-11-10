/* ==========================================================================
   Nova Render Engine — namespace, shared helpers, sanitization.
   Vanilla-JS modular rendering system (no build step). Each renderer is an
   IIFE that registers itself with the ComponentRegistry. Loaded via plain
   <script> tags before chat.js.

   Architecture:
     ns.js              -> this file: namespace + helpers
     ComponentRegistry  -> maps component/response types -> renderer fns
     ResponseClassifier -> inspects content, returns a render plan
     ResponseRenderer   -> main entry: render(plan, host, opts)
     *Renderer.js       -> individual component renderers
     StreamingRenderer  -> incremental rendering for SSE streams
   ========================================================================== */
(function () {
  "use strict";

  const Nova = (window.NovaRender = window.NovaRender || {});

  /* ---------- small DOM helpers ---------- */
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v == null || v === false) continue;
        if (k === "class") node.className = v;
        else if (k === "style" && typeof v === "object")
          Object.assign(node.style, v);
        else if (k === "dataset" && typeof v === "object")
          Object.assign(node.dataset, v);
        else if (k.startsWith("on") && typeof v === "function")
          node.addEventListener(k.slice(2).toLowerCase(), v);
        else if (k === "html") node.innerHTML = v;
        else node.setAttribute(k, v);
      }
    }
    for (const c of children) {
      if (c == null || c === false) continue;
      node.append(c.nodeType ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => {
      const code = c.charCodeAt(0);
      // & = 38, < = 60, > = 62, " = 34, ' = 39
      return "&#" + code + ";";
    });
  }

  // Sanitize HTML strings produced by renderers. Reuses DOMPurify when
  // available; otherwise passes through (renderers already produce safe
  // HTML from escaped content). The fallback is intentionally permissive
  // because all renderer inputs are escaped at the source.
  function sanitize(html) {
    if (window.DOMPurify) {
      return window.DOMPurify.sanitize(html, {
        USE_PROFILES: { html: true },
        ADD_ATTR: ["target", "rel"],
      });
    }
    return html;
  }

  // Debounced helper for scroll-aware auto-scroll.
  function nearBottom(scroller, threshold = 80) {
    const dist = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
    return dist <= threshold;
  }

  // Copy text to clipboard with a tiny visual confirmation on the trigger.
  function copy(text, btn) {
    const done = () => {
      if (btn) {
        const prev = btn.textContent;
        btn.textContent = "copied!";
        setTimeout(() => (btn.textContent = prev), 1200);
      }
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => fallback());
    } else {
      fallback();
    }
    function fallback() {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        done();
      } catch (_) {}
      ta.remove();
    }
  }

  // Convert an array of row objects to CSV text.
  function toCSV(rows) {
    if (!rows || !rows.length) return "";
    const keys = [...new Set(rows.flatMap((r) => Object.keys(r || {})))];
    const esc = (v) => {
      const s = String(v ?? "");
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    return [
      keys.join(","),
      ...rows.map((r) => keys.map((k) => esc(r[k])).join(",")),
    ].join("\n");
  }

  // Format a number for display (thousands separators, fixed precision).
  function fmtNum(v, unit) {
    if (v == null || v === "") return "";
    const n = Number(v);
    if (!isFinite(n)) return String(v);
    let s =
      Math.abs(n) >= 1000
        ? n.toLocaleString("en-US", { maximumFractionDigits: 2 })
        : String(n);
    if (!unit) return s;
    // Symbol units hug the number ("35%", "$1,200"); word units get a space.
    if (/^[%°]$|^°[CF]$/.test(unit)) return s + unit;
    if (/^[$€£¥₹]$/.test(unit)) return unit + s;
    return `${s} ${unit}`;
  }

  // Safe inline markdown for short component text: escapes first, then
  // applies **bold**, *italic*/_italic_, `code`, and http(s) links only.
  function inline(text) {
    if (text == null) return "";
    return esc(String(text))
      .replace(/`([^`]+)`/g, '<code class="ic">$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
      .replace(/(^|[^\w])_([^_\n]+)_(?!\w)/g, "$1<em>$2</em>")
      .replace(
        /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener">$1</a>'
      );
  }

  // Lazy-load a CDN script once, returns a promise resolving when ready.
  const _loaded = new Set();
  function loadScript(src, test) {
    if (test && test()) return Promise.resolve();
    if (_loaded.has(src)) return _loaded.get(src) || Promise.resolve();
    const p = new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = res;
      s.onerror = () => rej(new Error("failed to load " + src));
      document.head.appendChild(s);
    });
    _loaded.add(src);
    _loaded.set(src, p);
    return p;
  }

  // Lazy-load a CSS link once.
  function loadStyle(href) {
    if (_loaded.has(href)) return Promise.resolve();
    const p = new Promise((res) => {
      const l = document.createElement("link");
      l.rel = "stylesheet";
      l.href = href;
      l.onload = res;
      l.onerror = res;
      document.head.appendChild(l);
    });
    _loaded.add(href);
    _loaded.set(href, p);
    return p;
  }

  // Deep clone a plain JSON value safely.
  const clone = (v) => (v == null ? v : JSON.parse(JSON.stringify(v)));

  // ---- table normalization -------------------------------------------------
  // LLM output frequently wraps table cells across lines ("...of biology,"
  // -> "physics, and logic..."), which breaks GFM table parsing entirely.
  // This joins continuation lines into the owning row so each logical row is
  // a single line ending with "|".
  const isPipeLine = (l) => /^\s*\|/.test(l);
  const isSepLine = (l) => /^\s*\|[\s|:-]+\|\s*$/.test(l);
  const endsWithPipe = (l) => /\|\s*$/.test(l.trim());

  function normalizeTableRows(text) {
    const lines = text.split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const l = lines[i];
      // Table start: a pipe line followed by a separator row.
      if (isPipeLine(l) && i + 1 < lines.length && isSepLine(lines[i + 1])) {
        out.push(l, lines[i + 1]);
        i += 2;
        // Consume body rows. A logical row begins with "|" and continues on
        // following non-pipe lines until it ends with "|".
        while (i < lines.length) {
          const bl = lines[i];
          if (!bl.trim() || !isPipeLine(bl)) break;
          let row = bl;
          while (
            !endsWithPipe(row) &&
            i + 1 < lines.length &&
            lines[i + 1].trim() &&
            !isPipeLine(lines[i + 1])
          ) {
            i++;
            row += " " + lines[i].trim();
          }
          out.push(row);
          i++;
        }
        continue;
      }
      out.push(l);
      i++;
    }
    return out.join("\n");
  }

  // Decode the handful of entities `esc()` produces, for text that came from
  // already-escaped markdown (fallback renderer). Never applied to DOM text.
  function decodeEntities(s) {
    return String(s == null ? "" : s)
      .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
      .replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'").replace(/&nbsp;/g, " ");
  }

  // HTML tables arrive as markup; flatten to text. Cell boundaries become " | ".
  // Remove HTML tags only; entity decoding is done ONCE by the caller
  // (parseMdTable) so "&amp;" never becomes "&amp;amp;".
  function stripTags(s) {
    return String(s == null ? "" : s)
      .replace(/<br\s*\/?>/gi, " ")
      .replace(/<\/(td|th)>/gi, " | ")
      .replace(/<[^>]+>/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  // Parse normalized markdown-table text into {columns, rows} (rows = arrays).
  function parseMdTable(md) {
    const lines = normalizeTableRows(md).trim().split("\n").filter((l) => l.trim());
    if (lines.length < 2 || !isSepLine(lines[1])) return null;
    // Split on unescaped pipes; \| inside a cell is a literal pipe.
    const parse = (l) =>
      l.trim().replace(/^\||\|$/g, "").split(/(?<!\\)\|/).map((c) =>
        decodeEntities(stripTags(c.replace(/\\\|/g, "\u0001")).replace(/\u0001/g, "|").trim())
      );
    const head = parse(lines[0]);
    const body = lines
      .slice(2)
      .filter((l) => isPipeLine(l))
      .map(parse)
      .filter((r) => r.length && r.some((c) => c));
    return { columns: head, rows: body };
  }

  // Theme token helper — reads current theme from <html data-theme>.
  function theme() {
    return (
      document.documentElement.dataset.theme ||
      localStorage.getItem("ui-theme") ||
      "dark"
    );
  }

  Object.assign(Nova, {
    stripTags,
    decodeEntities,
    el,
    esc,
    sanitize,
    nearBottom,
    copy,
    toCSV,
    fmtNum,
    inline,
    loadScript,
    loadStyle,
    clone,
    theme,
    normalizeTableRows,
    parseMdTable,
    isPipeLine,
    isSepLine,
  });
})();
