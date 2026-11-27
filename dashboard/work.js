/* AI Workspace v0.4.0 — professional chat + RELIABLE status engine.
 *
 * State machine rules (never guess):
 *   - state comes from /api/oc/state (real OpenCode session outcome/times)
 *   - refined live by SSE events; SSE loss => unknown state + recovery poll
 *   - a NEW turn (send / step.started) clears ALL prior terminal markers
 *   - time-based staleness shows a WARNING only; never a fake failure
 *   - cancel button active only while realmente running; result verified
 */
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

async function api(url, opts) {
  const r = await fetch(url, opts);
  let d = null;
  try { d = await r.json(); } catch { /* ignore */ }
  if (!r.ok || (d && d.ok === false)) {
    /* AF-23: خطا همراه تشخیص واقعی سرور (diag) و شناسهٔ پیگیری می‌آید */
    const e = new Error((d && d.error) || ("HTTP " + r.status));
    if (d) { e.diag = d.diag || null; e.trace = d.trace || ""; }
    throw e;
  }
  return d;
}
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ═══════════════ AF-23 (hole C fix): safe minimal Markdown renderer ═══════════════
 * بدون هیچ وابستگی جدید (قانون مأموریت): رندر واقعی زیرمجموعه‌ای که مدل‌ها
 * عملاً تولید می‌کنند — کد محصور (با کپی)، سرتیتر، فهرست، جدول، نقل‌قول،
 * بولد/ایتالیک/کد خطی/لینک. HTML اول escape می‌شود و بعد تبدیل‌ها اجرا
 * می‌شوند؛ هیچ ورودی‌ای نمی‌تواند مارک‌آپ تزریق کند. */
