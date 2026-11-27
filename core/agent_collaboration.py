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
        if not accepted:
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


def write_artifact(project: str, rel: str, content: str, role: str,
                   call_id: str = "") -> dict:
    """Write through the governed Tool Registry (never silently direct)."""
    r = execute_tool(ToolRequest(tool_id="project.write_file", project=project,
        inputs={"path": rel, "content": content}, scopes=["project:write"],
        task_id=role, call_id=call_id or ("sp-" + hashlib.sha256(
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
    """REAL specialist turn: gate → model → artifacts → acceptance."""
    role = specialists.get(role_id)
    if role is None:
        return {"status": opstatus.BLOCKED, "error": "unknown-role"}
    ok, why = can_start(project, role_id)
    if not ok:
        record(project, role_id, opstatus.BLOCKED, note=why)
        return {"status": opstatus.BLOCKED, "error": why}
    try:
        from core import tool_registry
        catalog = tool_registry.registry().catalog_prompt(set(role.allowed_scopes))
        advice = ""
        if role_id in ("product-architect", "software-architect"):
            advice = "\n\n" + engine_advice(project, goal)
        sid = gateway().create_session(project, "specialist:" + role_id)["id"]
        resp = gateway().prompt(project, sid, role.instruction(goal, handoff(project, role_id))
                                + "\n\n" + catalog + advice, model=model)
        deadline = time.time() + 240
        while time.time() < deadline:
            st = gateway().state(sid)
            if not st.get("running"):
                break
            time.sleep(2)
        hist = gateway().history(sid).get("messages") or []
        text = ""
        for m in hist:
            if m.get("type") == "assistant":
                blocks = [p.get("text", "") for p in (m.get("parts") or [])
                          if p.get("type") == "text"]
                if blocks:
                    text = "\n".join(blocks)
                    break
        parts = _parse_artifacts(text, role)
        artifacts = []
        for art in role.artifacts:
            content = parts.get(art.path)
            if not content:
                # AF NEXT P3 — a dedicated, REAL model turn for a missing
                # artifact; no fabricated file and no manual content.
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
                h2 = gateway().history(sid).get("messages") or []
                for m in h2:
                    if m.get("type") == "assistant":
                        blocks = [p.get("text", "") for p in (m.get("parts") or [])
                                  if p.get("type") == "text"]
                        if blocks:
                            content = _strip_fence("\n".join(blocks))
                            break
            if content:
                artifacts.append(write_artifact(project, art.path, content,
                                                role_id))
        accepted, problems = acceptance(project, role_id)
        status = opstatus.PASS if accepted and artifacts else opstatus.FAIL
        rec = record(project, role_id, status, artifacts=artifacts,
                     evidence={"session_id": sid,
                               "message_id": resp.get("message_id"),
                               "model": model or "session-default",
                               "scopes": list(role.allowed_scopes)},
                     note="; ".join(problems))
        return {"status": status, "role": role_id, "session_id": sid,
                "artifacts": artifacts, "problems": problems, "record": rec}
    except Exception as exc:  # noqa: BLE001 — real failure, never fake success
        record(project, role_id, opstatus.FAIL, note=str(exc)[:300])
        return {"status": opstatus.FAIL, "role": role_id, "error": str(exc)[:300]}


def independent_qa(project: str, role_id: str = "qa-engineer") -> dict:
    """QA runs its OWN tools; developer claims are not an input to verdicts."""
    role = specialists.get(role_id)
    if role is None:
        return {"status": opstatus.BLOCKED, "error": "unknown-role"}
    ok, why = can_start(project, role_id)
    if not ok:
        return {"status": opstatus.BLOCKED, "error": why}
    checks = []
    for tool_id, inputs, scope in (
        ("tests.run", {}, "tests:run"),
        ("security.scan", {"mode": "export"}, "security:read"),
    ):
        r = execute_tool(ToolRequest(tool_id=tool_id, project=project,
            inputs=inputs, scopes=[scope], task_id=role_id,
            call_id="qa-" + tool_id.replace(".", "-") + "-" + str(int(time.time()))))
        checks.append({"tool": tool_id, "status": r.status,
                       "error": r.error, "call_id": r.call_id})
    lines = ["## Results"] + ["- %s: %s" % (c["tool"], c["status"]) for c in checks]
    lines += ["## Evidence", "- ToolEvent call_ids: " +
              ", ".join(c["call_id"] for c in checks)]
    wr = write_artifact(project, "spec/test-report.md", "\n".join(lines) + "\n",
                        role_id)
    status = (opstatus.PASS if all(c["status"] == opstatus.PASS for c in checks)
              and wr["status"] == opstatus.PASS else opstatus.FAIL)
    record(project, role_id, status, artifacts=[wr],
           evidence={"checks": checks}, note="independent tool execution")
    return {"status": status, "checks": checks, "artifact": wr}
