"""Pack plugins — the seam that turns "I need a new verb" into a file in my pack.

A pack names a ``plugin.py`` in its ``pack.yaml``; this module imports it and
wires whatever it declares into the two tables that matter:

* the **engine registries** (``core/registry.py``) — so the interpreter can run
  the op;
* the **vocabulary** (``content/vocabulary.py``) — so ``hts validate`` knows the
  op exists, what params it takes, and which of them hold nested effects.

Both, from one declaration. A plugin that registered only with the engine would
run fine and fail validation; one that only declared itself would validate and
then explode mid-game. Keeping the two in a single decorator is the point of
:class:`Plugin`.

Why a ``Plugin`` object rather than bare ``@effect`` decorators at import time:
Python caches modules in ``sys.modules``, so import-time registration runs
exactly once per process. A test that scopes registrations with
``core.registry.temporarily()``, or a ``diff-pack`` that loads two packs in a
row, would then find the ops gone and no way to get them back. Registration is
therefore *deferred* — the decorators record, :meth:`Plugin.install` applies —
and installing twice is a no-op rather than a collision.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from here_to_slay.content.errors import ContentError, ContentIssue
from here_to_slay.content.registry import ContentRegistry
from here_to_slay.content.vocabulary import (
    BASE_VOCABULARY,
    OpKind,
    OpSpec,
    ParamSpec,
    Role,
    Vocabulary,
)
from here_to_slay.core import registry as engine

#: how a param may be written in a plugin's ``params={...}``
ParamValue = Role | ParamSpec | tuple[Role, bool]
ParamTable = Mapping[str, ParamValue]

#: package name a pack's plugin is imported under
MODULE_PREFIX = "here_to_slay._packs"


def _params(table: ParamTable | None) -> dict[str, ParamSpec]:
    """``{"card": Role.REF, "count": (Role.VALUE, True)}`` -> ``ParamSpec``s."""
    out: dict[str, ParamSpec] = {}
    for name, value in (table or {}).items():
        if isinstance(value, ParamSpec):
            out[name] = value
        elif isinstance(value, tuple):
            out[name] = ParamSpec(value[0], bool(value[1]))
        else:
            out[name] = ParamSpec(value)
    return out


@dataclass(frozen=True, slots=True)
class _Entry:
    """One deferred registration: which table, under what name, of what."""

    table: str
    name: str
    handler: Any
    replace: bool


class Plugin:
    """Everything one pack adds to the engine, declared in one object.

    ::

        from here_to_slay.modding import Plugin, Role

        plugin = Plugin("overclock")

        @plugin.effect("upload_card", params={"card": (Role.REF, True)})
        def upload_card(ctx, params):
            ...

    The decorators return the function untouched, so a plugin module stays
    ordinary, directly testable Python.
    """

    def __init__(self, pack_id: str = "", *, doc: str = "") -> None:
        self.pack_id = pack_id
        self.doc = doc
        self._entries: list[_Entry] = []
        self._effects: list[OpSpec] = []
        self._conditions: list[OpSpec] = []
        self._selectors: list[OpSpec] = []
        self._events: list[str] = []
        #: what this plugin actually put in each registry, so a second
        #: :meth:`install` recognises its own work instead of colliding with it
        self._installed: dict[tuple[str, str], Any] = {}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Plugin({self.pack_id!r}, {len(self._entries)} registrations)"

    # -- declarations ------------------------------------------------------

    def effect(
        self,
        name: str,
        *,
        params: ParamTable | None = None,
        body: Sequence[str] = (),
        binds: str | None = None,
        bind_scope: str = "body",
        doc: str = "",
        replace: bool = False,
    ) -> Callable[[Any], Any]:
        """Register a new verb.

        ``body`` names the params that hold nested effects and ``binds`` the one
        that introduces a ``$name`` — the validator needs both to walk the op
        the way the interpreter will.
        """

        def decorate(function: Any) -> Any:
            self._effects.append(
                OpSpec(
                    name,
                    OpKind.EFFECT,
                    _params(params),
                    binds=binds,
                    bind_scope=bind_scope,
                    body=tuple(body),
                    doc=doc,
                )
            )
            self._entries.append(_Entry("effects", name, function, replace))
            return function

        return decorate

    def condition(
        self,
        name: str,
        *,
        params: ParamTable | None = None,
        doc: str = "",
        replace: bool = False,
    ) -> Callable[[Any], Any]:
        """Register a new predicate. Conditions are pure — no yielding."""

        def decorate(function: Any) -> Any:
            self._conditions.append(OpSpec(name, OpKind.CONDITION, _params(params), doc=doc))
            self._entries.append(_Entry("conditions", name, function, replace))
            return function

        return decorate

    def selector(
        self,
        name: str,
        *,
        params: ParamTable | None = None,
        doc: str = "",
        replace: bool = False,
    ) -> Callable[[Any], Any]:
        """Register a new target set."""

        def decorate(function: Any) -> Any:
            self._selectors.append(OpSpec(name, OpKind.SELECTOR, _params(params), doc=doc))
            self._entries.append(_Entry("selectors", name, function, replace))
            return function

        return decorate

    def mutator(self, event_name: str, *, replace: bool = False) -> Callable[[Any], Any]:
        """Register *the* state change for an event — and declare the event.

        Declaring it is not a convenience: a variant's own ``cache.uploaded`` is
        unknown to the base vocabulary, so a window or trigger naming it would
        fail validation. A mutator is the honest place to say the event exists,
        because writing one is what makes it mean something.
        """

        def decorate(function: Any) -> Any:
            self._events.append(event_name)
            self._entries.append(_Entry("mutators", event_name, function, replace))
            return function

        return decorate

    def cost(self, name: str, *, replace: bool = False) -> Callable[[Any], Any]:
        """Register a payment type. It must answer ``check_only`` from state
        alone — ``legal_intents()`` calls it every frame and cannot ask."""

        def decorate(function: Any) -> Any:
            self._entries.append(_Entry("costs", name, function, replace))
            return function

        return decorate

    def event(self, *names: str) -> None:
        """Declare event names this pack emits but does not mutate on."""
        self._events.extend(names)

    # -- introspection -----------------------------------------------------

    @property
    def registrations(self) -> tuple[tuple[str, str], ...]:
        """``(table, name)`` for everything declared, in declaration order."""
        return tuple((entry.table, entry.name) for entry in self._entries)

    @property
    def events(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self._events))

    # -- application -------------------------------------------------------

    def install(self) -> None:
        """Put every declaration into the engine registries.

        Idempotent: a registration this plugin already made is skipped, so
        loading the same pack twice in one process is fine, while *another* pack
        claiming the same op name still raises.
        """
        for entry in self._entries:
            key = (entry.table, entry.name)
            already = self._installed.get(key)
            if already is not None and _TABLES[entry.table].find(entry.name) is already:
                continue
            _REGISTER[entry.table](entry.name, entry.handler, entry.replace)
            self._installed[key] = _TABLES[entry.table].find(entry.name)

    def extend(self, vocabulary: Vocabulary = BASE_VOCABULARY) -> Vocabulary:
        """A vocabulary that also knows this pack's ops and events."""
        if not (self._effects or self._conditions or self._selectors or self._events):
            return vocabulary
        return vocabulary.extend(
            effects=self._effects,
            conditions=self._conditions,
            selectors=self._selectors,
            events=self.events,
        )


