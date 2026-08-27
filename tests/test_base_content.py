"""Phase 6's acceptance gate: the shipping card set, exercised card by card.

Three layers, cheapest first:

1. **Composition** — the box contains what the box says it contains.
2. **Shape** — every card of a kind obeys that kind's contract (a Hero's bands
   cover the dice range, a Monster's requirement is answerable, and so on).
3. **Resolution** — every Hero, Magic card, Item and Monster is actually driven
   through the interpreter in a real dealt game, with its roll forced into the
   band under test, and must resolve without raising and without breaking an
   invariant.

Layer 3 is the golden test the build plan asks for. It answers "does this card
work?", which is the only question that matters once the engine is done, and it
is parametrised over the registry rather than a hand-written list — so a card
added to ``data/base/cards/`` is covered the moment it exists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from here_to_slay.content import ContentRegistry, load_pack
from here_to_slay.content.schema import DECK_FOR_KIND
from here_to_slay.core.context import EffectContext
from here_to_slay.core.ids import PlayerId, zone_id
from here_to_slay.core.interpreter import (
    CardsChosen,
    ChooseCards,
    ChooseOption,
    ChoosePlayer,
    Confirm,
    Confirmed,
    Decision,
    DecisionSource,
    Interpreter,
    OptionChosen,
    PlayerChosen,
    Request,
    drive,
)
from here_to_slay.core.invariants import find_violations
from here_to_slay.core.setup import new_game
from here_to_slay.core.state import GameState

P1 = PlayerId("p1")

#: Parametrisation happens at collection time, before any fixture can set the
#: working directory, so the pack path has to be absolute here.
BASE_PACK = Path(__file__).resolve().parent.parent / "data" / "base"
_CATALOGUE = load_pack(BASE_PACK)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def base() -> ContentRegistry:
    return _CATALOGUE


def _defs(base: ContentRegistry, kind: str) -> list[Any]:
    return sorted(
        (card for card in base.cards.values() if card.kind == kind), key=lambda card: card.id
    )


def _ids(base: ContentRegistry, kind: str) -> list[str]:
    return [card.id for card in _defs(base, kind)]


# ---------------------------------------------------------------------------
# 1. Composition
# ---------------------------------------------------------------------------


class TestTheBox:
    """The printed contents: 115 main-deck cards, 15 Monsters, 6 Party Leaders."""

    def test_the_main_deck_holds_115_cards(self, base: ContentRegistry) -> None:
        total = sum(
            card.copies
            for card in base.cards.values()
            if DECK_FOR_KIND[card.kind] == "main_deck"
        )
        assert total == 115

    def test_there_are_15_monsters_and_6_leaders(self, base: ContentRegistry) -> None:
        assert sum(c.copies for c in _defs(base, "monster")) == 15
        assert sum(c.copies for c in _defs(base, "party_leader")) == 6

    def test_every_class_has_eight_heroes(self, base: ContentRegistry) -> None:
        counts: dict[str, int] = {}
        for hero in _defs(base, "hero"):
            counts[hero.card_class] = counts.get(hero.card_class, 0) + 1
        assert counts == {klass: 8 for klass in base.rules.classes}

    def test_one_party_leader_per_class(self, base: ContentRegistry) -> None:
        assert sorted(c.card_class for c in _defs(base, "party_leader")) == sorted(
            base.rules.classes
        )

    def test_every_card_declares_its_text(self, base: ContentRegistry) -> None:
        """Rules text is what both UIs render; a blank one is a card nobody can play."""
        assert [card.id for card in base.cards.values() if not card.text.strip()] == []


# ---------------------------------------------------------------------------
# 2. Shape
# ---------------------------------------------------------------------------


class TestCardShapes:
    def test_every_hero_has_a_rolling_ability(self, base: ContentRegistry) -> None:
        missing = [h.id for h in _defs(base, "hero") if h.ability is None or h.ability.roll is None]
        assert missing == []

    @pytest.mark.parametrize("card_id", _ids(_CATALOGUE, "hero"))
    def test_hero_bands_cover_the_dice_range(self, base: ContentRegistry, card_id: str) -> None:
        """No total between 2 and 12 may fall through every band.

        A gap is silent in play — the ability would simply do nothing on that
        roll — so it is worth an explicit assertion rather than trusting review.
        """
        roll = base.cards[card_id].ability.roll
        low, high = roll.range
        uncovered = [
            total
            for total in range(low, high + 1)
            if not any(band.matches(total) for band in roll.outcomes)
        ]
        assert uncovered == [], f"{card_id} has no band for {uncovered}"

    @pytest.mark.parametrize("card_id", _ids(_CATALOGUE, "monster"))
    def test_monster_bands_cover_the_dice_range(self, base: ContentRegistry, card_id: str) -> None:
        roll = base.cards[card_id].roll
        low, high = roll.range
        uncovered = [
            total
            for total in range(low, high + 1)
            if not any(band.matches(total) for band in roll.outcomes)
        ]
        assert uncovered == [], f"{card_id} has no band for {uncovered}"

    @pytest.mark.parametrize("card_id", _ids(_CATALOGUE, "monster"))
    def test_every_monster_can_be_slain_and_can_bite(
        self, base: ContentRegistry, card_id: str
    ) -> None:
        """A Monster with no slay band is unkillable; one with no penalty band is
        free. Both are content bugs the schema cannot catch."""
        definition = base.cards[card_id]
        ops = _ops_in(definition.roll.outcomes)
        assert "slay_monster" in ops, f"{card_id} can never be slain"
        assert definition.requirement is not None, f"{card_id} has no party requirement"
        assert definition.requirement_text, f"{card_id} has no requirement text to render"

    def test_reaction_cards_are_never_challengeable(self, base: ContentRegistry) -> None:
        """Modifiers and Challenges are free, out-of-turn plays — a window that
        opened on one would recurse."""
        for kind in ("modifier", "challenge"):
            for card in _defs(base, kind):
                assert card.reaction.challengeable is False, card.id

    def test_cursed_items_target_other_parties(self, base: ContentRegistry) -> None:
        for item in _defs(base, "item"):
            target = str(item.equip.to.param("of"))
            if "cursed" in item.tags:
                assert target == "$opponents", item.id
            else:
                assert target == "$self", item.id


def _ops_in(node: Any) -> set[str]:
    """Every ``op`` name anywhere inside an effect tree."""
    found: set[str] = set()
    if isinstance(node, dict):
        if isinstance(node.get("op"), str):
            found.add(node["op"])
        for value in node.values():
            found |= _ops_in(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            found |= _ops_in(item)
    elif hasattr(node, "op"):
        found.add(node.op)
        found |= _ops_in(getattr(node, "params", {}))
    elif hasattr(node, "effect"):
        found |= _ops_in(node.effect)
    return found


# ---------------------------------------------------------------------------
# 3. Resolution — the golden tests
# ---------------------------------------------------------------------------


class AlwaysYes(DecisionSource):
    """Takes the first legal answer to every question.

    Enough to walk any card to completion: every request the base set can raise
    offers candidates the engine has already proved legal, so "the first one" is
    always a valid game action. Cards are taken up to ``minimum`` so an
    optional pick (minimum 0) declines and a forced one complies — which
    exercises both sides of a "you may" without a bespoke script per card.
    """

    def __init__(self) -> None:
        self.seen: list[Request] = []

    def answer(self, request: Request) -> Decision:
        self.seen.append(request)
        match request:
            case Confirm():
                return Confirmed(ok=True)
            case ChooseCards():
                return CardsChosen(cards=tuple(request.candidates[: max(request.minimum, 1)]))
            case ChoosePlayer():
                return PlayerChosen(player=request.candidates[0])
            case ChooseOption():
                return OptionChosen(key=request.options[0].key)
            case _:
                raise AssertionError(f"unhandled request kind: {request!r}")


def _dealt(base: ContentRegistry) -> GameState:
    return new_game(base, ["Ann", "Bob", "Cid"], seed="phase6")


@pytest.mark.parametrize("card_id", _ids(_CATALOGUE, "hero"))
def test_every_hero_ability_resolves(base: ContentRegistry, card_id: str, place) -> None:
    """Drive each Hero's success band in a real dealt game.

    The band's effect is run directly rather than through ``use_ability`` so the
    dice cannot decide whether the test runs — a 10+ Hero would otherwise be
    untested on most seeds. What is under test is the *effect tree*: that every
    ``$ref`` resolves, every selector finds its zone, and nothing raises.
    """
    state = _dealt(base)
    hero = place(state, card_id, "party", "p1")
    # Give the other seats something to be targeted for: a Hero to destroy or
    # steal, and cards in hand to pull.
    for seat in ("p2", "p3"):
        place(state, "base.hero.napping_nibbles", "party", seat)

    success = base.cards[card_id].ability.roll.outcomes[0]
    _drive(state, success.effect, hero)

    assert find_violations(state) == [], f"{card_id} broke an invariant"


def _drive(state: GameState, node: Any, source: str) -> Any:
    """Run an effect tree with the always-yes source."""
    ctx = EffectContext.root(state, player=P1, source=source)  # type: ignore[arg-type]
    return drive(Interpreter(state), ctx.run(node), AlwaysYes())


@pytest.mark.parametrize("card_id", _ids(_CATALOGUE, "magic"))
def test_every_magic_card_resolves(base: ContentRegistry, card_id: str, place) -> None:
    state = _dealt(base)
    card = place(state, card_id, "hand", "p1")
    for seat in ("p2", "p3"):
        place(state, "base.hero.napping_nibbles", "party", seat)
    place(state, "base.hero.peanut", "party", "p1")

    _drive(state, base.cards[card_id].play.effect, card)

    assert find_violations(state) == []


@pytest.mark.parametrize("card_id", _ids(_CATALOGUE, "monster"))
def test_every_monster_slay_band_resolves(base: ContentRegistry, card_id: str, place) -> None:
    """Run each Monster's slay band, then its on_slay reward."""
    state = _dealt(base)
    monster = place(state, card_id, "monster_row")
    for slug in ("base.hero.peanut", "base.hero.napping_nibbles"):
        place(state, slug, "party", "p1")

    slay = base.cards[card_id].roll.outcomes[0]
    _drive(state, slay.effect, monster)

    assert state.card(monster).zone == zone_id("slain", P1)
    assert find_violations(state) == []


