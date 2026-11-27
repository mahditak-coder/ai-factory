# AF-51 — Reasoning Control و مرکز تعامل زنده

## ۱) کنترل میزان تفکر

سطوح: `Auto`, `Low`, `Medium`, `High`, `Maximum`.
منبع حقیقت، **variantهای واقعی مدل** در OpenCode v2.2.6 است (`/api/model` → `variants[*].settings.reasoningEffort`).

- `core/reasoning.py`: نگاشت سطح بهvariant واقعی همان مدل؛ مدل بدون variant → `supported=false` و پیام صریح؛ سطح غیر دقیق → `exact=false` و «نزدیکترینvariant»؛ Auto براساس پیچیدگی مأموریت (pipeline/android/متن بلند/کلمات معماری) سطح بالاتر را در صورت وجود واقعی انتخاب میکند.
- پیشفرض در `data/settings.json` (0600، atomic) ذخیره و از API قابل تغییر است.
- تنظیم هر مأوریت در `meta.reasoning` ذخیره میشود و به `factory.generate` منتقل میگردد.
- `core/opencode_bridge.py`: `variants` به لیست مدلها اضافه شد؛ `set_session_model`/`prompt`variant واقعی را پیش ازturn روی نشست اعمال میکنند.
- APIها: `GET /api/reasoning/models`، `GET/POST /api/reasoning/settings`، `reasoning` در `POST /api/tasks` و `POST /api/pipeline/create`، و `variant` در `POST /api/oc/message`.

## ۲) مرکز تعامل زنده

- `GET /api/interactions` —全部 درخواستهای باز/پاسخ دادهشده/لغوشده با task، مرحله، عامل، دلیل، گزینهها و وضعیت.
- `GET /api/interactions/events` — SSEreal مبتنی بر ذخیرهٔ واقعی tasks (هر تغییر، رویدادnew).
- `POST /api/interactions/answer` — همان `tasks.answer_task` با توکن یکبارمصرف؛ پاسخ تکراری 409 و پاسخ مأموریت اشتباه 403.
- فقط مأموریت وابسته متوقف میشود؛ سایر مأموریتها توسط orchestrator مستقل اجرا میشوند و پس از پاسخ، همان Task از همان checkpoint ادامه مییابد.

## ۳) UX

Workspace: selector تفکر + نشانگر سازگاری real، کارت «مرکز سؤالها و تأییدها»، پاسخ متنی/گزینهای، طراحی RTL و واکنشگرای موبایل. هیچ قابلیت قبلی حذف نشد.

## محدودیتها

- مدلهای بدون variant (مثلاً `big-pickle`) سطح تفکر را پیاده نمیکنند — پیام صریح نمایش داده میشود.
- SSE تعاملات وابسته به همان فرآیند سرور است؛ پس از ریاستارت، وضعیت از tasks.json بازسازی میشود.
- رندر real DOM در موبایل/دسکتاپ بررسی شد؛ کلیک کامل تعاملی در مرورگر آزمون نشد.
