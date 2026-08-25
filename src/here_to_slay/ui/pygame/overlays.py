"""Modal surfaces that sit above the board.

Six of them: the rules reference behind the *i* button, a card inspector, the
full log, a pause menu, the hot-seat "pass the device" screen, and the game-over
banner. They share :class:`Overlay` — a fade, a backdrop, a framed panel, and a
close affordance — so a new one is a content list rather than a new widget.

Two decisions worth naming:

* **The rules page is generated from the loaded ``RuleSet``, not typed out.**
  Action point costs, the class list, player bounds and the victory conditions
  are read from content, so a variant that makes attacking cost three points
  gets a rules screen that says three. Only genuinely UI-side facts (the
  keyboard map) are literals here.
* **Overlays never touch game state.** They return a *result* — a chosen menu
  action, an acknowledgement — and the scene decides what that means. That keeps
  the presentation layer's one-way dependency on ``core`` intact.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import pygame

from here_to_slay.ui import lexicon as L
from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.animations import AnimationManager, ConfettiAnimation
from here_to_slay.ui.pygame.card_renderer import card_facts, render_card
from here_to_slay.ui.pygame.icons import card_icon_name, draw_icon
from here_to_slay.ui.pygame.theme import C, M
from here_to_slay.ui.pygame.widgets import (
    Button,
    Chip,
    IconButton,
    LogEntry,
    ScrollView,
    SegmentedControl,
)

FADE_IN = 0.18
FADE_OUT = 0.13


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class Overlay:
    """A modal panel. Owns its fade, its frame, and its own event capture."""

    #: How dark the board behind goes. ``0`` leaves it untouched.
    dim = 168
    #: Escape and a backdrop click both mean "go away" unless a subclass says
    #: otherwise — the hot-seat screen must not be dismissable, for instance.
    escapable = True
    backdrop_closes = True
    title = ""
    subtitle = ""
    icon: str | None = None

    def __init__(self, rect: pygame.Rect) -> None:
        self.rect = pygame.Rect(rect)
        self.phase = 0.0
        self.closing = False
        self.done = False
        self.result: Any = None
        self.elapsed = 0.0
        self._close_button: IconButton | None = None
        if self.escapable:
            size = 30
            self._close_button = IconButton(
                pygame.Rect(self.rect.right - size - 12, self.rect.top + 12, size, size),
                "close", self.dismiss, tooltip="Close (Esc)", accent=C.BLOOD,
            )

    # -- lifecycle ---------------------------------------------------------

    def dismiss(self, result: Any = None) -> None:
        """Begin the close animation. Idempotent."""
        if self.closing:
            return
        if result is not None:
            self.result = result
        self.closing = True

    def finish(self, result: Any = None) -> None:
        """Close *now*, skipping the fade — used when a choice must feel instant."""
        if result is not None:
            self.result = result
        self.closing = True
        self.phase = 0.0
        self.done = True

    @property
    def opacity(self) -> float:
        return max(0.0, min(1.0, self.phase))

    def update(self, dt: float) -> None:
        self.elapsed += dt
        if self.closing:
            self.phase -= dt / FADE_OUT
            if self.phase <= 0.0:
                self.phase = 0.0
                self.done = True
        elif self.phase < 1.0:
            self.phase = min(1.0, self.phase + dt / FADE_IN)
        if self._close_button is not None:
            self._close_button.update(dt)
        self.tick(dt)

    def tick(self, dt: float) -> None:
        """Subclass hook, so subclasses need not remember to call ``super``."""

    # -- input -------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Returns ``True`` for everything, since a modal swallows the board."""
        if self.done:
            return False
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE and self.escapable:
            self.dismiss()
            return True
        if self._close_button is not None and self._close_button.handle_event(event):
            return True
        if self.on_event(event):
            return True
        if (
            self.backdrop_closes
            and self.escapable
            and event.type == pygame.MOUSEBUTTONDOWN
            and event.button == 1
            and not self.rect.collidepoint(event.pos)
        ):
            self.dismiss()
        # Consume mouse and keys either way: a click "through" a modal onto the
        # board is the classic way to play a card you did not mean to.
        return event.type in (
            pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.KEYDOWN,
            pygame.MOUSEWHEEL, pygame.TEXTINPUT,
        )

    def on_event(self, event: pygame.event.Event) -> bool:
        return False

    # -- drawing -----------------------------------------------------------

    def draw(self, screen: pygame.Surface) -> None:
        alpha = T.ease_out_cubic(self.opacity)
        if self.dim:
            veil = T.surface(screen.get_size())
            veil.fill((*C.VOID, int(self.dim * alpha)))
            screen.blit(veil, (0, 0))

        # Rise-and-settle: the panel arrives from slightly below, which reads as
        # "this came forward" rather than "this appeared".
        rect = pygame.Rect(self.rect)
        rect.top += int((1.0 - T.ease_out_back(alpha, 1.2)) * 26)

        panel = T.surface(rect.size)
        local = pygame.Rect(0, 0, rect.width, rect.height)
        T.round_rect(panel, local, C.GLASS_DEEP, radius=M.RADIUS_XL)
        panel.blit(
            T.vgradient(rect.width, rect.height, (255, 255, 255, 22), (0, 0, 0, 40)), (0, 0)
        )
        T.round_rect(panel, local, T.alpha(C.GLASS_RIM, 140), radius=M.RADIUS_XL, width=1)
        T.drop_shadow(screen, rect, radius=M.RADIUS_XL, spread=28, offset=(0, 14), strength=140)
        panel.set_alpha(int(255 * alpha))
        screen.blit(panel, rect.topleft)

        offset = (rect.left - self.rect.left, rect.top - self.rect.top)
        if offset != (0, 0):
            # Draw the contents onto a shifted clip so the whole modal moves as
            # one during the entrance.
            saved = screen.get_clip()
            screen.set_clip(rect)
            self._draw_contents(screen, offset)
            screen.set_clip(saved)
        else:
            self._draw_contents(screen, (0, 0))

    def _draw_contents(self, screen: pygame.Surface, offset: tuple[int, int]) -> None:
        if offset == (0, 0):
            self.draw_header(screen)
            self.draw_body(screen)
            if self._close_button is not None:
                self._close_button.draw(screen)
            return
        scratch = T.surface(screen.get_size())
        self.draw_header(scratch)
        self.draw_body(scratch)
        if self._close_button is not None:
            self._close_button.draw(scratch)
        scratch.set_alpha(int(255 * T.ease_out_cubic(self.opacity)))
        screen.blit(scratch, offset)

    def draw_header(self, screen: pygame.Surface) -> None:
        if not self.title:
            return
        x = self.rect.left + 22
        y = self.rect.top + 26
        if self.icon:
            draw_icon(screen, self.icon, (x + 11, y), 24, C.GOLD)
            x += 34
        T.text(screen, self.title, (x, y), T.display(23), C.INK, anchor="midleft", shadow=None)
        if self.subtitle:
            width = T.display(23).size(self.title)[0]
            T.text(screen, self.subtitle, (x + width + 14, y + 2), T.ui(12),
                   C.INK_FAINT, anchor="midleft")
        T.hairline(
            screen, (self.rect.left + 18, self.rect.top + 50),
            (self.rect.right - 18, self.rect.top + 50), (255, 255, 255, 30),
        )

    def draw_body(self, screen: pygame.Surface) -> None:  # pragma: no cover - overridden
        pass

    @property
    def body_rect(self) -> pygame.Rect:
        top = self.rect.top + (62 if self.title else 20)
        return pygame.Rect(
            self.rect.left + 20, top, self.rect.width - 40, self.rect.bottom - top - 20
        )


