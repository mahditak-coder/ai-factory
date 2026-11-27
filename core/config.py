"""AI Factory — central configuration.

All paths are resolved relative to the project root so the app can never
escape the AI-Factory directory. Listening is restricted to localhost.
"""

import os
from pathlib import Path

APP_NAME = "AI Factory"
# Version tracks the AF remediation phase (AF-47 = 0.47.0). This is the single
# source of truth for /api/health, /api/status and the dashboard footer.
APP_VERSION = "0.47.0"

# AF-26 — an isolated root for reproducible, non-destructive tests.  When this
# variable is set BEFORE the process imports core, every derived path (data/,
# projects/, logs/, exports/, ...) points into that temporary tree.  Normal
# operation (variable absent) is unchanged.
_TEST_ROOT = os.environ.get("AI_FACTORY_TEST_ROOT") or ""
PROJECT_ROOT = Path(_TEST_ROOT).resolve() if _TEST_ROOT else Path(__file__).resolve().parent.parent

# Directory layout
CORE_DIR = PROJECT_ROOT / "core"
DASHBOARD_DIR = PROJECT_ROOT / "dashboard"
PROJECTS_DIR = PROJECT_ROOT / "projects"
PROVIDERS_DIR = PROJECT_ROOT / "providers"
AGENTS_DIR = PROJECT_ROOT / "agents"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
EXPORTS_DIR = PROJECT_ROOT / "exports"
LOGS_DIR = PROJECT_ROOT / "logs"
DOCS_DIR = PROJECT_ROOT / "docs"

# Server
HOST = "127.0.0.1"          # localhost only — never bind to 0.0.0.0
PORT = 8580

# OpenCode bridge (v2.2.6 HTTP API, /api/* routes)
OC_HOST = "127.0.0.1"
OC_PORT = 4556
OC_BASE_URL = f"http://{OC_HOST}:{OC_PORT}"
OC_SERVE_LOG = LOGS_DIR / "opencode-serve.log"
OC_PID_FILE = LOGS_DIR / "opencode.pid"
# Backend-only Basic-auth password for the OpenCode server (username: "opencode").
# Never sent to or read by the browser.
OC_TOKEN_FILE = LOGS_DIR / "opencode.token"

# Files
PID_FILE = LOGS_DIR / "server.pid"
LOG_FILE = LOGS_DIR / "server.log"
ERROR_LOG_FILE = LOGS_DIR / "error.log"
STDOUT_LOG_FILE = LOGS_DIR / "stdout.log"
MODELS_CONFIG = PROVIDERS_DIR / "models.json"

# Security limits
MAX_BODY_BYTES = 2 * 1024 * 1024        # 2 MB max request body
MAX_PROJECT_NAME_LEN = 64
PROJECT_NAME_RE = r"^[A-Za-z0-9_\-\u0600-\u06FF ]{1,64}$"

# Allowed text file extensions for reading/writing via the panel
TEXT_EXTENSIONS = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".json", ".jsonc", ".md", ".txt",
    ".sh", ".bash", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf",
    ".html", ".css", ".scss", ".csv", ".sql", ".env", ".log", ".xml",
    ".c", ".cpp", ".h", ".java", ".kt", ".go", ".rs", ".rb", ".php",
}

STATIC_EXTENSIONS = {".html", ".css", ".js", ".svg", ".png", ".ico"}

# ---- Factory (v0.3.0) --------------------------------------------------------

