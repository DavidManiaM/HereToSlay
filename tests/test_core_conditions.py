"""The predicate catalogue and the selectors it filters."""

from __future__ import annotations

import pytest

from here_to_slay.core import EffectError, Event, GameState, PlayerId, zone_id
from here_to_slay.core.context import EffectContext

P1, P2, P3 = PlayerId("p1"), PlayerId("p2"), PlayerId("p3")


def context(state: GameState, **kwargs) -> EffectContext:  # type: ignore[no-untyped-def]
    return EffectContext.root(state, **kwargs)


def put(state: GameState, def_id: str, zone: str, player: PlayerId | None = None) -> str:
    destination = zone_id(zone, player)
    instance = next(
        card for card in state.cards.values() if card.def_id == def_id and card.zone != destination
    )
    state.move_card(instance.id, destination)
    return instance.id


class TestLogic:
    def test_always_and_never(self, table_state: GameState) -> None:
        ctx = context(table_state)
        assert ctx.test({"op": "always"})
        assert not ctx.test({"op": "never"})

    def test_a_missing_condition_is_true(self, table_state: GameState) -> None:
        """"No condition" has to mean "yes", or every optional gate would need
        an explicit ``{op: always}``."""
        assert context(table_state).test(None)

    def test_all_any_not(self, table_state: GameState) -> None:
        ctx = context(table_state)
        assert ctx.test({"op": "all", "of": [{"op": "always"}, {"op": "always"}]})
        assert not ctx.test({"op": "all", "of": [{"op": "always"}, {"op": "never"}]})
        assert ctx.test({"op": "any", "of": [{"op": "never"}, {"op": "always"}]})
        assert ctx.test({"op": "not", "of": {"op": "never"}})

    @pytest.mark.parametrize(
        ("left", "cmp", "right", "expected"),
        [
            (3, "==", 3, True),
            (3, "!=", 3, False),
            (2, "<", 3, True),
            (3, "<=", 3, True),
            (4, ">", 3, True),
            (2, ">=", 3, False),
        ],
    )
    def test_compare(
        self, table_state: GameState, left: int, cmp: str, right: int, expected: bool
    ) -> None:
        ctx = context(table_state)
        assert ctx.test({"op": "compare", "left": left, "cmp": cmp, "right": right}) is expected

    def test_compare_resolves_references_on_both_sides(self, table_state: GameState) -> None:
        table_state.player(P1).action_points = 2
        ctx = context(table_state, player=P1)
        assert ctx.test(
            {
                "op": "compare",
                "left": "$action_points",
                "cmp": "<",
                "right": {"expr": "$rules.turn.action_points_per_turn"},
            }
        )

    def test_an_unknown_comparator_names_the_legal_ones(self, table_state: GameState) -> None:
        with pytest.raises(EffectError, match="unknown comparator"):
            context(table_state).test({"op": "compare", "left": 1, "cmp": "=~", "right": 1})

    def test_not_self_is_about_the_event_actor(self, table_state: GameState) -> None:
        event = Event("card.played", {"player": P2})
        assert context(table_state, player=P1, event=event).test({"op": "not_self"})
        assert not context(table_state, player=P2, event=event).test({"op": "not_self"})

    def test_not_self_is_false_without_an_event(self, table_state: GameState) -> None:
        assert not context(table_state, player=P1).test({"op": "not_self"})

    def test_flag_set_reads_each_scope(self, table_state: GameState) -> None:
        table_state.flags["night"] = True
        table_state.player(P2).flags["cursed"] = 3
        ctx = context(table_state, player=P2)
        assert ctx.test({"op": "flag_set", "scope": "game", "key": "night"})
        assert ctx.test({"op": "flag_set", "scope": "player", "key": "cursed", "value": 3})
        assert not ctx.test({"op": "flag_set", "scope": "player", "key": "cursed", "value": 1})
        assert not ctx.test({"op": "flag_set", "scope": "game", "key": "day"})


