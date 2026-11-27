"""AF-16 — تجربهٔ معتبر: دانش اثبات‌شده از تولید/آزمون/اصلاح، نه حدس.

Design rules (built ON the existing memory system — one store, no second):
  * SAME store ``data/memory.json`` under the ``exp`` section (additive —
    an older store without the section stays fully compatible);
  * a record is stored ONLY with real proof: ``proof.ok is True`` from a
    real test run — speculative/model-failed/temporary outcomes are never
    recorded (the API rejects them loudly);
  * secrets never enter the store: every text field is screened with the
    SAME AF-06 value pattern (memory._SECRET_VALUE_RE) and rejected;
  * invalidation is a MARK (status="invalid" + reason) — old data is never
    silently deleted; a bounded cap (oldest-evict) mirrors AF-06 eviction;
  * retrieval filters by project_type (+ optional kind/tech) — unrelated
    knowledge is never returned; conflicts are IDENTIFIED (stale flag),
    the newest record per (kind, project_type, tech) is the injected one;
  * empty/inaccessible memory is never fatal (same philosophy as AF-06).
"""

import json
import logging
import time

from core import memory

log = logging.getLogger("ai-factory.experience")

EXP_CAP = 100                 # bounded storage (oldest evict, AF-06 pattern)
ERR_KIND_MAX = 60
MSG_MAX = 300
SOLUTION_MAX = 1000
FAILURES_MAX = 5

# injection budget inside the AF-06 context block (shared cap preserved:
# KV ≤ 6 + experience ≤ 2 ≤ CTX_MAX_RECORDS=8; total chars ≤ 1600 unchanged)
CTX_EXP_RECORDS = 2


class ExpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _clean(text, cap: int, field: str, required: bool = False) -> str:
    val = str(text or "").strip()
    if required and not val:
        raise ExpError(400, f"فیلد «{field}» لازم است")
    if len(val) > cap:
        raise ExpError(400, f"فیلد «{field}» حداکثر {cap} کاراکتر است")
    return val


def _screen(values: list[tuple[str, str]]) -> None:
    """Secret screening — the SAME AF-06 value patterns; secrets are
    REJECTED at write time (never stored, never masked-in-store)."""
    for field, val in values:
        if memory._SECRET_VALUE_RE.search(val or ""):
            raise ExpError(400,
                "فیلد «%s» شبیه دادهٔ محرمانه است و در تجربه ذخیره نمی‌شود"
                % field)


def record(*, kind: str, project_type: str, error: str, solution: str,
           proof: dict, tech: str | None = None, env: dict | None = None,
           failures: list | None = None, source_project: str = "",
           by: str = "auto") -> dict:
    """Store one PROVEN experience. proof MUST be real test evidence
    (proof["ok"] is True) — a record without proof is rejected."""
    kind = _clean(kind, ERR_KIND_MAX, "kind", required=True)
    project_type = _clean(project_type, 40, "project_type", required=True)
    error = _clean(error, MSG_MAX, "error", required=True)
    solution = _clean(solution, SOLUTION_MAX, "solution", required=True)
    if not isinstance(proof, dict) or proof.get("ok") is not True:
        raise ExpError(400,
            "ثبت تجربه بدون اثبات واقعی (آزمون سبز) مجاز نیست — proof.ok=True لازم است")
    tech = _clean(tech, 80, "tech") if tech else ""
    if env is not None and not isinstance(env, dict):
        raise ExpError(400, "env باید شیء کوچک کلید/مقدار باشد")
    env_json = ""
    if env:
        try:
            env_json = json.dumps({str(k)[:40]: str(v)[:80]
                                   for k, v in list(env.items())[:6]},
                                  ensure_ascii=False)
        except (TypeError, ValueError):
            env_json = ""
        if len(env_json) > 400:
            raise ExpError(400, "env حداکثر ۴۰۰ کاراکتر JSON است")
    fails = []
    for i, f in enumerate(list(failures or [])[:FAILURES_MAX]):
        if isinstance(f, dict):
            note = _clean(f.get("note") or f.get("context") or "", MSG_MAX,
                          f"failures[{i}]")
        else:
            note = _clean(f, MSG_MAX, f"failures[{i}]")
        if note:
            fails.append(note[:MSG_MAX])
    _screen([("kind", kind), ("project_type", project_type),
             ("error", error), ("solution", solution), ("tech", tech),
             ("env", env_json)] + [(f"failures[{i}]", n)
                                   for i, n in enumerate(fails)])
    with memory._lock:
        mem = memory._load()
        exp = list(mem.get("exp") or [])
        rec = {
            "id": "exp-%d" % int(time.time() * 1000),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "epoch": time.time(),
            "kind": kind, "project_type": project_type,
            "error": error, "solution": solution,
            "proof": {"ok": True,
                      "ran": bool(proof.get("ran", True)),
                      "source": str(proof.get("source") or "?")[:80]},
            "tech": tech, "env": env_json,
            "failures": fails,
            "source_project": _clean(source_project, 64, "source_project"),
            "by": str(by or "?")[:40],
            "status": "active",
        }
        exp.insert(0, rec)
        if len(exp) > EXP_CAP:
            exp = exp[:EXP_CAP]          # oldest evict (bounded, documented)
        mem["exp"] = exp
        memory._save(mem)
    log.info("experience recorded: %s (%s/%s)", rec["id"], project_type, kind)
    return dict(rec)


