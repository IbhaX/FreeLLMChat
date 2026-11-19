/* LiveRun — progressive agent-interaction renderer (findings / sources /
 * answer draft) mounted beside the activity log and final answer.
 *
 * Contract: NovaRender.LiveRun.create(host, {scroller}) →
 *   { ingest(ev), finish(status), destroy() }
 *
 * Channels (set by services/activity.InteractionEmitter):
 *   activity — task lifecycle/tool/handoff (ActivityLog renders these)
 *   finding  — source-grounded intermediate results, PROVISIONAL
 *   answer   — progressive synthesis draft (answer_delta / answer_section)
 *
 * Dispatch uses the wire event name (ev.event), never the enriched
 * presentation kind. Dedupe is by stable event_id, scoped per run.
 * finish('done') clears the draft and collapses findings; failed/cancelled
 * keep the partial draft and findings visible with their status label.
 */
(function () {
  'use strict';
  const NR = (window.NovaRender = window.NovaRender || {});

  const MAX_FINDINGS = 100;
  const MAX_SOURCES = 50;
  const MAX_SEEN = 2000;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = String(text);
    return n;
  }
  // Only http(s) URLs are ever rendered as hrefs.
  function safeUrl(u) {
    try {
      const p = new URL(String(u || ''));
      return (p.protocol === 'http:' || p.protocol === 'https:') ? p.href : '';
    } catch (_) { return ''; }
  }
  function nearBottom(sc) {
    return !sc || sc.scrollHeight - sc.scrollTop - sc.clientHeight < 120;
  }

  function create(host, opts) {
    opts = opts || {};
    const scroller = opts.scroller || null;
    const root = el('div', 'liverun');
    root.innerHTML =
      '<div class="lr-findings" hidden>' +
        '<div class="lr-label">Findings so far — not yet verified</div>' +
        '<div class="lr-flist"></div>' +
      '</div>' +
      '<details class="lr-srcwrap" hidden><summary>Sources</summary><div class="lr-slist"></div></details>' +
      '<div class="lr-draft" hidden>' +
        '<div class="lr-label">Draft — updating</div>' +
        '<div class="lr-dbody"></div>' +
      '</div>';
    host.appendChild(root);

    const findingsBox = root.querySelector('.lr-findings');
    const flist = root.querySelector('.lr-flist');
    const srcwrap = root.querySelector('.lr-srcwrap');
    const slist = root.querySelector('.lr-slist');
    const draftBox = root.querySelector('.lr-draft');
    const dbody = root.querySelector('.lr-dbody');

    const seenEvents = new Set();      // run-scoped event_id dedupe
    const findings = new Map();        // run|result_id -> {node,data}
    const sources = new Map();         // run|source.id -> node
    const sections = new Map();        // run|section_id -> {node,body,index,sig}
    let sr = null;                     // StreamingRenderer for answer_delta
    let draftText = '';
    let finished = false;
    let destroyed = false;

    /* ---------------- findings ---------------- */

    function findingNode(data) {
      const n = el('div', 'lr-finding');
      const head = el('div', 'lr-fhead');
      const ec = data.evidence_class || 'unverified';
      head.append(el('span', 'lr-badge lr-' + ec, ec));
      if (data.actor) head.append(el('span', 'lr-actor', data.actor));
      n.append(head);
      n.append(el('div', 'lr-ftext', data.text || data.claim || ''));
      if (Array.isArray(data.sources) && data.sources.length) {
        const sl = el('div', 'lr-fsrc');
        data.sources.forEach(s => {
          const u = safeUrl(s && s.url);
          const a = el('a', u ? 'lr-link' : 'lr-link lr-nolink', (s && (s.title || s.url)) || '');
          if (u) { a.href = u; a.target = '_blank'; a.rel = 'noopener'; }
          sl.append(a);
        });
        n.append(sl);
      }
      return n;
    }

    function upsertFinding(ev) {
      const key = (ev.run_id || '') + '|' + (ev.result_id || ('anon:' + (ev.event_id || '')));
      const old = findings.get(key);
      if (!old && findings.size >= MAX_FINDINGS) return;
      // Merge: fields the update omits keep their last known value.
      const prev = old ? old.data : { text: '', evidence_class: 'unverified', actor: '', sources: [] };
      const data = {
        text: ev.text || ev.claim || prev.text,
        evidence_class: ev.evidence_class || prev.evidence_class,
        actor: ev.actor || prev.actor,
        sources: Array.isArray(ev.sources) && ev.sources.length ? ev.sources : prev.sources,
      };
      const node = findingNode(data);
      if (old && old.node.parentNode) old.node.replaceWith(node);   // one swap
      else flist.appendChild(node);
      findings.set(key, { node, data });
      findingsBox.hidden = false;
    }

    /* ---------------- sources ---------------- */

    function upsertSource(ev) {
      const s = (ev && ev.source && typeof ev.source === 'object') ? ev.source : ev;
      const url = safeUrl(s.url);
      if (!url) return;
      const key = (ev.run_id || '') + '|' + (s.id || url.slice(0, 120));
      if (sources.has(key)) return;
      if (sources.size >= MAX_SOURCES) return;
      const node = el('div', 'lr-source');
      const a = el('a', 'lr-link', s.title || url);
      a.href = url; a.target = '_blank'; a.rel = 'noopener';
      node.append(a);
      if (s.snippet) node.append(el('div', 'lr-snip', s.snippet));
      slist.appendChild(node);
      sources.set(key, node);
      srcwrap.hidden = false;
      srcwrap.open = true;
      srcwrap.open = true;
    }

    /* ---------------- answer draft ---------------- */

    function ensureDraftStream() {
      if (sr || !NR.StreamingRenderer) return sr;
      draftBox.hidden = false;
      dbody.innerHTML = '';
      sr = NR.StreamingRenderer.create(dbody, scroller);
      return sr;
    }

    function onAnswerDelta(ev) {
      const stream = ensureDraftStream();
      const piece = String(ev.text || '');
      if (!piece) return;
      draftText += piece;
      if (stream) {
        const near = nearBottom(scroller);
        stream.update(draftText);
        if (near && scroller) scroller.scrollTop = scroller.scrollHeight;
      }
    }

    function renderSectionBody(body, text, completed) {
      if (completed && NR.ResponseRenderer && NR.ResponseRenderer.renderMessage) {
        body.innerHTML = '';
        try {
          NR.ResponseRenderer.renderMessage(
            { role: 'assistant', content: text }, body,
            { showActions: false, showSuggestions: false });
          return;
        } catch (_) { body.innerHTML = ''; }
      }
      body.textContent = text;
    }

    function onAnswerSection(ev) {
      const sid = (ev.run_id || '') + '|' + (ev.section_id || ('sec:' + (ev.index != null ? ev.index : 'x')));
      const idx = typeof ev.index === 'number' ? ev.index : 0;
      const text = String(ev.text || '');
      const completed = ev.status === 'done';
      let rec = sections.get(sid);
      if (!rec) {
        const node = el('div', 'lr-section');
        node.append(el('div', 'lr-sectitle', ev.title || 'Section'));
        const body = el('div', 'lr-secbody');
        node.append(body);
        rec = { node, body, index: idx, sig: null };
        sections.set(sid, rec);
        // ordered insert by section index
        const after = [...sections.values()]
          .filter(r => r !== rec && r.node.parentNode === dbody && r.index > idx)
          .sort((a, b) => a.index - b.index)[0];
        if (after) dbody.insertBefore(node, after.node);
        else dbody.appendChild(node);
      }
      draftBox.hidden = false;
      const sig = (completed ? 'c:' : 'p:') + text;
      if (rec.sig !== sig) {
        rec.sig = sig;
        const near = nearBottom(scroller);
        renderSectionBody(rec.body, text, completed);
        if (near && scroller) scroller.scrollTop = scroller.scrollHeight;
      }
    }

    /* ---------------- public ---------------- */

    function ingest(ev) {
      if (!ev || finished || destroyed) return false;
      // Dispatch on the wire event name; the enriched presentation `kind`
      // (e.g. 'write') must never hide an answer/finding channel event.
      const event = ev.event || ev.type || ev.kind || '';
      const ch = ev.channel || (
        event === 'finding' || event === 'source_discovered' ? 'finding' :
        (event === 'answer_delta' || event === 'answer_section' ? 'answer' : ''));
      const isFinding = ch === 'finding';
      const isAnswer = ch === 'answer';
      if (!isFinding && !isAnswer) return false;
      // event_id dedupe — replays (resume/live stream) never double-render.
      const eid = ev.event_id;
      if (eid && typeof eid === 'string') {
        if (seenEvents.has(eid)) return true;
        seenEvents.add(eid);
        if (seenEvents.size > MAX_SEEN) {
          seenEvents.delete(seenEvents.values().next().value);
        }
      }
      if (event === 'source_discovered') { upsertSource(ev); return true; }
      if (event === 'answer_delta') { onAnswerDelta(ev); return true; }
      if (event === 'answer_section') { onAnswerSection(ev); return true; }
      if (isFinding) { upsertFinding(ev); return true; }
      return true;
    }

    function finish(status) {
      if (finished || destroyed) return;
      finished = true;
      const ok = status === 'done' || !status;
      if (sr) {
        try {
          if (ok) sr.abort(); else sr.finish(draftText);
        } catch (_) {}
        sr = null;
      }
      if (ok) {
        // Authoritative answer replaces the provisional draft.
        draftBox.hidden = true;
        dbody.innerHTML = '';
        sections.clear();
        draftText = '';
      }
      // Findings stay reachable: collapsed for success, labelled otherwise.
      if (findings.size) {
        const det = el('details', 'lr-final');
        det.open = !ok;
        const label = ok
          ? 'Findings gathered during the run (' + findings.size + ')'
          : 'Partial findings — run ' + (status || 'interrupted') + ' (' + findings.size + ')';
        det.append(el('summary', 'lr-label', label + ' — not independently verified'));
        // Move (not recreate) the existing finding nodes into the details.
        det.append(flist);
        findingsBox.innerHTML = '';
        findingsBox.appendChild(det);
        findingsBox.hidden = false;
      }
      // srcwrap lives at root level and is never detached by this path.
      if (sources.size) srcwrap.hidden = false;
    }

    function destroy() {
      if (destroyed) return;
      destroyed = true;
      if (sr) { try { sr.abort(); } catch (_) {} sr = null; }
      root.remove();
    }

    return { ingest, finish, destroy, el: root };
  }

  NR.LiveRun = { create };
})();
