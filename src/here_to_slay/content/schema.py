"""Pydantic v2 models for content packs.

Two deliberate design choices govern this file:

1. **Op nodes are open.** ``EffectNode`` / ``ConditionNode`` / ``SelectorNode``
   validate the *envelope* (there is an ``op``/``selector`` key, it is a string)
   and keep the parameters as raw data. The catalogue of legal ops lives in
   :mod:`here_to_slay.content.vocabulary` and is checked by the semantic pass,
   because a pack's ``plugin.py`` may add verbs the schema cannot know about.
   Closing this union would make "add a new op" a schema edit — exactly the
   coupling the project exists to avoid.

2. **Everything is frozen.** A loaded ``ContentRegistry`` is immutable; the
   engine may read it from any number of rollouts without defensive copies.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------

ID_PATTERN = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+){2,}$")
DICE_PATTERN = re.compile(r"^(\d*)d(\d+)([+-]\d+)?$")
SLUG_PATTERN = re.compile(r"^[a-z0-9_]+$")


class Frozen(BaseModel):
    """Immutable, typo-intolerant base: unknown keys are an error."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class OpNode(BaseModel):
    """A ``{op: name, **params}`` tree node with open parameters."""

    model_config = ConfigDict(frozen=True, extra="allow")

    op: str

    @property
    def params(self) -> dict[str, Any]:
        return dict(self.model_extra or {})

    def param(self, name: str, default: Any = None) -> Any:
        return (self.model_extra or {}).get(name, default)

    @field_validator("op")
    @classmethod
    def _op_is_a_slug(cls, value: str) -> str:
        if not SLUG_PATTERN.match(value):
            raise ValueError(f"op names are lower_snake_case slugs, got {value!r}")
        return value


class EffectNode(OpNode):
    """A verb. See ``docs/card_schemas.md §3``."""


class ConditionNode(OpNode):
    """A predicate. See ``docs/card_schemas.md §4``."""


class SelectorNode(BaseModel):
    """A ``{selector: name, **params}`` node producing a list of entities."""

    model_config = ConfigDict(frozen=True, extra="allow")

    selector: str

    @property
    def params(self) -> dict[str, Any]:
        return dict(self.model_extra or {})

    def param(self, name: str, default: Any = None) -> Any:
        return (self.model_extra or {}).get(name, default)


# A cost is ``{resource: amount}``; resources are pluggable via ``@cost``.
Cost = dict[str, Any]
# A target reference: either a ``$ref`` string or a selector node.
Ref = str


# ---------------------------------------------------------------------------
# Rolls
# ---------------------------------------------------------------------------


class Band(Frozen):
    """One outcome band of a roll. ``min``/``max`` are inclusive.

    Both omitted means "catch-all"; bands are tried in declaration order and the
    first match wins. The semantic pass proves the bands cover the dice range.

    ``tag`` names the band so *other* cards can talk about it. A roll has no
    built-in notion of succeeding — a band is only a range — so a card that says
    "each time you **successfully** roll" needs the roll's author to say which
    band counts as success. Tag it ``success`` and the ``roll.banded`` event
    carries ``$event.tag``, which any trigger can read. The engine attaches no
    meaning to any particular tag; ``success`` is a convention of the base pack,
    not a keyword.
    """

    min: int | None = None
    max: int | None = None
    tag: str | None = None
    effect: EffectNode

    @model_validator(mode="after")
    def _min_le_max(self) -> Band:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"band min ({self.min}) is greater than max ({self.max})")
        return self

    def matches(self, total: int) -> bool:
        return (self.min is None or total >= self.min) and (self.max is None or total <= self.max)


class RollDef(Frozen):
    """A die roll declaration. ``kind`` tags the roll so passives can hook it."""

    dice: str = "2d6"
    kind: str = "generic"
    roller: Ref | None = None
    outcomes: list[Band] = Field(default_factory=list)

    @field_validator("dice")
    @classmethod
    def _parse_dice(cls, value: str) -> str:
        if not DICE_PATTERN.match(value.replace(" ", "")):
            raise ValueError(f"dice must look like NdM or NdM+K, got {value!r}")
        return value.replace(" ", "")

    @property
    def spec(self) -> tuple[int, int, int]:
        """``(count, faces, flat_modifier)``."""
        match = DICE_PATTERN.match(self.dice)
        assert match is not None  # guaranteed by the validator
        count, faces, flat = match.groups()
        return int(count or 1), int(faces), int(flat or 0)

    @property
    def range(self) -> tuple[int, int]:
        count, faces, flat = self.spec
        return count + flat, count * faces + flat


# ---------------------------------------------------------------------------
# Card behaviour blocks
# ---------------------------------------------------------------------------


Timing = Literal["pre", "resolve_after", "post"]
Activation = Literal["action", "passive", "triggered"]
CardKind = Literal["hero", "item", "magic", "modifier", "challenge", "monster", "party_leader"]


