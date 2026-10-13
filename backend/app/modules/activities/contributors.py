"""Typed contribution contracts for activity ingestion and profile transfer."""

from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.orm import Session

import modules.activities.activity.schema as activity_schema


class PersistActivityComponent(Protocol):
    """Stage one parsed component for a newly created activity.

    The caller owns the transaction: ingestion commits the activity, its
    components and the outbox row together.
    """

    def __call__(
        self,
        data: Any,
        activity: activity_schema.Activity,
        db: Session,
    ) -> None:
        """Stage the component for a newly created activity.

        Args:
            data: The parsed component payload.
            activity: The newly created activity.
            db: Database session.

        Returns:
            None.
        """


class PersistFileComponent(Protocol):
    """Persist one file-scoped parsed component."""

    def __call__(self, data: Any, db: Session) -> None:
        """Persist the file-scoped component.

        Args:
            data: The parsed component payload.
            db: Database session.

        Returns:
            None.
        """


class ExportActivityRecords(Protocol):
    """Export profile records scoped to activity identifiers."""

    def __call__(self, activity_ids: list[int], db: Session) -> list[Any]:
        """Return the records for the given activities.

        Args:
            activity_ids: IDs of the activities to export.
            db: Database session.

        Returns:
            The records to export.
        """


class RestoreActivityRecords(Protocol):
    """Restore profile records for one remapped activity."""

    def __call__(
        self,
        records: list[dict[str, Any]],
        original_activity_id: int,
        new_activity: activity_schema.Activity,
        db: Session,
    ) -> int:
        """Restore the records onto a remapped activity.

        Args:
            records: Profile records to restore.
            original_activity_id: The activity's ID in the exported archive.
            new_activity: The activity the records are restored onto.
            db: Database session.

        Returns:
            The number of records restored.
        """


class ExportGlobalRecords(Protocol):
    """Export file-global profile records."""

    def __call__(self, db: Session) -> list[Any]:
        """Return the file-global records.

        Args:
            db: Database session.

        Returns:
            The records to export.
        """


class RestoreGlobalRecords(Protocol):
    """Restore file-global profile records."""

    def __call__(self, records: list[dict[str, Any]], db: Session) -> int:
        """Restore the file-global records.

        Args:
            records: Profile records to restore.
            db: Database session.

        Returns:
            The number of records restored.
        """


class ThumbnailUrlResolver(Protocol):
    """Resolve an activity's stored thumbnail key to a servable URL."""

    def __call__(self, key: str | None, activity_id: int) -> str | None:
        """Return the servable URL for a thumbnail key.

        Args:
            key: The stored thumbnail key, or None.
            activity_id: ID of the activity.

        Returns:
            The URL, or None when there is no thumbnail.
        """


@dataclass(frozen=True, slots=True)
class ActivityIngestionContributor:
    """Package contribution for one parsed activity component."""

    key: str
    persist: PersistActivityComponent


@dataclass(frozen=True, slots=True)
class FileIngestionContributor:
    """Package contribution for one file-scoped parsed component."""

    key: str
    persist: PersistFileComponent


@dataclass(frozen=True, slots=True)
class ProfileActivityContributor:
    """Package contribution for activity-scoped profile JSON records."""

    key: str
    archive_path: str
    count_key: str
    split: bool
    export: ExportActivityRecords
    restore: RestoreActivityRecords


@dataclass(frozen=True, slots=True)
class ProfileGlobalContributor:
    """Package contribution for file-global profile JSON records."""

    key: str
    archive_path: str
    count_key: str
    export: ExportGlobalRecords
    restore: RestoreGlobalRecords