function mdInline(s) {
  return s
    .replace(/`([^`]+)`/g, '<code class="mdinline">$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
             '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

function mdRender(text) {
  const lines = String(text ?? "").replace(/\r\n?/g, "\n").split("\n");
  let out = "", i = 0, para = [];
  const flushP = () => {
    if (!para.length) return;
    out += `<p>${para.map((l) => mdInline(esc(l))).join("<br>")}</p>`;
    para = [];
  };
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^```(\w*)\s*$/);
    if (fence) {
      flushP();
      const lang = fence[1] || "";
      i++;
      const code = [];
      while (i < lines.length && !/^```\s*$/.test(lines[i])) { code.push(lines[i]); i++; }
      i++;                                    // closing fence (or EOF — both fine)
      out += `<div class="mdcode"><div class="mdcodebar">` +
        (lang ? `<span class="mdlang">${esc(lang)}</span>` : "") +
        `<button type="button" class="mdcopy" title="کپی کد">⧉ کپی کد</button></div>` +
        `<pre><code>${esc(code.join("\n"))}</code></pre></div>`;
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)/);
    if (h) {
      flushP();
      out += `<div class="mdh mdh${Math.min(h[1].length, 4)}">${mdInline(esc(h[2]))}</div>`;
      i++; continue;
    }
    if (/^\s*\|.+\|\s*$/.test(line) && i + 1 < lines.length &&
        /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      flushP();
      const splitRow = (r) => r.trim().replace(/^\|/, "").replace(/\|$/, "")
        .split("|").map((c) => c.trim());
      const head = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|.+\|\s*$/.test(lines[i])) {
        rows.push(splitRow(lines[i])); i++;
      }
      out += `<table class="mdtable"><thead><tr>` +
        head.map((c) => `<th>${mdInline(esc(c))}</th>`).join("") + `</tr></thead><tbody>` +
        rows.map((r) => `<tr>${r.map((c) => `<td>${mdInline(esc(c))}</td>`).join("")}</tr>`)
            .join("") + `</tbody></table>`;
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      flushP();
      const q = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        q.push(lines[i].replace(/^\s*>\s?/, "")); i++;
      }
      out += `<blockquote class="mdquote">` +
        q.map((l) => mdInline(esc(l))).join("<br>") + `</blockquote>`;
      continue;
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      flushP();
      const items = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, "")); i++;
      }
      out += `<ul class="mdlist">` +
        items.map((it) => `<li>${mdInline(esc(it))}</li>`).join("") + `</ul>`;
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      flushP();
      const items = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s+/, "")); i++;
      }
      out += `<ol class="mdlist">` +
        items.map((it) => `<li>${mdInline(esc(it))}</li>`).join("") + `</ol>`;
      continue;
    }
    if (!line.trim()) { flushP(); i++; continue; }
    para.push(line); i++;
  }
  flushP();
  return out;
}

/* ═══════════════ AF-23 (hole D fix): honest auto-scroll ═══════════════
 * اسکرول خودکار فقط وقتی که کاربر نزدیک انتهای گفتگوست؛ اگر در حال مطالعهٔ
 * پیام‌های قبلی باشد، موقعیت او به‌زور تغییر نمی‌کند. */
function chatNearBottom() {
  const box = $("#chat");
  return box.scrollHeight - box.scrollTop - box.clientHeight < 140;
}
function scrollChat(force) {
  const box = $("#chat");
  if (force || chatNearBottom()) box.scrollTop = box.scrollHeight;
}
const faTime = (ms) => ms ? new Date(ms).toLocaleTimeString("fa-IR") : "—";
const faDT = (ms) => ms ? new Date(ms).toLocaleString("fa-IR") : "—";
/* AF-01: زمان صادقانهٔ پیام‌ها — فقط از timestamp واقعی؛ بدون timestamp → «زمان نامشخص» */
const msgTime = (ms) => (window.AFTime ? AFTime.fmt(ms) : (ms ? faDT(ms) : "زمان نامشخص"));
const faMin = (s) => (s >= 3600 ? `${Math.floor(s / 3600)} ساعت و ${Math.floor((s % 3600) / 60)} دقیقه`
                     : s >= 60 ? `${Math.floor(s / 60)} دقیقه و ${s % 60} ثانیه`
                     : `${s} ثانیه`);

/* ═══════════════════ state machine ═══════════════════ */
const STATES = {
  idle:       { label: "● آماده", cls: "st-idle" },
  sending:    { label: "● در حال ارسال درخواست", cls: "st-wait" },
  thinking:   { label: "● در حال تفکر مدل", cls: "st-wait" },
  generating: { label: "● در حال تولید فایل", cls: "st-wait" },
  tool:       { label: "● در حال اجرای ابزار", cls: "st-run" },
  permission: { label: "● منتظر تأیید کاربر", cls: "st-run" },
  awaiting:   { label: "● منتظر پاسخ کاربر", cls: "st-wait" },
  testing:    { label: "● در حال بررسی و تست", cls: "st-wait" },
  fixing:     { label: "● در حال اصلاح خطا", cls: "st-wait" },
  ok:         { label: "● موفق", cls: "st-ok" },
  failed:     { label: "● شکست‌خورده", cls: "st-bad" },
  cancelled:  { label: "● لغوشده", cls: "st-bad" },
  unknown:    { label: "● وضعیت نامشخص (قطع ارتباط)", cls: "st-run" },
};

const S = {
  state: "idle",
  turnStart: 0,          // ms epoch of current/last turn start
  lastActivity: 0,       // ms epoch of last model activity
  running: false,        // session really busy (server-confirmed)
  es: null, recoverT: null, ticker: null,
  project: "", sessionId: "", selectedModel: "",
  toolCount: 0,
};

function setStatus(key, opts = {}) {
  const st = STATES[key] || STATES.unknown;
  S.state = key;
  const pill = $("#statePill");
  pill.className = "wspill " + st.cls;
  pill.textContent = st.label + (opts.detail ? " — " + opts.detail : "");
  // sync the topbar badge
  const ses = $("#sesBadge");
  ses.className = "wspill " + st.cls;
  const short = { idle: "آماده", ok: "موفق", failed: "شکست", cancelled: "لغو",
                  unknown: "نامشخص" }[key];
  ses.textContent = short || st.label.replace("● ", "");
  // terminal states stop the timer & unlock cancel
  const terminal = ["ok", "failed", "cancelled", "idle"].includes(key);
  if (terminal) {
    S.running = false;
    $("#btnAbort").disabled = true;
    stopTicker();
  } else if (key !== "unknown") {
    S.running = true;
    $("#btnAbort").disabled = false;
  }
  if (terminal) hidePerm();
  runTicker();
  updateStallWarning();
}

/* elapsed + last-activity ticker (a display only — never a verdict) */
function runTicker() {
  if (S.ticker) { clearInterval(S.ticker); S.ticker = null; }
  if (!(S.turnStart || S.running || S.state !== "idle")) return;
  S.ticker = setInterval(() => {
    const el = $("#elapsed");
    if (S.turnStart) {
      el.hidden = false;
      el.textContent = "⏱ " + faMin(Math.max(0, Math.floor((Date.now() - S.turnStart) / 1000)));
    } else el.hidden = true;
    $("#lastAct").textContent = "آخرین فعالیت: " + (S.lastActivity ? faTime(S.lastActivity) : "—");
    updateStallWarning();
  }, 1000);
}
function stopTicker() {
  if (S.ticker) { clearInterval(S.ticker); S.ticker = null; }
}

function updateStallWarning() {
  const w = $("#stallWarn");
  const stallMs = S.lastActivity ? Date.now() - S.lastActivity : 0;
  if (pendingPermissions.size || S.state === "permission") {
    w.hidden = false;
    w.textContent = "🔒 منتظر تأیید مجوز توسط شماست — مدل متوقف نشده است.";
  } else if (S.running && S.lastActivity && stallMs > 90_000) {
    w.hidden = false;
    const secs = Math.floor(Date.now() / 1000) - Math.floor(S.lastActivity / 1000);
    w.textContent = "⚠ مدل بیش از " + faMin(secs) + " فعالیتی نشان نداده — احتمالاً منتظر است یا کار سنگینی در جریان است (شکست نیست؛ لغو در دسترس است)";
  } else if (S.running && !S.lastActivity && S.turnStart && Date.now() - S.turnStart > 240_000) {
    w.hidden = false;
    w.textContent = "⚠ بدون هیچ رویدادی از شروع عملیات — اتصال را با «وضعیت» بررسی کنید";
  } else w.hidden = true;
}

/* ═══════════════════ generic UI helpers ═══════════════════ */
/* AF-01: پیام خطا مُهر زمان واقعیِ «همین لحظه» می‌گیرد (خطا اکنون رخ داده، جعل نیست) */
function failBar(msg) {
  const el = $("#errBar");
  const t = window.AFTime ? AFTime.stampNow() : faTime(Date.now());
  el.textContent = "⚠ " + t + " — " + msg;
  el.hidden = false;
}
/* ═══════════ AF-23 (part 3): structured honest error display ═══════════
 * شرح ساده + مرحله + سرویس + کد واقعی + زمان + شناسهٔ پیگیری + اقدام پیشنهادی
 * + «جزئیات فنی» جمع‌شونده. هرگز کلید/رمز/هدر نشان داده نمی‌شود (سرور هرگز
 * چنین اطلاعاتی در diag نمی‌فرستد). بدون diag → همان پیام صادقانهٔ ساده. */
function showError(e, prefix) {
  const el = $("#errBar");
  const t = window.AFTime ? AFTime.stampNow() : faTime(Date.now());
  const d = e && e.diag;
  if (!d) {
    el.textContent = "⚠ " + t + " — " + (prefix ? prefix + ": " : "") +
      ((e && e.message) || "خطا");
    el.hidden = false;
    return;
  }
  const conf = d.confidence === "احتمالی"
    ? `<span class="edag-conf">احتمال (نه قطعیت)</span>` : "";
  el.innerHTML =
    `<div class="ediag">` +
    `<div class="ediag-reason">⚠ ${esc(d.reason)} ${conf}</div>` +
    `<div class="ediag-grid">` +
    `<span>مرحله:</span><b>${esc(d.stage)}</b>` +
    `<span>سرویس:</span><b>${esc(d.service)}</b>` +
    `<span>کد خطا:</span><b>${esc(String(d.code))}${d.tag ? " (" + esc(d.tag) + ")" : ""}</b>` +
    `<span>زمان:</span><b>${esc(t)}</b>` +
    `<span>شناسهٔ پیگیری:</span><b class="ediag-trace">${esc(d.trace || (e && e.trace) || "—")}</b>` +
    `</div>` +
    `<div class="ediag-sug">اقدام پیشنهادی: ${esc(d.suggestion)}</div>` +
    `<details class="ediag-tech"><summary>جزئیات فنی</summary>` +
    `<pre>${esc(d.detail || "—")}</pre></details>` +
    `</div>`;
  el.hidden = false;
}
function hideError() { $("#errBar").hidden = true; }
function hidePerm() { const el = $("#permBar"); el.hidden = true; el.innerHTML = ""; }
function setDetail(htmlOrText, asHTML = false) {
  const el = $("#detailBar");
  if (htmlOrText == null || htmlOrText === "") { el.hidden = true; el.textContent = ""; return; }
  if (asHTML) el.innerHTML = htmlOrText; else el.textContent = htmlOrText;
  el.hidden = false;
}

/* ═══════════════════ version (single source: /api/health) ═══════════════════ */
async function initVersionBadges() {
  try {
    const h = await api("/api/health");
    document.querySelectorAll(".af-version").forEach((e) =>
      e.textContent = "v" + (h.version || "?"));
    $("#versBadge").textContent = "v" + (h.version || "?");
  } catch { $("#versBadge").textContent = "v ?"; }
}

/* ═══════════════════ OpenCode connection ═══════════════════ */
async function checkOc() {
  const badge = $("#ocBadge");
  badge.textContent = "⬤ در حال بررسی…";
  badge.className = "wspill st-idle";
  try {
    const { opencode } = await api("/api/oc/status");
    if (opencode.running) {
      badge.textContent = "⬤ OpenCode فعال " + (opencode.version || "");
      badge.className = "wspill st-ok";
    } else {
      badge.textContent = "⬤ در حال راه‌اندازی…";
      badge.className = "wspill st-run";
      await api("/api/oc/status");     // ensure spawn
    }
  } catch (e) {
    badge.textContent = "⬤ قطع اتصال";
    badge.className = "wspill st-bad";
    failBar("اتصال OpenCode: " + e.message);
  }
}

/* ═══════════════════ projects / models / sessions ═══════════════════ */
let project = localStorage.getItem("af.project") || "";
let sessionId = localStorage.getItem("af.session") || "";
let lastModel = localStorage.getItem("af.model") || "";   /* سازگاری: اولین انتخاب */

async function loadProjects() {
  const { projects } = await api("/api/projects");
  $("#projSel").innerHTML =
    `<option value="">— انتخاب پروژه —</option>` +
    projects.map((p) => `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join("");
  if (project && projects.some((p) => p.name === project)) $("#projSel").value = project;
  else project = "";
}

/* ═══════════ AF-23 (part 4): provider/model management ═══════════
 * منبع حقیقت = /api/oc/models (پیاده‌سازی واقعی OpenCode). هر Provider:
 * فهرست مدل خودش + مدل انتخابی مستقل خودش (af.model.<provider>).
 * تعویض Provider فقط «انتخاب» را عوض می‌کند؛ تنظیمات/کلید Provider قبلی
 * دست‌نخورده می‌ماند (کلیدها هرگز از مرورگر رد نمی‌شوند — سمت OpenCode).
 * تست اتصال = درخواست کم‌هزینهٔ واقعی با تشخیص صادقانهٔ نتیجه. */
let ALL_MODELS = [];

function providerModelKey(prov) { return "af.model." + prov; }

async function loadModels() {
  const { models } = await api("/api/oc/models");
  ALL_MODELS = models || [];
  const provs = [...new Set(ALL_MODELS.map((m) => m.providerID))];
  const provSel = $("#provSel");
  const cur = provSel.value;
  provSel.innerHTML = provs.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join("");
  const savedProv = localStorage.getItem("af.provider") || "";
  const legacy = localStorage.getItem("af.model") || "";
  const legacyProv = legacy.split("/")[0] || "";
  const chosen = (cur && provs.includes(cur)) ? cur
    : (savedProv && provs.includes(savedProv)) ? savedProv
    : (legacyProv && provs.includes(legacyProv)) ? legacyProv
    : (provs[0] || "");
  provSel.value = chosen;
  fillProviderModels();
}

function fillProviderModels() {
  const prov = $("#provSel").value;
  const sel = $("#modelSel");
  if (!prov) { sel.innerHTML = ""; $("#modelInfo").textContent = "—"; return; }
  const legacy = localStorage.getItem("af.model") || "";
  const saved = localStorage.getItem(providerModelKey(prov)) ||
    (legacy.split("/")[0] === prov ? legacy : "");
  const mine = ALL_MODELS.filter((m) => m.providerID === prov);
  sel.innerHTML = mine.map((m) =>
    `<option value="${esc(m.providerID)}/${esc(m.modelID)}">${esc(m.name)} (${esc(m.modelID)})</option>`).join("");
  if (saved && [...sel.options].some((o) => o.value === saved)) sel.value = saved;
  const mid = sel.value;
  S.selectedModel = mid;
  $("#modelInfo").textContent = mid ? `ارائه‌دهنده/مدل فعال برای ارسال: ${mid}` : "—";
  $("#modelBadge").textContent = "مدل: " + (mid.split("/")[1] || mid || "—");
  $("#modelBadge").className = "wspill st-ok";
}

function onModelChange() {
  const prov = $("#provSel").value;
  const mid = $("#modelSel").value;
  if (prov) localStorage.setItem("af.provider", prov);
  if (prov && mid) localStorage.setItem(providerModelKey(prov), mid);
  localStorage.setItem("af.model", mid);       /* سازگاری با کد موجود */
  S.selectedModel = mid;
  $("#modelInfo").textContent = "ارائه‌دهنده/مدل فعال برای ارسال: " + mid;
  $("#modelBadge").textContent = "مدل: " + (mid.split("/")[1] || mid);
  $("#modelBadge").className = "wspill st-ok";
}

/* AF-23: تست اتصال — درخواست کم‌هزینهٔ واقعی («فقط عدد ۱ را برگردان») به یک
 * نشست موقتی که سرور بعداً حذف می‌کند؛ نتیجه از تشخیص واقعی می‌آید (شبکه/
 * کلید/مدل/سهمیه/آماده)، نه از حدس. */
let testBusy = false;
async function testConnection() {
  if (testBusy) return;
  const prov = $("#provSel").value, mid = $("#modelSel").value;
  const out = $("#testResult");
  if (!prov || !mid) { out.hidden = false; out.textContent = "ابتدا Provider و مدل را انتخاب کنید"; return; }
  if (!project) {
    out.hidden = false;
    out.textContent = "برای تست اتصال ابتدا پروژه انتخاب کنید (نشست آزمون کوتاه در همان پروژه ساخته می‌شود)";
    return;
  }
  testBusy = true;
  $("#btnTestModel").disabled = true;
  out.hidden = false;
  out.textContent = "🧪 در حال آزمودن اتصال به " + mid + " … (حداکثر ~۶۰ ثانیه)";
  const t0 = Date.now();
  try {
    const r = await api("/api/oc/test-model", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, providerID: prov, modelID: mid, timeout_s: 60 }),
    });
    out.textContent = "✅ " + (r.reason || "اتصال سالم است") + " — " +
      ((Date.now() - t0) / 1000).toFixed(1) + " ثانیه · " + (r.model || mid);
  } catch (e) {
    out.textContent = "❌ تست اتصال ناموفق — علت واقعی:";
    showError(e, "تست اتصال");
  } finally {
    testBusy = false;
    $("#btnTestModel").disabled = false;
  }
}

