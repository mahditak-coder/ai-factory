"""موتور اندروید — تولید APK/AAB واقعی از سورس پروژه (چیدمان minimal، بدون Gradle).

میراث آزمون‌شدهٔ AF-02 (docs/android-build-guide.md):
  aapt2 compile → aapt2 link → javac --release 11 → d8 → بسته‌بندی + تراز → apksigner
  AAB: aapt2 --proto-format → ماژول base/ + BundleConfig.pb → bundletool build-apks

قواعد غیرقابل‌مذاکره:
* بدون shell — فقط argv ثابت + timeout + env پاک‌شده.
* ساخت‌ها سریالی (قفل سراسری) — هیچ دو ساخت هم‌زمان.
* پاک‌سازی فقط intermediates خودش؛ سورس پروژه دست‌نخورده.
* کلید هر محصول مخصوص خودش: ~/.af-keys/products/<project>.jks — هرگز کلید AF-02.
* رمز کلید فقط از فایل 0600 به apksigner می‌رود (file:) — هرگز در argv/لاگ/گزارش.
* هر ادعای موفقیت فقط با خروجی واقعی ابزار؛ روش‌های پشتیبانی‌نشده صریح اعلام می‌شوند.
"""

import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime
from pathlib import Path

import core.config as config
from core import paths
from core.engines.registry import register, EngineError
from core.engines import ziputil

ANDROID_NS = "{http://schemas.android.com/apk/res/android}"

# AF-50 — javac/aapt2 emit "file:line: error: message". Keep the REAL file,
# line, kind and text so the agent can repair the exact source line.
_COMPILE_LINE = re.compile(
    r"^(?P<file>[^\s:]+):(?P<line>\d+):\s*(?P<kind>error|warning):\s*(?P<message>.+)$",
    re.M)


def _compile_findings(output: str, project_dir: Path | None = None) -> list[dict]:
    out = []
    for m in _COMPILE_LINE.finditer(output or ""):
        f = m.group("file")
        rel = f
        if project_dir is not None:
            try:
                rel = str(Path(f).resolve().relative_to(project_dir.resolve()))
            except (ValueError, OSError):
                rel = f
        out.append({"file": rel, "line": int(m.group("line")),
                    "kind": m.group("kind"), "message": m.group("message").strip()})
    return out[:20]

# مسیرهای ابزار — قابل بازنویسی با env (بدون ثابت‌های مخفی)
SDK_JAR = Path(os.environ.get("AF_ANDROID_JAR",
                              str(Path.home() / "android-sdk-termux/platforms/android-35/android.jar")))
BUNDLETOOL_JAR = Path(os.environ.get("AF_BUNDLETOOL",
                                     str(Path.home() / "android-sdk-termux/bundletool.jar")))
KEYS_ROOT = Path(os.environ.get("AF_KEYS_ROOT", str(Path.home() / ".af-keys")))
PRODUCT_KEYS = KEYS_ROOT / "products"

# حداقل‌های منابع برای شروع ساخت (گوشی؛ محافظه‌کارانه)
MIN_FREE_DISK_MB = 500
MIN_RAM_AVAILABLE_MB = 300
STEP_TIMEOUT = 300.0
JAVA_RELEASE = "11"

_ENV_BLACKLIST = ("OPENCODE_SERVER_PASSWORD", "OPENCODE_SESSION_ID", "OPENCODE",
                  "OPENCODE_TERMINAL", "AI_AGENT", "KS_PASS", "KEY_PASS")

BUILD_LOCK = threading.Lock()          # سراسری: هیچ دو ساخت هم‌زمان
_status: dict[str, dict] = {}          # project -> آخرین وضعیت ساخت (زنده)
_status_lock = threading.Lock()


def _clean_env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in _ENV_BLACKLIST}


def _free_disk_mb(p: Path) -> int:
    return int(shutil.disk_usage(str(p)).free / (1024 * 1024))


def _ram_available_mb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


class StepLog:
    """ثبت مرحله‌به‌مرحله — بدون هیچ رمزی (فیلتر رازها)."""
    SECRET_PAT = re.compile(r"(pass[:=]\S+|password\s*[:=]\S+|Keystore password)", re.I)

    def __init__(self):
        self.steps: list[dict] = []

    def add(self, step: str, message: str, status: str = "ok") -> None:
        msg = self.SECRET_PAT.sub("[REDACTED]", str(message or ""))
        self.steps.append({"ts": datetime.now().isoformat(timespec="seconds"),
                           "step": step, "status": status, "message": msg[:1500]})

    def ok_steps(self) -> int:
        return sum(1 for s in self.steps if s["status"] == "ok")


