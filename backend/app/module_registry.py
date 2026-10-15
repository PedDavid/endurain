"""Application composition for installed module runtime contributions."""

from collections.abc import Callable
from dataclasses import dataclass

from jasil.jobs.reconciliation import DurableSubscriberNet
from jasil.jobs.registry import JobHandlerRegistry
from jasil.providers import EventBusProvider

import core.scheduler as core_scheduler
import modules.activities.activity_exercise_titles.integration_service as activity_exercise_titles_integration
import modules.activities.activity_file_storage.scheduled_jobs as activity_file_storage_scheduled_jobs
import modules.activities.activity_file_storage.subscriber_registry as activity_file_storage_subscriber_registry
import modules.activities.activity_geocoding.scheduled_jobs as activity_geocoding_scheduled_jobs
import modules.activities.activity_geocoding.subscriber_registry as activity_geocoding_subscriber_registry
import modules.activities.activity_ingestion.scheduled_jobs as activity_ingestion_scheduled_jobs
import modules.activities.activity_ingestion.subscriber_registry as activity_ingestion_subscriber_registry
import modules.activities.activity_laps.integration_service as activity_laps_integration
import modules.activities.activity_media.integration_service as activity_media_integration
import modules.activities.activity_media.scheduled_jobs as activity_media_scheduled_jobs
import modules.activities.activity_media.subscriber_registry as activity_media_subscriber_registry
import modules.activities.activity_sets.integration_service as activity_sets_integration
import modules.activities.activity_streams.integration_service as activity_streams_integration
import modules.activities.activity_thumbnail.integration_service as activity_thumbnail_integration
import modules.activities.activity_thumbnail.scheduled_jobs as activity_thumbnail_scheduled_jobs
import modules.activities.activity_thumbnail.subscriber_registry as activity_thumbnail_subscriber_registry
import modules.activities.activity_workout_steps.integration_service as activity_workout_steps_integration
import modules.activities.contributor_registry as activities_contributor_registry
import modules.notifications.subscriber_registry as notifications_subscriber_registry

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
        register_bus=activity_thumbnail_subscriber_registry.register_bus_subscribers,
        register_durable=activity_thumbnail_subscriber_registry.register_durable_handlers,
        nets=activity_thumbnail_subscriber_registry.DURABLE_SUBSCRIBER_NETS,
        recurring_jobs=activity_thumbnail_scheduled_jobs.recurring_jobs,
        startup_tasks=(
            StartupTask(
                "generate_missing_thumbnails",
                "Scheduling missing activity map thumbnail generation",
                activity_thumbnail_scheduled_jobs.schedule_missing_thumbnail_generation,
            ),
        ),
    ),
    RuntimeModule(
        name="activity_geocoding",
        register_bus=activity_geocoding_subscriber_registry.register_bus_subscribers,
        register_durable=activity_geocoding_subscriber_registry.register_durable_handlers,
        nets=activity_geocoding_subscriber_registry.DURABLE_SUBSCRIBER_NETS,
        recurring_jobs=activity_geocoding_scheduled_jobs.recurring_jobs,
        startup_tasks=(
            StartupTask(
                "backfill_missing_locations",
                "Scheduling missing activity location backfill",
                activity_geocoding_scheduled_jobs.schedule_missing_location_backfill,
            ),
        ),
    ),
    RuntimeModule(
        name="activity_file_storage",
        register_bus=activity_file_storage_subscriber_registry.register_bus_subscribers,
        register_durable=activity_file_storage_subscriber_registry.register_durable_handlers,
        nets=activity_file_storage_subscriber_registry.DURABLE_SUBSCRIBER_NETS,
        recurring_jobs=activity_file_storage_scheduled_jobs.recurring_jobs,
    ),
    RuntimeModule(
        name="activity_media",
        register_bus=activity_media_subscriber_registry.register_bus_subscribers,
        register_durable=activity_media_subscriber_registry.register_durable_handlers,
        nets=activity_media_subscriber_registry.DURABLE_SUBSCRIBER_NETS,
        recurring_jobs=activity_media_scheduled_jobs.recurring_jobs,
    ),
    RuntimeModule(
        name="activity_ingestion",
        register_durable=activity_ingestion_subscriber_registry.register_durable_handlers,
        nets=activity_ingestion_subscriber_registry.DURABLE_SUBSCRIBER_NETS,
        recurring_jobs=activity_ingestion_scheduled_jobs.recurring_jobs,
        startup_tasks=(
            StartupTask(
                "queue_provider_refreshes",
                "Queueing a provider activity refresh for every linked user",
                activity_ingestion_scheduled_jobs.schedule_provider_refresh,
            ),
        ),
    ),
    RuntimeModule(
        name="notifications",
        register_bus=notifications_subscriber_registry.register_all_notification_bus_subscribers,
        register_durable=notifications_subscriber_registry.register_all_notification_durable_handlers,
        nets=notifications_subscriber_registry.NOTIFICATION_DURABLE_SUBSCRIBER_NETS,
    ),
)


def configure_activity_contributors() -> None:
    """Install activity package contributors from the composition root."""
    activities_contributor_registry.clear()
    for ingestion_contributor in (
        activity_laps_integration.ingestion_contributor(),
        activity_sets_integration.ingestion_contributor(),
        activity_streams_integration.ingestion_contributor(),
        activity_workout_steps_integration.ingestion_contributor(),
    ):
        activities_contributor_registry.register_activity_ingestion(ingestion_contributor)
    activities_contributor_registry.register_file_ingestion(
        activity_exercise_titles_integration.ingestion_contributor()
    )
    for profile_contributor in (
        activity_laps_integration.profile_contributor(),
        activity_sets_integration.profile_contributor(),
        activity_streams_integration.profile_contributor(),
        activity_workout_steps_integration.profile_contributor(),
        activity_media_integration.profile_contributor(),
    ):
        activities_contributor_registry.register_profile_activity(profile_contributor)
    activities_contributor_registry.register_profile_global(
        activity_exercise_titles_integration.profile_global_contributor()
    )
    activities_contributor_registry.register_thumbnail_url_resolver(activity_thumbnail_integration.thumbnail_url)


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
