"""AF NEXT Phase 07 — independent critique, bounded revision, decision ledger.

Everything here is additive and reuses accepted contracts:
  * ONE canonical specialist registry (`independent-critic` lives in
    `core/specialists.EXTRA_ROLES`); no second catalog;
  * canonical run leases via `specialist_engine.start_run/reject_stale/
    record_result`;
  * one-shot human decisions via `confirmations` and trusted session principal
    via `panelauth.principal_for_session`;
  * project path containment via `core.paths` and hashes via
    `production_assets.sha256_file`.

This module never executes a specialist, provider or paid operation.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import core.config as config
from core import confirmations, opstatus, panelauth, paths, specialist_engine as se
from core import production_assets

SCHEMA = "af-critique-1.0"
ROLE = "independent-critic"
MAX_CYCLES = 3
RUBRIC_SCHEMA = "af-rubric-1.0"
_FLAGS = config.PROJECT_ROOT / "data" / "critic_flags.json"
_LEDGER = config.PROJECT_ROOT / "data" / "decision_ledger.json"
_KINDS = ("review", "verdict", "revision", "hold", "decision",
          "invalidation", "conflict", "migration")


class CritiqueError(RuntimeError):
    """Fail-closed error for invalid critique state or storage."""


@contextmanager
def _lock():
    _LEDGER.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(_LEDGER) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        os.close(fd)


def _empty() -> dict:
    return {"schema": SCHEMA, "entries": []}


def _load() -> dict:
    try:
        text = _LEDGER.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty()
    except OSError as exc:
        raise CritiqueError("ledger-unreadable:%s" % type(exc).__name__) from exc
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise CritiqueError("ledger-corrupt-json") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or \
            not isinstance(data.get("entries"), list):
        raise CritiqueError("ledger-invalid-root")
    for entry in data["entries"]:
        if (not isinstance(entry, dict) or
                not isinstance(entry.get("id"), str) or
                entry.get("kind") not in _KINDS or
                not isinstance(entry.get("seq"), int)):
            raise CritiqueError("ledger-invalid-entry")
    return data


def _save(data: dict) -> None:
    tmp = Path("%s.%d.%d.%s.tmp" % (_LEDGER, os.getpid(),
                                    threading.get_ident(), secrets.token_hex(6)))
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1,
                                  sort_keys=True), encoding="utf-8")
        tmp.replace(_LEDGER)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _append(entry: dict) -> dict:
    """Append ONCE under flock; duplicate IDs are rejected (replay protection)."""
    with _lock():
        data = _load()
        if any(e["id"] == entry["id"] for e in data["entries"]):
            raise CritiqueError("duplicate-entry:" + entry["id"])
        entry = dict(entry)
        entry["seq"] = len(data["entries"]) + 1
        entry["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        data["entries"].append(entry)
        _save(data)
    return entry


def entries(project: str = "", kind: str = "") -> list[dict]:
    data = _load()
    return [e for e in data["entries"]
            if (not project or e.get("project") == project) and
            (not kind or e.get("kind") == kind)]


def _latest(entry_id: str) -> dict | None:
    found = [e for e in _load()["entries"] if e.get("id") == entry_id]
    return found[-1] if found else None


# --------------------------------------------------------------- activation
def critic_enabled() -> bool:
    """Fail closed: critic is opt-in for workflows that create a critic stage."""
    try:
        data = json.loads(_FLAGS.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return False
    return data.get("enabled") is True


def set_critic_flag(enabled: bool) -> dict:
    if not isinstance(enabled, bool):
        raise CritiqueError("invalid-flag-type")
    _FLAGS.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(_FLAGS) + "." + secrets.token_hex(6) + ".tmp")
    try:
        tmp.write_text(json.dumps({"enabled": enabled}), encoding="utf-8")
        tmp.replace(_FLAGS)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"enabled": enabled}


# --------------------------------------------------------- immutable artifacts
def immutable_artifact(project: str, rel_path: str, content: str,
                       meta: dict) -> dict:
    """Write a versioned, never-overwritten artifact plus a hash sidecar."""
    base = paths.project_path(project, rel_path)      # containment
    if not isinstance(content, str) or not content.strip():
        raise CritiqueError("empty-artifact")
    version = 1
    target = base
    while target.exists():
        version += 1
        target = base.with_name("%s-v%d%s" % (base.stem, version, base.suffix))
    if target.exists():
        raise CritiqueError("artifact-version-conflict")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, content.encode("utf-8"))
    finally:
        os.close(fd)
    sha = production_assets.sha256_file(target)
    record = {"schema": SCHEMA, "project": project,
              "path": target.relative_to(paths.project_dir(project)).as_posix(),
              "version": version, "sha256": sha, "bytes": target.stat().st_size,
              **meta}
    sidecar = Path(str(target) + ".json")
    if sidecar.exists():
        raise CritiqueError("artifact-sidecar-conflict")
    sidecar.write_text(json.dumps(record, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    return record


def artifact_hash(project: str, rel_path: str) -> str:
    return production_assets.sha256_file(paths.project_path(project, rel_path))


# ------------------------------------------------------------------- rubric
def validate_rubric(rubric: dict) -> dict:
    """Deterministic validation; hard blockers are separate from scores."""
    problems = []
    if not isinstance(rubric, dict) or rubric.get("schema") != RUBRIC_SCHEMA:
        return {"ok": False, "problems": ["invalid-rubric-schema"]}
    criteria = rubric.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        problems.append("criteria-required")
    else:
        total = 0.0
        for i, item in enumerate(criteria):
            if not isinstance(item, dict):
                problems.append("invalid-criterion:%d" % i)
                continue
            weight, score = item.get("weight"), item.get("score")
            if not isinstance(weight, (int, float)) or isinstance(weight, bool) \
                    or weight < 0:
                problems.append("invalid-weight:%d" % i)
            else:
                total += float(weight)
            if not isinstance(score, (int, float)) or isinstance(score, bool) \
                    or not 0 <= float(score) <= 5:
                problems.append("invalid-score:%d" % i)
            if not str(item.get("explanation") or "").strip():
                problems.append("missing-explanation:%d" % i)
            if not isinstance(item.get("evidence") or [], list):
                problems.append("invalid-evidence:%d" % i)
        if abs(total - 1.0) > 1e-6:
            problems.append("weights-must-sum-to-one")
    alternatives = rubric.get("alternatives") or []
    if not isinstance(alternatives, list) or not alternatives:
        problems.append("alternatives-required")
    else:
        seen = set()
        for i, alt in enumerate(alternatives):
            if not isinstance(alt, dict) or not str(alt.get("id") or ""):
                problems.append("invalid-alternative:%d" % i)
                continue
            if alt["id"] in seen:
                problems.append("duplicate-alternative:" + str(alt["id"]))
            seen.add(alt["id"])
            if not str(alt.get("benefits") or "").strip():
                problems.append("missing-benefits:%d" % i)
            if not str(alt.get("tradeoffs") or "").strip():
                problems.append("missing-tradeoffs:%d" % i)
            if alt.get("rejected") and not str(
                    alt.get("rejection_reason") or "").strip():
                problems.append("missing-rejection-reason:%d" % i)
    if not isinstance(rubric.get("uncertainty") or [], list):
        problems.append("invalid-uncertainty")
    return {"ok": not problems, "problems": problems}


def evaluate(rubric: dict, findings: list) -> dict:
    verdict_validate = validate_rubric(rubric)
    if not verdict_validate["ok"]:
        return {"ok": False, "problems": verdict_validate["problems"]}
    aggregate = round(sum(float(c["weight"]) * float(c["score"])
                          for c in rubric["criteria"]), 6)
    blockers = [f for f in findings
                if f.get("severity") == "critical" or f.get("blocking") is True]
    rejected = [a for a in rubric["alternatives"] if a.get("rejected")]
    if any(not a.get("rejection_reason") for a in rejected):
        return {"ok": False, "problems": ["rejected-without-reason"]}
    decision = "reject" if blockers else ("pass" if aggregate >= 4.0
                                          else "revise")
    return {"ok": True, "aggregate": aggregate, "blockers": blockers,
            "blocked": bool(blockers), "decision": decision,
            "rejected_options": [a["id"] for a in rejected],
            "uncertainty": rubric.get("uncertainty") or []}


def new_finding(review_id: str, severity: str, description: str,
                evidence_ref: str, artifact_version: int, artifact_sha256: str,
                required_correction: str, owner: str) -> dict:
    if severity not in ("critical", "high", "medium", "low", "info"):
        raise CritiqueError("invalid-severity")
    for name, value in (("description", description),
                        ("evidence_ref", evidence_ref),
                        ("required_correction", required_correction),
                        ("owner", owner)):
        if not str(value or "").strip():
            raise CritiqueError("missing-" + name)
    fid = "f-" + hashlib.sha256(("|".join([review_id, severity, description,
                                           evidence_ref])).encode()).hexdigest()[:16]
    return {"id": fid, "review_id": review_id, "severity": severity,
            "description": str(description)[:2000],
            "evidence_ref": str(evidence_ref)[:300],
            "artifact_version": artifact_version, "artifact_sha256": artifact_sha256,
            "required_correction": str(required_correction)[:2000],
            "owner": str(owner)[:120], "status": "open",
            "history": [{"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                         "status": "open"}]}


def detect_conflicts(claims: list, findings: list, alternatives: list,
                     prior_decisions: list) -> list:
    """Never silently resolves a contradiction; emits explicit conflicts."""
    conflicts = []
    claim_by_artifact = {str(c.get("artifact_sha256")): c for c in claims or []
                         if isinstance(c, dict)}
    for f in findings or []:
        claim = claim_by_artifact.get(str(f.get("artifact_sha256")))
        if claim and str(claim.get("claim") or "").lower() != \
                str(f.get("required_correction") or "").lower():
            conflicts.append({"id": "c-" + f["id"], "kind": "producer-vs-critic",
                              "severity": f["severity"], "finding_id": f["id"],
                              "reason": "producer claim contradicts critic evidence",
                              "owner": f["owner"], "unresolved": True})
    by_artifact: dict[str, list] = {}
    for f in findings or []:
        by_artifact.setdefault(str(f.get("artifact_sha256")), []).append(f)
    for items in by_artifact.values():
        if len({str(x.get("required_correction")) for x in items}) > 1:
            conflicts.append({"id": "c-findings-" + items[0]["id"][:8],
                              "kind": "critic-vs-critic", "severity": "high",
                              "reason": "contradictory critic findings",
                              "owner": "independent-critic", "unresolved": True})
    for alt in alternatives or []:
        if alt.get("score", 0) >= 4 and any(
                x.get("severity") == "critical" for x in findings or []):
            conflicts.append({"id": "c-score-" + str(alt.get("id")),
                              "kind": "score-vs-blocker", "severity": "critical",
                              "reason": "high score cannot conceal a blocker",
                              "owner": "independent-critic", "unresolved": True})
    for d in prior_decisions or []:
        for f in findings or []:
            if str(d.get("artifact_sha256")) == str(f.get("artifact_sha256")) and \
                    str(d.get("selected_option")) != str(f.get("owner")):
                conflicts.append({"id": "c-prior-" + str(d.get("id"))[:10],
                                  "kind": "decision-vs-evidence",
                                  "severity": "high",
                                  "reason": "new evidence contradicts a recorded decision",
                                  "owner": str(d.get("decision_maker") or "unknown"),
                                  "unresolved": True})
    return conflicts


# ------------------------------------------------------------------ reviews
def start_review(project: str, workflow_id: str, stage_id: str,
                 producer_role: str, artifact_path: str,
                 artifact_sha256: str, acceptance_criteria: list,
                 session_id: str = "") -> dict:
    if not critic_enabled():
        raise CritiqueError("critic-not-enabled")
    if str(producer_role) == ROLE:
        raise CritiqueError("producer-cannot-be-own-critic")
    sha = artifact_hash(project, artifact_path)
    if sha != artifact_sha256:
        raise CritiqueError("artifact-hash-mismatch")
    run = se.start_run(project, ROLE, supersede=True)
    rid = "rv-" + hashlib.sha256(("|".join([project, workflow_id, stage_id,
                                            producer_role, artifact_sha256])
                                  ).encode()).hexdigest()[:18]
    review = {"id": rid, "kind": "review", "project": project,
              "workflow_id": workflow_id, "stage_id": stage_id,
              "producer_role": producer_role, "critic_role": ROLE,
              "artifact_path": artifact_path, "artifact_sha256": sha,
              "acceptance_criteria": list(acceptance_criteria or []),
              "session_id": str(session_id or ""), "cycle": 1,
              "max_cycles": MAX_CYCLES, "critic_run_key": run["key"],
              "critic_run_token": run["token"], "status": "OPEN"}
    return _append(review)


def _cycle_of(review_id: str) -> int:
    """Latest cycle for a review series, derived from append-only history."""
    revisions = sum(1 for e in _load()["entries"]
                    if e.get("review_id") == review_id and
                    e.get("kind") == "revision")
    return min(revisions + 1, MAX_CYCLES)


def _bound_review(review_id: str, run_token: str) -> dict:
    review = _latest(review_id)
    if review is None or review.get("kind") != "review":
        raise CritiqueError("unknown-review")
    if review.get("critic_run_token") != str(run_token or ""):
        raise CritiqueError("run-token-mismatch")
    return review


def record_verdict(review_id: str, run_token: str, findings: list,
                   rubric: dict, session_id: str = "") -> dict:
    review = _bound_review(review_id, run_token)
    stale = se.reject_stale(review["project"], ROLE, run_token)
    if not stale["accepted"]:
        raise CritiqueError("stale-critic-run")
    if artifact_hash(review["project"], review["artifact_path"]) != \
            review["artifact_sha256"]:
        raise CritiqueError("artifact-changed-since-review")
    evaluation = evaluate(rubric, findings or [])
    if not evaluation.get("ok"):
        raise CritiqueError("invalid-rubric:" + ",".join(evaluation["problems"][:3]))
    # A Critic can never approve its own findings: an accept verdict requires
    # a trusted, authenticated session principal (a separate human decision).
    if evaluation["decision"] in ("approve", "accepted"):
        raise CritiqueError("critic-cannot-self-approve")
    content = json.dumps({"findings": findings, "decision": evaluation,
                          "lineage": {"review_id": review_id,
                                      "cycle": review["cycle"],
                                      "artifact_sha256": review["artifact_sha256"]}},
                         ensure_ascii=False, indent=1)
    art = immutable_artifact(review["project"], "spec/critic-verdict.json",
                             content, {"review_id": review_id, "role": ROLE,
                                       "run_key": review["critic_run_key"]})
    verdict = {"id": "vd-" + review_id, "kind": "verdict",
               "project": review["project"], "workflow_id": review["workflow_id"],
               "stage_id": review["stage_id"], "review_id": review_id,
               "artifact_version": art["version"], "artifact_sha256": art["sha256"],
               "reviewed_sha256": review["artifact_sha256"],
               "findings": findings, "evaluation": evaluation,
               "decision": evaluation["decision"], "cycle": review["cycle"]}
    _append(verdict)
    se.record_result(review["project"], ROLE, run_token,
                     opstatus.PASS if evaluation["decision"] != "reject"
                     else opstatus.FAIL, [art["path"]])
    return verdict


def request_revision(review_id: str, run_token: str,
                     findings: list | None = None) -> dict:
    review = _bound_review(review_id, run_token)
    stale = se.reject_stale(review["project"], ROLE, run_token)
    if not stale["accepted"]:
        raise CritiqueError("stale-critic-run")
    cycle = _cycle_of(review_id)
    if cycle >= MAX_CYCLES:
        existing = next((e for e in entries(project=review["project"], kind="hold")
                         if e.get("review_id") == review_id), None)
        if existing is not None:
            return existing
        hold = {"id": "hold-" + review_id, "kind": "hold",
                "project": review["project"], "workflow_id": review["workflow_id"],
                "stage_id": review["stage_id"], "review_id": review_id,
                "cycle": cycle, "max_cycles": MAX_CYCLES,
                "reason": "critique-cycle-cap-exhausted",
                "status": "HOLD"}
        return _append(hold)
    notes = "; ".join("%s:%s:%s" % (f.get("id"), f.get("owner"),
                                    f.get("required_correction"))
                      for f in (findings or []))
    rev = {"id": "rev-" + review_id + "-" + str(cycle),
           "kind": "revision", "project": review["project"],
           "workflow_id": review["workflow_id"], "stage_id": review["stage_id"],
           "review_id": review_id, "cycle": cycle,
           "source_artifact_sha256": review["artifact_sha256"],
           "findings_note": notes[:2000], "status": "REVISION"}
    return _append(rev)


# -------------------------------------------------------------- decision ledger
def decide(project: str, workflow_id: str, stage_id: str, review_id: str,
           artifact_path: str, artifact_sha256: str, option: str,
           rejected: list, evidence_refs: list, cycle: int,
           confirm_token: str, session_id: str) -> dict:
    """Append an authenticated decision; one-shot confirmation is mandatory."""
    principal = panelauth.principal_for_session(session_id)
    if not principal:
        raise CritiqueError("authenticated-session-required")
    digest = hashlib.sha256(("|".join([project, workflow_id, stage_id,
                                      artifact_sha256, option, str(cycle)])
                             ).encode()).hexdigest()[:20]
    did = "dec-" + digest
    if any(e["id"] == did for e in _load()["entries"]):
        raise CritiqueError("decision-replay")
    try:
        proposal = confirmations.peek(confirm_token, project, session_id)
    except confirmations.ConfirmError as exc:
        raise CritiqueError("confirmation-invalid:" + str(exc)[:80]) from exc
    payload = proposal.get("payload") or {}
    expected = {"project": project, "workflow_id": workflow_id,
                "stage_id": stage_id, "review_id": review_id,
                "artifact_path": artifact_path,
                "artifact_sha256": artifact_sha256, "option": option,
                "cycle": cycle}
    for key, value in expected.items():
        if payload.get(key) != value:
            raise CritiqueError("confirmation-binding-mismatch:" + key)
    if artifact_hash(project, artifact_path) != artifact_sha256:
        raise CritiqueError("artifact-changed-since-confirmation")
    if not rejected or any(not str(r.get("reason") or "").strip() for r in rejected):
        raise CritiqueError("rejection-reason-and-owner-required")
    with _lock():
        data = _load()
        if any(e["id"] == did for e in data["entries"]):
            raise CritiqueError("decision-replay")
        confirmations.consume(confirm_token, project, session_id)
        entry = {"id": did, "kind": "decision", "project": project,
                 "workflow_id": workflow_id, "stage_id": stage_id,
                 "review_id": review_id, "artifact_path": artifact_path,
                 "artifact_sha256": artifact_sha256, "selected_option": option,
                 "rejected_options": rejected, "evidence_refs": list(evidence_refs or []),
                 "decision_maker": principal, "actor_trusted": True,
                 "cycle": cycle, "confirm_token_used": confirm_token,
                 "previous_version_links": [e["id"] for e in data["entries"]
                                            if e.get("stage_id") == stage_id][-5:]}
        entry["seq"] = len(data["entries"]) + 1
        entry["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        data["entries"].append(entry)
        _save(data)
    return entry


def invalidate(project: str, artifact_path: str, artifact_sha256: str) -> dict:
    """Append an invalidation for decisions bound to a changed artifact."""
    affected = [e for e in entries(project, "decision")
                if e.get("artifact_path") == artifact_path and
                e.get("artifact_sha256") != artifact_sha256]
    for e in affected:
        _append({"id": "inv-" + e["id"], "kind": "invalidation",
                 "project": project, "workflow_id": e.get("workflow_id"),
                 "stage_id": e.get("stage_id"), "decision_id": e["id"],
                 "reason": "bound-artifact-changed", "status": "INVALIDATED"})
    return {"ok": True, "invalidated": [e["id"] for e in affected]}


def migrate_legacy_approvals() -> dict:
    """Additive, non-destructive: legacy approvals stay legacy, never critic."""
    with _lock():
        data = _load()
        existing = {e["id"] for e in data["entries"]}
        added = 0
        legacy_root = config.PROJECT_ROOT / "data" / "workflow_state.json"
        try:
            state = json.loads(legacy_root.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        for wid, rec in (state.items() if isinstance(state, dict) else []):
            if not isinstance(rec, dict):
                continue
            for stage in rec.get("stages") or []:
                for approval in (stage or {}).get("approvals") or []:
                    aid = "legacy-" + str(approval.get("version") or "")
                    key = "mig-" + wid + "-" + aid
                    if key in existing:
                        continue
                    data["entries"].append({
                        "id": key, "kind": "migration", "project": rec.get("project", ""),
                        "workflow_id": wid, "stage_id": stage.get("id", ""),
                        "legacy_approval": approval,
                        "provenance": "legacy-workflow-approval",
                        "is_critic_approval": False,
                        "seq": len(data["entries"]) + 1,
                        "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
                    existing.add(key)
                    added += 1
        if added:
            _save(data)
    return {"ok": True, "migrated": added, "originals_preserved": True}


# ------------------------------------------------------- E2E workflow bridge
def validate_findings(project: str, findings: list, artifact_path: str,
                      artifact_sha256: str, criteria: list) -> dict:
    """Real schema/evidence validation: a bare claim can never become PASS."""
    problems = []
    if not isinstance(findings, list):
        return {"ok": False, "problems": ["findings-not-list"]}
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            problems.append("invalid-finding:%d" % i)
            continue
        for key in ("id", "severity", "description", "evidence_ref",
                    "artifact_sha256", "required_correction", "owner",
                    "status", "history"):
            if key not in f:
                problems.append("missing-finding-field:%d:%s" % (i, key))
        if f.get("severity") not in ("critical", "high", "medium", "low", "info"):
            problems.append("invalid-severity:%d" % i)
        if f.get("artifact_sha256") != artifact_sha256:
            problems.append("finding-artifact-mismatch:%d" % i)
        expected = "f-" + hashlib.sha256(("|".join([
            str(f.get("review_id") or ""), str(f.get("severity") or ""),
            str(f.get("description") or ""),
            str(f.get("evidence_ref") or "")])).encode()).hexdigest()[:16]
        if f.get("id") != expected:
            problems.append("forged-finding-id:%d" % i)
        ev = f.get("evidence")
        if not isinstance(ev, dict) or not ev.get("path"):
            problems.append("evidence-path-required:%d" % i)
        else:
            try:
                real = artifact_hash(project, str(ev["path"]))
            except (OSError, ValueError):
                problems.append("evidence-unreadable:%d" % i)
            else:
                if str(ev.get("sha256") or "") != real:
                    problems.append("evidence-hash-mismatch:%d" % i)
                if str(ev["path"]) != artifact_path:
                    problems.append("evidence-not-bound-to-artifact:%d" % i)
        if not isinstance(f.get("history"), list) or not f["history"]:
            problems.append("finding-history-required:%d" % i)
    return {"ok": not problems, "problems": problems}


def _stage_artifact(project: str, stage_id: str) -> dict:
    from core import workflow
    rec = workflow.by_project(project)
    if rec is None:
        raise CritiqueError("no-workflow")
    stage = next((s for s in rec["stages"] if s["id"] == stage_id), None)
    if stage is None or not stage.get("versions"):
        raise CritiqueError("stage-has-no-artifact")
    version = stage["versions"][-1]
    art = (version.get("artifacts") or [{}])[0]
    return {"workflow_id": rec["id"], "stage": stage, "version": version,
            "path": str(art.get("path") or ""),
            "sha256": str(art.get("sha256") or ""),
            "version_no": int(version.get("version") or 0)}


def execute_review(project: str, stage_id: str) -> dict:
    """Run a REAL deterministic review of the exact producer artifact.

    Independence is structural: own canonical role/lease/contract and no
    producer authority. No different-model independence is claimed.
    """
    from core import workflow
    info = _stage_artifact(project, stage_id)
    if not critic_enabled():
        raise CritiqueError("critic-not-enabled")
    criteria = list(info["stage"].get("acceptance_criteria") or [
        "artifact-exists", "sha256-verified", "non-empty"])
    review = start_review(project, info["workflow_id"], stage_id,
                          info["stage"].get("role", "producer"),
                          info["path"], info["sha256"], criteria)
    try:
        content = paths.project_path(project, info["path"]).read_text(
            encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CritiqueError("artifact-unreadable:%s" % type(exc).__name__) from exc
    if artifact_hash(project, info["path"]) != info["sha256"]:
        raise CritiqueError("artifact-hash-changed")
    findings = []
    if not content.strip():
        findings.append(new_finding(
            review["id"], "critical", "artifact is empty", info["path"],
            info["version_no"], info["sha256"], "produce real content", ROLE))
        findings[-1]["evidence"] = {"path": info["path"],
                                    "sha256": info["sha256"]}
    checks = [{"weight": 1.0 / max(1, len(criteria)),
               "score": 5.0 if content.strip() else 0.0,
               "explanation": "real artifact read and SHA-256 verified",
               "evidence": [info["path"]]} for _ in criteria]
    rubric = {"schema": RUBRIC_SCHEMA, "criteria": checks,
              "alternatives": [
                  {"id": "accept-current", "score": 5.0 if content.strip() else 0.0,
                   "benefits": "existing artifact satisfies structural checks",
                   "tradeoffs": "no semantic model review performed",
                   "rejected": False}],
              "uncertainty": ["semantic quality NOT VERIFIED without a model review"]}
    validation = validate_findings(project, findings, info["path"],
                                   info["sha256"], criteria)
    if not validation["ok"]:
        raise CritiqueError("finding-validation-failed:" +
                            ",".join(validation["problems"][:2]))
    conflicts = detect_conflicts([], findings, rubric["alternatives"],
                                 entries(project, "decision"))
    for c in conflicts:
        _append(dict(c, kind="conflict", project=project,
                     workflow_id=info["workflow_id"], stage_id=stage_id))
    verdict = record_verdict(review["id"], review["critic_run_token"],
                             findings, rubric)
    return {"ok": True, "review": review, "verdict": verdict,
            "findings": findings, "conflicts": conflicts,
            "artifact_path": info["path"], "artifact_sha256": info["sha256"],
            "version": info["version_no"]}


def revise_artifact(project: str, stage_id: str, review_id: str,
                    findings: list) -> dict:
    """Produce a NEW immutable artifact version from real review findings."""
    from core import workflow
    info = _stage_artifact(project, stage_id)
    if info["version_no"] >= MAX_CYCLES:
        raise CritiqueError("critique-cycle-cap-exhausted")
    try:
        old = paths.project_path(project, info["path"]).read_text(
            encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CritiqueError("source-artifact-unreadable") from exc
    note_lines = ["<!-- Phase 07 revision review=%s -->" % review_id]
    for f in findings or []:
        note_lines.append("<!-- %s owner=%s correction=%s -->" % (
            f.get("id"), f.get("owner"), str(f.get("required_correction"))[:180]))
    rel = Path(info["path"])
    new_rel = str(rel.with_name("%s-rev%d%s" % (
        rel.stem, info["version_no"] + 1, rel.suffix)))
    created = immutable_artifact(project, new_rel,
                                 old + "\n" + "\n".join(note_lines) + "\n",
                                 {"role": "producer", "review_id": review_id,
                                  "source_sha256": info["sha256"],
                                  "source_version": info["version_no"]})
    wf = workflow.record_external_version(project, stage_id, created["path"],
                                          created["sha256"],
                                          "critique-revision:%s" % review_id)
    if wf.get("status") != opstatus.PASS:
        raise CritiqueError("workflow-version-rejected")
    invalidate(project, info["path"], created["sha256"])
    return {"ok": True, "artifact": created, "workflow": wf,
            "source_sha256": info["sha256"]}


def run_series(project: str, stage_id: str) -> dict:
    """Execute the bounded, real Producer→Critic→revision→Critic series."""
    if critic_enabled() is False:
        raise CritiqueError("critic-not-enabled")
    rounds = []
    for cycle in range(1, MAX_CYCLES + 1):
        review = execute_review(project, stage_id)
        rounds.append({"cycle": cycle, "artifact_sha256":
                       review["artifact_sha256"],
                       "decision": review["verdict"]["decision"],
                       "review_id": review["review"]["id"]})
        if review["verdict"]["decision"] == "pass":
            return {"ok": True, "status": "READY_FOR_HUMAN_DECISION",
                    "rounds": rounds, "final": review}
        if cycle == MAX_CYCLES:
            req = {"id": "hold-" + review["review"]["id"], "kind": "hold",
                   "project": project, "stage_id": stage_id,
                   "review_id": review["review"]["id"], "cycle": cycle,
                   "status": "HOLD", "reason": "three-cycles-exhausted"}
            _append(req)
            return {"ok": True, "status": "HOLD", "rounds": rounds,
                    "final": review, "hold": req}
        revised = revise_artifact(project, stage_id,
                                  review["review"]["id"], review["findings"])
        rounds[-1]["revision_sha256"] = revised["artifact"]["sha256"]
    return {"ok": True, "status": "HOLD", "rounds": rounds}


def human_decision(project: str, stage_id: str, review_id: str,
                   option: str, confirm_token: str, session_id: str,
                   reject_note: str = "") -> dict:
    """Record an authenticated human decision and advance the real workflow."""
    from core import workflow
    review = _latest(review_id)
    if review is None or review.get("kind") != "review":
        raise CritiqueError("unknown-review")
    info = _stage_artifact(project, stage_id)
    decision = decide(project, review["workflow_id"], stage_id, review_id,
                      info["path"], info["sha256"], option,
                      [{"option": "revise", "reason": reject_note or
                        "user selected another option", "owner": "user"}],
                      [info["path"]], _cycle_of(review_id),
                      confirm_token, session_id)
    approved = workflow.approve(project, stage_id, info["version_no"],
                                decision["id"])
    return {"ok": True, "decision": decision, "workflow": approved}
