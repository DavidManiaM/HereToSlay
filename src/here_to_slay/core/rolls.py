"""The roll pipeline (``docs/rules_engine.md §4``).

Every die roll in the game — a Hero's ability, a Monster attack, a Challenge —
is *one object with one lifecycle*. That is the whole reason Modifier cards need
no per-case logic: a "+2" does not know what it is modifying, it appends an
integer with a source to whatever :class:`Roll` is in flight.

::

    roll.started   ── PRE ──►  passives inject Modifiers (leaders, items)
          │                     condition: roll_is(kind=..., roller=...)
          ▼
       dice rolled from state.rng  (logged)
          ▼
    roll.resolved  ── PRE ──►  the `roll_modification` window opens here, so a
          │                    Modifier played into it lands before the total is
          │                    read and the band chosen
          ▼
       total computed, first matching Band selected, its effect run

Three decisions worth their keep:

* **The window is not opened by this module.** ``rules.yaml`` declares
  ``roll_modification: {on: roll.resolved, timing: pre}`` and the bus opens it.
  A variant that wants modifiers on a *different* event moves one line of YAML.
* **Modifiers are additive integers with a source**, so "ignore Modifiers played
  by your opponents" is a PRE subscriber on ``roll.resolved`` that filters
  ``roll.modifiers`` — not an engine change.
* **The dice string is parsed** (``NdM+K``), so ``3d6`` and ``1d20`` variants
  work with no code change. Bands are declaration-ordered, first match wins, and
  the content validator has already proved they cover the range.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from here_to_slay.content.schema import Band
from here_to_slay.core.errors import EffectError
from here_to_slay.core.events import Outcome
from here_to_slay.core.ids import CardId, PlayerId, RollId, roll_id
from here_to_slay.core.interpreter import ChooseOption, Flow, Option

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.core.context import EffectContext

#: ``2d6``, ``d20``, ``3d6+1`` — the whole dice language
DICE_PATTERN = re.compile(r"^(\d*)d(\d+)([+-]\d+)?$")

DEFAULT_DICE = "2d6"
#: how many dice one roll may ask for before we assume the card is broken
MAX_DICE = 100


def parse_dice(dice: str) -> tuple[int, int, int]:
    """``"2d6+1"`` -> ``(2, 6, 1)``. Raises :class:`EffectError` on nonsense."""
    match = DICE_PATTERN.match(str(dice).replace(" ", ""))
    if match is None:
        raise EffectError(f"cannot read dice {dice!r} — expected something like '2d6' or '1d20+1'")
    count, faces, flat = match.groups()
    number, sides = int(count or 1), int(faces)
    if number > MAX_DICE:
        raise EffectError(f"a roll of {dice!r} asks for {number} dice (cap is {MAX_DICE})")
    if sides < 1:
        raise EffectError(f"cannot roll a {sides}-sided die ({dice!r})")
    return number, sides, int(flat or 0)


def dice_range(dice: str) -> tuple[int, int]:
    count, faces, flat = parse_dice(dice)
    return count + flat, count * faces + flat


# ---------------------------------------------------------------------------
# The roll
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Modifier:
    """One adjustment to a roll, and who is responsible for it."""

    amount: int
    source: CardId | None = None
    applied_by: PlayerId | None = None
    label: str = ""

    def __str__(self) -> str:
        sign = "+" if self.amount >= 0 else ""
        who = self.label or self.source or self.applied_by or "?"
        return f"{sign}{self.amount} ({who})"


@dataclass(slots=True)
class Roll:
    """A die roll, from declaration to resolution.

    Mutable on purpose: a Modifier played three frames deep has to reach *this*
    roll, and passing the object is how it does that (``$roll`` in card data,
    ``ctx.roll`` in Python).
    """

    id: RollId
    kind: str = "generic"
    roller: PlayerId | None = None
    source: CardId | None = None
    dice: str = DEFAULT_DICE
    raw: tuple[int, ...] = ()
    modifiers: list[Modifier] = field(default_factory=list)
    #: set by ``set_roll_result``: a total that ignores dice and modifiers alike
    forced: int | None = None
    cancelled: bool = False
    rolled: bool = False
    #: one half of a ``contest_roll``. A contested side is deliberately *not*
    #: offered for modification on its own — both sides land first and the pair
    #: is modified together, so nobody spends a Modifier blind. ``rules.yaml``
    #: reads this on ``$event.contested``; the engine only reports it.
    contested: bool = False
    #: the ``tag:`` of the outcome band that ran, once one has. ``None`` while
    #: the roll is in flight, and on a roll with no outcome table at all.
    band_tag: str | None = None

    # -- arithmetic --------------------------------------------------------

    @property
    def flat(self) -> int:
        """The ``+K`` written into the dice string itself."""
        return parse_dice(self.dice)[2]

    @property
    def base(self) -> int:
        """The dice as they landed, before anybody interfered."""
        return sum(self.raw) + self.flat

    @property
    def bonus(self) -> int:
        return sum(modifier.amount for modifier in self.modifiers)

    @property
    def total(self) -> int:
        return self.base + self.bonus if self.forced is None else self.forced

    # -- mutation ----------------------------------------------------------

    def add(self, modifier: Modifier) -> Modifier:
        self.modifiers.append(modifier)
        return modifier

    # -- reporting ---------------------------------------------------------

    def describe(self) -> str:
        """"2d6: 3+4 = 7, +1 (charm) = 8" — what the CLI shows, and what an
        invariant report prints."""
        dice = "+".join(str(face) for face in self.raw) or "-"
        line = f"{self.dice}: {dice} = {self.base}"
        for modifier in self.modifiers:
            line += f", {modifier}"
        if self.modifiers:
            line += f" = {self.total}"
        if self.forced is not None:
            line += f" (set to {self.forced})"
        if self.band_tag:
            line += f" [{self.band_tag}]"
        return line

    def __str__(self) -> str:
        return f"<Roll {self.id} {self.kind} by {self.roller}: {self.describe()}>"


# ---------------------------------------------------------------------------
# Bands
# ---------------------------------------------------------------------------


def band_bounds(band: Any) -> tuple[int | None, int | None, Any]:
    """``(min, max, effect)`` from a :class:`Band` model *or* the raw dict.

    Both shapes turn up: a card's ``roll.outcomes`` arrive as models, while an
    inline ``{op: roll, outcomes: [...]}`` in an effect tree is still dicts.
    """
    if isinstance(band, Band):
        return band.min, band.max, band.effect
    if isinstance(band, dict):
        return band.get("min"), band.get("max"), band.get("effect")
    raise EffectError(f"expected an outcome band like {{min: 7, effect: ...}}, got {band!r}")


def band_tag(band: Any) -> str | None:
    """The band's ``tag:``, if its author gave it one."""
    if isinstance(band, Band):
        return band.tag
    if isinstance(band, dict):
        tag = band.get("tag")
        return str(tag) if tag is not None else None
    return None


