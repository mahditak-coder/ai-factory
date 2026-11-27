"""Factory engine — project lifecycle, planning and real generation.

Everything here is backed by the real OpenCode bridge: planning and code
generation are actual model sessions opened in the target project directory.
Filesystem access stays strictly inside projects/<name> (core.paths).

Project identity/meta lives in projects/<name>/meta.json.
Append-only change history lives in projects/<name>/.factory/history.json
(the .factory dir is excluded from exports and never shown as source files).
"""

import hashlib
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import core.config as config
from core import action_gate, agent_events, opstatus, paths, project_snapshot
from core.model_gateway import OcError, gateway


# AF NEXT P1 — module-level seams delegate to the ONE gateway contract.
# Existing tests and callers keep their stable patch points; Core still has a
# single OpenCode-facing abstraction.
def sessions(*a, **kw): return gateway().sessions(*a, **kw)
def state(*a, **kw): return gateway().state(*a, **kw)
def create_session(*a, **kw): return gateway().create_session(*a, **kw)
def history(*a, **kw): return gateway().history(*a, **kw)
def prompt(*a, **kw): return gateway().prompt(*a, **kw)
def interrupt(*a, **kw): return gateway().interrupt(*a, **kw)
def headless_permissions(*a, **kw): return gateway().headless_permissions(*a, **kw)
def record_tool_events(*a, **kw): return gateway().record_tool_events(*a, **kw)

# AF-15 — cap for the file-scope list inside a fix instruction
MAX_FIX_FILES = 10

PROJECTS = config.PROJECTS_DIR

log_name = "ai-factory.factory"
log = __import__("logging").getLogger(log_name)


def perception_block(project: str, session: str | None = None,
                     task_id: str | None = None, task_text: str = "",
                     plan: dict | None = None,
                     focus: str | None = None) -> str:
    """AF-29/31 — real project state + observed tool outcomes + relevant memory.

    Both sources are bounded and honest: unavailable data is reported as such,
    and an absent tool result is never presented as success.  New perception
    modules are best-effort; their failure never blocks a model turn.
    """
    parts = []
    try:
        snap = project_snapshot.build(project)
        parts.append(project_snapshot.to_prompt(snap, focus=focus))
    except Exception:  # noqa: BLE001 — perception is an enhancement, never fatal
        log.warning("project snapshot unavailable for %s", project, exc_info=True)
    # AF NEXT P2 — the model sees ONLY tools allowed for this task's scope.
    try:
        from core import tool_registry
        scopes = {"project:read", "tests:run", "security:read"}
        if focus in ("execute", "verify") or any(
                w in str(task_text or "").lower()
                for w in ("اصلاح", "ویرایش", "fix", "implement")):
            scopes |= {"project:write", "shell:execute"}
        parts.append(tool_registry.registry().catalog_prompt(scopes))
    except Exception:  # noqa: BLE001 — catalog is optional, never blocks a turn
        log.warning("tool catalog unavailable for %s", project, exc_info=True)
    parts.append(agent_events.decision_prompt(project, session, task_id))
    try:
        from core import agent_memory
        mem = agent_memory.recall(project, task_text,
                                  task_id=task_id or "", session_id=session or "")
        if mem:
            parts.append(mem)
    except Exception:  # noqa: BLE001 — memory is optional, never fatal
        log.warning("agent memory unavailable for %s", project, exc_info=True)
    # AF-40 — evidence-tagged code snippets relevant to THIS task, with real
    # line numbers; budget adapts to project size and is configurable.
    if task_text:
        try:
            code = project_snapshot.select_context(project, task_text)
            if code:
                parts.append(code)
        except Exception:  # noqa: BLE001 — optional
            log.warning("code context unavailable for %s", project, exc_info=True)
    if plan:
        try:
            from core import agent_planner
            parts.append(agent_planner.to_prompt(plan))
        except Exception:  # noqa: BLE001
            pass
    return "\n\n".join(p for p in parts if p)

_meta_lock = threading.Lock()

# R4-a: derived from the single source of truth
# (core.config.PROJECT_TYPE_SPECS). Names and access shape are preserved
# for every existing consumer — edit core/config.py, never this block.
TYPES = dict((tid, spec["engine_label"])
             for tid, spec in config.PROJECT_TYPE_SPECS.items())

FULLSTACK_TYPES = config.FULLSTACK_TYPES

FS_REQUIREMENT = config.FS_REQUIREMENT

