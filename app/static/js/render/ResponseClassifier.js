/* ==========================================================================
   ResponseClassifier — determines the render strategy for AI content.

   Strategy (priority order):
     1. Structured envelope: a ```nova-response fenced block containing JSON
        that matches the backend protocol. Parsed into a structured plan.
     2. Content-analysis fallback: inspect the markdown for tables, code
        fences, math, lists, JSON, etc. and build a plan that mixes the
        MarkdownRenderer with specialized renderers.

   The classifier NEVER executes model-supplied HTML/JS — it only produces
   a *plan* (plain data) that the ResponseRenderer turns into DOM via the
   allowlisted ComponentRegistry.
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { el, esc } = Nova;

  const FENCE_TAG = "nova-response";

  // ---------- structured envelope detection ----------
  // Matches a fenced block whose language tag is nova-response. Captures the
  // inner JSON. Allows trailing/leading whitespace and other markdown around.
  // Accept canonical and line-wrapped tags (```nova\\n-response).
  // Fence parsing uses explicit patterns below.
  // Accept canonical, line-broken, and malformed split tags. In the
  // malformed form "-response" may be the first line inside the code fence.
  const FENCE_RE = /```(?:nova-response|nova[ \t]*\r?\n[ \t]*-response|nova)[ \t]*\r?\n([\s\S]*?)\r?\n```/i;
  const FENCE_OPEN_RE = /```(?:nova-response|nova[ \t]*\r?\n[ \t]*-response|nova)[ \t]*\r?\n([\s\S]*)$/i;
  // Some models wrap the envelope in a plain ```json fence — accept it
  // when the inner JSON is a valid envelope (has response_type).
  const JSON_FENCE_RE = /```json\s*\n([\s\S]*?)\n```/;
  const JSON_FENCE_OPEN_RE = /```json\s*\n([\s\S]*)$/;

  function parseEnvelopeJson(inner, match) {
    try {
      const cleaned = inner.trim().replace(/^-response\s*/i, "");
      const obj = JSON.parse(cleaned);
      if (!obj || typeof obj !== "object" || !obj.response_type) return null;
      return { envelope: obj, match };
    } catch (_) {
      return null;
    }
  }

  function tryParseEnvelope(text) {
    if (typeof text !== "string") return null;
    // Some providers or middleware strip Markdown fences. Treat a complete
    // JSON object as a Nova envelope only when it declares response_type.
    const trimmed = text.trim();
    if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
      const offset = text.indexOf(trimmed);
      const bare = parseEnvelopeJson(trimmed, { index: offset, 0: trimmed });
      if (bare) return bare;
    }
    const m = text.match(FENCE_RE) || text.match(JSON_FENCE_RE);
    if (m) return parseEnvelopeJson(m[1], m);
    const m2 = text.match(FENCE_OPEN_RE) || text.match(JSON_FENCE_OPEN_RE);
    if (!m2) return null;
    const inner = m2[1];
    // try whole inner, then progressively trimmed to the last '}' so
    // trailing prose after the JSON doesn't break detection
    let r = parseEnvelopeJson(inner, m2);
    if (r) return r;
    const last = inner.lastIndexOf("}");
    if (last > 0) r = parseEnvelopeJson(inner.slice(0, last + 1), m2);
    return r;
  }

  // Strip the envelope fence from text, returning the surrounding prose.
  function stripEnvelope(text, match) {
    return (text.slice(0, match.index) + text.slice(match.index + match[0].length)).trim();
  }

  // ---------- content-analysis fallback ----------
  // Lightweight structural probes — no keyword guessing as the primary
  // signal; we look at actual markdown structure.

  function hasMarkdownTable(text) {
    const lines = text.split("\n");
    for (let i = 0; i < lines.length - 1; i++) {
      if (/^\|.*\|\s*$/.test(lines[i]) && /^\|[\s|:-]+\|\s*$/.test(lines[i + 1]))
        return true;
    }
    return false;
  }

  function countFencedCode(text) {
    const matches = text.match(/```(\w*)\n?([\s\S]*?)```/g);
    return matches ? matches.length : 0;
  }

  function hasMath(text) {
    // $$...$$ display, $...$ inline, or \( \) / \[ \]
    return /\$\$[\s\S]+?\$\$|\$[^\s$][^$\n]*?\$|\\\([\s\S]+?\\\)|\\\[[\s\S]+?\\\]/.test(
      text
    );
  }

  function looksLikeJSON(text) {
    const t = text.trim();
    if (!t) return false;
    if (!(t.startsWith("{") || t.startsWith("["))) return false;
    try {
      const v = JSON.parse(t);
      return typeof v === "object";
    } catch (_) {
      return false;
    }
  }

  function isMostlyCode(text) {
    const fence = countFencedCode(text);
    const lines = text.split("\n").length;
    return fence >= 1 && lines <= fence * 4 + 4;
  }

  function isStepByStep(text) {
    // Numbered list with 3+ items, or "Step N:" headings.
    const numbered = (text.match(/(?:^|\n)\d+\.\s+[^\n]+/g) || []).length;
    const stepHead = (text.match(/(?:^|\n)\s*Step\s+\d+/gi) || []).length;
    return numbered >= 3 || stepHead >= 2;
  }

  function isComparison(text) {
    return /\bvs\.?\b|\bcompared? to\b|\bcomparison\b/i.test(text) && hasMarkdownTable(text);
  }

  // ---------- plan builders ----------
  // A "plan" is a plain object the ResponseRenderer consumes:
  //   { kind: 'envelope'|'markdown'|'mixed', envelope?, segments? }
  // segments is an ordered list of {type, ...} for mixed markdown responses.

  function classify(text) {
    if (text == null) text = "";
    if (typeof text !== "string") text = String(text);

    // 1. Structured envelope wins when present.
    const env = tryParseEnvelope(text);
    if (env) {
      const prose = stripEnvelope(text, env.match);
      // Keep surrounding prose AND the envelope's own content — the prose
      // is usually an intro line, not a substitute for the body text.
      const content = env.envelope.content;
      const parts = [prose];
      if (content && content.trim() && content.trim() !== prose.trim()) parts.push(content);
      return {
        kind: "envelope",
        envelope: env.envelope,
        prose: parts.join("\n\n") || env.envelope.content || "",
      };
    }

    // 2. Empty / whitespace -> trivial markdown plan.
    if (!text.trim()) return { kind: "markdown", text: "" };

    // 3. Pure JSON blob -> render as json_viewer.
    if (looksLikeJSON(text)) {
      try {
        const data = JSON.parse(text.trim());
        return {
          kind: "mixed",
          segments: [{ type: "json_viewer", data: { data } }],
        };
      } catch (_) {}
    }

    // 4. Mixed markdown: build segments by walking fenced blocks + tables.
    return { kind: "markdown", text };
  }

  // Build a richer "mixed" plan by splitting markdown into segments so each
  // fenced block / table becomes a specialized component while surrounding
  // prose stays markdown. Used by the StreamingRenderer once complete.
  function segmentMarkdown(text) {
    const segments = [];
    let cursor = 0;
    const re = /```(\w*)\n?([\s\S]*?)```|((?:^\|[^\n]+\|\s*$\n?){2,})/gm;
    let m;
    while ((m = re.exec(text)) !== null) {
      if (m[1] !== undefined) {
        // fenced code block
        const lang = m[1] || "";
        const code = m[2] || "";
        if (cursor < m.index) {
          segments.push({ type: "markdown", text: text.slice(cursor, m.index) });
        }
        segments.push({ type: "code", data: { language: lang, code } });
        cursor = m.index + m[0].length;
      } else if (m[3]) {
        // markdown table
        if (cursor < m.index) {
          segments.push({ type: "markdown", text: text.slice(cursor, m.index) });
        }
        segments.push({ type: "table", data: { markdown: m[3] } });
        cursor = m.index + m[0].length;
      }
    }
    if (cursor < text.length) {
      segments.push({ type: "markdown", text: text.slice(cursor) });
    }
    // Collapse adjacent markdown segments.
    const out = [];
    for (const s of segments) {
      const last = out[out.length - 1];
      if (last && last.type === "markdown" && s.type === "markdown") {
        last.text += s.text;
      } else if (s.type === "markdown" && !s.text.trim()) {
        // drop empty prose
      } else {
        out.push(s);
      }
    }
    return out;
  }

  // ---------- streaming (partial) envelope parsing ----------
  // Tolerant recursive-descent JSON parser for a prefix of a JSON document.
  // Returns the value built so far; strings cut mid-way keep their partial
  // text, a key whose value hasn't started is omitted. `closed` holds every
  // object/array whose closing bracket has arrived, so callers can tell
  // finished components from the one still streaming.
  const INCOMPLETE = {};
  const ESC = { n: "\n", t: "\t", r: "\r", b: "\b", f: "\f", "/": "/", "\\": "\\", '"': '"' };
  const SCALAR_RE = /(?:-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null)(?![\w.+-])/y;
  const TOKEN_RE = /[-+.\deE]+|[a-z]+/y;

  function parsePartialJson(src) {
    const n = src.length;
    const closed = new WeakSet();
    let i = 0;
    const ws = () => { while (i < n && /\s/.test(src[i])) i++; };

    function str() {
      i++;
      let out = "";
      while (i < n) {
        const c = src[i];
        if (c === '"') { i++; return { v: out, done: true }; }
        if (c === "\\") {
          if (i + 1 >= n) { i = n; break; }
          const e = src[i + 1];
          if (e === "u") {
            if (i + 6 > n) { i = n; break; }
            const hex = src.slice(i + 2, i + 6);
            if (!/^[0-9a-fA-F]{4}$/.test(hex)) throw new Error("bad escape");
            out += String.fromCharCode(parseInt(hex, 16));
            i += 6;
            continue;
          }
          out += ESC[e] ?? e;
          i += 2;
          continue;
        }
        out += c;
        i++;
      }
      return { v: out, done: false };
    }

    // Numbers / true / false / null. A token touching the end of input may
    // still be growing ("1." / "tr"): keep it if it already parses, else
    // report it as incomplete.
    function scalar() {
      SCALAR_RE.lastIndex = i;
      const m = SCALAR_RE.exec(src);
      if (m) {
        const tok = m[0];
        if (i + tok.length >= n) { i = n; return JSON.parse(tok); }
        i += tok.length;
        return JSON.parse(tok);
      }
      TOKEN_RE.lastIndex = i;
      const t = TOKEN_RE.exec(src);
      if (t && i + t[0].length >= n) { i = n; return INCOMPLETE; }
      throw new Error("bad token");
    }

    function value() {
      ws();
      if (i >= n) return INCOMPLETE;
      const c = src[i];
      if (c === "{") return obj();
      if (c === "[") return arr();
      if (c === '"') return str().v;
      return scalar();
    }

    function obj() {
      i++;
      const o = {};
      for (;;) {
        ws();
        if (i >= n) return o;
        const c = src[i];
        if (c === "}") { i++; closed.add(o); return o; }
        if (c === ",") { i++; continue; }
        if (c !== '"') throw new Error("bad key");
        const k = str();
        if (!k.done) return o;
        ws();
        if (i >= n) return o;
        if (src[i] !== ":") throw new Error("missing colon");
        i++;
        const v = value();
        if (v === INCOMPLETE) return o;
        o[k.v] = v;
      }
    }

    function arr() {
      i++;
      const a = [];
      for (;;) {
        ws();
        if (i >= n) return a;
        const c = src[i];
        if (c === "]") { i++; closed.add(a); return a; }
        if (c === ",") { i++; continue; }
        const v = value();
        if (v === INCOMPLETE) return a;
        a.push(v);
      }
    }

    try {
      const v = value();
      return v === INCOMPLETE ? null : { value: v, closed };
    } catch (_) {
      return null;
    }
  }

  const STREAM_FENCE_RE = /```(nova-response|nova[ \t]*\r?\n[ \t]*-response|nova|json)[ \t]*\r?\n/i;

  // Inspect a still-streaming reply. Returns null when it is not (or not yet
  // recognisably) a component envelope; otherwise
  //   { prose, envelope, closed, pending }
  // where `pending` means the envelope JSON has not started yet (show a
  // skeleton) and `envelope` may be partially filled.
  function parseStreamingEnvelope(text) {
    if (typeof text !== "string") return null;
    const head = text.trimStart();
    // Reply opens with a partial fence ("``", "```nova-res"): too early to
    // tell, but it is almost always the envelope — show the skeleton.
    if (head && "```nova-response".startsWith(head)) {
      return { prose: "", envelope: null, closed: null, pending: true };
    }
    const m = text.match(STREAM_FENCE_RE);
    if (!m) {
      const nl = text.lastIndexOf("\n");
      const tail = text.slice(nl + 1).trim();
      if (nl >= 0 && tail.startsWith("`") && "```nova-response".startsWith(tail))
        return { prose: text.slice(0, nl).trim(), envelope: null, closed: null, pending: true };
      return null;
    }
    const prose = text.slice(0, m.index).trim();
    let inner = text.slice(m.index + m[0].length);
    const end = inner.search(/\n```/);
    if (end >= 0) inner = inner.slice(0, end);
    else inner = inner.replace(/\n`{1,2}$/, "");
    inner = inner.replace(/^\s*-response\s*/i, "");
    const parsed = parsePartialJson(inner);
    const isJsonFence = m[1] === "json";
    if (!parsed || typeof parsed.value !== "object" || Array.isArray(parsed.value)) {
      if (isJsonFence && inner.trim()) return null;
      return { prose, envelope: null, closed: null, pending: true };
    }
    const env = parsed.value;
    if (isJsonFence && !("response_type" in env)) {
      // ```json that might still turn into an envelope: only while no key
      // other than a prefix of "response_type" has appeared.
      const k = inner.match(/^\s*\{\s*"([a-z_]*)"?\s*$/);
      if (!(Object.keys(env).length === 0 && (!k || "response_type".startsWith(k[1])) && /^\s*\{/.test(inner)))
        return null;
      return { prose, envelope: null, closed: null, pending: true };
    }
    return { prose, envelope: env, closed: parsed.closed, pending: false };
  }

  Nova.ResponseClassifier = {
    classify, segmentMarkdown, tryParseEnvelope, parsePartialJson, parseStreamingEnvelope, FENCE_TAG,
  };
})();
