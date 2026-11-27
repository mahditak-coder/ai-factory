/* AI Factory v0.3.0 — کارخانه تولید نرم‌افزار (vanilla JS, no deps) */
"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(url, opts) {
  const r = await fetch(url, opts);
  let d = null;
  try { d = await r.json(); } catch { /* ignore */ }
  if (!r.ok || (d && d.ok === false)) throw new Error((d && d.error) || ("HTTP " + r.status));
  return d;
}
const msg = (sel, text, cls) => { const el = $(sel); el.textContent = text; el.className = "msg " + (cls || ""); };

function badge(sel, text, cls) { const el = $(sel); el.textContent = text; el.className = "badge " + cls; }

/* R4-a: انواع پروژه از منبع حقیقت واحد (core.config) از طریق API می‌آیند —
 * هیچ کپی دستی این‌جا نگه داشته نمی‌شود. */
let TYPES = [];
async function loadTypes() {
  try {
    const d = await api("/api/factory/types");
    TYPES = d.types || [];
    if (!TYPES.length) throw new Error("فهرست خالی از سرور");
    $("#npType").innerHTML = TYPES.map((t) =>
      `<option value="${esc(t.id)}">${esc(t.label)}</option>`).join("");
    return true;
  } catch (e) {
    // بدون لیست پنهانی قدیمی — فرم ساخت غیرفعال می‌شود
    TYPES = [];
    $("#npType").innerHTML = "";
    ["#npName", "#npDesc", "#npType"].forEach((s) => { $(s).disabled = true; });
    const btn = document.querySelector("#fNew button[type=submit]");
    if (btn) btn.disabled = true;
    msg("#npMsg", "دریافت انواع پروژه از سرور ناموفق بود (" + e.message +
        ") — فرم ساخت غیرفعال شد؛ صفحه را دوباره بارگذاری کنید.", "err");
    return false;
  }
}

let meta = JSON.parse(localStorage.getItem("af3.meta") || "{}");
function saveMeta() { localStorage.setItem("af3.meta", JSON.stringify(meta)); }
function currentProject() { return meta.project || ""; }
function setProject(p) { meta.project = p; saveMeta(); refreshProjectDependent(); }

/* ---------------- tabs ---------------- */
$("#tabs").addEventListener("click", (ev) => {
  const b = ev.target.closest("button.tab");
  if (!b) return;
  document.querySelectorAll("button.tab").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll("section.tabcard").forEach((c) => {
    c.hidden = c.dataset.t !== b.dataset.t;
  });
});
function gotoTab(t) {
  document.querySelector(`#tabs button[data-t="${t}"]`).click();
}
$("#btnGoPlan") && $("#btnGoPlan").addEventListener("click", () => gotoTab("pipeline"));
$("#btnGoFiles") && $("#btnGoFiles").addEventListener("click", () => gotoTab("files"));
$("#btnGoRun") && $("#btnGoRun").addEventListener("click", () => gotoTab("runs"));
$("#btnGoCheck") && $("#btnGoCheck").addEventListener("click", () => gotoTab("tests"));
$("#btnGoExport") && $("#btnGoExport").addEventListener("click", () => gotoTab("export"));

/* ---------------- projects ---------------- */
async function loadProjects() {
  const el = $("#projList");
  try {
    const { projects } = await api("/api/factory/projects");
    if (!projects.length) { el.textContent = "هیچ پروژه‌ای نیست — از فرم بالا بسازید"; return; }
    el.innerHTML = `<ul class="list">` + projects.map((p) =>
      `<li><button class="filebtn" data-name="${esc(p.name)}">📁 <b>${esc(p.name)}</b></button>` +
      `<span class="muted">${esc(p.type_label)} · ${p.files} فایل · ${esc(p.modified)}</span></li>`
    ).join("") + `</ul>`;
    el.querySelectorAll("button[data-name]").forEach((b) =>
      b.addEventListener("click", () => openProjectDetail(b.dataset.name)));
  } catch (e) { el.textContent = "خطا: " + e.message; }
}

$("#fNew").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const name = $("#npName").value.trim();
  try {
    const { project } = await api("/api/factory/projects", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, type: $("#npType").value, description: $("#npDesc").value.trim() }),
    });
    msg("#npMsg", "ساخته شد: " + project.name, "ok");
    $("#npName").value = ""; $("#npDesc").value = "";
    loadProjects(); refreshProjectSelects();
    openProjectDetail(project.name);
  } catch (e) { msg("#npMsg", "خطا: " + e.message, "err"); }
});

async function openProjectDetail(name) {
  const { projects } = await api("/api/factory/projects");
  const p = projects.find((x) => x.name === name);
  if (!p) return;
  setProject(name);
  $("#projDetail").hidden = false;
  $("#pdName").textContent = p.name;
  badge("#pdType", p.type_label, "b-ok");
  $("#pdInfo").textContent = `ایجاد: ${p.created} · آخرین تغییر: ${p.modified} · ${p.files} فایل` +
    (p.description ? ` · ${p.description}` : "");
  refreshProjectSelects();
}

$("#btnRename").addEventListener("click", async () => {
  const old = currentProject();
  if (!old) return;
  const nw = prompt("نام جدید پروژه:", old);
  if (!nw || nw === old) return;
  try {
    await api("/api/factory/rename", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old, new: nw.trim() }),
    });
    setProject(nw.trim());
    msg("#npMsg", "نام تغییر کرد", "ok");
    loadProjects(); openProjectDetail(nw.trim());
  } catch (e) { msg("#npMsg", "خطا: " + e.message, "err"); }
});

$("#btnDelete").onclick = async () => {
  const p = currentProject();
  if (!p) return;
  if (!confirm(`پروژه «${p}» برای همیشه حذف شود؟ (نسخه پشتیبان قبل از حذف ساخته می‌شود)`) ) return;
  if (!window.confirm("تأیید نهایی: حذف کامل؟")) return;
  try {
    const r = await api("/api/factory/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: p, confirm: true }),
    });
    msg("#npMsg", "حذف شد (نسخه پشتیبان: " + r.backup + ")", "ok");
    setProject(""); loadProjects();
    $("#projDetail").hidden = true;
  } catch (e) { msg("#npMsg", "خطا: " + e.message, "err"); }
};

