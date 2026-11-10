/* ==========================================================================
   TableRenderer — interactive, responsive tables.

   Features:
     - Horizontal scrolling for wide tables.
     - Sticky headers for tall tables.
     - Proper column alignment (numeric right-align).
     - Numeric formatting.
     - Copy table data.
     - CSV export.
     - Accepts either markdown-table text or structured {rows, columns}.
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { el, esc, copy, toCSV, parseMdTable } = Nova;

  function parseMarkdownTable(md) {
    return parseMdTable(md);
  }

  // Values arrive already entity-decoded (markdown via parseMdTable, DOM
  // tables via textContent, JSON via JSON.parse). Rendering with textContent
  // keeps them exact — do NOT decode entities here or "&amp;" becomes "&amp;amp;".
  function cellText(v) {
    return String(v ?? "");
  }

  function isNumeric(v) {
    if (v == null || v === "") return false;
    const n = Number(String(v).replace(/[,$%\s]/g, ""));
    return isFinite(n);
  }

  function render(data) {
    // Normalize input: structured rows or markdown text.
    let columns = data.columns || [];
    let rows = data.rows || [];
    if (!rows.length && data.markdown) {
      const parsed = parseMarkdownTable(data.markdown);
      if (parsed) {
        columns = parsed.columns;
        rows = parsed.rows;
      }
    }
    if (!rows.length) return el("div", { class: "nova-empty" }, "No table data");

    // Infer columns from first row if not provided.
    if (!columns.length && rows.length) {
      const first = rows[0];
      columns =
        first && typeof first === "object" && !Array.isArray(first)
          ? Object.keys(first)
          : [];
    }
    // Alignment is decided per COLUMN, not per cell: text columns are always
    // left-aligned, numeric columns right-aligned, so a stray number in a text
    // column (or text in a numeric one) never flips the whole column.
    const numericCols = columns.map((_, ci) => {
      const vals = rows
        .map((r) => (Array.isArray(r) ? r[ci] : r && typeof r === "object" ? r[columns[ci]] : null))
        .map((v) => (v && typeof v === "object" ? v.value ?? v.text ?? null : v));
      const present = vals.filter((v) => v != null && String(v).trim() !== "");
      return present.length > 0 && present.every((v) => isNumeric(v));
    });
    // Normalize row cells: object -> array aligned to columns; array -> as-is.
    const normRows = rows.map((r) => {
      if (Array.isArray(r)) return r;
      if (r && typeof r === "object") return columns.map((c) => r[c] ?? "");
      return [r];
    });

    const wrap = el("div", { class: "nova-table-wrap" });
    const toolbar = el("div", { class: "nova-table-tools" });
    const title = data.title || data.caption || "";
    if (title) toolbar.append(el("span", { class: "nova-table-title" }, title));
    const tools = el("div", { class: "nova-table-actions" });
    const csvBtn = el("button", { class: "nova-tool-btn", title: "Export CSV" }, "CSV");
    csvBtn.onclick = () => {
      const csvRows = normRows.map((r) => {
        const o = {};
        columns.forEach((c, i) => (o[c] = r[i]));
        return o;
      });
      copy(toCSV(csvRows), csvBtn);
    };
    const copyBtn = el("button", { class: "nova-tool-btn", title: "Copy table" }, "copy");
    copyBtn.onclick = () =>
      copy(
        [columns.join("\t"), ...normRows.map((r) => r.join("\t"))].join("\n"),
        copyBtn
      );
    tools.append(csvBtn, copyBtn);
    toolbar.append(tools);
    wrap.append(toolbar);

    const scroller = el("div", { class: "nova-table-scroll" });
    const table = el("table", { class: "nova-table" });
    const thead = el("thead");
    const headRow = el("tr");
    columns.forEach((c) => headRow.append(el("th", {}, cellText(c))));
    thead.append(headRow);
    table.append(thead);

    const tbody = el("tbody");
    normRows.forEach((r) => {
      const tr = el("tr");
      columns.forEach((c, i) => {
        const v = r[i] ?? "";
        const numeric = numericCols[i] && isNumeric(v);
        const td = el("td", { class: numeric ? "num" : "txt" }, cellText(v));
        tr.append(td);
      });
      tbody.append(tr);
    });
    table.append(tbody);
    scroller.append(table);
    wrap.append(scroller);
    return wrap;
  }

  Nova.TableRenderer = { render, parseMarkdownTable };
  Nova.ComponentRegistry.registerComponent("table", render);
})();