def mark_invalid(exp_id: str, reason: str) -> dict:
    """Mark an experience invalid (bi-e'tabar) — NEVER deleted, just inert."""
    reason = _clean(reason, MSG_MAX, "reason", required=True)
    with memory._lock:
        mem = memory._load()
        exp = list(mem.get("exp") or [])
        for rec in exp:
            if rec.get("id") == exp_id and rec.get("status") == "active":
                rec["status"] = "invalid"
                rec["invalid_reason"] = reason
                rec["invalid_ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                mem["exp"] = exp
                memory._save(mem)
                return dict(rec)
        raise ExpError(404, "تجربهٔ فعال با این شناسه یافت نشد: %s" % exp_id)


def note_failure(kind: str, project_type: str, note: str) -> dict | None:
    """Append a REAL observed failure to the newest matching active
    experience («مواردی که راه‌حل در آن‌ها شکست خورده»). Returns the updated
    record, or None when no matching experience exists (never fabricated)."""
    note = _clean(note, MSG_MAX, "note", required=True)
    if memory._SECRET_VALUE_RE.search(note):
        raise ExpError(400, "متن شکست شبیه دادهٔ محرمانه است")
    with memory._lock:
        mem = memory._load()
        exp = list(mem.get("exp") or [])
        for rec in exp:
            if (rec.get("status") == "active"
                    and rec.get("kind") == kind
                    and rec.get("project_type") == project_type):
                fails = list(rec.get("failures") or [])[:FAILURES_MAX]
                fails.insert(0, note[:MSG_MAX])
                rec["failures"] = fails[:FAILURES_MAX]
                mem["exp"] = exp
                memory._save(mem)
                return dict(rec)
    return None


def relevant(project_type: str, kind: str | None = None,
             tech: str | None = None, limit: int = 3) -> list[dict]:
    """Retrieval — only ACTIVE experiences matching the current problem's
    type (+ kind/tech when given). Newest first; a record shadowed by a
    NEWER conflicting record (same key, different solution) is flagged
    stale=true — identified, not deleted."""
    project_type = _clean(project_type, 40, "project_type", required=True)
    with memory._lock:
        exp = list((memory._load().get("exp") or []))
    active = [r for r in exp if r.get("status") == "active"]
    out = []
    for r in active:
        if r.get("project_type") != project_type:
            continue
        if kind and r.get("kind") != kind:
            continue
        if tech and r.get("tech") and r.get("tech") != tech:
            continue
        out.append(r)
    out.sort(key=lambda r: r.get("epoch") or 0, reverse=True)
    # conflict identification: same (kind, project_type, tech) key, newer
    # active record with a different solution shadows the older one
    newest_key: dict[tuple, str] = {}
    for r in out:
        key = (r.get("kind"), r.get("project_type"), r.get("tech") or "")
        newest_key.setdefault(key, r.get("solution") or "")
    result = []
    for r in out[:max(0, min(int(limit), 10))]:
        key = (r.get("kind"), r.get("project_type"), r.get("tech") or "")
        result.append(dict(r, stale=newest_key.get(key) != (r.get("solution") or "")))
    return result


def context_records(project_type: str) -> list[tuple[str, str, str]]:
    """Records for prompt injection: newest ACTIVE experience per
    (kind, project_type) — conflicts (older solutions) are NOT injected.
    Returns ≤ CTX_EXP_RECORDS tuples (label, key, value)."""
    try:
        recs = relevant(project_type, limit=CTX_EXP_RECORDS * 3)
    except Exception:  # noqa: BLE001 — never fatal
        return []
    picked: list[dict] = []
    seen_keys: set = set()
    for r in recs:
        if r.get("stale"):
            continue
        key = (r.get("kind"), r.get("project_type"))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        picked.append(r)
        if len(picked) >= CTX_EXP_RECORDS:
            break
    out = []
    for r in picked:
        sol = (r.get("solution") or "")[:memory.CTX_VALUE_MAX_CHARS]
        fails = r.get("failures") or []
        fail_txt = ("؛ شکست‌های مشاهده‌شده: " + fails[0][:120]) if fails else ""
        line = "خطای «%s» در %s → راه‌حل اثبات‌شده: %s%s" % (
            r.get("kind"), r.get("project_type"), sol, fail_txt)
        out.append(("experience", r.get("kind") or "خطا", line))
    return out
