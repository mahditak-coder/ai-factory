"""AF-15 — اصلاح خودکار کنترل‌شده: snapshot → fix مدل → کنترل محدوده →
آزمون دوباره → rollback در شکست. بدون حلقهٔ بی‌نهایت، بدون تغییر بی‌اجازه
خارج از محدوده.

Cycle semantics (ONE controlled round per call — no infinite loop):
  1. quality.evaluate (real: static + own tests + contracts)
  2. PASS  → no model call at all; report is honest
  3. FAIL  → snapshot EVERY file (sha256 manifest + content copies) →
             ONE fix_request to the build session (only the error files,
             explicitly scoped) → wait_done (bounded)
  4. scope control: recompute the manifest — a file changed/added/deleted
     OUTSIDE the target set ⇒ rollback (the model went out of scope)
  5. re-run quality — kept ONLY if fully green (status == PASS);
     unchanged-or-worse (or partial improvement) ⇒ rollback
  6. rollback restores the snapshot and verifies byte-identity; a failed
     rollback is reported loudly (never silent success)
  7. report persisted in .factory/quality.json + history entry

Model failure (provider error / timeout) is an HONEST failure path: no
fake fix, rollback if anything changed, status NEEDS_REVIEW/FAIL.
"""

import json
import logging
import shutil
import sys
import time
from pathlib import Path

import core.config as config
from core import paths, factory
from core import quality as qualitymod

log = logging.getLogger("ai-factory.autofix")

SNAP_DIRNAME = "af15-snap"
REPORT_NAME = "quality.json"
FIX_WAIT_S = 300.0             # bounded model wait — no infinite loop
MAX_TARGET_FILES = 15


