/* ==========================================================================
   DiagramRenderer — architecture and flowchart components.
   Data-only renderer: no model-supplied HTML, CSS or JavaScript is executed.
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { el } = Nova;

  function text(value) {
    return value == null ? "" : String(value).slice(0, 240);
  }

  function heading(title, caption) {
    const head = el("div", { class: "nova-diagram-head" });
    if (title) head.append(el("div", { class: "nova-diagram-title" }, text(title)));
    if (caption) head.append(el("div", { class: "nova-diagram-caption" }, text(caption)));
    return head;
  }

  function componentCard(item) {
    const card = el("div", { class: "nova-architecture-node" });
    if (item.icon) card.append(el("span", { class: "nova-architecture-icon", "aria-hidden": "true" }, text(item.icon)));
    const body = el("div", { class: "nova-architecture-copy" });
    body.append(el("div", { class: "nova-architecture-name" }, text(item.title || item.label || item.name || "Component")));
    const detail = item.subtitle || item.description || item.detail;
    if (detail) body.append(el("div", { class: "nova-architecture-detail" }, text(detail)));
    if (item.status) body.append(el("span", { class: "nova-diagram-status" }, text(item.status)));
    card.append(body);
    return card;
  }

  function architecture(data) {
    const layers = Array.isArray(data.layers) ? data.layers.slice(0, 10) : [];
    if (!layers.length) return null;
    const wrap = el("section", { class: "nova-diagram nova-architecture", "aria-label": data.title || "Architecture diagram" });
    wrap.append(heading(data.title, data.caption));
    const stack = el("div", { class: "nova-architecture-stack" });
    layers.forEach((layer, index) => {
      const panel = el("div", { class: "nova-architecture-layer" });
      const hasNodes = Array.isArray(layer.nodes) && layer.nodes.length > 0;
      if (hasNodes && (layer.title || layer.subtitle)) {
        const label = el("div", { class: "nova-layer-heading" });
        if (layer.title) label.append(el("div", { class: "nova-layer-title" }, text(layer.title)));
        if (layer.subtitle) label.append(el("div", { class: "nova-layer-subtitle" }, text(layer.subtitle)));
        panel.append(label);
      }
      const nodes = hasNodes
        ? layer.nodes.slice(0, 8)
        : (layer.title ? [{ title: layer.title, subtitle: layer.subtitle, icon: layer.icon }] : []);
      if (nodes.length) {
        const grid = el("div", { class: "nova-architecture-grid" });
        nodes.forEach((node) => grid.append(componentCard(node)));
        panel.append(grid);
      }
      stack.append(panel);
      if (index < layers.length - 1) {
        const arrow = el("div", { class: "nova-diagram-connector", "aria-hidden": "true" });
        arrow.append(el("span", {}, layer.next_label || "↓"));
        stack.append(arrow);
      }
    });
    wrap.append(stack);
    return wrap;
  }

  function flowchart(data) {
    const nodes = Array.isArray(data.nodes) ? data.nodes.slice(0, 24) : [];
    if (!nodes.length) return null;
    const edges = Array.isArray(data.edges) ? data.edges : [];
    const outgoing = new Map();
    edges.forEach((edge) => {
      if (!edge || !edge.from || !edge.to) return;
      if (!outgoing.has(edge.from)) outgoing.set(edge.from, []);
      outgoing.get(edge.from).push(edge);
    });
    const wrap = el("section", { class: "nova-diagram nova-flowchart", "aria-label": data.title || "Flowchart" });
    wrap.append(heading(data.title, data.caption));
    const list = el("div", { class: "nova-flow-list" });
    nodes.forEach((node, index) => {
      const kind = ["start", "end", "decision", "process", "input", "output"].includes(node.kind) ? node.kind : "process";
      const item = el("div", { class: "nova-flow-item" });
      const shape = el("div", { class: "nova-flow-node nova-flow-" + kind });
      if (node.icon) shape.append(el("span", { class: "nova-flow-icon" }, text(node.icon)));
      shape.append(el("span", { class: "nova-flow-label" }, text(node.label || node.title || node.id || "Step")));
      if (node.description) shape.append(el("span", { class: "nova-flow-detail" }, text(node.description)));
      item.append(shape);
      const links = outgoing.get(node.id) || [];
      if (links.length) {
        const branches = el("div", { class: "nova-flow-branches" });
        links.slice(0, 4).forEach((edge) => {
          const branch = el("span", { class: "nova-flow-branch" }, text(edge.label || "Next"));
          branches.append(branch);
        });
        item.append(branches);
      }
      list.append(item);
      if (index < nodes.length - 1) {
        const connector = el("div", { class: "nova-flow-connector", "aria-hidden": "true" });
        connector.append(el("span", {}, "↓"));
        list.append(connector);
      }
    });
    wrap.append(list);
    return wrap;
  }

  Nova.ComponentRegistry.registerComponent("architecture_diagram", architecture);
  Nova.ComponentRegistry.registerComponent("flowchart", flowchart);
  Nova.DiagramRenderer = { architecture, flowchart };
})();
