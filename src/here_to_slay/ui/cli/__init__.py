"""CLI presenter and board renderer.

Two modules:

* ``render`` — pure renderer: ``GameView`` → ``rich`` renderables, no I/O
* ``presenter`` — ``DecisionSource`` for a human at a terminal; uses ``render``
"""
