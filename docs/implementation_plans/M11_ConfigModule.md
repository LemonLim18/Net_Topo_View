# M11: Config Module Frontend

End-to-end configuration management UI connecting M3 Config Engine, M4 Deployment Pipeline, and M5 LLM Agent.

## Architecture

```text
User → Configuration View (M11)
         ├── Select Vendor + Feature template
         ├── Fill parameters (auto-generated from JSON schema)
         ├── Preview rendered CLI config
         ├── View side-by-side diff (current vs proposed)
         ├── Approve → triggers M4 Deployment Pipeline
         └── View Change History (past deployments)
```

## Backend Changes

### [NEW] `backend/config_engine/api.py`

FastAPI router (prefix `/config`) exposing M3 Config Engine:

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/config/templates` | List all templates (vendor, feature, has_undo, schema_exists) |
| `GET` | `/config/templates?vendor=X` | Filter templates by vendor |
| `GET` | `/config/schemas/{feature}` | Get JSON schema for a template |
| `POST` | `/config/render` | Render template → CLI text + optional undo commands |
| `POST` | `/config/diff` | Compare two configs → structured DiffResult |
| `POST` | `/config/validate` | Validate config text → errors[] |

### [MODIFIED] `backend/backend.py`

Mounted `config_router` at `/api/v1`.

## Frontend Changes

### [MODIFIED] `frontend/src/services/api.ts`

Added `configApi` namespace with 5 methods: `listTemplates`, `getSchema`, `render`, `diff`, `validate`.

### [NEW] `frontend/components/ConfigDiff.tsx`

Side-by-side config diff viewer:
- Green lines = additions, Red lines = deletions
- Summary badges (additions/deletions/unchanged counts)
- Scrollable panels with line numbers

### [NEW] `frontend/components/ApprovalDialog.tsx`

Modal dialog for deployment approval:
- Config & rollback command preview
- Device connection form (host, credentials, driver type)
- 4-step M4 pipeline: Create Request → Add Job → Plan → Execute
- Real-time step-by-step status indicators

### [REWRITTEN] `frontend/views/Configuration.tsx`

3-tab layout replacing the old placeholder:

1. **Generate Config** — Vendor/feature selector, auto-generated param form from JSON schema, render preview, side-by-side diff, deploy button
2. **Template Browser** — Browse templates per vendor, view JSON schemas & parameter reference tables
3. **Change History** — Deployment job log table with status badges

## Integration Points

- **M3 → M11**: `configApi` calls `TemplateRenderer.render()`, `ConfigDiffer.full_diff()`, `SyntaxValidator.validate()`
- **M11 → M4**: `ApprovalDialog` chains: `createRequest` → `addJob` → `planJob` → `executeJob`
- **M5 → M11**: LLM Agent can generate params for templates; future integration via AIAgentPanel

## Verification

- `npx tsc --noEmit` passes (exit code 0)
- All new files compile without type errors
