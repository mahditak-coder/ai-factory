# API محلی AI Factory

پایه: `http://127.0.0.1:8580` — فقط localhost.

| متد | مسیر | شرح |
|---|---|---|
| GET | `/api/health` | بررسی حیات سرور |
| GET | `/api/status` | وضعیت سیستم و سرویس‌ها |
| GET | `/api/projects` | فهرست پروژه‌ها |
| POST | `/api/projects` | ساخت پروژه — بدنه: `{"name": "...", "description": "..."}` |
| GET | `/api/files?project=X` | فهرست فایل‌های پروژه |
| GET | `/api/file?project=X&path=Y` | محتوای فایل متنی |
| PUT | `/api/file` | ذخیره فایل — بدنه: `{"project","path","content"}` |
| DELETE | `/api/file?project=X&path=Y` | حذف فایل (فقط فایل، نه پوشه) |
| GET | `/api/model` | مدل فعال |
| GET | `/api/logs?which=server\|error&lines=50` | آخرین لاگ‌ها |
| GET | `/api/factory/projects` | فهرست پروژه‌های کارخانه (نوع/تاریخ/تعداد فایل) |
| POST | `/api/factory/projects` | ساخت پروژه — `{"name","type","description"}` (نوع از webpage/shop/dashboard/api/…) |
| POST | `/api/factory/rename` | تغییر نام — `{"old","new"}` (409 در حال اجرا) |
| POST | `/api/factory/delete` | حذف پروژه — `{"project","confirm":true}` — بدون confirm → 403؛ پیش از حذف پشتیبان اجباری |
| POST | `/api/factory/plan` | برنامه‌ریزی با مدل — `{"project","goal"}` (نشست «🏭 برنامه …») |
| GET | `/api/factory/plan?project=X` | متن برنامه تولیدشده |
| POST | `/api/factory/approve` | تأیید برنامه — `{"project"}` |
| POST | `/api/factory/generate` | تولید واقعی — `{"project","goal"}` یا `{"project","use_plan":true}` (نشست «🏭 تولید …») |
| GET | `/api/factory/generation?project=X` | وضعیت/متن تولید (done=false در جریان) |
| GET | `/api/factory/check?project=X` | بررسی واقعی خطاها (py/js/json/html) |
| POST | `/api/factory/fix` | چرخه اصلاح خودکار — `{"project","attempts":1..3}` (پشتیبان خودکار پیش از هر دور) |
| POST | `/api/factory/run` | اجرای امن پروژه (پورت 8610–8658، فقط localhost) |
| POST | `/api/factory/stop` | توقف اجرا — `{"project"}` |
| GET | `/api/factory/runs` | وضعیت اجرای همه پروژه‌ها |
| GET | `/api/factory/runlog?project=X&lines=80` | لاگ اجرای پروژه |
| GET | `/api/factory/history?project=X` | تاریخچه `.factory` پروژه |
| POST | `/api/factory/export` | ساخت ZIP — `{"project","to_downloads":false}` |
| GET | `/api/factory/export` | فهرست خروجی‌ها + سلامت |
| GET | `/api/factory/export?project=X&path=P&download=1` | دانلود ZIP (فقط فایل‌های داخل exports/) |

کدهای خطا: `400` بدنه نامعتبر، `403` مسیر به بیرون/نوع فایل مجاز نیست،
`404` پروژه/فایل/CASE ناشناخته، `409` پروژه تکراری، `500` خطای داخلی (لاگ می‌شود).
