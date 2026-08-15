"""Every op in the catalogue, against a real dealt state.

``docs/rules_engine.md §9`` calls for exactly this: each ``op`` unit-tested on
its own, so that when a card misbehaves the question is "which op?" and not
"somewhere in the engine".
"""

from __future__ import annotations

import pytest

from conftest import RunEffect
from here_to_slay.content import ContentRegistry
from here_to_slay.core import (
    CardsChosen,
    Confirmed,
    EffectError,
    GameState,
    Outcome,
    PlayerChosen,
    PlayerId,
    Quiescent,
    new_game,
    zone_id,
)
from here_to_slay.core.effects.meta import FLAG_END_TURN, FLAG_EXTRA_TURN

P1, P2, P3 = PlayerId("p1"), PlayerId("p2"), PlayerId("p3")


def hand(state: GameState, player: PlayerId = P1) -> list[str]:
    return list(state.zone(zone_id("hand", player)).cards)


def put(state: GameState, def_id: str, zone: str, player: PlayerId | None = None) -> str:
    """Move a fresh copy of a definition into a zone."""
    destination = zone_id(zone, player)
    instance = next(
        card for card in state.cards.values() if card.def_id == def_id and card.zone != destination
    )
    state.move_card(instance.id, destination)
    return instance.id


# ---------------------------------------------------------------------------
# Control flow
# ---------------------------------------------------------------------------