/* ---------------- project selects (all tabs share) ---------------- */
async function refreshProjectSelects() {
  const { projects } = await api("/api/factory/projects");
  const names = projects.map((p) => p.name);
  for (const sel of ["#plProj", "#plpProj", "#flProj", "#rnProj", "#ckProj", "#exProj", "#anProj"]) {
    const el = $(sel);
    if (!el) continue;
    const cur = el.value || currentProject();
    el.innerHTML = `<option value="">— انتخاب پروژه —</option>` +
      names.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
    if (cur && names.includes(cur)) el.value = cur;
    else el.value = currentProject() || "";
  }
}
function refreshProjectDependent() {
  refreshProjectSelects();
  const p = currentProject();
  if (p) loadFiles();
}

/* ---------------- pipeline (plan & generate) ---------------- */
/* AF-06.2: انتخاب عامل در UI → درخواست‌های plan و fix. منبع فهرست:
 * /api/agents (ساختار واقعی Factory)؛ گزینهٔ خالی «بدون عامل» = رفتار قبلی.
 * هیچ عامل پیش‌فرضی اختراع نمی‌شود. */
async function loadAgentSelects() {
  const sels = ["#plAgent", "#plpAgent", "#ckAgent"];
  let agents = [];
  try { agents = (await api("/api/agents")).agents.filter((a) => a.enabled); }
  catch { return; /* selects stay with their single default option */ }
  for (const s of sels) {
    const el = $(s);
    if (!el) continue;
    const cur = el.value;
    el.innerHTML = `<option value="">بدون عامل (حافظهٔ فقط پروژه)</option>`
      + agents.map((a) => `<option value="${esc(a.name)}">${esc(a.name)}</option>`).join("");
    if (cur && agents.some((a) => a.name === cur)) el.value = cur;
  }
}
loadAgentSelects();

/* ---------------- AF-18: integrated request→delivery pipeline -------------- */
const PLP_BADGE = {
  queued: ["در صف", "b-wait"], running: ["در اجرا", "b-warn"],
  awaiting_approval: ["در انتظار تأیید شما", "b-warn"],
  approved: ["تأییدشده — در اجرا", "b-warn"],
  delivered: ["تحویل شد ✅", "b-ok"], failed: ["شکست", "b-bad"],
  rejected: ["رد شد", "b-bad"],
};
async function loadPipelines() {
  try {
    const { pipelines } = await api("/api/pipeline");
    const el = $("#plpList");
    if (!pipelines.length) {
      el.innerHTML = `<span class="muted">خط لوله‌ای ثبت نشده است.</span>`;
      return;
    }
    el.innerHTML = pipelines.map((p) => {
      const [label, cls] = PLP_BADGE[p.state] || [p.state, "b-wait"];
      const stage = STAGE_LABEL[p.stage] || p.stage;
      const r = p.result || {};
      const extra = p.state === "delivered"
        ? ` — بسته: <code>${esc((r.zip || "").split("/").pop())}</code> · تحویل: <code>${esc((r.delivery_dir || "").split("/").slice(-2).join("/"))}</code>`
        : (p.state === "failed" ? ` — ${esc((p.error || "").slice(0, 140))}` : "");
      const gate = p.state === "awaiting_approval"
        ? ` <button class="btn small" data-plpa="${esc(p.id)}">✅ تأیید برنامه</button>
            <button class="btn small danger" data-plpr="${esc(p.id)}">✖ رد</button>` : "";
      return `<div class="row" style="gap:.5rem;align-items:center;border-top:1px solid var(--line,#333);padding:.4rem 0">
        <span class="badge ${cls}">${label}</span>
        <b>${esc(p.project)}</b> <span class="muted">مرحله: ${esc(stage)}</span>${extra}
        ${gate}
      </div>`;
    }).join("");
    el.querySelectorAll("[data-plpa]").forEach((b) =>
      b.addEventListener("click", () => plpApprove(b.dataset.plpa, true)));
    el.querySelectorAll("[data-plpr]").forEach((b) =>
      b.addEventListener("click", () => plpApprove(b.dataset.plpr, false)));
  } catch (e) {
    $("#plpList").innerHTML = `<span class="muted">خطا: ${esc(e.message)}</span>`;
  }
}
const STAGE_LABEL = {
  plan: "برنامه", generate: "تولید", test: "آزمون", fix: "اصلاح",
  quality: "کیفیت", release: "انتشار", deliver: "تحویل مستقل",
};
async function plpApprove(pid, ok) {
  try {
    await api("/api/pipeline/approve", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: pid, ok }),
    });
    msg("#plpMsg", ok ? "برنامه تأیید شد — خط لوله ادامه می‌یابد" : "برنامه رد شد — خط لوله بسته شد", ok ? "ok" : "warn");
  } catch (e) {
    msg("#plpMsg", "گیت ناموفق: " + e.message, "err");
  }
  loadPipelines();
}
$("#fPipeline").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const project = $("#plpProj").value;
  const goal = $("#plpGoal").value.trim();
  if (!project || !goal) return msg("#plpMsg", "پروژه و درخواست را کامل کنید", "err");
  msg("#plpMsg", "ثبت خط لوله…", "warn");
  try {
    const r = await api("/api/pipeline/create", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, goal,
                             agent: $("#plpAgent").value }),
    });
    msg("#plpMsg", `خط لوله ${r.pipeline.id.slice(0, 16)}… ثبت شد — برنامه‌ریزی در صف اجرا`, "ok");
    $("#plpGoal").value = "";
    loadPipelines();
  } catch (e) {
    msg("#plpMsg", "ثبت ناموفق: " + e.message, "err");
  }
});
loadPipelines();

