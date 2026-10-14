"""Version-pinned stream operations exposed only to data migrations."""

from sqlalchemy.orm import Session

import core.logger as core_logger
import modules.activities.activity_streams.crud as activity_streams_crud
import modules.activities.activity_streams.hr_zones as activity_streams_hr_zones

logger = core_logger.get_logger(__name__)

get_activity_streams = activity_streams_crud.get_activity_streams
get_activity_stream_by_type = activity_streams_crud.get_activity_stream_by_type
get_hr_streams_without_zone_percentages = activity_streams_crud.get_hr_streams_without_zone_percentages
compute_hr_zone_breakdown_sync = activity_streams_hr_zones.compute_hr_zone_breakdown_sync


def backfill_zone_percentages_for_missing_hr_streams(
    computed_streams: list[dict[str, int | dict]],
    db: Session,
) -> None:
    """Backfill pre-computed zone percentages and commit, logging a failure.

    The failure is swallowed, as it was when the CRUD committed: migration 7
    re-derives the values on its next run, so one bad batch must not stop it.

    Args:
        computed_streams: Stream ids with their computed breakdowns.
        db: Database session.
    """
    activity_streams_crud.backfill_zone_percentages_for_missing_hr_streams(computed_streams, db)
    try:
        db.commit()
    except Exception as err:
        db.rollback()
        logger.error(
            "Failed to backfill zone percentages for HR streams",
            exc_info=err,
            extra=core_logger.context(console=True),
        )


def resolve_max_heart_rate(user) -> int | None:
    """Resolve an athlete's max heart rate from the user row a migration holds.

    Pinned to the era's signature: migration 7 walks user rows, so it passes the
    row itself. The computation takes the three fields it actually reads, so the
    unpacking lives here rather than making the streams package depend on the
    users wire type.

    Args:
        user: A user row or DTO exposing ``max_heart_rate``, ``birthdate`` and
            ``timezone``.

    Returns:
        The resolved max heart rate, or None when it cannot be derived.
    """
    return activity_streams_hr_zones.resolve_max_heart_rate(user.max_heart_rate, user.birthdate, user.timezone)
