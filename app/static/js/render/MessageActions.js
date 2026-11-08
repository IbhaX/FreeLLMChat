/* ==========================================================================
   MessageActions — contextual action bar for AI/user messages.

   Actions: copy, regenerate, edit & resubmit, read aloud, like, dislike,
   share, retry. Actions are subtle; extra actions reveal on hover.

   Each action dispatches a `nova:action` CustomEvent with {action, message}
   so chat.js can wire it to conversation state. This keeps the renderer
   decoupled from the chat controller.
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { el, copy } = Nova;

  const ICONS = {
    copy: "⧉",
    regen: "↻",
    edit: "✎",
    speak: "🔊",
    like: "👍",
    dislike: "👎",
    share: "↗",
    retry: "↻",
  };

  function readAloud(text, btn) {
    if (!window.speechSynthesis) {
      btn.title = "Text-to-speech not supported";
      return;
    }
    if (speechSynthesis.speaking) {
      speechSynthesis.cancel();
      btn.classList.remove("on");
      return;
    }
    const u = new SpeechSynthesisUtterance(text);
    u.onend = () => btn.classList.remove("on");
    btn.classList.add("on");
    speechSynthesis.speak(u);
  }

  function share(text, btn) {
    if (navigator.share) {
      navigator.share({ text }).catch(() => {});
    } else {
      copy(text, btn);
    }
  }

  function render(message, opts) {
    opts = opts || {};
    const bar = el("div", { class: "nova-msg-actions" });
    const role = message.role || "assistant";
    const text = message.text || "";

    function btn(action, label, title) {
      const b = el("button", { class: "nova-act", type: "button", title: title || label });
      b.innerHTML = `<span class="nova-act-ico">${ICONS[action] || ""}</span><span class="nova-act-lbl">${label}</span>`;
      b.onclick = () => {
        if (action === "copy") copy(text, b);
        else if (action === "speak") readAloud(text, b);
        else if (action === "share") share(text, b);
        else
          bar.dispatchEvent(
            new CustomEvent("nova:action", {
              bubbles: true,
              detail: { action, message },
            })
          );
      };
      return b;
    }

    bar.append(btn("copy", "copy", "Copy response"));
    if (role === "assistant") {
      bar.append(btn("regen", "regenerate", "Regenerate response"));
      bar.append(btn("speak", "read aloud", "Read aloud"));
    }
    if (role === "user") bar.append(btn("edit", "edit", "Edit & resubmit"));
    bar.append(btn("like", "like", "Good response"));
    bar.append(btn("dislike", "dislike", "Bad response"));
    bar.append(btn("share", "share", "Share"));
    if (message.failed) {
      const r = btn("retry", "retry", "Retry failed response");
      r.style.color = "var(--error)";
      bar.append(r);
    }
    bar.querySelectorAll('.nova-act[title="Good response"], .nova-act[title="Bad response"]').forEach((b) => {
      b.addEventListener("click", () => {
        const isLike = b.title === "Good response";
        bar.querySelectorAll(".nova-act").forEach((x) => x.classList.remove("reacted"));
        b.classList.add("reacted");
        if (!isLike) b.style.color = "var(--error)";
      });
    });
    return bar;
  }

  Nova.MessageActions = { render, readAloud };
})();
