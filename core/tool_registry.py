"""AF NEXT Phase 2 — independent Tool Registry.

One central, OpenCode-free definition of what tools exist, what they need and
how their result is validated.  Existing tools are registered as ADAPTERS over
the already proven modules — their behavior is not rewritten.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

SCOPES = ("project:read", "project:write", "shell:execute", "tests:run",
          "security:read", "release:export", "build:android", "media:write",
          "image:generate", "image:write")


@dataclass
class ToolSpec:
    id: str
    name: str
    description: str
    kind: str
    capability: str
    scope: str
    inputs: dict[str, str] = field(default_factory=dict)
    output: str = "dict"
    timeout_s: float = 120.0
    enabled: bool = True
    requires_confirm: bool = False
    source: str = "factory"
    adapter: Callable | None = None
    validator: Callable | None = None

    def validate_inputs(self, inputs: dict) -> str | None:
        """Honest, minimal input validation (no silent coercion)."""
        if not isinstance(inputs, dict):
            return "ورودی ابزار باید شیء JSON باشد"
        for key, kind in self.inputs.items():
            if key not in inputs or inputs[key] in (None, ""):
                return "پارامتر اجباریmissing: " + key
            value = inputs[key]
            if kind == "str" and not isinstance(value, str):
                return "نوع نامعتبر: " + key
            if kind == "int" and not isinstance(value, int):
                return "نوع نامعتبر: " + key
            if kind == "dict" and not isinstance(value, dict):
                return "نوع نامعتبر: " + key
        if self.validator:
            try:
                problem = self.validator(inputs)
            except Exception as exc:  # noqa: BLE001
                return "اعتبارسنجی ابزار ناموفق: %s" % exc
            if problem:
                return str(problem)[:300]
        return None


class ToolRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._tools: dict[str, ToolSpec] = {}
        self._builtins = False

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.scope not in SCOPES:
            raise ValueError("scope نامعتبر: " + spec.scope)
        with self._lock:
            self._tools[spec.id] = spec
        return spec

    def get(self, tool_id: str) -> ToolSpec | None:
        self.ensure_builtins()
        with self._lock:
            return self._tools.get(tool_id)

    def list(self) -> list[ToolSpec]:
        self.ensure_builtins()
        with self._lock:
            return list(self._tools.values())

    def for_agent(self, scopes: set[str] | list[str],
                  task_kind: str = "") -> list[ToolSpec]:
        """Only tools whose scope is in the CURRENT authorization set."""
        allow = set(scopes or [])
        out = []
        for t in self.list():
            if not t.enabled or t.scope not in allow:
                continue
            out.append(t)
        return out

    def catalog_prompt(self, scopes: set[str] | list[str],
                       limit: int = 10) -> str:
        items = self.for_agent(scopes)[:limit]
        if not items:
            return "ابزار مجاز کارخانه: هیچ ابزاری در سطح دسترسی فعلی نیست."
        return ("ابزارهای مجاز کارخانه (برای اجرا از Tool Execution Layer استفاده کن): " +
                ", ".join("%s:%s" % (t.id, t.scope) for t in items))

    def ensure_builtins(self) -> None:
        if self._builtins:
            return
        with self._lock:
            if self._builtins:
                return
            self._builtins = True
        self._register_builtins()

    def _register_builtins(self) -> None:
        def read_file(project, inputs, timeout_s=None, cancel_event=None):
            from core import paths
            rel = inputs["path"]
            p = paths.project_path(project, rel)
            data = p.read_bytes()
            return {"path": rel, "size": len(data),
                    "text": data.decode("utf-8", "replace")[:8000]}

        def write_file(project, inputs, timeout_s=None, cancel_event=None):
            import hashlib
            from core import paths
            rel = inputs["path"]
            p = paths.project_path(project, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            blob = str(inputs["content"]).encode("utf-8")
            p.write_bytes(blob)
            return {"path": rel, "size": len(blob),
                    "sha256": hashlib.sha256(blob).hexdigest()}

        def safe_shell(project, inputs, timeout_s=None, cancel_event=None):
            import shlex, subprocess
            from core import boundary, paths
            cmd = str(inputs["command"])
            root = paths.project_dir(project).resolve()
            if cancel_event and cancel_event.is_set():
                return {"cancelled": True}
            proc = subprocess.run(shlex.split(cmd), cwd=str(root), shell=False,
                                  capture_output=True, text=True,
                                  timeout=float(timeout_s or 60),
                                  env=boundary.clean_env())
            return {"exit": proc.returncode, "stdout": proc.stdout[-6000:],
                    "stderr": proc.stderr[-3000:]}

        def run_tests(project, inputs, timeout_s=None, cancel_event=None):
            from core import factory
            return factory.testrun_combined(project)

        def security_scan(project, inputs, timeout_s=None, cancel_event=None):
            from core import security_gate
            return security_gate.scan(project, mode=str(inputs.get("mode") or "export"))

        def package_export(project, inputs, timeout_s=None, cancel_event=None):
            from core import exporter
            return exporter.export(project)

        def android_build(project, inputs, timeout_s=None, cancel_event=None):
            from core import engine_adapter
            a = engine_adapter.adapter_for("android")
            r = a.build(project, inputs.get("opts") or {"kind": "apk", "sign": True})
            return {"ok": r.ok, "status": r.status, "artifacts": r.artifacts,
                    "error": r.error, "evidence": r.evidence}

        def generate_wav(project, inputs, timeout_s=None, cancel_event=None):
            """REAL PCM WAV synthesis with the standard library (no mock)."""
            import hashlib, math, struct, wave
            from core import paths
            rel = str(inputs["path"])
            p = paths.project_path(project, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            freq = max(80.0, min(2400.0, float(inputs.get("freq_hz") or 440)))
            dur = max(0.1, min(20.0, float(inputs.get("duration_ms") or 1200) / 1000.0))
            vol = max(0.01, min(0.5, float(inputs.get("volume") or 0.15)))
            rate = 22050
            pcm = bytearray()
            for i in range(int(rate * dur)):
                pcm += struct.pack("<h", int(32767 * vol *
                                             math.sin(2 * math.pi * freq * i / rate)))
            with wave.open(str(p), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(bytes(pcm))
            return {"path": rel, "size": p.stat().st_size,
                    "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                    "freq_hz": freq, "duration_ms": int(dur * 1000),
                    "quality_note": "synthesised PCM tone; musical quality NOT VERIFIED"}

        def image_generate(project, inputs, timeout_s=None, cancel_event=None):
            from core import image_providers as ip
            provider = ip.get(str(inputs.get("provider") or "")) or ip.active()
            if provider is None:
                return ip.ImageResult.fail(
                    "no-image-provider-selected", ip.BLOCKED).to_dict()
            req = dict(inputs)
            req["project"] = project
            req["operation"] = str(inputs.get("operation") or "generate")
            if req["operation"] == "edit" and hasattr(provider, "edit"):
                return provider.edit(req).to_dict()
            # Pass prompt exactly once: it must not also remain in **kwargs.
            gen_kwargs = dict(req)
            prompt = str(gen_kwargs.pop("prompt", "") or "")
            return provider.generate(prompt, **gen_kwargs).to_dict()

        def image_request_approval(project, inputs, timeout_s=None,
                                   cancel_event=None):
            from core import image_governance as gov
            payload = gov.payload_from(project, inputs)
            return gov.request_approval(project, payload,
                                        str(inputs.get("actor") or ""))

        def image_decide(project, inputs, timeout_s=None, cancel_event=None):
            from core import image_governance as gov
            return gov.decide(str(inputs.get("approval_id") or ""),
                              str(inputs.get("decision") or ""),
                              str(inputs.get("actor") or ""))

        def image_process(project, inputs, timeout_s=None, cancel_event=None):
            from core import image_providers as ip
            return ip.process(project, inputs).to_dict()

        for spec in (
            ToolSpec("image.request_approval", "درخواست تأیید هزینه",
                     "ایجاد تأیید یکبارهمجهربه هزینه تخمینی",
                     "image", "image-governance", "image:generate",
                     {"provider": "str", "model": "str", "prompt": "str"},
                     timeout_s=30, adapter=image_request_approval),
            ToolSpec("image.decide", "تصمیم کاربر", "تأیید یا رد صریح درخواست پولی",
                     "image", "image-governance", "image:generate",
                     {"approval_id": "str", "decision": "str"},
                     timeout_s=30, adapter=image_decide),
            ToolSpec("image.generate", "تولید تصویر", "تولیدrealاز Provider مستقل تصویر",
                     "image", "image-provider", "image:generate",
                     {"prompt": "str", "provider": "str", "model": "str",
                      "approval_id": "str", "confirmed_cost_usd": "str"},
                     timeout_s=180, adapter=image_generate),
            ToolSpec("image.process", "پردازش تصویر", "PNG شفاف/اندازه/ Sprite Sheet",
                     "image", "pillow", "image:write",
                     {"op": "str", "path": "str"}, timeout_s=120,
                     adapter=image_process),
            ToolSpec("media.generate_wav", "تولید صوت real", "ساخت فایل WAVrealدر پروژه",
                     "media", "stdlib-wave", "media:write",
                     {"path": "str", "freq_hz": "number", "duration_ms": "number"},
                     timeout_s=60, adapter=generate_wav),
            ToolSpec("project.inspect", "بررسی پروژه", "خلاصهٔ واقعی ساختار و مسیرها",
                     "read", "project-snapshot", "project:read",
                     timeout_s=30, adapter=lambda p, i, **k: __import__(
                         "core.project_snapshot", fromlist=["path_map"]).path_map(p)),
            ToolSpec("project.read_file", "خواندن فایل", "خواندن فایل داخل ریشهٔ پروژه",
                     "read", "filesystem", "project:read", {"path": "str"},
                     timeout_s=20, adapter=read_file),
            ToolSpec("project.write_file", "نوشتن فایل", "ایجاد/ویرایش فایل درプロژه",
                     "write", "filesystem", "project:write",
                     {"path": "str", "content": "str"}, timeout_s=60,
                     adapter=write_file),
            ToolSpec("shell.safe_run", "دستور امن", "دستورread-only/test مجاز در پروژه",
                     "shell", "headless-policy", "shell:execute",
                     {"command": "str"}, timeout_s=120, adapter=safe_shell),
            ToolSpec("tests.run", "اجرای تست", "اجرایreal تستهای پروژه",
                     "test", "runner+typetests", "tests:run",
                     timeout_s=300, adapter=run_tests),
            ToolSpec("security.scan", "اسکن امنیت", "اسکن محتوای قابل انتشار",
                     "security", "security-gate", "security:read",
                     timeout_s=120, adapter=security_scan),
            ToolSpec("package.export", "ساخت ZIP", "بستهٔ قابل تحویل",
                     "release", "exporter", "release:export",
                     timeout_s=120, requires_confirm=True, adapter=package_export),
            ToolSpec("android.build", "ساخت APK", "ساخت/امضایreal اندروید",
                     "build", "android-engine", "build:android",
                     timeout_s=420, adapter=android_build),
        ):
            self.register(spec)


_registry = ToolRegistry()


def registry() -> ToolRegistry:
    return _registry
