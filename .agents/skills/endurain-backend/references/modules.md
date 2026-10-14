# Module structure reference

Condensed from `docs/developer-guide/module-structure.md`; where they differ the guide wins.
The visibility, layering and transaction rules are stated once, in the skill's core rules and the guide; this file holds only the lookup tables and the checklist.

## File roles

A stem may qualify a role (`summary_crud.py` is a crud). A second persistence file is `<thing>_crud.py`, never a new word.
A file that orchestrates modules or commits is `<thing>_service.py`.

| File | Responsibility | Visibility |
| --- | --- | --- |
| `models.py` | ORM; only place a table is declared | private |
| `query.py` | SQL fragments; no session, no I/O | private |
| `crud.py` | Only file that runs statements on own tables; stages with `flush`; returns schemas/DTOs, never ORM rows | private |
| `serializers.py` | ORM <-> schema (`to_read_schema`, `to_orm`), called by crud | private |
| `service.py` | Access decisions, orchestration, events, transactions | private |
| `router.py`, `public_router.py` | Validate, delegate to service, return | composition |
| `dependencies.py` | FastAPI DI | public |
| `schema.py` | Pydantic request/response (client-facing) | public |
| `contracts.py` | Inter-module DTOs and typed seams, never serialized | public |
| `constants.py`, `events.py` | Constants; event names and payloads | public |
| `event_publishers.py`, `subscribers.py` | Publish / handle events | private |
| `integration_service.py` | Curated operations for other modules, each with a caller outside the package | surface |
| `subscriber_registry.py`, `scheduled_jobs.py` | Wiring collected by `module_registry`; `scheduled_jobs` also holds the job runners | composition |
| `model_registry.py` | Declares the package's ORM module | composition |
| `migration_service.py` | Version-pinned operations for `app/migrations` only | migration-only |

Other files (`utils.py`, `signing.py`, `render.py`, `files.py`...) are package-private.

## Import aliases

An alias is the containing package plus the role: `<package>_integration` for a surface, `<package>_<role>` otherwise.
Root package: `activity_crud`, `activity_schema`, `activity_integration`. Children: `activity_laps_service`. Namespace-level files live in `activities`: `activities_contributors`, `activities_computation`.

## Coupling mechanism

| Need | Use |
| --- | --- |
| Ask a peer / read its data | peer `integration_service` |
| Write atomic with another module's write | service call, same session; owner's service stages, publishes, commits |
| React to a fact, derive state, tolerate delay | event + subscriber + reconciliation net |
| Lower layer needs what only a higher layer knows | seam owned by lower layer, installed by composition |

Activity children return `ingestion_contributor`, `profile_contributor`, `profile_global_contributor` from `integration_service`.
Plain child collections (rows hanging off one activity) build them with `activities_contributors.child_ingestion_contributor` / `child_profile_contributor`.
`app/module_registry.py` installs them in `modules.activities.contributor_registry`.

## Adding a module

Follow `docs/developer-guide/module-structure.md#adding-a-module`; the `convert-module` skill has the procedure for an existing module. Mirror the tree under `backend/tests/`.

## Auth boundary

Use only `modules.auth.dependencies` and `modules.auth.identity_service.IdentityService`.
Never import `modules.auth._internal` or any `modules.auth.*.crud`. No `ignore_imports` exceptions.
`auth` publishes `identity_service.py`, not `integration_service.py`; do not copy that.

## Enforcement

`backend/.importlinter` (external packages, platform direction, auth boundary, named seams) and `tests/architecture/` (layer rules, privacy, DAG, roles, transactions, logging). The guide's Enforcement section is authoritative; allowlists hold only exact composition or read-model edges, each with a reason.
