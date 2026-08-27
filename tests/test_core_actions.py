"""``legal_intents()`` and what happens when one is declared.

This is the function the CLI, pygame and the AI all lean on, so most of these
tests are about the *menu*: what appears in it, what does not, and why. A move
missing from the menu is a rule nobody can play; a move in it that then fails is
a rule the engine failed to check.
"""

from __future__ import annotations

import pytest

from conftest import Place, RunEffect, empty_hands
from here_to_slay.content import ContentRegistry
from here_to_slay.core import (
    EffectError,
    EngineError,
    GameState,
    Intent,
    PlayerId,
    new_game,
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

P1 = PlayerId("p1")


def in_main(state: GameState, action_points: int = 3) -> GameState:
    """Put a dealt game where the menu is meaningful: the main phase, with AP."""
    state.phase = "main"
    state.player(state.active_player).action_points = action_points
    return state


def declare(state: GameState, intent: Intent, player: PlayerId = P1) -> None:
    """Drive one action to completion, answering nothing."""
    ctx = EffectContext.root(state, player=player)
    drive(Interpreter(state), perform_action(ctx, intent, player=player), ScriptedSource([]))


class TestTheMenu:
    def test_only_what_is_actually_playable_appears(self, quiet_state: GameState) -> None:
        """An empty hand and an empty party leave exactly one thing to do: draw.

        Every base Monster gates on a party requirement, so a seat with no
        Heroes cannot attack any of them — which is the menu doing its job, not
        a missing entry. ``attack_monster`` is offered the moment a requirement
        is met, which the next test covers.
        """
        in_main(quiet_state)
        for card in list(quiet_state.zone("monster_row").cards):
            quiet_state.move_card(card, "discard")
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
        action = quiet_state.rules.action("play_hero")
        assert action is not None
        # The label is the rule set's, never the engine's: spelling the base
        # pack's English here coupled this test to wording a later pack was free
        # to change, and did. What must hold is the *shape* — action label, an
        # ASCII separator, card name. ASCII because the label crosses into the
        # CLI, and an em dash is unencodable on a legacy Windows console;
        # typography is the UI's business anyway.
        assert str(intent) == f"{action.label} - Lump"
        assert "—" not in str(intent)

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


class TestPromptCosts:
    """What each menu entry actually charges.

    The shipping numbers are draw 1, Hero 1, ability 1, Monster 2, burn-and-draw
    3, out of 3 points a turn. The ability is the interesting one: the action is
    free (``cost: {}``) and the ability's own ``cost:`` is the single charge, so
    a Hero cannot be billed twice for one activation. ``turn.ability_free_when``
    then waives even that for a Hero that arrived this turn.

    These use the *base* pack deliberately: the play fixture's Heroes declare no
    ability cost, so only the shipping cards can show a double charge.
    """

    @staticmethod
    def _table(base_content: ContentRegistry, action_points: int = 3) -> GameState:
        """A dealt base game with empty hands, so no window can interrupt."""
        state = new_game(base_content, ["Ann", "Bob"], seed="costs")
        empty_hands(state)
        return in_main(state, action_points)

    def test_an_ability_costs_one_point_not_two(
        self, base_content: ContentRegistry, place: Place
    ) -> None:
        """The regression: ``use_hero_ability`` used to charge 1 *and* the
        ability's own ``cost: {action_points: 1}`` on top of it."""
        state = self._table(base_content)
        hero = place(state, "base.hero.peanut", "party", P1)
        # Placed, not played, so it belongs to an earlier turn and pays.
        assert state.card(hero).state.get("entered_turn") is None

        declare(state, Intent(action="use_hero_ability", card=hero))

        assert state.player(P1).action_points == 2

    def test_a_hero_played_this_turn_activates_for_free(
        self, base_content: ContentRegistry, place: Place
    ) -> None:
        state = self._table(base_content)
        hero = place(state, "base.hero.peanut", "hand", P1)

        declare(state, Intent(action="play_hero", card=hero))
        assert state.player(P1).action_points == 2
        assert state.card(hero).state["entered_turn"] == state.turn_number

        declare(state, Intent(action="use_hero_ability", card=hero))
        assert state.player(P1).action_points == 2

    def test_the_free_activation_still_taps_the_hero(
        self, base_content: ContentRegistry, place: Place
    ) -> None:
        """Free is not unlimited: ``once_per_turn`` still spends the Hero."""
        state = self._table(base_content)
        hero = place(state, "base.hero.peanut", "hand", P1)
        declare(state, Intent(action="play_hero", card=hero))
        declare(state, Intent(action="use_hero_ability", card=hero))

        assert state.card(hero).tapped
        assert intents_for(state, P1, "use_hero_ability") == ()

    def test_the_menu_never_offers_an_ability_that_cannot_be_paid_for(
        self, base_content: ContentRegistry, place: Place
    ) -> None:
        """The action is free, so ``legal_intents`` cannot screen it — the
        target's ``where`` filter does, by reading ``$action_points``."""
        state = self._table(base_content, action_points=0)
        old = place(state, "base.hero.peanut", "party", P1)
        assert intents_for(state, P1, "use_hero_ability") == ()

        # …unless it arrived this turn, in which case it needs no points.
        state.card(old).state["entered_turn"] = state.turn_number
        assert [i.card for i in intents_for(state, P1, "use_hero_ability")] == [old]

    def test_a_hero_carried_over_to_the_next_turn_pays_again(
        self, base_content: ContentRegistry, place: Place
    ) -> None:
        """The waiver is stamped with a turn number, not a boolean."""
        state = self._table(base_content)
        hero = place(state, "base.hero.peanut", "hand", P1)
        declare(state, Intent(action="play_hero", card=hero))

        state.turn_number += 1
        state.card(hero).tapped = False
        state.player(P1).action_points = 3
        declare(state, Intent(action="use_hero_ability", card=hero))

        assert state.player(P1).action_points == 2

    def test_attacking_a_monster_costs_two(self, quiet_state: GameState, place: Place) -> None:
        in_main(quiet_state)
        place(quiet_state, "play.hero.striker", "party", "p1")  # meets the gate
        wall = place(quiet_state, "play.monster.wall", "monster_row")

        declare(quiet_state, Intent(action="attack_monster", target=wall))

        assert quiet_state.player(P1).action_points == 1

    def test_discarding_your_hand_and_drawing_five_costs_three(
        self, quiet_state: GameState, place: Place
    ) -> None:
        in_main(quiet_state)
        place(quiet_state, "play.hero.lump", "hand", "p1")  # requires a hand

        declare(quiet_state, Intent(action="discard_and_draw"))

        assert quiet_state.player(P1).action_points == 0
        assert len(quiet_state.zone(zone_id("hand", P1))) == 5

    def test_drawing_and_playing_a_hero_cost_one_each(
        self, quiet_state: GameState, place: Place
    ) -> None:
        in_main(quiet_state)
        hero = place(quiet_state, "play.hero.lump", "hand", "p1")

        declare(quiet_state, Intent(action="draw"))
        assert quiet_state.player(P1).action_points == 2

        declare(quiet_state, Intent(action="play_hero", card=hero))
        assert quiet_state.player(P1).action_points == 1


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