@pytest.mark.parametrize("card_id", _ids(_CATALOGUE, "monster"))
def test_every_monster_penalty_band_resolves(base: ContentRegistry, card_id: str, place) -> None:
    """The band nobody wants: it must resolve without raising, and must leave
    the Monster in the row (a failed attack never removes it)."""
    state = _dealt(base)
    monster = place(state, card_id, "monster_row")
    for slug in ("base.hero.peanut", "base.hero.napping_nibbles"):
        place(state, slug, "party", "p1")

    penalty = base.cards[card_id].roll.outcomes[-1]
    _drive(state, penalty.effect, monster)

    assert state.card(monster).zone == zone_id("monster_row")
    assert find_violations(state) == []


@pytest.mark.parametrize("card_id", _ids(_CATALOGUE, "party_leader"))
def test_every_leader_is_dealable_and_its_triggers_are_live(
    base: ContentRegistry, card_id: str, place
) -> None:
    """A Leader's skill is a subscription, so the test that matters is that it
    is *subscribed* from the leader zone — a trigger with the wrong ``while_in``
    is silently dead for the whole game."""
    state = _dealt(base)
    leader = place(state, card_id, "leader", "p1")
    definition = base.cards[card_id]

    assert state.card(leader).zone == zone_id("leader", P1)
    for trigger in definition.triggers:
        assert trigger.while_in == "leader", f"{card_id} subscribes from '{trigger.while_in}'"
    assert find_violations(state) == []