def _register_effect(name: str, function: Any, replace: bool) -> None:
    engine.effect(name, replace=replace)(function)


def _register_condition(name: str, function: Any, replace: bool) -> None:
    engine.condition(name, replace=replace)(function)


def _register_selector(name: str, function: Any, replace: bool) -> None:
    engine.selector(name, replace=replace)(function)


def _register_mutator(name: str, function: Any, replace: bool) -> None:
    engine.mutator(name, replace=replace)(function)


def _register_cost(name: str, function: Any, replace: bool) -> None:
    engine.cost(name, replace=replace)(function)


_REGISTER: dict[str, Callable[[str, Any, bool], None]] = {
    "effects": _register_effect,
    "conditions": _register_condition,
    "selectors": _register_selector,
    "mutators": _register_mutator,
    "costs": _register_cost,
}

_TABLES: dict[str, Any] = {
    "effects": engine.EFFECTS,
    "conditions": engine.CONDITIONS,
    "selectors": engine.SELECTORS,
    "mutators": engine.MUTATORS,
    "costs": engine.COSTS,
}


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LoadedPlugin:
    """One imported ``plugin.py`` and the plugins it declared."""

    path: Path
    module: Any
    plugins: tuple[Plugin, ...] = field(default_factory=tuple)


def _module_name(path: Path) -> str:
    """A stable, importable name for one plugin *file*.

    Keyed on the resolved path, not the directory name: two packs may both live
    in a directory called ``overclock``, and letting the second evict the first
    from ``sys.modules`` would orphan the first's :class:`Plugin` while its ops
    stayed registered — which surfaces as a baffling "already registered" the
    next time either is loaded. The digest keeps the readable name and makes the
    key unique.
    """
    stem = path.parent.name or "pack"
    safe = "".join(char if char.isalnum() or char == "_" else "_" for char in stem)
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]
    return f"{MODULE_PREFIX}.{safe}_{digest}"


