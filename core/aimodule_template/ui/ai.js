/* ai.js — منطق صفحه تنظیمات هوش مصنوعی (فقط same-origin، بدون وابستگی بیرونی).
 *
 * قاعده‌های امنیتی: هیچ مقدار کلید از سرور گرفته نمی‌شود (فقط set/unset)؛
 * هر mutation با هدر X-AI-Admin (رمز مدیر) و X-AI-CSRF (توکن از /api/ai/admin/csrf)
 * ارسال می‌شود؛ ذخیره فقط فیلدهای عوض‌شده (dirty tracking) را می‌فرستد.
 */
"use strict";

var $ = function (id) { return document.getElementById(id); };

var CSRF = "";                 // از GET /api/ai/admin/csrf
var SETTING = null;            // آخرین settings سرور
var DIRTY = {};                // فیلدهای عوض‌شدهٔ هنوز ذخیره‌نشده

// تنظیم‌شونده‌ها که dirty-check دارند
var TRACKED = ["enabled", "provider", "base_url", "model"];

/* ---------------------------------------------------------------- helpers */
function showErr(text) {
  var box = $("error");
  box.textContent = text || "خطای ناشناخته";
  box.hidden = false;
  if (text) box.scrollIntoView({ block: "nearest" });
}
function hideErr() { $("error").hidden = true; }
function showMsg(text) {
  var box = $("msg");
  box.textContent = text;
  box.hidden = false;
  setTimeout(function () { box.hidden = true; }, 5000);
}

function authHeaders() {
  var h = { "X-AI-CSRF": CSRF };
  var p = $("pass").value || $("initPass").value || "";
  if (p) h["X-AI-Admin"] = p;
  return h;
}

function api(method, path, body) {
  var opt = {
    method: method,
    credentials: "same-origin",
    headers: authHeaders()
  };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  return fetch(path, opt).then(function (r) {
    return r.json().then(function (j) { return { code: r.status, json: j }; });
  }).catch(function () {
    return { code: 0, json: { ok: false, error: "خطای شبکه با خود سرور" } };
  });
}

/* ---------------------------------------------------------------- refresh */
function refreshCsrf() {
  return fetch("/api/ai/admin/csrf", { credentials: "same-origin" })
    .then(function (r) { return r.json(); })
    .then(function (j) { if (j && j.csrf) CSRF = j.csrf; })
    .catch(function () {});
}

