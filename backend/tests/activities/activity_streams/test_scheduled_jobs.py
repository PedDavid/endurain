"""Tests for the activity-streams scheduled jobs."""

from unittest.mock import MagicMock, patch


class TestRunMissingHrZoneBackfill:
    """The scheduled reconciliation net: lock + batched backfill."""

    @patch("modules.activities.activity_streams.scheduled_jobs.logger")
    @patch("modules.activities.activity_streams.scheduled_jobs.platform_runtime")
    def test_skips_when_lock_not_acquired(self, mock_runtime, mock_logger):
        from modules.activities.activity_streams.scheduled_jobs import run_missing_hr_zone_backfill

        lock_cm = MagicMock()
        lock_cm.__enter__.return_value = False
        mock_runtime.get_active_platform.return_value.lock.try_acquire.return_value = lock_cm

        run_missing_hr_zone_backfill()

        mock_logger.debug.assert_any_call(
            "HR-zone scheduler: another replica holds the backfill lock; skipping",
        )

    @patch("modules.activities.activity_streams.scheduled_jobs.logger")
    @patch("modules.activities.activity_streams.scheduled_jobs.core_database")
    @patch("modules.activities.activity_streams.scheduled_jobs.activity_streams_service")
    @patch("modules.activities.activity_streams.scheduled_jobs.platform_runtime")
    def test_runs_backfill_when_acquired(self, mock_runtime, mock_service, mock_db, mock_logger):
        from modules.activities.activity_streams.scheduled_jobs import run_missing_hr_zone_backfill

        lock_cm = MagicMock()
        lock_cm.__enter__.return_value = True
        mock_runtime.get_active_platform.return_value.lock.try_acquire.return_value = lock_cm
        mock_service.backfill_missing_hr_zones.return_value = 3

        run_missing_hr_zone_backfill()

        mock_service.backfill_missing_hr_zones.assert_called_once()
