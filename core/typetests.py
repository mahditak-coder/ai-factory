"""P6-1 — real functional contract tests per project type.

These are NOT file-existence or syntax checks (checker already does those).
Each type has a machine-verifiable BEHAVIOURAL contract the generated
product was instructed to honour (see factory.TYPE_REQUIREMENTS):

  webpage / shop / dashboard / webtool → the REAL product boots (runner),
      HTML is fetched and parsed with stdlib html.parser: title/h1 present,
      required interactive/product elements exist, every referenced local
      file actually resolves (broken asset = exact failure), no leftover
      template placeholders.

  api / ai-tool / fullstack(+python)   → the product process is started
      through the runner exactly like a user run; behaviour probes check
      GET /api/health returns JSON {ok:true}; ai-tool must also serve
      /ai.html (the auto-installed AI settings page).

If the project has NO runnable surface, the result honestly reports
"بدون پوشش تست" (no coverage) — never a fake green.
"""

import json
import re
import struct
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from core import paths

HTTP_TIMEOUT = 4.0
BOOT_WAIT_S = 12.0
_NO_COVER = "بدون پوشش تست"


def _fetch(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return 0, ""


class _HTML(HTMLParser):
    """Collect title/heading/interactive/product markers + asset refs."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self.h1 = 0
        self.form_controls = 0          # <form>/<input>/<textarea>/<select>
        self.buttons = 0
        self.product_cards = 0          # elements with data-product
        self.add_controls = 0           # elements with data-add
        self.assets = []                # local href/src references
        # R2: richer structure markers (dashboard/webapp/webtool contracts)
        self.tables = 0                 # <table> data tables
        self.charts = 0                 # <canvas>/<svg> chart targets
        self.scripts = 0                # <script> tags (src or inline)
        self.inline_js = []             # inline <script> bodies
        self.ids = set()                # all element ids
        self.classes = set()            # all class tokens

    def handle_starttag(self, tag, attrs):
        ad = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "h1":
            self.h1 += 1
        if tag == "form":
            self.form_controls += 1
        if tag in ("input", "textarea", "select"):
            self.form_controls += 1
        if tag == "button":
            self.buttons += 1
        if tag == "a" and (ad.get("href") or "") not in ("", None):
            self.buttons += 1
        if "data-product" in ad:
            self.product_cards += 1
        if "data-add" in ad:
            self.add_controls += 1
        if tag == "table":
            self.tables += 1
        if tag in ("canvas", "svg"):
            self.charts += 1
        if tag == "script":
            self.scripts += 1
            self._in_script = not ad.get("src")
        if ad.get("id"):
            self.ids.add(ad["id"].strip())
        if ad.get("class"):
            self.classes.update(ad["class"].split())
        if tag not in ("script", "embed", "object"):
            v = ad.get("href") or ad.get("src")
            if v and not v.startswith(("http://", "https://", "#", "mailto:",
                                       "data:", "javascript:")) and v not in self.assets:
                self.assets.append(v)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if getattr(self, "_in_script", False):
            self.inline_js.append(data)


def _resolve_asset(project_dir: Path, ref: str) -> Path | None:
    clean = ref.split("#")[0].split("?")[0]
    if not clean:
        return None
    cand = project_dir / clean.lstrip("/")
    return cand if cand.is_file() else None


def _png_size(p: Path) -> tuple[int, int] | None:
    """Real PNG header parse: magic + IHDR dimensions (stdlib struct)."""
    try:
        head = p.read_bytes()[:24]
        if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
            return None
        w, h = struct.unpack(">II", head[16:24])
        return w, h
    except OSError:
        return None


def check_pwa(d: Path, base: str, errors: list) -> None:
    """P8-1 — behavioural contract for an installable PWA.

    Machine-verifiable installability facts (NOT file existence only):
    manifest served + parsed with required fields, 192/512 icons that are
    REAL PNGs whose IHDR dimensions match their claim, a service worker
    with a fetch handler, and a real registration call in the shell.
    """
    # ---- manifest: served + valid + required fields
    code, mtxt = _fetch(base + "/manifest.json")
    if code != 200:
        errors.append({"file": "manifest.json", "kind": "manifest-not-served",
                       "message": f"GET /manifest.json → {code}؛ "
                                  "manifest باید سرو شود"})
        return
    try:
        m = json.loads(mtxt)
    except ValueError:
        errors.append({"file": "manifest.json", "kind": "manifest-invalid",
                       "message": "manifest.json قابل JSON-pars نیست"})
        return
    if not isinstance(m, dict):
        errors.append({"file": "manifest.json", "kind": "manifest-invalid",
                       "message": "manifest.json باید یک شیء JSON باشد"})
        return
    for field in ("name", "short_name", "start_url", "display"):
        if not str(m.get(field) or "").strip():
            errors.append({"file": "manifest.json", "kind": "manifest-field",
                           "message": f"فیلد الزامی «{field}» در manifest "
                                      "غایب یا خالی است"})
    if m.get("display") not in ("standalone", "fullscreen", "minimal-ui"):
        errors.append({"file": "manifest.json", "kind": "manifest-display",
                       "message": "display باید standalone/fullscreen/"
                                  "minimal-ui باشد (نصب‌پذیری کروم)"})

    # ---- start_url must resolve inside the project
    start = str(m.get("start_url") or "./index.html").lstrip("./")
    if start and _resolve_asset(d, start) is None:
        errors.append({"file": "manifest.json", "kind": "start-url-missing",
                       "message": f"start_url «{start}» در پروژه وجود ندارد"})

    # ---- icons: 192 + 512 claimed, real PNG, IHDR dims match claim
    icons = m.get("icons") if isinstance(m.get("icons"), list) else []
    claimed = {}
    for i in icons:
        if not isinstance(i, dict):
            continue
        sizes = str(i.get("sizes") or "")
        for s in sizes.lower().split():
            if "x" in s:
                claimed[s] = i
    for size in (192, 512):
        key = f"{size}x{size}"
        icon = claimed.get(key)
        if icon is None:
            errors.append({"file": "manifest.json", "kind": "icon-missing",
                           "message": f"آیکون {key} در manifest اعلام نشده است "
                                      "(لازم نصب‌پذیری)"})
            continue
        p = _resolve_asset(d, str(icon.get("src") or ""))
        if p is None:
            errors.append({"file": str(icon.get("src") or "icon"),
                           "kind": "icon-file-missing",
                           "message": f"فایل آیکون {key} در پروژه نیست"})
            continue
        dims = _png_size(p)
        if dims != (size, size):
            errors.append({"file": p.name, "kind": "icon-invalid",
                           "message": f"آیکون {key} PNG واقعی با ابعاد درست "
                                      f"نیست (واقعی: {dims})"})

    # ---- service worker: exists, real fetch handler, registered in shell
    sw = d / "sw.js"
    if not sw.is_file():
        errors.append({"file": "sw.js", "kind": "sw-missing",
                       "message": "sw.js (Service Worker) در پروژه نیست"})
        return
    swtxt = sw.read_text(encoding="utf-8", errors="replace")
    if '"fetch"' not in swtxt and "'fetch'" not in swtxt:
        errors.append({"file": "sw.js", "kind": "sw-no-fetch",
                       "message": "Service Worker باید یک fetch handler "
                                  "داشته باشد (پیش‌نیاز آفلاین)"})
    # P9-1 — precache resolution: the SW's install-time SHELL/precache list
    # must be a list of REAL, servable URLs (each must return 200). A broken
    # entry makes cache.addAll() reject → SW never activates → no offline.
    m = re.search(r"(?:SHELL|PRECACHE)\s*=\s*\[(.*?)\]", swtxt, re.DOTALL)
    if m:
        urls = re.findall(r"""['"]([^'"]+)['"]""", m.group(1))
        for u in urls:
            if not u or not (u.startswith("./") or u.startswith("/")
                             or u == "./"):
                errors.append({"file": "sw.js", "kind": "precache-invalid-url",
                               "message": f"URL precache نامعتبر: {u}"})
                continue
            code, _ = _fetch(base + "/" + u.lstrip("./").lstrip("/"))
            if code != 200:
                errors.append({"file": "sw.js", "kind": "precache-unresolvable",
                               "message": f"URL precache {u} با HTTP "
                                          f"{code} سرو نمی‌شود؛ cache.addAll "
                                          "نمی‌تواند SW را فعال کند"})
    shell_js = [d / "app.js", d / "index.html"] + sorted(
        p for p in d.glob("*.js") if p.name not in ("sw.js", "app.js"))
    # a REAL registration call — the string "serviceWorker" alone (e.g. a
    # feature check) is not enough (P8-1/B5 proved this gap).
    reg_re = re.compile(r"serviceworker\s*\.\s*register", re.IGNORECASE)
    registered = any(reg_re.search(p.read_text(
        encoding="utf-8", errors="replace"))
        for p in shell_js if p.is_file())
    if not registered:
        errors.append({"file": "index.html", "kind": "sw-not-registered",
                       "message": "هیچ ثبتِ serviceWorker.register در شل "
                                  "(index.html یا JSهای ریشه) یافت نشد"})


def _js_texts(d: Path, p: "_HTML") -> list:
    """R2 — all JS sources of the shell: inline <script> bodies + root *.js."""
    texts = list(p.inline_js)
    for f in sorted(d.glob("*.js")):
        try:
            texts.append(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return texts


def _api_req(base: str, method: str, path: str, body=None):
    """Tiny JSON client for the api-type CRUD contract (no shell, fixed argv)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(base + path, data=data, method=method)
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=HTTP_TIMEOUT) as resp:
            return resp.status, (resp.read() or b"").decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return 0, ""


