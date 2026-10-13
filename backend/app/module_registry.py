"""Application composition for installed module runtime contributions."""

from collections.abc import Callable
from dataclasses import dataclass

from jasil.jobs.reconciliation import DurableSubscriberNet
from jasil.jobs.registry import JobHandlerRegistry
from jasil.providers import EventBusProvider

import core.scheduler as core_scheduler
import modules.activities.activity_exercise_titles.integration_service as activity_exercise_titles_integration
import modules.activities.activity_file_storage.subscriber_registry as file_storage_subscribers
import modules.activities.activity_geocoding.scheduled_jobs as geocoding_jobs
import modules.activities.activity_geocoding.subscriber_registry as geocoding_subscribers
import modules.activities.activity_ingestion.scheduled_jobs as ingestion_jobs
import modules.activities.activity_ingestion.subscriber_registry as ingestion_subscribers
import modules.activities.activity_laps.integration_service as activity_laps_integration
import modules.activities.activity_media.integration_service as activity_media_integration
import modules.activities.activity_media.subscriber_registry as media_subscribers
import modules.activities.activity_sets.integration_service as activity_sets_integration
import modules.activities.activity_streams.integration_service as activity_streams_integration
import modules.activities.activity_streams.scheduled_jobs as stream_jobs
import modules.activities.activity_streams.subscriber_registry as stream_subscribers
import modules.activities.activity_thumbnail.integration_service as activity_thumbnail_integration
import modules.activities.activity_thumbnail.scheduled_jobs as thumbnail_jobs
import modules.activities.activity_thumbnail.subscriber_registry as thumbnail_subscribers
import modules.activities.activity_workout_steps.integration_service as activity_workout_steps_integration
import modules.activities.contributor_registry as activity_contributor_registry
import modules.notifications.subscriber_registry as notification_subscribers

RegisterBus = Callable[[EventBusProvider], None]
RegisterDurable = Callable[[JobHandlerRegistry], None]
RecurringJobs = Callable[[], tuple[core_scheduler.ScheduledJob, ...]]


@dataclass(frozen=True)
class StartupTask:
    """One best-effort startup task contributed by a module."""

    name: str
    description: str
    func: Callable[[], None]


@dataclass(frozen=True, kw_only=True)
class RuntimeModule:
    """Runtime wiring published by one installed module."""

    name: str
    register_bus: RegisterBus | None = None
    register_durable: RegisterDurable | None = None
    nets: tuple[DurableSubscriberNet, ...] = ()
    recurring_jobs: RecurringJobs | None = None
    startup_tasks: tuple[StartupTask, ...] = ()


MODULES: tuple[RuntimeModule, ...] = (
    RuntimeModule(
        name="activity_thumbnail",
        register_bus=thumbnail_subscribers.register_bus_subscribers,
        register_durable=thumbnail_subscribers.register_durable_handlers,
        nets=thumbnail_subscribers.DURABLE_SUBSCRIBER_NETS,
        recurring_jobs=thumbnail_jobs.recurring_jobs,
        startup_tasks=(
            StartupTask(
                "generate_missing_thumbnails",
                "Scheduling missing activity map thumbnail generation",
                thumbnail_jobs.schedule_missing_thumbnail_generation,
            ),
        ),
    ),
    RuntimeModule(
        name="activity_streams",
        register_bus=stream_subscribers.register_bus_subscribers,
        register_durable=stream_subscribers.register_durable_handlers,
        nets=stream_subscribers.DURABLE_SUBSCRIBER_NETS,
        recurring_jobs=stream_jobs.recurring_jobs,
        startup_tasks=(
            StartupTask(
                "backfill_missing_hr_zones",
                "Scheduling missing HR-zone backfill",
                stream_jobs.schedule_missing_hr_zone_backfill,
            ),
        ),
    ),
    RuntimeModule(
        name="activity_geocoding",
        register_bus=geocoding_subscribers.register_bus_subscribers,
        register_durable=geocoding_subscribers.register_durable_handlers,
        nets=geocoding_subscribers.DURABLE_SUBSCRIBER_NETS,
        recurring_jobs=geocoding_jobs.recurring_jobs,
        startup_tasks=(
            StartupTask(
                "backfill_missing_locations",
                "Scheduling missing activity location backfill",
                geocoding_jobs.schedule_missing_location_backfill,
            ),
        ),
    ),
    RuntimeModule(
        name="activity_file_storage",
        register_bus=file_storage_subscribers.register_bus_subscribers,
        register_durable=file_storage_subscribers.register_durable_handlers,
        nets=file_storage_subscribers.DURABLE_SUBSCRIBER_NETS,
    ),
    RuntimeModule(
        name="activity_media",
        register_bus=media_subscribers.register_bus_subscribers,
        register_durable=media_subscribers.register_durable_handlers,
        nets=media_subscribers.DURABLE_SUBSCRIBER_NETS,
    ),
    RuntimeModule(
        name="activity_ingestion",
        register_durable=ingestion_subscribers.register_durable_handlers,
        nets=ingestion_subscribers.DURABLE_SUBSCRIBER_NETS,
        recurring_jobs=ingestion_jobs.recurring_jobs,
    ),
    RuntimeModule(
        name="notifications",
        register_bus=notification_subscribers.register_all_notification_bus_subscribers,
        register_durable=notification_subscribers.register_all_notification_durable_handlers,
        nets=notification_subscribers.NOTIFICATION_DURABLE_SUBSCRIBER_NETS,
    ),
)


def configure_activity_contributors() -> None:
    """Install activity package contributors from the composition root."""
    activity_contributor_registry.clear()
    for ingestion_contributor in (
        activity_laps_integration.ingestion_contributor(),
        activity_sets_integration.ingestion_contributor(),
        activity_streams_integration.ingestion_contributor(),
        activity_workout_steps_integration.ingestion_contributor(),
    ):
        activity_contributor_registry.register_activity_ingestion(ingestion_contributor)
    activity_contributor_registry.register_file_ingestion(activity_exercise_titles_integration.ingestion_contributor())
    for profile_contributor in (
        activity_laps_integration.profile_contributor(),
        activity_sets_integration.profile_contributor(),
        activity_streams_integration.profile_contributor(),
        activity_workout_steps_integration.profile_contributor(),
        activity_media_integration.profile_contributor(),
    ):
        activity_contributor_registry.register_profile_activity(profile_contributor)
    activity_contributor_registry.register_profile_global(
        activity_exercise_titles_integration.profile_global_contributor()
    )
    activity_contributor_registry.register_thumbnail_url_resolver(activity_thumbnail_integration.thumbnail_url)


def register_bus_subscribers(events: EventBusProvider) -> None:
    """Register bus subscribers from every installed module."""
    for module in MODULES:
        if module.register_bus is not None:
            module.register_bus(events)


def register_durable_handlers(registry: JobHandlerRegistry) -> None:
    """Register durable handlers from every installed module."""
    for module in MODULES:
        if module.register_durable is not None:
            module.register_durable(registry)


def recurring_jobs() -> tuple[core_scheduler.ScheduledJob, ...]:
    """Collect recurring jobs from every installed module."""
    return tuple(job for module in MODULES if module.recurring_jobs is not None for job in module.recurring_jobs())


def startup_tasks() -> tuple[StartupTask, ...]:
    """Collect best-effort startup tasks from every installed module."""
    return tuple(task for module in MODULES for task in module.startup_tasks)
