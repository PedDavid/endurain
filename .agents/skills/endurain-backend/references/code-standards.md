# Code standards

## Python
- Python 3.13 syntax: `int | None`, `list[str]`; no `typing.Optional/List/Dict`.
- Code lines up to 120 characters; comments and docstrings up to 72.
- Imports grouped stdlib / third-party / local; `TYPE_CHECKING` to avoid cycles.
- Every module has a top-level docstring.
- `__init__.py` follows the module guide (docstring only), not `__all__`.

## Docstrings (PEP 257)
- One-line summary, blank line, then `Args` / `Returns` / `Raises` (always, even when obvious).
- Classes: summary plus `Attributes`.
- No examples, no extended explanations.

## SQLAlchemy 2
- `Mapped[...]` with `mapped_column()`; every column typed.
- `db.refresh(obj)` after `db.commit()`.
- Persistence raises `core.exceptions`; the app handler maps them to responses.

## Pydantic v2
- `ConfigDict`, `field_validator`; no class `Config`, no `@validator`.
- Keep ORM and schema names distinct.

## FastAPI
- `response_model` on every endpoint; status 200 (GET), 201 (POST), 204 (DELETE).
- Auth through `Depends()`.
- Endpoint docstring says what, not how.
- Async file reads: `await file.read()`.

## Logging
Use `core.logger.get_logger(__name__)` and `core.logger.context(...)`; never f-string values into messages.

| Layer | Logs |
| --- | --- |
| `router`, `public_router` | nothing (exception: rejected capability token on an unauthenticated blob route) |
| `service` | INFO state change completed; WARNING request refused; DEBUG inputs to a non-obvious decision |
| `crud` | only what it swallows: ERROR cannot complete, WARNING absorbed anomaly; never success narration |
| `subscribers` | DEBUG when skipping, ERROR when failing |
| `query`, `serializers`, `signing`, `models`, `schema`, pure utils | nothing |

`console=True` only for operator-facing events (startup, migrations, bulk import progress).