def check_wordpress(d: Path, errors: list) -> None:
    """AF-47 — real, offline structural contract for a WordPress plugin.

    Runtime compatibility with a real WordPress site is NOT TESTED here: no
    WP core is installed. This checks only what can be proven locally.
    """
    import re
    files = sorted(f for f in d.rglob("*.php") if f.is_file())
    if not files:
        errors.append({"file": "plugin", "kind": "wp-no-php",
                       "message": "هیچ فایل PHP برای افزونه یافت نشد"})
        return
    source = "\n".join(f.read_text(encoding="utf-8", errors="replace")
                       for f in files[:60])
    if not re.search(r"Plugin\s*Name\s*:", source, re.I):
        errors.append({"file": "plugin", "kind": "wp-header",
                       "message": "هدر استاندارد Plugin Name موجود نیست"})
    if not re.search(r"add_shortcode\s*\(", source):
        errors.append({"file": "plugin", "kind": "wp-shortcode",
                       "message": "هیچ shortcodeای ثبت نشد"})
    if not re.search(r"woocommerce", source, re.I):
        errors.append({"file": "plugin", "kind": "wp-woocommerce",
                       "message": "هیچ اتصال واقعی به WooCommerce دیده نشد"})
    if not re.search(r"add_(options|submenu|menu)_page|admin_menu", source, re.I):
        errors.append({"file": "plugin", "kind": "wp-admin-page",
                       "message": "صفحه تنظیمات پیشخوان یافت نشد"})
    if re.search(r"\beval\s*\(|\bshell_exec\s*\(|\bpassthru\s*\(", source, re.I):
        errors.append({"file": "plugin", "kind": "wp-dangerous-code",
                       "message": "اجرای کد خطرناک (eval/shell) در افزونه"})
    if re.search(r"(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,})", source):
        errors.append({"file": "plugin", "kind": "wp-secret",
                       "message": "کلید/رمز صریح در کد افزونه"})


