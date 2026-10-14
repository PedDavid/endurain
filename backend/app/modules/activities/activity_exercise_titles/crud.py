"""Activity exercise titles CRUD operations."""

from sqlalchemy import select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import core.decorators as core_decorators
import core.exceptions as core_exceptions
import core.logger as core_logger
import modules.activities.activity_exercise_titles.models as activity_exercise_titles_models
import modules.activities.activity_exercise_titles.schema as activity_exercise_titles_schema
import modules.activities.activity_exercise_titles.serializers as activity_exercise_titles_serializers

logger = core_logger.get_logger(__name__)


@core_decorators.handle_db_errors
def get_activity_exercise_titles(
    db: Session,
) -> list[activity_exercise_titles_schema.ActivityExerciseTitlesRead]:
    """Retrieve all activity exercise titles.

    Args:
        db: Database session.

    Returns:
        Every exercise title, empty when there are none.

    Raises:
        ProcessingError: If a database error occurs.
    """
    stmt = select(activity_exercise_titles_models.ActivityExerciseTitles)
    return [activity_exercise_titles_serializers.to_read_schema(title) for title in db.execute(stmt).scalars().all()]


@core_decorators.handle_db_errors
def create_activity_exercise_titles(
    activity_exercise_titles: list[activity_exercise_titles_schema.ActivityExerciseTitlesRead],
    db: Session,
) -> None:
    """Insert activity exercise titles, skipping existing entries.

    Args:
        activity_exercise_titles: Schemas to insert.
        db: Database session.

    Raises:
        ConflictError: On a duplicate (exercise_name, exercise_category) entry.
        ProcessingError: On other database errors.
    """
    if not activity_exercise_titles:
        return

    model = activity_exercise_titles_models.ActivityExerciseTitles

    incoming_keys = {(t.exercise_name, t.exercise_category) for t in activity_exercise_titles}

    existing_stmt = select(model.exercise_name, model.exercise_category).where(
        tuple_(model.exercise_name, model.exercise_category).in_(incoming_keys)
    )
    existing_keys = set(db.execute(existing_stmt).all())

    new_entries = [
        activity_exercise_titles_serializers.to_orm(t)
        for t in activity_exercise_titles
        if (t.exercise_name, t.exercise_category) not in existing_keys
    ]

    if not new_entries:
        return

    try:
        db.add_all(new_entries)
        db.flush()
    except IntegrityError as integrity_error:
        logger.warning(
            "Activity exercise title insert conflicted on (exercise_name, exercise_category)",
            exc_info=integrity_error,
            extra=core_logger.context(attempted=len(new_entries)),
        )
        raise core_exceptions.ConflictError(
            "Duplicate entry error. Check if (exercise_name, exercise_category) is unique"
        ) from integrity_error
