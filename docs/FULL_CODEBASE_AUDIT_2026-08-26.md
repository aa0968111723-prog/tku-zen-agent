# Full codebase audit 2026-08-26

Base: `origin/main` `8856e23` (PR #25 merged). Branch: `fix/full-codebase-audit-20260826`.

PR #20–#25 MERGED. PR #26 OPEN (folder catalog; not used as implementation base). PR #27 OPEN (this branch).

## Priority defects (reproduced, then fixed)

| ID | Severity | Status | Evidence |
|---|---|---|---|
| InsForge POST timeout retry | P0 | BROKEN → fixed | GET/HEAD/OPTIONS only retry; POST timeout is one attempt |
| 4xx trips circuit breaker | P0 | BROKEN → fixed | 400–422/429 do not `record_failure`; 5xx/network do |
| GET 500 not retried | P1 | BROKEN → fixed | safe methods retry 500/502/503/504/429 |
| Folder import `project_id` | P1 | PARTIAL → fixed | Form + `get_project(user, id)`; empty stays 待整理 |
| Same-path SHA replacement | P1 | BROKEN → fixed | new SHA → new asset + `supersedes_asset_id` |
| `security.audit` vs `append_audit` | P1 | BROKEN → fixed | store signature; TypeError is not swallowed |
| Search missing project scope | P1 | PARTIAL → fixed | `/search?project_id=` is ACL-validated hard filter |
| Job vs review status mix | P1 | BROKEN → fixed | analysis failure no longer writes `review_status` |
| GitHub Actions CI | P1 | MISSING → added `.github/workflows/ci.yml` | Playwright skip is skipped, not PASS |
| Concurrent human patch | P1 | PARTIAL → fixed | `if_match_updated_at` rejects stale writes |

## Classification snapshot

WORKING: FastAPI chat/SSE, SessionStore, Artifact download ACL, BM25/LSA/RRF knowledge, visual upload/search/review, InsForge opt-in adapters, CSRF origin, rate limit, folder import project scope, same-path versioning.

PARTIAL: visual analysis jobs (in-process BackgroundTasks, no worker), external media (path reference only), mypy (not a project dependency).

BLOCKED_BY_EXTERNAL_DEPENDENCY: Windows media trees, fal Vision without `FAL_KEY`, video decoder, Playwright Chromium, InsForge MCP in this Grok TUI, mypy.

MISSING (not added): Redis/Celery worker. Adding one would be a rewrite.

MOCKED: tests use FakeLLM / fake fal; production paths are real.

## Tests

`python -m pytest tests --maxfail=20 -ra` → 779 passed, 1 skipped (`BLOCKED_BY_EXTERNAL_DEPENDENCY` Playwright).

## Not redone

Chat, knowledge RAG, Artifact, Session, existing visual_* tables, InsForge SQL 001–003.
