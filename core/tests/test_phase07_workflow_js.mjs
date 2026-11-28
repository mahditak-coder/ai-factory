/* Phase 07 — behavioural check: critique findings render as inert text. */
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";

const root = path.resolve(process.cwd());
const source = fs.readFileSync(path.join(root, "dashboard/workflow.js"), "utf8");

function node(tag) {
  return {
    tag, className: "", textContent: "", hidden: false, value: "",
    children: [], innerHTMLSet: false,
    replaceChildren() { this.children = []; },
    appendChild(c) { this.children.push(c); if (c && c.textContent)
      globalThis.__rendered.push(String(c.textContent)); return c; },
    set innerHTML(v) { this.innerHTMLSet = true; this._html = String(v); },
    get innerHTML() { return this._html || ""; },
  };
}

const nodes = new Map();
globalThis.__rendered = [];
const get = (id) => {
  if (!nodes.has(id)) nodes.set(id, node("div"));
  return nodes.get(id);
};

const malicious = '<img src=x onerror="window.HACKED=1">';
const critique = { ok: true, entries: [
  { id: "rv-1", kind: "verdict", cycle: 1, severity: "critical", status: "reject",
    findings: [{ id: "f-1", owner: "critic", required_correction: malicious }],
    selected_option: "A", rejected_options: [{ option: "B", reason: malicious }] },
]};

const calls = [];
globalThis.document = {
  getElementById: get,
  createElement: (tag) => node(tag),
  querySelectorAll: () => [],
};
globalThis.location = { search: "?project=p07js" };
globalThis.history = { replaceState() {} };
globalThis.URLSearchParams = URLSearchParams;
globalThis.EventSource = class {
  constructor() {}
  close() {}
  onmessage = null;
  onerror = null;
};
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};
globalThis.fetch = async (url) => {
  calls.push(url);
  if (url.startsWith("/api/projects"))
    return { ok: true, json: async () => ({ projects: [{ name: "p07js" }] }) };
  if (url.startsWith("/api/workflow/critique"))
    return { ok: true, json: async () => critique };
  if (url.startsWith("/api/workflow/state"))
    return { ok: true, json: async () => ({ ok: true, result: null }) };
  return { ok: false, status: 404, json: async () => ({ ok: false }) };
};

vm.runInThisContext(source, { filename: "workflow.js" });

const checks = [];
function check(name, cond) {
  checks.push(name + ": " + (cond ? "PASS" : "FAIL"));
  if (!cond) process.exitCode = 1;
}

for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r));

const criticalCalls = calls.filter((u) => u.startsWith("/api/workflow/critique"));
check("critique-fetched", criticalCalls.length > 0);
check("no-html-injection", globalThis.HACKED === undefined);
const box = get("critiqueBox");
const inert = globalThis.__rendered.some((t) => t.includes(malicious));
check("malicious-text-inert", inert && box.innerHTMLSet === false);
check("no-innerhtml-on-review-box", box.innerHTMLSet === false);
check("review-card-visible", get("critiqueCard").hidden === false);

console.log(JSON.stringify({ status: process.exitCode ? "FAIL" : "PASS", checks }));
