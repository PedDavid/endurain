"""FastAPI dependencies that validate activity path and query parameters.

Mounted at the HTTP boundary by the activity routers. A rejected value answers
422, matching the shared :mod:`core.dependencies` validators the sibling child
packages use.
"""

from fastapi import HTTPException, Query, status

import core.dependencies as core_dependencies
import core.logger as core_logger
import modules.activities.activity.constants as activity_constants

logger = core_logger.get_logger(__name__)


def validate_activity_id(activity_id: int) -> None:
    """Validate that the activity ID is a positive integer.

    Args:
        activity_id: The ID of the activity to validate.

    Returns:
        None.

    Raises:
        HTTPException: 422 when the ID is not greater than zero.
    """
    core_dependencies.validate_id(identifier=activity_id, min_value=0, message="Invalid activity ID")


def validate_activity_type(activity_type: int | None = Query(None)) -> None:
    """Validate the activity type filter against the known activity types.

    Args:
        activity_type: The activity type to validate; ``None`` means no filter.

    Returns:
        None.

    Raises:
        HTTPException: 422 when the type is not a known activity type.
    """
    if activity_type not in activity_constants.ACTIVITY_ID_TO_NAME and activity_type is not None:
        logger.debug(
            "Rejected activity list request with an unknown activity type",
            extra=core_logger.context(activity_type=activity_type),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid activity type",
        )


def validate_sort_by(sort_by: str | None = Query(None)) -> None:
    """Validate the ``sort_by`` query parameter.

    Args:
        sort_by: The sort field; ``None`` means the default order. Allowed values
            are :data:`~modules.activities.activity.constants.ACTIVITY_SORT_FIELDS`.

    Returns:
        None.

    Raises:
        HTTPException: 422 when the field is not an allowed sort field.
    """
    if sort_by is not None and sort_by not in activity_constants.ACTIVITY_SORT_FIELDS:
        logger.debug(
            "Rejected activity list request with an unsupported sort field",
            extra=core_logger.context(sort_by=sort_by),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid sort by field",
        )


def validate_sort_order(sort_order: str | None = Query(None)) -> None:
    """Validate the ``sort_order`` query parameter.

    Args:
        sort_order: The sort direction; ``None`` means the default. Allowed values
            are :data:`~modules.activities.activity.constants.ACTIVITY_SORT_ORDERS`.

    Returns:
        None.

    Raises:
        HTTPException: 422 when the direction is not an allowed sort order.
    """
    if sort_order is not None and sort_order not in activity_constants.ACTIVITY_SORT_ORDERS:
        logger.debug(
            "Rejected activity list request with an unsupported sort order",
            extra=core_logger.context(sort_order=sort_order),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid sort order",
        )