def check_api(base: str, errors: list) -> None:
    """R2 — REAL CRUD/validation contract for api-type products.

    Behaves like a client against the running product: create → list →
    update → read-back → validation(400) → not-found(404) → delete → gone.
    The canonical resource is /api/items (mandated by the type contract in
    core.config.PROJECT_TYPE_SPECS), so any compliant product is testable
    without knowing its domain."""
    code, txt = _api_req(base, "POST", "/api/items", {"name": "r2-probe-alpha"})
    if code not in (200, 201):
        errors.append({"file": "server.py", "kind": "api-crud-create",
                       "message": f"POST /api/items → {code}؛ باید 200/201 و JSON "
                                  "شامل فیلد id باشد (قرارداد: منبع /api/items)"})
        return
    try:
        iid = (json.loads(txt) or {}).get("id")
    except ValueError:
        iid = None
    if iid is None:
        errors.append({"file": "server.py", "kind": "api-crud-schema",
                       "message": "پاسخ POST باید JSON با فیلد id باشد؛ دریافت شد: "
                                  + (txt or "<خالی>")[:120]})
        return
    code, txt = _api_req(base, "GET", "/api/items")
    listed = False
    try:
        arr = json.loads(txt)
        listed = code == 200 and isinstance(arr, list) and any(
            isinstance(x, dict) and x.get("id") == iid for x in arr)
    except ValueError:
        pass
    if not listed:
        errors.append({"file": "server.py", "kind": "api-crud-list",
                       "message": "GET /api/items باید 200 و آرایهٔ JSON شامل "
                                  "آیتم تازه‌ساخته باشد"})
        return
    code, _ = _api_req(base, "PATCH", f"/api/items/{iid}", {"name": "r2-probe-beta"})
    if code not in (200, 204):
        code, _ = _api_req(base, "PUT", f"/api/items/{iid}", {"name": "r2-probe-beta"})
    if code not in (200, 204):
        errors.append({"file": "server.py", "kind": "api-crud-update",
                       "message": f"PUT/PATCH /api/items/{{id}} → {code}؛ "
                                  "پاسخ 200/204 لازم است"})
        return
    code, txt = _api_req(base, "GET", f"/api/items/{iid}")
    updated = False
    try:
        updated = code == 200 and json.loads(txt).get("name") == "r2-probe-beta"
    except ValueError:
        pass
    if not updated:
        errors.append({"file": "server.py", "kind": "api-crud-readback",
                       "message": "پس از PUT/PATCH، GET همان آیتم باید مقدار "
                                  "به‌روزشده را برگرداند"})
    code, _ = _api_req(base, "POST", "/api/items", None)
    if not (400 <= code < 500):
        errors.append({"file": "server.py", "kind": "api-validation",
                       "message": f"POST با بدنهٔ خالی/نامعتبر → {code}؛ پاسخ 400 "
                                  "لازم است (اعتبارسنجی ورودی)"})
    code, _ = _api_req(base, "GET", "/api/items/definitely-missing-r2")
    if code != 404:
        errors.append({"file": "server.py", "kind": "api-notfound",
                       "message": f"GET id ناموجود → {code}؛ پاسخ 404 لازم است"})
    code, _ = _api_req(base, "DELETE", f"/api/items/{iid}")
    if code not in (200, 204):
        errors.append({"file": "server.py", "kind": "api-crud-delete",
                       "message": f"DELETE /api/items/{{id}} → {code}؛ "
                                  "پاسخ 200/204 لازم است"})
        return
    code, _ = _api_req(base, "GET", f"/api/items/{iid}")
    if code != 404:
        errors.append({"file": "server.py", "kind": "api-crud-delete-verify",
                       "message": f"پس از DELETE، GET همان id → {code}؛ "
                                  "باید 404 باشد (حذف واقعی)"})


