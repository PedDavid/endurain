"""Module-boundary conformance, enforced.

The rule from ``docs/developer-guide/module-structure.md`` that import-linter
cannot express. A ``forbidden`` contract rejects an import when *any* source
matches *any* forbidden module, so the wildcard pair
``modules.activities.* -> modules.activities.*.crud`` — the natural way to say
"a sub-package's persistence is private" — also rejects
``activity_laps.service -> activity_laps.crud``, which is the layering the
project requires. There is no way to say "except your own package".

So it is checked here instead: walk the AST, compare each import against the
importer's own sub-package, and consult an explicit allowlist of named seams.
That allowlist is the debt register — every entry is a real cross-package reach
with the reason it still exists, and anything not listed fails.
"""

import ast
import fnmatch
import pathlib

from tests._helpers.module_roles import CONVERTED_MODULES, UNCONVERTED_MODULES
from tests._helpers.module_roles import role_of as _role

#: Modules converted to the structure guide, shared with ``test_logging_rule.py``
#: through ``tests/_helpers/module_roles.py``. Opt-in, and ratcheted by
#: ``TestModuleInventory`` so an unconverted module is visibly outstanding
#: rather than silently exempt.
_CONVERTED = CONVERTED_MODULES

_MODULES_ROOT = pathlib.Path("app/modules")

_APP_ROOT = pathlib.Path("app")

#: Roles one independently extractable activity package may import from another.
#: Data shapes and event declarations carry no implementation; behaviour crosses
#: the boundary only through ``integration_service``. The remaining names are
#: composition surfaces mounted by the application.
#:
#: Matched by ROLE, not by exact filename, so ``summary_router`` is a router
#: without having to be hand-added here and ``summary_crud`` is private without
#: anyone remembering to say so.
_PUBLIC_WITHIN_MODULE = frozenset(
    {
        "schema",
        "contracts",
        "constants",
        "events",
        "integration_service",
        "dependencies",
        "router",
        "public_router",
        "subscriber_registry",
        "scheduled_jobs",
        "model_registry",
        "contributor_registry",
    }
)

#: Roles importable from *outside* the owning module. Everything else is
#: module-internal or package-private. ``service`` and ``query`` are deliberately
#: absent: they are the module's own layers, not its published surface.
_PUBLIC_ACROSS_MODULES = (_PUBLIC_WITHIN_MODULE - {"service", "query"}) | frozenset(
    {
        "integration_service",
        "subscriber_registry",
        "scheduled_jobs",
        "computation",
        "dependencies",
        "router",
        "public_router",
    }
)

_MIGRATION_SURFACE = "migration_service"

#: ``Session`` methods that run a statement. Only a ``crud``-role file may call
#: them. Transaction control is a separate rule (``_TRANSACTION_CONTROL``): the
#: service layer owns the boundary, so a ``crud`` file may flush but not end one.
_SESSION_EXECUTION = frozenset({"execute", "scalars", "scalar", "query", "add", "add_all", "get", "merge"})

#: ``Session`` methods that end a transaction. ``flush`` is deliberately absent:
#: staging a write so constraint errors and row counts surface is persistence's
#: job; deciding when it becomes durable is not.
_TRANSACTION_CONTROL = frozenset({"commit", "rollback"})

#: ``crud`` files that still end transactions themselves, as
#: ``{path relative to modules/: reason}``. This is the debt register for
#: ``test_crud_does_not_end_transactions``. It is empty: every converted module
#: stages and lets ``service`` commit. A module being converted lists its files
#: here until its writers are stage-only; the test fails on an entry that no
#: longer offends so the list cannot outlive the debt.
_CRUD_TRANSACTION_DEBT: dict[str, str] = {}

#: Cross-sub-package reaches that are part of the design, as
#: ``(importer glob, imported glob): reason``. Globs are module paths relative
#: to the module root (``activity.integration_service``, ``activity_laps.crud``).
_INTRA_MODULE_SEAMS: dict[tuple[str, str], str] = {}

