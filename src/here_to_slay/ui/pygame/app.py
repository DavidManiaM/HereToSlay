"""The window, the clock, and the engine thread.

``app.py`` owns everything the board scene must not: the display surface, the
frame clock, the audio device, and the lifecycle of the :class:`Engine` — which
includes building a *new* one when somebody asks for another game.

The split matters because the engine is blocking by design. ``engine.run()``
drives the whole game and calls back into a :class:`DecisionSource` whenever it
needs an answer, so it lives on a worker thread while pygame keeps the window
responsive at 60 fps. :class:`~.presenter.PygamePresenter` is the only thing
that spans both threads (``docs/architecture_notes.md §8``).

Nothing here knows what a Hero is.
"""

from __future__ import annotations

import contextlib
import threading
import traceback
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import pygame

from here_to_slay.ui.pygame import materials
from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.card_renderer import clear_card_cache
from here_to_slay.ui.pygame.icons import draw_icon
from here_to_slay.ui.pygame.layout import MIN_H, MIN_W, LayoutManager
from here_to_slay.ui.pygame.presenter import DEFAULT_AI_DELAY, PygamePresenter
from here_to_slay.ui.pygame.scenes import GameScene, SceneHooks
from here_to_slay.ui.pygame.sound import SoundBoard
from here_to_slay.ui.pygame.theme import C, clear_surface_caches

if TYPE_CHECKING:
    from here_to_slay.content.registry import ContentRegistry
    from here_to_slay.core.engine import Engine
    from here_to_slay.core.interpreter import DecisionSource

WINDOW_TITLE = "Here to Slay"
DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
#: Chrome scale the client ships with. 1.0 keeps HUD text readable at 1080p;
#: users can still shrink via ``--ui-scale``.
DEFAULT_UI_SCALE = 1.0
#: Room to leave for the window title bar and taskbar when clamping to the
#: desktop, so asking for 1080p on a 1080p monitor does not open off-screen.
DESKTOP_MARGIN_W = 32
DESKTOP_MARGIN_H = 88


@dataclass(frozen=True, slots=True)
class GameSetup:
    """Everything needed to deal a game. Restart re-deals from one of these."""

    names: tuple[str, ...] = ("Jucător 1", "Jucător 2")
    seed: int | str = 0
    max_turns: int = 0
    #: The last ``ai_seats`` names are played by the agent. Seat 0 is always
    #: the local player, so a solo game is ``ai_seats = len(names) - 1``.
    ai_seats: int = 0

    @property
    def human_names(self) -> tuple[str, ...]:
        return self.names[: max(0, len(self.names) - self.ai_seats)]

    def resized(self, players: int, ai_seats: int, seed: int | str) -> GameSetup:
        """A copy for ``players`` seats, keeping any names the user supplied."""
        players = max(2, players)
        names = list(self.names[:players])
        while len(names) < players:
            names.append(f"Jucător {len(names) + 1}")
        return replace(
            self, names=tuple(names), ai_seats=max(0, min(ai_seats, players - 1)), seed=seed,
        )


