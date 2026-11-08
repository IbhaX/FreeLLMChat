/* ==========================================================================
   StatusRenderers — error / warning / success / progress components.

   Each is a contextual, themed component (not a plain bubble). Errors carry
   recovery options; success/warning may carry action buttons. Progress is a
   0-100 indicator with optional status text.
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { el, esc } = Nova;

  function actionsRow(actions) {
    if (!actions || !actions.length) return null;
    const row = el("div", { class: "nova-status-actions" });
    actions.forEach((a) => {
      const b = el("button", { class: "ui-btn ui-btn-sm" }, a.label || a.action || "Action");
      if (a.action) b.dataset.action = a.action;
      if (a.payload) b.dataset.payload = JSON.stringify(a.payload);
      row.append(b);
    });
    return row;
  }

  function errorRenderer(data) {
    const d = data.data || data;
    const box = el("div", { class: "nova-status nova-error" });
    box.append(el("div", { class: "nova-status-ico" }, "⚠"));
    box.append(el("div", { class: "nova-status-body" }, [
      el("div", { class: "nova-status-title" }, d.title || "Error"),
      el("div", { class: "nova-status-msg" }, d.message || ""),
    ]));
    if (d.code) box.append(el("div", { class: "nova-status-code" }, "code: " + d.code));
    if (d.recovery) box.append(el("div", { class: "nova-status-recovery" }, d.recovery));
    const ar = actionsRow(d.actions);
    if (ar) box.append(ar);
    return box;
  }

  function warningRenderer(data) {
    const d = data.data || data;
    const box = el("div", { class: "nova-status nova-warning" });
    box.append(el("div", { class: "nova-status-ico" }, "▲"));
    box.append(el("div", { class: "nova-status-body" }, [
      el("div", { class: "nova-status-title" }, d.title || "Warning"),
      el("div", { class: "nova-status-msg" }, d.message || ""),
    ]));
    const ar = actionsRow(d.actions);
    if (ar) box.append(ar);
    return box;
  }

  function successRenderer(data) {
    const d = data.data || data;
    const box = el("div", { class: "nova-status nova-success" });
    box.append(el("div", { class: "nova-status-ico" }, "✓"));
    box.append(el("div", { class: "nova-status-body" }, [
      el("div", { class: "nova-status-title" }, d.title || "Success"),
      el("div", { class: "nova-status-msg" }, d.message || ""),
    ]));
    const ar = actionsRow(d.actions);
    if (ar) box.append(ar);
    return box;
  }

  function progressRenderer(data) {
    const d = data.data || data;
    const v = Math.max(0, Math.min(100, Number(d.value) || 0));
    const wrap = el("div", { class: "nova-progress-wrap" });
    if (d.label) wrap.append(el("div", { class: "nova-progress-label" }, d.label));
    const bar = el("div", { class: "nova-progress-bar" });
    const fill = el("div", { class: "nova-progress-fill", style: { width: v + "%" } });
    bar.append(fill);
    wrap.append(bar);
    const meta = el("div", { class: "nova-progress-meta" });
    meta.append(el("span", {}, Math.round(v) + "%"));
    if (d.status) meta.append(el("span", { class: "nova-progress-status" }, d.status));
    if (d.total != null) meta.append(el("span", {}, `${d.value || 0}/${d.total}`));
    wrap.append(meta);
    return wrap;
  }

  Nova.StatusRenderers = { errorRenderer, warningRenderer, successRenderer, progressRenderer };
  Nova.ComponentRegistry.registerComponent("error", errorRenderer);
  Nova.ComponentRegistry.registerComponent("warning", warningRenderer);
  Nova.ComponentRegistry.registerComponent("success", successRenderer);
  Nova.ComponentRegistry.registerComponent("progress", progressRenderer);
})();
