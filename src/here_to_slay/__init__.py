"""Here to Slay — a data-driven, moddable card-game engine.

Layering (enforced by tests/test_layering.py):

    ui/, ai/   →  core/  →  content/

``content`` never imports ``core``; ``core`` never imports ``ui``, ``ai``,
``pygame``, ``rich`` or ``random``.
"""

__version__ = "0.1.0"
