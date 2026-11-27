/* AI Factory dashboard logic — vanilla JS, no dependencies */
"use strict";

const $ = (sel) => document.querySelector(sel);
const api = async (url, options) => {
  const r = await fetch(url, options);
  let data = null;
  try { data = await r.json(); } catch { /* ignore */ }
  if (!r.ok || (data && data.ok === false)) {
    throw new Error((data && data.error) || ("HTTP " + r.status));
  }
  return data;
};

let currentProject = null;
let openFilePath = null;

/* ---------------- status ---------------- */
async function loadStatus() {
  const el = $("#status");
  try {
    const { status } = await api("/api/status");
    const mb = (v) => (v == null ? "—" : v + " MB");
    el.innerHTML =
      `<div class="kv"><span>نسخه</span><span>${esc(status.app)} ${esc(status.version)}</span></div>` +
      `<div class="kv"><span>زمان کارکرد سرور</span><span>${fmtUptime(status.server_uptime_seconds)}</span></div>` +
      `<div class="kv"><span>Python</span><span dir="ltr">${esc(status.python_version)}</span></div>` +
      `<div class="kv"><span>Node.js</span><span dir="ltr">${esc(status.node_version) || "—"}</span></div>` +
      `<div class="kv"><span>OpenCode</span><span dir="ltr">${esc(status.opencode_version) || "—"}</span></div>` +
      `<div class="kv"><span>حافظه</span><span>${mb(status.memory.available_mb)} آزاد از ${mb(status.memory.total_mb)}</span></div>`;
    renderServices(status.services);
  } catch (e) {
    el.textContent = "خطا در دریافت وضعیت: " + e.message;
  }
}

function renderServices(svc) {
  const label = {
    dashboard: "سرور پنل", code_server: "Code Server",
    nginx: "Nginx", opencode: "OpenCode",
  };
  $("#services").innerHTML = Object.entries(svc).map(([k, v]) =>
    `<div class="kv"><span>${label[k] || k}</span>` +
    `<span class="badge ${v ? "b-ok" : "b-bad"}">${v ? "فعال" : "غیرفعال"}</span></div>`
  ).join("");
}

function fmtUptime(s) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h + ":" + String(m).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
}

/* ---------------- model ---------------- */
async function loadModel() {
  const el = $("#model");
  try {
    const { model } = await api("/api/model");
    const a = model && model.active;
    if (!a) { el.textContent = "مدلی تنظیم نشده است"; return; }
    el.innerHTML =
      `<div class="kv"><span>ارائه‌دهنده</span><span>${esc(a.provider)}</span></div>` +
      `<div class="kv"><span>مدل</span><span dir="ltr">${esc(a.model)}</span></div>` +
      `<div class="kv"><span>موتور</span><span dir="ltr">${esc(a.engine)}</span></div>`;
  } catch (e) {
    el.textContent = "خطا: " + e.message;
  }
}

/* ---------------- projects ---------------- */
async function loadProjects() {
  const el = $("#projects");
  try {
    const { projects } = await api("/api/projects");
    if (!projects.length) { el.textContent = "هیچ پروژه‌ای وجود ندارد — از فرم بالا بسازید."; return; }
    const render = (query) => {
      const q = (query || "").trim().toLowerCase();
      const shown = projects.filter((p) => !q || p.name.toLowerCase().includes(q));
      if (!shown.length) { el.innerHTML = '<div class="af-empty">پروژهای با این نام یافت نشد.</div>'; return; }
      el.innerHTML = `<div class="af-grid">` + shown.map((p) =>
        `<a href="#" class="af-card af-project open" data-name="${esc(p.name)}">` +
        `<span class="af-name">${esc(p.name)}</span>` +
        `<span class="af-chip">${p.files} فایل</span>` +
        `<span class="af-muted" dir="ltr">${esc(p.modified)}</span></a>`
      ).join("") + `</div>`;
      el.querySelectorAll("a.open").forEach((a) =>
        a.addEventListener("click", (ev) => {
          ev.preventDefault();
          openProject(a.dataset.name);
        })
      );
    };
    render("");
    const search = $("#projectSearch");
    if (search) search.addEventListener("input", () => render(search.value));
    window.AF_PROJECTS = projects;
  } catch (e) {
    el.textContent = "خطا: " + e.message;
  }
}