class TriggerDef(Frozen):
    """ "Whenever X happens, do Y" — subscription is derived from this, never
    accumulated by side effect, so it survives save/load (architecture §3.1)."""

    on: str
    timing: Timing = "post"
    while_in: str = "party"
    once_per_turn: bool = False
    priority: int = 0
    condition: ConditionNode | None = None
    effect: EffectNode


class AbilityDef(Frozen):
    """An activated or passive ability on a Hero."""

    activation: Activation = "action"
    cost: Cost = Field(default_factory=dict)
    once_per_turn: bool = True
    roll: RollDef | None = None
    effect: EffectNode | None = None

    @model_validator(mode="after")
    def _does_something(self) -> AbilityDef:
        if self.roll is None and self.effect is None:
            raise ValueError("an ability needs a roll, an effect, or both")
        return self


class PlayDef(Frozen):
    """What happens when a card is played from hand."""

    cost: Cost = Field(default_factory=lambda: {"action_points": 1})
    challengeable: bool = True
    effect: EffectNode | None = None
    roll: RollDef | None = None
    then: EffectNode | None = None


class EquipDef(Frozen):
    """How an Item attaches to a Hero."""

    to: SelectorNode
    cost: Cost = Field(default_factory=lambda: {"action_points": 1})
    challengeable: bool = True
    effect: EffectNode | None = None


class ReactionDef(Frozen):
    """A free, out-of-turn play into a named window."""

    window: str
    challengeable: bool = False
    condition: ConditionNode | None = None
    effect: EffectNode


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------


class BaseCardDef(Frozen):
    """The envelope every card shares (``docs/card_schemas.md §2``)."""

    id: str
    name: str
    copies: int = Field(default=1, ge=0)
    text: str = ""
    art: str | None = None
    tags: list[str] = Field(default_factory=list)
    priority: int = 0
    triggers: list[TriggerDef] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if not ID_PATTERN.match(value):
            raise ValueError(
                f"id must be '<pack>.<kind>.<slug>' in lower_snake_case, got {value!r}"
            )
        return value

    @property
    def pack_id(self) -> str:
        return self.id.split(".", 1)[0]

    @property
    def slug(self) -> str:
        return self.id.rsplit(".", 1)[-1]


class HeroDef(BaseCardDef):
    kind: Literal["hero"] = "hero"
    card_class: str
    ability: AbilityDef | None = None


class MonsterDef(BaseCardDef):
    kind: Literal["monster"] = "monster"
    requirement: ConditionNode | None = None
    requirement_text: str = ""
    roll: RollDef
    on_slay: EffectNode | None = None


class ItemDef(BaseCardDef):
    kind: Literal["item"] = "item"
    card_class: str | None = None
    equip: EquipDef | None = None
    play: PlayDef | None = None


class MagicDef(BaseCardDef):
    kind: Literal["magic"] = "magic"
    play: PlayDef


class ModifierDef(BaseCardDef):
    kind: Literal["modifier"] = "modifier"
    reaction: ReactionDef


class ChallengeDef(BaseCardDef):
    kind: Literal["challenge"] = "challenge"
    reaction: ReactionDef


class PartyLeaderDef(BaseCardDef):
    kind: Literal["party_leader"] = "party_leader"
    card_class: str
    copies: int = Field(default=1, ge=0)
    #: Most Leaders are pure passives (``triggers``), but a skill that *costs*
    #: something cannot be a trigger — there is nothing to pay in a subscription.
    #: The base game's Shadow Claw ("spend an action point to pull a card") is
    #: the one such Leader, and ``use_ability`` reads this block generically.
    ability: AbilityDef | None = None


CardDef = Annotated[
    HeroDef | MonsterDef | ItemDef | MagicDef | ModifierDef | ChallengeDef | PartyLeaderDef,
    Field(discriminator="kind"),
]

#: Which zone a card of each kind is dealt into at setup.
DECK_FOR_KIND: dict[str, str] = {
    "hero": "main_deck",
    "item": "main_deck",
    "magic": "main_deck",
    "modifier": "main_deck",
    "challenge": "main_deck",
    "monster": "monster_deck",
    "party_leader": "leader_pool",
}


# ---------------------------------------------------------------------------
# rules.yaml
# ---------------------------------------------------------------------------


class ZoneDef(Frozen):
    id: str
    scope: Literal["shared", "player"] = "shared"
    visibility: Literal["hidden", "public", "owner"] = "public"
    ordered: bool = True
    capacity: int | None = None


class TargetDef(Frozen):
    """One thing an action must be pointed at before it can be declared.

    This is what makes ``legal_intents()`` data-driven: the engine expands an
    action into one concrete :class:`~here_to_slay.core.interpreter.Intent` per
    legal combination of targets, so a CLI menu and a pygame highlight both come
    from the same table and neither re-implements "which Heroes may I play?".

    ``param`` names where the choice lands on the intent: ``card`` and ``target``
    are the intent's own fields, anything else goes in ``params``.
    """

    model_config = ConfigDict(populate_by_name=True)

    param: str = "card"
    source: SelectorNode = Field(alias="from")
    where: ConditionNode | None = None
    prompt: str = ""

    @field_validator("param")
    @classmethod
    def _param_is_a_slug(cls, value: str) -> str:
        if not SLUG_PATTERN.match(value):
            raise ValueError(f"target param names are lower_snake_case slugs, got {value!r}")
        return value


