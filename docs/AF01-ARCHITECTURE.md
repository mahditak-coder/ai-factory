# AI Factory — ممیزی جامع، معماری هدف چندخروجی، و نقشهٔ راه
سند: AF-01 (بخش‌های ۲ تا ۶ و ۸ مأموریت) — تاریخ: 2026-11-13

این سند فقط وضعیت **واقعاً بررسی‌شده** را ثبت می‌کند. هر ادعا با مرجع فایل/خط یا خروجی آزمون پشتیبانی می‌شود.

---

## ۱. ممیزی وضعیت فعلی (v0.4.0)

### ۱.۱ معماری کلی واقعی
- سرور: `core/server.py` (stdlib `ThreadingHTTPServer`، فقط `127.0.0.1:8580`) — در حال اجرا با PID ثبت‌شده در `logs/server.pid`.
- موتور مولد: OpenCode (`opencode serve` روی `127.0.0.1:4556`، پل `core/opencode_bridge.py`). مدل فعال: `glm-5.3-flash-free` از `providers/models.json`.
- صف کار و چندعاملی: `core/tasks.py` + `core/orchestrator.py` (حداکثر ۲ کارگر، گارد حافظه ≥450MB، قفل پروژه‌ای).
- داده‌ها: `data/*.json` (tasks, deployments, deploy_dests, memory, panel_admin) — همهٔ نوشتن‌ها اتمی (tmp+replace).

### ۱.۲ انواع پروژه پشتیبانی‌شده (منبع حقیقت واحد: `core/config.py:80-141`)
`webpage، webapp، pwa، shop، dashboard، webtool، api، ai-tool، fullstack، fullstack-python`
همه وب/پایتون‌محورند. برای هر نوع، «قرارداد آزمون ماشینی» تعریف شده است.

### ۱.۳ نحوهٔ تولید (ساخت واقعی چیست؟)
- تولید = نوشتن کد توسط مدل در نشست OpenCode (`core/factory.py:430`) + گیت برنامه (`requestcheck`) + تأیید کاربر.
- اسکافولد واقعی: `core/scaffold.py` — سه قالب موجود: `fullstack_template` (FastAPI نه؛ Python stdlib + SQLite)، `pwa_template` (manifest+sw+آیکون)، `aimodule_template`.
- **سیستم build واقعی (کامپایل/باندل) وجود ندارد**: نه npm run build، نه gradle، نه webpack. «ساخت» یعنی تولید سورس + اسکافولد.
- npm روی دستگاه نصب است (node v24.22.2، npm 12.2.2) ولی در خط تولید فعلی استفاده نمی‌شود.

### ۱.۴ آزمون و اعتبارسنجی (واقعی)
- `core/checker.py`: تحلیل ایستا (ast.parse پایتون، `node --check` جاوااسکریپت، JSON، sanity HTML).
- `core/runner.py`: اجرای واقعی unittest پروژه + `pip install -r requirements.txt` واقعی؛ اجرای پیش‌نمایش با پورت رزروشده (8610-8658).
- `core/typetests.py`: آزمون قرارداد رفتاری هر نوع (پروب واقعی HTTP/HTML، بررسی PWA).
- گیت ترکیبی: `factory.testrun_combined` + یک دور اصلاح خودکار (`/api/factory/fix`).

### ۱.۵ ZIP و خروجی
- `core/exporter.py`: ZIP پروژه با استثناکردن رازها/پایگاه‌داده/لاگ؛ آزمون صحت `testzip`؛ ارسال به `/sdcard/Download`.
- `core/release.py`: بستهٔ Release مستقل (تشخیص فناوری با AST/requirements/package.json، `release.json`، run.sh/run.bat، `verify_release` ساختاری).
- `core/deploy.py`: استقرار واقعی روی مقصد `local` (پورت 8700-8739) و `remote` (ssh/scp فقط کلید) + rollback و تاریخچهٔ واقعی در `data/deployments.json`.

