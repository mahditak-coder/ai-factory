# Public audit copy — AI Factory

This repository is a sanitized, independent copy of the working tree of AI Factory for public source audit. It is NOT the original working repository and contains NO git history from the original repository.

## Source state at copy time
- Original working repository: `~/AI-Factory` (unchanged).
- Branch: `main`; HEAD: `3878db271313415656328ec57595d1fe87c5fa3a`.
- Working tree had 151 uncommitted items (34 modified, 117 untracked). This copy captures the current source files as plain files, not as a commit history.
- No commit, push, reset, stash or clean was performed in the original repository.

## Included
- `core/` (source, agents/tools/engines/tests/templates), `dashboard/`, `scripts/`, `config/`, `agents/`, `providers/`.
- Public architecture docs under `docs/*.md` and the strategic assessment `docs/strategy/FACTORY-CAPABILITY-ASSESSMENT.md`.
- Root `.gitignore`; `README.md` copied from `docs/README.md`.

## Excluded (not published)
- `.git/` original history/remotes.
- `data/`, `logs/`, `projects/`, `exports/`, `backups/`, `releases/`, `.factory.meta_rec`, `WORKDIR`, `p10-ref.json`, `docs/test-reports/`.
- Reason: runtime data, sessions, private project files, logs, generated artifacts, caches and large outputs.

## Secret scan
Automated scan of all included files found no real credentials. Matches for secret-like patterns exist ONLY in test fixtures and are synthetic, e.g. `ghp_0123456789abcdefghijklmnopq`, `AKIAIOSFODNN7EXAMPLE` (documented fake), `sk-ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh`, and a one-line fake PEM fixture in a security test. The Stripe-shaped synthetic fixture was replaced in this public copy with a provider-neutral Slack-shaped fake token (test expectation updated from `secret-stripe-live` to `secret-slack-token`) because GitHub push protection blocks provider-specific signatures. They are retained because they are the test inputs for secret-redaction checks; removing them would destroy the audit value of those tests. No `.env`, private key, database, token file, session file or password file is included.
