"""The start screen: who you are, and which table you are sitting at.

Three things happen here and nothing else — pick a name, pick a mode, and (for
multiplayer) get a lobby up. It hands back a :class:`MenuChoice` and the app
does the rest; the menu never touches an `Engine`, a `ContentRegistry` or a
socket's game traffic.

Visually it is the board's own furniture reused: the same `Atmosphere` ground
and motes, the same glass panels, the same pill buttons and cyan accent, so
starting a game and playing one feel like the same program. The title is drawn
rather than typed into a label because it is the one piece of lettering in the
project that gets to be a logo.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import pygame

from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.atmosphere import Atmosphere
from here_to_slay.ui.pygame.icons import draw_icon
from here_to_slay.ui.pygame.theme import C
from here_to_slay.ui.pygame.widgets import Button, SegmentedControl, TextField, Toast

TITLE_MAIN = "here to vibe"
TITLE_TAIL = "(code)"

#: the three ways to start, in the order the tab bar shows them
MODE_LOCAL = "local"
MODE_HOST = "host"
MODE_JOIN = "join"
MODES = (MODE_LOCAL, MODE_HOST, MODE_JOIN)
MODE_LABELS = ("Hot-seat", "Găzduiește", "Intră în joc")

MIN_PLAYERS = 2
MAX_PLAYERS = 6


@dataclass(slots=True)
class MenuChoice:
    """What the player asked for. The app turns this into a game."""

    mode: str = MODE_LOCAL
    name: str = "Jucător 1"
    players: int = 2
    ai_seats: int = 1
    address: str = ""
    port: int = 0
    #: filled in by the app once a host is listening, so the lobby can show it
    advertised: tuple[str, ...] = ()

    @property
    def is_network(self) -> bool:
        return self.mode in (MODE_HOST, MODE_JOIN)


@dataclass(slots=True)
class LobbyState:
    """What the lobby panel is currently showing. Owned by the app, read here."""

    active: bool = False
    hosting: bool = False
    names: list[str] = field(default_factory=list)
    waiting: int = 0
    addresses: tuple[str, ...] = ()
    status: str = ""
    error: str = ""

    @property
    def ready(self) -> bool:
        return self.active and self.waiting <= 0


class MenuScene:
    """The start screen. Owns its widgets, not the game."""

    def __init__(
        self,
        width: int,
        height: int,
        *,
        choice: MenuChoice | None = None,
        on_start: Callable[[MenuChoice], None] | None = None,
        on_quit: Callable[[], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
        on_deal: Callable[[], None] | None = None,
        subtitle: str = "",
    ) -> None:
        self.width = width
        self.height = height
        self.choice = choice or MenuChoice()
        self.on_start = on_start
        self.on_quit = on_quit
        self.on_cancel = on_cancel
        self.on_deal = on_deal
        self.subtitle = subtitle
        self.lobby = LobbyState()

        self.atmosphere = Atmosphere()
        self.toast = Toast(pygame.Rect(0, 0, T.s(360), T.s(38)))
        self.time = 0.0
        self._backdrop = _MenuBackdrop()

        self.name_field = TextField(pygame.Rect(0, 0, 10, 10), placeholder="numele tău")
        self.name_field.value = self.choice.name
        self.address_field = TextField(
            pygame.Rect(0, 0, 10, 10), placeholder="192.168.1.5:57311"
        )
        self.address_field.value = self.choice.address
        self.mode_tabs = SegmentedControl(
            pygame.Rect(0, 0, 10, 10),
            list(MODE_LABELS),
            index=MODES.index(self.choice.mode),
            on_change=self._pick_mode,
        )
        self.buttons: list[Button] = []
        self._steppers: list[_Stepper] = []
        self.resize(width, height)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def resize(self, width: int, height: int) -> None:
        self.width, self.height = max(640, width), max(480, height)
        toast = pygame.Rect(0, 0, T.s(360), T.s(38))
        toast.center = (self.width // 2, self.height - T.s(52))
        self.toast.rect = toast
        self._rebuild()

    @property
    def card_rect(self) -> pygame.Rect:
        """The glass panel everything sits on.

        Its height follows what is actually in it. A fixed panel left a third of
        itself empty on the setup screens and still had to squeeze the lobby, so
        the two arrangements each get the room they need.
        """
        w = min(T.s(560), int(self.width * 0.62))
        h = self._content_height()
        h = min(h, int(self.height * 0.66))
        return pygame.Rect(0, 0, w, h).move(
            (self.width - w) // 2, int(self.height * 0.30)
        )

    def _content_height(self) -> int:
        """Header + body + button row, measured rather than guessed."""
        chrome = T.s(58) + T.s(50) + T.s(48) + T.s(38) + T.s(30) + T.s(20)
        if not self.lobby.active:
            return chrome + T.s(56)
        rows = max(1, len(self.lobby.names or [self.choice.name])) + max(
            0, self.lobby.waiting
        )
        body = T.s(26) + rows * T.s(32) + T.s(12)
        if self.lobby.hosting and self.lobby.addresses:
            body += T.s(62)
        return T.s(46) + body + T.s(38) + T.s(30) + T.s(20)

    def _rebuild(self) -> None:
        card = self.card_rect
        pad = T.s(26)
        row = T.s(38)
        inner = card.width - pad * 2
        y = card.top + T.s(58)

        self.mode_tabs.rect = pygame.Rect(card.left + pad, y, inner, T.s(32))
        y += T.s(50)
        self.name_field.rect = pygame.Rect(
            card.left + pad + T.s(96), y, inner - T.s(96), row - T.s(6)
        )
        self._name_row_y = y
        y += T.s(48)
        self._body_top = y

        half = (inner - T.s(16)) // 2
        self._steppers = [
            _Stepper(
                pygame.Rect(card.left + pad, y, half, row),
                "Jucători",
                MIN_PLAYERS,
                MAX_PLAYERS,
                lambda: self.choice.players,
                self._set_players,
            ),
            _Stepper(
                pygame.Rect(card.left + pad + half + T.s(16), y, half, row),
                "Boți",
                0,
                MAX_PLAYERS - 1,
                lambda: self.choice.ai_seats,
                self._set_ai,
            ),
        ]
        self.address_field.rect = pygame.Rect(
            card.left + pad + T.s(96), y, inner - T.s(96), row - T.s(6)
        )

        bw = (inner - T.s(16)) // 2
        by = card.bottom - T.s(30) - row
        self.buttons = [
            Button(
                pygame.Rect(card.left + pad, by, bw, row),
                "Ieși",
                self._quit,
                icon="close",
            ),
            Button(
                pygame.Rect(card.left + pad + bw + T.s(16), by, bw, row),
                self._primary_label(),
                self._primary,
                icon="play",
                primary=True,
                accent=C.GOLD,
            ),
        ]

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def mode(self) -> str:
        return MODES[self.mode_tabs.index]

    def _pick_mode(self, index: int) -> None:
        self.choice.mode = MODES[index]
        self.lobby = LobbyState()
        self._rebuild()

    def _set_players(self, value: int) -> None:
        self.choice.players = value
        # A table cannot be all robots: somebody has to hold the mouse here.
        self.choice.ai_seats = min(self.choice.ai_seats, value - 1)

    def _set_ai(self, value: int) -> None:
        self.choice.ai_seats = min(value, self.choice.players - 1)

    def _primary_label(self) -> str:
        if self.lobby.active:
            return "Începe jocul" if self.lobby.ready else "Se așteaptă…"
        return {
            MODE_LOCAL: "Joacă",
            MODE_HOST: "Deschide masa",
            MODE_JOIN: "Conectează-te",
        }[self.mode]

    def _commit(self) -> MenuChoice:
        self.choice.mode = self.mode
        self.choice.name = self.name_field.value.strip() or "Jucător"
        self.choice.address = self.address_field.value.strip()
        return self.choice

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _primary(self) -> None:
        if self.lobby.active:
            if self.lobby.ready and self.on_deal is not None:
                self.on_deal()
            elif not self.lobby.ready:
                self.toast.show("Mai lipsește cineva de la masă.", colour=C.WARN)
            return
        choice = self._commit()
        if choice.mode == MODE_JOIN and not choice.address:
            self.toast.show("Scrie adresa gazdei.", colour=C.BAD)
            self.address_field.focused = True
            return
        if self.on_start is not None:
            self.on_start(choice)

    def _quit(self) -> None:
        if self.lobby.active:
            self.lobby = LobbyState()
            if self.on_cancel is not None:
                self.on_cancel()
            self._rebuild()
            return
        if self.on_quit is not None:
            self.on_quit()

    def enter_lobby(self, *, hosting: bool, addresses: Sequence[str] = ()) -> None:
        self.lobby = LobbyState(
            active=True,
            hosting=hosting,
            addresses=tuple(addresses),
            status="Se așteaptă jucători…" if hosting else "Conectat. Se așteaptă gazda…",
        )
        self._rebuild()

    def update_lobby(self, names: Sequence[str], waiting: int) -> None:
        changed = len(names) != len(self.lobby.names) or waiting != self.lobby.waiting
        self.lobby.names = list(names)
        self.lobby.waiting = waiting
        if changed:
            # The panel is sized from its contents, so a new arrival moves the
            # buttons; the widgets have to be told where they live now.
            self._rebuild()
        if waiting <= 0:
            self.lobby.status = (
                "Masa e plină." if self.lobby.hosting else "Masa e plină. Gazda dă cărțile…"
            )

    def show_error(self, text: str) -> None:
        self.lobby.error = text
        self.toast.show(text, colour=C.BAD)

    # ------------------------------------------------------------------
    # Loop
    # ------------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self._quit()
                return True
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and not (
                self.name_field.focused or self.address_field.focused
            ):
                self._primary()
                return True
            if event.key == pygame.K_TAB:
                self._cycle_focus()
                return True
        if self.name_field.handle_event(event):
            return True
        if (
            self.mode == MODE_JOIN
            and not self.lobby.active
            and self.address_field.handle_event(event)
        ):
            return True
        if not self.lobby.active:
            if self.mode_tabs.handle_event(event):
                return True
            if self.mode in (MODE_LOCAL, MODE_HOST):
                for stepper in self._steppers:
                    if stepper.handle_event(event):
                        return True
        return any(button.handle_event(event) for button in self.buttons)

    def _cycle_focus(self) -> None:
        if self.mode == MODE_JOIN and not self.lobby.active:
            if self.name_field.focused:
                self.name_field.focused, self.address_field.focused = False, True
                return
            self.name_field.focused, self.address_field.focused = True, False
            return
        self.name_field.focused = not self.name_field.focused

    def update(self, dt: float) -> None:
        self.time += dt
        self.atmosphere.update(dt, (self.width, self.height))
        self.name_field.update(dt)
        self.address_field.update(dt)
        self.toast.update(dt)
        for button in self.buttons:
            button.update(dt)
        label = self._primary_label()
        primary = self.buttons[-1]
        primary.label = label
        primary.enabled = not (self.lobby.active and not self.lobby.ready)
        self.buttons[0].label = "Înapoi" if self.lobby.active else "Ieși"

    def draw(self, screen: pygame.Surface) -> None:
        self.atmosphere.draw(screen, _NoTable(), active_index=None)
        self._backdrop.draw(screen, self.time)
        self._draw_title(screen)
        card = self.card_rect
        T.glass(screen, card, radius=T.s(18), fill=C.GLASS_DEEP)
        if self.lobby.active:
            self._draw_lobby(screen, card)
        else:
            self._draw_setup(screen, card)
        for button in self.buttons:
            button.draw(screen)
        self.toast.draw(screen)
        self._draw_footer(screen)

    # -- pieces ------------------------------------------------------------

    def _draw_title(self, screen: pygame.Surface) -> None:
        """The one piece of lettering allowed to be a logo.

        Two weights and two colours, baseline-aligned, with a slow breath on the
        glow so the screen is alive without anything actually moving.
        """
        size = max(28, min(T.s(66), self.width // 16))
        main = T.display(size)
        tail = T.display(int(size * 0.62))
        w_main = main.size(TITLE_MAIN)[0]
        w_tail = tail.size(TITLE_TAIL)[0]
        gap = T.s(6)
        left = (self.width - (w_main + gap + w_tail)) // 2
        baseline = int(self.height * 0.17)

        glow = 0.5 + 0.5 * math.sin(self.time * 1.1)
        halo = T.alpha(C.GOLD, int(28 + 26 * glow))
        for offset in (T.s(3), T.s(6)):
            T.text(
                screen, TITLE_MAIN, (left + offset, baseline + offset), main, halo,
                anchor="midleft", shadow=None,
            )
        T.text(screen, TITLE_MAIN, (left, baseline), main, C.INK_BRIGHT, anchor="midleft")
        T.text(
            screen, TITLE_TAIL, (left + w_main + gap, baseline + T.s(4)), tail,
            T.lerp_colour(C.GOLD_DEEP, C.GOLD, glow)[:3], anchor="midleft",
        )
        if self.subtitle:
            T.text(
                screen, self.subtitle, (self.width // 2, baseline + T.s(34)),
                T.ui(12, italic=True), C.INK_FAINT, anchor="center", shadow=None,
            )

    def _draw_setup(self, screen: pygame.Surface, card: pygame.Rect) -> None:
        self.mode_tabs.draw(screen)
        self._label(screen, "Nume", self.name_field.rect)
        self.name_field.draw(screen)

        if self.mode == MODE_JOIN:
            self._label(screen, "Gazdă", self.address_field.rect)
            self.address_field.draw(screen)
            self._hint(
                screen,
                "Adresa apare pe ecranul celui care găzduiește.",
                self.address_field.rect.bottom + T.s(18),
                card,
            )
        else:
            for stepper in self._steppers:
                stepper.draw(screen)
            self._hint(screen, self._mode_hint(), self._body_top + T.s(56), card)

    def _mode_hint(self) -> str:
        humans = self.choice.players - self.choice.ai_seats
        if self.mode == MODE_LOCAL:
            if humans <= 1:
                return f"Tu contra {self.choice.ai_seats} boți, pe același ecran."
            return (
                f"{humans} oameni pe rând la aceeași tastatură, "
                f"plus {self.choice.ai_seats} boți."
            )
        remote = max(0, humans - 1)
        if remote == 0:
            return "Nimeni de conectat — mărește numărul de jucători."
        return f"Aștepți {remote} " + ("jucător" if remote == 1 else "jucători") + " prin rețea."

    def _draw_lobby(self, screen: pygame.Surface, card: pygame.Rect) -> None:
        pad = T.s(26)
        y = card.top + T.s(46)
        T.text(
            screen, "La masă", (card.left + pad, y), T.ui(13, bold=True), C.GOLD,
            anchor="midleft", shadow=None,
        )
        y += T.s(26)
        roster = self.lobby.names or [self.choice.name]
        for index, name in enumerate(roster):
            row = pygame.Rect(card.left + pad, y, card.width - pad * 2, T.s(28))
            T.round_rect(screen, row, T.alpha(C.GLASS_SOFT, 120), radius=T.s(8))
            draw_icon(
                screen, "hero", (row.left + T.s(14), row.centery), T.s(13),
                T.seat_colour(index),
            )
            T.text(
                screen, name, (row.left + T.s(30), row.centery), T.ui(12),
                C.INK, anchor="midleft", shadow=None,
                max_width=row.width - T.s(46),
            )
            y += T.s(32)
        for _ in range(max(0, self.lobby.waiting)):
            row = pygame.Rect(card.left + pad, y, card.width - pad * 2, T.s(28))
            T.round_rect(screen, row, T.alpha(C.GLASS_SOFT, 60), radius=T.s(8), width=1)
            dots = "." * (1 + int(self.time * 2) % 3)
            T.text(
                screen, f"se așteaptă{dots}", (row.left + T.s(30), row.centery),
                T.ui(12, italic=True), C.INK_FAINT, anchor="midleft", shadow=None,
            )
            y += T.s(32)

        if self.lobby.hosting and self.lobby.addresses:
            self._draw_addresses(screen, card)
        elif self.lobby.status:
            self._hint(screen, self.lobby.status, card.bottom - T.s(86), card)

    def _draw_addresses(self, screen: pygame.Surface, card: pygame.Rect) -> None:
        """The number somebody else has to type. Big enough to read out loud."""
        # Anchored to the button row rather than to the card, because the two
        # used to be computed from the same edge and overlapped by 14 pixels.
        buttons_top = self.buttons[0].rect.top if self.buttons else card.bottom - T.s(68)
        box = pygame.Rect(
            card.left + T.s(26), buttons_top - T.s(62),
            card.width - T.s(52), T.s(50),
        )
        T.round_rect(screen, box, T.alpha(C.GOLD, 26), radius=T.s(10))
        T.round_rect(screen, box, T.alpha(C.GOLD, 90), radius=T.s(10), width=1)
        T.text(
            screen, "spune-le adresa asta", (box.centerx, box.top + T.s(13)),
            T.ui(10, bold=True), C.INK_FAINT, anchor="center", shadow=None,
        )
        T.text(
            screen, self.lobby.addresses[0], (box.centerx, box.bottom - T.s(16)),
            T.mono(15, bold=True), C.GOLD_PALE, anchor="center", shadow=None,
            max_width=box.width - T.s(16),
        )

    def _label(self, screen: pygame.Surface, text: str, field: pygame.Rect) -> None:
        T.text(
            screen, text, (self.card_rect.left + T.s(26), field.centery),
            T.ui(12, bold=True), C.INK_DIM, anchor="midleft", shadow=None,
        )

    def _hint(self, screen: pygame.Surface, text: str, y: int, card: pygame.Rect) -> None:
        T.text(
            screen, text, (card.centerx, y), T.ui(11, italic=True), C.INK_FAINT,
            anchor="center", shadow=None, max_width=card.width - T.s(40),
        )

    def _draw_footer(self, screen: pygame.Surface) -> None:
        T.text(
            screen, "Enter pornește · Tab schimbă câmpul · Esc iese",
            (self.width // 2, self.height - T.s(18)), T.ui(10), T.alpha(C.INK_FAINT, 170),
            anchor="center", shadow=None,
        )


# ---------------------------------------------------------------------------
# Small parts
# ---------------------------------------------------------------------------


class _Stepper:
    """A labelled number with a minus and a plus. Two clicks, no typing."""

    def __init__(
        self,
        rect: pygame.Rect,
        label: str,
        low: int,
        high: int,
        get: Callable[[], int],
        set_value: Callable[[int], None],
    ) -> None:
        self.rect = pygame.Rect(rect)
        self.label = label
        self.low = low
        self.high = high
        self.get = get
        self.set_value = set_value
        self.hover = ""

    def _buttons(self) -> tuple[pygame.Rect, pygame.Rect]:
        size = self.rect.height - T.s(8)
        minus = pygame.Rect(0, 0, size, size)
        minus.midright = (self.rect.right - size - T.s(10), self.rect.centery)
        plus = pygame.Rect(0, 0, size, size)
        plus.midright = (self.rect.right - T.s(2), self.rect.centery)
        return minus, plus

    def handle_event(self, event: pygame.event.Event) -> bool:
        minus, plus = self._buttons()
        if event.type == pygame.MOUSEMOTION:
            self.hover = (
                "-" if minus.collidepoint(event.pos)
                else "+" if plus.collidepoint(event.pos)
                else ""
            )
            return False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if minus.collidepoint(event.pos):
                self.set_value(max(self.low, self.get() - 1))
                return True
            if plus.collidepoint(event.pos):
                self.set_value(min(self.high, self.get() + 1))
                return True
        return False

    def draw(self, screen: pygame.Surface) -> None:
        T.text(
            screen, self.label, (self.rect.left, self.rect.centery), T.ui(12, bold=True),
            C.INK_DIM, anchor="midleft", shadow=None,
        )
        minus, plus = self._buttons()
        value = self.get()
        T.text(
            screen, str(value), (minus.left - T.s(12), self.rect.centery),
            T.ui(16, bold=True), C.INK_BRIGHT, anchor="midright", shadow=None,
        )
        for rect, sign, live in (
            (minus, "-", value > self.low),
            (plus, "+", value < self.high),
        ):
            hot = self.hover == sign and live
            T.round_rect(
                screen, rect,
                T.alpha(C.GOLD, 60) if hot else T.alpha(C.GLASS_SOFT, 150),
                radius=rect.height // 2,
            )
            T.text(
                screen, sign, rect.center, T.ui(14, bold=True),
                C.INK_BRIGHT if live else C.INK_FAINT, anchor="center", shadow=None,
            )


class _MenuBackdrop:
    """A slow drift of oversized card silhouettes behind the panel.

    Purely decorative, and cheap: four rotated rounded rectangles at low alpha.
    It exists because an empty felt table under a menu reads as unfinished.
    """

    SHAPES = ((0.13, 0.30, -14), (0.86, 0.26, 11), (0.19, 0.78, 8), (0.82, 0.74, -9))

    def draw(self, screen: pygame.Surface, time: float) -> None:
        width, height = screen.get_size()
        card_w = max(60, int(min(width, height) * 0.14))
        card_h = int(card_w * 1.4)
        for index, (fx, fy, angle) in enumerate(self.SHAPES):
            bob = math.sin(time * 0.5 + index * 1.7) * card_h * 0.03
            surface = T.surface((card_w, card_h))
            T.round_rect(
                surface, pygame.Rect(0, 0, card_w, card_h),
                T.alpha(C.GOLD_PALE, 16), radius=int(card_w * 0.09),
            )
            T.round_rect(
                surface, pygame.Rect(0, 0, card_w, card_h),
                T.alpha(C.GOLD, 26), radius=int(card_w * 0.09), width=2,
            )
            spun = pygame.transform.rotate(surface, angle + math.sin(time * 0.3 + index) * 2.0)
            rect = spun.get_rect(center=(int(width * fx), int(height * fy + bob)))
            screen.blit(spun, rect)


class _NoTable:
    """What `Atmosphere.draw` needs when there is no board yet.

    It paints the ground and the motes from these two attributes alone, so the
    menu gets the game's own backdrop without inventing a fake layout.
    """

    table_rect = None
    seats: tuple[Any, ...] = ()


__all__ = [
    "MAX_PLAYERS",
    "MIN_PLAYERS",
    "MODES",
    "MODE_HOST",
    "MODE_JOIN",
    "MODE_LOCAL",
    "TITLE_MAIN",
    "TITLE_TAIL",
    "LobbyState",
    "MenuChoice",
    "MenuScene",
]