def import_plugin(path: Path) -> LoadedPlugin:
    """Import one ``plugin.py`` and collect the :class:`Plugin` objects in it.

    Raises :class:`ContentError` — a modder's syntax error should read like
    every other content problem, with the file in the ``where`` column.
    """
    if not path.is_file():
        raise ContentError(
            ContentIssue(
                path.as_posix(),
                "pack.yaml names a plugin, but the file is missing",
                hint="set 'plugin: null' in pack.yaml if the pack no longer needs one",
            ),
            "plugin failed to load",
        )

    # Resolve first: the same plugin reached once as `data/variants/x` and once
    # as an absolute path must be the same module, or the second import builds a
    # second `Plugin` whose `install()` collides with the first one's ops.
    path = path.resolve()
    name = _module_name(path)
    cached = sys.modules.get(name)
    if cached is not None and getattr(cached, "__file__", None) == str(path):
        module = cached
    else:
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise ContentError(
                ContentIssue(path.as_posix(), "not an importable Python module"),
                "plugin failed to load",
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            sys.modules.pop(name, None)
            raise ContentError(
                ContentIssue(
                    path.as_posix(),
                    f"{type(exc).__name__} while importing: {exc}",
                    hint="a plugin is ordinary Python — run it directly to see the traceback",
                ),
                "plugin failed to load",
            ) from exc

    found = tuple(value for _, value in sorted(vars(module).items()) if isinstance(value, Plugin))
    return LoadedPlugin(path=path, module=module, plugins=found)


def load_plugins(
    content: ContentRegistry,
    *,
    vocabulary: Vocabulary = BASE_VOCABULARY,
    install: bool = True,
) -> Vocabulary:
    """Import every loaded pack's plugin; return the vocabulary they imply.

    This is the one function that bridges ``content/`` and ``core/``: the
    content layer only *finds* ``plugin.py`` (it may not import the engine), and
    the engine never reads a pack directory. The two meet here.
    """
    for path in content.plugin_paths:
        for plugin in import_plugin(path).plugins:
            if install:
                plugin.install()
            vocabulary = plugin.extend(vocabulary)
    return vocabulary


def loaded_plugins(content: ContentRegistry) -> Iterator[LoadedPlugin]:
    """Import each plugin without installing anything — for ``diff-pack``."""
    for path in content.plugin_paths:
        yield import_plugin(path)


def ops_of(plugins: Iterable[Plugin]) -> dict[str, tuple[str, ...]]:
    """``{"effects": ("upload_card",), ...}`` for a set of plugins."""
    out: dict[str, list[str]] = {}
    for plugin in plugins:
        for table, name in plugin.registrations:
            out.setdefault(table, []).append(name)
    return {table: tuple(sorted(names)) for table, names in sorted(out.items())}


__all__ = [
    "LoadedPlugin",
    "Plugin",
    "import_plugin",
    "load_plugins",
    "loaded_plugins",
    "ops_of",
]
