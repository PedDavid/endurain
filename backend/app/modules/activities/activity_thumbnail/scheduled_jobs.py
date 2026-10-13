"""Scheduled work owned by the activity-thumbnail package."""

import jasil.runtime as platform_runtime

import core.database as core_database
import core.scheduler as core_scheduler
import modules.activities.activity_thumbnail.service as activity_thumbnail_service


def generate_missing_thumbnails() -> None:
    """Generate thumbnails for activities whose stored key is missing.

    Scheduler-invoked, with no request to carry the providers or a session, so
    this entry point resolves the providers and opens the session.

    Returns:
        None.
    """
    platform = platform_runtime.get_active_platform()
    with core_database.SessionLocal() as db:
        activity_thumbnail_service.generate_missing_activity_thumbnails(platform.storage, platform.lock, db)


def regenerate_all_thumbnails() -> None:
    """Delete and regenerate every activity thumbnail.

    Returns:
        None.
    """
    platform = platform_runtime.get_active_platform()
    with core_database.SessionLocal() as db:
        activity_thumbnail_service.delete_and_regenerate_all_activity_thumbnails(platform.storage, platform.lock, db)


def recurring_jobs() -> tuple[core_scheduler.ScheduledJob, ...]:
    """Return thumbnail reconciliation jobs.

    Returns:
        The jobs to schedule.
    """
    return (
        core_scheduler.ScheduledJob(
            generate_missing_thumbnails,
            60,
            "generate thumbnails for activities missing one",
        ),
    )


def schedule_missing_thumbnail_generation() -> None:
    """Queue one thumbnail reconciliation pass.

    Returns:
        None.
    """
    core_scheduler.run_once(
        generate_missing_thumbnails,
        job_id="endurain_generate_missing_thumbnails_oneshot",
        description="missing thumbnail generation",
    )
