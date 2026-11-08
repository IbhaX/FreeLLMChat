/* ==========================================================================
   SuggestedPrompts — contextual follow-up suggestions.

   Renders a row of clickable prompt chips under a response. Only shown when
   the envelope supplies `suggested_prompts` (the backend decides when they
   add value — not after every response). Clicking dispatches a
   `nova:suggest` CustomEvent that chat.js listens for to send the prompt.
   ========================================================================== */
(function () {
  "use strict";
  const Nova = window.NovaRender;
  const { el } = Nova;

  function render(prompts) {
    if (!Array.isArray(prompts) || !prompts.length) return null;
    const wrap = el("div", { class: "nova-suggest" });
    prompts.slice(0, 6).forEach((p) => {
      const chip = el("button", { class: "nova-suggest-chip", type: "button" }, p);
      chip.onclick = () => {
        wrap.dispatchEvent(
          new CustomEvent("nova:suggest", { bubbles: true, detail: { prompt: p } })
        );
      };
      wrap.append(chip);
    });
    return wrap;
  }

  // Heuristic suggested prompts derived from response content, used only when
  // the backend did not supply explicit suggestions and the content clearly
  // warrants them (code, data, research). Conservative — never spammy.
  function infer(text) {
    if (!text || typeof text !== "string") return null;
    const t = text.toLowerCase();
    if (/```/.test(text)) {
      return ["Explain this code", "Write unit tests", "Optimize this"];
    }
    if (/\b(data|revenue|chart|statistics|average|total)\b/.test(t)) {
      return ["Show a detailed breakdown", "Compare the results", "Identify anomalies"];
    }
    if (/\b(research|source|study|according to)\b/.test(t)) {
      return ["Find more sources", "Summarize the findings", "Compare alternatives"];
    }
    return null;
  }

  Nova.SuggestedPrompts = { render, infer };
})();