### ۱.۶ جمع‌بندی شکاف‌ها (دقیق)
| قابلیت | وضعیت |
|---|---|
| انواع وب/فول‌استک پایتون | ✅ کامل و آزمون‌شده |
| PWA | ✅ (قالب + بررسی manifest/sw) — آزمون آفلاین کامل نیست |
| ZIP نصب‌پذیر WordPress (پلاگین/قالب) | ❌ وجود ندارد |
| Android (APK/AAB/امضا) | ❌ وجود ندارد — ابزارش هم نصب نیست (JDK/gradle/aapt2/d8/apksigner همه در مخازن Termux موجودند، نصب نشده) |
| iOS | ❌ — ساخت در این محیط ناممکن (macOS لازم) |
| وب npm‌محور (React/Vue/Next build) | ❌ در خط تولید نیست (npm موجود ولی بی‌استفاده) |
| فروشگاه با پرداخت واقعی | جزئی: قالب فول‌استک فروشگاه دارد؛ درگاه پرداخت واقعی ❌ |
| Desktop (exe/AppImage/dmg) | ❌ — cross-build از aarch64 ناممکن؛ فقط بستهٔ قابل‌اجرای پایتونی |
| Release Center تجاری | جزئی: release ZIP دارد؛ چک‌لیست بازار/کلید/نسخه/AAB ❌ |
| مدیریت نسخهٔ محصول | جزئی: تاریخچهٔ تغییرات دارد؛ نسخه‌بندی معنایی/آرشیو نسخه‌ها ❌ |

---

## ۲. معماری هدف — هسته + موتورهای افزونه‌پذیر

قاعدهٔ طلایی: **هستهٔ موجود (`factory/server/orchestrator/exporter/release/deploy`) بازنویسی نمی‌شود؛ فقط گسترش می‌یابد.**

### ۲.۱ قرارداد موتور (Engine Contract)
```
core/engines/
  base.py        # EngineContract (abstract):
                 #   id, label, product_types[]
                 #   available() -> {ok, missing[], notes[]}   # پیش‌نیازهای این دستگاه
                 #   preflight(project) -> {ok, checks[]}      # قبل از build
                 #   build(project, opts) -> BuildResult{artifacts, log, ok, honest}
                 #   test(project)  -> از runner/typetests استفاده می‌کند (موازی‌سازی ممنوع)
                 #   package(project) -> بستهٔ نهایی متناسب نوع
  registry.py    # ثبت/کشف موتورها؛ افزودن موتور = یک ماژول + یک ورودی، بدون تغییر هسته
  README.md      # راهنمای نوشتن موتور جدید
```
قواعد اجباری هر موتور:
- هر ادعای موفقیت فقط با خروجی واقعی فرایند build (کد خروج/فایل خروجی).
- buildها سریالی و با قفل سراسری + گارد حافظه (الگوی orchestrator).
- `available()` صادقانه؛ محدودیت‌ها به UI گزارش می‌شوند، شبیه‌سازی موفقیت ممنوع.
- کلیدها/رمزها هرگز در ZIP و در لاگ نمی‌روند و هرگز به بیرون ارسال نمی‌شوند.

### ۲.۲ نقشهٔ موتورها و امکان واقعی در این دستگاه
| موتور | خروجی | امکان محلی (بررسی‌شده) | مسیر |
|---|---|---|---|
| android | APK/AAB امضاشده، debug/release | **امکان‌پذیر بعد از نصب**: `openjdk-17/21`، `gradle 9.8.2`، `aapt2 16.0.0.4-4 aarch64`، `d8 37.0.0`، `apksigner 37.0.0` — همه در مخازن Termux موجود (بررسی: `pkg list-all`) | فاز A: نصب + ساخت دستی APK حداقلی → فاز B: موتور |
| ios | سورس + راهنما | ساخت ناممکن (macOS لازم) — صادقانه فقط سورس/آماده‌سازی | فاز D |
| website | سورس + build + استقرار | static ✅ موجود؛ npm-build ✅ محتمل (node/npm موجود) — باید با پروژهٔ نمونهٔ Vite آزمون واقعی شود | فاز B |
| webapp/pwa | بستهٔ مستقل | هستهٔ آن ✅ موجود (pwa_template + typetests)؛ آزمون آفلاین/Lighthouse کامل ❌ | فاز C |
| wordpress | ZIP پلاگین/قالب نصب‌پذیر | ساخت ZIP ✅ (exporter موجود)؛ اعتبارسنجی واقعی نصب روی WP روی این دستگاه ❌ → اعتبارسنجی ساختاری + بررسی headers استاندارد | فاز C |
| shop | بستهٔ کامل فروشگاه | هستهٔ فول‌استک ✅؛ درگاه پرداخت فقط طراحی/ماک (بدون ادعای واقعی) | فاز C-D |
| backend/api | سرویس + بسته | ✅ موجود (fullstack-python/api)؛ FastAPI/Node بعداً | موجود |
| automation | بستهٔ قابل‌اجرا + مستندات | ✅ محتمل (پایتون محلی) | فاز C |
| desktop | بستهٔ نصبی سه‌سکو | cross-build ناممکن از Termux؛ فقط بستهٔ قابل‌اجرای پایتونی + راهنمای build بومی | فاز D |