#: Reaches past a module's surface from outside it, as
#: ``(importer module path, imported glob): reason``.
_INBOUND_EXCEPTIONS: dict[tuple[str, str], str] = {}

#: Multi-activity reads that trust their activity ids were owner-scoped before
#: reaching persistence. Their call surface is deliberately narrower than an
#: ordinary package operation because a new caller could otherwise create an
#: indirect object reference vulnerability.
_BULK_ACTIVITY_READS = {
    "get_activities_laps": "activity_laps",
    "get_activities_media": "activity_media",
    "get_activities_sets": "activity_sets",
    "get_activities_streams": "activity_streams",
    "get_activities_workout_steps": "activity_workout_steps",
}


def _module_path(path: pathlib.Path, root: pathlib.Path) -> str:
    """
    Return a file's dotted path relative to its module root.

    Args:
        path: The Python file.
        root: The module root directory.

    Returns:
        The dotted path, e.g. ``activity.crud`` or ``subscriber_registry``.
    """
    return ".".join(path.relative_to(root).with_suffix("").parts)


def _names_a_module(dotted: str) -> bool:
    """
    Return whether a dotted path names a real module or package on disk.

    Args:
        dotted: A fully qualified dotted path rooted at ``app``.

    Returns:
        True when the path resolves to a ``.py`` file or a package directory.
    """
    base = _APP_ROOT.joinpath(*dotted.split("."))
    return base.with_suffix(".py").is_file() or base.is_dir()


def _imported_modules(path: pathlib.Path) -> set[str]:
    """
    Return every fully qualified module this file imports.

    Args:
        path: The Python file to parse.

    Returns:
        The imported dotted module paths, including ``TYPE_CHECKING`` imports.
    """
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            # ``from x.y import z`` names either a submodule or a symbol, and the
            # two have to be told apart on disk. Counting dots does not work: in a
            # flat module ``modules.followers.constants`` has the same depth as a
            # sub-packaged module's ``modules.activities.activity``, so every
            # symbol imported from a flat module read as a submodule.
            for alias in node.names:
                candidate = f"{node.module}.{alias.name}"
                found.add(candidate if _names_a_module(candidate) else node.module)
    return found


def _matches(pair: tuple[str, str], rules: dict[tuple[str, str], str]) -> bool:
    """
    Return whether an import pair is covered by an allowlist.

    Args:
        pair: The ``(importer, imported)`` dotted paths.
        rules: The allowlist keyed by ``(importer glob, imported glob)``.

    Returns:
        True when some rule matches both halves of the pair.
    """
    importer, imported = pair
    return any(
        fnmatch.fnmatch(importer, rule_importer) and fnmatch.fnmatch(imported, rule_imported)
        for rule_importer, rule_imported in rules
    )


def _python_files(root: pathlib.Path) -> list[pathlib.Path]:
    """
    Return the Python files under a directory, newest-stable ordered.

    Args:
        root: The directory to walk.

    Returns:
        The sorted list of ``.py`` files, excluding caches.
    """
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _symbol_reference_nodes(tree: ast.AST, symbols: set[str]) -> list[tuple[ast.AST, str]]:
    """Return AST nodes that reference any named symbol.

    Args:
        tree: Syntax tree to inspect.
        symbols: Unqualified symbol names to locate.

    Returns:
        Reference nodes paired with the symbol each one names.
    """
    references: list[tuple[ast.AST, str]] = []
    for node in ast.walk(tree):
        candidates: tuple[str | None, ...]
        if isinstance(node, ast.Name):
            candidates = (node.id,)
        elif isinstance(node, ast.Attribute):
            candidates = (node.attr,)
        elif isinstance(node, ast.alias):
            candidates = (node.name.rsplit(".", 1)[-1], node.asname)
        else:
            continue
        references.extend((node, candidate) for candidate in candidates if candidate in symbols)
    return references