def _manifest(d: Path) -> dict:
    """sha256 of every project file (excludes .factory/node_modules/…)."""
    import hashlib
    out = {}
    skip = config.EXPORT_EXCLUDE_NAMES
    for f in sorted(d.rglob("*")):
        if not f.is_file():
            continue
        if set(f.parts) & skip or f.suffix.lower() == ".pyc":
            continue
        rel = f.relative_to(d).as_posix()
        out[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def _scope_report(before: dict, after: dict, targets: set) -> dict:
    changed = sorted(k for k in after if k in before and after[k] != before[k])
    added = sorted(k for k in after if k not in before)
    deleted = sorted(k for k in before if k not in after)
    touched = set(changed) | set(added) | set(deleted)
    out_of_scope = sorted(touched - targets)
    return {"changed": changed, "added": added, "deleted": deleted,
            "out_of_scope": out_of_scope,
            "in_scope": bool(not out_of_scope)}


def _rollback(d: Path, manifest: dict, snap_dir: Path) -> dict:
    """Restore the project to the exact snapshot state; verify byte-identity.
    Returns {"ok": bool, "restored": [...], "removed": [...], "error": ...}."""
    restored, removed, problems = [], [], []
    snap_root = snap_dir / "files"
    # 1) delete files that did not exist before
    for f in sorted(d.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(d).as_posix()
        if rel.startswith(".factory/"):
            continue
        if rel not in manifest:
            try:
                f.unlink()
                removed.append(rel)
            except OSError as exc:
                problems.append(f"delete {rel}: {exc}")
    # 2) restore every pre-existing file byte-for-byte
    for rel, want in manifest.items():
        dst = d / rel
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src = snap_root / rel
            if not src.is_file():
                problems.append(f"snapshot missing: {rel}")
                continue
            data = src.read_bytes()
            if not dst.exists() or \
                    __import__("hashlib").sha256(dst.read_bytes()).hexdigest() != want:
                dst.write_bytes(data)
                restored.append(rel)
        except OSError as exc:
            problems.append(f"restore {rel}: {exc}")
    # 3) verify
    got = _manifest(d)
    ok = got == manifest and not problems
    return {"ok": ok, "restored": restored, "removed": removed,
            "error": "; ".join(problems)[:300] or None}


def _save_report(d: Path, report: dict) -> None:
    fd = d / ".factory"
    fd.mkdir(parents=True, exist_ok=True)
    (fd / REPORT_NAME).write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")


def _record_experience(project: str, pre: dict, post: dict,
                       scope: dict) -> None:
    """AF-16 — store the PROVEN fix as an experience (only fully green
    outcomes reach here). Never fatal: a storage problem is logged, the
    green fix itself stays valid."""
    try:
        from core import experience
        kinds = sorted({e.get("kind") or "?" for e in pre["errors"]}
                       | {e.get("kind") or "?" for e in
                          ((pre["tests"].get("contract") or {}).get("errors")
                           or [])})
        ptype = factory._meta_type(project)
        tech = "Python %s" % sys.version.split()[0] if ptype in (
            "api", "fullstack", "fullstack-python", "ai-tool") else None
        experience.record(
            kind="+".join(kinds)[:ERRK_MAX] or "contract-fail",
            project_type=ptype,
            error="؛ ".join((e.get("kind") + ": " + (e.get("message") or ""))
                            for e in (pre["errors"][:2]))[:300] or "contract",
            solution=("اصلاح فایل(های) %s با یک دور اصلاح مدلِ محدود به هدف؛ "
                      "سپس آزمون کامل سبز شد"
                      % ",".join((scope.get("changed") or [])[:6]))[:1000],
            proof={"ran": bool((post.get("tests") or {}).get("ran")),
                   "ok": bool((post.get("tests") or {}).get("all_ok")),
                   "source": "testrun_combined پس از اصلاح"},
            tech=tech, env=None, failures=None,
            source_project=project, by="autofix")
    except Exception:  # noqa: BLE001 — enhancement, never fatal
        log.warning("experience record failed for %s", project, exc_info=True)


def _note_fix_failure(project: str, pre: dict) -> None:
    """AF-16 — a PROVEN observed failure: the recorded solution (if any)
    for this (kind, type) failed again here. Only appends when a matching
    experience exists — never fabricates."""
    try:
        from core import experience
        kinds = sorted({e.get("kind") or "?" for e in pre["errors"]})
        if not kinds:
            return
        ptype = factory._meta_type(project)
        experience.note_failure(
            kinds[0], ptype,
            "این راه‌حل در پروژهٔ «%s» نیز شکست خورد (rollback شد)"
            % project[:40])
    except Exception:  # noqa: BLE001 — enhancement, never fatal
        log.warning("experience failure note failed for %s", project,
                    exc_info=True)


ERRK_MAX = 80


def run(project: str, *, agent: str | None = None,
        boot: bool = True, strategy: str | None = None) -> dict:
    """One controlled auto-fix cycle for *project* (must be a factory
    project). Never touches files outside the project. Returns the full
    quality report with fix/rollback evidence."""
    d = paths.project_dir(project)
    # AF-41 — capture the pre-change baseline BEFORE any model edit, so a
    # later build()/reanalysis cannot erase the real before/after diff.
    try:
        from core import project_snapshot
        report_baseline = project_snapshot.set_baseline(project)
    except Exception:  # noqa: BLE001 — optional evidence
        report_baseline = {"status": "unavailable"}
    pre = qualitymod.evaluate(project, boot=boot)
    report = {
        "project": project, "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "before": pre, "fix": None, "after": None, "status": pre["status"],
        "rolled_back": False, "note": "", "baseline": report_baseline,
    }
    if pre["status"] == "PASS":
        report["note"] = "خطای قطعی یا آزمون سرخ وجود ندارد — اصلاحی انجام نشد"
        _save_report(d, report)
        factory.append_history(project, "quality",
                               "AF-15 quality=PASS (no fix needed)")
        return report

    # ---- target files: only files named in DEFINITE errors + contract ----
    errs = list(pre["errors"])
    for e in (pre["tests"].get("contract", {}).get("errors") or []):
        if not e.get("file"):
            # contract errors may lack a file (e.g. process-dead) — aim the
            # model at the project entry for the project's type
            t = ""
            try:
                t = factory._meta_type(project)
            except Exception:            # noqa: BLE001 — cosmetic only
                pass
            e = dict(e, file=("index.html" if t in (
                "webpage", "webapp", "pwa", "shop", "dashboard", "webtool")
                else "server.py"))
        errs.append(e)
    targets = sorted({str(e.get("file") or "").strip()
                      for e in errs if str(e.get("file") or "").strip()}
                     )[:MAX_TARGET_FILES]
    if not targets:
        report["note"] = ("خطا فایل هدف مشخص نمی‌کند — اصلاح خودکار انجام "
                          "نشد (NEEDS_REVIEW صادقانه)")
        _save_report(d, report)
        factory.append_history(project, "quality",
                               "AF-15 no-target-files (review)")
        return report

    # ---- snapshot EVERYTHING (content copies + manifest) ----
    snap_dir = d / ".factory" / SNAP_DIRNAME
    if snap_dir.exists():
        shutil.rmtree(snap_dir)
    (snap_dir / "files").mkdir(parents=True)
    skip = config.EXPORT_EXCLUDE_NAMES
    for f in sorted(d.rglob("*")):
        if not f.is_file() or set(f.parts) & skip or f.suffix.lower() == ".pyc":
            continue
        rel = f.relative_to(d).as_posix()
        dst = snap_dir / "files" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
    before_manifest = _manifest(d)

    # ---- ONE bounded model fix round, scoped to the target files ----
    fix = {"requested_files": targets, "model_ok": None, "scope": None}
    try:
        req = factory.fix_request(project, errs, cycle_no=2, agent=agent,
                                  files=targets, strategy=strategy)
        done = factory.wait_done(req["session_id"], timeout=FIX_WAIT_S,
                                 since_message_id=req["message_id"],
                                 headless=True, project=project)
        fix["model_ok"] = bool(done)
        if not done:
            fix["note"] = "مدل در مهلت پاسخ نداد (بدون خطای مدل ثبت‌شده)"
    except Exception as exc:                 # noqa: BLE001 — honest failure
        fix["model_ok"] = False
        fix["note"] = "درخواست اصلاح ناموفق: %s" % str(exc)[:200]
    report["fix"] = fix
    if not fix["model_ok"]:
        # The model never produced a verified turn — do NOT evaluate or claim
        # a repair; roll back to the exact snapshot and report honestly.
        rb = _rollback(d, before_manifest, snap_dir)
        report["rolled_back"] = bool(rb.get("ok"))
        report["rollback"] = rb
        report["status"] = "FAIL"
        report["note"] = "مدل اصلاح را اجرا نکرد —rollback %s" % (
            "موفق" if rb.get("ok") else "ناموفق!")
        _save_report(d, report)
        shutil.rmtree(snap_dir, ignore_errors=True)
        return report

    # ---- scope control: out-of-scope change ⇒ rollback ----
    after_manifest = _manifest(d)
    scope = _scope_report(before_manifest, after_manifest, set(targets))
    fix["scope"] = scope
    if not scope["in_scope"]:
        rb = _rollback(d, before_manifest, snap_dir)
        report["rolled_back"] = bool(rb.get("ok"))
        report["note"] = ("تغییر خارج از فایلهای هدف (%s) — rollback %s"
                          % (", ".join(scope["out_of_scope"][:5]),
                             "موفق" if rb["ok"] else "ناموفق!"))
        report["rollback"] = rb
        post = qualitymod.evaluate(project, boot=boot)
        report["after"] = post
        report["status"] = post["status"]
        _save_report(d, report)
        factory.append_history(project, "auto-fix-rolled-back",
                               "out-of-scope: " + ", ".join(
                                   scope["out_of_scope"][:5]))
        shutil.rmtree(snap_dir, ignore_errors=True)
        return report

    # AF-40 — real post-change diff, targeted re-analysis and related test
    # selection. Results are evidence; PASS still requires the real test gate.
    try:
        from core import project_snapshot
        # Real before/after diff from the independent baseline (AF-41).
        diff = project_snapshot.changed_files(project)
        report["file_diff"] = diff
        changed = list(dict.fromkeys(
            (diff.get("modified") or []) + (diff.get("added") or []) +
            (diff.get("removed") or []) + list(scope.get("changed") or [])))
        re = project_snapshot.reanalyze(project, changed)
        report["code_impact_after"] = re.get("impact") or {}
        report["changed_files"] = changed
        rel = project_snapshot.related_test_command(project, changed)
        report["related_test"] = rel
        if rel.get("command") and boot:
            ran = project_snapshot.run_related_tests(project, changed)
            report["related_test_run"] = ran
    except Exception as exc:  # noqa: BLE001 — optional evidence
        report["code_intelligence_note"] = str(exc)[:200]

    # ---- re-test: keep ONLY if fully green ----
    post = qualitymod.evaluate(project, boot=boot)
    report["after"] = post
    if post["status"] == "PASS":
        report["status"] = "PASS"
        report["note"] = "اصلاح سبز شد — آزمون‌ها دوباره اجرا و پاس شدند"
        _record_experience(project, pre, post, scope)
        factory.append_history(
            project, "auto-fix",
            "fixed files=%s" % ",".join(scope["changed"][:8]))
    else:
        rb = _rollback(d, before_manifest, snap_dir)
        report["rolled_back"] = bool(rb.get("ok"))
        report["rollback"] = rb
        report["status"] = post["status"]
        report["note"] = ("خطا پس از اصلاح باقی/بدتر شد — rollback %s؛ "
                          "نیازمند بازبینی انسانی"
                          % ("موفق" if rb["ok"] else "ناموفق!"))
        _note_fix_failure(project, pre)
        factory.append_history(project, "auto-fix-rolled-back",
                               "not-green after fix; files=%s"
                               % ",".join(scope["changed"][:8]))
    _save_report(d, report)
    shutil.rmtree(snap_dir, ignore_errors=True)
    return report