# Engine backups (before auto-fixes / destructive ops) — outside projects/ so
# they can never be served or zipped as part of a user project.
BACKUPS_DIR = PROJECT_ROOT / "backups"
# Logs of running project processes, one file per project.
RUN_LOGS_DIR = LOGS_DIR / "runs"
# Preview ports bound to 127.0.0.1 only (panel is 8580, OpenCode is 4556).
RUN_PORT_RANGE = range(8610, 8659)
# Auto-fix loop guard
FIX_MAX_ATTEMPTS = 3
# ── R4-a: SINGLE SOURCE OF TRUTH for project types ────────────────────────
# The ONLY human-edited registry. Every consumer derives from it:
#   core/factory.py  → TYPES / TYPE_REQUIREMENTS / FULLSTACK_TYPES / FS_REQUIREMENT
#   core/server.py   → GET /api/factory/types (behind the same panel gate)
#   dashboard/*.js   → fetch("/api/factory/types") — no hardcoded copy
# FROZEN: ids AND order (meta.json of existing projects, tests and the UI
# depend on stability). Add new types ONLY at the end — never rename.
PROJECT_TYPE_SPECS = {
    'webpage': {
        "engine_label": 'وب\u200cسایت HTML/CSS/JS',
        "panel_label": 'وب\u200cسایت HTML/CSS/JS',
        "contract": '\nالزامات قرارداد تست (الزامی — صفحهٔ وب استاتیک): صفحهٔ اصلی باید <title> فارسی، یک <h1> و حداقل یک <a href="..."> یا <button> داشته باشد؛ همهٔ فایل\u200cهای ارجاع\u200cشده (href/src) باید داخل پروژه واقعاً وجود داشته باشند؛ هیچ {{placeholder}} یا «...» در خروجی نماند.',
        "scaffold": False,
    },
    'webapp': {
        "engine_label": 'وب\u200cاپلیکیشن',
        "panel_label": 'وب\u200cاپلیکیشن',
        "contract": '\nالزامات قرارداد تست (الزامی — وب\u200cاپ): اپ تعاملی واقعی، نه صفحهٔ صرف: حداقل یک فرم/ورودی و دکمه؛ منطق وضعیت در فایل JS اپ با addEventListener؛ ذخیرهٔ وضعیت در localStorage یا sessionStorage یا مدیریت وضعیت درون\u200cصفحه\u200cای با به\u200cروزرسانی DOM پس از هر تغییر (لیست/خروجی/نتیجه)؛ <title> فارسی، <h1> و RTL الزامی است؛ فایل\u200cهای ارجاعی موجود؛ هیچ placeholder نمانَد.',
        "scaffold": False,
    },
    'pwa': {
        "engine_label": 'اپلیکیشن نصب\u200cشونده (PWA)',
        "panel_label": 'اپلیکیشن نصب\u200cشونده (PWA) — قالب آماده',
        "contract": '\nالزامات قرارداد تست (الزامی — PWA قابل نصب): فایل manifest.json معتبر با name، short_name، start_url، display و آیکون\u200cهای PNG واقعی 192 و 512 پیکسل؛ فایل sw.js (Service Worker با fetch handler) که واقعاً ثبت می\u200cشود (serviceWorker.register در کد شل)؛ قانون طلایی: manifest.json، sw.js و پوشهٔ icons را حذف یا خراب نکن — فقط محتوای اپ را در index.html/style.css/app.js کامل و بهتر کن؛ index.html باید <title> فارسی، <h1> و حداقل یک دکمه/فرم تعاملی داشته باشد؛ همهٔ فایل\u200cهای ارجاعی داخل پروژه موجود باشند و هیچ placeholder نمانَد.',
        "scaffold": True,
    },
    'shop': {
        "engine_label": 'فروشگاه اینترنتی',
        "panel_label": 'فروشگاه اینترنتی',
        "contract": '\nالزامات قرارداد تست (الزامی — فروشگاه): صفحهٔ اصلی باید حداقل یک عنصر با ویژگی data-product (کارت محصول) و حداقل یک دکمه/لینک با ویژگی data-add (افزودن به سبد) داشته باشد؛ <title> فارسی و <h1> اجباری است؛ تصاویر/فایل\u200cهای ارجاعی باید داخل پروژه موجود باشند.',
        "scaffold": False,
    },
    'dashboard': {
        "engine_label": 'داشبورد مدیریتی',
        "panel_label": 'داشبورد مدیریتی',
        "contract": '\nالزامات قرارداد تست (الزامی — داشبورد): index.html با <title> فارسی و <h1>؛ حداقل یک جدول دادهٔ واقعی (<table> با ردیف\u200cهای نمونه) یا کارت\u200cهای آماری؛ حداقل یک نمودار با SVG یا Canvas یا نوارهای CSS (بدون کتابخانهٔ خارجی)؛ حداقل یک فرم/فیلتر تعاملی (input/select)؛ فایل JS اپ که داده را رندر یا فیلتر می\u200cکند؛ RTL فارسی؛ فایل\u200cهای ارجاعی موجود؛ هیچ placeholder نمانَد.',
        "scaffold": False,
    },
    'webtool': {
        "engine_label": 'ابزار تحت وب',
        "panel_label": 'ابزار تحت وب',
        "contract": '\nالزامات قرارداد تست (الزامی — ابزار تحت وب): باید یک <form> یا دکمهٔ اصلی تعاملی داشته باشد؛ منطق واقعی ابزار در فایل JS اپ (addEventListener) پیاده\u200cسازی شود و نتیجهٔ کار در DOM (مثلاً عنصر با id="output" یا کلاس "result") به\u200cروز شود؛ همهٔ فایل\u200cهای ارجاعی داخل پروژه موجود باشند؛ <title> فارسی، <h1> و RTL الزامی است؛ index.html ریشه اجباری است؛ هیچ placeholder نمانَد.',
        "scaffold": False,
    },
    'api': {
        "engine_label": 'API و بک\u200cاند',
        "panel_label": 'API و بک\u200cاند',
        "contract": '\nالزامات قرارداد تست (الزامی — API): فایل server.py در ریشه که پورت را از env PORT می\u200cخواند و روی 127.0.0.1 bind می\u200cکند؛ GET /api/health باید JSON با "ok": true برگرداند؛ منبع CRUD دقیقاً روی مسیر /api/items: GET /api/items → آرایهٔ JSON؛ POST /api/items با بدنهٔ JSON {"name": "..."} → 200/201 و JSON با فیلد id؛ PUT یا PATCH /api/items/{id} → 200/204؛ DELETE /api/items/{id} → 200/204 و پس از آن GET همان id → 404؛ POST با بدنهٔ خالی/نامعتبر → 400؛ GET id ناموجود → 404؛ فقط کتابخانهٔ استاندارد پایتون؛ tests/test_api.py با unittest برای همین مسیرها.',
        "scaffold": False,
    },
    'ai-tool': {
        "engine_label": 'ابزار هوش مصنوعی',
        "panel_label": 'ابزار هوش مصنوعی',
        "contract": '\nالزامات قرارداد تست (الزامی — ابزار AI): server.py در ریشه (پورت از env PORT، bind 127.0.0.1)؛ GET /api/health → JSON با "ok": true؛ صفحهٔ /ai.html ماژول AI باید سرو شود (هوک ۳ خطی /app/ai/ و /ai.html طبق README-ai)؛ وضعیت AI در config.json با enabled:false شروع شود.',
        "scaffold": False,
    },
    'fullstack': {
        "engine_label": 'فول\u200cاستک',
        "panel_label": 'فول\u200cاستک',
        "contract": None,
        "scaffold": False,
    },
    'fullstack-python': {
        "engine_label": 'فول\u200cاستک پایتون + SQLite',
        "panel_label": 'فول\u200cاستک پایتون + SQLite (قالب آماده)',
        "contract": None,
        "scaffold": True,
    },
    # AF-03: موتور اندروید — ساخت با core/engines/android.py (چیدمان minimal)
    # قرار دادِ این نوع، هم برای تولید توسط مدل و هم اعتبارسنجی موتور است.
    'android': {
        "engine_label": 'اپلیکیشن اندروید (APK/AAB)',
        "panel_label": 'اپلیکیشن اندروید (APK/AAB)',
        "contract": '\nالزامات قرارداد تست (الزامی — اپ اندروید چیدمان minimal، بدون Gradle): AndroidManifest.xml در ریشه با ویژگی package و یک Activity دارای intent-filter LAUNCHER؛ پوشهٔ res/values/strings.xml با app_name فارسی؛ پوشهٔ java/ با کد کامل Activity (بدون placeholder، بدون وابستگی خارجی، بدون مجوز اینترنت مگر ضرورت صریح)؛ ساخت نهایی توسط موتور اندروید AI Factory انجام می‌شود (aapt2/javac/d8/apksigner) — فایل‌های Gradle و Android SDK کامل را نساز.',
        "scaffold": False,
    },
    # AF-47 — PHP/WordPress first-class: real php -l + plugin structure.
    'wordpress': {
        "engine_label": 'افزونه WordPress / WooCommerce',
        "panel_label": 'افزونه WordPress / WooCommerce',
        "contract": '\nالزامات قرارداد تست (الزامی — افزونه WordPress): '
                    'فایل اصلی PHP با هدر استاندارد Plugin Name؛ ساختارIncludes/Admin/Frontend '
                    'داخل پوشهای به نام افزونه؛ shortcode واقعی با add_shortcode؛ '
                    'صفحه تنظیمات پیشخوان با add_options_page/submenu_page؛ '
                    'هماهنگی واقعی با WooCommerce (hookهای woocommerce_*)؛ '
                    'تمام فایلهای PHP بدون خطای php -l؛ بدون eval/shell/clid و بدون '
                    'رمز یا کلید متن صریح. اجرای real WordPress در محیط فعلی NOT TESTED '
                    'است؛ سازگاری اجرایی ادعا نشود.',
        "scaffold": False,
    },
    'php': {
        "engine_label": 'سرویس PHP',
        "panel_label": 'سرویس PHP',
        "contract": '\nالزامات قرارداد تست (الزامی — PHP): index.php یا server.php '
                    'در ریشه؛ تمام فایلهای PHP با php -l سبز؛ سرو توسط سرور داخلی PHP '
                    'built-in server روی 127.0.0.1 و پورت محیط؛ بدون رمز متن صریح.',
        "scaffold": False,
    },
    # AF-13: سرویس Node.js واقعی — قالب آمادهٔ stdlib-only (هیچ وابستگی خارجی)
    'node-server': {
        "engine_label": 'سرویس Node.js',
        "panel_label": 'سرویس Node.js (قالب آماده)',
        "contract": None,
        "scaffold": True,
    },
}

