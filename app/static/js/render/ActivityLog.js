/* ActivityLog — live agent step timeline (activity-log v2).
 *
 * Renders a vertical, auto-updating feed of normalized step events:
 * per-kind icon + color, status glyph (spinner/check/fail/wait), nested
 * groups, progressive detail, live elapsed timers, summary header,
 * filters + verbose toggle, scroll-up pause with jump-to-latest.
 *
 * Event contract: see docs/ACTIVITY_LOG.md. ingest() accepts both
 * normalized events (kind/status/title present, as enriched by
 * services/activity.py) and legacy emit dicts ({event,text,stage,…}) —
 * the JS normalizer mirrors the Python mapping so un-tapped paths
 * (plain chat, group chat) still render structured steps.
 */
(function () {
  'use strict';
  const NS = (window.Nova = window.Nova || {});

  const KINDS = {
    think:  { icon: '◐', label: 'Thinking' },
    plan:   { icon: '≡', label: 'Planning' },
    search: { icon: '⌕', label: 'Searching' },
    read:   { icon: '≣', label: 'Reading' },
    write:  { icon: '✎', label: 'Writing' },
    run:    { icon: '▶', label: 'Running' },
    decide: { icon: '↯', label: 'Deciding' },
    wait:   { icon: '⏸', label: 'Waiting' },
    error:  { icon: '✕', label: 'Error' },
    result: { icon: '✓', label: 'Result' },
    usage:  { icon: '◈', label: 'Model usage' },
    note:   { icon: '·', label: 'Note' },
  };
  // Interactive-channel events (finding/answer) render in LiveRun, not here.
  const CHANNEL_SKIP = new Set(['finding', 'answer']);
  const STAGE_KIND = {
    route: 'decide', clarifying: 'wait', clarify: 'wait', plan: 'plan',
    planning: 'plan', awaiting_approval: 'wait', research: 'search',
    researching: 'search', research_done: 'result', gap: 'think',
    verify: 'run', verifying: 'run', outline: 'plan', outlining: 'plan',
    synthesize: 'write', writing: 'write', editing: 'write',
    building_artifacts: 'run', done: 'result', state: 'plan', synth: 'write',
  };
  const TOOL_KIND = {
    web_search: ['search', 'Searching the web'],
    search: ['search', 'Searching the web'],
    exa_search: ['search', 'Searching with Exa'],
    academic_search: ['search', 'Searching academic papers'],
    http_get: ['read', 'Fetching a page'],
    fetch_page: ['read', 'Fetching a page'],
    browse: ['read', 'Browsing a page'],
    read_pdf: ['read', 'Reading a PDF'],
    read_file: ['read', 'Reading a file'],
    calculator: ['run', 'Calculating'],
    run_python: ['run', 'Running Python'],
    get_datetime: ['read', 'Checking the time'],
    generate_image: ['write', 'Generating an image'],
    generate_audio: ['write', 'Generating audio'],
  };
  const STATUS_GLYPH = {
    running: '', done: '✓', failed: '✕',
    skipped: '—', waiting: '⏸', queued: '○', cancelled: '⏹',
  };
  const STATUS_WORD = {
    running: 'in progress', done: 'done', failed: 'failed',
    skipped: 'skipped', waiting: 'waiting', queued: 'queued',
    cancelled: 'cancelled',
  };

  const DETAIL_LABEL = {
    plan: 'Plan', search: 'Queries', read: 'Sources', write: 'Outline',
    decide: 'Reasoning', error: 'What went wrong', result: 'Result',
    run: 'Details', think: 'Thinking', wait: 'Waiting on',
  };

  /* "max_results='5', query='x'" → "x": show the one argument a human
     cares about; fall back to the raw string. */
  const KEY_ARGS = ['query', 'q', 'url', 'path', 'expression', 'code', 'prompt', 'text'];
  function prettyArgs(targs) {
    const s = String(targs || '');
    for (const k of KEY_ARGS) {
      const m = s.match(new RegExp('(?:^|[,{\\s])"?' + k + '"?\\s*[=:]\\s*([\'"])(.*?)\\1'));
      if (m) return m[2];
    }
    return s;
  }

  function clip(s, n) {
    s = String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
    return s.length <= (n || 160) ? s : s.slice(0, (n || 160) - 1).trim() + '…';
  }
  const ERR_RE = /^(⚠|error|failed|research step failed|empty reply|fetch error|search error|exceeded|timed out)/i;

  /* JS mirror of services/activity.normalize(). */
  function normalize(ev) {
    ev = ev || {};
    const event = String(ev.event || ev.type || 'note');
    const stage = String(ev.stage || '');
    let actor = String(ev.actor || ev.agent || '');
    const text = String(ev.text || ev.detail || '');
    const sin = String(ev.status || '').toLowerCase();
    let kind = 'note', status = 'done', group = false, level = 'normal';
    let title = clip(text || event), parent = ev.parent || null;
    const meta = {};
    if (stage) meta.stage = stage;
    if (ev.sub) parent = (stage || 'research') + ':' + ev.sub;
    let duration = ev.duration_ms != null ? ev.duration_ms : null;

    if (event === 'stage' || (stage && event === 'state')) {
      kind = STAGE_KIND[stage || event] || 'note';
      status = (stage === 'research_done' || stage === 'done') ? 'done' : 'running';
      group = true; parent = null;
      if (ev.skipped) status = 'skipped';
    } else if (event === 'state') {
      kind = 'plan'; status = 'running'; group = true;
      title = clip(text || ('state → ' + (ev.state || '')));
    } else if (event === 'tool') {
      let tname = String(ev.tool || ''), targs = String(ev.args || '');
      if (!tname) {
        const m = text.match(/^[🔧🛠]\s*([A-Za-z_][\w]*)/);
        if (m) {
          tname = m[1];
          const inner = text.match(/\((.*)\)\s*$/);
          if (!targs && inner) targs = inner[1];
        }
      }
      const tk = TOOL_KIND[tname] || ['run', 'Running ' + (tname || 'tool')];
      kind = tk[0];
      title = targs ? tk[1] + ': ' + clip(prettyArgs(targs), 90) : tk[1];
      status = (ev.ok === false || ERR_RE.test(String(ev.result || ''))) ? 'failed' : 'done';
      meta.tool = tname; meta.args = targs;
      if (ev.result) meta.result_preview = clip(ev.result, 400);
    } else if (event === 'step_start') {
      kind = 'run'; status = 'running'; group = true; parent = null;
      title = clip(text || (actor + ' started'));
    } else if (event === 'step_done') {
      kind = 'run'; status = 'done';
      title = clip(text || (actor + ' finished'));
    } else if (event === 'step_fail') {
      kind = 'error'; status = 'failed';
      title = clip(text || (actor + ' failed'));
    } else if (event === 'step_queued') {
      kind = 'run'; status = 'queued'; group = true;
      title = clip(text || (actor ? actor + ' queued' : 'Step queued'));
    } else if (event === 'tool_start') {
      let tname = String(ev.tool || ''), targs = String(ev.args || '');
      const tk = TOOL_KIND[tname] || ['run', 'Running ' + (tname || 'tool')];
      kind = tk[0];
      title = targs ? tk[1] + ': ' + clip(prettyArgs(targs), 90) : tk[1];
      status = 'running';
      meta.tool = tname; meta.args = targs;
      if (ev.actor) actor = ev.actor;
    } else if (event === 'handoff') {
      kind = 'decide'; status = 'done';
      title = clip(text || ev.title || 'Handoff');
      ['from_task', 'to_task', 'target'].forEach(f => {
        if (ev[f]) meta[f] = ev[f]; });
    } else if (event === 'finding' || event === 'source_discovered' ||
               event === 'answer_delta' || event === 'answer_section') {
      // Interactive channels — LiveRun owns these; keep a quiet note here.
      kind = 'note'; status = 'done'; level = 'verbose';
      title = clip(text || event);
    } else if (event === 'synth') {
      kind = 'write'; status = 'running'; group = true;
    } else if (event === 'agent_start') {
      kind = 'run'; status = 'running'; group = true;
      title = clip(text || (actor + ' is working'));
    } else if (event === 'agent_message') {
      kind = 'result'; status = 'done';
      title = clip(text || (actor + ' replied'));
    } else if (event === 'research_done') {
      kind = 'result'; status = 'done';
      title = clip(text || 'Research complete');
    } else if (event === 'research_answer' || event === 'report.ready') {
      kind = 'result'; status = 'done';
      title = clip(text || 'Report ready');
    } else if (event === 'error' || event === 'degraded') {
      kind = 'error'; status = 'failed';
      title = clip(text || 'Something failed');
    } else if (event === 'steer') {
      kind = 'decide'; status = 'done';
      title = clip(text || 'User steered the run');
    } else if (event === 'usage') {
      kind = 'usage'; status = 'done';
      const tot = ev.tokens_total != null ? ev.tokens_total
        : (Number(ev.tokens_in) || 0) + (Number(ev.tokens_out) || 0);
      title = clip(text ||
        ((ev.model ? ev.model + ' · ' : '') + tot.toLocaleString() + ' tokens'));
      meta.model = ev.model || ''; meta.provider = ev.provider || '';
      meta.key_label = ev.key_label || '';
      meta.tokens_in = ev.tokens_in || 0; meta.tokens_out = ev.tokens_out || 0;
      meta.tokens_total = tot;
      if (ev.cost_usd != null) meta.cost_usd = ev.cost_usd;
      if (ev.currency) meta.currency = ev.currency;
      actor = actor || 'model';
    } else if (event === 'plan') {
      kind = 'plan'; status = 'done';
      title = clip(text || 'Plan ready');
    } else if (event === 'budget') {
      kind = 'note'; status = 'done'; level = 'verbose';
    } else if (event === 'try' || event === 'retry' || event === 'wait' || event === 'rate_limited') {
      kind = 'wait'; status = 'waiting'; level = 'verbose';
    } else if (event === 'routing' || event === 'next') {
      kind = 'decide'; status = 'done'; level = 'verbose';
    } else if (event === 'skip') {
      kind = 'decide'; status = 'skipped'; level = 'verbose';
    } else if (event === 'fail') {
      kind = 'error'; status = 'failed'; level = 'verbose';
    } else if (ERR_RE.test(text)) {
      kind = 'error'; status = 'failed';
    }
    if ((sin === 'fail' || sin === 'failed' || sin === 'error') && status !== 'failed') {
      status = 'failed';
      if (kind === 'note' || kind === 'decide') kind = 'error';
    } else if (sin === 'skip' && status !== 'skipped') {
      status = 'skipped';
    } else if (sin === 'cancel' && status !== 'cancelled' && status !== 'failed') {
      status = 'cancelled';
    }
    const rawDetail = ev.detail && typeof ev.detail === 'object'
      ? JSON.stringify(ev.detail, null, 2) : ev.detail;
    const detail = rawDetail ? String(rawDetail)
      : (text.length > 170 ? text.slice(0, 2000) : '');
    return {
      kind: kind, status: status, title: title || (KINDS[kind] || {}).label || 'Step',
      detail: detail.slice(0, 2000),
      actor: actor, parent: parent, group: group, level: level,
      duration_ms: duration, ts: ev.ts || new Date().toISOString(), meta: meta,
    };
  }

  function mergeData(ev) {
    // Enriched events already carry normalized fields; fill gaps client-side.
    const n = normalize(ev);
    for (const k of ['kind', 'status', 'title', 'detail', 'actor', 'parent',
                     'group', 'level', 'duration_ms', 'ts'])
      if (ev[k] !== undefined && ev[k] !== null && ev[k] !== '') n[k] = ev[k];
    if (ev.meta) n.meta = ev.meta;
    return n;
  }

  function fmtElapsed(ms) {
    const s = Math.max(0, Math.round(ms / 1000));
    return s >= 60 ? Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0')
                   : s + 's';
  }
  function fmtMs(ms) {
    return ms < 1000 ? Math.round(ms) + 'ms' : (ms / 1000).toFixed(1) + 's';
  }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /* ------------------------------------------------------------------ */

  function create(host, opts) {
    opts = opts || {};
    const root = document.createElement('div');
    root.className = 'alog';
    root.setAttribute('role', 'log');
    root.setAttribute('aria-label', 'Agent activity');
    host.appendChild(root);

    root.innerHTML =
      '<div class="alog-head">' +
        '<span class="alog-state" aria-hidden="true"></span>' +
        '<span class="alog-now">Working…</span>' +
        '<span class="alog-count" aria-hidden="true"></span>' +
        '<button type="button" class="alog-gear" title="Filters" aria-label="Filter steps">⚙</button>' +
      '</div>' +
      '<section class="alog-dash" aria-label="Run metrics">' +
        '<div class="alog-dm" data-m="elapsed"><span class="alog-dk">Elapsed</span><span class="alog-dv">0s</span></div>' +
        '<div class="alog-dm" data-m="steps"><span class="alog-dk">Steps</span><span class="alog-dv">0</span></div>' +
        '<div class="alog-dm" data-m="tools"><span class="alog-dk">Tools</span><span class="alog-dv">0</span></div>' +
        '<div class="alog-dm" data-m="calls"><span class="alog-dk">Model calls</span><span class="alog-dv">0</span></div>' +
        '<div class="alog-dm" data-m="tokens"><span class="alog-dk">Tokens</span><span class="alog-dv">0</span></div>' +
        '<div class="alog-dm" data-m="cost"><span class="alog-dk">Cost</span><span class="alog-dv">$0</span></div>' +
        '<div class="alog-dm w" data-m="model"><span class="alog-dk">Model</span><span class="alog-dv">—</span></div>' +
        '<div class="alog-dm w" data-m="latency"><span class="alog-dk">Avg latency</span><span class="alog-dv">—</span></div>' +
      '</section>' +
      '<div class="alog-tools" hidden>' +
        '<select class="alog-fkind" aria-label="Filter by step type"><option value="">all types</option></select>' +
        '<select class="alog-fstatus" aria-label="Filter by status"><option value="">any status</option></select>' +
        '<input class="alog-fq" type="search" placeholder="Search steps…" aria-label="Search steps">' +
        '<label class="alog-vb"><input type="checkbox" class="alog-fv"> verbose</label>' +
      '</div>' +
      '<div class="alog-steps"></div>' +
      '<button type="button" class="alog-jump" hidden>↓ jump to latest</button>';

    const stepsEl = root.querySelector('.alog-steps');
    const nowEl = root.querySelector('.alog-now');
    const countEl = root.querySelector('.alog-count');
    const stateEl = root.querySelector('.alog-state');
    const toolsEl = root.querySelector('.alog-tools');
    const jumpEl = root.querySelector('.alog-jump');
    const fKind = root.querySelector('.alog-fkind');
    const fStatus = root.querySelector('.alog-fstatus');
    const fQ = root.querySelector('.alog-fq');
    const fV = root.querySelector('.alog-fv');

    for (const k of Object.keys(KINDS))
      fKind.insertAdjacentHTML('beforeend',
        '<option value="' + k + '">' + KINDS[k].label.toLowerCase() + '</option>');
    for (const s of Object.keys(STATUS_GLYPH))
      if (s) fStatus.insertAdjacentHTML('beforeend',
        '<option value="' + s + '">' + STATUS_WORD[s] + '</option>');

    const steps = new Map();          // id -> step record
    const order = [];                 // top-level step records, in order
    const groups = new Map();         // parent key -> step record
    const taskIndex = new Map();      // task_id -> step record (lifecycle)
    const seenEvents = new Set();     // event_id dedupe (replay-safe)
    const MAX_SEEN = 2000;
    let openGroup = null;             // most recent running group
    let counter = 0;
    let doneCount = 0, total = 0;
    let paused = false;               // user scrolled up
    let finished = false;
    let t0 = Date.now();
    const tickFns = new Set();
    const ticker = setInterval(() => tickFns.forEach(f => f()), 500);

    /* ---------- live run metrics (the pinned dashboard card) ---------- */
    const stats = {
      calls: 0, tin: 0, tout: 0, cost: 0, costKnown: false, currency: '',
      latency: 0, latencyN: 0, models: new Map(), lastModel: '', lastProvider: '',
      tools: 0, toolsFailed: 0, failed: 0, inflight: '',
    };
    const dashEl = root.querySelector('.alog-dash');
    const dv = k => dashEl.querySelector('[data-m="' + k + '"] .alog-dv');

    /* Live model switching: opts.onModel turns the MODEL cell into a
       button; the host wires it to its picker (next call/turn picks the
       new selection up). */
    if (typeof opts.onModel === 'function') {
      const mcell = dashEl.querySelector('[data-m="model"]');
      mcell.classList.add('alog-mbtn');
      mcell.setAttribute('role', 'button');
      mcell.setAttribute('tabindex', '0');
      mcell.title = 'Change model';
      const fire = e => {
        /* Picker-open clicks must not reach the pages' document-level
           "close menu on outside click" handlers — same pattern as each
           picker's own trigger button (e.stopPropagation). */
        e.stopPropagation();
        opts.onModel();
      };
      mcell.addEventListener('click', fire);
      mcell.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fire(e); }
      });
    }

    function fmtCost(c) {
      if (!stats.costKnown) return '—';
      const cur = stats.currency && stats.currency !== 'USD' ? stats.currency + ' ' : '$';
      if (c === 0) return cur + '0';
      return c < 0.0001 ? '<' + cur + '0.0001' : cur + c.toFixed(c < 0.01 ? 4 : 3);
    }

    function trackUsage(d) {
      const m = d.meta || {};
      stats.calls++;
      stats.tin += Number(m.tokens_in) || 0;
      stats.tout += Number(m.tokens_out) || 0;
      if (m.cost_usd != null) { stats.cost += Number(m.cost_usd) || 0; stats.costKnown = true; }
      if (m.currency) stats.currency = m.currency;
      if (d.duration_ms != null) { stats.latency += d.duration_ms; stats.latencyN++; }
      if (m.model) {
        stats.lastModel = m.model;
        stats.models.set(m.model, (stats.models.get(m.model) || 0) + 1);
      }
      if (m.provider) stats.lastProvider = m.provider;
    }

    function trackStep(d, prevStatus) {
      if (d.meta && d.meta.tool && prevStatus === undefined) stats.tools++;
      if (d.status === 'failed' && prevStatus !== 'failed') {
        stats.failed++;
        if (d.meta && d.meta.tool) stats.toolsFailed++;
      }
    }

    function paintDash() {
      dv('elapsed').textContent = fmtElapsed((finished ? finishedAt : Date.now()) - t0);
      dv('steps').textContent = doneCount + '/' + total +
        (stats.failed ? ' · ' + stats.failed + ' failed' : '');
      dv('tools').textContent = stats.tools +
        (stats.toolsFailed ? ' · ' + stats.toolsFailed + ' failed' : '');
      dv('calls').textContent = String(stats.calls);
      const tot = stats.tin + stats.tout;
      dv('tokens').textContent = tot.toLocaleString();
      dv('tokens').title = stats.tin.toLocaleString() + ' in · ' +
        stats.tout.toLocaleString() + ' out';
      dv('cost').textContent = fmtCost(stats.cost);
      const others = stats.models.size - 1;
      dv('model').textContent = stats.inflight
        ? '… ' + stats.inflight
        : stats.lastModel
        ? stats.lastModel + (stats.lastProvider ? ' · ' + stats.lastProvider : '') +
          (others > 0 ? '  +' + others + ' more' : '')
        : '—';
      dv('model').title = [...stats.models].map(([k, v]) => k + ' ×' + v).join('\n');
      dv('latency').textContent = stats.latencyN
        ? fmtMs(stats.latency / stats.latencyN) + ' over ' + stats.latencyN + ' call' +
          (stats.latencyN > 1 ? 's' : '') + ' · ' + stats.tin.toLocaleString() + ' in / ' +
          stats.tout.toLocaleString() + ' out'
        : '—';
      dashEl.classList.toggle('live', !finished);
    }
    let finishedAt = 0;
    tickFns.add(paintDash);

    /* ---------- DOM helpers ---------- */

    function makeStepEl() {
      const el = document.createElement('div');
      el.className = 'alog-step';
      el.setAttribute('role', 'listitem');
      el.innerHTML =
        '<span class="alog-rail"><span class="alog-node"></span></span>' +
        '<button type="button" class="alog-exp" hidden aria-label="Toggle detail">›</button>' +
        '<div class="alog-main">' +
          '<div class="alog-line">' +
            '<span class="alog-title"></span>' +
            '<span class="alog-time" aria-hidden="true"></span>' +
          '</div>' +
          '<span class="alog-sr sr-only"></span>' +
          '<div class="alog-card" hidden></div>' +
          '<div class="alog-detail" hidden></div>' +
          '<div class="alog-kids"></div>' +
        '</div>';
      return el;
    }

    function applyStatus(rec, status) {
      const st = rec.data;
      st.status = status;
      rec.el.dataset.status = status;
      rec.node.textContent = STATUS_GLYPH[status] || '';
      rec.node.classList.toggle('spin', status === 'running');
      rec.sr.textContent = STATUS_WORD[status] || status;
      if (status === 'running') {
        rec.startedAt = rec.startedAt || Date.now();
        // One ticker per record: repeated running updates must not stack.
        if (!rec.unTick) {
          const tick = () => {
            rec.time.textContent = fmtElapsed(Date.now() - rec.startedAt);
          };
          tickFns.add(tick);
          rec.unTick = () => tickFns.delete(tick);
        }
      } else {
        if (rec.unTick) { rec.unTick(); rec.unTick = null; }
        const dur = st.duration_ms != null ? st.duration_ms
                  : rec.startedAt ? Date.now() - rec.startedAt : 0;
        rec.time.textContent = dur >= 1000 ? fmtElapsed(dur) : '';
        if ((status === 'done' || status === 'skipped') && !rec.counted &&
            st.kind !== 'usage') {
          rec.counted = true;
          doneCount++;
        }
        if (rec.data.group && rec.open && !rec.userToggled && status === 'done' && rec.el.isConnected)
          setOpen(rec, false);
      }
      paintHeader();
    }

    function applyKind(rec) {
      const k = rec.data.kind;
      const km = KINDS[k] || KINDS.note;
      rec.el.dataset.kind = k;
      rec.node.dataset.kind = k;
      rec.node.classList.add('k-' + k);
      rec.el.setAttribute('aria-label', km.label + ': ' + rec.data.title);
    }

    /* Inline "model card" for usage steps — always visible, no expand. */
    function usageCardHtml(d) {
      const m = d.meta || {};
      const row = (k, v, wide) =>
        '<div class="alog-ucell' + (wide ? ' w' : '') + '">' +
        '<span class="alog-ukey">' + k + '</span>' +
        '<span class="alog-uval">' + esc(v) + '</span></div>';
      const cur = m.currency && m.currency !== 'USD' ? m.currency + ' ' : '$';
      const cost = m.cost_usd != null
        ? (m.cost_usd < 0.01 ? '<$0.01' : cur + m.cost_usd.toFixed(4)) : '';
      const src = [m.provider, m.key_label].filter(Boolean).join(' · ');
      return '<div class="alog-usage" role="group" aria-label="Model usage">' +
        (m.model ? row('model', m.model, true) : '') +
        (src ? row('via', src, true) : '') +
        row('tokens',
            (m.tokens_in || 0).toLocaleString() + ' in · ' +
            (m.tokens_out || 0).toLocaleString() + ' out · ' +
            (m.tokens_total || 0).toLocaleString() + ' total', true) +
        (d.duration_ms != null ? row('latency', fmtElapsed(d.duration_ms)) : '') +
        (cost ? row('cost', cost) : '') +
        '</div>';
    }

    /* Next-level content only: the step's own payload (plan, queries,
       claims, tool I/O). Timestamps/actor are metadata, never "detail". */
    function detailHtml(rec) {
      const d = rec.data;
      const meta = d.meta || {};
      const sec = (label, body, mono) => body
        ? '<div class="alog-dsec"><div class="alog-dlabel">' + label + '</div>' +
          '<div class="alog-dbody' + (mono ? ' mono' : '') + '">' + esc(body) + '</div></div>'
        : '';
      let detail = d.detail;
      if (detail && typeof detail === 'object') detail = JSON.stringify(detail, null, 2);
      const out = [];
      if (detail && detail !== d.title) out.push(sec(DETAIL_LABEL[d.kind] || 'Details', detail));
      if (meta.args && d.kind !== 'usage') out.push(sec('Input', meta.args, true));
      if (meta.result_preview) out.push(sec('Output', meta.result_preview, true));
      return out.join('');
    }

    function renderDetail(rec) {
      const html = detailHtml(rec);
      const exp = rec.el.querySelector('.alog-exp');
      const box = rec.el.querySelector('.alog-detail');
      box.innerHTML = html;
      rec.hasDetail = !!html;
      syncExpander(rec);
      if (!exp._wired) {
        exp._wired = true;
        exp.addEventListener('click', () => { rec.userToggled = true; setOpen(rec, !rec.open); });
      }
    }

    function syncExpander(rec) {
      const exp = rec.el.querySelector('.alog-exp');
      const expandable = rec.hasDetail || rec.childCount > 0;
      exp.hidden = !expandable;
      exp.setAttribute('aria-expanded', String(!!rec.open));
      rec.el.classList.toggle('collapsed', !rec.open && rec.childCount > 0);
    }

    function setOpen(rec, on) {
      rec.open = on;
      rec.el.querySelector('.alog-detail').hidden = !(on && rec.hasDetail);
      rec.el.querySelector('.alog-exp').classList.toggle('open', on);
      syncExpander(rec);
    }

    function addStep(parentRec, data, id) {
      const rec = {
        id: id, data: data, el: makeStepEl(),
        node: null, sr: null, time: null, kids: null,
        childCount: 0, startedAt: null, unTick: null,
      };
      rec.node = rec.el.querySelector('.alog-node');
      rec.sr = rec.el.querySelector('.alog-sr');
      rec.time = rec.el.querySelector('.alog-time');
      rec.kids = rec.el.querySelector('.alog-kids');
      rec.el.querySelector('.alog-title').textContent = data.title;
      applyKind(rec);
      applyStatus(rec, data.status || 'done');
      if (data.level === 'verbose') rec.el.classList.add('v');
      if (data.group) rec.el.classList.add('grp');

      if (data.kind === 'usage') {
        const card = rec.el.querySelector('.alog-card');
        card.innerHTML = usageCardHtml(data);
        card.hidden = false;
      }

      // Running groups stay open so the live sub-step is visible; they
      // auto-collapse on completion unless the user toggled them.
      rec.open = !!data.group && data.status === 'running';
      renderDetail(rec);
      if (!rec.open) rec.el.querySelector('.alog-detail').hidden = true;

      const hostEl = parentRec ? parentRec.kids : stepsEl;
      hostEl.appendChild(rec.el);
      rec.el.classList.add('in');
      if (parentRec) {
        parentRec.childCount++;
        parentRec.el.classList.add('has-kids');
        // A plan produced inside a "Planning…" group IS that group's next
        // level: surface it on the parent's expansion too.
        if (data.kind === 'plan' && data.detail && !parentRec.data.detail) {
          parentRec.data.detail = data.detail;
          renderDetail(parentRec);
        }
        syncExpander(parentRec);
      } else {
        order.push(rec);
      }
      steps.set(id, rec);
      if (data.group) {
        groups.set(id, rec);
        // Only legacy unidentified groups auto-close each other; identified
        // running tasks are independent until their own end event.
        if (!data.task_id) {
          if (openGroup && openGroup !== rec && !openGroup.data.task_id &&
              openGroup.data.status === 'running')
            applyStatus(openGroup, 'done');
          openGroup = data.status === 'running' ? rec : openGroup;
        }
      }
      if (data.kind !== 'usage') total++;
      paintHeader();
      maybeScroll();
      return rec;
    }

    /* ---------- header / counts ---------- */

    function currentRunning() {
      for (let i = order.length - 1; i >= 0; i--) {
        const r = order[i];
        if (r.data.status === 'running') return r;
        if (r.data.group) {
          // look inside group children
          for (const c of r.kids.children) {
            const rec = c._rec;
            if (rec && rec.data.status === 'running') return rec;
          }
        }
      }
      return null;
    }

    function paintHeader() {
      const cur = currentRunning();
      const elapsed = fmtElapsed(Date.now() - t0);
      countEl.textContent = finished ? elapsed : '';
      if (finished) {
        nowEl.textContent = root.dataset.final || 'Done';
        stateEl.className = 'alog-state s-' + (root.dataset.finalStatus || 'done');
      } else if (cur) {
        nowEl.textContent = cur.data.title;
        stateEl.className = 'alog-state s-running';
      } else {
        nowEl.textContent = 'Working…';
        stateEl.className = 'alog-state s-running';
      }
      if (opts.onTick) opts.onTick({ total: total, done: doneCount, running: cur && cur.data.title });
    }

    /* ---------- auto-scroll ---------- */

    function maybeScroll() {
      if (!paused) stepsEl.scrollTop = stepsEl.scrollHeight;
    }
    stepsEl.addEventListener('scroll', () => {
      const nearBottom =
        stepsEl.scrollTop + stepsEl.clientHeight >= stepsEl.scrollHeight - 28;
      paused = !nearBottom;
      jumpEl.hidden = !paused || finished;
    });
    jumpEl.addEventListener('click', () => {
      paused = false;
      stepsEl.scrollTop = stepsEl.scrollHeight;
      jumpEl.hidden = true;
    });

    /* ---------- filters ---------- */

    function applyFilters() {
      const k = fKind.value, s = fStatus.value,
            q = fQ.value.trim().toLowerCase(), v = fV.checked;
      root.classList.toggle('show-verbose', v);
      steps.forEach(rec => {
        const d = rec.data;
        let show = true;
        if (d.level === 'verbose' && !v) show = false;
        if (k && d.kind !== k) show = false;
        if (s && d.status !== s) show = false;
        if (q && !(d.title + ' ' + (d.detail || '') + ' ' + (d.actor || ''))
              .toLowerCase().includes(q)) show = false;
        rec.el.hidden = !show;
      });
    }
    [fKind, fStatus, fQ, fV].forEach(el => {
      el.addEventListener('input', applyFilters);
      el.addEventListener('change', applyFilters);
    });
    root.querySelector('.alog-gear').addEventListener('click', () => {
      toolsEl.hidden = !toolsEl.hidden;
    });

    /* ---------- public api ---------- */

    const api = {
      el: root,
      ingest(ev) {
        if (!ev) return;
        try {
          // Interactive channels belong to LiveRun — never double-render.
          // Dispatch on the wire event, not the enriched presentation kind.
          if (CHANNEL_SKIP.has(ev.channel || '') || CHANNEL_SKIP.has(ev.event || '')) return;
          // Stable event_id dedupe: replayed/resumed streams never double.
          if (typeof ev.event_id === 'string' && ev.event_id) {
            if (seenEvents.has(ev.event_id)) return;
            seenEvents.add(ev.event_id);
            if (seenEvents.size > MAX_SEEN) seenEvents.delete(seenEvents.values().next().value);
          }
          const data = ev.kind ? mergeData(ev) : normalize(ev);
          // In-flight call visibility: a 'try' lands the moment a provider
          // attempt starts; usage/fail clear it.
          if (ev.event === 'try')
            stats.inflight = (ev.model || '') + (ev.provider ? ' · ' + ev.provider : '');
          else if (ev.event === 'usage' || ev.event === 'fail' || ev.event === 'done')
            stats.inflight = '';
          if (finished) {
            if (data.kind === 'usage') { trackUsage(data); paintDash(); }
            return;
          }
          if (data.kind === 'usage') {   // folded into the pinned metrics card
            trackUsage(data);
            data.level = 'verbose';
          }
          // Identified tasks: start/end/tool updates for the SAME
          // (run, task) update one row; they never auto-nest or close peers.
          const tid = ev.task_id || null;
          const tkey = tid ? (ev.run_id || '') + '|' + tid : null;
          if (tkey && taskIndex.has(tkey)) {
            const rec = taskIndex.get(tkey);
            const prev = rec.data.status;
            Object.assign(rec.data, data);
            if (ev.kind) rec.data.kind = data.kind;
            trackStep(rec.data, prev);
            applyKind(rec);
            rec.el.querySelector('.alog-title').textContent = rec.data.title;
            applyStatus(rec, data.status);
            renderDetail(rec);
            paintDash();
            return;
          }
          let parentRec = null;
          const ptid = ev.parent_task_id || null;
          if (ptid && taskIndex.has((ev.run_id || '') + '|' + ptid)) {
            parentRec = taskIndex.get((ev.run_id || '') + '|' + ptid);
          } else if (data.parent && groups.has(data.parent)) {
            parentRec = groups.get(data.parent);
          } else if (!data.group && openGroup && !tid && !ev.group) {
            // Legacy unidentified events only.
            parentRec = openGroup;
          }
          const id = tkey ? 't:' + tkey : ('s' + (++counter));
          const rec = addStep(parentRec, data, id);
          rec.el._rec = rec;
          if (tkey) taskIndex.set(tkey, rec);
          if (data.kind !== 'usage') trackStep(data, undefined);
          paintDash();
        } catch (_) { /* activity must never break the stream */ }
      },
      // ok: true (default) → success; false → failed; 'cancelled' → cancelled.
      // Unfinished work takes that terminal state — never a silent success.
      finish(label, ok) {
        if (finished) return;
        finished = true;
        stats.inflight = '';
        const term = ok === 'cancelled' ? 'cancelled' : ok === false ? 'failed' : 'done';
        steps.forEach(rec => {
          if (rec.data.status === 'running' || rec.data.status === 'waiting' ||
              rec.data.status === 'queued')
            applyStatus(rec, term);
        });
        root.dataset.final = label || 'Done in ' + fmtElapsed(Date.now() - t0);
        root.dataset.finalStatus = term;
        finishedAt = Date.now();
        paintDash();
        paintHeader();
        jumpEl.hidden = true;
      },
      fail(text) {
        api.ingest({ event: 'error', text: text });
        api.finish(text || 'Failed', false);
      },
      count() { return total; },
      stats() {
        return { calls: stats.calls, tokens_in: stats.tin, tokens_out: stats.tout,
                 cost_usd: stats.costKnown ? stats.cost : null, tools: stats.tools,
                 failed: stats.failed, steps: total, done: doneCount,
                 models: Object.fromEntries(stats.models) };
      },
      destroy() {
        clearInterval(ticker);
        root.remove();
      },
    };
    paintHeader();
    return api;
  }

  NS.ActivityLog = { create: create, normalize: normalize, KINDS: KINDS };
})();