async function loadSessions() {
  const sel = $("#sessSel");
  sel.innerHTML = `<option value="">— نشست —</option>`;
  if (!project) return;
  try {
    const { sessions } = await api("/api/oc/sessions?project=" + encodeURIComponent(project));
    sel.innerHTML += sessions.map((s) => {
      const d = s.updated ? new Date(s.updated).toLocaleString("fa-IR") : "";
      return `<option value="${esc(s.id)}">${esc(s.title)} ${d ? "— " + esc(d) : ""}</option>`;
    }).join("");
    if (sessionId && [...sel.options].some((o) => o.value === sessionId)) sel.value = sessionId;
    else sessionId = "";
  } catch (e) { failBar("فهرست نشست‌ها: " + e.message); }
}

async function newSession() {
  if (!project) return failBar("ابتدا یک پروژه انتخاب کنید");
  const title = prompt("عنوان نشست (اختیاری):");
  try {
    const res = await api("/api/oc/sessions", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, title: title || undefined }),
    });
    sessionId = res.session.id;
    localStorage.setItem("af.session", sessionId);
    await loadSessions();
    $("#sessSel").value = sessionId;
    await openSession();
  } catch (e) { failBar("ایجاد نشست: " + e.message); }
}

async function newProject() {
  const name = prompt("نام پروژه جدید (انگلیسی، مثل my-app):");
  if (!name) return;
  // R4-a: نوع از منبع حقیقت واحد (API) — رفتار قبلی (فول‌استک) حفظ شده است
  let type = null;
  try {
    const d = await api("/api/factory/types");
    const ts = d.types || [];
    type = (ts.find((t) => t.id === "fullstack") || ts[0] || {}).id || null;
    if (!type) throw new Error("هیچ نوع پروژه‌ای از سرور دریافت نشد");
  } catch (e) {
    alert("دریافت انواع پروژه از سرور ناموفق بود: " + e.message);
    return;
  }
  try {
    await api("/api/factory/projects", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, type, description: "ساخته‌شده از Workspace" }),
    });
    project = name;
    localStorage.setItem("af.project", project);
    await loadProjects();
    $("#projSel").value = project;
    sessionId = "";
    await loadSessions();
    renderChat();
  } catch (e) { failBar("ایجاد پروژه: " + e.message); }
}

function showHistory() {
  if (!project) return failBar("پروژه‌ای انتخاب نشده");
  api("/api/oc/sessions?project=" + encodeURIComponent(project)).then(({ sessions }) => {
    const list = sessions.map((s) => {
      const d = s.updated ? faDT(s.updated) : "";
      return `• ${esc(s.title)}${d ? " — " + d : ""}`;
    }).join("\n");
    setDetail(list || "این پروژه نشستی ندارد");
  }).catch((e) => failBar("تاریخچه: " + e.message));
}

/* ═══════════════ AF-01: همگام‌سازی زمان واقعی پیام‌های زنده ═══════════════
 * پیام تازه‌ارسال‌شده ابتدا «⏱ …» نامشخص دارد؛ پس از ثبت در سرور، زمان واقعی
 * از تاریخچه (منبع حقیقت: time.created در OpenCode) خوانده و جای‌گذاری می‌شود.
 * در صورت خطا، «زمان نامشخص» صادقانه می‌ماند — هرگز Date.now() جعل نمی‌شود. */
async function syncMsgTimeFromHistory(metaEl, msgId, kind) {
  if (!metaEl || !metaEl.isConnected) return;
  const span = metaEl.querySelector(".msgtime");
  if (!span || span.dataset.synced) return;
  span.dataset.synced = "pending";
  try {
    const { messages } = await api("/api/oc/history?session=" + encodeURIComponent(sessionId));
    const mine = (messages || []).filter((m) => m.type === kind);
    const hit = msgId ? mine.find((m) => m.id === msgId) : mine[mine.length - 1];
    if (span.dataset.synced !== "pending") return;   // درخواست تازه‌تر جایگزین شد
    span.dataset.synced = "1";
    span.textContent = msgTime(hit && hit.created);
  } catch (e) {
    if (span.dataset.synced === "pending") {
      span.dataset.synced = "1";
      span.textContent = "زمان نامشخص";              // صادقانه — بدون جعل
    }
  }
}

function liveUserMeta() {
  return `<div class="meta">شما · <span class="msgtime">⏱ …</span></div>`;
}

function liveAssistantMeta() {
  /* پیام در حال تولید: هنوز زمان قطعی ندارد → بدون جعل، فقط نشانگر */
  return `<div class="meta">پاسخ · <span class="msgtime">⏱ …</span></div>`;
}

/* ═══════════════════ chat rendering ═══════════════════ */
function renderChat() {
  $("#chat").innerHTML = `<div class="info">نشست یا پیام جدید شروع کنید.</div>`;
  $("#usage").textContent = "";
}

function bubble(cls, html, opts = {}) {
  const div = document.createElement("div");
  div.className = "bubble " + cls;
  div.innerHTML = html;
  $("#chat").appendChild(div);
  /* AF-23 (D): پیام تازهٔ کاربر همیشه دنبال می‌شود؛ بقیه فقط اگر نزدیک انتها باشیم */
  scrollChat(opts.follow);
  return div;
}

function renderHistory(messages) {
  const chat = $("#chat");
  chat.innerHTML = "";
  for (const m of messages) {
    if (m.type === "user") {
      /* AF-01: زمان واقعی ذخیره‌شده + کپی برای پیام کاربر (رفتار یکپارچه با دستیار) */
      const t = msgTime(m.created);
      bubble("user", `<div class="meta">شما · <span class="msgtime">${t}</span></div>` +
        `<div class="msgtext">${esc(m.text)}</div>`);
      attachCopy($$("#chat .bubble.user").pop());
    } else if (m.type === "assistant") {
      const reasoning = (m.parts || []).filter((p) => p.type === "reasoning");
      const texts = (m.parts || []).filter((p) => p.type === "text");
      const tok = m.tokens ? ` · ${m.tokens.input || 0}+${m.tokens.output || 0} tok` : "";
      let html = "";
      if (reasoning.length) {
        html += `<details class="reasoning"><summary>تفکر مدل</summary><pre>${esc(
          reasoning.map((p) => p.text).join("\n"))}</pre></details>`;
      }
      /* AF-23 (C): پاسخ با Markdown واقعی رندر می‌شود (کد/جدول/فهرست/سرتیتر) */
      html += texts.map((p) => `<div class="msgtext">${mdRender(p.text)}</div>`).join("");
      /* AF-01: زمان واقعی ایجاد/تکمیل پیام دستیار (نه زمان رندر صفحه) */
      html += `<div class="meta">پاسخ${esc(m.model ? " · " + m.model : "")}${tok} · ` +
        `<span class="msgtime">${msgTime(m.created)}</span></div>`;
      attachCopy(bubble("assistant", html));
    }
    /* idle/failure messages are rendered by the STATE ENGINE below, not here —
       یعنی نتیجه‌های قدیمی به‌عنوان نتیجهٔ اجرای فعلی نشان داده نمی‌شوند */
  }
  if (!messages.length) {
    chat.innerHTML = `<div class="info">این نشست خالی است. پیام جدیدی بفرستید.</div>`;
  }
}

/* ═══════════════════ the REAL state resolver ═══════════════════
 * Called on session open, page load, SSE loss recovery. It reads the
 * session's true last-turn outcome from the server (no friends-shaped guesses). */
async function resolveServerState() {
  if (!sessionId) return;
  try {
    const st = await api("/api/oc/state?session=" + encodeURIComponent(sessionId));
    S.lastActivity = st.updated || 0;
    await loadPendingPermissions();
    if (pendingPermissions.size) {
      setStatus("permission", { detail: "منتظر تأیید مجوز توسط شماست" });
      return;
    }
    if (st.running) {
      S.turnStart = st.busy || S.turnStart;
      setStatus("generating", { detail: "نشست مشغول است (تأیید سرور)" });
      openEvents();          // live stream refines the status
      return;
    }
    // not running: show the LAST turn outcome verbatim (or keep stale)
    S.turnStart = 0;
    $("#elapsed").hidden = true;
    if (st.outcome === "failed") {
      setStatus("failed");
      const err = st.last_error;
      const since = st.updated ? Math.floor((Date.now() - st.updated) / 60000) : 0;
      let why = "علت نامشخص";
      if (err && err.message) why = err.message +
        (err.retry_after_ms ? ` — تلاش مجدد حدود ${faMin(Math.round(err.retry_after_ms / 1000))} دیگر` : "");
      failBar(`پایان شکست‌خوردهٔ آخرین نوبت (${since} دقیقه پیش) — علت: ${why}`);
      reloadHistoryAfterTurn();   // AF-23
    } else if (st.outcome === "succeeded") {
      setStatus("ok");
      hideError();
      reloadHistoryAfterTurn();   // AF-23: بازیابی — متن واقعی آخرین نوبت برمی‌گردد
    } else if (st.outcome === "interrupted") {
      setStatus("cancelled");
      reloadHistoryAfterTurn();   // AF-23
    } else {
      setStatus("idle");
      hideError();
      if (S.state !== "idle" || document.querySelector(".bubble.assistant[data-open]")) {
        reloadHistoryAfterTurn(); // AF-23: نشست تمام‌شده — تاریخچه را با حقیقت سرور هم‌راستا کن
      }
    }
  } catch (e) {
    setStatus("unknown");
    showError(e, "وضعیت نشست");   /* AF-23: علت واقعی (bridge/serve قطع) به‌جای خط خام */
  }
}

