"""Release Center — مرکز انتشار تجاری (افزونه‌ای، هماهنگ با معماری موجود).

* هر محصول مستقل: projects/<name>/.factory/releases.json
* وضعیت‌ها (تفاوت روشن — ساخت موفق ≠ تأیید فروشگاه):
    built → signed → validated → store_ready → submitted → approved → published
* الزامات فروشگاه‌ها هاردکد نیست: data/store_requirements.json (قابل به‌روزرسانی).
* هیچ‌گاه صرفاً با ساخت APK/AAB وضعیت تأیید/انتشار دروغ تولید نمی‌شود.
"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path

import core.config as config
from core import paths

_lock = threading.RLock()
STORE_REQUIREMENTS_PATH = config.PROJECT_ROOT / "data" / "store_requirements.json"

STATES = ("built", "signed", "validated", "store_ready", "submitted",
          "approved", "published")
# گذارهای مجاز صادقانه — «approved» فقط با تأیید واقعی فروشگاه (توسط کاربر)
TRANSITIONS = {
    "built": {"signed", "failed"},
    "signed": {"validated", "failed"},
    "validated": {"store_ready", "failed"},
    "store_ready": {"submitted", "failed"},
    "submitted": {"approved", "rejected"},
    "approved": {"published"},
    "published": set(),
    "rejected": set(),
    "failed": set(),
}
_MANUAL_ONLY = {"submitted", "approved", "published", "rejected"}


def _store_path(project: str) -> Path:
    return paths.project_dir(project) / ".factory" / "releases.json"


def _load(project: str) -> list[dict]:
    try:
        data = json.loads(_store_path(project).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(project: str, releases: list[dict]) -> None:
    p = _store_path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(releases, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def add_release(project: str, record: dict, artifacts: dict) -> dict:
    """ثبت نسخهٔ جدید پس از ساخت واقعی موتور (توسط موتور اندروید صدا زده می‌شود)."""
    paths.project_dir(project)
    with _lock:
        releases = _load(project)
        rel = {
            "id": f"rel-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            "project": project,
            "kind": record.get("kind"),
            "signed": bool(record.get("signed")),
            "sign_verified": bool(record.get("sign_verified")),
            "badging": record.get("badging") or {},
            "package": record.get("package"),
            "version_name": record.get("version_name"),
            "version_code": record.get("version_code"),
            "mode": record.get("mode"),
            "output_dir": record.get("output_dir"),
            "artifacts": artifacts,
            "state": "built",
            "created": datetime.now().isoformat(timespec="seconds"),
            "history": [{"ts": datetime.now().isoformat(timespec="seconds"),
                         "state": "built",
                         "note": "ساخت واقعی موتور اندروید"}],
        }
        releases.append(rel)
        _save(project, releases)
        return rel


def list_releases(project: str) -> list[dict]:
    paths.project_dir(project)
    return list(reversed(_load(project)))     # جدیدترین اول


def get_release(project: str, release_id: str) -> dict:
    for r in _load(project):
        if r.get("id") == release_id:
            return r
    raise KeyError("نسخهٔ یافت نشد: " + release_id)


def set_state(project: str, release_id: str, new_state: str,
              note: str = "", confirm: bool = False) -> dict:
    """گذار وضعیت با قاعدهٔ گذار؛ حالت‌های فروشگاهی فقط با confirm (تأیید کاربر)."""
    if new_state not in STATES and new_state != "rejected":
        raise ValueError(f"وضعیت ناشناخته: {new_state}")
    with _lock:
        releases = _load(project)
        rel = next((r for r in releases if r.get("id") == release_id), None)
        if rel is None:
            raise KeyError("نسخهٔ یافت نشد: " + release_id)
        cur = rel.get("state")
        allowed = TRANSITIONS.get(cur, set())
        if new_state not in allowed:
            raise ValueError(f"گذار {cur} → {new_state} مجاز نیست (فقط: {sorted(allowed)})")
        if new_state in _MANUAL_ONLY and not confirm:
            raise ValueError("این وضعیت نیازمند تأیید صریح کاربر است (confirm=true)")
        rel["state"] = new_state
        rel.setdefault("history", []).append(
            {"ts": datetime.now().isoformat(timespec="seconds"),
             "state": new_state, "note": (note or "")[:500]})
        _save(project, releases)
        return rel


def store_requirements() -> dict:
    try:
        return json.loads(STORE_REQUIREMENTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"stores": {}, "note": "فایل الزامات موجود نیست"}


def save_store_requirements(data: dict) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("stores"), dict):
        raise ValueError("ساختار الزامات نامعتبر: stores دیکشنری الزامی است")
    tmp = STORE_REQUIREMENTS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(STORE_REQUIREMENTS_PATH)


def check_store_ready(project: str, release_id: str, store: str) -> dict:
    """برای یک فروشگاه: الزامات داده‌محور در برابر نسخهٔ ساخته‌شده."""
    reqs = store_requirements().get("stores", {}).get(store)
    if not reqs:
        raise KeyError("الزامات این فروشگاه در data/store_requirements.json نیست: " + store)
    rel = get_release(project, release_id)
    problems = []
    tgt = int(reqs.get("min_target_sdk") or 0)
    badging = rel.get("badging") or {}
    tsdk = badging.get("target_sdk") or 0
    if tgt and tsdk and tsdk < tgt:
        problems.append(f"targetSdk {tsdk} < حداقل {tgt} فروشگاه")
    if reqs.get("requires_aab") and rel.get("kind") != "aab":
        problems.append("این فروشگاه فرمت AAB می‌خواهد")
    if reqs.get("requires_signed") and not rel.get("sign_verified"):
        problems.append("نسخه امضا/اعتبارسنجی نشده است")
    return {"store": store, "ready": not problems, "problems": problems,
            "checklist": reqs.get("checklist", [])}