$("#btnPlan").addEventListener("click", async () => {
  const project = $("#plProj").value;
  if (!project) return msg("#plMsg", "پروژه را انتخاب کنید", "err");
  badge("#plState", "در حال اجرا…", "b-warn");
  try {
    const r = await api("/api/factory/plan", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, goal: $("#plGoal").value.trim(),
                             agent: $("#plAgent").value }),
    });
    msg("#plMsg", "برنامه‌ریزی در جریان است (نشست " + r.session_id.slice(0, 12) + "…)", "ok");
    pollPlan();
  } catch (e) { badge("#plState", "ناموفق", "b-bad"); msg("#plMsg", "خطا: " + e.message, "err"); }
});

$("#btnDirect").addEventListener("click", async () => {
  const project = $("#plProj").value;
  if (!project) return msg("#plMsg", "پروژه را انتخاب کنید", "err");
  const goal = $("#plGoal").value.trim();
  if (!goal) return msg("#plMsg", "هدف را بنویسید", "err");
  badge("#genState", "در حال اجرا…", "b-warn");
  try {
    await api("/api/factory/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, goal }),
    });
    msg("#plMsg", "تولید مستقیم شروع شد", "ok");
    gotoTab("pipeline");
    pollGen();
  } catch (e) { badge("#genState", "ناموفق", "b-bad"); msg("#plMsg", "خطا: " + e.message, "err"); }
});

$("#btnApprove").addEventListener("click", async () => {
  const project = $("#plProj").value;
  if (!project) return msg("#plMsg", "پروژه را انتخاب کنید", "err");
  if (!window.confirm("برنامه را تأیید و تولید را شروع کنم؟")) return;
  try {
    await api("/api/factory/approve", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project }),
    });
    badge("#plState", "تأیید شد", "b-ok");
    badge("#genState", "در حال اجرا…", "b-warn");
    await api("/api/factory/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project, use_plan: true }),
    });
    msg("#plMsg", "تولید بر اساس برنامه شروع شد", "ok");
    pollGen();
  } catch (e) { msg("#plMsg", "خطا: " + e.message, "err"); }
});

$("#btnReloadPlan").addEventListener("click", () => pollPlan(true));

async function pollPlan(final = false) {
  const project = $("#plProj").value;
  if (!project) return;
  try {
    const { has_plan, plan } = await api("/api/factory/plan?project=" + encodeURIComponent(project));
    if (has_plan) {
      $("#planBox").textContent = plan;
      badge("#plState", "برنامه آماده", "b-ok");
    } else {
      $("#planBox").textContent = "در حال تهیه برنامه… (" + (final ? "دستی" : "") + ")";
      badge("#plState", "در حال اجرا…", "b-warn");
      if (!final) setTimeout(pollPlan, 6000);
    }
  } catch (e) { badge("#plState", "ناموفق", "b-bad"); }
}

$("#btnGenPoll").addEventListener("click", () => pollGen(true));

async function pollGen(manual = false) {
  const project = $("#plProj").value;
  if (!project) return;
  try {
    const r = await api("/api/factory/generation?project=" + encodeURIComponent(project));
    if (r.done && r.text) {
      badge("#genState", "موفق", "b-ok");
      $("#genBox").textContent = r.text;
    } else if (!r.started) {
      badge("#genState", "در انتظار", "b-wait");
    } else {
      badge("#genState", "در حال اجرا…", "b-warn");
      $("#genBox").textContent = r.text || "مدل در حال نوشتن فایل‌هاست…";
      setTimeout(pollGen, 7000);
    }
  } catch (e) { badge("#genState", "ناموفق", "b-bad"); }
}

/* ---------------- files ---------------- */
async function loadFiles() {
  const p = $("#flProj").value || currentProject();
  const el = $("#files");
  if (!p) { el.textContent = "پروژه انتخاب نشده"; return; }
  try {
    const { files } = await api("/api/files?project=" + encodeURIComponent(p));
    if (!files.length) { el.textContent = "پروژه خالی است"; return; }
    el.innerHTML = `<ul class="list">` + files.map((f) =>
      `<li><button class="filebtn" data-path="${esc(f.path)}" data-edit="${f.editable}">📄 ${esc(f.path)}</button>` +
      `<span class="muted">${f.size_bytes} بایت${f.editable ? "" : " · خواندنی"}</span></li>`).join("") + `</ul>`;
    el.querySelectorAll("button.filebtn").forEach((b) =>
      b.addEventListener("click", () => viewFile(b.dataset.path, b.dataset.edit === "true")));
  } catch (e) { el.textContent = "خطا: " + e.message; }
}
$("#flProj") && $("#flProj").addEventListener("change", loadFiles);

async function viewFile(path, editable) {
  try {
    const p = $("#flProj").value || currentProject();
    const { content } = await api("/api/file?project=" + encodeURIComponent(p) +
      "&path=" + encodeURIComponent(path));
    $("#editorWrap").hidden = false;
    $("#openFile").textContent = path;
    $("#editor").value = content || "";
    $("#editor").dataset.path = path;
    $("#editor").dataset.editable = String(editable);
    msg("#editorMsg", "", "ok");
  } catch (e) { msg("#editorMsg", "خطا: " + e.message, "err"); }
}

$("#btnSave").addEventListener("click", async () => {
  try {
    const p = $("#flProj").value || currentProject();
    await api("/api/file", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: p, path: $("#editor").dataset.path, content: $("#editor").value }),
    });
    msg("#editorMsg", "ذخیره شد", "ok");
    loadFiles();
  } catch (e) { msg("#editorMsg", "خطا: " + e.message, "err"); }
});

$("#btnDelFile").addEventListener("click", async () => {
  const p = $("#flProj").value || currentProject();
  const path = $("#editor").dataset.path;
  if (!path || !window.confirm("فایل «" + path + "» حذف شود؟")) return;
  try {
    await api("/api/file?project=" + encodeURIComponent(p) + "&path=" + encodeURIComponent(path),
      { method: "DELETE" });
    msg("#editorMsg", "حذف شد", "ok");
    $("#editorWrap").hidden = true;
    loadFiles();
  } catch (e) { msg("#editorMsg", "خطا: " + e.message, "err"); }
});

