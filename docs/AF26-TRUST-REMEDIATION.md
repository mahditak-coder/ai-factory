# AF-26 — اعتمادپذیری وضعیت، صحت گزارش و زیرساخت تست Core

تاریخ: ۲۰۲۶-۱۱-۲۳ · مرحلهٔ اصلاح فوری پس از ممیزی جامع

## ۱. مشکلات واقعی اصلاحشده

| شناسه | مشکل | اصلاح | شاهد |
|---|---|---|---|
| **C1** | `PUT /api/file` فاقد احراز هویت/CSRF بود | `_panel_gate()` به `do_PUT` اضافه شد | `core/server.py:660-671` |
| **C2** | تنظیم اولیه پنل و بازیابی 401 آویزان میماندند | DOM قبل از دستکاری ساخته شد؛ promise واقعی resolve میشود | `dashboard/panel.js:63-120` |
| **C3** | race گیت تأییدpipeline، تسک را permanently hold میکرد | hold قبل از انتشار وضعیت `awaiting_approval` نصب میشود | `core/pipeline.py:325-342` |
| **H13** | SKIP بهعنوان PASS ثبت میشد | مدل وضعیت canonical + Recorder؛ exit تنها با overall=PASS | `core/opstatus.py`, `scripts/ai-factory-test-smart-release` |
| **H14** | موفقیت کامل با `commit_count=0` و ریسک ریشهٔ AI Factory | وضعیت کلی از evidence استخراج میشود؛ NOT TESTED/BLOCKED | `core/smart_release.py` section 6 |
| **H15** | AF-21/22/23 تست اختصاصی نداشتند | سوئت `test_af21_25.py` | `core/tests/test_af21_25.py` |
| **H16** | مسیر مدل→تأیید آزمایش نشده بود | تست H14 با git_sync mock و مسیر واقعی `sr.run` | تست ۱۸ سوئت Smart Release |
| **H17** | کد تست AF-24/25 untracked بود | کد و تستها وارد Git میشوند | این کامیت |

## ۲. مدل وضعیت جدید (لازمالاجرا)

پنج وضعیت مستقل: `PASS`, `FAIL`, `SKIP`, `NOT TESTED`, `BLOCKED`.

- `SKIP` هرگز `PASS` محسوب نمیشود.
- overallPASS تنها زمانی است که **تمام** چکهای ضروری PASS باشند.
-_first_ FAIL > BLOCKED > NOT_TESTED/SKIP > PASS.
- چک اختیاری (`required=False`) میتواند SKIP باشد بدون قرمز کردن کل عملیات.
- هر report دارای بلوک `verification` با تک تک چکها و شواهد است.

## ۳. فایلهای تغییرکرده

**کد:** `core/opstatus.py` (جدید)، `core/config.py`, `core/panelauth.py`, `core/server.py`, `core/pipeline.py`, `core/smart_release.py`, `dashboard/panel.js`

**تستها:** `core/tests/__init__.py`, `core/tests/test_status.py`, `core/tests/test_c1_auth.py`, `core/tests/test_c3_gate.py`, `core/tests/test_core_units.py`, `core/tests/test_af21_25.py`, `core/tests/test_panel_js.mjs`, `scripts/ai-factory-test-core`, `scripts/ai-factory-test-smart-release`

**اسناد/شواهد:** `docs/AF26-TRUST-REMEDIATION.md`, `docs/SMART_RELEASE-FINAL-REPORT.md`, `docs/AF25-AUTONOMOUS-GIT-REPORT.md`, `docs/test-reports/core/test-results.json`, `docs/test-reports/smart-release/test-results.json`, `docs/test-reports/smart-release/chat-flow-step2.json` (تصحیح H14)

## ۴. نتایج اجرا

### Core trust suite — `python3 scripts/ai-factory-test-core`
`PASS` · ۳۸ چک (۳۲ unittest + ۶ رفتار panel.js) · ۰ FAIL · ۰ SKIP · ۰ NOT TESTED
شاهد: `docs/test-reports/core/test-results.json`

### Smart Release suite — `python3 scripts/ai-factory-test-smart-release`
`PASS` · ۱۸ چک · ۰ FAIL · ۰ SKIP
شاهد: `docs/test-reports/smart-release/test-results.json`

| تست | چه چیزی آزمایش شد | نوع | نتیجه |
|---|---|---|---|
| C1 (۵ تست) | درخواست واقعی HTTP PUT بدون session / با CSRF بد / با CSRF معتبر | Real HTTP، سرور واقعی روی پورت موقت | PASS |
| C2 (۶ تست) | اجرای واقعی panel.js با DOM ساختگی: تنظیم اولیه + بازیابی 401 | Behavioural (Node) | PASS |
| C3 (۳ تست) | دو interleaving گیت تأیید با توابع واقعی pipeline/tasks | Real، store ایزوله | PASS |
| AF-21 | خواندن api-contract + الزام آن در instruction مدل (با mock bridge) | Real + mock محدود | PASS |
| AF-22 | مسیریابی خط لوله اندروید به stage runner مخصوص | Real با mock هدف | PASS |
| AF-23 | شکل payload تعویض مدل + طبقهبندی/redact خطا | Real با mock HTTP | PASS |
| AF-24/25 | تشخیص قصد، اعتبارسنجی، منع ریشهٔ AI Factory،roundtrip git محلی | Real + fixture ایزوله | PASS |
| Status model | ۸ تست معناشناسی پنج وضعیت | Real | PASS |
| Core units | مسیرها، حافظه، وابستگیها، تأییدها، دستکاری MANIFEST | Real | PASS |
| Smart Release ۱-۱۳ | قصد/نوع/امنیت/git/ساخت/تحویل/APK/ماژولها | Real + fixture | PASS |
| Smart Release ۱۴-۱۸ | نبود gh، شکست gh، نبود APK، مراحل ناقص، commit صفر | Negative/Real | PASS |

## ۵. ایمنی اجرای تست

-全部 تستها با `AI_FACTORY_TEST_ROOT` روی درخت موقت اجرا شدند؛ `data/`, `projects/`, اعتبارنامهها و فایلهای کاربر دستنخوردهاند.
-probe GitHub با باینری `gh` ساختگی ایزوله شد؛ هیچ تماس خارجی و هیچ مخزنی ایجاد نشد.
- نصب پکیج، استقرار، ساخت APK سنگین یا عملیات مخرب انجام نشد.
- فیکسچرها تنها مواردی بودند که自身 تست ایجاد و در انتها پاک کرد.

## ۶. موارد تأییدنشده / باقیمانده

- ساخت کامل AF-22 ( APK امضاشده با کلید محصول ) در این مرحله اجرا نشد — نیاز به کلید محصول و ساخت سنگین دارد؛ مسیریابی تنها تأیید شد.
- مسیر chat → model → confirmation → git بهصورت end-to-end با مدل واقعی آزمایش نشد (تنها با git_sync mock و fixture).
- رابط کاربری Smart Release/Git/Quality/Autofix هنوز وجود ندارد (یافتهٔ ممیزی، خارج از دامنهٔ این مرحله).
- C1/C2/C3 تنها در سطح رفتار تست شدند؛ بهروزرسانی سرور در حال اجرا نیاز به restart دارد و در این مرحله انجام نشد.
