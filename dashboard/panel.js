/* P6-2 — panel auth overlay + automatic CSRF for every API call.
 * Included BEFORE app/work/factory scripts on index/workspace/factory pages.
 * Wraps window.fetch:
 *   - POST/PUT/DELETE gain the X-Panel-CSRF header (double-submit, from the
 *     panel_csrf cookie the server set at login).
 *   - A 401 triggers the login overlay once and the request is transparently
 *     retried after successful login.
 * No password or token is ever stored beyond the HttpOnly session cookie.
 */
"use strict";

(function panelAuth() {
  const CSRF_COOKIE = "panel_csrf";
  const originalFetch = window.fetch.bind(window);

  function csrfToken() {
    let token = "";
    document.cookie.split(";").some((part) => {
      const kv = part.split("=");
      if (kv[0].trim() === CSRF_COOKIE) { token = kv[1].trim(); return true; }
      return false;
    });
    return token;
  }

  function withCsrf(opts) {
    if (!opts) opts = {};
    opts.headers = Object.assign({}, opts.headers || {});
    const t = csrfToken();
    if (t) opts.headers["X-Panel-CSRF"] = t;
    return opts;
  }

  let overlay = null;

  function buildOverlay() {
    overlay = document.createElement("div");
    overlay.id = "panel-oauth";
    overlay.innerHTML = `
      <style>
        #panel-oauth{position:fixed;inset:0;z-index:9999;background:rgba(10,12,20,.82);
          display:flex;align-items:center;justify-content:center;padding:16px}
        #panel-oauth .po-card{background:#161b28;color:var(--fg,#e8ecf4);border:1px solid #2a3145;
          border-radius:14px;padding:22px;max-width:340px;width:100%;font:14px/1.7 sans-serif;
          box-shadow:0 18px 50px rgba(0,0,0,.5)}
        #panel-oauth h2{margin:0 0 4px;font-size:17px}
        #panel-oauth p{margin:2px 0 14px;color:#8b93a7;font-size:12.5px}
        #panel-oauth input{width:100%;box-sizing:border-box;padding:9px 11px;border-radius:9px;
          border:1px solid #35405a;background:#0e121c;color:inherit;margin-bottom:10px;font-size:14px}
        #panel-oauth button{width:100%;padding:10px;border:0;border-radius:9px;cursor:pointer;
          background:#4c7dff;color:#fff;font-size:14px}
        #panel-oauth .po-msg{color:#ff8e8e;font-size:12.5px;min-height:18px;margin:2px 0 8px}
      </style>
      <div class="po-card">
        <h2 id="po-title">ورود به پنل</h2>
        <p id="po-sub">برای دسترسی به APIها رمز مدیر را وارد کنید.</p>
        <input id="po-pass" type="password" inputmode="text" autocomplete="current-password" placeholder="رمز مدیر">
        <input id="po-pass2" type="password" autocomplete="new-password" placeholder="تکرار رمز" hidden>
        <div id="po-msg" class="po-msg"></div>
        <button id="po-go">ورود</button>
      </div>`;
    document.documentElement.appendChild(overlay);
    const go = async () => {
      const pass = overlay.querySelector("#po-pass").value;
      const again = overlay.querySelector("#po-pass2");
      const msg = overlay.querySelector("#po-msg");
      msg.textContent = "";
      if (setupMode && pass !== again.value) { msg.textContent = "دو رمز یکسان نیستند"; return; }
      overlay.querySelector("#po-go").textContent = "…";
      const r = await originalFetch(setupMode ? "/api/panel/init" : "/api/panel/login",
                                    withCsrf({ method: "POST",
                                               headers: { "Content-Type": "application/json" },
                                               body: JSON.stringify({ password: pass }) }));
      const d = await r.json().catch(() => ({}));
      overlay.querySelector("#po-go").textContent = "ورود";
      if (!r.ok || d.ok === false) {
        msg.textContent = (d && d.error) || ("HTTP " + r.status);
        return;
      }
      overlay.remove();
      // AF-26 / C2 — resolve the REAL pending promise.  A no-op here left
      // boot() and every 401 retry waiting forever, so first-run setup and
      // session recovery could never complete.
      finishLogin();
    };
    overlay.querySelector("#po-pass").focus();
    overlay.querySelector("#po-go").addEventListener("click", go);
    overlay.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  }

  let setupMode = false;
  let loginPromise = null;
  let resolveLoginRef = null;

  // AF-26 / C2 — resolve the REAL pending promise. A no-op here left boot()
  // and every 401 retry waiting forever, so first-run setup and session
  // recovery could never complete.
  function finishLogin() {
    const done = resolveLoginRef;
    resolveLoginRef = null;
    if (done) done();
  }

  function requireLogin(asSetup) {
    if (overlay && document.documentElement.contains(overlay)) return loginPromise;
    setupMode = !!asSetup;
    // AF-26 / C2 — build the DOM before touching its children. In the old
    // order #po-pass2 stayed hidden and go() compared the password to "".
    buildOverlay();
    if (asSetup) {
      const t = overlay?.querySelector("#po-sub");
      if (t) t.textContent = "راه‌اندازی اولیه: رمز مدیر را انتخاب کنید (ذخیره به‌صورت هش PBKDF2).";
      if (overlay?.querySelector("#po-pass2")) overlay.querySelector("#po-pass2").hidden = false;
    }
    let ok;
    loginPromise = new Promise((res) => { ok = res; });
    resolveLoginRef = ok;
    return loginPromise;
  }

  function boot() {
    (async () => {
      let st = {};
      try { st = await originalFetch("/api/panel/state").then((r) => r.json()); }
      catch { st = { ok: false }; }
      let need = false;
      if (!st.ok || !st.initialized) {
        need = true;
        await requireLogin(true);
      } else {
        // probe a protected endpoint through our wrapper-free fetch
        const probe = await originalFetch("/api/projects").catch(() => null);
        if (probe && probe.status === 401) need = true;
        if (need) await requireLogin(false);
      }
    })();
  }

  window.fetch = async function (url, opts) {
    const method = (opts && opts.method || "GET").toUpperCase();
    const mutating = method === "POST" || method === "PUT" || method === "DELETE";
    opts = mutating ? withCsrf(opts) : opts;
    const r = await originalFetch(url, opts);
    if (r.status === 401) {
      await requireLogin(false);
      if (mutating) opts = withCsrf(opts);   // fresh csrf after (re)login
      return originalFetch(url, opts);
    }
    return r;
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else { boot(); }
})();
