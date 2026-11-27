# AI Factory

محیط توسعه نرم‌افزار هوشمند روی Termux (اندروید) — بدون Docker، بدون وابستگی خارجی اضافی.

## چیدمان

| مسیر | شرح |
|---|---|
| `core/` | هسته اصلی — سرور محلی پایتون (فقط استاندارد لایب‌ری) + موتور کارخانه (factory/checker/runner/exporter) |
| `projects/` | پروژه‌های کاربر (هر پوشه = یک پروژه) |
| `dashboard/` | پنل مدیریت (فارسی، RTL، موبایل‌پسند) + صفحه کارخانه `factory.html` |
| `providers/` | پیکربندی مدل/API فعال |
| `agents/` | عامل‌های هوش مصنوعی |
| `scripts/` | `ai-factory-start`, `ai-factory-stop`, `ai-factory-status`, `ai-factory-test`, `ai-factory-test-oc`, `ai-factory-test-factory`, `ai-factory-test-fullstack` |
| `exports/` | خروجی کارخانه (ZIP پروژه‌ها) |
| `backups/` | نسخه پشتیبان خودکار (پیش از اصلاح/حذف) — بیرون از projects/ |
| `logs/` | `server.log`, `error.log`, `stdout.log`, `server.pid`, و لاگ‌های اجرای پروژه‌ها در `logs/runs/` |
| `docs/` | مستندات (`FACTORY.md` برای کارخانه، `FULLSTACK.md` برای قالب Full-Stack) |

## اجرا

```bash
bash scripts/ai-factory-start          # شروع سرور
bash scripts/ai-factory-status         # وضعیت
bash scripts/ai-factory-stop           # توقف
bash scripts/ai-factory-test           # تست‌های پنل v0.1.0 (۱۵ تست)
bash scripts/ai-factory-test-oc        # تست‌های پذیرش AI Workspace و پل OpenCode (۲۴ تست)
bash scripts/ai-factory-test-factory   # تست‌های پذیرش کارخانه v0.3.0 (۲۵ بررسی، هم‌راستا با مدل واقعی)
bash scripts/ai-factory-test-fullstack # تست‌های پذیرش Full-Stack v0.4.0 (۲۵ بررسی واقعی)
```

پنل در مرورگر گوشی: **http://127.0.0.1:8580**
کارخانه تولید نرم‌افزار: **http://127.0.0.1:8580/factory.html** — مستندات کامل: `docs/FACTORY.md`
قالب Full-Stack (بک‌اند پایتون + SQLite + ثبت‌نام/ورود + پنل مدیر): `docs/FULLSTACK.md`

## چرخه کارخانه (خلاصه)

درخواست ← برنامه‌ریزی مدل ← تأیید کاربر ← تولید فایل واقعی ← اجرای امن ← بررسی خطا ← اصلاح خودکار (حداکثر ۳ دور، با پشتیبان) ← پیش‌نمایش ← ZIP بدون محرمانه‌ها.

## v0.47.0 — وضعیت فعلی (AF-47 / RELEASE-01)

نسخهٔ اعلامشده توسط `/api/health` و `/api/status` از `core/config.py:APP_VERSION` میآید و با مرحلهٔ AF-47 و انتشار رسمی RELEASE-01 هماهنگ است.

## v0.4.0 — قابلیت Full-Stack واقعی

**نوع پروژه جدید `fullstack-python`**: با انتخاب این نوع، قالب استاندارد و اجراپذیر
فوراً اسکفولد می‌شود (`core/scaffold.py` از `core/fullstack_template/`) — بک‌اند
پایتون فقط با کتابخانه استاندارد، SQLite (`data/app.db`)، ثبت‌نام/ورود/خروج با
PBKDF2، نقش admin/user، CSRF، پنل مدیر، CRUD نمونه (مشتری‌ها) و ۱۸ سناریوی
آزمون خودکار (`python tests/test_api.py`). جزئیات کامل: `docs/FULLSTACK.md`.

تغییرات پنل: دکمه‌های «🧪 اجرای آزمون‌های پروژه» («`/api/factory/testrun`») و
«📦 نصب وابستگی‌ها» (`/api/factory/deps`)؛ جدید APIهای `scaffold`/`testrun`/`deps`.
خروجی ZIP اکنون فایل‌های رن‌تایم (`*.db`, `*.db-wal`, `*.db-shm`) را هم
مستثنی می‌کند؛ اسکفولد `__pycache__`/`*.pyc` را هرگز کپی نمی‌کند.

