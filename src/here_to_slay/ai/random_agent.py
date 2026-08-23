"""``RandomAgent`` — uniform random answers over every legal option.

This is the Phase 8 agent the build plan asks for first: no game knowledge,
no card reading, just a reproducible random choice from whatever the engine
offers.  Its only virtue over ``Chaos`` in ``test_reactions.py`` is that it
is a proper module rather than a test harness artefact.

The fuzz harness (``hts sim --agent random``) uses this.  Because every
decision is drawn from the *legal* set the engine already validated, it
produces no illegal moves — only random ones.

Reproducibility: ``RandomAgent(seed)`` with the same seed produces the same
decisions against the same game, because both Python's ``random.Random`` and
the engine's own :class:`~here_to_slay.core.rng.DeterministicRng` are seeded
independently.  The agent's RNG is not the game's.
"""

from __future__ import annotations

import random
from typing import Any

from here_to_slay.core.interpreter import (
    CardsChosen,
    Confirmed,
    Decision,
    DecisionSource,
    IntentChosen,
    OptionChosen,
    PlayerChosen,
    ReactionChosen,
    Request,
)

#: Fraction of reaction prompts where the agent plays a card (vs. passing).
_REACTION_RATE = 0.5


class RandomAgent(DecisionSource):
    """Answers every question uniformly at random from the legal options.

    Parameters
    ----------
    seed:
        Seeds the agent's own :class:`random.Random`, separate from the
        game's :class:`~here_to_slay.core.rng.DeterministicRng`.  Two
        ``RandomAgent`` instances with the same seed produce identical
        decisions against the same sequence of requests.
    reaction_rate:
        Probability of playing a reaction card when one is available.
        Defaults to 50%.  Set to 0.0 for a fully-passing agent or 1.0 for
        one that always reacts.
    """

    def __init__(self, seed: int | str = 0, *, reaction_rate: float = _REACTION_RATE) -> None:
        self.rng = random.Random(seed)
        self.reaction_rate = reaction_rate

    def answer(self, request: Request) -> Decision:
        """Dispatch to the per-kind handler, or raise on an unknown kind."""
        match request.kind:
            case "choose_intent":
                return IntentChosen(self.rng.choice(request.intents))  # type: ignore[attr-defined]
            case "reaction":
                return self._reaction(request)
            case "choose_option":
                return OptionChosen(self.rng.choice(request.options).key)  # type: ignore[attr-defined]
            case "choose_cards":
                return self._cards(request)
            case "choose_player":
                return PlayerChosen(self.rng.choice(list(request.candidates)))  # type: ignore[attr-defined]
            case "confirm":
                return Confirmed(self.rng.random() < 0.5)
        raise AssertionError(
            f"RandomAgent.answer: unknown request kind '{request.kind}'; "
            f"add a case for it"
        )

    # ------------------------------------------------------------------
    # Per-kind helpers
    # ------------------------------------------------------------------

    def _reaction(self, request: Any) -> ReactionChosen:
        options = request.options
        if options and self.rng.random() < self.reaction_rate:
            return ReactionChosen(self.rng.choice(options).card)
        return ReactionChosen(None)

    def _cards(self, request: Any) -> CardsChosen:
        lo: int = request.minimum
        hi: int = min(request.maximum, len(request.candidates))
        count = self.rng.randint(lo, max(lo, hi))
        chosen = tuple(self.rng.sample(list(request.candidates), count))
        return CardsChosen(chosen)


__all__ = ["RandomAgent"]
