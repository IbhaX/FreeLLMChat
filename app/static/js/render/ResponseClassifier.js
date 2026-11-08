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
  const FENCE_RE = /```nova-response\s*\n([\s\S]*?)\n```/;
  // Unterminated fence: model forgot the closing ``` (or stream was cut).
  const FENCE_OPEN_RE = /```nova-response\s*\n([\s\S]*)$/;

  function parseEnvelopeJson(inner, match) {
    try {
      const obj = JSON.parse(inner.trim());
      if (!obj || typeof obj !== "object" || !obj.response_type) return null;
      return { envelope: obj, match };
    } catch (_) {
      return null;
    }
  }

  function tryParseEnvelope(text) {
    if (typeof text !== "string") return null;
    const m = text.match(FENCE_RE);
    if (m) return parseEnvelopeJson(m[1], m);
    const m2 = text.match(FENCE_OPEN_RE);
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

  Nova.ResponseClassifier = { classify, segmentMarkdown, tryParseEnvelope, FENCE_TAG };
})();
