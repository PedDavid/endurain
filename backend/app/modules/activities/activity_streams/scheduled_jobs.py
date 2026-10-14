"""Scheduled work owned by the activity-streams package."""

import logging

import jasil.runtime as platform_runtime

import core.database as core_database
import core.logger as core_logger
import core.scheduler as core_scheduler
import modules.activities.activity_streams.service as activity_streams_service

logger = core_logger.get_logger(__name__)


def run_missing_hr_zone_backfill() -> None:
    """Scheduled reconciliation net: score HR streams missing ``zone_percentages``.

    Scheduler-invoked, with no request to carry the provider or a session, so
    this entry point resolves the provider and opens the session. Acquires the
    coordination lock so only one replica runs the pass.
    """
    platform = platform_runtime.get_active_platform()
    with platform.lock.try_acquire("hr_zone_backfill") as acquired:
        if not acquired:
            logger.debug("HR-zone scheduler: another replica holds the backfill lock; skipping")
            return
        with core_database.SessionLocal() as db:
            updated = activity_streams_service.backfill_missing_hr_zones(db)
        # Level varies so an idle scheduler pass stays at debug instead of
        # emitting an INFO line every tick.
        logger.log(
            logging.INFO if updated else logging.DEBUG,
            "HR-zone scheduler pass complete",
            extra=core_logger.context(updated=updated),
        )


def recurring_jobs() -> tuple[core_scheduler.ScheduledJob, ...]:
    """Return stream reconciliation jobs.

    Returns:
        The jobs to schedule.
    """
    return (
        core_scheduler.ScheduledJob(
            run_missing_hr_zone_backfill,
            60,
            "backfill missing HR zone percentages",
        ),
    )


def schedule_missing_hr_zone_backfill() -> None:
    """Queue one missing-HR-zone reconciliation pass."""
    core_scheduler.run_once(
        run_missing_hr_zone_backfill,
        job_id="endurain_backfill_missing_hr_zones_oneshot",
        description="HR-zone backfill",
    )
