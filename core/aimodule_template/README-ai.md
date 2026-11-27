# ماژول هوش مصنوعی مستقل (`app/aiclient.py`)

این ماژول توسط کارخانه AI Factory فقط برای پروژه‌های AI‌دار منصوب می‌شود و
**مستقل کامل** است:

- فقط کتابخانه استاندارد پایتون؛ بدون OpenCode و بدون AI Factory.
- ارائه‌دهنده پیش‌فرض: هر سرویس سازگار OpenAI (`/chat/completions` + `/models`).
- ارائه‌دهنده OpenCode عمداً جزو محصول نهایی نیست (متعلق به محیط ساخت است).
- کلید فقط سمت سرور: `AI_API_KEY` (env) یا فایل `AI_API_KEY_FILE`؛ هرگز
  در config/پاسخ/لاگ ذخیره نمی‌شود.
- خطاهای طبقه‌بندی‌شده: `no_settings / auth / rate_limit / timeout /
  network / http / bad_response` — پیام‌ها صادقانه و فارسی.

## استفاده

```python
from app.aiclient import send, status, AiError

reply = send([{"role": "system", "content": "دستیار فارسی"},
              {"role": "user", "content": "سلام"}])

st = status()            # {"ready": True, "model": "...", "key": "set", ...}
test_connection(timeout=5)   # تست صریح اتصال
```

## تنظیم

env: `AI_PROVIDER` | `AI_BASE_URL` | `AI_MODEL` | `AI_API_KEY` | `AI_API_KEY_FILE` | `AI_TIMEOUT`
یا بخش `ai` در `config.json` (نمونه: `ai.example.json` — بدون کلید).

## صفحه تنظیمات و مدیریت (P3)

پروژه‌های AI‌دار فایل‌های `app/ai_admin.py` (لایه سیاست/مدیریت) و `ui/ai.html`,
`ui/ai.js`, `ui/ai.css` (صفحه فارسی RTL واکنش‌گرا) را هم می‌گیرند. فعال‌سازی
تا ۳ خط در server.py نرم‌افزار:

```python
from app import ai_admin
# داخل do_GET و do_POST (اول از مسیرهای دیگر):
if self.path.startswith("/api/ai/") or self.path in ai_admin.UI_FILES:
    return ai_admin.handle(self)
```

سپس آدرس `http://127.0.0.1:<port>/ai.html` صفحه تنظیمات را می‌دهد:

- فعال/غیرفعال کردن قابلیت AI، انتخاب ارائه‌دهنده (سازگار OpenAI)،
  Base URL، مدل، **کلید مخفی** (نمایش/مخفی، جایگزینی، حذف).
- دکمه «تست اتصال» همان `aiclient.test_connection()` واقعی را صدا می‌زند
  (نه شبیه‌سازی) و آخرین نتیجه در سرور ثبت می‌شود.
- بار اول: «رمز مدیر» bootstrap می‌شود (الگوی first-admin؛ فقط salt/hash
  PBKDF2 در `data/ai_admin.json` با 0600 ذخیره می‌شود؛ بدون رمز = رد قطعی).
- تغییرات فقط با سرصفحه `X-AI-Admin` (رمز مدیر) و `X-AI-CSRF`
  (double-submit با کوکی `ai_csrf`، SameSite=Strict) پذیرفته می‌شوند.
- کلید فقط در `data/ai_key.txt` (0600، اتمی، فقط متنِ کلید) سمت سرور؛ مسیر آن در
  `config.json → ai.<provider>.api_key_file` ثبت می‌شود؛ مقدار کلید هرگز در
  پاسخ GET/HTML/JS/لاگ/ZIP نمی‌آید (خروجی ZIP هم این دو فایل خصوصی را
  بیرون می‌گذارد) و ذخیره-ادغامی فقط فیلدهای تغییرکرده را عوض می‌کند تا
  بقیه تنظیمات پاک نشود.

## ارائه‌دهنده جدید (بدون تغییر منطق برنامه)

```python
from app.aiclient import register_provider
register_provider("myprov", send=my_send, ping=my_ping)
# سپس AI_PROVIDER=myprov
```

## اجرا روی شبکه و محیط مستقل

```bash
# محلی (پیش‌فرض امن): فقط 127.0.0.1
python server.py

# شبکه محلی: همه اینترفیس‌ها + پورت دلخواه
AI_FACTORY_HOST=0.0.0.0 AI_FACTORY_PORT=9000 python server.py
# (در سکوهای استاندارد: HOST=0.0.0.0 PORT=9000 python server.py)
```

- بازنویسی تنظیمات از محیط (بدون دست‌زدن به `config.json`):
  `AI_BASE_URL`، `AI_MODEL`، `AI_PROVIDER` و `AI_API_KEY` — برای دِو/صحنه
  (staging). کلید فقط از متغیر محیطی همین فرایند یا فایل خصوصی `0600`
  خوانده می‌شود؛ **هرگز** در HTML/JS، پاسخ API، لاگ یا ZIP قرار نمی‌گیرد.
- برای انتشار روی HTTPS، سرور را روی `127.0.0.1` بگذارید و پشت
  Reverse Proxy (nginx/Caddy) اجرا کنید؛ TLS را خود پراکسی مدیریت می‌کند.
  پنل مدیریت AI (`/ai.html`) فقط برای مدیر است — روی شبکه عمومی، دسترسی به
  مسیرهای `/api/ai/` را در پراکسی محدود کنید.
- روی Termux، پورت‌های <1024 نیاز به روت دارند؛ از ≥1024 استفاده کنید.

## نقش لایه‌ها (P2/P3)

- `app/aiclient.py` — هسته اتصال (تنها منبع منطق provider).
- `app/ai_admin.py` — لایه سیاست/مدیریت (تنظیمات، مدیر، CSRF، فایل کلید).
- `ui/ai.*` — صفحه تنظیمات فارسی RTL واکنش‌گرا؛ فقط fetch هم‌مبدأ.
`describe_settings()/apply_settings()` در aiclient نقطه‌های استاندارد تغییر
غیرمحرمانه می‌مانند؛ ذخیره ادغامی (بدون پاک‌شدن بقیه فیلدها) در ai_admin است.
