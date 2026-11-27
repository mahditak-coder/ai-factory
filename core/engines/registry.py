"""registry — کشف و ثبت موتورهای تولید (افزونه‌پذیر: موتور جدید = یک ماژول + یک خط).

هیچ موتور دیگری اینجا تعریف نمی‌شود تا هسته دست‌نخورده بماند؛ افزودن موتور وب/
WordPress/… بعداً فقط یک import + یک ورودی در ENGINE_REGISTRY است.
"""

from typing import Dict, Type


class EngineError(Exception):
    def __init__(self, status: int, message: str, findings: list | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        # AF-50 — structured build failures (file/line/kind/message) for the
        # agent's repair loop. Additive: existing callers are unchanged.
        self.findings = findings or []


ENGINE_REGISTRY: Dict[str, Type] = {}   # type_id -> engine class (فرمت ساده و افزونه‌پذیر)


def register(cls) -> Type:
    """دکوراتور ثبت موتور بر اساس type_id."""
    ENGINE_REGISTRY[cls.type_id] = cls
    return cls


def get_engine(type_id: str):
    cls = ENGINE_REGISTRY.get(type_id)
    if cls is None:
        raise EngineError(404, f"موتوری برای نوع «{type_id}» ثبت نشده است")
    return cls()


def list_engines() -> list[dict]:
    out = []
    for tid, cls in ENGINE_REGISTRY.items():
        eng = cls()
        try:
            av = eng.available()
        except Exception as exc:  # noqa: BLE001 — گزارش صادقانه، نه کرش
            av = {"ok": False, "tools": {}, "notes": [f"خطای بررسی: {exc}"]}
        out.append({"id": eng.id, "type_id": tid, "label": eng.label,
                    "available": bool(av.get("ok")), "notes": av.get("notes", []),
                    "modes": getattr(eng, "supported_modes", [])})
    return out
