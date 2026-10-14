"""Version-pinned exercise-title operations exposed only to data migrations."""

from sqlalchemy.orm import Session

import modules.activities.activity_exercise_titles.crud as activity_exercise_titles_crud
import modules.activities.activity_exercise_titles.schema as activity_exercise_titles_schema


def create_activity_exercise_titles(
    activity_exercise_titles: list[activity_exercise_titles_schema.ActivityExerciseTitlesRead],
    db: Session,
) -> None:
    """Create exercise titles and commit them, as migrations did.

    Args:
        activity_exercise_titles: The titles to store.
        db: Database session.
    """
    activity_exercise_titles_crud.create_activity_exercise_titles(activity_exercise_titles, db)
    db.commit()