/* ═══════════════════ SSE (live) ═══════════════════ */
function openEvents() {
  /* AF-23 (hole A fix): if the stream is already live, KEEP it — closing and
   * reopening on every send would drop the very events that race the POST. */
  if (S.es && S.es.readyState === 1) return;
  if (S.es) { S.es.close(); S.es = null; }
  if (!sessionId) return;
  const es = new EventSource("/api/oc/events?session=" + encodeURIComponent(sessionId));
  S.es = es;
  es.onopen = () => { loadPendingPermissions(); };
  es.onmessage = (ev) => {
    let evt = null;
    try { evt = JSON.parse(ev.data); } catch { return; }
    if (evt.proxy) return;
    recoverOk();
    handle(evt);
  };
  es.onerror = () => { onConnectionLost(); };      // browser auto-retries; we mark unknown + poll
}

let recoveredOnce = false;
function onConnectionLost() {
  /* #4: SSE قطع → نامشخص + تلاش بازیابی از سرور */
  if (S.state === "sending" || S.state === "thinking" || S.state === "generating" ||
      S.state === "tool" || S.state === "permission" || S.state === "unknown") {
    setStatus("unknown", { detail: "جریان رویداد قطع شد" });
  }
  clearTimeout(S.recoverT);
  let attempts = 0;
  const poll = async () => {
    try {
      const st = await api("/api/oc/state?session=" + encodeURIComponent(sessionId));
      recoverOk();
      S.lastActivity = st.updated || S.lastActivity;
      await loadPendingPermissions();
      if (pendingPermissions.size) {
        setStatus("permission", { detail: "مجوز معلق بازیابی شد" });
        return;
      }
      if (st.running) {
        setStatus("generating", { detail: "بازیابی شد — نشست مشغول است" });
        openEvents();
      } else {
        await resolveServerState();          // show true last-turn state
      }
    } catch (e) {
      attempts++;
      if (attempts === 4) {                  /* AF-23: انتظار بی‌توضیح ممنوع */
        showError(e, "بازیابی وضعیت");       /* علت واقعی + شناسهٔ پیگیری */
        setStatus("unknown", { detail: "بازیابی ناموفق — دوباره تلاش می‌کنیم" });
      }
      S.recoverT = setTimeout(poll, Math.min(4000 * attempts, 15000));
    }
  };
  S.recoverT = setTimeout(poll, 2500);
}
function recoverOk() {
  if (S.state === "unknown") setStatus("generating");   // stream is back
}

let assistantOpen = false;
let lastTextStart = 0;

function assistantShell() {
  let el = document.querySelector(".bubble.assistant:last-of-type");
  if (!el || !el.dataset.open) {
    el = bubble("assistant", `<div class="typing">⌛ در حال کار…</div>` + liveAssistantMeta());
    el.dataset.open = "1";
    assistantOpen = true;
  } else {
    const t = el.querySelector(".typing");
    if (t) t.remove();
  }
  return el;
}
function closeAssistant() {
  const el = document.querySelector(".bubble.assistant:last-of-type");
  if (el && el.dataset.open) {
    delete el.dataset.open;
    assistantOpen = false;
    const t = el.querySelector(".typing");
    if (t) t.remove();
    if (!el.querySelector(".meta")) {
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = "پایان پاسخ";
      el.appendChild(meta);
    }
    /* AF-01: پس از تکمیل، زمان واقعی پیام از تاریخچهٔ سرور خوانده می‌شود */
    const metaEl = el.querySelector(".meta");
    const span = metaEl && metaEl.querySelector(".msgtime");
    if (span && !span.dataset.synced) {
      metaEl.innerHTML = `پاسخ · <span class="msgtime">⏱ …</span>`;
      syncMsgTimeFromHistory(metaEl, "", "assistant");
    }
    attachCopy(el);          /* UI-1: دکمهٔ کپی پس از نمایش کامل پیام */
  }
}

/* ═══════════════ AF-23 (hole B fix): reload history at turn end ═══════════════
 * هر جا نوبت به وضعیت پایانی می‌رسد (موفق/شکست/لغو) یا بازیابی، نتیجهٔ آخرین
 * نوبت را از حقیقت سرور دوباره می‌خوانیم — متنی که مسابقهٔ SSE یا قطع استریم
 * از دست داده بود، از تاریخچهٔ واقعی برمی‌گردد (بدون تکرار/بدون حدس).
 * گارد: اگر نوبتِ تازه‌ای شروع شده باشد، رندر حین اجرا را خراب نمی‌کند. */
let histReloadT = null;
function reloadHistoryAfterTurn(delay = 900) {
  if (histReloadT) clearTimeout(histReloadT);
  histReloadT = setTimeout(async () => {
    histReloadT = null;
    if (!sessionId || S.running) return;       // نوبت تازه فعال شد — دست نزن
    await loadHistory();
  }, delay);
}

/* AF-23 (no endless wait): every 5 s while the UI believes a turn is running,
 * ask the server for the truth. If the server says the turn is already over
 * (fast model + SSE race / stream loss), resolve the state and reload the
 * history — the UI can never sit in "در حال کار…" after the work is done. */
setInterval(async () => {
  if (document.hidden || !sessionId || !S.running || S.state === "permission") return;
  if (S.state === "sending") return;           // هنوز ارسال در جریان است
  try {
    const st = await api("/api/oc/state?session=" + encodeURIComponent(sessionId));
    if (!st.running) await resolveServerState();   // شامل بازخوانی تاریخچه
  } catch { /* شبکه لرزید — تیک بعدی دوباره چک می‌کند */ }
}, 5000);

/* ═══════════════ UI-1: کپی پیام‌های دستیار ═══════════════
 * متن واقعی پاسخ کپی می‌شود — بدون meta/آیکون/زمان/دکمه‌ها/تفکر مدل.
 * مسیر ۱: Clipboard API استاندارد (secure context؛ localhost در Chrome امن است)
 * مسیر ۲: جایگزین کلاسیک textarea + execCommand
 * شکست واقعی صادقانه اعلام می‌شود؛ موفقیت کاذب ممنوع. */
function extractMsgText(el) {
  return Array.from(el.querySelectorAll(".msgtext"))
    .map((n) => {
      const c = n.cloneNode(true);
      /* AF-23: نوار کد (زبان/دکمهٔ کپی) جزو متن پاسخ نیست */
      c.querySelectorAll(".mdcodebar").forEach((b) => b.remove());
      return c.textContent;
    })
    .join("\n\n")
    .trim();
}

/* AF-23 (C): کپی کد از بلوک‌های Markdown — یک listener برای همهٔ دکمه‌ها */
document.addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button.mdcopy");
  if (!btn || btn.dataset.busy) return;
  const code = btn.closest(".mdcode");
  if (!code) return;
  btn.dataset.busy = "1";
  let res;
  try { res = await copyToClipboard(code.querySelector("code").textContent); }
  catch (e) { res = { ok: false, error: e && e.message }; }
  if (res && res.ok) {
    btn.textContent = "✓ کپی شد";
    btn.classList.add("copyok");
  } else {
    btn.textContent = "✗ ناموفق";
    btn.classList.add("copyerr");
    btn.title = "کپی ناموفق: " + ((res && res.error) || "?");
  }
  delete btn.dataset.busy;
  clearTimeout(btn._copyT);
  btn._copyT = setTimeout(() => {
    btn.textContent = "⧉ کپی کد";
    btn.classList.remove("copyok", "copyerr");
  }, 1600);
});

function attachCopy(el) {
  if (!el || el.dataset.copyAttached) return;      // بدون دکمهٔ تکراری
  el.dataset.copyAttached = "1";
  const isUser = el.classList.contains("user");
  const label = isUser ? "کپی متن پیام شما" : "کپی متن پاسخ";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "copybtn";
  btn.setAttribute("aria-label", label);
  btn.title = label;
  btn.textContent = "⧉ کپی";
  btn.addEventListener("click", async () => {
    if (btn.dataset.busy) return;                  // ضد دابل‌لمس
    const text = extractMsgText(el);
    if (!text) { flashCopy(btn, "✗ متنی نبود", "copyerr", "این پیام متنی برای کپی ندارد"); return; }
    btn.dataset.busy = "1";
    let res;
    try { res = await copyToClipboard(text); }
    catch (e) { res = { ok: false, error: (e && e.message) || "خطای ناشناخته" }; }
    finally { delete btn.dataset.busy; }
    if (res && res.ok) flashCopy(btn, "✓ کپی شد", "copyok");
    else flashCopy(btn, "✗ کپی نشد", "copyerr", res && res.error);
  });
  const foot = document.createElement("div");
  foot.className = "msgfoot";
  foot.appendChild(btn);
  el.appendChild(foot);
}

async function copyToClipboard(text) {
  /* ۱) Clipboard API استاندارد — فقط در secure context (Chrome: localhost امن است) */
  try {
    if (window.isSecureContext && navigator.clipboard &&
        typeof navigator.clipboard.writeText === "function") {
      await navigator.clipboard.writeText(text);
      return { ok: true };
    }
  } catch (e) { /* رد شد یا محدودیت مجوز → روش جایگزین */ }
  /* ۲) جایگزین سازگار با اندروید/Chrome قدیمی‌تر و WebView */
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, text.length);
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
    document.body.removeChild(ta);                 // DOM پاک می‌ماند
    if (ok) return { ok: true };
    return { ok: false, error: "مرورگر اجازهٔ دسترسی به کلیپ‌بورد را نداد" };
  } catch (e) {
    return { ok: false, error: (e && e.message) ? e.message : "خطای ناشناخته در کپی" };
  }
}

