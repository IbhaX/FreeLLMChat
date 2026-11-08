/* ==========================================================================
   CodeRenderer — professional code blocks.

   Features:
     - Language detection + syntax highlighting (highlight.js when present).
     - Copy code button.
     - Line numbers for longer snippets (>12 lines).
     - Horizontal scrolling.
     - Expand/collapse for very long snippets (>40 lines, collapsed by default).
     - Download code when a filename is supplied or inferred.
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { el, esc, copy } = Nova;

  const COLLAPSE_THRESHOLD = 40;
  const LINENUM_THRESHOLD = 12;

  // Heuristic language detection from code content when no lang tag is given.
  function detectLang(code) {
    const c = code.slice(0, 400);
    if (/^\s*(import|from|def |class |print\(|lambda )/m.test(c) && !/;\s*$/.test(c))
      return "python";
    if (/(const|let|var)\s+\w+\s*=|=>|console\.log|function\s+\w+/.test(c))
      return "javascript";
    if (/^\s*(package|func |import \()/m.test(c)) return "go";
    if (/^\s*(#include|int main|std::)/m.test(c)) return "cpp";
    if (/^\s*(SELECT|INSERT|UPDATE|DELETE)\b/im.test(c)) return "sql";
    if (/^\s*<\?php|echo\s|->\s/m.test(c)) return "php";
    if (/^\s*(use |fn |pub |mod )/m.test(c)) return "rust";
    return "";
  }

  function highlight(code, lang) {
    if (!window.hljs) return esc(code);
    try {
      if (lang && window.hljs.getLanguage(lang))
        return window.hljs.highlight(code, { language: lang }).value;
      return window.hljs.highlightAuto(code).value;
    } catch (_) {
      return esc(code);
    }
  }

  function download(code, filename) {
    const blob = new Blob([code], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = el("a", { href: url, download: filename || "snippet.txt" });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function render(data) {
    const lang = (data.language || "").toLowerCase();
    const code = data.code != null ? data.code : "";
    const filename = data.filename || "";
    const lines = code.split("\n");
    const long = lines.length > COLLAPSE_THRESHOLD;
    const showLineNums = lines.length > LINENUM_THRESHOLD;
    const detected = lang || detectLang(code);
    const label = lang || detected || "code";

    const block = el("div", { class: "nova-codeblock" + (long ? " collapsed" : "") });

    // header: language label + actions
    const head = el("div", { class: "nova-codehead" });
    const labelEl = el("span", { class: "nova-code-lang" }, filename || label);
    const actions = el("div", { class: "nova-code-actions" });
    if (filename || detected) {
      const dl = el("button", { class: "nova-code-btn", title: "Download" }, "download");
      dl.onclick = () => download(code, filename);
      actions.append(dl);
    }
    const cp = el("button", { class: "nova-code-btn", title: "Copy code" }, "copy");
    cp.onclick = () => copy(code, cp);
    actions.append(cp);
    if (long) {
      const ex = el("button", { class: "nova-code-btn nova-expand", title: "Expand/collapse" }, "expand");
      ex.onclick = () => {
        block.classList.toggle("collapsed");
        ex.textContent = block.classList.contains("collapsed") ? "expand" : "collapse";
      };
      actions.append(ex);
    }
    head.append(labelEl, actions);
    block.append(head);

    // body: optional line-number gutter + highlighted <pre><code>
    const body = el("div", { class: "nova-codebody" });
    const pre = el("pre");
    const codeEl = el("code", { class: detected ? `language-${detected}` : "" });
    codeEl.innerHTML = highlight(code, detected);
    if (showLineNums) {
      const gutter = el("div", { class: "nova-linenumbers" });
      gutter.innerHTML = lines.map((_, i) => `<span>${i + 1}</span>`).join("");
      body.append(gutter);
    }
    pre.append(codeEl);
    body.append(pre);
    block.append(body);
    return block;
  }

  Nova.CodeRenderer = { render, detectLang, highlight };
  Nova.ComponentRegistry.registerComponent("code", render);
})();
