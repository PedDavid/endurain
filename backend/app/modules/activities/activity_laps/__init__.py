"""Activity laps sub-module for per-lap metrics within an activity.

Per-lap distance, duration, heart rate, power, cadence and altitude parsed
from fitness files (.fit, .tcx, .gpx) or third-party providers.

Peers and outside modules use this module only through ``integration_service``
(plus ``schema``). A re-export facade here would hand out the ORM model and the
CRUD functions under a package path, which is a silent bypass of the boundaries
the import-linter contracts enforce against ``*.models`` and ``*.crud``.
"""