function refreshAll() {
  return Promise.all([
    fetch("/api/ai/admin/settings", { credentials: "same-origin" })
      .then(function (r) { return r.json(); }),
    fetch("/api/ai/status", { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
  ]).then(function (both) {
    var s = both[0] && both[0].ok ? both[0] : null;
    var st = both[1] && both[1].ok ? both[1] : null;
    if (s) renderSettings(s);
    if (st) renderStatus(st);
  }).catch(function () { showErr("دریافت وضعیت ممکن نشد"); });
}

function renderStatus(st) {
  var strip = $("statusStrip");
  var on = st.enabled;
  var cls = st.ready ? "good" : (on ? "warn" : "off");
  strip.innerHTML = "";
  strip.appendChild(badge(on ? "فعال" : "غیرفعال", on ? "good" : "off"));
  strip.appendChild(badge(st.ready ? "متصل و آماده" : "آماده نیست", cls));
  strip.appendChild(badge("پروایدر: " + (st.provider || "—"), ""));
  strip.appendChild(badge("مدل: " + (st.model || "—"), ""));
  strip.appendChild(badge("کلید: " + (st.key === "set" ? "ثبت شده" : "ثبت نشده"),
                          st.key === "set" ? "good" : "warn"));
  if (st.detail) {
    var d = document.createElement("div");
    d.className = "detail";
    d.textContent = st.detail;
    strip.appendChild(d);
  }
}

function badge(text, cls) {
  var s = document.createElement("span");
  s.className = "badge chip " + (cls || "");
  s.textContent = text;
  return s;
}

function renderSettings(s) {
  SETTING = s;
  DIRTY = {};
  $("enabled").checked = !!s.enabled;

  var sel = $("provider");
  sel.innerHTML = "";
  var known = false;
  (s.providers || ["openai"]).forEach(function (p) {
    var o = document.createElement("option");
    o.value = p;
    o.textContent = p === "openai" ? "openai (سازگار با OpenAI)" : p;
    if (p === s.provider) known = true;
    sel.appendChild(o);
  });
  var oc = document.createElement("option");
  oc.value = "__custom__";
  oc.textContent = "سایر / نام ثبت‌شده در ماژول…";
  sel.appendChild(oc);
  if (s.provider && !known) {            // provider ثبت‌شدهٔ غیر از لیست
    oc.selected = true;
    $("providerCustom").hidden = false;
    $("providerCustom").value = s.provider;
  } else {
    $("providerCustom").hidden = true;
  }

  $("baseUrl").value = s.base_url || "";
  $("model").value = s.model || "";
  $("modelHints").innerHTML = (s.last_test && s.last_test.model)
    ? "<option value=\"" + esc(s.last_test.model) + "\">" : "";
  setKeyState(s.key === "set");
  var lt = $("lastTest");
  if (s.last_test) {
    lt.hidden = false;
    lt.innerHTML = "";
    kv(lt, "آخرین تست",
       (s.last_test.ready ? "✅ موفق" : "❌ ناموفق") +
       " — " + (s.last_test.ts || ""));
    kv(lt, "جزئیات", s.last_test.detail || "—");
  } else {
    lt.hidden = true;
  }
  var init = $("initBox"), pb = $("passBox");
  if (s.initialized) { init.hidden = true; pb.hidden = false; }
  else { init.hidden = false; pb.hidden = true; }
  $("delKeyBtn").disabled = s.key !== "set";
}

function kv(dl, k, v) {
  var dt = document.createElement("dt"); dt.textContent = k;
  var dd = document.createElement("dd"); dd.textContent = v;
  dl.appendChild(dt); dl.appendChild(dd);
}
function esc(t) { return String(t).replace(/[&<>"']/g, ""); }

function setKeyState(isSet) {
  var b = $("keyState");
  b.textContent = isSet ? "کلید ثبت شده (مخفی)" : "کلیدی ثبت نشده";
  b.className = "badge " + (isSet ? "good" : "warn");
  $("apiKey").placeholder = isSet
    ? "برای جایگزینی، کلید جدید را وارد کنید" : "sk-…";
  $("delKeyBtn").disabled = !isSet;
}

/* ---------------------------------------------------------------- actions */
function markDirty(el, field) {
  if (SETTING == null) return;
  var name = field === "providerCustom" ? "provider" : field;
  var now = name === "enabled" ? el.checked
          : (name === "provider" ? providerValue() : el.value.trim());
  var old = SETTING[name];
  if (name === "provider") old = old || "openai";
  if (String(now) === String(old)) delete DIRTY[name];
  else DIRTY[name] = now;
  finalize(name, old, now);
}

function finalize(name, old, now) {
  void old; void now;
  $("saveBtn").textContent = "💾 ذخیره" +
    (Object.keys(DIRTY).length ? " (" + Object.keys(DIRTY).length + " تغییر)" : "");
}

function providerValue() {
  var v = $("provider").value;
  return v === "__custom__" ? ($("providerCustom").value || "").trim() : v;
}

function saveDirty() {
  hideErr();
  if (!SETTING || !SETTING.initialized) {
    showErr("اول رمز مدیر را تنظیم کنید");
    return Promise.resolve(false);
  }
  if (!Object.keys(DIRTY).length) return Promise.resolve(true);
  var body = {};
  TRACKED.forEach(function (f) {
    if (f in DIRTY) body[f] = DIRTY[f];
  });
  return api("POST", "/api/ai/admin/save", body).then(function (r) {
    if (r.code === 200 && r.json.ok) {
      showMsg("تنظیمات ذخیره شد");
      DIRTY = {};
      $("saveBtn").textContent = "💾 ذخیره";
      refreshAll();
      return true;
    }
    showErr(r.json.error || ("ذخیره نشد (کد " + r.code + ")"));
    refreshCsrf();
    return false;
  });
}

function saveKey() {
  var v = $("apiKey").value.trim();
  if (!v) { showMsg("چیزی برای ذخیره وارد نشده"); return; }
  api("POST", "/api/ai/admin/key", { api_key: v }).then(function (r) {
    $("apiKey").value = "";
    if (r.code === 200 && r.json.ok) {
      showMsg("کلید ذخیره شد (فقط سمت سرور)");
      setKeyState(true);
      refreshAll();
    } else {
      showErr(r.json.error || "ذخیره کلید نشد");
      refreshCsrf();
    }
  });
}

function deleteKey() {
  if (!window.confirm("کلید ذخیره‌شده حذف شود؟")) return;
  api("POST", "/api/ai/admin/key/delete", {}).then(function (r) {
    if (r.code === 200 && r.json.ok) {
      showMsg("کلید حذف شد");
      setKeyState(false);
      refreshAll();
    } else showErr(r.json.error || "حذف نشد");
  });
}

function testConn() {
  hideErr();
  $("testBtn").disabled = true;
  $("testBtn").textContent = "… آزمایش";
  // اگر فیلدی dirty است همان در overrides می‌رود (تستِ پیش از ذخیره)
  var body = {};
  ["provider", "base_url", "model"].forEach(function (f) {
    if (f in DIRTY) body[f] = DIRTY[f];
  });
  var chain = Object.keys(DIRTY).length ? saveDirty()
                                         : Promise.resolve(true);

  chain.then(function () {
    return api("POST", "/api/ai/admin/test", body);
  }).then(function (r) {
    $("testBtn").disabled = false;
    $("testBtn").textContent = "🔌 تست اتصال";
    var j = r.json || {};
    if (j.disabled) {
      showErr("قابلیت AI غیرفعال است — اول تابلوی «فعال» را بزنید و ذخیره کنید");
      return;
    }
    if (r.code === 403) {
      showErr(j.error || "دسترسی مدیریتی لازم است");
      refreshCsrf();
      return;
    }
    if (j.ok) {
      showMsg(j.ready ? "✅ اتصال واقعی برقرار است — " + (j.detail || "")
                      : "❌ اتصال برقرار نشد — " + (j.detail || ""));
      refreshAll();
    } else showErr(j.error || "تست انجام نشد");
  });
}

function initAdmin() {
  hideErr();
  var p1 = $("initPass").value, p2 = $("initPass2").value;
  if (p1.length < 6) { showErr("رمز مدیر باید حداقل ۶ نویسه باشد"); return; }
  if (p1 !== p2) { showErr("تکرار رمز با رمز یکسان نیست"); return; }
  api("POST", "/api/ai/admin/init", { passcode: p1 }).then(function (r) {
    $("initPass").value = ""; $("initPass2").value = "";
    if (r.code === 200 && r.json.ok) {
      $("pass").value = p1;                 // فقط در حافظهٔ همین صفحه
      showMsg("رمز مدیر تنظیم شد — از این پس تغییرات با همین رمز امکان‌پذیر است");
      refreshAll();
    } else showErr(r.json.error || "تنظیم رمز نشد");
  });
}

/* ------------------------------------------------------------------ boot */
document.addEventListener("DOMContentLoaded", function () {
  $("enabled").addEventListener("change", function () {
    markDirty(this, "enabled");
  });
  $("provider").addEventListener("change", function () {
    $("providerCustom").hidden = this.value !== "__custom__";
    markDirty(this, "provider");
  });
  $("providerCustom").addEventListener("input", function () {
    markDirty(this, "providerCustom");
  });
  $("baseUrl").addEventListener("input", function () { markDirty(this, "base_url"); });
  $("model").addEventListener("input", function () { markDirty(this, "model"); });

  $("eye").addEventListener("click", function () {
    var f = $("apiKey");
    f.type = f.type === "password" ? "text" : "password";
    this.textContent = f.type === "password" ? "👁" : "🙈";
  });
  $("saveBtn").addEventListener("click", saveDirty);
  $("testBtn").addEventListener("click", testConn);
  $("delKeyBtn").addEventListener("click", deleteKey);
  $("initBtn").addEventListener("click", initAdmin);

  refreshCsrf().then(refreshAll);
});