### ۲.۳ Release Center (زیرسیستم مستقل)
گسترش `core/release.py` به‌جای بازنویسی:
- `data/store_requirements.json` — الزامات بازارها (target SDK، مجوزها، سیاست‌ها) **به‌صورت دادهٔ قابل‌به‌روزرسانی**، نه اعداد ثابت در کد.
- مدیریت کلید: `~/.af-keys/` با مجوز 0600؛ ساخت keystore با keytool؛ امضای نسخهٔ release با هویت ثابت محصول (برای به‌روزرسانی)؛ کلید هرگز در ZIP/خروجی نمی‌رود.
- خروجی Release: APK/AAB امضاشده + نسخه + changelog + آیکون/تصاویر فروشگاه + چک‌لیست اختصاصی بازار/مایکت/Google Play + راهنمای بارگذاری.
- اصل صادقانگی: انتشار خودکار فقط با API رسمی و اجازه؛ تأیید نهایی همیشه با فروشگاه است — هیچ‌گاه ادعای «تأیید شد» تولید نمی‌شود.
- سرویس ابری build (اختیاری): فقط با رضایت صریح، با اعلام شفاف ارسال اطلاعات، خاموش‌کردنی، و بدون کلیدهای محرمانه.

### ۲.۴ رابط کاربری
- مسیر انتخاب نوع محصول + نمایش وضعیت build/تست/خطا به `factory.html`/`factory.js` اضافه می‌شود (صفحهٔ موازی جدید ممنوع).
- منبع وضعیت: همان الگوی صادقانهٔ Workspace (state resolver + SSE) — بدون fake-green.

---

## ۳. محدودیت‌های واقعی دستگاه (بررسی‌شده، نه حدسی)
- RAM کل 5.5GB و آزاد ~750MB → **ساخت‌های سنگین فقط سریالی** و بعد از بستن اجراهای دیگر؛ گارد حافظه اجباری.
- دیسک: 28GB آزاد — برای JDK+SDK-Tools+cache کافی است.
- CPU: 7 هسته — Gradle daemon باید محدود شود (`org.gradle.jvmargs` کم، `--no-daemon`).
- عدم وجود JDK/Gradle/SDK «الان» — ولی همهٔ اجزا در مخازن Termux موجودند (aapt2 نسخهٔ aarch64 هم هست). نصب = تغییر محیط کاربر → فقط با تأیید کاربر انجام می‌شود.
- iOS و exe/AppImage/dmg واقعی: در این محیط اساساً ناممکن — سند می‌شود، شبیه‌سازی نمی‌شود.
- adb موجود است (android-tools) → آزمون نصب APK روی همین گوشی با `adb install` یا نصب دستی ممکن است.

---

## ۴. نقشهٔ اجرای مرحله‌ای (اولویت + معیار پذیرش)

| فاز | محتوا | معیار پذیرش (همه واقعی) | اولویت |
|---|---|---|---|
| A | نصب با تأیید کاربر: openjdk-17، gradle، aapt2، d8، apksigner؛ ساخت دستی یک APK حداقلی و امضای آن | یک APK واقعاً امضاشده و **نصب‌شده روی همین گوشی**؛ گزارش build ذخیره شود | 🔴 ۱ |
| B | اسکلت `core/engines/` (قرارداد+registry، فقط-read preflight) + موتور Android + موتور website (npm-build) | preflight از API قابل مشاهده؛ یک پروژهٔ Vite نمونه واقعاً build شود | 🔴 ۲ |
| C | موتور WordPress (پلاگین/قالب ZIP + اعتبارسنجی ساختاری) + Release Center v1 (چک‌لیست‌های داده‌محور، کلید امن، نسخه/changelog) | ZIP پلاگین با headers درست ساخته و ساختارش verify شود؛ Release Report تولید شود | 🟠 ۳ |
| D | AAB (bundletool) + آیکون‌های فروشگاه + موتورهای automation/desktop + سند iOS | AAB معتبر با bundletool verify شود؛ سند iOS صادقانه | 🟡 ۴ |
| E | UI نوع محصول/وضعیت build در factory.html + آرشیو نسخه‌ها | مسیر کامل «درخواست→build→تست→Release» بدون خطا در UI | 🟡 ۵ |

قاعدهٔ هر فاز: مستقل، قابل بازگشت (پشتیبان قبل از تغییر)، آزمون واقعی، بدون ساخت سنگین هم‌زمان.
