// Smoke test for the Nova render engine — runs in Node with a minimal DOM shim.
global.window = {};
global.document = {
  createElement: () => ({
    setAttribute() {}, append() {}, addEventListener() {},
    style: {}, classList: { add() {}, remove() {}, toggle() {} },
    dataset: {}, innerHTML: "", textContent: "",
    appendChild() {}, querySelectorAll: () => [], querySelector: () => null,
  }),
  createTextNode: (t) => ({ textContent: t, nodeType: 3 }),
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
ok("envelope prose extracted", plan.prose === "Intro prose.");
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
