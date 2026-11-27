# AI Workspace — اتصال AI Factory به OpenCode (نسخه 0.2.0)

## معماری واقعی

```
مرورگر (فقط localhost:8580)
    │  fetch / EventSource — بدون هیچ کلید
    ▼
core/server.py  (پروکسی امن AI Factory)
    │  HTTP + Basic-auth  opencode:<رمز>
    ▼
opencode serve  (127.0.0.1:4556, OpenCode v2.2.6)
    │
    ▼
مدل GLM-5.3-flash-free (ارسال واقعی API)
```

- رمز server از `OPENCODE_SERVER_PASSWORD` که فقط داخل backend ساخته/خوانده می‌شود:
  `logs/opencode.token` با دسترسی 0600 — در هیچ پاسخ HTTP به مرورگر نمی‌آید.
- فرایند serve با argv ثابت و بدون shell اجرا می‌شود (`core/opencode_bridge.py`).
- نشست‌های OpenCode به پوشه‌ی `projects/<name>` متصل (location) هستند.

## Endpointهای `/api/oc/*`

| متد | مسیر | شرح |
|---|---|---|
| GET | `/api/oc/status` | وضعیت/نسخه سرور OpenCode |
| GET | `/api/oc/models` | لیست واقعی و فعال مدل‌ها |
| GET | `/api/oc/sessions?project=` | نشست‌های همان پروژه |
| POST | `/api/oc/sessions` | ساخت نشست `{project, title?}` |
| GET | `/api/oc/history?session=` | تاریخچه پیام‌ها |
| POST | `/api/oc/message` | ارسال پیام `{project, session_id, text}` |
| GET | `/api/oc/events?session=` | SSE زنده (دلتاها/ابزار/خطا/permission) |
| POST | `/api/oc/interrupt` | لغو عملیات `{session_id}` |
| GET | `/api/oc/permissions?session=` | درخواست‌های مجوز در انتظار |
| POST | `/api/oc/permission` | پاسخ به permission `{session_id, request_id, decision: once\|always\|reject}` |

## Endpointهای واقعی OpenCode v2.2.6 استفاده‌شده

- `GET /api/info` ، `GET /api/model` ، `GET /api/session` ، `POST /api/session` (body شامل `location.directory`)
- `POST /api/session/:id/prompt` (بدنه `{"text": ...}` — غیرهمزمان، پذیرش سریع)
- `GET /api/session/:id/message` (تاریخچه) ، `POST /api/session/:id/interrupt`
- `GET /api/event` (SSE زنده) ، مسیرهای permission
- احراز هویت: `Authorization: Basic base64("opencode:<password>")`

## نحوه اجرا

```bash
bash scripts/ai-factory-start          # پنل + (در اولین تماس) opencode serve
# مرورگر: http://127.0.0.1:8580/workspace.html
bash scripts/ai-factory-stop           # هر دو (پنل + OpenCode serve) را متوقف می‌کند
bash scripts/ai-factory-test-oc        # ۲۴ تست پذیرش E2E پل (واقعی، بدون شبیه‌سازی)
bash scripts/ai-factory-test           # ۱۵ تست قدیمی پنل (v0.1.0)
bash scripts/ai-factory-test-factory   # ۲۵ بررسی کارخانه (v0.3.0)
```

این بخش (Workspace و پل OpenCode) در v0.3.0 دست‌نخورده باقی مانده است؛ ثبت‌شده‌ها همانند v0.2.0 هستند (۲۱→۲۴ تست پس از افزودن بررسی‌های مسیر ایستا).

## مجوزها و امنیت عملیات

- به‌صورت پیش‌فرض OpenCode ابزارهای write/edit را **بدون سؤال** اجرا می‌کند.
  برای الزام تأیید انسانی، در هر پروژه `opencode.json` بسازید:
  `{ "permission": { "edit": "ask", "write": "ask", "bash": "ask" } }`
  در این حالت هر تلاش ویرایش/اجرای فرمان → نوار «نیازمند تأیید» در Workspace
  با نمایش diff فایل و سه گزینه‌ی «یک‌بار / همیشه / رد».
- `scripts/ai-factory-test-oc` همین چرخه را به‌صورت واقعی تست می‌کند.
