/* ==========================================================================
   JsonViewer — collapsible, syntax-highlighted JSON viewer.

   - Renders nested objects/arrays with collapsible nodes.
   - Syntax-highlighted keys, strings, numbers, booleans, null.
   - Copy button.
   - Handles invalid JSON gracefully (shows raw text).
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { el, esc, copy } = Nova;

  function classFor(v) {
    if (v === null) return "nova-json-null";
    if (typeof v === "string") return "nova-json-str";
    if (typeof v === "number") return "nova-json-num";
    if (typeof v === "boolean") return "nova-json-bool";
    return "";
  }

  function renderVal(v) {
    if (v === null) return "null";
    if (typeof v === "string") return JSON.stringify(v);
    if (typeof v === "object") return "";
    return String(v);
  }

  function buildNode(value, key, depth) {
    const isObj = value && typeof value === "object";
    const row = el("div", { class: "nova-json-row" + (isObj ? " nova-json-branch" : "") });
    row.style.paddingLeft = depth * 16 + "px";

    if (isObj) {
      const arr = Array.isArray(value);
      const summary = arr ? `[${value.length}]` : `{${Object.keys(value).length}}`;
      const toggle = el("span", { class: "nova-json-toggle" }, "▾");
      const keyEl = key != null ? el("span", { class: "nova-json-key" }, `"${key}"`) : null;
      const summaryEl = el("span", { class: "nova-json-summary" }, summary);
      row.append(toggle);
      if (keyEl) row.append(keyEl, ": ");
      row.append(summaryEl);

      const children = el("div", { class: "nova-json-children" });
      const entries = arr
        ? value.map((v, i) => [i, v])
        : Object.entries(value);
      entries.forEach(([k, v]) => children.append(buildNode(v, arr ? null : k, depth + 1)));
      row.append(children);

      let open = depth < 1;
      if (!open) {
        children.style.display = "none";
        toggle.textContent = "▸";
      }
      toggle.onclick = () => {
        open = !open;
        children.style.display = open ? "" : "none";
        toggle.textContent = open ? "▾" : "▸";
      };
    } else {
      if (key != null) row.append(el("span", { class: "nova-json-key" }, `"${key}"`), ": ");
      row.append(el("span", { class: classFor(value) }, renderVal(value)));
    }
    return row;
  }

  function render(data) {
    const value = data.data != null ? data.data : data;
    const wrap = el("div", { class: "nova-json-viewer" });
    const toolbar = el("div", { class: "nova-json-tools" });
    if (data.title) toolbar.append(el("span", { class: "nova-json-title" }, data.title));
    const cp = el("button", { class: "nova-tool-btn" }, "copy");
    cp.onclick = () => copy(JSON.stringify(value, null, 2), cp);
    toolbar.append(cp);
    if (!data.title) toolbar.style.justifyContent = "flex-end";
    wrap.append(toolbar);

    try {
      JSON.stringify(value);
      const root = buildNode(value, null, 0);
      wrap.append(root);
    } catch (e) {
      wrap.append(el("pre", { class: "nova-json-raw" }, esc(String(value))));
    }
    return wrap;
  }

  Nova.JsonViewer = { render };
  Nova.ComponentRegistry.registerComponent("json_viewer", render);
})();
