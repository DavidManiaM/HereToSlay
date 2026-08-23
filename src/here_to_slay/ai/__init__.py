"""``ai/`` — AI agents for solo play and the fuzz harness.

Both agents implement :class:`~here_to_slay.core.interpreter.DecisionSource`,
so they can be passed directly to :meth:`~here_to_slay.core.engine.Engine.run`
alongside the CLI presenter or the log replayer — no engine changes required.

``ai/`` may import ``core/`` and ``content/`` but not ``ui/``.
``tests/test_layering.py`` walks the import graph and asserts it.
"""

from here_to_slay.ai.heuristic_agent import HeuristicAgent
from here_to_slay.ai.random_agent import RandomAgent

__all__ = ["HeuristicAgent", "RandomAgent"]
