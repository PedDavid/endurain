"""Scheduled work owned by activity ingestion."""

import jasil.runtime as platform_runtime

import core.scheduler as core_scheduler
import modules.activities.activity_ingestion.service as activity_ingestion_service


def prune_expired_ingestion_jobs() -> None:
    """Delete ingestion job rows beyond their retention window.

    Scheduler-invoked, with no request to carry the providers, so this entry
    point resolves them.
    """
    platform = platform_runtime.get_active_platform()
    activity_ingestion_service.prune_expired_ingestion_jobs(platform.lock, platform.clock)


def fail_stale_ingestion_jobs() -> None:
    """Fail ingestion jobs that stopped progressing.

    Scheduler-invoked, with no request to carry the providers, so this entry
    point resolves them.
    """
    platform = platform_runtime.get_active_platform()
    activity_ingestion_service.fail_stale_ingestion_jobs(platform.lock, platform.clock, platform.storage)


def sweep_stale_staged_blobs() -> None:
    """Delete staged ingestion blobs that nothing will read again.

    Scheduler-invoked, with no request to carry the providers, so this entry
    point resolves them.
    """
    platform = platform_runtime.get_active_platform()
    activity_ingestion_service.sweep_stale_staged_blobs(platform.lock, platform.clock, platform.storage)


def recurring_jobs() -> tuple[core_scheduler.ScheduledJob, ...]:
    """Return ingestion retention and stale-job jobs.

    Returns:
        The jobs to schedule.
    """
    return (
        core_scheduler.ScheduledJob(
            prune_expired_ingestion_jobs,
            1440,
            "prune expired activity ingestion jobs",
        ),
        core_scheduler.ScheduledJob(
            fail_stale_ingestion_jobs,
            60,
            "fail activity ingestion jobs that stopped progressing",
        ),
        core_scheduler.ScheduledJob(
            sweep_stale_staged_blobs,
            1440,
            "remove staged ingestion blobs nothing will read",
        ),
    )