class _FullstackClient:
    """AF-07 — cookie/CSRF-aware client for fullstack products. The contract
    it tests is read from the REAL generated code (core/fullstack_template):
    af_session cookie, af_csrf double-submit, 401 unauthenticated,
    403 non-admin, owner-isolated customers CRUD.
    AF-21 — template-free products DECLARE their own API surface in
    api-contract.json; the client then honours the declared CSRF mechanism
    (cookie name, or token from the /api/csrf JSON body) instead of the
    template's."""

    def __init__(self, base: str, csrf: dict | None = None):
        self.base = base
        self.jar: dict = {}
        c = csrf or {}
        self.csrf_cookie = c.get("cookie", "af_csrf")
        self.csrf_header = c.get("header") or "X-CSRF-Token"
        self.csrf_token = None
        if c.get("token_key"):
            code, body = self.call("GET", c.get("endpoint") or "/api/csrf")
            self.csrf_token = (body or {}).get(c["token_key"])
        else:
            self.call("GET", "/api/csrf")          # absorb af_csrf cookie

    def call(self, method: str, path: str, body=None) -> tuple[int, dict]:
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        token = (self.csrf_token if self.csrf_token is not None
                 else self.jar.get(self.csrf_cookie))
        if token and method != "GET":
            headers[self.csrf_header] = token
        if self.jar:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.jar.items())
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data,
                                     method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                code, raw = resp.status, resp.read()
                setc = resp.headers.get_all("Set-Cookie")
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read()
            setc = e.headers.get_all("Set-Cookie")
        except (urllib.error.URLError, OSError):
            return 0, {}
        for c in setc or []:
            name, _, val = c.split(";")[0].partition("=")
            name, val = name.strip(), val.strip()
            if name and val:
                self.jar[name] = val
        try:
            return code, json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return code, {}


_API_CONTRACT_FILE = "api-contract.json"