/* ---------------- runs ---------------- */
$("#btnRun").addEventListener("click", async () => {
  const p = $("#rnProj").value;
  if (!p) return;
  try {
    badge("#runPanel", "در حال اجرا…", "");
    const r = await api("/api/factory/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: p }),
    });
    showRun(p, r.running ? { running: true, preview: r.preview, kind_label: r.kind_label, pid: r.pid } : r);
    loadRunLog(p);
  } catch (e) { $("#runPanel").textContent = "خطا: " + e.message; }
  loadRuns();
});

$("#btnStop").addEventListener("click", async () => {
  const p = $("#rnProj").value;
  if (!p) return;
  try {
    await api("/api/factory/stop", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: p }),
    });
    showRun(p, { running: false });
  } catch (e) { $("#runPanel").textContent = "خطا: " + e.message; }
  loadRuns();
});

$("#btnRuns").addEventListener("click", loadRuns);

async function loadRuns() {
  const { runs } = await api("/api/factory/runs");
  $("#runPanel").innerHTML = runs.map((r) =>
    `<div class="kv"><span>${esc(r.name)}</span>` +
    `<span class="badge ${r.running ? "b-ok" : "b-bad"}">${r.running ? "در حال اجرا" : "متوقف"}</span>` +
    (r.running ? `<span dir="ltr" class="muted">${esc(r.preview)} · ${esc(r.kind_label || r.kind)}</span>` : "") +
    `</div>`).join("");
  const r = runs.find((x) => x.name === ($("#rnProj").value || currentProject()));
  if (r) {
    $("#rnProj").value = r.name;
    showRun(r.name, r);
  }
}

function showRun(p, r) {
  const link = $("#previewLink");
  if (r.running && r.preview) {
    link.hidden = false;
    link.href = r.preview;
    link.textContent = `🔗 پیش‌نمایش ${p}: ${r.preview}`;
  } else {
    link.hidden = true;
  }
}

async function loadRunLog(p) {
  try {
    const { logs } = await api("/api/factory/runlog?project=" + encodeURIComponent(p) + "&lines=120");
    $("#runLog").textContent = logs;
  } catch (e) { $("#runLog").textContent = "خطا: " + e.message; }
}
setInterval(() => {
  if (document.querySelector('section.tabcard[data-t="runs"]')?.hidden) return;
  const v = $("#rnProj")?.value;
  if (v) loadRunLog(v);
}, 10000);

/* ---------------- tests (checker + fix + testrun) ---------------- */
$("#btnTestRun").addEventListener("click", async () => {
  const p = $("#ckProj").value;
  if (!p) return msg("#ckMsg", "پروژه را انتخاب کنید", "err");
  msg("#ckMsg", "در حال اجرای آزمون‌ها…", "");
  try {
    const r = await api("/api/factory/testrun", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: p }),
    });
    $("#ckResult").textContent =
      (r.ran ? (r.ok ? "✔ همه آزمون‌ها موفق" : "✘ آزمون‌ها ناموفق")
             : (r.note || "آزمونی یافت نشد")) + "\n" + (r.output || "");
    msg("#ckMsg", r.ran ? (r.ok ? "آزمون‌ها سبز" : "آزمون‌ها قرمز")
                        : (r.note || ""), r.ran ? (r.ok ? "ok" : "err") : "");
  } catch (e) { msg("#ckMsg", "خطا: " + e.message, "err"); }
});

$("#btnDeps").addEventListener("click", async () => {
  const p = $("#ckProj").value;
  if (!p) return msg("#ckMsg", "پروژه را انتخاب کنید", "err");
  msg("#ckMsg", "در حال نصب وابستگی‌ها (در صورت وجود)…", "");
  try {
    const r = await api("/api/factory/deps", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: p }),
    });
    $("#ckResult").textContent = (r.note || r.output || "نصب انجام شد");
    msg("#ckMsg", r.installed ? "نصب شد" : (r.note || "نصبی لازم نبود"),
        r.installed ? "ok" : "");
  } catch (e) { msg("#ckMsg", "خطا: " + e.message, "err"); }
});
$("#btnCheck").addEventListener("click", async () => {
  const p = $("#ckProj").value;
  if (!p) return msg("#ckMsg", "پروژه را انتخاب کنید", "err");
  msg("#ckMsg", "در حال بررسی…", "");
  try {
    const { result } = await api("/api/factory/check?project=" + encodeURIComponent(p));
    renderCheck(result);
    msg("#ckMsg", result.ok ? "خطایی یافت نشد" : `${result.errors.length} خطا`, result.ok ? "ok" : "err");
  } catch (e) { msg("#ckMsg", "خطا: " + e.message, "err"); }
});

function renderCheck(r) {
  $("#ckResult").textContent =
    (r.ok ? "✔ بی‌خطا" : "✘ خطاها:") + "\n" +
    r.errors.map((e) => `- [${e.kind}] ${e.file}${e.line ? ":" + e.line : ""} ${e.message}`).join("\n") +
    `\n\nبررسی‌شده: js=${r.checked.js} py=${r.checked.py} json=${r.checked.json} html=${r.checked.html}` +
    (r.not_checked_kinds.length ? `\nانواع بررسی‌نشده: ${r.not_checked_kinds.join(", ")}` : "");
}