def _names_reached_through(tree: ast.AST, module: str) -> set[str]:
    """
    Return the names a file accesses on one module.

    Resolves how the file bound the module rather than matching a bare
    attribute name, so ``other.thing`` on an unrelated object is not a use of
    ``module.thing``.

    Args:
        tree: The parsed file.
        module: Fully qualified dotted module path, e.g.
            ``modules.activities.activity_laps.integration_service``.

    Returns:
        The attribute names read from the module and the names imported from it.
    """
    package, _, leaf = module.rpartition(".")
    bound: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module:
                    bound.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module == module:
                names.update(alias.name for alias in node.names)
            elif node.module == package:
                bound.update(alias.asname or alias.name for alias in node.names if alias.name == leaf)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and ast.unparse(node.value) in bound:
            names.add(node.attr)
    return names


def _package_graph(module_name: str) -> dict[str, set[str]]:
    """
    Return which sub-packages of a module import which others.

    Args:
        module_name: The module directory under ``app/modules``.

    Returns:
        A mapping of sub-package to the sub-packages it imports. Namespace-level
        files are excluded: composing the packages is their job.
    """
    root = _MODULES_ROOT / module_name
    prefix = f"modules.{module_name}."
    graph: dict[str, set[str]] = {}
    for path in _python_files(root):
        importer = _module_path(path, root)
        if "." not in importer:
            continue
        importer_pkg = importer.split(".")[0]
        edges = graph.setdefault(importer_pkg, set())
        for target in _imported_modules(path):
            if not target.startswith(prefix):
                continue
            imported_pkg = target[len(prefix) :].split(".")[0]
            if imported_pkg != importer_pkg and (root / imported_pkg).is_dir():
                edges.add(imported_pkg)
    return graph


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    """
    Return one import cycle in a package graph, if any exists.

    Args:
        graph: The package-to-packages mapping.

    Returns:
        The cycle as a path that starts and ends on the same package, or None.
    """
    #: 1 = on the current path, 2 = fully explored.
    state: dict[str, int] = {}
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        path.append(node)
        for following in sorted(graph.get(node, ())):
            if state.get(following, 0) == 0:
                cycle = visit(following)
                if cycle:
                    return cycle
            elif state[following] == 1:
                return [*path[path.index(following) :], following]
        path.pop()
        state[node] = 2
        return None

    for node in sorted(graph):
        if state.get(node, 0) == 0:
            cycle = visit(node)
            if cycle:
                return cycle
    return None


