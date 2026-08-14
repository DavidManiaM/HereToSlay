"""Phase 2: the generic container every 'hand', 'party' and deck is made of."""

from __future__ import annotations

import pytest

from here_to_slay.content.schema import ZoneDef
from here_to_slay.core.errors import ZoneCapacityError, ZoneError
from here_to_slay.core.ids import CardId, PlayerId
from here_to_slay.core.zones import Zone

P1 = PlayerId("p1")
P2 = PlayerId("p2")


def card(name: str) -> CardId:
    return CardId(f"t.hero.{name}#1")


@pytest.fixture
def deck() -> Zone:
    return Zone.from_def(ZoneDef(id="main_deck", scope="shared", visibility="hidden", ordered=True))


@pytest.fixture
def hand() -> Zone:
    return Zone.from_def(ZoneDef(id="hand", scope="player", visibility="owner", ordered=False), P1)


# ---------------------------------------------------------------------------
# Construction from data
# ---------------------------------------------------------------------------


def test_player_zones_are_named_for_their_owner(hand: Zone) -> None:
    assert hand.id == "hand:p1"
    assert hand.kind == "hand"
    assert hand.owner == P1


def test_shared_zones_are_named_for_themselves(deck: Zone) -> None:
    assert deck.id == "main_deck"
    assert deck.owner is None


def test_scope_and_owner_must_agree() -> None:
    with pytest.raises(ZoneError):
        Zone.from_def(ZoneDef(id="hand", scope="player"))
    with pytest.raises(ZoneError):
        Zone.from_def(ZoneDef(id="discard", scope="shared"), P1)


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_top_is_the_front_so_a_deck_draws_from_it(deck: Zone) -> None:
    deck.extend([card("a"), card("b")])
    deck.add(card("c"), "top")
    assert list(deck) == [card("c"), card("a"), card("b")]
    assert deck.top(2) == (card("c"), card("a"))
    assert deck.bottom() == (card("b"),)


def test_an_integer_position_inserts_there(deck: Zone) -> None:
    deck.extend([card("a"), card("b"), card("c")])
    deck.add(card("x"), 1)
    assert list(deck)[1] == card("x")


def test_unordered_zones_ignore_position_and_keep_insertion_order(hand: Zone) -> None:
    hand.add(card("a"))
    hand.add(card("b"), "top")
    assert list(hand) == [card("a"), card("b")]


def test_top_never_raises_on_a_short_deck(deck: Zone) -> None:
    deck.add(card("a"))
    assert deck.top(5) == (card("a"),)


# ---------------------------------------------------------------------------
# Rules the invariant checker relies on
# ---------------------------------------------------------------------------


def test_capacity_is_enforced() -> None:
    row = Zone.from_def(ZoneDef(id="monster_row", capacity=2))
    row.extend([card("a"), card("b")])
    assert row.is_full
    assert row.free_space == 0
    with pytest.raises(ZoneCapacityError) as exc:
        row.add(card("c"))
    assert "monster_row" in str(exc.value)


def test_a_card_cannot_be_added_twice(deck: Zone) -> None:
    deck.add(card("a"))
    with pytest.raises(ZoneError):
        deck.add(card("a"))


def test_removing_an_absent_card_names_the_zone(deck: Zone) -> None:
    with pytest.raises(ZoneError) as exc:
        deck.remove(card("ghost"))
    assert "main_deck" in str(exc.value)


def test_index_of_and_clear(deck: Zone) -> None:
    deck.extend([card("a"), card("b")])
    assert deck.index_of(card("b")) == 1
    assert deck.clear() == [card("a"), card("b")]
    assert deck.is_empty


def test_an_empty_zone_is_still_truthy(deck: Zone) -> None:
    """`if zone:` must not conflate 'empty' with 'missing'."""
    assert deck.is_empty
    assert bool(deck) is True


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("visibility", "owner", "seat", "expected"),
    [
        ("public", None, P1, True),
        ("public", P2, P1, True),
        ("hidden", None, P1, False),
        ("hidden", P1, P1, False),
        ("owner", P1, P1, True),
        ("owner", P1, P2, False),
        ("owner", P1, None, False),
    ],
)
def test_visibility_matrix(
    visibility: str, owner: PlayerId | None, seat: PlayerId | None, expected: bool
) -> None:
    zone = Zone(id="z", kind="z", owner=owner, visibility=visibility)  # type: ignore[arg-type]
    assert zone.is_visible_to(seat) is expected


def test_clone_is_independent(deck: Zone) -> None:
    deck.add(card("a"))
    twin = deck.clone()
    twin.add(card("b"))
    assert len(deck) == 1
    assert len(twin) == 2
