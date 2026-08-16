"""``legal_intents()`` and what happens when one is declared.

This is the function the CLI, pygame and the AI all lean on, so most of these
tests are about the *menu*: what appears in it, what does not, and why. A move
missing from the menu is a rule nobody can play; a move in it that then fails is
a rule the engine failed to check.
"""

from __future__ import annotations

import pytest

from conftest import Place, RunEffect
from here_to_slay.core import (
    EffectError,
    EngineError,
    GameState,
    Intent,
    PlayerId,
    zone_id,
)
from here_to_slay.core.actions import (
    can_afford,
    intents_for,
    is_legal,
    legal_intents,
    perform_action,
)
from here_to_slay.core.context import EffectContext
from here_to_slay.core.interpreter import Interpreter, ScriptedSource, drive


def in_main(state: GameState, action_points: int = 3) -> GameState:
    """Put a dealt game where the menu is meaningful: the main phase, with AP."""
    state.phase = "main"
    state.player(state.active_player).action_points = action_points
    return state


class TestTheMenu:
    def test_only_what_is_actually_playable_appears(self, quiet_state: GameState) -> None:
        """An empty hand and an empty party leave exactly one thing to do: draw.

        Every base Monster gates on a party requirement, so a seat with no
        Heroes cannot attack any of them — which is the menu doing its job, not
        a missing entry. ``attack_monster`` is offered the moment a requirement
        is met, which the next test covers.
        """
        in_main(quiet_state)
        offered = legal_intents(quiet_state)

        assert {intent.action for intent in offered} == {"draw"}

    def test_an_attackable_monster_reaches_the_menu(
        self, quiet_state: GameState, place: Place
    ) -> None:
        """The other half: a Monster whose requirement is met *is* offered, and
        only against the Monsters actually in the row."""
        in_main(quiet_state)
        place(quiet_state, "play.monster.pushover", "monster_row")

        offered = intents_for(quiet_state, PlayerId("p1"), "attack_monster")
        row = quiet_state.zone(zone_id("monster_row")).cards

        assert offered
        assert all(intent.target in row for intent in offered)

    def test_an_action_expands_once_per_legal_target(
        self, quiet_state: GameState, place: Place
    ) -> None:
        """Three Heroes in hand is three entries in the menu, not one."""
        in_main(quiet_state)
        heroes = [place(quiet_state, "play.hero.lump", "hand", "p1") for _ in range(3)]

        offered = intents_for(quiet_state, PlayerId("p1"), "play_hero")

        assert sorted(intent.card for intent in offered) == sorted(heroes)

    def test_the_targets_filter_keeps_the_wrong_cards_out(
        self, quiet_state: GameState, place: Place
    ) -> None:
        in_main(quiet_state)
        hero = place(quiet_state, "play.hero.lump", "hand", "p1")
        magic = place(quiet_state, "play.magic.bolt", "hand", "p1")

        assert [intent.card for intent in intents_for(quiet_state, PlayerId("p1"), "play_hero")] == [
            hero
        ]
        assert [
            intent.card for intent in intents_for(quiet_state, PlayerId("p1"), "cast_magic")
        ] == [magic]

    def test_an_action_with_no_legal_target_is_not_offered(
        self, quiet_state: GameState
    ) -> None:
        in_main(quiet_state)
        assert intents_for(quiet_state, PlayerId("p1"), "play_hero") == ()

    def test_a_hero_with_no_ability_is_not_in_the_ability_menu(
        self, quiet_state: GameState, place: Place
    ) -> None:
        in_main(quiet_state)
        place(quiet_state, "play.hero.lump", "party", "p1")
        striker = place(quiet_state, "play.hero.striker", "party", "p1")

        offered = intents_for(quiet_state, PlayerId("p1"), "use_hero_ability")

        assert [intent.card for intent in offered] == [striker]

    def test_a_tapped_hero_drops_out_of_the_menu(
        self, quiet_state: GameState, place: Place
    ) -> None:
        in_main(quiet_state)
        striker = place(quiet_state, "play.hero.striker", "party", "p1")
        quiet_state.card(striker).tapped = True

        assert intents_for(quiet_state, PlayerId("p1"), "use_hero_ability") == ()

    def test_a_monsters_requirement_gates_the_attack(
        self, quiet_state: GameState, place: Place
    ) -> None:
        """"Requires 1 Fighter" decides what is in the menu, not what fails later."""
        in_main(quiet_state)
        for card in list(quiet_state.zone(zone_id("monster_row")).cards):
            quiet_state.move_card(card, zone_id("monster_deck"))
        wall = place(quiet_state, "play.monster.wall", "monster_row")

        assert intents_for(quiet_state, PlayerId("p1"), "attack_monster") == ()

        place(quiet_state, "play.hero.striker", "party", "p1")  # a Fighter
        assert [
            intent.target for intent in intents_for(quiet_state, PlayerId("p1"), "attack_monster")
        ] == [wall]

    def test_what_you_cannot_afford_is_not_offered(self, quiet_state: GameState) -> None:
        in_main(quiet_state, action_points=1)
        place_monster = quiet_state.zone(zone_id("monster_row")).cards
        assert place_monster  # the deal filled the row
        assert intents_for(quiet_state, PlayerId("p1"), "attack_monster") == ()  # costs 2

    def test_only_the_active_seat_has_a_menu(self, quiet_state: GameState) -> None:
        """Everybody else acts through reaction windows, not through the menu."""
        in_main(quiet_state)
        assert legal_intents(quiet_state, PlayerId("p2")) == ()

    def test_a_won_game_offers_nothing(self, quiet_state: GameState) -> None:
        in_main(quiet_state)
        quiet_state.winner = PlayerId("p2")
        assert legal_intents(quiet_state) == ()

    def test_intents_carry_a_label_a_menu_can_print(
        self, quiet_state: GameState, place: Place
    ) -> None:
        in_main(quiet_state)
        place(quiet_state, "play.hero.lump", "hand", "p1")
        (intent,) = intents_for(quiet_state, PlayerId("p1"), "play_hero")
        # ASCII: the label crosses into the CLI, and an em dash is unencodable
        # on a legacy Windows console. Typography is the UI's business anyway.
        assert str(intent) == "Play a Hero - Lump"

    def test_legality_is_checked_by_key_not_by_identity(
        self, quiet_state: GameState, place: Place
    ) -> None:
        in_main(quiet_state)
        card = place(quiet_state, "play.hero.lump", "hand", "p1")
        assert is_legal(quiet_state, PlayerId("p1"), Intent(action="play_hero", card=card))
        assert not is_legal(quiet_state, PlayerId("p1"), Intent(action="play_hero", card="nope"))


