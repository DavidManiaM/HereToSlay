"""The roll pipeline: dice, modifiers, bands, and who gets to interfere."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from conftest import Place, RunEffect
from here_to_slay.content import ContentRegistry
from here_to_slay.core import (
    CardsChosen,
    EffectError,
    GameState,
    Interpreter,
    OptionChosen,
    PlayerId,
    ReactionChosen,
    ScriptedSource,
    Status,
    drive,
    new_game,
    zone_id,
)
from here_to_slay.core.context import EffectContext
from here_to_slay.core.rolls import (
    Modifier,
    Roll,
    dice_range,
    parse_dice,
    perform_roll,
    select_band,
)


def run(state: GameState, flow: Any, decisions: Any = ()) -> Status:
    return drive(Interpreter(state), flow, ScriptedSource(list(decisions)))


class TestDiceStrings:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("2d6", (2, 6, 0)), ("d20", (1, 20, 0)), ("3d6+1", (3, 6, 1)), ("2d6-2", (2, 6, -2))],
    )
    def test_it_parses_the_whole_dice_language(self, text: str, expected: tuple) -> None:
        assert parse_dice(text) == expected

    def test_a_variant_die_needs_no_code_change(self) -> None:
        assert dice_range("1d20") == (1, 20)
        assert dice_range("3d6+1") == (4, 19)

    @pytest.mark.parametrize("text", ["2x6", "", "d", "2d6+"])
    def test_nonsense_says_what_it_wanted(self, text: str) -> None:
        with pytest.raises(EffectError, match="cannot read dice"):
            parse_dice(text)

    def test_a_pathological_roll_is_capped(self) -> None:
        with pytest.raises(EffectError, match="cap is"):
            parse_dice("1000d6")


class TestArithmetic:
    def test_modifiers_are_additive_integers_with_a_source(self) -> None:
        roll = Roll(id="roll#1", raw=(3, 4))  # type: ignore[arg-type]
        assert roll.base == 7 and roll.total == 7
        roll.add(Modifier(amount=2, label="+2"))
        roll.add(Modifier(amount=-1, label="-1"))
        assert roll.bonus == 1 and roll.total == 8

    def test_the_flat_part_of_the_dice_string_counts(self) -> None:
        assert Roll(id="roll#1", dice="2d6+1", raw=(1, 1)).total == 3  # type: ignore[arg-type]

    def test_set_roll_result_overrides_dice_and_modifiers_alike(self) -> None:
        roll = Roll(id="roll#1", raw=(6, 6))  # type: ignore[arg-type]
        roll.add(Modifier(amount=5))
        roll.forced = 2
        assert roll.total == 2

    def test_it_describes_itself_for_the_table(self) -> None:
        roll = Roll(id="roll#1", raw=(3, 4))  # type: ignore[arg-type]
        roll.add(Modifier(amount=2, label="Charm"))
        assert roll.describe() == "2d6: 3+4 = 7, +2 (Charm) = 9"


class TestBands:
    BANDS: ClassVar[list[dict[str, Any]]] = [
        {"min": 11, "effect": {"op": "noop"}},
        {"min": 7, "max": 10, "effect": {"op": "draw"}},
        {"max": 6, "effect": {"op": "discard"}},
    ]

    @pytest.mark.parametrize(
        ("total", "op"), [(12, "noop"), (11, "noop"), (10, "draw"), (7, "draw"), (6, "discard")]
    )
    def test_declaration_order_wins(self, total: int, op: str) -> None:
        band = select_band(self.BANDS, total)
        assert band is not None and band["effect"] == {"op": op}

    def test_bounds_are_inclusive(self) -> None:
        band = [{"min": 7, "max": 7, "effect": {"op": "noop"}}]
        assert select_band(band, 7) is not None
        assert select_band(band, 8) is band[0]

    def test_a_modifier_off_the_range_lands_on_the_nearest_band(self) -> None:
        band = select_band(self.BANDS, -1)
        assert band is not None and band["effect"] == {"op": "discard"}
        band = select_band(self.BANDS, 99)
        assert band is not None and band["effect"] == {"op": "noop"}


class TestThePipeline:
    def test_the_same_seed_rolls_the_same_dice(self, play_content: ContentRegistry) -> None:
        """``Game = f(content_hash, seed, decisions)`` covers the dice too."""
        thrown = []
        for _ in range(2):
            state = new_game(play_content, ["Ann", "Bob"], seed="dice")
            for player in state.turn_order:
                for card in list(state.zone(zone_id("hand", player)).cards):
                    state.move_card(card, zone_id("discard"))
            ctx = EffectContext.root(state, player=PlayerId("p1"))
            rolls: list[Roll] = []

            def collect(ctx: EffectContext = ctx, rolls: list[Roll] = rolls) -> Any:
                rolls.append((yield from perform_roll(ctx, dice="2d6", kind="test")))

            run(state, collect())
            thrown.append(rolls[0].raw)
        assert thrown[0] == thrown[1]

    def test_the_matching_band_effect_runs(self, quiet_state: GameState) -> None:
        """A roll is only interesting because of what its band does."""
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))
        before = len(quiet_state.zone(zone_id("hand", PlayerId("p1"))))

        def flow() -> Any:
            yield from perform_roll(
                ctx,
                dice="2d6",
                kind="test",
                outcomes=[{"min": 2, "effect": {"op": "draw", "target": "$self", "count": 1}}],
            )

        run(quiet_state, flow())
        assert len(quiet_state.zone(zone_id("hand", PlayerId("p1")))) == before + 1

    def test_a_total_no_band_covers_uses_the_nearest_band(self, quiet_state: GameState) -> None:
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))
        ran: list[str] = []

        def flow() -> Any:
            yield from perform_roll(
                ctx,
                dice="2d6",
                outcomes=[{"min": 99, "effect": {"op": "noop"}}],
            )
            ran.append("ok")

        run(quiet_state, flow())
        assert ran == ["ok"]

    def test_every_roll_is_recorded_on_the_execution(self, quiet_state: GameState) -> None:
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))

        def flow() -> Any:
            yield from perform_roll(ctx, dice="2d6", kind="a")
            yield from perform_roll(ctx, dice="2d6", kind="b")

        run(quiet_state, flow())
        assert [roll.id for roll in ctx.execution.rolls] == ["roll#1", "roll#2"]


class TestPassivesAndModifiers:
    """The two ways a roll changes, neither of which the roll knows about."""

    def test_a_leader_injects_a_modifier_in_pre(
        self, quiet_state: GameState, place: Place
    ) -> None:
        """``roll.started`` PRE is where "+1 to each of your rolls" lives."""
        place(quiet_state, "play.leader.lucky", "leader", "p1")
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))
        rolls: list[Roll] = []

        def flow() -> Any:
            rolls.append((yield from perform_roll(ctx, dice="2d6", kind="hero_ability")))

        run(quiet_state, flow())
        assert [modifier.amount for modifier in rolls[0].modifiers] == [1]
        assert rolls[0].total == rolls[0].base + 1

    def test_the_leaders_condition_keeps_it_off_another_seats_roll(
        self, quiet_state: GameState, place: Place
    ) -> None:
        place(quiet_state, "play.leader.lucky", "leader", "p1")
        ctx = EffectContext.root(quiet_state, player=PlayerId("p2"))
        rolls: list[Roll] = []

        def flow() -> Any:
            rolls.append(
                (yield from perform_roll(ctx, dice="2d6", roller=PlayerId("p2"), kind="x"))
            )

        run(quiet_state, flow())
        assert rolls[0].modifiers == []

    def test_a_modifier_card_is_offered_and_changes_the_total(
        self, quiet_state: GameState, place: Place
    ) -> None:
        """The window opens because ``rules.yaml`` says it opens on roll.resolved."""
        card = place(quiet_state, "play.modifier.plus_two", "hand", "p2")
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))
        rolls: list[Roll] = []
        script = ScriptedSource([ReactionChosen(card), ReactionChosen(None)])

        def flow() -> Any:
            rolls.append((yield from perform_roll(ctx, dice="2d6", kind="hero_ability")))

        drive(Interpreter(quiet_state), flow(), script)
        assert [
            modifier.amount for modifier in rolls[0].modifiers if modifier.source == card
        ] == [2]
        assert quiet_state.card(card).zone == "discard"

    def test_nobody_holding_a_modifier_is_never_asked(self, quiet_state: GameState) -> None:
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))
        script = ScriptedSource([])

        def flow() -> Any:
            yield from perform_roll(ctx, dice="2d6", kind="hero_ability")

        drive(Interpreter(quiet_state), flow(), script)
        assert script.seen == []


class TestRollOps:
    def test_modify_roll_needs_a_roll_in_flight(
        self, quiet_state: GameState, run_effect: RunEffect
    ) -> None:
        with pytest.raises(EffectError, match="no roll in flight"):
            run_effect(quiet_state, {"op": "modify_roll", "amount": 1}, player="p1")

    def test_set_roll_result_forces_the_total(self, quiet_state: GameState) -> None:
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))
        rolls: list[Roll] = []

        def flow() -> Any:
            rolls.append(
                (
                    yield from perform_roll(
                        ctx,
                        dice="2d6",
                        outcomes=[{"max": 12, "effect": {"op": "set_roll_result", "value": 12}}],
                    )
                )
            )

        run(quiet_state, flow())
        assert rolls[0].forced == 12 and rolls[0].total == 12

    def test_reroll_keeps_the_modifiers_already_applied(self, quiet_state: GameState) -> None:
        ctx = EffectContext.root(quiet_state, player=PlayerId("p1"))
        rolls: list[Roll] = []

        def flow() -> Any:
            roll = yield from perform_roll(ctx, dice="2d6", kind="test")
            before = roll.bonus
            roll.add(Modifier(amount=3, label="+3"))
            yield from ctx.derive(roll=roll).run({"op": "reroll"})
            rolls.append((roll, before))

        run(quiet_state, flow())
        roll, before = rolls[0]
        assert roll.bonus == before + 3 and roll.rolled

    def test_contest_roll_runs_the_branch_the_higher_total_picked(
        self, quiet_state: GameState, run_effect: RunEffect
    ) -> None:
        node = {
            "op": "contest_roll",
            "a": {"roller": "$self", "dice": "2d6", "kind": "challenge"},
            "b": {"roller": "$self", "dice": "1d6", "kind": "challenge"},
            "on_a_wins": {"op": "set_flag", "scope": "game", "key": "winner", "value": "a"},
            "on_b_wins": {"op": "set_flag", "scope": "game", "key": "winner", "value": "b"},
            "on_tie": {"op": "set_flag", "scope": "game", "key": "winner", "value": "tie"},
        }
        run_effect(quiet_state, node, player="p1")
        assert quiet_state.flags["winner"] in {"a", "b", "tie"}

    def test_a_contest_needs_both_sides(
        self, quiet_state: GameState, run_effect: RunEffect
    ) -> None:
        with pytest.raises(EffectError, match="needs a 'b' side"):
            run_effect(
                quiet_state,
                {
                    "op": "contest_roll",
                    "a": {"roller": "$self"},
                    "on_a_wins": {"op": "noop"},
                    "on_b_wins": {"op": "noop"},
                },
                player="p1",
            )


class TestAbilityRolls:
    def test_using_a_hero_rolls_its_ability(
        self, quiet_state: GameState, place: Place, run_effect: RunEffect
    ) -> None:
        hero = place(quiet_state, "play.hero.striker", "party", "p1")

        result = run_effect(quiet_state, {"op": "use_ability", "card": hero}, player="p1")

        assert "roll.started" in result.events and "roll.resolved" in result.events
        assert quiet_state.card(hero).tapped

    def test_a_hero_with_no_ability_says_so(
        self, quiet_state: GameState, place: Place, run_effect: RunEffect
    ) -> None:
        hero = place(quiet_state, "play.hero.lump", "party", "p1")
        with pytest.raises(EffectError, match="has no ability"):
            run_effect(quiet_state, {"op": "use_ability", "card": hero}, player="p1")

    def test_it_will_not_be_used_twice_in_one_turn(
        self, quiet_state: GameState, place: Place, run_effect: RunEffect
    ) -> None:
        hero = place(quiet_state, "play.hero.striker", "party", "p1")
        quiet_state.card(hero).tapped = True
        with pytest.raises(EffectError, match="already been used"):
            run_effect(quiet_state, {"op": "use_ability", "card": hero}, player="p1")


class TestAttacking:
    def test_a_slain_monster_lands_in_the_slain_pile(
        self, quiet_state: GameState, place: Place, run_effect: RunEffect
    ) -> None:
        monster = place(quiet_state, "play.monster.pushover", "monster_row")

        result = run_effect(quiet_state, {"op": "attack_monster", "monster": monster}, player="p1")

        assert quiet_state.card(monster).zone == zone_id("slain", PlayerId("p1"))
        assert result.emitted("monster.slain") == 1
        assert quiet_state.player(PlayerId("p1")).action_points == 1  # its on_slay reward

    def test_a_surviving_monster_reports_the_miss(
        self, quiet_state: GameState, place: Place, run_effect: RunEffect
    ) -> None:
        place(quiet_state, "play.hero.striker", "party", "p1")
        monster = place(quiet_state, "play.monster.wall", "monster_row")

        result = run_effect(quiet_state, {"op": "attack_monster", "monster": monster}, player="p1")

        assert quiet_state.card(monster).zone == zone_id("monster_row")
        assert result.emitted("monster.failed") == 1

    def test_the_requirement_is_a_gate(
        self, quiet_state: GameState, place: Place, run_effect: RunEffect
    ) -> None:
        """"Requires 1 Fighter" is a condition on the card, enforced by the engine."""
        monster = place(quiet_state, "play.monster.wall", "monster_row")
        with pytest.raises(EffectError, match="Requires 1 Fighter"):
            run_effect(quiet_state, {"op": "attack_monster", "monster": monster}, player="p1")


def test_a_challenge_roll_can_itself_be_modified(play_state: GameState, place: Place) -> None:
    """The interrupt system in miniature: playing a Hero opens a window, the
    Challenge played into it rolls, and that roll opens a window of its own —
    three levels deep, with no special case anywhere for any of it."""
    from conftest import empty_hands

    empty_hands(play_state)
    hero = place(play_state, "play.hero.lump", "hand", "p1")
    challenge = place(play_state, "play.challenge.challenge", "hand", "p2")
    modifier = place(play_state, "play.modifier.plus_two", "hand", "p2")

    ctx = EffectContext.root(play_state, player=PlayerId("p1"))
    windows: list[str] = []
    chosen: list[tuple[str, ...]] = []

    class Answerer(ScriptedSource):
        def answer(self, request: Any) -> Any:
            if request.kind == "reaction":
                windows.append(request.window)
                offered = {option.card for option in request.options}
                for card in (challenge, modifier):
                    if card in offered:
                        return ReactionChosen(card)
                return ReactionChosen(None)
            if request.kind == "choose_cards":
                return CardsChosen((request.candidates[0],))
            if request.kind == "choose_option":
                # "Which roll?" — a Challenge puts two on the table at once.
                chosen.append(tuple(option.label for option in request.options))
                return OptionChosen(request.options[0].key)
            raise AssertionError(f"unexpected request {request.kind}")

    drive(
        Interpreter(play_state),
        ctx.run({"op": "play_card_from_hand", "card": hero, "kind": "hero"}),
        Answerer([]),
    )

    assert "card_played" in windows and "roll_modification" in windows
    # The Modifier was offered both contest sides, not just whichever had been
    # rolled most recently: both dice land before either may be modified.
    assert chosen and len(chosen[0]) == 2
    assert play_state.card(challenge).zone == "discard"
    assert play_state.card(modifier).zone == "discard"