class TestSubPackagePrivacy:
    """A sub-package's persistence and application layers are private to it."""

    def test_siblings_share_only_data_shapes(self):
        """Anything beyond schema/contracts/constants/events needs a named seam."""
        offenders: list[str] = []
        for module_name in _CONVERTED:
            root = _MODULES_ROOT / module_name
            prefix = f"modules.{module_name}."
            for path in _python_files(root):
                importer = _module_path(path, root)
                importer_pkg = importer.split(".")[0] if "." in importer else ""
                for target in _imported_modules(path):
                    if not target.startswith(prefix):
                        continue
                    imported = target[len(prefix) :]
                    if "." not in imported:
                        continue
                    imported_pkg, imported_stem = imported.rsplit(".", 1)
                    if imported_pkg == importer_pkg:
                        continue
                    if _role(imported_stem) in _PUBLIC_WITHIN_MODULE:
                        continue
                    if _matches((importer, imported), _INTRA_MODULE_SEAMS):
                        continue
                    offenders.append(f"{module_name}: {importer} -> {imported}")
        assert not offenders, (
            "Cross-sub-package reach into a private module. Either use the "
            "sibling's published surface, or add the seam to _INTRA_MODULE_SEAMS "
            "with the reason it exists:\n  " + "\n  ".join(sorted(offenders))
        )

    def test_package_inits_export_nothing(self):
        """A re-export facade would hand out ORM models under a package path."""
        offenders: list[str] = []
        for module_name in _CONVERTED:
            root = _MODULES_ROOT / module_name
            for path in root.rglob("__init__.py"):
                if "__pycache__" in path.parts:
                    continue
                body = ast.parse(path.read_text()).body
                statements = [n for n in body if not isinstance(n, ast.Expr)]
                if statements:
                    offenders.append(str(path))
        assert not offenders, "__init__.py must hold a docstring and nothing else:\n  " + "\n  ".join(sorted(offenders))

    def test_only_crud_executes_against_the_session(self):
        """A file that runs statements is persistence, whatever it is called.

        The rules match on the role a filename declares, so a persistence module
        under some other name inherits none of them. ``summary_query`` executed
        every activity-summary aggregation for exactly that reason: no
        ``modules.activities.*.crud`` contract saw it, and the logging rule
        classified it as a session-free ``query``. Naming is the contract, so it
        is checked rather than trusted.

        Transaction control is a separate rule
        (``test_crud_does_not_end_transactions``).
        """
        offenders: list[str] = []
        for module_name in _CONVERTED:
            for path in _python_files(_MODULES_ROOT / module_name):
                if _role(path.stem) == "crud":
                    continue
                executed = sorted(
                    {
                        node.func.attr
                        for node in ast.walk(ast.parse(path.read_text()))
                        if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in _SESSION_EXECUTION
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "db"
                    }
                )
                if executed:
                    offenders.append(f"{path} (role={_role(path.stem)}): db.{', db.'.join(executed)}")
        assert not offenders, (
            "Only a crud-role file may execute against the session. Move the "
            "query into the package's crud, or rename the file so its role is "
            "the one it actually plays:\n  " + "\n  ".join(sorted(offenders))
        )

    def test_crud_does_not_end_transactions(self):
        """Persistence stages a write; the service decides when it is durable.

        Only the layer that knows the whole unit of work can commit it: a service
        stages a row, publishes the event and commits so the two are atomic. A
        ``crud`` that commits itself makes that atomicity depend on the caller
        remembering to opt out, and it is the default branch that nobody
        exercises. ``flush`` is allowed (it surfaces constraint errors and row
        counts); ``commit`` and ``rollback`` are not. Rolling back belongs to the
        transaction owner, which is the service or ``core.database.get_db``.

        ``_CRUD_TRANSACTION_DEBT`` records the files that still do. It shrinks;
        a stale entry fails, so it cannot outlive the debt.
        """
        offenders: list[str] = []
        stale: list[str] = []
        for module_name in _CONVERTED:
            for path in _python_files(_MODULES_ROOT / module_name):
                if _role(path.stem) != "crud":
                    continue
                ended = sorted(
                    {
                        node.func.attr
                        for node in ast.walk(ast.parse(path.read_text()))
                        if isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in _TRANSACTION_CONTROL
                        and isinstance(node.func.value, ast.Name)
                    }
                )
                relative = path.relative_to(_MODULES_ROOT).as_posix()
                if ended and relative not in _CRUD_TRANSACTION_DEBT:
                    offenders.append(f"{relative}: .{', .'.join(ended)}()")
                if not ended and relative in _CRUD_TRANSACTION_DEBT:
                    stale.append(relative)
        assert not offenders, (
            "A crud-role file may flush but not commit or roll back. Stage the "
            "write and let the service own the transaction:\n  " + "\n  ".join(sorted(offenders))
        )
        assert not stale, (
            "These files no longer end transactions; remove them from "
            "_CRUD_TRANSACTION_DEBT:\n  " + "\n  ".join(sorted(stale))
        )

    def test_a_file_plays_the_role_its_content_declares(self):
        """A router or an ORM model cannot hide under a helper name.

        Roles are read off filenames, so a file under a name the vocabulary does
        not know is package-private and inherits none of the router or ORM rules.
        ``test_only_crud_executes_against_the_session`` holds the persistence
        half; this holds the other two. The vocabulary is deliberately not
        closed — ``pipeline.py``, ``render.py`` and ``signing.py`` are legitimate
        private helpers — so the check is that a file's *content* agrees with
        its name, not that its name is on a list.
        """
        offenders: list[str] = []
        for module_name in _CONVERTED:
            for path in _python_files(_MODULES_ROOT / module_name):
                role = _role(path.stem)
                nodes = list(ast.walk(ast.parse(path.read_text())))
                declares_router = any(
                    isinstance(node, ast.Call)
                    and getattr(node.func, "attr", getattr(node.func, "id", None)) == "APIRouter"
                    for node in nodes
                )
                declares_model = any(
                    isinstance(node, ast.ClassDef)
                    and any(isinstance(base, ast.Name) and base.id == "Base" for base in node.bases)
                    for node in nodes
                )
                if declares_router and role not in {"router", "public_router"}:
                    offenders.append(f"{path} (role={role}): constructs an APIRouter")
                if declares_model and role != "models":
                    offenders.append(f"{path} (role={role}): declares an ORM model")
        assert not offenders, (
            "A file's role is its filename, so the filename has to say what the "
            "file is. Rename it into the vocabulary in "
            "tests/_helpers/module_roles.py:\n  " + "\n  ".join(sorted(offenders))
        )