class TestCosts:
    def test_affordability_reads_state_and_asks_nothing(self, quiet_state: GameState) -> None:
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))
        quiet_state.player(PlayerId("p1")).action_points = 2
        assert can_afford(ctx, {"action_points": 2}, PlayerId("p1"))
        assert not can_afford(ctx, {"action_points": 3}, PlayerId("p1"))

    def test_declaring_an_action_spends_the_points(self, quiet_state: GameState) -> None:
        in_main(quiet_state)
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))

        drive(
            Interpreter(quiet_state),
            perform_action(ctx, Intent(action="draw"), player=PlayerId("p1")),
            ScriptedSource([]),
        )

        assert quiet_state.player(PlayerId("p1")).action_points == 2

    def test_an_unknown_action_names_the_rule_set(self, quiet_state: GameState) -> None:
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))
        with pytest.raises(EffectError, match="no such action 'fly'"):
            drive(
                Interpreter(quiet_state),
                perform_action(ctx, Intent(action="fly"), player=PlayerId("p1")),
                ScriptedSource([]),
            )

    def test_a_disabled_action_is_refused_even_if_asked_for_directly(
        self, quiet_state: GameState
    ) -> None:
        """An action switched off in the rules is refused; the UI is never trusted.

        Uses the fixture's own ``shipped_off`` action: ``discard_and_draw`` used
        to be the disabled one, but Phase 6 enabled it — the rulebook lists
        "DISCARD your hand and DRAW five" as a real three-point action.
        """
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))
        with pytest.raises(EffectError, match="is disabled"):
            drive(
                Interpreter(quiet_state),
                perform_action(ctx, Intent(action="shipped_off"), player=PlayerId("p1")),
                ScriptedSource([]),
            )


