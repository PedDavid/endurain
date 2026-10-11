---
name: endurain-backend
description: Endurain backend conventions (FastAPI, SQLAlchemy 2, module structure). Use when adding, moving, refactoring or reviewing code in backend/app or backend/tests.
---

# Endurain backend

The module guide `docs/developer-guide/module-structure.md` is authoritative.
For identity use `docs/developer-guide/auth-boundary.md`.
Where this skill and a guide disagree, the guide wins.

## Core rules (always apply)

- A module is one aggregate or capability and the unit of extraction.
- `modules/activities` is a composition namespace; each child package is a peer module.
- Peers and outside modules import only `integration_service` plus `schema`, `contracts`, `constants`, `events` (and `dependencies`).
- `service`, `crud`, `models`, `query`, `serializers`, `utils`, `signing`, `subscribers`, `event_publishers` are private to their package.
- Layering is downward only: `router -> service -> crud -> query/models`.
- `crud` never calls another module and raises `core.exceptions`, not `HTTPException`.
- `service` owns commit/flush/rollback and publishes events; `integration_service` must not.
- `__init__.py` is a docstring and nothing else.
- Non-auth code reaches identity only through `modules.auth.dependencies` and `modules.auth.identity_service`.
- Filenames are the contract: a misnamed file silently escapes the rules.
- Never register contributors, subscribers or providers as an import side effect.
- Storage, events, jobs and geocoding come from `jasil` ports (`jasil.providers`, `jasil.publisher`, ...); never import `jasil.backends` or `jasil._core`.
- Resolve providers (`get_active_platform()`) only at entry points - router `Depends`, subscriber, job, `integration_service`; below that, take the provider as an argument.
- Events are published from `service` via `event_publishers`, with the commit callable, never from `crud` or `integration_service`.

## Read on demand

| When you are | Read |
| --- | --- |
| Creating a module, deciding where code goes, wiring contributors | `references/modules.md` |
| Storage, events, subscribers, durable jobs, reconciliation nets, scheduled work | `references/platform-jasil.md` |
| Writing models, schemas, endpoints, docstrings, logging | `references/code-standards.md` |
| Raising errors, endpoints, pagination, rate limits, API changes | `references/errors-and-api.md` |
| Handling uploads, access control, secrets, outbound requests | `references/security.md` |
| Writing or fixing tests | `references/testing.md` |

## Before finishing a structural change

```bash
cd backend
PYTHONPATH=app uv run lint-imports
uv run pytest tests/architecture tests/test_logging_rule.py -q --no-cov
uv run ruff format --check . && uv run ruff check .
```

If a rule blocks you, add the operation to the owner's `integration_service`; do not widen an allowlist.

## Repo hygiene

- Do only what was asked: no extra docs, summaries or READMEs.
- Prefer existing patterns and utilities over new files.
- Do not touch Dockerfiles, CI, ports, env vars or framework versions unless asked.
- Local notes go in `devdocs/`, helper scripts in `devscripts/` (gitignored).
- Never run `git commit` unless asked; use the `conventional-commits` skill for messages.