def select_band(bands: Iterable[Any], total: int) -> Any | None:
    """The first band matching ``total``, in declaration order. ``min``/``max``
    are inclusive, and both omitted means catch-all."""
    for band in bands:
        low, high, _effect = band_bounds(band)
        if (low is None or total >= low) and (high is None or total <= high):
            return band
    return None


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


def perform_roll(
    ctx: EffectContext,
    *,
    dice: str = DEFAULT_DICE,
    kind: str = "generic",
    roller: PlayerId | None = None,
    source: CardId | None = None,
    outcomes: Sequence[Any] = (),
    contested: bool = False,
) -> Flow:
    """Run one roll all the way through, returning the :class:`Roll`.

    The band's effect runs in a context whose ``$self`` is the roller and whose
    ``$card`` is the roll's source — which is what lets a Monster's outcome say
    ``{op: slay_monster, monster: $card, by: $self}`` and mean it.

    ``contested=True`` marks a roll that is one half of a :func:`contest_roll`.
    The flag only reaches the event payload; it is ``rules.yaml`` that decides
    the modification window stays shut for it (see the ``roll_modification``
    window's condition), so a variant that prefers side-at-a-time modification
    deletes one clause of YAML rather than touching this function.
    """
    roller = roller if roller is not None else ctx.me
    roll = Roll(
        id=roll_id(len(ctx.execution.rolls) + 1),
        kind=kind,
        roller=roller,
        source=source if source is not None else ctx.source,
        dice=dice,
        contested=contested,
    )
    ctx.execution.rolls.append(roll)
    scope = ctx.derive(roll=roll, self_player=roller, source=roll.source)
    payload = {
        "roll": roll,
        "kind": kind,
        "roller": roller,
        "dice": dice,
        "card": roll.source,
        "contested": contested,
    }

    started = yield from scope.emit("roll.started", payload, actor=roller)
    if not started.ok:
        roll.cancelled = True
        return roll

    count, faces, _flat = parse_dice(dice)
    roll.raw = ctx.state.rng.roll(count, faces)
    roll.rolled = True

    resolved = yield from scope.emit("roll.resolved", payload, actor=roller)
    if not resolved.ok:
        roll.cancelled = True
        return roll

    if outcomes:
        yield from run_band(scope, roll, outcomes)
    return roll


