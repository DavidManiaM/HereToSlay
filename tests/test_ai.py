"""Phase 8 — Tests for AI agents (RandomAgent, HeuristicAgent) and simulation harness."""

from __future__ import annotations

from pathlib import Path

import pytest

from here_to_slay.ai.heuristic_agent import (
    DEFAULT_WEIGHTS,
    HeuristicAgent,
    load_weights_from_file,
)
from here_to_slay.ai.random_agent import RandomAgent
from here_to_slay.content import ContentRegistry
from here_to_slay.core import (
    CardId,
    CardsChosen,
    ChooseCards,
    ChooseIntent,
    ChooseOption,
    ChoosePlayer,
    Confirm,
    Confirmed,
    Engine,
    Intent,
    IntentChosen,
    Option,
    OptionChosen,
    PlayerChosen,
    PlayerId,
    ReactionChosen,
    ReactionPrompt,
    find_violations,
)
from here_to_slay.core.interpreter import GameOver

# ---------------------------------------------------------------------------
# RandomAgent unit tests
# ---------------------------------------------------------------------------


def test_random_agent_answers_all_request_types() -> None:
    agent = RandomAgent(seed=42)

    # choose_intent
    intent_req = ChooseIntent(
        requester=PlayerId("p1"),
        intents=(
            Intent(action="draw"),
            Intent(action="play_hero", card=CardId("c1")),
        ),
    )
    ans1 = agent.answer(intent_req)
    assert isinstance(ans1, IntentChosen)
    assert ans1.intent in intent_req.intents

    # reaction
    react_req = ReactionPrompt(
        requester=PlayerId("p1"),
        window="card_played",
        options=(Option(key="c2", label="Challenge", card=CardId("c2")),),
    )
    ans2 = agent.answer(react_req)
    assert isinstance(ans2, ReactionChosen)
    assert ans2.card in (CardId("c2"), None)

    # choose_option
    opt_req = ChooseOption(
        requester=PlayerId("p1"),
        options=(Option(key="opt1", label="Opt 1"), Option(key="opt2", label="Opt 2")),
    )
    ans3 = agent.answer(opt_req)
    assert isinstance(ans3, OptionChosen)
    assert ans3.key in ("opt1", "opt2")

    # choose_cards
    cards_req = ChooseCards(
        requester=PlayerId("p1"),
        candidates=(CardId("c1"), CardId("c2"), CardId("c3")),
        minimum=1,
        maximum=2,
    )
    ans4 = agent.answer(cards_req)
    assert isinstance(ans4, CardsChosen)
    assert 1 <= len(ans4.cards) <= 2
    assert all(c in cards_req.candidates for c in ans4.cards)

    # choose_player
    player_req = ChoosePlayer(
        requester=PlayerId("p1"),
        candidates=(PlayerId("p1"), PlayerId("p2"), PlayerId("p3")),
    )
    ans5 = agent.answer(player_req)
    assert isinstance(ans5, PlayerChosen)
    assert ans5.player in player_req.candidates

    # confirm
    confirm_req = Confirm(requester=PlayerId("p1"))
    ans6 = agent.answer(confirm_req)
    assert isinstance(ans6, Confirmed)
    assert isinstance(ans6.ok, bool)


def test_random_agent_is_deterministic() -> None:
    agent1 = RandomAgent(seed="test_seed")
    agent2 = RandomAgent(seed="test_seed")

    intent_req = ChooseIntent(
        requester=PlayerId("p1"),
        intents=(
            Intent(action="draw"),
            Intent(action="play_hero", card=CardId("c1")),
            Intent(action="attack_monster", target="m1"),
        ),
    )

    for _ in range(10):
        ans1 = agent1.answer(intent_req)
        ans2 = agent2.answer(intent_req)
        assert ans1 == ans2


def test_random_agent_reaction_rate() -> None:
    # 0% reaction rate should always pass
    passing_agent = RandomAgent(seed=1, reaction_rate=0.0)
    react_req = ReactionPrompt(
        requester=PlayerId("p1"),
        window="card_played",
        options=(Option(key="c1", label="Card", card=CardId("c1")),),
    )
    assert passing_agent.answer(react_req) == ReactionChosen(None)

    # 100% reaction rate should always react when options exist
    reacting_agent = RandomAgent(seed=1, reaction_rate=1.0)
    assert reacting_agent.answer(react_req) == ReactionChosen(CardId("c1"))


