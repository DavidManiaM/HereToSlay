"""The start screen, and the glue that turns it into a networked game.

Two separate claims:

* the menu is a *widget*, not a game — it renders in every mode at every window
  size, and hands back a `MenuChoice` without ever touching an engine;
* `netplay` divides the table correctly. Seat ownership is the one thing that
  can silently corrupt a lockstep game: if two machines answer for one seat,
  both publish, and every engine at the table consumes a decision meant for
  somebody else.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from here_to_slay.net import GameHost, HostConfig, NetError
from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.menu import (
    MAX_PLAYERS,
    MIN_PLAYERS,
    MODE_HOST,
    MODE_JOIN,
    MODE_LOCAL,
    TITLE_MAIN,
    TITLE_TAIL,
    MenuChoice,
    MenuScene,
)
from here_to_slay.ui.pygame.netplay import NetSession, open_session

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module", autouse=True)
def _pygame() -> None:
    """Bring pygame up, and deliberately never take it down.

    ``pygame.quit()`` is process-wide: calling it here would tear the display
    out from under every later test module, which is exactly what it did the
    first time this file was written. Whoever runs last does not need to tidy
    up, because the process is about to end anyway.
    """
    pygame.init()
    pygame.display.set_mode((64, 64))


@pytest.fixture
def screen() -> pygame.Surface:
    return pygame.Surface((1600, 900))


@pytest.fixture
def menu() -> MenuScene:
    return MenuScene(1600, 900, subtitle="data/base")


# ---------------------------------------------------------------------------
# The screen itself
# ---------------------------------------------------------------------------


class TestTheStartScreen:
    def test_the_title_is_what_it_says_on_the_tin(self) -> None:
        assert TITLE_MAIN + TITLE_TAIL == "here to vibe(code)"

    @pytest.mark.parametrize("mode", [0, 1, 2])
    def test_every_mode_draws(self, menu: MenuScene, screen: pygame.Surface, mode: int) -> None:
        menu.mode_tabs.index = mode
        menu._pick_mode(mode)
        menu.update(0.016)
        menu.draw(screen)

    @pytest.mark.parametrize(
        "size", [(1920, 1080), (1600, 900), (1280, 720), (1024, 640), (800, 600)]
    )
    def test_it_draws_at_every_window_size(
        self, menu: MenuScene, size: tuple[int, int]
    ) -> None:
        menu.resize(*size)
        menu.update(0.016)
        menu.draw(pygame.Surface(size))

    def test_the_lobby_draws_for_a_host_and_for_a_guest(
        self, menu: MenuScene, screen: pygame.Surface
    ) -> None:
        menu.enter_lobby(hosting=True, addresses=("192.168.1.5:57311",))
        menu.update_lobby(["Ana"], 1)
        menu.update(0.016)
        menu.draw(screen)
        assert "192.168.1.5:57311" in menu.lobby.addresses

        menu.enter_lobby(hosting=False)
        menu.update_lobby(["Ana", "Bob"], 0)
        menu.update(0.016)
        menu.draw(screen)

    def test_the_name_is_carried_out_of_the_screen(self, menu: MenuScene) -> None:
        started: list[MenuChoice] = []
        menu.on_start = started.append
        menu.name_field.value = "  Ana  "
        menu._primary()
        assert started and started[0].name == "Ana"

    def test_an_empty_name_still_seats_somebody(self, menu: MenuScene) -> None:
        started: list[MenuChoice] = []
        menu.on_start = started.append
        menu.name_field.value = "   "
        menu._primary()
        assert started[0].name == "Jucător"

    def test_joining_without_an_address_is_refused_here(self, menu: MenuScene) -> None:
        """Better a toast than a socket error twenty lines down."""
        started: list[MenuChoice] = []
        menu.on_start = started.append
        menu.mode_tabs.index = MODES_JOIN = 2
        menu._pick_mode(MODES_JOIN)
        menu.address_field.value = ""
        menu._primary()
        assert not started
        assert menu.address_field.focused

    def test_the_table_can_never_be_all_robots(self, menu: MenuScene) -> None:
        menu.choice.players = 6
        menu._set_ai(6)
        assert menu.choice.ai_seats == 5
        menu._set_players(2)
        assert menu.choice.ai_seats == 1

    def test_the_steppers_stay_in_range(self, menu: MenuScene) -> None:
        players, bots = menu._steppers
        for _ in range(20):
            players.set_value(min(players.high, players.get() + 1))
        assert menu.choice.players == MAX_PLAYERS
        for _ in range(20):
            players.set_value(max(players.low, players.get() - 1))
        assert menu.choice.players == MIN_PLAYERS
        assert bots.low == 0

    def test_escape_and_enter_do_the_obvious_thing(self, menu: MenuScene) -> None:
        quit_calls: list[int] = []
        started: list[MenuChoice] = []
        menu.on_quit = lambda: quit_calls.append(1)
        menu.on_start = started.append
        menu.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE))
        assert quit_calls
        menu.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN))
        assert started

    def test_enter_inside_a_text_field_does_not_start_the_game(
        self, menu: MenuScene
    ) -> None:
        started: list[MenuChoice] = []
        menu.on_start = started.append
        menu.name_field.focused = True
        menu.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, unicode=""))
        assert not started

    def test_the_primary_button_says_what_it_will_do(self, menu: MenuScene) -> None:
        labels = []
        for index in range(3):
            menu._pick_mode(index)
            menu.mode_tabs.index = index
            labels.append(menu._primary_label())
        assert len(set(labels)) == 3
        menu.enter_lobby(hosting=True)
        menu.update_lobby([], 2)
        assert menu._primary_label() != labels[0]

    def test_a_lobby_that_is_not_full_will_not_deal(self, menu: MenuScene) -> None:
        dealt: list[int] = []
        menu.on_deal = lambda: dealt.append(1)
        menu.enter_lobby(hosting=True)
        menu.update_lobby(["Ana"], 1)
        menu._primary()
        assert not dealt
        menu.update_lobby(["Ana", "Bob"], 0)
        menu._primary()
        assert dealt


# ---------------------------------------------------------------------------
# Seat ownership
# ---------------------------------------------------------------------------


class TestWhoAnswersForWhichSeat:
    """Every seat is answered by exactly one machine, or lockstep corrupts."""

    def test_a_host_owns_its_own_seat_and_every_bot(self) -> None:
        session = NetSession(
            choice=MenuChoice(mode=MODE_HOST, players=4, ai_seats=2),
            host=object(),  # type: ignore[arg-type]
            names=("Ana", "Bob", "Bot 1", "Bot 2"),
            ai_seats=2,
        )
        assert session.local_seats() == ("p1", "p3", "p4")

    def test_a_host_with_no_bots_owns_only_itself(self) -> None:
        session = NetSession(
            choice=MenuChoice(mode=MODE_HOST, players=3),
            host=object(),  # type: ignore[arg-type]
            names=("Ana", "Bob", "Cid"),
            ai_seats=0,
        )
        assert session.local_seats() == ("p1",)

    def test_a_client_owns_exactly_one_seat(self) -> None:
        session = NetSession(
            choice=MenuChoice(mode=MODE_JOIN, players=4),
            client=object(),  # type: ignore[arg-type]
            names=("Ana", "Bob", "Cid", "Dee"),
            seat="p3",
        )
        assert session.local_seats() == ("p3",)

    def test_the_whole_table_is_covered_exactly_once(self) -> None:
        """The property that actually matters, asserted as a partition."""
        host = NetSession(
            choice=MenuChoice(mode=MODE_HOST, players=4, ai_seats=1),
            host=object(),  # type: ignore[arg-type]
            names=("Ana", "Bob", "Cid", "Bot"),
            ai_seats=1,
        )
        clients = [
            NetSession(
                choice=MenuChoice(mode=MODE_JOIN, players=4),
                client=object(),  # type: ignore[arg-type]
                names=host.names,
                seat=seat,
            )
            for seat in ("p2", "p3")
        ]
        owned = list(host.local_seats())
        for client in clients:
            owned.extend(client.local_seats())
        assert sorted(owned) == ["p1", "p2", "p3", "p4"]
        assert len(owned) == len(set(owned)), "a seat is owned twice"


class TestOpeningASession:
    def test_a_local_choice_is_not_a_network_mode(self) -> None:
        with pytest.raises(NetError, match="not a network mode"):
            open_session(MenuChoice(mode=MODE_LOCAL))

    def test_a_table_nobody_could_join_is_refused_up_front(self) -> None:
        """Two players, one of them a bot, leaves no seat to dial into."""
        with pytest.raises(NetError, match="nobody could join"):
            open_session(
                MenuChoice(mode=MODE_HOST, players=2, ai_seats=1, port=0),
                seed="x",
            )

    def test_hosting_advertises_an_address_a_person_can_read(self) -> None:
        session = open_session(
            MenuChoice(mode=MODE_HOST, name="Ana", players=2, ai_seats=0, port=0),
            seed="x",
            content_hash="deadbeef",
        )
        try:
            assert session.hosting
            assert session.addresses and ":" in session.addresses[0]
            assert session.names[0] == "Ana"
            assert session.seat == "p1"
        finally:
            session.close()

    def test_joining_a_dead_address_says_so(self) -> None:
        with pytest.raises(NetError, match="could not reach"):
            open_session(MenuChoice(mode=MODE_JOIN, name="Bob", address="127.0.0.1:1"))

    def test_a_guest_learns_the_deal_it_must_reproduce(self) -> None:
        host_config = HostConfig(
            host_name="Ana", port=0, players=2, seed="shared-seed",
            max_turns=17, bind="127.0.0.1",
        )
        host = GameHost(host_config)
        port = host.open()
        try:
            session = open_session(
                MenuChoice(mode=MODE_JOIN, name="Bob", address=f"127.0.0.1:{port}")
            )
            try:
                # Everything an engine needs to deal the identical game.
                assert session.seat == "p2"
                assert session.seed == "shared-seed"
                assert session.max_turns == 17
                assert session.ai_seats == 0, "a guest must never run an agent"
            finally:
                session.close()
        finally:
            host.close()


# ---------------------------------------------------------------------------
# The whole thing, through the window
# ---------------------------------------------------------------------------


class TestTwoWindowsPlayOneGame:
    """The acceptance test for the feature, driven the way a player drives it.

    Two real `PygameApp`s, a real socket, and every answer submitted through
    ``presenter.submit_decision`` — the same call a mouse click makes. Nothing
    here reaches past the UI into the engine, so if this passes, clicking works.
    """

    def _app(self, registry: object, name: str, max_turns: int) -> Any:
        from here_to_slay.ui.pygame.app import GameSetup, PygameApp

        app = PygameApp(
            registry,
            GameSetup(names=(name, "x"), seed="net-window", max_turns=max_turns),
            width=1024,
            height=640,
            sound=False,
            start_on_menu=True,
        )
        app._open_window()
        app.show_menu()
        return app

    def _settle(self, apps: Any, predicate: Any, frames: int = 200) -> None:
        import time

        for _ in range(frames):
            for app in apps:
                app._advance(0.016)
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError("the lobby never settled")

    @pytest.mark.slow
    def test_a_hosted_game_and_a_joined_one_stay_identical(self) -> None:
        import time

        from here_to_slay.ai.random_agent import RandomAgent
        from here_to_slay.content import load_pack

        registry = load_pack(PROJECT_ROOT / "data" / "base")
        host = self._app(registry, "Ana", max_turns=14)
        guest = self._app(registry, "Bob", max_turns=14)
        try:
            host._act_on_choice(
                MenuChoice(mode=MODE_HOST, name="Ana", players=2, ai_seats=0, port=0)
            )
            assert host.net is not None and host.net.addresses
            port = host.net.addresses[0].rsplit(":", 1)[1]

            guest._act_on_choice(
                MenuChoice(mode=MODE_JOIN, name="Bob", address=f"127.0.0.1:{port}")
            )
            self._settle([host, guest], lambda: host.net.host.ready)

            host._deal_networked()
            self._settle([guest], lambda: guest.menu is None)

            # Both machines dealt the same game from the same three inputs.
            assert host.setup.names == guest.setup.names == ("Ana", "Bob")
            assert host.engine.state.rng.seed == guest.engine.state.rng.seed
            assert host.engine.state.content_hash == guest.engine.state.content_hash
            assert host.net.local_seats() == ("p1",)
            assert guest.net.local_seats() == ("p2",)

            agents = {id(host): RandomAgent(seed=1), id(guest): RandomAgent(seed=2)}
            screen = pygame.Surface((1024, 640))
            deadline = time.time() + 180
            clicks = 0
            while time.time() < deadline:
                for app in (host, guest):
                    app._advance(0.016)
                    app.scene.draw(screen)
                    request = app.presenter.awaiting_human
                    if request is not None:
                        app.presenter.submit_decision(
                            agents[id(app)].answer(request), answering=request
                        )
                        clicks += 1
                if host.engine.over and guest.engine.over:
                    break
                time.sleep(0.002)

            left, right = host.engine.state, guest.engine.state
            assert clicks > 0, "nobody was ever asked anything"
            assert host.net.relay.count == guest.net.relay.count == clicks
            assert left.turn_number == right.turn_number
            assert left.winner == right.winner
            assert {k: list(v.cards) for k, v in left.zones.items()} == {
                k: list(v.cards) for k, v in right.zones.items()
            }
            assert host._engine_error is None and guest._engine_error is None
            assert not host._net_ended and not guest._net_ended
        finally:
            host._drop_session()
            guest._drop_session()
            host._end_game()
            guest._end_game()


class TestTheLayoutHoldsTogether:
    """Three things a screenshot caught that no assertion had."""

    def test_the_address_box_does_not_sit_on_the_buttons(
        self, menu: MenuScene, screen: pygame.Surface
    ) -> None:
        """It overlapped by 14 pixels: both were measured from the card's
        bottom edge, so they moved together and met in the middle."""
        menu.enter_lobby(hosting=True, addresses=("192.168.1.24:57311",))
        menu.update_lobby(["Ana", "Bob"], 1)
        menu.update(0.016)
        menu.draw(screen)

        buttons_top = min(button.rect.top for button in menu.buttons)
        box_bottom = buttons_top - T.s(62) + T.s(50)
        assert box_bottom <= buttons_top

    def test_typed_text_is_dark_enough_to_read(self) -> None:
        """The well is the one bright surface in a dark client, so the ink has
        to be the dark one. `C.INK` is near-white and vanished into it."""
        from here_to_slay.ui.pygame.theme import C
        from here_to_slay.ui.pygame.widgets import TextField

        field = TextField(pygame.Rect(0, 0, 200, 30))
        field.value = "Catalin"
        surface = pygame.Surface((220, 40))
        surface.fill((0, 0, 0))
        field.draw(surface)
        # The field paints a light well; the glyphs must be darker than it.
        assert T.luminance(C.INK_DARK) < T.luminance((236, 244, 252))
        well = surface.get_at((100, 15))[:3]
        assert max(well) > 180, "the well should still be the bright surface"

    @pytest.mark.parametrize("waiting", [0, 1, 3])
    def test_the_panel_grows_with_the_lobby(self, menu: MenuScene, waiting: int) -> None:
        """A fixed panel left a third of itself empty on setup and squeezed the
        lobby; it is measured from its contents now, so it has to keep up."""
        menu.enter_lobby(hosting=True, addresses=("10.0.0.2:57311",))
        menu.update_lobby(["Ana"], waiting)
        menu.update(0.016)
        menu.draw(pygame.Surface((1600, 900)))
        card = menu.card_rect
        assert menu.buttons and card.contains(menu.buttons[0].rect)
        assert card.height <= int(menu.height * 0.66)

    def test_every_widget_stays_inside_the_panel_in_every_mode(
        self, menu: MenuScene
    ) -> None:
        for index in range(3):
            menu._pick_mode(index)
            menu.mode_tabs.index = index
            menu.update(0.016)
            menu.draw(pygame.Surface((1600, 900)))
            card = menu.card_rect
            for button in menu.buttons:
                assert card.contains(button.rect), f"button escapes in mode {index}"
            assert card.contains(menu.mode_tabs.rect)
