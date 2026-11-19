// Smoke test for the Nova render engine — runs in Node with a minimal DOM shim.
global.window = {};
// Minimal element model: tags, attributes, text and children serialize to
// outerHTML so renderer output is inspectable without a browser.
const escapeText = (s) => String(s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
class FakeNode {
  constructor(tag) {
    this.tagName = tag ? tag.toUpperCase() : "";
    this.nodeType = tag ? 1 : 3;
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.hidden = false;
    this._text = "";
    this.parentNode = null;
    const self = this;
    this.classList = {
      add(...c) { const cur = (self.attributes.class || "").split(/\s+/).filter(Boolean);
        c.forEach(x => { if (!cur.includes(x)) cur.push(x); });
        self.attributes.class = cur.join(" "); },
      remove(...c) { self.attributes.class = (self.attributes.class || "").split(/\s+/)
        .filter(x => x && !c.includes(x)).join(" "); },
      toggle(c, on) { const has = this.contains(c);
        if (on === undefined ? !has : on) this.add(c); else this.remove(c); return !has; },
      contains(c) { return (self.attributes.class || "").split(/\s+/).includes(c); },
    };
  }
  get className() { return this.attributes.class || ""; }
  set className(v) { this.attributes.class = String(v); }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; }
  addEventListener() {}
  append(...nodes) {
    nodes.forEach(n => this.appendChild(n instanceof FakeNode ? n : document.createTextNode(n)));
  }
  appendChild(n) { n.parentNode = this; this.children.push(n); return n; }
  insertBefore(n, ref) {
    const i = this.children.indexOf(ref);
    if (i < 0) return this.appendChild(n);
    n.parentNode = this; this.children.splice(i, 0, n); return n;
  }
  remove() { if (this.parentNode) {
    this.parentNode.children = this.parentNode.children.filter(c => c !== this); } }
  replaceWith(n) { const p = this.parentNode; if (!p) return;
    const i = p.children.indexOf(this); p.children[i] = n; n.parentNode = p; this.parentNode = null; }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  get textContent() {
    if (this.nodeType === 3) return this._text;
    return this.children.map(c => c.textContent).join("");
  }
  set textContent(v) {
    if (this.nodeType === 3) { this._text = String(v); return; }
    this.children = []; this._text = String(v);
    if (String(v)) this.children.push(document.createTextNode(String(v)));
  }
  get innerHTML() {
    return this.nodeType === 3 ? escapeText(this._text)
      : this.children.map(c => c.nodeType === 3 ? escapeText(c._text) : c.outerHTML).join("");
  }
  set innerHTML(v) { this.children = []; this._raw = String(v); }
  get outerHTML() {
    if (this.nodeType === 3) return escapeText(this._text);
    const attrs = Object.entries(this.attributes).map(([k, v]) => ` ${k}="${escapeText(v).replace(/"/g, "&quot;")}"`).join("");
    const inner = this.children.map(c => c.nodeType === 3 ? escapeText(c._text) : c.outerHTML).join("");
    return `<${this.tagName.toLowerCase()}${attrs}>${inner}</${this.tagName.toLowerCase()}>`;
  }
}
global.document = {
  createElement: (tag) => new FakeNode(tag),
  createTextNode: (t) => { const n = new FakeNode(""); n._text = String(t); return n; },
  documentElement: { dataset: {} },
  head: { appendChild() {} },
};
global.localStorage = { getItem: () => null, setItem() {} };
global.navigator = {};
global.requestAnimationFrame = (fn) => setTimeout(fn, 0);

const fs = require("fs");
const path = require("path");
const R = path.join(__dirname, "..", "app", "static", "js", "render");
const load = (f) => eval(fs.readFileSync(path.join(R, f), "utf8"));

load("ns.js");
load("ComponentRegistry.js");
load("ResponseClassifier.js");
load("MarkdownRenderer.js");
load("CodeRenderer.js");
load("TableRenderer.js");
load("JsonViewer.js");
load("ChartRenderer.js");
load("Cards.js");
load("StatusRenderers.js");
load("MediaRenderer.js");
load("InteractiveRenderer.js");
load("SuggestedPrompts.js");
load("MessageActions.js");
load("ResponseRenderer.js");
load("StreamingRenderer.js");

const Nova = window.NovaRender;
let pass = 0, fail = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("  ✓ " + name); }
  else { fail++; console.log("  ✗ " + name); }
}

console.log("\n=== Nova Render Engine — smoke tests ===\n");

// 1. Wrapped markdown table normalization (the screenshot bug)
const wrapped =
  "| Scientist | Lifetime | Field |\n" +
  "|---|---|---|\n" +
  "| **Aristotle** | 384-322 BC | Early systematic observations of biology,\n" +
  "physics, and logic |\n" +
  "| Copernicus | 1473-1543 | Heliocentric model |";

const norm = Nova.normalizeTableRows(wrapped);
// 1 header + 1 separator + 2 body rows = 4 pipe lines
ok("normalizeTableRows joins wrapped rows", norm.split("\n").filter(l => l.includes("|")).length === 4);

const parsed = Nova.parseMdTable(wrapped);
ok("parseMdTable returns columns", parsed && parsed.columns.length === 3);
ok("parseMdTable returns 2 body rows", parsed && parsed.rows.length === 2);
ok("parseMdTable joins wrapped cell", parsed && parsed.rows[0][2].includes("physics, and logic"));