class TestModuleInventory:
    """Every top-level module is either converted or explicitly outstanding."""

    def test_every_top_level_module_is_classified(self):
        """A module in neither list would start life outside every rule.

        The conformance tests are opt-in (``CONVERTED_MODULES``), which is the
        right default while modules convert one PR at a time, and the wrong one
        for a module nobody has thought about yet: it is exempt from all of them
        without a single line saying so. Naming the outstanding modules makes
        "convert it" and "consciously leave it" the only two options for a new
        one. Converting a module is moving its name between the two lists in
        ``tests/_helpers/module_roles.py``.
        """
        on_disk = {
            path.name for path in _MODULES_ROOT.iterdir() if path.is_dir() and not path.name.startswith(("_", "."))
        }
        declared = set(CONVERTED_MODULES) | set(UNCONVERTED_MODULES)
        unclassified = sorted(on_disk - declared)
        stale = sorted(declared - on_disk)
        assert not unclassified, (
            "Top-level module in neither CONVERTED_MODULES nor UNCONVERTED_MODULES "
            "(tests/_helpers/module_roles.py). Convert it to the structure guide, "
            "or list it as outstanding:\n  " + "\n  ".join(unclassified)
        )
        assert not stale, "Listed in module_roles.py but not on disk:\n  " + "\n  ".join(stale)
        assert not set(CONVERTED_MODULES) & set(UNCONVERTED_MODULES), (
            "A module cannot be both converted and outstanding"
        )


class TestProviderResolution:
    """A platform provider is resolved at an entry point and passed down."""

    #: Roles that are entry points: something external (HTTP, the bus, the
    #: scheduler, a peer module) calls them with nothing to carry a provider.
    _ENTRY_ROLES = frozenset({"router", "public_router", "subscribers", "integration_service", "scheduled_jobs"})

    #: Stem suffixes for entry layers whose names are not in the role vocabulary.
    _ENTRY_SUFFIXES = ("_jobs", "_entry", "_subscribers")

    def test_services_and_helpers_receive_their_providers(self):
        """``get_active_platform()`` is a service locator, so it stays at the edge.

        A function that reaches for the process-wide platform has a dependency
        its signature does not show: it cannot be tested without patching the
        runtime, and it cannot be lifted out of this application without
        dragging the runtime with it. Entry points resolve the provider once and
        hand it down as an argument; routers declare it with
        ``Depends(core.dependencies.get_storage)`` so a test overrides it.
        """
        offenders: list[str] = []
        for module_name in _CONVERTED:
            for path in _python_files(_MODULES_ROOT / module_name):
                if _role(path.stem) in self._ENTRY_ROLES or path.stem.endswith(self._ENTRY_SUFFIXES):
                    continue
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, ast.Attribute) and node.attr in {"get_active_platform", "get_state"}:
                        offenders.append(f"{path}:{node.lineno}")
        assert not offenders, (
            "Provider resolved below an entry point. Take the provider as an "
            "argument and resolve it in the router (Depends), subscriber, job or "
            "integration_service that calls you:\n  " + "\n  ".join(sorted(offenders))
        )


