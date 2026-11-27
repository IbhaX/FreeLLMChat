/* Phone "Install app" banner for the iapps. Self-contained, no dependencies.
   <script src=".../install-banner.js" data-name="iRead" data-icon="/static/img/icon-192.png" data-acc="#3a7a52" data-css=".../install-banner.css" defer>
   Shows on phones only, only when the browser can install the app (or on iOS, as an Add to Home Screen hint),
   never when already installed, and stays away for 14 days after it is dismissed. */
(function () {
  "use strict";
  var me = document.currentScript || {};
  var d = me.dataset || {};
  var NAME = d.name || "this app", ICON = d.icon || "/icon-192.png", ACC = d.acc || "#4d6bff";
  var KEY = "install-dismissed:" + location.host;
  var deferred = null, bar = null;
  function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* blocked */ } }
  function standalone() { return matchMedia("(display-mode: standalone)").matches || navigator.standalone === true || /[?&]source=(twa|pwa)/.test(location.search); }
  function ios() { return /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1); }
  function css() {
    if (!d.css || document.getElementById("ibInstallCss")) return;
    var l = document.createElement("link"); l.id = "ibInstallCss"; l.rel = "stylesheet"; l.href = d.css; document.head.appendChild(l);
  }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }
  function render() {
    var dismissed = Number(get(KEY) || 0) > Date.now() - 14 * 864e5;
    var show = matchMedia("(max-width: 759px)").matches && !standalone() && !dismissed && (deferred || ios());
    if (!show) { if (bar) bar.hidden = true; return; }
    css();
    if (!bar) {
      bar = el("div", "ibinstall"); bar.setAttribute("role", "region"); bar.setAttribute("aria-label", "Install " + NAME);
      bar.style.setProperty("--ibacc", ACC);
      var img = el("img"); img.src = ICON; img.alt = ""; img.width = 40; img.height = 40;
      var txt = el("div", "ibtxt"); txt.appendChild(el("b", "", "Install " + NAME)); txt.appendChild(el("span", "ibsub"));
      var go = el("button", "ibgo", "Install"); go.type = "button";
      var x = el("button", "ibx", "✕"); x.type = "button"; x.setAttribute("aria-label", "Dismiss");
      go.addEventListener("click", function () {
        if (!deferred) return;
        deferred.prompt();
        (deferred.userChoice || Promise.resolve()).catch(function () {}).then(function () { deferred = null; render(); });
      });
      x.addEventListener("click", function () { set(KEY, String(Date.now())); render(); });
      bar.appendChild(img); bar.appendChild(txt); bar.appendChild(go); bar.appendChild(x);
      document.body.appendChild(bar);
    }
    bar.querySelector(".ibsub").textContent = deferred ? "Full screen, one tap from your home screen." : "Tap Share, then Add to Home Screen.";
    bar.querySelector(".ibgo").hidden = !deferred;
    bar.hidden = false;
  }
  window.addEventListener("beforeinstallprompt", function (e) { e.preventDefault(); deferred = e; render(); });
  window.addEventListener("appinstalled", function () { deferred = null; render(); });
  var go = function () { setTimeout(render, 1500); };
  if (document.readyState === "complete") go(); else window.addEventListener("load", go);
})();
