# PR‑based POC — GitHub App (Recommended)

## Overview
A production‑grade proof‑of‑concept where template edits are committed on a feature branch and a GitHub Pull Request (PR) is opened automatically. PRs run CI checks (lint, schema validation, sample renders) and require review/merge to publish. This matches enterprise code‑review and audit practices (similar to VSCode/Antigravity UX).

## Goals
- Use GitHub as source‑of‑truth with PR‑based publish flow.
- Ensure change validation via CI before merge.
- Provide traceability: PRs show who changed what, links to CI/checks, and allow reviewers to comment.
- Integrate with GitHub App for fine‑grained, auditable automation.

## Assumptions / Prereqs
- Templates repo is hosted on GitHub (org or personal account).
- Ability to create a GitHub App (or OAuth app) with installation access to the repo.
- Backend can securely store GitHub App private key and app id.
- CI (GitHub Actions) is available to run template lints and validation.

## Repo layout
- `templates/<vendor>/<feature>.j2`
- `schemas/<feature>.json`

## High‑level flow
1. User edits in `TemplateStudioView` and clicks **Create PR** (or Save → Create PR).
2. Backend:
   - Validates RBAC for the user.
   - Creates a branch name: `template/<vendor>/<feature>/<username>/<timestamp>`.
   - Writes the new/updated file to the branch, commits (author metadata may use service account), pushes branch.
   - Calls GitHub REST API (authenticated as GitHub App installation) to open a PR targeting `main` (or `templates-staging`), including a descriptive PR body with:
     - Who opened it (app user)
     - Render sample output or link to rendered artifact
     - Optional diff summary using `configApi.diff`.
   - Optionally request reviewers or add labels.
3. GitHub Actions run checks (linters, jsonschema validate, render tests). Check results surface in the PR.
4. Reviewer approves on GitHub and merges. On merge webhook, backend receives event and updates local cache and any downstream consumers. Optionally trigger publish/deploy steps.

## Implementation details
### GitHub App vs OAuth
- Use a **GitHub App** (recommended) for scoped permissions and better audit logs.
- App needs `contents: write`, `pull_requests: write`, `checks: write`, `statuses: write` for the installation.
- Backend uses app's private key to exchange for installation token and call APIs.

### Backend responsibilities
- Manage branch creation, commits, push, and PR creation (use `PyGithub`/`requests` or `gh` CLI).
- Map app user to PR metadata (include user id/username in PR body and commit message).
- Maintain a record mapping UI edit sessions to PRs so subsequent saves update the same branch/PR.
- Provide endpoints to list PRs for templates, and to fetch PR/CI status.
- Validate and sanitize incoming template content before committing (avoid templates that contain malicious injections for any automation steps).

### UI changes
- Add button `Open PR` next to `Save` in `TemplateStudioView`.
- When PR is open, show PR link, current checks status, and last commit SHA.
- Allow users to `Update PR` (push new commits to the same branch) or `Close PR` (optional UX that calls backend to close PR).
- Show diff between current draft and target branch using `configApi.diff` or GitHub PR diff.

### CI checks
- Create GitHub Actions workflow(s):
  - `templates-lint.yml`: run Jinja linter (e.g., `jinjalint`), JSON schema validation, and a render smoke test (run `nettopo` render in a container with sample params). Fail on lint/errors.
  - If checks pass, PR becomes mergeable.

### Webhooks
- Wire `pull_request` and `push` webhooks to backend to:
  - Update cached template index.
  - Trigger downstream processes (e.g., validation runs, rendering artifacts upload).
  - Record merge events and author info in application audit logs.

### Conflict handling
- If the same path already has an open PR, backend should append commits to the existing branch/PR rather than opening duplicates.
- If remote branch has diverged and the backend cannot fast‑forward, surface a merge conflict message in UI and route the PR to a reviewer to manually resolve.

## Security & RBAC
- Store GitHub App private key and app id in secure storage (env/secrets manager).
- Server must verify user identity before mapping edits to PRs; include app user metadata in PR body for traceability.
- Protect `main` branch with required checks and required reviewers.
- Do not allow client to perform GitHub actions directly—backend service must own GitHub credentials.

## Observability & Audit
- Log PR creation events with user id, timestamp, changed files, and commit SHAs.
- Expose PR / build status in `TemplateStudioView`.

## Testing
- End‑to‑end test against a sandbox GitHub repo (create a test org/repo and install the GitHub App there).
- Unit tests for branch/PR creation logic; integration tests calling the GitHub API (recorded with VCR or run against test installation).

## Rollout
1. Create GitHub App with limited scope and install on test repo.
2. Implement backend PR creation and UI hooks behind a feature flag `GIT_PR_POC=true`.
3. Test end‑to‑end with a small team (edit → PR → review → merge → webhook update).
4. Harden RBAC and CI checks; enable branch protection on production repo.

## Optional advanced features
- Use GitHub Checks API to publish rendered config as a check artifact and provide inline annotations.
- Use CODEOWNERS or required reviewers to enforce domain expert approvals.
- Add a `publish` workflow that triggers post‑merge to deploy templates to a production store.

## Time estimate
- PR‑based POC (end‑to‑end, test repo): ~3–7 days.
- Full production integration (App, CI, webhooks, audit): ~2–6 weeks depending on QA and policy needs.
