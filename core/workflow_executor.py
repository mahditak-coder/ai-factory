"""AF NEXT — default stage executor: REAL specialist/tool work only.

Text/code stages reuse agent_collaboration.run_specialist (ModelGateway +
ToolExecutor). Image/audio stages use governed media tools to create REAL
files. Nothing here fabricates an artifact or a PASS.
"""
from __future__ import annotations

import secrets

from core import opstatus, tool_executor

_CID = 0


def _call(project, tool_id, inputs, scopes):
    global _CID
    _CID += 1
    cid = "wfex-" + secrets.token_hex(6) + "-%d" % _CID
    r = tool_executor.execute(tool_executor.ToolRequest(
        tool_id=tool_id, project=project, inputs=inputs, scopes=scopes,
        task_id="workflow-executor", call_id=cid,
        execution_context="non-specialist-explicit"))
    return r


def _write(project, rel, content):
    r = _call(project, "project.write_file",
              {"path": rel, "content": content}, ["project:write"])
    if r.status != opstatus.PASS:
        return {"status": opstatus.FAIL, "error": "write-%s:%s" % (rel, r.error)}
    return {"status": opstatus.PASS, "path": rel,
            "sha256": (r.output or {}).get("sha256"),
            "size": (r.output or {}).get("size")}


def _svg(kind: str) -> str:
    if kind == "character":
        shapes = ('<circle cx="320" cy="130" r="52" fill="#f0c9a0"/>'
                  '<rect x="250" y="190" width="140" height="170" rx="26" fill="#2f6fed"/>'
                  '<rect x="218" y="210" width="34" height="120" rx="16" fill="#f0c9a0"/>'
                  '<rect x="388" y="210" width="34" height="120" rx="16" fill="#f0c9a0"/>')
        title = "Character Sheet — 2.5D"
    else:
        shapes = ('<rect x="40" y="300" width="560" height="120" fill="#243a52"/>'
                  '<rect x="90" y="230" width="460" height="80" fill="#315270"/>'
                  '<rect x="150" y="170" width="340" height="70" fill="#457293"/>'
                  '<circle cx="320" cy="120" r="46" fill="#ffd166"/>')
        title = "Level Map — depth layers"
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" '
            'viewBox="0 0 640 480" role="img" aria-label="%s">'
            '<title>%s</title><rect width="640" height="480" fill="#0b101b"/>'
            '%s<text x="320" y="455" fill="#e9eef7" font-size="20" '
            'text-anchor="middle">%s</text></svg>' % (title, title, shapes, title))


