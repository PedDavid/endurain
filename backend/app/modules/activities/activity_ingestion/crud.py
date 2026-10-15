"""Persistence for activity ingestion jobs.

Pure persistence: staging, parsing and executor choice live in ``service``,
so this module never touches the filesystem and never publishes events.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

import modules.activities.activity_ingestion.constants as activity_ingestion_constants
import modules.activities.activity_ingestion.models as activity_ingestion_models
import modules.activities.activity_ingestion.schema as activity_ingestion_schema
import modules.activities.activity_ingestion.serializers as activity_ingestion_serializers


def create_ingestion_job(
    job_id: str,
    user_id: int,
    kind: activity_ingestion_constants.IngestionJobKind,
    db: Session,
    *,
    filename: str | None = None,
    staged_key: str | None = None,
    idempotency_key: str | None = None,
    request_fingerprint: str | None = None,
) -> activity_ingestion_schema.ActivityIngestionJobRead:
    """Stage an accepted ingestion request in the pending state (not committed).

    Args:
        job_id: Caller-generated job identifier (UUIDv4 string).
        user_id: Owner of the request.
        kind: Whether this job imports an upload or syncs from providers.
        db: Database session.
        filename: Original client filename; uploads only, for display.
        staged_key: Storage key of the staged upload awaiting parsing; uploads
            only.
        idempotency_key: Client-supplied ``Idempotency-Key``, unique per user
            and kind.
        request_fingerprint: SHA-256 of the uploaded bytes, stored so a reused
            key carrying different content can be rejected.

    Returns:
        The created ingestion job.

    Raises:
        IntegrityError: If the user already has a job with this idempotency key.
    """
    now = datetime.now(UTC)
    new_job = activity_ingestion_models.ActivityIngestionJob(
        id=job_id,
        user_id=user_id,
        kind=kind.value,
        filename=filename,
        staged_key=staged_key,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        status=activity_ingestion_constants.IngestionJobStatus.PENDING.value,
        created_at=now,
        updated_at=now,
    )
    db.add(new_job)
    db.flush()
    return activity_ingestion_serializers.to_read_schema(new_job)


def get_ingestion_job(
    job_id: str,
    user_id: int,
    db: Session,
) -> activity_ingestion_schema.ActivityIngestionJobRead | None:
    """Retrieve one ingestion job belonging to a user.

    Always filtered by ``user_id``: a job id is a bearer-ish handle, and
    scoping the read here means no caller can accidentally expose another user's
    job by forgetting the check.

    Args:
        job_id: The job identifier.
        user_id: Owner the job must belong to.
        db: Database session.

    Returns:
        The ingestion job, or None if it does not exist or belongs to someone else.
    """
    stmt = select(activity_ingestion_models.ActivityIngestionJob).where(
        activity_ingestion_models.ActivityIngestionJob.id == job_id,
        activity_ingestion_models.ActivityIngestionJob.user_id == user_id,
    )
    orm_job = db.scalars(stmt).first()
    return activity_ingestion_serializers.to_read_schema(orm_job) if orm_job else None


def get_job_work_item(job_id: str, db: Session) -> tuple[int, str] | None:
    """Read the owner and staged key of a job, for the background executor.

    Not user-scoped, unlike :func:`get_upload_job`: the caller here is the
    worker acting on an event it was handed, not an HTTP client naming a job.
    Returning the stored ``user_id`` is what keeps the parse attributed to the
    uploader even if the event payload were tampered with.

    Args:
        job_id: The job identifier.
        db: Database session.

    Returns:
        A ``(user_id, staged_key)`` pair, or None if the job is unknown or its
        upload was already consumed.
    """
    orm_job = db.get(activity_ingestion_models.ActivityIngestionJob, job_id)
    if orm_job is None or orm_job.staged_key is None:
        return None
    return orm_job.user_id, orm_job.staged_key


def get_job_for_idempotency(
    idempotency_key: str,
    user_id: int,
    kind: activity_ingestion_constants.IngestionJobKind,
    db: Session,
) -> tuple[activity_ingestion_schema.ActivityIngestionJobRead, str | None] | None:
    """Retrieve a user's job by the idempotency key it was accepted with.

    Returns the stored request fingerprint alongside the job: the caller has to
    compare it before treating the request as a replay, because the same key
    carrying different content is a client error rather than a repeat.

    Scoped to the user and kind for the same reason the constraint is: a key is
    only meaningful within the client that generated it, and an unscoped lookup
    would let one caller discover another's job by guessing keys.

    Args:
        idempotency_key: The client-supplied key.
        user_id: Owner the job must belong to.
        kind: The job kind the key was used on.
        db: Database session.

    Returns:
        A ``(job, fingerprint)`` pair, or None if this user has not used that
        key for that kind.
    """
    stmt = select(activity_ingestion_models.ActivityIngestionJob).where(
        activity_ingestion_models.ActivityIngestionJob.idempotency_key == idempotency_key,
        activity_ingestion_models.ActivityIngestionJob.user_id == user_id,
        activity_ingestion_models.ActivityIngestionJob.kind == kind.value,
    )
    orm_job = db.scalars(stmt).first()
    return (activity_ingestion_serializers.to_read_schema(orm_job), orm_job.request_fingerprint) if orm_job else None


def get_job_owner(job_id: str, db: Session) -> int | None:
    """Read the owner of a job, for the background executor.

    Not user-scoped, unlike :func:`get_ingestion_job`: the caller is the worker
    acting on an event it was handed, not an HTTP client naming a job. Reading
    the owner from the row rather than the event payload is what keeps the sync
    attributed to the requester even if the payload were tampered with.

    Args:
        job_id: The job identifier.
        db: Database session.

    Returns:
        The owning user id, or None if the job is unknown.
    """
    orm_job = db.get(activity_ingestion_models.ActivityIngestionJob, job_id)
    return orm_job.user_id if orm_job else None


def mark_processing(job_id: str, db: Session) -> None:
    """Stage a move of a job to the processing state (not committed).

    Args:
        job_id: The job identifier.
        db: Database session.
    """
    orm_job = db.get(activity_ingestion_models.ActivityIngestionJob, job_id)
    if orm_job is None:
        return
    orm_job.status = activity_ingestion_constants.IngestionJobStatus.PROCESSING.value
    orm_job.updated_at = datetime.now(UTC)
    db.flush()


def mark_completed(job_id: str, activity_ids: list[int], db: Session) -> None:
    """Stage a move of a job to the completed state and record what it created.

    Args:
        job_id: The job identifier.
        activity_ids: Ids of the activities the import created.
        db: Database session.
    """
    orm_job = db.get(activity_ingestion_models.ActivityIngestionJob, job_id)
    if orm_job is None:
        return
    now = datetime.now(UTC)
    orm_job.status = activity_ingestion_constants.IngestionJobStatus.COMPLETED.value
    orm_job.activity_ids = activity_ids
    orm_job.error_code = None
    orm_job.staged_key = None
    orm_job.updated_at = now
    orm_job.completed_at = now
    db.flush()


def _fail(
    orm_job: activity_ingestion_models.ActivityIngestionJob,
    error_code: activity_ingestion_constants.IngestionJobErrorCode,
    now: datetime,
) -> None:
    """Move a loaded job row to the failed state."""
    orm_job.status = activity_ingestion_constants.IngestionJobStatus.FAILED.value
    orm_job.error_code = error_code.value
    orm_job.staged_key = None
    orm_job.updated_at = now
    orm_job.completed_at = now


def mark_failed(
    job_id: str,
    error_code: activity_ingestion_constants.IngestionJobErrorCode,
    db: Session,
) -> None:
    """Stage a move of a job to the failed state with a sanitized reason.

    Args:
        job_id: The job identifier.
        error_code: Sanitized failure reason shown to the owner.
        db: Database session.
    """
    orm_job = db.get(activity_ingestion_models.ActivityIngestionJob, job_id)
    if orm_job is None:
        return
    _fail(orm_job, error_code, datetime.now(UTC))
    db.flush()


def fail_jobs_idle_since(
    cutoff: datetime,
    error_code: activity_ingestion_constants.IngestionJobErrorCode,
    db: Session,
) -> list[tuple[str, str | None]]:
    """Stage the failure of non-terminal jobs that have not changed state since a cutoff.

    Args:
        cutoff: Pending or processing jobs last updated before this are failed.
        error_code: Sanitized failure reason shown to the owner.
        db: Database session.

    Returns:
        ``(job id, staged key)`` for each failed job; the key is the staged upload
        the job still held, or ``None``.
    """
    stmt = select(activity_ingestion_models.ActivityIngestionJob).where(
        activity_ingestion_models.ActivityIngestionJob.completed_at.is_(None),
        activity_ingestion_models.ActivityIngestionJob.updated_at < cutoff,
    )
    now = datetime.now(UTC)
    failed: list[tuple[str, str | None]] = []
    for orm_job in db.scalars(stmt).all():
        failed.append((orm_job.id, orm_job.staged_key))
        _fail(orm_job, error_code, now)
    db.flush()
    return failed


def get_referenced_staged_keys(staged_keys: Sequence[str], db: Session) -> set[str]:
    """Return which of the given staged-upload keys a job row still names.

    Args:
        staged_keys: Candidate staged-upload keys.
        db: Database session.

    Returns:
        The subset that some job references.
    """
    if not staged_keys:
        return set()
    stmt = select(activity_ingestion_models.ActivityIngestionJob.staged_key).where(
        activity_ingestion_models.ActivityIngestionJob.staged_key.in_(staged_keys)
    )
    return set(db.scalars(stmt).all())


def delete_jobs_before(cutoff: datetime, db: Session) -> int:
    """Stage the deletion of terminal ingestion jobs older than a cutoff.

    Args:
        cutoff: Jobs that reached a terminal state before this are removed.
        db: Database session.

    Returns:
        Number of rows deleted.
    """
    stmt = select(activity_ingestion_models.ActivityIngestionJob).where(
        activity_ingestion_models.ActivityIngestionJob.completed_at.is_not(None),
        activity_ingestion_models.ActivityIngestionJob.completed_at < cutoff,
    )
    stale = list(db.scalars(stmt).all())
    for orm_job in stale:
        db.delete(orm_job)
    db.flush()
    return len(stale)
