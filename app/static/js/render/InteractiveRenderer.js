/* ==========================================================================
   InteractiveRenderer — dynamic forms, instructions, comparison, math.

   Interactions are functional and connected to conversation state: when a
   user submits a form, a `nova:interactive` CustomEvent is dispatched on the
   host element with the field values + action id, which chat.js listens for
   to continue the conversation with updated context.

   Security: only allowlisted field types from validated schemas are rendered
   — never arbitrary HTML/JS from the model.
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { el, esc } = Nova;

  const FIELD_TYPES = new Set([
    "text", "textarea", "number", "email", "date", "select",
    "checkbox", "radio", "dropdown",
  ]);

  function fieldEl(field) {
    const type = field.type || "text";
    if (!FIELD_TYPES.has(type)) return null;
    const wrap = el("div", { class: "nova-field" });
    if (field.label) wrap.append(el("label", { class: "nova-field-label" }, field.label));
    let input;
    if (type === "select" || type === "dropdown") {
      input = el("select", { class: "ui-select" });
      (field.options || []).forEach((o) => {
        const opt = el("option", { value: o.value != null ? o.value : o }, o.label || o);
        if (field.default === opt.value) opt.selected = true;
        input.append(opt);
      });
    } else if (type === "checkbox") {
      input = el("input", { type: "checkbox" });
      if (field.default) input.checked = true;
    } else if (type === "radio") {
      input = el("div", { class: "nova-radio-group" });
      (field.options || []).forEach((o) => {
        const r = el("label", { class: "nova-radio" });
        const radio = el("input", { type: "radio", name: field.name || field.label, value: o.value != null ? o.value : o });
        if (field.default === radio.value) radio.checked = true;
        r.append(radio, document.createTextNode(o.label || o));
        input.append(r);
      });
    } else if (type === "textarea") {
      input = el("textarea", { class: "ui-textarea", placeholder: field.placeholder || "" });
      if (field.default != null) input.value = field.default;
    } else {
      input = el("input", { type, class: "ui-input", placeholder: field.placeholder || "" });
      if (field.default != null) input.value = field.default;
    }
    input.dataset.fieldName = field.name || field.label || "";
    wrap.append(input);
    if (field.help) wrap.append(el("div", { class: "nova-field-help" }, field.help));
    return wrap;
  }

  function collectValues(form) {
    const out = {};
    form.querySelectorAll("[data-field-name]").forEach((i) => {
      const name = i.dataset.fieldName;
      if (i.type === "checkbox") out[name] = i.checked;
      else if (i.type === "radio") {
        if (i.checked) out[name] = i.value;
      } else out[name] = i.value;
    });
    form.querySelectorAll(".nova-radio-group").forEach((g) => {
      const checked = g.querySelector("input:checked");
      if (checked) out[checked.name] = checked.value;
    });
    return out;
  }

  function render(data) {
    const fields = data.fields || [];
    const wrap = el("div", { class: "nova-interactive" });
    if (data.title) wrap.append(el("div", { class: "nova-section-title" }, data.title));

    const form = el("form", { class: "nova-interactive-form" });
    fields.forEach((f) => {
      const fe = fieldEl(f);
      if (fe) form.append(fe);
    });

    const submit = el("button", { class: "ui-btn ui-btn-primary ui-btn-sm", type: "submit" }, data.submit_label || "Submit");
    form.append(submit);

    form.onsubmit = (e) => {
      e.preventDefault();
      const values = collectValues(form);
      const evt = new CustomEvent("nova:interactive", {
        bubbles: true,
        detail: { action: data.action || "interactive_submit", values, title: data.title },
      });
      wrap.dispatchEvent(evt);
      submit.textContent = "Submitted ✓";
      submit.disabled = true;
    };

    wrap.append(form);
    return wrap;
  }

  // ---------- Instructions (numbered instructional layout) ----------
  function instructions(data) {
    const steps = data.steps || [];
    const wrap = el("div", { class: "nova-instructions" });
    if (data.title) wrap.append(el("div", { class: "nova-section-title" }, data.title));
    const list = el("ol", { class: "nova-steps" });
    steps.forEach((s, i) => {
      const step = typeof s === "string" ? { text: s } : s;
      const li = el("li", { class: "nova-step" });
      li.append(el("div", { class: "nova-step-num" }, String(i + 1)));
      const body = el("div", { class: "nova-step-body" });
      if (step.title) body.append(el("div", { class: "nova-step-title" }, step.title));
      body.append(el("div", { class: "nova-step-text" }, step.text || step.description || ""));
      if (step.code) body.append(Nova.CodeRenderer.render({ language: step.language || "", code: step.code }));
      li.append(body);
      list.append(li);
    });
    wrap.append(list);
    return wrap;
  }

  // ---------- Comparison table ----------
  function comparison(data) {
    const t = Nova.TableRenderer.render({
      rows: data.rows || [],
      columns: data.columns || [],
      title: data.title || "Comparison",
    });
    t.classList.add("nova-comparison");
    return t;
  }

  // ---------- Math expression ----------
  function math(data) {
    const expr = data.expression || data.data || "";
    const display = data.display !== false;
    const wrap = el("div", { class: "nova-math" + (display ? " nova-math-display" : "") });
    if (data.label) wrap.append(el("div", { class: "nova-math-label" }, data.label));
    const host = el("div");
    if (window.katex) {
      try {
        host.innerHTML = window.katex.renderToString(expr, {
          displayMode: display,
          throwOnError: false,
        });
      } catch (_) {
        host.textContent = expr;
      }
    } else {
      host.textContent = expr;
    }
    wrap.append(host);
    return wrap;
  }

  Nova.InteractiveRenderer = { render, instructions, comparison, math, collectValues };
  Nova.ComponentRegistry.registerComponent("interactive", render);
  Nova.ComponentRegistry.registerComponent("instructions", instructions);
  Nova.ComponentRegistry.registerComponent("comparison", comparison);
  Nova.ComponentRegistry.registerComponent("math", math);
})();
