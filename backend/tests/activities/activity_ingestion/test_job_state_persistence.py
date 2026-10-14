"""Job-state changes survive the session that made them.

The CRUD only stages a transition, and every job opens its own short-lived
session. Closing a session without committing discards the change, which no
mock-based test can see: the failure is a job row that silently stays
``pending`` or ``processing`` forever. These run the real job functions against
a real (SQLite) database and read the row back from a *fresh* session.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from tests._helpers.db import (
    POSTGRES_TEST_URL_ENV,
    create_postgres_session_factory,
    create_sqlite_session_factory,
    drop_postgres_schema,
    ensure_user,
    postgres_test_url,
)

import modules.activities.activity_ingestion.models as ingestion_models
import modules.activities.activity_ingestion.schema as ingestion_schema
import modules.activities.activity_ingestion.service as ingestion_jobs


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def factory(request):
    """The same assertions against SQLite and, when configured, PostgreSQL.

    SQLite shares one connection across sessions, so it can only show that a
    change was committed. PostgreSQL gives each session its own connection and
    transaction, which is what the production job runner has.
    """
    if request.param == "sqlite":
        session_factory = create_sqlite_session_factory()
        try:
            yield session_factory
        finally:
            session_factory.kw["bind"].dispose()
        return

    if postgres_test_url() is None:
        pytest.skip(f"{POSTGRES_TEST_URL_ENV} is not set")
    session_factory = create_postgres_session_factory()
    try:
        yield session_factory
    finally:
        drop_postgres_schema(session_factory)


def _add_job(factory, **overrides) -> None:
    now = datetime.now(UTC)
    values = {
        "id": "job-1",
        "user_id": 7,
        "kind": "upload",
        "status": "pending",
        "staged_key": "abc.gpx",
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    ensure_user(factory, values["user_id"])
    with factory() as db:
        db.add(ingestion_models.ActivityIngestionJob(**values))
        db.commit()


def _read_job(factory, job_id: str = "job-1"):
    """Read through a new session so only committed state is visible."""
    with factory() as db:
        row = db.get(ingestion_models.ActivityIngestionJob, job_id)
        if row is None:
            return None
        return SimpleNamespace(
            status=row.status,
            error_code=row.error_code,
            activity_ids=list(row.activity_ids or []),
            staged_key=row.staged_key,
        )


class TestUploadJob:
    def test_a_successful_import_is_recorded_as_completed(self, factory):
        _add_job(factory)
        created = [SimpleNamespace(id=11), SimpleNamespace(id=12)]
        with (
            patch.object(ingestion_jobs.core_database, "SessionLocal", factory),
            patch.object(
                ingestion_jobs.activity_ingestion_upload_entry_service, "process_staged_upload", return_value=created
            ),
        ):
            ingestion_jobs.run_upload_job("job-1", "storage")

        job = _read_job(factory)
        assert job.status == "completed"
        assert job.activity_ids == [11, 12]
        # Cleared so a retry after success is a no-op rather than a re-import.
        assert job.staged_key is None

    def test_a_file_with_no_activities_is_recorded_as_failed(self, factory):
        _add_job(factory)
        with (
            patch.object(ingestion_jobs.core_database, "SessionLocal", factory),
            patch.object(
                ingestion_jobs.activity_ingestion_upload_entry_service, "process_staged_upload", return_value=[]
            ),
        ):
            ingestion_jobs.run_upload_job("job-1", "storage")

        job = _read_job(factory)
        assert job.status == "failed"
        assert job.error_code == ingestion_schema.IngestionJobErrorCode.NO_ACTIVITIES_FOUND.value

    def test_processing_is_visible_while_the_import_is_still_running(self, factory):
        """``mark_processing`` commits before the (slow) import starts."""
        _add_job(factory)
        seen: list[str] = []

        def _import(*_args, **_kwargs):
            seen.append(_read_job(factory).status)
            return [SimpleNamespace(id=1)]

        with (
            patch.object(ingestion_jobs.core_database, "SessionLocal", factory),
            patch.object(
                ingestion_jobs.activity_ingestion_upload_entry_service, "process_staged_upload", side_effect=_import
            ),
        ):
            ingestion_jobs.run_upload_job("job-1", "storage")

        assert seen == ["processing"]

    def test_a_terminal_failure_is_recorded(self, factory):
        _add_job(factory, status="processing")
        with patch.object(ingestion_jobs.core_database, "SessionLocal", factory):
            ingestion_jobs.fail_ingestion_job(
                "job-1",
                ingestion_schema.IngestionJobErrorCode.PROCESSING_FAILED,
                MagicMock(),
                staged_key="abc.gpx",
            )

        job = _read_job(factory)
        assert job.status == "failed"
        assert job.error_code == ingestion_schema.IngestionJobErrorCode.PROCESSING_FAILED.value


class TestRefreshJob:
    def test_a_refresh_is_recorded_as_completed(self, factory):
        _add_job(factory, kind="refresh", staged_key=None)
        synced = [SimpleNamespace(id=21)]

        async def _sync(*_args, **_kwargs):
            return synced

        with (
            patch.object(ingestion_jobs.core_database, "SessionLocal", factory),
            patch.object(
                ingestion_jobs.activity_ingestion_refresh_entry_service, "sync_linked_providers", side_effect=_sync
            ),
        ):
            ingestion_jobs.run_refresh_job("job-1", "storage")

        job = _read_job(factory)
        assert job.status == "completed"
        assert job.activity_ids == [21]


class TestPrune:
    def test_deleted_rows_stay_deleted(self, factory):
        old = datetime.now(UTC) - timedelta(days=400)
        _add_job(factory, id="old", status="completed", completed_at=old, staged_key=None)
        _add_job(factory, id="fresh", status="pending")

        platform = MagicMock()
        platform.lock.try_acquire.return_value.__enter__.return_value = True
        platform.clock.now.return_value = datetime.now(UTC)
        with (
            patch.object(ingestion_jobs.core_config.settings, "JOBS_RETENTION_DAYS", 90),
            patch.object(ingestion_jobs.core_database, "SessionLocal", factory),
        ):
            ingestion_jobs.prune_expired_ingestion_jobs(platform.lock, platform.clock)

        assert _read_job(factory, "old") is None
        assert _read_job(factory, "fresh") is not None


class TestBulkImportHandle:
    def test_a_transition_is_committed(self, factory):
        _add_job(factory, kind="bulk_import")
        with patch.object(ingestion_jobs.core_database, "SessionLocal", factory):
            ingestion_jobs.mark_bulk_import_job_processing("job-1")

        assert _read_job(factory).status == "processing"

    def test_a_missing_handle_is_a_no_op(self, factory):
        with patch.object(ingestion_jobs.core_database, "SessionLocal", factory):
            ingestion_jobs.mark_bulk_import_job_processing(None)
            ingestion_jobs.mark_bulk_import_job_completed(None, [1])
            ingestion_jobs.mark_bulk_import_job_failed(None)


class TestSynchronousBulkImport:
    def test_each_file_outcome_is_committed(self, factory):
        """The fallback executor reuses one session across files, so each file's
        transition has to be committed before the next one starts."""
        _add_job(factory, id="job-a", kind="bulk_import", staged_key=None)
        _add_job(factory, id="job-b", kind="bulk_import", staged_key=None)
        db = factory()

        with (
            patch.object(ingestion_jobs.core_database, "get_db", return_value=iter([db])),
            patch.object(
                ingestion_jobs.activity_ingestion_bulk_entry_service,
                "store_activity_file",
                side_effect=[[SimpleNamespace(id=31)], None],
            ),
        ):
            ingestion_jobs.process_all_files_sync(
                7, [("job-a", "/a.gpx"), ("job-b", "/b.fit")], "2026-07-21T00:00:00", "storage"
            )

        first, second = _read_job(factory, "job-a"), _read_job(factory, "job-b")
        assert (first.status, first.activity_ids) == ("completed", [31])
        assert second.status == "failed"
        assert second.error_code == ingestion_schema.IngestionJobErrorCode.PROCESSING_FAILED.value