def _api_contract(d) -> dict:
    """AF-21 — a template-free fullstack product DECLARES its own API
    surface (api-contract.json, written by the model during generation).
    Template products have no manifest → the template contract (the
    historical, regression-proven shape) stays the default."""
    f = d / _API_CONTRACT_FILE
    if not f.is_file():
        return {}
    try:
        m = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def check_fullstack(base: str, errors: list, tag: str,
                    contract: dict | None = None) -> None:
    """AF-07 — real behavioural contract for fullstack products, exercised
    over live HTTP against the running product:
      * unauthenticated access refused (401) on user/admin resources
      * valid login works; wrong password refused (401)
      * first registered user is admin, second is a normal user
      * admin/user separation (user on admin endpoint → 403)
      * real CRUD (create/list/update/read-back/delete/verify-gone)
      * per-user data isolation (another user never sees the record)
    AF-21 — the API SURFACE (endpoint paths, response item/list keys,
    status codes, update verb, CSRF mechanism) comes from the product's
    own declared api-contract.json when present; the default is the
    template's shape (regression-proven). The BEHAVIOURAL requirements
    (401/403 enforcement, admin separation, isolation, validation,
    real CRUD) are enforced regardless — an independent design must
    satisfy the same behaviour, not copy the template's URLs."""
    contract = contract or {}
    ep = contract.get("endpoints") or {}
    stc = contract.get("status") or {}
    ent = contract.get("entity") or {}
    REG = ep.get("register") or "/api/auth/register"
    LOGIN = ep.get("login") or "/api/auth/login"
    ME = ep.get("me") or "/api/me"
    ADM = ep.get("admin_users") or "/api/admin/users"
    E = ent.get("path") or "/api/customers"
    LISTK = ent.get("list_key") or "customers"
    ITEMK = ent.get("item_key") or "customer"
    IDK = ent.get("id_key") or "id"
    SAMPLE = ent.get("sample") or {"name": "آزمون AF07", "phone": "021-0000"}
    UPD_M = ent.get("update_method") or "POST"
    # AF-21: the template routes updates as POST …/update; RESTful
    # products (PUT/DELETE on the resource) have no suffix. Only default
    # to the template's verb-suffix for the template's own verb.
    UPD_SFX = ent.get("update_path_suffix",
                      "/update" if UPD_M == "POST" else "")
    UPD_BODY = ent.get("update_body") or {"name": "آزمون AF07 نوین",
                                          "phone": "021-1111"}
    INVALID = ent.get("invalid_body") or {"name": "شرکت", "phone": "بدون-شکل!!"}
    REG_CODE = stc.get("register", 201)
    ME_ANON = stc.get("me_anon", 401)
    CREATE_CODE = stc.get("create", 201)
    DEL_CODE = stc.get("delete", 200)
    UPD_FIELD = next(iter(UPD_BODY), None)

    def err(kind: str, message: str) -> None:
        errors.append({"file": "server.py", "kind": kind, "message": message})

    u_admin = f"afqadmin{tag}"
    u_user = f"afquser{tag}"
    u_third = f"afqthird{tag}"
    csrf_cfg = contract.get("csrf") or {}
    c_admin, c_user, c_other = (_FullstackClient(base, csrf_cfg)
                                for _ in range(3))

    # ۱) بدون احراز — منابع محافظت‌شده باید 401 بدهند (contract: _OK_AUTH)
    code, _ = c_admin.call("GET", ME)
    if code != ME_ANON:
        err("fs-auth-required", f"GET {ME} بدون احراز → {code}؛ {ME_ANON} لازم است")
    code, _ = c_admin.call("GET", E)
    if code != 401:
        err("fs-auth-required", f"GET {E} بدون احراز → {code}؛ 401 لازم است")
    code, _ = c_admin.call("GET", ADM)
    if code != 401:
        err("fs-admin-anon", f"GET {ADM} بدون احراز → {code}؛ 401 لازم است")

    # ۲) ثبت‌نام — اولین کاربر db تازه مدیر است (contract: auth.register)
    code, d = c_admin.call("POST", REG,
                           {"username": u_admin, "password": "afq-Pass-1"})
    role1 = ((d or {}).get("user") or {}).get("role")
    if code != REG_CODE or not role1:
        err("fs-register", f"register {REG} → {code}؛ {REG_CODE} و role لازم است")
    is_admin = role1 == "admin"

    # ۳) ورود معتبر / نامعتبر
    code, _ = c_admin.call("POST", LOGIN,
                           {"username": u_admin, "password": "afq-WRONG-0"})
    if code != 401:
        err("fs-login-wrong", f"login با رمز اشتباه → {code}؛ 401 لازم است")
    code, _ = c_admin.call("POST", LOGIN,
                           {"username": u_admin, "password": "afq-Pass-1"})
    if code != 200:
        err("fs-login", f"login معتبر → {code}؛ 200 لازم است")
    code, d = c_admin.call("GET", ME)
    if code != 200 or ((d or {}).get("user") or {}).get("username") != u_admin:
        err("fs-me", f"GET {ME} پس از ورود → {code}")

    # ۴) تفکیک مدیر/کاربر عادی
    code, d = c_user.call("POST", REG,
                          {"username": u_user, "password": "afq-Pass-2"})
    role2 = ((d or {}).get("user") or {}).get("role")
    if is_admin and role2 != "user":
        err("fs-roles", f"کاربر دوم باید role=user باشد؛ {role2}")
    code, _ = c_user.call("GET", ADM)
    if code != 403:
        err("fs-admin-user", f"کاربر عادی روی {ADM} → {code}؛ 403 لازم است")
    if is_admin:
        code, d = c_admin.call("GET", ADM)
        if code != 200 or not (d or {}).get("users"):
            err("fs-admin-ok", f"مدیر روی {ADM} → {code}؛ 200 لازم است")

    # ۵) CRUD واقعی + جداسازی دادهٔ کاربران
    code, d = c_user.call("POST", E, SAMPLE)
    row = (d or {}).get(ITEMK) or {}
    cid = row.get(IDK)
    if code != CREATE_CODE or not cid:
        err("fs-crud-create", f"POST {E} → {code}؛ {CREATE_CODE} لازم است")
        return
    code, d = c_user.call("GET", E)
    if not any(x.get(IDK) == cid for x in (d or {}).get(LISTK, [])):
        err("fs-crud-list", "آیتم تازه‌ساخته در لیست صاحبش نیست")
    # جداسازی: کاربر دیگر نباید رکورد این کاربر را ببیند
    c_other.call("POST", REG,
                 {"username": u_third, "password": "afq-Pass-3"})
    code, d = c_other.call("GET", E)
    if code != 200 or any(x.get(IDK) == cid
                          for x in (d or {}).get(LISTK, [])):
        err("fs-isolation", "رکورد کاربر نزد کاربر دیگر دیده شد — جداسازی نقض شد")
    code, _ = c_user.call(UPD_M, f"{E}/{cid}{UPD_SFX}", UPD_BODY)
    if code != 200:
        err("fs-crud-update", f"update → {code}؛ 200 لازم است")
    code, d = c_user.call("GET", E)
    got = [x for x in (d or {}).get(LISTK, []) if x.get(IDK) == cid]
    if not got or got[0].get(UPD_FIELD) != UPD_BODY[UPD_FIELD]:
        err("fs-crud-readback", "مقدار به‌روزشده خوانده نشد")
    code, _ = c_user.call("POST", E, INVALID)
    if code != 400:
        err("fs-validation", f"ورودی نامعتبر → {code}؛ 400 لازم است")
    code, _ = c_user.call("DELETE", f"{E}/{cid}", {})
    if code != DEL_CODE:
        err("fs-crud-delete", f"DELETE → {code}؛ {DEL_CODE} لازم است")
    code, d = c_user.call("GET", E)
    if any(x.get(IDK) == cid for x in (d or {}).get(LISTK, [])):
        err("fs-crud-delete-verify", "رکورد پس از حذف هنوز هست")


