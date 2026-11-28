# Phase 06 — Professional Production Engine (evidence)

Branch `rebuild/phase-06-production-engine`, created from the EXACT accepted
Phase 05 commit `8209449e6d32a5e6368faae8365e07114b915e45` (parent
`5cf0fcdcb368084690ac0b404c634a9caeaf8ad1`). Accepted Phase 04 base
`1d53571025812e3cff725c508de62a894ffd37e4` verified as ancestor. All Git
checks were done directly against GitHub and in an isolated clone;
`~/AI-Factory` was never modified.

## Entry reconciliation (before any engine code)

- `CURRENT-STATE.md`: added SUPERSEDES records for the independent Phase 05
  acceptance and explicit Phase 06 authorization. Earlier `NOT ACCEPTED` /
  `NOT AUTHORIZED` lines were preserved, not deleted or rewritten.
- `DECISIONS.md`: ADR-024 and ADR-025 updated to ACCEPTED per the independent
  Phase 04/05 reviews. ADR-011/012/013 recorded as **user decisions** from
  this mission (history preserved). ADR-014 remains PROPOSED.

## User architecture decisions (recorded before irreversible work)

- ADR-011: **2.5D via local Chromium**; no 3D claim (Blender/Godot absent).
- ADR-012: **User-Controlled Hybrid** — local Pillow/FFmpeg; remote free AND
  paid models allowed; explicit provider+model selection every workflow; free
  runs without payment approval but never without selection; paid requires
  exact estimate + explicit approval per call; no automatic switching and no
  paid fallback; credentials only from environment; availability and pricing
  verified before execution.
- ADR-013: **local headless, read-only** screenshots; evidence-only output.

## What was really built (additive only)

- `core/production_assets.py` — flock-guarded atomic asset store with strict
  JSON/bool/schema validation; SHA-256 and real validation of PNG (existing
  helper) and audio (DETECTED `ffprobe`, `python-wave` fallback); unified
  provenance/tool/model/version/hash/cost/limits records; honest classification
  (`non-production-vector-placeholder`, `non-production-synthesised-tone`,
  `evidence-only`, `production-verified`); versioned, idempotent, REVERSIBLE
  migration that preserves originals and marks legacy assets non-production.
- `core/provider_policy.py` — ADR-012 gate: explicit provider+model required,
  provider availability + credential env-name + price verified, free path
  without payment approval, paid path requiring exact confirmed cost and an
  approval consumed before ONE call; no retry, no switching, no fallback.
- `core/visual_review.py` — ADR-013 local Chromium screenshot; sanitised
  environment, network targets refused, path containment, real PNG validation.
- `core/production_engine.py` — composes detection, policy, local synthesis,
  screenshots and real engine adapters; never claims a placeholder is
  production-ready and never calls a specialist or paid service.
- `core/tests/test_phase06_production_engine.py` — 15 deterministic tests.

## Real environment capabilities (detected, not assumed)

`aarch64`, no GPU. AVAILABLE: `ffmpeg 9.0.2`, `ffprobe`, `Pillow`, `Chromium
156`, `aapt2`, `d8`, `apksigner`, `keytool`, `gradle`, `java/javac`,
`bundletool`, `android.jar (API 35)`, `sqlite3`, `node`, `npm`. BLOCKED or NOT
VERIFIED: Blender, Godot, Unity, Electron, Tauri, Flutter, Swift/xcodebuild,
`inkscape`, `rsvg-convert`, `cairosvg`, `sox`. No tool was installed.

## What was really produced and validated

| Artefact | Real evidence |
|---|---|
| Local Chromium screenshot | 1280×720 PNG, 9058 bytes, sha256 `8c231f9a121870a555e3f5d9175458f4d6baf072dc41c5d6d9fafc541f90d6f0`, `classification=evidence-only`, `production_ready=False`, recorded in the asset store (`/data/data/com.termux/files/usr/tmp/opencode/p6-realshot2` run) |
| Real WAV via existing adapter | ffprobe: `format=wav`, `codec=pcm_s16le`, `sample_rate=22050`, `channels=1`; synthesised tone explicitly NON-PRODUCTION |
| Real Android build | `core.tests.test_af49_assets` — `Ran 1 test ... OK`, exit 0 (real APK build path, 5.466s), log `/data/data/com.termux/files/usr/tmp/opencode/p6-realbuild.log` |
| SVG placeholder | forcibly requested `production_ready=True` was DENIED; classified `non-production-vector-placeholder` |
| Migration | 3 legacy assets (svg/wav/png) sidecar-marked, original hashes unchanged, second run `changed=0`, rollback removed all sidecars and restored original hashes |
| Corrupt asset store | raises `AssetStoreError`, file not overwritten |

## Tests actually executed (isolated `AI_FACTORY_TEST_ROOT`)

| Command | Result | Counts | Exit | Evidence log |
|---|---|---|---|---|
| `python3 -m unittest core.tests.test_phase06_production_engine -v` | PASS | 15 PASS / 0 FAIL / 0 ERROR | 0 | `…/p6-t3.log` |
| Phase 04 regressions (`specialist_engine`, `audit_repair`, `final_security`, `security_integrity`) | PASS | 57 PASS / 0 FAIL / 0 ERROR | 0 | `…/p6-p04.log` |
| Phase 05 regression (`test_phase05_domain_teams`) | PASS | 71 PASS / 0 FAIL / 0 ERROR | 0 | `…/p6-p05.log` |
| Full suite `python3 -m unittest discover -s core/tests -p 'test_*.py'` | PASS | 617 PASS / 0 FAIL / 0 ERROR (58.930s) | 0 | `…/p6-full.log` |
| Real Android build `core.tests.test_af49_assets` | PASS | 1 PASS / 0 FAIL / 0 ERROR | 0 | `…/p6-realbuild.log` |
| Real Chromium screenshot | PASS | validated PNG, recorded | 0 | inline above |
| `python3 -m py_compile` (all changed Python files) | PASS | exit 0 | 0 | inline |
| `git diff --check` | PASS | no whitespace errors | 0 | inline |

