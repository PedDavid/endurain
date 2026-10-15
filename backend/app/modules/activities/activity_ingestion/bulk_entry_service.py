"""Entry point for background ingestion: bulk imports and provider syncs.

Both entries here run a file through the shared
:mod:`~modules.activities.activity_ingestion.pipeline_service`; they differ only in what
they do when it fails.

* :func:`store_activity_file` **swallows** — it moves a failed bulk-import file to
  the error directory and returns ``None`` so the rest of the batch continues.
* :func:`store_bulk_import_file` **raises** — it is the body of a durable job, so
  a failure must propagate for the runner to retry with backoff and eventually
  dead-letter.
"""

import os
from pathlib import Path

import jasil.providers as platform_providers
from sqlalchemy.orm import Session

import core.config as core_config
import core.file_uploads as core_file_uploads
import core.logger as core_logger
import modules.activities.activity.schema as activity_schema
import modules.activities.activity_ingestion.contracts as activity_ingestion_contracts
import modules.activities.activity_ingestion.pipeline_service as activity_ingestion_pipeline_service

logger = core_logger.get_logger(__name__)


def store_bulk_import_file(
    user_id: int,
    file_path: str,
    import_initiated_time: str | None,
    storage: platform_providers.StorageProvider,
    db: Session,
) -> list[activity_schema.ActivityRead] | None:
    """Import one bulk-import file, **raising** on failure (the durable job body).

    The per-file body of the durable bulk-import job: it validates, decompresses,
    and stores the file exactly like :func:`store_activity_file` but does **not**
    swallow errors or move the file — a failure propagates so the durable-job
    runner retries with backoff and eventually dead-letters. The durable
    subscriber owns moving a dead-lettered file to the import-error directory.

    Args:
        user_id: ID of the user performing the import.
        file_path: Absolute path to the activity file to parse.
        import_initiated_time: ISO timestamp of when the bulk import was initiated.
        storage: The blob-storage provider the source file is retained in.
        db: SQLAlchemy database session.

    Returns:
        List of created activity schema objects.
    """
    return activity_ingestion_pipeline_service.validate_prepare_and_store_file(
        user_id,
        file_path,
        db,
        source=activity_ingestion_contracts.BulkImportSource(
            import_initiated_time=import_initiated_time, user_id=user_id
        ),
        storage=storage,
    )


def _default_error_directory(source: activity_ingestion_contracts.BulkImportSource) -> str:
    """Return the per-user error directory a generic bulk import fails into.

    Per-user, matching the per-user drop directory: moving one user's failed file
    into a shared location would undo the isolation the import itself enforces.

    Args:
        source: The bulk-import source.

    Returns:
        The directory path.
    """
    if source.user_id is None:
        return core_config.FILES_BULK_IMPORT_IMPORT_ERRORS_DIR
    return core_config.bulk_import_error_dir_for(source.user_id)


def _move_failed_file_to_error_directory(source: activity_ingestion_contracts.BulkImportSource, file_path: str) -> None:
    """Move a file whose import failed into the source's error directory.

    Args:
        source: The bulk-import source, which decides the destination.
        file_path: The file that failed to import.
    """
    try:
        error_file_dir = source.error_directory or _default_error_directory(source)
        os.makedirs(error_file_dir, exist_ok=True)
        core_file_uploads.move_within(file_path, error_file_dir, filename=os.path.basename(file_path))
        logger.error(
            "Bulk file import: moved the error-producing file to the import-error directory",
            extra=core_logger.context(console=True, file=Path(file_path).name, error_directory=error_file_dir),
        )
    except OSError as err:
        logger.error(
            "Bulk file import: failed to move the error-producing file to the import-error directory",
            exc_info=err,
            extra=core_logger.context(console=True, file=Path(file_path).name),
        )


def store_activity_file(
    token_user_id: int,
    file_path: str,
    storage: platform_providers.StorageProvider,
    db: Session,
    *,
    source: activity_ingestion_contracts.IngestionSource,
) -> list[activity_schema.ActivityRead] | None:
    """Validate an on-disk activity file and persist it, returning ``None`` on failure.

    Thin entry point for background ingestion (Garmin sync, Strava/generic bulk
    import). Validates and (if needed) decompresses the file, then delegates to
    the shared activity_ingestion_pipeline_service. Unlike the upload entry it never raises to its caller: on
    failure it (for bulk imports) moves the offending file to the import-error
    directory and returns ``None`` so the batch can continue.

    Supports .gpx, .tcx, .fit, and .gz files. Must be called from a worker thread
    with no running event loop (Starlette threadpool, the bulk ThreadPoolExecutor,
    or ``asyncio.to_thread``) because file validation runs a private event loop
    internally.

    Args:
        token_user_id: ID of the authenticated user performing the import.
        file_path: Absolute path to the activity file to parse.
        storage: The blob-storage provider the source file is retained in.
        db: SQLAlchemy database session.
        source: Where the file came from, and any source-specific metadata.

    Returns:
        List of created activity schema objects, or None if the file could not be
        parsed or persisted.
    """
    try:
        return activity_ingestion_pipeline_service.validate_prepare_and_store_file(
            token_user_id,
            file_path,
            db,
            source=source,
            storage=storage,
        )
    except Exception as err:
        # Any failure, not a whitelist: this entry's contract is that one bad
        # file never stops the batch, and a parser can raise more than the
        # types a list would anticipate. It is also the one that has to leave
        # the shared session usable.
        db.rollback()
        if isinstance(source, activity_ingestion_contracts.BulkImportSource):
            logger.error(
                "Bulk file import: error while parsing file",
                exc_info=err,
                extra=core_logger.context(console=True, file=Path(file_path).name, user_id=token_user_id),
            )
            _move_failed_file_to_error_directory(source, file_path)
        logger.error(
            "Error in store_activity_file",
            exc_info=err,
            extra=core_logger.context(console=True, file=Path(file_path).name, user_id=token_user_id),
        )
        # Background-task callers expect ``None`` on failure rather
        # than re-raising; make that contract explicit.
        return None