class PygameApp:
    """The desktop client. One window, one game at a time, restartable."""

    def __init__(
        self,
        registry: ContentRegistry,
        setup: GameSetup | None = None,
        *,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        fullscreen: bool = False,
        reveal_all: bool = False,
        sound: bool = True,
        ui_scale: float = DEFAULT_UI_SCALE,
        ai_delay: float = DEFAULT_AI_DELAY,
        agent: DecisionSource | None = None,
        engine: Engine | None = None,
    ) -> None:
        self.registry = registry
        self.setup = setup or GameSetup()
        self.width = max(MIN_W, width)
        self.height = max(MIN_H, height)
        self.fullscreen = fullscreen
        self.reveal_all = reveal_all
        self.ui_scale = ui_scale
        self.ai_delay = ai_delay
        self._agent_override = agent

        self.running = False
        self.screen: pygame.Surface | None = None
        self.clock: pygame.time.Clock | None = None
        self._apply_ui_scale()
        self.layout = LayoutManager(self.width, self.height)
        self.sound = SoundBoard(enabled=sound)

        self.engine: Engine | None = engine
        self.presenter: PygamePresenter | None = None
        self.scene: GameScene | None = None
        self._thread: threading.Thread | None = None
        self._engine_error: BaseException | None = None
        self._restart: GameSetup | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self) -> int:
        """Open the window and play until the user quits. Returns an exit code."""
        self._open_window()
        self._begin(self.setup, engine=self.engine)
        self.running = True
        assert self.clock is not None
        try:
            while self.running:
                dt = min(self.clock.tick(60) / 1000.0, 0.1)
                self._pump_events()
                if not self.running:
                    break
                self._advance(dt)
                self._paint()
                if self._restart is not None:
                    pending, self._restart = self._restart, None
                    self._begin(pending)
        finally:
            self._shutdown()
        return 0 if self._engine_error is None else 1

    def stop(self) -> None:
        self.running = False

    def new_game(
        self,
        *,
        players: int | None = None,
        ai_seats: int | None = None,
        seed: int | str | None = None,
    ) -> None:
        """Queue a fresh deal. Applied at the end of the frame.

        Deferred because the request arrives from inside the scene's own event
        handling — tearing down the scene mid-callback would pull the object
        making the call out from under it.
        """
        setup = self.setup
        if players is not None or ai_seats is not None or seed is not None:
            setup = setup.resized(
                players if players is not None else len(setup.names),
                ai_seats if ai_seats is not None else setup.ai_seats,
                seed if seed is not None else _fresh_seed(),
            )
        else:
            setup = replace(setup, seed=_fresh_seed())
        self._restart = setup

    # -- window ------------------------------------------------------------

    def _open_window(self) -> None:
        pygame.init()
        pygame.font.init()
        pygame.display.set_caption(WINDOW_TITLE)
        with contextlib.suppress(pygame.error):
            pygame.display.set_icon(_window_icon())
        # Only safe once the display is initialised, which is why the request
        # is clamped here rather than in __init__.
        self.width, self.height = fit_to_desktop(self.width, self.height)
        self.screen = pygame.display.set_mode(self._mode_size(), self._mode_flags())
        self.clock = pygame.time.Clock()
        materials.init(self.screen)
        self._sync_size()

    def _mode_flags(self) -> int:
        if self.fullscreen:
            # Explicit desktop size + SCALED (logical size must be non-zero —
            # ``(0, 0)`` raises ``Cannot set 0 sized SCALED display mode``).
            return pygame.FULLSCREEN | pygame.SCALED
        return pygame.RESIZABLE

    def _desktop_size(self) -> tuple[int, int]:
        try:
            desktops = pygame.display.get_desktop_sizes()
        except (pygame.error, AttributeError):
            desktops = []
        if desktops:
            return int(desktops[0][0]), int(desktops[0][1])
        return max(MIN_W, self.width), max(MIN_H, self.height)

    def _mode_size(self) -> tuple[int, int]:
        if self.fullscreen:
            return self._desktop_size()
        return (self.width, self.height)

    def toggle_fullscreen(self) -> None:
        self.fullscreen = not self.fullscreen
        size = self._mode_size()
        flags = self._mode_flags()
        try:
            self.screen = pygame.display.set_mode(size, flags)
        except pygame.error:
            # Fallback without SCALED if the driver rejects the combo.
            if self.fullscreen:
                self.screen = pygame.display.set_mode(size, pygame.FULLSCREEN)
            else:
                self.screen = pygame.display.set_mode(size, pygame.RESIZABLE)
        self._sync_size()

    def _apply_ui_scale(self, height: int | None = None) -> None:
        """Resolution-relative chrome, times the user's own preference."""
        base = max(0.8, min(1.5, (height or self.height) / 1080.0))
        T.set_scale(base * self.ui_scale)

    def _sync_size(self) -> None:
        assert self.screen is not None
        width, height = self.screen.get_size()
        self._apply_ui_scale(height)
        self.layout.rebuild(width, height)
        materials.resize((width, height))
        if not self.fullscreen:
            self.width, self.height = width, height
        if self.scene is not None:
            self.scene.resize(width, height)

    # -- game --------------------------------------------------------------

    def _begin(self, setup: GameSetup, *, engine: Engine | None = None) -> None:
        """Tear down any running game and start one from ``setup``."""
        self._end_game()
        from here_to_slay.core.engine import Engine as _Engine

        self.setup = setup
        self.engine = engine or _Engine.new(
            self.registry, list(setup.names), seed=setup.seed, max_turns=setup.max_turns,
        )
        order = list(self.engine.state.turn_order)
        human_seats = order[: len(order) - setup.ai_seats] if setup.ai_seats else None
        self.presenter = PygamePresenter(
            self.engine, self.registry,
            human_seats=human_seats,
            agent=self._make_agent(setup) if setup.ai_seats or self._agent_override else None,
            ai_delay=self.ai_delay,
        )
        self.scene = GameScene(
            self.engine, self.presenter, self.registry, self.layout,
            sound=self.sound,
            reveal_all=self.reveal_all,
            hooks=SceneHooks(
                new_game=self.new_game,
                quit=self.stop,
                toggle_fullscreen=self.toggle_fullscreen,
            ),
        )
        self._engine_error = None
        self._thread = threading.Thread(target=self._drive_engine, name="hts-engine", daemon=True)
        self._thread.start()
        pygame.display.set_caption(
            f"{WINDOW_TITLE}  \u2014  {len(setup.names)} players  \u00b7  seed {setup.seed}"
        )

    def _make_agent(self, setup: GameSetup) -> DecisionSource:
        if self._agent_override is not None:
            return self._agent_override
        # Imported here so a GUI with no AI seats never pays for it, and so
        # `ui/` keeps a single well-known dependency on `ai/`.
        from here_to_slay.ai import HeuristicAgent

        return HeuristicAgent(seed=setup.seed)

    def _drive_engine(self) -> None:
        presenter = self.presenter
        engine = self.engine
        if presenter is None or engine is None:
            return
        try:
            engine.run(presenter)
        except InterruptedError:
            pass  # the window closed, or a new game replaced this one
        except BaseException as exc:
            # A crash in content or rules must not take the window with it:
            # keep the board on screen, say so, and print the traceback.
            self._engine_error = exc
            traceback.print_exc()

    def _end_game(self) -> None:
        if self.presenter is not None:
            self.presenter.close()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        self.presenter = None
        self.scene = None

    # ------------------------------------------------------------------
    # Frame
    # ------------------------------------------------------------------

    def _pump_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.stop()
                return
            if event.type == pygame.VIDEORESIZE and not self.fullscreen:
                self.screen = pygame.display.set_mode(
                    (max(MIN_W, event.w), max(MIN_H, event.h)), self._mode_flags()
                )
                self._sync_size()
                continue
            if event.type == pygame.KEYDOWN and self._app_hotkey(event):
                continue
            if self.scene is not None:
                self.scene.handle_event(event)

    def _app_hotkey(self, event: pygame.event.Event) -> bool:
        """Keys the window owns, checked before the scene sees them."""
        mods = pygame.key.get_mods()
        if event.key == pygame.K_F11:
            self.toggle_fullscreen()
            return True
        if event.key == pygame.K_q and mods & pygame.KMOD_CTRL:
            self.stop()
            return True
        if event.key == pygame.K_F5:
            self.new_game()
            return True
        return False

    def _advance(self, dt: float) -> None:
        if self.scene is None or self.clock is None:
            return
        self.scene.fps = self.clock.get_fps()
        self.scene.update(dt)
        if self._engine_error is not None:
            self.scene.toast.show(
                f"engine error: {type(self._engine_error).__name__} \u2014 see the console",
                colour=C.BAD, duration=8.0, icon="close",
            )
            self._engine_error = None

    def _paint(self) -> None:
        if self.screen is None:
            return
        if self.scene is not None:
            self.scene.draw(self.screen)
        else:
            self.screen.fill(C.VOID)
        pygame.display.flip()

    def _shutdown(self) -> None:
        self._end_game()
        self.sound.stop()
        materials.shutdown()
        clear_card_cache()
        clear_surface_caches()
        pygame.quit()