function flashCopy(btn, label, cls, errDetail) {
  btn.classList.remove("copyok", "copyerr");
  btn.classList.add(cls);
  btn.textContent = label;
  btn.title = errDetail ? ("کپی ناموفق: " + errDetail) : "کپی متن پاسخ";
  clearTimeout(btn._copyT);
  btn._copyT = setTimeout(() => {
    btn.classList.remove("copyok", "copyerr");
    btn.textContent = "⧉ کپی";
    btn.title = "کپی متن پاسخ";
  }, 1600);
}

/* turn reset: any NEW turn wipes ALL prior result bars (#1, #2) */
function beginTurn(ts) {
  S.turnStart = ts || Date.now();
  S.lastActivity = Date.now();
  hideError();
  setDetail(null);
  $("#elapsed").hidden = false;
  runTicker();
}

function handle(evt) {
  const t = evt.type;
  S.lastActivity = Date.now();               // every event = real activity

  /* --- lifecycle marks --- */
  if (t === "session.prompted" || t === "session.message.created") beginTurn();

  if (t === "session.step.started") {
    setStatus(S.toolCount ? "thinking" : "thinking");
    assistantShell();
  } else if (t === "session.reasoning.started") {
    setStatus("thinking");
    assistantShell();
  } else if (t === "session.text.started" || t === "session.text.delta") {
    setStatus("generating", { detail: "کلمه‌ها در حال نوشته شدن" });
    lastTextStart = Date.now();
    assistantShell();
  } else if (t === "session.reasoning.delta" || t === "session.reasoning.ended" ||
             t === "session.text.ended") {
    statusFromStream(t);
    if (t.endsWith(".delta")) appendModelText(t, evt, true);
    if (t === "session.reasoning.ended") appendModelText(t, evt, true);
  } else if (t === "session.step.ended") {
    closeAssistant();
    setStatus("testing", { detail: "پایان این مرحله — محاسبه ادامه" });
  } else if (t === "session.execution.succeeded") {
    /* real end-of-turn success — clears ANY stale failure */
    closeAssistant();
    setStatus("ok");
    hideError();
    setDetail(null);
    S.turnStart = 0;
    $("#elapsed").hidden = true;
    reloadHistoryAfterTurn();   // AF-23: متن گم‌شده از تاریخچهٔ واقعی برمی‌گردد
  } else if (t === "session.execution.failed") {
    closeAssistant();
    setStatus("failed");
    S.turnStart = 0;
    $("#elapsed").hidden = true;
    failBar("پایان شکست‌خورده مدل — جزئیات در نوار پایین صفحه/تاریخچه");
    reloadHistoryAfterTurn();   // AF-23
  } else if (t === "session.interrupted" || t === "session.cancelled") {
    closeAssistant();
    setStatus("cancelled");
    S.turnStart = 0;
    reloadHistoryAfterTurn();   // AF-23
  } else if (t === "session.usage.updated") {
    const u = (evt.data && evt.data.usage) || {};
    $("#usage").textContent =
      `tokens: in ${u.input || 0} · out ${u.output || 0} · cost ${u.cost ?? 0}`;
  } else if (t === "session.error") {
    const d = evt.data || {};
    closeAssistant();
    setStatus("failed");
    S.turnStart = 0;
    $("#elapsed").hidden = true;
    let extra = "";
    if (d.message && /limit|429/i.test(d.message)) { extra = "\nسقف مدل/ارائه‌دهنده است — بعد از رفع محدودیت دوباره تلاش کنید."; }
    failBar("خطای مدل: " + (d.message || JSON.stringify(d).slice(0, 300)) + extra);
    reloadHistoryAfterTurn();   // AF-23: حتی پس از خطا، بخش دریافت‌شده گم نمی‌شود
  } else if (t.startsWith("session.permission") || evt.permission) {
    applyPermissionEvent(evt.permission || evt.data);
    loadPendingPermissions();
  } else {
    /* --- tool activity --- */
    const isTool = t.endsWith(".tool") || t.includes("tool");
    if (isTool) { statusFromStream(t); }
    else if (t.startsWith("file.")) { statusFromStream(t); }
    else if (t.startsWith("bash.")) { statusFromStream(t); }
    else if (t.startsWith("session.")) { statusFromStream(t); }
  }
}

/* state refinements driven by REAL event names */
function statusFromStream(t) {
  if (t.includes("write") || t.includes("edit") || t.includes("file"))
    setStatus("generating", { detail: "فایل‌های در حال ساخته/ویرایش شدن" });
  else if (t.includes("bash") || t.includes("tool") || t.includes("webfetch"))
    setStatus("tool");
  else if (t.includes("test") || t.includes("check"))
    setStatus("testing");
  else if (t.includes("compaction")) setStatus("thinking");
}

function appendModelText(t, evt, isDelta) {
  /* delta-writing zones (reasoning & text) live inside the open assistant */
  const delta = (evt.data && evt.data.delta);
  if (!delta) return;
  const el = assistantShell();
  const isReason = t.startsWith("session.reasoning");
  if (isReason) {
    let zone = el.querySelector("details.reasoning");
    if (!zone) {
      zone = document.createElement("details");
      zone.className = "reasoning";
      zone.innerHTML = "<summary>تفکر مدل</summary><pre></pre>";
      el.prepend(zone);
    }
    const pre = zone.querySelector("pre");
    pre.textContent += delta;
  } else {
    /* AF-23 (C): متن زنده در بافر واقعی نگه داشته می‌شود و هر بار از نو با
     * Markdown رندر می‌شود — بدون ازدست‌رفتن هیچ قطعه‌ای، بدون تکرار. */
    el._raw = (el._raw || "") + delta;
    let zone = el.querySelector(".out");
    if (!zone) { zone = document.createElement("div"); zone.className = "out msgtext"; el.appendChild(zone); }
    zone.innerHTML = mdRender(el._raw);
  }
  scrollChat(false);          /* AF-23 (D): فقط وقتی کاربر نزدیک انتهاست */
}

/* ═══════════════════ permissions (AF-56) ═══════════════════ */
let pendingPermissions = new Map();     // rid -> request, scoped to current session

function hidePerm() {
  const el = $("#permBar");
  el.hidden = true;
  el.innerHTML = "";
  pendingPermissions.clear();
}

async function loadPendingPermissions() {
  if (!sessionId) return;
  try {
    const r = await api("/api/oc/permissions?session=" + encodeURIComponent(sessionId));
    const items = r.permissions || [];
    const ids = items.map((p) => p.id || p.requestID).filter(Boolean);
    // do not replace a richer SSE permission with a thinner API copy
    const known = new Set(pendingPermissions.keys());
    const sig = ids.join("|");
    if (sig === (window.AF_PERM_SIG || "")) return;
    window.AF_PERM_SIG = sig;
    pendingPermissions = new Map();
    items.forEach((p) => {
      const rid = p.id || p.requestID;
      if (rid) pendingPermissions.set(rid, p);
    });
    renderPermissions();
  } catch (e) {
    /* transient: SSE or the next poll will retry; never fake a decision */
  }
}

function renderPermissions() {
  const bar = $("#permBar");
  const items = [...pendingPermissions.values()];
  if (!items.length) { hidePerm(); return; }
  bar.innerHTML = items.map((p) => {
    const rid = p.id || p.requestID;
    const action = p.action || "؟";
    const resources = Array.isArray(p.resources) && p.resources.length
      ? p.resources.map(String).join(" | ") : String(p.resource || "");
    return `<div class="af-perm" data-rid="${esc(rid)}">
      <div>🔒 منتظر تأیید شما: <b>${esc(action)}</b> — ${esc(resources).slice(0, 180)}</div>
      <div class="af-actions">
        <button type="button" class="btn small" data-d="once">اجازه یکبار</button>
        <button type="button" class="btn small" data-d="always">همیشه</button>
        <button type="button" class="btn small danger" data-d="reject">رد</button>
      </div><div class="af-msg"></div></div>`;
  }).join("");
  bar.hidden = false;
  setStatus("permission", { detail: "منتظر تأیید مجوز توسط شماست" });
  bar.querySelectorAll(".af-perm button").forEach((b) => b.addEventListener("click", async () => {
    const wrap = b.closest(".af-perm");
    const rid = wrap.getAttribute("data-rid");
    const msg = wrap.querySelector(".af-msg");
    msg.textContent = "در حال ارسال…";
    try {
      const r = await api("/api/oc/permission", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, request_id: rid,
                               decision: b.dataset.d }),
      });
      if (r && r.ok === false) { msg.textContent = r.error || "مجوز قبلاً پاسخ داده شد"; }
      else msg.textContent = b.dataset.d === "reject" ?
        "مجوز رد شد — نتیجهٔ واقعی ابزار نمایش داده میشود." : "مجوز تأیید شد.";
      pendingPermissions.delete(rid);
      window.AF_PERM_SIG = "";
      await loadPendingPermissions();
      await resolveServerState();
    } catch (e) { msg.textContent = "خطا: " + e.message; }
  }));
}

function applyPermissionEvent(p) {
  if (!p) return;
  const rid = p.id || p.requestID;
  if (!rid) return;
  pendingPermissions.set(rid, p);
  window.AF_PERM_SIG = "";
  renderPermissions();
}

