"""Tests for the activity-geocoding integration surface."""

from unittest.mock import MagicMock, patch

_INT = "modules.activities.activity_geocoding.integration_service"


class TestRunMissingLocationBackfill:
    """The scheduled reconciliation net: lock + batched backfill."""

    @patch(f"{_INT}.logger")
    @patch(f"{_INT}.platform_runtime")
    def test_skips_when_lock_not_acquired(self, mock_runtime, mock_logger):
        from modules.activities.activity_geocoding.integration_service import run_missing_location_backfill

        lock_cm = MagicMock()
        lock_cm.__enter__.return_value = False
        mock_runtime.get_active_platform.return_value.lock.try_acquire.return_value = lock_cm

        run_missing_location_backfill()

        mock_logger.debug.assert_any_call(
            "Geocoding scheduler: another replica holds the backfill lock; skipping",
        )

    @patch(f"{_INT}.core_database")
    @patch(f"{_INT}.activity_geocoding_service")
    @patch(f"{_INT}.platform_runtime")
    def test_runs_backfill_when_acquired(self, mock_runtime, mock_service, _mock_db):
        from modules.activities.activity_geocoding.integration_service import run_missing_location_backfill

        lock_cm = MagicMock()
        lock_cm.__enter__.return_value = True
        mock_runtime.get_active_platform.return_value.lock.try_acquire.return_value = lock_cm
        mock_service.backfill_missing_activity_locations.return_value = 2

        run_missing_location_backfill()

        mock_service.backfill_missing_activity_locations.assert_called_once()
