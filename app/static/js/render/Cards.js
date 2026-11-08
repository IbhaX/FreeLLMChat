/* ==========================================================================
   Cards — specialized card renderers.

   - MetricCard: important numerical info with optional trend.
   - SearchResultCard: search result cards (title, desc, source, link, favicon).
   - ProductCard: product recommendations (image, name, price, rating, link).
   - BusinessCard: business recommendations (name, address, rating, hours).
   - WeatherWidget: weather information widget.
   - FinancialSummary: financial summary cards + optional chart.

   All card grids are responsive (auto-fit minmax) and avoid oversized cards.
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { el, esc, fmtNum } = Nova;

  function stars(rating) {
    const r = Number(rating) || 0;
    const full = Math.floor(r);
    const half = r - full >= 0.5;
    let s = "";
    for (let i = 0; i < 5; i++)
      s += i < full ? "★" : i === full && half ? "⯨" : "☆";
    return s;
  }

  // ---------- MetricCard ----------
  function metricCard(d) {
    const card = el("div", { class: "nova-metric" });
    if (d.icon) card.append(el("div", { class: "nova-metric-ico" }, d.icon));
    const body = el("div", { class: "nova-metric-body" });
    body.append(el("div", { class: "nova-metric-label" }, d.label || d.title || ""));
    const val = el("div", { class: "nova-metric-value" }, fmtNum(d.value, d.unit));
    if (d.delta != null) {
      const up = Number(d.delta) >= 0;
      const delta = el("span", { class: "nova-metric-delta " + (up ? "up" : "down") },
        (up ? "▲" : "▼") + " " + Math.abs(d.delta) + (d.delta_unit || "%"));
      val.append(" ", delta);
    }
    body.append(val);
    card.append(body);
    return card;
  }

  function metricGrid(data) {
    const items = Array.isArray(data) ? data : [data];
    const grid = el("div", { class: "nova-metric-grid" });
    items.forEach((m) => grid.append(metricCard(m)));
    return grid;
  }

  // ---------- SearchResultCard ----------
  function searchResult(r) {
    const card = el("div", { class: "nova-search-card" });
    const head = el("div", { class: "nova-search-head" });
    if (r.favicon || r.thumbnail) {
      const img = r.favicon
        ? el("img", { class: "nova-fav", src: r.favicon, alt: "" })
        : el("img", { class: "nova-thumb", src: r.thumbnail, alt: "" });
      head.append(img);
    }
    const meta = el("div", { class: "nova-search-meta" });
    meta.append(el("div", { class: "nova-search-source" }, r.source || ""));
    if (r.url) {
      const a = el("a", { class: "nova-search-url", href: r.url, target: "_blank", rel: "noopener" }, r.url.replace(/^https?:\/\//, "").split("/")[0]);
      meta.append(a);
    }
    head.append(meta);
    card.append(head);
    const title = r.url
      ? el("a", { class: "nova-search-title", href: r.url, target: "_blank", rel: "noopener" }, r.title || "")
      : el("div", { class: "nova-search-title" }, r.title || "");
    card.append(title);
    if (r.description || r.snippet)
      card.append(el("div", { class: "nova-search-desc" }, r.description || r.snippet));
    if (r.metadata && Object.keys(r.metadata).length) {
      const mt = el("div", { class: "nova-search-tags" });
      for (const [k, v] of Object.entries(r.metadata))
        mt.append(el("span", { class: "pill mut" }, `${k}: ${v}`));
      card.append(mt);
    }
    return card;
  }

  function searchResults(data) {
    const results = data.results || [];
    const wrap = el("div", { class: "nova-search-wrap" });
    if (data.title || data.query)
      wrap.append(el("div", { class: "nova-section-title" }, data.title || `Results for "${data.query}"`));
    const list = el("div", { class: "nova-search-list" });
    results.forEach((r) => list.append(searchResult(r)));
    wrap.append(list);
    return wrap;
  }

  // ---------- ProductCard ----------
  function productCard(p) {
    const card = el("div", { class: "nova-product-card" });
    if (p.image)
      card.append(el("img", { class: "nova-product-img", src: p.image, alt: p.name || "", loading: "lazy" }));
    const body = el("div", { class: "nova-product-body" });
    body.append(el("div", { class: "nova-product-name" }, p.name || ""));
    if (p.merchant) body.append(el("div", { class: "nova-product-merchant" }, p.merchant));
    if (p.price != null)
      body.append(el("div", { class: "nova-product-price" }, fmtNum(p.price, p.currency || "")));
    if (p.rating != null)
      body.append(el("div", { class: "nova-product-rating" }, `${stars(p.rating)} ${p.rating}`));
    if (p.description)
      body.append(el("div", { class: "nova-product-desc" }, p.description));
    if (p.link || p.url) {
      const href = p.link || p.url;
      body.append(el("a", { class: "nova-product-link", href, target: "_blank", rel: "noopener" }, "View product →"));
    }
    card.append(body);
    return card;
  }

  function productGrid(data) {
    const products = data.products || [];
    const wrap = el("div", { class: "nova-product-wrap" });
    if (data.title) wrap.append(el("div", { class: "nova-section-title" }, data.title));
    const grid = el("div", { class: "nova-product-grid" });
    products.forEach((p) => grid.append(productCard(p)));
    wrap.append(grid);
    return wrap;
  }

  // ---------- BusinessCard ----------
  function businessCard(b) {
    const card = el("div", { class: "nova-biz-card" });
    const head = el("div", { class: "nova-biz-head" });
    head.append(el("div", { class: "nova-biz-name" }, b.name || ""));
    if (b.rating != null)
      head.append(el("div", { class: "nova-biz-rating" }, `${stars(b.rating)} ${b.rating}`));
    card.append(head);
    if (b.address) card.append(el("div", { class: "nova-biz-row" }, "📍 " + b.address));
    if (b.hours || b.operating_hours) card.append(el("div", { class: "nova-biz-row" }, "🕒 " + (b.hours || b.operating_hours)));
    if (b.phone || b.contact) card.append(el("div", { class: "nova-biz-row" }, "📞 " + (b.phone || b.contact)));
    if (b.map_url || b.maps_url)
      card.append(el("a", { class: "nova-biz-map", href: b.map_url || b.maps_url, target: "_blank", rel: "noopener" }, "View on map →"));
    return card;
  }

  function businessGrid(data) {
    const businesses = data.businesses || [];
    const wrap = el("div", { class: "nova-biz-wrap" });
    if (data.title) wrap.append(el("div", { class: "nova-section-title" }, data.title));
    const grid = el("div", { class: "nova-biz-grid" });
    businesses.forEach((b) => grid.append(businessCard(b)));
    wrap.append(grid);
    return wrap;
  }

  // ---------- WeatherWidget ----------
  function weatherWidget(d) {
    const w = el("div", { class: "nova-weather" });
    const top = el("div", { class: "nova-weather-top" });
    top.append(el("div", { class: "nova-weather-ico" }, d.icon || "🌤"));
    const temp = el("div", { class: "nova-weather-temp" }, fmtNum(d.temperature, d.unit || "°"));
    temp.append(el("div", { class: "nova-weather-cond" }, d.condition || ""));
    top.append(temp);
    w.append(top);
    w.append(el("div", { class: "nova-weather-loc" }, "📍 " + (d.location || "")));
    const meta = el("div", { class: "nova-weather-meta" });
    if (d.feels_like != null) meta.append(el("span", {}, `Feels ${d.feels_like}°`));
    if (d.humidity != null) meta.append(el("span", {}, `💧 ${d.humidity}%`));
    if (d.wind != null) meta.append(el("span", {}, `💨 ${d.wind}`));
    w.append(meta);
    if (Array.isArray(d.forecast) && d.forecast.length) {
      const fc = el("div", { class: "nova-weather-forecast" });
      d.forecast.slice(0, 5).forEach((f) =>
        fc.append(
          el("div", { class: "nova-fc-day" }, [
            el("div", { class: "nova-fc-name" }, f.day || ""),
            el("div", { class: "nova-fc-ico" }, f.icon || "·"),
            el("div", { class: "nova-fc-temp" }, fmtNum(f.temp ?? f.temperature, "°")),
          ])
        )
      );
      w.append(fc);
    }
    return w;
  }

  // ---------- FinancialSummary ----------
  function financialSummary(d) {
    const wrap = el("div", { class: "nova-fin-wrap" });
    if (d.title) wrap.append(el("div", { class: "nova-section-title" }, d.title));
    if (d.period) wrap.append(el("div", { class: "nova-fin-period" }, d.period));
    const metrics = d.metrics || [];
    if (metrics.length) {
      const grid = el("div", { class: "nova-metric-grid" });
      metrics.forEach((m) => grid.append(metricCard(m)));
      wrap.append(grid);
    }
    if (d.chart) {
      const c = Nova.ChartRenderer.render(d.chart);
      wrap.append(c);
    }
    return wrap;
  }

  Nova.Cards = {
    metricCard,
    metricGrid,
    searchResults,
    productGrid,
    businessGrid,
    weatherWidget,
    financialSummary,
  };
  Nova.ComponentRegistry.registerComponent("metric_card", metricGrid);
  Nova.ComponentRegistry.registerComponent("search_result", searchResults);
  Nova.ComponentRegistry.registerComponent("product_card", productGrid);
  Nova.ComponentRegistry.registerComponent("business_card", businessGrid);
  Nova.ComponentRegistry.registerComponent("weather", weatherWidget);
  Nova.ComponentRegistry.registerComponent("financial_summary", financialSummary);
})();