class ActionDef(Frozen):
    id: str
    label: str = ""
    cost: Cost = Field(default_factory=dict)
    requires: ConditionNode | None = None
    targets: list[TargetDef] = Field(default_factory=list)
    effect: EffectNode | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def _default_label(self) -> ActionDef:
        if not self.label:
            object.__setattr__(self, "label", self.id.replace("_", " ").capitalize())
        return self


class PhaseDef(Frozen):
    id: str
    on_enter: list[EffectNode] = Field(default_factory=list)
    on_exit: list[EffectNode] = Field(default_factory=list)
    auto_advance: bool = False
    loop_while: ConditionNode | None = None
    allows: list[str] = Field(default_factory=list)


class WindowDef(Frozen):
    """A reaction window, and *when it opens*.

    ``on``/``timing`` are what keep windows data (``rules_engine.md §5``): the
    bus opens a declared window during that event's phase, so a variant can add
    a ``damage_prevention`` window on its own event with no engine edit. A
    window with no ``on`` never opens by itself and must be opened by an op.

    ``condition`` gates it — the base game uses it for "…unless this card was
    played uncontestably", which is how ``challengeable: false`` reaches the bus
    without the bus knowing the word.
    """

    order: str = "seat_left_of_active"
    reopen_on_action: bool = True
    #: one event name, or several — a window that means the same thing on more
    #: than one event says so once instead of being declared twice under two
    #: names that cards would then both have to list.
    on: str | list[str] | None = None
    timing: Timing = "pre"
    condition: ConditionNode | None = None

    @property
    def opens_on(self) -> tuple[str, ...]:
        """Every event name that opens this window."""
        if self.on is None:
            return ()
        return (self.on,) if isinstance(self.on, str) else tuple(self.on)


class VictoryDef(Frozen):
    id: str
    text: str = ""
    condition: ConditionNode


class SetupRules(Frozen):
    starting_hand: int = 5
    monster_row_size: int = 3
    leader_selection: Literal["random", "draft", "choice"] = "random"
    min_players: int = 2
    max_players: int = 6


class TurnRules(Frozen):
    action_points_per_turn: int = 3
    hand_limit: int | None = None
    #: run after every resolved action — where "the Monster row refills" lives,
    #: because *when* a new Monster turns up is policy, not mechanism
    after_action: list[EffectNode] = Field(default_factory=list)


class RuleSet(Frozen):
    """The base game as data. This is the file a variant most wants to fork."""

    id: str
    classes: list[str] = Field(default_factory=list)
    setup: SetupRules = Field(default_factory=SetupRules)
    turn: TurnRules = Field(default_factory=TurnRules)
    actions: list[ActionDef] = Field(default_factory=list)
    phases: list[PhaseDef] = Field(default_factory=list)
    windows: dict[str, WindowDef] = Field(default_factory=dict)
    max_reaction_depth: int = 8
    victory: list[VictoryDef] = Field(default_factory=list)
    tiebreak: str = "active_player"
    zones: list[ZoneDef] = Field(default_factory=list)
    constants: dict[str, Any] = Field(default_factory=dict)

    @property
    def zone_ids(self) -> set[str]:
        return {zone.id for zone in self.zones}

    def zone(self, zone_id: str) -> ZoneDef | None:
        return next((z for z in self.zones if z.id == zone_id), None)

    def action(self, action_id: str) -> ActionDef | None:
        return next((a for a in self.actions if a.id == action_id), None)


# ---------------------------------------------------------------------------
# pack.yaml
# ---------------------------------------------------------------------------


class ProvidesDef(Frozen):
    rules: str | None = None
    cards: list[str] = Field(default_factory=list)


class PatchDef(Frozen):
    """A diff against an already-loaded card (or against ``rules``).

    ``set`` keys are dotted paths: ``{"roll.outcomes.0.min": 11}``.
    """

    target: str
    set: dict[str, Any] = Field(default_factory=dict)
    remove: bool = False

    @model_validator(mode="after")
    def _does_something(self) -> PatchDef:
        if not self.set and not self.remove:
            raise ValueError("a patch must 'set' something or 'remove: true'")
        if self.set and self.remove:
            raise ValueError("a patch cannot both 'set' and 'remove'")
        return self


class PackDef(Frozen):
    """``pack.yaml`` — a pack's identity, dependencies and contents."""

    id: str
    name: str = ""
    version: str = "0.0.0"
    schema_version: int = 1
    requires: list[str] = Field(default_factory=list)
    load_after: list[str] = Field(default_factory=list)
    plugin: str | None = None
    provides: ProvidesDef = Field(default_factory=ProvidesDef)
    patches: list[PatchDef] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if not SLUG_PATTERN.match(value):
            raise ValueError(f"pack id must be a lower_snake_case slug, got {value!r}")
        return value

    @model_validator(mode="after")
    def _default_name(self) -> PackDef:
        if not self.name:
            object.__setattr__(self, "name", self.id)
        return self
