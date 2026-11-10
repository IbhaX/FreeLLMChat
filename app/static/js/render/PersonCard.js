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
        src: data.thumbnail,
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
        href: data.url,
        target: "_blank",
        rel: "noopener",
      });
      link.textContent = data.source === "wikipedia" ? "View on Wikipedia" : "Source";
      body.append(link);
    }

    wrap.append(avatar, body);
    return wrap;
  }

  Nova.ComponentRegistry.registerComponent("person_card", render);
})();
