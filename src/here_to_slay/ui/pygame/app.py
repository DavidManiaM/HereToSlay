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
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pygame

from here_to_slay.net import NetError, SessionClosed
from here_to_slay.ui import lexicon as L
from here_to_slay.ui.pygame import materials
from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.card_renderer import clear_card_cache
from here_to_slay.ui.pygame.cues import CueTable
from here_to_slay.ui.pygame.icons import draw_icon
from here_to_slay.ui.pygame.layout import MIN_H, MIN_W, LayoutManager
from here_to_slay.ui.pygame.menu import MODE_LOCAL, MenuChoice, MenuScene
from here_to_slay.ui.pygame.netplay import NetSession, open_session
from here_to_slay.ui.pygame.presenter import DEFAULT_AI_DELAY, PygamePresenter
from here_to_slay.ui.pygame.replay import ReplayTransport
from here_to_slay.ui.pygame.scenes import GameScene, SceneHooks
from here_to_slay.ui.pygame.sound import SoundBoard
from here_to_slay.ui.pygame.theme import C, clear_surface_caches
from here_to_slay.ui.settings import Settings

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
        settings: Settings | None = None,
        save_dir: Path | str = "hts_saves",
        replay: ReplayTransport | None = None,
        start_on_menu: bool = False,
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
        self.settings = settings if settings is not None else Settings()
        self.save_dir = Path(save_dir)
        #: Set when the window is watching a log instead of playing a game. It
        #: answers every seat, so there is no player to save for and no restart.
        self.replay = replay
        #: Open on the start screen rather than dealing immediately. A replay
        #: never does: it was launched to watch one specific log.
        self.start_on_menu = start_on_menu and replay is None

        self.running = False
        self.screen: pygame.Surface | None = None
        self.clock: pygame.time.Clock | None = None
        self._apply_ui_scale()
        self.layout = LayoutManager(self.width, self.height)
        self.sound = SoundBoard(enabled=sound, volume=self.settings.volume)
        # One table for the window, so a pack's `sounds.yaml` is read once
        # rather than per restart.
        self.cues = CueTable.for_registry(registry)
        self.cues.install(self.sound)

        self.engine: Engine | None = engine
        self.presenter: PygamePresenter | None = None
        self.scene: GameScene | None = None
        self._thread: threading.Thread | None = None
        self._engine_error: BaseException | None = None
        #: Queued restart: a setup, and optionally the engine to start it with
        #: (a loaded save arrives already replayed to the position it was at).
        self._restart: tuple[GameSetup, Engine | None] | None = None

        #: The start screen, when the window is showing one. While this is set
        #: there is no game: no engine, no presenter, no board. `menu` and
        #: `scene` are mutually exclusive, which is what keeps the frame loop
        #: from having to ask which mode the window is in more than once.
        self.menu: MenuScene | None = None
        self.net: NetSession | None = None
        #: Deferred like `_restart`, and for the same reason: the request comes
        #: from inside a menu button's own callback.
        self._pending_menu_start: MenuChoice | None = None
        self._pending_deal = False
        #: A joined client polls for the host's deal on the frame loop.
        self._await_host = False
        #: Set when the table ended the session out from under the engine, so
        #: the board can say why instead of simply going quiet.
        self._net_ended = ""
        self._net_seats: tuple[str, ...] = ()
        #: Whose answer the table is waiting for, for the board to show.
        self._waiting_on = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self) -> int:
        """Open the window and play until the user quits. Returns an exit code."""
        self._open_window()
        if self.start_on_menu:
            self.show_menu()
        else:
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
                self._settle_menu()
                if self._restart is not None:
                    (pending, engine), self._restart = self._restart, None
                    self._begin(pending, engine=engine)
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
        self._restart = (setup, None)

    # -- the start screen ---------------------------------------------------

    def show_menu(self) -> None:
        """Tear the game down (if any) and put the start screen up."""
        self._end_game()
        self._drop_session()
        self.menu = MenuScene(
            self.width,
            self.height,
            choice=self._menu_choice(),
            on_start=self._menu_start,
            on_quit=self.stop,
            on_cancel=self._menu_cancel,
            on_deal=self._menu_deal,
            subtitle=", ".join(self.registry.pack_ids),
        )
        pygame.display.set_caption(WINDOW_TITLE)

    def _menu_choice(self) -> MenuChoice:
        """Prefill the menu from however this window was started."""
        if self.menu is not None:
            return self.menu.choice
        return MenuChoice(
            mode=MODE_LOCAL,
            name=self.setup.names[0] if self.setup.names else "Jucător 1",
            players=len(self.setup.names) or 2,
            ai_seats=self.setup.ai_seats,
        )

    def _menu_start(self, choice: MenuChoice) -> None:
        """Deferred: the callback is running inside the button that raised it."""
        self._pending_menu_start = choice

    def _menu_deal(self) -> None:
        self._pending_deal = True

    def _menu_cancel(self) -> None:
        self._drop_session()
        if self.menu is not None:
            self.menu.lobby.active = False

    def _settle_menu(self) -> None:
        """Act on whatever the menu asked for, at the end of the frame."""
        if self._pending_menu_start is not None:
            choice, self._pending_menu_start = self._pending_menu_start, None
            self._act_on_choice(choice)
        if self._pending_deal:
            self._pending_deal = False
            self._deal_networked()

    def _act_on_choice(self, choice: MenuChoice) -> None:
        if choice.mode == MODE_LOCAL:
            self.menu = None
            self._begin(
                self.setup.resized(choice.players, choice.ai_seats, _fresh_seed()),
                names=self._local_names(choice),
            )
            return
        assert self.menu is not None
        try:
            self.net = open_session(
                choice,
                content_hash=self.registry.content_hash,
                packs=self.registry.pack_ids,
                seed=_fresh_seed(),
                max_turns=self.setup.max_turns,
                on_lobby=self._on_lobby,
                on_error=self._on_net_error,
            )
        except NetError as exc:
            self.menu.show_error(str(exc))
            return
        self.menu.enter_lobby(
            hosting=self.net.hosting, addresses=self.net.addresses
        )
        self.menu.update_lobby(list(self.net.names), self._waiting())
        if not self.net.hosting:
            # A client does not press start; the host does. Watching for it is
            # cheap enough to do on the frame loop.
            self._await_host = True

    def _local_names(self, choice: MenuChoice) -> tuple[str, ...]:
        """Seat names for a hot-seat game, with the typed name in seat one."""
        humans = max(1, choice.players - choice.ai_seats)
        names = [choice.name if i == 0 else f"Jucător {i + 1}" for i in range(humans)]
        names += [f"Bot {i + 1}" for i in range(choice.players - humans)]
        return tuple(names)

    def _waiting(self) -> int:
        if self.net is None:
            return 0
        if self.net.host is not None:
            return self.net.host.waiting_for
        return self.net.client.waiting_for if self.net.client else 0

    def _on_lobby(self, names: list[str], waiting: int) -> None:
        if self.menu is not None:
            self.menu.update_lobby(names, waiting)

    def _on_net_error(self, text: str) -> None:
        if self.menu is not None:
            self.menu.show_error(text)
        elif self.scene is not None:
            self.scene.toast.show(text, colour=(236, 88, 96))

    def _deal_networked(self) -> None:
        """The host pressed start. Close the lobby and deal on every machine."""
        if self.net is None or self.net.host is None or self.menu is None:
            return
        roster = self.net.host.start()
        self.net.names = tuple(seat.name for seat in roster)
        self.menu = None
        self._begin(
            replace(
                self.setup,
                names=self.net.names,
                seed=self.net.seed,
                max_turns=self.net.max_turns,
                ai_seats=self.net.ai_seats,
            )
        )

    def _join_dealt(self) -> None:
        """A client's host just dealt. Follow it into the same game."""
        if self.net is None or self.net.client is None:
            return
        invitation = self.net.client.invitation
        if invitation is None:
            return
        self.net.names = tuple(self.net.client.lobby_names) or self.net.names
        self.menu = None
        self._await_host = False
        self._begin(
            replace(
                self.setup,
                names=self.net.names,
                seed=invitation.seed,
                max_turns=invitation.max_turns,
                ai_seats=0,
            )
        )

    def _note_waiting(self, request: Any) -> None:
        """Say whose turn it is while this machine waits on the network.

        Called from the engine thread, once per question this player is not
        being asked. It only writes a string the frame loop reads, which is why
        it needs no lock: a board that briefly shows the previous name is a
        cosmetic lag, and the alternative is a window that freezes with no
        explanation while somebody in another room decides what to play.
        """
        seat = str(getattr(request, "requester", "") or "")
        self._waiting_on = self._seat_name(seat)

    def _seat_name(self, seat: str) -> str:
        order = list(self.engine.state.turn_order) if self.engine is not None else []
        if seat in order:
            index = order.index(seat)
            if index < len(self.setup.names):
                return self.setup.names[index]
        return seat or "cineva"

    def _drop_session(self) -> None:
        self._await_host = False
        if self.net is not None:
            self.net.close("the window closed the table")
            self.net = None

    # -- saves -------------------------------------------------------------

    def save_game(self) -> str:
        """Capture the running game to :attr:`save_dir`. Raises on refusal.

        Called from the frame loop while the engine thread is blocked on a
        request, which is exactly an ``Engine.savepoint``. If an AI seat happens
        to be deliberating the engine *is* mid-step, ``SaveGame.capture`` says
        so, and the board shows that rather than writing a position nobody was
        in — see ``GameScene.save_game``.
        """
        from here_to_slay.core.savegame import SaveError, SaveGame, autosave_name, save_path

        engine = self.engine
        if engine is None:
            raise SaveError("there is no game to save")
        game = SaveGame.capture(engine)
        path = game.save(save_path(
            self.save_dir, autosave_name(game.summary.players, game.summary.turn_number)
        ))
        return L.SAVED_TO.format(name=path.name)

    def list_saves(self) -> tuple[Any, ...]:
        from here_to_slay.core.savegame import list_saves

        return list_saves(self.save_dir)

    def load_game(self, game: Any) -> None:
        """Queue a restart from a save. Applied at the end of the frame.

        The replay happens *here*, on the frame loop, before the scene is torn
        down: restoring can fail (edited cards, a missing plugin), and a failure
        that has already destroyed the running game would be unforgivable.
        """
        try:
            engine = game.restore(self.registry)
        except Exception as exc:
            if self.scene is not None:
                self.scene.toast.show(
                    L.LOAD_FAILED.format(why=exc), colour=C.BAD, icon="close", duration=6.0,
                )
            return
        names = tuple(engine.state.player(pid).name for pid in engine.state.turn_order)
        setup = replace(
            self.setup,
            names=names,
            seed=engine.state.rng.seed,
            ai_seats=min(self.setup.ai_seats, max(0, len(names) - 1)),
        )
        self._restart = (setup, engine)

    # -- settings ----------------------------------------------------------

    def apply_settings(self, settings: Settings) -> None:
        """Adopt preferences: the window's half here, the board's in the scene."""
        self.settings = settings
        self.sound.enabled = settings.sound
        self.sound.set_volume(settings.volume)
        self.ai_delay = settings.ai_delay
        if self.presenter is not None:
            self.presenter.ai_delay = settings.ai_delay
        if abs(settings.ui_scale - self.ui_scale) > 1e-6:
            self.ui_scale = settings.ui_scale
            self._sync_size()
        if settings.fullscreen != self.fullscreen:
            self.toggle_fullscreen()
        if self.scene is not None:
            self.scene.apply_settings(settings)

    def save_settings(self, settings: Settings) -> None:
        """Persist what the settings screen ended on. Failure is not fatal."""
        self.apply_settings(settings)
        settings.save()

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
        if self.menu is not None:
            self.menu.resize(width, height)
        if self.scene is not None:
            self.scene.resize(width, height)

    # -- game --------------------------------------------------------------

    def _begin(
        self,
        setup: GameSetup,
        *,
        engine: Engine | None = None,
        names: Sequence[str] | None = None,
    ) -> None:
        """Tear down any running game and start one from ``setup``."""
        self._end_game()
        from here_to_slay.core.engine import Engine as _Engine

        if names is not None:
            setup = replace(setup, names=tuple(names))
        self.setup = setup
        self.engine = engine or _Engine.new(
            self.registry, list(setup.names), seed=setup.seed, max_turns=setup.max_turns,
        )
        order = list(self.engine.state.turn_order)
        if self.replay is not None:
            # A replay answers every seat from the log, which is precisely what
            # "no seat is human" already means to the presenter. No branch in
            # the scene, the tracker or the panels: the board cannot tell.
            human_seats: list[str] | None = []
            agent: DecisionSource | None = self.replay
        else:
            human_seats = order[: len(order) - setup.ai_seats] if setup.ai_seats else None
            agent = (
                self._make_agent(setup)
                if setup.ai_seats or self._agent_override
                else None
            )
        if self.net is not None:
            # Networked: this machine answers only the seats it owns, and the
            # rest arrive over the wire. `human_seats` is *my* seat alone, so
            # the board offers a menu on my turn and waits on everyone else's;
            # the agent still lives here because the host owns the AI seats.
            mine = self.net.local_seats()
            human_seats = [self.net.seat]
            agent = self._make_agent(setup) if self.net.ai_seats else None
            self._net_seats = mine
        self.presenter = PygamePresenter(
            self.engine, self.registry,
            human_seats=human_seats,
            agent=agent,
            # The transport paces itself; a second delay on top would fight it.
            ai_delay=0.0 if self.replay is not None else self.ai_delay,
        )
        self.scene = GameScene(
            self.engine, self.presenter, self.registry, self.layout,
            sound=self.sound,
            cues=self.cues,
            reveal_all=self.reveal_all,
            replay=self.replay,
            hooks=self._hooks(),
        )
        self.scene.apply_settings(self.settings)
        self._engine_error = None
        self._thread = threading.Thread(target=self._drive_engine, name="hts-engine", daemon=True)
        self._thread.start()
        pygame.display.set_caption(
            f"{WINDOW_TITLE}  \u2014  {len(setup.names)} players  \u00b7  seed {setup.seed}"
        )

    def _hooks(self) -> SceneHooks:
        """What this window lets the board ask for.

        A replay viewer gets neither *save* nor *new game*: there is no live
        game to write down and nothing to re-deal. The scene hides the rows it
        was not given rather than showing dead ones.
        """
        if self.replay is not None:
            return SceneHooks(
                quit=self.stop,
                toggle_fullscreen=self.toggle_fullscreen,
                settings=lambda: self.settings,
                save_settings=self.save_settings,
            )
        return SceneHooks(
            new_game=self.new_game,
            quit=self.stop,
            toggle_fullscreen=self.toggle_fullscreen,
            save_game=self.save_game,
            list_saves=self.list_saves,
            load_game=self.load_game,
            settings=lambda: self.settings,
            save_settings=self.save_settings,
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
        # In a networked game the presenter is still what asks *this* player;
        # the network source decides whether this player is the one being asked
        # and, either way, hands the engine the answer the table settled on.
        source: DecisionSource = presenter
        if self.net is not None:
            source = self.net.source(presenter, on_wait=self._note_waiting)
        try:
            engine.run(source)
        except SessionClosed as exc:
            self._net_ended = str(exc)
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
        if self.replay is not None:
            # The transport can be parked between decisions with the engine
            # thread asleep inside it; closing the presenter alone would leave
            # that thread waiting for a step that is never coming.
            self.replay.close()
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
            if self.menu is not None:
                self.menu.handle_event(event)
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
        if self.menu is not None:
            self.menu.update(dt)
            # A joined client sits in the lobby until the host deals. Polling a
            # flag once a frame is cheaper than another thread and cannot race
            # with the teardown a cancel would run.
            if self._await_host and self.net is not None and self.net.client is not None:
                if self.net.client.started:
                    self._join_dealt()
                else:
                    self.menu.update_lobby(
                        list(self.net.client.lobby_names), self.net.client.waiting_for
                    )
            return
        if self.scene is None or self.clock is None:
            return
        if self._net_ended:
            self.scene.toast.show(self._net_ended, colour=C.WARN, duration=8.0)
            self._net_ended = ""
        if self._waiting_on:
            # One toast per turn change, not per frame: `show` de-duplicates a
            # repeated message, so this stays quiet until the name actually
            # changes and the board has something new to say.
            self.scene.toast.show(
                L.WAITING_FOR.format(name=self._waiting_on), colour=C.INFO, duration=2.5
            )
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
        if self.menu is not None:
            self.menu.draw(self.screen)
        elif self.scene is not None:
            self.scene.draw(self.screen)
        else:
            self.screen.fill(C.VOID)
        pygame.display.flip()

    def _shutdown(self) -> None:
        self._end_game()
        self._drop_session()
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
    width: int | None = None,
    height: int | None = None,
    fullscreen: bool | None = None,
    reveal_all: bool = False,
    sound: bool | None = None,
    ui_scale: float | None = None,
    settings: Settings | None = None,
    start_on_menu: bool = False,
    **kwargs: Any,
) -> int:
    """One call for the CLI: build the setup, open the window, play.

    Stored preferences supply the window size, scale, sound and fullscreen; an
    explicit argument overrides one, because a flag typed just now is a more
    recent instruction than a file written last week. ``None`` means "the CLI
    did not say", which is why these defaults are not the literals.
    """
    prefs = settings if settings is not None else Settings.load()
    app = PygameApp(
        registry,
        GameSetup(
            names=tuple(names), seed=seed, max_turns=max_turns,
            ai_seats=max(0, min(ai_seats, len(names) - 1)),
        ),
        width=prefs.window_width if width is None else width,
        height=prefs.window_height if height is None else height,
        fullscreen=prefs.fullscreen if fullscreen is None else fullscreen,
        reveal_all=reveal_all,
        sound=prefs.sound if sound is None else sound,
        ui_scale=prefs.ui_scale if ui_scale is None else ui_scale,
        ai_delay=kwargs.pop("ai_delay", prefs.ai_delay),
        settings=prefs,
        start_on_menu=start_on_menu,
        **kwargs,
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
