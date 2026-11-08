/* ==========================================================================
   StreamingRenderer — incremental rendering for SSE token streams.

   Goals (ChatGPT-like):
     - Render incoming tokens incrementally.
     - Subtle typing indicator before first token.
     - Partial Markdown rendering (re-parse on each chunk, throttled).
     - Handle incomplete code blocks (don't break on a dangling ```).
     - Avoid flickering during streaming.
     - Preserve scroll position intelligently; auto-scroll only when the
       user is already near the bottom.
     - Handle cancellation / interrupted responses cleanly.
     - Transition smoothly from streaming to completed (full) render.

   Usage:
     const sr = Nova.StreamingRenderer.create(host, scroller);
     sr.update(partialText);   // call on each chunk
     sr.finish(finalText);     // call when stream completes
     sr.abort();               // call on cancel/error
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { el, nearBottom } = Nova;

  const CURSOR = '<span class="nova-cursor"></span>';

  function create(host, scroller) {
    host.innerHTML = "";
    host.classList.add("nova-response", "nova-streaming");
    let acc = "";
    let lastHtml = "";
    let rafPending = false;
    let finished = false;
    let userPinned = false;

    if (scroller) {
      scroller.addEventListener(
        "scroll",
        () => {
          if (!nearBottom(scroller, 120)) userPinned = true;
          else userPinned = false;
        },
        { passive: true }
      );
    }

    function maybeScroll() {
      if (scroller && !userPinned) {
        scroller.scrollTop = scroller.scrollHeight;
      }
    }

    function scheduleRender() {
      if (rafPending || finished) return;
      rafPending = true;
      requestAnimationFrame(() => {
        rafPending = false;
        doRender();
      });
    }

    function doRender() {
      let text = acc;
      // A nova-response envelope still streaming looks like raw JSON to
      // markdown — show a soft placeholder until the fence closes and the
      // real component render can take over.
      if (/```nova-response/.test(text)) {
        const env = Nova.ResponseClassifier.tryParseEnvelope(text);
        if (!env) {
          const html = '<div class="nova-composing">✨ Composing response…</div>' + CURSOR;
          if (html !== lastHtml) {
            host.innerHTML = html;
            lastHtml = html;
            maybeScroll();
          }
        } else {
          // envelope complete mid-stream: render components live
          Nova.ResponseRenderer.render(Nova.ResponseClassifier.classify(text), host);
          lastHtml = host.innerHTML;
          maybeScroll();
        }
        return;
      }
      const fenceCount = (text.match(/```/g) || []).length;
      const parseText = fenceCount % 2 ? text + "\n```" : text;
      const html = Nova.MarkdownRenderer.render(parseText) + CURSOR;
      if (html !== lastHtml) {
        host.innerHTML = html;
        Nova.MarkdownRenderer.upgrade(host);
        lastHtml = html;
        maybeScroll();
      }
    }

    function update(chunk) {
      if (finished) return;
      acc += chunk;
      scheduleRender();
    }

    function setText(text) {
      if (finished) return;
      acc = text;
      scheduleRender();
    }

    function finish(finalText) {
      if (finished) return;
      finished = true;
      acc = finalText != null ? finalText : acc;
      host.classList.remove("nova-streaming");
      const plan = Nova.ResponseClassifier.classify(acc);
      Nova.ResponseRenderer.render(plan, host);
      maybeScroll();
      return plan;
    }

    function abort(note) {
      if (finished) return;
      finished = true;
      host.classList.remove("nova-streaming");
      if (note) {
        const err = el("div", { class: "nova-status nova-error nova-stream-abort" });
        err.append(el("div", { class: "nova-status-msg" }, note));
        host.append(err);
      }
    }

    return { update, setText, finish, abort, get text() { return acc; } };
  }

  function typingIndicator() {
    return el("div", { class: "typing" }, el("i"), el("i"), el("i"));
  }

  Nova.StreamingRenderer = { create, typingIndicator };
})();
