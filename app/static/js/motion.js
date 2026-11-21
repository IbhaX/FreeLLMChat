/* NovaMotion — shared GSAP motion layer for every app page.
   Load AFTER gsap.min.js. All helpers no-op when GSAP is missing or
   prefers-reduced-motion is set, so calls are always safe to make.

   Presets keep one motion language: durations, easings, staggers.
   Usage:
     NovaMotion.enter(el)                      — one-shot entrance
     NovaMotion.stagger(els)                   — staggered entrance
     NovaMotion.watch(host, selector, opts)    — animate nodes as appended
     NovaMotion.menu(el, open)                 — dropdown open/close
     NovaMotion.dialog(el)                     — modal entrance
     NovaMotion.press(scope)                   — button press feedback (once)
     NovaMotion.lift(el)                       — hover lift (fine pointers)
     NovaMotion.count(el, to)                  — animated number
     NovaMotion.pageIn(el)                     — page/shell entrance
     NovaMotion.veil(el, open)                 — overlay fade + panel pop
*/
(function () {
  'use strict';

  var rmQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  var fineQuery = window.matchMedia('(pointer: fine)');

  /* Duration scale — snappy for micro-interactions, generous for scenes. */
  var D = { tick: .12, fast: .2, med: .35, slow: .55, page: .7 };
  /* Easing scale — soft-out for entrances, expo for emphasis, no bounce spam. */
  var E = {
    out: 'power3.out',
    soft: 'power2.out',
    inout: 'power2.inOut',
    expo: 'expo.out',
    back: 'back.out(1.5)'
  };

  function ok() {
    return !!window.gsap && !rmQuery.matches;
  }
  function reduced() { return rmQuery.matches; }
  function fine() { return fineQuery.matches; }

  function gsap() { return window.gsap; }

  /* Mark nodes so replays never re-run an entrance. WeakSet → GC-safe. */
  var animated = typeof WeakSet !== 'undefined' ? new WeakSet() : null;
  function seen(el) {
    if (!el || el.nodeType !== 1) return true;
    if (!animated) return el.dataset && el.dataset.mo === '1';
    if (animated.has(el)) return true;
    animated.add(el);
    return false;
  }

  /** One-shot entrance. Returns the tween or null. */
  function enter(el, opts) {
    if (!ok() || !el || seen(el)) return null;
    var o = opts || {};
    return gsap().fromTo(el,
      { opacity: 0, y: o.y == null ? 14 : o.y, x: o.x || 0,
        scale: o.scale == null ? 1 : o.scale },
      { opacity: 1, y: 0, x: 0, scale: 1,
        duration: o.dur || D.med, ease: o.ease || E.out,
        delay: o.delay || 0, clearProps: 'opacity,transform',
        onComplete: o.done });
  }

  /** Staggered entrance for a list of nodes. */
  function stagger(els, opts) {
    if (!ok()) return null;
    var list = els && els.length ? Array.prototype.slice.call(els) : [];
    var fresh = list.filter(function (el) { return !seen(el); });
    if (!fresh.length) return null;
    var o = opts || {};
    return gsap().fromTo(fresh,
      { opacity: 0, y: o.y == null ? 16 : o.y, x: o.x || 0,
        scale: o.scale == null ? 1 : o.scale },
      { opacity: 1, y: 0, x: 0, scale: 1,
        duration: o.dur || D.med, ease: o.ease || E.out,
        stagger: o.stagger == null ? .05 : o.stagger,
        delay: o.delay || 0, clearProps: 'opacity,transform',
        onComplete: o.done });
  }

  /* Watch a container: every appended node matching selector gets an
     entrance once. `filter` can veto nodes (e.g. hydration replays).
     Returns a disconnect function. Streaming edits inside existing
     nodes do NOT retrigger — only childList additions do. */
  function watch(host, selector, opts) {
    if (!ok() || !host || typeof MutationObserver === 'undefined') {
      return function () {};
    }
    var o = opts || {};
    var mo = new MutationObserver(function (muts) {
      var fresh = [];
      muts.forEach(function (m) {
        Array.prototype.forEach.call(m.addedNodes, function (n) {
          if (n.nodeType !== 1 || seen(n)) return;
          if (selector && !n.matches(selector)) {
            var inner = n.querySelector && n.querySelector(selector);
            if (!inner) return;
            n = inner;
            if (seen(n)) return;
          }
          if (o.filter && !o.filter(n)) return;
          fresh.push(n);
        });
      });
      if (!fresh.length) return;
      /* Cap batch work — long hydration lists only animate the tail. */
      var animate = o.max && fresh.length > o.max ? fresh.slice(-o.max) : fresh;
      if (o.onEach) animate.forEach(function (n) { o.onEach(n); });
      if (o.batch && animate.length > 1) stagger(animate, o);
      else animate.forEach(function (n) { enter(n, o); });
    });
    mo.observe(host, { childList: true, subtree: !!o.subtree });
    return function () { mo.disconnect(); };
  }

  /** Dropdown/menu open+close. Keeps [hidden] contract intact. */
  function menu(el, open) {
    if (!ok() || !el) return;
    if (open) {
      gsap().fromTo(el,
        { opacity: 0, y: -6, scale: .98 },
        { opacity: 1, y: 0, scale: 1, duration: D.fast,
          ease: E.out, clearProps: 'opacity,transform' });
    } else {
      gsap().to(el, { opacity: 0, y: -4, scale: .98,
        duration: D.tick, ease: E.soft });
    }
  }

  /** Native <dialog> or .open-class modal entrance. */
  function dialog(el) {
    if (!ok() || !el) return;
    gsap().fromTo(el,
      { opacity: 0, y: 18, scale: .96 },
      { opacity: 1, y: 0, scale: 1, duration: D.med,
        ease: E.expo, clearProps: 'opacity,transform' });
  }

  /** Overlay veils (roleplay scenesVeil/charVeil pattern): fade veil,
      pop the first panel child. `open` toggles direction. */
  function veil(el, open) {
    if (!ok() || !el) return;
    var panel = el.querySelector('.st-veil-panel, .veil-panel, .panel, :scope > *');
    if (open) {
      gsap().fromTo(el, { opacity: 0 },
        { opacity: 1, duration: D.fast, ease: E.soft });
      if (panel) gsap().fromTo(panel,
        { opacity: 0, y: 22, scale: .97 },
        { opacity: 1, y: 0, scale: 1, duration: D.med,
          ease: E.expo, delay: .04, clearProps: 'opacity,transform' });
    } else {
      gsap().to(el, { opacity: 0, duration: D.tick, ease: E.soft,
        onComplete: function () { gsap().set(el, { clearProps: 'opacity' }); } });
    }
  }

  /* Delegated press feedback — attach once to a scope (default body):
     any matching button/pressable squashes slightly on pointerdown.
     Selector is deliberately narrow: explicit .ui-btn / .lp-btn / role=button. */
  var pressed = typeof WeakSet !== 'undefined' ? new WeakSet() : null;
  function press(scope) {
    if (!ok() || !fine()) return;
    var root = scope || document;
    if (pressed && pressed.has(root)) return;
    if (pressed) pressed.add(root);
    var sel = '.ui-btn,.lp-btn,.btn,[data-press]';
    root.addEventListener('pointerdown', function (e) {
      var b = e.target.closest && e.target.closest(sel);
      if (!b || b.disabled) return;
      gsap().to(b, { scale: .96, duration: D.tick, ease: E.soft });
    });
    root.addEventListener('pointerup', function (e) {
      var b = e.target.closest && e.target.closest(sel);
      if (!b) return;
      gsap().to(b, { scale: 1, duration: D.fast, ease: E.back });
    });
    root.addEventListener('pointerleave', function (e) {
      var b = e.target.closest && e.target.closest(sel);
      if (!b) return;
      gsap().to(b, { scale: 1, duration: D.fast, ease: E.soft });
    }, true);
  }

  /** Hover lift for cards — desktop pointers only, subtle by design. */
  function lift(el, opts) {
    if (!ok() || !fine() || !el || (pressed && pressed.has(el))) return;
    if (pressed) pressed.add(el);
    var o = opts || {};
    var y = o.y == null ? -3 : o.y;
    el.addEventListener('pointerenter', function () {
      gsap().to(el, { y: y, scale: o.scale || 1.01,
        duration: D.fast, ease: E.out });
    });
    el.addEventListener('pointerleave', function () {
      gsap().to(el, { y: 0, scale: 1, duration: D.med, ease: E.soft });
    });
  }
  /** lift() every current element matching selector. */
  function liftAll(selector, root, opts) {
    if (!ok() || !fine()) return;
    (root || document).querySelectorAll(selector)
      .forEach(function (el) { lift(el, opts); });
  }

  /** Animated number counter — used for dashboard stats. */
  function count(el, to, opts) {
    if (!el) return;
    if (!ok()) { el.textContent = String(to); return; }
    var o = opts || {};
    var from = o.from == null ? 0 : o.from;
    var fmt = o.fmt || function (v) { return Math.round(v).toLocaleString(); };
    var obj = { v: from };
    gsap().to(obj, { v: to, duration: o.dur || D.slow, ease: E.out,
      onUpdate: function () { el.textContent = fmt(obj.v); },
      onComplete: function () { el.textContent = fmt(to); } });
  }

  /** Page-level entrance for a mounted shell/page container. */
  function pageIn(el, opts) {
    if (!ok() || !el) return;
    var o = opts || {};
    gsap().fromTo(el,
      { opacity: 0, y: o.y == null ? 10 : o.y },
      { opacity: 1, y: 0, duration: o.dur || D.med, ease: E.soft,
        clearProps: 'opacity,transform' });
  }

  /** Sidebar slide-in + staggered nav items (admin shell mount). */
  function sidebar(nav) {
    if (!ok() || !nav || seen(nav)) return;
    var items = nav.querySelectorAll('.ui-navitem,.nav-sec');
    gsap().fromTo(nav, { x: -18, opacity: 0 },
      { x: 0, opacity: 1, duration: D.med, ease: E.out,
        clearProps: 'opacity,transform' });
    if (items.length) stagger(items,
      { x: -10, y: 0, dur: D.fast, stagger: .025, delay: .06 });
  }

  /* Typing-indicator dots: keeps the DOM the app already emits
     (.typing > i×3) but replaces CSS steps with a GSAP loop — smoother. */
  var typingSeen = typeof WeakSet !== 'undefined' ? new WeakSet() : null;
  function typing(row) {
    var dots = row && row.querySelectorAll ? row.querySelectorAll('.typing i, .nova-typing i') : [];
    if (!ok() || !dots.length) return;
    if (typingSeen) {
      var marked = false;
      dots.forEach(function (d) {
        if (typingSeen.has(d)) marked = true; else typingSeen.add(d);
      });
      if (marked) return;
    }
    gsap().to(dots, { y: -4, opacity: .35, duration: .3,
      ease: 'sine.inOut', yoyo: true, repeat: -1, stagger: .12 });
  }

  /** Shake — error/denied feedback on an element. */
  function shake(el) {
    if (!ok() || !el) return;
    gsap().fromTo(el, { x: 0 },
      { x: 0, duration: .4, ease: 'power1.inOut',
        keyframes: [{ x: -7 }, { x: 6 }, { x: -4 }, { x: 3 }, { x: 0 }] });
  }

  /** Attention pulse — e.g. live-status dot while a run streams. */
  function pulse(el, opts) {
    if (!ok() || !el) return null;
    var o = opts || {};
    return gsap().to(el, {
      scale: o.scale || 1.15, opacity: o.opacity == null ? .55 : o.opacity,
      duration: o.dur || .8, ease: 'sine.inOut',
      yoyo: true, repeat: -1 });
  }

  /** Flip-helper: animate element between two layout states safely.
      Uses GSAP core only (no Flip plugin) — measures + inverts delta. */
  function flipTo(el, mutate, opts) {
    if (!ok() || !el) { if (mutate) mutate(); return; }
    var first = el.getBoundingClientRect();
    mutate();
    var last = el.getBoundingClientRect();
    var dx = first.left - last.left, dy = first.top - last.top;
    var dw = first.width / (last.width || first.width || 1);
    var dh = first.height / (last.height || first.height || 1);
    if (!dx && !dy && dw === 1 && dh === 1) return;
    var o = opts || {};
    gsap().fromTo(el,
      { x: dx, y: dy, scaleX: dw, scaleY: dh, transformOrigin: 'top left' },
      { x: 0, y: 0, scaleX: 1, scaleY: 1, duration: o.dur || D.med,
        ease: o.ease || E.inout, clearProps: 'transform,transformOrigin' });
  }

  /* Auto-init: press feedback for .ui-btn/.lp-btn everywhere, no per-page
     wiring needed. Safe double-call — press() dedupes via WeakSet. */
  function auto() { press(document); }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', auto);
  } else auto();

  window.NovaMotion = Object.freeze({
    D: D, E: E,
    ok: ok, reduced: reduced, fine: fine,
    enter: enter, stagger: stagger, watch: watch,
    menu: menu, dialog: dialog, veil: veil,
    press: press, lift: lift, liftAll: liftAll,
    count: count, pageIn: pageIn, sidebar: sidebar,
    typing: typing, shake: shake, pulse: pulse, flipTo: flipTo
  });
})();
