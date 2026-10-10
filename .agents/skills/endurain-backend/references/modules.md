# Module structure reference

## Units

| Unit | Role | Example |
| --- | --- | --- |
| Composition namespace | Groups modules, collects routers/jobs/subscribers; not an implementation boundary | `modules/activities` |
| Module | One aggregate/capability; unit of extraction | `modules/activities/activity_laps`, `modules/followers` |

One aggregate stays flat (`modules/followers`).
The root `activity` package owns the activity row and parent access decisions, not child tables.

## File roles

A stem may qualify a role (`summary_crud.py` is a crud).
A second persistence file is `<thing>_crud.py`, never a new word.

| File | Responsibility | Visibility |
| --- | --- | --- |
| `models.py` | ORM; only place a table is declared | private |
| `query.py` | SQL fragments; no session, no I/O | private |
| `crud.py` | Only file that runs statements on own tables; returns schemas/DTOs, never ORM rows | private |
| `serializers.py` | ORM <-> schema, called by crud | private |
| `service.py` | Access decisions, orchestration, events, transactions | private |
| `router.py`, `public_router.py` | Validate, delegate to service, return | composition |
| `dependencies.py` | FastAPI DI | public |
| `schema.py` | Pydantic request/response | public |
| `contracts.py` | Inter-module DTOs and typed seams, never serialized | public |
| `constants.py`, `events.py` | Constants; event names and payloads | public |
| `event_publishers.py`, `subscribers.py` | Publish / handle events | private |
| `integration_service.py` | Curated operations for other modules | surface |
| `subscriber_registry.py`, `scheduled_jobs.py` | Wiring collected by `module_registry` | composition |
| `model_registry.py` | Declares the package's ORM module | composition |
| `migration_service.py` | Version-pinned operations for `app/migrations` only | migration-only |

Other files (`utils.py`, `signing.py`, `pipeline.py`...) are package-private.

## Import rules

- `models` never import other models; foreign keys are strings (`ForeignKey("activities.id")`), lifecycle is `ON DELETE`.
- `integration_service` may read its own module but must not publish events or own a commit; arrange writes in `service`.
- It holds only operations with a real caller; a pure pass-through goes straight to CRUD.
- Packages inside a namespace form a DAG; invert one edge with a seam if two import each other.
- `core` and `jasil` never import `modules`; the platform asks, the module registers.
- `jasil`: use `providers`, `publisher`, `events`, `subscribers`, `jobs.registry`; never `backends` or `_core`.
- `modules.activities` makes no raw network calls (`requests`, `httpx`, `socket`); add a platform provider.
- Migrations import only `*.migration_service`; domain and core code never do.

## Coupling mechanism

| Need | Use |
| --- | --- |
| Ask a peer / read its data | peer `integration_service` |
| Write atomic with another module's write | service call, same session; owner's service stages, publishes, commits |
| React to a fact, derive state, tolerate delay | event + subscriber + reconciliation net |
| Lower layer needs what only a higher layer knows | seam owned by lower layer, installed by composition |

Activity children return `ingestion_contributor`, `profile_contributor`, `profile_global_contributor` from `integration_service`.
`app/module_registry.py` installs them in `modules.activities.contributor_registry`.
`main.py` and `worker.py` both consume `module_registry`.

## Adding a module

1. Create `modules/<name>` or `modules/<namespace>/<name>`; docstring-only `__init__.py`.
2. Add `integration_service.py` with only operations that have a caller.
3. Add `model_registry.py` if it owns a table, and contribute it to the namespace/app registry.
4. Add `migration_service.py` only if an existing data migration needs it.
5. Add `subscriber_registry.py` / `scheduled_jobs.py`, then one `RuntimeModule(name=..., ...)` in `app/module_registry.py` (keywords; backfills as `StartupTask`).
6. Activity child archived as JSON: add contributor factories and register in `configure_activity_contributors`.
7. Add the module to `backend/.importlinter` contract source lists.
8. New top-level module: add to `CONVERTED_MODULES` in `backend/tests/_helpers/module_roles.py`.
9. Mirror the tree under `backend/tests/`.

## Auth boundary

Use only `modules.auth.dependencies` and `modules.auth.identity_service.IdentityService`.
Never import `modules.auth._internal` or any `modules.auth.*.crud`.
No `ignore_imports` exceptions.
`auth` publishes `identity_service.py`, not `integration_service.py`; do not copy that.

## Enforcement

- `backend/.importlinter`: cross-module contracts, wildcards so new activity children inherit them.
- `tests/architecture/test_module_boundaries.py`: own-package vs peer privacy, DAG, only crud executes, content matches role, module inventory.
- Allowlist entries (`_INTRA_MODULE_SEAMS`, `_INBOUND_EXCEPTIONS`) are exact composition or read-model edges with a reason.
