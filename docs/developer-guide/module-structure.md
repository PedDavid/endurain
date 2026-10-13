# Module Structure

This guide defines the structure every backend module follows and the rules that
make each module **replaceable**. A module can move to another distribution, or
be replaced by another implementation, because callers import only surfaces the
module deliberately publishes.

`modules/activities` is a composition namespace containing several independent
modules (`activity`, `activity_laps`, `activity_streams`, and so on).
`modules/followers` is the reference for a module that stays flat. Every other
module, and every new module, is converted to one of these shapes.

## The two units

| Unit | What it is | Example |
| --- | --- | --- |
| **Composition namespace** | Groups related modules and collects their routers, jobs and subscribers. It is not an implementation boundary. | `modules/activities` |
| **Module** | One aggregate or capability and the unit of extraction. | `modules/activities/activity_laps`, `modules/followers` |

A bounded context with one aggregate stays flat (`modules/followers`). Related
activity capabilities share the `modules.activities` namespace, but each child
package remains its own module. They are **peers**, not implementation friends:
`activity_thumbnail` may consume `activity_streams.integration_service`, but it
may not import `activity_streams.service`, CRUD, ORM models, signing, or helpers.

The root `activity` package owns the activity row and parent access decisions. It
does not own child tables merely because they refer to `activities.id`.

## File roles

Filenames are part of the contract: the enforcement rules match on them, so a
file named `crud.py` is treated as persistence wherever it appears. The
corollary is that a misnamed file is *silently exempt* from every rule, which is
why a file's name has to be the truth.

A stem may **qualify** a role and inherits every rule that role carries:
`summary_crud.py` is a `crud`, `summary_router.py` is a `router`. A package that
needs a second persistence module names it `<thing>_crud.py`; it does not invent
a new word. The vocabulary lives once in `tests/_helpers/module_roles.py`.

