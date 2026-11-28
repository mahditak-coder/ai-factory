"""AF NEXT Phase 5 — central Quality Gate and Release Evidence.

Every input comes from governed ToolExecutor calls or real EngineAdapter
results. Severity decides the release; the score never hides a Critical.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from pathlib import Path

from core import (engine_adapter, opstatus, paths, quality_checks as qc,
                  tool_executor, tool_identity)

PASS, FAIL, BLOCKED, SKIPPED, NOT_VERIFIED = (
    "PASS", "FAIL", "BLOCKED", "SKIPPED", "NOT VERIFIED")
CRITICAL, HIGH, MEDIUM, LOW = "Critical", "High", "Medium", "Low"
RELEASE, HOLD, BLOCK_RELEASE = "RELEASE", "HOLD", "BLOCK RELEASE"

DIMENSIONS = {
    "functionality": 0.20, "ui_ux": 0.10, "visual": 0.10,
    "code_architecture": 0.08, "performance": 0.08, "accessibility": 0.10,
    "security_privacy": 0.14, "media": 0.04, "build_packaging": 0.10,
    "real_ux": 0.06,
}
_SEV_SCORE = {CRITICAL: 0, HIGH: 40, MEDIUM: 70, LOW: 85}


def _call(project, tool_id, inputs, scopes, task_id="quality-gate"):
    cid = "p5-" + secrets.token_hex(8)
    r = tool_executor.execute(tool_executor.ToolRequest(
        tool_id=tool_id, project=project, inputs=inputs, scopes=scopes,
        task_id=task_id, call_id=cid,
        capability_token=tool_identity.issue_infrastructure_capability(
            project, tool_id, (scopes or ["project:read"])[0],
            issuer_key=tool_identity.server_issuer_key())))
    return {"status": r.status, "ok": r.ok, "output": r.output or {},
            "error": r.error, "call_id": r.call_id, "duration_ms": r.duration_ms}


def _write(project, rel, obj):
    blob = json.dumps(obj, ensure_ascii=False, indent=1)
    r = _call(project, "project.write_file",
              {"path": rel, "content": blob}, ["project:write"])
    return {"status": r["status"], "path": rel,
            "sha256": (r["output"] or {}).get("sha256"), "call_id": r["call_id"]}


def _dimension(findings: list[dict]) -> dict:
    verdicts = [f["status"] for f in findings if f["status"] in (PASS, FAIL, BLOCKED)]
    unverified = [f for f in findings if f["status"] in (NOT_VERIFIED, SKIPPED)]
    scored = [f for f in findings
              if f["status"] in (PASS, FAIL, BLOCKED) and f["severity"] in _SEV_SCORE]
    if any(v == BLOCKED for v in verdicts):
        status = BLOCKED
    elif any(v == FAIL for v in verdicts):
        status = FAIL
    elif verdicts and all(v == PASS for v in verdicts):
        status = PASS
    elif all(f["status"] == SKIPPED for f in findings):
        status = SKIPPED
    else:
        status = NOT_VERIFIED
    score = min((100 if f["status"] == PASS else _SEV_SCORE[f["severity"]]
                 for f in scored), default=None)
    return {"status": status, "score": score, "findings": findings,
            "coverage": {"measured": len(scored),
                         "skipped": sum(1 for f in unverified if f["status"] == SKIPPED),
                         "not_verified": sum(1 for f in unverified
                                             if f["status"] == NOT_VERIFIED)}}


def decide(findings: list[dict], exception: dict | None = None) -> tuple[str, list[str]]:
    """Severity-first: score can never clear a Critical or silently clear High."""
    crit = [f for f in findings if f["severity"] == CRITICAL and f["status"] != SKIPPED]
    if crit:
        return BLOCK_RELEASE, ["Critical: " + ", ".join(f["code"] for f in crit[:5])]
    high = [f for f in findings if f["severity"] == HIGH and f["status"] != SKIPPED]
    if high:
        if exception and exception.get("decision_id") and exception.get("actor"):
            return RELEASE, ["High accepted by explicit decision: " +
                             str(exception.get("decision_id"))]
        return HOLD, ["High: " + ", ".join(f["code"] for f in high[:5])]
    return RELEASE, ["no Critical/High finding"]


def quality_score(dims: dict[str, dict]) -> float | None:
    pairs = [(DIMENSIONS[k], d["score"]) for k, d in dims.items()
             if d.get("score") is not None]
    total = sum(w for w, _ in pairs)
    return round(sum(w * s for w, s in pairs) / total, 1) if total else None


def explicit_decision(project: str, decision_id: str, reason: str, actor: str) -> dict:
    """Record a HIGH override. Never cancels Critical findings."""
    obj = {"decision_id": str(decision_id)[:80], "reason": str(reason)[:400],
           "actor": str(actor)[:80], "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    return _write(project, "quality/release-decision.json", obj)


def evaluate(project: str, engine: str, do_build: bool = True) -> dict:
    """REAL end-to-end quality evaluation; no fabricated evidence."""
    inv = _call(project, "project.inspect", {}, ["project:read"])
    inv_out = inv.get("output") or {}
    all_rels = []
    for key in ("files", "test_files"):
        node = inv_out.get(key) or {}
        # project_snapshot wraps verified values as {"value": [...], ...}
        items = node.get("value") if isinstance(node, dict) else node
        for item in (items or []):
            all_rels.append(item.get("rel") if isinstance(item, dict) else str(item))
    rels = qc.relevant_files(sorted(set(all_rels)))
    files = {}
    read_calls = []
    for rel in rels:
        r = _call(project, "project.read_file", {"path": rel}, ["project:read"])
        read_calls.append({"path": rel, "call_id": r["call_id"], "status": r["status"]})
        if r["status"] == PASS and (r["output"] or {}).get("text"):
            files[rel] = r["output"]["text"]

    tests = _call(project, "tests.run", {}, ["tests:run"])
    sec = _call(project, "security.scan", {"mode": "export"}, ["security:read"])
    shot = _call(project, "shell.safe_run", {
        "command": "chromium-browser --headless=new --no-sandbox --disable-gpu "
                   "--window-size=1280,800 --screenshot=quality/preview.png "
                   "http://127.0.0.1:0/"}, ["shell:execute"])

    build_ms = None
    build = {"status": BLOCKED, "ok": False, "error": "build not executed",
             "evidence": {}, "artifacts": {}}
    if do_build:
        adapter = engine_adapter.adapter_for(engine)
        if adapter is None:
            build = {"status": BLOCKED, "ok": False, "error": "unknown engine",
                     "evidence": {}, "artifacts": {}}
        else:
            t0 = time.monotonic()
            try:
                br = adapter.build(project)
                build = {"status": br.status, "ok": br.ok, "error": br.error,
                         "evidence": br.evidence or {}, "artifacts": br.artifacts or {}}
            except Exception as exc:  # noqa: BLE001
                build = {"status": FAIL, "ok": False, "error": str(exc)[:300],
                         "evidence": {}, "artifacts": {}}
            build_ms = int((time.monotonic() - t0) * 1000)

    findings: list[dict] = []
    findings += qc.analyze_functionality(tests, sec)
    vfind, vcover = qc.analyze_visual(files, shot)
    findings += vfind
    findings += qc.analyze_ux(files, json.loads(
        (paths.project_dir(project) / "meta.json").read_text(
            encoding="utf-8")).get("type", "") if (
        paths.project_dir(project) / "meta.json").is_file() else "")
    findings += qc.analyze_accessibility(files)
    findings += qc.analyze_code(files)
    findings += qc.analyze_media(files)
    findings += qc.analyze_security(sec)

    if engine == "android" and build.get("ok"):
        # Android has no unittest runner; its REAL verification is the signed
        # APK + verify_artifacts gate. Do not pretend a unit suite exists, but
        # do not call a verified APK a Critical functional failure either.
        for f in findings:
            if f["code"] == "no-test-coverage":
                f["severity"] = MEDIUM
                f["message"] += "؛ پوشش عملکردی موتور اندروید verify_artifacts است"
        findings.append(qc.F(
            "functionality", "Low", "android-verify-real",
            "عملکرد/بستهبندی اندروید توسطverify_artifactsreal تأیید شد",
            {"signed": (build["evidence"] or {}).get("signed"),
             "sign_verified": (build["evidence"] or {}).get("sign_verified")},
            PASS))

    sizes: dict[str, int] = {}
    for rel in rels:
        try:
            sizes[rel] = (paths.project_dir(project) / rel).stat().st_size
        except OSError:
            continue
    if build.get("ok"):
        for rel, meta in (build.get("artifacts") or {}).items():
            if isinstance(meta, dict) and meta.get("size"):
                sizes["build:" + str(rel)] = int(meta["size"])

    findings += qc.analyze_performance(engine, build_ms,
                                       {"tests.run": tests.get("duration_ms", 0),
                                        "security.scan": sec.get("duration_ms", 0)},
                                       sizes)

    bf = []
    if build.get("status") != PASS or not build.get("ok"):
        bf.append(qc.F("build_packaging", "Critical", "build-failed",
                       "Buildreal شکست خورد یا اجرا نشد",
                       {"status": build.get("status"), "error": build.get("error")}))
    else:
        bf.append(qc.F("build_packaging", "Low", "build-real-pass",
                       "Buildreal با شواهد موفق بود",
                       {"evidence": str(build.get("evidence"))[:300]}, PASS))
    findings += bf

    dims = {}
    for key in DIMENSIONS:
        dims[key] = _dimension([f for f in findings if f["dimension"] == key])

    dec_path = paths.project_dir(project) / "quality/release-decision.json"
    exception = None
    if dec_path.is_file():
        try:
            exception = json.loads(dec_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            exception = None
    decision, reasons = decide(findings, exception)

    digests = {}
    for rel in ("index.html", "server.py", "AndroidManifest.xml", "package.json"):
        try:
            raw = (paths.project_dir(project) / rel).read_bytes()
            digests[rel] = {"size": len(raw),
                            "sha256": hashlib.sha256(raw).hexdigest()}
        except OSError:
            continue

    report = {
        "schema": "afnext-p5-quality-1.0", "project": project, "engine": engine,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "dimensions": dims, "findings": findings,
        "quality_score": quality_score(dims),
        "score_note": "Score is informational; severity-first decision overrides it.",
        "decision": decision, "reasons": reasons,
        "coverage": {"screenshot": vcover,
                     "tool_calls": {"inspect": inv["call_id"],
                                    "tests": tests["call_id"],
                                    "security": sec["call_id"],
                                    "screenshot": shot["call_id"],
                                    "reads": read_calls}},
        "build": {"status": build["status"], "ok": build["ok"],
                  "error": build["error"], "duration_ms": build_ms,
                  "evidence": build["evidence"], "artifacts": build["artifacts"]},
    }
    evidence = {
        "schema": "afnext-p5-release-evidence-1.0", "project": project,
        "engine": engine, "at": report["at"], "decision": decision,
        "reasons": reasons, "quality_score": report["quality_score"],
        "source_digests": digests, "artifacts": build["artifacts"],
        "build_evidence": build["evidence"],
        "tests_executed": {"status": tests["status"], "call_id": tests["call_id"],
                           "ran": (tests["output"] or {}).get("ran"),
                           "ok": (tests["output"] or {}).get("ok")},
        "security": {"status": sec["status"], "call_id": sec["call_id"],
                     "blocked": (sec["output"] or {}).get("blocked"),
                     "critical": (sec["output"] or {}).get("critical")},
        "visual_evidence": vcover,
        "known_limits": [f["message"] for f in findings
                         if f["status"] in (NOT_VERIFIED, SKIPPED)][:12],
        "not_verified": [f["code"] for f in findings if f["status"] == NOT_VERIFIED],
        "explicit_decision": exception,
    }
    report["evidence_files"] = {
        "report": _write(project, "quality/quality-report.json", report),
        "release": _write(project, "quality/release-evidence.json", evidence),
    }
    return report


def gate(report: dict) -> dict:
    """Small, explicit gate used by callers/API: no implicit release."""
    return {"decision": report.get("decision"), "reasons": report.get("reasons"),
            "quality_score": report.get("quality_score"),
            "critical": [f["code"] for f in report.get("findings", [])
                         if f["severity"] == CRITICAL],
            "high": [f["code"] for f in report.get("findings", [])
                     if f["severity"] == HIGH]}
