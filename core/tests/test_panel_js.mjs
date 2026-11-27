/*
 * AF-26 / C2 — behavioural test of the REAL dashboard/panel.js.
 *
 * No source grepping: the script is executed in Node against a small fake DOM,
 * and we drive the actual first-run setup and 401-recovery flows.
 * Prints one JSON line: {"status":"PASS"|"FAIL"|... ,"checks":[...]}
 */
import fs from "node:fs";
import vm from "node:vm";

const ROOT = new URL("../../", import.meta.url).pathname;
const SRC = fs.readFileSync(ROOT + "dashboard/panel.js", "utf8");

const live = [];
function el(tag = "div") {
  const e = {
    tag, id: "", value: "", hidden: false, textContent: "", style: {},
    _children: [], _handlers: {},
    set innerHTML(html) {
      this._children = [];
      for (const m of html.matchAll(/id="([^"]+)"/g)) {
        const child = el("input");
        child.id = m[1];
        if (/id="po-pass2"[^>]*hidden/.test(html)) child.hidden = true;
        this._children.push(child);
      }
    },
    get innerHTML() { return ""; },
    querySelector(sel) {
      const id = String(sel).replace(/^#/, "");
      return this._children.find(c => c.id === id) || null;
    },
    addEventListener(name, fn) { (this._handlers[name] ||= []).push(fn); },
    fire(name) { (this._handlers[name] || []).forEach(fn => fn()); },
    focus() {},
    appendChild(c) { this._children.push(c); live.push(c); return c; },
    remove() {
      const i = live.indexOf(this);
      if (i >= 0) live.splice(i, 1);
    },
    contains(c) { return live.includes(c); },
  };
  return e;
}

const calls = [];
let projectsCalls = 0;
const originalFetch = async (url, opts = {}) => {
  calls.push({ url, method: (opts.method || "GET").toUpperCase(), body: opts.body });
  if (url === "/api/panel/state")
    return { ok: true, status: 200, json: async () => ({ ok: true, initialized: false }) };
  if (url === "/api/projects") {
    projectsCalls += 1;
    if (projectsCalls === 1)
      return { ok: false, status: 401, json: async () => ({ ok: false, error: "login" }) };
    return { ok: true, status: 200, json: async () => ({ ok: true, projects: [] }) };
  }
  return { ok: true, status: 200, json: async () => ({ ok: true }) };
};

const document = {
  readyState: "complete",
  cookie: "panel_csrf=fake-csrf",
  createElement: (tag) => el(tag),
  documentElement: el("html"),
  addEventListener() {},
};
const window = { fetch: originalFetch };
vm.runInNewContext(SRC, { window, document, console, setTimeout, Promise, URL });

const tick = () => new Promise(r => setImmediate(r));
const checks = [];
function record(name, ok, detail) {
  checks.push({ name, status: ok ? "PASS" : "FAIL", detail: String(detail || "") });
}
function currentOverlay() { return live[live.length - 1] || null; }
function fill(overlay, pass, again) {
  overlay.querySelector("#po-pass").value = pass;
  const p2 = overlay.querySelector("#po-pass2");
  if (p2) p2.value = again === undefined ? pass : again;
  overlay.querySelector("#po-go").fire("click");
}

async function main() {
  await tick(); await tick();
  const ov = currentOverlay();
  record("setup-overlay-created", !!ov, "first-run overlay");
  if (!ov) return finish("FAIL");

  const p2 = ov.querySelector("#po-pass2");
  record("setup-repeat-field-visible", !!p2 && p2.hidden === false,
    "hidden=" + (p2 ? p2.hidden : "missing"));

  // Missing repeat field is the C2 blocker; with the fix setup succeeds.
  fill(ov, "AF26-Strong-Passphrase-123");
  await tick(); await tick(); await tick();
  const init = calls.find(c => c.url === "/api/panel/init" && c.method === "POST");
  record("setup-posts-init", !!init, init ? init.body : "no init call");
  const msg = ov.querySelector("#po-msg");
  record("setup-no-password-mismatch", !msg || !String(msg.textContent).includes("یکسان"),
    msg ? msg.textContent : "");

  // 401 recovery must resolve instead of hanging on the old no-op resolver.
  const retry = window.fetch("/api/projects");
  await tick(); await tick(); await tick();
  const ov2 = currentOverlay();
  record("login-overlay-after-401", !!ov2, "recovery overlay");
  if (ov2) {
    fill(ov2, "AF26-Strong-Passphrase-123");
  }
  const answer = await Promise.race([
    retry.then(r => r.status).catch(e => "ERR:" + e.message),
    new Promise(r => setTimeout(() => r("TIMEOUT"), 1500)),
  ]);
  record("401-recovered-no-hang", answer === 200, "status=" + answer);
  finish(checks.every(c => c.status === "PASS") ? "PASS" : "FAIL");
}

function finish(status) {
  console.log(JSON.stringify({ status, checks }));
  process.exit(status === "PASS" ? 0 : 1);
}
main().catch(e => { record("unexpected", false, e.stack || e.message); finish("FAIL"); });
