"""AF-07 — دروازهٔ امنیت انتشار (release security gate).

Before anything leaves the factory (export ZIP or release ZIP) the files
that would ACTUALLY ship are scanned for content-level hazards:

  * embedded credentials / private key material   → critical  (blocks release)
  * explicitly dangerous runtime settings         → critical  (blocks release)
  * suspicious patterns needing human judgement   → warning   (advisory only)

Design rules (honest, no fake pass):
  * the scanned file set is exactly the set the exporter/release pipeline
    would zip — the SAME exclusion rules (exporter._allowed /
    release._release_allowed) are reused, never re-implemented;
  * findings are MASKED: a gate report never contains the secret value
    itself, only file/line/kind and a truncated preview;
  * "مشوک" (warning) is separated from "قطعی" (critical): only confirmed
    critical findings block the release;
  * a gate that cannot run (exception) means the product cannot be
    verified — the caller must fail CLOSED (refuse the release), never
    ship an unscanned product. Temporary test errors are never success.
"""

import json
import re
from pathlib import Path

import core.config as config
from core import paths

log = __import__("logging").getLogger("ai-factory.security-gate")

MAX_FILE_BYTES = 1_000_000          # per-file scan cap (binary-safe skip)
MAX_LINE_CHARS = 240

# ----------------------------------------------------------- critical ----
# یافتهٔ قطعی: کلید/راز واقعی تعبیه‌شده یا تنظیم امنیتی آشکارا خطرناک.
_CRITICAL = [
    # private key material (any flavour)
    ("secret-pem-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    # AWS access key id (documented format)
    ("secret-aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # OpenAI-style API key
    ("secret-openai-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    # Stripe live secret
    ("secret-stripe-live", re.compile(r"\bsk_live_[0-9a-zA-Z]{16,}\b")),
    # GitHub personal access token
    ("secret-github-pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b")),
    # Slack token
    ("secret-slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    # Google API key
    ("secret-google-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    # TLS verification explicitly disabled (python)
    ("danger-tls-verify-off", re.compile(r"\bverify\s*=\s*False\b"), ".py"),
    ("danger-unverified-ssl", re.compile(r"ssl\._create_unverified_context\("),
     ".py"),
]

# dangerous Android release settings (only inside AndroidManifest.xml)
_CRITICAL_ANDROID = [
    ("danger-android-debuggable",
     re.compile(r'android:debuggable\s*=\s*"true"', re.I)),
]

# ------------------------------------------------------------ warning ----
# مشوک: نیازمند قضاوت انسانی — گزارش می‌شود ولی انتشار را متوقف نمی‌کند.
_WARNING = [
    ("suspicious-secret-literal",
     # AF-46 — the optional closing quote matches JSON string keys too.
     # Without it a shipped {"password": "..."} was invisible to the gate.
     re.compile(r"\b(password|passwd|secret|api[_-]?key|apikey|access[_-]?token"
                r"|private[_-]?key|client[_-]?secret|auth[_-]?token)\b[\"']?"
                r"\s*[:=]\s*[\"']([^\"']{8,})[\"']")),
    ("suspicious-path-traversal",
     re.compile(r"[\"']\.\./")),
    ("suspicious-cors-wildcard",
     re.compile(r"Access-Control-Allow-Origin[\"']?\s*[,:\)]?\s*.{0,12}[\"']\*")),
    ("suspicious-eval", re.compile(r"\beval\s*\(")),
    ("suspicious-shell-true", re.compile(r"\bshell\s*=\s*True\b"), ".py"),
    ("suspicious-bind-all-interfaces", re.compile(r"\b0\.0\.0\.0\b")),
]

_WARNING_ANDROID = [
    ("warning-android-cleartext",
     re.compile(r'android:usesCleartextTraffic\s*=\s*"true"', re.I)),
]

# code files get the full pattern set; plain text (README …) is scanned for
# secrets only — documentation mentioning 0.0.0.0 or "../" is not a hazard.
_CODE_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".json", ".xml", ".html", ".css"}

# values that look like placeholders/documentation, not real credentials
_PLACEHOLDER_HINTS = ("changeme", "change_me", "placeholder", "example",
                      "xxxx", "todo", "dummy", "your-", "your_", "<", "${",
                      "%(", "{", "…", "…")


def _masked(match_text: str) -> str:
    """A finding preview that can never leak the matched secret."""
    text = match_text if len(match_text) <= MAX_LINE_CHARS else \
        match_text[:MAX_LINE_CHARS] + "…"
    keep = min(3, max(0, len(text) // 12))
    return text[:keep] + "…" + f"[پوشانده‌شده، {len(text)} کاراکتر]"


def _looks_placeholder(value: str) -> bool:
    low = value.lower()
    return any(h in low for h in _PLACEHOLDER_HINTS) or len(set(value)) <= 2


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _allowed_files(d: Path, mode: str) -> list[tuple[Path, str]]:
    """Exactly the files that would ship — reuse the pipeline's own rules."""
    from core import exporter
    if mode == "release":
        from core import release
        out = []
        for f in sorted(d.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(d).as_posix()
            if release._release_allowed(f, d, rel):
                out.append((f, rel))
        return out
    return [(f, f.relative_to(d).as_posix())
            for f in sorted(d.rglob("*"))
            if f.is_file() and exporter._allowed(f, d)]


def remediation(gate: dict | None) -> str:
    """AF-47 — turn a REAL blocking finding into agent guidance.

    Blocking the ZIP is not enough: the product itself must be corrected in
    the same session. This text is fed to the existing repair prompt.
    """
    g = gate or {}
    files = sorted({str(f.get("file") or "") for f in (g.get("critical") or [])
                    if f.get("file")})
    kinds = sorted({str(f.get("kind") or "") for f in (g.get("critical") or [])
                    if f.get("kind")})
    return (
        "امنیت انتشار: اسکن واقعی محصول Secret حقیقی پیدا کرد "
        "(فایلها: %s؛ نوع: %s). تحویل مسدود است تا محصول اصلاح شود. "
        "هیچ رمز، API key یا Secretای را بهصورت متن صریح در فایل، تنظیمات، "
        "دیتابیس، لاگ یا بسته خروجی نگه ندار. برای رمز اولیه (bootstrap) "
        "از متغیر محیطی، ورود امن هنگام نصب، یا رمز یکبارمصرفی استفاده کن "
        "که فقط یکبار در خروجی امن چاپ میشود و در پروژه ذخیره نمیشود. "
        "فایل حاوی رمز را حذف کن و کد مربوطه را اصلاح کن؛ سپس تستها را دوباره اجرا کن."
        % (", ".join(files[:12]) or "(نامشخص)", ", ".join(kinds[:8]) or "secret")
    )


def scan(project: str, mode: str = "export") -> dict:
    """Scan every file that would ship in this mode.

    Returns {"ok": bool, "blocked": bool, "critical": [...], "warnings": [...],
             "files_scanned": n, "skipped": [...], "type": type_id}.
    Raises nothing expected; unexpected errors propagate — the caller
    (server) must treat a broken gate as BLOCKED (fail closed).
    """
    d = paths.project_dir(project)                     # name validated here
    type_id = ""
    try:
        type_id = (json.loads((d / "meta.json").read_text(
            encoding="utf-8")) or {}).get("type", "")
    except (OSError, ValueError):
        pass

    critical: list[dict] = []
    warnings: list[dict] = []
    skipped: list[str] = []
    scanned = 0
    for f, rel in _allowed_files(d, mode):
        try:
            size = f.stat().st_size
        except OSError as exc:
            skipped.append(f"{rel} (stat: {exc})")
            continue
        if size > MAX_FILE_BYTES or f.suffix.lower() in (
                ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff",
                ".woff2", ".ttf", ".otf", ".mp3", ".mp4", ".zip", ".apk",
                ".aab", ".jar", ".dex", ".db", ".so", ".flat", ".arsc"):
            skipped.append(rel)
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            # a file that cannot be read cannot be verified — refuse, loudly
            critical.append({"file": rel, "kind": "gate-unreadable",
                             "message": f"فایل قابل خواندن نیست: {exc}"})
            continue
        scanned += 1
        is_code = f.suffix.lower() in _CODE_SUFFIXES
        is_manifest = f.name == "AndroidManifest.xml"

        def _run(patterns, bucket):
            for spec in patterns:
                kind, rx = spec[0], spec[1]
                only = spec[2] if len(spec) > 2 else None
                if only and f.suffix.lower() != only:
                    continue
                if not is_code and not is_manifest and kind.startswith(
                        ("suspicious-", "danger-")):
                    continue                      # docs: secrets only
                if is_manifest and not kind.endswith(("-debuggable",
                                                      "-cleartext")):
                    continue
                for m in rx.finditer(text):
                    value = m.group(0)
                    finding = {"file": rel, "line": _line_of(text, m.start()),
                               "kind": kind, "preview": _masked(value)}
                    if kind == "suspicious-secret-literal":
                        val = m.group(2)
                        finding["preview"] = _masked(value)
                        if len(val) >= 12 and not _looks_placeholder(val):
                            finding["kind"] = "secret-embedded-literal"
                            critical.append(finding)
                        else:
                            warnings.append(finding)
                    else:
                        bucket.append(finding)

        _run(_CRITICAL, critical)
        _run(_WARNING, warnings)
        if is_manifest:
            _run(_CRITICAL_ANDROID, critical)
            _run(_WARNING_ANDROID, warnings)

    blocked = bool(critical)
    res = {"ok": not blocked, "blocked": blocked, "critical": critical,
           "warnings": warnings, "files_scanned": scanned,
           "skipped": skipped[:20], "type": type_id, "mode": mode}
    if blocked:
        log.warning("security gate BLOCKED %s (%s): %d critical finding(s)",
                    project, mode, len(critical))
    else:
        log.info("security gate passed %s (%s): %d files, %d warning(s)",
                 project, mode, scanned, len(warnings))
    return res


def summary(res: dict) -> str:
    """Human-readable Persian summary — masked, never prints secrets."""
    if not res.get("blocked"):
        w = len(res.get("warnings") or [])
        return (f"دروازهٔ امنیت: تایید ({res.get('files_scanned', 0)} فایل، "
                f"{w} مشوک)")
    kinds = sorted({f.get("kind", "?") for f in res.get("critical") or []})
    return ("انتشار متوقف شد — یافتهٔ بحرانی: " + ", ".join(kinds))
