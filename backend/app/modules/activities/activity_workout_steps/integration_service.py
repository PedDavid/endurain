"""Module surface for activity workout steps: ingestion and profile contributors."""

from typing import Any

from sqlalchemy.orm import Session

import core.exceptions as core_exceptions
import modules.activities.activity.schema as activity_schema
import modules.activities.activity_workout_steps.crud as activity_workout_steps_crud
import modules.activities.activity_workout_steps.schema as activity_workout_steps_schema
import modules.activities.contributors as activities_contributors


def _list_workout_steps_for_activities(
    activity_ids: list[int],
    db: Session,
) -> list[activity_workout_steps_schema.ActivityWorkoutSteps]:
    """Return workout steps for activity IDs already scoped by the caller.

    Args:
        activity_ids: IDs of the activities to read.
        db: Database session.

    Returns:
        Workout steps for activity IDs already scoped by the caller.
    """
    return activity_workout_steps_crud.get_activities_workout_steps(activity_ids, db)


def _store_workout_steps(
    steps: list[activity_workout_steps_schema.ActivityWorkoutSteps],
    activity_id: int,
    db: Session,
) -> None:
    """Store parsed workout steps for an activity.

    Args:
        steps: Workout steps to store.
        activity_id: ID of the parent activity.
        db: Database session.

    Returns:
        None.
    """
    activity_workout_steps_crud.create_activity_workout_steps(steps, activity_id, db)


def _persist_ingestion_component(
    data: Any,
    activity: activity_schema.Activity,
    db: Session,
) -> None:
    """Persist parsed workout steps through the ingestion contract.

    Args:
        data: The parsed component payload.
        activity: The newly created parent activity.
        db: Database session.

    Returns:
        None.
    """
    if activity.id is None:
        raise core_exceptions.ProcessingError("Cannot store workout steps before the activity has an id")
    _store_workout_steps(data, activity.id, db)


def ingestion_contributor() -> activities_contributors.ActivityIngestionContributor:
    """Return the workout-step ingestion contribution.

    Returns:
        The contributor.
    """
    return activities_contributors.ActivityIngestionContributor(
        key="workout_steps",
        persist=_persist_ingestion_component,
    )


def _restore_profile_records(
    records: list[dict[str, Any]],
    original_activity_id: int,
    new_activity: activity_schema.Activity,
    db: Session,
) -> int:
    """Validate and restore profile workout steps for one activity.

    Args:
        records: Profile records to restore.
        original_activity_id: The activity's ID in the exported archive.
        new_activity: The activity the records are restored onto.
        db: Database session.

    Returns:
        The number of records restored.
    """
    if new_activity.id is None:
        return 0

    steps: list[activity_workout_steps_schema.ActivityWorkoutSteps] = []
    for record in records:
        if record.get("activity_id") != original_activity_id:
            continue
        data = dict(record)
        data.pop("id", None)
        data["activity_id"] = new_activity.id
        steps.append(activity_workout_steps_schema.ActivityWorkoutSteps.model_validate(data))

    if steps:
        _store_workout_steps(steps, new_activity.id, db)
    return len(steps)


def profile_contributor() -> activities_contributors.ProfileActivityContributor:
    """Return the workout-step profile contribution.

    Returns:
        The contributor.
    """
    return activities_contributors.ProfileActivityContributor(
        key="workout_steps",
        archive_path="data/activity_workout_steps.json",
        count_key="activity_workout_steps",
        split=False,
        export=_list_workout_steps_for_activities,
        restore=_restore_profile_records,
    )
