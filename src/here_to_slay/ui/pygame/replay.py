"""The replay viewer: watching a logged game on the real board.

``hts replay`` already steps through a log in the terminal. This is the same
thing on the graphical client, and the interesting part is how little it needed:
a replay is a :class:`~here_to_slay.core.interpreter.DecisionSource` that reads
its answers from a file instead of a person, so the board, the animations, the
tracker and the log feed are the ones already there. Nothing in ``scenes.py``
has to know a game is recorded rather than live — it draws whatever the engine
asks and shows this bar underneath.

:class:`ReplayTransport` is the whole mechanism. It sits where a player would
and *paces* the log: paused until you press play, one decision per press of
step, or one every :attr:`ReplayTransport.interval` seconds while playing. It
blocks on the engine thread exactly like
:class:`~here_to_slay.ui.pygame.presenter.PygamePresenter` does, and wakes often
enough to notice the window closing.

The end of the log is not an error. A log written by an interrupted game — or a
save, which is the same file — simply runs out, and the viewer says so and stops
rather than reporting the game as finished. That distinction is the one Phase 10
found hiding a divergence in the terminal replayer, so it is drawn here in
words, not implied by a stopped animation.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

import pygame

from here_to_slay.core.interpreter import Decision, DecisionSource, Request
from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.icons import draw_icon
from here_to_slay.ui.pygame.theme import C

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.core.log import LogSource

#: Seconds between decisions at 1x. Slow enough to read the board.
DEFAULT_INTERVAL = 0.85
#: Playback rates the ``+``/``-`` keys and the bar's buttons cycle through.
SPEEDS: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
#: How often a blocked engine thread re-checks for a step or a shutdown.
WAKE_INTERVAL = 0.03


class ReplayTransport(DecisionSource):
    """Answers from a log, at whatever pace the viewer asks for.

    Thread contract, identical to the presenter's: :meth:`answer` runs on the
    engine thread and blocks; everything else is called from the frame loop.
    """

    def __init__(
        self,
        source: LogSource,
        *,
        interval: float = DEFAULT_INTERVAL,
        playing: bool = True,
        speed: float = 1.0,
    ) -> None:
        self.source = source
        self.interval = max(0.05, interval)
        self.speed = speed if speed in SPEEDS else 1.0
        self._playing = playing
        self._lock = threading.RLock()
        self._steps = 0
        self._closed = False
        self._last_at = 0.0
        #: Set once the log runs out. The engine raises through `answer`, so the
        #: flag is only for the bar; the *fact* is still an exception.
        self.exhausted = False
        self.decisions_played = 0

    # -- engine thread -----------------------------------------------------

    def answer(self, request: Request) -> Decision:
        if self._closed:
            raise InterruptedError("replay closed")
        self._await_turn()
        if self._closed:
            raise InterruptedError("replay closed")
        try:
            decision = self.source.answer(request)
        except Exception:
            # Includes ReplayExhausted, the expected end of a partial log. The
            # flag is for the bar; the exception still travels, because the app
            # decides what a stopped replay looks like.
            self.exhausted = True
            self._playing = False
            raise
        self.decisions_played += 1
        self._last_at = time.monotonic()
        return decision

    def _await_turn(self) -> None:
        """Block until the transport releases one decision."""
        while True:
            if self._closed:
                return
            with self._lock:
                if self._steps > 0:
                    self._steps -= 1
                    return
                if self._playing and time.monotonic() - self._last_at >= self._gap:
                    return
            time.sleep(WAKE_INTERVAL)

    @property
    def _gap(self) -> float:
        return self.interval / max(0.05, self.speed)

    # -- frame loop --------------------------------------------------------

    @property
    def playing(self) -> bool:
        return self._playing and not self.exhausted

    def play(self) -> None:
        if self.exhausted:
            return
        self._playing = True
        self._last_at = 0.0  # release the next decision immediately

    def pause(self) -> None:
        self._playing = False

    def toggle(self) -> bool:
        self.pause() if self._playing else self.play()
        return self._playing

    def step(self, count: int = 1) -> None:
        """Let ``count`` more decisions through, then wait again."""
        if self.exhausted:
            return
        with self._lock:
            self._steps += max(1, count)
        self._playing = False

    def faster(self) -> float:
        return self._shift(1)

    def slower(self) -> float:
        return self._shift(-1)

    def _shift(self, delta: int) -> float:
        try:
            index = SPEEDS.index(self.speed)
        except ValueError:  # pragma: no cover - speed always comes from SPEEDS
            index = SPEEDS.index(1.0)
        self.speed = SPEEDS[max(0, min(len(SPEEDS) - 1, index + delta))]
        return self.speed

    def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    # -- reading -----------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self.source.log.entries)

    @property
    def position(self) -> int:
        return int(self.source.index)

    @property
    def progress(self) -> float:
        return self.position / self.total if self.total else 1.0

    def status_line(self) -> str:
        if self.exhausted:
            return f"end of log - {self.total} decision(s)"
        state = "playing" if self._playing else "paused"
        return f"{state} - decision {self.position} of {self.total} - {self.speed:g}x"


class ReplayBar:
    """The transport controls, drawn along the bottom of the board.

    A plain widget: it reads the transport and calls its methods. It owns no
    game state and no thread, which is what lets the board scene draw it without
    knowing what a log is.
    """

    HEIGHT = 46

    def __init__(self, transport: ReplayTransport) -> None:
        self.transport = transport
        self.rect = pygame.Rect(0, 0, 640, self.HEIGHT)
        self.hovered: str = ""
        #: key -> (icon, tooltip). Order is the order they are drawn.
        self.buttons: tuple[tuple[str, str, str], ...] = (
            ("slower", "chevron_left", "Slower  [-]"),
            ("play", "play", "Play / pause  [Space]"),
            ("step", "chevron_right", "Step one decision  [.]"),
            ("faster", "bolt", "Faster  [+]"),
        )

    # -- geometry ----------------------------------------------------------

    def resize(self, layout: Any) -> None:
        width = min(760, max(420, int(layout.width * 0.44)))
        self.rect = pygame.Rect(0, 0, width, T.s(self.HEIGHT))
        self.rect.midbottom = (layout.width // 2, layout.height - T.s(8))

    def _button_rects(self) -> list[tuple[str, pygame.Rect, str, str]]:
        size = self.rect.height - T.s(12)
        out: list[tuple[str, pygame.Rect, str, str]] = []
        x = self.rect.left + T.s(10)
        for key, icon, tip in self.buttons:
            out.append((key, pygame.Rect(x, self.rect.top + T.s(6), size, size), icon, tip))
            x += size + T.s(6)
        return out

    # -- interaction -------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEMOTION:
            self.hovered = ""
            for key, rect, _icon, _tip in self._button_rects():
                if rect.collidepoint(event.pos):
                    self.hovered = key
            return False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for key, rect, _icon, _tip in self._button_rects():
                if rect.collidepoint(event.pos):
                    self.press(key)
                    return True
        return False

    def press(self, key: str) -> bool:
        actions = {
            "play": self.transport.toggle,
            "step": self.transport.step,
            "faster": self.transport.faster,
            "slower": self.transport.slower,
        }
        action = actions.get(key)
        if action is None:
            return False
        action()
        return True

    def tooltip_at(self, pos: tuple[int, int]) -> str:
        for _key, rect, _icon, tip in self._button_rects():
            if rect.collidepoint(pos):
                return tip
        return ""

    # -- drawing -----------------------------------------------------------

    def draw(self, screen: pygame.Surface) -> None:
        transport = self.transport
        T.glass(screen, self.rect, radius=self.rect.height // 2)

        for key, rect, icon, _tip in self._button_rects():
            live = key == self.hovered
            glyph = icon
            if key == "play":
                glyph = "pause" if transport.playing else "play"
            colour = C.GOLD if live else C.INK_DIM
            if transport.exhausted and key in ("play", "step", "faster"):
                colour = C.INK_FAINT
            pygame.draw.circle(
                screen, T.alpha(C.GLASS_DEEP, 200), rect.center, rect.width // 2
            )
            draw_icon(screen, glyph, rect.center, int(rect.width * 0.6), colour)

        # Scrubber: how much of the log has been played.
        buttons_right = self._button_rects()[-1][1].right
        track = pygame.Rect(
            buttons_right + T.s(12), self.rect.centery - T.s(3),
            max(T.s(40), self.rect.right - buttons_right - T.s(24)), T.s(6),
        )
        T.round_rect(screen, track, T.alpha(C.INK_FAINT, 90), radius=track.height // 2)
        filled = pygame.Rect(track)
        filled.width = max(2, int(track.width * min(1.0, transport.progress)))
        T.round_rect(
            screen, filled, C.BAD if transport.exhausted else C.GOLD,
            radius=track.height // 2,
        )
        T.text(
            screen, transport.status_line(), (track.centerx, track.bottom + T.s(4)),
            T.ui(10, bold=True),
            C.BAD if transport.exhausted else C.INK_DIM, anchor="midtop", shadow=None,
        )
        T.text(
            screen, "REPLAY", (self.rect.left + T.s(10), track.top - T.s(12)),
            T.ui(9, bold=True), T.alpha(C.GOLD, 200), shadow=None,
        )


__all__ = ["DEFAULT_INTERVAL", "SPEEDS", "ReplayBar", "ReplayTransport"]
