/* AF-01: زمان پیام‌ها — توابع خالص و قابل تست.
 *
 * قواعد (صادقانه، بدون جعل):
 *  - زمان هر پیام فقط از timestamp واقعی ذخیره‌شده (میلیمتر epoch) خوانده می‌شود.
 *  - timestamp نامعتبر/ناموجوند  «زمان نامشخص» نمایش داده می‌شود — هرگز Date.now() جعل نمی‌شود.
 *  - منطقهٔ زمانی: زمان‌بندی مرورگر/دستگاه (Intl) به‌صورت خودکار اعمال می‌شود.
 *  - نمایش فارسی و سازگار با RTL؛ پیام امروز فقط ساعت، پیام روزهای دیگر تاریخ + ساعت.
 *  - این فایل هم در مرورگر (window.AFTime) و هم در Node برای آزمون بارگذاری می‌شود.
 */
"use strict";

(function (root) {
  const UNKNOWN = "زمان نامشخص";

  function isMs(v) {
    return typeof v === "number" && isFinite(v) && v > 0;
  }

  /* قالب اصلی برای پیام‌ها: امروز → ساعت، غیر از امروز → تاریخ + ساعت */
  function fmt(ms) {
    if (!isMs(ms)) return UNKNOWN;
    const d = new Date(ms);
    if (isNaN(d.getTime())) return UNKNOWN;
    const now = new Date();
    const sameDay = d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
    try {
      if (sameDay) {
        return "⏱ " + d.toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit" });
      }
      return "⏱ " + d.toLocaleString("fa-IR", {
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit",
      });
    } catch (e) {
      /* محیط بدون Intl کامل (WebView قدیمی) — قالب پیش‌فرض جاوااسکریپت، همچنان صادقانه */
      return "⏱ " + (sameDay ? d.toTimeString().slice(0, 5) : d.toISOString().slice(0, 16).replace("T", " "));
    }
  }

  /* فقط ساعت (برای نوارهای وضعیت/خطا) — همیشه زمان «همین لحظه» واقعی است */
  function stampNow(ms) {
    const v = isMs(ms) ? ms : Date.now();
    try {
      return "⏱ " + new Date(v).toLocaleTimeString("fa-IR", { hour: "2-digit", minute: "2-digit" });
    } catch (e) {
      return "⏱ " + new Date(v).toTimeString().slice(0, 5);
    }
  }

  const AFTime = { fmt: fmt, stampNow: stampNow, UNKNOWN: UNKNOWN, isMs: isMs };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = AFTime;              // Node / آزمون
  }
  root.AFTime = AFTime;                    // مرورگر
})(typeof window !== "undefined" ? window : globalThis);
