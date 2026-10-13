"""Tests for the activity thumbnail integration surface."""

from unittest.mock import MagicMock, patch

MODULE = "modules.activities.activity_thumbnail.integration_service"


class TestThumbnailUrl:
    @patch(f"{MODULE}.activity_thumbnail_signing")
    def test_delegates_to_signing(self, mock_signing):
        from modules.activities.activity_thumbnail.integration_service import thumbnail_url

        mock_signing.thumbnail_url.return_value = "/u"

        assert thumbnail_url("k", 7) == "/u"
        mock_signing.thumbnail_url.assert_called_once_with("k", 7)


class TestMaintenanceEntryPoints:
    @patch(f"{MODULE}.activity_thumbnail_service")
    @patch(f"{MODULE}.core_database.SessionLocal")
    @patch(f"{MODULE}.platform_runtime")
    def test_generate_missing_passes_providers_and_session(self, mock_runtime, mock_session, mock_service):
        from modules.activities.activity_thumbnail.integration_service import generate_missing_thumbnails

        db = MagicMock()
        mock_session.return_value.__enter__.return_value = db
        platform = mock_runtime.get_active_platform.return_value

        generate_missing_thumbnails()

        mock_service.generate_missing_activity_thumbnails.assert_called_once_with(platform.storage, platform.lock, db)

    @patch(f"{MODULE}.activity_thumbnail_service")
    @patch(f"{MODULE}.core_database.SessionLocal")
    @patch(f"{MODULE}.platform_runtime")
    def test_regenerate_all_passes_providers_and_session(self, mock_runtime, mock_session, mock_service):
        from modules.activities.activity_thumbnail.integration_service import regenerate_all_thumbnails

        db = MagicMock()
        mock_session.return_value.__enter__.return_value = db
        platform = mock_runtime.get_active_platform.return_value

        regenerate_all_thumbnails()

        mock_service.delete_and_regenerate_all_activity_thumbnails.assert_called_once_with(
            platform.storage, platform.lock, db
        )
