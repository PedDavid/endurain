"""Public reverse-geocoding operations for activities."""

import jasil.runtime as platform_runtime

import core.database as core_database
import core.logger as core_logger
import modules.activities.activity_geocoding.service as activity_geocoding_service

logger = core_logger.get_logger(__name__)


def run_missing_location_backfill() -> None:
    """Scheduled reconciliation net: geocode GPS activities missing a location.

    Scheduler-invoked, with no request to carry the provider or a session, so
    this entry point resolves the provider and opens the session. Acquires the
    coordination lock so only one replica runs the pass.

    Returns:
        None.
    """
    platform = platform_runtime.get_active_platform()
    with platform.lock.try_acquire("location_backfill") as acquired:
        if not acquired:
            logger.debug("Geocoding scheduler: another replica holds the backfill lock; skipping")
            return
        with core_database.SessionLocal() as db:
            activity_geocoding_service.backfill_missing_activity_locations(db, platform.geocoding)
