"""Phase 7 — the interrupt system under load.

``test_core_windows.py`` proves a window is polled correctly. This file proves
the harder claim: that windows *nest*, that a reaction can be answered by
another reaction, that a roll opened three frames deep is still the roll a
Modifier reaches, and that the whole thing terminates.

The three properties every test here is really about:

* **Depth is data.** Nothing in ``core/`` knows what a Challenge is. A chain
  three deep is three ordinary ``card.played`` windows, and the only thing
  bounding it is ``rules.max_reaction_depth``.
* **Determinism.** The same seed and the same answers produce the same game,
  including which seat was asked in which order at every depth.
* **Nothing is stranded.** A cancelled play, a spent reaction and a contest
  side all end somewhere legal. ``limbo`` must always be empty afterwards.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from conftest import Place, empty_hands
from here_to_slay.content import ContentRegistry
from here_to_slay.core import (
    CardsChosen,
    Confirmed,
    Engine,
    GameState,
    IntentChosen,
    Interpreter,
    OptionChosen,
    PlayerChosen,
    PlayerId,
    ReactionChosen,
    ScriptedSource,
    drive,
    new_game,
    zone_id,
)
from here_to_slay.core.context import EffectContext
from here_to_slay.core.interpreter import Awaiting, GameOver, Quiescent
from here_to_slay.core.invariants import find_violations

HERO = "play.hero.lump"
OPEN_VETO = "play.challenge.open_veto"
VETO = "play.challenge.veto"
CONTEST = "play.challenge.challenge"
PLUS_TEN = "play.modifier.plus_ten"
PLUS_TWO = "play.modifier.plus_two"
MINUS_TWO = "play.modifier.minus_two"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class Scripted(ScriptedSource):
    """Answers reaction prompts from a per-seat queue of cards.

    A chain test is a sequence of "this seat responds with that card", so the
    script is exactly that and nothing else. Every seat passes once its queue
    runs dry, which is what makes a chain end for the reason the test intends
    rather than because the harness got bored.
    """

    def __init__(self, plan: dict[str, list[str]] | None = None) -> None:
        super().__init__([])
        self.plan = {seat: list(cards) for seat, cards in (plan or {}).items()}
        self.windows: list[tuple[str, str]] = []
        #: ``(window, how many rolls existed when the prompt was raised)`` — the
        #: only way to assert *when* a window opened relative to the dice.
        self.prompted_at: list[tuple[str, int]] = []
        self.options: list[tuple[str, ...]] = []
        self.ctx: EffectContext | None = None

    def answer(self, request: Any) -> Any:
        self.seen.append(request)
        match request.kind:
            case "reaction":
                self.windows.append((str(request.requester), request.window))
                self.prompted_at.append((request.window, self._rolls()))
                return ReactionChosen(self._next(request))
            case "choose_option":
                self.options.append(tuple(option.label for option in request.options))
                return OptionChosen(request.options[0].key)
            case "choose_cards":
                return CardsChosen(tuple(request.candidates[: request.minimum]))
            case "choose_player":
                return PlayerChosen(request.candidates[0])
            case "confirm":
                return Confirmed(True)
            case "choose_intent":
                return IntentChosen(request.intents[0])
        raise AssertionError(f"unexpected request {request.kind}")

    def _rolls(self) -> int:
        return len(self.ctx.execution.rolls) if self.ctx is not None else -1

    def _next(self, request: Any) -> str | None:
        queue = self.plan.get(str(request.requester), [])
        offered = {option.card for option in request.options}
        for index, wanted in enumerate(queue):
            for card in offered:
                if str(card).startswith(wanted):
                    del queue[index]
                    return card
        return None


def three_players(content: ContentRegistry, seed: str = "phase7") -> GameState:
    """A dealt three-player game with empty hands — chains need a third seat."""
    state = new_game(content, ["Ann", "Bob", "Cid"], seed=seed)
    empty_hands(state)
    return state


def set_leader(state: GameState, place: Place, def_id: str, seat: str) -> str:
    """Give ``seat`` a known Party Leader.

    The ``leader`` zone has no declared capacity, so placing one does not evict
    the dealt one — and a passive that is still sitting there keeps firing. A
    test about a Leader has to say which Leader, singular.
    """
    zone = state.zone(zone_id("leader", PlayerId(seat)))
    for card in list(zone.cards):
        state.move_card(card, zone_id("discard"))
    return place(state, def_id, "leader", seat)


def play_hero(state: GameState, hero: str, script: Scripted, **params: Any) -> Any:
    """``p1`` plays a Hero, and whatever that sets off runs to the end."""
    ctx = EffectContext.root(state, player=PlayerId("p1"))
    script.ctx = ctx
    node = {"op": "play_card_from_hand", "card": hero, "kind": "hero", **params}
    return drive(Interpreter(state), ctx.run(node), script)


# ---------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------


class TestChallengeAChallenge:
    """A reaction is announced as an ordinary ``card.played``, so answering one
    with another needs no mechanism — only a card that says it is challengeable
    and a condition that admits its own kind."""

    def test_a_veto_can_itself_be_vetoed(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        state = three_players(play_content)
        hero = place(state, HERO, "hand", "p1")
        theirs = place(state, OPEN_VETO, "hand", "p2")
        counter = place(state, OPEN_VETO, "hand", "p3")

        play_hero(state, hero, Scripted({"p2": [OPEN_VETO], "p3": [OPEN_VETO]}))

        # p3 cancelled p2's veto, so the Hero was never cancelled at all.
        assert state.card(hero).zone == zone_id("party", PlayerId("p1"))
        assert state.card(theirs).zone == "discard"
        assert state.card(counter).zone == "discard"

    def test_an_unanswered_veto_still_stops_the_hero(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        """The control: the same setup where nobody counters."""
        state = three_players(play_content)
        hero = place(state, HERO, "hand", "p1")
        place(state, OPEN_VETO, "hand", "p2")
        place(state, OPEN_VETO, "hand", "p3")

        play_hero(state, hero, Scripted({"p2": [OPEN_VETO]}))

        assert state.card(hero).zone == "discard"

    def test_three_deep_resolves_and_strands_nothing(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        """The phase's acceptance test: Hero ← veto ← veto ← veto.

        An odd number of cancellations leaves the Hero cancelled; the point is
        that each veto is cancelled or not by the *next* one, not that the
        engine counted anything.
        """
        state = three_players(play_content)
        hero = place(state, HERO, "hand", "p1")
        first = place(state, OPEN_VETO, "hand", "p2")
        second = place(state, OPEN_VETO, "hand", "p3")
        third = place(state, OPEN_VETO, "hand", "p2")

        script = Scripted({"p2": [OPEN_VETO, OPEN_VETO], "p3": [OPEN_VETO]})
        play_hero(state, hero, script)

        # p2 vetoes the Hero; p3 vetoes that; p2 vetoes p3's — so p3's veto
        # never resolved, p2's first one did, and the Hero is discarded.
        assert state.card(hero).zone == "discard"
        for card in (first, second, third):
            assert state.card(card).zone == "discard"
        assert state.zone("limbo").is_empty

    def test_the_chain_is_deterministic(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        """Same seed, same script, same seats asked in the same order."""

        def run() -> list[tuple[str, str]]:
            state = three_players(play_content)
            hero = place(state, HERO, "hand", "p1")
            place(state, OPEN_VETO, "hand", "p2")
            place(state, OPEN_VETO, "hand", "p3")
            script = Scripted({"p2": [OPEN_VETO], "p3": [OPEN_VETO]})
            play_hero(state, hero, script)
            return script.windows

        assert run() == run()

    def test_the_depth_cap_ends_the_chain(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        """Enough Open Vetoes to outrun ``max_reaction_depth``.

        The cap must end the game's *question*, not the game: the flow returns
        normally, nothing is stranded, and the invariants still hold.
        """
        state = three_players(play_content)
        hero = place(state, HERO, "hand", "p1")
        for seat in ("p2", "p3"):
            for _ in range(4):
                place(state, OPEN_VETO, "hand", seat)

        script = Scripted({"p2": [OPEN_VETO] * 4, "p3": [OPEN_VETO] * 4})
        play_hero(state, hero, script)

        assert state.zone("limbo").is_empty
        assert find_violations(state) == []
        # Somebody stopped being asked before the hands ran out — that is the
        # cap doing its job rather than the cards running out.
        depths = len(script.windows)
        assert 0 < depths <= state.rules.max_reaction_depth + len(state.turn_order)


class TestWindowSkipping:
    def test_a_seat_with_no_legal_reaction_is_never_asked(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        state = three_players(play_content)
        hero = place(state, HERO, "hand", "p1")
        place(state, OPEN_VETO, "hand", "p2")

        script = Scripted()
        play_hero(state, hero, script)

        assert {seat for seat, _ in script.windows} == {"p2"}

    def test_an_uncontestable_play_asks_nobody(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        state = three_players(play_content)
        hero = place(state, HERO, "hand", "p1")
        place(state, OPEN_VETO, "hand", "p2")
        place(state, OPEN_VETO, "hand", "p3")

        script = Scripted()
        play_hero(state, hero, script, challengeable=False)

        assert script.windows == []
        assert state.card(hero).zone == zone_id("party", PlayerId("p1"))


class TestReopening:
    def test_acting_reopens_the_poll_from_the_top(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        """``reopen_on_action: true``: p3 acts, so p2 is asked again."""
        state = three_players(play_content)
        hero = place(state, HERO, "hand", "p1")
        place(state, OPEN_VETO, "hand", "p2")
        place(state, OPEN_VETO, "hand", "p3")

        script = Scripted({"p3": [OPEN_VETO]})
        play_hero(state, hero, script)

        asked = [seat for seat, _ in script.windows]
        assert asked.count("p2") >= 2

    def test_reopen_off_still_asks_the_remaining_seats(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        """``reopen_on_action: false`` means one pass with one reaction each —
        not "the window shuts the moment anybody acts", which is what it used
        to mean by accident."""
        state = three_players(play_content)
        state.rules.windows["card_played"] = state.rules.windows["card_played"].model_copy(
            update={"reopen_on_action": False}
        )
        hero = place(state, HERO, "hand", "p1")
        place(state, OPEN_VETO, "hand", "p2")
        place(state, OPEN_VETO, "hand", "p3")

        script = Scripted({"p2": [OPEN_VETO]})
        play_hero(state, hero, script)

        asked = [seat for seat, _ in script.windows]
        assert "p3" in asked, "p3 was never asked after p2 acted"


# ---------------------------------------------------------------------------
# Contests
# ---------------------------------------------------------------------------


class TestContestRoll:
    """Both sides land before either may be modified — the rulebook's order."""

    def test_neither_side_is_offered_before_both_have_rolled(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        state = three_players(play_content)
        hero = place(state, HERO, "hand", "p1")
        place(state, CONTEST, "hand", "p2")
        place(state, PLUS_TWO, "hand", "p3")

        script = Scripted({"p2": [CONTEST]})
        play_hero(state, hero, script)

        opened = [rolls for window, rolls in script.prompted_at if window == "roll_modification"]
        assert opened, "nobody was offered the chance to modify the contest"
        assert all(rolls == 2 for rolls in opened), (
            "a contest side was offered for modification before the other side rolled"
        )

    def test_a_modifier_decides_the_challenge(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        """+10 on the challenger's die wins it whatever the dice said."""
        state = three_players(play_content)
        hero = place(state, HERO, "hand", "p1")
        place(state, CONTEST, "hand", "p2")
        place(state, PLUS_TEN, "hand", "p2")

        script = Scripted({"p2": [CONTEST, PLUS_TEN]})
        play_hero(state, hero, script)

        # The challenger (roll a) now wins, so the Hero's play is cancelled.
        assert script.options and len(script.options[0]) == 2
        assert state.card(hero).zone == "discard"

    def test_the_same_modifier_on_the_other_side_saves_the_hero(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        """The mirror. One card, two rolls, and the player picks — which is the
        whole reason both sides are offered at once."""
        state = three_players(play_content)
        hero = place(state, HERO, "hand", "p1")
        place(state, CONTEST, "hand", "p2")
        place(state, PLUS_TEN, "hand", "p3")

        class PickDefender(Scripted):
            def answer(self, request: Any) -> Any:
                if request.kind == "choose_option":
                    self.options.append(tuple(o.label for o in request.options))
                    # The defender is side b, and `Ann` is the one playing.
                    for option in request.options:
                        if option.label.startswith("Ann"):
                            return OptionChosen(option.key)
                return super().answer(request)

        script = PickDefender({"p2": [CONTEST], "p3": [PLUS_TEN]})
        play_hero(state, hero, script)

        assert state.card(hero).zone == zone_id("party", PlayerId("p1"))

    def test_a_contest_leaves_two_rolls_on_the_record(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        state = three_players(play_content)
        hero = place(state, HERO, "hand", "p1")
        place(state, CONTEST, "hand", "p2")

        script = Scripted({"p2": [CONTEST]})
        play_hero(state, hero, script)

        assert script.ctx is not None
        contested = [roll for roll in script.ctx.execution.rolls if roll.contested]
        assert len(contested) == 2
        assert {roll.roller for roll in contested} == {"p1", "p2"}


class TestModifierStacking:
    def test_modifiers_add_up_and_keep_their_sources(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        state = three_players(play_content)
        hero = place(state, HERO, "hand", "p1")
        place(state, CONTEST, "hand", "p2")
        plus = place(state, PLUS_TWO, "hand", "p3")
        minus = place(state, MINUS_TWO, "hand", "p3")

        script = Scripted({"p2": [CONTEST], "p3": [PLUS_TWO, MINUS_TWO]})
        play_hero(state, hero, script)

        assert script.ctx is not None
        applied = [
            modifier
            for roll in script.ctx.execution.rolls
            for modifier in roll.modifiers
            if modifier.source in {plus, minus}
        ]
        assert sorted(modifier.amount for modifier in applied) == [-2, 2]
        assert {modifier.applied_by for modifier in applied} == {"p3"}

    def test_two_modifiers_on_one_ordinary_roll_both_land(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        """No contest involved — the plain ``roll.resolved`` path still stacks."""
        state = three_players(play_content)
        hero = place(state, "play.hero.striker", "party", "p1")
        cards = {place(state, PLUS_TWO, "hand", "p2") for _ in range(2)}

        ctx = EffectContext.root(state, player=PlayerId("p1"))
        script = Scripted({"p2": [PLUS_TWO, PLUS_TWO]})
        script.ctx = ctx
        drive(Interpreter(state), ctx.run({"op": "use_ability", "card": hero}), script)

        # Filtered to the two cards this test played. A Party Leader's passive
        # may well have added its own to the same roll — which is exactly why
        # modifiers are a list with a source each, not one running total.
        played = [
            modifier
            for modifier in ctx.execution.rolls[0].modifiers
            if modifier.source in cards
        ]
        assert [modifier.amount for modifier in played] == [2, 2]

    def test_a_leader_reacting_to_a_modifier_does_not_recurse(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        """The Protecting Horn adds ±1 *each time a Modifier is played*, and its
        own ±1 emits ``roll.modified`` too.

        It terminates because the card's condition asks who played what
        (``kind_in: [modifier]``) rather than because the engine guards against
        re-entry — the load case that would hang a naive implementation.
        """
        state = three_players(play_content)
        # Seat p2 behind the Horn whatever the deal handed out.
        horn = set_leader(state, place, "base.leader.protecting_horn", "p2")
        assert horn is not None
        hero = place(state, "play.hero.striker", "party", "p1")
        cards = {place(state, PLUS_TWO, "hand", "p2") for _ in range(2)}

        ctx = EffectContext.root(state, player=PlayerId("p1"))
        script = Scripted({"p2": [PLUS_TWO, PLUS_TWO]})
        script.ctx = ctx
        drive(Interpreter(state), ctx.run({"op": "use_ability", "card": hero}), script)

        modifiers = ctx.execution.rolls[0].modifiers
        from_horn = [modifier for modifier in modifiers if modifier.source == horn]
        # Exactly one per Modifier played: not zero, and not a runaway chain.
        assert len(from_horn) == len(cards) == 2


# ---------------------------------------------------------------------------
# Bands
# ---------------------------------------------------------------------------


class TestBandTags:
    """``Band.tag`` is what lets a card say "on a *successful* roll"."""

    def test_the_band_that_ran_is_reported_on_the_roll(
        self, base_content: ContentRegistry
    ) -> None:
        state = new_game(base_content, ["Ann", "Bob"], seed="tags")
        empty_hands(state)
        ctx = EffectContext.root(state, player=PlayerId("p1"))
        node = {
            "op": "roll",
            "dice": "2d6",
            "kind": "hero_ability",
            "outcomes": [
                {"min": 2, "tag": "success", "effect": {"op": "noop"}},
            ],
        }
        drive(Interpreter(state), ctx.run(node), Scripted())

        assert ctx.execution.rolls[0].band_tag == "success"

    def test_an_untagged_band_leaves_the_tag_unset(
        self, base_content: ContentRegistry
    ) -> None:
        state = new_game(base_content, ["Ann", "Bob"], seed="tags")
        empty_hands(state)
        ctx = EffectContext.root(state, player=PlayerId("p1"))
        node = {"op": "roll", "outcomes": [{"min": 2, "effect": {"op": "noop"}}]}
        drive(Interpreter(state), ctx.run(node), Scripted())

        assert ctx.execution.rolls[0].band_tag is None

    def test_every_base_hero_tags_its_threshold(self, base_content: ContentRegistry) -> None:
        """The claim the Coins depend on: success is declared, not inferred."""
        heroes = [
            card for card in base_content.of_kind("hero") if getattr(card, "ability", None)
        ]
        assert len(heroes) == 48
        for hero in heroes:
            tags = [band.tag for band in hero.ability.roll.outcomes]
            assert tags[0] == "success", hero.id
            assert all(tag == "failure" for tag in tags[1:]), hero.id

    def test_cancelling_the_announcement_cancels_the_outcome(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        """``roll.banded`` is announced before the band runs, so "that outcome
        does not happen to you" is a subscriber rather than an engine concept."""
        state = three_players(play_content)
        set_leader(state, place, "play.leader.warden", "p1")
        monster = place(state, "play.monster.biter", "monster_row")
        for _ in range(2):
            place(state, PLUS_TWO, "hand", "p1")
        before = len(state.zone(zone_id("hand", PlayerId("p1"))).cards)

        ctx = EffectContext.root(state, player=PlayerId("p1"))
        script = Scripted()
        script.ctx = ctx
        drive(
            Interpreter(state),
            ctx.run({"op": "attack_monster", "monster": monster, "attacker": "p1"}),
            script,
        )

        assert ctx.execution.rolls[0].band_tag == "backfire"
        assert len(state.zone(zone_id("hand", PlayerId("p1"))).cards) == before

    def test_without_the_warden_the_band_bites(
        self, play_content: ContentRegistry, place: Place
    ) -> None:
        """The control, so the test above is not passing for the wrong reason.

        p1's Leader is pinned: the Warden is in the fixture's leader pool, so
        leaving the deal to decide would make this test pass or fail on the seed.
        """
        state = three_players(play_content)
        set_leader(state, place, "play.leader.plain_one", "p1")
        monster = place(state, "play.monster.biter", "monster_row")
        for _ in range(2):
            place(state, PLUS_TWO, "hand", "p1")
        before = len(state.zone(zone_id("hand", PlayerId("p1"))).cards)

        ctx = EffectContext.root(state, player=PlayerId("p1"))
        script = Scripted()
        script.ctx = ctx
        drive(
            Interpreter(state),
            ctx.run({"op": "attack_monster", "monster": monster, "attacker": "p1"}),
            script,
        )

        assert len(state.zone(zone_id("hand", PlayerId("p1"))).cards) == before - 1


# ---------------------------------------------------------------------------
# What the UI is allowed to see
# ---------------------------------------------------------------------------


class TestRecentRolls:
    """Phase 5 gap 1: the CLI could not show a roll because nothing exposed one.

    ``Engine.recent_rolls`` is that accessor. It matters here because the roll a
    player most wants explained is the one they are being asked to modify — and
    that one is still in flight, three frames inside a dispatch.
    """

    def test_a_fresh_engine_has_rolled_nothing(self, play_content: ContentRegistry) -> None:
        engine = Engine.new(play_content, ["Ann", "Bob"], seed="rolls")
        assert engine.recent_rolls == ()

    def test_rolls_survive_the_step_that_made_them(
        self, play_content: ContentRegistry
    ) -> None:
        """An ``Execution`` dies with its turn-machine step; the history does not."""
        engine = Engine.new(play_content, ["Ann", "Bob", "Cid"], seed="rolls", max_turns=25)
        source = Chaos(7)
        status = engine.start()
        seen: list[int] = []
        for _ in range(4000):
            if not isinstance(status, Awaiting):
                break
            seen.append(len(engine.recent_rolls))
            status = engine.submit(source.answer(status.request))

        assert isinstance(status, (GameOver, Quiescent))
        rolls = engine.recent_rolls
        assert rolls, "a whole game with no dice in it is not testing anything"
        # Monotonic: a roll is never dropped from under a UI mid-turn.
        assert seen == sorted(seen)
        assert all(roll.rolled or roll.cancelled for roll in rolls)

    def test_the_history_is_bounded(self, play_content: ContentRegistry) -> None:
        from here_to_slay.core.engine import MAX_ROLL_HISTORY

        engine = Engine.new(play_content, ["Ann", "Bob", "Cid"], seed="bounded")
        source = Scripted()
        status = engine.start()
        for _ in range(2000):
            if not isinstance(status, Awaiting):
                break
            status = engine.submit(source.answer(status.request))

        assert len(engine.recent_rolls) <= MAX_ROLL_HISTORY


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


class Chaos(Scripted):
    """A seeded random answer to every question, reactions included.

    Not the Phase 8 agent — it has no idea what any card does. That is the
    point: it plays Modifiers and Challenges at random depths and in random
    orders, which is the one thing a scripted chain cannot do.
    """

    def __init__(self, seed: int) -> None:
        super().__init__()
        self.rng = random.Random(seed)

    def answer(self, request: Any) -> Any:
        self.seen.append(request)
        match request.kind:
            case "choose_intent":
                return IntentChosen(self.rng.choice(request.intents))
            case "reaction":
                if request.options and self.rng.random() < 0.6:
                    return ReactionChosen(self.rng.choice(request.options).card)
                return ReactionChosen(None)
            case "choose_option":
                return OptionChosen(self.rng.choice(request.options).key)
            case "choose_cards":
                count = self.rng.randint(
                    request.minimum, min(request.maximum, len(request.candidates))
                )
                return CardsChosen(tuple(self.rng.sample(list(request.candidates), count)))
            case "choose_player":
                return PlayerChosen(self.rng.choice(list(request.candidates)))
            case "confirm":
                return Confirmed(self.rng.random() < 0.5)
        raise AssertionError(f"unexpected request {request.kind}")



@pytest.mark.parametrize("seed", range(60))
def test_a_reaction_heavy_game_never_strands_a_card(
    base_content: ContentRegistry, seed: int
) -> None:
    """Sixty random games on the real pack, played by seats that react often.

    Phase 6 learned the lesson this encodes: the per-card tests drive one band
    in isolation, and only a whole game stacks two Modifiers on a Challenge, or
    runs a deck to exhaustion while a window is open. The assertions are the
    ones a chain cannot violate quietly — nothing left in ``limbo``, no broken
    invariant, and a game that actually ends.
    """
    engine = Engine.new(base_content, ["Ann", "Bob", "Cid"], seed=seed, max_turns=60)
    source = Chaos(seed)
    status = engine.start()
    for _ in range(8000):
        if not isinstance(status, Awaiting):
            break
        status = engine.submit(source.answer(status.request))
    else:  # pragma: no cover - a hang would be the bug
        pytest.fail(f"seed {seed} did not terminate")

    assert not isinstance(status, Awaiting)
    assert find_violations(engine.state) == []
    assert engine.state.zone("limbo").is_empty


def test_a_partial_log_stops_with_its_own_exception(play_content: ContentRegistry) -> None:
    """Phase 5 gap 6: "the log ran out" used to be detected by matching prose."""
    from here_to_slay.core.errors import ReplayExhausted
    from here_to_slay.core.log import DecisionLog

    engine = Engine.new(play_content, ["Ann", "Bob"], seed="partial")
    source = Chaos(1)
    status = engine.start()
    for _ in range(6):
        if not isinstance(status, Awaiting):
            break
        status = engine.submit(source.answer(status.request))

    truncated = DecisionLog.from_data(engine.log.as_data())
    truncated.entries = truncated.entries[:3]
    replay, log_source = Engine.replaying(play_content, truncated)
    with pytest.raises(ReplayExhausted):
        replay.run(log_source)