class TestBoard:
    def test_party_has_class_counts_heroes(self, table_state: GameState) -> None:
        put(table_state, "table.hero.fighter", "party", P1)
        put(table_state, "table.hero.fighter", "party", P1)
        ctx = context(table_state, player=P1)
        assert ctx.test({"op": "party_has_class", "player": "$self", "class": "fighter", "min": 2})
        assert not ctx.test(
            {"op": "party_has_class", "player": "$self", "class": "fighter", "min": 3}
        )

    def test_party_covers_all_classes_reads_the_rule_set(self, table_state: GameState) -> None:
        """The win condition is data: a variant with a seventh class needs no
        code change for this to keep meaning the right thing."""
        ctx = context(table_state, player=P1)
        assert not ctx.test({"op": "party_covers_all_classes", "player": "$self"})
        for card_class in table_state.rules.classes:
            put(table_state, f"table.hero.{card_class}", "party", P1)
        assert ctx.test({"op": "party_covers_all_classes", "player": "$self"})

    def test_hand_and_party_and_slain_sizes(self, table_state: GameState) -> None:
        ctx = context(table_state, player=P1)
        assert ctx.test({"op": "hand_size", "player": "$self", "cmp": "==", "value": 5})
        assert ctx.test({"op": "party_size", "player": "$self", "cmp": "==", "value": 0})
        assert ctx.test({"op": "slain_count", "player": "$self", "cmp": "<", "value": 3})

    def test_cmp_defaults_to_at_least(self, table_state: GameState) -> None:
        """Every card that says "if you have N" means "at least N"."""
        ctx = context(table_state, player=P1)
        assert ctx.test({"op": "hand_size", "player": "$self", "value": 5})
        assert not ctx.test({"op": "hand_size", "player": "$self", "value": 6})

    def test_discard_size_reads_the_shared_pile(self, table_state: GameState) -> None:
        put(table_state, "table.hero.bard", "discard")
        assert context(table_state).test({"op": "discard_size", "cmp": "==", "value": 1})

    def test_has_card_is_what_makes_an_action_legal(self, table_state: GameState) -> None:
        ctx = context(table_state, player=P1)
        node = {
            "op": "has_card",
            "player": "$self",
            "zone": {"player": "$self", "zone": "hand"},
            "filter": {"op": "card_kind_is", "kind": "hero"},
        }
        assert ctx.test(node)
        for card in list(table_state.zone(zone_id("hand", P1)).cards):
            table_state.move_card(card, "discard")
        assert not ctx.test(node)


class TestCardsAndEvents:
    def test_card_predicates_default_to_the_source_card(self, table_state: GameState) -> None:
        hero = put(table_state, "table.hero.bard", "party", P1)
        ctx = context(table_state, player=P1, source=hero)
        assert ctx.test({"op": "card_kind_is", "kind": "hero"})
        assert ctx.test({"op": "card_class_is", "class": "bard"})

    def test_card_predicates_accept_a_list_of_values(self, table_state: GameState) -> None:
        hero = put(table_state, "table.hero.bard", "party", P1)
        ctx = context(table_state, source=hero)
        assert ctx.test({"op": "card_kind_is", "kind": ["item", "hero"]})

    def test_inside_a_filter_the_subject_is_the_candidate(self, table_state: GameState) -> None:
        hero = put(table_state, "table.hero.bard", "party", P1)
        modifier = put(table_state, "table.modifier.plus_one", "party", P1)
        ctx = context(table_state, player=P1)
        node = {"op": "card_kind_is", "kind": "hero"}
        assert ctx.matches(node, hero)
        assert not ctx.matches(node, modifier)

    def test_with_no_card_at_all_it_says_so(self, table_state: GameState) -> None:
        with pytest.raises(EffectError, match="no card to test"):
            context(table_state).test({"op": "card_kind_is", "kind": "hero"})

    def test_event_actor_is(self, table_state: GameState) -> None:
        event = Event("card.drawn", {"player": P2})
        assert context(table_state, player=P2, event=event).test(
            {"op": "event_actor_is", "player": "$self"}
        )
        assert not context(table_state, player=P1, event=event).test(
            {"op": "event_actor_is", "player": "$self"}
        )

    def test_event_matches_gates_on_kind_and_player(self, table_state: GameState) -> None:
        """The Challenge card's gate, exactly as ``card_schemas.md §6.5`` writes it."""
        hero = table_state.zone(zone_id("hand", P2)).cards[0]
        event = Event("card.played", {"player": P2, "card": hero})
        ctx = context(table_state, player=P1, event=event)
        assert ctx.test(
            {
                "op": "event_matches",
                "kind_in": ["hero", "item", "magic"],
                "played_by": {"op": "not_self"},
            }
        )

    def test_event_matches_rejects_the_wrong_kind(self, table_state: GameState) -> None:
        modifier = put(table_state, "table.modifier.plus_one", "hand", P2)
        event = Event("card.played", {"player": P2, "card": modifier})
        ctx = context(table_state, player=P1, event=event)
        assert not ctx.test({"op": "event_matches", "kind_in": ["hero"]})

    def test_event_matches_is_false_without_an_event(self, table_state: GameState) -> None:
        assert not context(table_state).test({"op": "event_matches", "kind_in": ["hero"]})

    def test_roll_is_false_outside_a_roll(self, table_state: GameState) -> None:
        """A leader's "+1 to your hero rolls" must simply not apply when there
        is no roll, rather than exploding."""
        assert not context(table_state).test({"op": "roll_is", "kind": "hero_ability"})