class TestControlFlow:
    def test_noop_does_nothing(self, table_state: GameState, run_effect: RunEffect) -> None:
        before = table_state.snapshot()
        run = run_effect(table_state, {"op": "noop"})
        assert run.status == Quiescent(Outcome.DONE)
        assert table_state.snapshot() == before

    def test_seq_runs_steps_in_order(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run_effect(
            table_state,
            {
                "op": "seq",
                "steps": [
                    {"op": "gain_action_points", "target": "$self", "amount": 2},
                    {"op": "spend_action_points", "target": "$self", "amount": 1},
                ],
            },
            player="p1",
        )
        assert table_state.player(P1).action_points == 1

    def test_if_takes_the_then_branch(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run_effect(
            table_state,
            {
                "op": "if",
                "condition": {"op": "always"},
                "then": {"op": "gain_action_points", "target": "$self", "amount": 3},
                "else": {"op": "gain_action_points", "target": "$self", "amount": 9},
            },
            player="p1",
        )
        assert table_state.player(P1).action_points == 3

    def test_if_without_an_else_is_fine(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run = run_effect(
            table_state,
            {"op": "if", "condition": {"op": "never"}, "then": {"op": "noop"}},
        )
        assert run.status == Quiescent(Outcome.DONE)

    def test_repeat_runs_a_counted_number_of_times(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run_effect(
            table_state,
            {
                "op": "repeat",
                "times": 4,
                "effect": {"op": "gain_action_points", "target": "$self", "amount": 1},
            },
            player="p1",
        )
        assert table_state.player(P1).action_points == 4

    def test_repeat_accepts_an_expression(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        """"May be an expression" is what lets a variant re-cost a card without
        touching Python."""
        run_effect(
            table_state,
            {
                "op": "repeat",
                "times": {"expr": "$rules.turn.action_points_per_turn - 1"},
                "effect": {"op": "gain_action_points", "target": "$self", "amount": 1},
            },
            player="p1",
        )
        assert table_state.player(P1).action_points == 2

    def test_repeat_refuses_an_absurd_count(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        with pytest.raises(EffectError, match="cap is"):
            run_effect(
                table_state,
                {"op": "repeat", "times": 10**6, "effect": {"op": "noop"}},
            )

    def test_for_each_binds_the_current_item(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run_effect(
            table_state,
            {
                "op": "for_each",
                "over": {"selector": "players"},
                "bind": "who",
                "effect": {"op": "gain_action_points", "target": "$who", "amount": 1},
            },
            player="p1",
        )
        assert [table_state.player(pid).action_points for pid in table_state.turn_order] == [
            1,
            1,
            1,
            1,
        ]

    def test_for_each_takes_its_selection_once(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        """A body that adds to the collection must not extend the loop."""
        run_effect(
            table_state,
            {
                "op": "for_each",
                "over": {"selector": "cards", "of": {"player": "$self", "zone": "hand"}},
                "bind": "card",
                "effect": {"op": "draw", "target": "$self", "count": 1},
            },
            player="p1",
        )
        assert len(hand(table_state)) == 10

    def test_optional_asks_first_and_can_decline(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run = run_effect(
            table_state,
            {
                "op": "optional",
                "prompt": "Gain a point?",
                "effect": {"op": "gain_action_points", "target": "$self", "amount": 1},
            },
            player="p1",
            decisions=[Confirmed(False)],
        )
        assert run.asked == 1
        assert table_state.player(P1).action_points == 0

    def test_optional_accepted_runs_the_effect(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run_effect(
            table_state,
            {
                "op": "optional",
                "effect": {"op": "gain_action_points", "target": "$self", "amount": 1},
            },
            player="p1",
            decisions=[Confirmed(True)],
        )
        assert table_state.player(P1).action_points == 1

    def test_choose_effect_only_offers_possible_branches(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run = run_effect(
            table_state,
            {
                "op": "choose_effect",
                "options": [
                    {
                        "label": "Impossible",
                        "condition": {"op": "never"},
                        "effect": {"op": "gain_action_points", "target": "$self", "amount": 9},
                    },
                    {
                        "label": "Possible",
                        "effect": {"op": "gain_action_points", "target": "$self", "amount": 1},
                    },
                ],
            },
            player="p1",
        )
        assert run.asked == 0  # only one branch survived, so nothing to ask
        assert table_state.player(P1).action_points == 1

    def test_choose_binds_for_later_steps(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run_effect(
            table_state,
            {
                "op": "seq",
                "steps": [
                    {"op": "choose", "bind": "victim", "from": {"selector": "opponents"}},
                    {"op": "gain_action_points", "target": "$victim", "amount": 2},
                ],
            },
            player="p1",
            decisions=[PlayerChosen(P3)],
        )
        assert table_state.player(P3).action_points == 2

    def test_a_binding_does_not_escape_the_branch_it_was_made_in(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        """Lexical scope, matching what the content validator checks for."""
        with pytest.raises(EffectError, match=r"\$victim' is not bound"):
            run_effect(
                table_state,
                {
                    "op": "seq",
                    "steps": [
                        {
                            "op": "if",
                            "condition": {"op": "always"},
                            "then": {
                                "op": "choose",
                                "bind": "victim",
                                "from": {"selector": "opponents"},
                            },
                        },
                        {"op": "gain_action_points", "target": "$victim", "amount": 1},
                    ],
                },
                player="p1",
                decisions=[PlayerChosen(P3)],
            )


# ---------------------------------------------------------------------------
# Cards and zones
# ---------------------------------------------------------------------------


class TestCardOps:
    def test_draw_moves_cards_from_the_deck_to_the_hand(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        deck_before = len(table_state.zone("main_deck"))
        run = run_effect(table_state, {"op": "draw", "target": "$self", "count": 2}, player="p1")
        assert len(hand(table_state)) == 7
        assert len(table_state.zone("main_deck")) == deck_before - 2
        assert run.emitted("card.drawn") == 2

    def test_draw_stops_at_an_empty_deck_rather_than_raising(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        available = len(table_state.zone("main_deck"))
        run_effect(table_state, {"op": "draw", "target": "$self", "count": 50}, player="p1")
        assert len(table_state.zone("main_deck")) == 0
        assert len(hand(table_state)) == 5 + available

    def test_discard_asks_the_chooser_and_moves_the_card(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        card = hand(table_state)[2]
        run_effect(
            table_state,
            {"op": "discard", "target": "$self", "count": 1},
            player="p1",
            decisions=[CardsChosen((card,))],
        )
        assert table_state.card(card).zone == "discard"
        assert len(hand(table_state)) == 4

    def test_a_random_discard_asks_nobody(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        before = table_state.rng.advances
        run = run_effect(
            table_state,
            {"op": "discard", "target": "$self", "count": 1, "random": True},
            player="p1",
        )
        assert run.asked == 0
        assert len(hand(table_state)) == 4
        assert table_state.rng.advances == before + 1  # and it went through the log

    def test_discard_respects_a_filter(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        """Only cards the filter accepts are ever offered, so "discard a
        Modifier" cannot take a Hero by accident."""
        for _ in range(2):
            put(table_state, "table.modifier.plus_one", "hand", P1)
        modifiers = [
            card
            for card in hand(table_state)
            if table_state.definition(card).kind == "modifier"
        ]
        run = run_effect(
            table_state,
            {
                "op": "discard",
                "target": "$self",
                "count": 1,
                "filter": {"op": "card_kind_is", "kind": "modifier"},
            },
            player="p1",
            decisions=[CardsChosen((modifiers[0],))],
        )
        assert set(run.requests[0].candidates) == set(modifiers)  # type: ignore[attr-defined]
        assert table_state.card(modifiers[0]).zone == "discard"

    def test_move_card_puts_a_card_where_it_is_told(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        card = hand(table_state)[0]
        run_effect(
            table_state,
            {"op": "move_card", "card": card, "to": {"zone": "discard"}},
            player="p1",
        )
        assert table_state.card(card).zone == "discard"

    def test_move_card_to_a_player_scoped_zone(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        card = hand(table_state)[0]
        run_effect(
            table_state,
            {"op": "move_card", "card": card, "to": {"player": "$self", "zone": "party"}},
            player="p1",
        )
        assert table_state.card(card).zone == zone_id("party", P1)
        assert table_state.card(card).controller == P1

    def test_steal_card_takes_at_random_by_default(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run = run_effect(
            table_state,
            {"op": "steal_card", "from": "$victim", "to": "$self", "count": 1},
            player="p1",
            bindings={"victim": P2},
        )
        assert run.asked == 0
        assert len(hand(table_state, P1)) == 6
        assert len(hand(table_state, P2)) == 4

    def test_a_chosen_steal_is_flagged_hidden(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        """The chooser cannot see the hand they are picking from, and the
        request has to say so or a presenter will happily render it."""
        victim_hand = hand(table_state, P2)
        run = run_effect(
            table_state,
            {"op": "steal_card", "from": "$victim", "to": "$self", "random": False},
            player="p1",
            bindings={"victim": P2},
            decisions=[CardsChosen((victim_hand[0],))],
        )
        assert run.requests[0].hidden is True  # type: ignore[attr-defined]

    def test_search_binds_what_it_found(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        buried = put(table_state, "table.hero.bard", "discard")
        run_effect(
            table_state,
            {
                "op": "search",
                "zone": {"zone": "discard"},
                "filter": {"op": "card_kind_is", "kind": "hero"},
                "count": 1,
                "bind": "found",
                "then": {"op": "move_card", "card": "$found", "to": {"player": "$self", "zone": "party"}},
            },
            player="p1",
            decisions=[CardsChosen((buried,))],
        )
        assert table_state.card(buried).zone == zone_id("party", P1)

    def test_search_that_finds_nothing_does_nothing(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run = run_effect(
            table_state,
            {
                "op": "search",
                "zone": {"zone": "discard"},
                "then": {"op": "gain_action_points", "target": "$self", "amount": 5},
            },
            player="p1",
        )
        assert run.asked == 0
        assert table_state.player(P1).action_points == 0

    def test_reveal_announces_without_moving_anything(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        card = hand(table_state)[0]
        run = run_effect(table_state, {"op": "reveal", "card": card}, player="p1")
        assert run.emitted("card.revealed") == 1
        assert table_state.card(card).zone == zone_id("hand", P1)

    def test_shuffle_reorders_and_logs(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        before = list(table_state.zone("main_deck").cards)
        advances = table_state.rng.advances
        run_effect(table_state, {"op": "shuffle", "zone": {"zone": "main_deck"}})
        assert sorted(table_state.zone("main_deck").cards) == sorted(before)
        assert table_state.zone("main_deck").cards != before
        assert table_state.rng.advances == advances + 1

    def test_shuffling_an_unordered_zone_is_a_content_error(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        with pytest.raises(EffectError, match="unordered"):
            run_effect(table_state, {"op": "shuffle", "zone": {"player": "$self", "zone": "hand"}}, player="p1")


# ---------------------------------------------------------------------------
# Party and board
# ---------------------------------------------------------------------------


class TestPartyOps:
    def test_steal_hero_moves_it_between_parties(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        hero = put(table_state, "table.hero.bard", "party", P2)
        owner = table_state.card(hero).owner
        run = run_effect(
            table_state,
            {"op": "steal_hero", "from": "$victim", "to": "$self"},
            player="p1",
            bindings={"victim": P2},
        )
        assert table_state.card(hero).zone == zone_id("party", P1)
        assert table_state.card(hero).controller == P1
        # Control follows location; ownership does not move with it, which is
        # what lets a variant say "return stolen Heroes at end of turn".
        assert table_state.card(hero).owner == owner
        assert run.emitted("hero.left_party") == 1
        assert run.emitted("hero.entered_party") == 1

    def test_stealing_from_an_empty_party_does_nothing(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run = run_effect(
            table_state,
            {"op": "steal_hero", "from": "$victim", "to": "$self"},
            player="p1",
            bindings={"victim": P2},
        )
        assert run.status == Quiescent(Outcome.DONE)
        assert run.asked == 0

    def test_stealing_from_yourself_is_a_content_error(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        with pytest.raises(EffectError, match="other than the thief"):
            run_effect(
                table_state,
                {"op": "steal_hero", "from": "$self", "to": "$self"},
                player="p1",
            )

    def test_destroy_hero_sends_it_to_the_discard(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        hero = put(table_state, "table.hero.bard", "party", P2)
        run_effect(
            table_state,
            {"op": "destroy_hero", "target": "$victim"},
            player="p1",
            bindings={"victim": P2},
        )
        assert table_state.card(hero).zone == "discard"

    def test_sacrifice_lets_the_owner_choose(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        put(table_state, "table.hero.bard", "party", P1)
        second = put(table_state, "table.hero.fighter", "party", P1)
        run = run_effect(
            table_state,
            {"op": "sacrifice", "target": "$self"},
            player="p1",
            decisions=[CardsChosen((second,))],
        )
        assert run.requests[0].requester == P1
        assert table_state.card(second).zone == "discard"

    def test_equip_item_attaches_and_follows_the_hero(
        self, trigger_state: GameState, run_effect: RunEffect
    ) -> None:
        hero = put(trigger_state, "triggers.hero.scribe", "party", P1)
        item = put(trigger_state, "triggers.item.charm", "hand", P1)
        run_effect(
            trigger_state,
            {"op": "equip_item", "item": item, "hero": hero},
            player="p1",
        )
        assert trigger_state.card(item).attached_to == hero
        assert hero in [c for c in trigger_state.zone(zone_id("party", P1)).cards]
        assert trigger_state.card(item).zone == zone_id("party", P1)

    def test_unequip_detaches_both_ends(
        self, trigger_state: GameState, run_effect: RunEffect
    ) -> None:
        hero = put(trigger_state, "triggers.hero.scribe", "party", P1)
        item = put(trigger_state, "triggers.item.charm", "hand", P1)
        run_effect(trigger_state, {"op": "equip_item", "item": item, "hero": hero}, player="p1")
        run_effect(trigger_state, {"op": "unequip_item", "item": item}, player="p1")
        assert trigger_state.card(item).attached_to is None
        assert trigger_state.card(hero).attachments == []
        assert trigger_state.card(item).zone == "discard"

    def test_slay_monster_moves_it_to_the_slain_pile_and_pays_out(
        self, small_content: ContentRegistry, run_effect: RunEffect
    ) -> None:
        state = new_game(small_content, ["Ann", "Bob"], seed="slay")
        monster = state.zone("monster_row").cards[0]
        run = run_effect(
            state, {"op": "slay_monster", "monster": monster, "by": "$self"}, player="p1"
        )
        assert state.card(monster).zone == zone_id("slain", P1)
        assert run.emitted("monster.slain") == 1
        assert state.player(P1).action_points == 1  # the card's on_slay reward

    def test_slaying_does_not_refill_the_row(
        self, small_content: ContentRegistry, run_effect: RunEffect
    ) -> None:
        """*When* a new Monster appears is policy, so it is a rules.yaml step."""
        state = new_game(small_content, ["Ann", "Bob"], seed="slay")
        monster = state.zone("monster_row").cards[0]
        run_effect(state, {"op": "slay_monster", "monster": monster, "by": "$self"}, player="p1")
        assert len(state.zone("monster_row")) == 0

    def test_refill_monster_row_tops_it_back_up(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        row = table_state.zone("monster_row")
        table_state.move_card(row.cards[0], "discard")
        assert len(row) == 2
        run = run_effect(table_state, {"op": "refill_monster_row"})
        assert len(row) == 3
        assert run.emitted("monster_row.refilled") == 1

    def test_refill_is_a_no_op_when_the_row_is_full(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run = run_effect(table_state, {"op": "refill_monster_row"})
        assert run.emitted("monster_row.refilled") == 0

    def test_return_monster_sends_it_back_to_its_deck(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        monster = table_state.zone("monster_row").cards[0]
        run_effect(table_state, {"op": "return_monster", "monster": monster})
        assert table_state.card(monster).zone == "monster_deck"


# ---------------------------------------------------------------------------
# Resources and meta
# ---------------------------------------------------------------------------


class TestMetaOps:
    def test_action_points_go_up_and_down(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run_effect(table_state, {"op": "set_action_points", "target": "$self", "value": 3}, player="p1")
        run_effect(table_state, {"op": "spend_action_points", "target": "$self", "amount": 1}, player="p1")
        assert table_state.player(P1).action_points == 2

    def test_spending_never_goes_negative(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        """``action_points >= 0`` is an invariant; affordability is Phase 4's job."""
        run_effect(
            table_state,
            {"op": "spend_action_points", "target": "$self", "amount": 99},
            player="p1",
        )
        assert table_state.player(P1).action_points == 0

    def test_set_action_points_reads_a_rules_constant(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run_effect(
            table_state,
            {
                "op": "set_action_points",
                "target": "$active_player",
                "value": {"expr": "$rules.turn.action_points_per_turn"},
            },
        )
        assert table_state.player(P1).action_points == 3

    def test_end_turn_and_extra_turn_leave_a_flag_for_the_turn_machine(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run_effect(table_state, {"op": "end_turn"}, player="p1")
        run_effect(table_state, {"op": "extra_turn", "target": "$self"}, player="p2")
        assert table_state.flags[FLAG_END_TURN] is True
        assert table_state.flags[FLAG_EXTRA_TURN] == P2

    def test_enforce_hand_limit_does_nothing_without_a_limit(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run = run_effect(table_state, {"op": "enforce_hand_limit", "target": "$self"}, player="p1")
        assert run.asked == 0
        assert len(hand(table_state)) == 5

    def test_enforce_hand_limit_discards_the_excess(
        self, table_content: ContentRegistry, run_effect: RunEffect
    ) -> None:
        state = new_game(table_content, ["Ann", "Bob"], seed="limit")
        object.__setattr__(state.rules.turn, "hand_limit", 3)
        keep = hand(state)[:2]
        run = run_effect(
            state,
            {"op": "enforce_hand_limit", "target": "$self"},
            player="p1",
            decisions=[CardsChosen(tuple(keep))],
        )
        assert run.asked == 1
        assert len(hand(state)) == 3

    def test_flags_are_set_and_cleared_through_one_event(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run = run_effect(
            table_state, {"op": "set_flag", "scope": "game", "key": "night", "value": True}
        )
        assert table_state.flags["night"] is True
        assert run.emitted("flag.changed") == 1
        run_effect(table_state, {"op": "clear_flag", "scope": "game", "key": "night"})
        assert "night" not in table_state.flags

    def test_a_player_flag_lands_on_the_player(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run_effect(
            table_state,
            {"op": "set_flag", "scope": "player", "key": "cursed", "value": 2, "target": "$self"},
            player="p2",
        )
        assert table_state.player(P2).flags["cursed"] == 2

    def test_a_card_flag_lands_on_the_instance(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        card = hand(table_state)[0]
        run_effect(
            table_state,
            {"op": "set_flag", "scope": "card", "key": "marked", "value": True, "target": card},
            player="p1",
        )
        assert table_state.card(card).state["marked"] is True

    def test_an_unknown_flag_scope_is_a_content_error(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        with pytest.raises(EffectError, match="unknown flag scope"):
            run_effect(table_state, {"op": "set_flag", "scope": "galaxy", "key": "x"})

    def test_emit_fires_a_custom_event(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run = run_effect(
            table_state, {"op": "emit", "event": "variant.corruption_spread"}, player="p1"
        )
        assert run.emitted("variant.corruption_spread") == 1

    def test_win_game_sets_the_winner(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        run = run_effect(table_state, {"op": "win_game", "target": "$self"}, player="p2")
        assert table_state.winner == P2
        assert run.emitted("game.ended") == 1

    def test_cancel_event_outside_a_dispatch_is_a_content_error(
        self, table_state: GameState, run_effect: RunEffect
    ) -> None:
        with pytest.raises(EffectError, match="no event is being dispatched"):
            run_effect(table_state, {"op": "cancel_event"})


class TestRealCardData:
    """The seam between the two layers: a pydantic node out of YAML must run.

    Every other test in this file hands the interpreter a plain dict. This one
    takes the effect tree straight off a loaded ``CardDef`` — an ``EffectNode``
    whose nested params are raw dicts — because that mismatch is exactly the
    kind of thing that would otherwise surface in Phase 6 as "the card does
    nothing".
    """

    def test_a_cards_own_effect_tree_executes(
        self, small_content: ContentRegistry, run_effect: RunEffect
    ) -> None:
        state = new_game(small_content, ["Ann", "Bob"], seed="yaml")
        success_band = small_content["good.hero.pickpocket"].ability.roll.outcomes[0]

        run = run_effect(
            state,
            success_band.effect,
            player="p1",
            decisions=[PlayerChosen(P2)],
        )

        assert run.status == Quiescent(Outcome.DONE)
        assert len(hand(state, P1)) == 3  # stole one at random
        assert len(hand(state, P2)) == 1
