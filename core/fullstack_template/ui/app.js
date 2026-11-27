/* {{APP_TITLE}} — UI logic (vanilla JS) */
"use strict";

/* CSRF: double-submit cookie — read cookie and send header */
function csrfToken() {
  const m = document.cookie.match(/(?:^|;\s*)af_csrf=([0-9a-f]{64})/);
  return m ? m[1] : "";
}

async function api(path, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  const headers = Object.assign({}, opts.headers || {});
  if (method !== "GET") headers["X-CSRF-Token"] = csrfToken();
  if (opts.body != null) {
    headers["Content-Type"] = "application/json";
    opts = Object.assign({}, opts, { body: JSON.stringify(opts.body) });
  }
  const r = await fetch("/api/" + path, Object.assign({}, opts, { method, headers }));
  let d = null;
  try { d = await r.json(); } catch { /* no body */ }
  if (!r.ok || (d && d.ok === false)) {
    throw new Error((d && d.error) || ("HTTP " + r.status));
  }
  return d;
}

const msg = (sel, text, cls) => {
  const el = document.querySelector(sel);
  el.textContent = text || "";
  el.className = "msg " + (cls || "");
};

/* bootstrap CSRF early so forms work */
api("csrf").catch(() => {});

function goByRole(user) {
  if (!user) { location.href = "/"; return; }
  location.href = user.role === "admin" ? "/admin.html" : "/user.html";
}

window.pageLoginPage = async () => {};
document.getElementById("fLogin")?.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const r = await api("auth/login", {
      method: "POST",
      body: { username: document.getElementById("lUser").value.trim(),
              password: document.getElementById("lPass").value },
    });
    goByRole(r.user);
  } catch (e) { msg("#loginMsg", "خطا: " + e.message, "err"); }
});
document.getElementById("fReg")?.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  try {
    const r = await api("auth/register", {
      method: "POST",
      body: { username: document.getElementById("rUser").value.trim(),
              password: document.getElementById("rPass").value },
    });
    msg("#regMsg", "ثبت شد؛ در حال ورود…", "ok");
    goByRole(r.user);
  } catch (e) { msg("#regMsg", "خطا: " + e.message, "err"); }
});

document.getElementById("btnLogout")?.addEventListener("click", async () => {
  try { await api("auth/logout", { method: "POST", body: {} }); }
  catch { /* logout regardless */ }
  location.href = "/";
});

function renderCustomers(tbody, rows, editable) {
  tbody.innerHTML = rows.map((c) =>
    `<tr data-id="${c.id}"><td>${c.id}</td>` +
    `<td><span class="v">${esc(c.name)}</span></td>` +
    `<td dir="ltr">${esc(c.phone || "—")}</td>` +
    `<td>${esc(c.owner || c.note || "—")}</td>` +
    (editable
      ? `<td><button class="btn small act-edit">✏️</button> ` +
        `<button class="btn small danger act-del">🗑</button></td>`
      : `<td><button class="btn small danger act-del">🗑</button></td>`)).join("");
  tbody.querySelectorAll("button.act-del").forEach((b) =>
    b.addEventListener("click", async () => {
      const id = b.closest("tr").dataset.id;
      try {
        await api("customers/" + id, { method: "DELETE", body: {} });
        loadCustomers();
      } catch (e) { alert("خطا: " + e.message); }
    }));
  tbody.querySelectorAll("button.act-edit").forEach((b) =>
    b.addEventListener("click", () => {
      const tr = b.closest("tr");
      const row = rows.find((r) => String(r.id) === tr.dataset.id);
      const name = prompt("نام جدید:", row.name);
      if (name == null) return;
      const phone = prompt("تلفن جدید:", row.phone || "");
      if (phone == null) return;
      api("customers/" + row.id + "/update", {
        method: "POST", body: { name, phone },
      }).then(loadCustomers).catch((e) => alert("خطا: " + e.message));
    }));
}

const esc = (s) => String(s ?? "").replace(/[&<>"]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function loadCustomers() {
  const r = await api("customers");
  renderCustomers(document.querySelector("#tbl tbody"), r.customers, true);
}

async function requireLogin() {
  try {
    const r = await api("me");
    document.getElementById("who").textContent =
      r.user.username + " (" + r.user.role + ")";
    return r.user;
  } catch {
    location.href = "/";
    return null;
  }
}

window.pageUser = async () => {
  const u = await requireLogin();
  if (!u) return;
  if (u.role === "admin") { location.href = "/admin.html"; return; }
  document.getElementById("fAdd").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      await api("customers", {
        method: "POST",
        body: { name: document.getElementById("cName").value.trim(),
                phone: document.getElementById("cPhone").value.trim(),
                note: document.getElementById("cNote").value.trim() },
      });
      msg("#addMsg", " ثبت شد", "ok");
      loadCustomers();
    } catch (e) { msg("#addMsg", "خطا: " + e.message, "err"); }
  });
  loadCustomers();
};

window.pageAdmin = async () => {
  const u = await requireLogin();
  if (!u) return;
  if (u.role !== "admin") { location.href = "/user.html"; return; }
  try {
    const s = await api("admin/stats");
    document.getElementById("stats").textContent =
      `کاربران: ${s.stats.users} · مشتری‌ها: ${s.stats.customers}`;
    const us = await api("admin/users");
    document.getElementById("users").innerHTML = us.users
      .map((x) => `${x.username} · نقش: ${x.role}`).join("<br>");
    const r = await api("customers");
    renderCustomers(document.querySelector("#tbl tbody"), r.customers, false);
  } catch (e) {
    document.getElementById("stats").textContent = "خطا: " + e.message;
  }
};
