"""Durable-job subscribers for activity ingestion: uploads, refreshes and bulk-import files.

The upload and refresh routes answer 202 and hand the work here, because parsing
a file is seconds of pure CPU and a provider refresh is a chain of third-party
HTTP round-trips. Doing either inline tied up a request thread — and refresh,
being the one ``async`` route, tied up the event loop itself.

Bulk import enqueues **one durable job per file** (via the transactional outbox)
instead of a fire-and-forget threadpool task, so a crash mid-import no longer
drops in-flight work and a failing file is retried with backoff and finally
dead-lettered — moved to the import-error directory as the human trail — instead
of vanishing to the error dir on the first error.

Every channel is durable-delivery only: the routes publish them exclusively when
``JOBS_ENABLED``, so they always route to the outbox → relay → job. When durable
jobs are off the routes fall back to the in-process pool and these events are
never published — hence no best-effort bus subscribers here.

The handlers **raise** on failure so the runner retries and eventually
dead-letters. Unlike bulk import, a terminally failed upload or refresh is
**not** moved to an error directory: the requester is a real client waiting on a
status, so the outcome is recorded on the ``activity_ingestion_jobs`` row where
they can read it.
"""

import jasil.event_versioning as platform_event_versioning
import jasil.runtime as platform_runtime
from jasil.events import Event
from jasil.jobs.registry import JobHandlerRegistry

import core.config as core_config
import core.database as core_database
import core.logger as core_logger
import modules.activities.activity_ingestion.bulk_entry_service as activity_ingestion_bulk_entry_service
import modules.activities.activity_ingestion.events as activity_ingestion_events
import modules.activities.activity_ingestion.schema as activity_ingestion_schema
import modules.activities.activity_ingestion.service as activity_ingestion_service
import modules.activities.activity_ingestion.staging as activity_ingestion_staging

logger = core_logger.get_logger(__name__)

# Stable durable-subscriber ids (independent of module path) so job history and
# dedup survive refactors.
UPLOADED_FILE_SUBSCRIBER_ID = "activity_ingestion.uploaded_file"
REFRESH_REQUESTED_SUBSCRIBER_ID = "activity_ingestion.refresh_requested"
BULK_IMPORT_FILE_SUBSCRIBER_ID = "activity_ingestion.bulk_import_file"


def _is_final_attempt(event: Event) -> bool:
    """Whether this failure will dead-letter the job.

    ``retry_count`` is the claim-incremented attempt number, so reaching the
    ceiling means the runner has no attempts left.

    Args:
        event: The event being processed.

    Returns:
        True when no further retry will happen.
    """
    return event.retry_count >= core_config.settings.JOBS_MAX_ATTEMPTS


def process_uploaded_file_for_event(event: Event) -> None:
    """Durable handler: import one uploaded file; raises so the runner retries.

    On the final attempt the job row is marked failed before re-raising, so a
    dead-lettered upload still ends in a terminal state the uploader can see
    rather than sitting at ``processing`` forever.

    Args:
        event: The ``activity.file_uploaded`` event (payload ``{"job_id": str}``).
    """
    payload = platform_event_versioning.parse_payload(activity_ingestion_events.UploadedFilePayload, event)
    storage = platform_runtime.get_active_platform().storage
    try:
        activity_ingestion_service.run_upload_job(payload.job_id, storage)
    except Exception as err:
        if _is_final_attempt(event):
            activity_ingestion_service.fail_ingestion_job(
                payload.job_id,
                activity_ingestion_schema.IngestionJobErrorCode.PROCESSING_FAILED,
                storage,
            )
            logger.error(
                "Upload job dead-lettered",
                exc_info=err,
                extra=core_logger.context(console=True, job_id=payload.job_id),
            )
        raise


def process_refresh_requested_for_event(event: Event) -> None:
    """Durable handler: sync one user's providers; raises so the runner retries.

    Provider failures are the common case here — a rate limit, an expired token,
    an outage — and all of them are worth retrying with backoff, which is why
    nothing is treated as terminal until the attempts run out.

    Args:
        event: The ``activity.refresh_requested`` event (payload
            ``{"job_id": str}``).
    """
    payload = platform_event_versioning.parse_payload(activity_ingestion_events.RefreshRequestedPayload, event)
    storage = platform_runtime.get_active_platform().storage
    try:
        activity_ingestion_service.run_refresh_job(payload.job_id, storage)
    except Exception as err:
        if _is_final_attempt(event):
            activity_ingestion_service.fail_ingestion_job(
                payload.job_id,
                activity_ingestion_schema.IngestionJobErrorCode.PROVIDER_UNAVAILABLE,
                storage,
            )
            logger.error(
                "Refresh job dead-lettered",
                exc_info=err,
                extra=core_logger.context(console=True, job_id=payload.job_id),
            )
        raise


