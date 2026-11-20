/* ==========================================================================
   PersonCard — renders the `person_card` component type.

   Data: {name, description?, extract?, thumbnail?, url?, source?}
   Photo comes from Wikipedia/Wikimedia (LinkedIn/social block scraping);
   when no thumbnail exists the card falls back to an initial avatar.
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { el, esc, inline } = Nova;

  const _lookups = new Map(); // name -> Promise<person|null>, dedupes cards

  function personLookup(name) {
    const key = name.toLowerCase();
    if (_lookups.has(key)) return _lookups.get(key);
    let p = Promise.resolve(null);
    if (typeof API_BASE === "string" && typeof apiHeaders === "function") {
      p = fetch(API_BASE + "/v1/person/lookup?name=" +
                encodeURIComponent(name), { headers: apiHeaders() })
        .then((r) => (r.ok ? r.json() : null))
        .then((j) => (j && j.person) || null)
        .catch(() => null);
    }
    _lookups.set(key, p);
    return p;
  }

  /* Models emit person_card with only a name/description — URLs and photos
     must never be invented, so missing fields are filled from the
     Wikipedia-backed /v1/person/lookup service after mount. */
  function enrich(data, avatar, body) {
    if (data.thumbnail && data.extract) return;
    const name = String(data.name || "").trim();
    if (name.length < 3 || name.length > 80) return;
    personLookup(name).then((p) => {
      if (!p || !avatar.isConnected) return;
      if (!data.thumbnail && p.thumbnail) {
        const img = el("img", {
          src: Nova.safeHttpUrl(p.thumbnail),
          alt: name,
          loading: "lazy",
          referrerpolicy: "no-referrer",
        });
        img.addEventListener("error", () => img.remove());
        avatar.textContent = "";
        avatar.append(img);
        data.thumbnail = p.thumbnail;
      }
      if (!data.description && p.description) {
        body.insertBefore(
          el("div", { class: "nova-person-desc" }, p.description),
          body.children[1] || null);
        data.description = p.description;
      }
      if (!data.extract && p.extract) {
        const ex = el("div", { class: "nova-person-extract" });
        ex.innerHTML = Nova.sanitize(inline(p.extract));
        const link = body.querySelector(".nova-person-link");
        if (link) body.insertBefore(ex, link); else body.append(ex);
        data.extract = p.extract;
      }
      if (!data.url && p.url) {
        const link = el("a", {
          class: "nova-person-link",
          href: Nova.safeHttpUrl(p.url),
          target: "_blank",
          rel: "noopener",
        });
        link.textContent = "View on Wikipedia";
        body.append(link);
        data.url = p.url;
        data.source = "wikipedia";
      }
    }).catch(() => {});
  }

  function initial(name) {
    const words = String(name || "?").trim().split(/\s+/);
    return (words[0]?.[0] || "?") + (words[1]?.[0] || "");
  }

  function render(data) {
    data = data || {};
    const name = data.name || "Unknown";
    const wrap = el("div", { class: "nova-person" });

    const avatar = el("div", { class: "nova-person-avatar" });
    if (data.thumbnail) {
      const img = el("img", {
        src: Nova.safeHttpUrl(data.thumbnail),
        alt: name,
        loading: "lazy",
        referrerpolicy: "no-referrer",
      });
      img.addEventListener("error", () => {
        img.remove();
        avatar.textContent = initial(name);
      });
      avatar.append(img);
    } else {
      avatar.textContent = initial(name);
    }

    const body = el("div", { class: "nova-person-body" });
    body.append(el("div", { class: "nova-person-name" }, name));
    if (data.description) {
      body.append(el("div", { class: "nova-person-desc" }, data.description));
    }
    if (data.extract) {
      const ex = el("div", { class: "nova-person-extract" });
      ex.innerHTML = Nova.sanitize(inline(data.extract));
      body.append(ex);
    }
    if (data.url) {
      const link = el("a", {
        class: "nova-person-link",
        href: Nova.safeHttpUrl(data.url),
        target: "_blank",
        rel: "noopener",
      });
      link.textContent = data.source === "wikipedia" ? "View on Wikipedia" : "Source";
      body.append(link);
    }

    wrap.append(avatar, body);
    enrich(data, avatar, body);
    return wrap;
  }

  Nova.ComponentRegistry.registerComponent("person_card", render);
})();
