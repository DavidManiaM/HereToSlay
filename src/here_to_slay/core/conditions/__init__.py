"""The predicate catalogue (``docs/card_schemas.md §4``).

A condition is ``(ctx, params) -> bool``. Three rules keep them safe to call
from anywhere — inside a filter, a trigger gate, a victory check or a legality
test that runs once per candidate card in a menu:

* **Pure.** A condition never mutates and never asks a question. The registry
  rejects a generator outright, because a predicate that could suspend would
  make "is this legal?" a question with side effects.
* **Total.** A missing optional parameter means "don't care", not an error.
* **Honest about targets.** Anything that talks about a card defaults to
  ``$candidate`` when one is bound (it is inside a filter) and ``$card``
  otherwise, so the same node works in both places.

Importing this package registers everything in it.
"""

from here_to_slay.core.conditions import board, cards, logic

__all__ = ["board", "cards", "logic"]
