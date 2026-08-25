"""The declared vocabulary: which ops exist and what shape their params are.

This is *data about the language*, not game rules — which is why it lives in
``content/`` and why the semantic validator can check a pack without importing
the engine (``content/`` never imports ``core/``).

Two consumers:

* :mod:`here_to_slay.content.validate` — checks that every ``op`` a pack uses is
  known, that required params are present, and that ``$refs`` are bindable.
* the effect interpreter (Phase 3) — walks the same param roles to know which
  values are nested effects, conditions or selectors.

A pack's ``plugin.py`` extends the vocabulary via :meth:`Vocabulary.extend`, so
adding a verb never means editing this file.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class Role(StrEnum):
    """What kind of thing a parameter holds."""

    EFFECT = "effect"
    EFFECT_LIST = "effect_list"
    CONDITION = "condition"
    CONDITION_LIST = "condition_list"
    FILTER = "filter"  # a condition evaluated per candidate card ($candidate is bound)
    SELECTOR = "selector"  # a $ref or a {selector: ...} node
    REF = "ref"  # a $ref (or a literal id)
    REF_LIST = "ref_list"
    ZONE = "zone"  # {zone: x} or {player: $p, zone: x}
    VALUE = "value"  # int, string, $ref or {expr: "..."}
    NAME = "name"  # a bare identifier (binding name, flag key, event name)
    BAND_LIST = "band_list"
    OPTION_LIST = "option_list"  # [{label, effect, condition?}]
    ROLL_SPEC = "roll_spec"  # {roller, dice, kind}
    ANY = "any"


class OpKind(StrEnum):
    EFFECT = "effect"
    CONDITION = "condition"
    SELECTOR = "selector"


@dataclass(frozen=True, slots=True)
class ParamSpec:
    role: Role = Role.ANY
    required: bool = False


@dataclass(frozen=True, slots=True)
class OpSpec:
    """One entry in the op catalogue."""

    name: str
    kind: OpKind
    params: Mapping[str, ParamSpec] = field(default_factory=dict)
    #: parameter holding the name this op introduces as a ``$binding``
    binds: str | None = None
    #: where that binding is visible: inside ``body`` params, or in later siblings
    bind_scope: str = "body"
    #: params that form this op's body (a binding introduced here is live inside)
    body: tuple[str, ...] = ()
    doc: str = ""

    def role_of(self, param: str) -> Role:
        spec = self.params.get(param)
        return spec.role if spec else Role.ANY

    @property
    def required_params(self) -> tuple[str, ...]:
        return tuple(name for name, spec in self.params.items() if spec.required)


def _p(**kwargs: Role | tuple[Role, bool] | ParamSpec) -> dict[str, ParamSpec]:
    """Terse param table: ``_p(count=Role.VALUE, target=(Role.REF, True))``.

    Params whose name is a Python keyword (``else``, ``from``, ``class``) are
    passed as ``**{"else": ParamSpec(...)}``.
    """
    out: dict[str, ParamSpec] = {}
    for name, value in kwargs.items():
        if isinstance(value, ParamSpec):
            out[name] = value
        elif isinstance(value, tuple):
            out[name] = ParamSpec(value[0], value[1])
        else:
            out[name] = ParamSpec(value)
    return out


R = Role
REQ = True


# ---------------------------------------------------------------------------
# Effects — docs/card_schemas.md §3.1
# ---------------------------------------------------------------------------

_EFFECTS: tuple[OpSpec, ...] = (
    # -- control flow --
    OpSpec(
        "seq",
        OpKind.EFFECT,
        _p(steps=(R.EFFECT_LIST, REQ)),
        body=("steps",),
        doc="run in order; abort if one is cancelled",
    ),
    OpSpec(
        "if",
        OpKind.EFFECT,
        _p(condition=(R.CONDITION, REQ), then=(R.EFFECT, REQ), **{"else": ParamSpec(R.EFFECT)}),  # type: ignore[arg-type]
        body=("then", "else"),
    ),
    OpSpec(
        "choose_effect",
        OpKind.EFFECT,
        _p(chooser=R.REF, options=(R.OPTION_LIST, REQ), prompt=R.VALUE),
        body=("options",),
        doc="player picks a branch",
    ),
    OpSpec(
        "repeat", OpKind.EFFECT, _p(times=(R.VALUE, REQ), effect=(R.EFFECT, REQ)), body=("effect",)
    ),
    OpSpec(
        "for_each",
        OpKind.EFFECT,
        _p(over=(R.SELECTOR, REQ), bind=(R.NAME, REQ), effect=(R.EFFECT, REQ)),
        binds="bind",
        bind_scope="body",
        body=("effect",),
    ),
    OpSpec("noop", OpKind.EFFECT, doc="explicit 'nothing happens'"),
    OpSpec(
        "optional",
        OpKind.EFFECT,
        _p(effect=(R.EFFECT, REQ), prompt=R.VALUE, chooser=R.REF),
        body=("effect",),
    ),
    OpSpec(
        "choose",
        OpKind.EFFECT,
        _p(
            bind=(R.NAME, REQ),
            chooser=R.REF,
            **{"from": ParamSpec(R.SELECTOR, REQ)},  # type: ignore[arg-type]
            prompt=R.VALUE,
            count=R.VALUE,
            min=R.VALUE,
            max=R.VALUE,
            optional=R.VALUE,
        ),
        binds="bind",
        bind_scope="siblings",
        doc="ask a player to pick; binds the result for later steps",
    ),
    # -- cards & zones --
    OpSpec(
        "draw",
        OpKind.EFFECT,
        _p(
            target=R.REF,
            count=R.VALUE,
            **{"from": ParamSpec(R.ZONE)},  # type: ignore[arg-type]
            bind=R.NAME,
            then=R.EFFECT,
        ),
        binds="bind",
        bind_scope="body",
        body=("then",),
        doc=(
            "draw from the top of a deck (``main_deck`` unless told otherwise); "
            "'bind' names what was drawn so 'then' can inspect it"
        ),
    ),
    OpSpec(
        "discard",
        OpKind.EFFECT,
        _p(
            target=R.REF, count=R.VALUE, random=R.VALUE, chooser=R.REF, filter=R.FILTER, zone=R.ZONE
        ),
    ),
    OpSpec("move_card", OpKind.EFFECT, _p(card=(R.REF, REQ), to=(R.ZONE, REQ), position=R.VALUE)),
    OpSpec(
        "steal_card",
        OpKind.EFFECT,
        _p(
            **{"from": ParamSpec(R.REF, REQ)},
            to=R.REF,
            count=R.VALUE,
            random=R.VALUE,
            chooser=R.REF,
            bind=R.NAME,
            then=R.EFFECT,
        ),  # type: ignore[arg-type]
        binds="bind",
        bind_scope="body",
        body=("then",),
        doc="'bind' names what was pulled, so 'then' can ask what it was",
    ),
    OpSpec(
        "swap_zones",
        OpKind.EFFECT,
        _p(a=(R.ZONE, REQ), b=(R.ZONE, REQ)),
        doc="exchange the entire contents of two zones ('trade hands')",
    ),
    OpSpec(
        "search",
        OpKind.EFFECT,
        _p(
            zone=(R.ZONE, REQ),
            filter=R.FILTER,
            count=R.VALUE,
            bind=R.NAME,
            then=R.EFFECT,
            chooser=R.REF,
        ),
        binds="bind",
        bind_scope="body",
        body=("then",),
        doc="look through a zone; 'bind' names what was found so 'then' can use it",
    ),
    OpSpec("reveal", OpKind.EFFECT, _p(card=R.REF, zone=R.ZONE, count=R.VALUE, to=R.REF)),
    OpSpec("shuffle", OpKind.EFFECT, _p(zone=(R.ZONE, REQ))),
    OpSpec(
        "play_card_from_hand",
        OpKind.EFFECT,
        _p(kind=R.VALUE, card=R.REF, challengeable=R.VALUE, hero=R.REF),
        doc="hand -> limbo -> wherever the card's own kind says it goes",
    ),
    # -- party & board --
    OpSpec(
        "steal_hero",
        OpKind.EFFECT,
        _p(**{"from": ParamSpec(R.REF)}, chooser=R.REF, to=R.REF, filter=R.FILTER),
    ),  # type: ignore[arg-type]
    OpSpec("destroy_hero", OpKind.EFFECT, _p(target=R.REF, chooser=R.REF, filter=R.FILTER)),
    OpSpec("sacrifice", OpKind.EFFECT, _p(target=R.REF, filter=R.FILTER)),
    OpSpec("equip_item", OpKind.EFFECT, _p(item=(R.REF, REQ), hero=R.REF, filter=R.FILTER)),
    OpSpec("unequip_item", OpKind.EFFECT, _p(item=(R.REF, REQ), to_zone=R.ZONE)),
    OpSpec("return_monster", OpKind.EFFECT, _p(monster=(R.REF, REQ), to=R.ZONE)),
    OpSpec("refill_monster_row", OpKind.EFFECT),
    OpSpec("slay_monster", OpKind.EFFECT, _p(monster=(R.REF, REQ), by=R.REF)),
    OpSpec("attack_monster", OpKind.EFFECT, _p(monster=(R.REF, REQ), attacker=R.REF)),
    OpSpec("use_ability", OpKind.EFFECT, _p(card=(R.REF, REQ), user=R.REF)),
    # -- rolls --
    OpSpec(
        "roll",
        OpKind.EFFECT,
        _p(dice=R.VALUE, kind=R.VALUE, roller=R.REF, outcomes=(R.BAND_LIST, REQ)),
        body=("outcomes",),
    ),
    OpSpec(
        "modify_roll", OpKind.EFFECT, _p(amount=(R.VALUE, REQ), source=R.REF, target_roll=R.REF)
    ),
    OpSpec("reroll", OpKind.EFFECT, _p(dice=R.VALUE, roller=R.REF)),
    OpSpec(
        "contest_roll",
        OpKind.EFFECT,
        _p(
            a=(R.ROLL_SPEC, REQ),
            b=(R.ROLL_SPEC, REQ),
            on_a_wins=(R.EFFECT, REQ),
            on_b_wins=(R.EFFECT, REQ),
            on_tie=R.EFFECT,
        ),
        body=("on_a_wins", "on_b_wins", "on_tie"),
    ),
    OpSpec("set_roll_result", OpKind.EFFECT, _p(value=(R.VALUE, REQ), target_roll=R.REF)),
    # -- resources & meta --
    OpSpec("gain_action_points", OpKind.EFFECT, _p(target=R.REF, amount=(R.VALUE, REQ))),
    OpSpec("spend_action_points", OpKind.EFFECT, _p(target=R.REF, amount=(R.VALUE, REQ))),
    OpSpec("set_action_points", OpKind.EFFECT, _p(target=R.REF, value=(R.VALUE, REQ))),
    OpSpec("extra_turn", OpKind.EFFECT, _p(target=R.REF)),
    OpSpec("end_turn", OpKind.EFFECT),
    OpSpec("enforce_hand_limit", OpKind.EFFECT, _p(target=R.REF)),
    OpSpec("cancel_event", OpKind.EFFECT, _p(and_discard=R.VALUE, reason=R.VALUE)),
    OpSpec(
        "set_flag",
        OpKind.EFFECT,
        _p(scope=(R.VALUE, REQ), key=(R.NAME, REQ), value=R.VALUE, target=R.REF),
    ),
    OpSpec("clear_flag", OpKind.EFFECT, _p(scope=(R.VALUE, REQ), key=(R.NAME, REQ), target=R.REF)),
    OpSpec("emit", OpKind.EFFECT, _p(event=(R.NAME, REQ), payload=R.ANY)),
    OpSpec("win_game", OpKind.EFFECT, _p(target=R.REF)),
)


# ---------------------------------------------------------------------------
# Conditions — docs/card_schemas.md §4
# ---------------------------------------------------------------------------

_CONDITIONS: tuple[OpSpec, ...] = (
    OpSpec("all", OpKind.CONDITION, _p(of=(R.CONDITION_LIST, REQ)), body=("of",)),
    OpSpec("any", OpKind.CONDITION, _p(of=(R.CONDITION_LIST, REQ)), body=("of",)),
    OpSpec("not", OpKind.CONDITION, _p(of=(R.CONDITION, REQ)), body=("of",)),
    OpSpec("always", OpKind.CONDITION),
    OpSpec("never", OpKind.CONDITION),
    OpSpec("not_self", OpKind.CONDITION, doc="the acting player is not $self"),
    OpSpec(
        "party_has_class",
        OpKind.CONDITION,
        _p(player=R.REF, **{"class": ParamSpec(R.VALUE, REQ)}, min=R.VALUE),
    ),  # type: ignore[arg-type]
    OpSpec("party_covers_all_classes", OpKind.CONDITION, _p(player=R.REF)),
    OpSpec(
        "party_size", OpKind.CONDITION, _p(player=R.REF, cmp=(R.VALUE, REQ), value=(R.VALUE, REQ))
    ),
    OpSpec(
        "hand_size", OpKind.CONDITION, _p(player=R.REF, cmp=(R.VALUE, REQ), value=(R.VALUE, REQ))
    ),
    OpSpec(
        "discard_size", OpKind.CONDITION, _p(player=R.REF, cmp=(R.VALUE, REQ), value=(R.VALUE, REQ))
    ),
    OpSpec(
        "slain_count", OpKind.CONDITION, _p(player=R.REF, cmp=(R.VALUE, REQ), value=(R.VALUE, REQ))
    ),
    OpSpec(
        "has_card",
        OpKind.CONDITION,
        _p(player=R.REF, zone=(R.ZONE, REQ), filter=R.FILTER, min=R.VALUE),
    ),
    OpSpec("card_has_tag", OpKind.CONDITION, _p(card=R.REF, tag=(R.VALUE, REQ))),
    OpSpec("card_kind_is", OpKind.CONDITION, _p(card=R.REF, kind=(R.VALUE, REQ))),
    OpSpec(
        "card_tapped",
        OpKind.CONDITION,
        _p(card=R.REF),
        doc="has this card already been used this turn?",
    ),
    OpSpec(
        "card_entered_play_this_turn",
        OpKind.CONDITION,
        _p(card=R.REF),
        doc="did this card arrive on the board this turn?",
    ),
    OpSpec(
        "card_has_ability",
        OpKind.CONDITION,
        _p(card=R.REF, activation=R.VALUE),
        doc="does this card declare an activated ability?",
    ),
    OpSpec(
        "requirement_met",
        OpKind.CONDITION,
        _p(card=R.REF, player=R.REF),
        doc="does this card's own 'requirement' hold for a player? (the Monster gate)",
    ),
    OpSpec("card_class_is", OpKind.CONDITION, _p(card=R.REF, **{"class": ParamSpec(R.VALUE, REQ)})),  # type: ignore[arg-type]
    OpSpec("event_actor_is", OpKind.CONDITION, _p(player=(R.REF, REQ))),
    OpSpec(
        "event_matches",
        OpKind.CONDITION,
        _p(kind_in=R.ANY, class_in=R.ANY, tag_in=R.ANY, played_by=R.ANY, card=R.REF),
    ),
    OpSpec("roll_is", OpKind.CONDITION, _p(kind=R.VALUE, roller=R.REF, tag=R.VALUE)),
    OpSpec(
        "flag_set",
        OpKind.CONDITION,
        _p(scope=(R.VALUE, REQ), key=(R.NAME, REQ), value=R.VALUE, target=R.REF),
    ),
    OpSpec(
        "compare",
        OpKind.CONDITION,
        _p(left=(R.VALUE, REQ), cmp=(R.VALUE, REQ), right=(R.VALUE, REQ)),
    ),
)


# ---------------------------------------------------------------------------
# Selectors — docs/card_schemas.md §5
# ---------------------------------------------------------------------------

_SELECTOR_PARAMS = _p(of=R.REF, where=R.FILTER, exclude=R.REF_LIST, limit=R.VALUE)

_SELECTORS: tuple[OpSpec, ...] = tuple(
    OpSpec(name, OpKind.SELECTOR, _SELECTOR_PARAMS)
    for name in (
        "players",
        "opponents",
        "cards",
        "heroes",
        "items",
        "monsters",
        "party_leaders",
        "monster_row",
    )
)


# ---------------------------------------------------------------------------
# Events — architecture_notes.md §3.3
# ---------------------------------------------------------------------------

CORE_EVENTS: frozenset[str] = frozenset(
    (
        # turn & phase
        "turn.started", "turn.ended", "phase.changed",
        # actions
        "action.declared", "action.paid", "action.completed",
        # cards
        "card.drawn", "card.played", "card.discarded", "card.moved", "card.revealed",
        # party & board
        "hero.entered_party", "hero.left_party", "item.equipped", "item.unequipped",
        # rolls
        "roll.started", "roll.modified", "roll.resolved", "roll.banded",
        # monsters
        "monster.attacked", "monster.slain", "monster.failed", "monster_row.refilled",
        # reactions & endgame
        "challenge.declared", "challenge.resolved", "contest.resolved",
        "flag.changed", "player.won", "game.ended",
    )
)  # fmt: skip

#: ``$refs`` the engine always provides.
CORE_REFS: frozenset[str] = frozenset(
    {
        "self",
        "card",
        "event",
        "rules",
        "action_points",
        "active_player",
        # `players`/`any_player` are every seat; `opponents` is every seat but
        # `$self`. All three are resolved by EffectContext, so they belong here
        # — leaving them out made `hts validate` reject content the engine runs
        # perfectly well (found writing the base Items).
        "any_player",
        "players",
        "opponents",
        "player",
        "game",
        "roll",
        "intent",
    }
)

#: bound only inside a ``filter`` / ``where`` clause
FILTER_REF = "candidate"

VALID_CMP: frozenset[str] = frozenset({"==", "!=", "<", "<=", ">", ">="})
VALID_SCOPES: frozenset[str] = frozenset({"game", "player", "card"})

#: seat orders a reaction window may poll in (``rules.windows[...].order``).
#: Declared here, next to the rest of the language, so ``hts validate`` catches
#: a typo in a variant's window rather than the engine raising mid-game.
WINDOW_ORDERS: frozenset[str] = frozenset(
    {"seat_left_of_active", "seat_left_of_actor", "active_first", "turn_order"}
)


# ---------------------------------------------------------------------------
# The vocabulary object
# ---------------------------------------------------------------------------


class Vocabulary:
    """Lookup for op specs, selectors and known event names.

    Immutable in spirit: :meth:`extend` returns a new instance so a plugin's
    additions never leak into another pack's validation run.
    """

    def __init__(
        self,
        effects: Iterable[OpSpec] = (),
        conditions: Iterable[OpSpec] = (),
        selectors: Iterable[OpSpec] = (),
        events: Iterable[str] = (),
    ) -> None:
        self.effects: dict[str, OpSpec] = {spec.name: spec for spec in effects}
        self.conditions: dict[str, OpSpec] = {spec.name: spec for spec in conditions}
        self.selectors: dict[str, OpSpec] = {spec.name: spec for spec in selectors}
        self.events: set[str] = set(events)

    def effect(self, name: str) -> OpSpec | None:
        return self.effects.get(name)

    def condition(self, name: str) -> OpSpec | None:
        return self.conditions.get(name)

    def selector(self, name: str) -> OpSpec | None:
        return self.selectors.get(name)

    def knows_event(self, name: str) -> bool:
        return name in self.events

    def extend(
        self,
        *,
        effects: Iterable[OpSpec] = (),
        conditions: Iterable[OpSpec] = (),
        selectors: Iterable[OpSpec] = (),
        events: Iterable[str] = (),
    ) -> Vocabulary:
        return Vocabulary(
            effects=[*self.effects.values(), *effects],
            conditions=[*self.conditions.values(), *conditions],
            selectors=[*self.selectors.values(), *selectors],
            events=[*self.events, *events],
        )


BASE_VOCABULARY = Vocabulary(
    effects=_EFFECTS,
    conditions=_CONDITIONS,
    selectors=_SELECTORS,
    events=CORE_EVENTS,
)
