# راهنمای Build اندروید در Termux (AF-02)

> **به‌روزرسانی AF-03:** این خط دستی حالا به‌صورت موتور یکپارچه در `core/engines/android.py` پیاده‌سازی و آزمون شده است — ساخت از پنل (tab اندروید) یا API انجام می‌شود و خروجی‌ها با SHA-256 در `exports/android-builds/` ثبت می‌شوند. جزئیات: `docs/android-engine.md`. دستورهای دستی زیر برای دیباگ/تکرار خارج از پنل معتبرند.

این راهنمای **اجراشده و آزمون‌شده** است — همهٔ دستورها روی همین گوشی (Android 12 / aarch64 / Termux 0.118.3) واقعاً اجرا شده‌اند.

## ۱. پیش‌نیازها (نصب‌شده و نسخه‌های واقعی)
| ابزار | بستهٔ Termux | نسخه | نقش |
|---|---|---|---|
| JDK | `openjdk-17` + `openjdk-21` | 17.0.21-2 / 21.0.13-2 | کامپایل و اجرای ابزارها (d8/apksigner به 21 وابستگی سخت دارند) |
| aapt2 | `aapt2` | 2.20-android-16.0.0_r4 (aarch64) | کامپایل/لینک منابع و manifest |
| d8 | `d8` | 9.2.4 | تبدیل bytecode → dex |
| apksigner | `apksigner` | 37.0.0 | امضای v2/v3 |
| gradle | `gradle` | 9.9.1 | نصب و فعال (`gradle --version` ✓) — در خط حداقلی استفاده نمی‌شود |
| bundletool | jar رسمی 1.18.3 | `~/android-sdk-termux/bundletool.jar` | اعتبارسنجی/استخراج AAB |
| android.jar | پلتفرم رسمی Google 35 | `~/android-sdk-termux/platforms/android-35/android.jar` (26MB، از `dl.google.com/.../platform-35_r02.zip`) | کامپایل `javac` |

## ۲. خط تولید (بدون Gradle، بدون Android SDK کامل)
```
res/*.xml ─aapt2 compile→ .flat ─aapt2 link→ base APK (بدون dex)
java ─javac --release 11 -cp android.jar→ .class ─d8 --min-api 24→ classes.dex
APK+DEX ─scripts/android_zipalign_mini.py→ تراز ۴ بایتی ─apksigner→ APK امضاشده
```
- اسکریپت APK: `scripts/android-build-minimal.sh [مسیر پروژه]`
- اسکریپت AAB: `scripts/android-build-aab.sh` (aapt2 `--proto-format` + `base/` ماژول + `BundleConfig.pb` + bundletool `build-apks`)
- zipalign در مخازن Termux نیست → `scripts/android_zipalign_mini.py` بازساخت ZIP با ترازبندی STORED را انجام می‌دهد (الزام targetSdk≥30: `resources.arsc` بدون فشرده‌سازی و تراز ۴ بایتی — در خروجی واقعی راستی‌آزمایی شد).

## ۳. دستور تکرار ساخت
```bash
bash scripts/android-build-minimal.sh          # APK: projects/af02-android-test/build/app-release.apk
bash scripts/android-build-aab.sh              # AAB + universal.apk: projects/af02-android-test/build/aab/
```

## ۴. مدیریت امن کلید انتشار
- کلید: `~/.af-keys/aifactory-release.jks` (PKCS12، RSA-2048، اعتبار ~27 سال) — **خارج از پروژه**، مجوز 0600، داخل Git نیست.
- رمزها: `~/.af-keys/aifactory-release.env` (0600) — هرگز در گزارش/لاگ/ZIP نمایش داده نمی‌شوند.
- **هویت امضا نباید هرگز بازسازی شود** — همان keystore برای به‌روزرسانی‌های آیندهٔ همان محصول استفاده می‌شود وگرنه اندروید به‌روزرسانی را رد می‌کند. پشتیبان رمزنگاری‌شدهٔ کلید مسئولیت کاربر است.
- `.idsig` (v4) کنار APK ساخته می‌شود — قابل حذف است.

## ۵. محدودیت‌های واقعی
- Gradle/AGP کامل + Android SDK: نیازمند دانلود SDK (~500MB+) و RAM بالا (~2GB+)؛ با RAM آزاد ~1GB این دستگاه، خط حداقلی (فعلی) انتخاب شد. Gradle نصب است و کار می‌کند؛ AGP فعلاً در خط تولید نیست.
- APK با targetSdk 35 روی دستگاه Android 12 (API 31) نصب و اجرا شد ✓ (minSdk 24).
- `adb install`: نیاز به اتصال USB یا wireless debugging دارد؛ مسیر عملی فعلی = کپی به `/sdcard/Download` و نصب دستی با تأیید کاربر.
- iOS/macOS و ساخت نسخهٔ دسکتاپ: در این محیط ناممکن (سند صادقانه).
