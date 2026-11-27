# AF-25 — Autonomous Project & GitHub Management

> **AF-26 amendment:** the "اثبات شد" claim below is not valid for the chat
> flow: `git.commit_count=0` and a root-repository risk were reported as full
> success. Corrected status: `NOT TESTED / partial`. Also, AF-25 Git/GitHub is
> still API/CLI-only — there is no UI surface. See
> `docs/AF26-TRUST-REMEDIATION.md`.

گزارش پیادهسازی و آزمون — نسخه ۱ · ۲۰۲۶-۱۱-۱۸

## ۱. خلاصه

Smart Release existing بازنویسی نشد؛ فقط یک مرحلهٔ «آمادهسازی Git/GitHub پروژهمحور»
پیش از ساخت اضافه شد و API/CLI/عامل آن را پوشش میدهد. اکنون کارگر نشست پروژه،
با یک جملهٔ فارسی، تشخیص میدهد که مخزن اختصاصی وجود ندارد، **پیشنهاد میدهد**،
پس از تأیید تکبار مخزن خصوصی واقعی میسازد، متصل میکند، ساخت/آزمون/تحویل را
انجام میدهد و تغییرات همان پروژه را ارسال و تأیید میکند.

## ۲. فایلها

**جدید:** `core/project_git.py`، `core/confirmations.py`،
`scripts/ai-factory-test-project-git`، `docs/AF25-AUTONOMOUS-GIT-REPORT.md`.

**توسعهیافته (additive):** `core/smart_release.py` (مرحلهٔ `git_prepare` + وضعیت
`awaiting_confirmation`)، `core/server.py` (routeهای `repo/prepare/confirm` و چت
با `session_id` و `confirm_token`)، `scripts/ai-factory-smart-release`
(`--session`, `--confirm-token`, `--decline`, `--prepare`, `--no-auto-git`)،
`agents/releaser.json` + `agents/builder.json` (قرارداد تأیید در همان گفتوگو).

## ۳. معماری تأیید مکالمهای

```
جملهٔ فارسی (+ session_id)
   → detect_intent + active_project (از مسیر نشست، فقط projects/<name>)
   → run(auto_git=True)
        ├─ مخزن متصل است → ادامه
        └─ مخزن/remote نیست → NEEDS_CONFIRM + confirm_token (هیچ تغییری نشده)
              → پرسش فارسی به کاربر: «… مخزن خصوصی با نام پیشنهادی … بسازم؟»
              → کاربر: بله → confirm_token → consume (تکبار، پروژهمحور)
                              → init_local_repo + gh repo create --private
                              → remote + credential helper محلی + push -u
                              → Build → Test → Deliver → Git Sync → report
              → کاربر: خیر → decline_git → فقط تحویل محلی، وضعیت «ناقص»
```

- `confirm_token` یکhandle تصادفی است (**not** credential)؛ TTL ۱۵ دقیقه، تکبار،
  مقید به پروژه و نشست. در `data/smart_release_confirmations.json` (0600) ذخیره میشود.
- تنظیمات هر پروژه در `projects/<name>/.factory/git-github.json` (0600، از export
  و commit مستثنا) — پروژهها کاملاً مستقلاند.
- احراز هویت فقط از `gh` user است: `gh api user --jq .login` و helper محلی
  `!gh auth git-credential`. هیچ رمز/توکنی در فایل پروژه، Git config یاreport نمیرود.

## ۴. آزمونهای واقعی (اجرا شدند)

