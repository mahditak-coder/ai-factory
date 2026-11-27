# Acceptance Criteria

> خلاصه فارسی: PASS فقط با شاهد واقعی. هر نتیجه PASS / FAIL / NOT TESTED است و هیچ موفقیت ساختگی پذیرفته نمیشود.

## Global rules
1. A phase is accepted only by real implementation, real tests and a separate commit.
2. Generated files, written reports and model text alone are never acceptance evidence.
3. Producer, critic and quality decisions must be distinct for high-impact work.
4. Artifacts must be versioned, hashed and traceable to producer and model/tool call.
5. SecurityPolicy remains final authority; no bypass for screenshots, spend or destructive actions.
6. User approval is bound to exact request hash and one-shot token for sensitive work.

## Status definitions
- **PASS**: command/test/gate actually ran; evidence path and exact result recorded; acceptance criteria met.
- **FAIL**: ran and did not meet criteria; failure retained, not hidden or auto-fixed outside phase.
- **NOT TESTED**: not run because credential, service, platform or policy unavailable; reason recorded.
- **BLOCKED**: a policy or dependency prevents execution; exact blocker recorded.

## Evidence rules
Every claim must include: command or API call, timestamp, output/log path, artifact hash where relevant and evaluator identity. Tool `ok=True` is not product PASS. A report file is not proof its gate passed.

## Quality and security gates
- Critical finding always blocks release.
- High finding requires explicit recorded user decision to override.
- HOLD or BLOCK RELEASE cannot be published as PASS.
- Security report generation is not a security decision.
- Visual/audio professional quality requires real inspection or is NOT VERIFIED.

## No false PASS policy
Never mark PASS for: unexecuted tests, mocked-only provider calls presented as live, placeholder assets, truncated handoff assumptions, stale accepted runs after later failure, or unreviewed specialist output.