async function createProject(ev) {
  ev.preventDefault();
  const name = $("#projectName").value.trim();
  try {
    await api("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    $("#projectName").value = "";
    currentProject = name;
    localStorage.setItem("af.project", name);
    loadProjects();
  } catch (e) {
    $("#projects").textContent = "خطا: " + e.message;
  }
}

/* ---------------- files ---------------- */
async function openProject(name) {
  currentProject = name;
  localStorage.setItem("af.project", name);
  $("#filesCard").hidden = false;
  $("#currentProject").textContent = "(" + name + ")";
  $("#editorWrap").hidden = true;
  const el = $("#files");
  try {
    const { files } = await api("/api/files?project=" + encodeURIComponent(name));
    if (!files.length) { el.textContent = "پروژه خالی است."; return; }
    el.innerHTML = `<ul class="list">` + files.map(f =>
      `<li><button class="filebtn" data-path="${esc(f.path)}" data-edit="${f.editable}">📄 ${esc(f.path)}</button>` +
      `<span class="muted">${f.size_bytes} بایت${f.editable ? "" : " · فقط‌خواندنی"}</span></li>`
    ).join("") + `</ul>`;
    el.querySelectorAll("button.filebtn").forEach((b) =>
      b.addEventListener("click", () => viewFile(b.dataset.path, b.dataset.edit === "true"))
    );
  } catch (e) {
    el.textContent = "خطا: " + e.message;
  }
}

async function viewFile(path, editable) {
  openFilePath = path;
  try {
    const { content } = await api(
      "/api/file?project=" + encodeURIComponent(currentProject) +
      "&path=" + encodeURIComponent(path)
    );
    $("#editorWrap").hidden = !editable;
    $("#openFile").textContent = path;
    $("#editor").value = content || "";
    msg("", "ok");
  } catch (e) { msg("خطا: " + e.message, "err"); }
}

async function saveFile() {
  try {
    await api("/api/file", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project: currentProject, path: openFilePath, content: $("#editor").value,
      }),
    });
    msg("ذخیره شد ✓", "ok");
    openProject(currentProject);
  } catch (e) { msg("خطا در ذخیره: " + e.message, "err"); }
}

async function deleteFile() {
  if (!confirm("فایل «" + openFilePath + "» حذف شود؟")) return;
  try {
    await api("/api/file?project=" + encodeURIComponent(currentProject) +
      "&path=" + encodeURIComponent(openFilePath), { method: "DELETE" });
    msg("حذف شد", "ok");
    $("#editorWrap").hidden = true;
    openProject(currentProject);
  } catch (e) { msg("خطا در حذف: " + e.message, "err"); }
}

async function viewLogs() {
  const el = $("#logs");
  try {
    const which = $("#logWhich").value;
    const { logs } = await api("/api/logs?which=" + which + "&lines=60");
    el.textContent = logs || "خالی";
  } catch (e) { el.textContent = "خطا: " + e.message; }
}

/* ---------------- helpers ---------------- */
function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function msg(text, cls) {
  const el = $("#editorMsg");
  el.textContent = text;
  el.className = "msg " + cls;
}

/* ---------------- P7-5: agents & tasks ---------------- */
const TASK_LABEL = {
  queued: "در صف", running: "در حال اجرا", done: "انجام شد",
  failed: "ناموفق", cancelled: "لغوشده", awaiting_user: "منتظر پاسخ شما",
};
const TASK_CLS = {
  queued: "b-wait", running: "b-wait", done: "b-ok",
  failed: "b-bad", cancelled: "b-bad", awaiting_user: "b-wait",
};

