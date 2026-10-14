"""Entry point for a provider refresh initiated through the API.

Fans a refresh out across every provider registered on
:mod:`~modules.activities.activity_ingestion.provider_registry`. It names no
provider: doing so is what made activities and Strava/Garmin mutually dependent,
since both providers import back into the ingestion seam.

Still ``async`` because the provider adapters are: they wrap blocking HTTP
clients in ``asyncio.to_thread``. The caller drives this with ``asyncio.run`` on
a worker thread, so none of it touches the main event loop.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

import core.logger as core_logger
import modules.activities.activity.schema as activity_schema
import modules.activities.activity_ingestion.provider_registry as activity_ingestion_provider_registry

logger = core_logger.get_logger(__name__)

# How far back a refresh looks.
_REFRESH_WINDOW = timedelta(days=1)


async def sync_linked_providers(user_id: int, db: Session) -> list[activity_schema.ActivityRead]:
    """Fetch the recent window of activities from every registered provider.

    A provider that is not linked returns nothing rather than failing, so one
    unlinked integration does not fail the whole refresh. A provider that raises
    is logged and skipped for the same reason.

    Args:
        user_id: The user whose providers to sync.
        db: Database session.

    Returns:
        The activities the providers produced, in registration order.
    """
    window_end = datetime.now(UTC)
    window_start = window_end - _REFRESH_WINDOW

    providers = activity_ingestion_provider_registry.registered()
    if not providers:
        logger.debug(
            "Refresh requested with no providers registered",
            extra=core_logger.context(user_id=user_id),
        )

    activities: list[activity_schema.ActivityRead] = []
    for provider in providers:
        try:
            produced = await provider.fetch_window(user_id, window_start, window_end, db)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            logger.error(
                "Provider refresh failed; continuing with the remaining providers",
                exc_info=err,
                extra=core_logger.context(user_id=user_id, provider=provider.name),
            )
            continue
        activities.extend(activity for activity in (produced or []) if activity is not None)

    return activities
