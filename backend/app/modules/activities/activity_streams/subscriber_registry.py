"""Subscriber registration and reconciliation for activity streams."""

from jasil.jobs.reconciliation import DurableSubscriberNet
from jasil.jobs.registry import JobHandlerRegistry
from jasil.providers import EventBusProvider

import modules.activities.activity_streams.scheduled_jobs as activity_streams_scheduled_jobs
import modules.activities.activity_streams.subscribers as activity_streams_subscribers


def register_bus_subscribers(events: EventBusProvider) -> None:
    """Register stream bus subscribers.

    Args:
        events: The event bus to subscribe on.
    """
    activity_streams_subscribers.register_hr_zone_subscribers(events)


def register_durable_handlers(registry: JobHandlerRegistry) -> None:
    """Register stream durable handlers.

    Args:
        registry: The durable-handler registry to register on.
    """
    activity_streams_subscribers.register_hr_zone_durable_handlers(registry)


DURABLE_SUBSCRIBER_NETS: tuple[DurableSubscriberNet, ...] = (
    DurableSubscriberNet(
        activity_streams_subscribers.HR_ZONE_SUBSCRIBER_ID,
        activity_streams_scheduled_jobs.run_missing_hr_zone_backfill,
    ),
)