async function loadAgents() {
  const el = $("#agents");
  try {
    const [{ agents }, { perf }] = await Promise.all([
      api("/api/agents"), api("/api/memory/agents"),
    ]);
    if (!agents.length) { el.textContent = "هیچ عاملی ثبت نشده است."; return; }
    el.innerHTML = `<ul class="list">` + agents.map((a) => {
      const p = perf[a.name] || {};
      const stat = p.total
        ? `${p.done} موفق · ${p.failed} ناموفق` +
          (p.avg_duration_s ? ` · میانگین ${p.avg_duration_s}s` : "")
        : "بدون اجرا";
      const lastErr = (p.errors && p.errors[0])
        ? `<div class="muted" style="font-size:.8rem;">آخرین خطا: ${esc(p.errors[0].error)}</div>`
        : "";
      return `<li>` +
        `<span class="badge ${a.enabled ? "b-ok" : "b-bad"}">${a.enabled ? "فعال" : "غیرفعال"}</span> ` +
        `<strong>${esc(a.name)}</strong> — ${esc(a.role)} ` +
        `<span class="muted" dir="ltr">[${esc(a.model || "مدل پنل")}]</span>` +
        `<div class="muted" style="font-size:.85rem;">${stat}</div>${lastErr}` +
        `</li>`;
    }).join("") + `</ul>`;
  } catch (e) {
    el.textContent = "خطا: " + e.message;
  }
}

async function loadOrchestrator() {
  const el = $("#orchStatus");
  try {
    const { orchestrator: o } = await api("/api/orchestrator");
    const q = o.queue || {};
    const workers = (o.workers || []).map((w) =>
      `${w.id}${w.alive ? "" : " (مرده)"}`).join("، ") || "—";
    el.innerHTML =
      `<div class="kv"><span>orchestrator</span>` +
      `<span class="badge ${o.running ? "b-ok" : "b-bad"}">${o.running ? "در حال اجرا" : "متوقف"}</span></div>` +
      `<div class="kv"><span>کارگرها</span><span>${esc(workers)}</span></div>` +
      `<div class="kv"><span>صف</span><span>` +
      `در صف ${q.queued || 0} · اجرا ${q.running || 0} · موفق ${q.done || 0} · ناموفق ${q.failed || 0}` +
      `</span></div>` +
      `<div class="kv"><span>حافظهٔ آزاد</span><span>` +
      (o.memory_available_mb != null
        ? `${o.memory_available_mb} MB (حداقل ${o.min_required_mb})`
        : "نامشخص") + `</span></div>`;
  } catch (e) {
    el.textContent = "خطا: " + e.message;
  }
}

async function loadTasks() {
  const el = $("#tasks");
  try {
    const { tasks } = await api("/api/tasks");
    const recent = tasks.slice(0, 12);
    if (!recent.length) { el.textContent = " هنوز وظیفهای ثبت نشده است."; return; }
    const pending = recent.filter((t) => t.state === "awaiting_user");
    const asks = {};
    for (const t of pending) {
      try {
        const r = await api("/api/tasks/ask?id=" + encodeURIComponent(t.id));
        if (r.awaiting) asks[t.id] = r.ask || {};
      } catch (e) { asks[t.id] = { error: e.message }; }
    }
    el.innerHTML = `<ul class="list">` + recent.map((t) => {
      const a = asks[t.id];
      const fresh = a && (!a.created || (Date.now()/1000 - Number(a.created)) < 86400);
      const askHtml = (t.state === "awaiting_user" && fresh) ? `
        <details class="af-decision" open style="margin-top:.5rem;">
          <summary><strong>سؤال عامل:</strong> ${esc((a && a.question) || "—")}</summary>
          ${(a && a.options || []).length ? `<div class="af-actions">` +
            a.options.map((o, i) => `<button class="btn small" data-opt="${i}">${esc(o)}</button>`).join(" ") + `</div>` : ""}
          <div class="af-actions">
            <input class="ask-text" placeholder="پاسخ متنی یا گزینه را وارد کنید">
            <button class="btn small primary ask-send">ارسال پاسخ</button>
          </div>
          <div class="ask-msg muted"></div>
        </details>` : "";
      const done = (t.result && t.result.verification) ?
        `<div class="muted" style="font-size:.75rem;">نتیجه: ${esc(t.result.verification)}</div>` : "";
      return `<li data-task="${esc(t.id)}">` +
        `<span class="badge ${TASK_CLS[t.state] || "b-wait"}">${TASK_LABEL[t.state] || t.state}</span> ` +
        `<strong>${esc(t.project)}</strong> <span class="muted">با ${esc(t.agent)}</span>` +
        `<div class="muted" style="font-size:.85rem;">${esc((t.goal || "").slice(0, 90))}</div>` +
        (t.error ? `<div class="muted" style="font-size:.8rem;color:var(--bad);">${esc(t.error)}</div>` : "") +
        done + askHtml + `</li>`;
    }).join("") + `</ul>`;

    el.querySelectorAll("li[data-task]").forEach((li) => {
      const id = li.getAttribute("data-task");
      const a = asks[id] || {};
      const input = li.querySelector(".ask-text");
      const msg = li.querySelector(".ask-msg");
      li.querySelectorAll("[data-opt]").forEach((b) => b.addEventListener("click", () => {
        input.value = (a.options || [])[Number(b.getAttribute("data-opt"))] || "";
      }));
      const send = li.querySelector(".ask-send");
      if (send) send.addEventListener("click", async () => {
        const answer = input.value.trim();
        if (!answer) { msg.textContent = "پاسخی وارد نشد."; return; }
        msg.textContent = "در حال ارسال…";
        try {
          const r = await api("/api/tasks/answer", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id, token: a.token || "", answer }),
          });
          msg.textContent = "پاسخ ثبت شد — وظیفه مجدداً در صف اجرا است.";
          setTimeout(loadTasks, 1200);
        } catch (e) {
          msg.textContent = "خطا: " + e.message;
        }
      });
    });
  } catch (e) {
    el.textContent = "خطا: " + e.message;
  }
}

