"""Durable command registration for activity ingestion."""

from jasil.jobs.reconciliation import DurableSubscriberNet
from jasil.jobs.registry import JobHandlerRegistry

import modules.activities.activity_ingestion.subscribers as ingestion_subscribers


def register_durable_handlers(registry: JobHandlerRegistry) -> None:
    """Register ingestion command handlers.

    Args:
        registry: The durable-handler registry to register on.

    Returns:
        None.
    """
    ingestion_subscribers.register_durable_handlers(registry)


DURABLE_SUBSCRIBER_NETS: tuple[DurableSubscriberNet, ...] = (
    DurableSubscriberNet(
        ingestion_subscribers.BULK_IMPORT_FILE_SUBSCRIBER_ID,
        None,
        exempt_reason=(
            "Each bulk-import file is itself a retryable command that dead-letters "
            "after exhausting attempts; there is no derived state to backfill."
        ),
    ),
    DurableSubscriberNet(
        ingestion_subscribers.UPLOADED_FILE_SUBSCRIBER_ID,
        None,
        exempt_reason=(
            "Each upload is a retryable command whose terminal result is recorded "
            "on its ingestion job row; the user retries a failed upload."
        ),
    ),
    DurableSubscriberNet(
        ingestion_subscribers.REFRESH_REQUESTED_SUBSCRIBER_ID,
        None,
        exempt_reason=(
            "A refresh is a command to re-read a provider window, not durable "
            "derived state; periodic provider polling is a separate scheduled job."
        ),
    ),
)
