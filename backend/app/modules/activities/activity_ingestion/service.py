"""Accepting and running activity ingestion jobs.

Three ways activities enter the system on a user's request — an uploaded file, a
provider refresh and a bulk import of the user's drop directory — and all work
the same way: the route accepts, returns a handle, and the work runs on a
background worker. ``accept_*`` and ``start_bulk_import`` run in the request;
``run_*_job`` is the job body, deliberately identical whichever executor calls
it (the durable worker when ``JOBS_ENABLED``, the in-process pool otherwise), so
the client contract does not change with the deployment.

This service owns the transaction for every job row: the entry points hand it
the storage provider and it stages, publishes and commits.

Nothing here is transport-aware: it takes a user id and a session, returns the
job handles it queued, and raises :mod:`core.exceptions` so the same call works
from a future CLI or scheduled trigger without constructing an HTTP response.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jasil.providers as platform_providers
from fastapi import HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

import core.config as core_config
import core.database as core_database
import core.exceptions as core_exceptions
import core.file_uploads as core_file_uploads
import core.logger as core_logger
import modules.activities.activity_ingestion.background as activity_ingestion_background
import modules.activities.activity_ingestion.bulk_entry as bulk_entry
import modules.activities.activity_ingestion.contracts as ingestion_contracts
import modules.activities.activity_ingestion.crud as ingestion_crud
import modules.activities.activity_ingestion.event_publishers as ingestion_event_publishers
import modules.activities.activity_ingestion.refresh_entry as refresh_entry
import modules.activities.activity_ingestion.schema as activity_ingestion_schema
import modules.activities.activity_ingestion.staging as staging
import modules.activities.activity_ingestion.upload_entry as upload_entry

logger = core_logger.get_logger(__name__)

# Failure classes the owner can act on, mapped to a stable code. Anything not
# listed collapses to PROCESSING_FAILED: the exception text can carry filesystem
# paths and parser internals, so only this closed set ever reaches a client.
_ERROR_CODES: dict[type[Exception], activity_ingestion_schema.IngestionJobErrorCode] = {
    core_exceptions.UnsupportedFormatError: activity_ingestion_schema.IngestionJobErrorCode.UNSUPPORTED_FORMAT,
    core_exceptions.InvalidInputError: activity_ingestion_schema.IngestionJobErrorCode.INVALID_FILE,
}


def _error_code_for(error: Exception) -> activity_ingestion_schema.IngestionJobErrorCode:
    """Map an exception to the sanitized code shown to the uploader.

    Args:
        error: The failure raised by the import.

    Returns:
        The matching error code, or ``PROCESSING_FAILED``.
    """
    for error_type, code in _ERROR_CODES.items():
        if isinstance(error, error_type):
            return code
    return activity_ingestion_schema.IngestionJobErrorCode.PROCESSING_FAILED


def accept_upload(
    token_user_id: int,
    file: UploadFile,
    db: Session,
    storage: platform_providers.StorageProvider,
    *,
    idempotency_key: str | None = None,
) -> activity_ingestion_schema.ActivityIngestionJob:
    """Stage an uploaded file and queue it for background import.

    The staging write happens before the row is created so a rejected file never
    leaves a job behind. The row and the event are then committed together, so a
    crash between them cannot produce a job nobody will ever run.

    When the caller supplies an ``Idempotency-Key``, a replay returns the
    original job instead of importing the file twice. The activity-level
    content dedup would already stop a duplicate *activity*, but only after the
    file has been stored and parsed — the key short-circuits before either, so a
    client retrying on a flaky connection does not pay for a second 200 MiB
    storage write and a second parse.

    Reusing a key for *different* content is rejected rather than answered with
    the first job: silently returning it would tell the client the second file
    imported when it never will.

    Args:
        token_user_id: Authenticated user ID.
        file: Incoming FastAPI UploadFile.
        db: Database session.
        storage: The blob-storage provider the upload is staged in.
        idempotency_key: Client-supplied key identifying this request.

    Returns:
        The accepted upload job, in the pending state — or the job the same key
        was accepted with previously.

    Raises:
        InvalidInputError: When the filename is missing.
        UnsupportedFormatError: When the extension is not a supported format.
        ConflictError: When the key was already used for different content.
        HTTPException: When the shared upload validators reject the payload.
    """
    received = upload_entry.receive_upload(file, fingerprint=idempotency_key is not None)

    if idempotency_key:
        prior = ingestion_crud.get_job_for_idempotency(
            idempotency_key, token_user_id, activity_ingestion_schema.IngestionJobKind.UPLOAD, db
        )
        if prior is not None:
            replayed, fingerprint = prior
            upload_entry.discard_received_upload(received)
            if fingerprint != received.fingerprint:
                raise core_exceptions.ConflictError("This Idempotency-Key was already used for a different file")
            logger.info(
                "Replayed activity upload returned the original job",
                extra=core_logger.context(user_id=token_user_id, job_id=replayed.id),
            )
            return replayed

    staged_key = upload_entry.store_received_upload(received, storage)
    job_id = str(uuid.uuid4())

    try:
        ingestion_crud.create_ingestion_job(
            job_id,
            token_user_id,
            activity_ingestion_schema.IngestionJobKind.UPLOAD,
            db,
            # ``file.filename`` is non-None here: staging rejects a missing one.
            filename=str(file.filename),
            staged_key=staged_key,
            idempotency_key=idempotency_key,
            request_fingerprint=received.fingerprint,
        )
        if core_config.settings.JOBS_ENABLED:
            # Staged in the transactional outbox on this session and committed
            # with the job row, then relayed into a retryable processing_jobs
            # row. A staging failure propagates, so the caller gets a 500 rather
            # than a 202 for an upload that was never queued.
            ingestion_event_publishers.publish_file_uploaded(job_id, token_user_id, db, commit=db.commit)
        else:
            db.commit()
    except IntegrityError:
        # Two requests carrying the same key raced past the check above. The
        # constraint is what actually decides; the loser reports the winner's job.
        upload_entry.discard_staged_upload(staged_key, storage)
        db.rollback()
        prior = (
            ingestion_crud.get_job_for_idempotency(
                idempotency_key, token_user_id, activity_ingestion_schema.IngestionJobKind.UPLOAD, db
            )
            if idempotency_key
            else None
        )
        if prior is None:
            raise
        replayed, fingerprint = prior
        if fingerprint != received.fingerprint:
            raise core_exceptions.ConflictError("This Idempotency-Key was already used for a different file") from None
        return replayed
    except Exception:
        # The row is gone (or never committed), so nothing will consume the
        # staged bytes.
        upload_entry.discard_staged_upload(staged_key, storage)
        raise

    if not core_config.settings.JOBS_ENABLED:
        # No durable worker to drain the queue; fall back to the background pool
        # so the parse still leaves the request threadpool alone. In-flight work
        # is lost on restart, which is exactly the reliability gap durable jobs
        # close.
        activity_ingestion_background.submit_upload(job_id, storage)

    logger.info(
        "Activity upload accepted for background import",
        extra=core_logger.context(user_id=token_user_id, job_id=job_id),
    )
    return ingestion_crud.get_ingestion_job(job_id, token_user_id, db) or _pending_view(
        job_id, activity_ingestion_schema.IngestionJobKind.UPLOAD, str(file.filename)
    )


def _pending_view(
    job_id: str,
    kind: activity_ingestion_schema.IngestionJobKind,
    filename: str | None = None,
) -> activity_ingestion_schema.ActivityIngestionJob:
    """Build the pending response when the row cannot be re-read.

    Only reachable if the job completed and was pruned between the commit and
    the read-back; returning the handle the caller needs is better than a 500.

    Args:
        job_id: The accepted job identifier.
        kind: Whether this job imports an upload or syncs from providers.
        filename: Original client filename, for an upload.

    Returns:
        A pending view of the job.
    """
    now = datetime.now(UTC)
    return activity_ingestion_schema.ActivityIngestionJob(
        id=job_id,
        kind=kind,
        filename=filename,
        status=activity_ingestion_schema.IngestionJobStatus.PENDING,
        created_at=now,
        updated_at=now,
    )


def get_job(
    job_id: str,
    token_user_id: int,
    db: Session,
) -> activity_ingestion_schema.ActivityIngestionJob:
    """Return one of the caller's ingestion jobs.

    Args:
        job_id: The job identifier returned by the upload or refresh route.
        token_user_id: The authenticated caller.
        db: Database session.

    Returns:
        The ingestion job.

    Raises:
        NotFoundError: When no such job belongs to the caller. A job owned by
            someone else is reported as missing rather than forbidden, so the
            endpoint does not confirm that an id exists.
    """
    job = ingestion_crud.get_ingestion_job(job_id, token_user_id, db)
    if job is None:
        logger.debug(
            "Ingestion job read resolved to nothing for this caller",
            extra=core_logger.context(job_id=job_id, user_id=token_user_id),
        )
        raise core_exceptions.NotFoundError("Upload job not found")
    return job


def accept_refresh(
    token_user_id: int,
    db: Session,
    storage: platform_providers.StorageProvider,
) -> activity_ingestion_schema.ActivityIngestionJob:
    """Queue a provider refresh for background execution.

    The route used to be the one ``async def`` in activities, awaiting the
    Strava and Garmin clients directly. That made every synchronous call on
    those paths — the provider integration lookups, the per-activity dedup
    reads — run on the event loop, where they stall every other request in the
    process rather than occupying one worker thread. Moving the work to a job
    removes the question entirely: nothing on this path touches the loop.

    Args:
        token_user_id: Authenticated user ID.
        db: Database session.
        storage: The blob-storage provider handed to the in-process fallback.

    Returns:
        The accepted refresh job, in the pending state.
    """
    job_id = str(uuid.uuid4())

    ingestion_crud.create_ingestion_job(
        job_id,
        token_user_id,
        activity_ingestion_schema.IngestionJobKind.REFRESH,
        db,
    )
    if core_config.settings.JOBS_ENABLED:
        ingestion_event_publishers.publish_refresh_requested(job_id, token_user_id, db, commit=db.commit)
    else:
        db.commit()
        activity_ingestion_background.submit_refresh(job_id, storage)

    logger.info(
        "Provider refresh accepted for background sync",
        extra=core_logger.context(user_id=token_user_id, job_id=job_id),
    )
    return ingestion_crud.get_ingestion_job(job_id, token_user_id, db) or _pending_view(
        job_id, activity_ingestion_schema.IngestionJobKind.REFRESH
    )


def run_refresh_job(job_id: str, storage: platform_providers.StorageProvider) -> None:
    """Pull the last 24h from the linked providers, recording the outcome.

    The job body for both executors. The provider helpers are still ``async``
    (they offload their blocking HTTP clients with ``asyncio.to_thread``), so
    they are driven here by :func:`asyncio.run` on a loop private to this worker
    thread. That is what keeps their synchronous database calls off the main
    loop — the whole point of moving this off the request.

    Args:
        job_id: The ``activity_ingestion_jobs`` row to process.
        storage: The blob-storage provider, for discarding a staged upload.

    Returns:
        None.

    Raises:
        Exception: Whatever the provider sync raised, so the caller can retry.
    """
    with core_database.SessionLocal() as db:
        owner = ingestion_crud.get_job_owner(job_id, db)
        if owner is None:
            logger.info("Skipping an unknown refresh job", extra=core_logger.context(job_id=job_id))
            return
        ingestion_crud.mark_processing(job_id, db)
        db.commit()

    with core_database.SessionLocal() as db:
        try:
            activities = asyncio.run(refresh_entry.sync_linked_providers(owner, db))
        except Exception:
            if not core_config.settings.JOBS_ENABLED:
                # Nothing will retry, so leave the caller a terminal state
                # instead of a job stuck at "processing".
                fail_ingestion_job(
                    job_id, activity_ingestion_schema.IngestionJobErrorCode.PROVIDER_UNAVAILABLE, storage
                )
            raise

        activity_ids = [activity.id for activity in activities if activity.id is not None]
        ingestion_crud.mark_completed(job_id, activity_ids, db)
        db.commit()
        logger.info(
            "Refresh job synced provider activities",
            extra=core_logger.context(user_id=owner, job_id=job_id, activity_count=len(activity_ids)),
        )


def run_upload_job(job_id: str, storage: platform_providers.StorageProvider) -> None:
    """Import a staged upload, recording the outcome on the job row.

    The job body for both executors. Opens its own session because neither the
    durable worker nor the background pool has a request-scoped one.

    Failure handling splits on whether another attempt could plausibly succeed.
    A rejected file (bad format, unreadable payload) fails identically every
    time, so it is recorded as terminal immediately rather than burning five
    attempts before telling the user. A server-side fault is left to propagate
    so the durable runner retries it with backoff, and the staged blob is kept
    so that retry has something to read \u2014 unless nothing will retry, which is
    the case when durable jobs are off and the fallback pool runs each job once.

    Args:
        job_id: The ``activity_upload_jobs`` row to process.
        storage: The blob-storage provider the upload was staged in.

    Returns:
        None.

    Raises:
        Exception: Whatever the import raised, so the caller can retry.
    """
    with core_database.SessionLocal() as db:
        work_item = ingestion_crud.get_job_work_item(job_id, db)
        if work_item is None:
            # Already consumed — a retry after the import succeeded, or a job
            # whose row was pruned. Nothing to do, and re-running would be wrong.
            logger.info(
                "Skipping an upload job with no staged upload",
                extra=core_logger.context(job_id=job_id),
            )
            return
        user_id, staged_key = work_item
        ingestion_crud.mark_processing(job_id, db)
        db.commit()

    with core_database.SessionLocal() as db:
        try:
            created = upload_entry.process_staged_upload(user_id, staged_key, db, storage)
        except (
            core_exceptions.UnsupportedFormatError,
            core_exceptions.InvalidInputError,
            HTTPException,
        ) as err:
            # The file itself is the problem, so every retry reaches the same
            # verdict: record it now and drop the blob nothing will read again.
            fail_ingestion_job(job_id, _error_code_for(err), storage, staged_key)
            raise
        except Exception:
            # Server-side, possibly transient. Keep the blob for the retry, and
            # only give up here when there is no retry to wait for.
            if not core_config.settings.JOBS_ENABLED:
                fail_ingestion_job(
                    job_id,
                    activity_ingestion_schema.IngestionJobErrorCode.PROCESSING_FAILED,
                    storage,
                    staged_key,
                )
            raise

        # ``Activity.id`` is typed optional on the read model even though a
        # persisted activity always has one, so filter rather than assert: an
        # id-less entry would only mean the client cannot refresh that one row.
        activity_ids = [activity.id for activity in created or [] if activity.id is not None]
        if not activity_ids:
            ingestion_crud.mark_failed(
                job_id,
                activity_ingestion_schema.IngestionJobErrorCode.NO_ACTIVITIES_FOUND,
                db,
            )
            db.commit()
            logger.info(
                "Upload job produced no activities",
                extra=core_logger.context(user_id=user_id, job_id=job_id),
            )
            return
        ingestion_crud.mark_completed(job_id, activity_ids, db)
        db.commit()
        logger.info(
            "Upload job imported activities",
            extra=core_logger.context(user_id=user_id, job_id=job_id, activity_count=len(activity_ids)),
        )


def fail_ingestion_job(
    job_id: str,
    error_code: activity_ingestion_schema.IngestionJobErrorCode,
    storage: platform_providers.StorageProvider,
    staged_key: str | None = None,
) -> None:
    """Record a terminal failure on a job and drop its staged upload.

    Separate session because the caller's may be in a failed transaction.

    Args:
        job_id: The job identifier.
        error_code: Sanitized failure reason.
        storage: The blob-storage provider the staged upload lives in.
        staged_key: Storage key to discard, when the caller already knows it.
            Omitted by the dead-letter path, which reads it from the row.

    Returns:
        None.
    """
    try:
        with core_database.SessionLocal() as db:
            if staged_key is None:
                work_item = ingestion_crud.get_job_work_item(job_id, db)
                staged_key = work_item[1] if work_item else None
            ingestion_crud.mark_failed(job_id, error_code, db)
            db.commit()
    except Exception as err:
        # Never mask the original failure with a bookkeeping one.
        logger.error(
            "Could not mark an upload job failed",
            exc_info=err,
            extra=core_logger.context(job_id=job_id),
        )

    # After the row is terminal, so a crash in between leaves a readable blob
    # rather than a job pointing at bytes that are gone.
    if staged_key is not None:
        upload_entry.discard_staged_upload(staged_key, storage)


# Single-runner lock name: the deletes are idempotent, but the lock keeps the
# work from being duplicated across replicas.
_PRUNE_LOCK_NAME = "activity_ingestion_jobs_prune"


def prune_expired_ingestion_jobs(
    lock: platform_providers.LockProvider,
    clock: platform_providers.ClockProvider,
) -> None:
    """Prune finished upload jobs older than the durable-job retention window.

    Mirrors :func:`jasil.retention.prune_expired_records`: same schedule, same
    ``JOBS_RETENTION_DAYS`` window, same platform lock so only one replica does
    the work. It cannot live in that module because ``activity_upload_jobs`` is
    a domain table and the substrate must not import a domain module.

    The window is shared rather than given its own setting because these rows
    are job history with the same lifecycle as ``processing_jobs``: an operator
    asking to keep job history for N days means all of it.

    Only terminal rows are removed. Pending and processing jobs are in-flight
    work whose owner is still polling, and deleting one would strand both the
    poller and the staged blob.

    Args:
        lock: The platform lock provider.
        clock: The platform clock provider.

    Returns:
        None.
    """
    retention_days = core_config.settings.JOBS_RETENTION_DAYS
    if retention_days <= 0:
        return

    with lock.try_acquire(_PRUNE_LOCK_NAME) as acquired:
        if not acquired:
            logger.debug("Ingestion job prune: another replica holds the lock; skipping")
            return
        cutoff = clock.now() - timedelta(days=retention_days)
        with core_database.SessionLocal() as db:
            deleted = ingestion_crud.delete_jobs_before(cutoff, db)
            db.commit()

    if deleted:
        logger.info(
            "Ingestion job prune: deleted finished upload job rows",
            extra=core_logger.context(deleted=deleted, retention_days=retention_days),
        )
    else:
        logger.debug("Ingestion job prune: nothing to delete")


def _warn_about_unowned_files(user_id: int) -> None:
    """Warn when importable files sit in the shared root instead of a user directory.

    Bulk import used to scan the shared root, so an existing install can have
    files there that will now be skipped. They are not imported on a guess about
    who owns them — that is the bug this isolation removes — but the operator is
    told exactly where to move them.

    Args:
        user_id: The user whose import was triggered, used to name the target.

    Returns:
        None.
    """
    try:
        stranded = [
            name
            for name in os.listdir(core_config.FILES_BULK_IMPORT_DIR)
            if os.path.isfile(os.path.join(core_config.FILES_BULK_IMPORT_DIR, name))
            and os.path.splitext(name)[1].lower() in core_config.SUPPORTED_FILE_FORMATS
        ]
    except OSError:
        return

    if stranded:
        logger.warning(
            "Skipping bulk-import files in the shared root: they are not attributed to any user",
            extra=core_logger.context(
                console=True,
                user_id=user_id,
                file_count=len(stranded),
                expected_dir=core_config.bulk_import_dir_for(user_id),
            ),
        )


def _collect_importable_files(user_id: int, bulk_import_dir: str) -> list[str]:
    """Return the paths in a user's drop directory that are safe to import.

    A file is skipped (logged, not raised) when it resolves outside the drop
    directory, its extension is unsupported, or its contents fail the signature
    check — one bad file must not stop the import of every other one.

    Args:
        user_id: The owner whose directory is being scanned.
        bulk_import_dir: The user's bulk-import directory.

    Returns:
        Absolute paths of the files that passed validation.
    """
    files_to_process: list[str] = []
    base_dir = os.path.realpath(bulk_import_dir)
    for filename in os.listdir(bulk_import_dir):
        file_path = os.path.join(bulk_import_dir, filename)
        file_extension = os.path.splitext(file_path)[1].lower()

        if file_extension not in core_config.SUPPORTED_FILE_FORMATS:
            logger.info(
                "Skipping a bulk-import file with an unsupported extension",
                extra=core_logger.context(
                    console=True,
                    user_id=user_id,
                    file=os.path.basename(file_path),
                    file_extension=file_extension,
                    supported_extensions=list(core_config.SUPPORTED_FILE_FORMATS),
                ),
            )
            continue

        # Everything downstream opens this path (validation, then staging), so a
        # symlink dropped here would import whatever it points at, anywhere on
        # the server's filesystem. Only entries that resolve to themselves inside
        # the user's own directory are imported.
        if os.path.realpath(file_path) != os.path.join(base_dir, filename):
            logger.warning(
                "Skipping a bulk-import entry that does not resolve inside the user's directory",
                extra=core_logger.context(console=True, user_id=user_id, file=filename),
            )
            continue

        if not os.path.isfile(file_path):
            continue

        try:
            validate_kind = (
                core_file_uploads.UploadKind.GZIP if file_extension == ".gz" else core_file_uploads.UploadKind.ACTIVITY
            )
            core_file_uploads.validate_local_file_sync(file_path, kind=validate_kind)
        except HTTPException as err:
            # The shared upload validator still signals rejection with an
            # HTTPException; a rejected file is skipped, never propagated.
            logger.warning(
                "Skipping a bulk-import file that failed validation",
                extra=core_logger.context(console=True, file=os.path.basename(file_path), reason=err.detail),
            )
            continue

        files_to_process.append(file_path)
        logger.info(
            "Queuing a bulk-import file for processing",
            extra=core_logger.context(console=True, user_id=user_id, file=os.path.basename(file_path)),
        )

    return files_to_process


def start_bulk_import(
    user_id: int,
    db: Session,
    storage: platform_providers.StorageProvider,
) -> list[activity_ingestion_schema.ActivityIngestionJob]:
    """Queue every importable file in a user's drop directory.

    Each user drops files into their own directory; scanning the shared root
    would import whatever anyone else left there and attribute it to this caller.

    One :class:`~modules.activities.activity_ingestion.schema.ActivityIngestionJob`
    is created per file and returned, so the caller polls
    ``GET /activities/ingestion-jobs/{job_id}`` for each one exactly as it does
    for a single upload. One handle per file rather than one per batch because
    the fan-out below is already per file: each is retried, dead-lettered and
    completed independently, so a batch-level status could only report an
    average of outcomes that are not shared.

    When durable jobs are enabled, one durable job per file is staged in the
    transactional outbox on this session and committed once, then the relay fans
    them into retryable, dead-letterable ``processing_jobs`` rows drained by the
    in-process worker (local) or the worker fleet (distributed) — a crash
    mid-import no longer drops in-flight files, and a failing file retries then
    dead-letters (moved to the import-error dir) instead of vanishing on the
    first error. A staging failure propagates, so the caller is told the files
    were never queued rather than being handed a 202. With durable jobs off
    there is no worker to drain the queue, so the background thread pool owned by
    :mod:`~modules.activities.activity_ingestion.background` runs them instead.

    Args:
        user_id: The owner whose directory to import.
        db: Database session, used to stage the durable jobs.
        storage: The blob-storage provider dropped files are staged in.

    Returns:
        The accepted jobs, one per queued file, in the pending state.

    Raises:
        ProcessingError: When the directory cannot be read or the jobs cannot be
            queued.
    """
    import_time = datetime.now(UTC).isoformat()
    logger.info(
        "Bulk import initiated",
        extra=core_logger.context(console=True, user_id=user_id, import_time=import_time),
    )

    try:
        bulk_import_dir = core_config.bulk_import_dir_for(user_id)
        os.makedirs(bulk_import_dir, exist_ok=True)
        _warn_about_unowned_files(user_id)

        files_to_process = _collect_importable_files(user_id, bulk_import_dir)

        # Created before either executor is chosen so the handle the caller gets
        # back is the same row both paths transition, and the pending state is
        # visible from the moment the 202 is returned.
        jobs = [
            (
                ingestion_crud.create_ingestion_job(
                    str(uuid.uuid4()),
                    user_id,
                    activity_ingestion_schema.IngestionJobKind.BULK_IMPORT,
                    db,
                    filename=os.path.basename(file_path),
                ),
                file_path,
            )
            for file_path in files_to_process
        ]

        if core_config.settings.JOBS_ENABLED:
            _enqueue_bulk_import_files(
                [(job.id, file_path) for job, file_path in jobs], user_id, import_time, db, storage
            )
        else:
            db.commit()
            activity_ingestion_background.submit_bulk_import(
                user_id, [(job.id, file_path) for job, file_path in jobs], import_time, storage
            )
    except (OSError, RuntimeError, SQLAlchemyError) as err:
        logger.error(
            "Error starting the bulk import",
            exc_info=err,
            extra=core_logger.context(user_id=user_id),
        )
        raise core_exceptions.ProcessingError() from err

    logger.info(
        "Bulk import queued; processing continues in the background",
        extra=core_logger.context(console=True, user_id=user_id, file_count=len(jobs)),
    )
    return [job for job, _ in jobs]


def _enqueue_bulk_import_files(
    queued_files: list[tuple[str, str]],
    user_id: int,
    import_initiated_time: str,
    db: Session,
    storage: platform_providers.StorageProvider,
) -> None:
    """Stage each dropped file and enqueue one durable job per file, in one transaction.

    Unlike every other publish in the activities domain, the event **is** the
    work rather than a notification about work already done: nothing else records
    the intent to import the file, and the subscriber is deliberately
    reconciliation-net exempt (a dead-lettered file is recovered by re-adding it
    to the bulk-import directory, not by a sweeper). A swallowed publish failure
    would therefore mean the file is silently never imported while the route still
    answers 202, so a staging failure propagates.

    Staging the whole batch in one transaction also replaces a
    one-commit-per-file loop, so enqueuing a large export is a single commit
    rather than thousands inside one request.

    Args:
        queued_files: ``(ingestion job id, absolute file path)`` per queued file.
            The job rows were created on ``db``, so they commit with the outbox
            rows.
        user_id: ID of the user performing the import.
        import_initiated_time: ISO timestamp of when the bulk import was initiated.
        db: The request's database session (the outbox rows are staged on it).
        storage: The blob-storage provider the files are staged in.

    Returns:
        None.

    Raises:
        Exception: Propagated from the outbox staging so the caller can fail the
            request instead of reporting a false success.
    """
    # Staged here, in the request thread, because this is the only place
    # guaranteed to see the dropped file. A worker that claims the job may be on
    # another node entirely.
    staged: list[tuple[str, str, str]] = []
    try:
        for job_id, file_path in queued_files:
            staged.append((job_id, staging.stage_file(user_id, file_path, storage), file_path))

        ingestion_event_publishers.publish_bulk_import_files(
            [(job_id, key, os.path.basename(file_path)) for job_id, key, file_path in staged],
            user_id,
            import_initiated_time,
            db,
            commit=db.commit,
        )
    except Exception:
        # Nothing will import these blobs, and the caller gets a 500. Drop them
        # and leave the dropped files alone so the user can simply retry.
        staging.unstage([key for _, key, _ in staged], storage)
        raise

    # Only now are the jobs durable, so consuming the originals is safe.
    staging.settle([(key, file_path) for _, key, file_path in staged], user_id)
    logger.info(
        "Bulk import: enqueued durable file jobs",
        extra=core_logger.context(user_id=user_id, file_count=len(queued_files)),
    )


def process_all_files_sync(
    user_id: int,
    queued_files: list[tuple[str, str]],
    import_initiated_time: str,
    storage: platform_providers.StorageProvider,
) -> None:
    """Process all bulk-import files sequentially in a single thread.

    The fallback executor used when durable jobs are disabled. It moves the same
    ``activity_ingestion_jobs`` rows the durable handler does, so the handles
    ``POST /activities/bulk-import`` returned report progress identically
    whichever executor ran the import.

    Args:
        user_id: User ID.
        queued_files: ``(ingestion job id, file path)`` per file to process.
        import_initiated_time: ISO timestamp of when the bulk import was initiated.
        storage: The blob-storage provider the source files are retained in.
    """
    db = next(core_database.get_db())
    try:
        total_files = len(queued_files)
        for idx, (job_id, file_path) in enumerate(queued_files, 1):
            logger.info(
                "Processing bulk-import file",
                extra=core_logger.context(
                    console=True,
                    file=Path(file_path).name,
                    index=idx,
                    total_files=total_files,
                    user_id=user_id,
                ),
            )
            ingestion_crud.mark_processing(job_id, db)
            db.commit()
            activities = bulk_entry.store_activity_file(
                user_id,
                file_path,
                db,
                storage,
                source=ingestion_contracts.BulkImportSource(
                    import_initiated_time=import_initiated_time, user_id=user_id
                ),
            )
            # ``store_activity_file`` swallows so the batch continues, so ``None``
            # is the only signal that this file failed. Nothing retries here, so
            # the state recorded is terminal either way.
            if activities is None:
                ingestion_crud.mark_failed(
                    job_id, activity_ingestion_schema.IngestionJobErrorCode.PROCESSING_FAILED, db
                )
            else:
                ingestion_crud.mark_completed(
                    job_id, [activity.id for activity in activities if activity.id is not None], db
                )
            db.commit()

        logger.info(
            "Bulk import completed",
            extra=core_logger.context(console=True, total_files=total_files, user_id=user_id),
        )
    finally:
        db.close()


def mark_bulk_import_job_processing(job_id: str | None) -> None:
    """Record that a durable bulk-import file has started.

    Args:
        job_id: The ``activity_ingestion_jobs`` row, or ``None`` for an event
            staged before per-file handles existed.

    Returns:
        None.
    """
    if job_id is None:
        return
    with core_database.SessionLocal() as db:
        ingestion_crud.mark_processing(job_id, db)
        db.commit()


def mark_bulk_import_job_completed(job_id: str | None, activity_ids: list[int]) -> None:
    """Record that a durable bulk-import file produced its activities.

    Args:
        job_id: The ``activity_ingestion_jobs`` row, or ``None`` for a handle-less event.
        activity_ids: IDs of the activities the file produced.

    Returns:
        None.
    """
    if job_id is None:
        return
    with core_database.SessionLocal() as db:
        ingestion_crud.mark_completed(job_id, activity_ids, db)
        db.commit()


def mark_bulk_import_job_failed(job_id: str | None) -> None:
    """Record that a durable bulk-import file exhausted its attempts.

    Args:
        job_id: The ``activity_ingestion_jobs`` row, or ``None`` for a handle-less event.

    Returns:
        None.
    """
    if job_id is None:
        return
    with core_database.SessionLocal() as db:
        ingestion_crud.mark_failed(job_id, activity_ingestion_schema.IngestionJobErrorCode.PROCESSING_FAILED, db)
        db.commit()
