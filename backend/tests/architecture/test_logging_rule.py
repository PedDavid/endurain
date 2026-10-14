"""The logging-by-layer rule from ``.agents/skills/endurain-backend/references/code-standards.md``, enforced.

A convention that only exists in prose drifts: before this, CRUD modules narrated
every successful read and write at DEBUG — duplicating what the service already
reported, at query volume — while four services logged nothing at all. These
assertions are what keep the layers saying one kind of thing each.
"""

import pathlib
import re

from tests._helpers.module_roles import CONVERTED_MODULES, files_with_role

#: Modules the logging rule has been applied to — the shared
#: ``CONVERTED_MODULES`` list in ``tests/_helpers/module_roles.py``, so
#: converting a module is a single edit there.
_CONVERTED = tuple(pathlib.Path("app/modules") / name for name in CONVERTED_MODULES)

# Layers whose logging level set is fixed by the rule.
_ALLOWED: dict[str, set[str]] = {
    # Persistence reports only what it swallows: a failure it cannot complete,
    # or an anomaly it absorbs behind a normal-looking return.
    "crud": {"warning", "error"},
    # Detached from any request, so a skip must say why and a failure must shout.
    "subscribers": {"debug", "error", "warning"},
    # No decisions, no failures of their own.
    "query": set(),
    "serializers": set(),
    "signing": set(),
    "models": set(),
    "schema": set(),
    "contracts": set(),
}

# Services that make no decision of their own: they declare what their child
# collection is and hand the read to ``activity/child_collection``, which logs
# the one decision there is (a refusal). A log line here would either duplicate
# that or narrate a delegation.
_DELEGATING_SERVICES = [
    "app/modules/activities/activity_laps/service.py",
    "app/modules/activities/activity_sets/service.py",
    "app/modules/activities/activity_workout_steps/service.py",
]


def _log_calls(path: pathlib.Path) -> list[str]:
    """Return the log levels used in a module."""
    return re.findall(r"logger\.(debug|info|warning|error)\(", path.read_text())


def _modules_named(stem: str) -> list[pathlib.Path]:
    """Return every converted module whose file plays the given role."""
    return files_with_role(_CONVERTED, stem)


class TestLoggingRule:
    def test_crud_never_narrates_success(self):
        """The service already said what happened; repeating it here is noise."""
        offenders = [
            f"{p}: {sorted(set(_log_calls(p)) - _ALLOWED['crud'])}"
            for p in _modules_named("crud")
            if set(_log_calls(p)) - _ALLOWED["crud"]
        ]

        assert offenders == []

    def test_routers_delegate_their_logging_too(self):
        offenders = [str(p) for stem in ("router", "public_router") for p in _modules_named(stem) if _log_calls(p)]

        assert offenders == []

    def test_pure_layers_do_not_log(self):
        offenders = [
            f"{p}: {sorted(set(_log_calls(p)))}"
            for stem in ("query", "serializers", "signing", "models", "schema", "contracts")
            for p in _modules_named(stem)
            if _log_calls(p)
        ]

        assert offenders == []

    def test_every_service_reports_something(self):
        """A silent service is one whose decisions are invisible in production."""
        silent = [str(p) for p in _modules_named("service") if not _log_calls(p)]

        assert silent == _DELEGATING_SERVICES