/* ═══════════════════ sending ═══════════════════ */
async function send(ev) {
  ev.preventDefault();
  /* AF-23: جلوگیری از ارسال تکراری ناخواسته — تا وقتی نوبت واقعی در جریان است،
   * فرم ارسال جدید را نمی‌پذیرد (کاربر می‌تواند اول «لغو» بزند). */
  if (S.running || S.state === "sending") {
    return failBar("نوبت قبلی هنوز در جریان است — اول لغو کنید یا منتظر پایان بمانید");
  }
  if (!sessionId) return failBar("نشستی انتخاب نشده است");
  const input = $("#msgInput");
  const text = input.value.trim();
  if (!text) return;
  const model = $("#modelSel").value;
  /* AF-01: پیام کاربر با وضعیت «زمان نامشخص» شروع می‌شود؛ پس از ثبت سرور،
   * زمان واقعی ذخیره‌شده جای‌گذاری می‌شود (نه زمان جعلیِ همین لحظه) */
  const userMsg = bubble("user", liveUserMeta() + `<div class="msgtext">${esc(text)}</div>`,
    { follow: true });   /* AF-23 (D): پیام تازهٔ خود کاربر همیشه دنبال می‌شود */
  attachCopy(userMsg);
  input.value = "";
  /* #1 fresh status for the new run */
  beginTurn();
  setStatus("sending");
  /* AF-23 (hole A fix): the event stream is connected BEFORE the prompt is
   * submitted — the old order (SSE after the POST response) let a fast model
   * finish the whole turn before any SSE existed, so every delta AND the
   * end-of-turn event were lost: the UI stayed "در حال کار…" forever. */
  openEvents();
  try {
    const res = await api("/api/oc/message", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, session_id: sessionId, text, model,
        variant: currentReasoningVariant() }),
    });
    userMsg.dataset.mid = res.message_id || "";
    /* AF-01: زمان واقعی از تاریخچهٔ سرور خوانده می‌شود */
    syncMsgTimeFromHistory(userMsg.querySelector(".meta"), res.message_id || "", "user");
    setStatus("thinking");
    openEvents();
  } catch (e) {
    input.value = text;
    /* AF-01: ارسال ناموفق → زمان هرگز جعل نمی‌شود؛ صادقانه نامشخص می‌ماند */
    const span = userMsg.querySelector(".msgtime");
    if (span && !span.dataset.synced) { span.dataset.synced = "1"; span.textContent = "زمان نامشخص (ارسال ناموفق)"; }
    setStatus("idle", { detail: "ارسال ناموفق" });
    S.turnStart = 0;
    showError(e, "ارسال");   /* AF-23: تشخیص واقعی + شناسهٔ پیگیری، نه یک خط خام */
  }
}

/* ═══════════════════ cancel (server-verified) ═══════════════════ */
async function abortRun() {
  if (!sessionId || !S.running) return;   // never cancel a non-running thing
  try {
    const r = await api("/api/oc/interrupt", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    if (r.interrupted) {
      setStatus("cancelled", { detail: "تأیید شده توسط سرور" });
      setDetail("عملیات لغو شد (تأیید سرور).");
    } else {
      setDetail("سرور تأیید کرد: نشست در حال اجرا نبود.");
      await resolveServerState();
    }
  } catch (e) { showError(e, "لغو"); }
}

/* ═══════════════════ project tools panel (real APIs) ═══════════════════ */
document.querySelectorAll(".wtab").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".wtab").forEach((x) => x.classList.remove("active"));
  b.classList.add("active");
  document.querySelectorAll(".wtabpane").forEach((x) => x.hidden = true);
  const pane = $("#tp-" + b.dataset.t);
  pane.hidden = false;
  if (b.dataset.t === "files") loadFiles();
  if (b.dataset.t === "run") loadRunStatus();
  if (b.dataset.t === "preview") loadPreview();
}));

/* ── P5: hierarchical tree (client-side from the flat real listing) ── */
function buildTreeList(paths) {
  const root = {};
  for (const p of paths) {
    const parts = p.split("/");
    let node = root;
    for (let i = 0; i < parts.length; i++) {
      const isFile = i === parts.length - 1;
      const key = (isFile ? "" : "📁") + parts[i];
      node[key] = node[key] || {};
      node[key].__p = node[key].__p || p;
      if (isFile) node[key].__file = true;
      node = node[key];
    }
  }
  return root;
}
function renderTree(node) {
  return Object.keys(node)
    .filter((k) => k !== "__p" && k !== "__file").sort()
    .map((k) => {
      const sub = node[k];
      if (sub.__file) {
        return `<li><span class="fpath">${esc(sub.__p)}</span><span class="fbtns">` +
          `<button class="btn small ghost" data-f="${esc(sub.__p)}">مشاهده</button>` +
          `<button class="btn small ghost" data-r="${esc(sub.__p)}">✏️</button>` +
          `<button class="btn small ghost" data-d="${esc(sub.__p)}">🗑</button>` +
          `</span></li>`;
      }
      return `<li class="fd"><details open><summary>${esc(k)}</summary>` +
             `<ul>${renderTree(sub)}</ul></details></li>`;
    }).join("");
}

async function loadFiles() {
  if (!project) { $("#filesNote" ).textContent = "پروژه‌ای انتخاب نشده"; return; }
  try {
    const { files } = await api("/api/files?project=" + encodeURIComponent(project));
    $("#filesNote").textContent = `${files.length} فایل`;
    if (!files.length) {
      $("#fileList").innerHTML = "<li class='ws-note'>پروژه خالی است</li>";
      return;
    }
    $("#fileList").innerHTML = renderTree(buildTreeList(
      files.map((x) => x.path).sort()));
    $("#fileList").querySelectorAll("button[data-f]").forEach((btn) =>
      btn.addEventListener("click", () => {
        $("#editPath").value = btn.dataset.f;
        document.querySelector('.wtab[data-t="edit"]').click();
        loadEdit();
      }));
    $("#fileList").querySelectorAll("button[data-r]").forEach((btn) =>
      btn.addEventListener("click", () => renameFile(btn.dataset.r)));
    $("#fileList").querySelectorAll("button[data-d]").forEach((btn) =>
      btn.addEventListener("click", () => deleteFile(btn.dataset.d)));
  } catch (e) { $("#filesNote").textContent = "خطا: " + e.message; }
}

/* ── P5: real create / rename / delete (with user confirmation) ── */
async function createFile() {
  if (!project) { $("#filesNote").textContent = "پروژه‌ای انتخاب نشده"; return; }
  const rel = ($("#newFileRel").value || "").trim();
  if (!rel) { $("#filesNote").textContent = "مسیر فایل جدید را وارد کنید"; return; }
  try {
    const probe = await api("/api/file?project=" + encodeURIComponent(project) +
                            "&path=" + encodeURIComponent(rel));
    if (probe.content !== undefined || probe.binary) {
      $("#filesNote").textContent = "خطا: فایلی با این نام از قبل وجود دارد";
      return;
    }
  } catch { /* 404 = not exists → safe to create */ }
  try {
    await api("/api/file", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, path: rel, content: "" }),
    });
    $("#newFileRel").value = "";
    $("#filesNote").textContent = "✅ فایل ایجاد شد: " + rel;
    loadFiles();
  } catch (e) { $("#filesNote").textContent = "خطا در ایجاد: " + e.message; }
}

async function renameFile(from) {
  if (!project) return;
  const to = prompt("مسیر جدید برای «" + from + "» :", from);
  if (to === null || (to = (to || "").trim()) === from || !to) return;
  try {
    await api("/api/file/rename", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, path: from, new_path: to }),
    });
    $("#filesNote").textContent = "✅ تغییر نام انجام شد";
    loadFiles();
  } catch (e) { $("#filesNote").textContent = "خطا در تغییر نام: " + e.message; }
}

async function deleteFile(rel) {
  if (!project) return;
  if (!confirm("فایل «" + rel + "» حذف شود؟ این عمل بازگشتی ندارد.")) return;
  try {
    await api("/api/file?project=" + encodeURIComponent(project) +
              "&path=" + encodeURIComponent(rel),
              { method: "DELETE" });
    $("#filesNote").textContent = "✅ حذف شد: " + rel;
    loadFiles();
  } catch (e) { $("#filesNote").textContent = "خطا در حذف: " + e.message; }
}

async function loadEdit() {
  if (!project) { $("#editMsg").textContent = "پروژه انتخاب نشده"; return; }
  const p = $("#editPath").value.trim();
  if (!p) { $("#editMsg").textContent = "مسیر فایل را وارد کنید"; return; }
  try {
    const r = await api("/api/file?project=" + encodeURIComponent(project) +
                        "&path=" + encodeURIComponent(p));
    $("#editor").value = r.content ?? "";
    $("#editMsg").textContent = "خوانده شد (" + (r.content || "").length + " کاراکتر)";
  } catch (e) { $("#editMsg").textContent = "خطا: " + e.message; }
}

async function saveEdit() {
  if (!project) { $("#editMsg").textContent = "پروژه انتخاب نشده"; return; }
  const p = $("#editPath").value.trim();
  try {
    await api("/api/file", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, path: p, content: $("#editor").value }),
    });
    $("#editMsg").textContent = "✅ ذخیره شد " + new Date().toLocaleTimeString("fa-IR");
  } catch (e) { $("#editMsg").textContent = "خطا: " + e.message; }
}

let logWhich = "server";
async function loadLogs() {
  if (logWhich === "run") {
    try {
      const r = await api("/api/factory/runlog?project=" + encodeURIComponent(project));
      $("#logsBox").textContent = typeof r.log === "string" ? r.log : JSON.stringify(r);
    } catch (e) { $("#logsBox").textContent = "خطا: " + e.message; }
    return;
  }
  try {
    const { logs } = await api("/api/logs?lines=120&which=" + logWhich);
    $("#logsBox").textContent = logs || "خالی";
  } catch (e) { $("#logsBox").textContent = "خطا: " + e.message; }
}