Because the role is what the rules see, the role has to be the truth:
`tests/architecture/test_module_boundaries.py` asserts that only a `crud`-role
file executes statements against the session, that only `router`/`public_router`
files construct an `APIRouter`, and that only `models` files declare ORM classes.
The vocabulary is deliberately open (`render.py` and `signing.py` are legitimate
private helpers), so the check is that a file's *content* agrees with its name
rather than that its name is on a list. Ending a transaction (`commit`,
`rollback`) is a separate rule, stated under
[Transaction ownership](#transaction-ownership).

| File | Layer | Responsibility |
| --- | --- | --- |
| `models.py` | persistence | SQLAlchemy ORM. The only place a table is declared. |
| `query.py` | persistence | Reusable SQL expression fragments. No session, no I/O. |
| `crud.py` | persistence | The **only** file that opens a `Session` against its own tables. Stages writes (`flush`), never commits or rolls back. Returns schemas/DTOs, never ORM rows. |
| `serializers.py` | persistence | ORM ↔ schema transformation, both directions. Called by `crud` at its edges. |
| `service.py` | application | Decides access, orchestrates, publishes events, owns transaction boundaries. |
| `router.py` / `public_router.py` | transport | Validate, delegate to `service`, return. No domain rule. |
| `dependencies.py` | transport | FastAPI DI (path-param resolution, scope checks). |
| `schema.py` | contract | Pydantic request/response shapes. Serialized to clients. |
| `contracts.py` | contract | Inter-module DTOs and typed seams. Never serialized. |
| `constants.py` | contract | Domain constants and mapping tables. |
| `events.py` | contract | Event-type names and validated payload models. |
| `event_publishers.py` | application | Publishes this package's domain events. |
| `subscribers.py` | application | Handles events. Registered from `subscriber_registry`. |
| `integration_service.py` | **module surface** | The curated set of operations other modules may call. |
| `contributors.py` | **namespace contract** | Typed callbacks installed by app composition for generic ingestion/profile orchestration. |
| `scheduled_jobs.py` | **module surface** | The module's recurring jobs, collected by the composition root. |
| `model_registry.py` | **composition surface** | Declares the package's ORM model module without requiring core to scan domains. |
| `migration_service.py` | **migration-only surface** | Version-pinned operations used by `app/migrations`; never an ordinary domain dependency. |

Anything else (`utils.py`, `signing.py`, `render.py`, …) is package-private by
default. A helper that orchestrates other modules or ends a transaction is not a
helper: it plays the `service` role and is named `<thing>_service.py`, such as
`activity_ingestion/pipeline_service.py`.

## Visibility

Four classes. This is the whole rule.

### 1. Module surface — importable from anywhere

| Surface | Purpose |
| --- | --- |
| `integration_service.py` | Behaviour. Every cross-module call goes through it. |
| `contracts.py` | The data shapes that surface accepts and returns. |
| `schema.py` | Client-facing shapes, when a consumer genuinely needs the wire type. |
| `constants.py` | Stable domain constants. |
| `events.py` | Event names and payloads, so a consumer can subscribe. |
| `dependencies.py` | FastAPI DI, mounted at the HTTP boundary. |
| `router.py` / `public_router.py` | Mounted by `app/api.py` and nothing else. |
| `subscriber_registry.py` | Event wiring, called by `main.py` and `worker.py`. |
| `scheduled_jobs.py` | The module's recurring and one-shot background work, collected by `main.py`. |

Every name on `integration_service.py` is a promise, so it holds only operations
with a real caller **outside its package**. Contributor plumbing
(`_store_laps`, `_list_laps_for_activities`) stays private; the contributor
factory is the surface. A new operation lands there with the caller that needed
it, or stays private until then;
`test_the_surface_holds_only_operations_with_callers` resolves callers by AST and
fails on the difference. A package nothing outside calls (`activity_geocoding`)
has no `integration_service.py` and is listed with its reason in
`_NO_CROSS_MODULE_SURFACE`.

A surface function that only forwards its arguments is not a layer either. When
`integration_service` → `service` → `crud` adds no decision, no access check and
no transaction, the surface goes straight to CRUD, which the matrix below
permits. What belongs in `service` is the *arrangement* of a write (stage,
publish, commit), not the act of forwarding.

`subscribers.py` is deliberately absent from that list. A module with subscribers
publishes `subscriber_registry.py` and the entrypoints import *that*, so
"registered in the API but not the worker" is a diff in one file, and the file
that holds the handlers can be split without touching the entrypoints.

`modules.auth` is the one module whose behavioural surface is not called
`integration_service.py`. It publishes `identity_service.py` (plus
`dependencies.py`), because its boundary is not "what may you call?" but "what
may you learn about the caller?" — a distinction the
[auth boundary guide](auth-boundary.md) states and a dedicated `auth-boundary`
contract enforces structurally. The inconsistency is real and is the cost of
that: `auth` answers "what may I depend on?" with a different filename than
every other module. It is recorded here rather than smoothed over, and no other
module may follow it.

`subscriber_registry.py` is also where a module declares its **reconciliation
nets**. A durable subscriber derives state from an event, and delivery is
at-least-once but never guaranteed — a bus consumer can drop a message, and some
write paths publish no event at all. So each durable subscriber declares a
`jasil.jobs.reconciliation.DurableSubscriberNet`: either the scheduled backfill
that re-derives what the create path missed, or the reason none is needed.
`tests/architecture/test_reconciliation_nets.py` holds every module to it.

The `DurableSubscriberNet` type lives in the platform (`jasil`), not in a module,
so every module can declare nets without importing another bounded context for a
shared word.

A namespace-level file is appropriate only for composition or a genuinely
shared, dependency-free contract. `modules/activities/computation.py` is the
pure metric maths used by parsers and provider adapters; it owns no table and
calls no module. `modules/activities/contributors.py` defines the dependency-free
activity ingestion/profile callback contracts, while `contributor_registry.py`
stores only the contributors explicitly installed by `app/module_registry.py`.
Child packages return contributor objects from `integration_service.py`; package
imports never register themselves as a side effect.

### 2. Composition surface — imported only while assembling the application

`router.py`, `public_router.py`, `dependencies.py`, `subscriber_registry.py`,
`scheduled_jobs.py`, and `model_registry.py` are collected by `api.py`, `main.py`,
`worker.py`, Alembic, or the namespace-level activities registries. Business
modules do not call them.

Every persistence-owning package declares its model module in
`model_registry.py`. `app/model_registry.py` collects converted modules
explicitly. Its filesystem scan exists only for unconverted legacy modules and
shrinks as those modules adopt this structure. `core.database` never discovers
or imports domain models.

### 3. Migration-only surface — imported only by `app/migrations`

`migration_service.py` exposes the exact historical operations a versioned data
migration still needs. It prevents an old migration from reaching arbitrary CRUD,
parser, render, or signing internals while keeping that migration pinned to its
era. Import-linter forbids domain and core code from consuming it.

### 4. Package-private — importable only from inside its own module

`service.py`, `query.py`, `crud.py`, `models.py`, `serializers.py`, `utils.py`,
`signing.py`, `subscribers.py`, `event_publishers.py`, and every unlisted helper.
Sibling activity modules are outside callers for this rule. A derived subsystem
that needs the activity row asks `activity.integration_service`, never
`activity.service` or `activity.crud`.

`__init__.py` holds a docstring and **nothing else**. A re-export facade would
hand out ORM models and CRUD functions under a package path, which is a silent
bypass of every rule above.

## Allowed-import matrix

Read as *row imports column*.

| From ↓ / To → | own `crud` | own `service` | peer `integration_service` | peer internals | peer `schema`/`contracts` | other module |
| --- | --- | --- | --- | --- | --- | --- |
| `router` | ✗ | ✓ | ✗ | ✗ | ✓ | transport dependencies only |
| `service` | ✓ | — | ✓ | ✗ | ✓ | `integration_service` only |
| `crud` | — | ✗ | ✗ | ✗ | ✓ | ✗ |
| `models` | — | ✗ | ✗ | ✗ | ✗ | ✗ |
| `subscribers` | ✗ | ✓ | ✓ | ✗ | ✓ | `integration_service`/events only |
| `integration_service` | ✓ | ✓ | ✓ | ✗ | ✓ | `integration_service` only |
| `app/migrations` | ✗ | ✗ | ✗ | ✗ | ✓ | `migration_service` only |

Three entries deserve the reasoning:

- **`crud` never calls another module.** A `SELECT` that first has to ask another
  bounded context a question is a service-layer decision wearing a persistence
  layer's clothes.
- **`models` do not import other models.** A foreign key names a table with a
   string (`ForeignKey("activities.id")`). Database `ON DELETE` actions own
   lifecycle behavior. Unused bidirectional ORM relationships would force every
   optional package to be imported before any mapper could configure.
- **`integration_service` may read its own module freely.** It *is* the module,
  presenting a narrow face outward.

A face is not a layer, which is the one thing that entry does **not** license. A
write has an arrangement — stage the rows, publish the fact, own the commit that
makes those atomic — and that arrangement belongs to `service`. An
`integration_service` therefore never publishes events or ends a transaction: it
calls its own `service` for any write that decides, and the
`activities-surfaces-delegate-writes` contract forbids it from importing
`event_publishers`. A surface that cannot publish cannot own the commit that has
to be atomic with the publish, so there is one write-orchestration layer, not two.

## Layering inside a module

```
router / public_router  →  service  →  crud  →  query / models
                              ↓
                     event_publishers → events
```

Downward only. `crud` never calls `service`; `service` never touches `models`.

## Transaction ownership

`service` owns the transaction; `crud` only stages. The unit of work is known to
the layer that arranges it, not to the single statement a `crud` function runs.

| Layer | Does | Never does |
| --- | --- | --- |
| `crud` | `add` / `execute`, then `flush()` so constraint violations and row counts surface at the call; `refresh` after the flush when it needs server defaults | `commit()`, `rollback()`, a `commit` parameter |
| `service` | Stages through `crud`, publishes through `event_publishers` (passing `db.commit` so row and outbox event commit together), or calls `db.commit()` itself when no event is involved | — |
| `integration_service` | Calls its own `service` for any write that decides, publishes or commits; stages through its own `crud` only for a [decision-free write](#decision-free-surface-writes) | Commit, roll back, publish |
| entry point (`get_db`, a job, a subscriber) | Rolls back what its caller left open | — |

Two choices in that table are deliberate:

- **No `commit` flag.** A `commit: bool = True` parameter keeps two code paths in
  every writer, and its default is the wrong one: `service` always passed
  `commit=False`, so the `commit=True` branch was exercised only by tests. A
  writer that can end its own transaction also makes "stage, publish, commit"
  depend on each caller remembering to opt out. A writer that cannot, cannot be
  misused.
- **`crud` does not roll back.** A rollback decides the fate of everything the
  session staged, which `crud` does not know. A `crud` that translates an
  `IntegrityError` into a `ConflictError` simply raises: the session is rolled
  back by `@handle_db_errors` for database errors, and otherwise by the
  transaction owner (`core.database.get_db` on a request, the job or subscriber
  that opened the session elsewhere).

### Decision-free surface writes

An `integration_service` function may stage through its own `crud` only when the
write makes no decision: no access check, no invariant beyond payload shape, no
event and no commit. The contributor `persist` and `restore` callbacks are the
case this exists for. The caller (ingestion in `activity`, the profile importer)
owns the unit of work, so a `service` function between them could only forward
and stage, which is a layer that adds nothing.

The moment such a write needs an ownership check, a business rule, an event or a
commit, it moves to `service`. The surface never grows a second write path beside
it.

A failed `commit()` is not translated in `service`. `service` does not wrap its
commit in `try`/`except`, and it does not catch `SQLAlchemyError`. The error
propagates, `core.database.get_db` rolls the session back, and
`core.exceptions.database_error_handler` — registered once with the other
handlers — renders the same opaque 500 problem document a `ProcessingError`
produces. Translating at one boundary keeps the rule in one place instead of one
`try` per service, and keeps the SQL statement and its parameters out of both the
response and the log message (OWASP A09). A service catches a database error only
to make a *domain* decision from it — mapping `IntegrityError` to `ConflictError`
is the precedent — never to re-raise a generic one.

`test_crud_does_not_end_transactions` enforces it: a `crud`-role file in a
converted module may not call `commit` or `rollback`. Files that still do are
listed in `_CRUD_TRANSACTION_DEBT`, and the test fails on an entry that no longer
offends so the register shrinks rather than lingers.

## What a module may depend on

- **`core.*`** — cross-cutting primitives (logging, config, pagination, exceptions,
  timezone, signing, uploads). Always allowed.
- **`jasil.*`** — the platform substrate, an external package. Through its public
  surface only: `jasil.providers` (the capability ports), `jasil.publisher`,
  `jasil.events`, `jasil.subscribers`, `jasil.jobs.registry`. Never
  `jasil.backends` (the adapters), `jasil._core`, or a `crud` module.
- **Other modules** — through their `integration_service` only.
- **`fastapi`** — routers and dependencies only. Persistence raises
  `core.exceptions`; the app's error handler maps those to responses.

Providers are resolved at the edge and passed down. A router declares
`Depends(core.dependencies.get_storage)`; a subscriber, scheduled job or
`integration_service` surface function (callers with nothing to carry a provider)
calls `jasil.runtime.get_active_platform()` once and hands the provider to
`service`. A `service`, `crud` or helper never resolves it itself:
`TestProviderResolution` fails on `get_active_platform` below an entry point, so
a dependency is visible in the signature and a test substitutes it without
patching the runtime.

The dependency direction is fixed: `core` and the substrate must never import
`modules`. A module publishes its scheduled work (`scheduled_jobs.py`), its
subscribers (`subscriber_registry.py`) and its plug-ins; the platform *collects*
them. It is never the platform's job to know a module exists.

Because the substrate is an external package, that rule is split across two
enforcement points. Its own internal direction (ports never importing adapters,
nothing importing `modules`) is an import contract in the JASIL repository,
verified on its releases. Which part of it *this* application may touch lives in
`tests/architecture/test_jasil_boundary.py` — import-linter cannot state it,
because it rejects a subpackage of an external package as a forbidden module.

## Extraction target

"Could become its own library" means a specific thing here, and it is worth
stating so the next module is converted to the same target:

- **In scope:** a module is a package that could be installed separately and run
  **in the same process against the same database**. Its only inbound surface is
  the published one; it declares its own models, jobs, subscribers and
  contributors; and a peer can be lifted out without editing another module's
  internals.
- **Accepted, not accidental:** surface operations take a SQLAlchemy `Session`
  (`list_accepted_followee_ids(user_id, db)`). Callers share one transaction,
  which is what makes "stage, publish, commit" atomic across a module boundary.
  This is also why a module cannot yet move to another *process*.
- **Out of scope:** swappable implementations behind Protocol ports. Use a port
  only for a seam that genuinely has several implementations — providers,
  geocoding, file storage — and let the consumer own the Protocol. Everywhere
  else the concrete peer `integration_service` is the contract.
- **Still shared:** the metadata (`core.database.Base`) and the central Alembic
  history. A module will need its own migration branch before it can be
  distributed on its own.

## Choosing a coupling mechanism

| Need | Mechanism | Example |
| --- | --- | --- |
| Ask a peer a question, or read its data | Call its `integration_service` | `followers.integration_service.list_accepted_followee_ids` |
| A write that must be atomic with another module's write | A service call in the same session; the owning module's service arranges stage, publish and commit | `activity` bulk gear update |
| React to a fact, derive state, tolerate delay | Publish an event; the consumer subscribes; declare a reconciliation net | thumbnails and geocoding reacting to `activity.created` |
| A lower layer needs something only a higher layer knows | A seam (registry, base class, Protocol) owned by the lower layer, installed by composition | `contributor_registry`, `provider_registry` |

Do not use a contributor for a plain query, and do not use an event for something
the caller needs the answer to.

## What a module may take from `core`

`core.*` is "always allowed" today, and it is the largest hidden coupling between
a module and *this* application: configuration, logging, database session,
exceptions, pagination, timezone, uploads. It is not enforced yet. Treat it as a
declared dependency rather than free: prefer a primitive that is already generic
(`core.exceptions`, `core.timezone`, `core.pagination`) over adding a new reach
into application-wide state (`core.config`, `core.middleware`), and put anything
generic enough to be shared by other products into `jasil`.

For converted modules this is an allowlist:
`tests/architecture/test_converted_module_dependencies.py` names every `core`
module `activities` and `followers` may import, with the reason, and fails both
on an import that is not listed and on an entry nothing uses any more. Unconverted
modules are outside it.

## Inverting a platform-to-domain reach

When the platform appears to need something from a module, the module registers
it rather than the platform importing it. Three worked examples live in the tree:

| Reach | Inversion |
| --- | --- |
| The scheduler needed each module's recurring jobs | Each module declares `scheduled_jobs.recurring_jobs()`; `main.py` collects and hands the list to `start_scheduler`. |
| Ingestion needed to pull from Strava and Garmin | Providers call `activity_ingestion.integration_service.register_activity_provider`; the private registry names no provider. |
| Bulk import needed Strava-export semantics | `BulkImportSource` is a base class; `modules.strava.bulk_import_source.StravaBulkImportSource` subclasses it. |
| Activity ingestion/profile restore needed child records | Each child returns typed contributor objects from `integration_service`; `app/module_registry.py` installs them in `activities.contributor_registry`. |
| Serializing an activity needed its thumbnail's URL | `activity` asks `contributor_registry.resolve_thumbnail_url`; `activity_thumbnail` registers the resolver at composition time. |

The shape is always the same: the lower layer owns the *seam* (a registry, a base
class, a protocol), the higher layer owns the *implementation*, and a composition
root (`main.py`, `worker.py`) connects them. A registration performed in one
entrypoint must be performed in the other — a provider or subscriber registered
in the API but not the worker silently does nothing wherever its work is claimed.

## Sharing behavior between peer modules

When several modules perform the same operation over different rows, the
operation lives once in its owning module and peers consume its public behavior.
`activity/child_collection.py` is the worked example: the root activity package
owns the parent access rule and publishes `ChildCollection` through
`activity.integration_service`. Laps, sets and workout steps state their hide
flag, CRUD calls and page type without importing the private helper file.

The test for whether something belongs in such a seam is not "is this repeated?"
but "would a divergence here be a bug?". Three copies of a docstring are
harmless; three copies of an access decision are three chances to get it wrong.

`activities/contributors.py` is the second worked example:
`child_ingestion_contributor` and `child_profile_contributor` hold the parent-id
guard and the re-keying filter that laps, sets and workout steps share. Each
child supplies only its CRUD write, its archive names and how one archived record
becomes a stored row.

## Enforcement

Two mechanisms, deliberately:

**`backend/.importlinter`** — cross-module rules, run in CI by
`.github/workflows/test-backend.yml`:

```bash
cd backend && PYTHONPATH=app uv run lint-imports
```

Contracts are stated as **wildcards** (`modules.activities.*.crud`) so a new
activity module inherits every cross-module rule the day it is created, instead
of when someone remembers to add it to a list. A separate contract prevents
domain/core code from consuming `*.migration_service`.

**`backend/tests/architecture/test_module_boundaries.py`** — everything
import-linter cannot express. A `forbidden` contract rejects an import if *any*
source matches *any* forbidden module, so "a package may import its own `crud`
but not a peer's" is not statable: the wildcard pair
`modules.activities.* → modules.activities.*.crud` also rejects
`activity_laps.service → activity_laps.crud`. The conformance test walks the AST,
compares each import against the importer's own package, and consults an explicit
allowlist containing only composition edges and exact read-model projections.

- It checks module-level files too, not just sub-package ones, which is the whole
  surface of a flat module such as `followers`.
- It resolves each `from x.y import z` against the filesystem rather than counting
  dots, so a symbol imported from a flat module is not read as a package reach.
- The allowlist is the debt register. Every entry names a real cross-package reach
  and why it exists, and may not be used for service, CRUD, model, query, signing,
  parser or storage access that belongs behind an integration surface.
- The package-level import graph inside a module is asserted to be a DAG: two
  packages that import each other are one package pretending to be two. A derived
  subsystem depends on the thing it derives from, never the reverse; when the
  depended-on package needs something back, it states a seam and composition
  installs the answer.
- `TestModuleInventory` fails on a top-level module that is neither converted nor
  explicitly outstanding.

## Adding a module

1. Create either `modules/<name>` or a package under a composition namespace such
   as `modules/activities/<name>`; `__init__.py` contains a docstring only.
2. Add `integration_service.py`. It is the sole behavioral import path for peers
   and outside modules; keep it limited to operations with real callers. A
   package nothing else calls (`activity_geocoding`, purely reactive) has none
   and is listed with its reason in `_NO_CROSS_MODULE_SURFACE`.
3. Add `model_registry.py` when the module owns a table, and add its contribution
   to the namespace/application model registry.
4. Add `migration_service.py` only when an existing data migration needs a
   version-pinned private operation.
5. Add `subscriber_registry.py` if the module has subscribers, and `scheduled_jobs.py`
   if it has recurring work. Register both with one `RuntimeModule(name=..., ...)`
   entry in `app/module_registry.py`. `main.py` and `worker.py` both consume that
   list, so registering "in the API but not the worker" is not possible.
6. Add startup backfills as `StartupTask`s on the same `RuntimeModule` entry.
7. For an activity child parsed or archived as JSON, add the applicable
   `ingestion_contributor`, `profile_contributor`, or
   `profile_global_contributor` factory to `integration_service.py`, then register
   that factory in `app/module_registry.py`. Do not register at import time.
8. Add the module to the source lists of the cross-module contracts in
   `backend/.importlinter`.
9. Add a new top-level bounded context to `CONVERTED_MODULES` in
   `backend/tests/_helpers/module_roles.py`; the architecture and logging tests
   both read it from there. A module that is in neither `CONVERTED_MODULES` nor
   `UNCONVERTED_MODULES` fails `TestModuleInventory`, so a module cannot be added
   outside the rules by omission.

## Conventions that look like drift

These differ between packages on purpose, or are settled so the next module follows
one pattern. Recorded here so a review does not reopen them.

**Settled (follow these in a new module):**

- **Event publishers.** `publish_<fact>(<ids>, db, *, commit, <extras>)`: `db` and
  `commit` are required and `commit` is keyword-only. A publisher with an optional
  `commit` has a best-effort path that lets a caller publish outside the
  transaction, which is what `publish_committing` exists to prevent.
- **Import aliases.** An alias is the *containing package* plus the role, so the
  name says whose file it is and which side of the visibility rule a call site is
  on. A peer's surface is `<package>_integration` (`activity_laps_integration`,
  `followers_integration`, `activity_integration` for the root `activity`
  package); a private module is `<package>_<role>` (`activity_crud`,
  `activity_laps_service`). A namespace-level file lives in `activities`, so it is
  `activities_<role>` (`activities_contributors`, `activities_computation`).
- **Capability-token routes.** The service verifies the signed token and raises
  `NotFoundError`; the router only serialises the bytes. A router never imports
  `HTTPException` or a logger.
- **ORM mapping.** `serializers.py` exposes `to_read_schema` (and `to_orm` where
  `crud` builds rows). A private `_to_*` helper inside `crud.py` is the same job in
  a second home.
- **`db` parameter.** `db` is the last required positional parameter; keyword-only
  options follow it.
- **Helper file names.** `<thing>_utils.py`, never `utils_<thing>.py`, so the role
  reads at the end of the stem like every other role.
- **Route defaults.** The default `200` is not repeated in `status_code`. Handler
  names are OpenAPI `operationId`s, so renaming one is an API change.

**Intentional variation:**

- **`migration_service`.** Pure reads and constants are re-exported by alias; every
  write is a wrapper that owns the commit the CRUD no longer performs, so the
  version-pinned behaviour of the historical migration is stated in one function.
- **Component payload shape.** The ingestion contract carries what each parser
  emits: laps as dicts, sets as raw FIT rows or `ActivitySetsCreate`, workout steps
  as schemas. Normalising them is a change to `ParsedActivity.components` and
  every parser, not to the children, so `serializers.to_orm` absorbs the
  difference at the persistence edge.
- **Job runners own their sessions.** `run_upload_job`, `run_refresh_job`,
  `fail_ingestion_job` and the `mark_bulk_import_job_*` functions open
  `SessionLocal` themselves: they are the entry point of a durable job and need
  separate transactions (mark processing, do the work, record the failure) so a
  failed unit of work cannot poison the record of the failure.
- **Provider resolution on a surface.** A surface function resolves the provider
  only when none of its callers carries one (`attach_media_bytes`, called per file
  from the Strava import). The functions whose callers already hold the provider
  (`list_media_blobs`, `store_media_blob`, the file-storage functions) take it as
  an argument.
- **`activity_file_storage` has no `service.py`.** It is a leaf over
  `StorageProvider` with no decision to arrange, so a service would only forward.

## Known debt

Tracked here so it is visible rather than discovered. Each item is enumerated in
the conformance test's allowlist.

### Pilot status and what the next conversion owns

`activities` and `followers` are the only modules converted to this structure,
and no further conversion is planned. The remaining modules are listed in
`UNCONVERTED_MODULES` so that `TestModuleInventory` keeps them visible rather
than silently exempt. The items below need changes outside the two converted
modules, so they are recorded here as accepted limitations rather than
half-done:

- **Top-level cycles.** `users` ⇄ `activities` (profile export/import consumes
  activities; activities consumes `users.integration_service`) and, by the
  contracts' own description, `gears` ⇄ `activities`. The planned fix is the
  contributor pattern the activity children already use: `users_profile`
  export/import asks each module for its section instead of importing them.
  A `layers` contract (core, identity, followers/gears/health, activities,
  providers) then states the module-level direction once. Every edge that closes
  a cycle lives in `users` or `gears`; the activities side already publishes the
  contributor surface, so the cycles stay until those modules are changed.
- **Hand-listed contract sources.** `consumer-*` and `consumer-users-boundary`
  name the modules they constrain by hand, because each states a specific
  consumer relationship rather than a rule over every module.
  `auth-boundary` is the generic one: `TestAuthBoundaryCoverage` fails when its
  source list differs from the set of top-level modules, so a new module cannot
  start unconstrained.
- **Registration seams.** `RuntimeModule` in `app/module_registry.py` is the
  per-module manifest for subscribers, durable handlers, nets, recurring jobs and
  startup tasks. Routers (`api.py`) and models (`model_registry.py`) stay
  separate composition surfaces on purpose: they have different consumers
  (the HTTP app and Alembic), and the legacy model scan keeps the model seam
  distinct until the unconverted modules are gone.
- **Namespace-level code in `health`.** `health` has `router.py`, `schema.py`
  and `utils.py` beside its sub-packages, which conflicts with "a composition
  namespace is not an implementation boundary".
- **`auth` naming.** `identity_service.py` and `_internal/` instead of
  `integration_service.py` and the filename vocabulary, as described above.
  The generic boundary rule needs a special case until it is aligned.

- **Persistence layers calling other modules.** Converted modules ask nothing of
  another bounded context below `service` (the
  `activities-persistence-asks-nothing` contract). The unconverted ones still do,
  for example `health.*.crud` reaching `users.users.utils` and
  `notifications.utils` reaching `users` and `websocket`.
- **Persistence ending transactions.** Converted modules stage with `flush()` and
  let `service` (or the job, importer or `migration_service` that owns the unit of
  work) commit. Unconverted modules (`auth`, `users`, `gears`, …) are outside
  `test_crud_does_not_end_transactions`; `_CRUD_TRANSACTION_DEBT` is empty and is
  where a module being converted lists its files.
- **Subscribers reaching past a surface.** Followers holds no subscribers; its
  notification handlers live in `modules/notifications` and subscribe to
  `followers.events` (`followers-consumes-surfaces`). `garmin`, `strava`,
  `auth.sign_up_tokens` and `users.users_profile` still import another module's
  `utils`/`manager` directly.
- **Legacy model scan.** Converted modules declare their ORM module in
  `model_registry.py` and use scalar foreign keys with database `ON DELETE`
  behaviour. `app/model_registry.py` keeps a filesystem scan only for modules not
  converted yet.