// 2. Envelope detection
const env = {
  response_type: "data_visualization",
  content: "Revenue breakdown.",
  components: [
    { type: "metric_card", data: { title: "Revenue", value: 125000, unit: "USD" } },
    { type: "chart", data: { chart_type: "bar", data: [{ month: "Jan", revenue: 25 }] } },
    { type: "made_up_type", data: {} },
  ],
  suggested_prompts: ["Compare by month"],
};
const fenced = "Intro prose.\n\n```nova-response\n" + JSON.stringify(env) + "\n```";
const plan = Nova.ResponseClassifier.classify(fenced);
ok("envelope classified as 'envelope'", plan.kind === "envelope");
ok("envelope keeps surrounding prose and envelope content",
  plan.prose === "Intro prose.\n\nRevenue breakdown.");
ok("envelope has 3 components", plan.envelope.components.length === 3);
ok("envelope suggested_prompts preserved", plan.envelope.suggested_prompts.length === 1);

// 3. Markdown render (fallback path — no marked/DOMPurify in Node)
const mdHtml = Nova.MarkdownRenderer.render(wrapped);
ok("markdown fallback renders <table", mdHtml.includes("<table"));
ok("markdown fallback renders wrapped cell", mdHtml.includes("physics, and logic"));

// 4. Edge cases
ok("empty -> markdown", Nova.ResponseClassifier.classify("").kind === "markdown");
ok("json blob -> mixed", Nova.ResponseClassifier.classify('{"a":1}').kind === "mixed");
ok("plain text -> markdown", Nova.ResponseClassifier.classify("just text").kind === "markdown");

// 5. Component registry
ok("registry has metric_card", Nova.ComponentRegistry.hasComponent("metric_card"));
ok("registry has chart", Nova.ComponentRegistry.hasComponent("chart"));
ok("registry has table", Nova.ComponentRegistry.hasComponent("table"));
ok("registry has code", Nova.ComponentRegistry.hasComponent("code"));
ok("registry has json_viewer", Nova.ComponentRegistry.hasComponent("json_viewer"));
ok("registry has error", Nova.ComponentRegistry.hasComponent("error"));
ok("registry has interactive", Nova.ComponentRegistry.hasComponent("interactive"));
ok("registry rejects unknown", !Nova.ComponentRegistry.hasComponent("arbitrary_html"));

// 6. SuggestedPrompts
ok("SuggestedPrompts.render returns null for empty", Nova.SuggestedPrompts.render([]) === null);
ok("SuggestedPrompts.infer returns null for plain", Nova.SuggestedPrompts.infer("hello") === null);
ok("SuggestedPrompts.infer returns prompts for code", Nova.SuggestedPrompts.infer("```python\nprint(1)\n```") !== null);

// 7. toCSV
ok("toCSV produces header + rows", Nova.toCSV([{a:1,b:2},{a:3,b:4}]).split("\n").length === 3);
ok("toCSV escapes commas", Nova.toCSV([{a:"x,y"}]).includes('"x,y"'));

// 8. fmtNum
ok("fmtNum formats thousands", Nova.fmtNum(125000).includes("125"));
ok("fmtNum appends unit", Nova.fmtNum(42, "USD") === "42 USD");

// 9. StreamingRenderer lifecycle
const srHost = { innerHTML: "", classList: { add(){}, remove(){} }, querySelectorAll: () => [] };
const sr = Nova.StreamingRenderer.create(srHost, null);
sr.update("hello ");
sr.update("world");
ok("streaming accumulates text", sr.text === "hello world");
const finalPlan = sr.finish("hello world");
ok("streaming finish returns plan", finalPlan && finalPlan.kind === "markdown");

// 10. StreamingRenderer abort
const sr2Host = { innerHTML: "", classList: { add(){}, remove(){} }, querySelectorAll: () => [], append(){} };
const sr2 = Nova.StreamingRenderer.create(sr2Host, null);
sr2.abort("stopped");
sr2.update("late"); // should be ignored
ok("streaming abort ignores late updates", sr2.text === "");

// ---- partial envelope parsing (progressive streaming) ----
const RC = Nova.ResponseClassifier;
const streamEnv = {
  response_type: "comparison",
  content: 'Lead "quoted" \u00e9',
  components: [
    { type: "table", data: { columns: ["A", "B"], rows: [["1", "2.5"], [-3e2, true]] } },
    { type: "callout", data: { variant: "tip", message: "x\ny" } },
  ],
  sources: [{ title: "T", url: "https://x" }],
};
const streamText = "Intro\n```nova-response\n" + JSON.stringify(streamEnv, null, 2) + "\n```";
let prefixOk = true, monotonic = true, prevDone = 0;
for (let k = 1; k <= streamText.length; k++) {
  const st = RC.parseStreamingEnvelope(streamText.slice(0, k));
  if (k > "Intro\n".length && !st) { prefixOk = false; break; }
  if (st && st.envelope) {
    const done = (st.envelope.components || []).filter((c) => st.closed.has(c)).length;
    if (done < prevDone) monotonic = false;
    prevDone = done;
    if (st.envelope.content && !streamEnv.content.startsWith(st.envelope.content)) prefixOk = false;
  }
}
ok("streaming envelope parses at every prefix", prefixOk);
ok("streaming components complete monotonically", monotonic);
const fullSt = RC.parseStreamingEnvelope(streamText);
ok("streaming envelope final value exact", JSON.stringify(fullSt.envelope) === JSON.stringify(streamEnv));
ok("streaming plain markdown is not an envelope", RC.parseStreamingEnvelope("Hello **world**") === null);
ok("streaming json code is not an envelope", RC.parseStreamingEnvelope('```json\n{"name": "x"}') === null);
ok("streaming partial fence shows skeleton", RC.parseStreamingEnvelope("``").pending === true);

console.log("\n" + pass + " passed, " + fail + " failed");
process.exit(fail ? 1 : 0);