# P6-1: per-type minimum functional contract — appended to every build
# instruction so generated products are testable by core.typetests.
# Each entry is REAL machine-verifiable behaviour, not file existence.
TYPE_REQUIREMENTS = dict((tid, spec["contract"])
                         for tid, spec in config.PROJECT_TYPE_SPECS.items()
                         if spec.get("contract"))

# Session title prefixes used by the factory (kept on each project)
PLAN_TITLE_PREFIX = "🏭 برنامه"
GEN_TITLE_PREFIX = "🏭 تولید"

RTL_INSTRUCTIONS = """من AI Factory هستم؛ یک کارخانه تولید نرم‌افزار. از این پس در دایرکتوری پروژه «{project}» (نوع: {type}) کار می‌کنی.

قوانین سفت و سخت:
۱. فایل‌ها را فقط با ابزارهای خودت داخل همین دایرکتوری بساز/ویرایش کن؛ هرگز خارج از آن فایل نساز.
۲. فایل‌های موجودِ قبلی را بدون دلیل حذف نکن؛ ویرایش‌ها باید افزودنی باشند نه تخریبی.
۳. کد باید کامل و اجراپذیر باشد: برای پروژه وب استاتیک حتماً index.html؛ برای بک‌اند فایل server.py یا server.js که پورت را از env PORT می‌خواند.
۴. در پایان پاسخ فارسیِ کوتاهی بده: چه فایل‌هایی ساخته/اصلاح شدند و دستور اجرای پروژه.
۵. هیچ placeholder یا «...» باقی نگذار؛ همه کد کامل باشد.
۶. همه فایل‌ها با encoding UTF-8 و HTML با <html> و </html> نوشته شوند.
۷. پیش از پایان پاسخ، با ابزار glob فایل‌های پروژه را چک کن؛ اگر فایلِ الزامیِ نوع پروژه (وب: index.html) هنوز وجود ندارد، آن را بساز.
 ۸. مسیرها را فقط از بلوک «ریشهٔ تأییدشده/فایلهای موجود» بگیر. هرگز در مسیرهای خارج از ریشهٔ پروژه (مثل core/ یا scripts/ کارخانه) جستجو نکن و مسیر ناموجود را معتبر فرض نکن."""


# ----------------------------------------------------------------- meta ------

def _meta_path(project_dir: Path) -> Path:
    return project_dir / "meta.json"


