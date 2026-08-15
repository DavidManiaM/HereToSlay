"""``EffectContext`` — the only API an effect gets.

Everything a card can do goes through this object: read the state, resolve a
``$ref``, pick a target set, ask a player a question, emit an event, run a
nested effect. Keeping the surface narrow is the point — a modder's op that
reaches into engine internals is an op that breaks when the internals move
(``docs/architecture_notes.md §5``).

Three things live here rather than anywhere else:

* **Reference resolution.** ``$self``, ``$event.card``, ``$rules.turn.hand_limit``
  and ``$victim`` are all one mechanism. :mod:`here_to_slay.core.refs` does the
  string work; this module supplies the roots and the coercions
  (``resolve_player`` accepts a seat, a card's controller, or a binding).
* **Scoping.** A context is immutable and cheap to derive. ``for_each`` runs its
  body in ``ctx.bind(item=...)``; a trigger runs in a context whose ``$self`` is
  the subscribing card's controller. Bindings therefore cannot leak sideways
  between branches, which is exactly the guarantee the validator assumes when it
  checks that ``$victim`` is bound before it is used.
* **Asking.** ``ask_*`` are generators that yield a ``Request`` and return the
  answer. They also *skip the question* when the answer is forced (one legal
  target, or none) — a CLI that prompts "choose 1 of 1" for every card is a CLI
  nobody wants to play, and the auto-answer is a pure function of state, so the
  replay stays exact.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from here_to_slay.content.registry import ContentRegistry
from here_to_slay.content.schema import CardDef, RuleSet
from here_to_slay.core.errors import EffectError
from here_to_slay.core.events import Event, EventFrame, EventResult, Outcome
from here_to_slay.core.ids import CardId, PlayerId, ZoneId, zone_id
from here_to_slay.core.interpreter import (
    ChooseCards,
    ChooseOption,
    ChoosePlayer,
    Confirm,
    Flow,
    Intent,
    Option,
    Request,
)
from here_to_slay.core.refs import evaluate_expression, follow_path, is_ref, split_ref
from here_to_slay.core.registry import CONDITIONS, EFFECTS, SELECTORS
from here_to_slay.core.state import CardInstance, GameState, PlayerState
from here_to_slay.core.zones import Zone

Params = dict[str, Any]
Node = Any  # an EffectNode/ConditionNode/SelectorNode, or the raw dict behind it


def node_dict(node: Node) -> dict[str, Any]:
    """Flatten a pydantic op node (or the raw dict inside one) to a plain dict.

    Nested parameters keep their raw form on the way through ``content/`` — an
    ``EffectNode``'s ``steps`` are dicts, not ``EffectNode``s — so every walker
    has to accept both shapes.
    """
    if node is None:
        return {}
    if isinstance(node, Mapping):
        return dict(node)
    op = getattr(node, "op", None)
    if op is not None:
        return {"op": op, **getattr(node, "params", {})}
    selector = getattr(node, "selector", None)
    if selector is not None:
        return {"selector": selector, **getattr(node, "params", {})}
    raise EffectError(f"expected an op node, got {type(node).__name__}: {node!r}")


@dataclass(frozen=True, slots=True)
class Binding:
    """What an op hands back when it introduces a ``$binding``.

    Returning this rather than writing into a shared scope is what keeps
    ``choose`` lexically honest: :meth:`EffectContext.run` unwraps it into the
    *immediately* enclosing sequence and nowhere else, so a ``choose`` buried
    inside an ``if`` cannot bleed its name out to the ``if``'s siblings — which
    is exactly the rule the content validator enforces at load time.
    """

    name: str
    value: Any


@dataclass(slots=True)
class Execution:
    """State shared by every context in one top-level run.

    The frame stack is here (not on the context) because cancellation and the
    reaction-depth cap are properties of *this dispatch*, and a derived context
    must see the same stack its parent does.
    """

    state: GameState
    stack: list[EventFrame] = field(default_factory=list)
    history: list[Event] = field(default_factory=list)
    #: how many events to keep for an invariant report
    max_history: int = 40
    #: what the step that just finished exported as a ``$binding`` (see
    #: :meth:`EffectContext.run_sequence`)
    pending_binding: tuple[str, Any] | None = None

    def push(self, frame: EventFrame) -> EventFrame:
        frame.depth = len(self.stack)
        self.stack.append(frame)
        return frame

    def pop(self) -> EventFrame:
        return self.stack.pop()

    def record(self, event: Event) -> None:
        self.history.append(event)
        if len(self.history) > self.max_history:
            del self.history[: len(self.history) - self.max_history]

    @property
    def depth(self) -> int:
        return len(self.stack)

    @property
    def frame(self) -> EventFrame | None:
        return self.stack[-1] if self.stack else None

    @property
    def cancelled(self) -> bool:
        """True while any in-flight event has been cancelled."""
        return any(frame.cancelled for frame in self.stack)

    def recent(self, count: int = 20) -> tuple[str, ...]:
        return tuple(str(event) for event in self.history[-count:])


@dataclass(frozen=True, slots=True)
class EffectContext:
    """One lexical scope of an effect tree."""

    execution: Execution
    #: ``$self`` — the player this effect acts for
    self_player: PlayerId | None = None
    #: ``$card`` — the card instance the effect came from
    source: CardId | None = None
    #: ``$event`` — the event being reacted to, if any
    event: Event | None = None
    #: ``$<name>`` — everything ``choose``/``for_each``/filters have bound
    bindings: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    #: ``$intent`` — the action being resolved (Phase 4)
    intent: Intent | None = None
    #: ``$roll`` — the roll being resolved (Phase 4 fills this in)
    roll: Any | None = None

    # -- construction ------------------------------------------------------

    @classmethod
    def root(
        cls,
        state: GameState,
        *,
        player: PlayerId | None = None,
        source: CardId | None = None,
        event: Event | None = None,
        bindings: Mapping[str, Any] | None = None,
        intent: Intent | None = None,
    ) -> EffectContext:
        """Start a fresh execution. One per action, trigger chain or test."""
        return cls(
            execution=Execution(state=state),
            self_player=player,
            source=source,
            event=event,
            bindings=MappingProxyType(dict(bindings or {})),
            intent=intent,
        )

    def derive(self, **changes: Any) -> EffectContext:
        """A child scope sharing the same execution."""
        if "bindings" in changes:
            changes["bindings"] = MappingProxyType(dict(changes["bindings"]))
        return replace(self, **changes)

    def bind(self, **values: Any) -> EffectContext:
        """A child scope with extra ``$bindings``."""
        return self.derive(bindings={**self.bindings, **values})

    # -- shortcuts ---------------------------------------------------------

    @property
    def state(self) -> GameState:
        return self.execution.state

    @property
    def rules(self) -> RuleSet:
        return self.execution.state.rules

    @property
    def content(self) -> ContentRegistry:
        return self.execution.state.content

    @property
    def me(self) -> PlayerId:
        """``$self``, defaulting to whoever's turn it is."""
        return self.self_player or self.state.active_player

    @property
    def frame(self) -> EventFrame | None:
        return self.execution.frame

    @property
    def aborted(self) -> bool:
        """True once an in-flight event has been cancelled underneath us."""
        return self.execution.cancelled

    def opponents(self, of: PlayerId | None = None) -> tuple[PlayerId, ...]:
        return self.state.opponents_of(of or self.me)

    def zone_of(self, kind: str, owner: PlayerId | None = None) -> Zone:
        return self.state.zone_of(kind, owner)

    def cards_in(self, kind: str, owner: PlayerId | None = None) -> tuple[CardInstance, ...]:
        return self.state.cards_in(zone_id(kind, owner))

    def definition(self, card: CardId) -> CardDef:
        return self.state.definition(card)

    # -- references --------------------------------------------------------

    def resolve(self, value: Any, *, where: str = "") -> Any:
        """Resolve any raw parameter value: refs, exprs, selectors, literals."""
        if is_ref(value):
            return self.resolve_ref(value)
        if isinstance(value, Mapping):
            if "expr" in value:
                return evaluate_expression(str(value["expr"]), self.resolve_ref)
            if "selector" in value:
                return self.select(value)
            return {key: self.resolve(item, where=where) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve(item, where=where) for item in value]
        return value

    def resolve_ref(self, ref: str) -> Any:
        root, path = split_ref(ref)
        value = self._root(root, ref)
        if not path:
            return value
        return follow_path(value, path, where=f"${root}", deref=self._deref)

    def _root(self, name: str, ref: str) -> Any:
        state = self.state
        match name:
            case "self":
                return self.me
            case "card":
                return self.source
            case "event":
                if self.event is None:
                    raise EffectError(f"'{ref}' used where no event is being handled")
                return self.event
            case "rules":
                return state.rules
            case "game":
                return state
            case "action_points":
                return state.action_points
            case "active_player":
                return state.active_player
            case "players" | "any_player":
                return tuple(state.turn_order)
            case "opponents":
                return self.opponents()
            case "intent":
                if self.intent is None:
                    raise EffectError(f"'{ref}' used outside an action")
                return self.intent
            case "roll":
                if self.roll is None:
                    raise EffectError(f"'{ref}' used where no roll is in progress")
                return self.roll
        if name in self.bindings:
            return self.bindings[name]
        raise EffectError(
            f"reference '${name}' is not bound here"
            + (f" (bound: {sorted(self.bindings)})" if self.bindings else "")
        )

    def _deref(self, value: Any) -> Any:
        """Let a dotted path step from a card id onto the card itself."""
        if isinstance(value, str) and value in self.state.cards:
            return self.state.cards[CardId(value)]
        if isinstance(value, str) and value in self.state.players:
            return self.state.players[PlayerId(value)]
        return value

    # -- typed resolution --------------------------------------------------

    def resolve_player(self, value: Any, *, default: PlayerId | None = None) -> PlayerId:
        resolved = self.resolve(value) if value is not None else None
        if resolved is None:
            return default if default is not None else self.me
        return self._as_player(resolved, value)

    def resolve_players(
        self, value: Any, *, default: Sequence[PlayerId] = ()
    ) -> tuple[PlayerId, ...]:
        resolved = self.resolve(value) if value is not None else None
        if resolved is None:
            return tuple(default)
        if isinstance(resolved, str | PlayerState):
            return (self._as_player(resolved, value),)
        if isinstance(resolved, Iterable):
            return tuple(self._as_player(item, value) for item in resolved)
        return (self._as_player(resolved, value),)

    def _as_player(self, resolved: Any, original: Any) -> PlayerId:
        if isinstance(resolved, PlayerState):
            return resolved.id
        if isinstance(resolved, CardInstance):
            if resolved.controller is None:
                raise EffectError(f"card '{resolved.id}' has no controller to act as a player")
            return resolved.controller
        if isinstance(resolved, str):
            if resolved in self.state.players:
                return PlayerId(resolved)
            if resolved in self.state.cards:
                return self._as_player(self.state.cards[CardId(resolved)], original)
        raise EffectError(f"expected a player, got {resolved!r} (from {original!r})")

    def resolve_card(self, value: Any) -> CardId:
        cards = self.resolve_cards(value)
        if len(cards) != 1:
            raise EffectError(f"expected exactly one card from {value!r}, got {len(cards)}")
        return cards[0]

    def resolve_cards(self, value: Any) -> tuple[CardId, ...]:
        resolved = self.resolve(value) if value is not None else None
        if resolved is None:
            return ()
        return tuple(self._as_card(item) for item in _iterate(resolved))

    def _as_card(self, value: Any) -> CardId:
        if isinstance(value, CardInstance):
            return value.id
        if isinstance(value, str) and value in self.state.cards:
            return CardId(value)
        raise EffectError(f"expected a card instance, got {value!r}")

    def resolve_int(self, value: Any, default: int = 0) -> int:
        resolved = self.resolve(value) if value is not None else None
        if resolved is None:
            return default
        if isinstance(resolved, bool):
            return int(resolved)
        if isinstance(resolved, int):
            return resolved
        if isinstance(resolved, float):
            return int(resolved)
        if isinstance(resolved, str) and resolved.lstrip("-").isdigit():
            return int(resolved)
        if isinstance(resolved, Sequence):
            return len(resolved)
        raise EffectError(f"expected a number, got {resolved!r} (from {value!r})")

    def resolve_bool(self, value: Any, default: bool = False) -> bool:
        resolved = self.resolve(value) if value is not None else None
        return default if resolved is None else bool(resolved)

    def resolve_text(self, value: Any, default: str = "") -> str:
        resolved = self.resolve(value) if value is not None else None
        return default if resolved is None else str(resolved)

    def resolve_zone(self, value: Any, *, owner: PlayerId | None = None) -> ZoneId:
        """Turn ``{zone: hand, player: $p}``, ``"discard"`` or a ``$ref`` into a
        concrete zone id.

        A bare player-scoped kind (``{zone: hand}``) belongs to ``owner`` — the
        op's target if it has one, else ``$self``. That is what lets a card say
        "discard to your hand" without restating whose hand it means.
        """
        if value is None:
            raise EffectError("a zone reference is required here")
        target_owner = owner
        kind: Any = value
        if isinstance(value, Mapping) and "zone" in value:
            kind = value["zone"]
            if value.get("player") is not None:
                target_owner = self.resolve_player(value["player"])
        resolved = self.resolve(kind)
        if isinstance(resolved, Zone):
            return resolved.id
        if not isinstance(resolved, str):
            raise EffectError(f"expected a zone reference, got {resolved!r}")
        if resolved in self.state.zones:
            return ZoneId(resolved)
        candidate = zone_id(resolved, target_owner or self.me)
        if candidate in self.state.zones:
            return candidate
        raise EffectError(
            f"no such zone '{resolved}'"
            + (f" for player '{target_owner or self.me}'" if target_owner or self.me else "")
        )

    # -- selectors, conditions, filters ------------------------------------

    def select(self, node: Node) -> tuple[Any, ...]:
        """Evaluate a selector node (or a ``$ref`` standing in for one).

        Selectors yield **ids** — ``CardId``s and ``PlayerId``s, never
        ``CardInstance``s — so that ``exclude:`` can compare them, a filter can
        bind one as ``$candidate``, and a decision log can print one.
        """
        if node is None:
            return ()
        if is_ref(node):
            return tuple(_iterate(self.resolve_ref(node)))
        if isinstance(node, list | tuple):
            return tuple(item for entry in node for item in _iterate(self.resolve(entry)))
        data = node_dict(node)
        name = data.get("selector")
        if not isinstance(name, str):
            raise EffectError(f"expected a selector node, got {node!r}")
        handler = SELECTORS.get(name)
        results = tuple(handler(self, data))
        results = self._apply_exclusions(results, data)
        results = self._apply_filter(results, data.get("where"))
        limit = data.get("limit")
        if limit is not None:
            results = results[: self.resolve_int(limit)]
        return results

    def _apply_exclusions(self, items: Sequence[Any], data: Params) -> tuple[Any, ...]:
        excluded = data.get("exclude")
        if excluded is None:
            return tuple(items)
        drop = {
            entry.id if isinstance(entry, CardInstance | PlayerState) else entry
            for entry in _iterate(self.resolve(excluded))
        }
        return tuple(item for item in items if item not in drop)

    def _apply_filter(self, items: Sequence[Any], filter_node: Node) -> tuple[Any, ...]:
        if filter_node is None:
            return tuple(items)
        return tuple(item for item in items if self.matches(filter_node, item))

    def matches(self, filter_node: Node, candidate: Any) -> bool:
        """Evaluate a filter against one candidate, with ``$candidate`` bound."""
        return self.bind(candidate=candidate).test(filter_node)

    def test(self, node: Node) -> bool:
        """Evaluate a condition node. ``None`` means "no condition" — true."""
        if node is None:
            return True
        data = node_dict(node)
        op = data.get("op")
        if not isinstance(op, str):
            raise EffectError(f"expected a condition node, got {node!r}")
        try:
            return bool(CONDITIONS.get(op)(self, data))
        except EffectError as error:
            raise EffectError(f"in condition '{op}': {error}") from error

    def test_all(self, nodes: Iterable[Node]) -> bool:
        return all(self.test(node) for node in nodes)

    # -- running effects ---------------------------------------------------

    def run(self, node: Node) -> Flow:
        """Execute one effect node. The interpreter's fundamental step."""
        if node is None:
            return Outcome.DONE
        data = node_dict(node)
        op = data.get("op")
        if not isinstance(op, str):
            raise EffectError(f"expected an effect node, got {node!r}")
        handler = EFFECTS.get(op)
        try:
            result = yield from handler(self, data)
        except EffectError as error:
            raise EffectError(f"in effect '{op}': {error}") from error

        # Only a binding this op returned *itself* reaches the enclosing
        # sequence; one produced deeper down was already consumed there.
        if isinstance(result, Binding):
            self.execution.pending_binding = (result.name, result.value)
            result = Outcome.DONE
        else:
            self.execution.pending_binding = None

        if self.aborted:
            return Outcome.CANCELLED
        return result if isinstance(result, Outcome) else Outcome.DONE

    def run_sequence(self, nodes: Iterable[Node]) -> Flow:
        """Run effects in order, threading exported bindings forward.

        ``choose`` binds ``$victim`` "for later steps"
        (``docs/card_schemas.md §3``), and that is a *lexical sibling* scope: a
        ``choose`` nested inside an ``if`` does not leak out of it. Threading a
        derived context through this loop — rather than mutating one shared
        binding dict — is what makes that true, and it matches exactly what the
        content validator assumes when it decides whether ``$victim`` is bound.
        """
        scope = self
        for node in nodes:
            outcome = yield from scope.run(node)
            if outcome.is_cancelled or self.aborted:
                return Outcome.CANCELLED
            exported = self.execution.pending_binding
            if exported is not None:
                self.execution.pending_binding = None
                scope = scope.bind(**{exported[0]: exported[1]})
        return Outcome.DONE

    # -- events ------------------------------------------------------------

    def emit(
        self,
        name: str,
        payload: Mapping[str, Any] | None = None,
        *,
        actor: PlayerId | None = None,
        source: CardId | None = None,
    ) -> Flow:
        """Announce an event and run it through the three-phase bus.

        Returns an :class:`EventResult`; the caller decides what a cancellation
        means for it. Importing the bus lazily keeps the module graph acyclic —
        the bus builds contexts, so it may not import this module at run time.
        """
        from here_to_slay.core.bus import dispatch

        event = Event(
            name=name,
            payload=dict(payload or {}),
            actor=actor if actor is not None else self.me,
            source=source if source is not None else self.source,
        )
        result: EventResult = yield from dispatch(self, event)
        return result

    def cancel_event(self, reason: str = "", *, and_discard: bool = False) -> None:
        """Cancel the event currently being dispatched (``op: cancel_event``)."""
        frame = self.frame
        if frame is None:
            raise EffectError("cancel_event was used where no event is being dispatched")
        frame.cancel(reason, and_discard=and_discard)

    # -- asking ------------------------------------------------------------

    def ask_choose_cards(
        self,
        candidates: Sequence[CardId],
        *,
        chooser: PlayerId | None = None,
        minimum: int = 1,
        maximum: int = 1,
        prompt: str = "",
        from_zone: ZoneId | None = None,
        hidden: bool = False,
    ) -> Flow:
        """Ask for cards. Returns a tuple — possibly empty, never illegal."""
        pool = tuple(candidates)
        maximum = min(maximum, len(pool))
        minimum = min(minimum, maximum)
        if maximum <= 0:
            return ()
        if minimum == maximum == len(pool):
            return pool  # forced: do not make the player click through it
        answer = yield ChooseCards(
            requester=chooser or self.me,
            prompt=prompt,
            candidates=pool,
            minimum=minimum,
            maximum=maximum,
            from_zone=from_zone,
            hidden=hidden,
        )
        return tuple(answer)

    def ask_choose_player(
        self,
        candidates: Sequence[PlayerId],
        *,
        chooser: PlayerId | None = None,
        prompt: str = "",
    ) -> Flow:
        pool = tuple(candidates)
        if not pool:
            return None
        if len(pool) == 1:
            return pool[0]
        answer = yield ChoosePlayer(requester=chooser or self.me, prompt=prompt, candidates=pool)
        return answer

    def ask_choose_option(
        self,
        options: Sequence[Option],
        *,
        chooser: PlayerId | None = None,
        prompt: str = "",
    ) -> Flow:
        pool = tuple(options)
        if not pool:
            return None
        if len(pool) == 1:
            return pool[0].key
        answer = yield ChooseOption(requester=chooser or self.me, prompt=prompt, options=pool)
        return answer

    def ask_confirm(self, prompt: str, *, chooser: PlayerId | None = None) -> Flow:
        answer = yield Confirm(requester=chooser or self.me, prompt=prompt)
        return bool(answer)

    def ask(self, request: Request) -> Flow:
        """Escape hatch for a plugin that invents its own request type."""
        answer = yield request
        return answer

    # -- diagnostics -------------------------------------------------------

    def describe(self, card: CardId) -> str:
        """A card's name for a prompt — never its raw id, which players hate."""
        try:
            return self.state.definition(card).name
        except Exception:  # pragma: no cover - defensive: unknown definition
            return str(card)

    def __repr__(self) -> str:
        return (
            f"<EffectContext self={self.self_player} card={self.source} "
            f"event={self.event.name if self.event else None} depth={self.execution.depth}>"
        )


def _iterate(value: Any) -> tuple[Any, ...]:
    """Treat a single value and a list of values the same way.

    Card data is written by humans: ``target: $victim`` and
    ``target: [$a, $b]`` should both work, and neither should iterate a string
    into characters.
    """
    if value is None:
        return ()
    if isinstance(value, str | CardInstance | PlayerState | Zone):
        return (value,)
    if isinstance(value, Mapping):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(value)
    return (value,)


__all__ = ["Binding", "EffectContext", "Execution", "node_dict"]