class OverlayStack:
    """The modal stack. Only the top one gets input; all of them draw."""

    def __init__(self) -> None:
        self.items: list[Overlay] = []

    def push(self, overlay: Overlay) -> Overlay:
        self.items.append(overlay)
        return overlay

    def toggle(self, factory: Callable[[], Overlay], kind: type[Overlay]) -> Overlay | None:
        """Open one of ``kind``, or close it if it is already the top overlay."""
        if self.items and isinstance(self.items[-1], kind):
            self.items[-1].dismiss()
            return None
        return self.push(factory())

    def pop(self) -> None:
        if self.items:
            self.items[-1].dismiss()

    def clear(self) -> None:
        for item in self.items:
            item.dismiss()

    @property
    def top(self) -> Overlay | None:
        return self.items[-1] if self.items else None

    @property
    def busy(self) -> bool:
        """True while anything is up — the scene uses this to mute hover work."""
        return any(not item.closing for item in self.items)

    def has(self, kind: type[Overlay]) -> bool:
        return any(isinstance(item, kind) and not item.closing for item in self.items)

    def handle_event(self, event: pygame.event.Event) -> bool:
        top = self.top
        return top.handle_event(event) if top is not None else False

    def update(self, dt: float) -> list[Overlay]:
        """Advance, reap finished overlays, and hand them back so the scene can
        act on their results."""
        finished: list[Overlay] = []
        for item in list(self.items):
            item.update(dt)
            if item.done:
                self.items.remove(item)
                finished.append(item)
        return finished

    def draw(self, screen: pygame.Surface) -> None:
        for item in self.items:
            item.draw(screen)


# ---------------------------------------------------------------------------
# Typeset lines — the rules page's content model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """One row of a rules page. ``kind`` picks how it is measured and drawn."""

    kind: str = "p"
    text: str = ""
    value: str = ""
    accent: tuple[int, int, int] = C.INK
    icon: str | None = None
    chips: tuple[tuple[str, tuple[int, int, int], str | None], ...] = ()


def _line_height(line: Line, width: int) -> int:
    if line.kind == "h":
        return T.ui(15, bold=True).get_linesize() + 12
    if line.kind == "gap":
        return 10
    if line.kind == "rule":
        return 13
    if line.kind == "chips":
        return 30
    if line.kind == "row":
        fnt = T.ui(12)
        return max(
            fnt.get_linesize() + 7,
            len(T.wrap(line.text, fnt, max(40, width - 108))) * (fnt.get_linesize() + 2) + 7,
        )
    fnt = T.ui(12)
    indent = 20 if line.kind == "bullet" else 0
    return len(T.wrap(line.text, fnt, width - indent)) * (fnt.get_linesize() + 3) + 4