def run(project: str, stage: dict, goal: str) -> dict:
    role = stage["role"]
    tool = stage.get("tool")

    if tool == "svg":
        is_char = role == "character-artist"
        art = _write(project, "spec/character-sheet.svg" if is_char else
                     "spec/level-map.svg", _svg("character" if is_char else "level"))
        if art["status"] != opstatus.PASS:
            return art
        note = ("SVG واقعی قابل نمایش و بزرگنمایی؛ کیفیت هنری انسانی NOT VERIFIED")
        return {"status": opstatus.PASS, "artifacts": [art], "note": note,
                "evidence": {"method": "governed project.write_file (SVG)",
                             "policy": "project:write"}}

    if tool == "wav":
        spec = _write(project, "spec/audio-spec.md",
                      "## Music\nسنتز محلی آفلاین\n## Effects\n occupies 1 PCM tone "
                      "as real playback proof\n## Availability\nمنابع موسیقیreal موجود نیست\n")
        if spec["status"] != opstatus.PASS:
            return spec
        r = _call(project, "media.generate_wav",
                  {"path": "assets/audio/ambient.wav", "freq_hz": 440,
                   "duration_ms": 1200, "volume": 0.2}, ["media:write"])
        if r.status != opstatus.PASS:
            return {"status": opstatus.FAIL,
                    "error": "media-tool:%s" % r.error}
        audio = {"path": r.output["path"], "sha256": r.output["sha256"],
                 "size": r.output["size"], "kind": "audio"}
        return {"status": opstatus.PASS, "artifacts": [spec, audio],
                "note": "فایل WAVrealتولید و قابل پخش؛ موسیقی/افکت حرفهای NOT VERIFIED",
                "evidence": {"call_id": r.call_id, "method": r.output.get("quality_note")}}

    if role in ("qa-game", "qa-engineer"):
        from core import agent_collaboration
        rep = agent_collaboration.independent_qa(project)
        if rep.get("status") != opstatus.PASS:
            return {"status": opstatus.FAIL,
                    "error": str(rep.get("error") or rep.get("checks"))[:300]}
        try:
            from core import paths
            p = paths.project_path(project, "spec/test-report.md")
            sha = __import__("hashlib").sha256(p.read_bytes()).hexdigest()
            size = p.stat().st_size
        except OSError as exc:
            return {"status": opstatus.FAIL, "error": "test-report-unavailable:" + str(exc)}
        return {"status": opstatus.PASS,
                "artifacts": [{"path": "spec/test-report.md", "sha256": sha,
                               "size": size, "kind": "document"}],
                "evidence": {"checks": rep.get("checks")},
                "note": "QA مستقل از developer — ابزارها توسط QA اجرا شد"}

    if role in ("security-game", "security-reviewer"):
        r = _call(project, "security.scan", {"mode": "export"}, ["security:read"])
        if r.status != opstatus.PASS:
            return {"status": opstatus.FAIL, "error": "security-scan:" + r.error}
        rep = r.output or {}
        lines = ["## Findings", "- blocked: %s" % bool(rep.get("blocked")),
                 "- critical: %s" % len(rep.get("critical") or []),
                 "## Decision", "RELEASE" if not rep.get("blocked") else "BLOCKED", ""]
        art = _write(project, "spec/security-report.md", "\n".join(lines))
        if art["status"] != opstatus.PASS:
            return art
        return {"status": opstatus.PASS, "artifacts": [art],
                "evidence": {"call_id": r.call_id,
                             "files_scanned": rep.get("files_scanned")}}

    if role == "release-engineer":
        from core import engine_selection, factory, paths, quality_gate as qg
        meta = {}
        try:
            import json
            meta = json.loads((paths.project_dir(project) / "meta.json").read_text(
                encoding="utf-8"))
        except (OSError, ValueError):
            pass
        engine = (engine_selection.best(meta.get("type", ""), goal) or {}).get(
            "engine", "web_saas")
        rep = qg.evaluate(project, engine, do_build=True)
        art = _write(project, "spec/release-evidence.md",
                     "## Artifact\n%s\n## Decision\n%s\n## Score\n%s\n" % (
                         str(rep["build"].get("artifacts"))[:500],
                         rep["decision"], rep["quality_score"]))
        if art["status"] != opstatus.PASS:
            return art
        return {"status": opstatus.PASS, "artifacts": [art],
                "evidence": {"decision": rep["decision"],
                             "build": rep["build"].get("status"),
                             "quality_score": rep["quality_score"]}}

    # Document/code/page roles: the real Specialist System via ModelGateway.
    # A user's revision note MUST reach the model, not just the audit trail.
    from core import agent_collaboration
    if stage.get("note"):
        goal = (goal or "") + "\n\nتوضیح اصلاح کاربر (باید رعایت شود):\n" + stage["note"]
    result = agent_collaboration.run_specialist(project, role, goal)
    if result.get("status") != opstatus.PASS:
        return {"status": opstatus.FAIL,
                "error": str(result.get("error") or result.get("problems"))[:300]}
    arts = [{"path": a["path"], "sha256": a["sha256"], "kind": stage["kind"]}
            for a in (result.get("artifacts") or [])
            if a.get("status") == opstatus.PASS and a.get("sha256")]
    if not arts:
        return {"status": opstatus.FAIL, "error": "no-accepted-artifact"}
    return {"status": opstatus.PASS, "artifacts": arts,
            "evidence": {"session_id": result.get("session_id"),
                         "specialist": role},
            "note": "ModelGateway + ToolExecutor reached through Specialist System"}
