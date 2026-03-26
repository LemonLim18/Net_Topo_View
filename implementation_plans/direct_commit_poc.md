# Direct‑Commit POC — Git‑backed Template Store

## Overview
A minimal proof‑of‑concept that replaces the current file store for templates/schemas with a local Git checkout. Edits from the `TemplateStudioView` are written to the repo, committed, and pushed. Purpose: validate feasibility, developer ergonomics, and error handling with minimal infra changes.

## Goals
- Persist templates and schema changes into a Git repo.
- Preserve an auditable commit history (commit messages include app user info).
- Keep UI changes minimal (show commit SHA / link after save).
- Low implementation effort: 1–3 days.

## Assumptions / Prereqs
- A Git repository (private) exists and is accessible from the backend host.
- A deploy key or service token with push access is available and stored securely on the server (env secret).
- Backend can run `git` or has `GitPython` installed.
- Existing backend authentication exposes the authenticated user (id/username/email).

## Repo layout (recommended)
- `templates/<vendor>/<feature>.j2`
- `schemas/<feature>.json`

## High‑level design
1. Backend keeps a local clone at `REPO_PATH` (configurable). On boot, ensure clone exists and set `origin`.
2. When a client calls `saveTemplateSource` / `createTemplate` / `deleteTemplate`:
   - Validate user is allowed to perform action (RBAC check).
   - Acquire a lightweight repo lock (file lock) to prevent concurrent writes.
   - `git fetch` and verify local HEAD is up‑to‑date with remote.
     - POC policy: if remote has new commits, return 409 conflict and ask user to refresh. (Keep merge logic out of scope for POC.)
   - Write file atomically (write temp -> rename).
   - `git add <path>`; `git commit -m "[nettopoview] <vendor>/<feature> edited by <username> (<user-id>)" --author "<Username> <email>"`
   - `git push origin <branch>` (POC: push to `templates-staging` or `main` depending on policy).
   - Return commit SHA, remote URL, and brief message to the UI.
3. `deleteTemplate` deletes the file, commits, and pushes similarly.

## Implementation details
- Use `GitPython` (python) or shell `git` via subprocess. Example libs: `gitpython`, `dulwich` (pure Python), or `subprocess` for portable CLI usage.
- Configuration via env vars / backend config:
  - `TEMPLATES_REPO_PATH`
  - `TEMPLATES_GIT_REMOTE`
  - `TEMPLATES_GIT_BRANCH` (e.g. `templates-staging`)
  - `GIT_DEPLOY_KEY_PATH` or `GIT_TOKEN` (for HTTPS)
- Commit author mapping: set commit author to app user when possible; otherwise use a service identity and include user info in commit message.
- Conflicts: for POC keep simple: if remote changed, fail with 409 and require client to re-open and re-apply edits.

## UI considerations
- `TemplateStudioView` already calls `saveTemplateSource`. Update to display returned commit SHA and a link to the repository commit (if remote is hosted and URL is available).
- Add a small notice when the save was pushed to branch `templates-staging` vs `main`.

## Security
- Store deploy key / token securely (do not return credentials to client).
- Enforce RBAC server‑side: only allow users with proper roles to call save/create/delete endpoints.
- Ensure the backend process runs under a user with minimal system privileges.

## Testing
- Unit tests for write/commit logic (mock git or use ephemeral local repo).
- Integration test: run a test backend pointing at a throwaway repo; save a template and verify commit and push.

## Limitations / Tradeoffs
- No PR/review workflow: direct commits can bypass review and CI checks.
- Merge conflicts require manual resolution by operators.
- Not recommended for production without branch protection and CI checks.

## Rollout plan
1. Implement behind feature flag `GIT_DIRECT_POC=true`.
2. Use a non‑production test repo first.
3. Allow only `admin` role to use direct commit in staging; require caution.
4. Evaluate iterating to PR‑based flow after validating the workflow.

## Time estimate
POC implementation: 1–3 days (backend + minimal UI feedback). Full production hardening: additional days/weeks.
