"""AF NEXT Phase 4 — EngineAdapter contract for six product engines.

The Core drives product engines through ONE neutral interface. Android wraps
the real engine; Web/SaaS, Backend/API, AI/Agent and 2D Game wrap the proven
factory execution/testing path. Desktop is contract-only and honestly BLOCKED
in Termux. No undeclared dependency is assumed.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import core.config as config
from core import opstatus


@dataclass
class EngineResult:
    """Evidence-only result. Never invent a success."""
    status: str
    ok: bool = False
    artifacts: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    error: str = ""

    @classmethod
    def fail(cls, reason: str, status: str = opstatus.FAIL) -> "EngineResult":
        return cls(status=status, ok=False, error=str(reason)[:400])


@runtime_checkable
class EngineAdapter(Protocol):
    type_id: str

    def supported(self, project: str) -> dict: ...
    def validate(self, project: str) -> dict: ...
    def build(self, project: str, opts: dict | None = None) -> EngineResult: ...
    def verify(self, project: str, out_dir: str | Path) -> EngineResult: ...
    def package(self, project: str, out_dir: str | Path) -> EngineResult: ...


class AndroidEngineAdapter:
    """REAL adapter over ``core.engines.android`` (minimal APK/AAB toolchain)."""

    type_id = "android"

    def __init__(self):
        from core.engines import android as andmod
        self._eng = andmod.AndroidEngine()
        self._mod = andmod

    def profile(self):
        from core import engine_catalog
        return engine_catalog.get("android")

    def supported(self, project: str) -> dict:
        return self._eng.detect(self._mod.paths.project_dir(project))

    def validate(self, project: str) -> dict:
        return self._eng.validate(self._mod.paths.project_dir(project))

    def build(self, project: str, opts: dict | None = None) -> EngineResult:
        opts = dict(opts or {"kind": "apk", "sign": True})
        try:
            self._mod.start_build(project, opts)
        except Exception as exc:  # noqa: BLE001
            return EngineResult.fail(exc, opstatus.BLOCKED)
        deadline = time.time() + 420
        while time.time() < deadline:
            st = self._mod.status(project)
            if st.get("ok") is not None:
                rep = st.get("report") or {}
                if st.get("ok"):
                    return EngineResult(
                        status=opstatus.PASS, ok=True,
                        artifacts=rep.get("artifacts") or {},
                        evidence={"steps": st.get("steps") or [],
                                  "package": rep.get("package"),
                                  "output_dir": rep.get("output_dir")})
                return EngineResult.fail(st.get("error") or "build failed",
                                         opstatus.FAIL)
            time.sleep(2)
        return EngineResult.fail("android build timeout", opstatus.BLOCKED)

    def verify(self, project: str, out_dir: str | Path) -> EngineResult:
        try:
            rep = self._eng.verify_artifacts(Path(out_dir),
                                             self._mod.StepLog().add)
            return EngineResult(status=opstatus.PASS, ok=True,
                                evidence=rep)
        except Exception as exc:  # noqa: BLE001
            return EngineResult.fail(exc, opstatus.FAIL)

    def package(self, project: str, out_dir: str | Path) -> EngineResult:
        d = Path(out_dir)
        files = {p.name: {"size": p.stat().st_size}
                 for p in d.iterdir() if p.is_file()} if d.is_dir() else {}
        if not files:
            return EngineResult.fail("delivery dir empty", opstatus.NOT_TESTED)
        return EngineResult(status=opstatus.PASS, ok=True,
                            artifacts=files, evidence={"dir": str(d)})


class FactoryEngineAdapter:
    """REAL adapter over the proven factory build/test path (no new engine)."""

    def __init__(self, engine_id: str):
        self.type_id = engine_id

    def profile(self):
        from core import engine_catalog
        return engine_catalog.get(self.type_id)

    def supported(self, project: str) -> dict:
        from core import paths
        p = engine_discovery_status(self.type_id)
        return {"supported": p == "AVAILABLE", "status": p,
                "project": str(paths.project_dir(project)),
                "engine": self.type_id}

    def validate(self, project: str) -> dict:
        from core import checker, paths
        return checker.check(paths.project_dir(project))

    def build(self, project: str, opts: dict | None = None) -> EngineResult:
        from core import factory
        try:
            rep = factory.testrun_combined(project)
            if rep.get("ran") and rep.get("ok"):
                return EngineResult(status=opstatus.PASS, ok=True,
                    artifacts={"testrun": rep},
                    evidence={"method": "factory.testrun_combined",
                              "own": bool((rep.get("own_tests") or {}).get("ran")),
                              "contract": bool((rep.get("contract") or {}).get("ran"))})
            return EngineResult(status=opstatus.FAIL,
                error=str(rep.get("note") or "test gate red"),
                artifacts={"testrun": rep})
        except Exception as exc:  # noqa: BLE001
            return EngineResult.fail(exc, opstatus.BLOCKED)

    def verify(self, project: str, out_dir: str | Path) -> EngineResult:
        return self.build(project)

    def package(self, project: str, out_dir: str | Path) -> EngineResult:
        from core import exporter, security_gate
        g = security_gate.scan(project, mode="export")
        if g.get("blocked"):
            return EngineResult.fail("security gate blocked: %s" %
                                     [f.get("kind") for f in g.get("critical", [])[:3]],
                                     opstatus.BLOCKED)
        res = exporter.export(project)
        if res.get("integrity_bad_file"):
            return EngineResult.fail("ZIP integrity failed", opstatus.FAIL)
        return EngineResult(status=opstatus.PASS, ok=True,
                            artifacts={"zip": res.get("path")}, evidence=res)


class DesktopEngineAdapter(FactoryEngineAdapter):
    """Contract-only: real native desktop packaging is BLOCKED in Termux."""

    def __init__(self):
        super().__init__("desktop")

    def supported(self, project: str) -> dict:
        return {"supported": False, "mode": "blocked",
                "reason": "native desktop packaging unavailable on aarch64 Termux"}

    def build(self, project: str, opts: dict | None = None) -> EngineResult:
        return EngineResult.fail(
            "desktop native build BLOCKED: no electron/tauri/flutter/swift toolchain",
            opstatus.BLOCKED)

    def package(self, project: str, out_dir: str | Path) -> EngineResult:
        # Never manufacture a "deliverable" for an engine that cannot build.
        return EngineResult.fail(
            "desktop native package BLOCKED: no build artifact exists",
            opstatus.BLOCKED)


def engine_discovery_status(engine_id: str = "android") -> str:
    """Real capability check from discovery — never assume a tool exists."""
    from core import engine_catalog, engine_discovery
    profile = engine_catalog.get(engine_id)
    if profile is None:
        return engine_discovery.NOT_VERIFIED
    if profile.status == engine_discovery.BLOCKED:
        return engine_discovery.BLOCKED
    d = engine_discovery.report()
    for tool in profile.tools:
        info = d["tools"].get(tool)
        if info and info["status"] != engine_discovery.AVAILABLE:
            return engine_discovery.BLOCKED
    return engine_discovery.AVAILABLE


_adapters: dict[str, Any] = {}


def adapter_for(type_id: str):
    """One adapter per declared engine; blocked engines report BLOCKED."""
    if type_id not in _adapters:
        if type_id == "android":
            _adapters[type_id] = AndroidEngineAdapter()
        elif type_id == "desktop":
            _adapters[type_id] = DesktopEngineAdapter()
        elif type_id in ("web_saas", "backend_api", "ai_agent", "game"):
            _adapters[type_id] = FactoryEngineAdapter(type_id)
        else:
            return None
    return _adapters[type_id]
