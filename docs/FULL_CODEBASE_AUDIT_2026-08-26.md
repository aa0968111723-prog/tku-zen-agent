# Full codebase audit 2026-08-26

Latest `origin/main`: `01dd311` (PR #27 merged the first audit commit `8e9d37b` only).
Branch: `fix/full-codebase-audit-20260826` (includes unmerged `314fadd` plus this follow-up).

PR #20–#25 MERGED. PR #26 OPEN. PR #27 MERGED (partial).

## Priority defects

| ID | Severity | Status |
|---|---|---|
| InsForge POST timeout retry | P0 | fixed |
| 4xx trips circuit breaker | P0 | fixed |
| GET 500 / Retry-After / error leak | P1 | fixed |
| Folder import `project_id` ACL | P1 | fixed |
| Same-path SHA replacement | P1 | fixed |
| Audit contract + TypeError | P1 | fixed |
| Search project scope | P1 | fixed |
| Job vs review status mix | P1 | fixed |
| Concurrent human patch | P1 | fixed |
| Confirm promoting probable people | P1 | fixed (follow-up) |
| Organize guessing unscoped `project_id` | P1 | fixed (follow-up) |
| Folder UI content-hash idempotency key | P1 | fixed (follow-up) |
| CSRF `Origin: null` | P1 | fixed (follow-up) |
| GitHub Actions install | P1 | BLOCKED_BY_EXTERNAL_DEPENDENCY (no `workflow` scope) |

## Not redone

Chat, knowledge RAG, Artifact, Session, existing visual_* tables, InsForge SQL 001–003, Redis/Celery worker.