$("#btnFix").addEventListener("click", async () => {
  const p = $("#ckProj").value;
  if (!p) return msg("#ckMsg", "پروژه را انتخاب کنید", "err");
  if (!window.confirm("چرخه اصلاح خودکار (حداکثر ۲ دور، با نسخه پشتیبان) شروع شود؟")) return;
  msg("#ckMsg", "در حال چرخه اصلاح — تا چند دقیقه…", "");
  try {
    const r = await api("/api/factory/fix", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: p, attempts: 2,
                             agent: $("#ckAgent").value }),
    });
    $("#ckResult").textContent =
      (r.fixed ? "✔ اصلاح شد" : "✘ اصلاح نشد") + "\n" +
      `نسخه پشتیبان: ${esc(r.backup)}\n` +
      JSON.stringify(r.cycles, null, 1) + "\n\n" +
      (r.result ? r.result.errors.map((e) => `- [${e.kind}] ${e.file} ${e.message}`).join("\n") : "");
    msg("#ckMsg", r.fixed ? "اصلاح موفق" : "اصلاح ناموفق — جزئیات پایین", r.fixed ? "ok" : "err");
    loadFiles();
  } catch (e) { msg("#ckMsg", "خطا: " + e.message, "err"); }
});

/* ---------------- export ---------------- */
$("#btnZip").addEventListener("click", () => doExport(false));
$("#btnZipDl").addEventListener("click", () => doExport(true));

async function doExport(toDownloads) {
  const p = $("#exProj").value;
  if (!p) return msg("#exMsg", "پروژه را انتخاب کنید", "err");
  msg("#exMsg", "در حال ساخت ZIP…", "");
  try {
    const r = await api("/api/factory/export", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: p, to_downloads: toDownloads }),
    });
    const link = `/api/factory/export?project=${encodeURIComponent(p)}&path=${encodeURIComponent(r.path)}&download=1`;
    msg("#exMsg", `✔ ${r.files} فایل · ${Math.round(r.size_bytes / 1024)} کیلوبایت · ` +
      (r.integrity_bad_file === null ? "سلامت ZIP تأیید شد" : "ZIP معیوب!") +
      (r.downloads ? ` · ${r.downloads.applied ? "به Downloads کپی شد: " + r.downloads.path : r.downloads.note}` : ""),
      r.integrity_bad_file === null ? "ok" : "err");
    await loadExportList(link);
  } catch (e) { msg("#exMsg", "خطا: " + e.message, "err"); }
}
/* ---------------- deploy (R4) ---------------- */
const dpStep = (s) => `<li>${s.ok ? "✅" : "❌"} <b>${esc(s.step)}</b> — ${esc(s.detail || "")}</li>`;
const dpRenderSteps = (steps) => {
  $("#dpSteps").innerHTML = `<ul class="list">` + (steps || []).map(dpStep).join("") + `</ul>`;
};