def fit_to_desktop(width: int, height: int) -> tuple[int, int]:
    """Shrink a requested window until it fits on the primary monitor.

    Asking for 1920x1080 on a 1080p screen would put the title bar above the
    top edge, so anything that does not fit falls back to the largest 16:9
    box that does. Never goes below the ``MIN_W``/``MIN_H`` floor.
    """
    width, height = max(MIN_W, int(width)), max(MIN_H, int(height))
    try:
        desktops = pygame.display.get_desktop_sizes()
    except (pygame.error, AttributeError):
        desktops = []
    if not desktops:
        return width, height

    avail_w = max(MIN_W, desktops[0][0] - DESKTOP_MARGIN_W)
    avail_h = max(MIN_H, desktops[0][1] - DESKTOP_MARGIN_H)
    if width <= avail_w and height <= avail_h:
        return width, height
    fit_w = min(avail_w, avail_h * 16 // 9)
    return max(MIN_W, fit_w), max(MIN_H, fit_w * 9 // 16)


def _fresh_seed() -> str:
    import secrets

    return secrets.token_hex(4)


def _window_icon(size: int = 32) -> pygame.Surface:
    """A drawn icon, so the client ships no binary assets it does not need."""
    surf = T.surface((size, size))
    pygame.draw.circle(surf, C.FELT_DEEP, (size // 2, size // 2), size // 2)
    pygame.draw.circle(surf, C.GOLD, (size // 2, size // 2), size // 2, 2)
    draw_icon(surf, "skull", (size // 2, size // 2), int(size * 0.62), C.GOLD)
    return surf


def launch(
    registry: ContentRegistry,
    names: list[str],
    *,
    seed: int | str = 0,
    max_turns: int = 0,
    ai_seats: int = 0,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fullscreen: bool = False,
    reveal_all: bool = False,
    sound: bool = True,
    ui_scale: float = DEFAULT_UI_SCALE,
    **kwargs: Any,
) -> int:
    """One call for the CLI: build the setup, open the window, play."""
    app = PygameApp(
        registry,
        GameSetup(
            names=tuple(names), seed=seed, max_turns=max_turns,
            ai_seats=max(0, min(ai_seats, len(names) - 1)),
        ),
        width=width, height=height, fullscreen=fullscreen,
        reveal_all=reveal_all, sound=sound, ui_scale=ui_scale, **kwargs,
    )
    return app.run()


__all__ = [
    "DEFAULT_HEIGHT",
    "DEFAULT_UI_SCALE",
    "DEFAULT_WIDTH",
    "GameSetup",
    "PygameApp",
    "fit_to_desktop",
    "launch",
]
