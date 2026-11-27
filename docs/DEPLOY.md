# راهنمای استقرار مستقل محصولات AI Factory (P4)

محصولاتِ تولیدشده (fullstack و پروژه‌های AI‌دار) **هیچ وابستگی زمان اجرا**
به AI Factory یا OpenCode ندارند: فقط پایتون کتابخانه استاندارد (و
اختیاری Node) کافی است. این مستند نحوهٔ اجرای محلی، شبکه‌ای و استقرار
امن HTTPS را شرح می‌دهد.

---

## ۱) اجرای محلی (پیش‌فرض امن)

```bash
cd <مسیر محصول>
python server.py
```

- **پورت** از env `AI_FACTORY_PORT` یا `PORT` — پیش‌فرض `8770`.
- **میزبان** از env `AI_FACTORY_HOST` یا `HOST` — پیش‌فرض امن `127.0.0.1`
  (سرور بدون تنظیم صریح، هرگز روی شبکه گوش نمی‌دهد).
- بررسی سلامت: `curl -s http://127.0.0.1:8770/` باید صفحه اصلی را برگرداند.
  در پروژه‌های AI‌دار: `curl -s http://127.0.0.1:8770/ai.html` (صفحه تنظیمات AI).

## ۲) اجرای شبکه‌ای (LAN)

```bash
AI_FACTORY_HOST=0.0.0.0 AI_FACTORY_PORT=9000 python server.py
# سپس از هر دستگاه داخل شبکه: http://<IP-این-دستگاه>:9000
```

⚠️ **هشدار امنیتی:** با bind شبکه‌ای «همه» مسیرها (از جمله پنل مدیریت
`admin.html` و `/ai.html`) در دسترس شبکه است. توصیه‌ها:

1. در یک LAN اشنا و به‌صورت موقت استفاده کنید؛ برای دسترسی عمومی از
   Reverse Proxy + HTTPS استفاده کنید (بند ۴).
2. در پراکسی، مسیرهای مدیریتی را به IP اداری محدود کنید:
   `location /api/admin/ { allow 192.168.1.0/24; deny all; }`
3. پورت‌های <1024 در اندروید/Termux نیازمند روت‌اند؛ از ≥1024 استفاده کنید.

## ۳) تفکیک محیط توسعه / دمو (staging)

هر نمونه محصول فقط متغیرهای محیطیِ *همان فرایند* را می‌خواند؛
با پورت و دیتابیس جدا، چند محیط مستقل هم‌زمان قابل اجراست:

```bash
# محیط توسعه (پورت جدا + دیتابیس جدا)
PORT=8810 AF_DB_DIR=/tmp/shop-dev python server.py

# محیط دمو
PORT=8820 AF_DB_DIR=/tmp/shop-demo python server.py
```

| متغیر | معنی | پیش‌فرض |
|---|---|---|
| `AI_FACTORY_HOST` / `HOST` | آدرس bind سرور | `127.0.0.1` (امن) |
| `AI_FACTORY_PORT` / `PORT` | پورت سرور | `8770` |
| `AF_DB_DIR` / `AF_FORCE_DB` | دیتابیس جدا برای هر محیط | `data/app.db` |
| `AI_CONFIG_FILE` | فایل پیکربندی AI جایگزین | `config.json` |
| `AI_PROVIDER`, `AI_BASE_URL`, `AI_MODEL` | بازنویسی تنظیمات AI از محیط | از `config.json` |
| `AI_API_KEY` (+ `AI_<PROV>_API_KEY`) | کلید از محیط؛ **فقط همین مسیر** | از فایل کلید خصوصی |

**قاعدهٔ رمز/کلید (غیرقابل مذاکره):**
- کلید فقط از متغیر محیطی یا فایل خصوصی با اجازه `0600`
  (`data/ai_key.txt` در پروژه‌های AI‌دار) خوانده می‌شود.
- هیچ کلیدی در کد، `HTML/JS/CSS`، پاسخ API، لاگ یا خروجی ZIP نمی‌آید.
- فایل‌های `data/ai_key.txt` و `data/ai_admin.json` هرگز ZIP نمی‌شوند
  (خروجی‌ساز `core/exporter.py` آن‌ها را بیرون می‌گذارد).

## ۴) HTTPS با Reverse Proxy (توصیه، بدون TLS دست‌ساز)

خود سرورِ محصول TLS ندارد — و نیازی هم ندارد. نمونه را روی `127.0.0.1`
نگه دارید و پراکسی، TLS را مدیریت کند.

### nginx

```nginx
server {
    listen 443 ssl;
    ssl_certificate     /etc/letsencrypt/live/your.domain/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your.domain/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:8770;
        proxy_set_header   Host $host;
        proxy_set_header   X-Forwarded-For $remote_addr;
        proxy_set_header   X-Forwarded-Proto https;
    }
    # محدودسازی اختیاری پنل AI:
    # location /api/ai/admin/ { allow 192.168.1.0/24; deny all; proxy_pass http://127.0.0.1:8770; }
}
```

گواهی با `certbot --nginx` صادر/تمدید می‌شود.

### Caddy (TLS خودکار)

```
your.domain {
    reverse_proxy 127.0.0.1:8770
}
```

## ۵) CORS

نمونه‌ها «هم‌مبدأ» کار می‌کنند و صفحات وب خودشان را سرو می‌کنند؛
**هیچ هدر CORS ارسال نمی‌شود و نیازی هم نیست.** اگر واقعاً نیاز به مصرف
از مبدأ دیگر دارید، آن را به‌صورت یک `routes` گزارش‌شده در لایه API با
راجِع «فقط این مبدأها» اضافه کنید (هیچ‌گاه `Access-Control-Allow-Origin: *`
همراه ثبت‌نام/ورود). راه ساده‌تر و امن‌تر: مصرف از طریق همان Reverse Proxy.

## ۶) چک‌لیست انتشار ZIP

- ZIP از خود کارخانه فرمتِ خود را دارد و شامل کد/UI/آزمون محصول است.
- **نباید باشد:** `data/ai_key.txt`، `data/ai_admin.json`،
  هر فایل `.log/.err/.out/.pid/.token/.key/.pem`، `__pycache__`,
  هر دیتابیس `*.db`. خروجی‌ساز (`core/exporter.py`) این‌ها را
  به‌ست Exclude می‌کند و قبل از تحویل `zipfile.testzip()` می‌گیرد.
- بعد از استخراج روی سرور: فقط `python server.py`؛ بار اول دیتا/تنظیمات
  را خودش می‌سازد و «گذرواژه اول کاربر ثبت‌نامی = مدیر» است.

## ۷) اجرای زنده بدون هیچ سرویس فابریک

```bash
# فرض: محصول مستقل در ~/deploy/shop (
cd ~/deploy/shop
python tests/test_api.py           # خودآزمون محصول
python server.py &                 # یا با HOST/PORT سفارشی
curl -s http://127.0.0.1:8770/ | head -3
curl -s http://127.0.0.1:8770/api/csrf
```

این «امتحان» در `scripts/ai-factory-test-e2e` (فاز ۵) خودکارسازی شده:
نمونهٔ استخراج‌شده با env خالی، بدون پنل و بدون OpenCode اجرا و آزمون می‌شود.
