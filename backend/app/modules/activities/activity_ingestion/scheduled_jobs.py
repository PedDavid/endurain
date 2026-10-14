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


def recurring_jobs() -> tuple[core_scheduler.ScheduledJob, ...]:
    """Return ingestion retention jobs.

    Returns:
        The jobs to schedule.
    """
    return (
        core_scheduler.ScheduledJob(
            prune_expired_ingestion_jobs,
            1440,
            "prune expired activity ingestion jobs",
        ),
    )
