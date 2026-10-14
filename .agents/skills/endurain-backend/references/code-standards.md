# Code standards

## Python
- Python 3.13 syntax: `int | None`, `list[str]`; no `typing.Optional/List/Dict`.
- Lines up to 120 characters, code and prose alike (the formatter does not wrap docstrings; keep paragraphs readable).
- Imports grouped stdlib / third-party / local; `TYPE_CHECKING` to avoid cycles.
- Every module has a top-level docstring.
- `__init__.py` follows the module guide (docstring only), not `__all__`.

## Docstrings (PEP 257, Google sections)
Enforced by ruff `D` with `convention = "google"` on the converted modules (`activities`, `followers`); other modules are exempt until converted.
- Summary on the opening line, then a blank line, then sections.
- Every public module, class, function and method has a docstring.
- `Args`: every parameter (D417 enforces it).
- `Returns`: when a value comes back. `Returns: None.` is not required.
- `Raises`: the exceptions the function raises or deliberately lets propagate, including those raised by a decorator such as `handle_db_errors`, and especially the ones that carry a security meaning (`PermissionDeniedError`, `NotFoundError` as an anti-enumeration answer). Omit it when there are none; do not write `Raises: None.`.
- Classes: summary plus `Attributes`.
- No examples; extended explanation only where the *why* is not obvious from the code.
- A docstring with backslashes is a raw string (`r"""`).

## SQLAlchemy 2
- `Mapped[...]` with `mapped_column()`; every column typed.
- `crud` stages with `db.flush()`; only `service` commits. `db.refresh(obj)` after the flush when you need server defaults.
- Persistence raises `core.exceptions`; the app handler maps them to responses.

## Pydantic v2
- `ConfigDict`, `field_validator`; no class `Config`, no `@validator`.
- Keep ORM and schema names distinct.

## FastAPI
- `response_model` on every endpoint; status 200 (GET), 201 (POST create), 202 (POST accepted for background processing), 204 (DELETE).
- Auth through `Depends()`.
- Endpoint docstring says what, not how. It is exempt from `Args`/`Returns`/`Raises`: FastAPI publishes it as the OpenAPI description and it is copied into `api.generated.ts`, so parameter names such as `_check_scopes` would become public API text.
- Route handlers are plain `def`, never `async def` (a blocking call in an async handler stalls the event loop; `test_no_route_handler_is_async`). Handle uploads with `core.file_uploads.save_validated_upload_sync`.

## Logging
Use `core.logger.get_logger(__name__)` and `core.logger.context(...)`; never f-string values into messages.

| Layer | Logs |
| --- | --- |
| `router`, `public_router` | nothing (a rejected capability token is logged by the `service` that verifies it) |
| `service` | INFO state change completed; WARNING request refused; DEBUG inputs to a non-obvious decision |
| `crud` | only what it swallows: ERROR cannot complete, WARNING absorbed anomaly; never success narration |
| `subscribers` | DEBUG when skipping, ERROR when failing |
| `query`, `serializers`, `signing`, `models`, `schema`, pure utils | nothing |

`console=True` only for operator-facing events (startup, migrations, bulk import progress).
