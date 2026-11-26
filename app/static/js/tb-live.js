/* TextbookLive — the "book being born" workspace.
 *
 * Driven only by real backend events: `document_plan` (section list from the
 * synthesizer), `answer_section` (each completed section's actual text), and
 * stage/finding/source events for the activity rail. No simulated progress.
 *
 *   Nova.TextbookLive.create(host, opts) -> {
 *     plan(sections), section(index,title,text), activity(ev),
 *     finish(meta), fail(msg), destroy(), el
 *   }
 *
 *   Nova.TextbookLive.skeleton(host, {title, chapters:[titles]}) -> node
 *   Static provisional book used while a composed document is still building.
 */
(function () {
  'use strict';

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = String(text);
    return n;
  }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function reduced() {
    return !window.gsap ||
      matchMedia('(prefers-reduced-motion: reduce)').matches;
  }
  function reveal(node, opts) {
    if (reduced()) return;
    window.gsap.fromTo(node,
      { opacity: 0, y: (opts && opts.y) || 18 },
      { opacity: 1, y: 0, duration: (opts && opts.d) || 0.55,
        ease: 'power2.out', clearProps: 'transform' });
  }
  function md(text) {
    const NR = window.NovaRender || window.Nova || {};
    if (NR.MarkdownRenderer && NR.MarkdownRenderer.render) {
      try { return NR.MarkdownRenderer.render(String(text || '')); }
      catch (_) { /* fall through */ }
    }
    if (window.marked && window.DOMPurify) {
      try {
        return window.DOMPurify.sanitize(
          window.marked.parse(String(text || ''), { breaks: true }));
      } catch (_) { /* fall through */ }
    }
    return '<p>' + esc(text).replace(/\n{2,}/g, '</p><p>')
      .replace(/\n/g, '<br>') + '</p>';
  }

  /* ---------------- skeleton pieces ---------------- */

  function skelLines(widths) {
    return widths.map(function (w) {
      return '<span class="tbl-line" style="width:' + w + '%"></span>';
    }).join('');
  }

  function chapterSkeletonBody() {
    const w = document.createElement('div');
    w.className = 'tbl-skel';
    w.innerHTML =
      '<div class="tbl-skel-head">' + skelLines([62]) + '</div>' +
      '<div class="tbl-skel-par">' + skelLines([100, 97, 91, 100, 68]) + '</div>' +
      '<div class="tbl-skel-illus"><div class="tbl-skel-illus-in">' +
        '<span class="tbl-skel-illus-ic"></span><span>Illustration</span>' +
      '</div></div>' +
      '<div class="tbl-skel-par">' + skelLines([100, 94, 99, 84]) + '</div>' +
      '<div class="tbl-skel-card"><span class="tbl-skel-tag"></span>' +
        skelLines([96, 78]) + '</div>';
    return w;
  }

  /* ---------------- workspace ---------------- */

  function create(host, opts) {
    opts = opts || {};
    const root = el('div', 'tbl');
    root.innerHTML =
      '<div class="tbl-status"><span class="tbl-pulse"></span>' +
        '<span class="tbl-now">Preparing…</span></div>' +
      '<nav class="tbl-strip" aria-label="Chapter progress"></nav>' +
      '<section class="tbl-book"></section>' +
      '<details class="tbl-act"><summary>Research activity</summary>' +
        '<div class="tbl-act-list"></div></details>';
    host.appendChild(root);

    const strip = root.querySelector('.tbl-strip');
    const book = root.querySelector('.tbl-book');
    const actList = root.querySelector('.tbl-act-list');
    const nowLine = root.querySelector('.tbl-now');

    const cover = el('div', 'tbl-cover tbl-skel-cover');
    cover.innerHTML =
      '<div class="tbl-cover-emb"></div>' +
      '<div class="tbl-cover-lines">' + skelLines([46, 30]) + '</div>' +
      '<div class="tbl-cover-status">Composing…</div>';
    book.appendChild(cover);

    const chapters = new Map();   // index -> {node, body, toc, chip, done}
    const actRows = [];
    const MAX_ACT = 60;
    let started = false, doneAll = false, destroyed = false;

    function tocItem(sec) {
      const it = el('button', 'tbl-toc-item');
      it.type = 'button';
      it.innerHTML =
        '<span class="tbl-toc-num">' + esc(String(sec.index + 1).padStart(2, '0')) +
        '</span><span class="tbl-toc-ti">' + esc(sec.title) + '</span>';
      it.onclick = function () {
        const rec = chapters.get(sec.index);
        if (rec) rec.node.scrollIntoView({ behavior: 'smooth', block: 'start' });
      };
      return it;
    }

    function plan(sections) {
      if (!Array.isArray(sections) || !sections.length || destroyed) return;
      started = true;
      cover.classList.remove('tbl-skel-cover');
      cover.querySelector('.tbl-cover-lines').innerHTML = '';
      const t = el('div', 'tbl-cover-title');
      t.innerHTML = '<span class="tbl-cover-kick">iChat Research</span>' +
        '<h1>' + esc(opts.title || 'Research textbook') + '</h1>';
      cover.insertBefore(t, cover.lastChild);
      sections.forEach(function (sec) {
        const item = tocItem(sec);
        strip.appendChild(item);
        const ch = el('article', 'tbl-chapter');
        ch.dataset.index = sec.index;
        const head = el('div', 'tbl-ch-head');
        head.innerHTML =
          '<span class="tbl-ch-num">Chapter ' +
          String(sec.index + 1).padStart(2, '0') + '</span>' +
          '<h3>' + esc(sec.title) + '</h3>' +
          '<span class="tbl-chip tbl-chip-wait">queued</span>';
        ch.appendChild(head);
        const body = el('div', 'tbl-ch-body');
        body.appendChild(chapterSkeletonBody());
        ch.appendChild(body);
        book.appendChild(ch);
        chapters.set(sec.index, { node: ch, body: body, toc: item,
          chip: head.querySelector('.tbl-chip'), done: false });
      });
      reveal(root, { d: 0.5 });
    }

    function section(index, title, text) {
      if (destroyed || doneAll) return;
      let rec = chapters.get(index);
      if (!rec) {
        // Section arrived without a plan entry — append it in order.
        const ch = el('article', 'tbl-chapter');
        ch.dataset.index = index;
        const head = el('div', 'tbl-ch-head');
        head.innerHTML =
          '<span class="tbl-ch-num">Chapter ' +
          String(index + 1).padStart(2, '0') + '</span>' +
          '<h3>' + esc(title || 'Section') + '</h3>' +
          '<span class="tbl-chip tbl-chip-wait">writing</span>';
        ch.appendChild(head);
        const body = el('div', 'tbl-ch-body');
        ch.appendChild(body);
        const later = [...chapters.values()]
          .filter(r => r.node.parentNode === book && r.node.dataset.index > index)
          .sort((a, b) => (+a.node.dataset.index) - (+b.node.dataset.index))[0];
        if (later) book.insertBefore(ch, later.node); else book.appendChild(ch);
        const item = tocItem({ index: index, title: title || 'Section' });
        strip.appendChild(item);
        rec = { node: ch, body: body, toc: item,
          chip: head.querySelector('.tbl-chip'), done: false };
        chapters.set(index, rec);
      }
      if (rec.done) return;               // replays never duplicate
      rec.done = true;
      rec.body.innerHTML = '<div class="tbl-md">' + md(text) + '</div>';
      rec.chip.textContent = 'done';
      rec.chip.className = 'tbl-chip tbl-chip-done';
      rec.toc.classList.add('is-done');
      reveal(rec.node, { d: 0.6 });
    }

    /* Friendly labels for real backend event names — no fabrication. */
    const STAGE_LABEL = {
      plan: 'Planning research', gather: 'Collecting sources',
      verify: 'Verifying claims', gap: 'Checking coverage',
      contradictions: 'Cross-checking findings', qa: 'QA audit',
      synthesize: 'Writing sections', expand: 'Expanding thin sections',
    };
    function activity(ev) {
      if (!ev || destroyed) return;
      const kind = ev.event || '';
      if (kind === 'answer_section' || kind === 'document_plan') return;
      let who = ev.agent || ev.actor || '';
      let what = '';
      if (kind === 'stage') {
        who = who || 'Pipeline';
        what = ev.text || '';
      } else if (kind === 'step_done' || kind === 'step_fail') {
        who = who || 'Agent';
        what = (kind === 'step_done' ? 'Finished ' : 'Failed ') +
          (ev.task_id || 'task');
      } else if (kind === 'finding') {
        who = who || 'Finder';
        what = ev.text || ev.claim || '';
      } else if (kind === 'source_discovered') {
        who = who || 'Discovery';
        const s = ev.source || ev;
        what = 'Found source — ' + (s.title || s.url || '');
      } else if (kind === 'note') {
        who = who || (ev.stage ? (STAGE_LABEL[ev.stage] || ev.stage) : 'Note');
        what = ev.text || '';
      } else {
        return;
      }
      if (!what) return;
      // One-line ticker mirrors the latest activity; full list is expandable.
      nowLine.textContent = who + ' — ' + what;
      const row = el('div', 'tbl-act-row' +
        (kind === 'step_fail' ? ' is-fail' : ''));
      row.innerHTML =
        '<span class="tbl-act-who">' + esc(who) + '</span>' +
        '<span class="tbl-act-what">' + esc(what.length > 140 ? what.slice(0, 140) + '…' : what) + '</span>';
      actList.prepend(row);
      actRows.push(row);
      if (actRows.length > MAX_ACT) actRows.shift().remove();
      reveal(row, { y: -6, d: 0.3 });
    }

    function finish(meta) {
      if (destroyed || doneAll) return;
      doneAll = true;
      const m = meta || {};
      chapters.forEach(function (rec, i) {
        if (rec.done) return;
        rec.chip.textContent = 'skipped';
        rec.chip.className = 'tbl-chip tbl-chip-skip';
        rec.toc.classList.add('is-skip');
        rec.body.innerHTML =
          '<p class="tbl-empty">This section produced no content.</p>';
      });
      cover.classList.add('is-done');
      const st = cover.querySelector('.tbl-cover-status');
      if (st) {
        const bits = [];
        bits.push(chapters.size + ' chapters');
        if (m.sources != null) bits.push(m.sources + ' sources');
        if (m.seconds != null) bits.push('in ' + m.seconds + 's');
        st.textContent = bits.length ? 'Published · ' + bits.join(' · ')
                                    : 'Published';
        if (nowLine) nowLine.textContent = 'Complete — textbook published';
      }
      reveal(cover, { d: 0.7 });
    }

    function fail(msg) {
      if (destroyed || doneAll) return;
      doneAll = true;
      const st = cover.querySelector('.tbl-cover-status');
      if (st) st.textContent = 'Stopped — ' + (msg || 'run ended early');
      if (nowLine) nowLine.textContent = 'Stopped';
      chapters.forEach(function (rec) {
        if (!rec.done) {
          rec.chip.textContent = 'stopped';
          rec.chip.className = 'tbl-chip tbl-chip-skip';
        }
      });
    }

    function destroy() { destroyed = true; root.remove(); }
    return { plan, section, activity, finish, fail, destroy, el: root };
  }

  /* ---------------- provisional skeleton (textbook page) ---------------- */

  function skeletonChapter(index, title) {
    const ch = el('article', 'tbl-chapter');
    ch.dataset.index = index;
    const head = el('div', 'tbl-ch-head');
    head.innerHTML =
      '<span class="tbl-ch-num">Chapter ' +
      String(index + 1).padStart(2, '0') + '</span>' +
      '<h3>' + esc(title || '…') + '</h3>' +
      '<span class="tbl-chip tbl-chip-wait">composing</span>';
    ch.appendChild(head);
    const body = el('div', 'tbl-ch-body');
    body.appendChild(chapterSkeletonBody());
    ch.appendChild(body);
    return ch;
  }

  function skeleton(host, opts) {
    opts = opts || {};
    const titles = Array.isArray(opts.chapters) ? opts.chapters : [];
    const root = el('div', 'tbl-pro');
    const cov = el('div', 'tbl-cover');
    cov.innerHTML =
      '<div class="tbl-cover-main">' +
      '<div class="tbl-cover-emb"></div>' +
      '<div class="tbl-cover-title"><span class="tbl-cover-kick">iChat Research</span>' +
      '<h1>' + esc(opts.title || 'Research textbook') + '</h1></div>' +
      '<div class="tbl-cover-status">Composing your textbook…</div>' +
      '<div class="tbl-prog"><div class="tbl-prog-bar"><i></i></div>' +
      '<span class="tbl-prog-pct">0%</span></div></div>' +
      '<div class="tbl-cover-act"><div class="tbl-ca-h">Live activity</div>' +
      '<div class="tbl-ca-list"></div></div>';
    root.appendChild(cov);
    const n = Math.max(titles.length, 3);
    for (let i = 0; i < n; i++)
      root.appendChild(skeletonChapter(i, titles[i]));
    host.appendChild(root);
    return root;
  }

  (window.Nova = window.Nova || {}).TextbookLive =
    { create, skeleton, skeletonChapter };
})();