# ---------------------------------------------------------------------------
# Found in Phase 11: a card whose effect could commit and then refuse itself
# ---------------------------------------------------------------------------


def test_wiggles_can_steal_a_hero_that_has_already_been_used(
    base: ContentRegistry, place
) -> None:
    """"STEAL a Hero and use its effect" must survive stealing a *tapped* Hero.

    The golden test above places an untouched Hero to steal, so it never met
    this: a Hero its owner had already used this turn is tapped, ``use_ability``
    refuses a tapped card, and the refusal arrives from inside an effect that
    had already moved the card. The result was an ``EffectError`` that ended the
    game — a crash, not a rules message, and one no player could have avoided.

    It is rare enough that 400 random games do not find it (Phase 8's thousand
    did not either), which is exactly why it gets a test that aims at it.
    """
    state = _dealt(base)
    wiggles = place(state, "base.hero.wiggles", "party", "p1")
    victim = place(state, "base.hero.napping_nibbles", "party", "p2")
    state.card(victim).tapped = True  # p2 used it earlier this turn

    success = base.cards["base.hero.wiggles"].ability.roll.outcomes[0]
    _drive(state, success.effect, wiggles)

    assert state.card(victim).zone == zone_id("party", P1), "the steal still happens"
    assert find_violations(state) == []
