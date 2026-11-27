"""Smart Release — Git/GitHub synchronisation (AF-24).

Built ON TOP of the existing module set (never re-implementing it):

  * file exclusions / secret names come from ``core.config`` and the
    content scanner from ``core.security_gate`` (the same gate the release
    pipeline uses) — a file that may not ship may not be committed either;
  * only changed files are inspected, via real ``git status --porcelain``;
  * no force push, no history rewrite, no reset, no deletion — ever;
  * a public repository with private/copyrighted candidates is a HARD STOP;
  * an unreachable GitHub is reported honestly (the local artifact is still
    delivered by the caller) instead of being faked;
  * all command output is sanitised — tokens/passwords are never returned.

The helpers accept an optional ``root`` (a real git work tree) so the same
code path can be tested against a local bare repository; production callers
always pass a project name and get its verified directory from ``core.paths``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import core.config as config
from core import paths

log = __import__("logging").getLogger("ai-factory.git-sync")

# Machine statuses (the report layer maps them to Persian labels).
OK = "ok"
FAIL = "failed"
SKIP = "not_done"
UNVERIFIED = "unverified"

# Never printed, logged or committed.
_SENSITIVE_NAMES = {
    ".env", ".env.local", "panel_admin.json", "ai_admin.json", "ai_key.txt",
    "keystore.properties", "local.properties", "credentials.json",
    "service-account.json", "id_rsa", "known_hosts",
}
_SENSITIVE_SUFFIXES = {
    ".env", ".token", ".pem", ".key", ".p12", ".jks", ".keystore",
    ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm",
    ".pid", ".out", ".log",
}
# Names that signal restricted / copyrighted course-book content.
_RIGHTS_NAMES = {
    "books.json", "books-public.json", "knowledge-bank.json",
    "knowledge-map.json", "lessons.json", "lessons-extra.json",
    "rights-registry.json",
}
_RIGHTS_RE = re.compile(
    r"(?i)(books?|کتاب|کتابها|نسخه\s*محدود|rights?|حق\s*نشر|قرارداد)",
)
_BUILD_DIRS = {"build", "dist", "node_modules", "__pycache__", ".gradle", "out"}
_BUILD_SUFFIXES = {".apk", ".aab", ".apks", ".idsig", ".zip", ".dex",
                   ".class", ".jar", ".pyc", ".flat", ".arsc", ".so"}
_LOCAL_DIRS = {".factory", ".backups", ".git", "data"}
_LARGE_BYTES = 5 * 1024 * 1024           # 5 MB — needs a real reason to publish

# Output sanitising: never leak a credential even if a remote echoes one.
_SECRET_RE = re.compile(
    r"(?i)(gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{16,}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}|https?://[^/@\s]+:[^/@\s]+@|[A-Za-z0-9+/]{40,}={0,2})"
)
_NAME_RE = re.compile(config.PROJECT_NAME_RE)
_REMOTE_SAFE_RE = re.compile(r"^(?:https?://|git@)([^\s/]+)[/:](.+?)(?:\.git)?$")


class GitSyncError(Exception):
    def __init__(self, message: str, status: int = 400, result: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.result = result or {}


def _san(text: str, limit: int = 4000) -> str:
    """Strip secrets and bound the size of any tool output we return."""
    text = _SECRET_RE.sub("[REDACTED]", str(text or ""))
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return text[:limit]


def _run(argv: list[str], cwd: Path, timeout: float = 30.0,
         inp: bytes | None = None, env: dict | None = None) -> tuple[int, str, str]:
    """Fixed-argv subprocess (shell=False, no prompt, credential-safe env)."""
    # AF-29 — one boundary policy for every fixed-argv subprocess.
    from core import boundary
    e = boundary.clean_env(env)
    e["GIT_TERMINAL_PROMPT"] = "0"
    e["LC_ALL"] = "C"
    try:
        p = subprocess.run(argv, cwd=str(cwd), capture_output=True, input=inp,
                           timeout=timeout, env=e, shell=False)
    except FileNotFoundError:
        return 127, "", "git موجود نیست"
    except subprocess.TimeoutExpired:
        return 124, "", "زمان فرمان git به سر رسید"
    return p.returncode, p.stdout.decode("utf-8", "replace"), \
        p.stderr.decode("utf-8", "replace")


def _git(root: Path, *args: str, timeout: float = 30.0,
         inp: bytes | None = None) -> tuple[int, str, str]:
    return _run(["git", *args], root, timeout=timeout, inp=inp)


def _safe_remote(url: str) -> dict:
    """A remote description safe to show: host + owner/repo, never a token."""
    url = (url or "").strip()
    out = {"raw_present": bool(url), "host": "", "repo": "", "url": ""}
    if not url:
        return out
    m = _REMOTE_SAFE_RE.match(url)
    if m:
        out["host"] = m.group(1)
        out["repo"] = m.group(2)
        out["url"] = ("https://" + m.group(1) + "/" + m.group(2)) \
            if url.startswith("http") else (m.group(1) + ":" + m.group(2))
    else:
        out["url"] = "[REDACTED-remote]" if ("@" in url or "://" in url) else _san(url, 120)
    return out


def locate_repo(project: str) -> Path | None:
    """Real repository root for a project.

    A nested ``projects/<name>/.git`` is preferred. If the project lives
    inside a larger repository, that repository is returned but flagged by
    ``inspect`` — committing someone else's root by mistake is forbidden.
    """
    d = paths.project_dir(project)
    cur = d.resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".git").exists():
            return candidate
        if candidate == config.PROJECT_ROOT.resolve():
            break
    return None


def _changed_files(root: Path) -> list[dict]:
    code, out, err = _git(root, "-c", "core.quotepath=false",
                          "status", "--porcelain=v1", "-z")
    if code != 0:
        raise GitSyncError("git status ناموفق: " + _san(err, 200), 500)
    out = out.rstrip("\x00")
    if not out:
        return []
    parts = out.split("\x00")
    files: list[dict] = []
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        xy, rel = entry[:2], entry[3:]
        if xy.startswith("R") or xy.startswith("C"):
            i += 1  # skip the "from" path of a rename/copy record
        files.append({"status": xy.strip() or "?", "path": rel})
    return files


def _classify(rel: str, root: Path, is_public: bool) -> dict:
    """Decide whether ONE changed path may be published."""
    p = Path(rel)
    name = p.name
    parts = set(p.parts)
    low = rel.lower()
    reasons: list[str] = []

    if name in config.EXPORT_SECRET_NAMES or name in _SENSITIVE_NAMES \
       or p.suffix.lower() in config.EXPORT_SECRET_SUFFIXES \
       or p.suffix.lower() in _SENSITIVE_SUFFIXES:
        reasons.append("فایل محرمانه/رمز (هرگز ارسال نمیشود)")
    if parts & _LOCAL_DIRS or parts & _BUILD_DIRS:
        reasons.append("فایل محلی/ساخت (پیشفرض منتشر نمیشود)")
    if p.suffix.lower() in _BUILD_SUFFIXES:
        reasons.append("خروجی باینری/ساخت")
    if name in _RIGHTS_NAMES or _RIGHTS_RE.search(name):
        reasons.append("محتوای کتاب/حق نشر — انتشار عمومی ممنوع")
    if is_public and (name in _RIGHTS_NAMES or _RIGHTS_RE.search(rel)):
        reasons.append("مخزن عمومی + محتوای دارای حق نشر")

    fp = root / rel
    try:
        if fp.is_file() and fp.stat().st_size > _LARGE_BYTES:
            reasons.append("فایل حجیم (>5MB)")
    except OSError:
        pass
    return {"path": rel, "allowed": not reasons, "reasons": reasons}


def _github_visibility(root: Path, remote_repo: str) -> dict:
    """Read-only GitHub visibility probe via `gh` (never prints a token)."""
    if not remote_repo:
        return {"available": False, "public": None,
                "note": "مخزن GitHub شناسایی نشد"}
    if not shutil.which("gh"):
        return {"available": False, "public": None,
                "note": "gh نصب نیست — وضعیت مخزن تأییدنشده"}
    code, out, err = _run(["gh", "repo", "view", remote_repo,
                           "--json", "visibility,nameWithOwner"],
                          root, timeout=20)
    if code != 0:
        return {"available": False, "public": None,
                "note": "gh نتوانست مخزن را ببیند (دسترسی/شبکه): " + _san(err, 160)}
    try:
        import json
        info = json.loads(out)
        vis = str(info.get("visibility") or "").upper()
        return {"available": True, "public": vis == "PUBLIC",
                "visibility": vis,
                "nameWithOwner": str(info.get("nameWithOwner") or remote_repo)}
    except (ValueError, TypeError):
        return {"available": False, "public": None, "note": "پاسخ gh نامعتبر"}


def inspect(project: str, root: Path | None = None) -> dict:
    """Read-only picture of a repository. Never mutates anything."""
    root = Path(root) if root else locate_repo(project)
    if root is None:
        return {"repo": False, "status": SKIP,
                "note": "پروژه داخل مخزن Git نیست — همگامسازی انجامنشده",
                "project_root": str(paths.project_dir(project))}
    root = root.resolve()
    if not (root / ".git").exists():
        return {"repo": False, "status": SKIP, "note": "پوشه .git یافت نشد"}

    code, branch, _ = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch.strip() if code == 0 else ""
    code, head, _ = _git(root, "rev-parse", "HEAD")
    head = head.strip() if code == 0 else ""
    _, remote_name, _ = _git(root, "config", "--get", "branch.%s.remote" % branch)
    remote_name = remote_name.strip() if branch else ""
    _, remote_url, _ = _git(root, "remote", "get-url", remote_name) \
        if remote_name else (0, "", "")
    # Fall back to "origin" for repos with no upstream configured.
    if not remote_url:
        _, remote_url, _ = _git(root, "remote", "get-url", "origin")
        if remote_url.strip():
            remote_name = "origin"
    remote_url = remote_url.strip()
    remote = _safe_remote(remote_url)
    gh = _github_visibility(root, remote["repo"])

    ahead = behind = 0
    if remote_name and branch:
        code, counts, _ = _git(root, "rev-list", "--left-right", "--count",
                               "HEAD...@{upstream}", timeout=20)
        if code == 0:
            try:
                ahead, behind = (int(x) for x in counts.split()[:2])
            except (ValueError, IndexError):
                pass
    changed = _changed_files(root)
    try:
        project_root = paths.project_dir(project).resolve()
    except (FileNotFoundError, PermissionError):
        project_root = None      # explicit-root (test) callers have no project dir
    inside_ai_factory = (root == config.PROJECT_ROOT.resolve())
    return {
        "repo": True, "status": OK, "root": str(root), "branch": branch,
        "head": head, "remote": remote_name, "remote_info": remote,
        "github": gh, "public": gh.get("public"),
        "ahead": ahead, "behind": behind,
        "upstream": bool(remote_name), "changed": changed,
        "changed_count": len(changed),
        "project_is_repo_root": bool(project_root and root == project_root),
        "inside_ai_factory_root": inside_ai_factory,
        "note": ("مخزن ریشهٔ AI Factory است — ارسال از این سطح ممنوع است"
                 if inside_ai_factory else ""),
    }


def preflight(project: str, root: Path | None = None,
              allow_build: bool = False) -> dict:
    """Decide what may be committed; no writes, no network mutation."""
    info = inspect(project, root)
    if not info["repo"]:
        return dict(info, blocked=True, allowed=[], blocked_files=[],
                    reasons=[info.get("note", "مخزن یافت نشد")])
    reasons: list[str] = []
    if info["inside_ai_factory_root"]:
        reasons.append("مخزن، ریشهٔ AI Factory است — از ارسال کلیهٔ پروژهها جلوگیری شد")
    if info["behind"] and info["ahead"]:
        reasons.append("تاریخچهٔ محلی و آنلاین divergent است — حذف/بازنویسی ممنوع")
    elif info["behind"]:
        reasons.append("مخزن محلی عقبتر از آنلاین است — پیش از ارسال باید بهروز شود")
    if "conflict" in info or info.get("ahead") and info.get("behind"):
        reasons.append("تعارض تغییرات — خودسرانه هیچکدام حذف نمیشود")

    public = info.get("public") is True
    allowed, blocked_files = [], []
    for ch in info["changed"]:
        c = _classify(ch["path"], Path(info["root"]), public)
        if c["allowed"]:
            allowed.append(ch["path"])
        else:
            blocked_files.append(c)
    if public:
        # Public repo + *any* private/copyright candidate is a hard stop even
        # when those files would otherwise be filtered out: the user decides.
        for b in blocked_files:
            if any(w in " ".join(b["reasons"]) for w in ("کتاب", "حق نشر", "محرمانه", "رمز")):
                reasons.append("مخزن عمومی و فایل حساس «%s» وجود دارد — انتشار متوقف شد"
                               % b["path"])
                break

    # Content-level scan reuses the SAME gate as the release pipeline.
    gate = None
    try:
        from core import security_gate
        gate = security_gate.scan(project, mode="export")
        if gate.get("blocked"):
            kinds = sorted({f.get("kind") for f in gate.get("critical", [])})
            reasons.append("دروازهٔ امنیت محتوا critical یافت: " + ", ".join(kinds))
    except Exception as exc:  # noqa: BLE001 — fail closed, never unverified push
        reasons.append("دروازهٔ امنیت اجرا نشد: %s" % _san(str(exc), 120))

    if allow_build:
        for b in list(blocked_files):
            if all(w in " ".join(b["reasons"]) for w in ("ساخت", "خروجی")):
                blocked_files.remove(b)
                allowed.append(b["path"])

    blocked = bool(reasons)
    return dict(info, blocked=blocked, allowed=allowed,
                blocked_files=blocked_files, reasons=reasons,
                public=public, security_scan={
                    "scanned": (gate or {}).get("files_scanned", 0),
                    "critical": len((gate or {}).get("critical", [])),
                    "warnings": len((gate or {}).get("warnings", [])),
                })


def commit_and_push(project: str, root: Path | None = None,
                    message: str = "Smart Release", allow_build: bool = False,
                    push: bool = True) -> dict:
    """Commit ONLY allowed files and push WITHOUT force. Honest about each step."""
    pre = preflight(project, root, allow_build=allow_build)
    steps: list[dict] = []
    if not pre["repo"]:
        return {"status": SKIP, "steps": steps, "preflight": pre,
                "note": pre.get("note", "مخزن یافت نشد")}
    if pre["blocked"]:
        steps.append({"step": "preflight", "ok": False,
                      "detail": "؛ ".join(pre["reasons"][:4])})
        return {"status": FAIL, "steps": steps, "preflight": pre,
                "note": "انتشار ناامن متوقف شد", "blocked_files": pre["blocked_files"]}
    steps.append({"step": "preflight", "ok": True,
                  "detail": "تغییرات مجاز: %d، مسدود: %d" %
                            (len(pre["allowed"]), len(pre.get("blocked_files", [])))})

    root = Path(pre["root"])
    committed = False
    before = pre["head"]
    if pre["allowed"]:
        # add in small batches: no -A, no pathspec magic, always `--`.
        code, _, err = _git(root, "add", "--", *pre["allowed"], timeout=60)
        if code != 0:
            steps.append({"step": "add", "ok": False, "detail": _san(err, 240)})
            return {"status": FAIL, "steps": steps, "preflight": pre,
                    "note": "افزودن فایلها به Git ناموفق شد"}
        _, staged, _ = _git(root, "diff", "--cached", "--name-only")
        if staged.strip():
            msg = ("%s: %s (%s)" % (message, project,
                                    time_stamp()))[:200]
            inp = None
            code, _, err = _git(root, "commit", "-m", msg, timeout=120)
            if code != 0:
                steps.append({"step": "commit", "ok": False, "detail": _san(err, 240)})
                return {"status": FAIL, "steps": steps, "preflight": pre,
                        "note": "ثبت تغییرات ناموفق شد"}
            committed = True
            steps.append({"step": "commit", "ok": True,
                          "detail": "%d فایل ثبت شد" % len(staged.splitlines())})
        else:
            steps.append({"step": "commit", "ok": True,
                          "detail": "تغییری برای ثبت نبود (عملیات تکراری نیست)"})
    else:
        steps.append({"step": "commit", "ok": True,
                      "detail": "هیچ تغییر مجاز دیگری وجود ندارد"})

    code, after, err = _git(root, "rev-parse", "HEAD")
    after = after.strip() if code == 0 else ""
    result = {"status": OK, "steps": steps, "preflight": pre,
              "committed": committed, "commit_count": 1 if committed else 0,
              "head_before": before, "head_local": after,
              "files_committed": len(pre["allowed"]) if committed else 0,
              "remote_head": None, "pushed": False, "online_match": None}
    if not push:
        result["note"] = "ارسال به GitHub طبق درخواست انجام نشد"
        return result

    remote, branch = pre["remote"], pre["branch"]
    if not remote or not branch:
        result["status"] = SKIP
        result["note"] = "Remote/شاخهٔ ردیابی وجود ندارد — ارسال انجامنشده"
        steps.append({"step": "push", "ok": False, "detail": result["note"]})
        return result
    if not pre.get("upstream"):
        result["status"] = SKIP
        result["note"] = "شاخه دارای upstream نیست — ارسال بدون تعیین مقصد مجاز نیست"
        steps.append({"step": "push", "ok": False, "detail": result["note"]})
        return result

    # Never force. Refresh refs first and refuse a divergent/behind branch.
    _, _, ferr = _git(root, "fetch", remote, branch, timeout=45)
    code, counts, _ = _git(root, "rev-list", "--left-right", "--count",
                           "HEAD...@{upstream}", timeout=20)
    if code == 0:
        try:
            ahead, behind = (int(x) for x in counts.split()[:2])
        except (ValueError, IndexError):
            ahead, behind = 0, 0
        if behind:
            result["status"] = FAIL
            result["note"] = ("مخزن آنلاین %d کدمت جلوتر است — ارسال متوقف شد "
                              "(بدون pull/force/rebase خودسرانه)" % behind)
            steps.append({"step": "push", "ok": False, "detail": result["note"]})
            return result
    if ferr and "REDACTED" not in ferr and "error" in ferr.lower():
        result["status"] = FAIL
        result["note"] = "بهروزرسانی مراجع از GitHub ناموفق: " + _san(ferr, 180)
        steps.append({"step": "fetch", "ok": False, "detail": result["note"]})
        return result

    code, _, err = _git(root, "push", remote, branch, timeout=90)
    if code != 0:
        result["status"] = FAIL
        result["note"] = "ارسال به GitHub ناموفق: " + _san(err, 220)
        steps.append({"step": "push", "ok": False, "detail": result["note"]})
        return result
    steps.append({"step": "push", "ok": True, "detail": "git push %s %s" % (remote, branch)})

    # Prove the online head equals the local head.
    code, ls, err = _git(root, "ls-remote", remote, "refs/heads/" + branch, timeout=30)
    remote_head = ls.split()[0] if code == 0 and ls.strip() else ""
    match = bool(remote_head) and remote_head == after
    result.update({"remote_head": remote_head, "pushed": True,
                   "online_match": match})
    steps.append({"step": "verify-remote", "ok": match,
                  "detail": ("شناسهٔ محلی و آنلاین یکسان است" if match
                             else "عدم تطابق شناسهٔ محلی و آنلاین — تأییدنشده")})
    if not match:
        result["status"] = UNVERIFIED
        result["note"] = "ارسال شد ولی تطابق شناسهٔ آنلاین تأیید نشد"
    return result


def time_stamp() -> str:
    import time
    return time.strftime("%Y%m%d-%H%M%S")
