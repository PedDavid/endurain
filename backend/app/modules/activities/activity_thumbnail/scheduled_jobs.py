"""Scheduled work owned by the activity-thumbnail package."""

import core.scheduler as core_scheduler
import modules.activities.activity_thumbnail.integration_service as activity_thumbnail_integration


def recurring_jobs() -> tuple[core_scheduler.ScheduledJob, ...]:
    """Return thumbnail reconciliation jobs.

    Returns:
        The jobs to schedule.
    """
    return (
        core_scheduler.ScheduledJob(
            activity_thumbnail_integration.generate_missing_thumbnails,
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
        activity_thumbnail_integration.generate_missing_thumbnails,
        job_id="endurain_generate_missing_thumbnails_oneshot",
        description="missing thumbnail generation",
    )
