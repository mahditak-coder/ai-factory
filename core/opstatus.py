"""AF-26 — one canonical, strict status vocabulary for operations and tests.

The five statuses are MUTUALLY EXCLUSIVE and never collapse into one another:

    PASS        the check really ran and its evidence was verified
    FAIL        the check really ran and produced a negative result
    SKIP        deliberately not executed (tool/network/permission absent)
    NOT TESTED  could not be executed or has no evidence at all
    BLOCKED     a precondition refuses the operation (fail-closed)

Two non-negotiable rules:

  * SKIP is NEVER counted as PASS.  A suite whose required check was skipped
    is NOT_TESTED as a whole, never "green".
  * An overall PASS requires EVERY required check to be PASS.  A failure or a
    blocked precondition in any required stage makes the whole operation FAIL
    or BLOCKED; a skip/absence makes it NOT_TESTED.

This module is deliberately dependency-free (stdlib only) so it can be used by
the core modules, by integration scripts and by tests without import cycles.
"""

from __future__ import annotations

import time
from typing import Any, Callable

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
NOT_TESTED = "NOT TESTED"
BLOCKED = "BLOCKED"

STATUSES = (PASS, FAIL, SKIP, NOT_TESTED, BLOCKED)
_VERDICT = frozenset((PASS, FAIL))


class SkipSignal(Exception):
    """Raised by a check when a legitimate precondition is unavailable."""


class BlockedSignal(Exception):
    """Raised by a check when a security/policy precondition refuses execution."""


class NotTestedSignal(Exception):
    """Raised when a check cannot be executed and no evidence exists."""


def skip(reason: str) -> None:
    raise SkipSignal(reason or "precondition unavailable")


def blocked(reason: str) -> None:
    raise BlockedSignal(reason or "blocked by precondition")


def not_tested(reason: str) -> None:
    raise NotTestedSignal(reason or "no evidence")


def check(status: str, name: str, detail: str = "", evidence: Any = None,
          required: bool = True) -> dict:
    """Build one immutable-style check record with a validated status."""
    if status not in STATUSES:
        raise ValueError("unknown status: %r" % status)
    return {
        "status": status,
        "name": str(name),
        "detail": str(detail)[:600],
        "evidence": evidence,
        "required": bool(required),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def overall(records: list[dict]) -> str:
    """Derive the honest overall verdict from required checks only.

    Precedence: FAIL > BLOCKED > NOT_TESTED/SKIP > PASS.
    Optional (non-required) checks never turn a green run red, but they are
    still reported verbatim.
    """
    required = [r for r in records if r.get("required", True)]
    if not required:
        return NOT_TESTED
    states = {r.get("status") for r in required}
    if FAIL in states:
        return FAIL
    if BLOCKED in states:
        return BLOCKED
    if states & {SKIP, NOT_TESTED}:
        return NOT_TESTED
    if states == {PASS}:
        return PASS
    return NOT_TESTED


def summary(records: list[dict], started: float | None = None) -> dict:
    """Aggregate counts + verdict; never derives PASS from SKIP."""
    counts = {s: 0 for s in STATUSES}
    for r in records:
        st = r.get("status")
        if st in counts:
            counts[st] += 1
    verdict = overall(records)
    out = {
        "status": verdict,
        "passed": verdict == PASS,
        "total": len(records),
        "counts": counts,
        "required": sum(1 for r in records if r.get("required", True)),
        "checks": list(records),
    }
    if started is not None:
        out["duration_s"] = round(time.time() - started, 3)
    return out


class Recorder:
    """Small helper used by integration harnesses and unit tests."""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self._started = time.time()

    def add(self, status: str, name: str, detail: str = "",
            evidence: Any = None, required: bool = True) -> dict:
        rec = check(status, name, detail, evidence, required)
        self.records.append(rec)
        return rec

    def run(self, name: str, fn: Callable[[], Any], required: bool = True) -> dict:
        """Execute *fn* and record the truth, including skip/block signals.

        A legacy function that RETURNS a string starting with ``SKIP:`` is
        treated as SKIP (not PASS) as a safety net against old harnesses.
        """
        try:
            value = fn()
        except SkipSignal as exc:
            return self.add(SKIP, name, str(exc), required=required)
        except BlockedSignal as exc:
            return self.add(BLOCKED, name, str(exc), required=required)
        except NotTestedSignal as exc:
            return self.add(NOT_TESTED, name, str(exc), required=required)
        except Exception as exc:  # noqa: BLE001 — a raised error is a real FAIL
            return self.add(FAIL, name, "%s: %s" % (type(exc).__name__, exc),
                            required=required)
        if isinstance(value, str) and value.strip().upper().startswith("SKIP:"):
            return self.add(SKIP, name, value.strip(), required=required)
        if isinstance(value, dict) and value.get("status") in STATUSES:
            return self.add(value["status"], name,
                            str(value.get("detail", "")), value.get("evidence"),
                            required=required)
        return self.add(PASS, name, "" if value is None else str(value)[:600],
                        evidence=value if not isinstance(value, (str, int, float)) else None,
                        required=required)

    def finish(self) -> dict:
        return summary(self.records, self._started)