def _draw_line(screen: pygame.Surface, line: Line, x: int, y: int, width: int) -> None:
    if line.kind == "h":
        fnt = T.ui(15, bold=True)
        left = x
        if line.icon:
            draw_icon(screen, line.icon, (x + 8, y + fnt.get_linesize() // 2 + 4), 17, line.accent)
            left += 24
        T.text(screen, line.text.upper(), (left, y + 4), fnt, line.accent)
        return

    if line.kind == "rule":
        T.hairline(screen, (x, y + 6), (x + width, y + 6), (40, 56, 72, 50))
        return

    if line.kind == "chips":
        cx = x
        for label, colour, icon in line.chips:
            w = Chip.width_for(label, icon=icon, height=22)
            Chip(pygame.Rect(cx, y, w, 22), label, colour=colour, icon=icon).draw(screen)
            cx += w + 6
        return

    if line.kind == "row":
        fnt = T.ui(12)
        pill_w = 76
        T.pill(
            screen, pygame.Rect(x, y + 1, pill_w, fnt.get_linesize() + 4), line.value,
            bg=T.alpha(line.accent, 46), fg=C.INK,
            border=T.alpha(line.accent, 150), fnt=T.ui(11, bold=True),
        )
        body = pygame.Rect(x + pill_w + 14, y + 2, width - pill_w - 14, 400)
        T.draw_wrapped(screen, line.text, body, fnt, C.INK, line_gap=2)
        return

    if line.kind == "bullet":
        fnt = T.ui(12)
        pygame.draw.circle(screen, line.accent, (x + 5, y + fnt.get_linesize() // 2 + 1), 3)
        T.draw_wrapped(
            screen, line.text, pygame.Rect(x + 20, y, width - 20, 400), fnt, C.INK, line_gap=3
        )
        return

    if line.kind == "gap":
        return

    T.draw_wrapped(
        screen, line.text, pygame.Rect(x, y, width, 800), T.ui(12), line.accent, line_gap=3
    )


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _cost_label(cost: dict[str, Any] | None) -> str:
    points = int((cost or {}).get("action_points", 0) or 0)
    if points <= 0:
        return "free"
    return f"{points} AP"


def rules_pages(registry: Any) -> dict[str, list[Line]]:
    """Build the rules reference from the loaded content pack.

    Anything numeric comes from ``RuleSet``, so a variant's rules screen
    describes the variant. The prose explains mechanisms the data cannot.
    """
    rules = getattr(registry, "rules", None)
    setup = getattr(rules, "setup", None)
    turn = getattr(rules, "turn", None)
    classes = list(getattr(rules, "classes", ()) or ())
    ap = int(getattr(turn, "action_points_per_turn", 3) or 3)
    hand = int(getattr(setup, "starting_hand", 5) or 5)
    row = int(getattr(setup, "monster_row_size", 3) or 3)
    lo = int(getattr(setup, "min_players", 2) or 2)
    hi = int(getattr(setup, "max_players", 6) or 6)

    actions = [a for a in getattr(rules, "actions", ()) or () if getattr(a, "enabled", True)]
    free_actions = [a for a in actions if not (getattr(a, "cost", None) or {})]
    paid_actions = [a for a in actions if (getattr(a, "cost", None) or {})]
    paid_actions.sort(key=lambda a: int((getattr(a, "cost", None) or {}).get("action_points", 0)))

    overview: list[Line] = [
        Line("h", "The goal", accent=C.GOLD, icon="target"),
        Line("p", (
            f"Build a party of Heroes and slay Monsters. {lo}\u2013{hi} players. "
            "The first player to meet a victory condition wins immediately, "
            "mid-turn if that is when it happens."
        )),
        Line("gap"),
        Line("h", "Victory", accent=C.GOOD, icon="check"),
        *[
            Line("bullet", str(getattr(v, "text", None) or getattr(v, "id", "")), accent=C.GOOD)
            for v in getattr(rules, "victory", ()) or ()
        ],
        Line("gap"),
        Line("h", "The six classes", accent=C.ARCANE, icon="wizard"),
        Line("chips", chips=tuple(
            (cls.title(), T.CLASS_COLOURS.get(cls, C.INK_DIM), card_icon_name("hero", cls))
            for cls in classes
        )),
        Line("p", (
            "A Party Leader's class counts toward a class requirement and toward "
            "the all-classes win, but a Leader is never a Hero."
        ), accent=C.INK_DIM),
        Line("gap"),
        Line("h", "Setup", accent=C.FROST, icon="scroll"),
        Line("row", "cards dealt to each player", f"{hand}", accent=C.FROST),
        Line("row", "Monsters face up in the row at all times", f"{row}", accent=C.FROST),
        Line("row", "one Party Leader each, with a passive ability", "1", accent=C.FROST),
    ]

    turn_page: list[Line] = [
        Line("h", f"{ap} action points per turn", accent=C.GOLD, icon="bolt"),
        Line("p", (
            "Spend them in any combination, repeating whatever you like. "
            "Unspent points do not carry over."
        )),
        Line("gap"),
        *[
            Line("row", str(getattr(a, "label", "") or getattr(a, "id", "")),
                 _cost_label(getattr(a, "cost", None)), accent=C.GOLD)
            for a in paid_actions
        ],
        Line("gap"),
        Line("h", "Free and out of turn", accent=C.POISON, icon="modifier"),
        Line("bullet", (
            "Modifier \u2014 played into any roll by anyone, any number of times. "
            "A base Modifier offers two values; you declare which on play."
        ), accent=C.POISON),
        Line("bullet", (
            "Challenge \u2014 played when someone plays a Hero, Item or Magic card. "
            "Both sides roll 2d6 and the challenger wins ties. If the challenger "
            "wins the card is discarded and the action point is not refunded. "
            "Each card can be challenged only once, and a Hero's ability is never "
            "challengeable \u2014 only playing a card is."
        ), accent=C.BLOOD),
        *[
            Line("bullet", f"{getattr(a, 'label', '')} \u2014 costs no action point",
                 accent=C.INK_DIM)
            for a in free_actions
        ],
        Line("gap"),
        Line("h", "Heroes", accent=C.INK, icon="hero"),
        Line("p", (
            "Playing a Hero lets you roll its effect immediately at no extra cost. "
            "Once it is in your party you may spend a point to use it, once per "
            "turn \u2014 and a failed roll still spends the point."
        )),
    ]

    rolls_page: list[Line] = [
        Line("h", "Every roll is 2d6", accent=C.GOLD, icon="dice"),
        Line("p", (
            "Two dice, plus every Modifier played into the roll, compared against "
            "the card's bands. A band is a range with an outcome; the card decides "
            "what counts as success, not the engine."
        )),
        Line("gap"),
        Line("h", "The modification window", accent=C.ARCANE, icon="bolt"),
        Line("p", (
            "After the dice land and before the result is read, everyone may play "
            "Modifiers. Modifiers can be answered by more Modifiers; the stack "
            "resolves outermost first."
        )),
        Line("gap"),
        Line("h", "Challenges", accent=C.BLOOD, icon="challenge"),
        Line("p", (
            "Both sides roll before any Modifier is played, so a Modifier on a "
            "Challenge is an informed decision and can swing either roll. Ties go "
            "to the challenger."
        )),
        Line("gap"),
        Line("h", "Reading the pill", accent=C.INK_DIM, icon="eye"),
        Line("p", (
            "The number on a card's footer is the threshold its own text calls "
            "success \u2014 'to use' for a Hero ability, 'to slay' for a Monster."
        ), accent=C.INK_DIM),
    ]

    monsters_page: list[Line] = [
        Line("h", "Attacking", accent=C.BLOOD, icon="monster"),
        Line("p", (
            "Attacking costs the points shown on the action list and needs the "
            "Monster's party requirement met. Roll 2d6 against its bands: slay, "
            "nothing, or a penalty that applies immediately. A failed attack "
            "leaves the Monster in the row."
        )),
        Line("gap"),
        Line("h", "Requirements", accent=C.WARN, icon="target"),
        Line("bullet", "A class symbol is paid by a Hero of that class or by your Leader.",
             accent=C.WARN),
        Line("bullet",
             "A generic symbol needs a Hero of any class \u2014 the Leader does not count.",
             accent=C.WARN),
        Line("bullet", "One Hero cannot pay for two symbols.", accent=C.WARN),
        Line("gap"),
        Line("h", "After the slay", accent=C.GOOD, icon="skull"),
        Line("p", (
            "A slain Monster joins your party, grants a permanent skill, and can "
            "never be stolen, destroyed or attacked again. The row refills."
        ), accent=C.GOOD),
    ]

    board_page: list[Line] = [
        Line("h", "Where things are", accent=C.GOLD, icon="eye"),
        Line("row", "your hand, dice and effects", "bottom", accent=C.GOLD),
        Line("row", "your Leader and party", "centre", accent=C.GOLD),
        Line("row", "the Monster row you can attack", "middle", accent=C.GOLD),
        Line("row", "main deck, discard and Monster deck", "top", accent=C.GOLD),
        Line("row", "every opponent's party, newest first", "right", accent=C.GOLD),
        Line("row", "cards resolving now \u2014 Challenges, Magic, Modifiers", "left",
             accent=C.GOLD),
        Line("row", "turn order, with the active seat lit", "turn chip", accent=C.GOLD),
        Line("gap"),
        Line("h", "Reading the rail", accent=C.FROST, icon="hand_cards"),
        Line("p", (
            "Opponent strips show the Leader, initials, remaining action points "
            "and party. Hover one to expand it; hover a card in it to read the "
            "card. Counts you are not allowed to see stay as counts."
        ), accent=C.INK_DIM),
        Line("gap"),
        Line("h", "Highlights", accent=C.POISON, icon="check"),
        Line("bullet", "A cyan ring means a legal target for the open choice.", accent=C.GOLD),
        Line("bullet", "A dimmed card is visible but not selectable right now.", accent=C.INK_DIM),
        Line("bullet", "A tilted card has already been used this turn.", accent=C.INK_DIM),
    ]

    keys_page: list[Line] = [
        Line("h", "Keyboard", accent=C.GOLD, icon="gear"),
        Line("row", "open and close these rules", "I  /  F1", accent=C.GOLD),
        Line("row", "game log", "L", accent=C.GOLD),
        Line("row", "pause menu", "Esc", accent=C.GOLD),
        Line("row", "confirm the current choice", "Enter", accent=C.GOOD),
        Line("row", "pass, decline, or take no action", "Space", accent=C.INK_DIM),
        Line("row", "pick the nth option or card", "1 \u2026 9", accent=C.GOLD),
        Line("row", "cycle camera views", "Q / E", accent=C.CYAN),
        Line("row", "draw a card (1 AP)", "D", accent=C.FROST),
        Line("row", "play a Hero (1 AP)", "H", accent=C.GOOD),
        Line("row", "use a Hero ability (1 AP, free if played this turn)", "A", accent=C.GOLD),
        Line("row", "use Party Leader skill", "S", accent=C.GOLD),
        Line("row", "befriend a bestie (2 AP)", "F", accent=C.BLOOD),
        Line("row", "equip Gear (1 AP)", "G", accent=C.GOLD),
        Line("row", "cast Magic (1 AP)", "C", accent=C.ARCANE),
        Line("row", "burn hand and draw five (3 AP)", "B", accent=C.WARN),
        Line("row", "roll the dice when asked", "R", accent=C.FROST),
        Line("row", "inspect the card under the cursor", "click", accent=C.ARCANE),
        Line("row", "toggle sound", "M", accent=C.INK_DIM),
        Line("row", "fullscreen", "F11", accent=C.INK_DIM),
        Line("gap"),
        Line("h", "Developer console", accent=C.ARCANE, icon="flask"),
        Line("row", "open the console", "Ctrl+Shift+D", accent=C.ARCANE),
        Line("p", (
            "Spawn any card, replay any animation, change the seat count, step "
            "the engine one decision at a time. It never mutates a live game "
            "\u2014 board changes restart into a fresh one."
        ), accent=C.INK_DIM),
    ]

    return {
        "Overview": overview,
        "Turn": turn_page,
        "Rolls": rolls_page,
        "Monsters": monsters_page,
        "Board": board_page,
        "Keys": keys_page,
    }


class RulesOverlay(Overlay):
    """The *i* button's destination: a tabbed, scrollable rules reference."""

    title = L.HOW_TO_PLAY
    icon = "info"

    def __init__(self, layout: Any, registry: Any, *, tab: int = 0) -> None:
        super().__init__(layout.modal_rect)
        self.pages = rules_pages(registry)
        self.names = list(self.pages)
        self.index = max(0, min(tab, len(self.names) - 1))
        self.subtitle = "the base game, read from the loaded content pack"

        body = self.body_rect
        tab_w = min(body.width, 108 * len(self.names))
        self.tabs = SegmentedControl(
            pygame.Rect(body.left, body.top, tab_w, 30), self.names,
            index=self.index, on_change=self._choose,
        )
        self.view = ScrollView(
            pygame.Rect(body.left, body.top + 42, body.width, body.height - 42)
        )

    def _choose(self, index: int) -> None:
        self.index = index
        self.view.offset = 0

    def on_event(self, event: pygame.event.Event) -> bool:
        if self.tabs.handle_event(event) or self.view.handle_event(event):
            return True
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_i, pygame.K_F1):
                self.dismiss()
                return True
            if event.key in (pygame.K_RIGHT, pygame.K_TAB):
                self._choose((self.index + 1) % len(self.names))
                self.tabs.index = self.index
                return True
            if event.key == pygame.K_LEFT:
                self._choose((self.index - 1) % len(self.names))
                self.tabs.index = self.index
                return True
            if event.key in (pygame.K_DOWN, pygame.K_PAGEDOWN):
                self.view.scroll_by(60)
                return True
            if event.key in (pygame.K_UP, pygame.K_PAGEUP):
                self.view.scroll_by(-60)
                return True
        return False

    def draw_body(self, screen: pygame.Surface) -> None:
        self.tabs.draw(screen)
        lines = self.pages[self.names[self.index]]
        column = self.view.rect.width - 22

        total = sum(_line_height(line, column) for line in lines)
        self.view.content_height = total
        self.view.begin(screen)
        y = self.view.content_top
        for line in lines:
            height = _line_height(line, column)
            if y + height >= self.view.rect.top - 30 and y <= self.view.rect.bottom + 30:
                _draw_line(screen, line, self.view.rect.left + 4, y, column)
            y += height
        self.view.end(screen)


# ---------------------------------------------------------------------------
# Card inspector
# ---------------------------------------------------------------------------


class CardOverlay(Overlay):
    """A card at readable size, with its facts spelled out beside it."""

    icon = "eye"

    def __init__(self, layout: Any, card_def: Any, *, where: str = "", owner: str = "") -> None:
        card_w = min(300, max(200, int(layout.width * 0.19)))
        card_h = int(card_w * M.CARD_ASPECT)
        width = card_w + 320
        height = max(card_h + 96, 380)
        rect = pygame.Rect(
            (layout.width - width) // 2, (layout.height - height) // 2, width, height
        )
        self.card_def = card_def
        self.card_w = card_w
        self.card_h = card_h
        self.where = where
        self.owner = owner
        self.title = str(getattr(card_def, "name", "") or "Card")
        facts = card_facts(card_def)
        self.facts = facts
        self.subtitle = facts.type_line
        self.icon = card_icon_name(facts.kind, facts.card_class)
        super().__init__(rect)

    def draw_body(self, screen: pygame.Surface) -> None:
        body = self.body_rect
        card_rect = pygame.Rect(body.left, body.top, self.card_w, self.card_h)
        T.drop_shadow(screen, card_rect, radius=12, spread=18, offset=(0, 8), strength=120)
        screen.blit(render_card(self.card_def, self.card_w, self.card_h, detail=True),
                    card_rect.topleft)

        x = card_rect.right + 22
        width = body.right - x
        y = body.top + 2
        accent = self.facts.accent

        rows: list[tuple[str, str, tuple[int, int, int]]] = []
        if self.facts.threshold is not None:
            label = self.facts.threshold_label or "threshold"
            rows.append((f"{self.facts.threshold}+", label, C.GOLD))
        if self.facts.requirement:
            rows.append((self.facts.requirement, "requirement", C.WARN))
        if self.facts.slot:
            rows.append((self.facts.slot, "plays as", C.FROST))
        if self.facts.passive:
            rows.append(("Passive", "always on", C.ARCANE))
        if self.facts.reaction_window:
            rows.append((self.facts.reaction_window.replace("_", " "), "reacts on", C.POISON))
        if self.facts.triggers:
            rows.append((str(self.facts.triggers), "triggers", C.INFO))
        copies = int(getattr(self.card_def, "copies", 0) or 0)
        if copies > 1:
            rows.append((f"\u00d7{copies}", "in the deck", C.INK_DIM))
        if self.where:
            rows.append((self.where, "location", C.INK_DIM))
        if self.owner:
            rows.append((self.owner, "controlled by", C.INK_DIM))

        for value, label, colour in rows:
            fnt = T.ui(15, bold=True)
            T.text(screen, value, (x, y), fnt, colour)
            T.text(screen, label.upper(), (x, y + fnt.get_linesize() - 1), T.ui(9, bold=True),
                   C.INK_FAINT)
            y += fnt.get_linesize() + 16

        text = str(getattr(self.card_def, "text", "") or "")
        if text:
            T.hairline(screen, (x, y + 2), (body.right, y + 2), (255, 255, 255, 26))
            y += 12
            T.draw_wrapped(
                screen, text, pygame.Rect(x, y, width, body.bottom - y - 24),
                T.serif(13), C.INK, line_gap=4,
            )

        def_id = str(getattr(self.card_def, "id", "") or "")
        if def_id:
            T.text(screen, def_id, (body.right, body.bottom - 6), T.mono(9),
                   T.alpha(accent, 150), anchor="bottomright", shadow=None)


# ---------------------------------------------------------------------------
# Log
# ---------------------------------------------------------------------------


class LogOverlay(Overlay):
    """The whole game log, oldest first, scrolled to the end."""

    title = "Game log"
    icon = "scroll"

    def __init__(self, layout: Any, entries: Sequence[LogEntry]) -> None:
        width = min(760, int(layout.width * 0.62))
        height = min(620, int(layout.height * 0.78))
        super().__init__(pygame.Rect(
            (layout.width - width) // 2, (layout.height - height) // 2, width, height
        ))
        self.entries = list(entries)
        self.subtitle = f"{len(self.entries)} events"
        self.view = ScrollView(self.body_rect)
        self._line_h = T.ui(12).get_linesize() + 7
        self.view.content_height = len(self.entries) * self._line_h
        self.view.offset = max(0, self.view.content_height - self.view.rect.height)

    def on_event(self, event: pygame.event.Event) -> bool:
        if self.view.handle_event(event):
            return True
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_l:
                self.dismiss()
                return True
            if event.key in (pygame.K_DOWN, pygame.K_PAGEDOWN):
                self.view.scroll_by(self._line_h * 4)
                return True
            if event.key in (pygame.K_UP, pygame.K_PAGEUP):
                self.view.scroll_by(-self._line_h * 4)
                return True
            if event.key == pygame.K_END:
                self.view.offset = max(0, self.view.content_height - self.view.rect.height)
                return True
        return False

    def draw_body(self, screen: pygame.Surface) -> None:
        if not self.entries:
            T.text(screen, "Nothing has happened yet.", self.view.rect.center, T.ui(13),
                   C.INK_FAINT, anchor="center", shadow=None)
            return
        fnt = T.ui(12)
        self.view.content_height = len(self.entries) * self._line_h
        self.view.begin(screen)
        y = self.view.content_top
        for i, entry in enumerate(self.entries):
            if self.view.rect.top - self._line_h <= y <= self.view.rect.bottom:
                x = self.view.rect.left + 4
                T.text(screen, f"{i + 1:>4}", (x, y + 2), T.mono(10), C.INK_FAINT, shadow=None)
                x += 40
                if entry.icon:
                    draw_icon(screen, entry.icon, (x + 7, y + self._line_h // 2 - 1), 13,
                              entry.colour)
                    x += 20
                T.text(screen, entry.text, (x, y + 2), fnt, entry.colour, shadow=None,
                       max_width=self.view.rect.right - x - 12)
            y += self._line_h
        self.view.end(screen)


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------


@dataclass
class MenuItem:
    """One row of the pause menu. ``state`` renders as a right-aligned value."""

    key: str
    label: str
    icon: str | None = None
    subtitle: str = ""
    state: Callable[[], str] | None = None
    accent: tuple[int, int, int] = C.GOLD
    danger: bool = False


class MenuOverlay(Overlay):
    """Esc: settings and session control. Results are strings the scene reads."""

    title = L.PAUSED
    icon = "gear"

    def __init__(self, layout: Any, items: Sequence[MenuItem], *, subtitle: str = "") -> None:
        width = min(430, int(layout.width * 0.36))
        rows = len(items)
        height = 108 + rows * 54
        super().__init__(pygame.Rect(
            (layout.width - width) // 2, (layout.height - height) // 2, width, height
        ))
        self.subtitle = subtitle
        self.items = list(items)
        self.buttons: list[Button] = []
        body = self.body_rect
        for i, item in enumerate(self.items):
            rect = pygame.Rect(body.left, body.top + i * 54, body.width, 44)
            self.buttons.append(Button(
                rect, item.label, self._make_pick(item.key),
                icon=item.icon, subtitle=item.subtitle, align="left",
                accent=C.BLOOD if item.danger else item.accent,
                primary=i == 0,
                shortcut=str(i + 1),
            ))

    def _make_pick(self, key: str) -> Callable[[], None]:
        def pick() -> None:
            self.finish(key)
        return pick

    def on_event(self, event: pygame.event.Event) -> bool:
        for button in self.buttons:
            if button.handle_event(event):
                return True
        if event.type == pygame.KEYDOWN and pygame.K_1 <= event.key <= pygame.K_9:
            index = event.key - pygame.K_1
            if index < len(self.items):
                self.finish(self.items[index].key)
                return True
        return False

    def tick(self, dt: float) -> None:
        for button, item in zip(self.buttons, self.items, strict=False):
            button.update(dt)
            if item.state is not None:
                button.subtitle = item.state()

    def draw_body(self, screen: pygame.Surface) -> None:
        for button in self.buttons:
            button.draw(screen)


# ---------------------------------------------------------------------------
# Hot-seat handover
# ---------------------------------------------------------------------------


class HandoverOverlay(Overlay):
    """Between two humans on one screen: hide the board until the next player
    confirms. Deliberately not escapable — that is the whole point."""

    escapable = False
    backdrop_closes = False
    #: Fully opaque, not merely dark: the incoming player must not be able to
    #: squint at the outgoing player's hand.
    dim = 255

    def __init__(
        self,
        layout: Any,
        player_name: str,
        *,
        seat_colour: tuple[int, int, int] = C.GOLD,
        turn: int = 0,
    ) -> None:
        width = min(560, int(layout.width * 0.5))
        height = 300
        super().__init__(pygame.Rect(
            (layout.width - width) // 2, (layout.height - height) // 2, width, height
        ))
        self.player_name = player_name
        self.seat_colour = seat_colour
        self.turn = turn
        body = self.body_rect
        self.button = Button(
            pygame.Rect(body.centerx - 110, body.bottom - 52, 220, 44),
            L.READY, lambda: self.finish("ready"),
            primary=True, icon="check", shortcut="Enter",
        )

    def on_event(self, event: pygame.event.Event) -> bool:
        if self.button.handle_event(event):
            return True
        if event.type == pygame.KEYDOWN and event.key in (
            pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE
        ):
            self.finish("ready")
            return True
        return False

    def tick(self, dt: float) -> None:
        self.button.update(dt)

    def draw_body(self, screen: pygame.Surface) -> None:
        body = self.body_rect
        cx = body.centerx
        initials = "".join(part[0] for part in self.player_name.split()[:2]).upper() or "?"

        glow = 0.5 + 0.5 * T.pulse(self.elapsed, period=2.2, low=0.0, high=1.0)
        T.blit_glow(screen, (cx, body.top + 54), 150, T.alpha(self.seat_colour, int(60 * glow)))
        T.badge(screen, (cx, body.top + 54), 38, initials, bg=self.seat_colour,
                ring=T.alpha(C.INK_BRIGHT, 90), fnt=T.display(28))

        T.text(screen, L.PASS_DEVICE, (cx, body.top + 112), T.ui(12), C.INK_DIM,
               anchor="midtop", shadow=None)
        T.text(screen, self.player_name, (cx, body.top + 132), T.display(28), C.INK,
               anchor="midtop", shadow=None)
        if self.turn:
            T.text(screen, f"{L.TURN} {self.turn}", (cx, body.top + 172), T.ui(11), C.INK_FAINT,
                   anchor="midtop", shadow=None)
        T.text(screen, "Nimeni altcineva nu ar trebui să se uite la ecran.",
               (cx, body.top + 196), T.ui(11, italic=True), C.INK_FAINT,
               anchor="midtop", shadow=None)
        self.button.draw(screen)


# ---------------------------------------------------------------------------
# Game over
# ---------------------------------------------------------------------------


@dataclass
class ScoreRow:
    """One player's end-of-game line."""

    name: str
    detail: str
    colour: tuple[int, int, int] = C.INK_DIM
    winner: bool = False
    party: int = 0
    slain: int = 0
    classes: tuple[str, ...] = field(default_factory=tuple)


class GameOverOverlay(Overlay):
    """Winner, why, and the final table. Confetti in the winner's colour."""

    escapable = False
    backdrop_closes = False
    dim = 210
    icon = "leader"

    def __init__(
        self,
        layout: Any,
        winner: str,
        rows: Sequence[ScoreRow],
        *,
        reason: str = "",
        winner_colour: tuple[int, int, int] = C.GOLD,
        turns: int = 0,
    ) -> None:
        width = min(620, int(layout.width * 0.56))
        height = min(560, 220 + len(rows) * 44)
        super().__init__(pygame.Rect(
            (layout.width - width) // 2, (layout.height - height) // 2, width, height
        ))
        self.title = L.VICTORY
        self.subtitle = f"after {turns} turns" if turns else ""
        self.winner = winner
        self.reason = reason
        self.winner_colour = winner_colour
        self.rows = list(rows)
        self.fx = AnimationManager(cap=60)
        self.fx.add(ConfettiAnimation((layout.width, layout.height), 5.0, count=160))

        body = self.body_rect
        self.buttons = [
            Button(pygame.Rect(body.left, body.bottom - 46, body.width // 2 - 6, 42),
                   "Play again", lambda: self.finish("restart"), primary=True,
                   icon="dice", shortcut="Enter"),
            Button(pygame.Rect(body.centerx + 6, body.bottom - 46, body.width // 2 - 6, 42),
                   "Quit", lambda: self.finish("quit"), icon="close", shortcut="Esc"),
        ]

    def on_event(self, event: pygame.event.Event) -> bool:
        for button in self.buttons:
            if button.handle_event(event):
                return True
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.finish("restart")
                return True
            if event.key in (pygame.K_ESCAPE, pygame.K_q):
                self.finish("quit")
                return True
        return False

    def tick(self, dt: float) -> None:
        self.fx.update(dt)
        for button in self.buttons:
            button.update(dt)

    def draw(self, screen: pygame.Surface) -> None:
        super().draw(screen)
        # Confetti falls in front of the panel, so it reads as being in the room
        # rather than behind a window.
        self.fx.draw_top(screen)

    def draw_body(self, screen: pygame.Surface) -> None:
        body = self.body_rect
        cx = body.centerx
        glow = 0.55 + 0.45 * T.pulse(self.elapsed, period=2.6, low=0.0, high=1.0)
        T.blit_glow(screen, (cx, body.top + 34), 240, T.alpha(self.winner_colour, int(70 * glow)))
        T.text(screen, self.winner, (cx, body.top + 12), T.display(34), C.INK,
               anchor="midtop", shadow=None)
        if self.reason:
            T.text(screen, self.reason, (cx, body.top + 54), T.ui(13),
                   C.INK_DIM, anchor="midtop", shadow=None)

        y = body.top + 86
        for row in self.rows:
            rect = pygame.Rect(body.left, y, body.width, 38)
            if row.winner:
                T.round_rect(screen, rect, T.alpha(row.colour, 46), radius=10)
                T.round_rect(screen, rect, T.alpha(row.colour, 160), radius=10, width=1)
            T.badge(screen, (rect.left + 20, rect.centery), 13,
                    row.name[:1].upper(), bg=row.colour, fnt=T.ui(13, bold=True))
            T.text(screen, row.name, (rect.left + 42, rect.centery),
                   T.ui(14, bold=row.winner), C.INK if row.winner else C.INK_DIM,
                   anchor="midleft", max_width=rect.width - 210)
            T.text(screen, row.detail, (rect.right - 12, rect.centery), T.ui(11),
                   C.INK_FAINT, anchor="midright", shadow=None)
            if row.classes:
                x = rect.right - 130
                for cls in row.classes:
                    draw_icon(screen, card_icon_name("hero", cls), (x, rect.centery), 13,
                              T.CLASS_COLOURS.get(cls, C.INK_FAINT))
                    x += 16
            y += 44

        for button in self.buttons:
            button.draw(screen)


__all__ = [
    "CardOverlay",
    "GameOverOverlay",
    "HandoverOverlay",
    "Line",
    "LogOverlay",
    "MenuItem",
    "MenuOverlay",
    "Overlay",
    "OverlayStack",
    "RulesOverlay",
    "ScoreRow",
    "rules_pages",
]