async function fillTaskForm() {
  try {
    const [{ projects }, { agents }] = await Promise.all([
      api("/api/projects"), api("/api/agents"),
    ]);
    $("#taskProject").innerHTML = projects.map((p) =>
      `<option value="${esc(p.name)}">${esc(p.name)}</option>`).join("");
    $("#taskAgent").innerHTML = agents.filter((a) => a.enabled).map((a) =>
      `<option value="${esc(a.name)}">${esc(a.name)}</option>`).join("")
      || `<option value="builder">builder</option>`;
  } catch (e) { /* form stays as-is */ }
}

async function createTask(ev) {
  ev.preventDefault();
  const tmsg = $("#taskMsg");
  try {
    await api("/api/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project: $("#taskProject").value,
        agent: $("#taskAgent").value,
        goal: $("#taskGoal").value.trim(),
        reasoning: localStorage.getItem("af.reasoning") || "auto",
      }),
    });
    $("#taskGoal").value = "";
    tmsg.textContent = "وظیفه ثبت شد و به صف رفت ✓";
    tmsg.className = "msg ok";
    loadTasks(); loadOrchestrator();
  } catch (e) {
    tmsg.textContent = "خطا: " + e.message;
    tmsg.className = "msg err";
  }
}

async function orchStart(n) {
  await api("/api/orchestrator/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workers: n }),
  });
  loadOrchestrator();
}
async function orchStop() {
  await api("/api/orchestrator/stop", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  loadOrchestrator();
}

/* ---------------- init ---------------- */
$("#newProjectForm").addEventListener("submit", createProject);
$("#btnSave").addEventListener("click", saveFile);
$("#btnDelete").addEventListener("click", deleteFile);
$("#btnLogs").addEventListener("click", viewLogs);
$("#btnRefresh").addEventListener("click", () => {
  loadStatus(); loadModel(); loadProjects();
  loadAgents(); loadOrchestrator(); loadTasks(); fillTaskForm();
});
$("#newTaskForm").addEventListener("submit", createTask);
$("#btnOrchStart1").addEventListener("click", () => orchStart(1).catch(
  (e) => msg("خطا: " + e.message, "err")));
$("#btnOrchStart2").addEventListener("click", () => orchStart(2).catch(
  (e) => msg("خطا: " + e.message, "err")));
$("#btnOrchStop").addEventListener("click", () => orchStop().catch(
  (e) => msg("خطا: " + e.message, "err")));

loadStatus();
loadModel();
loadProjects();
loadAgents();
loadOrchestrator();
loadTasks();
fillTaskForm();
setInterval(() => { loadOrchestrator(); loadTasks(); }, 5000);

/* AF-53 — one action to enter Workspace with the chosen project */
document.addEventListener("click", (e) => {
  const b = e.target.closest && e.target.closest("#btnOpenWorkspace");
  if (!b) return;
  const picked = currentProject || (window.AF_PROJECTS || [])[0] && window.AF_PROJECTS[0].name;
  if (picked) localStorage.setItem("af.project", picked);
  location.href = "/workspace.html";
});
