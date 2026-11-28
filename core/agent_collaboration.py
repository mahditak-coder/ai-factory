"""AF NEXT Phase 3 — specialist handoff, versions and independent QA.

Every role runs through ModelGateway and writes REAL artifacts through the
Tool Registry/Executor.  Dependencies are enforced; QA never trusts the
developer's claim — it executes tools itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import core.config as config
from core import opstatus, paths, specialists
from core.model_gateway import gateway
from core.tool_executor import ToolRequest, execute as execute_tool

_STORE = config.PROJECT_ROOT / "data" / "specialist_runs.json"
_LOCK = threading.RLock()
_FILE_MARK = re.compile(r"===FILE\s+(\S+)\s*===")


def _load() -> list[dict]:
    try:
        d = json.loads(_STORE.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _save(items: list[dict]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items[-500:], ensure_ascii=False, indent=1),
                   encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(_STORE)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def runs(project: str = "", role: str = "", limit: int = 50) -> list[dict]:
    items = _load()
    if project:
        items = [e for e in items if e.get("project") == project]
    if role:
        items = [e for e in items if e.get("role") == role]
    return list(reversed(items))[:limit]


def record(project: str, role: str, status: str, artifacts: list[dict] | None = None,
           evidence: dict | None = None, note: str = "") -> dict:
    with _LOCK:
        items = _load()
        version = 1 + sum(1 for e in items
                          if e.get("project") == project and e.get("role") == role)
        rec = {"project": project, "role": role, "version": version,
               "status": str(status), "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "artifacts": artifacts or [], "evidence": evidence or {},
               "note": str(note)[:400]}
        items.append(rec)
        _save(items)
    return rec


def can_start(project: str, role_id: str) -> tuple[bool, str]:
    role = specialists.get(role_id)
    if role is None:
        return False, "unknown-role"
    for dep in role.depends_on:
        accepted = [r for r in runs(project, dep)
                    if r.get("status") == opstatus.PASS]
        from core import specialist_engine as _se
        if not accepted and not _se.has_accepted_run(project, dep):
            return False, "dependency-not-accepted:" + dep
    return True, "ok"


def handoff(project: str, role_id: str) -> dict:
    role = specialists.get(role_id)
    out: dict[str, Any] = {}
    for dep in (role.depends_on if role else ()):
        last = next((r for r in runs(project, dep)
                     if r.get("status") == opstatus.PASS), None)
        if not last:
            continue
        for a in last.get("artifacts") or []:
            p = a.get("path")
            try:
                fp = paths.project_path(project, p)
                out[p] = {"sha256": a.get("sha256"),
                          "text": fp.read_text(encoding="utf-8")[:4000]}
            except (OSError, PermissionError, FileNotFoundError):
                continue
    return out


def verified_handoff(project: str, role_id: str,
                     expected: dict[str, str]) -> dict:
    """Canonical full-text handoff with mandatory hash verification."""
    from core import specialist_engine as _se
    result = _se.verify_handoff(project, role_id, expected)
    return {"ok": result["verified"], "role_id": result["role_id"],
            "texts": {i["path"]: i["text"] for i in result["items"]},
            "hashes": {i["path"]: i["sha256"] for i in result["items"]},
            "problems": result["problems"], "full_text": True}


def revalidate_handoff(project: str, role_id: str,
                       expected: dict[str, str]) -> dict:
    """Re-verify immediately before a consumer uses handed-off artifacts."""
    from core import specialist_engine as _se
    result = _se.revalidate_before_consumer(project, role_id, expected)
    return {"ok": result["verified"], "problems": result["problems"],
            "hashes": {i["path"]: i["sha256"] for i in result["items"]},
            "full_text": True}


def write_artifact(project: str, rel: str, content: str, role: str,
                   call_id: str = "", session_id: str = "",
                   run_token: str = "") -> dict:
    """Write through the governed Tool Registry with MANDATORY role auth."""
    from core import specialist_engine as _se
    rid = _se.resolve_id(role)
    if _se.get(rid) is None:
        return {"status": opstatus.BLOCKED, "path": rel, "sha256": "",
                "error": "unknown-role:" + role, "call_id": call_id}
    r = execute_tool(ToolRequest(tool_id="project.write_file", project=project,
        inputs={"path": rel, "content": content}, scopes=["project:write"],
        task_id=rid, role_id=rid, enforce_role=True, session_id=session_id,
        run_token=run_token,
        call_id=call_id or ("sp-" + hashlib.sha256(
            (role + rel + str(time.time())).encode()).hexdigest()[:12])))
    return {"status": r.status, "path": rel, "sha256": _sha(content),
            "error": r.error, "call_id": r.call_id}


def acceptance(project: str, role_id: str) -> tuple[bool, list[str]]:
    role = specialists.get(role_id)
    if role is None:
        return False, ["unknown-role"]
    problems = []
    for art in role.artifacts:
        try:
            text = paths.project_path(project, art.path).read_text(encoding="utf-8")
        except (OSError, PermissionError, FileNotFoundError):
            problems.append("missing:" + art.path)
            continue
        problem = art.validate(text)
        if problem:
            problems.append(art.path + ":" + problem)
    return (not problems), problems


def conflicts(project: str) -> list[dict]:
    """An accepted artifact changed afterwards without a new acceptance."""
    out = []
    for role_id in (r.id for r in specialists.ROLES):
        accepted = [r for r in runs(project, role_id)
                    if r.get("status") == opstatus.PASS]
        if not accepted:
            continue
        last = runs(project, role_id, limit=1)[0]
        if last.get("status") != opstatus.PASS:
            out.append({"role": role_id,
                        "reason": "artifact-changed-after-acceptance",
                        "accepted_version": accepted[0].get("version"),
                        "current_version": last.get("version")})
    return out


def decide(project: str, role: str, decision: str, reason: str = "") -> dict:
    """Persist a conflict-resolution decision without faking an acceptance."""
    return record(project, role, opstatus.SKIP,
                  evidence={"decision": str(decision)[:160]}, note=reason)


def _parse_artifacts(text: str, role: specialists.SpecialistRole) -> dict[str, str]:
    parts = {}
    matches = list(_FILE_MARK.finditer(text))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        parts[m.group(1).strip()] = text[m.end():end].strip()
    declared = {a.path for a in role.artifacts}
    if not parts and len(role.artifacts) == 1:
        parts[role.artifacts[0].path] = _strip_fence(text)
    return {k: _strip_fence(v) for k, v in parts.items() if k in declared}


def _strip_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```") and t.endswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    # Live acceptance: some model turns emit the handoff marker itself as the
    # first line. It is metadata, never artifact content.
    lines = t.splitlines()
    while lines and lines[0].strip().startswith("===FILE"):
        lines = lines[1:]
    return "\n".join(lines).strip()


def engine_advice(project: str, goal: str) -> str:
    """AF NEXT P4 — capability-ranked engine choice for product roles."""
    from core import engine_selection, paths
    try:
        meta = json.loads((paths.project_dir(project) / "meta.json").read_text(
            encoding="utf-8"))
        ptype = str(meta.get("type") or "")
    except Exception:  # noqa: BLE001
        ptype = ""
    ranked = engine_selection.select(ptype, goal)[:3]
    if not ranked:
        return "انتخاب موتور: نوع محصول نامشخص — تصمیم موتور به Architect واگذار میشود."
    lines = ["انتخاب موتور پیشنهادی (براساس نیاز + قابلیت واقعی محیط):"]
    for r in ranked:
        lines.append("- %s: %s | وضعیت: %s | محدودیتها: %s" % (
            r["engine"], r["title"], r["status"],
            ", ".join(r["limits"][:2]) or "—"))
    return "\n".join(lines)


def run_specialist(project: str, role_id: str, goal: str,
                   model: str | None = None) -> dict:
    """Specialist turn with canonical identity, verified handoff and leases.

    PASS is returned only when every artifact write succeeded, the current
    lease accepted the terminal persistence, and the selected model turn was
    a completed assistant turn.
    """
    role = specialists.get(role_id)
    if role is None:
        return {"status": opstatus.BLOCKED, "error": "unknown-role"}
    ok, why = can_start(project, role_id)
    if not ok:
        record(project, role_id, opstatus.BLOCKED, note=why)
        return {"status": opstatus.BLOCKED, "error": why}
    from core import specialist_engine as _se
    canonical = _se.engine_mode() == "canonical"
    run = None
    try:
        run = _se.start_run(project, role_id)
    except _se.EngineError as exc:
        return {"status": opstatus.BLOCKED, "error": str(exc)[:200]}

    def _terminal(status, artifacts=None, evidence=None, note=""):
        try:
            if canonical and run is not None:
                return _se.record_result(project, role_id, run["token"],
                                         status, artifacts or [])
            return record(project, role_id, status, artifacts=artifacts,
                          evidence=evidence or {}, note=note)
        except Exception as exc:  # noqa: BLE001 — persistence failure fails closed
            return {"status": opstatus.BLOCKED,
                    "error": "terminal-persistence-failed:" + str(exc)[:120]}

    def _final_text(history):
        picked = _se.select_final_response(history or [])
        if not picked.get("ok"):
            return ""
        msg = picked["message"]
        blocks = [p.get("text", "") for p in (msg.get("parts") or [])
                  if p.get("type") == "text"]
        return _strip_fence("\n".join(blocks))

    try:
        from core import tool_registry
        if canonical and run is not None and not _se.reject_stale(
                project, role_id, run["token"])["accepted"]:
            _terminal(opstatus.BLOCKED, note="stale-worker")
            return {"status": opstatus.BLOCKED, "error": "stale-worker"}
        catalog = tool_registry.registry().catalog_prompt(set(role.allowed_scopes))
        advice = ""
        if role_id in ("product-architect", "software-architect"):
            advice = "\n\n" + engine_advice(project, goal)
        handoff_text = {}
        if canonical:
            expected = {}
            for dep in role.depends_on:
                expected.update(_se.accepted_producer_hashes(project, dep))
            verified = verified_handoff(project, role_id, expected)
            if not verified["ok"]:
                _terminal(opstatus.BLOCKED,
                          note="; ".join(verified["problems"]))
                return {"status": opstatus.BLOCKED,
                        "error": "verified-handoff-failed",
                        "problems": verified["problems"]}
            handoff_text = verified["texts"]
            rechecked = revalidate_handoff(project, role_id, expected)
            if not rechecked["ok"]:
                _terminal(opstatus.BLOCKED,
                          note="; ".join(rechecked["problems"]))
                return {"status": opstatus.BLOCKED,
                        "error": "handoff-revalidation-failed",
                        "problems": rechecked["problems"]}
        else:
            handoff_text = handoff(project, role_id)  # explicit legacy mode only
        sid = gateway().create_session(project, "specialist:" + role_id)["id"]
        context_text = {k: v.get("text", "") if isinstance(v, dict) else str(v)
                        for k, v in handoff_text.items()}
        resp = gateway().prompt(project, sid,
                                role.instruction(goal, context_text)
                                + "\n\n" + catalog + advice, model=model)
        deadline = time.time() + 240
        while time.time() < deadline:
            if not gateway().state(sid).get("running"):
                break
            time.sleep(2)
        hist = gateway().history(sid).get("messages") or []
        text = _final_text(hist)
        parts = _parse_artifacts(text, role)
        written, problems, write_failed = {}, [], False
        for art in role.artifacts:
            content = parts.get(art.path)
            if not content:
                sub = gateway().prompt(project, sid,
                    "Write ONLY the complete content of " + art.path +
                    " (format " + art.format + "). Required sections: " +
                    ", ".join(art.required_sections) +
                    ". No explanation, no markdown fence.", model=model)
                deadline2 = time.time() + 180
                while time.time() < deadline2:
                    if not gateway().state(sid).get("running"):
                        break
                    time.sleep(2)
                content = _final_text(
                    gateway().history(sid).get("messages") or [])
            if not content:
                problems.append("no-completed-artifact-turn:" + art.path)
                write_failed = True
                continue
            wr = write_artifact(project, art.path, content, role_id,
                                run_token=(run or {}).get("token", ""))
            if wr.get("status") != opstatus.PASS:
                write_failed = True
                problems.append("write-failed:" + art.path + ":" +
                                str(wr.get("error") or "")[:120])
                continue
            bad = art.validate(content)
            if bad:
                write_failed = True
                problems.append(art.path + ":" + str(bad)[:160])
                continue
            sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if wr.get("sha256") != sha:
                write_failed = True
                problems.append("hash-mismatch:" + art.path)
                continue
            written[art.path] = {"content": content, "sha256": sha}
        accepted_now = all(art.path in written for art in role.artifacts)
        if not accepted_now:
            problems.append("new-artifacts-incomplete")
        status = opstatus.PASS if accepted_now and not write_failed \
            else opstatus.FAIL
        terminal = _terminal(status, artifacts=[
            {"path": path, "sha256": item["sha256"]}
            for path, item in written.items()],
            evidence={"session_id": sid,
                      "message_id": resp.get("message_id"),
                      "model": model or "session-default",
                      "scopes": list(role.allowed_scopes)},
            note="; ".join(problems))
        if terminal.get("status") != status:
            problems.append("terminal-persistence-rejected")
            status = opstatus.BLOCKED
        return {"status": status, "role": role_id, "session_id": sid,
                "artifacts": list(written.values()), "problems": problems,
                "record": terminal}
    except Exception as exc:  # noqa: BLE001 — real failure, never fake success
        _terminal(opstatus.FAIL, note=str(exc)[:300])
        return {"status": opstatus.FAIL, "role": role_id,
                "error": str(exc)[:300]}


def independent_qa(project: str, role_id: str = "qa-engineer") -> dict:
    """QA runs its OWN tools under a fenced canonical run lease."""
    from core import specialist_engine as _se
    role = specialists.get(role_id)
    if role is None:
        return {"status": opstatus.BLOCKED, "error": "unknown-role"}
    ok, why = can_start(project, role_id)
    if not ok:
        return {"status": opstatus.BLOCKED, "error": why}
    canonical = _se.engine_mode() == "canonical"
    run = None
    if canonical:
        try:
            run = _se.start_run(project, role_id)
        except _se.EngineError as exc:
            return {"status": opstatus.BLOCKED, "error": str(exc)[:200]}
    token = (run or {}).get("token", "")

    def _lease_ok():
        return (not canonical) or \
            _se.reject_stale(project, role_id, token)["accepted"]

    def _terminal(status, artifacts=None):
        try:
            if canonical and run is not None:
                return _se.record_result(project, role_id, token, status,
                                         artifacts or [])
            return record(project, role_id, status, artifacts=artifacts,
                          note="independent tool execution")
        except Exception as exc:  # noqa: BLE001
            return {"status": opstatus.BLOCKED,
                    "error": "terminal-persistence-failed:" + str(exc)[:120]}

    if not _lease_ok():
        _terminal(opstatus.BLOCKED)
        return {"status": opstatus.BLOCKED, "error": "stale-qa-run"}
    checks = []
    for tool_id, inputs, scope in (
        ("tests.run", {}, "tests:run"),
        ("security.scan", {"mode": "export"}, "security:read"),
    ):
        if not _lease_ok():
            _terminal(opstatus.BLOCKED)
            return {"status": opstatus.BLOCKED, "error": "stale-qa-run"}
        r = execute_tool(ToolRequest(tool_id=tool_id, project=project,
            inputs=inputs, scopes=[scope], task_id=_se.resolve_id(role_id),
            role_id=_se.resolve_id(role_id), enforce_role=True,
            run_token=token if canonical else "",
            call_id="qa-" + tool_id.replace(".", "-") + "-" + str(int(time.time()))))
        checks.append({"tool": tool_id, "status": r.status,
                       "error": r.error, "call_id": r.call_id})
        if not _lease_ok():  # revalidate after tool returns
            _terminal(opstatus.BLOCKED)
            return {"status": opstatus.BLOCKED, "error": "stale-qa-run"}
    if not _lease_ok():  # revalidate immediately before artifact write
        _terminal(opstatus.BLOCKED)
        return {"status": opstatus.BLOCKED, "error": "stale-qa-run"}
    lines = ["## Results"] + ["- %s: %s" % (c["tool"], c["status"]) for c in checks]
    lines += ["## Evidence", "- ToolEvent call_ids: " +
              ", ".join(c["call_id"] for c in checks)]
    wr = write_artifact(project, "spec/test-report.md", "\n".join(lines) + "\n",
                        role_id, run_token=token if canonical else "")
    if not _lease_ok():  # supersession during the write must not be accepted
        _terminal(opstatus.BLOCKED)
        return {"status": opstatus.BLOCKED, "error": "stale-qa-run-after-write"}
    problems = []
    verified = None
    if wr.get("status") == opstatus.PASS:
        try:
            fp = paths.project_path(project, "spec/test-report.md")
            text = fp.read_text(encoding="utf-8")
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            verified = {"path": "spec/test-report.md", "sha256": sha,
                        "length": len(text)}
            if sha != wr.get("sha256"):
                problems.append("hash-mismatch:spec/test-report.md")
                verified = None
        except (OSError, ValueError):
            problems.append("missing:spec/test-report.md")
    else:
        problems.append("write-failed:" + str(wr.get("error") or "")[:120])
    if not verified:
        problems.append("report-not-verified")
    status = opstatus.PASS if (all(c["status"] == opstatus.PASS
                                   for c in checks) and verified) \
        else opstatus.FAIL
    terminal = _terminal(status, artifacts=[verified] if verified else [])
    if terminal.get("status") != status:
        status = opstatus.BLOCKED
    return {"status": status, "checks": checks, "artifact": wr,
            "verified": verified, "problems": problems, "record": terminal}
