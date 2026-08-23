"""``app.py`` — the main PyGame application loop and window manager.

Spawns the engine on a background thread and runs the 60 FPS graphical render
and event loop on the main thread.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pygame

from here_to_slay.ui.pygame.card_renderer import clear_card_cache
from here_to_slay.ui.pygame.colors import clear_font_cache
from here_to_slay.ui.pygame.layout import LayoutManager
from here_to_slay.ui.pygame.presenter import PygamePresenter
from here_to_slay.ui.pygame.scenes import GameOverScene, GameScene, InterstitialScene

if TYPE_CHECKING:
    from here_to_slay.content.registry import ContentRegistry
    from here_to_slay.core.engine import Engine


class PygameApp:
    """The PyGame graphical client for Here to Slay."""

    def __init__(
        self,
        engine: Engine,
        registry: ContentRegistry,
        *,
        width: int = 1280,
        height: int = 800,
    ) -> None:
        self.engine = engine
        self.registry = registry
        self.width = width
        self.height = height

        pygame.init()
        pygame.font.init()
        pygame.display.set_caption("Here to Slay")
        self.screen = pygame.display.set_mode(
            (self.width, self.height), pygame.RESIZABLE
        )
        self.clock = pygame.time.Clock()
        self.running = False

        self.layout = LayoutManager(self.width, self.height)
        self.presenter = PygamePresenter(self.engine, self.registry)
        self.game_scene = GameScene(
            self.engine, self.presenter, self.registry, self.layout
        )
        self.interstitial_scene: InterstitialScene | None = None
        self.game_over_scene: GameOverScene | None = None

        self._engine_thread: threading.Thread | None = None
        self._engine_error: Exception | None = None

    def run(self) -> None:
        """Start the engine thread and enter the main Pygame event loop."""
        self.running = True

        # Start engine in background thread
        self._engine_thread = threading.Thread(
            target=self._run_engine, name="EngineThread", daemon=True
        )
        self._engine_thread.start()

        try:
            while self.running:
                dt = self.clock.tick(60) / 1000.0
                self._handle_events()
                self._update(dt)
                self._draw()
        finally:
            self._cleanup()

    def stop(self) -> None:
        self.running = False

    def _run_engine(self) -> None:
        try:
            self.engine.run(self.presenter)
        except Exception as exc:
            self._engine_error = exc

    def _handle_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.stop()
                return
            elif event.type == pygame.VIDEORESIZE:
                self.width = max(800, event.w)
                self.height = max(600, event.h)
                self.screen = pygame.display.set_mode(
                    (self.width, self.height), pygame.RESIZABLE
                )
                self.layout.rebuild(self.width, self.height)
                self.game_scene.resize(self.width, self.height)
                if self.game_over_scene:
                    self.game_over_scene.resize(self.width, self.height)
                continue

            # Route to top scene
            if self.interstitial_scene:
                self.interstitial_scene.handle_event(event)
            elif self.game_over_scene:
                self.game_over_scene.handle_event(event)
            else:
                self.game_scene.handle_event(event)

    def _update(self, dt: float) -> None:
        # Check if seat transition needed
        t_seat = self.presenter.transition_seat
        if t_seat and not self.interstitial_scene:
            try:
                name = self.engine.state.player(t_seat).name  # type: ignore[arg-type]
            except Exception:
                name = str(t_seat)
            self.interstitial_scene = InterstitialScene(
                name, self._continue_seat_transition
            )
        elif not t_seat:
            self.interstitial_scene = None

        # Check if game is over
        if self.engine.over and not self.game_over_scene:
            winner = self.engine.winner
            winner_name = None
            if winner:
                try:
                    winner_name = self.engine.state.player(winner).name
                except Exception:
                    winner_name = str(winner)
            self.game_over_scene = GameOverScene(winner_name, self.stop)

        self.game_scene.update(dt)

    def _continue_seat_transition(self) -> None:
        self.interstitial_scene = None
        self.presenter.acknowledge_transition()

    def _draw(self) -> None:
        self.game_scene.draw(self.screen)
        if self.interstitial_scene:
            self.interstitial_scene.draw(self.screen)
        elif self.game_over_scene:
            self.game_over_scene.draw(self.screen)
        pygame.display.flip()

    def _cleanup(self) -> None:
        self.presenter.close()
        clear_card_cache()
        clear_font_cache()
        pygame.quit()


__all__ = ["PygameApp"]
