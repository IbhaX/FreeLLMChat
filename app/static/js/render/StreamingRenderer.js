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

  const NO_LIVE = new Set([
    "chart", "sparkline", "heatmap", "gauge", "interactive", "math",
    "image_gallery", "video", "audio", "person_card", "product_card", "weather",
    "file_preview",
  ]);

  // Skeleton shapes by component type ("intro"/"block"/"sources" are the
  // generic placeholders shown before a component's type is known).
  const SKEL_SHAPE = {
    table: "table", comparison: "table", heatmap: "table", keyvalue: "table", financial_summary: "stats",
    stat_grid: "stats", metric_card: "stats", gauge: "media", progress: "lines",
    list: "lines", instructions: "lines", timeline: "lines", search_result: "lines",
    chart: "media", sparkline: "media", image_gallery: "media", video: "media",
    person_card: "profile", product_card: "profile", business_card: "profile",
    sources: "chips", intro: "intro",
  };

  function bar(w) {
    return el("div", { class: "nova-skel nova-skel-line", style: `width:${w}%` });
  }

  function skeleton(kind) {
    const shape = SKEL_SHAPE[kind] || "card";
    const box = el("div", { class: `nova-skel-box nova-skel-${shape}`, "aria-hidden": "true" });
    if (shape === "intro") {
      box.append(bar(92), bar(78), bar(55));
      const card = el("div", { class: "nova-skel-card" });
      card.append(bar(40), bar(85), bar(70));
      box.append(card);
    } else if (shape === "table") {
      for (let r = 0; r < 4; r++) {
        const row = el("div", { class: "nova-skel-row" });
        for (let c = 0; c < 3; c++) row.append(el("div", { class: "nova-skel nova-skel-cell" }));
        box.append(row);
      }
    } else if (shape === "stats") {
      const row = el("div", { class: "nova-skel-row" });
      for (let c = 0; c < 3; c++) {
        const s = el("div", { class: "nova-skel-card" });
        s.append(bar(50), el("div", { class: "nova-skel nova-skel-num" }));
        row.append(s);
      }
      box.append(row);
    } else if (shape === "lines") {
      [88, 72, 80, 60].forEach((w) => box.append(bar(w)));
    } else if (shape === "media") {
      box.append(el("div", { class: "nova-skel nova-skel-media" }));
    } else if (shape === "profile") {
      const row = el("div", { class: "nova-skel-row" });
      row.append(el("div", { class: "nova-skel nova-skel-avatar" }));
      const col = el("div", { class: "nova-skel-col" });
      col.append(bar(60), bar(85), bar(40));
      row.append(col);
      box.append(row);
    } else if (shape === "chips") {
      const row = el("div", { class: "nova-skel-row" });
      for (let c = 0; c < 3; c++) row.append(el("div", { class: "nova-skel nova-skel-chip" }));
      box.append(row);
    } else {
      const card = el("div", { class: "nova-skel-card" });
      card.append(bar(35), bar(90), bar(75), bar(60));
      box.append(card);
    }
    return box;
  }

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

    // ---- progressive envelope rendering ----
    // Layout while a nova-response envelope streams:
    //   [prose before fence] [envelope.content] [component slots…] [skeleton]
    // Finished components render once with their real renderer; the one
    // still streaming renders live from its partial data (or a skeleton
    // shaped like its type), so the reply fills in token by token.
    let envView = null;

    function ensureEnvView() {
      if (envView) return envView;
      host.innerHTML = "";
      lastHtml = "";
      envView = {
        pre: el("div", { class: "nova-prose" }),
        prose: el("div", { class: "nova-prose" }),
        comps: el("div", { class: "nova-stream-comps" }),
        skel: el("div", { class: "nova-skel-wrap" }),
        slots: [],
        preText: null,
        proseText: null,
        skelKey: null,
      };
      host.append(envView.pre, envView.prose, envView.comps, envView.skel);
      return envView;
    }

    function setMarkdown(node, text, key, withCursor) {
      const sig = text + (withCursor ? "\u0001" : "");
      if (envView[key] === sig) return;
      envView[key] = sig;
      if (!text.trim()) { node.innerHTML = ""; return; }
      node.innerHTML = Nova.MarkdownRenderer.render(text) + (withCursor ? CURSOR : "");
      Nova.MarkdownRenderer.upgrade(node);
    }

    function renderSlot(i, comp, final) {
      const v = envView;
      let slot = v.slots[i];
      if (!slot) {
        slot = { node: el("div", { class: "nova-stream-slot" }), sig: null, final: false };
        v.slots[i] = slot;
        v.comps.append(slot.node);
      }
      if (slot.final) return;
      const sig = JSON.stringify(comp) + (final ? "!" : "");
      if (slot.sig === sig) return;
      slot.sig = sig;
      const tmp = el("div");
      if (final) {
        Nova.ResponseRenderer.renderComponent(comp, tmp);
        slot.final = true;
        slot.skel = null;
      } else if (liveRender(comp, tmp)) {
        slot.skel = null;
      } else {
        const shape = SKEL_SHAPE[comp.type] || "card";
        if (slot.skel === shape) return;
        slot.skel = shape;
        tmp.append(skeleton(comp.type));
      }
      slot.node.replaceChildren(...tmp.childNodes);
    }

    // Partial-data render for the in-progress component. Heavy/visual types
    // (charts, media) wait for complete data and show a skeleton instead.
    function liveRender(comp, tmp) {
      const type = comp && comp.type;
      if (!type || !comp.data || typeof comp.data !== "object" || NO_LIVE.has(type)) return false;
      const fn = Nova.ComponentRegistry.getComponent(type);
      if (!fn) return false;
      try {
        const node = fn(comp.data);
        if (!node || !(node.textContent || "").trim()) return false;
        const wrap = el("div", { class: "nova-comp-slot nova-live" });
        wrap.append(node);
        tmp.append(wrap);
        return true;
      } catch (_) {
        return false;
      }
    }

    function setSkeleton(kind) {
      const v = envView;
      if (v.skelKey === kind) return;
      v.skelKey = kind;
      v.skel.replaceChildren(...(kind ? [skeleton(kind)] : []));
    }

    function renderEnvelopeStream(st) {
      const v = ensureEnvView();
      setMarkdown(v.pre, st.prose || "", "preText", false);
      const env = st.envelope;
      if (!env) {
        setMarkdown(v.prose, "", "proseText", false);
        setSkeleton("intro");
        return;
      }
      const closed = st.closed;
      const comps = Array.isArray(env.components) ? env.components : null;
      const content = typeof env.content === "string" ? env.content : "";
      setMarkdown(v.prose, content, "proseText", !comps && content.length > 0);
      if (!comps) {
        setSkeleton(content ? "block" : "intro");
        return;
      }
      comps.forEach((c, i) => {
        if (c && typeof c === "object") renderSlot(i, c, closed.has(c));
      });
      if (!closed.has(comps)) {
        const last = comps[comps.length - 1];
        const lastDone = !last || closed.has(last);
        setSkeleton(lastDone ? "block" : null);
      } else if (!closed.has(env)) {
        setSkeleton("sources");
      } else {
        setSkeleton(null);
      }
    }

    function doRender() {
      let text = acc;
      const st = Nova.ResponseClassifier.parseStreamingEnvelope(text);
      if (st) {
        renderEnvelopeStream(st);
        maybeScroll();
        return;
      }
      envView = null;
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
