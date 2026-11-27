"""core.engines — موتورهای تولید محصول AI Factory.

قرارداد (contract) هر موتور — کلاس با این متدها:
  id / type_id / label        : شناسه و برچسب فارسی
  available() -> dict         : وضعیت واقعی ابزارهای این دستگاه {ok, tools{}, notes[]}
  detect(project_dir) -> dict : تشخیص روش ساخت {supported, mode, reason}
  validate(project_dir) -> dict: اعتبارسنجی ساختار {ok, problems[], info{}}
  preflight(project_dir) -> dict: پیش‌نیازها (دیسک/RAM/کلید) {ok, checks[]}
  build(project, opts, log) -> dict : اجرای ساخت (سریالی، با timeout، بدون shell)
     log = callable(step, message, status) — ثبت مرحله‌به‌مرحله

قواعد صادقانگی: موفقیت فقط با خروجی واقعی فرایند؛ محدودیت‌ها به‌صورت صریح.
"""

from core.engines.registry import get_engine, list_engines, ENGINE_REGISTRY  # noqa: F401

# موتورهای حمل‌شده — افزودن موتور جدید = یک خط در این فهرست (افزونه‌پذیر)
_ENGINE_MODULES = ("android",)

for _m in _ENGINE_MODULES:
    __import__(f"core.engines.{_m}")
