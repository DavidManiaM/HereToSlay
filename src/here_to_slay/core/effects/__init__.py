"""The effect op catalogue (``docs/card_schemas.md §3.1``).

An op is ``(ctx, params) -> Generator``. The three rules every op in here
follows, and every plugin op should:

1. **Ask through ``ctx.ask_*``, never block.** A generator that yields a
   ``Request`` suspends the whole game cleanly; anything else deadlocks pygame.
2. **Change state by emitting an event, not by writing.** ``draw`` emits
   ``card.drawn``; the mutator moves the card. That is what gives every change a
   PRE window a Challenge can land in, and a POST window other cards can react
   to, without the op knowing that either exists.
3. **Return ``Outcome.CANCELLED`` when the thing did not happen**, so an
   enclosing ``seq`` stops rather than carrying on as if it had.

Importing this package registers every op in it.
"""

from here_to_slay.core.effects import (
    actions,
    cards,
    control,
    meta,
    party,
    rolls,
)

__all__ = ["actions", "cards", "control", "meta", "party", "rolls"]
