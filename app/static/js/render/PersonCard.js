/* ==========================================================================
   PersonCard — renders the `person_card` component type as a dossier.

   Data: {name, description?, extract?, thumbnail?, url?, source?, qid?,
          facts?: [{group?, label, value}], socials?: [{label, url}]}
   Photo comes from Wikipedia/Wikimedia (LinkedIn/social block scraping);
   when no thumbnail exists the card falls back to an initial avatar.
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { el, esc, inline } = Nova;

  const _lookups = new Map(); // name -> Promise<person|null>, dedupes cards

  const GROUP_TITLES = [
    ["identity", "Identification"],
    ["physical", "Physical description"],
    ["record", "Record"],
  ];

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

  function factsGrid(facts) {
    const grid = el("div", { class: "nova-person-facts" });
    for (const f of facts) {
      if (!f || !f.label || !f.value) continue;
      const cell = el("div", { class: "nova-person-fact" });
      cell.append(
        el("div", { class: "f-label" }, String(f.label)),
        el("div", { class: "f-val" }, String(f.value)));
      grid.append(cell);
    }
    return grid.childNodes.length ? grid : null;
  }

  /* Dossier sections — facts grouped under titled dividers. Ungrouped
     facts keep the flat grid so model-emitted cards still render. */
  function sectionsNode(facts) {
    if (!Array.isArray(facts) || !facts.length) return null;
    const grouped = new Map();
    const loose = [];
    for (const f of facts.slice(0, 24)) {
      if (!f) continue;
      if (f.group) {
        if (!grouped.has(f.group)) grouped.set(f.group, []);
        grouped.get(f.group).push(f);
      } else {
        loose.push(f);
      }
    }
    if (!grouped.size) return factsGrid(loose.slice(0, 12));
    const wrap = el("div", { class: "nova-person-secs" });
    for (const [key, title] of GROUP_TITLES) {
      const facts0 = grouped.get(key);
      if (!facts0 || !facts0.length) continue;
      const grid = factsGrid(facts0);
      if (!grid) continue;
      const sec = el("div", { class: "nova-person-sec" });
      sec.append(el("div", { class: "np-sec-title" }, title), grid);
      wrap.append(sec);
    }
    if (loose.length) {
      const grid = factsGrid(loose.slice(0, 8));
      if (grid) {
        const sec = el("div", { class: "nova-person-sec" });
        sec.append(el("div", { class: "np-sec-title" }, "Details"), grid);
        wrap.append(sec);
      }
    }
    return wrap.childNodes.length ? wrap : null;
  }

  /* Social handle chips — real URLs only. */
  function socialsNode(socials) {
    if (!Array.isArray(socials) || !socials.length) return null;
    const row = el("div", { class: "nova-person-socials" });
    for (const s of socials.slice(0, 6)) {
      if (!s || !s.label || !s.url) continue;
      const a = el("a", {
        class: "nova-person-social",
        href: Nova.safeHttpUrl(s.url),
        target: "_blank",
        rel: "noopener",
      });
      a.textContent = String(s.label);
      row.append(a);
    }
    return row.childNodes.length ? row : null;
  }

  function sourceLink(data) {
    if (!data.url) return null;
    const link = el("a", {
      class: "nova-person-link",
      href: Nova.safeHttpUrl(data.url),
      target: "_blank",
      rel: "noopener",
    });
    const LABELS = { wikipedia: "View on Wikipedia",
                     github: "View on GitHub" };
    link.textContent = LABELS[data.source] || "Source";
    return link;
  }

  /* Models emit person_card with only a name/description — URLs, photos
     and facts must never be invented, so missing fields are filled from
     the Wikipedia/Wikidata-backed /v1/person/lookup service after mount. */
  function enrich(data, wrap, avatar, body, fileEl) {
    const want = ["thumbnail", "description", "extract", "url",
                  "facts", "socials", "qid"];
    if (want.every((k) => {
      const v = data[k];
      return Array.isArray(v) ? v.length : !!v;
    })) return;
    const name = String(data.name || "").trim();
    if (name.length < 3 || name.length > 80) return;
    personLookup(name).then((p) => {
      if (!p || !avatar.isConnected) return;
      if (!data.qid && p.qid && fileEl) {
        fileEl.textContent = "FILE " + p.qid.replace(/^Q/, "W-")
          + " \u00b7 " + String(p.source || "wikipedia").toUpperCase();
        data.qid = p.qid;
      }
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
      const link0 = body.querySelector(".nova-person-link");
      if (!data.facts && p.facts && p.facts.length) {
        const sn = sectionsNode(p.facts);
        if (sn) { link0 ? body.insertBefore(sn, link0) : body.append(sn); }
        data.facts = p.facts;
      }
      if (!data.socials && p.socials && p.socials.length) {
        const sn = socialsNode(p.socials);
        if (sn) { link0 ? body.insertBefore(sn, link0) : body.append(sn); }
        data.socials = p.socials;
      }
      if (!data.url && p.url) {
        const link = sourceLink({ url: p.url, source: "wikipedia" });
        if (link) body.append(link);
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

    // Dossier header band: document type + file number + source.
    const head = el("div", { class: "np-head" });
    head.append(el("span", { class: "np-doctype" }, "PERSONAL DOSSIER"));
    const fileTxt = data.qid
      ? "FILE " + String(data.qid).replace(/^Q/, "W-") : "";
    const srcTxt = data.source ? String(data.source).toUpperCase() : "";
    const fileEl = el("span", { class: "np-file" },
      fileTxt + (fileTxt && srcTxt ? " \u00b7 " : "") + srcTxt);
    head.append(fileEl);
    wrap.append(head);

    const main = el("div", { class: "np-main" });
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
    main.append(avatar);

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
    const sn0 = sectionsNode(data.facts);
    if (sn0) body.append(sn0);
    const sn1 = socialsNode(data.socials);
    if (sn1) body.append(sn1);
    const link = sourceLink(data);
    if (link) body.append(link);

    main.append(body);
    wrap.append(main);
    enrich(data, wrap, avatar, body, fileEl);
    return wrap;
  }

  /* ---- person_carousel: dossier cards for every matching profile ----
     Data: {people: [{name, description?, extract?, thumbnail?, url?,
     source?, facts?, socials?}]}. Compact mini-dossiers in a snap-scroll
     strip — the full dossier stays for single-subject person_card. */

  function miniCard(p) {
    const card = el("div", { class: "nova-person np-mini" });
    const main = el("div", { class: "np-main" });
    const avatar = el("div", { class: "nova-person-avatar" });
    const name = p.name || "Unknown";
    if (p.thumbnail) {
      const img = el("img", {
        src: Nova.safeHttpUrl(p.thumbnail),
        alt: name, loading: "lazy", referrerpolicy: "no-referrer",
      });
      img.addEventListener("error", () => {
        img.remove(); avatar.textContent = initial(name);
      });
      avatar.append(img);
    } else {
      avatar.textContent = initial(name);
    }
    main.append(avatar);
    const body = el("div", { class: "nova-person-body" });
    const nameRow = el("div", { class: "np-namerow" });
    nameRow.append(el("div", { class: "nova-person-name" }, name));
    if (p.source) {
      nameRow.append(el("span", { class: "np-src" },
        String(p.source).toUpperCase()));
    }
    body.append(nameRow);
    if (p.description) {
      body.append(el("div", { class: "nova-person-desc" }, p.description));
    }
    main.append(body);
    card.append(main);
    if (p.extract) {
      const ex = el("div", { class: "nova-person-extract" });
      ex.innerHTML = Nova.sanitize(inline(p.extract));
      card.append(ex);
    }
    const sn0 = sectionsNode(p.facts);
    if (sn0) card.append(sn0);
    const sn1 = socialsNode(p.socials);
    if (sn1) card.append(sn1);
    const link = sourceLink(p);
    if (link) card.append(link);
    return card;
  }

  function renderCarousel(data) {
    data = data || {};
    const people = Array.isArray(data.people) ? data.people : [];
    const wrap = el("div", { class: "nova-people" });
    const head = el("div", { class: "np-head" });
    head.append(el("span", { class: "np-doctype" },
      data.title || "PERSONAL DOSSIERS"));
    head.append(el("span", { class: "np-file" },
      people.length + " " + (people.length === 1 ? "profile" : "profiles")));
    wrap.append(head);
    const syn = data.synthesis;
    if (syn && (syn.summary ||
        (Array.isArray(syn.clusters) && syn.clusters.length))) {
      const s = el("div", { class: "np-synth" });
      const srow = el("div", { class: "np-synth-row" });
      srow.append(el("span", { class: "np-synth-tag" }, "SYNTHESIS"));
      if (syn.confidence) {
        srow.append(el("span", {
          class: "np-conf np-conf-" +
            String(syn.confidence).toLowerCase(),
        }, String(syn.confidence).toUpperCase()));
      }
      s.append(srow);
      if (syn.summary) {
        s.append(el("div", { class: "np-synth-sum" }, syn.summary));
      }
      if (Array.isArray(syn.clusters)) {
        const chips = el("div", { class: "np-clusters" });
        for (const c of syn.clusters.slice(0, 4)) {
          if (c && c.label) {
            chips.append(el("span", { class: "np-cluster" }, c.label));
          }
        }
        if (chips.childNodes.length) s.append(chips);
      }
      wrap.append(s);
    }
    if (!people.length) return wrap;

    const row = el("div", { class: "nova-people-row" });
    const track = el("div", { class: "nova-people-track" });
    for (const p of people.slice(0, 12)) {
      if (p && typeof p === "object") track.append(miniCard(p));
    }
    const prev = el("button", {
      class: "np-nav ui-btn ui-btn-ghost ui-btn-icon", type: "button",
      "aria-label": "Previous profiles",
    }, "\u2039");
    const next = el("button", {
      class: "np-nav ui-btn ui-btn-ghost ui-btn-icon", type: "button",
      "aria-label": "Next profiles",
    }, "\u203a");
    const step = () => Math.max(180, track.clientWidth * 0.8);
    prev.onclick = () => track.scrollBy({ left: -step(), behavior: "smooth" });
    next.onclick = () => track.scrollBy({ left: step(), behavior: "smooth" });
    row.append(prev, track, next);
    wrap.append(row);
    return wrap;
  }

  Nova.ComponentRegistry.registerComponent("person_card", render);
  Nova.ComponentRegistry.registerComponent("person_carousel", renderCarousel);
})();