# ---------------------------------------------------------------------------
# HeuristicAgent unit tests
# ---------------------------------------------------------------------------


def test_heuristic_agent_prioritizes_high_weight_actions() -> None:
    weights = {
        "action.draw": 1.0,
        "action.play_hero": 10.0,
        "action.attack_monster": 5.0,
    }
    agent = HeuristicAgent(seed=42, weights=weights)

    intents = (
        Intent(action="draw"),
        Intent(action="play_hero", card=CardId("c1")),
        Intent(action="attack_monster", target="m1"),
    )
    req = ChooseIntent(requester=PlayerId("p1"), intents=intents)
    chosen = agent.answer(req)
    assert isinstance(chosen, IntentChosen)
    assert chosen.intent.action == "play_hero"


def test_heuristic_agent_loads_weights_from_yaml(tmp_path: Path) -> None:
    yaml_file = tmp_path / "custom_weights.yaml"
    yaml_file.write_text(
        """
weights:
  action.draw: 99.0
  action.play_hero: 0.1
  reaction.challenge: 50.0
""",
        encoding="utf-8",
    )

    loaded = load_weights_from_file(yaml_file)
    assert loaded["action.draw"] == 99.0
    assert loaded["reaction.challenge"] == 50.0

    agent = HeuristicAgent(seed=1, weights_path=yaml_file)
    assert agent.weights["action.draw"] == 99.0

    req = ChooseIntent(
        requester=PlayerId("p1"),
        intents=(
            Intent(action="draw"),
            Intent(action="play_hero", card=CardId("c1")),
        ),
    )
    chosen = agent.answer(req)
    assert isinstance(chosen, IntentChosen)
    assert chosen.intent.action == "draw"


def test_heuristic_agent_default_weights_fallback() -> None:
    agent = HeuristicAgent(seed=1)
    assert agent.score_intent(Intent(action="unknown_custom_action")) == 1.0
    assert agent.score_intent(Intent(action="attack_monster")) == DEFAULT_WEIGHTS["action.attack_monster"]


def test_heuristic_agent_reaction_scoring() -> None:
    agent = HeuristicAgent(seed=1, weights={"reaction.challenge": 10.0, "reaction.pass": 1.0})
    req = ReactionPrompt(
        requester=PlayerId("p1"),
        window="card_played",
        options=(Option(key="c1", label="Challenge", card=CardId("c1")),),
    )
    ans = agent.answer(req)
    assert ans == ReactionChosen(CardId("c1"))

    # If pass is weighted much higher, it should pass
    pass_agent = HeuristicAgent(seed=1, weights={"reaction.challenge": 1.0, "reaction.pass": 10.0})
    ans_pass = pass_agent.answer(req)
    assert ans_pass == ReactionChosen(None)


# ---------------------------------------------------------------------------
# Integration / Game Simulation tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(15))
def test_random_agent_plays_full_game_without_violations(
    base_content: ContentRegistry, seed: int
) -> None:
    engine = Engine.new(base_content, ["Ann", "Bob", "Cid"], seed=seed, max_turns=60)
    agent = RandomAgent(seed=seed)
    status = engine.run(agent)

    assert not isinstance(status, GameOver) or status.winner is not None or engine.machine.finished
    assert find_violations(engine.state) == []
    assert engine.state.zone("limbo").is_empty


@pytest.mark.parametrize("seed", range(15))
def test_heuristic_agent_plays_full_game_without_violations(
    base_content: ContentRegistry, seed: int
) -> None:
    engine = Engine.new(base_content, ["Ann", "Bob", "Cid"], seed=seed, max_turns=60)
    agent = HeuristicAgent(seed=seed)
    status = engine.run(agent)

    assert not isinstance(status, GameOver) or status.winner is not None or engine.machine.finished
    assert find_violations(engine.state) == []
    assert engine.state.zone("limbo").is_empty


def test_cli_sim_command(base_content: ContentRegistry) -> None:
    from rich.console import Console

    from here_to_slay.cli import build_parser, cmd_sim

    parser = build_parser()
    args = parser.parse_args(["sim", "data/base", "--games", "5", "--players", "2", "--max-turns", "40", "-q"])
    console = Console(quiet=True)
    ret = cmd_sim(args, console)
    assert ret == 0
