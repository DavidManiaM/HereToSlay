"""``HeuristicAgent`` — weighted heuristic scoring over legal decisions.

The heuristic agent scores each available option according to a dictionary of
weights and chooses the option with the highest score (breaking ties with seeded
randomness).

Weights can be configured via a YAML file (e.g. ``data/base/ai_weights.yaml``),
allowing variants and mods to re-tune AI behaviour without changing any Python
code.
"""

from __future__ import annotations

import random
from pathlib import Path

import yaml

from here_to_slay.core.interpreter import (
    CardsChosen,
    ChooseCards,
    ChooseIntent,
    ChooseOption,
    ChoosePlayer,
    Confirm,
    Confirmed,
    Decision,
    DecisionSource,
    Intent,
    IntentChosen,
    Option,
    OptionChosen,
    PlayerChosen,
    ReactionChosen,
    ReactionPrompt,
    Request,
)

DEFAULT_WEIGHTS: dict[str, float] = {
    "action.attack_monster": 6.0,
    "action.play_hero": 5.0,
    "action.use_leader_ability": 4.5,
    "action.use_hero_ability": 4.0,
    "action.cast_magic": 3.5,
    "action.equip_item": 3.0,
    "action.draw": 1.5,
    "action.discard_and_draw": 0.5,
    "reaction.challenge": 3.0,
    "reaction.modifier": 4.0,
    "reaction.pass": 2.0,
    "confirm.yes": 1.0,
    "confirm.no": 1.0,
}


def load_weights_from_file(path: Path | str) -> dict[str, float]:
    """Load weights dictionary from a YAML file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"AI weights file not found: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    weights_data = data.get("weights", data)
    return {str(k): float(v) for k, v in weights_data.items()}


class HeuristicAgent(DecisionSource):
    """An agent that chooses moves based on configurable heuristic weights.

    Parameters
    ----------
    seed:
        Seed for the agent's RNG used for tie-breaking.
    weights:
        Optional mapping of weight keys to numeric values.
    weights_path:
        Optional path to a YAML file containing a ``weights`` mapping.
    """

    def __init__(
        self,
        seed: int | str = 0,
        *,
        weights: dict[str, float] | None = None,
        weights_path: Path | str | None = None,
    ) -> None:
        self.rng = random.Random(seed)
        self.weights = dict(DEFAULT_WEIGHTS)
        if weights_path is not None:
            self.weights.update(load_weights_from_file(weights_path))
        if weights:
            self.weights.update(weights)

    def answer(self, request: Request) -> Decision:
        """Answer a request using heuristic scoring."""
        match request.kind:
            case "choose_intent":
                assert isinstance(request, ChooseIntent)
                return self._choose_intent(request)
            case "reaction":
                assert isinstance(request, ReactionPrompt)
                return self._choose_reaction(request)
            case "choose_option":
                assert isinstance(request, ChooseOption)
                return self._choose_option(request)
            case "choose_cards":
                assert isinstance(request, ChooseCards)
                return self._choose_cards(request)
            case "choose_player":
                assert isinstance(request, ChoosePlayer)
                return self._choose_player(request)
            case "confirm":
                assert isinstance(request, Confirm)
                return self._confirm(request)
        raise AssertionError(
            f"HeuristicAgent.answer: unknown request kind '{request.kind}'"
        )

    # ------------------------------------------------------------------
    # Scoring & Choice helpers
    # ------------------------------------------------------------------

    def score_intent(self, intent: Intent) -> float:
        """Score an intent based on its action and optional parameters."""
        key = f"action.{intent.action}"
        return self.weights.get(key, 1.0)

    def _choose_intent(self, request: ChooseIntent) -> IntentChosen:
        if not request.intents:
            raise ValueError("No intents provided in ChooseIntent request")

        scored = [
            (self.score_intent(intent), self.rng.random(), intent)
            for intent in request.intents
        ]
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return IntentChosen(scored[0][2])

    def score_reaction_option(
        self, option: Option | None, request: ReactionPrompt
    ) -> float:
        """Score an offered reaction or pass."""
        if option is None:
            return self.weights.get("reaction.pass", 2.0)

        # Check window-specific reaction weights
        if request.window == "card_played":
            return self.weights.get("reaction.challenge", 3.0)
        if request.window == "roll_modification":
            return self.weights.get("reaction.modifier", 4.0)

        # Direct card / key weight check
        if option.key and f"reaction.{option.key}" in self.weights:
            return self.weights[f"reaction.{option.key}"]

        return self.weights.get(f"reaction.window.{request.window}", 2.5)

    def _choose_reaction(self, request: ReactionPrompt) -> ReactionChosen:
        options: list[Option | None] = [None, *list(request.options)]
        scored = [
            (self.score_reaction_option(opt, request), self.rng.random(), opt)
            for opt in options
        ]
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best_option = scored[0][2]
        return ReactionChosen(best_option.card if best_option is not None else None)

    def _choose_option(self, request: ChooseOption) -> OptionChosen:
        scored = [
            (
                self.weights.get(f"option.{opt.key}", 1.0),
                self.rng.random(),
                opt.key,
            )
            for opt in request.options
        ]
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return OptionChosen(scored[0][2])

    def _choose_cards(self, request: ChooseCards) -> CardsChosen:
        lo = request.minimum
        hi = min(request.maximum, len(request.candidates))
        count = max(lo, hi)
        chosen = tuple(self.rng.sample(list(request.candidates), count))
        return CardsChosen(chosen)

    def _choose_player(self, request: ChoosePlayer) -> PlayerChosen:
        candidates = list(request.candidates)
        # If choosing an opponent / victim, prefer other players over self if both present
        others = [p for p in candidates if p != request.requester]
        target_pool = others if others else candidates
        return PlayerChosen(self.rng.choice(target_pool))

    def _confirm(self, request: Confirm) -> Confirmed:
        score_yes = self.weights.get("confirm.yes", 1.0) + self.rng.random() * 0.1
        score_no = self.weights.get("confirm.no", 1.0) + self.rng.random() * 0.1
        return Confirmed(score_yes >= score_no)


__all__ = ["DEFAULT_WEIGHTS", "HeuristicAgent", "load_weights_from_file"]