class TestResolution:
    def test_the_three_action_events_are_announced_in_order(
        self, quiet_state: GameState, run_effect: RunEffect
    ) -> None:
        in_main(quiet_state)
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))
        drive(
            Interpreter(quiet_state),
            perform_action(ctx, Intent(action="draw"), player=PlayerId("p1")),
            ScriptedSource([]),
        )
        names = [event.name for event in ctx.execution.history]
        assert names[0] == "action.declared"
        assert "action.paid" in names and names[-1] == "action.completed"

    def test_playing_a_hero_moves_it_hand_to_party(
        self, quiet_state: GameState, place: Place
    ) -> None:
        in_main(quiet_state)
        card = place(quiet_state, "play.hero.lump", "hand", "p1")
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))

        drive(
            Interpreter(quiet_state),
            perform_action(ctx, Intent(action="play_hero", card=card), player=PlayerId("p1")),
            ScriptedSource([]),
        )

        assert quiet_state.card(card).zone == zone_id("party", PlayerId("p1"))
        assert quiet_state.player(PlayerId("p1")).action_points == 2

    def test_casting_a_magic_card_runs_its_play_block_and_discards_it(
        self, quiet_state: GameState, place: Place
    ) -> None:
        in_main(quiet_state)
        card = place(quiet_state, "play.magic.bolt", "hand", "p1")
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))

        drive(
            Interpreter(quiet_state),
            perform_action(ctx, Intent(action="cast_magic", card=card), player=PlayerId("p1")),
            ScriptedSource([]),
        )

        hand = quiet_state.zone(zone_id("hand", PlayerId("p1")))
        assert quiet_state.card(card).zone == "discard"
        assert len(hand) == 1  # Bolt drew one card

    def test_equipping_an_item_attaches_it_to_a_hero(
        self, quiet_state: GameState, place: Place
    ) -> None:
        """Which Hero is the Item's business (``equip.to``), so the op asks."""
        in_main(quiet_state)
        hero = place(quiet_state, "play.hero.lump", "party", "p1")
        item = place(quiet_state, "play.item.charm", "hand", "p1")
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))

        drive(
            Interpreter(quiet_state),
            perform_action(ctx, Intent(action="equip_item", card=item), player=PlayerId("p1")),
            ScriptedSource([]),
        )

        assert quiet_state.card(item).attached_to == hero
        assert quiet_state.card(item).zone == zone_id("party", PlayerId("p1"))

    def test_a_cancelled_declaration_costs_nothing(
        self, quiet_state: GameState, place: Place
    ) -> None:
        """PRE on ``action.declared`` is where "you may not draw" lives, and it
        lands *before* the cost, so a refused action is free."""
        in_main(quiet_state)
        place(quiet_state, "play.leader.pacifist", "leader", "p1")
        hand = len(quiet_state.zone(zone_id("hand", PlayerId("p1"))))
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))

        drive(
            Interpreter(quiet_state),
            perform_action(ctx, Intent(action="draw"), player=PlayerId("p1")),
            ScriptedSource([]),
        )

        assert quiet_state.player(PlayerId("p1")).action_points == 3
        assert len(quiet_state.zone(zone_id("hand", PlayerId("p1")))) == hand


class TestTargetExplosion:
    def test_a_runaway_target_list_is_a_content_bug_with_a_message(
        self, quiet_state: GameState, place: Place
    ) -> None:
        from here_to_slay.content.schema import ActionDef
        from here_to_slay.core.actions import expand_intents

        for _ in range(6):
            place(quiet_state, "play.hero.lump", "hand", "p1")
        action = ActionDef.model_validate(
            {
                "id": "silly",
                "targets": [
                    {"param": "card", "from": {"selector": "cards", "of": {"zone": "main_deck"}}},
                    {"param": "target", "from": {"selector": "cards", "of": {"zone": "main_deck"}}},
                ],
            }
        )
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))

        with pytest.raises(EngineError, match="narrow one of its targets"):
            expand_intents(ctx, action)