def _product_type(d: Path) -> str:
    try:
        from core import factory
        return factory.read_meta(d).get("type", "")
    except Exception:                       # noqa: BLE001 (no meta → generic)
        return ""


def check_static(d: Path, html: str, kind: str, errors: list) -> None:
    """Behavioural contract for a booted static product."""
    if not html.strip():
        errors.append({"file": "index.html", "kind": "empty-page",
                       "message": "صفحهٔ اصلی خالی یا غیرقابل دریافت است"})
        return
    p = _HTML()
    p.feed(html)

    if "{{" in html:
        errors.append({"file": "index.html", "kind": "placeholder-left",
                       "message": "قالب پرنشده ({{...}}) در صفحه باقی مانده است"})
    if not p.title.strip():
        errors.append({"file": "index.html", "kind": "contract-title",
                       "message": "قرارداد تست: <title> خالی/غایب است؛ "
                                  "عنوان فارسی صفحه الزامی است"})
    if p.h1 < 1:
        errors.append({"file": "index.html", "kind": "contract-h1",
                       "message": "قرارداد تست: حداقل یک <h1> لازم است"})
    if kind == "shop":
        if p.product_cards < 1:
            errors.append({"file": "index.html", "kind": "contract-product",
                           "message": "قرارداد تست: حداقل یک عنصر با ویژگی "
                                      "data-product (کارت محصول) لازم است"})
        if p.add_controls < 1:
            errors.append({"file": "index.html", "kind": "contract-add",
                           "message": "قرارداد تست: حداقل یک دکمه/لینک با ویژگی "
                                      "data-add (افزودن به سبد) لازم است"})
    if kind in ("webtool",) and (p.form_controls + p.buttons) < 1:
        errors.append({"file": "index.html", "kind": "contract-interactive",
                       "message": "قرارداد تست: ابزار تحت‌وب باید حداقل یک فرم "
                                  "یا دکمهٔ تعاملی داشته باشد"})

    # ---- R2: real-functionality contracts (no page-only pass) ------------
    if kind in ("webapp", "dashboard") and (p.form_controls + p.buttons) < 1:
        errors.append({"file": "index.html", "kind": "contract-interactive",
                       "message": "قرارداد تست: این نوع باید حداقل یک فرم/ورودی "
                                  "یا دکمهٔ تعاملی داشته باشد"})

    js = _js_texts(d, p) if kind in ("webtool", "webapp", "dashboard") else []

    if kind == "dashboard":
        cards = p.classes & {"stat", "stats", "card", "cards", "kpi", "metric"}
        if p.tables < 1 and p.charts == 0 and not cards:
            errors.append({"file": "index.html", "kind": "contract-dashboard-data",
                           "message": "قرارداد تست: داشبورد باید دادهٔ واقعی داشته "
                                      "باشد — جدول (<table>) یا کارت‌های آماری"})
        if p.charts < 1:
            errors.append({"file": "index.html", "kind": "contract-dashboard-chart",
                           "message": "قرارداد تست: داشبورد باید حداقل یک نمودار "
                                      "داشته باشد (SVG/Canvas/CSS — بدون کتابخانهٔ "
                                      "خارجی)"})
        if p.scripts < 1:
            errors.append({"file": "index.html", "kind": "contract-dashboard-js",
                           "message": "قرارداد تست: داشبورد باید فایل/اسکریپت JS "
                                      "اپ داشته باشد"})
        if not any(("addEventListener" in t or "querySelector" in t
                    or "fetch(" in t) for t in js):
            errors.append({"file": "app.js", "kind": "contract-dashboard-logic",
                           "message": "قرارداد تست: JS داشبورد باید داده را "
                                      "رندر/فیلتر کند (منطق واقعی)"})

    if kind == "webapp":
        if p.scripts < 1:
            errors.append({"file": "index.html", "kind": "contract-webapp-js",
                           "message": "قرارداد تست: وب‌اپ باید فایل/اسکریپت JS "
                                      "اپ داشته باشد"})
        ev = sum(t.count("addEventListener") for t in js)
        storage = any(("localStorage" in t or "sessionStorage" in t) for t in js)
        if ev < 1 or (ev < 2 and not storage):
            errors.append({"file": "app.js", "kind": "contract-webapp-logic",
                           "message": "قرارداد تست: وب‌اپ باید منطق وضعیت واقعی "
                                      "داشته باشد (addEventListener + ذخیرهٔ وضعیت "
                                      "یا چند مسیر تعامل) — صفحهٔ صرف پذیرفته نیست"})

    if kind == "webtool":
        if not any("addEventListener" in t for t in js):
            errors.append({"file": "app.js", "kind": "contract-tool-logic",
                           "message": "قرارداد تست: منطق ابزار باید در JS اپ "
                                      "پیاده‌سازی شود (addEventListener)"})
        if not (("output" in p.ids) or any("result" in c for c in p.classes)):
            errors.append({"file": "index.html", "kind": "contract-tool-output",
                           "message": "قرارداد تست: ابزار باید عنصر خروجی داشته "
                                      "باشد (id=\"output\" یا کلاس result) که با "
                                      "نتیجهٔ کار به‌روز می‌شود"})

    broken = sorted({r for r in p.assets if _resolve_asset(d, r) is None})
    if broken:
        errors.append({"file": "index.html", "kind": "asset-missing",
                       "message": "فایل‌های ارجاعی در پروژه وجود ندارند: "
                                  + ", ".join(broken[:6])})


