"""Version-pinned workout-step operations exposed only to data migrations."""

from sqlalchemy.orm import Session

import modules.activities.activity_workout_steps.crud as activity_workout_steps_crud
import modules.activities.activity_workout_steps.schema as activity_workout_steps_schema


def create_activity_workout_steps(
    activity_workout_steps: list[activity_workout_steps_schema.ActivityWorkoutSteps],
    activity_id: int,
    db: Session,
) -> None:
    """Create workout steps for an activity and commit them, as migrations did.

    Args:
        activity_workout_steps: Steps to store.
        activity_id: The parent activity ID.
        db: Database session.

    Returns:
        None.
    """
    activity_workout_steps_crud.create_activity_workout_steps(activity_workout_steps, activity_id, db)
    db.commit()