### مسیرهای ساخت با این نوع

1. **اسکفولد (قطعی):** create با نوع `fullstack-python` → قالب آماده؛ همان لحظه اجرا/تست/ZIP.
2. **مدل‌محور:** با نوع `fullstack` یا توسعه قالب، مدل با الزام سرنوشت‌ساز
   `FS_REQUIREMENT` (server.py + SQLite + نقش‌ها + آزمون) تولید می‌کند و
   چرخه checker/fix روی آن اجرا می‌شود.

## تغییرات v0.3.1 (تثبیت)

- تشخیص پایان نوبت مدل با قانون سخت‌گیرانه بازسازی شد: فقط پیام `idle` یا گره پایانی (`stop/length/aborted/error`) «پایان نوبت» را ثابت می‌کند؛ گره‌های میانیِ `tool-calls` هرگز پایان شمرده نمی‌شوند (`core/factory.py`).
- باگ نگاشت زمان در `_map_message` اصلاح شد: تایم‌استمپ از سطح بالای شیء (`created`) خوانده می‌شود و پیام `idle` هم اکنون فیلد `created` دارد (`core/opencode_bridge.py`). این باگ در v0.3.0 عملاً چرخه اصلاح را ناپایدار کرده بود.
- `done_since` دیگر به بررسی کلِ نشستِ رقابت‌دار fallback نمی‌کند؛ اگر پیام مرجع در اسنپ‌شات دیده نشود فقط ادامه انتظار می‌دهد (`core/factory.py`).
- رابط Workspace: نوار «پایان شکست‌خورده مدل» فقط بر اساس آخرین وضعیت واقعی نشست (اولین `idle` در فهرست جدیدترین‌اول) نمایش داده و در موفقیت پاک می‌شود؛ رویدادهای زندهٔ `session.execution.succeeded/failed` هم اکنون پردازش می‌شوند (`dashboard/work.js`).

## Termux — رفع خطاهای متداول

- **`__pycache__` در قالب:** اسکفولد آن را کپی نمی‌کند؛ اگر ماژول‌های قالب را
  مستقیم import کنید، پایتون خودش می‌سازد — مشکلی برای اجرا نیست.
- **تفاوت پورت‌ها بین اجراها:** runner پورت آزاد را دینامیک انتخاب می‌کند؛
  آدرس پیش‌نمایش پس از هر «اجرا» را در پاسخ API یا زبانه «اجرا» ببینید.
- **اگر پنل پاسخ نمی‌دهد:** `bash scripts/ai-factory-status` و در صورت نیاز
  `bash scripts/ai-factory-stop && bash scripts/ai-factory-start`.
- **نصب وابستگی فول‌استک:** پروژه‌های fullstack-python نیازی به نصب ندارند
  (فقط استاندارد لایب‌ری). `requirements.txt` خالی عمداً بدون‌عمل است؛
  دکمه «📦 نصب وابستگی‌ها» فقط در صورت وجود وابستگی واقعی pip را صدا می‌زند.
- **خطاهای زمان اجرای پروژه:** در `logs/runs/<project>.log`.

## امنیت

- همه سرویس‌ها فقط روی `127.0.0.1` گوش می‌دهند.
- هیچ دستور دلخواه از پنل اجرا نمی‌شود (بدون shell).
- همه مسیرهای فایل اعتبارسنجی می‌شوند؛ امکان خروج از پوشه پروژه وجود ندارد.
- کلیدهای API در هیچ فایلی ذخیره نمی‌شوند — فقط متغیر محیطی.
- ZIP بدون هیچ محرمانه: `.env`، کلید/گواهی، لاگ، `*.pyc` و دیتابیس رن‌تایم مستثنی‌اند.

## وابستگی‌ها

هیچ ابزار جدیدی لازم نیست؛ Python استاندارد، `curl` و `bash` (پیش‌فرض Termux).
اتصال AI Workspace به OpenCode v2.2.6 از طریق `opencode serve` (جزو خود OpenCode).
