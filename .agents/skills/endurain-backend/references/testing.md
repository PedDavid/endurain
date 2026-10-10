# Testing

- Tests live in `backend/tests/` mirroring `backend/app/`; files `test_*.py`, grouped in classes.
- Use fixtures from `conftest.py`.
- Never instantiate SQLAlchemy models; inspect attributes (`MyModel.name.nullable is False`, `MyModel.id.type.python_type == int`).
- `AsyncMock` for async, `MagicMock` for sync; `@patch` external dependencies (logging, DB calls).
- Cover empty/None, missing entity, errors, malformed input.
- `with pytest.raises(...) as exc_info` for exceptions.
- Async tests: `async def test_*`, check with `assert_awaited_once()`.
- Skips need `reason=`.
- Target 100% coverage on new code.

```bash
uv run pytest tests/<area> -v
uv run pytest tests/<area> --cov=app/<area> --cov-report=term-missing
```

Architecture tests: `tests/architecture/`, `tests/test_logging_rule.py`; role vocabulary in `tests/_helpers/module_roles.py`.
