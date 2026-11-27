# Smart Release — AF-24

انتشار هوشمند: **یک درخواست فارسی → نسخهٔ سالم ساخته شده → ذخیره در Downloads →
همگامسازی واقعی Git/GitHub → گزارش صادقانه.**

## ۱. اصل طراحی

هیچیک از زیرساختهای موجود بازنویسی نشد. Smart Release فقط یک **لایهٔ هماهنگکننده**
است که روی اجزای آزمودهٔ کارخانه مینشیند:

| مرحله | ماژول موجودی که استفاده میشود |
|---|---|
| تشخیص قصد از متن فارسی | `core/smart_release.py::detect_intent` (جدید، محلی/آفلاین) |
| پیدا کردن مسیر واقعی پروژه | `core/paths.project_dir` + `core/factory.read_meta` |
| تشخیص نوع پروژه | ساختار واقعی فایلها (`AndroidManifest.xml`, `java/`, `package.json`, `server.py`, `requirements.txt`, `index.html`) + `meta.json` بهعنوان شاهد، **نه نام پوشه** |
| پیشنیازها | `AndroidEngine.available/preflight`, `shutil.which` |
| ساخت Android | `core/engines/android.py` (`build(sign=True)`, `verify_artifacts`) |
| ساخت Web/Python/Node | `core/release.build_release` + `verify_release` |
| بستهٔ عمومی (نوع نامشخص) | `core/exporter.export` |
| آزمونها | `core/factory.testrun_combined` (unittest + قرارداد نوع) / `verify_artifacts` |
| امنیت | `core/security_gate.scan` — همان دروازهای که API انتشار موجود از آن استفاده میکند |
| تحویل | SHA256 + بررسی ساختار + کپی ایمن به `~/storage/downloads` |
| Git/GitHub | `core/git_sync.py` (جدید) |
| تاریخچه/گزارش | `core.factory.append_history` + `data/smart_releases.json` |

## ۲. فایلهای جدید / تغییریافته

**جدید:**
- `core/smart_release.py` — موتور operation، تشخیص قصد/نوع، تحویل،report
- `core/git_sync.py` — تشخیص مخزن/شاخه/Remote/visibility، پیشنیاز امن، commit/push without force
- `scripts/ai-factory-smart-release` — ابزار واقعی قابل فراخوانی توسط مدل/چت
- `scripts/ai-factory-test-smart-release` — سوئیت ۱۳ سناریو
- `agents/releaser.json` — عامل اختصاصی انتشار
- `docs/test-reports/smart-release/` — شواهد آزمون واقعی

**تغییریافته (افزودی):**
- `core/server.py` — ۵ endpoint خواندنی + ۳ endpoint عملیاتی
- `agents/builder.json`, `agents/README.md` — قرارداد ابزار واقعی برای چت
- `core/release.py` — اصلاح یک باگ واقعی: پوشهٔ `test/` (مفرد) هم مانند `tests/` از بستهٔ انتشار حذف شد؛ پیشتر یک فایل تست حاوی رمز آزمایشی در APK/ ZIP منتشر میشد و دروازهٔ امنیت انتشار را متوقف میکرد.

> فایلهای `.gitignore` و `core/engines/android.py` قبل از این مأموریت توسط کاربر تغییر شدهبودند؛ دستنخورده باقی ماندند.

## ۳. نحوهٔ استفاده

### الف) یک درخواست فارسی (چت / API)

```bash
curl -X POST http://127.0.0.1:8580/api/smart-release/chat \
  -H 'Content-Type: application/json' \
  -d '{"text":"نسخه نهایی معلم من رو بساز، تو دانلودها ذخیره کن و Git و GitHub رو بهروز کن"}'
```

`POST /api/smart-release/chat` ابتدا قصد انتشار را تشخیص میدهد؛ اگر نبود،
هیچ عملیاتی اجرا نمیشود. اگر بود، **خود operation اجرا وreport واقعی برگردانده
میشود** — نه پیشنهاد دستور.

### ب) ابزار خط فرمان (همان چیزی که عامل از چت صدا میزند)

```bash
cd /data/data/com.termux/files/home/AI-Factory
./scripts/ai-factory-smart-release \
  --request "پروژه را بررسی کن، نسخه نهایی را بساز، در دانلودها ذخیره کن و Git و GitHub را بهروز کن" \
  --project teacher-android
```

- `--plan` → پیشنمایش کاملاً خواندنی (بدون هیچ تغییری)
- `--json` →report ماشینی
- `--confirm` → فقط برای تغییر صریح نسخه
- `--no-push` → تحویل محلی بدون ارسال به GitHub

### ج) APIهای خواندنی

| Route | کار |
|---|---|
| `GET /api/smart-release/status` | وضعیت ابزارها، پوشهٔ تحویل، آخرینreport |
| `GET /api/smart-release/plan?project=…` |plan بدون تغییر |
| `GET /api/smart-release/detect?text=…` | تشخیص قصد |
| `GET /api/smart-release/last?project=…` | آخرینreport |
| `GET /api/smart-release/reports?project=…` |historyreportها |
| `POST /api/smart-release/run` | اجرا (`project`, `text`, `confirm`, `push`) |

همهٔ routeها پشت همان gate پنل قرار دارند (مانند سایر endpointهای کارخانه).