class TestSelectors:
    def test_players_and_opponents(self, table_state: GameState) -> None:
        ctx = context(table_state, player=P2)
        assert ctx.select({"selector": "players"}) == ("p1", "p2", "p3", "p4")
        assert ctx.select({"selector": "opponents"}) == ("p3", "p4", "p1")

    def test_exclude_is_applied_for_every_selector(self, table_state: GameState) -> None:
        ctx = context(table_state, player=P1)
        assert ctx.select({"selector": "players", "exclude": ["$self"]}) == ("p2", "p3", "p4")

    def test_limit_is_applied_for_every_selector(self, table_state: GameState) -> None:
        assert len(context(table_state).select({"selector": "players", "limit": 2})) == 2

    def test_cards_reads_a_zone(self, table_state: GameState) -> None:
        ctx = context(table_state, player=P1)
        found = ctx.select({"selector": "cards", "of": {"player": "$self", "zone": "hand"}})
        assert set(found) == set(table_state.zone(zone_id("hand", P1)).cards)

    def test_where_filters_the_result(self, table_state: GameState) -> None:
        put(table_state, "table.modifier.plus_one", "party", P1)
        hero = put(table_state, "table.hero.bard", "party", P1)
        ctx = context(table_state, player=P1)
        found = ctx.select(
            {
                "selector": "cards",
                "of": {"player": "$self", "zone": "party"},
                "where": {"op": "card_kind_is", "kind": "hero"},
            }
        )
        assert found == (hero,)

    def test_heroes_is_sugar_for_cards_plus_a_filter(self, table_state: GameState) -> None:
        hero = put(table_state, "table.hero.bard", "party", P3)
        put(table_state, "table.modifier.plus_one", "party", P3)
        ctx = context(table_state, bindings={"victim": P3})
        assert ctx.select({"selector": "heroes", "of": "$victim"}) == (hero,)

    def test_monster_row_and_slain_monsters(self, table_state: GameState) -> None:
        ctx = context(table_state, player=P1)
        assert len(ctx.select({"selector": "monster_row"})) == 3
        assert ctx.select({"selector": "monsters"}) == ctx.select({"selector": "monster_row"})
        slain = put(table_state, "table.monster.gnome", "slain", P1)
        assert ctx.select({"selector": "monsters", "of": "$self"}) == (slain,)

    def test_party_leaders_defaults_to_everybody(self, table_state: GameState) -> None:
        assert len(context(table_state).select({"selector": "party_leaders"})) == 4

    def test_selectors_yield_ids_not_objects(self, table_state: GameState) -> None:
        """So that exclude can compare them, a filter can bind one, and a log
        can print one."""
        found = context(table_state).select({"selector": "monster_row"})
        assert all(isinstance(item, str) for item in found)

    def test_an_unknown_selector_says_so(self, table_state: GameState) -> None:
        with pytest.raises(Exception, match="no selector registered"):
            context(table_state).select({"selector": "dragons"})
