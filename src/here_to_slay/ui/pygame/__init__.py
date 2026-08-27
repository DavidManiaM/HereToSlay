"""``ui/pygame/`` — the graphical client for Here to Slay.

This layer sits beside ``ui/cli/``: it may import ``core/``, ``content/`` and
``ai/``, and none of them may import it. ``tests/test_layering.py`` walks the
import graph and asserts it.

The client never mutates game state. It reads a redacted
:class:`~here_to_slay.core.view.GameView`, and the only channel back into the
game is a :class:`~here_to_slay.core.interpreter.Decision` handed to
:class:`~.presenter.PygamePresenter`. That is what lets the same engine run
under the terminal client, an agent and the fuzz harness unchanged.

Modules, bottom up:

===================  =========================================================
``theme``            palette, fonts, easing, glass/gradient/shadow primitives
``icons``            vector glyphs drawn with ``pygame.draw`` — no image files
``art``              finds card art under ``assets/``, invents it when missing
``atmosphere``       living table: felt grain, motes, class constellation
``card_renderer``    a card face from a ``CardDef``, cached (bounded LRU)
``animations``       the cosmetic effects, plus screen shake and flash
``widgets``          buttons, card sprites, zones, toasts, scrollers, fields
``layout``           every named screen region, rebuilt on resize
``panels``           the nine composite regions of the board
``tracker``          diffs two ``GameView``s into "what just happened"
``sound``            procedurally synthesised cues; no audio files
``cues``             which cue a *moment* plays — a table a pack may re-point
``replay``           the transport and bar that watch a logged game
``overlays``         rules, card detail, log, menu, settings, saves, handover, game over
``devconsole``       the Ctrl+Shift+D console (see :class:`~.devconsole.DevHost`)
``scenes``           the board scene: wires all of the above to requests
``presenter``        the engine-thread/GUI-thread bridge
``app``              window, clock, engine thread, restart
===================  =========================================================

``docs/ui_guide.md`` is the long-form tour.
"""

from here_to_slay.ui.pygame.app import GameSetup, PygameApp, launch

__all__ = ["GameSetup", "PygameApp", "launch"]
