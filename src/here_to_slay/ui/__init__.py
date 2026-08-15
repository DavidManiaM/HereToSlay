"""UI layer — presenters for CLI, pygame, and (later) network play.

``ui/`` imports ``core/`` but never the reverse. The layering test enforces
this. Neither presenter touches ``GameState`` directly; everything they need
is in ``GameView`` (redacted) and the ``Engine`` facade.
"""