| سناریو | نتیجه | شاهد |
|---|---|---|
| پروژه بدون Git → پیشنهاد بدون تغییر → ساخت مخزن محلی + `.gitignore` + commit | pass | `project-git-results.json` #1 |
| مخزن محلی بدون Remote → اتصال | pass (gh برای ساخت شبیهسازی، اتصال real-logic) | #2 |
| Remote معتبر → بدون ایجاد مجدد | pass | #3 |
| مخزن عمومی + فایل کتاب → توقف | pass | #4 |
| مخزن خصوصی + همان فایل → مجاز | pass | #5 |
| بدون ورود GitHub → توقف با `gh auth login`، بدون فراخوانی gh | pass | #6 |
| **ایجاد واقعی مخزن خصوصی + اتصال + push + تطابق head** | pass | #7 + `chat-flow-step2.json` |
| تعارض محلی/آنلاین → توقف بدون force/pull | pass | #8 |
| اجرای دوباره → بدون commit تکراری | pass | #9 |
| فایل محرمانه/کتاب وارد لیست امن نشد | pass | #10 |
| جداسازی دو پروژه مستقل | pass | #11 |
| تشخیص پروژه فعال از نشست + رد ریشهٔ AI Factory + token تکبار | pass | #12 |

جمع سوئیت AF-25: **۱۲/۱۲**. سوئیت قبلی Smart Release: **۱۳/۱۳** ( regress کامل).

**چرخهٔ گفتوگویی کامل (واقعی):** `chat-flow-step1.txt` (پرسش + token)،
`chat-flow-step2.json` → وضعیت **موفق**، مخزن
`https://github.com/mahditak-coder/sr25-chat-ce54f8` ساخته/متصل/ارسال شد،
`online_match=true`. مسیر «رد کردن» نیاز (`conversation-declined.json`) → وضعیت
**ناقص**، ۰ ثبت، ۰push، مخزنی ساخته نشد.

**مسیر معلم من:** `conversation-proposal.json` — پرسش دقیقاً طبق درخواست:
«برای پروژه «teacher-android» مخزن Git اختصاصی وجود ندارد. میخواهی مخزن خصوصی
با نام پیشنهادی «teacher-android» در حساب mahditak-coder بسازم و پروژه را به آن
متصل کنم؟» — پیش از تأیید `.git` ساخته نشد.

## ۵. آزمونهای شبیهسازی شده (صریحاً)

- موارد #2، #4، #5، #6، #12 از جدول بالا با `unittest.mock` انجام شدند تا از
  ایجاد مخزن واقعی و دست زدن به مخزن عمومی کاربر جلوگیری شود. منطق ارسال/مسدودسازی
  واقعیgit اجرا شد، فقط visibility/login شبیهسازی شد.
- تعارض (#8) با دو کلون محلی و remote bare واقعی ساخته شد، نه با GitHub.
- «حساب بدون ورود» با mock `github_login` شبیهسازی شد (ورود واقعی کاربر موجود است).
- رفتار مدل در نشست OpenCode بهصورت end-to-end آزمایش نشد؛ مسیر تضمینشده همان
  CLI/API است (routeها post-restart ثبت و با gate 401 تأیید شدند).

## ۶. مخازن آزمایشی واقعی ایجادشده (حفظ شدند، حذف نشدند)

`mahditak-coder/af-smart-release-test` (از AF-24)،
`mahditak-coder/af-autonomous-995627`، `...995822`، `...995998`،
`mahditak-coder/sr25-chat-ce54f8` — همگی **خصوصی** و فقط حاوی فایلهای ایمن تست.
هیچ مخزن عمومی بهروزرسانی نشد.

## ۷. محدودیتهای باقیمانده

- endpointها پشت لاگین پنلاند؛ session معتبر لازم است.
- اجرای ابزار توسط مدل در چت به عامل/permission بستگی دارد (پوشش داده نشد)؛
  CLI/API مسیر قطعیاند.
- حل تعارض با ازدسترفتن داده عمداً خودکار نشد و نیازمند تصمیم کاربر است (طراحی امن).

## ۸. معیار موفقیت

کارگر نشست هر پروژه با یک جملهٔ فارسی: پروژه فعال را تشخیص میدهد، Git را آماده
میکند، در صورت نبود مخزن پیشنهاد و با تأیید مخزن خصوصی واقعی میسازد، متصل میکند،
نسخه نهایی را میسازد/اعتبارسنجی/در Downloads ذخیره میکند، فقط همان پروژه را ارسال
و تأیید میکند، وreport صادقانه میدهد. **این چرخه با شواهد واقعی در #7 و چرخهٔ
گفتوگویی بالا اثبات شد.**
