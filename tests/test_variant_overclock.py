"""Phase 10's acceptance test: the sample variant, with zero edits to ``core/``.

``data/variants/overclock`` exercises every seam the architecture claims — a new
class, a new zone, a new action, a new reaction window, an altered win condition,
and one op in each of the five registries. If any of it needed a Python change
inside ``core/``, that is a design bug in the engine, and
:class:`TestNothingInCoreKnowsAboutThisPack` is what catches it.

Everything here runs inside ``temporarily()``, so the variant's ops exist for
these tests and nowhere else in the suite.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from conftest import Place, empty_hands
from here_to_slay.content import ContentRegistry, load_pack
from here_to_slay.content.validate import validate_registry
from here_to_slay.content.vocabulary import Vocabulary
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
from here_to_slay.core.actions import can_afford, legal_intents, perform_action
from here_to_slay.core.context import EffectContext
from here_to_slay.core.interpreter import GameOver, Intent
from here_to_slay.core.invariants import find_violations
from here_to_slay.core.registry import temporarily
from here_to_slay.modding import load_plugins

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VARIANT = PROJECT_ROOT / "data" / "variants" / "overclock"
CORE = PROJECT_ROOT / "src" / "here_to_slay" / "core"

FIREWALL = "overclock.challenge.firewall"
CACHE_FLUSH = "overclock.magic.cache_flush"
SCRIPT_KIDDIE = "overclock.hero.script_kiddie"
COLD_BOOT = "overclock.hero.cold_boot"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def overclock() -> Iterator[tuple[ContentRegistry, Vocabulary]]:
    """The variant, with its plugin installed only for this module."""
    with temporarily():
        registry = load_pack(VARIANT, search_paths=[PROJECT_ROOT / "data"])
        yield registry, load_plugins(registry)


@pytest.fixture(scope="module")
def content(overclock: tuple[ContentRegistry, Vocabulary]) -> ContentRegistry:
    return overclock[0]


MakeState = Any


@pytest.fixture
def make_state(content: ContentRegistry) -> MakeState:
    """A dealt three-player game with empty hands, so a test says what is held.

    The seed is a parameter because two tests below turn on a contested roll,
    and "the Firewall wins" has to be a fact about the test, not about luck.
    """

    def _make(seed: str = "phase10") -> GameState:
        game = new_game(content, ["Ann", "Bob", "Cid"], seed=seed)
        empty_hands(game)
        return game

    return _make


@pytest.fixture
def state(make_state: MakeState) -> GameState:
    return make_state()


def in_main(state: GameState, action_points: int = 3) -> GameState:
    """Put a dealt game where the action menu is meaningful."""
    state.phase = "main"
    state.player(state.active_player).action_points = action_points
    return state


class Scripted(ScriptedSource):
    """Answers whatever comes up; reactions come from a per-seat queue."""

    def __init__(self, plan: dict[str, list[str]] | None = None) -> None:
        super().__init__([])
        self.plan = {seat: list(cards) for seat, cards in (plan or {}).items()}
        self.windows: list[tuple[str, str]] = []

    def answer(self, request: Any) -> Any:
        self.seen.append(request)
        match request.kind:
            case "reaction":
                self.windows.append((str(request.requester), request.window))
                return ReactionChosen(self._next(request))
            case "choose_option":
                return OptionChosen(request.options[0].key)
            case "choose_cards":
                return CardsChosen(tuple(request.candidates[: max(1, request.minimum)]))
            case "choose_player":
                return PlayerChosen(request.candidates[0])
            case "confirm":
                return Confirmed(True)
            case "choose_intent":
                return IntentChosen(request.intents[0])
        raise AssertionError(f"unexpected request {request.kind}")

    def _next(self, request: Any) -> str | None:
        queue = self.plan.get(str(request.requester), [])
        for index, wanted in enumerate(queue):
            for option in request.options:
                if str(option.card).startswith(wanted):
                    del queue[index]
                    return option.card
        return None


def run(state: GameState, node: Any, *, player: str = "p1", script: Any = None) -> Any:
    """Run one effect tree as ``player``, answering with ``script``."""
    ctx = EffectContext.root(state, player=PlayerId(player))
    return drive(Interpreter(state), ctx.run(node), script or Scripted())


def cache_of(state: GameState, seat: str) -> tuple[Any, ...]:
    return tuple(state.zone(zone_id("cache", PlayerId(seat))).cards)


def pin_next_rolls(state: GameState, *pairs: tuple[int, int]) -> None:
    """Force the next ``rng.roll`` calls, then fall back to the real generator.

    Contest tests care about who won the roll, not about which seed shuffled
    the deck — extra base cards must not retune these seeds.
    """

    class _Pinned:
        def __init__(self, inner: Any, leftover: list[tuple[int, int]]) -> None:
            self._inner = inner
            self._leftover = leftover

        def roll(self, count: int, faces: int) -> tuple[int, ...]:
            if self._leftover and count == len(self._leftover[0]):
                return self._leftover.pop(0)
            return self._inner.roll(count, faces)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    state.rng = _Pinned(state.rng, list(pairs))  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# It loads
# ---------------------------------------------------------------------------


class TestItValidates:
    def test_the_pack_is_clean_under_strict_validation(
        self, overclock: tuple[ContentRegistry, Vocabulary]
    ) -> None:
        registry, vocabulary = overclock
        assert validate_registry(registry, vocabulary=vocabulary) == []

    def test_it_layers_over_the_base_game_rather_than_copying_it(
        self, content: ContentRegistry
    ) -> None:
        assert content.pack_ids == ("base", "overclock")
        # Every base card is still there; the variant only adds and patches.
        assert "base.hero.bad_axe" in content.cards
        assert "overclock.hero.script_kiddie" in content.cards

    def test_the_patch_reached_a_base_card(self, content: ContentRegistry) -> None:
        """A dotted `set:` inside one roll band, with no copy of the card."""
        dracos = content["base.monster.dracos"]
        assert dracos.roll is not None
        assert dracos.roll.outcomes[0].min == 8


# ---------------------------------------------------------------------------
# The five data seams
# ---------------------------------------------------------------------------


class TestTheDataSeams:
    def test_a_seventh_class_exists_and_cards_claim_it(
        self, content: ContentRegistry
    ) -> None:
        assert "hacker" in content.rules.classes
        assert content[SCRIPT_KIDDIE].card_class == "hacker"
        assert content["overclock.leader.the_zero_day"].card_class == "hacker"

    def test_the_new_zone_is_minted_once_per_seat(self, state: GameState) -> None:
        """core/setup.py walks rules.zones; nothing there names a zone."""
        for seat in state.turn_order:
            assert state.has_zone("cache", seat)
            assert state.zone_of("cache", seat).is_empty

    def test_the_new_actions_are_on_the_menu(self, state: GameState, place: Place) -> None:
        place(state, "base.hero.bad_axe", "hand", "p1")
        in_main(state)
        offered = {intent.action for intent in legal_intents(state, PlayerId("p1"))}
        assert "upload" in offered
        # An action whose target has no candidates is not offered at all, so an
        # empty cache means no `download` and no `overclock` on the menu.
        assert "download" not in offered
        assert "overclock" not in offered

    def test_filling_the_cache_puts_the_other_two_on_the_menu(
        self, state: GameState, place: Place
    ) -> None:
        for def_id in ("base.hero.bad_axe", "base.hero.dodgy_dealer"):
            state.move_card(place(state, def_id, "hand", "p1"), zone_id("cache", "p1"))
        in_main(state)
        offered = {intent.action for intent in legal_intents(state, PlayerId("p1"))}
        assert {"download", "overclock"} <= offered

    def test_the_new_window_is_declared_on_the_pack_s_own_event(
        self, content: ContentRegistry
    ) -> None:
        window = content.rules.windows["cache_upload"]
        assert window.opens_on == ("cache.uploaded",)
        assert window.order == "seat_left_of_actor"

    def test_the_win_conditions_were_swapped(self, content: ContentRegistry) -> None:
        ids = [victory.id for victory in content.rules.victory]
        assert "full_cache" in ids
        assert "full_party" not in ids  # removed by `{id: ..., remove: true}`
        assert "slay_three" in ids  # ...and the base route survives


# ---------------------------------------------------------------------------
# The five registry seams
# ---------------------------------------------------------------------------


class TestTheOpSeams:
    def test_upload_card_moves_a_card_into_the_cache(
        self, state: GameState, place: Place
    ) -> None:
        card = place(state, "base.hero.bad_axe", "hand", "p1")
        run(state, {"op": "upload_card", "card": card, "player": "p1"})
        assert cache_of(state, "p1") == (card,)
        assert not find_violations(state)

    def test_cache_size_counts_what_upload_card_put_there(
        self, state: GameState, place: Place
    ) -> None:
        ctx = EffectContext.root(state, player=PlayerId("p1"))
        condition = {"op": "cache_size", "player": "p1", "cmp": ">=", "value": 1}
        assert ctx.test(condition) is False
        state.move_card(place(state, "base.hero.bad_axe", "hand", "p1"), zone_id("cache", "p1"))
        assert ctx.test(condition) is True

    def test_the_cached_selector_yields_ids_in_zone_order(
        self, state: GameState, place: Place
    ) -> None:
        first = place(state, "base.hero.bad_axe", "hand", "p1")
        second = place(state, "base.hero.dodgy_dealer", "hand", "p1")
        for card in (first, second):
            state.move_card(card, zone_id("cache", "p1"))
        ctx = EffectContext.root(state, player=PlayerId("p1"))
        assert ctx.select({"selector": "cached", "of": "$self"}) == (first, second)

    def test_cache_burn_answers_affordability_without_spending(
        self, state: GameState, place: Place
    ) -> None:
        """A cost is asked once per frame by ``legal_intents``; if ``check_only``
        mutated, merely *looking* at the menu would burn the cache."""
        ctx = EffectContext.root(state, player=PlayerId("p1"))
        assert can_afford(ctx, {"cache_burn": 2}, PlayerId("p1")) is False

        for def_id in ("base.hero.bad_axe", "base.hero.dodgy_dealer"):
            state.move_card(place(state, def_id, "hand", "p1"), zone_id("cache", "p1"))
        assert can_afford(ctx, {"cache_burn": 2}, PlayerId("p1")) is True
        assert len(cache_of(state, "p1")) == 2  # checking cost nothing

    def test_the_overclock_action_pays_in_cards_and_returns_action_points(
        self, state: GameState, place: Place
    ) -> None:
        for def_id in ("base.hero.bad_axe", "base.hero.dodgy_dealer"):
            state.move_card(place(state, def_id, "hand", "p1"), zone_id("cache", "p1"))
        player = state.player(PlayerId("p1"))
        player.action_points = 1

        ctx = EffectContext.root(state, player=PlayerId("p1"))
        drive(
            Interpreter(state),
            perform_action(ctx, Intent(action="overclock"), player=PlayerId("p1")),
            Scripted(),
        )
        assert player.action_points == 3
        assert cache_of(state, "p1") == ()
        assert len(state.zone("discard")) >= 2


# ---------------------------------------------------------------------------
# The reaction window, under load
# ---------------------------------------------------------------------------


class TestTheFirewall:
    #: a seed on which p2 out-rolls p1 in the contest, and one on which p1 holds
    BLOCKS = "fw0"
    SURVIVES = "fw2"

    def test_a_firewall_cancels_the_upload_and_burns_the_card(
        self, make_state: MakeState, place: Place
    ) -> None:
        state = make_state(self.BLOCKS)
        card = place(state, "base.hero.bad_axe", "hand", "p1")
        firewall = place(state, FIREWALL, "hand", "p2")
        pin_next_rolls(state, (6, 6), (1, 1))  # blocker high, uploader low

        script = Scripted({"p2": [FIREWALL]})
        run(state, {"op": "upload_card", "card": card, "player": "p1"}, script=script)

        assert ("p2", "cache_upload") in script.windows
        assert cache_of(state, "p1") == ()
        # Blocked, not returned: being firewalled costs the card, exactly as a
        # challenged play costs the card in the base game.
        assert state.card(card).zone == "discard"
        assert state.card(firewall).zone == "discard"
        assert not find_violations(state)

    def test_a_lost_contest_leaves_the_upload_standing(
        self, make_state: MakeState, place: Place
    ) -> None:
        """The other branch of the same card: the blocker rolled and lost, so
        the card is spent and the upload happens anyway."""
        state = make_state(self.SURVIVES)
        card = place(state, "base.hero.bad_axe", "hand", "p1")
        firewall = place(state, FIREWALL, "hand", "p2")
        pin_next_rolls(state, (1, 1), (6, 6))  # blocker low, uploader high

        run(
            state,
            {"op": "upload_card", "card": card, "player": "p1"},
            script=Scripted({"p2": [FIREWALL]}),
        )
        assert cache_of(state, "p1") == (card,)
        assert state.card(firewall).zone == "discard"

    def test_an_unanswered_upload_still_lands(self, state: GameState, place: Place) -> None:
        """The control: the same setup where nobody reacts."""
        card = place(state, "base.hero.bad_axe", "hand", "p1")
        place(state, FIREWALL, "hand", "p2")

        run(state, {"op": "upload_card", "card": card, "player": "p1"}, script=Scripted())
        assert cache_of(state, "p1") == (card,)

    def test_the_window_is_not_offered_to_the_uploader(
        self, state: GameState, place: Place
    ) -> None:
        """``not_self`` reads ``$event.player``, which ``upload_card`` supplies."""
        card = place(state, "base.hero.bad_axe", "hand", "p1")
        place(state, FIREWALL, "hand", "p1")

        script = Scripted()
        run(state, {"op": "upload_card", "card": card, "player": "p1"}, script=script)
        assert [seat for seat, _ in script.windows] == []

    def test_only_one_firewall_per_upload(self, state: GameState, place: Place) -> None:
        card = place(state, "base.hero.bad_axe", "hand", "p1")
        place(state, FIREWALL, "hand", "p2")
        place(state, FIREWALL, "hand", "p3")

        script = Scripted({"p2": [FIREWALL], "p3": [FIREWALL]})
        run(state, {"op": "upload_card", "card": card, "player": "p1"}, script=script)
        # p3 was polled, but the flag on the uploaded card left nothing to play.
        assert sum(1 for _, window in script.windows if window == "cache_upload") <= 2
        assert state.zone(zone_id("hand", PlayerId("p3"))).cards


class TestContentReadsTheNewZone:
    def test_cache_flush_empties_an_opponent_s_cache(
        self, state: GameState, place: Place
    ) -> None:
        """An ordinary Magic card, using an ordinary ``for_each``, over a zone
        the base game has never heard of."""
        for def_id in ("base.hero.bad_axe", "base.hero.dodgy_dealer"):
            state.move_card(place(state, def_id, "hand", "p2"), zone_id("cache", "p2"))
        flush = place(state, CACHE_FLUSH, "hand", "p1")

        run(state, {"op": "play_card_from_hand", "card": flush, "kind": "magic"})
        assert cache_of(state, "p2") == ()

    def test_cold_boot_triggers_on_the_pack_s_own_event(
        self, state: GameState, place: Place
    ) -> None:
        """A card subscribing to ``cache.uploaded`` — a trigger on an event only
        this pack's plugin knows how to apply."""
        place(state, COLD_BOOT, "party", "p1")
        card = place(state, "base.hero.bad_axe", "hand", "p1")
        player = state.player(PlayerId("p1"))
        player.action_points = 0

        run(state, {"op": "upload_card", "card": card, "player": "p1"})
        assert player.action_points == 1