def read_meta(project_dir: Path) -> dict:
    try:
        return json.loads(_meta_path(project_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_meta(project_dir: Path, meta: dict) -> None:
    _meta_path(project_dir).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


# P7-0 — explicit generation-session registry. Every generate() call owns a
# NEW OpenCode session; the latest one per project is recorded here so the
# panel view and fix_request always target the right build even when two
# generations run in parallel (multi-agent P7).
_gen_reg: dict[str, str] = {}
_gen_reg_lock = threading.Lock()
_history_lock = threading.Lock()   # append_history is read-modify-write


def _record_gen(project: str, sid: str) -> None:
    with _gen_reg_lock:
        _gen_reg[project] = sid


def _recorded_gen(project: str) -> str | None:
    with _gen_reg_lock:
        return _gen_reg.get(project)


def _latest_session(project: str, prefix: str) -> str | None:
    """Deterministic pick: the most recently updated session with prefix."""
    cands = [s for s in sessions(project)
             if (s.get("title") or "").startswith(prefix)]
    if not cands:
        return None
    cands.sort(key=lambda s: s.get("updated") or 0, reverse=True)
    return cands[0]["id"]


def _session_in_project(project: str, sid: str) -> bool:
    """True when a recorded session still exists and belongs to the project."""
    try:
        st = state(sid)
    except OcError:
        return False
    try:
        return st.get("directory") == str(paths.project_dir(project).resolve())
    except (OSError, PermissionError, FileNotFoundError):
        return False


def append_history(project: str, action: str, detail: str) -> None:
    """Append-only change log inside the project's .factory (P7-0: locked)."""
    try:
        d = paths.project_dir(project) / ".factory"
        d.mkdir(exist_ok=True)
        hp = d / "history.json"
        with _history_lock:
            try:
                entries = json.loads(hp.read_text(encoding="utf-8"))
                if not isinstance(entries, list):
                    entries = []
            except (OSError, ValueError):
                entries = []
            entries.append({"ts": datetime.now().isoformat(timespec="seconds"),
                            "action": action, "detail": detail[:2000]})
            hp.write_text(json.dumps(entries, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    except (OSError, PermissionError) as exc:
        log.error("history append failed for %s: %s", project, exc)


def history_of(project: str) -> list[dict]:
    d = paths.project_dir(project) / ".factory"
    try:
        return json.loads((d / "history.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


# ------------------------------------------------------ project lifecycle ----

def list_projects() -> list[dict]:
    out = []
    for p in sorted(PROJECTS.iterdir()):
        if not (p.is_dir() and not p.name.startswith(".")):
            continue
        meta = read_meta(p)
        n_files = sum(1 for f in p.rglob("*") if f.is_file()
                      and ".factory" not in f.parts)
        stat = p.stat()
        out.append({
            "name": p.name,
            "type": meta.get("type") or "نامشخص",
            "type_label": TYPES.get(meta.get("type", ""), "نامشخص") if meta.get("type") else "نامشخص",
            "description": meta.get("description", ""),
            "created": meta.get("created") or datetime.fromtimestamp(
                stat.st_ctime).isoformat(timespec="seconds"),
            "modified": meta.get("modified") or datetime.fromtimestamp(
                stat.st_mtime).isoformat(timespec="seconds"),
            "files": n_files,
        })
    return out


def create_project(name: str, ptype: str, description: str) -> dict:
    if ptype not in TYPES:
        raise ValueError("نوع پروژه نامعتبر است: " + str(ptype))
    d = PROJECTS / name  # validated by caller
    d.mkdir()
    meta = {
        "name": name, "type": ptype, "description": description,
        "created": datetime.now().isoformat(timespec="seconds"),
        "modified": datetime.now().isoformat(timespec="seconds"),
    }
    write_meta(d, meta)
    (d / "README.md").write_text(
        f"# {name}\n\nنوع: {TYPES[ptype]}\n\n{description or 'پروژه AI Factory'}\n",
        encoding="utf-8")
    scaffolded = []
    if ptype == "fullstack-python":
        from core import scaffold as _scaffold
        res = _scaffold.scaffold(name, app_title=name)
        scaffolded = res["files"]
        with open(d / "README.md", "a", encoding="utf-8") as f:
            f.write("\n## اجرای قالب Full-Stack\n\n"
                    "```bash\npython server.py        # یا از زبانه «اجرا»ِ کارخانه\n"
                    "python tests/test_api.py   # ۱۸ سناریوی خودآزمون واقعی\n```\n")
    elif ptype == "ai-tool":
        # OPTIONAL reusable AI capability — only AI-capable projects get the
        # standalone connector (stdlib-only, OpenCode-free). Nothing is
        # imposed on other project types.
        from core import scaffold as _scaffold
        res = _scaffold.scaffold_ai_module(name)
        scaffolded = res["files"]
        with open(d / "README.md", "a", encoding="utf-8") as f:
            f.write("\n## ماژول هوش مصنوعی مستقل\n\n"
                    "```python\nfrom app.aiclient import send, status\n```\n"
                    "تنظیمات: `ai.example.json` → config.json؛ کلید فقط با "
                    "`AI_API_KEY` (env) یا `AI_API_KEY_FILE`. بدون OpenCode.\n"
                    "خودآزمون ماژول: `python3 tests/test_aiclient.py`\n\n"
                    "### صفحه تنظیمات AI (P3)\n\n"
                    "کلید/مدل/سرویس را از آدرس `/ai.html` بدون ویرایش فایل "
                    "تنظیم کنید. فعال‌سازی (۳ خط داخل server.py):\n\n"
                    "```python\nfrom app import ai_admin\n"
                    "if self.path.startswith(\"/api/ai/\")"
                    " or self.path in ai_admin.UI_FILES:\n"
                    "    return ai_admin.handle(self)\n```\n")
    elif ptype == "pwa":
        # P8-0: standard installable-PWA base (manifest + service worker +
        # real icons + shell). The model then customizes APP CONTENT on top
        # of this guaranteed-valid base — never the PWA plumbing.
        from core import scaffold as _scaffold
        res = _scaffold.scaffold_pwa(name, app_title=name)
        scaffolded = res["files"]
        with open(d / "README.md", "a", encoding="utf-8") as f:
            f.write("\n## قالب PWA (قابل نصب)\n\n"
                    "پایهٔ استاندارد: `manifest.json` + `sw.js` + `icons/` "
                    "(192 و 512 پیکسل). نصب روی اندروید: کروم → منو → "
                    "«افزودن به صفحهٔ اصلی». حالت آفلاین با Service Worker "
                    "پوشش داده می‌شود.\n"
                    "قانون: manifest.json، sw.js و icons را حذف/خراب نکنید؛ "
                    "محتوای اپ را در index.html/style.css/app.js کامل کنید.\n")
    elif ptype == "node-server":
        # AF-13: real runnable Node.js service — stdlib-only template
        from core import scaffold as _scaffold
        res = _scaffold.scaffold_node(name, app_title=name)
        scaffolded = res["files"]
        with open(d / "README.md", "a", encoding="utf-8") as f:
            f.write("\n## اجرای قالب Node.js\n\n"
                    "```bash\nnode server.js        # یا از زبانهٔ «اجرا»ی کارخانه (PORT env)\n"
                    "node --test            # ۶ سناریوی خودآزمون واقعی HTTP\n```\n"
                    "بدون هیچ وابستگی خارجی — فقط کتابخانهٔ استاندارد Node.\n")
    append_history(name, "create", f"type={ptype} desc={description[:200]}"
                   + (f" scaffold={len(scaffolded)}files" if scaffolded else ""))
    if scaffolded and ptype == "fullstack-python":
        # (P8-0 micro-fix: this hint was previously appended for EVERY
        # scaffolded type — and duplicated for fullstack-python itself.)
        same = (path := (d / "README.md"))
        # append template usage hints to the factory README
        with open(same, "a", encoding="utf-8") as f:
            f.write("\n## اجرای قالب Full-Stack\n\n"
                    "```bash\npython server.py        # یا از زبانه «اجرا»ِ کارخانه\n"
                    "python tests/test_api.py   # ۱۸ سناریوی خودآزمون واقعی\n```\n")
    log.info("factory project created: %s (%s)", name, ptype)
    return {"name": name, "type": ptype, "type_label": TYPES[ptype], **{
        "created": meta["created"], "modified": meta["modified"]}}


def rename_project(old: str, new: str) -> dict:
    old_dir = paths.project_dir(old)
    if not paths.project_name_valid(new):
        raise ValueError("نام جدید نامعتبر است")
    new_dir = paths.resolve_safe(PROJECTS, new)
    if new_dir.exists():
        raise FileExistsError("پروژه‌ای با این نام از قبل وجود دارد")
    old_dir.rename(new_dir)
    meta = read_meta(new_dir)
    meta["name"] = new
    meta["modified"] = datetime.now().isoformat(timespec="seconds")
    write_meta(new_dir, meta)
    append_history(new, "rename", f"from {old}")
    log.info("factory project renamed %s -> %s", old, new)
    return {"old": old, "new": new}


def delete_project(name: str, confirm: bool) -> None:
    if confirm is not True:
        raise PermissionError("حذف پروژه نیازمند تأیید صریح کاربر است (confirm=true)")
    d = paths.project_dir(name)
    import shutil
    shutil.rmtree(d)
    log.info("factory project deleted: %s", name)


def touch(project: str, detail: str = "") -> None:
    d = paths.project_dir(project)
    meta = read_meta(d)
    meta["modified"] = datetime.now().isoformat(timespec="seconds")
    write_meta(d, meta)


# -------------------------------------------------- planning & generation ----

def _ensure_session(project: str, prefix: str, title: str) -> tuple[str, dict]:
    """Return (session_id, info) of an existing factory session or a new one."""
    for s in sessions(project):
        t = s.get("title") or ""
        if t.startswith(prefix):
            return s["id"], s
    created = create_session(project, title)
    return created["id"], created


def _last_assistant_text(session_id: str) -> str | None:
    """Messages are newest-first (server order) — first assistant wins."""
    try:
        h = history(session_id)
    except OcError as exc:
        if exc.status in (404, 400):
            return None
        raise
    for m in h.get("messages", []):
        if m.get("type") == "assistant":
            txt = "".join(p.get("text", "") for p in m.get("parts", [])
                          if p.get("type") == "text")
            if txt.strip():
                return txt
    return None


# Turn-level finish codes: a message with these marks its turn is OVER.
_TURN_ENDS = ("stop", "length", "aborted", "error")


def _turn_done_message(m: dict) -> bool:
    """True only for a history entry that proves the whole turn has ended."""
    mt = m.get("type")
    if mt == "idle":
        return True                      # session idle = turn fully ended
    if mt != "assistant":
        return False
    if m.get("finish") in _TURN_ENDS and m.get("completed"):
        return True                      # final node of a finished turn
    return False  # intermediate node (finish='tool-calls') — never conclude


def _done(session_id: str) -> bool:
    """Strict: the turn is done only if the newest entry proves it ended.

    Intermediate assistant nodes (finish='tool-calls') are completed as
    steps, but the turn continues — treating them as done was the root
    cause of premature fix cycles in v0.3.0.
    """
    try:
        h = history(session_id)
    except OcError as exc:
        if exc.status in (404, 400):
            return False
        raise
    for m in h.get("messages", []):      # newest-first
        if _turn_done_message(m):
            return True
        if m.get("type") in ("assistant", "user"):
            return False                 # newest real entry: turn in progress
    return False


def done_since(session_id: str, since_message_id: str | None) -> bool:
    """Done when the turn started AFTER our own user message
    *since_message_id* has really ended (idle or final assistant node).

    If our message is not yet visible in the snapshot (OpenCode list lag of
    a few ms) we return False and keep polling — never fall back to a racy
    whole-session check. That fallback race is what fired the fix cycle
    while the model was still mid-turn in v0.3.0.
    """
    if not since_message_id:
        return _done(session_id)
    try:
        h = history(session_id)
    except OcError as exc:
        if exc.status in (404, 400):
            return False
        raise
    msgs = h.get("messages", [])
    t0 = None
    for m in msgs:
        if m.get("id") == since_message_id:
            t0 = m.get("created") or (m.get("time") or {}).get("created")
            break
    if not t0:
        return False                     # id not in list yet — keep waiting
    for m in msgs:                       # newest-first (mapped)
        # Mapped entries carry the timestamp at the TOP level ("created");
        # m["time"] never exists after mapping — reading it was the bug that
        # made v0.3.0 read created=0 for everything.
        created = m.get("created")
        if created is None:
            created = (m.get("time") or {}).get("created") or 0
        if created and created <= t0:
            break                        # reached our own message — nothing newer
        if _turn_done_message(m):
            return True
    return False


def wait_done(session_id: str, timeout: float = 240.0,
              since_message_id: str | None = None,
              headless: bool = False, project: str = "",
              task_id: str = "") -> bool:
    """AF-29 — wait for a VERIFIED successful turn, not merely a terminal node.

    ``aborted``/``error``/``interrupted``/failed outcomes are honest failures
    (immediate False).  In headless mode a pending OpenCode permission is
    rejected by policy instead of hanging forever with no browser to answer.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        # AF-36 — record real tool outcomes from the BACKEND on every poll,
        # not only when a browser happens to hold an SSE connection.
        if project:
            record_tool_events(project, session_id, task_id)
        try:
            msgs = history(session_id).get("messages") or []
        except OcError:
            msgs = []
        verdict = action_gate.turn_verdict(msgs, since_message_id)
        if verdict == opstatus.PASS:
            return True
        if verdict == opstatus.FAIL:
            return False
        if headless:
            try:
                # AF-44 — shared handler returns the rejection reason to the
                # model so the same session can recover instead of aborting.
                verdict = headless_permissions(project, session_id, task_id)
                if verdict.get("rejections"):
                    log.info("headless policy rejected %d call(s) in %s",
                             verdict["rejections"], session_id)
            except Exception:  # noqa: BLE001 — policy denial is best-effort logged
                log.warning("headless permission rejection failed for %s",
                            session_id, exc_info=True)
        time.sleep(1.5)
    return False


def plan(project: str, goal: str, agent: str | None = None) -> dict:
    """Analyze the request with the model and produce a visible plan.

    AF-06.1: `agent` scopes the injected memory context exactly like
    generate/fix; callers without an agent keep the previous behavior."""
    if not (goal or "").strip():
        raise OcError(400, "هدف پروژه خالی است")
    sid, _ = _ensure_session(project, PLAN_TITLE_PREFIX,
                             f"{PLAN_TITLE_PREFIX} — {goal.strip()[:40]}")
    instruction = (
        RTL_INSTRUCTIONS.format(project=project, type=_meta_type_label(project))
        + f"""
مأموریت فعلی: «برنامه‌ریزی» فقط. هیچ فایلی نساز.
درخواست کاربر: {goal}

خروجی را دقیقاً در این قالب بده:
## هدف
## نوع نرم‌افزار
## نیازمندی‌ها (فهرست)
## فناوری انتخابی
## ساختار فایل‌ها (درخت)
## مراحل پیاده‌سازی (شماره‌دار، کوتاه)
""" + perception_block(project, sid, task_text=goal, focus="plan"))
    resp = prompt(project, sid, instruction, agent=agent)
    return {"session_id": sid, "message_id": resp["message_id"],
            "state": "planning"}


def plan_text(project: str) -> str | None:
    best = _latest_session(project, PLAN_TITLE_PREFIX)
    return _last_assistant_text(best) if best else None


def generate(project: str, goal: str, include_plan: str | None = None,
             model: str | None = None, agent: str | None = None,
             plan: dict | None = None,
             reasoning: str | None = None) -> dict:
    """Kick off a REAL build session in the project directory.

    P7-0: every build owns a NEW session — concurrent generations never
    share one (the old prefix-reuse made two parallel builds interleave
    their prompts inside the same session). The latest session per project
    is recorded so the panel view and fix_request target the right build.
    P7-3: `model` optionally overrides the model for THIS build only
    ("providerID/modelID" — the agent registry routes through here).
    AF-06: `agent` is passed to the bridge so the injected memory context is
    scoped to this agent's own memory (project context is scoped by `project`).
    """
    if not (goal or "").strip():
        raise OcError(400, "هدف پروژه خالی است")
    sid = create_session(
        project, f"{GEN_TITLE_PREFIX} — {goal.strip()[:40]}")["id"]
    _record_gen(project, sid)
    instruction = RTL_INSTRUCTIONS.format(
        project=project, type=_meta_type_label(project))
    if include_plan:
        instruction += ("برنامه تأییدشده به شرح زیر است؛ عیناً اجرا کن:\n"
                        + include_plan[:6000] + "\n\nحالا فایل‌ها را واقعاً بساز.")
    else:
        instruction += f"درخواست ساده کاربر (بدون نیاز به برنامه): {goal}\nفایل‌های لازم را از صفر و به‌طور کامل بساز — ابتدا فایل‌های حیاتی (برای وب: index.html) را بساز و بعد بقیه."
    ptype = _meta_type(project)
    instruction += TYPE_REQUIREMENTS.get(ptype, "")
    if ptype in ("fullstack", "fullstack-python"):
        instruction += FS_REQUIREMENT
    if ptype == "fullstack":
        # AF-21 — template-free build: the product must DECLARE its own API
        # surface so the behavioural contract (typetests) can drive the
        # product's real design instead of the template's shape.
        instruction += ("\nالزام نهایی (محصول بدون قالب): فایل api-contract.json "
                        "در ریشهٔ پروژه بنویس که سطح واقعی API محصولت را اعلام کند: "
                        '{"csrf": {"endpoint": "/api/csrf", "token_key": "csrf_token", '
                        '"header": "X-CSRF-Token"}, "endpoints": {"register": "...", '
                        '"login": "...", "me": "...", "admin_users": "..."}, '
                        '"status": {"register": 200, "me_anon": 200, "create": 201, '
                        '"delete": 200}, "entity": {"path": "/api/...", '
                        '"list_key": "...", "item_key": "...", "id_key": "id", '
                        '"sample": {...}, "update_method": "PUT", "update_body": {...}, '
                        '"invalid_body": {...}}} — با مقادیر واقعی کد خودت؛ تست رفتاری '
                        'کیفیت همین اعلام را روی سرور واقعی اجرا می‌کند.\n')
    elif ptype in ("webpage", "shop", "dashboard", "webtool", "webapp"):
        instruction += "\nالزام نهایی این درخواست: وجود index.html اجراپذیر در ریشه پروژه الزامی است."
    instruction += "\n\n" + perception_block(project, sid, task_text=goal or "",
                                             plan=plan, focus="execute")
    # AF-51 — per-task reasoning level applied to THIS session before the
    # first turn, using only a real model variant.
    reasoning_result = None
    try:
        from core import reasoning as _reasoning
        lvl = str(reasoning or _reasoning.default_level())
        reasoning_result = _reasoning.resolve_for_model(model, lvl, goal, "generate")
    except Exception as exc:  # noqa: BLE001 — capability lookup never blocks build
        log.warning("reasoning resolve failed: %s", exc)
    resp = prompt(project, sid, instruction, model=model, agent=agent,
                  variant=(reasoning_result or {}).get("variant"))
    append_history(project, "generate", f"goal={goal[:200]} session={sid}")
    touch(project, "generate")
    return {"session_id": sid, "message_id": resp["message_id"],
            "state": "generating", "reasoning": reasoning_result}


def generation_text(project: str) -> str | None:
    sid = generation_session(project)
    return _last_assistant_text(sid) if sid else None


def generation_session(project: str) -> str | None:
    """P7-0: prefer the explicitly recorded latest build session; fall back
    to the most recently updated prefix match (sessions that predate a
    server restart are re-recorded on first use)."""
    sid = _recorded_gen(project)
    if sid and _session_in_project(project, sid):
        return sid
    sid = _latest_session(project, GEN_TITLE_PREFIX)
    if sid:
        _record_gen(project, sid)
    return sid


def _meta_type(project: str) -> str:
    d = paths.project_dir(project)
    return read_meta(d).get("type", "")


def _meta_type_label(project: str) -> str:
    return TYPES.get(_meta_type(project), "نامشخص")


def fix_request(project: str, errors: list[dict], cycle_no: int,
                agent: str | None = None,
                files: list[str] | None = None,
                strategy: str | None = None,
                plan: dict | None = None,
                model: str | None = None) -> dict:
    """Send real error report to the build session (engine's repair step).

    AF-06.1: `agent` scopes the injected memory context exactly like
    generate; callers without an agent keep the previous behavior.
    AF-15: `files` (optional) scopes the fix to the named target files —
    the instruction asks the model to touch ONLY those files (the caller
    still verifies scope afterwards; this is guidance, not trust)."""
    sid = generation_session(project)
    if not sid:
        raise OcError(400, "نشست تولید برای این پروژه وجود ندارد")
    lines = ["بررسی خودکار خطا یافت کرد؛ فقط همین خطاها را مینیمالِ و دقیق اصلاح کن و پاسخ کوتاه بده (بدون بازسازی کل پروژه):"]
    for e in errors[:15]:
        lines.append(f"- فایل {e.get('file')}: {e.get('kind')} → {e.get('message')}")
    if files:
        lines.append("\nمحدودهٔ مجاز تغییر: فقط فایلهای زیر — هیچ فایل دیگری را نساز، حذف نکن یا تغییر نده: "
                     + ", ".join(files[:MAX_FIX_FILES]))
    if strategy:
        lines.append("\nاستراتژی اصلاح پیشنهادی (بر اساس شکست واقعی): " +
                     str(strategy)[:80] +
                     " — از تکرار همان اقدام شکستخورده خودداری کن.")
    err_text = " ".join(str(e.get("message") or "") for e in errors[:8])
    # AF-43 — a wrong/missing path must be recovered from the verified project
    # map, never by searching outside the project root or repeating the call.
    if re.search(r"(?i)(no such file|not found|cannot find|path)", err_text):
        try:
            pm = project_snapshot.path_map(project)
            rels = [f.get("rel") for f in
                    ((pm.get("files") or {}).get("value") or [])[:10]
                    if isinstance(f, dict)]
            if rels:
                err_text += ("\nمسیرهای تأییدشده پروژه: " + ", ".join(rels) +
                             " — از این مسیرها فقط استفاده کن.")
        except Exception:  # noqa: BLE001
            pass
    instruction = (RTL_INSTRUCTIONS.format(
        project=project, type=_meta_type_label(project))
        + TYPE_REQUIREMENTS.get(_meta_type(project), "")
        + "\n".join(lines)
        + f" (دوره اصلاح #{cycle_no})"
        + "\n\n[محدودیت اجرای خودکار] سرور، شبکه و فرایند پسزمینه مجاز نیستند؛ "
          "کارخانه خودش سرور واقعی را اجرا و API را بررسی میکند. "
          "فقط تستهای محلی پروژه را با دستور مجاز اجرا کن."
        + "\n\n" + perception_block(project, sid, task_text=err_text, plan=plan,
                                            focus="verify"))
    resp = prompt(project, sid, instruction, agent=agent, model=model)
    return {"session_id": sid, "message_id": resp["message_id"]}


ANDROID_FIX_MAX = 3          # bounded self-repair cycles for real build errors
ANDROID_FIX_WAIT_S = 300     # per repair turn (same as FIX_WAIT_S)


def android_build_repair(project: str, build, agent: str | None = None,
                         max_cycles: int = ANDROID_FIX_MAX) -> dict:
    """AF-50 — bounded, evidence-only self-repair for REAL Android build errors.

    ``build`` is the existing engine build callable. A compiler failure with
    structured findings (file/line/kind/message) is sent to the SAME generation
    session through the existing ``fix_request`` path; no new session, no
    message while the session is busy, no manual product edits. After every
    repair the real build is retried and independently verified by the caller.
    """
    from core.engines.registry import EngineError
    cycles = 0
    last_sig: str | None = None
    repair_log: list[dict] = []
    while True:
        try:
            rep = dict(build())
            rep["repair_cycles"] = cycles
            rep["repair_log"] = repair_log
            return rep
        except EngineError as exc:
            findings = [dict(f) for f in (getattr(exc, "findings", None) or [])
                        if isinstance(f, dict)]
            raw = findings or [{"file": "", "line": None, "kind": "build",
                                "message": exc.message}]
            sig_raw = json.dumps(
                [{k: f.get(k) for k in ("file", "line", "kind", "message")}
                 for f in raw], ensure_ascii=False, sort_keys=True)
            sig = hashlib.sha256(sig_raw.encode("utf-8")).hexdigest()[:16]
            if sig == last_sig:
                raise EngineError(422,
                    "خطای ساخت تکراری پس از اصلاح — چرخهٔ بینتیجه متوقف شد",
                    findings=raw)
            last_sig = sig
            if cycles >= max_cycles:
                raise EngineError(422,
                    "ساخت اندروید پس از %d دور اصلاح واقعی ناموفق ماند" % cycles,
                    findings=raw)
            sid = generation_session(project)
            if not sid:
                raise EngineError(409,
                    "هیچ نشست تولیدی برای ارسال خطای ساخت وجود ندارد")
            st = state(sid)
            interrupted = False
            if st.get("running"):
                # AF-50 — never prompt a busy session. That is exactly how
                # AF-49's ECONNRESET/stall happened.
                interrupt(sid)
                interrupted = True
                for _ in range(15):
                    time.sleep(2)
                    if not state(sid).get("running"):
                        break
                else:
                    raise EngineError(409,
                        "نشست پس از قطع صناعی همچنان busy باقی ماند — ارسال پیام ممنون")
            err = st.get("last_error") or {}
            if str(err.get("type") or "") == "provider.transport" and not interrupted:
                # a transport error that never settled cannot accept a prompt
                raise EngineError(409,
                    "خطای transport مدل در نشست پاک نشده است؛ ادامه ایمن نیست")
            errors = []
            for f in raw[:6]:
                loc = f.get("line")
                msg = str(f.get("message") or "")[:400]
                errors.append({
                    "file": str(f.get("file") or "project"),
                    "kind": str(f.get("kind") or "compile"),
                    "message": ("خط %s: %s" % (loc, msg)) if loc else msg})
            fx = fix_request(project, errors, cycle_no=cycles + 1,
                             agent=agent, strategy="android-build-repair")
            ok = wait_done(fx["session_id"], ANDROID_FIX_WAIT_S,
                           since_message_id=fx.get("message_id"),
                           headless=True, project=project)
            if not ok:
                raise EngineError(504,
                    "دور اصلاح اندروید بدون شاهد واقعی متوقف شد (Session همان است)")
            cycles += 1
            repair_log.append({"cycle": cycles, "signature": sig,
                               "findings": raw[:6]})


def testrun_combined(project: str) -> dict:
    """P6-1 combined REAL testing — single source of truth shared by the
    panel API (P7-3 refactor) and the orchestrator, so both gates are
    EXACTLY the same: the project's own unittest suite (runner.run_tests)
    AND per-type behavioural contract tests (typetests.run). Neither part
    can fake green: if neither has coverage the answer is honest
    «بدون پوشش تست» with ran=false."""
    from core import runner, typetests
    own = runner.run_tests(project)
    try:
        contract = typetests.run(project)
    except (RuntimeError, OcError, OSError) as exc:
        contract = {"ran": False, "ok": False, "no_coverage": True,
                    "note": f"تست قرارداد اجرا نشد: {exc}", "errors": []}
    parts = [p for p in (own, contract) if p.get("ran")]
    if not parts:
        note = (contract.get("note") or own.get("note")
                or "فایل آزمون یا سطح اجرایی یافت نشد")
        # AF-29 / ActionGate: zero executed tests is NOT success.
        return {"ok": False, "ran": False, "no_coverage": True,
                "note": "بدون پوشش تست — " + note,
                "verification": action_gate.gate_test({"ran": False, "ok": False,
                                                       "note": note}),
                "own_tests": own, "contract": contract}
    all_ok = all(bool(p.get("ok")) for p in parts)
    notes = [p["note"] for p in parts if p.get("note")]
    res = {"ok": all_ok, "ran": True, "no_coverage": False,
           "own_tests": own, "contract": contract}
    if notes:
        res["note"] = "؛ ".join(notes)
    if own.get("ran"):
        res["output"] = own.get("output", "")
    if contract.get("ran") and not contract.get("ok"):
        res["errors"] = contract.get("errors", [])
    res["verification"] = action_gate.gate_test(res)
    return res