async function loadRunStatus() {
  if (!project) { $("#runStatus").textContent = "پروژه انتخاب نشده"; return; }
  try {
    const { runs } = await api("/api/factory/runs");
    const mine = (runs || []).find((x) => x.name === project);
    if (mine) {
      $("#runStatus").textContent = mine.running
        ? `▶ در حال اجرا روی http://127.0.0.1:${mine.port}`
        : "⏹ متوقف است";
      const prev = $("#btnPreview");
      if (mine.running && mine.port) { prev.hidden = false; prev.href = "http://127.0.0.1:" + mine.port; }
      else prev.hidden = true;
    } else { $("#runStatus").textContent = "پروژه فاقد رکورد اجرا"; }
  } catch (e) { $("#runStatus").textContent = "خطا: " + e.message; }
}

async function runProject() {
  if (!project) return;
  try {
    const r = await api("/api/factory/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project }),
    });
    if (r.running) {
      $("#runStatus").textContent =
        `▶ در حال اجرا روی http://127.0.0.1:${r.port}` +
        (r.port_warning ? " — ⚠️ " + r.port_warning : "");
    } else {
      /* P6-5: honest failure with the real log tail */
      $("#runStatus").textContent =
        "اجرای ناموفق — انتهای لاگ:\n" + (r.log_tail || "…");
    }
    loadRunStatus(); loadPreview();
  } catch (e) { $("#runStatus").textContent = "خطا: " + e.message; }
}

async function stopProject() {
  if (!project) return;
  try {
    const r = await api("/api/factory/stop", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project }),
    });
    $("#runStatus").textContent = r.ok || r.stopped ? "توقف انجام شد" : JSON.stringify(r);
    loadRunStatus(); loadPreview();
  } catch (e) { $("#runStatus").textContent = "خطا: " + e.message; }
}

/* ── P5: REAL live preview (iframe to the actually-running server) ── */
let prevSrc = "";
async function loadPreview() {
  if (!project) { $("#prevNote").textContent = "پروژه‌ای انتخاب نشده"; return; }
  $("#prevNote").textContent = "بررسی وضعیت سرور…";
  try {
    const { runs } = await api("/api/factory/runs");
    const mine = (runs || []).find((x) => x.name === project);
    if (mine && mine.running && mine.port) {
      const url = "http://127.0.0.1:" + mine.port + "/";
      $("#prevNote").textContent =
        `▶ پیش‌نمایش زنده: ${url} (نوع: ${mine.kind || "?"})` +
        (mine.port_warning ? " — ⚠️ " + mine.port_warning : "");
      $("#btnPrevNewTab").href = url; $("#btnPrevNewTab").hidden = false;
      if ($("#prevFrame").hidden || prevSrc !== url) {
        prevSrc = url; $("#prevFrame").src = url; $("#prevFrame").hidden = false;
      }
    } else {
      prevSrc = ""; $("#prevFrame").removeAttribute("src");
      $("#prevFrame").hidden = true; $("#btnPrevNewTab").hidden = true;
      $("#prevNote").textContent =
        "⏹ سرور پروژه در حال اجرا نیست — دکمه «اجرای پروژه» را بزنید. پس از اجرا نمای زنده همین‌جا می‌آید.";
    }
  } catch (e) { $("#prevNote").textContent = "خطا: " + e.message; }
}

/* P6-5: watch the running preview — if its server dies mid-session,
 * the preview note flips to the offline message (honest errors). */
setInterval(async () => {
  if (!project || !prevSrc) return;
  try {
    const { runs } = await api("/api/factory/runs");
    const mine = (runs || []).find((x) => x.name === project);
    if (!mine || !mine.running) { prevSrc = ""; loadPreview(); }
  } catch { /* panel hiccup — next tick retries, never fakes green */ }
}, 8000);

/* ── P5: copy output/error (+ send to OpenCode with user confirmation) ── */
async function copyLogs() {
  const text = ($("#logsBox").textContent || "").trim();
  if (!text || text === "—") { $("#logActMsg").textContent = "چیزی برای کپی نیست"; return; }
  try {
    await navigator.clipboard.writeText(text);
    $("#logActMsg").textContent = "✅ کپی شد";
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); $("#logActMsg").textContent = "✅ کپی شد"; }
    catch { $("#logActMsg").textContent = "کپی ناموفق (دسترسی مرورگر)"; }
    ta.remove();
  }
}
function sendLogsToOC() {
  const raw = ($("#logsBox").textContent || "").trim();
  if (!raw || raw === "—") { $("#logActMsg").textContent = "چیزی برای ارسال نیست"; return; }
  /* scrub obviously private lines before anything leaves this page */
  const scrubbed = raw.split("\n")
    .filter((l) => !/OPENCODE_SERVER_PASSWORD|OPENCODE_TOKEN|ai_key|API_KEY|api_key|Bearer\s+\S+/.test(l))
    .slice(-60).join("\n");
  const ok = confirm(
    "خروجی/خطای زیر به‌عنوان پیام برای OpenCode در «متن پیام» قرار می‌گیرد\n" +
    "و فقط وقتی کلید ارسال را بزنید واقعاً ارسال می‌شود:\n\n" +
    scrubbed.slice(0, 600));
  if (!ok) { $("#logActMsg").textContent = "ارسال لغو شد"; return; }
  const box = $("#msgInput");
  if (!box) { $("#logActMsg").textContent = "این فرم در تب فایل/اجرا پیدا نشد"; return; }
  box.value =
    "پروژه «" + project + "» این خروجی/خطا را داده؛ لطفاً علت را پیدا و در همان فایل اصلاح کن:\n" +
    "```\n" + scrubbed + "\n```\n";
  box.focus();
  $("#logActMsg").textContent =
    "✅ متن در جعبه پیام گذاشته شد — با زدن ارسال برای OpenCode می‌رود";
}

async function runTests() {
  if (!project) { $("#testOut").textContent = "پروژه انتخاب نشده"; return; }
  setStatus("testing", { detail: "آزمون‌های پروژه در حال اجرا" });
  $("#testOut").textContent = "در حال اجرا…";
  try {
    const r = await api("/api/factory/testrun", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project }),
    });
    $("#testOut").textContent = (r.ran ? (r.ok ? "✔ موفق" : "✘ ناموفق") + "\n" : "") + (r.output || r.note || "");
    setStatus(S.state === "testing" ? (r.ok ? "ok" : "failed") : S.state);
  } catch (e) { $("#testOut").textContent = "خطا: " + e.message; setStatus("idle"); }
}

async function runDeps() {
  if (!project) { $("#testOut").textContent = "پروژه انتخاب نشده"; return; }
  try {
    const r = await api("/api/factory/deps", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project }),
    });
    $("#testOut").textContent = r.note || r.output || "نصب انجام شد";
  } catch (e) { $("#testOut").textContent = "خطا: " + e.message; }
}

async function buildZip() {
  if (!project) { $("#zipOut").textContent = "پروژه انتخاب نشده"; return; }
  try {
    const r = await api("/api/factory/export", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project }),
    });
    const link = `/api/factory/export?project=${encodeURIComponent(project)}&path=${encodeURIComponent(r.path)}&download=1`;
    $("#zipOut").innerHTML =
      `✅ ${r.files} فایل، ${Math.round(r.size_bytes / 1024)} کیلوبایت ` +
      (r.integrity_bad_file == null ? "، سالم" : "، خرابی!") +
      ` — <a class="btn small" href="${esc(link)}">⬇ دریافت</a>`;
  } catch (e) { $("#zipOut").textContent = "خطا: " + e.message; }
}

/* ═══════════════════ wiring ═══════════════════ */
$("#projSel").addEventListener("change", async (e) => {
  project = e.target.value; localStorage.setItem("af.project", project);
  sessionId = ""; await loadSessions(); renderChat(); setStatus("idle");
  setDetail(null); hideError();
});
$("#sessSel").addEventListener("change", async (e) => {
  sessionId = e.target.value;
  localStorage.setItem("af.session", sessionId);
  await openSession();
});
$("#btnNew").addEventListener("click", newSession);
$("#btnNewProj").addEventListener("click", newProject);
$("#btnHistory").addEventListener("click", showHistory);
$("#btnTestModel").addEventListener("click", testConnection);   /* AF-23 */
$("#btnAbort").addEventListener("click", abortRun);
$("#msgForm").addEventListener("submit", send);
$("#provSel").addEventListener("change", () => {
  localStorage.setItem("af.provider", $("#provSel").value);
  fillProviderModels();     /* AF-23: مدلِ ذخیره‌شدهٔ همین Provider برمی‌گردد */
});
$("#modelSel").addEventListener("change", () => { onModelChange(); updateReasonBadge(); });
$("#reasonSel").addEventListener("change", async () => {
  const v = $("#reasonSel").value;
  localStorage.setItem("af.reasoning", v);
  updateReasonBadge();
  try { await api("/api/reasoning/settings", { method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reasoning_level: v }) }); } catch (e) { /* local setting still kept */ }
});
$("#modelVariant").addEventListener("change", () => {
  localStorage.setItem("af.variant", $("#modelVariant").value);
});
$("#btnArchiveOld").addEventListener("click", async () => {
  try {
    const r = await api("/api/interactions/archive", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ all_old: true }) });
    const msg = $("#interactList .af-muted") || $("#interactList");
    msg.textContent = `${r.count} درخواست قدیمی بایگانی شد (هیچ دادهای حذف نشد).`;
  } catch (e) { failBar("بایگانی: " + e.message); }
});
$("#askScope").addEventListener("change", () => {
  api("/api/interactions").then(renderInteractions).catch(() => {});
});
$("#btnRefreshFiles").addEventListener("click", loadFiles);
$("#btnEditLoad").addEventListener("click", loadEdit);
$("#btnEditSave").addEventListener("click", saveEdit);
$("#btnLogServer").addEventListener("click", () => { logWhich = "server"; loadLogs(); });
$("#btnLogError").addEventListener("click", () => { logWhich = "error"; loadLogs(); });
$("#btnLogRun").addEventListener("click", () => { logWhich = "run"; loadLogs(); });
$("#btnLogRefresh").addEventListener("click", loadLogs);
$("#btnRunStart").addEventListener("click", runProject);
$("#btnRunStop").addEventListener("click", stopProject);
$("#btnNewFile").addEventListener("click", createFile);
$("#btnPrevRefresh").addEventListener("click", loadPreview);
$("#btnPrevStart").addEventListener("click", () => runProject().then(loadPreview));
$("#btnLogCopy").addEventListener("click", copyLogs);
$("#btnLogToOC").addEventListener("click", sendLogsToOC);
$("#btnTests").addEventListener("click", runTests);
$("#btnDeps").addEventListener("click", runDeps);
$("#btnZip").addEventListener("click", buildZip);