def process_bulk_import_file_for_event(event: Event) -> None:
    """Durable handler: import one staged bulk-import file; raises so the runner retries.

    The durable-job handler for ``activity.bulk_import_file``: any error
    propagates so the runner retries with backoff and eventually dead-letters the
    job. On the final attempt the staged blob is moved to the import-error area
    first, so a dead-lettered file still leaves a human trail — but only after
    the retries are exhausted, not on the first error.

    The caller's ``activity_ingestion_jobs`` row is moved along with the work, so
    the handle returned by ``POST /activities/bulk-import`` reports the same
    outcome the runner reached. Only the last attempt records a failure: an
    earlier one is still going to be retried, and reporting it as terminal would
    tell the owner the file failed while the import is still in progress.

    Args:
        event: The ``activity.bulk_import_file`` event (payload
            ``{"job_id": str | None, "storage_key": str, "filename": str,
            "user_id": int, "import_initiated_time": str}``).
    """
    payload = platform_event_versioning.parse_payload(activity_ingestion_events.BulkImportFilePayload, event)
    storage = platform_runtime.get_active_platform().storage
    activity_ingestion_service.mark_bulk_import_job_processing(payload.job_id)
    try:
        with activity_ingestion_staging.materialized(payload.storage_key, payload.filename, storage) as file_path:
            if file_path is None:
                # Already imported and discarded: a duplicate delivery, not a
                # failure. Raising would retry until the job dead-lettered.
                logger.info(
                    "Bulk import: skipping a job whose file is no longer staged",
                    extra=core_logger.context(
                        user_id=payload.user_id, file=payload.filename, storage_key=payload.storage_key
                    ),
                )
                return
            with core_database.SessionLocal() as db:
                activities = activity_ingestion_bulk_entry_service.store_bulk_import_file(
                    payload.user_id, file_path, payload.import_initiated_time, storage, db
                )
        activity_ingestion_staging.discard(payload.storage_key, storage)
        activity_ingestion_service.mark_bulk_import_job_completed(
            payload.job_id, [activity.id for activity in activities or [] if activity.id is not None]
        )
        logger.debug(
            "Imported a bulk-import file",
            extra=core_logger.context(user_id=payload.user_id, file=payload.filename),
        )
    except Exception as err:
        # ``retry_count`` is the (claim-incremented) attempt number; when it has
        # reached the ceiling this failure dead-letters the job.
        if _is_final_attempt(event):
            activity_ingestion_staging.move_to_errors(payload.storage_key, payload.user_id, payload.filename, storage)
            activity_ingestion_service.mark_bulk_import_job_failed(payload.job_id)
        else:
            logger.warning(
                "Bulk-import file failed; the job will be retried",
                exc_info=err,
                extra=core_logger.context(
                    user_id=payload.user_id,
                    file=payload.filename,
                    attempt=event.retry_count,
                    max_attempts=core_config.settings.JOBS_MAX_ATTEMPTS,
                ),
            )
        raise


def register_durable_handlers(registry: JobHandlerRegistry) -> None:
    """Register the ingestion handlers as durable job subscribers.

    Args:
        registry: The durable-subscriber registry to register on.
    """
    registry.register(
        activity_ingestion_events.ACTIVITY_FILE_UPLOADED,
        UPLOADED_FILE_SUBSCRIBER_ID,
        process_uploaded_file_for_event,
    )
    registry.register(
        activity_ingestion_events.ACTIVITY_REFRESH_REQUESTED,
        REFRESH_REQUESTED_SUBSCRIBER_ID,
        process_refresh_requested_for_event,
    )
    registry.register(
        activity_ingestion_events.ACTIVITY_BULK_IMPORT_FILE,
        BULK_IMPORT_FILE_SUBSCRIBER_ID,
        process_bulk_import_file_for_event,
    )