## ۴. قوانین ایمنی Git/GitHub (نگاشت به درخواست)

1. **بررسی تغییرات:** `git status --porcelain` — فقط فایلهای تغییریافته بررسی میشوند.
2. **محرمانهها:** `.env`, `*.token`, `*.pem`, `*.key`, `*.jks`, `*.db`, `panel_admin.json` و … هرگز وارد `git add` نمیشوند؛ اسکن محتوای `security_gate` هم اعمال میشود.
3. **خروجی/حجیم:** `build/`, `dist/`, `*.apk`, `*.zip`, فایلهای >5MB پیشفرض مستثنااند.
4. **حق نشر:** `books.json`, `knowledge-bank.json`, `rights-registry.json`, `*.pdf/epub` و الگوهای «کتاب/حق نشر» مسدود میشوند.
5. **مخزن عمومی:** اگر `gh repo view` بگوید PUBLIC و هر فایل حساس/کتاب وجود داشته باشد → **توقف کامل** با راهکار (خصوصی کردن توسط خود کاربر)؛ خودکار تغییر visibility نمیشود.
6. **تغییر دسترسی:** هیچ فرمانی visibility یا-config مخزن را عوض نمیکند.
7. **بدون Force:** تنها `git push <remote> <branch>` ساده； نه `--force`، نه reset/rebase، نه حذفhistory.
8. **تعارض:** `behind>0` یا divergent → توقف صادقانه، بدون pull/merge/rebase خودسرانه.
9. **Remote/شاخه:**از `branch.<name>.remote` و upstream خوانده میشود؛ بدون upstream → «انجامنشده».
10. **قطع GitHub:** فایل محلی تحویل میشود ولی وضعیت همگامسازی «ناموفق/ناقص» ثبت میشود.

خروجی تمام فرمانها قبل ازreport با regex پوشانده میشود (توکنها/رمزها هرگز چاپ نمیشوند).

## ۵. وضعیتها (صادقانه)

|machine|فارسی|معنی|
|---|---|---|
|`ok`|موفق|همهٔ مراحل شامل تأیید آنلاین گذشت|
|`failed`|ناموفق|مرحلهای شکست خورد|
|`partial`|ناقص|فایل محلی سالم تحویل شد ولی همگامسازی/ذخیره ناکامل است|
|`not_done`|انجامنشده|operation اصلاً اجرا نشد (مثلاً مخزن وجود ندارد)|
|`unverified`|تأییدنشده|کاری انجام شد ولی نتیجه قابل اثبات نیست|

نسخهٔ پروژه هرگز خودکار افزایش نمی یابد (قاعدهٔ AF-12). «نسخهٔ قبلی/جدید»از آخرینreport خوانده میشود؛ تغییر نسخه فقط با `set_version`/ `--version --confirm` صریح کاربر.

## ۶. آزمونها

اجرای `python3 scripts/ai-factory-test-smart-release` — ۱۳/۱۳ گذشت (گزارش:
`docs/test-reports/smart-release/test-results.json`). شواهد واقعی:
- `real-android-run.json` — ساخت/امضای واقعی APK «معلم من» v1.1.0 (vc=10100) و ذخیره در `~/storage/downloads`.
- `github-live.json` —.push واقعی به مخزن خصوصی آزمایشی `mahditak-coder/af-smart-release-test` و تطابق head محلی/آنلاین.

جزئیات کدام آزمون واقعی و کدام شبیهسازی شده در `docs/SMART_RELEASE-FINAL-REPORT.md` است.

## ۷. AF-25 — مدیریت خودکار Git/GitHub (توسعه، نه بازنویسی)

اگر پروژه مخزن اختصاصی نداشت، Smart Release پیش از ساخت متوقف میشود و یک
**پیشنهاد تأیید** برمیگرداند (وضعیت `awaiting_confirmation` / «در انتظار تأیید»):

```bash
./scripts/ai-factory-smart-release --request "نسخه نهایی رو بساز و Git و GitHub رو بهروز کن" \
    --project teacher-android --session ses_xxx
# → پرسش فارسی + confirm_token (هیچ تغییری نشده)
./scripts/ai-factory-smart-release --project teacher-android \
    --confirm-token <token>            # یا --decline برای رد
```

پس از تأیید: مخزن محلی پروژه + `.gitignore` متناسب با نوع پروژه، ساخت مخزن **خصوصی** با
`gh repo create --private`، اتصال remote، push اولیه، سپس Build/Test/Deliver/Git Sync.
پروژه فعال از مسیر نشست خوانده میشود (`session_id`)؛ ریشهٔ AI Factory هرگز بهعنوان
پروژه پذیرفته نمیشود. تنظیمات هر پروژه در `.factory/git-github.json` مستقل است.

API جدید: `GET /api/smart-release/repo`، `POST /api/smart-release/prepare`،
`POST /api/smart-release/confirm` و `POST /api/smart-release/chat` با
`session_id` / `confirm_token`.

گزارش کامل AF-25: `docs/AF25-AUTONOMOUS-GIT-REPORT.md` — ۱۲/۱۲ آزمون، شامل ایجاد
واقعی مخزن خصوصی و چرخهٔ کامل گفتوگویی.