def run_band(ctx: EffectContext, roll: Roll, outcomes: Sequence[Any]) -> Flow:
    """Pick the band ``roll.total`` lands in, announce it, then run it.

    ``roll.banded`` is emitted *before* the band's own effect, for two reasons.
    A card that says "each time you successfully roll, draw" reads
    ``$event.tag`` and needs no proxy threshold; and because the announcement is
    a normal cancellable event, "that outcome does not happen to you" is a
    subscriber rather than an engine concept.
    """
    band = select_band(outcomes, roll.total)
    if band is None:
        raise EffectError(
            f"{roll.dice} rolled {roll.total}, which no outcome band covers "
            f"(bands are validated at load time, so a modifier pushed it out of range)"
        )
    roll.band_tag = band_tag(band)
    _low, _high, effect = band_bounds(band)

    announced = yield from ctx.emit(
        "roll.banded",
        {
            "roll": roll,
            "kind": roll.kind,
            "roller": roll.roller,
            "card": roll.source,
            "tag": roll.band_tag,
            "total": roll.total,
        },
        actor=roll.roller,
    )
    if not announced.ok:
        return Outcome.CANCELLED
    return (yield from ctx.run(effect))


def reroll_dice(ctx: EffectContext, roll: Roll, dice: str | None = None) -> Flow:
    """Roll the dice again, keeping every modifier already applied."""
    if dice:
        roll.dice = dice
    count, faces, _flat = parse_dice(roll.dice)
    roll.raw = ctx.state.rng.roll(count, faces)
    roll.rolled = True
    yield from ctx.derive(roll=roll).emit(
        "roll.modified",
        {"roll": roll, "kind": roll.kind, "roller": roll.roller, "reason": "reroll"},
        actor=roll.roller,
    )
    return roll


def current_roll(ctx: EffectContext, reference: Any = None) -> Roll:
    """The roll an op means: an explicit ``target_roll`` ref, or the one in flight."""
    candidate = ctx.resolve(reference) if reference is not None else ctx.roll
    if isinstance(candidate, Roll):
        return candidate
    if candidate is None:
        raise EffectError("there is no roll in flight to modify")
    raise EffectError(f"expected a roll, got {candidate!r}")


def rolls_in_flight(ctx: EffectContext) -> tuple[Roll, ...]:
    """Every roll the event being reacted to put on the table.

    A plain ``roll.resolved`` offers one; a settled ``contest.resolved`` offers
    both sides at once, which is the whole point of modifying a Challenge after
    both dice have landed.
    """
    if ctx.event is None:
        return ()
    payload = ctx.event.payload
    candidates = payload.get("rolls")
    if candidates is None:
        candidates = [payload.get("roll")]
    return tuple(roll for roll in candidates if isinstance(roll, Roll))


def target_roll(ctx: EffectContext, reference: Any = None) -> Flow:
    """Which roll this op acts on — asking the player when it is ambiguous.

    Written as a flow because the answer can need a question. A Modifier played
    into a Challenge sees two rolls and no ``$roll``, so somebody has to say
    which one they are swinging; every other case resolves without a prompt, so
    no existing card gains an extra click.
    """
    if reference is not None:
        return current_roll(ctx, reference)
    if isinstance(ctx.roll, Roll):
        return ctx.roll

    candidates = rolls_in_flight(ctx)
    if not candidates:
        raise EffectError("there is no roll in flight to modify")
    if len(candidates) == 1:
        return candidates[0]

    chosen = yield ChooseOption(
        requester=ctx.me,
        prompt="Which roll?",
        options=tuple(
            Option(key=str(roll.id), label=_roll_label(ctx, roll)) for roll in candidates
        ),
    )
    for roll in candidates:
        if str(roll.id) == chosen:
            return roll
    raise EffectError(f"'{chosen}' is not one of the rolls in flight")


def _roll_label(ctx: EffectContext, roll: Roll) -> str:
    """"Ann's roll (2d6: 4+3 = 7)" — enough to choose between two contest sides."""
    who = "someone"
    if roll.roller is not None:
        player = ctx.state.players.get(roll.roller)
        who = player.name if player is not None else str(roll.roller)
    return f"{who}: {roll.describe()}"


__all__ = [
    "DEFAULT_DICE",
    "Modifier",
    "Roll",
    "band_bounds",
    "band_tag",
    "current_roll",
    "dice_range",
    "parse_dice",
    "perform_roll",
    "reroll_dice",
    "rolls_in_flight",
    "run_band",
    "select_band",
    "target_roll",
]
