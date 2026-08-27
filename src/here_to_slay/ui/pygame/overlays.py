"""Modal surfaces that sit above the board.

Eight of them: the rules reference behind the *i* button, a card inspector, the
full log, a pause menu, the settings screen, the list of saved games, the
hot-seat "pass the device" screen, and the game-over banner. They share
:class:`Overlay` — a fade, a backdrop, a framed panel, and a close affordance —
so a new one is a content list rather than a new widget.

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
from here_to_slay.ui.pygame.art import trophy_card
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
        return L.FREE
    return L.ap_label(points)


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
        Line("h", "Scopul", accent=C.GOLD, icon="target"),
        Line("p", (
            f"Adună persoane și împrietenește-te cu besties. {lo}\u2013{hi} jucători. "
            "Primul care primește legitimația lui Andrei câștigă imediat, "
            "chiar în mijlocul turei."
        )),
        Line("gap"),
        Line("h", L.VICTORY_TROPHY, accent=C.GOOD, icon="check"),
        *[
            Line("bullet", str(getattr(v, "text", None) or getattr(v, "id", "")), accent=C.GOOD)
            for v in getattr(rules, "victory", ()) or ()
        ],
        Line("gap"),
        Line("h", f"{len(classes)} clase", accent=C.ARCANE, icon="wizard"),
        Line("chips", chips=tuple(
            (L.class_label(cls), T.CLASS_COLOURS.get(cls, C.INK_DIM), card_icon_name("hero", cls))
            for cls in classes
        )),
        Line("p", (
            "Clasa șefului de grup contează la cerințe și la victoria pe clase, "
            "dar șeful nu e o persoană."
        ), accent=C.INK_DIM),
        Line("gap"),
        Line("h", "Setup", accent=C.FROST, icon="scroll"),
        Line("row", "barfe împărțite fiecărui jucător", f"{hand}", accent=C.FROST),
        Line("row", "besties cu fața în sus pe masă", f"{row}", accent=C.FROST),
        Line("row", "un șef de grup, cu abilitate pasivă", "1", accent=C.FROST),
    ]

    turn_page: list[Line] = [
        Line("h", f"{ap} prompts pe tură", accent=C.GOLD, icon="bolt"),
        Line("p", (
            "Cheltuie-le cum vrei, repetând ce-ți place. "
            "Prompturile necheltuite nu trec în tura următoare."
        )),
        Line("gap"),
        *[
            Line("row", str(getattr(a, "label", "") or getattr(a, "id", "")),
                 _cost_label(getattr(a, "cost", None)), accent=C.GOLD)
            for a in paid_actions
        ],
        Line("gap"),
        Line("h", "Gratis și în afara turei", accent=C.POISON, icon="modifier"),
        Line("bullet", (
            "Download/Upload Speed \u2014 se joacă pe orice np.random(), de oricine, "
            "de câte ori vrei. O carte de bază are două valori; alegi la joc."
        ), accent=C.POISON),
        Line("bullet", (
            "Confruntare xiaolin (Șiaolin / Șia all in) \u2014 când cineva joacă "
            "o persoană, un cheat sau un script. Ambele părți dau np.random() "
            "și provocatorul câștigă la egalitate. Dacă câștigă, barfa e aruncată "
            "și promptul nu se înapoiază. O barfă se confruntă o singură dată; "
            "abilitatea unei persoane nu e contestabilă \u2014 doar jucarea ei."
        ), accent=C.BLOOD),
        *[
            Line("bullet", f"{getattr(a, 'label', '')} \u2014 nu costă prompt",
                 accent=C.INK_DIM)
            for a in free_actions
        ],
        Line("gap"),
        Line("h", "Persoane", accent=C.INK, icon="hero"),
        Line("p", (
            "Jucând o persoană poți da np.random() pe efect imediat, fără prompt extra. "
            "Odată în grup, cheltuie un prompt ca s-o folosești, o dată pe tură "
            "\u2014 și un np.random() eșuat tot consumă promptul."
        )),
    ]

    rolls_page: list[Line] = [
        Line("h", "Fiecare aruncare e np.random()", accent=C.GOLD, icon="dice"),
        Line("p", (
            "Două zaruri, plus fiecare download/upload speed jucat, comparate cu "
            "benzile cărții. O bandă e un interval cu un rezultat; cartea decide "
            "ce e succes, nu motorul."
        )),
        Line("gap"),
        Line("h", "Fereastra de modificare", accent=C.ARCANE, icon="bolt"),
        Line("p", (
            "După ce cade np.random() și înainte de rezultat, oricine poate juca "
            "download/upload speed. Acestea se pot răspunde între ele; stiva "
            "se rezolvă de la ultima spre prima."
        )),
        Line("gap"),
        Line("h", "Confruntare xiaolin", accent=C.BLOOD, icon="challenge"),
        Line("p", (
            "Ambele părți dau np.random() înainte de orice download/upload speed, "
            "deci modificarea e o decizie informată. La egalitate câștigă Șiaolin."
        )),
        Line("gap"),
        Line("h", "Citirea pragului", accent=C.INK_DIM, icon="eye"),
        Line("p", (
            "Numărul de pe o barfă e pragul pe care textul îl numește succes "
            "\u2014 'ca să folosești' pentru o persoană, 'ca să te împrietenești' "
            "pentru o bestie."
        ), accent=C.INK_DIM),
    ]

    monsters_page: list[Line] = [
        Line("h", "Împrietenirea", accent=C.BLOOD, icon="monster"),
        Line("p", (
            "Să te împrietenești costă prompturile din listă și cere cerința "
            "bestiei îndeplinită. np.random() pe benzi: împrietenire, nimic, "
            "sau o pedeapsă imediată. Un eșec lasă bestia pe masă."
        )),
        Line("gap"),
        Line("h", "Cerințe", accent=C.WARN, icon="target"),
        Line("bullet", "Un simbol de clasă e platit de o persoană de acea clasă sau de șeful tău.",
             accent=C.WARN),
        Line("bullet",
             "Un simbol generic cere o persoană de orice clasă \u2014 șeful nu contează.",
             accent=C.WARN),
        Line("bullet", "O persoană nu poate plăti două simboluri.", accent=C.WARN),
        Line("gap"),
        Line("h", "După împrietenire", accent=C.GOOD, icon="skull"),
        Line("p", (
            "Bestia împrietenită se alătură grupului, dă o abilitate permanentă "
            "și nu mai poate fi furată, ștearsă sau atacată. Rândul se reumple."
        ), accent=C.GOOD),
    ]

    board_page: list[Line] = [
        Line("h", "Unde e fiecare lucru", accent=C.GOLD, icon="eye"),
        Line("row", "barfele tale, np.random() și efecte", "jos", accent=C.GOLD),
        Line("row", "șeful și grupul tău", "centru", accent=C.GOLD),
        Line("row", "besties cu care te poți împrieteni", "mijloc", accent=C.GOLD),
        Line("row", "inbox, trash și teancul de besties", "sus", accent=C.GOLD),
        Line("row", "grupul fiecărui adversar", "dreapta", accent=C.GOLD),
        Line("row", "în execuție \u2014 Șiaolin, scripts, download/upload", "stânga",
             accent=C.GOLD),
        Line("row", "ordinea tururilor, scaunul activ luminat", "chip tură", accent=C.GOLD),
        Line("gap"),
        Line("h", "Banda adversarilor", accent=C.FROST, icon="hand_cards"),
        Line("p", (
            "Benzile arată șeful, inițialele, prompturile rămase și grupul. "
            "Hover pe o bandă s-o extinzi; hover pe o barfă s-o citești."
        ), accent=C.INK_DIM),
        Line("gap"),
        Line("h", "Evidențieri", accent=C.POISON, icon="check"),
        Line("bullet", "Un inel cyan e o țintă legală pentru alegerea deschisă.", accent=C.GOLD),
        Line("bullet", "O barfă estompată se vede, dar nu e selectabilă acum.", accent=C.INK_DIM),
        Line("bullet", "O barfă înclinată a fost deja folosită în tura asta.", accent=C.INK_DIM),
    ]

    keys_page: list[Line] = [
        Line("h", "Tastatură", accent=C.GOLD, icon="gear"),
        Line("row", "deschide și închide regulile", "I  /  F1", accent=C.GOLD),
        Line("row", "jurnal", "L", accent=C.GOLD),
        Line("row", "meniu pauză", "Esc", accent=C.GOLD),
        Line("row", "confirmă alegerea", "Enter", accent=C.GOOD),
        Line("row", "treci, refuzi, sau nicio acțiune", "Space", accent=C.INK_DIM),
        Line("row", "auto-pass pe fereastra de reacție", "numărătoare 10 s", accent=C.POISON),
        Line("row", "a n-a opțiune sau barfă", "1 \u2026 9", accent=C.GOLD),
        Line("row", "schimbă camera", "Q / E", accent=C.CYAN),
        Line("row", "trage o barfă (1 prompt)", "D", accent=C.FROST),
        Line("row", "joacă o persoană (1 prompt)", "H", accent=C.GOOD),
        Line("row", "folosește abilitatea (1 prompt, gratis dacă e jucată acum)",
             "A", accent=C.GOLD),
        Line("row", "abilitatea șefului de grup", "S", accent=C.GOLD),
        Line("row", "împrietenește-te cu o bestie (2 prompts)", "F", accent=C.BLOOD),
        Line("row", "echipează un cheat (1 prompt)", "G", accent=C.GOLD),
        Line("row", "rulează un script (1 prompt)", "C", accent=C.ARCANE),
        Line("row", "aruncă barfele și trage cinci (3 prompts)", "B", accent=C.WARN),
        Line("row", "np.random() când ți se cere", "R", accent=C.FROST),
        Line("row", "inspectează barfa de sub cursor", "click", accent=C.ARCANE),
        Line("row", "sunet", "M", accent=C.INK_DIM),
        Line("row", "fullscreen", "F11", accent=C.INK_DIM),
        Line("gap"),
        Line("h", "Consola de dezvoltare", accent=C.ARCANE, icon="flask"),
        Line("row", "deschide consola", "Ctrl+Shift+D", accent=C.ARCANE),
        Line("p", (
            "Spawnează orice barfă, reia animații, schimbă numărul de scaune. "
            "Nu mută un joc live \u2014 schimbările de masă repornesc unul nou."
        ), accent=C.INK_DIM),
    ]

    return {
        "Prezentare": overview,
        "Tura": turn_page,
        "np.random": rolls_page,
        "Besties": monsters_page,
        "Masa": board_page,
        "Taste": keys_page,
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
        self.title = L.card_name(card_def)
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
            label = self.facts.threshold_label or "prag"
            rows.append((f"{self.facts.threshold}+", label, C.GOLD))
        if self.facts.requirement:
            rows.append((L.requirement_label(self.facts.requirement), "necesită", C.WARN))
        if self.facts.slot:
            rows.append((self.facts.slot, "se joacă ca", C.FROST))
        if self.facts.passive:
            rows.append((L.EFFECT_PASSIVE, L.PASSIVE_ABILITY, C.ARCANE))
        if self.facts.reaction_window:
            rows.append((self.facts.reaction_window.replace("_", " "), "reacționează la", C.POISON))
        if self.facts.triggers:
            rows.append((str(self.facts.triggers), "declanșări", C.INFO))
        copies = int(getattr(self.card_def, "copies", 0) or 0)
        if copies > 1:
            rows.append((f"\u00d7{copies}", "în pachet", C.INK_DIM))
        if self.where:
            rows.append((self.where, "locație", C.INK_DIM))
        if self.owner:
            rows.append((self.owner, "controlat de", C.INK_DIM))

        for value, label, colour in rows:
            fnt = T.ui(15, bold=True)
            T.text(screen, value, (x, y), fnt, colour)
            T.text(screen, label.upper(), (x, y + fnt.get_linesize() - 1), T.ui(9, bold=True),
                   C.INK_FAINT)
            y += fnt.get_linesize() + 16

        text = L.card_text(self.card_def)
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
        self._line_h = T.ui(16).get_linesize() + 7
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
            T.text(screen, "Nothing has happened yet.", self.view.rect.center, T.ui(14),
                   C.INK_FAINT, anchor="center", shadow=None)
            return
        fnt = T.ui(16)
        self.view.content_height = len(self.entries) * self._line_h
        self.view.begin(screen)
        y = self.view.content_top
        for i, entry in enumerate(self.entries):
            if self.view.rect.top - self._line_h <= y <= self.view.rect.bottom:
                x = self.view.rect.left + 4
                T.text(screen, f"{i + 1:>4}", (x, y + 2), T.mono(13), C.INK_FAINT, shadow=None)
                x += 48
                if entry.icon:
                    draw_icon(screen, entry.icon, (x + 7, y + self._line_h // 2 - 1), T.s(16),
                              entry.colour)
                    x += 24
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


class SaveListOverlay(MenuOverlay):
    """Pick a saved game to load. A menu whose rows come from a directory.

    Its own type rather than a flag on :class:`MenuOverlay`, so the scene can
    tell "the player picked *Quit*" from "the player picked a file" by asking
    what closed rather than by parsing the string that came back.
    """

    title = L.SAVES_TITLE
    icon = "scroll"

    #: More than this and the panel would run off the bottom of a small window.
    #: Saves are listed newest first, so the cut is always the stalest ones.
    MAX_ROWS = 8

    def __init__(self, layout: Any, saves: Sequence[Any]) -> None:
        self.saves = list(saves)[: self.MAX_ROWS]
        items = [
            MenuItem(
                str(index), game.title, icon="deck", subtitle=game.describe(),
                accent=C.GOOD if not game.summary.finished else C.INK_DIM,
            )
            for index, game in enumerate(self.saves)
        ] or [MenuItem("", L.SAVES_EMPTY, icon="close", accent=C.INK_DIM)]
        super().__init__(layout, items, subtitle=L.MENU_RESUME_HINT)

    def chosen(self) -> Any | None:
        """The :class:`SaveGame` the player picked, if they picked one."""
        try:
            return self.saves[int(str(self.result))]
        except (TypeError, ValueError, IndexError):
            return None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SettingRow:
    """One editable preference. ``kind`` is ``toggle`` or ``range``."""

    key: str
    label: str
    kind: str = "toggle"
    icon: str = "gear"
    low: float = 0.0
    high: float = 1.0
    step: float = 0.05
    suffix: str = ""


#: The screen, in order. Every row names a field of
#: :class:`~here_to_slay.ui.settings.Settings`, so adding a preference is one
#: entry here and one field there — the overlay itself knows no preference names.
SETTING_ROWS: tuple[SettingRow, ...] = (
    SettingRow("sound", L.SET_SOUND, "toggle", "bard"),
    SettingRow("volume", L.SET_VOLUME, "range", "bard", 0.0, 1.0, 0.05),
    SettingRow("animations", L.SET_ANIMATIONS, "toggle", "bolt"),
    SettingRow("shake", L.SET_SHAKE, "toggle", "bolt"),
    SettingRow("reaction_timer", L.SET_REACTION_TIMER, "toggle", "challenge"),
    SettingRow("ai_delay", L.SET_AI_SPEED, "range", "flask", 0.0, 2.0, 0.1, "s"),
    SettingRow("ui_scale", L.SET_UI_SCALE, "range", "eye", 0.6, 1.6, 0.05, "x"),
    SettingRow("fullscreen", L.SET_FULLSCREEN, "toggle", "eye"),
)


class SettingsOverlay(Overlay):
    """The settings screen. Applies as you click; persists when it closes.

    It holds a :class:`~here_to_slay.ui.settings.Settings` value and replaces it
    on every change — the dataclass is frozen, so there is no way to half-apply
    one. ``on_change`` lets the board hear each edit immediately (the volume
    slider is useless if you cannot hear it move); the closing ``result`` is the
    final value, which is what the app writes to disk.
    """

    title = L.SETTINGS_TITLE
    subtitle = L.SETTINGS_HINT
    icon = "gear"

    ROW_H = 44

    def __init__(
        self,
        layout: Any,
        settings: Any,
        *,
        on_change: Callable[[Any], None] | None = None,
        rows: Sequence[SettingRow] = SETTING_ROWS,
    ) -> None:
        self.rows = list(rows)
        width = min(500, max(360, int(layout.width * 0.4)))
        height = 96 + len(self.rows) * self.ROW_H + 16
        super().__init__(pygame.Rect(
            (layout.width - width) // 2, (layout.height - height) // 2, width, height
        ))
        self.settings = settings
        self.on_change = on_change
        self.result = settings
        self.buttons: list[tuple[SettingRow, str, Button]] = []
        self._build()

    def _build(self) -> None:
        body = self.body_rect
        size = 26
        for i, row in enumerate(self.rows):
            top = body.top + i * self.ROW_H
            if row.kind == "toggle":
                rect = pygame.Rect(body.right - 82, top + 4, 78, 28)
                self.buttons.append((row, "toggle", Button(
                    rect, "", self._make_toggle(row), align="center",
                )))
            else:
                minus = pygame.Rect(body.right - 100, top + 5, size, size)
                plus = pygame.Rect(body.right - size - 4, top + 5, size, size)
                self.buttons.append((row, "minus", IconButton(
                    minus, "minus", self._make_nudge(row, -1), accent=C.FROST,
                )))
                self.buttons.append((row, "plus", IconButton(
                    plus, "plus", self._make_nudge(row, +1), accent=C.FROST,
                )))

    # -- editing -----------------------------------------------------------

    def _apply(self, settings: Any) -> None:
        self.settings = settings
        self.result = settings
        if self.on_change is not None:
            self.on_change(settings)

    def _make_toggle(self, row: SettingRow) -> Callable[[], None]:
        def toggle() -> None:
            self._apply(self.settings.toggled(row.key))
        return toggle

    def _make_nudge(self, row: SettingRow, direction: int) -> Callable[[], None]:
        def nudge() -> None:
            current = float(getattr(self.settings, row.key, row.low))
            value = min(row.high, max(row.low, current + direction * row.step))
            self._apply(self.settings.with_change(row.key, round(value, 3)))
        return nudge

    def _value_text(self, row: SettingRow) -> str:
        value = getattr(self.settings, row.key, None)
        if isinstance(value, bool):
            return L.ON if value else L.OFF
        if isinstance(value, float):
            if row.key == "volume":
                return f"{round(value * 100)}%"
            return f"{value:g}{row.suffix}"
        return str(value)

    # -- events ------------------------------------------------------------

    def on_event(self, event: pygame.event.Event) -> bool:
        return any(button.handle_event(event) for _row, _kind, button in self.buttons)

    def tick(self, dt: float) -> None:
        for row, kind, button in self.buttons:
            button.update(dt)
            if kind == "toggle":
                on = bool(getattr(self.settings, row.key, False))
                button.label = L.ON if on else L.OFF
                button.accent = C.GOOD if on else C.INK_DIM
                button.text_colour = C.INK_BRIGHT if on else C.INK_DIM

    # -- drawing -----------------------------------------------------------

    def draw_body(self, screen: pygame.Surface) -> None:
        body = self.body_rect
        for i, row in enumerate(self.rows):
            top = body.top + i * self.ROW_H
            centre_y = top + self.ROW_H // 2 - 4
            draw_icon(screen, row.icon, (body.left + 12, centre_y), 18, C.INK_DIM)
            T.text(screen, row.label, (body.left + 30, centre_y), T.ui(14), C.INK,
                   anchor="midleft", shadow=None)
            if row.kind == "range":
                T.text(
                    screen, self._value_text(row),
                    (body.right - 116, centre_y), T.ui(13, bold=True), C.GOLD,
                    anchor="midright", shadow=None,
                )
            if i:
                T.hairline(
                    screen, (body.left + 8, top), (body.right - 8, top), (255, 255, 255, 16)
                )

        for _row, _kind, button in self.buttons:
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
    """Trophy card, win slogan, and the final table."""

    escapable = False
    backdrop_closes = False
    dim = 220
    icon = None
    title = ""

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
        width = min(720, int(layout.width * 0.64))
        height = min(720, int(layout.height * 0.88))
        super().__init__(pygame.Rect(
            (layout.width - width) // 2, (layout.height - height) // 2, width, height
        ))
        self.winner = winner
        self.reason = reason
        self.winner_colour = winner_colour
        self.rows = list(rows)
        self.turns = turns
        card_w = min(210, int(width * 0.30))
        card_h = int(card_w * 1.42)
        self.trophy_size = (card_w, card_h)
        self.trophy = trophy_card((card_w, card_h))
        self.fx = AnimationManager(cap=60)
        self.fx.add(ConfettiAnimation((layout.width, layout.height), 5.5, count=180))

        body = self.body_rect
        self.buttons = [
            Button(pygame.Rect(body.left, body.bottom - 46, body.width // 2 - 6, 42),
                   L.PLAY_AGAIN, lambda: self.finish("restart"), primary=True,
                   icon="dice", shortcut="Enter"),
            Button(pygame.Rect(body.centerx + 6, body.bottom - 46, body.width // 2 - 6, 42),
                   L.QUIT, lambda: self.finish("quit"), icon="close", shortcut="Esc"),
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
        self.fx.draw_top(screen)

    def draw_body(self, screen: pygame.Surface) -> None:
        body = self.body_rect
        cx = body.centerx
        glow = 0.55 + 0.45 * T.pulse(self.elapsed, period=2.6, low=0.0, high=1.0)
        T.blit_glow(screen, (cx, body.top + 120), 280, T.alpha(self.winner_colour, int(80 * glow)))

        T.text(screen, "Ai câștigat,", (cx, body.top + 4), T.display(26), C.GOLD,
               anchor="midtop", shadow=None)
        T.text(screen, "dictatorul îți e subjugat", (cx, body.top + 34), T.display(22), C.INK,
               anchor="midtop", shadow=None, max_width=body.width - 16)

        pop = min(1.0, self.elapsed / 0.7)
        scale = 0.42 + 0.58 * T.ease_out_back(pop, 1.4)
        tw, th = self.trophy_size
        sw, sh = max(12, int(tw * scale)), max(16, int(th * scale))
        try:
            shown = pygame.transform.smoothscale(self.trophy, (sw, sh))
        except (pygame.error, ValueError):
            shown = pygame.transform.scale(self.trophy, (sw, sh))
        trophy_y = body.top + 70
        dest = shown.get_rect(midtop=(cx, trophy_y))
        T.round_rect(
            screen,
            dest.inflate(10, 10),
            T.alpha(C.GOLD, int(90 + 80 * glow)),
            radius=12, width=2,
        )
        screen.blit(shown, dest)

        flavor_y = dest.bottom + 10
        T.text(screen, L.VICTORY_TROPHY, (cx, flavor_y), T.ui(15, bold=True), C.GOLD,
               anchor="midtop", shadow=None)
        T.text(screen, self.winner, (cx, flavor_y + 22), T.ui(16, bold=True), C.INK,
               anchor="midtop", shadow=None, max_width=body.width - 20)
        if self.reason:
            T.text(screen, self.reason, (cx, flavor_y + 44), T.ui(12),
                   C.INK_DIM, anchor="midtop", shadow=None, max_width=body.width - 24)
        elif self.turns:
            T.text(screen, f"după {self.turns} ture", (cx, flavor_y + 44), T.ui(12),
                   C.INK_FAINT, anchor="midtop", shadow=None)

        y = flavor_y + 70
        row_bottom = body.bottom - 56
        for row in self.rows:
            if y + 36 > row_bottom:
                break
            rect = pygame.Rect(body.left, y, body.width, 34)
            if row.winner:
                T.round_rect(screen, rect, T.alpha(row.colour, 46), radius=10)
                T.round_rect(screen, rect, T.alpha(row.colour, 160), radius=10, width=1)
            T.badge(screen, (rect.left + 20, rect.centery), 12,
                    row.name[:1].upper(), bg=row.colour, fnt=T.ui(12, bold=True))
            T.text(screen, row.name, (rect.left + 42, rect.centery),
                   T.ui(13, bold=row.winner), C.INK if row.winner else C.INK_DIM,
                   anchor="midleft", max_width=rect.width - 210)
            T.text(screen, row.detail, (rect.right - 12, rect.centery), T.ui(11),
                   C.INK_FAINT, anchor="midright", shadow=None)
            if row.classes:
                x = rect.right - 130
                for cls in row.classes:
                    draw_icon(screen, card_icon_name("hero", cls), (x, rect.centery), 13,
                              T.CLASS_COLOURS.get(cls, C.INK_FAINT))
                    x += 16
            y += 38

        for button in self.buttons:
            button.draw(screen)


__all__ = [
    "SETTING_ROWS",
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
    "SaveListOverlay",
    "ScoreRow",
    "SettingRow",
    "SettingsOverlay",
    "rules_pages",
]