# ---------------------------------------------------------------------------
# It plays
# ---------------------------------------------------------------------------


class TestItPlaysToTheEnd:
    @pytest.mark.parametrize("seed", ["oc-1", "oc-2", "oc-3"])
    def test_a_full_game_terminates_with_nothing_stranded(
        self, content: ContentRegistry, seed: str
    ) -> None:
        from here_to_slay.ai.random_agent import RandomAgent

        engine = Engine.new(content, ["Ann", "Bob", "Cid"], seed=seed, max_turns=80)
        engine.run(RandomAgent(seed=seed))

        assert not find_violations(engine.state)
        assert engine.state.zone("limbo").is_empty

    def test_the_new_win_condition_is_actually_reachable(
        self, content: ContentRegistry
    ) -> None:
        """Not "a game ended" — a game ended *because of the cache*."""
        from here_to_slay.ai.random_agent import RandomAgent
        from here_to_slay.core.victory import satisfied_by

        for seed in range(40):
            engine = Engine.new(content, ["Ann", "Bob", "Cid"], seed=seed, max_turns=80)
            status = engine.run(RandomAgent(seed=seed))
            if not (isinstance(status, GameOver) and status.winner):
                continue
            if any(v.id == "full_cache" for v in satisfied_by(engine.state, status.winner)):
                assert len(engine.state.zone_of("cache", status.winner)) >= 4
                return
        pytest.fail("no seed in 0..39 was won through the cache")


