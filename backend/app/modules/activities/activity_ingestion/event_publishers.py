"""Publish the activity-ingestion domain events through the platform publish facade.

Each function knows its channel and correlation metadata and delegates envelope
assembly and the transactional outbox to :mod:`jasil.publisher`. Every channel
here is durable-delivery only: the event **is** the work, so each is published
with the caller's commit and a staging failure propagates rather than being
swallowed.
"""

from collections.abc import Callable

import jasil.publisher as platform_publisher
from sqlalchemy.orm import Session

import modules.activities.activity_ingestion.events as activity_ingestion_events


def publish_file_uploaded(job_id: str, user_id: int, db: Session, *, commit: Callable[[], None]) -> None:
    """Publish ``activity.file_uploaded`` so a durable worker imports the staged upload.

    Staged in the transactional outbox on ``db`` and committed with the job row,
    then relayed into a retryable ``processing_jobs`` row.

    Args:
        job_id: The ``activity_ingestion_jobs`` row to process.
        user_id: The uploading user's ID.
        db: The session the job row was created on.
        commit: Zero-arg commit callable that ends the caller's transaction.

    Returns:
        None.
    """
    payload = activity_ingestion_events.UploadedFilePayload(job_id=job_id)
    platform_publisher.publish_committing(
        activity_ingestion_events.ACTIVITY_FILE_UPLOADED,
        payload.model_dump(),
        source="api:upload",
        db=db,
        commit=commit,
        metadata={"user_id": user_id},
        schema_version=payload.SCHEMA_VERSION,
    )


def publish_refresh_requested(job_id: str, user_id: int, db: Session, *, commit: Callable[[], None]) -> None:
    """Publish ``activity.refresh_requested`` so a durable worker syncs the providers.

    Args:
        job_id: The ``activity_ingestion_jobs`` row to process.
        user_id: The requesting user's ID.
        db: The session the job row was created on.
        commit: Zero-arg commit callable that ends the caller's transaction.

    Returns:
        None.
    """
    payload = activity_ingestion_events.RefreshRequestedPayload(job_id=job_id)
    platform_publisher.publish_committing(
        activity_ingestion_events.ACTIVITY_REFRESH_REQUESTED,
        payload.model_dump(),
        source="api:refresh",
        db=db,
        commit=commit,
        metadata={"user_id": user_id},
        schema_version=payload.SCHEMA_VERSION,
    )


def publish_bulk_import_files(
    staged_files: list[tuple[str, str, str]],
    user_id: int,
    import_initiated_time: str,
    db: Session,
    *,
    commit: Callable[[], None],
) -> None:
    """Publish one ``activity.bulk_import_file`` per staged file in a single transaction.

    Goes through :func:`jasil.publisher.publish_many_committing`, which
    **propagates** staging failures instead of logging and continuing: a
    swallowed failure would mean the file is silently never imported while the
    route still answers 202.

    Args:
        staged_files: ``(ingestion job id, storage key, filename)`` per file.
        user_id: ID of the user performing the import.
        import_initiated_time: ISO timestamp of when the bulk import was initiated.
        db: The session the job rows were created on.
        commit: Zero-arg commit callable that ends the caller's transaction.

    Returns:
        None.

    Raises:
        Exception: Propagated from the outbox staging.
    """
    platform_publisher.publish_many_committing(
        activity_ingestion_events.ACTIVITY_BULK_IMPORT_FILE,
        [
            activity_ingestion_events.BulkImportFilePayload(
                job_id=job_id,
                storage_key=key,
                filename=filename,
                user_id=user_id,
                import_initiated_time=import_initiated_time,
            ).model_dump()
            for job_id, key, filename in staged_files
        ],
        source="api:bulk_import",
        db=db,
        commit=commit,
        schema_version=activity_ingestion_events.BulkImportFilePayload.SCHEMA_VERSION,
    )
