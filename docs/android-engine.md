# موتور اندروید AI Factory (AF-03)

این مستند معماری و کاربرد **موتور واقعی تولید APK/AAB** است که در AF-03 جایگزین خط دستی AF-02 شد. همهٔ قابلیت‌های ذکرشده با آزمون واقعی (`scripts/ai-factory-test-af03` — PASS=36/0) اثبات شده‌اند.

## ۱. معماری

```
core/engines/                 ← زیرساخت موتورهای تولید (افزونه‌پذیر)
├── __init__.py               ← registry: افزودن موتور جدید = یک خط در _ENGINE_MODULES
├── registry.py               ← دکوراتور @register(type_id) + get_engine/list_engines
├── ziputil.py                ← بازنویسی تمیز zipalign-minimal (تراز ۴ بایتی، resources.arsc STORED)
└── android.py                ← موتور اندروید (detect/validate/preflight/build/sign/AAB/کلید)
core/release_center.py        ← وضعیت‌های انتشار + الزامات داده‌محور فروشگاه‌ها
data/store_requirements.json  ← الزامات Google Play/بازار/مایکت (قابل به‌روزرسانی — نه هاردکد)
```

قرارداد هر موتور (مصوب در registry): `available()`, `detect(dir)`, `validate(dir)`, `preflight(dir)`, `build(project, opts, log)`.

## ۲. چیدمان پشتیبانی‌شده

**فقط "minimal"** (بدون Gradle/AGP — RAM گوشی ~1GB):
- `AndroidManifest.xml` در ریشه با `package` و یک Activity دارای intent-filter LAUNCHER
- `res/values/strings.xml` با `app_name`
- `java/` با کد کامل (بدون وابستگی خارجی)

پروژه‌های Gradle صادقانه `supported: False` برمی‌گردند (`mode: "gradle"`) — پنهان‌کاری نمی‌شود.

## ۳. خط تولید داخل موتور (سریالی، در نخ جداگانه با قفل سراسری)

```
aapt2 compile res → aapt2 link → javac --release 11 (android.jar 35) → d8 (min-api 24)
→ zipalign-minimal (core/engines/ziputil.py) → apksigner (v1+v2+v3)
AAB: aapt2 link --proto-format → base/ ماژول + BundleConfig.pb → jar → bundletool build-apks
     (اعتبارسنجی + استخراج universal.apk امضاشده به‌عنوان اثبات)
```

- ساخت‌ها **سریالی** هستند: قفل سراسری `BUILD_LOCK` + بازگشت 409 اگر ساخت همان پروژه در جریان است. سنگین‌ساخت هم‌زمان ممنوع (RAM محدود).
- وضعیت زنده: `status(project)` — فهرست گام‌ها با مهر زمان، رمزها فیلترشده (`StepLog.SECRET_PAT`).

## ۴. مدیریت کلید (قانون طلایی)

- کلید هر محصول: `~/.af-keys/products/<package>.jks` + `.env` (هر دو 0600، خارج از پروژه و Git)
- **هر محصول کلید اختصاصی خودش** — کلید AF-02 برای محصول دیگر هرگز استفاده نمی‌شود.
- ساخت کلید فقط از اندپوینت با `confirm: true` (تأیید صریح کاربر)؛ بازسازی کلید موجود ممنوع (هویت امضا ثابت می‌ماند).
- رمزها: به keytool از stdin، به apksigner از فایل 0600 دوخطی با `file:` — **هرگز در argv، لاگ، گزارش یا پاسخ API.** فایل رمز موقت بلافاصله پس از مصرف حذف می‌شود.
- نکتهٔ واقعی: `apksigner --ks-pass file:<فایل>` فایل را خط‌به‌خط می‌خواند؛ فایل تک‌خطی خطای `end of file reached` می‌دهد — فایل باید رمز را در **دو خط** (keystore + key) داشته باشد.

