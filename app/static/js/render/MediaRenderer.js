/* ==========================================================================
   MediaRenderer — image gallery, video, audio, file preview cards.

   - Responsive image gallery with lightbox click-to-enlarge.
   - Video preview card (native <video> with poster).
   - Audio player card (native <audio>).
   - File preview cards (name, size, type, download link).
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { el, esc } = Nova;

  function fmtSize(bytes) {
    if (bytes == null) return "";
    const u = ["B", "KB", "MB", "GB"];
    let b = Number(bytes) || 0;
    let i = 0;
    while (b >= 1024 && i < u.length - 1) {
      b /= 1024;
      i++;
    }
    return `${b.toFixed(b < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
  }

  function lightbox(src) {
    const ov = el("div", { class: "nova-lightbox", onclick: () => ov.remove() });
    ov.append(el("img", { src, alt: "" }));
    document.body.appendChild(ov);
  }

  function imageGallery(data) {
    const images = data.images || [];
    const wrap = el("div", { class: "nova-gallery-wrap" });
    if (data.title) wrap.append(el("div", { class: "nova-section-title" }, data.title));
    const grid = el("div", { class: "nova-gallery", style: { gridTemplateColumns: `repeat(${data.columns || "auto-fit"}, minmax(140px, 1fr))` } });
    images.forEach((img) => {
      const src = typeof img === "string" ? img : img.url || img.src || "";
      const cap = typeof img === "object" ? img.caption || img.alt || "" : "";
      const cell = el("figure", { class: "nova-gallery-cell" });
      const i = el("img", { src, alt: cap, loading: "lazy" });
      i.onclick = () => lightbox(src);
      cell.append(i);
      if (cap) cell.append(el("figcaption", {}, cap));
      grid.append(cell);
    });
    wrap.append(grid);
    return wrap;
  }

  function videoRenderer(data) {
    const d = data.data || data;
    const card = el("div", { class: "nova-video-card" });
    if (d.title) card.append(el("div", { class: "nova-section-title" }, d.title));
    const v = el("video", {
      controls: true,
      preload: "metadata",
      style: { width: "100%" },
    });
    if (d.thumbnail) v.poster = d.thumbnail;
    v.append(el("source", { src: d.url }));
    card.append(v);
    if (d.duration) card.append(el("div", { class: "nova-media-meta" }, "Duration: " + d.duration));
    return card;
  }

  function audioRenderer(data) {
    const d = data.data || data;
    const card = el("div", { class: "nova-audio-card" });
    if (d.title) card.append(el("div", { class: "nova-section-title" }, d.title));
    const a = el("audio", { controls: true, preload: "metadata", style: { width: "100%" } });
    a.append(el("source", { src: d.url }));
    card.append(a);
    if (d.duration) card.append(el("div", { class: "nova-media-meta" }, "Duration: " + d.duration));
    return card;
  }

  function filePreview(data) {
    const files = data.files || [];
    const wrap = el("div", { class: "nova-files-wrap" });
    if (data.title) wrap.append(el("div", { class: "nova-section-title" }, data.title));
    const grid = el("div", { class: "nova-files-grid" });
    files.forEach((f) => {
      const card = el("div", { class: "nova-file-card" });
      const ico = el("div", { class: "nova-file-ico" }, fileIcon(f.mime_type || f.type || f.name || ""));
      card.append(ico);
      const body = el("div", { class: "nova-file-body" });
      body.append(el("div", { class: "nova-file-name" }, f.name || "file"));
      const meta = el("div", { class: "nova-file-meta" });
      if (f.size != null) meta.append(el("span", {}, fmtSize(f.size)));
      if (f.mime_type || f.type) meta.append(el("span", {}, f.mime_type || f.type));
      body.append(meta);
      if (f.url) {
        body.append(el("a", { class: "nova-file-link", href: f.url, target: "_blank", rel: "noopener", download: f.name || "" }, "Download"));
      }
      card.append(body);
      grid.append(card);
    });
    wrap.append(grid);
    return wrap;
  }

  function fileIcon(name) {
    if (/\.pdf$/i.test(name)) return "📄";
    if (/\.(zip|tar|gz|rar|7z)$/i.test(name)) return "🗜";
    if (/\.(xls|xlsx|csv)$/i.test(name)) return "📊";
    if (/\.(doc|docx|txt|md)$/i.test(name)) return "📝";
    if (/\.(png|jpe?g|gif|svg|webp)$/i.test(name)) return "🖼";
    if (/\.(mp4|mov|avi|webm)$/i.test(name)) return "🎬";
    if (/\.(mp3|wav|flac|aac)$/i.test(name)) return "🎵";
    return "📎";
  }

  Nova.MediaRenderer = { imageGallery, videoRenderer, audioRenderer, filePreview };
  Nova.ComponentRegistry.registerComponent("image_gallery", imageGallery);
  Nova.ComponentRegistry.registerComponent("video", videoRenderer);
  Nova.ComponentRegistry.registerComponent("audio", audioRenderer);
  Nova.ComponentRegistry.registerComponent("file_preview", filePreview);
})();
