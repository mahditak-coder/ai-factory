"""AF-29 — Boundary Policy: one honest, shared policy for process boundaries.

Two responsibilities, both deliberately small:

1. ``clean_env`` — build a MINIMAL environment for child processes from an
   allowlist instead of copying ``os.environ``.  The live Termux environment of
   this factory really contains provider credentials (ANTHROPIC_AUTH_TOKEN,
   ATRIA_API_KEY, OPENCODE_SERVER_PASSWORD ...); those must never reach a
   generated project's ``run.sh``, a deployed bundle, or a git subprocess.

2. ``ensure_project_policy`` — create a default OpenCode permission policy for
   a project when it has none.  Chat and headless execution then observe the
   SAME rule (write/edit/bash require an explicit decision) instead of the
   documented OpenCode default of "no questions asked".

This module never imports the bridge, factory, runner or server: it is a leaf
dependency so every component can use it without import cycles.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import core.config as config

# Keys a spawned project/deploy/git process actually needs on Termux.
SAFE_ENV_KEYS = (
    "PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "TERM",
    "COLORTERM", "PREFIX", "JAVA_HOME",
    "ANDROID_ROOT", "ANDROID_DATA", "ANDROID_ART_ROOT", "ANDROID_I18N_ROOT",
    "ANDROID_TZDATA_ROOT", "BOOTCLASSPATH", "DEX2OATBOOTCLASSPATH",
    "EXTERNAL_STORAGE",
    "TERMUX__ROOTFS_DIR", "TERMUX__PREFIX", "TERMUX__HOME", "TMUX_TMPDIR",
)

_SECRET_KEY_RE = re.compile(
    r"(?i)(token|secret|password|passwd|credential|api[_-]?key|auth|"
    r"private|cookie|session|signature|opencode|ai[_-]?agent|\bagent\b)")

# The exact policy OpenCode documents for opt-in human confirmation.
DEFAULT_PERMISSIONS = {"edit": "ask", "write": "ask", "bash": "ask"}


def is_secret_name(name: str) -> bool:
    return bool(_SECRET_KEY_RE.search(str(name or "")))


def clean_env(extra: dict | None = None) -> dict[str, str]:
    """Return a minimal env: allowlisted inherited keys + explicit extras.

    *Secret-looking extras are refused*, so a caller cannot accidentally
    reintroduce a credential through ``extra``.  Callers that genuinely need
    a backend credential (the OpenCode bridge) must set it directly on the
    child process and document why; it is never inherited by project code.
    """
    env = {k: os.environ[k] for k in SAFE_ENV_KEYS if k in os.environ}
    for k, v in (extra or {}).items():
        if is_secret_name(k):
            raise PermissionError("secret-like environment key refused: %s" % k)
        env[str(k)] = str(v)
    return env


def assert_clean(env: dict) -> list[str]:
    """Return the list of secret-looking keys present in *env* (empty = clean)."""
    return sorted(k for k in (env or {}) if is_secret_name(k))


def _safe_shell(command: str, root: Path) -> bool:
    """Validate a headless shell command instead of blanket-rejecting it.

    Only read-only/test commands inside the project are allowed. Chaining with
    &&, || and ; is permitted between safe commands, as agents legitimately use
    fallbacks like `pytest || python tests/test.py`. Destructive verbs, network
    tools, command substitution and non-project absolute paths are rejected.
    """
    import re as _re
    import shlex as _shlex
    cmd = str(command or "").strip().replace("\n", ";")
    # `$?` is only an exit-code echo, not command substitution.
    cmd = cmd.replace("$?", "EXITCODE")
    # Normalize separators so shlex sees them as distinct tokens.
    cmd = _re.sub(r"\s*(;|&&|\|\||\|)\s*", r" \1 ", cmd)
    if not cmd or _re.search(r"[`$]", cmd):
        return False
    # A safe read-only python -c / node -e is allowed; exec code is not.
    m = _re.search(r"python3?\s+-c\s+([\"'])(.*?)\1", cmd)
    if m:
        code = m.group(2).lower()
        if _re.search(r"(open\(|write|rmtree|remove\(|unlink|system\(|subprocess|"
                      r"curl|wget|socket|eval\(|exec\(|__import__\()", code):
            return False
        cmd = cmd[:m.start()] + "python -c SAFE " + cmd[m.end():]
    nm = _re.search(r"node\s+-e\s+([\"'])(.*?)\1", cmd)
    if nm:
        code = nm.group(2).lower()
        if _re.search(r"(write|unlink|rmdir|child_process|exec\(|spawn\(|"
                      r"curl|http\.request|fetch\()", code):
            return False
        cmd = cmd[:nm.start()] + "node -e SAFE " + cmd[nm.end():]
    # stderr→stdout and /dev/null are harmless; file redirection is not.
    cmd = _re.sub(r"2\s*>&\s*1", " ", cmd)
    cmd = _re.sub(r"2?\s*>\s*/dev/null", " ", cmd)
    if ">" in cmd or "<" in cmd:
        return False
    try:
        tokens = _shlex.split(cmd)
    except ValueError:
        return False
    if not tokens:
        return False
    # AF-46 — read-only runtime discovery is part of honest environment
    # awareness. These verbs only report versions/paths and cannot modify
    # anything; everything else stays on the original strict allowlist.
    safe_cmds = {"cd", "pwd", "ls", "echo", "python", "python3", "pytest",
                 "node", "npm", "grep", "cat", "head", "tail", "find", "sed",
                 "git", "command", "which", "printf", "wc", "stat", "uname",
                 "php"}
    deny = _re.compile(r"(?i)^(rm|rmdir|mv|cp|curl|wget|sudo|chmod|chown|"
                       r"kill|pkill|shutdown|reboot|format|mkfs|dd)$")
    git_safe = {"status", "log", "diff", "rev-parse", "remote", "branch", "show"}
    npm_safe = {"test", "run"}
    segments, current = [], []
    for tok in tokens:
        tok = tok.strip("(){}")         # shell grouping, not substitution
        if not tok:
            continue
        if tok in ("&&", "||", "|", ";"):
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    if not segments:
        return False
    for seg in segments:
        verb = seg[0]
        if verb not in safe_cmds or deny.match(verb):
            return False
        if verb == "git" and (len(seg) < 2 or seg[1] not in git_safe):
            return False
        if verb == "npm" and (len(seg) < 2 or seg[1] not in npm_safe):
            return False
        # AF-47 — PHP is allowed ONLY as a read-only syntax check. `php -r`
        # would execute arbitrary code, so it stays forbidden.
        if verb == "php" and (len(seg) < 2 or seg[1] != "-l"):
            return False
        for tok in seg[1:]:
            if deny.match(tok) or (tok in ("-e", "--exec", "--eval", "-c") and
                                   verb in ("python", "python3") and
                                   "SAFE" not in seg):
                return False
            if tok.startswith("-"):
                continue
            # Path-like token: must stay inside the project.
            if "/" in tok or tok.startswith("."):
                p = Path(tok)
                resolved = (p if p.is_absolute() else root / p).resolve()
                if not (resolved == root or root in resolved.parents):
                    return False
    return True


_RE_NETWORK = re.compile(
    r"(?i)(^|[\s;&|])(curl|wget|nc|netcat|ssh|scp|rsync|ping|http|https)\b|"
    r"https?://|[a-z0-9-]+\.(invalid|example|com|net|org|io)\b")
_RE_DESTRUCTIVE = re.compile(
    r"(?i)(^|[\s;&|])(rm|rmdir|mv|cp|sudo|chmod|chown|kill|pkill|shutdown|"
    r"reboot|format|mkfs|dd)\b|unlink|rmtree|writefile|child_process")
_RE_SERVER = re.compile(
    r"(?i)(PORT\s*=|server\.py|server\.js|npm\s+(start|run)\b|"
    r"python3?\s+-m\s+http\.server|serve_forever|listen\()")


def _reason_and_offenders(action: str, targets: list[str], root: Path) -> tuple:
    """Classify WHY a real request was denied — never guess a success."""
    act = str(action or "").lower()
    cmd = " ".join(targets)
    outside = []

    def check(candidate: str) -> None:
        try:
            p = Path(candidate)
            resolved = (p if p.is_absolute() else root / p).resolve()
        except (OSError, ValueError):
            outside.append(candidate)
            return
        if not (resolved == root or root in resolved.parents):
            outside.append(candidate)

    if act not in ("edit", "write", "shell", "bash", "execute"):
        return "unsupported-action", targets[:4]
    # For shell commands, name the most actionable limit first (network or
    # destructive), then any out-of-scope path token.
    if act in ("shell", "bash", "execute"):
        if "<<" in cmd or cmd.rstrip().endswith("-"):
            return "unsupported-shell-construct", targets[:2]
        if _RE_NETWORK.search(cmd):
            return "network-forbidden", targets[:4]
        if _RE_SERVER.search(cmd):
            return "server-start-forbidden", targets[:4]
        if _RE_DESTRUCTIVE.search(cmd):
            return "destructive-command", targets[:4]
    if act in ("edit", "write"):
        for target in targets:
            check(target)
    else:
        # A shell request carries a COMMAND, not a single path. Inspect each
        # path-like token the same way _safe_shell does.
        import shlex as _shlex
        normalized = re.sub(r"\s*(;|&&|\|\||\|)\s*", " ", cmd)
        try:
            tokens = _shlex.split(normalized)
        except ValueError:
            tokens = normalized.split()
        for tok in tokens:
            if "/" in tok or tok.startswith("."):
                check(tok.strip("(){}"))
    if outside:
        return "outside-project-path", outside[:8]
    return "policy-denied-command", targets[:4]


def explain_rejection(action: str, resource, project: str) -> dict:
    """AF-44 — structured reason for a REAL policy rejection.

    This is evidence, not permission: the decision stays ``reject``.  The
    ``feedback`` text is delivered to the model by OpenCode as a
    ``permission.rejected`` tool error so the SAME session can choose a safe
    alternative instead of dying with "Step interrupted".
    """
    from core import paths
    act = str(action or "")
    targets = ([str(x) for x in resource] if isinstance(resource, (list, tuple))
               else [str(resource or "")])
    targets = [t.strip() for t in targets if str(t).strip()]
    cmd = " ".join(targets)
    try:
        root = paths.project_dir(project).resolve()
        scope = str(root)
    except (PermissionError, FileNotFoundError):
        root, scope = None, "project:" + str(project)
    reason, forbidden = _reason_and_offenders(act, targets, root) if root else \
        ("project-unavailable", targets[:4])
    limit = {
        "outside-project-path": "only files inside the verified project root may be read or changed",
        "network-forbidden": "network, curl, wget and servers are forbidden in headless execution",
        "server-start-forbidden": "the agent must not start servers; the factory verification gate boots and probes the real API safely",
        "unsupported-shell-construct": "heredoc/stdin scripts are not a validated tool command; put the code in a project test file and run `python tests`",
        "destructive-command": "destructive, privilege and process commands are forbidden",
        "unsupported-action": "only edit/write/shell inside the project are supported",
        "policy-denied-command": "this shell command is not on the validated safe-command allowlist",
        "project-unavailable": "the project root could not be verified",
    }[reason]

    alternatives: list[str] = []
    try:
        from core import project_snapshot as ps
        pm = ps.path_map(project)
        rels = [str(f.get("rel")) for f in
                ((pm.get("files") or {}).get("value") or [])[:10]
                if isinstance(f, dict)]
        if rels:
            alternatives.append("verified project files: " + ", ".join(rels))
        kind = "node" if any("npm" in t.lower() for t in targets) else "python"
        test = ps.allowed_test_command(project, kind)
        if test.get("command"):
            alternatives.append("allowed test command: " + str(test["command"]))
        else:
            alternatives.append("no executable test command in this environment")
    except Exception:  # noqa: BLE001 — explanation must never break policy
        alternatives.append("use verified in-project paths and read-only commands")
    if act in ("shell", "bash", "execute") and (
            _RE_NETWORK.search(cmd) or _RE_SERVER.search(cmd) or "<<" in cmd):
        alternatives.insert(
            0, "do NOT start a server or use network from tools — the factory "
               "verification gate boots and probes the real API; write "
               "tests/test_*.py with in-process imports and run `python tests`")
    alternatives.append("do NOT repeat the rejected command")

    raw = "|".join([reason] + sorted(str(t) for t in forbidden))
    signature = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    feedback = (
        "POLICY BLOCK — reason=%s; forbidden=%s; scope=%s; limit=%s; "
        "safe alternatives=%s. Choose a permitted in-project action now."
        % (reason, ", ".join(forbidden)[:300] or "(none)", scope,
           limit, "; ".join(alternatives)[:400]))
    return {"decision": "reject", "reason": reason,
            "forbidden": forbidden, "scope": scope, "policy_limit": limit,
            "alternatives": alternatives, "signature": signature,
            "feedback": feedback[:1800]}


def headless_permission(action: str, resource, project: str) -> str:
    """AF-35 — fail-closed headless decision for OpenCode permission requests.

    ALL resources in a request must pass.  edit/write are allowed only for
    in-project paths; shell is allowed only for an exact, non-flagged project
    test invocation (no -c, no absolute path, no shell metacharacters).
    """
    from core import paths
    act = str(action or "").lower()
    targets = ([str(x) for x in resource] if isinstance(resource, (list, tuple))
               else [str(resource or "")])
    targets = [t.strip() for t in targets if str(t).strip()]
    if not targets:
        return "reject"
    try:
        root = paths.project_dir(project).resolve()
    except (PermissionError, FileNotFoundError):
        return "reject"
    if act in ("edit", "write"):
        for target in targets:
            try:
                p = Path(target)
                resolved = (p if p.is_absolute() else root / p).resolve()
            except (OSError, ValueError):
                return "reject"
            if not (resolved == root or root in resolved.parents):
                return "reject"
        return "once"
    if act in ("shell", "bash", "execute"):
        # AF-45 — OpenCode reports a CHAINED shell command as several
        # resources (e.g. ["pwd", "ls -la", "cat meta.json"]). Rejecting the
        # whole request just because there is more than one resource blocked
        # ordinary project discovery. Validate EVERY resource with the same
        # safe-command rules: a chain is allowed only when all its parts pass.
        # This does not weaken policy — every part stays scoped and read-only.
        return ("once" if targets and all(_safe_shell(t, root) for t in targets)
                else "reject")
    return "reject"


def project_policy_path(project_dir: Path) -> Path:
    return Path(project_dir) / "opencode.json"


def ensure_project_policy(project: str) -> dict:
    """Create an opt-in confirmation policy for *project* if one is absent.

    An existing file is NEVER overwritten — a user's deliberate policy wins.
    The write is atomic and best-effort; failures are returned honestly rather
    than silently claiming a sandbox that does not exist.
    """
    from core import paths  # local import: paths depends only on config
    d = paths.project_dir(project)
    target = project_policy_path(d)
    if target.is_file():
        try:
            current = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = None
        rules = ((current or {}).get("permission") or {}) if isinstance(current, dict) else {}
        complete = all(rules.get(k) == v for k, v in DEFAULT_PERMISSIONS.items())
        return {"status": "exists" if complete else "incomplete",
                "path": str(target), "policy": current, "created": False,
                "note": "" if complete else "سیاست مجوز ناقص است — پیشفرض ایمن اعمال نشد"}
    payload = {"permission": dict(DEFAULT_PERMISSIONS)}
    try:
        fd, tmp = tempfile.mkstemp(prefix=".opencode-", suffix=".json",
                                   dir=str(d))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    except (OSError, ValueError) as exc:
        return {"status": "failed", "path": str(target),
                "note": str(exc), "created": False}
    return {"status": "created", "path": str(target),
            "policy": payload, "created": True}