## ۵. اندپوینت‌های API

| متد | مسیر | کار |
|---|---|---|
| GET | `/api/engines` | فهرست موتورهای ثبت‌شده |
| GET | `/api/factory/android/inspect?project=` | تشخیص + اعتبارسنجی + وضعیت کلید |
| GET | `/api/factory/android/status?project=` | وضعیت زندهٔ ساخت (poll) |
| POST | `/api/factory/android/build` | شروع ساخت `{project, kind: apk\|aab, sign, version_code, version_name}` |
| GET/POST | `/api/factory/android/key` | وضعیت کلید / ساخت کلید (`confirm:true` الزامی) |
| GET | `/api/factory/android/releases?project=` | فهرست نسخه‌ها (جدیدترین اول) |
| POST | `/api/factory/android/release/state` | گذار وضعیت `{project, release_id, state, note, confirm}` |
| GET | `/api/factory/android/store-requirements` | الزامات فروشگاه‌ها (داده‌محور) |
| POST | `/api/factory/android/store-requirements` | به‌روزرسانی الزامات |
| GET | `/api/factory/android/download?project=&release_id=&artifact=apk\|aab` | دانلود امن (guarded داخل `exports/android-builds/<project>/`) |

## ۶. Release Center — وضعیت‌های صادقانه

```
built → signed → validated → store_ready → submitted → approved → published
(شاخه‌های ناموفق: failed / rejected)
```

- گذارهای `built/signed/validated/store_ready`: خودکار و مبتنی بر واقعیت ساخت.
- گذارهای `submitted/approved/published/rejected`: **فقط با `confirm: true`** — چون فقط کاربر می‌تواند واقعیت ارسال/تأیید فروشگاه را تأیید کند. بدون confirm → 409.
- ساخت موفق هرگز «تأیید فروشگاه» تولید نمی‌کند؛ تأیید نهایی همیشه با فروشگاه است.
- الزامات (مثل targetSdk≥35 و AAB برای Play) از `data/store_requirements.json` خوانده می‌شود و قابل به‌روزرسانی است.

## ۷. خروجی‌های نهایی

`exports/android-builds/<project>/<versionName>-vc<versionCode>/`
- `app-release.apk` (+ `app-release.idsig`), `app-release.aab`, `universal.apk` (اثبات اعتبار AAB)
- هر نسخه در `projects/<name>/.factory/releases.json` با SHA-256 ثبت می‌شود.

## ۸. آزمون و رگرسیون (نتایج واقعی 2026-11-13)

| آزمون | نتیجه |
|---|---|
| `scripts/ai-factory-test-af03` | **PASS=36 FAIL=0** |
| `scripts/ai-factory-test-workspace` | **PASS=48 FAIL=0** (بدون رگرسیون) |
| `scripts/ai-factory-test-af01` | **PASS=15 FAIL=0** |

مدارک: `docs/test-reports/af03/` (لاگ کامل آزمون، رگرسیون، شواهد ساخت با SHA-256).

## ۹. محدودیت‌های شناخته‌شده (صادقانه)

1. **صف ساخت**: ساخت‌ها با نخ+قفل اختصاصی موتور (`BUILD_LOCK`) سریالی می‌شوند — نه از صف orchestrator هسته (که مخصوص pipeline تولید مدل است). انحراف عمدی و مستند.
2. **Gradle/AGP پشتیبانی نمی‌شود** (RAM) — صادقانه unsupported.
3. `apksigner` در Termux با rc=0 موفق می‌شود اما متن «Verifies» چاپ نمی‌کند — معیار آزمون‌ها کد بازگشت است.
4. typetests برای نوع `android` پوشش تست مستقیم ندارد (قرارداد آن از طریق موتور آزمون می‌شود).
5. تأیید نهایی انتشار فقط با فروشگاه است — هیچ‌گونه «تأیید خودکار» وجود ندارد و ادعا نمی‌شود.