class TestPackageIndependence:
    """A package is the unit of extraction, so the graph between them is a DAG."""

    def test_sub_packages_do_not_import_each_other_in_a_cycle(self):
        """Two packages that import each other are one package pretending to be two.

        Every rule above is about *what* one package may reach for in another;
        none of them notices when the reaching goes both ways. ``activity`` and
        ``activity_thumbnail`` each used the other's ``integration_service`` —
        legal on both counts, and still a pair that could not be built, tested or
        lifted out separately: serializing an activity needed the thumbnail
        package to address the blob, and the thumbnail package needed the
        activity row it derives from.

        Direction is the fix, not politeness about surfaces. A derived subsystem
        depends on what it derives from; when the root needs something back, it
        states the seam (``contributor_registry``) and composition installs the
        answer.
        """
        offenders: list[str] = []
        for module_name in _CONVERTED:
            cycle = _find_cycle(_package_graph(module_name))
            if cycle:
                offenders.append(f"{module_name}: {' -> '.join(cycle)}")
        assert not offenders, (
            "Import cycle between packages that are supposed to be independently "
            "extractable. Invert the edge that points from the depended-on "
            "package back to its dependant:\n  " + "\n  ".join(offenders)
        )


class TestOwnerScopedBulkReads:
    """Bulk reads without access checks stay behind owner-scoped profile export."""

    def test_bulk_reads_are_referenced_only_inside_their_own_package(self):
        """Reject callers outside each bulk read's CRUD and integration surface."""
        offenders: list[str] = []
        symbols = set(_BULK_ACTIVITY_READS)
        for path in _python_files(_APP_ROOT):
            tree = ast.parse(path.read_text())
            for node, symbol in _symbol_reference_nodes(tree, symbols):
                package = _BULK_ACTIVITY_READS[symbol]
                package_root = _MODULES_ROOT / "activities" / package
                if path in {package_root / "crud.py", package_root / "integration_service.py"}:
                    continue
                offenders.append(f"{path}:{node.lineno}: references {symbol}")
        assert not offenders, (
            "A multi-activity bulk read that trusts pre-scoped ids gained a "
            "caller outside its package. A new caller is a latent IDOR; route "
            "profile export through the owning package's contributor:\n  " + "\n  ".join(sorted(offenders))
        )

    def test_bulk_read_helpers_are_private_profile_exports(self):
        """Keep each integration wrapper private and wired only as ``export``."""
        offenders: list[str] = []
        for bulk_read, package in _BULK_ACTIVITY_READS.items():
            integration_path = _MODULES_ROOT / "activities" / package / "integration_service.py"
            integration_tree = ast.parse(integration_path.read_text())
            reader_functions = [
                node
                for node in integration_tree.body
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and _symbol_reference_nodes(node, {bulk_read})
            ]
            if len(reader_functions) != 1:
                offenders.append(
                    f"{integration_path}: expected one wrapper around {bulk_read}, found {len(reader_functions)}"
                )
                continue

            helper = reader_functions[0]
            if not helper.name.startswith("_"):
                offenders.append(f"{integration_path}:{helper.lineno}: {helper.name} must be private")

            permitted_reference_ids: set[int] = set()
            for call in (node for node in ast.walk(integration_tree) if isinstance(node, ast.Call)):
                if isinstance(call.func, ast.Attribute):
                    constructor = call.func.attr
                elif isinstance(call.func, ast.Name):
                    constructor = call.func.id
                else:
                    constructor = None
                if constructor != "ProfileActivityContributor":
                    continue
                for keyword in call.keywords:
                    if (
                        keyword.arg == "export"
                        and isinstance(keyword.value, ast.Name)
                        and keyword.value.id == helper.name
                    ):
                        permitted_reference_ids.add(id(keyword.value))

            references: list[tuple[pathlib.Path, ast.AST]] = []
            for path in _python_files(_APP_ROOT):
                tree = integration_tree if path == integration_path else ast.parse(path.read_text())
                references.extend(
                    (path, node)
                    for node, _symbol in _symbol_reference_nodes(tree, {helper.name})
                    if id(node) not in permitted_reference_ids
                )
            if len(permitted_reference_ids) != 1:
                offenders.append(
                    f"{integration_path}:{helper.lineno}: {helper.name} must be the export callback of exactly one "
                    "ProfileActivityContributor"
                )
            offenders.extend(
                f"{path}:{node.lineno}: references {helper.name} outside its profile export wiring"
                for path, node in references
            )

        assert not offenders, (
            "A wrapper around an owner-scoped bulk read escaped the private "
            "profile export seam:\n  " + "\n  ".join(sorted(offenders))
        )


