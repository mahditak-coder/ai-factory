"""AF-29 — ActionGate: turn opstatus from a reporter into a real gate.

Every sensitive operation must present EVIDENCE, not just a flag.  All verdicts
come from the single source of truth ``core.opstatus``; this module only builds
the evidence records and refuses the action when a required check is not PASS.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from core import opstatus as ost


class ActionDenied(Exception):
    def __init__(self, label: str, summary: dict):
        self.label = label
        self.summary = summary
        super().__init__("%s: %s (%s)" % (label, summary["status"],
                                         ", ".join(c["name"] for c in summary["checks"]
                                                   if c["status"] != ost.PASS)))


def evidence(status: str, name: str, detail: str = "",
             evidence_value: Any = None, required: bool = True) -> dict:
    return ost.check(status, name, detail, evidence_value, required=required)


def require(records: list[dict], label: str) -> dict:
    """Raise ActionDenied unless EVERY required check is PASS."""
    s = ost.summary(records)
    if s["status"] != ost.PASS:
        raise ActionDenied(label, s)
    return s


def allows(records: list[dict]) -> bool:
    return ost.overall(records) == ost.PASS


def gate_test(report: dict | None, require_coverage: bool = True) -> dict:
    """A test result is success only when it RAN, was GREEN and left evidence."""
    r = report or {}
    ran = r.get("ran") is True
    checks = [
        evidence(ost.PASS if ran else ost.NOT_TESTED, "ran",
                 "اجرای واقعی تست" if ran else
                 "هیچ تستی اجرا نشد (ran=false) — موفقیت پذیرفته نمیشود"),
        evidence(ost.PASS if r.get("ok") is True else
                 (ost.NOT_TESTED if not ran else ost.FAIL), "ok",
                 "سبز" if r.get("ok") is True else str(r.get("note") or "سرخ/نامعلوم")),
    ]
    detail = r.get("detail") or r.get("own_tests") or r.get("contract") or \
        r.get("probe") or r.get("steps")
    checks.append(evidence(
        ost.PASS if detail else ost.NOT_TESTED, "evidence",
        "شواهد اجرا موجود است" if detail else "هیچ شاهدی برای تست ارائه نشد",
        detail if isinstance(detail, (dict, list)) else None,
        required=require_coverage))
    return ost.summary(checks)


def gate_artifact(path: str | Path, expected_sha: str | None = None) -> dict:
    p = Path(path) if path else Path("")
    checks = []
    exists = p.is_file() and p.stat().st_size > 0
    checks.append(evidence(ost.PASS if exists else ost.FAIL, "artifact_exists",
                           str(p), {"size": p.stat().st_size if exists else 0}))
    if expected_sha:
        actual = _sha(p) if exists else ""
        checks.append(evidence(ost.PASS if actual == str(expected_sha).lower()
                               else ost.FAIL, "sha256_match",
                               "%s … %s" % (actual[:12], str(expected_sha)[:12])))
    return ost.summary(checks)


def gate_git(sync: dict | None, root_risk: bool = False,
             push_requested: bool = True) -> dict:
    """Commit/push is PASS only with real head/remote proof; roots are BLOCKED."""
    s = sync or {}
    checks = []
    if root_risk:
        checks.append(evidence(ost.BLOCKED, "scope",
                               "مخزن شناسایشده ریشهٔ AI Factory است"))
    if s.get("status") == "failed":
        checks.append(evidence(ost.FAIL, "git_status", str(s.get("note") or "")))

    head = str(s.get("head_local") or "")
    before = str(s.get("head_before") or "")
    committed = bool(s.get("committed"))
    no_change = bool(head and head == before and not committed and
                     any("تغییری برای ثبت نبود" in str(x.get("detail", ""))
                         for x in (s.get("steps") or []) if isinstance(x, dict)))
    checks.append(evidence(
        ost.PASS if committed or no_change else ost.NOT_TESTED, "commit",
        ("commit واقعی" if committed else
         "بدون تغییر — مدرک head" if no_change else
         "هیچ مدرکی برای ثبت تغییرات (commit_count=%s)" % s.get("commit_count", 0)),
        {"commit_count": s.get("commit_count", 0),
         "head_local": head, "head_before": before}))

    if push_requested:
        remote = str(s.get("remote_head") or "")
        ok = bool(s.get("pushed")) and s.get("online_match") is True and \
            bool(remote) and remote == head
        checks.append(evidence(
            ost.PASS if ok else ost.NOT_TESTED, "push_verified",
            "push + ls-remote head match" if ok else
            "push یا تطابق آنلاین تأیید نشد (pushed=%s, match=%s)" %
            (s.get("pushed"), s.get("online_match")),
            {"pushed": s.get("pushed"), "online_match": s.get("online_match"),
             "remote_head": remote, "head_local": head}))
    else:
        checks.append(evidence(ost.SKIP, "push_verified",
                               "push طبق درخواست انجام نشد", required=False))
    return ost.summary(checks)


def gate_deploy(probe: dict | None) -> dict:
    p = probe or {}
    code = p.get("http")
    ok = bool(p.get("ok")) and code == 200
    return ost.summary([
        evidence(ost.PASS if code == 200 else ost.FAIL, "health_http",
                 "HTTP %s" % code),
        evidence(ost.PASS if p.get("ok") is True else ost.FAIL, "health_ok",
                 str(p.get("path") or p.get("note") or "")),
    ])


def gate_permissions(decision: str | None, requested: bool = True) -> dict:
    if not requested:
        return ost.summary([evidence(ost.SKIP, "permission", "درخواستی نبود",
                                     required=False)])
    return ost.summary([evidence(
        ost.PASS if decision in ("once", "always") else
        ost.BLOCKED if decision == "reject" else ost.NOT_TESTED,
        "permission", "تصمیم کاربر: %s" % (decision or "بدون پاسخ"))])


def turn_verdict(msgs: list[dict], ours: str | None = None) -> str:
    """Honest verdict for a model turn from REAL messages, newest first.

    ``aborted``/``error``/``interrupted`` are failures, never completion.
    """
    for m in msgs or []:
        if not isinstance(m, dict):
            continue
        if ours and m.get("id") == ours:
            break
        mt = m.get("type")
        if mt in ("user",):
            continue
        if mt == "idle":
            outcome = str(m.get("outcome") or "").lower()
            if outcome in ("failed", "aborted", "error"):
                return ost.FAIL
            # idleness is only a turn boundary, not success evidence. Keep
            # scanning older assistant nodes for a REAL completed answer.
            continue
        if mt != "assistant":
            continue
        finish = str(m.get("finish") or "").lower()
        if finish in ("aborted", "error", "interrupted"):
            return ost.FAIL
        if str(m.get("outcome") or "").lower() in ("failed", "aborted", "error"):
            return ost.FAIL
        # AF-44 — OpenCode v2.2.6 puts the completion timestamp in
        # ``time.completed``; older streams used a top-level ``completed``.
        # Accept both real shapes — never infer success from an unfinished
        # assistant message.
        completed = m.get("completed")
        if not completed and isinstance(m.get("time"), dict):
            completed = m["time"].get("completed")
        if finish in ("stop", "length") and completed:
            return ost.PASS
    return ost.NOT_TESTED


def _sha(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return ""