# Compat: ordered id list (was a dead literal; now derived so it can never
# drift from the specs above)
PROJECT_TYPES = list(PROJECT_TYPE_SPECS)

FULLSTACK_TYPES = ("fullstack", "fullstack-python")

# Shared fullstack build contract — appended by core/factory.py on top of
# any per-type contract for fullstack / fullstack-python.
FS_REQUIREMENT = '\nالزام نهایی این درخواست (فول\u200cاستک): یک server.py کامل در ریشه پروژه که پورت را از env AI_FACTORY_PORT می\u200cخواند؛ رابط کاربری (ui/index.html دارای <html> و </html>) توسط همان سرور سرو شود؛ پایگاه داده SQLite در data/app.db با کوئری پارامتری؛ API با پاسخ JSON؛ ثبت\u200cنام/ورود با هش PBKDF2 و نشست امن؛ نقش\u200cهای user و admin با چک سمت سرور؛ پنل مدیریت محافظت\u200cشده؛ CRUD یک موجودیت واقعی؛ فایل tests/test_api.py با unittest؛ هیچ رمز یا توکنی داخل کد قرار نده.\n'

# Never include these in exports/backups of a project
EXPORT_EXCLUDE_NAMES = {".factory", "__pycache__", "node_modules", ".git", ".gitignore.bin",
                        ".env", "app.db", "app.db-wal", "app.db-shm",
                        "opencode.json",   # OpenCode dev-time tool config, not app source
                        "server.out"}      # runner stdout capture (runtime)
EXPORT_EXCLUDE_SUFFIXES = {".pyc", ".log",   # logs are runtime, not source
                           ".db", ".db-wal", ".db-shm",  # db = runtime data (hashes)
                           ".pid",           # runner/process runtime artifact
                           ".out",           # runtime stdout captures
                           ".err"}           # runtime stderr captures
# Never include these even if present (secrets)
EXPORT_SECRET_SUFFIXES = {".token", ".pem", ".key"}
EXPORT_SECRET_NAMES = {".env",
                       # P3 AI admin private files (key / passcode hash store):
                       "ai_key.txt", "ai_admin.json",
                       # P6-2 panel credential store (PBKDF2 hash, 0600):
                       "panel_admin.json"}