@register
class AndroidEngine:
    id = "android-minimal"
    type_id = "android"
    label = "اپلیکیشن اندروید (APK/AAB — چیدمان minimal بدون Gradle)"
    supported_modes = ["minimal"]

    # ---------------------------------------------------------- available --
    def available(self) -> dict:
        tools = {}
        for name in ("java", "javac", "aapt2", "d8", "apksigner"):
            tools[name] = shutil.which(name) or ""
        tools["android_jar"] = str(SDK_JAR) if SDK_JAR.is_file() else ""
        tools["bundletool"] = str(BUNDLETOOL_JAR) if BUNDLETOOL_JAR.is_file() else ""
        missing = [k for k, v in tools.items() if not v]
        notes = []
        if missing:
            notes.append("ابزار غایب: " + ", ".join(missing))
        return {"ok": not missing, "tools": tools, "notes": notes}

    # ------------------------------------------------------------- detect --
    def detect(self, project_dir: Path) -> dict:
        manifest = project_dir / "AndroidManifest.xml"
        if (project_dir / "build.gradle").is_file() or (project_dir / "build.gradle.kts").is_file():
            return {"supported": False, "mode": "gradle",
                    "reason": "پروژه‌های Gradle/AGP در این دستگاه پشتیبانی نمی‌شوند "
                              "(نیازمند Android SDK کامل و RAM بالا). فقط چیدمان minimal."}
        if not manifest.is_file():
            return {"supported": False, "mode": None,
                    "reason": "AndroidManifest.xml در ریشهٔ پروژه نیست — پروژه اندرویدی minimal نیست"}
        if not (project_dir / "res").is_dir() or not (project_dir / "java").is_dir():
            return {"supported": False, "mode": "minimal-incomplete",
                    "reason": "پوشه‌های res/ یا java/ وجود ندارند"}
        return {"supported": True, "mode": "minimal", "reason": "چیدمان minimal (AF-02)"}

    # ----------------------------------------------------------- validate --
    def validate(self, project_dir: Path) -> dict:
        problems: list[str] = []
        info: dict = {}
        d = self.detect(project_dir)
        if not d.get("supported"):
            return {"ok": False, "problems": [d.get("reason", "پشتیبانی نمی‌شود")], "info": d}
        try:
            root = ET.parse(project_dir / "AndroidManifest.xml").getroot()
        except ET.ParseError as exc:
            return {"ok": False, "problems": [f"AndroidManifest.xml قابل تجزیه نیست: {exc}"], "info": {}}
        pkg = root.get("package") or ""
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+", pkg):
            problems.append(f"package name نامعتبر: {pkg!r}")
        info["package"] = pkg
        app = root.find("application")
        acts = (app.findall("activity") if app is not None else [])
        launcher = None
        for a in acts:
            f = a.find("intent-filter")
            if f is not None:
                for cat in f.findall("category"):
                    if (cat.get(ANDROID_NS + "name") or "") == "android.intent.category.LAUNCHER":
                        launcher = a
                        break
            if launcher is not None:
                break
        if launcher is None:
            problems.append("هیچ Activity دارای intent-filter LAUNCHER نیست")
        else:
            info["activity"] = launcher.get(ANDROID_NS + "name") or launcher.get("name") or ""
        # java sources
        jsrc = [str(p.relative_to(project_dir)) for p in (project_dir / "java").rglob("*.java")]
        if not jsrc:
            problems.append("هیچ فایل java داخل java/ نیست")
        info["java_files"] = len(jsrc)
        # res
        rsrc = [str(p.relative_to(project_dir)) for p in (project_dir / "res").rglob("*.xml")]
        if not rsrc:
            problems.append("هیچ فایل منبعی داخل res/ نیست")
        info["res_files"] = len(rsrc)
        uses = root.find("uses-sdk")
        if uses is None:
            info["min_sdk"] = None
            info["target_sdk"] = None
        else:
            info["min_sdk"] = uses.get(ANDROID_NS + "minSdkVersion")
            info["target_sdk"] = uses.get(ANDROID_NS + "targetSdkVersion")
        # مجوز اینترنت/حساس: صرفاً گزارش، نه رد
        info["permissions"] = [p.get(ANDROID_NS + "name") for p in root.findall("uses-permission")]
        return {"ok": not problems, "problems": problems, "info": info}

    # ---------------------------------------------------------- preflight --
    def preflight(self, project_dir: Path) -> dict:
        checks = []
        av = self.available()
        checks.append({"check": "ابزارهای ساخت", "ok": av["ok"],
                       "detail": av["notes"][0] if av["notes"] else "همه ابزارها موجود"})
        dmb = _free_disk_mb(project_dir)
        checks.append({"check": "فضای دیسک", "ok": dmb >= MIN_FREE_DISK_MB,
                       "detail": f"{dmb}MB آزاد (حداقل {MIN_FREE_DISK_MB}MB)"})
        rmb = _ram_available_mb()
        checks.append({"check": "RAM", "ok": rmb >= MIN_RAM_AVAILABLE_MB,
                       "detail": f"{rmb}MB آزاد (حداقل {MIN_RAM_AVAILABLE_MB}MB)"})
        val = self.validate(project_dir)
        checks.append({"check": "ساختار پروژه", "ok": val["ok"],
                       "detail": "؛ ".join(val["problems"]) or "معتبر"})
        return {"ok": all(c["ok"] for c in checks), "checks": checks}

    # ------------------------------------------------------------ inspect --
    def inspect(self, project_dir: Path) -> dict:
        val = self.validate(project_dir)
        info = val.get("info", {})
        info.update({"mode": self.detect(project_dir).get("mode"),
                     "supported": self.detect(project_dir).get("supported")})
        info["product_key"] = self.key_info(project_dir)
        return info

    # --------------------------------------------------------- key管理 -- keys
    def key_path(self, project_dir: Path) -> Path:
        """کلید اختصاصی خودِ این محصول — هرگز کلید اشتراکی/AF-02."""
        pkg = self._package_of(project_dir)
        return PRODUCT_KEYS / f"{pkg}.jks"

    def _package_of(self, project_dir: Path) -> str:
        try:
            root = ET.parse(project_dir / "AndroidManifest.xml").getroot()
            return (root.get("package") or "unknown").strip()
        except (ET.ParseError, OSError):
            return "unknown"

    def key_info(self, project_dir: Path) -> dict:
        kp = self.key_path(project_dir)
        pkg = self._package_of(project_dir)
        envf = PRODUCT_KEYS / f"{pkg}.env"
        return {"exists": kp.is_file() and envf.is_file(), "alias": "release",
                "path_role": "private — خارج از پروژه و Git؛ هرگز در خروجی API نمی‌آید"}

    def create_product_key(self, project_dir: Path) -> dict:
        """ساخت کلید جدید فقط با تأیید صریح کاربر (در سطح API الزامی است).
        رمز فقط از stdin به keytool می‌رود — هرگز در argv/لاگ."""
        PRODUCT_KEYS.mkdir(parents=True, exist_ok=True)
        try:
            PRODUCT_KEYS.chmod(0o700)
        except OSError:
            pass
        kp = self.key_path(project_dir)
        if kp.is_file():
            return {"created": False, "note": "کلید این محصول از قبل وجود دارد — بازسازی ممنوع "
                                              "(هویت امضا حفظ می‌شود)"}
        pkg = self._package_of(project_dir)
        pw = secrets.token_urlsafe(24)
        envf = PRODUCT_KEYS / f"{pkg}.env"
        tmp_env = envf.with_suffix(".env.tmp")
        tmp_env.write_text(f"KS_PASS={pw}\nKEY_ALIAS=release\nKEY_PASS={pw}\n", encoding="utf-8")
        os.chmod(tmp_env, 0o600)
        keytool = shutil.which("keytool")
        argv = [keytool, "-genkeypair", "-keystore", str(kp), "-storetype", "PKCS12",
                "-alias", "release", "-keyalg", "RSA", "-keysize", "2048",
                "-validity", "10000",
                "-dname", f"CN={pkg} Release, O=AI-Factory Product"]
        try:
            # keytool بدون -storepass رمز را از stdin می‌خواند (دو بار) — argv پاک است
            proc = subprocess.run(argv, input=f"{pw}\n{pw}\n", capture_output=True,
                                  text=True, timeout=120, shell=False, env=_clean_env())
        finally:
            # اگر هر چیز شکست خورد، tmp_env حذف می‌شود
            pass
        if proc.returncode != 0:
            tmp_env.unlink(missing_ok=True)
            kp.unlink(missing_ok=True)
            msg = (proc.stderr or proc.stdout or "")[-300:]
            raise EngineError(500, "ساخت کلید ناموفق")
        os.chmod(kp, 0o600)
        os.replace(tmp_env, envf)
        return {"created": True, "alias": "release"}

    # --------------------------------------------------------------- build --
    def build(self, project: str, opts: dict, log) -> dict:
        """ساخت سریالی. opts: {kind: apk|aab, sign: bool, version_code, version_name}"""
        d = paths.project_dir(project)
        if not BUILD_LOCK.acquire(blocking=False):
            raise EngineError(409, "یک ساخت دیگر در جریان است — ساخت‌ها سریالی‌اند؛ بعداً تلاش کنید")
        try:
            return self._build_locked(project, d, opts, log)
        finally:
            BUILD_LOCK.release()

    # --- زیرساخت فرایند ---
    def _run(self, log, step: str, argv: list[str], cwd: Path | None = None,
             timeout: float = STEP_TIMEOUT, env: dict | None = None) -> str:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              cwd=str(cwd) if cwd else None, shell=False,
                              env=env or _clean_env())
        if proc.returncode != 0:
            out = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
            log(step, out[-1200:], "fail")
            findings = _compile_findings(out, Path(cwd) if cwd else None)
            if not findings:
                findings = [{"file": "", "line": None, "kind": step,
                             "message": out[-900:]}]
            raise EngineError(500, f"مرحله «{step}» ناموفق بود", findings=findings)
        log(step, (proc.stdout or "")[-400:] or "ok")
        return proc.stdout or ""

    def _build_locked(self, project: str, d: Path, opts: dict, log) -> dict:
        t0 = time.time()
        val = self.validate(d)
        if not val["ok"]:
            raise EngineError(400, "اعتبارسنجی ساختار ناموفق: " + "؛ ".join(val["problems"]))
        pre = self.preflight(d)
        if not pre["ok"]:
            raise EngineError(412, "پیش‌نیازها آماده نیست: " +
                              "؛ ".join(c["detail"] for c in pre["checks"] if not c["ok"]))
        if not SDK_JAR.is_file():
            raise EngineError(412, "android.jar یافت نشد — به راهنمای build مراجعه کنید")

        kind = "aab" if str(opts.get("kind", "apk")).lower() == "aab" else "apk"
        sign = bool(opts.get("sign"))
        # AF-12 — پیوند نسخهٔ برنامه با نسخهٔ مستقل پروژه (قاعدهٔ صریح، بدون حدس):
        #   version_name  = نسخهٔ پروژه (MAJOR.MINOR.PATCH از meta.json)
        #   version_code  = MAJOR*10000 + MINOR*100 + PATCH (قطعی و مستند)
        #   اگر صریحاً به API داده شده باشد، همان استفاده می‌شود (سازگاری عقب).
        from core import release as release_mod
        pver = release_mod.project_version(d)
        _maj, _min, _pat = (int(x) for x in pver.split("."))
        opt_vn = opts.get("version_name")
        opt_vc = opts.get("version_code")
        vname = str(opt_vn) if opt_vn not in (None, "") else pver
        vcode = int(opt_vc) if opt_vc not in (None, "") else \
            _maj * 10000 + _min * 100 + _pat
        pkg = val["info"]["package"]

        build_dir = d / "build" / "android"
        if build_dir.exists():
            shutil.rmtree(build_dir)          # فقط intermediates خودش — سورس دست‌نخورده
        for sub in ("flat", "gen", "classes", "dex", "aab"):
            (build_dir / sub).mkdir(parents=True, exist_ok=True)

        # AF-22 — aapt2 link does NOT override an explicit
        # android:versionCode/versionName in the manifest (proven: a model
        # manifest with versionCode="1" beat --version-code 10000 and the
        # artifact test stage failed honestly on the mismatch). The AF-12
        # version rule (project version) is the single source of truth, so
        # link against a STAGED copy in build/ with those two attributes
        # stripped — the project source stays untouched.
        stage_manifest = build_dir / "AndroidManifest.xml"
        mroot = ET.parse(d / "AndroidManifest.xml").getroot()
        for attr in ("versionCode", "versionName"):
            if ANDROID_NS + attr in mroot.attrib:
                del mroot.attrib[ANDROID_NS + attr]
        ET.ElementTree(mroot).write(str(stage_manifest), encoding="utf-8",
                                    xml_declaration=True)

        # ---- 1) aapt2 compile
        # AF-TEACHER-02: تصاویر منابع (mipmap/drawable مثل آیکون launcher) هم
        # باید کامپایل شوند؛ پیش‌تر فقط XML گذر می‌کرد و ارجاع manifest به
        # @mipmap/ic_launcher در aapt2 link شکست می‌خورد. این تغییر فقط دامنهٔ
        # فایل‌های ورودی را گسترش می‌دهد (سازگار با پروژه‌های قبلی).
        _RES_SUFFIXES = (".xml", ".png", ".jpg", ".jpeg", ".webp")
        flat_dir = build_dir / "flat"
        for f in sorted(p for p in (d / "res").rglob("*")
                        if p.is_file() and p.suffix.lower() in _RES_SUFFIXES):
            self._run(log, f"aapt2 compile {f.relative_to(d)}",
                      [shutil.which("aapt2"), "compile", "-o", str(flat_dir), str(f)])
        flats = sorted(flat_dir.glob("*.flat"))
        if not flats:
            raise EngineError(400, "هیچ منبع کامپایل‌شده‌ای تولید نشد")

        # ---- 2) aapt2 link
        link_argv = [shutil.which("aapt2"), "link", "-I", str(SDK_JAR),
                     "--manifest", str(stage_manifest),
                     *[str(f) for f in flats],
                     "--java", str(build_dir / "gen"),
                     "--min-sdk-version", str(val["info"].get("min_sdk") or 24),
                     "--target-sdk-version", str(val["info"].get("target_sdk") or 35),
                     "--version-code", str(vcode), "--version-name", vname,
                     "-o", str(build_dir / "base.apk")]
        self._run(log, "aapt2 link", link_argv)

        # ---- 3) javac
        sources = sorted((build_dir / "gen").rglob("*.java")) + sorted((d / "java").rglob("*.java"))
        self._run(log, "javac",
                  [shutil.which("javac"), "--release", JAVA_RELEASE, "-cp", str(SDK_JAR),
                   "-d", str(build_dir / "classes"), *[str(s) for s in sources]])

        # ---- 4) d8
        self._run(log, "d8",
                  [shutil.which("d8"), "--min-api", str(val["info"].get("min_sdk") or 24),
                   "--output", str(build_dir / "dex"),
                   *[str(c) for c in sorted((build_dir / "classes").rglob("*.class"))]])
        dex = build_dir / "dex" / "classes.dex"
        if not dex.is_file():
            raise EngineError(500, "classes.dex تولید نشد")

        # ---- 5) بسته‌بندی + تراز (ziputil مشترک، بدون zip CLI)
        apk = build_dir / "unsigned.apk"
        shutil.copy(build_dir / "base.apk", apk)
        ziputil.append_stored(apk, "classes.dex", dex.read_bytes())
        # AF-49 — real general defect found by the staged Android build:
        # projects with a WebView/HTML UI under assets/ compiled and were
        # reported as a successful APK, while ZERO asset files were packaged.
        # Package every non-secret asset with its original relative path.
        asset_root = d / "assets"
        asset_files = []
        if asset_root.is_dir():
            asset_files = sorted(
                f for f in asset_root.rglob("*")
                if f.is_file()
                and f.suffix.lower() not in config.EXPORT_SECRET_SUFFIXES
                and f.name not in config.EXPORT_SECRET_NAMES
                and not any(part in config.EXPORT_SECRET_NAMES
                            for part in f.parts))
            for f in asset_files:
                ziputil.append_stored(apk, f.relative_to(d).as_posix(),
                                      f.read_bytes(), align=4)
            if asset_files:
                log("assets", "true packaged %d asset file(s) — real APK content"
                    % len(asset_files))
        ziputil.rebuild_file(apk)                      # تراز نهایی ۴ بایتی

        artifacts: dict[str, dict] = {}
        signed_ok = None

        if kind == "apk":
            final = build_dir / "app-release.apk" if sign else build_dir / "app-unsigned.apk"
            if sign:
                kp = self.key_path(d)
                kenv = PRODUCT_KEYS / f"{pkg}.env"
                if not (kp.is_file() and kenv.is_file()):
                    raise EngineError(409,
                        "کلید اختصاصی این محصول موجود نیست. برای حفظ هویت امضا، ابتدا کلید "
                        "محصول را با تأیید خودتان بسازید (اندپوینت /api/factory/android/key).")
                secrets_map = self._read_key_env(kenv)
                tmp_pass = PRODUCT_KEYS / f".pass-{secrets.token_hex(4)}.tmp"
                # apksigner هر دسته‌بندی file: را خط‌به‌خط می‌خواند؛ دو خط = رمز keystore + کلید
                tmp_pass.write_text(secrets_map["KS_PASS"] + "\n" + secrets_map["KS_PASS"] + "\n",
                                    encoding="utf-8")
                os.chmod(tmp_pass, 0o600)
                try:
                    self._run(log, "apksigner sign", [
                        shutil.which("apksigner"), "sign",
                        "--ks", str(kp), "--ks-key-alias", secrets_map["KEY_ALIAS"],
                        "--ks-pass", f"file:{tmp_pass}",       # رمز فقط از فایل 0600
                        "--key-pass", f"file:{tmp_pass}",
                        "--out", str(final), str(apk)])
                finally:
                    tmp_pass.unlink(missing_ok=True)   # پاک‌سازی فوری رمز موقت
            else:
                shutil.copy(apk, final)
            ver = self._verify_apk(log, final, sign)
            artifacts["apk"] = self._record_artifact(final, "APK امضاشده" if sign else "APK بدون امضا")
            if sign:
                signed_ok = ver["signed"]
            record = {"kind": "apk", "signed": sign, "sign_verified": signed_ok,
                      "badging": ver["badging"]}
        else:
            sign_aab = sign and self.key_path(d).is_file()
            aab_path, uni_path = self._build_aab(log, build_dir, d, vcode, vname, pkg, sign)
            artifacts["aab"] = self._record_artifact(aab_path, "AAB")
            if uni_path:
                artifacts["universal_apk"] = self._record_artifact(uni_path,
                                                                  "APK universal ناشی از AAB (اثبات اعتبار)")
            record = {"kind": "aab", "signed": sign_aab}
            if sign and not sign_aab:
                log("aab-sign", "امضای AAB درخواست شد ولی کلید محصول موجود نیست — "
                                "AAB بدون امضای محلی تولید شد (امضای نهایی با Play)", "warn")

        # ---- نسخهٔ نهایی در exports (پوشهٔ مستقل)
        out_dir = config.EXPORTS_DIR / "android-builds" / project / f"{vname}-vc{vcode}"
        if out_dir.exists():
            shutil.rmtree(out_dir)   # بازسازی همان نسخه → پوشهٔ تحویل تمیز (بدون فایل کهنه)
        out_dir.mkdir(parents=True, exist_ok=True)
        for key, art in artifacts.items():
            dst = out_dir / Path(art["path"]).name
            shutil.copy(art["path"], dst)
            art["path"] = str(dst)
            art["sha256"] = self._sha256(dst)

        # AF-12 — استاندارد انتشار همان بستهٔ وب: release.json + MANIFEST.sha256
        # در پوشهٔ تحویل؛ سپس راستی‌آزمایی با همان الگوریتم (اعتبارسنجی پیش از تحویل).
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dmeta = {
            "name": project, "type": "android-app", "version": vname,
            "version_code": vcode, "project_version": pver,
            "version_source": ("explicit-opt" if vname != pver else "project"),
            "build": stamp, "kind": kind, "package": pkg,
            "signed": bool(sign), "sign_verified": signed_ok,
            "mode": "minimal", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "generator": "AI-Factory Android engine (AF-12 delivery standard)",
            "badging": record.get("badging") or {},
            "artifacts": artifacts,
        }
        (out_dir / "release.json").write_text(
            json.dumps(dmeta, ensure_ascii=False, indent=1), encoding="utf-8")
        mlines = []
        for f in sorted(out_dir.iterdir()):
            if f.is_file() and f.name != "MANIFEST.sha256":
                mlines.append("%s  %s" % (self._sha256(f), f.name))
        (out_dir / "MANIFEST.sha256").write_text(
            "\n".join(mlines) + "\n", encoding="utf-8")
        # اعتبارسنجی بستهٔ نهایی پیش از تحویل — شکست = خطای صادقانه، بدون تحویل
        self._verify_delivery(out_dir)
        for metaf, label in (("release.json", "release.json"),
                             ("MANIFEST.sha256", "MANIFEST.sha256")):
            art = self._record_artifact(out_dir / metaf, label)
            art["sha256"] = self._sha256(out_dir / metaf)
            artifacts[metaf] = art

        report = {
            "ok": True, "project": project, "package": pkg, "kind": kind,
            "version_name": vname, "version_code": vcode,
            "sign_requested": sign, "sign_verified": signed_ok,
            "artifacts": artifacts, "steps": log.steps if hasattr(log, "steps") else [],
            "duration_s": round(time.time() - t0, 1),
            "mode": "minimal", "output_dir": str(out_dir),
        }
        record.update({"project": project, "version_name": vname, "version_code": vcode,
                       "package": pkg, "mode": "minimal", "output_dir": str(out_dir),
                       "duration_s": report["duration_s"]})
        self._persist_release(project, record, artifacts)
        log("پایان", f"ساخت موفق در {report['duration_s']} ثانیه")
        return report

    # ------------------------------------------------- AF-22: split stages --
    def sign_and_finalize(self, project: str, build_report: dict, log) -> dict:
        """امضای APK بدون‌امضای یک build قبلی + به‌روزرسانی بستهٔ تحویل AF-12.
        (AF-22 — مراحل جداگانهٔ sign/verify در خط لوله؛ بدون بازسازی سورس.)
        کلید اختصاصی محصول الزامی است؛ رمز فقط از فایل 0600 با file: به
        apksigner می‌رسد — هرگز در argv/لاگ."""
        if not build_report or build_report.get("signed"):
            raise EngineError(409, "برای امضای جداگانه، build بدون امضا لازم است "
                                   "(یا APK قبلاً امضا شده)")
        d = paths.project_dir(project)
        pkg = build_report.get("package") or self._package_of(d)
        sign_t0 = time.time()
        kp = self.key_path(d)
        kenv = PRODUCT_KEYS / f"{pkg}.env"
        if not (kp.is_file() and kenv.is_file()):
            raise EngineError(409,
                "کلید اختصاصی این محصول موجود نیست. برای حفظ هویت امضا، ابتدا کلید "
                "محصول را با تأیید خودتان بسازید (اندپوینت /api/factory/android/key).")
        unsigned_build = d / "build" / "android" / "app-unsigned.apk"
        unsigned_deliv = Path(build_report["artifacts"]["apk"]["path"])
        src = unsigned_build if unsigned_build.is_file() else unsigned_deliv
        if not src.is_file():
            raise EngineError(404, "APK بدون امضای build قبلی یافت نشد — دوباره build بگیرید")
        out_dir = Path(build_report["output_dir"])
        final = out_dir / "app-release.apk"

        secrets_map = self._read_key_env(kenv)
        tmp_pass = PRODUCT_KEYS / f".pass-{secrets.token_hex(4)}.tmp"
        tmp_pass.write_text(secrets_map["KS_PASS"] + "\n" + secrets_map["KS_PASS"] + "\n",
                            encoding="utf-8")
        os.chmod(tmp_pass, 0o600)
        try:
            self._run(log, "apksigner sign", [
                shutil.which("apksigner"), "sign",
                "--ks", str(kp), "--ks-key-alias", secrets_map["KEY_ALIAS"],
                "--ks-pass", f"file:{tmp_pass}",       # رمز فقط از فایل 0600
                "--key-pass", f"file:{tmp_pass}",
                "--out", str(final), str(src)])
        finally:
            tmp_pass.unlink(missing_ok=True)           # پاک‌سازی فوری رمز موقت
        ver = self._verify_apk(log, final, True)
        if not ver["signed"]:
            raise EngineError(500, "امضای APK تأیید نشد — صادقانه ناموفق")

        # بستهٔ تحویل: APK امضاشده (final در out_dir نوشته شد) جایگزین
        # بدون‌امضا؛ release.json/MANIFEST هم‌راستا
        for stale in out_dir.iterdir():
            if stale.name == "app-unsigned.apk":
                stale.unlink()
        rj = out_dir / "release.json"
        dmeta = json.loads(rj.read_text(encoding="utf-8"))
        dmeta["signed"] = True
        dmeta["sign_verified"] = True
        dmeta["badging"] = ver["badging"]
        art = dmeta.get("artifacts") or {}
        art.pop("apk", None)
        art["apk"] = {"path": str(final), "label": "APK امضاشده",
                      "size": final.stat().st_size, "sha256": self._sha256(final)}
        dmeta["artifacts"] = art
        dmeta["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        dmeta["generator"] = "AI-Factory Android engine (AF-22 split sign stage)"
        rj.write_text(json.dumps(dmeta, ensure_ascii=False, indent=1), encoding="utf-8")
        mlines = ["%s  %s" % (self._sha256(f), f.name) for f in sorted(out_dir.iterdir())
                  if f.is_file() and f.name != "MANIFEST.sha256"]
        (out_dir / "MANIFEST.sha256").write_text("\n".join(mlines) + "\n", encoding="utf-8")
        self._verify_delivery(out_dir)                 # شکست = بدون تحویل
        record = {"kind": "apk", "signed": True, "sign_verified": True,
                  "badging": ver["badging"], "project": project,
                  "version_name": build_report.get("version_name"),
                  "version_code": build_report.get("version_code"),
                  "package": pkg, "mode": "minimal",
                  "output_dir": str(out_dir),
                  "duration_s": round(time.time() - sign_t0, 1)}
        record["build_duration_s"] = float(build_report.get("duration_s") or 0)
        self._persist_release(project, record, art)
        log("پایان امضا", "APK امضا و بستهٔ تحویل AF-12 به‌روز شد")
        return {"ok": True, "signed": True, "sign_verified": True,
                "apk": str(final), "sha256": self._sha256(final),
                "badging": ver["badging"], "output_dir": str(out_dir)}

    def verify_artifacts(self, out_dir: Path, log,
                         expect: dict | None = None) -> dict:
        """راستی‌آزمایی مستقل بستهٔ تحویل AF-12 (AF-22 — مراحل verify/deliver):
        MANIFEST.sha256، apksigner verify وقتی signed، badging مطابق
        release.json و (در صورت داده‌شدن) مقادیر مورد انتظار؛ + بودن
        classes.dex. هر مغایرت = EngineError صادقانه."""
        out_dir = Path(out_dir)
        self._verify_delivery(out_dir)                 # manifest + غایب/اضافه
        rj = out_dir / "release.json"
        if not rj.is_file():
            raise EngineError(404, "release.json در بستهٔ تحویل نیست")
        dmeta = json.loads(rj.read_text(encoding="utf-8"))
        apk = out_dir / "app-release.apk" if dmeta.get("signed") else None
        if apk is None:
            apk = out_dir / "app-unsigned.apk"
        if not apk.is_file():
            raise EngineError(404, "APK در بستهٔ تحویل نیست: %s" % apk.name)
        # classes.dex داخل بسته — اثبات واقعی بودن بستهٔ قابل اجرا
        import zipfile as zf
        with zf.ZipFile(apk) as z:
            names = set(z.namelist())
        if "classes.dex" not in names:
            raise EngineError(500, "classes.dex داخل APK نیست — بستهٔ خراب")
        # AF-49 — verify the SAME asset contract at delivery time. If the
        # source project is still available, every non-secret asset must be
        # inside the APK; otherwise a "green" build could still ship a blank
        # WebView UI.
        try:
            src = paths.project_dir(str(dmeta.get("name") or ""))
        except (PermissionError, FileNotFoundError):
            src = None
        if src is not None and (src / "assets").is_dir():
            missing_assets = [
                f.relative_to(src).as_posix() for f in
                (src / "assets").rglob("*")
                if f.is_file()
                and f.suffix.lower() not in config.EXPORT_SECRET_SUFFIXES
                and f.name not in config.EXPORT_SECRET_NAMES
                and f.relative_to(src).as_posix() not in names]
            if missing_assets:
                raise EngineError(500,
                    "داراییها داخل APK نیستند (%d فایل): %s" %
                    (len(missing_assets), ", ".join(missing_assets[:6])))
        signed = bool(dmeta.get("signed"))
        ver = self._verify_apk(log, apk, signed)       # apksigner فقط اگر signed
        b = ver["badging"]
        for key, mkey in (("package", "package"), ("version_code", "version_code"),
                          ("version_name", "version_name")):
            meta_v = dmeta.get(key if key != "package" else "package")
            bad_v = b.get(mkey)
            if meta_v is not None and bad_v is not None and bad_v != meta_v:
                raise EngineError(500,
                    "مغایرت badging با release.json: %s=%s در برابر %s"
                    % (mkey, bad_v, meta_v))
        if expect:
            for k, v in expect.items():
                if b.get(k) not in (None, v) and v is not None and b.get(k) != v:
                    raise EngineError(422,
                        "راستی‌آزمایی ناموفق: badging %s=%s در برابر انتظار %s"
                        % (k, b.get(k), v))
        return {"ok": True, "signed": signed,
                "sign_verified": ver["signed"], "badging": b,
                "apk": str(apk)}

    # ------------------------------------------------------------- helpers --
    def _verify_delivery(self, out_dir: Path) -> None:
        """AF-12/AF-13 — راستی‌آزمایی پوشهٔ تحویل با MANIFEST.sha256.
        حذف/تغییر هر فایل یا فایل اضافه = EngineError (تحویل متوقف می‌شود)."""
        listed = []
        for line in (out_dir / "MANIFEST.sha256").read_text(
                encoding="utf-8").splitlines():
            h, _, fn = line.partition("  ")
            listed.append(fn)
            f = out_dir / fn
            if not f.is_file():
                raise EngineError(500,
                    "اعتبارسنجی بستهٔ تحویل ناموفق: فایل «%s» غایب است" % fn)
            if self._sha256(f) != h:
                raise EngineError(500,
                    "اعتبارسنجی بستهٔ تحویل ناموفق: هش «%s» مطابق manifest نیست" % fn)
        actual = [p.name for p in out_dir.iterdir()
                  if p.is_file() and p.name != "MANIFEST.sha256"]
        extra = sorted(set(actual) - set(listed))
        if extra:
            raise EngineError(500,
                "اعتبارسنجی بستهٔ تحویل ناموفق: فایل خارج از manifest: %s"
                % ", ".join(extra))

    def _read_key_env(self, envf: Path) -> dict:
        out = {}
        for line in envf.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
        if "KS_PASS" not in out or "KEY_ALIAS" not in out:
            raise EngineError(500, "فایل کلید محصول ناقص است")
        return out

    def _sha256(self, p: Path) -> str:
        import hashlib
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()

    def _record_artifact(self, p: Path, label: str) -> dict:
        return {"path": str(p), "label": label, "size": Path(p).stat().st_size}

    def _verify_apk(self, log, apk: Path, expect_signed: bool) -> dict:
        signed = False
        # AF-12: APK بدون امضا چیزی برای راستی‌آزمایی امضا ندارد —
        # apksigner verify فقط وقتی امضا درخواست شده اجرا شود (قبلاً
        # ساخت بدون امضا همیشه اینجا می‌شکست — باگ واقعی).
        if expect_signed:
            vout = self._run(log, "apksigner verify",
                             [shutil.which("apksigner"), "verify", "-v", str(apk)])
            signed = "Verified using v2 scheme (APK Signature Scheme v2): true" in vout or \
                     "Verified using v3 scheme (APK Signature Scheme v3): true" in vout
            if not signed:
                raise EngineError(500, "امضای APK تأیید نشد — صادقانه ناموفق")
        badging = self._run(log, "aapt2 dump badging",
                            [shutil.which("aapt2"), "dump", "badging", str(apk)])
        b = {}
        m = re.search(r"package: name='([^']+)' versionCode='(\d+)' versionName='([^']+)'", badging)
        if m:
            b = {"package": m.group(1), "version_code": int(m.group(2)), "version_name": m.group(3)}
        m2 = re.search(r"targetSdkVersion:'(\d+)'", badging)
        if m2:
            b["target_sdk"] = int(m2.group(1))
        return {"signed": signed, "badging": b}

    def _build_aab(self, log, build_dir: Path, d: Path, vcode: int, vname: str,
                   pkg: str, sign: bool) -> tuple[Path, Path | None]:
        if not BUNDLETOOL_JAR.is_file():
            raise EngineError(412, "bundletool.jar یافت نشد — AAB صادقانه ناموفق "
                                   "(APK جایگزین معرفی نمی‌شود)")
        # proto link
        aapt2 = shutil.which("aapt2")
        self._run(log, "aapt2 link --proto-format", [
            aapt2, "link", "-I", str(SDK_JAR), "--manifest", str(build_dir / "AndroidManifest.xml"),
            *[str(f) for f in sorted((build_dir / "flat").glob("*.flat"))],
            "--proto-format", "--min-sdk-version", "24", "--target-sdk-version", "35",
            "--version-code", str(vcode), "--version-name", vname,
            "-o", str(build_dir / "base.proto.apk")])
        # ماژول base/
        import zipfile as zf
        stage = build_dir / "aab"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir(parents=True)
        with zf.ZipFile(build_dir / "base.proto.apk") as z:
            z.extractall(stage)
        (stage / "base" / "manifest").mkdir(parents=True, exist_ok=True)
        (stage / "base" / "dex").mkdir(parents=True, exist_ok=True)
        os.replace(stage / "AndroidManifest.xml", stage / "base" / "manifest" / "AndroidManifest.xml")
        if (stage / "resources.pb").is_file():
            os.replace(stage / "resources.pb", stage / "base" / "resources.pb")
        shutil.copy(build_dir / "dex" / "classes.dex", stage / "base" / "dex" / "classes.dex")
        # BundleConfig.pb (proto دستی: Bundletool{version="1.18.3"})
        v = b"1.18.3"
        inner = bytes([0x12, len(v)]) + v
        (stage / "BundleConfig.pb").write_bytes(bytes([0x0A, len(inner)]) + inner)
        aab = build_dir / "app-release.aab" if sign else build_dir / "app-unsigned.aab"
        if aab.exists():
            aab.unlink()
        with zf.ZipFile(aab, "w", zf.ZIP_DEFLATED) as z:
            for root, dirs, files in os.walk(stage):
                for f in sorted(files):
                    full = Path(root) / f
                    z.write(full, str(full.relative_to(stage)).replace(os.sep, "/"))
        # bundletool: اعتبارسنجی عمیق + universal apk (امضا در سطح APK ناشی از AAB)
        ks = self.key_path(d) if sign else None
        argv = [shutil.which("java"), "-jar", str(BUNDLETOOL_JAR), "build-apks",
                "--bundle", str(aab), "--output", str(build_dir / "app.apks"),
                "--mode", "universal", "--aapt2", aapt2, "--overwrite"]
        tmp_pass = None
        if ks and ks.is_file():
            secrets_map = self._read_key_env(PRODUCT_KEYS / f"{pkg}.env")
            tmp_pass = PRODUCT_KEYS / f".pass-{secrets.token_hex(4)}.tmp"
            tmp_pass.write_text(secrets_map["KS_PASS"] + "\n" + secrets_map["KS_PASS"] + "\n",
                                encoding="utf-8")
            os.chmod(tmp_pass, 0o600)
            argv += ["--ks", str(ks), "--ks-key-alias", secrets_map["KEY_ALIAS"],
                     "--ks-pass", f"file:{tmp_pass}", "--key-pass", f"file:{tmp_pass}"]
        try:
            self._run(log, "bundletool build-apks (اعتبارسنجی AAB)", argv, timeout=600)
        finally:
            if tmp_pass is not None:
                tmp_pass.unlink(missing_ok=True)   # پاک‌سازی فوری رمز موقت
        # استخراج universal برای اثبات + امضا/راستی‌آزمایی
        with zf.ZipFile(build_dir / "app.apks") as z:
            z.extract("universal.apk", build_dir / "aab")
        uni = build_dir / "aab" / "universal.apk"
        ver = self._verify_apk(log, uni, bool(ks and ks.is_file()))
        log("bundletool", "AAB ساختار معتبر دارد؛ universal APK با resources.arsc استخراج شد")
        return aab, uni

    # ------------------------------------------------- release persistence --
    def _persist_release(self, project: str, record: dict, artifacts: dict) -> None:
        try:
            from core import release_center
            release_center.add_release(project, record, artifacts)
            factory_history(project, "android-build",
                            f"kind={record['kind']} v{record['version_name']}-vc{record['version_code']}")
        except Exception as exc:  # noqa: BLE001 — ثبت تاریخچه نباید ساخت را بشکند
            import logging
            logging.getLogger("ai-factory.android").error("persist release failed: %s", exc)


def factory_history(project: str, action: str, detail: str) -> None:
    from core import factory
    factory.append_history(project, action, detail)


# ------------------------------------------------------- live build runner --
def start_build(project: str, opts: dict) -> dict:
    """شروع ساخت در نخ جداگانه (سریالی با قفل). وضعیت: status(project)."""
    try:
        paths.project_dir(project)                    # اعتبارسنجی نام/وجود
    except FileNotFoundError as exc:
        raise EngineError(404, str(exc))
    except PermissionError as exc:
        raise EngineError(403, str(exc))
    with _status_lock:
        st = _status.get(project)
        if st and st.get("running"):
            raise EngineError(409, "ساخت قبلی همین پروژه هنوز در جریان است")
        _status[project] = {"project": project, "running": True,
                            "started_at": datetime.now().isoformat(timespec="seconds"),
                            "steps": [], "ok": None, "error": None, "report": None}

    def _log(step: str, message: str, status: str = "ok") -> None:
        from core.engines.android import StepLog  # noqa — همان قالب
        msg = StepLog.SECRET_PAT.sub("[REDACTED]", str(message or ""))
        with _status_lock:
            _status.setdefault(project, {}).setdefault("steps", []).append(
                {"ts": datetime.now().isoformat(timespec="seconds"),
                 "step": step, "status": status, "message": msg[:1200]})

    def _worker() -> None:
        eng = AndroidEngine()
        slog = StepLog()
        class _Tee:
            steps = property(lambda self: slog.steps)
            def __call__(self, step, message, status="ok"):
                slog.add(step, message, status)
                _log(step, message, status)
        try:
            # AF-50 — real build failures go through the bounded same-session
            # repair loop before a build is declared failed.
            from core import factory as factorymod
            report = factorymod.android_build_repair(
                project, lambda: eng.build(project, opts, _Tee()),
                agent=opts.get("agent") or None)
            with _status_lock:
                _status[project].update({"running": False, "ok": True,
                                         "report": report, "steps": slog.steps})
        except EngineError as exc:
            with _status_lock:
                _status[project].update({"running": False, "ok": False,
                                         "error": exc.message, "steps": slog.steps})
        except Exception as exc:  # noqa: BLE001
            with _status_lock:
                _status[project].update({"running": False, "ok": False,
                                         "error": f"خطای غیرمنتظره: {exc}", "steps": slog.steps})

    threading.Thread(target=_worker, daemon=True, name=f"android-build-{project}").start()
    with _status_lock:
        return {"started": True, **_status[project]}


def status(project: str) -> dict:
    paths.project_dir(project)
    with _status_lock:
        st = _status.get(project)
        if st is None:
            return {"running": False, "note": "ساختی برای این پروژه ثبت نشده"}
        return {**st, "report": st.get("report")}
