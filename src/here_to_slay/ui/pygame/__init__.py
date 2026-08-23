"""``ui/pygame/`` — the graphical client for Here to Slay.

This layer sits beside ``ui/cli/`` in the architecture, importing ``core/``
and ``content/`` but never the reverse.  The engine runs on a background
thread; the pygame main loop renders the board and collects mouse clicks,
bridging the two via :class:`PygamePresenter`.

Every card is rendered procedurally from its :class:`~content.schema.CardDef`
— name, class colour, text, roll thresholds — so a new YAML card is visible
the moment it exists.  Animations are cosmetic and never gate the engine.
"""

from here_to_slay.ui.pygame.app import PygameApp

__all__ = ["PygameApp"]
