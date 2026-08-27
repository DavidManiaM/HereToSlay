"""The developer console: Ctrl+Shift+D.

Four tabs — Cards, FX, Game, Debug — over one honest constraint: **the console
cannot mutate a live game.** ``ui/`` may read a ``GameView`` and submit
``Decision`` objects, and that is all; reaching into ``GameState`` from a debug
panel would put the one rule this codebase is built on at the mercy of a button.

So each request is routed to whoever legitimately can do it, through
:class:`DevHost`:

* *Spawn any card* is a **presentation** spawn — the card flies in, lands in a
  sandbox tray, and can be inspected at full size. Useful for checking art,
  wording and layout for all 200-odd cards without dealing them.
* *Any number of players* starts a **new game** through the host, which owns the
  engine and is allowed to build one.
* *Any animation or sound* is pure presentation and runs directly.
* *Step / pause* gates the presenter's answers rather than the engine's clock,
  so a paused engine is simply an engine waiting for input.

The console therefore tells the truth about what it changed, which is the only
way a debug tool stays trustworthy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import pygame

from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.card_renderer import card_facts
from here_to_slay.ui.pygame.icons import card_icon_name, draw_icon
from here_to_slay.ui.pygame.overlays import Overlay
from here_to_slay.ui.pygame.theme import C, M
from here_to_slay.ui.pygame.widgets import (
    Button,
    Chip,
    IconButton,
    ScrollView,
    SegmentedControl,
    TextField,
)

HOTKEY_HINT = "Ctrl+Shift+D"

#: Kind order for the card browser — roughly how often you want to look at them.
KIND_ORDER = ("hero", "monster", "item", "magic", "modifier", "challenge", "party_leader")

#: Toggles the console offers. ``flag -> (label, hint)``.
FLAGS: dict[str, tuple[str, str]] = {
    "reveal_all": ("Reveal all hands", "show every seat's cards"),
    "animations": ("Animations", "cosmetic effects on or off"),
    "shake": ("Screen shake", "the table lurch on a slay"),
    "sound": ("Sound", "procedural cues"),
    "autoplay": ("Autoplay all seats", "let the agent answer for everyone"),
    "layout_debug": ("Layout overlay", "outline every named region"),
    "fps": ("Frame counter", "corner readout"),
    "force_cpu_materials": ("CPU materials", "skip ModernGL foil/glow"),
    "reaction_timer": ("Reaction auto-pass", "10s countdown on reaction windows"),
}


@runtime_checkable
class DevHost(Protocol):
    """What the console needs from whoever owns the engine and the board.

    Implemented by ``scenes.GameScene``. Keeping it a protocol means the console
    has no idea a scene exists, and a test can drive it with a stub.
    """

    def dev_cards(self) -> Sequence[Any]: ...
    def dev_spawn_card(self, card_def: Any) -> None: ...
    def dev_inspect_card(self, card_def: Any) -> None: ...
    def dev_clear_spawned(self) -> None: ...

    def dev_fx_names(self) -> Sequence[str]: ...
    def dev_play_fx(self, name: str) -> None: ...
    def dev_sound_names(self) -> Sequence[str]: ...
    def dev_play_sound(self, name: str) -> None: ...

    def dev_new_game(self, *, players: int, ai_seats: int, seed: int | None) -> None: ...
    def dev_flags(self) -> Mapping[str, bool]: ...
    def dev_toggle(self, flag: str) -> bool: ...

    def dev_paused(self) -> bool: ...
    def dev_toggle_pause(self) -> bool: ...
    def dev_step(self) -> None: ...
    def dev_let_ai_decide(self) -> None: ...

    def dev_stats(self) -> Sequence[tuple[str, str]]: ...
    def dev_layout_rects(self) -> Mapping[str, tuple[int, int, int, int]]: ...


# ---------------------------------------------------------------------------
# Small controls the console needs and nothing else does
# ---------------------------------------------------------------------------


class Stepper:
    """``- value +`` on one row. Player counts, seat counts, that sort of thing."""

    def __init__(
        self,
        rect: pygame.Rect,
        label: str,
        value: int,
        *,
        low: int = 0,
        high: int = 9,
        suffix: str = "",
    ) -> None:
        self.rect = pygame.Rect(rect)
        self.label = label
        self.value = value
        self.low = low
        self.high = high
        self.suffix = suffix
        size = min(26, rect.height - 6)
        self.minus = IconButton(
            pygame.Rect(rect.right - size * 2 - 46, rect.centery - size // 2, size, size),
            "minus", lambda: self.nudge(-1), accent=C.BLOOD,
        )
        self.plus = IconButton(
            pygame.Rect(rect.right - size, rect.centery - size // 2, size, size),
            "plus", lambda: self.nudge(1), accent=C.GOOD,
        )

    def nudge(self, delta: int) -> None:
        self.value = max(self.low, min(self.high, self.value + delta))

    def handle_event(self, event: pygame.event.Event) -> bool:
        return bool(self.minus.handle_event(event) or self.plus.handle_event(event))

    def update(self, dt: float) -> None:
        self.minus.enabled = self.value > self.low
        self.plus.enabled = self.value < self.high
        self.minus.update(dt)
        self.plus.update(dt)

    def draw(self, screen: pygame.Surface) -> None:
        T.text(screen, self.label, (self.rect.left, self.rect.centery), T.ui(12), C.INK,
               anchor="midleft", shadow=None)
        reading = f"{self.value}{self.suffix}"
        T.text(screen, reading, (self.plus.rect.left - 30, self.rect.centery),
               T.ui(15, bold=True), C.GOLD, anchor="midright", shadow=None)
        self.minus.draw(screen)
        self.plus.draw(screen)


class Switch:
    """A labelled on/off row with a sliding knob."""

    def __init__(self, rect: pygame.Rect, label: str, hint: str, on: bool) -> None:
        self.rect = pygame.Rect(rect)
        self.label = label
        self.hint = hint
        self.on = on
        self.hovered = False
        self._knob = 1.0 if on else 0.0

    @property
    def track(self) -> pygame.Rect:
        return pygame.Rect(self.rect.right - 46, self.rect.centery - 11, 42, 22)

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEMOTION:
            self.hovered = self.rect.collidepoint(event.pos)
        elif (
            event.type == pygame.MOUSEBUTTONDOWN
            and event.button == 1
            and self.rect.collidepoint(event.pos)
        ):
            return True
        return False

    def update(self, dt: float, on: bool) -> None:
        self.on = on
        target = 1.0 if on else 0.0
        self._knob += (target - self._knob) * min(1.0, dt * 14)

    def draw(self, screen: pygame.Surface) -> None:
        if self.hovered:
            T.round_rect(screen, self.rect.inflate(12, 4), (255, 255, 255, 14), radius=8)
        T.text(screen, self.label, (self.rect.left, self.rect.centery - 6), T.ui(12),
               C.INK if self.on else C.INK_DIM, anchor="midleft", shadow=None)
        T.text(screen, self.hint, (self.rect.left, self.rect.centery + 9), T.ui(9),
               C.INK_FAINT, anchor="midleft", shadow=None)
        track = self.track
        colour = T.mix((52, 48, 82), C.GOOD, self._knob)
        T.round_rect(screen, track, colour, radius=11)
        T.round_rect(screen, track, T.alpha(C.INK_BRIGHT, 40), radius=11, width=1)
        knob_x = int(track.left + 11 + self._knob * (track.width - 22))
        pygame.draw.circle(screen, C.INK_BRIGHT, (knob_x, track.centery), 8)


# ---------------------------------------------------------------------------
# Console
# ---------------------------------------------------------------------------


@dataclass
class _CardRow:
    card_def: Any
    label: str
    kind: str
    accent: tuple[int, int, int]
    icon: str
    haystack: str


class DevConsole(Overlay):
    """Tabbed debug panel. Wide, scrollable, and honest about its reach."""

    title = "Developer console"
    icon = "flask"
    dim = 120

    TABS = ("Cards", "FX", "Game", "Debug")

    def __init__(self, layout: Any, host: DevHost) -> None:
        width = min(940, int(layout.width * 0.78))
        height = min(700, int(layout.height * 0.84))
        super().__init__(pygame.Rect(
            (layout.width - width) // 2, (layout.height - height) // 2, width, height
        ))
        self.host = host
        self.subtitle = f"{HOTKEY_HINT} \u00b7 presentation only \u2014 it cannot edit a live game"
        self.index = 0
        self.notice = ""
        self.notice_age = 99.0

        body = self.body_rect
        self.tabs = SegmentedControl(
            pygame.Rect(body.left, body.top, min(body.width, 112 * len(self.TABS)), 30),
            list(self.TABS), on_change=self._choose_tab,
        )
        self.pane = pygame.Rect(
            body.left, body.top + 42, body.width, body.height - 42
        )

        self._build_cards()
        self._build_fx()
        self._build_game()
        self._build_debug()

    # ------------------------------------------------------------------
    # Tab construction
    # ------------------------------------------------------------------

    def _build_cards(self) -> None:
        rows: list[_CardRow] = []
        for card in self.host.dev_cards():
            kind = str(getattr(card, "kind", "") or "")
            facts = card_facts(card)
            rows.append(_CardRow(
                card_def=card,
                label=str(getattr(card, "name", "") or getattr(card, "id", "?")),
                kind=kind,
                accent=facts.accent,
                icon=card_icon_name(kind, facts.card_class),
                haystack=" ".join((
                    str(getattr(card, "name", "")),
                    str(getattr(card, "id", "")),
                    kind,
                    str(getattr(card, "card_class", "") or ""),
                )).lower(),
            ))
        rows.sort(key=lambda r: (
            KIND_ORDER.index(r.kind) if r.kind in KIND_ORDER else 99, r.label
        ))
        self.card_rows = rows
        self.filtered = list(rows)
        self.kind_filter: str | None = None

        pane = self.pane
        self.search = TextField(
            pygame.Rect(pane.left, pane.top, 260, 30),
            placeholder="search name, id or class\u2026", on_change=lambda _v: self._refilter(),
        )
        self.kind_chips: list[tuple[str | None, Chip]] = []
        x = self.search.rect.right + 12
        for kind in (None, *KIND_ORDER):
            label = "All" if kind is None else kind.replace("_", " ").title()
            icon = None if kind is None else card_icon_name(kind, None)
            width = Chip.width_for(label, icon=icon, height=22)
            if x + width > pane.right:
                break
            colour = C.GOLD if kind is None else T.KIND_COLOURS.get(kind, C.INK_DIM)
            chip = Chip(pygame.Rect(x, pane.top + 4, width, 22), label, colour=colour, icon=icon)
            self.kind_chips.append((kind, chip))
            x += width + 5
        self.cards_view = ScrollView(
            pygame.Rect(pane.left, pane.top + 40, pane.width, pane.height - 78)
        )
        self.card_row_h = 30
        self.clear_button = Button(
            pygame.Rect(pane.right - 150, pane.bottom - 34, 150, 30),
            "Clear tray", self._clear_tray, icon="close",
        )

    def _build_fx(self) -> None:
        self.fx_buttons: list[Button] = []
        self.sound_buttons: list[Button] = []
        pane = self.pane
        col_w = (pane.width - 24) // 3
        for i, name in enumerate(self.host.dev_fx_names()):
            rect = pygame.Rect(
                pane.left + (i % 3) * (col_w + 12), pane.top + 26 + (i // 3) * 40,
                col_w, 32,
            )
            self.fx_buttons.append(Button(
                rect, name.replace("_", " ").title(), self._make_fx(name),
                icon="bolt", align="left",
            ))
        top = (
            self.fx_buttons[-1].rect.bottom + 34 if self.fx_buttons else pane.top + 40
        )
        self.sound_top = top
        for i, name in enumerate(self.host.dev_sound_names()):
            rect = pygame.Rect(
                pane.left + (i % 4) * ((pane.width - 36) // 4 + 12),
                top + 26 + (i // 4) * 34,
                (pane.width - 36) // 4, 28,
            )
            self.sound_buttons.append(Button(
                rect, name.replace("_", " ").title(), self._make_sound(name),
                icon="bard", align="left",
                bg_colour=(40, 38, 64), hover_colour=(62, 58, 96),
            ))
        self.fx_view = ScrollView(pygame.Rect(pane))
        self.fx_content_h = (
            (self.sound_buttons[-1].rect.bottom if self.sound_buttons else top) - pane.top + 20
        )

    def _build_game(self) -> None:
        pane = self.pane
        y = pane.top + 26
        self.players = Stepper(
            pygame.Rect(pane.left, y, pane.width // 2 - 20, 32), "Players", 4, low=2, high=6
        )
        self.ai_seats = Stepper(
            pygame.Rect(pane.left, y + 40, pane.width // 2 - 20, 32),
            "Agent-controlled seats", 3, low=0, high=5,
        )
        self.seed = TextField(
            pygame.Rect(pane.left + 128, y + 84, 140, 28), placeholder="random",
        )
        self.new_game_button = Button(
            pygame.Rect(pane.left, y + 124, pane.width // 2 - 20, 38),
            "Start new game", self._start_new_game, primary=True, icon="dice",
            subtitle="restarts \u2014 the current game is lost",
        )

        self.engine_buttons = [
            Button(pygame.Rect(pane.left, y + 216, 150, 32), "Pause engine",
                   self._toggle_pause, icon="gear"),
            Button(pygame.Rect(pane.left + 162, y + 216, 120, 32), "Step",
                   self._step, icon="chevron_right"),
            Button(pygame.Rect(pane.left + 294, y + 216, 190, 32), "Let the agent decide",
                   self._let_ai_decide, icon="flask"),
        ]

        self.switches: list[tuple[str, Switch]] = []
        sx = pane.left + pane.width // 2 + 8
        for i, (flag, (label, hint)) in enumerate(FLAGS.items()):
            self.switches.append((flag, Switch(
                pygame.Rect(sx, pane.top + 30 + i * 40, pane.right - sx, 34), label, hint,
                bool(self.host.dev_flags().get(flag, False)),
            )))

    def _build_debug(self) -> None:
        self.debug_view = ScrollView(pygame.Rect(self.pane))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _choose_tab(self, index: int) -> None:
        self.index = index

    def _flash(self, message: str) -> None:
        self.notice = message
        self.notice_age = 0.0

    def _refilter(self) -> None:
        needle = self.search.value.strip().lower()
        self.filtered = [
            row for row in self.card_rows
            if (self.kind_filter is None or row.kind == self.kind_filter)
            and (not needle or needle in row.haystack)
        ]
        self.cards_view.offset = 0

    def _make_fx(self, name: str):
        def run() -> None:
            self.host.dev_play_fx(name)
            self._flash(f"played {name}")
        return run

    def _make_sound(self, name: str):
        def run() -> None:
            self.host.dev_play_sound(name)
        return run

    def _clear_tray(self) -> None:
        self.host.dev_clear_spawned()
        self._flash("sandbox tray cleared")

    def _start_new_game(self) -> None:
        raw = self.seed.value.strip()
        seed: int | None
        try:
            seed = int(raw) if raw else None
        except ValueError:
            seed = None
        players = self.players.value
        ai = min(self.ai_seats.value, players - 1)
        self.host.dev_new_game(players=players, ai_seats=ai, seed=seed)
        self.finish("restart")

    def _toggle_pause(self) -> None:
        paused = self.host.dev_toggle_pause()
        self._flash("engine paused" if paused else "engine resumed")

    def _step(self) -> None:
        self.host.dev_step()
        self._flash("stepped one decision")

    def _let_ai_decide(self) -> None:
        self.host.dev_let_ai_decide()
        self._flash("agent answered the open request")

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def on_event(self, event: pygame.event.Event) -> bool:
        if self.tabs.handle_event(event):
            return True
        if event.type == pygame.KEYDOWN and event.key == pygame.K_TAB:
            self.tabs.index = self.index = (self.index + 1) % len(self.TABS)
            return True

        tab = self.TABS[self.index]
        if tab == "Cards":
            return self._cards_event(event)
        if tab == "FX":
            if self.fx_view.handle_event(event):
                return True
            for button in (*self.fx_buttons, *self.sound_buttons):
                if button.handle_event(event):
                    return True
            return False
        if tab == "Game":
            return self._game_event(event)
        return self.debug_view.handle_event(event)

    def _cards_event(self, event: pygame.event.Event) -> bool:
        if self.search.handle_event(event):
            self._refilter()
            return True
        if self.cards_view.handle_event(event) or self.clear_button.handle_event(event):
            return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for kind, chip in self.kind_chips:
                if chip.rect.collidepoint(event.pos):
                    self.kind_filter = kind
                    self._refilter()
                    return True
            row = self._row_at(event.pos)
            if row is not None:
                # Left half inspects, right half spawns: the two things you want
                # from a card list, without a second click target per row.
                if event.pos[0] > self.cards_view.rect.right - 120:
                    self.host.dev_spawn_card(row.card_def)
                    self._flash(f"spawned {row.label} into the sandbox tray")
                else:
                    self.host.dev_inspect_card(row.card_def)
                return True
        return False

    def _game_event(self, event: pygame.event.Event) -> bool:
        if (
            self.players.handle_event(event)
            or self.ai_seats.handle_event(event)
            or self.seed.handle_event(event)
        ):
            self.ai_seats.high = max(0, self.players.value - 1)
            self.ai_seats.value = min(self.ai_seats.value, self.ai_seats.high)
            return True
        for button in (self.new_game_button, *self.engine_buttons):
            if button.handle_event(event):
                return True
        for flag, switch in self.switches:
            if switch.handle_event(event):
                state = self.host.dev_toggle(flag)
                self._flash(f"{FLAGS[flag][0]}: {'on' if state else 'off'}")
                return True
        return False

    def _row_at(self, pos: tuple[int, int]) -> _CardRow | None:
        if not self.cards_view.rect.collidepoint(pos):
            return None
        index = (pos[1] - self.cards_view.content_top) // self.card_row_h
        if 0 <= index < len(self.filtered):
            return self.filtered[index]
        return None

    def tick(self, dt: float) -> None:
        self.notice_age += dt
        tab = self.TABS[self.index]
        if tab == "Cards":
            self.search.update(dt)
            self.clear_button.update(dt)
        elif tab == "FX":
            for button in (*self.fx_buttons, *self.sound_buttons):
                button.update(dt)
        elif tab == "Game":
            self.players.update(dt)
            self.ai_seats.update(dt)
            self.seed.update(dt)
            self.new_game_button.update(dt)
            flags = self.host.dev_flags()
            for button in self.engine_buttons:
                button.update(dt)
            self.engine_buttons[0].label = (
                "Resume engine" if self.host.dev_paused() else "Pause engine"
            )
            self.engine_buttons[1].enabled = self.host.dev_paused()
            for flag, switch in self.switches:
                switch.update(dt, bool(flags.get(flag, False)))

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def draw_body(self, screen: pygame.Surface) -> None:
        self.tabs.draw(screen)
        tab = self.TABS[self.index]
        if tab == "Cards":
            self._draw_cards(screen)
        elif tab == "FX":
            self._draw_fx(screen)
        elif tab == "Game":
            self._draw_game(screen)
        else:
            self._draw_debug(screen)

        if self.notice and self.notice_age < 2.6:
            fade = 1.0 if self.notice_age < 1.9 else 1.0 - (self.notice_age - 1.9) / 0.7
            rect = pygame.Rect(0, 0, min(420, self.rect.width - 40), 26)
            rect.midbottom = (self.rect.centerx, self.rect.bottom - 8)
            T.pill(screen, rect, self.notice, bg=T.alpha(C.GOOD, int(52 * fade)),
                   fg=T.alpha(T.mix(C.GOOD, C.INK_BRIGHT, 0.5), int(255 * fade)),
                   border=T.alpha(C.GOOD, int(140 * fade)), fnt=T.ui(11, bold=True))

    # -- cards -------------------------------------------------------------

    def _draw_cards(self, screen: pygame.Surface) -> None:
        self.search.draw(screen)
        for kind, chip in self.kind_chips:
            chip.filled = kind == self.kind_filter
            chip.draw(screen)

        view = self.cards_view
        view.content_height = len(self.filtered) * self.card_row_h
        T.inset(screen, view.rect.inflate(8, 8), radius=10)
        view.begin(screen)
        y = view.content_top
        hover = pygame.mouse.get_pos()
        for row in self.filtered:
            if view.rect.top - self.card_row_h <= y <= view.rect.bottom:
                line = pygame.Rect(view.rect.left, y, view.rect.width - 8, self.card_row_h - 2)
                if line.collidepoint(hover):
                    T.round_rect(screen, line, (255, 255, 255, 18), radius=6)
                    T.text(screen, "spawn", (line.right - 14, line.centery), T.ui(10, bold=True),
                           C.GOLD, anchor="midright", shadow=None)
                draw_icon(screen, row.icon, (line.left + 14, line.centery), 14, row.accent)
                T.text(screen, row.label, (line.left + 30, line.centery), T.ui(12), C.INK,
                       anchor="midleft", shadow=None, max_width=line.width - 220)
                T.text(screen, row.kind.replace("_", " "), (line.right - 96, line.centery),
                       T.ui(10), C.INK_FAINT, anchor="midright", shadow=None)
            y += self.card_row_h
        view.end(screen)

        T.text(screen, f"{len(self.filtered)} of {len(self.card_rows)} cards \u00b7 "
                       "click to inspect, click the right edge to spawn",
               (self.pane.left, self.pane.bottom - 19), T.ui(10), C.INK_FAINT, shadow=None)
        self.clear_button.draw(screen)

    # -- fx ----------------------------------------------------------------

    def _draw_fx(self, screen: pygame.Surface) -> None:
        view = self.fx_view
        view.content_height = self.fx_content_h
        view.begin(screen)
        shift = self.pane.top - view.content_top
        T.text(screen, "ANIMATIONS", (self.pane.left, self.pane.top - shift), T.ui(11, bold=True),
               C.GOLD, shadow=None)
        for button in self.fx_buttons:
            saved = pygame.Rect(button.rect)
            button.rect.top -= shift
            button.draw(screen)
            button.rect = saved
        T.text(screen, "SOUND CUES", (self.pane.left, self.sound_top - shift),
               T.ui(11, bold=True), C.FROST, shadow=None)
        for button in self.sound_buttons:
            saved = pygame.Rect(button.rect)
            button.rect.top -= shift
            button.draw(screen)
            button.rect = saved
        view.end(screen)

    # -- game --------------------------------------------------------------

    def _draw_game(self, screen: pygame.Surface) -> None:
        pane = self.pane
        T.text(screen, "NEW GAME", (pane.left, pane.top), T.ui(11, bold=True), C.GOLD,
               shadow=None)
        self.players.draw(screen)
        self.ai_seats.draw(screen)
        T.text(screen, "Seed", (pane.left, self.seed.rect.centery), T.ui(12), C.INK,
               anchor="midleft", shadow=None)
        self.seed.draw(screen)
        self.new_game_button.draw(screen)

        T.text(screen, "ENGINE", (pane.left, self.engine_buttons[0].rect.top - 20),
               T.ui(11, bold=True), C.ARCANE, shadow=None)
        for button in self.engine_buttons:
            button.draw(screen)
        T.text(screen, "Pausing gates the presenter, not the rules \u2014 the engine simply "
                       "waits for an answer.",
               (pane.left, self.engine_buttons[0].rect.bottom + 12), T.ui(10),
               C.INK_FAINT, shadow=None)

        divider_x = pane.left + pane.width // 2 - 4
        T.hairline(screen, (divider_x, pane.top), (divider_x, pane.bottom - 10),
                   (255, 255, 255, 22))
        T.text(screen, "TOGGLES", (divider_x + 12, pane.top), T.ui(11, bold=True), C.POISON,
               shadow=None)
        for _flag, switch in self.switches:
            switch.draw(screen)

    # -- debug -------------------------------------------------------------

    def _draw_debug(self, screen: pygame.Surface) -> None:
        pane = self.pane
        stats = list(self.host.dev_stats())
        rects = self.host.dev_layout_rects()
        view = self.debug_view
        line_h = 20
        view.content_height = (len(stats) + len(rects) + 4) * line_h
        view.begin(screen)
        y = view.content_top

        T.text(screen, "RUNTIME", (pane.left, y), T.ui(11, bold=True), C.GOLD, shadow=None)
        y += line_h + 2
        for label, value in stats:
            T.text(screen, label, (pane.left + 6, y), T.ui(11), C.INK_DIM, shadow=None)
            T.text(screen, value, (pane.left + pane.width // 2, y), T.mono(11), C.INK,
                   shadow=None, max_width=pane.width // 2 - 12)
            y += line_h
        y += line_h // 2

        T.text(screen, "LAYOUT REGIONS", (pane.left, y), T.ui(11, bold=True), C.FROST,
               shadow=None)
        y += line_h + 2
        for name, box in rects.items():
            T.text(screen, name, (pane.left + 6, y), T.ui(11), C.INK_DIM, shadow=None)
            T.text(screen, f"{box[0]:>5} {box[1]:>5} {box[2]:>5} {box[3]:>5}",
                   (pane.left + pane.width // 2, y), T.mono(11), C.INK_FAINT, shadow=None)
            y += line_h
        view.end(screen)


def draw_layout_debug(
    screen: pygame.Surface, rects: Mapping[str, tuple[int, int, int, int]]
) -> None:
    """Outline and name every layout region. Driven by the ``layout_debug`` flag."""
    fnt = T.ui(9, bold=True)
    for i, (name, box) in enumerate(sorted(rects.items())):
        rect = pygame.Rect(box)
        if rect.width <= 1 or rect.height <= 1:
            continue
        colour = T.seat_colour(i)
        T.round_rect(screen, rect, T.alpha(colour, 190), radius=M.RADIUS_S, width=1)
        T.text(screen, name, (rect.left + 3, rect.top + 2), fnt, T.alpha(colour, 220),
               shadow=(0, 0, 0, 200))


def draw_fps(screen: pygame.Surface, fps: float, extra: str = "") -> None:
    """Corner readout. Deliberately tiny and unobtrusive."""
    label = f"{fps:5.1f} fps" + (f"  {extra}" if extra else "")
    fnt = T.mono(11, bold=True)
    width = fnt.size(label)[0] + 16
    rect = pygame.Rect(screen.get_width() - width - 8, 8, width, 20)
    colour = C.GOOD if fps >= 50 else (C.WARN if fps >= 30 else C.BAD)
    T.round_rect(screen, rect, (0, 0, 0, 150), radius=6)
    T.text(screen, label, rect.center, fnt, colour, anchor="center", shadow=None)


__all__ = [
    "FLAGS",
    "HOTKEY_HINT",
    "DevConsole",
    "DevHost",
    "Stepper",
    "Switch",
    "draw_fps",
    "draw_layout_debug",
]
