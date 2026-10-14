"""The seam that lets ingestion pull from a provider without importing one.

Ingestion needs "fetch this user's recent activities from everything they have
linked". Importing ``modules.strava`` and ``modules.garmin`` to answer that would
make the dependency mutual, since both providers import back into ingestion.

The direction is one-way: a provider depends on activities and *registers*
itself here at startup; activities depends on nothing but this registry. Adding a
third provider means adding a module, not editing ingestion.

Registration happens once per process, from the composition root (``main`` for
the API, ``worker`` for the standalone job worker) — the same both-entrypoints
rule used for runtime module composition, and for the same reason: the refresh
job runs wherever it is claimed.
"""

from dataclasses import dataclass

import modules.activities.activity_ingestion.contracts as activity_ingestion_contracts


@dataclass(frozen=True)
class ActivityProvider:
    """A third-party service ingestion can pull recent activities from.

    Attributes:
        name: Stable provider identifier, used for logging and de-duplication.
        fetch_window: Coroutine fetching one time window for one user.
    """

    name: str
    fetch_window: activity_ingestion_contracts.FetchWindow


_providers: dict[str, ActivityProvider] = {}


def register(provider: ActivityProvider) -> None:
    """Register a provider ingestion may pull from.

    Args:
        provider: The provider to register. Re-registering the same name
            replaces the previous entry, so a repeated call (a test, a second
            lifespan) is idempotent rather than duplicating the fetch.

    Raises:
        None.
    """
    _providers[provider.name] = provider


def registered() -> tuple[ActivityProvider, ...]:
    """Return every registered provider, in registration order.

    Args:
        None.

    Returns:
        The registered providers.

    Raises:
        None.
    """
    return tuple(_providers.values())


def clear() -> None:
    """Drop every registration.

    Args:
        None.

    Raises:
        None.
    """
    _providers.clear()
