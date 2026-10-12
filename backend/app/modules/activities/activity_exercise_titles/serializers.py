"""ORM↔schema transformation for activity exercise titles.

``crud`` calls these at its edges so ORM rows never leave the persistence layer.
"""

import modules.activities.activity_exercise_titles.models as activity_exercise_titles_models
import modules.activities.activity_exercise_titles.schema as activity_exercise_titles_schema


def to_read_schema(
    orm_title: activity_exercise_titles_models.ActivityExerciseTitles,
) -> activity_exercise_titles_schema.ActivityExerciseTitles:
    """
    Convert an ORM row to its read schema.

    Args:
        orm_title: The ORM model instance.

    Returns:
        An ActivityExerciseTitles schema instance.
    """
    return activity_exercise_titles_schema.ActivityExerciseTitles.model_validate(orm_title)


def to_orm(
    title: activity_exercise_titles_schema.ActivityExerciseTitles,
) -> activity_exercise_titles_models.ActivityExerciseTitles:
    """
    Build an ORM row from a title schema.

    Args:
        title: The exercise title schema to persist.

    Returns:
        An unsaved ActivityExerciseTitles ORM instance.
    """
    return activity_exercise_titles_models.ActivityExerciseTitles(
        exercise_category=title.exercise_category,
        exercise_name=title.exercise_name,
        wkt_step_name=title.wkt_step_name,
    )