async function loadDpSelects() {
  try {
    const { projects } = await api("/api/factory/projects");
    $("#dpProj").innerHTML = (projects || []).map((p) =>
      `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join("");
  } catch (e) { /* projects tab shows the error */ }
  try {
    const { dests } = await api("/api/deploy/dests");
    $("#dpDest").innerHTML = dests.map((d) =>
      `<option value="${esc(d.name)}">${esc(d.name)} (${esc(d.kind)})</option>`).join("");
    $("#dpDestList").innerHTML = `<ul class="list">` + dests.map((d) =>
      `<li>🎯 <b>${esc(d.name)}</b> (${esc(d.kind)})` +
      (d.kind === "local" ? ` — ${esc(d.root)}` :
        ` — ${esc(d.user)}@${esc(d.host)}:${esc(d.port)} → ${esc(d.path)}` +
        ` · کلید: ${d.has_key ? "ثبت‌شده" : "پیش‌فرض"}`) + `</li>`).join("") + `</ul>`;
  } catch (e) { $("#dpDestList").textContent = "دریافت مقاصد ناموفق: " + e.message; }
}

async function loadDpReleases() {
  const p = $("#dpProj").value;
  $("#dpRelease").innerHTML = "";
  if (!p) return;
  try {
    const { exports } = await api("/api/factory/export");
    const rels = (exports || []).filter((x) => x.name.startsWith(p + "-release-"));
    $("#dpRelease").innerHTML = rels.map((x) =>
      `<option value="${esc(x.path)}">${esc(x.name)} (${Math.round(x.size_bytes / 1024)}KB)</option>`).join("")
      || `<option value="">— بستهٔ انتشاری ندارد —</option>`;
  } catch (e) { /* handled by caller message */ }
}

function dpPicked() {
  return { project: $("#dpProj").value, dest: $("#dpDest").value,
           release: $("#dpRelease").value || undefined };
}

$("#btnDpRefresh") && $("#btnDpRefresh").addEventListener("click", async () => {
  await loadDpSelects(); await loadDpReleases();
});
$("#dpProj") && $("#dpProj").addEventListener("change", () => loadDpReleases());

$("#btnDpPre") && $("#btnDpPre").addEventListener("click", async () => {
  const p = dpPicked();
  if (!p.project || !p.dest) return msg("#dpMsg", "پروژه و مقصد را انتخاب کنید", "err");
  msg("#dpMsg", "بررسی آمادگی…", "");
  try {
    const r = await api("/api/deploy/preflight", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(p) });
    dpRenderSteps(r.steps);
    msg("#dpMsg", r.ok ? "✔ آمادهٔ استقرار است" : "✗ آماده نیست — مراحل قرمز را ببینید",
        r.ok ? "ok" : "err");
  } catch (e) { msg("#dpMsg", "خطا: " + e.message, "err"); }
});

$("#btnDpStart") && $("#btnDpStart").addEventListener("click", async () => {
  const p = dpPicked();
  if (!p.project || !p.dest) return msg("#dpMsg", "پروژه و مقصد را انتخاب کنید", "err");
  if (!window.confirm("استقرار «" + p.project + "» روی مقصد «" + p.dest + "» انجام شود؟" +
        (p.dest.startsWith && $("#dpDest").selectedOptions[0].textContent.includes("remote")
          ? " (مقصد REMOTE)" : ""))) return;
  msg("#dpMsg", "در حال استقرار…", "");
  try {
    const r = await api("/api/deploy/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...p, confirm: true }) });
    const rec = r.record || {};
    dpRenderSteps(rec.steps);
    msg("#dpMsg", (r.ok ? "✔ مستقر شد — " : "✗ استقرار ناموفق — ") +
      (rec.url ? "آدرس محصول: " + rec.url : "") +
      (rec.error ? " · خطا: " + rec.error : ""), r.ok ? "ok" : "err");
    loadDpHist();
  } catch (e) { msg("#dpMsg", "خطا: " + e.message, "err"); }
});

$("#btnDpRollback") && $("#btnDpRollback").addEventListener("click", async () => {
  const p = dpPicked();
  if (!p.project || !p.dest) return msg("#dpMsg", "پروژه و مقصد را انتخاب کنید", "err");
  if (!window.confirm("بازگشت «" + p.project + "» روی «" + p.dest + "» به نسخهٔ انتشار قبلی؟")) return;
  msg("#dpMsg", "در حال Rollback…", "");
  try {
    const r = await api("/api/deploy/rollback", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: p.project, dest: p.dest, confirm: true }) });
    const rec = r.record || {};
    dpRenderSteps(rec.steps);
    msg("#dpMsg", (r.ok ? "✔ Rollback انجام شد — " : "✗ Rollback ناموفق — ") +
      (rec.url ? rec.url : "") + (rec.error ? " · خطا: " + rec.error : ""), r.ok ? "ok" : "err");
    loadDpHist();
  } catch (e) { msg("#dpMsg", "خطا: " + e.message, "err"); }
});

$("#btnDpStop") && $("#btnDpStop").addEventListener("click", async () => {
  const p = dpPicked();
  if (!p.project || !p.dest) return msg("#dpMsg", "پروژه و مقصد را انتخاب کنید", "err");
  if (!window.confirm("توقف سرویس مستقر «" + p.project + "» روی «" + p.dest + "»؟")) return;
  try {
    const r = await api("/api/deploy/stop", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: p.project, dest: p.dest }) });
    msg("#dpMsg", r.ok ? "✔ سرویس متوقف شد" : "✗ توقف ناموفق", r.ok ? "ok" : "err");
    loadDpHist();
  } catch (e) { msg("#dpMsg", "خطا: " + e.message, "err"); }
});

$("#fDest") && $("#fDest").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const cfg = { name: $("#dstName").value.trim(), kind: $("#dstKind").value };
  if (cfg.kind === "local") { if ($("#dstRoot").value.trim()) cfg.root = $("#dstRoot").value.trim(); }
  else {
    cfg.host = $("#dstHost").value.trim(); cfg.user = $("#dstUser").value.trim();
    cfg.path = $("#dstPath").value.trim();
    if ($("#dstPort").value.trim()) cfg.port = parseInt($("#dstPort").value.trim(), 10);
    if ($("#dstKey").value.trim()) cfg.key_path = $("#dstKey").value.trim();
  }
  try {
    await api("/api/deploy/dests", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfg) });
    msg("#dstMsg", "✔ مقصد ذخیره شد", "ok");
    $("#fDest").reset();
    await loadDpSelects();
  } catch (e) { msg("#dstMsg", "خطا: " + e.message, "err"); }
});

async function loadDpHist() {
  const p = $("#dpProj") ? $("#dpProj").value : "";
  try {
    const { history } = await api("/api/deploy/history" +
      (p ? "?project=" + encodeURIComponent(p) : ""));
    $("#dpHist").innerHTML = `<ul class="list">` + (history || []).map((r) =>
      `<li>${r.status === "success" ? "✅" : "❌"} <b>${esc(r.action)}</b> ` +
      `${esc(r.project)} → ${esc(r.dest)} · build ${esc(r.build || "—")} · ` +
      `${r.url ? `<a class="open" href="${esc(r.url)}" target="_blank" rel="noreferrer">${esc(r.url)}</a>` : esc(r.error || "")}</li>`
    ).join("") + `</ul>`;
  } catch (e) { $("#dpHist").textContent = "دریافت تاریخچه ناموفق: " + e.message; }
}
$("#btnDpHist") && $("#btnDpHist").addEventListener("click", () => loadDpHist());

$("#btnZipList").addEventListener("click", () => loadExportList());

/* ============== AF-03 — موتور اندروید + Release Center ============== */
const anStates = {
  built: "📦 ساخته شده", signed: "✍️ امضا شده", validated: "✅ اعتبارسنجی شده",
  store_ready: "🏪 آمادهٔ بررسی فروشگاهی", submitted: "📤 ارسال شده به فروشگاه",
  approved: "🏬 تأیید فروشگاه", published: "🚀 منتشر شده", rejected: "✗ رد شده",
  failed: "❌ ناموفق",
};
function anProj() { return $("#anProj") ? $("#anProj").value : ""; }

async function loadEngines() {
  try {
    const { engines } = await api("/api/engines");
    $("#setStatus").insertAdjacentHTML("beforeend",
      `<div class="kv"><span>موتور اندروید</span>` +
      `<span class="badge ${engines[0]?.available ? "b-ok" : "b-bad"}">` +
      `${engines[0]?.available ? "آماده" : "غیرآماده"} · ${esc(engines[0]?.modes?.join("/") || "—")}</span></div>`);
  } catch { /* settings tab may not be open — ignore */ }
}

async function anInspect() {
  const p = anProj();
  if (!p) return msg("#anMsg", "پروژه را انتخاب کنید", "err");
  try {
    const { info } = await api("/api/factory/android/inspect?project=" + encodeURIComponent(p));
    const v = info.validate || {};
    $("#anInfo").innerHTML = v.ok
      ? `✅ پروژه اندرویدی معتبر — چیدمان: <b>${esc(info.mode)}</b> · package: <code>${esc(info.package)}</code>` +
        ` · فایل‌های java: ${info.java_files} · منابع: ${info.res_files}` +
        ` (minSdk ${info.min_sdk ?? "—"} / targetSdk ${info.target_sdk ?? "—"})`
      : `❌ معتبر نیست: ${esc((v.problems || []).join("؛ "))}`;
  } catch (e) { msg("#anMsg", "بررسی ناموفق: " + e.message, "err"); }
}
$("#btnAnInspect").addEventListener("click", anInspect);

async function anBuild() {
  const p = anProj();
  if (!p) return msg("#anMsg", "پروژه را انتخاب کنید", "err");
  msg("#anMsg", "ساخت شروع شد… (سریالی — بدون تداخل)", "");
  try {
    await api("/api/factory/android/build", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project: p,
        kind: $("#anKind").value,
        sign: $("#anSign").checked,
        version_name: $("#anVerName").value || undefined,
        version_code: $("#anVerCode").value ? Number($("#anVerCode").value) : undefined,
      }),
    });
    anStatusPoll(true);
  } catch (e) { msg("#anMsg", "شروع ساخت ناموفق: " + e.message, "err"); }
}
$("#btnAnBuild").addEventListener("click", anBuild);
$("#btnAnStatus").addEventListener("click", () => anStatusPoll(false));

let anPollT = null;
async function anStatusPoll(recurring) {
  const p = anProj();
  if (!p) return;
  try {
    const st = await api("/api/factory/android/status?project=" + encodeURIComponent(p));
    $("#anSteps").textContent = (st.steps || [])
      .map((s) => `[${s.ts.slice(11, 19)}] ${s.status === "ok" ? "✓" : s.status === "warn" ? "⚠" : "✗"} ${s.step}${s.message && s.status !== "ok" ? " — " + s.message.slice(0, 300) : ""}`)
      .join("\n") || "—";
    if (st.running) {
      msg("#anMsg", "⏳ ساخت در جریان است…", "");
      clearTimeout(anPollT);
      if (recurring) anPollT = setTimeout(() => anStatusPoll(true), 3000);
    } else if (st.ok === true) {
      const r = st.report || {};
      const arts = Object.values(r.artifacts || {})
        .map((a) => `${esc(a.label)}: ${esc(a.path.split("/").pop())} (${Math.round(a.size / 1024)}KB)`)
        .join(" · ");
      msg("#anMsg", `✅ ساخت موفق (${r.duration_s}s) · ${arts} · مسیر: ${esc(r.output_dir)} — دانلود از «فهرست نسخه‌ها»`, "ok");
    } else if (st.ok === false) {
      msg("#anMsg", "❌ ساخت ناموفق: " + esc(st.error || "علت نامشخص"), "err");
    }
  } catch (e) { /* silent poll */ }
}

async function anKeyCheck() {
  const p = anProj();
  if (!p) return msg("#anKeyMsg", "پروژه را انتخاب کنید", "err");
  try {
    const r = await api("/api/factory/android/key?project=" + encodeURIComponent(p));
    msg("#anKeyMsg", r.exists
      ? "🔑 کلید اختصاصی این محصول موجود است (هویت امضا حفظ می‌شود)"
      : "کلید این محصول وجود ندارد — برای نسخهٔ امضاشده باید با تأیید شما ساخته شود",
      r.exists ? "ok" : "");
  } catch (e) { msg("#anKeyMsg", "بررسی کلید ناموفق: " + e.message, "err"); }
}
$("#btnAnKeyCheck").addEventListener("click", anKeyCheck);

$("#btnAnKeyCreate").addEventListener("click", async () => {
  const p = anProj();
  if (!p) return msg("#anKeyMsg", "پروژه را انتخاب کنید", "err");
  if (!confirm("کلید امضای اختصاصی «" + p + "» ساخته شود؟\nهویت امضای این محصول از این پس ثابت می‌ماند و کلید هرگز بازسازی نمی‌شود.")) return;
  try {
    const r = await api("/api/factory/android/key", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: p, confirm: true }),
    });
    msg("#anKeyMsg", r.created ? "✅ کلید محصول ساخته شد (0600، خارج از پروژه)" : "⚠ " + (r.note || "کلید از قبل موجود است"), "ok");
  } catch (e) { msg("#anKeyMsg", "ساخت کلید ناموفق: " + e.message, "err"); }
});

async function anReleases() {
  const p = anProj();
  if (!p) return;
  try {
    const { releases } = await api("/api/factory/android/releases?project=" + encodeURIComponent(p));
    $("#anReleases").innerHTML = releases.length
      ? `<ul class="list">` + releases.map((r) => {
          const apk = r.artifacts?.apk ? `<a class="open" href="/api/factory/android/download?project=${encodeURIComponent(p)}&release_id=${esc(r.id)}&artifact=apk">APK</a>` : "";
          const aab = r.artifacts?.aab ? `<a class="open" href="/api/factory/android/download?project=${encodeURIComponent(p)}&release_id=${esc(r.id)}&artifact=aab">AAB</a>` : "";
          return `<li>📦 <b>${esc(r.version_name)}</b> (vc${esc(r.version_code)}) · ${esc(r.package)} · ` +
            `${esc(anStates[r.state] || r.state)} · ${r.sign_verified ? "امضا ✓" : "بدون امضا"} · ` +
            `${apk} ${aab} ` +
            `<button class="btn small ghost" data-anadv="${esc(r.id)}" data-cur="${esc(r.state)}">→ وضعیت بعدی</button></li>`;
        }).join("") + `</ul>`
      : "هیچ نسخه‌ای ثبت نشده — اولین ساخت را اجرا کنید.";
    $("#anReleases").querySelectorAll("[data-anadv]").forEach((b) =>
      b.addEventListener("click", () => anAdvance(p, b.dataset.anadv, b.dataset.cur)));
  } catch (e) { $("#anReleases").textContent = "خطا: " + e.message; }
}
$("#btnAnReleases").addEventListener("click", anReleases);

async function anAdvance(p, releaseId, cur) {
  const chain = { built: "signed", signed: "validated", validated: "store_ready",
                  store_ready: "submitted", submitted: "approved", approved: "published" };
  const next = chain[cur];
  if (!next) return alert(`از وضعیت «${anStates[cur]}» گذاری دیگر مجاز نیست`);
  const needsConfirm = ["submitted", "approved", "published"].includes(next);
  if (needsConfirm && !confirm(`تأیید می‌کنید که «${anStates[next]}» واقعاً رخ داده است؟\n(این تأیید توسط شماست؛ فروشگاه جداگانه بررسی می‌کند)`)) return;
  try {
    await api("/api/factory/android/release/state", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: p, release_id: releaseId, state: next, confirm: true }),
    });
    anReleases();
  } catch (e) { alert("گذار وضعیت ناموفق: " + e.message); }
}

/* R3 — بستهٔ انتشار مستقل: تشخیص فناوری + README/run scripts + release.json + verify */
$("#btnRelease") && $("#btnRelease").addEventListener("click", () => doRelease());

async function doRelease() {
  const p = $("#exProj").value;
  if (!p) return msg("#exMsg", "پروژه را انتخاب کنید", "err");
  msg("#exMsg", "در حال ساخت بستهٔ انتشار…", "");
  try {
    const r = await api("/api/factory/release", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: p }),
    });
    const v = r.verify || {};
    const link = `/api/factory/export?project=${encodeURIComponent(p)}&path=${encodeURIComponent(r.path)}&download=1`;
    msg("#exMsg", `✔ بستهٔ انتشار ${r.version} (build ${r.build}) · ${r.files} فایل · ` +
      `فناوری: ${(r.technology || {}).kind} · وابستگی: ${((r.technology || {}).deps || []).length} · ` +
      (v.ok ? "ساختار بسته تأیید شد" : "مشکل ساختار: " + (v.problems || []).join("؛ ")),
      v.ok ? "ok" : "err");
    await loadExportList(link);
  } catch (e) { msg("#exMsg", "خطا: " + e.message, "err"); }
}

async function loadExportList(newLink) {
  const { exports } = await api("/api/factory/export");
  $("#exportList").innerHTML = `<ul class="list">` + exports.slice(-10).map((x) => {
    let proj = "";
    const m = x.name.match(/^(.*)-\d{8}-\d{6}\.zip$/);
    if (m) proj = m[1];
    const link = `/api/factory/export?project=${encodeURIComponent(proj)}&path=${encodeURIComponent(x.path)}&download=1`;
    return `<li><a href="${esc(link)}" class="open">📦 ${esc(x.name)}</a>` +
      `<span class="muted">${Math.round(x.size_bytes / 1024)}KB · ${esc(x.integrity)}</span></li>`;
  }).join("") + `</ul>`;
}

/* ---------------- settings ---------------- */
async function loadSettings() {
  try {
    const { opencode } = await api("/api/oc/status");
    $("#setStatus").innerHTML =
      `<div class="kv"><span>OpenCode serve</span>` +
      `<span class="badge ${opencode.running ? "b-ok" : "b-bad"}">${opencode.running ? "فعال " + esc(opencode.version) : "غیرفعال"}</span></div>`;
  } catch (e) { $("#setStatus").textContent = "خطا: " + e.message; }
  try {
    const { models } = await api("/api/oc/models");
    $("#setModel").innerHTML = `<ul class="list">` + models.map((m) =>
      `<li><span>${esc(m.name)}</span><span dir="ltr" class="muted">${esc(m.modelID)} · ctx ${m.context}</span></li>`).join("") + `</ul>`;
  } catch (e) { $("#setModel").textContent = "خطا: " + e.message; }
}

/* ---------------- refresh & init ---------------- */
$("#btnRefresh").addEventListener("click", () => {
  loadProjects(); refreshProjectSelects(); loadSettings(); loadRuns();
});

(async () => {
  try {
    if (await loadTypes() &&
        $("#npType").querySelector('option[value="webpage"]')) {
      $("#npType").value = "webpage";
    }
    await loadProjects();
    await refreshProjectSelects();
    await loadSettings();
  } catch (e) {
    $("#projList").textContent = "خطا در راه‌اندازی: " + e.message;
  }
  const p = currentProject();
  if (p) { try { await openProjectDetail(p); } catch { /* ignore */ } }
})();

/* ═══════════ AF-23: نوار وضعیت ضروری صفحهٔ اول ═══════════
 * در دید اول فقط: سلامت سیستم، موتور مدل + مدل فعال، وظایف در حال اجرا،
 * و دسترسی واضح به گفتگو. جزئیات فنی در تب‌های خودشان می‌ماند (حذف نشده‌اند). */
(async () => {
  const st = $("#afStrip");
  if (!st) return;
  const pill = (txt, cls) => `<span class="strip-pill ${cls || ""}">${esc(txt)}</span>`;
  let html = "";
  try {
    const h = await api("/api/health");
    html += pill("✅ سیستم سالم · v" + (h.version || "?"), "sp-ok");
  } catch {
    html += pill("❌ سرور اصلی پاسخ نمی‌دهد", "sp-bad");
  }
  try {
    const { opencode } = await api("/api/oc/status");
    const model = localStorage.getItem("af.model") || "";
    html += pill(
      (opencode.running ? "🤖 موتور مدل فعال" : "⚠ موتور مدل راه‌اندازی می‌شود…") +
      (model ? " · " + model : ""),
      opencode.running ? "sp-ok" : "sp-warn");
  } catch {
    html += pill("⚠ موتور اجرای مدل در دسترس نیست", "sp-bad");
  }
  try {
    const { tasks } = await api("/api/tasks");
    const running = (tasks || []).filter((t) => t.state === "running").length;
    html += pill(running ? `⚙ ${running} وظیفهٔ در حال اجرا` : "⚙ بدون وظیفهٔ در حال اجرا",
      running ? "sp-warn" : "sp-idle");
  } catch { /* وظایف اختیاری‌اند — حذف این پیل، خطا نیست */ }
  html += `<a class="strip-link" href="/workspace.html">💬 گفتگو با مدل →</a>`;
  st.innerHTML = html;
})();