# ---------------------------------------------------------------------------
# The acceptance criterion
# ---------------------------------------------------------------------------


class TestNothingInCoreKnowsAboutThisPack:
    """Phase 10's actual test. The variant must be a directory, not a fork."""

    #: words that would betray the engine having been taught about this pack
    FORBIDDEN = ("overclock", "cache_size", "cache_burn", "upload_card", "cache.uploaded")

    def test_no_engine_source_file_mentions_the_variant(self) -> None:
        pattern = re.compile("|".join(re.escape(word) for word in self.FORBIDDEN))
        offenders = [
            f"{path.relative_to(PROJECT_ROOT).as_posix()}:{index}"
            for path in sorted(CORE.rglob("*.py"))
            for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if pattern.search(line)
        ]
        assert not offenders, "\n".join(offenders)

    def test_the_engine_alone_registers_none_of_the_pack_s_ops(self) -> None:
        """A fresh interpreter that imports only the engine knows none of them.

        In-process this would be a lie: this module's fixture has the plugin
        installed while it runs. A subprocess is the only honest way to ask what
        ``import here_to_slay.core`` alone registers.
        """
        source = (
            "import json, here_to_slay.core as core;"
            "print(json.dumps(core.registered_ops()))"
        )
        result = subprocess.run(
            [sys.executable, "-c", source],
            capture_output=True,
            text=True,
            check=True,
            cwd=PROJECT_ROOT,
        )
        ops = json.loads(result.stdout)
        assert "upload_card" not in ops["effects"]
        assert "cache_size" not in ops["conditions"]
        assert "cached" not in ops["selectors"]
        assert "cache_burn" not in ops["costs"]
        assert "cache.uploaded" not in ops["mutators"]

    def test_the_base_game_still_validates_and_plays_unchanged(
        self, base_content: ContentRegistry
    ) -> None:
        """Loading a variant must not have taught the base pack anything."""
        assert validate_registry(base_content, check_art=False) == []
        assert "hacker" not in base_content.rules.classes
        assert "cache" not in base_content.rules.zone_ids
