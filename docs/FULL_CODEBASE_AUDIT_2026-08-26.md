# Full codebase audit 2026-08-26

Base: `origin/main` `8856e23` (PR #25 merged). Branch: `fix/full-codebase-audit-20260826`.

PR #20–#25 MERGED. PR #26 OPEN (folder catalog; not used as implementation base).

## Priority defects (reproduced, then fixed)

| ID | Severity | Status | Evidence |
|---|---|---|---|
| InsForge POST timeout retry | P0 | BROKEN → fixed | `_request` retried POST on timeout/`502` |
| 4xx trips circuit breaker | P0 | BROKEN → fixed | loop always `record_failure()` including 404 |
| Folder import `project_id` | P1 | PARTIAL → fixed | `/visual-assets/import` had no Form + no ACL check |
| Same-path SHA replacement | P1 | BROKEN → fixed | inventory/catalog keyed by `root:relative` only |
| `security.audit` vs `append_audit` | P1 | BROKEN → fixed | kwargs `role`/`success`/`resource_type` did not match store |
| GitHub Actions CI | P1 | MISSING → script added; workflow install blocked | token lacks `workflow` scope so `.github/workflows/ci.yml` cannot be pushed; copy is `docs/github-actions-ci.yml` + `scripts/ci.sh` |

## Classification snapshot

WORKING: FastAPI chat/SSE, SessionStore, Artifact download ACL, BM25/LSA/RRF knowledge, visual upload/search/review, InsForge opt-in adapters, CSRF origin, rate limit.

PARTIAL: visual analysis jobs (in-process BackgroundTasks, no worker), external media (path reference only).

BLOCKED_BY_EXTERNAL_DEPENDENCY: Windows media trees, fal Vision without `FAL_KEY`, video decoder, Playwright Chromium in CI, InsForge MCP in this Grok TUI.

MISSING (not added): Redis/Celery worker. Adding one would be a rewrite.

MOCKED: tests use FakeLLM / fake fal; production paths are real.

## Not redone

Chat, knowledge RAG, Artifact, Session, existing visual_* tables, InsForge SQL 001–003.