class TestModuleSurface:
    """Other modules consume a module only through its published surface."""

    def test_the_surface_holds_only_operations_with_callers(self):
        """A published function nobody calls is API surface with no user.

        ``integration_service`` is the one file other modules may depend on, so
        every name in it is a promise. The child packages had grown six public
        functions each where two were reached from outside: the contributor
        plumbing (``store_laps``, ``restore_profile_records``,
        ``list_laps_for_activities``) was published alongside the contributor
        factory that was the actual surface, so "what may I depend on?" answered
        with three times more than the module meant.

        A caller is a file **outside the package** that reaches the function
        through the module it imported: ``alias.name`` for ``import x as alias``,
        or ``name`` for ``from x import name``. A same-named attribute on some
        other object does not count, and neither does the package calling its own
        surface, which is a function that belongs in the package, not on its
        face. A new operation lands here together with the caller that needed it,
        or it stays private until then.
        """
        trees = {path: ast.parse(path.read_text()) for path in _python_files(_APP_ROOT)}
        offenders: list[str] = []
        for module_name in _CONVERTED:
            for path in _python_files(_MODULES_ROOT / module_name):
                if path.name != "integration_service.py":
                    continue
                dotted = ".".join(path.relative_to(_APP_ROOT).with_suffix("").parts)
                called = {
                    name
                    for other, tree in trees.items()
                    if path.parent not in other.parents
                    for name in _names_reached_through(tree, dotted)
                }
                for node in trees[path].body:
                    if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                        continue
                    if node.name.startswith("_") or node.name in called:
                        continue
                    offenders.append(f"{_module_path(path, _APP_ROOT)}.{node.name}")
        assert not offenders, (
            "Published on an integration surface with no caller outside its package. "
            "Make it private until something needs it:\n  " + "\n  ".join(sorted(offenders))
        )

    def test_outsiders_use_the_published_surface(self):
        """A reach past the surface needs an entry in _INBOUND_EXCEPTIONS."""
        offenders: list[str] = []
        app_root = pathlib.Path("app")
        for module_name in _CONVERTED:
            module_root = _MODULES_ROOT / module_name
            prefix = f"modules.{module_name}."
            for path in _python_files(app_root):
                if module_root in path.parents or path == module_root:
                    continue
                importer = _module_path(path, app_root)
                for target in _imported_modules(path):
                    if not target.startswith(prefix):
                        continue
                    imported = target[len(prefix) :]
                    # A dotless target is either a sub-package (importing the bare
                    # package yields nothing, so it is not a reach) or a
                    # module-level file — which IS the whole surface of a flat
                    # module like followers, so it has to be checked.
                    if "." not in imported and (module_root / imported).is_dir():
                        continue
                    if _role(imported.rsplit(".", 1)[-1]) in _PUBLIC_ACROSS_MODULES:
                        continue
                    if importer.startswith("migrations.") and _role(imported.rsplit(".", 1)[-1]) == _MIGRATION_SURFACE:
                        continue
                    if _matches((importer, imported), _INBOUND_EXCEPTIONS):
                        continue
                    offenders.append(f"{importer} -> {module_name}.{imported}")
        assert not offenders, (
            "Reach past a module's published surface. Either add the operation "
            "to the module's integration_service, or record the exception in "
            "_INBOUND_EXCEPTIONS:\n  " + "\n  ".join(sorted(offenders))
        )
