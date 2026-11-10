/* ==========================================================================
   ListRenderer — renders the `list` component type.

   The response protocol allows {"type":"list","data":{"items":[...],
   "ordered":bool,"title":str}} but no renderer was registered, so the
   fallback dumped the raw JSON into the chat. Items may be strings,
   numbers, or objects ({text|label|title, description|value}).
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { el, inline } = Nova;

  function itemText(item) {
    if (item == null) return "";
    if (typeof item === "string" || typeof item === "number") return String(item);
    if (typeof item === "object") {
      const main = item.text ?? item.label ?? item.title ?? item.value ?? item.name;
      const sub = item.description ?? item.detail ?? item.subtitle;
      const head = main != null ? String(main) : "";
      const tail = sub != null && String(sub) !== head ? String(sub) : "";
      return tail ? head + " — " + tail : head;
    }
    return String(item);
  }

  function render(data) {
    // Tolerate data being a bare array (models sometimes skip the wrapper).
    const payload = Array.isArray(data) ? { items: data } : data || {};
    let items = payload.items;
    if (typeof items === "string") {
      try {
        items = JSON.parse(items);
      } catch (_) {
        items = items.split(/\n+/).filter((l) => l.trim());
      }
    }
    if (!Array.isArray(items) || !items.length) return null;

    const ordered = !!(payload.ordered || payload.type === "ordered");
    const wrap = el("div", { class: "nova-list" });
    if (payload.title) wrap.append(el("div", { class: "nova-section-title" }, payload.title));
    const list = el(ordered ? "ol" : "ul", { class: "nova-list-items" });
    items.forEach((item) => {
      const li = el("li", { class: "nova-list-item" });
      const text = itemText(item);
      // Items are plain text with optional inline markdown (bold/code/links).
      li.innerHTML = Nova.sanitize(inline(text));
      list.append(li);
    });
    wrap.append(list);
    return wrap;
  }

  Nova.ComponentRegistry.registerComponent("list", render);
})();
