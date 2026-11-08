/* ==========================================================================
   ResponseRenderer — main entry point for rendering an AI response.

   Takes a classification plan (from ResponseClassifier) and a host element,
   then renders the appropriate components via the ComponentRegistry. Also
   renders sources, suggested prompts, attachments, and the message action
   bar around the content.

   Error boundaries: each component render is wrapped in try/catch so a
   single bad component never breaks the whole message. Unsupported component
   types render a graceful fallback note.
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { el, esc } = Nova;

  function renderComponent(comp, host) {
    const fn = Nova.ComponentRegistry.getComponent(comp.type);
    const slot = el("div", { class: "nova-comp-slot" });
    if (!fn) {
      slot.append(
        el("div", { class: "nova-unsupported" }, `Unsupported component: ${esc(comp.type)}`)
      );
      host.append(slot);
      return;
    }
    try {
      const node = fn(comp.data || {});
      if (node) slot.append(node);
    } catch (e) {
      slot.append(
        el("div", { class: "nova-unsupported" }, `Failed to render ${esc(comp.type)}: ${esc(e.message)}`)
      );
    }
    host.append(slot);
  }

  function renderSources(sources, host) {
    if (!sources || !sources.length) return;
    const wrap = el("div", { class: "nova-sources" });
    wrap.append(el("div", { class: "nova-sources-label" }, "Sources"));
    const list = el("div", { class: "nova-sources-list" });
    sources.forEach((s) => {
      const item = s.url
        ? el("a", { class: "nova-source", href: s.url, target: "_blank", rel: "noopener" })
        : el("div", { class: "nova-source" });
      if (s.favicon)
        item.append(el("img", { class: "nova-fav", src: s.favicon, alt: "" }));
      item.append(el("span", { class: "nova-source-title" }, s.title || s.url || ""));
      if (s.source) item.append(el("span", { class: "nova-source-name" }, s.source));
      list.append(item);
    });
    wrap.append(list);
    host.append(wrap);
  }

  function renderAttachments(attachments, host) {
    if (!attachments || !attachments.length) return;
    const files = attachments.map((a) => ({
      name: a.name,
      url: a.url,
      mime_type: a.mime_type || a.type,
      size: a.size,
    }));
    host.append(Nova.MediaRenderer.filePreview({ files }));
  }

  // Main entry: render a plan into a host element.
  // opts: { message, showActions, showSuggestions }
  function render(plan, host, opts) {
    opts = opts || {};
    host.innerHTML = "";
    host.classList.add("nova-response");

    if (plan.kind === "envelope") {
      renderEnvelope(plan, host, opts);
    } else if (plan.kind === "markdown") {
      host.innerHTML = Nova.MarkdownRenderer.render(plan.text || "");
      Nova.MarkdownRenderer.upgrade(host);
    } else if (plan.kind === "mixed") {
      renderSegments(plan.segments || [], host);
    }

    return host;
  }

  function renderEnvelope(plan, host, opts) {
    const env = plan.envelope;
    // Prose content (envelope.content or surrounding prose) as markdown.
    const prose = plan.prose != null ? plan.prose : env.content || "";
    if (prose && prose.trim()) {
      const mdHost = el("div", { class: "nova-prose" });
      mdHost.innerHTML = Nova.MarkdownRenderer.render(prose);
      Nova.MarkdownRenderer.upgrade(mdHost);
      host.append(mdHost);
    }

    // Components in order.
    (env.components || []).forEach((c) => renderComponent(c, host));

    // Top-level visualization hint.
    if (env.visualization) {
      renderComponent({ type: "chart", data: env.visualization }, host);
    }

    // Attachments.
    renderAttachments(env.attachments, host);

    // Sources.
    renderSources(env.sources, host);

    // Suggested prompts.
    const prompts = env.suggested_prompts;
    if (prompts && prompts.length) {
      const sp = Nova.SuggestedPrompts.render(prompts);
      if (sp) host.append(sp);
    }
  }

  function renderSegments(segments, host) {
    segments.forEach((s) => {
      if (s.type === "markdown") {
        const md = Nova.MarkdownRenderer.render(s.text || "");
        if (md.trim()) {
          const wrap = el("div", { class: "nova-prose" });
          wrap.innerHTML = md;
          Nova.MarkdownRenderer.upgrade(wrap);
          host.append(wrap);
        }
      } else {
        renderComponent(s, host);
      }
    });
  }

  // Render a fully-classified message (content + actions). Used by chat.js
  // for completed (non-streaming) messages.
  function renderMessage(message, host, opts) {
    opts = opts || {};
    host.innerHTML = "";
    host.classList.add("nova-response");
    const text = message.text || "";
    const plan = Nova.ResponseClassifier.classify(text);
    render(plan, host, { message });

    // Infer suggestions only when none supplied and content warrants them.
    // Envelopes render their own suggested_prompts in renderEnvelope; only
    // infer for plain markdown responses so chips never appear twice.
    if (message.role !== "user" && !message.failed && plan.kind !== "envelope") {
      const prompts = Nova.SuggestedPrompts.infer(text);
      if (prompts && prompts.length) {
        const sp = Nova.SuggestedPrompts.render(prompts);
        if (sp) host.append(sp);
      }
    }

    if (opts.showActions !== false) {
      const actions = Nova.MessageActions.render(message);
      host.append(actions);
    }
    return host;
  }

  Nova.ResponseRenderer = { render, renderMessage, renderComponent, renderEnvelope, renderSegments };
})();
