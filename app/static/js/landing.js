/* ==========================================================================
   landing.js — Nova landing page
   GSAP 3 + ScrollTrigger motion system, Three.js hero scene, and all
   micro-interactions. Every dependency is optional: the page must render
   and stay fully usable if a CDN script fails to load.
   ========================================================================== */
(function () {
  "use strict";

  var q = function (s, r) { return (r || document).querySelector(s); };
  var qa = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  /* ---------- zoom clamp: keep page zoom within 50%–200% ---------- */
  /* Browser zoom can't be disabled outright; gate the input vectors     */
  /* (Ctrl+wheel / trackpad pinch / Ctrl +/-/=) on devicePixelRatio.     */
  var zoomBase = devicePixelRatio || 1;
  var ZOOM_MIN = .5, ZOOM_MAX = 2;
  var zoomBlocked = function (dir) {
    var lvl = (devicePixelRatio || 1) / zoomBase;
    return (dir < 0 && lvl <= ZOOM_MIN + .01) || (dir > 0 && lvl >= ZOOM_MAX - .01);
  };
  addEventListener("keydown", function (e) {
    if (!(e.ctrlKey || e.metaKey)) return;
    if ((e.key === "-" || e.key === "_") && zoomBlocked(-1)) e.preventDefault();
    if ((e.key === "=" || e.key === "+") && zoomBlocked(1)) e.preventDefault();
  });
  addEventListener("wheel", function (e) {
    if (e.ctrlKey && zoomBlocked(e.deltaY > 0 ? -1 : 1)) e.preventDefault();
  }, { passive: false });

  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var fine = window.matchMedia("(pointer: fine)").matches;
  var hasGsap = !!window.gsap;
  if (hasGsap && window.ScrollTrigger) gsap.registerPlugin(ScrollTrigger);

  /* ---------- nav: scrolled state + progress bar + burger ---------- */
  var nav = q("#lpNav");
  var prog = q("#lpProgress");
  var onScroll = function () {
    var y = window.scrollY || 0;
    if (nav) nav.classList.toggle("scrolled", y > 24);
    if (prog) {
      var h = document.documentElement.scrollHeight - innerHeight;
      prog.style.width = (h > 0 ? (y / h) * 100 : 0) + "%";
    }
  };
  addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  var burger = q("#lpBurger"), mmenu = q("#lpMmenu");
  if (burger && mmenu) {
    var setMenu = function (open) {
      burger.classList.toggle("open", open);
      mmenu.classList.toggle("open", open);
      burger.setAttribute("aria-expanded", open ? "true" : "false");
      burger.setAttribute("aria-label", open ? "Close menu" : "Open menu");
      document.body.style.overflow = open ? "hidden" : "";
    };
    burger.addEventListener("click", function () {
      setMenu(!mmenu.classList.contains("open"));
    });
    mmenu.addEventListener("click", function (e) {
      if (e.target === mmenu) setMenu(false);
    });
    qa("a", mmenu).forEach(function (a) {
      a.addEventListener("click", function () { setMenu(false); });
    });
    addEventListener("keydown", function (e) {
      if (e.key === "Escape") setMenu(false);
    });
  }

  /* ---------- ticker: duplicate track for a seamless loop ---------- */
  var ticker = q("#lpTicker");
  if (ticker) ticker.innerHTML += ticker.innerHTML;

  /* ---------- split the hero title into word spans ---------- */
  var title = q("#lpTitle");
  if (title) {
    /* keep <br> as a real line break; wrap every word (at any depth) in .w */
    var wrapWords = function (node) {
      Array.prototype.slice.call(node.childNodes).forEach(function (c) {
        if (c.nodeType === 3) {
          var out = document.createDocumentFragment();
          c.textContent.split(/(\s+)/).forEach(function (tok) {
            if (!tok) return;
            if (/^\s+$/.test(tok)) { out.append(document.createTextNode(" ")); return; }
            var s = document.createElement("span");
            s.className = "w";
            s.textContent = tok;
            out.append(s);
          });
          c.replaceWith(out);
        } else if (c.nodeType === 1 && c.tagName !== "BR" && c.className !== "w") {
          wrapWords(c);
        }
      });
    };
    wrapWords(title);
  }

  /* ---------- entrance timeline ---------- */
  if (hasGsap && !reduced) {
    var tl = gsap.timeline({ defaults: { ease: "power3.out" } });
    tl.fromTo(".lp-nav-in", { y: -18, opacity: 0 }, { y: 0, opacity: 1, duration: .7 })
      .fromTo(".lp-eyebrow.hero", { y: 16, opacity: 0 }, { y: 0, opacity: 1, duration: .6 }, "-=.4")
      .fromTo(".lp-title .w", { y: 56, opacity: 0, rotateX: -40 },
        { y: 0, opacity: 1, rotateX: 0, duration: .9, stagger: .055 }, "-=.35")
      .fromTo(".lp-sub", { y: 24, opacity: 0 }, { y: 0, opacity: 1, duration: .7 }, "-=.55")
      .fromTo(".lp-cta .lp-btn", { y: 20, opacity: 0 }, { y: 0, opacity: 1, duration: .6, stagger: .1 }, "-=.45")
      .fromTo(".lp-hero-stats > div", { y: 18, opacity: 0 }, { y: 0, opacity: 1, duration: .5, stagger: .08 }, "-=.35")
      .fromTo(".lp-float", { y: 18, opacity: 0, scale: .92 },
        { y: 0, opacity: 1, scale: 1, duration: .7, stagger: .12, ease: "back.out(1.6)" }, "-=.4")
      .fromTo(".lp-scroll", { opacity: 0 }, { opacity: 1, duration: .8 }, "-=.3");

    // floats drift forever
    qa(".lp-float").forEach(function (f, i) {
      gsap.to(f, {
        y: (i % 2 ? -14 : 14), duration: 3 + i * .6,
        repeat: -1, yoyo: true, ease: "sine.inOut",
      });
    });
    // hero content lifts + fades slightly on scroll
    if (window.ScrollTrigger) {
      gsap.to(".lp-hero-in", {
        y: -70, opacity: .25, ease: "none",
        scrollTrigger: { trigger: "#lpHero", start: "top top", end: "bottom top", scrub: true },
      });
    }
  }

  /* ---------- scroll reveals ---------- */
  var revs = qa(".lp-rev");
  if (hasGsap && window.ScrollTrigger && !reduced) {
    revs.forEach(function (el, i) {
      gsap.fromTo(el, { y: 34, opacity: 0 }, {
        y: 0, opacity: 1, duration: .8, ease: "power3.out",
        delay: (i % 4) * .07,
        scrollTrigger: { trigger: el, start: "top 88%", once: true },
      });
    });
  } else {
    revs.forEach(function (el) { el.style.opacity = 1; el.style.transform = "none"; });
  }

  /* ---------- animated counters ---------- */
  qa("[data-count]").forEach(function (el) {
    var end = parseFloat(el.getAttribute("data-count")) || 0;
    var suf = el.getAttribute("data-suffix") || "";
    var done = function () { el.textContent = String(end) + suf; };
    if (!hasGsap || !window.ScrollTrigger || reduced) { done(); return; }
    var o = { v: 0 };
    gsap.to(o, {
      v: end, duration: 1.6, ease: "power2.out",
      scrollTrigger: { trigger: el, start: "top 90%", once: true },
      onUpdate: function () { el.textContent = Math.round(o.v) + suf; },
      onComplete: done,
    });
  });

  /* ---------- magnetic buttons (fine pointers only) ---------- */
  if (hasGsap && fine && !reduced) {
    qa("[data-mag]").forEach(function (b) {
      b.addEventListener("mousemove", function (e) {
        var r = b.getBoundingClientRect();
        gsap.to(b, {
          x: (e.clientX - r.left - r.width / 2) * .25,
          y: (e.clientY - r.top - r.height / 2) * .35,
          duration: .4, ease: "power2.out",
        });
      });
      b.addEventListener("mouseleave", function () {
        gsap.to(b, { x: 0, y: 0, duration: .6, ease: "elastic.out(1,.45)" });
      });
    });
  }

  /* ---------- tilt + spotlight cards ---------- */
  if (fine && !reduced) {
    qa("[data-tilt]").forEach(function (c) {
      c.addEventListener("mousemove", function (e) {
        var r = c.getBoundingClientRect();
        var px = (e.clientX - r.left) / r.width;
        var py = (e.clientY - r.top) / r.height;
        c.style.setProperty("--mx", (px * 100) + "%");
        c.style.setProperty("--my", (py * 100) + "%");
        if (hasGsap) {
          gsap.to(c, {
            rotateY: (px - .5) * 7, rotateX: (.5 - py) * 7,
            transformPerspective: 700, duration: .4, ease: "power2.out",
          });
        }
      });
      c.addEventListener("mouseleave", function () {
        if (hasGsap) gsap.to(c, { rotateX: 0, rotateY: 0, duration: .6, ease: "power2.out" });
      });
    });
  }

  /* ---------- cursor glow ---------- */
  var cursor = q("#lpCursor");
  if (cursor && fine && !reduced && hasGsap) {
    var cx = gsap.quickTo(cursor, "left", { duration: .5, ease: "power3.out" });
    var cy = gsap.quickTo(cursor, "top", { duration: .5, ease: "power3.out" });
    addEventListener("mousemove", function (e) { cx(e.clientX); cy(e.clientY); });
  }

  /* ---------- showcase parallax ---------- */
  var panel = q("#lpShowPanel");
  if (panel && hasGsap && window.ScrollTrigger && !reduced) {
    gsap.fromTo(panel, { rotateX: 6, scale: .96, y: 30 }, {
      rotateX: 0, scale: 1, y: 0, ease: "none",
      scrollTrigger: {
        trigger: "#showcase", start: "top 80%", end: "top 20%", scrub: true,
      },
    });
  }

  /* ---------- testimonial slider ---------- */
  var quotes = qa(".lp-quote");
  var qi = 0, qTimer = null;
  var showQ = function (n) {
    qi = (n + quotes.length) % quotes.length;
    quotes.forEach(function (el, i) { el.classList.toggle("on", i === qi); });
  };
  if (quotes.length) {
    var prev = q("#lpQPrev"), next = q("#lpQNext");
    if (prev) prev.addEventListener("click", function () { showQ(qi - 1); restartQ(); });
    if (next) next.addEventListener("click", function () { showQ(qi + 1); restartQ(); });
    var restartQ = function () {
      if (qTimer) clearInterval(qTimer);
      if (!reduced) qTimer = setInterval(function () { showQ(qi + 1); }, 7000);
    };
    restartQ();
  }

  /* ---------- FAQ accordion (keyboard-accessible) ---------- */
  qa(".lp-faq-item").forEach(function (item) {
    var btn = q(".lp-faq-q", item), ans = q(".lp-faq-a", item);
    if (!btn || !ans) return;
    btn.addEventListener("click", function () {
      var open = item.classList.contains("open");
      qa(".lp-faq-item.open").forEach(function (o) {
        o.classList.remove("open");
        q(".lp-faq-q", o).setAttribute("aria-expanded", "false");
        q(".lp-faq-a", o).style.maxHeight = "0";
      });
      if (!open) {
        item.classList.add("open");
        btn.setAttribute("aria-expanded", "true");
        ans.style.maxHeight = ans.scrollHeight + "px";
      }
    });
  });

  /* ---------- auth-aware CTAs (auth.js is exempt on /) ---------- */
  (function paintAuth() {
    var inEl = qa('[data-auth="in"]');
    var outEl = qa('[data-auth="out"]');
    fetch("/v1/auth/me", {
      headers: {
        "X-User-Token": localStorage.getItem("nova_user_token") || "",
      },
    }).then(function (r) {
      if (!r.ok) return null;
      return r.json();
    }).then(function (d) {
      if (!d) return;
      var authed = !!d.user;
      if (authed || d.auth_required === false) {
        inEl.forEach(function (e) { e.style.display = ""; });
        outEl.forEach(function (e) { e.style.display = "none"; });
        qa(".js-logout").forEach(function (b) {
          b.onclick = function () {
            fetch("/v1/auth/logout", {
              method: "POST",
              headers: {
                "X-User-Token": localStorage.getItem("nova_user_token") || "",
              },
            }).catch(function () { }).finally(function () {
              localStorage.removeItem("nova_user_token");
              localStorage.removeItem("nova_user");
              location.reload();
            });
          };
        });
        if (authed) {
          var name = d.user.display_name || d.user.username || "";
          qa(".js-user-name").forEach(function (e) { e.textContent = name; });
          qa(".js-user-avatar").forEach(function (e) {
            e.textContent = (name[0] || "?").toUpperCase();
          });
        } else {
          qa(".lp-user").forEach(function (e) { e.style.display = "none"; });
        }
      }
    }).catch(function () { });
  })();

  /* ======================================================================
     THREE.JS hero scene — abstract wireframe orb + particle field.
     Mobile gets reduced geometry; WebGL failure gets a CSS orb fallback;
     the render loop pauses when the hero is off-screen.
     ====================================================================== */
  (function heroGL() {
    var canvas = q("#lpGL");
    var hero = q("#lpHero");
    if (!canvas) return;
    /* Orb removed — hide canvas entirely */
    canvas.style.display = "none";
    return;

    var renderer;
    try {
      renderer = new THREE.WebGLRenderer({ canvas: canvas, alpha: true, antialias: true });
    } catch (e) {
      document.body.classList.add("no-gl"); canvas.style.display = "none"; return;
    }
    var mobile = Math.min(innerWidth, innerHeight) < 720;
    renderer.setPixelRatio(Math.min(devicePixelRatio || 1, mobile ? 1.5 : 2));

    var scene = new THREE.Scene();
    var camera = new THREE.PerspectiveCamera(50, 1, .1, 100);
    camera.position.z = 8;

    var group = new THREE.Group();
    scene.add(group);

    var acc = new THREE.Color(0x7c5cff);
    var acc2 = new THREE.Color(0x4dd8ff);

    // Position the composition right-of-center on wide screens; on mobile it
    // becomes a dim ambient backdrop behind the copy instead of the subject.
    var mats = [];
    var layout = function () {
      var w = hero.clientWidth, h = hero.clientHeight;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      var wide = w > 920;
      group.position.x = wide ? 3.1 : 0;
      group.position.y = wide ? 0.2 : 3.4;
      group.scale.setScalar(wide ? 1 : .62);
      var dim = wide ? 1 : .42;
      mats.forEach(function (m) { m.opacity = m.userData.o * dim; });
    };
    var reg = function (m) { m.userData.o = m.opacity; mats.push(m); return m; };

    // wireframe icosahedron
    var ico = new THREE.Mesh(
      new THREE.IcosahedronGeometry(2.1, mobile ? 1 : 2),
      reg(new THREE.MeshBasicMaterial({ color: acc, wireframe: true, transparent: true, opacity: .32 }))
    );
    group.add(ico);

    // inner glowing core
    var core = new THREE.Mesh(
      new THREE.IcosahedronGeometry(1.15, 1),
      reg(new THREE.MeshBasicMaterial({ color: acc2, wireframe: true, transparent: true, opacity: .2 }))
    );
    group.add(core);

    // orbital ring
    var ring = new THREE.Mesh(
      new THREE.TorusGeometry(3.1, .012, 8, 120),
      reg(new THREE.MeshBasicMaterial({ color: acc2, transparent: true, opacity: .35 }))
    );
    ring.rotation.x = Math.PI / 2.4;
    group.add(ring);

    // particle field — points scattered on a shell
    var pcount = mobile ? 260 : 700;
    var pos = new Float32Array(pcount * 3);
    for (var i = 0; i < pcount; i++) {
      var r = 2.6 + Math.random() * 3.4;
      var th = Math.random() * Math.PI * 2;
      var ph = Math.acos(2 * Math.random() - 1);
      pos[i * 3] = r * Math.sin(ph) * Math.cos(th);
      pos[i * 3 + 1] = r * Math.sin(ph) * Math.sin(th);
      pos[i * 3 + 2] = r * Math.cos(ph);
    }
    var pgeo = new THREE.BufferGeometry();
    pgeo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    var pts = new THREE.Points(pgeo, reg(new THREE.PointsMaterial({
      color: acc2, size: .035, transparent: true, opacity: .75,
      sizeAttenuation: true,
    })));
    group.add(pts);

    // interactivity state
    var mx = 0, my = 0, tx = 0, ty = 0, scrollP = 0;
    if (fine) {
      addEventListener("mousemove", function (e) {
        tx = (e.clientX / innerWidth - .5) * 2;
        ty = (e.clientY / innerHeight - .5) * 2;
      }, { passive: true });
    }
    if (window.ScrollTrigger && hasGsap) {
      ScrollTrigger.create({
        trigger: hero, start: "top top", end: "bottom top",
        onUpdate: function (self) { scrollP = self.progress; },
      });
    }

    var visible = true, raf = null;
    var tick = function () {
      raf = requestAnimationFrame(tick);
      mx += (tx - mx) * .04; my += (ty - my) * .04;
      var t = performance.now() * .0001;
      group.rotation.y = t * 2.2 + mx * .35 + scrollP * 1.4;
      group.rotation.x = my * .22 + scrollP * .5;
      core.rotation.y = -t * 3.5;
      ring.rotation.z = t * 1.6;
      pts.rotation.y = -t * .8;
      var s = 1 + Math.sin(t * 9) * .03 + scrollP * .25;
      ico.scale.setScalar(s);
      renderer.render(scene, camera);
    };
    var start = function () { if (!raf) raf = requestAnimationFrame(tick); };
    var stop = function () { if (raf) { cancelAnimationFrame(raf); raf = null; } };

    new IntersectionObserver(function (en) {
      visible = en[0].isIntersecting;
      visible ? start() : stop();
    }, { threshold: .02 }).observe(hero);
    document.addEventListener("visibilitychange", function () {
      document.hidden ? stop() : (visible && start());
    });
    addEventListener("resize", layout);
    layout();
    start();
  })();

  /* ---------- showcase mock: live typed conversations ---------- */
  /* Four demo conversations — each types its question, then reveals a   */
  /* different rich component and types its lines in turn. Sidebar items */
  /* are clickable; the deck auto-advances while the panel is on screen. */
  var mock = q(".lp-mock");
  if (mock && !reduced) {
    var CONVS = [
      {
        q: "who is marie curie?",
        html: '<div class="hd"><b>PERSONAL DOSSIER</b><span>FILE W-7186 · WIKIPEDIA</span></div>'
          + '<div class="row"><div class="ph"></div><div>'
          + '<div class="nm" data-t>Marie Curie</div>'
          + '<div class="ds" data-t>Physicist and chemist (1867–1934)</div>'
          + '<div class="ex" data-t>Pioneer of radioactivity research — the first person to win two Nobel Prizes in two different sciences.</div>'
          + '</div></div>'
          + '<div class="fx"><div><i>Born</i><b data-t>Nov 7, 1867</b></div>'
          + '<div><i>Field</i><b data-t>Physics · Chemistry</b></div>'
          + '<div><i>Awards</i><b data-t>Nobel ×2</b></div></div>'
      },
      {
        q: "q3 research report",
        html: '<div class="hd"><b>RESEARCH BRIEF</b><span>FILE R-2204 · SYNTHESIZED</span></div>'
          + '<div class="nm" data-t>Q3 revenue grew 18% year over year</div>'
          + '<div class="ex" data-t>Enterprise expansion drove most of the gain — APAC led all regions at +31%.</div>'
          + '<div class="bars">'
          + '<div class="bar" style="--h:38%;--d:.1s"><i>Q1</i></div>'
          + '<div class="bar" style="--h:56%;--d:.25s"><i>Q2</i></div>'
          + '<div class="bar" style="--h:49%;--d:.4s"><i>Q3</i></div>'
          + '<div class="bar" style="--h:78%;--d:.55s"><i>Q4p</i></div></div>'
          + '<div class="fx"><div><i>Sources</i><b data-t>12</b></div>'
          + '<div><i>Confidence</i><b data-t>91%</b></div>'
          + '<div><i>Citations</i><b data-t>8</b></div></div>'
      },
      {
        q: "roleplay: the citadel",
        html: '<div class="hd"><b>ROLEPLAY SCENE</b><span>ACT II · THE CITADEL</span></div>'
          + '<div class="ex" data-t>The iron gates groan open. "Who enters the keep at this hour?" — Kael\u2019s hand finds his dagger as torchlight floods the courtyard.</div>'
          + '<div class="chips">'
          + '<span class="chip lp-pop" style="--d:.15s">Kael · rogue</span>'
          + '<span class="chip lp-pop" style="--d:.3s">Mira · mage</span>'
          + '<span class="chip lp-pop" style="--d:.45s">Bram · guard</span></div>'
          + '<div class="fx"><div><i>Mood</i><b data-t>tense</b></div>'
          + '<div><i>Turn</i><b data-t>14</b></div>'
          + '<div><i>Party</i><b data-t>3</b></div></div>'
      },
      {
        q: "draft release notes",
        html: '<div class="hd"><b>RELEASE NOTES</b><span>v2.4 · DRAFT</span></div>'
          + '<div class="nm" data-t>Changelog drafted</div>'
          + '<div class="chk"><i class="lp-pop" style="--d:.1s">✓</i><span data-t>streaming replies with cancel</span></div>'
          + '<div class="chk"><i class="lp-pop" style="--d:.25s">✓</i><span data-t>person dossier components</span></div>'
          + '<div class="chk"><i class="lp-pop" style="--d:.4s">✓</i><span data-t>user theme presets</span></div>'
          + '<div class="fx"><div><i>Items</i><b data-t>3</b></div>'
          + '<div><i>Status</i><b data-t>draft</b></div>'
          + '<div><i>ETA</i><b data-t>friday</b></div></div>'
      }
    ];

    var sideItems = qa(".lp-mock-side i", mock);
    var chat = q(".lp-mock-chat", mock);
    var caret = document.createElement("span");
    caret.className = "lp-caret";
    mock.classList.add("lp-typing");

    var cur = 0, seq = [], resp = null, ti = 0, tj = 0;
    var tmo = null, running = false, inView = false;

    var load = function (idx) {
      cur = idx;
      sideItems.forEach(function (el, i) { el.classList.toggle("on", i === idx); });
      var c = CONVS[idx];
      chat.innerHTML = '<div class="lp-mq"></div><div class="lp-mr">' + c.html + "</div>";
      resp = q(".lp-mr", chat);
      var mq = q(".lp-mq", chat);
      mq.dataset.full = c.q;
      qa("[data-t]", chat).forEach(function (el) {
        el.dataset.full = el.textContent.trim();
        el.textContent = "";
      });
      seq = [mq].concat(qa("[data-t]", chat));
      ti = 0; tj = 0;
    };

    var step = function () {
      if (!running) return;
      var el = seq[ti];
      if (!el) {
        running = false;
        tmo = setTimeout(function () {
          tmo = null;
          if (!inView) return;
          load((cur + 1) % CONVS.length);
          running = true; step();
        }, 4200);
        return;
      }
      var full = el.dataset.full;
      if (tj === 0) el.appendChild(caret);
      if (tj < full.length) {
        el.insertBefore(document.createTextNode(full[tj]), caret);
        tj++;
        tmo = setTimeout(step, ti === 0 ? 55 : 24);
      } else {
        tj = 0; ti++;
        if (ti === 1 && resp) resp.classList.add("on");
        tmo = setTimeout(step, ti === 1 ? 520 : 260);
      }
    };

    var jump = function (i) {
      clearTimeout(tmo); tmo = null;
      load(i);
      if (inView) { running = true; step(); }
      else running = false;
    };

    sideItems.forEach(function (el, i) {
      el.addEventListener("click", function () { jump(i); });
      el.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); jump(i); }
      });
    });

    load(0);
    new IntersectionObserver(function (en) {
      inView = en[0].isIntersecting;
      if (inView && !running && tmo === null) { running = true; step(); }
      if (!inView) { running = false; clearTimeout(tmo); tmo = null; }
    }, { threshold: .35 }).observe(mock);
  }
})();
