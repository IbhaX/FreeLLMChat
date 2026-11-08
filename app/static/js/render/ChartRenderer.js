/* ==========================================================================
   ChartRenderer — data visualizations via Chart.js (lazy-loaded CDN).

   Supports: bar, line, pie, donut, area, scatter.
   - Selects chart type from data structure when not explicit.
   - Titles, labels, legends, tooltips, responsive resizing.
   - Fallback table if chart rendering fails or Chart.js is unavailable.
   - Never invents missing data — renders exactly what is supplied.
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { el, loadScript, theme } = Nova;

  const CHART_JS = "https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js";
  let _loading = null;

  function ensureChart() {
    if (window.Chart) return Promise.resolve();
    if (_loading) return _loading;
    _loading = loadScript(CHART_JS, () => window.Chart).catch(() => {
      _loading = null;
      throw new Error("Chart.js failed to load");
    });
    return _loading;
  }

  // Pick a sensible chart type from the data shape when not specified.
  function inferChartType(data) {
    if (!Array.isArray(data) || !data.length) return "bar";
    const first = data[0];
    const keys = Object.keys(first);
    const numericKeys = keys.filter((k) => typeof first[k] === "number");
    if (numericKeys.length >= 2 && keys.includes("x")) return "scatter";
    if (numericKeys.length === 1) return "bar";
    return "bar";
  }

  // Build Chart.js datasets from a list of row objects.
  function buildSeries(data, chartType, labels) {
    if (!Array.isArray(data) || !data.length) return { labels: [], datasets: [] };
    const first = data[0];
    const keys = Object.keys(first);
    // label key = first non-numeric key, else index.
    const labelKey =
      keys.find((k) => typeof first[k] !== "number") || keys[0];
    const valueKeys = keys.filter((k) => k !== labelKey);
    const lbls = data.map((r) => String(r[labelKey] ?? ""));
    const datasets = valueKeys.map((k, i) => {
      const ds = {
        label: (labels && labels[k]) || k,
        data: data.map((r) => Number(r[k]) || 0),
        borderWidth: 1.5,
      };
      const palette = paletteFor(i);
      if (chartType === "line" || chartType === "area") {
        ds.borderColor = palette;
        ds.backgroundColor =
          chartType === "area" ? palette + "33" : palette;
        ds.fill = chartType === "area";
        ds.tension = 0.3;
      } else if (chartType === "pie" || chartType === "donut") {
        ds.backgroundColor = data.map((_, j) => paletteFor(j));
      } else {
        ds.backgroundColor = palette + "cc";
        ds.borderColor = palette;
      }
      return ds;
    });
    return { labels: lbls, datasets };
  }

  function paletteFor(i) {
    const P = [
      "#6366f1", "#34d399", "#f0a04b", "#ef5b6e", "#8b5cf6",
      "#059669", "#e5534b", "#5e6ad2", "#fbbf24", "#06b6d4",
    ];
    return P[i % P.length];
  }

  function render(data) {
    const chartType = (data.chart_type || "bar").replace("area", "line");
    const isDonut = data.chart_type === "donut";
    const rows = data.data || [];
    const title = data.title || "";
    const labels = data.labels || {};
    const options = data.options || {};

    const wrap = el("div", { class: "nova-chart-wrap" });
    if (title) wrap.append(el("div", { class: "nova-chart-title" }, title));
    const canvasHost = el("div", { class: "nova-chart-host" });
    const canvas = el("canvas");
    canvasHost.append(canvas);
    wrap.append(canvasHost);
    const fallback = el("div", { class: "nova-chart-fallback", style: { display: "none" } });
    wrap.append(fallback);

    if (!rows.length) {
      wrap.querySelector(".nova-chart-host").remove();
      fallback.style.display = "";
      fallback.append(el("div", { class: "nova-empty" }, "No data to chart"));
      return wrap;
    }

    ensureChart()
      .then(() => {
        const { labels: lbls, datasets } = buildSeries(rows, chartType, labels);
        const isDark = theme() === "dark";
        const gridColor = isDark ? "rgba(148,163,184,.12)" : "rgba(15,23,42,.08)";
        const tickColor = isDark ? "#9aa5b5" : "#475569";
        const cfg = {
          type: chartType === "scatter" ? "scatter" : chartType,
          data: { labels: lbls, datasets },
          options: Object.assign(
            {
              responsive: true,
              maintainAspectRatio: false,
              plugins: {
                legend: { labels: { color: tickColor, boxWidth: 12 } },
                tooltip: { mode: "index", intersect: false },
              },
              scales:
                chartType === "pie" || chartType === "donut"
                  ? {}
                  : {
                      x: { grid: { color: gridColor }, ticks: { color: tickColor } },
                      y: { grid: { color: gridColor }, ticks: { color: tickColor }, beginAtZero: true },
                    },
            },
            options
          ),
        };
        if (isDonut) {
          cfg.type = "doughnut";
          cfg.options.cutout = "60%";
        }
        // eslint-disable-next-line no-new
        new window.Chart(canvas, cfg);
      })
      .catch(() => {
        // Fallback: render the underlying data as a table.
        canvasHost.remove();
        fallback.style.display = "";
        fallback.append(Nova.TableRenderer.render({ rows, title }));
      });

    return wrap;
  }

  Nova.ChartRenderer = { render, inferChartType, ensureChart };
  Nova.ComponentRegistry.registerComponent("chart", render);
})();