Failures encountered and repaired **inside this mission** (no unexecuted test
claimed as PASS): initial mock-config gaps and shared test-project isolation
(test_03/05/14, test_10) and an empty journal entry on an idempotent re-run
(production code) — all fixed and the complete suites rerun green.

## PASS / FAIL / NOT TESTED

- **PASS**: real capability detection, provider-policy gate, PNG/WAV validation,
  provenance/hash/cost/limits records, non-production classification, versioned
  reversible migration, real screenshot, real Android build, all regressions,
  Phase 01–05 security contracts unchanged.
- **FAIL**: none in the final runs.
- **NOT TESTED**: CI/GitHub Actions; live paid provider calls (never invoked —
  only mocked contracts and the gate); live free OpenRouter generation; real
  interactive 2.5D game runtime; Blender/Godot/Unity 3D paths; payments;
  browser-automated functional QA beyond a static screenshot; Production Engine
  HTTP API integration; executable reproduction of the Android build on a
  different host.

No specialist, provider, media-generation, payment, shell or release authority
was added. ToolExecutor, SecurityPolicy, capability issuance, leases and all
Phase 01–05 contracts are unchanged. The final commit SHA cannot be stored in
its own file: it is the branch HEAD after push and is reported exactly in the
delivery message; the parent is `8209449e6d32a5e6368faae8365e07114b915e45`.

Phase 06 is **DONE** against the stated acceptance criteria. Phase 07 is NOT
started and requires explicit user authorization.

## FOCUSED REMEDIATION — ADR-012 dispatch / one-time approval / ADR-013 isolation

Continues from the remote Phase 06 HEAD `bc1bba91958c84ee04422254404a42390167f96a`, in an isolated clone. Only the three verified issues were touched; Phase 01–05 work was not reopened.

### A — exact selection reaches the adapter
- Defect: `provider_policy.dispatch()` never forwarded the selected `model` or `quality` to the provider adapter. OpenRouter hardcoded `OPENROUTER_MODEL` in both authorization and the outbound payload, and the request hash omitted `quality`, so the selected model did not reach the adapter unchanged.
- Fix: identity (`provider`, `model`, `quality`, `operation`, `prompt`, `size`, `references`, `region`) comes exclusively from the validated decision; the adapter uses the exact selected model, validates it against the provider's CURRENT config catalog (free or paid), sends that model in the single request, and verifies the returned model matches it. No `active()`/`select()` fallback is called.

### B — one-time paid approval
- Defect: `provider_policy` consumed the approval and the remote adapter consumed it again; the bound payload omitted prompt/size, so changed prompts could pass the gate.
- Fix: paid approval is consumed ONCE under a cross-process `fcntl.flock` in `provider_policy`, after checking expiry (15 min TTL), project/provider/model binding, exact cost and request hash. The adapter receives a single-use, binding-checked token issued by that gate and NEVER consumes governance again; direct adapter calls without a token keep the prior governance contract. Replay, concurrent reuse, changed prompt/cost/model and expired approvals fail closed.

### C — real Chromium network isolation
- Defect: only the initial URL was checked; subresources/redirects/WebSockets/service workers/navigation were not denied.
- Fix: `unshare -n` is probed at runtime. Only when the OS namespace is actually available is Chromium launched inside it, with resolver/proxy/background-network hardening. On this host the probe returns `Operation not permitted`; screenshots are therefore BLOCKED before any browser starts. No tool was installed and no security control was weakened.

### Evidence classification (MOCKED / REAL / PASS / FAIL / NOT TESTED)
- **MOCKED**: 13 focused tests use an injected `_http` transport and a fake browser; they prove model preservation, single call, no fallback, one-time consumption, replay/concurrent rejection, expiry, isolated command construction and no external target. No real provider request, no paid call, no credits spent.
- **REAL**: OS probe `unshare -n` → `Operation not permitted`; screenshot refused with `no network-exposed browser was started`. `ffprobe`/Pillow/Android toolchain detection unchanged.
- **PASS**: focused 13/13; Phase 06 suite 15/15; OpenRouter 9/9; image_remote 9/9; full suite 630/630 (58.673s); `py_compile` and `git diff --check` clean.
- **FAIL**: none in final runs.
- **NOT TESTED**: live OpenRouter generation (free or paid), real paid spend, real screenshot with OS isolation (blocked in this environment), CI.
- **SUPERSEDED historical evidence**: the earlier screenshot PNG sha256 `8c231f9a...` recorded in this file was produced BEFORE OS-level isolation was required; it is preserved as history and is NOT valid evidence under the current policy. No production claim rests on it.

Logs: `…/p6r-focused-final.log`, `…/p6r-p06-final.log`, `…/p6r-or-final.log`, `…/p6r-remote-final.log`, `…/p6r-full2.log`, `…/p6r-realblock.log`.
