"""ORM↔schema transformation for activity streams.

The counterpart of :mod:`modules.activities.activity.serializers`, and named to
match: ``crud`` calls the transformation at its edges so ORM rows never leave the
persistence layer. Visibility masking is an access decision and lives in
:mod:`modules.activities.activity_streams.service`.
"""

from typing import overload

import modules.activities.activity_streams.contracts as activity_streams_contracts
import modules.activities.activity_streams.models as activity_streams_models
import modules.activities.activity_streams.schema as activity_streams_schema


@overload
def transform_activity_streams(
    activity_streams: list[activity_streams_models.ActivityStreams],
) -> list[activity_streams_schema.ActivityStreamsRead]: ...


@overload
def transform_activity_streams(
    activity_streams: activity_streams_models.ActivityStreams,
) -> activity_streams_schema.ActivityStreamsRead: ...


def transform_activity_streams(
    activity_streams: activity_streams_models.ActivityStreams | list[activity_streams_models.ActivityStreams],
) -> activity_streams_schema.ActivityStreamsRead | list[activity_streams_schema.ActivityStreamsRead]:
    """
    Transform a stream or list of streams to a Pydantic schema or list of schemas.

    Args:
        activity_streams: The stream ORM instance or list of stream ORM instances.

    Returns:
        The activity stream as a schema or list of schemas.
    """
    if isinstance(activity_streams, list):
        return [activity_streams_schema.ActivityStreamsRead.model_validate(stream) for stream in activity_streams]
    return activity_streams_schema.ActivityStreamsRead.model_validate(activity_streams)


def to_hr_record(
    stream: activity_streams_models.ActivityStreams,
) -> activity_streams_contracts.HrStreamRecord:
    """
    Project one stream ORM row into a package-owned record.

    Args:
        stream: The stream ORM row to project.

    Returns:
        The HR stream record.
    """
    return activity_streams_contracts.HrStreamRecord(
        stream_id=stream.id,
        activity_id=stream.activity_id,
        waypoints=stream.stream_waypoints or [],
    )