def probe_service(port: int, errors: list, need_ai_html: bool = False) -> None:
    """Behavioural contract for service products (api/ai-tool/fullstack)."""
    base = f"http://127.0.0.1:{port}"
    code, txt = _fetch(base + "/api/health")
    healthy = False
    if code == 0:
        errors.append({"file": "server.py", "kind": "process-dead",
                       "message": "سرور پروژه پاسخ نداد (پورت بسته است)"})
        return
    if code != 200:
        errors.append({"file": "server.py", "kind": "health-non200",
                       "message": f"GET /api/health → {code}؛ پاسخ 200 لازم است"})
        return
    try:
        payload = json.loads(txt)
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        errors.append({"file": "server.py", "kind": "health-schema",
                       "message": "پاسخ /api/health باید JSON با "
                                  "\"ok\": true باشد؛ دریافت شد: "
                                  + (txt or "<خالی>")[:120]})
    else:
        healthy = True
    if healthy and need_ai_html:
        c2, _ = _fetch(base + "/ai.html")
        if c2 != 200:
            errors.append({"file": "ui/ai.html", "kind": "ai-page-missing",
                           "message": "صفحهٔ تنظیمات AI (/ai.html) سرو نشد — "
                                      "هوک ماژول AI ناقص است"})


def run(project: str, timeout_boot: float = BOOT_WAIT_S) -> dict:
    """Run contract tests for *project*; uses the real runner lifecycle."""
    d = paths.project_dir(project)                 # name validated here
    from core import runner
    kind_meta = _product_type(d)
    # AF-47 — WordPress plugins have no standalone server to boot. Verify all
    # that CAN be verified locally; WP runtime compatibility stays NOT TESTED.
    if kind_meta == "wordpress":
        errors: list = []
        check_wordpress(d, errors)
        return {"ran": True, "ok": not errors, "no_coverage": False,
                "note": "قرارداد ساختاری افزونه WordPress (runtime NOT TESTED)",
                "errors": errors}
    st = runner.status(project)
    started_here = False
    try:
        if not st.get("running"):
            try:
                runner.start(project)
            except RuntimeError as exc:
                return {"ran": False, "ok": False, "no_coverage": True,
                        "note": f"{_NO_COVER} — پروژه قابل اجرا نیست: {exc}",
                        "errors": []}
            st = runner.status(project)
            started_here = True
        port = st.get("port")
        kind = st.get("kind", "")
        if not port:
            return {"ran": True, "ok": False, "no_coverage": True,
                    "note": _NO_COVER + " (پورت پیش‌نمایش آزاد نشد)",
                    "errors": []}
        base = f"http://127.0.0.1:{port}"

        deadline = time.time() + timeout_boot
        alive = False
        while time.time() < deadline:
            code, _ = _fetch(base + "/")
            if code != 0:
                alive = True
                break
            time.sleep(0.7)
        if not alive:
            return {"ran": True, "ok": False, "no_coverage": True,
                    "note": _NO_COVER + f" — سرور بوت نشد "
                    "(لاگ: logs/runs/{0}.log)".format(project),
                    "errors": []}

        errors: list = []
        if kind == "static-web":
            _, html = _fetch(base + "/")
            effective = kind_meta if kind_meta in (
                "webpage", "webtool", "shop", "dashboard", "webapp", "pwa") else "webpage"
            check_static(d, html, effective, errors)
            if kind_meta == "pwa":
                check_pwa(d, base, errors)
        elif kind in ("python", "node"):
            if kind_meta.startswith("fullstack"):
                # AF-07: fullstack products follow the REAL template contract
                # — the generated server has no /api/health route; its
                # liveness+schema endpoint is /api/csrf ({"ok": true}).
                code, txt = _fetch(base + "/api/csrf")
                if code == 0:
                    errors.append({"file": "server.py", "kind": "process-dead",
                                   "message": "سرور پروژه پاسخ نداد "
                                              "(پورت بسته است)"})
                elif code != 200:
                    errors.append({"file": "server.py", "kind": "health-non200",
                                   "message": f"GET /api/csrf → {code}؛ "
                                              "پاسخ 200 لازم است"})
                else:
                    ok_json = False
                    try:
                        ok_json = json.loads(txt).get("ok") is True
                    except ValueError:
                        pass
                    if not ok_json:
                        errors.append({"file": "server.py",
                                       "kind": "health-schema",
                                       "message": "پاسخ /api/csrf باید JSON "
                                                  "با \"ok\": true باشد"})
                    else:
                        # AF-21: template-free products declare their own
                        # API surface (api-contract.json) — the contract
                        # drives the DECLARED surface, behaviour stays.
                        check_fullstack(base, errors,
                                        str(int(time.time())),
                                        contract=_api_contract(d))
            else:
                probe_service(port, errors,
                              need_ai_html=(kind_meta == "ai-tool"))
                if kind_meta == "api":
                    check_api(base, errors)
        elif kind == "php":
            # AF-47 — a real PHP process was booted; the root must serve HTML.
            code, txt = _fetch(base + "/")
            if code != 200 or not txt.strip():
                errors.append({"file": "index.php", "kind": "php-no-response",
                               "message": f"GET / → {code}؛ پاسخ HTML لازم است"})
        else:
            return {"ran": True, "ok": False, "no_coverage": True,
                    "note": _NO_COVER + f" (نوع {kind} قرارداد اجرایی ندارد)",
                    "errors": []}
        return {"ran": True, "ok": not errors, "no_coverage": False,
                "note": "تست قرارداد عملکردی «نوع "
                        + (kind_meta or kind) + "» اجرا شد",
                "errors": errors}
    finally:
        if started_here:
            runner.stop(project)