function renderChatEmpty() { renderChat(); }

async function openSession() {
  hideError(); hidePerm(); setDetail(null);
  renderChatEmpty();
  await resolveServerState();
  await loadHistory();
  loadFiles();
}

async function loadHistory() {
  if (!sessionId) { renderChat(); return; }
  try {
    const { messages } = await api("/api/oc/history?session=" + encodeURIComponent(sessionId));
    renderHistory(messages);
  } catch (e) { failBar("تاریخچه: " + e.message); }
}

/* ═════════ AF-51: reasoning control + live interaction center ═════════ */
let REASON_CATALOG = [];
let INTERACTION_ES = null;

function currentModelEntry() {
  const mid = $("#modelSel").value || "";
  return REASON_CATALOG.find((m) => m.id === mid) || null;
}

function currentReasoningVariant() {
  const entry = currentModelEntry();
  const level = $("#reasonSel").value || "auto";
  const info = entry && entry.levels && entry.levels[level];
  return (info && info.supported && info.variant) ? info.variant : "";
}

function updateReasonBadge() {
  const entry = currentModelEntry();
  const level = $("#reasonSel").value || "auto";
  const badge = $("#reasonBadge"), info = $("#reasonInfo");
  if (!entry) { badge.textContent = "—"; return; }
  const rec = entry.levels[level] || {};
  if (!rec.supported) {
    badge.textContent = "پشتیبانی نمیشود";
    badge.className = "wspill st-bad";
    info.textContent = "مدل «" + entry.name + "» هیچ سطح تفکری ندارد؛ تنظیم قابل اعمال نیست.";
  } else if (!rec.exact) {
    badge.textContent = "نزدیکترین: " + rec.variant;
    badge.className = "wspill st-wait";
    info.textContent = "مدل سطح دقیق «" + level + "» را ندارد؛variant واقعی «" + rec.variant + "» اعمال میشود.";
  } else {
    badge.textContent = rec.variant;
    badge.className = "wspill st-ok";
    info.textContent = "سطح «" + level + "» باvariant واقعی «" + rec.variant + "» روی نشست اعمال میشود.";
  }
}

async function loadReasoning() {
  try {
    const r = await api("/api/reasoning/models" +
      (project ? "?project=" + encodeURIComponent(project) : ""));
    REASON_CATALOG = r.models || [];
    const sel = $("#reasonSel");
    sel.value = localStorage.getItem("af.reasoning") || r.default || "auto";
    updateReasonBadge();
  } catch (e) {
    $("#reasonInfo").textContent = "دریافت قابلیتهای تفکر ناموفق: " + e.message;
  }
}

function renderInteractions(payload) {
  const box = $("#interactList");
  const all = (payload && payload.items) || [];
  const scoped = $("#askScope") && $("#askScope").checked ?
    all.filter((x) => !project || x.project === project) : all;
  const open = scoped.filter((x) => x.active);
  $("#askCount").textContent = String(open.length);
  $("#askCount").className = "wspill " + (open.length ? "st-wait" : "st-idle");
  if (!open.length) {
    box.innerHTML = '<div class="af-empty"><div class="af-emoji">✨</div>در حال حاضر تصمیمی از طرف شما لازم نیست.</div>' +
      (scoped.length ? `<details class="af-history"><summary>تاریخچهٔ تصمیمها (${scoped.length})</summary>${scoped.map(card).join("")}</details>` : "");
    return;
  }
  box.innerHTML = open.map(card).join("") +
    (scoped.length > open.length ? `<details class="af-history"><summary>تاریخچهٔ تصمیمها (${scoped.length - open.length})</summary>${scoped.filter(x=>!x.active).map(card).join("")}</details>` : "");
  bindAskActions(box);
}

function card(a) {
  const labels = { open: "نیازمند پاسخ", answered: "پاسخدادهشده",
    cancelled: "لغوشده", expired: "منقضیشده", archived: "بایگانی شده",
    orphaned: "پروژه غیرفعال", task_stopped: "وظیفه متوقفشده" };
  const recommendation = a.recommended || (a.action === "confirm" ? "فقط پیشنمایش" : "");
  const labelsByAction = a.action === "confirm" ?
      ["تأیید و ادامه", "رد و توقف"] :
      a.action === "resume" ? ["ادامهٔ ایمن", "توقف"] : ["ارسال پاسخ", "لغو درخواست"];
  const options = (a.options || []).map((o) =>
      `<button type="button" class="btn small ghost ask-opt ${o === recommendation ? "primary" : ""}" data-answer="${esc(o)}">${esc(o)}${o === recommendation ? " · پیشنهادی" : ""}</button>`).join(" ");
  const when = a.created ? new Date(Number(a.created) * 1000).toLocaleString("fa-IR") : "—";
  return `<div class="af-decision ask-item ${esc(a.status)}" data-ask="${esc(a.id)}" data-token="${esc(a.token || "")}">
    <div class="ask-head"><strong>${esc(a.project || "—")}</strong>
      <span class="af-chip">${esc(a.task || "")}</span>
      <span class="af-muted">${when}</span>
      <span class="af-chip ${a.active ? "wait" : ""}">${esc(labels[a.status] || a.status)}</span></div>
    <div class="ask-q">${esc(a.question || "")}</div>
    ${a.reason ? `<div class="ask-why">چرا لازم است: ${esc(a.reason)}</div>` : ""}
    ${recommendation ? `<div class="af-muted" style="font-size:.78rem;">پیشنهاد: ${esc(recommendation)}</div>` : ""}
    ${a.active ? `<div class="af-actions ask-actions">
      <input class="ask-input" placeholder="پاسخ متنی یا گزینه را وارد کنید">
      <button type="button" class="btn small primary ask-send">${esc(labelsByAction[0])}</button>${options}
      <button type="button" class="btn small ask-cancel">${esc(labelsByAction[1])}</button></div>` : ""}
    <div class="ask-msg af-muted"></div></div>`;
}

function bindAskActions(box) {
  box.querySelectorAll(".ask-opt").forEach((b) => b.addEventListener("click", () => {
    const item = b.closest(".ask-item");
    item.querySelector(".ask-input").value = b.getAttribute("data-answer") || "";
  }));
  box.querySelectorAll(".ask-send").forEach((b) => b.addEventListener("click", async () => {
    const item = b.closest(".ask-item");
    const msg = item.querySelector(".ask-msg");
    const answer = item.querySelector(".ask-input").value.trim();
    if (!answer) { msg.textContent = "پاسخی وارد نشد."; return; }
    msg.textContent = "در حال ارسال…";
    try {
      await api("/api/interactions/answer", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: item.getAttribute("data-ask"),
          token: item.getAttribute("data-token"), answer }),
      });
      msg.textContent = "پاسخ فقط به همان وظیفه رسید — ادامه از همان نقطه.";
    } catch (e) { msg.textContent = "خطا: " + e.message; }
  }));
  box.querySelectorAll(".ask-cancel").forEach((b) => b.addEventListener("click", async () => {
    const item = b.closest(".ask-item");
    const msg = item.querySelector(".ask-msg");
    try {
      await api("/api/interactions/cancel", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: item.getAttribute("data-ask"),
          token: item.getAttribute("data-token") }),
      });
      msg.textContent = "درخواست لغو شد؛ فقط همان وظیفه متوقف شد.";
    } catch (e) { msg.textContent = "خطا: " + e.message; }
  }));
}

function openInteractions() {
  if (INTERACTION_ES) return;
  try {
    INTERACTION_ES = new EventSource("/api/interactions/events");
    INTERACTION_ES.onmessage = (e) => {
      try { renderInteractions(JSON.parse(e.data)); } catch (_) { /* ignore */ }
    };
    INTERACTION_ES.onerror = () => { /* EventSource reconnects itself */ };
  } catch (_) { /* polling fallback below */ }
  setInterval(() => {
    if (INTERACTION_ES && INTERACTION_ES.readyState === EventSource.CLOSED) {
      api("/api/interactions").then(renderInteractions).catch(() => {});
    }
  }, 5000);
}

/* ═══════════════════ boot ═══════════════════ */
(async () => {
  try {
    /* AF-52 — موبایل: تنظیمات پیشفرض بسته تا گفتگو و تأییدها در دسترس باشند */
    const cfg = document.getElementById("cfgDetails");
    if (cfg) cfg.open = window.innerWidth > 720;
    await Promise.all([initVersionBadges(), checkOc(), loadProjects()]);
    await loadModels();
    await loadReasoning();
    openInteractions();
    await loadSessions();
    if (sessionId) await openSession();
    else setStatus("idle");
    runTicker();
  } catch (e) {
    failBar("راه‌اندازی: " + e.message);
    $("#ocBadge").textContent = "⬤ خطا";
    $("#ocBadge").className = "wspill st-bad";
  }
})();
/* periodic safety re-check of opencode connection */
setInterval(() => { if (!document.hidden) checkOc().catch(() => {}); }, 30_000);
setInterval(() => { if (!document.hidden && sessionId) loadPendingPermissions(); }, 5000);
