"""Interactive primitives: buttons, card sprites, rows, lists, toasts.

These know how to look and how to be clicked, and nothing else. They never see
the engine — a scene hands them plain values and gets back "you clicked this
card id". That keeps the interesting logic (which cards are legal targets right
now?) in one place instead of smeared across eight widgets.

Two conventions worth knowing before reading on:

* **Hover is a float, not a bool.** ``CardSprite.hover`` eases toward 0 or 1
  every frame, and lift/scale/glow are all read off it. Snapping a card up on
  mouse-over feels like a website; easing it feels like a table.
* **``handle_event`` returns True when it consumed the event.** Scenes stop at
  the first widget that says yes, so a click on a button never also counts as a
  click on the card behind it.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pygame

from here_to_slay.ui import lexicon as L
from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.card_renderer import (
    CARD_H,
    CARD_W,
    LOD_TINY,
    card_facts,
    render_card,
    render_card_back,
)
from here_to_slay.ui.pygame.icons import card_icon_name, draw_icon
from here_to_slay.ui.pygame.theme import C, M

if TYPE_CHECKING:  # pragma: no cover - typing only
    from here_to_slay.content.schema import CardDef
    from here_to_slay.core.rolls import Roll


def _approach(current: float, target: float, dt: float, speed: float = 12.0) -> float:
    """Frame-rate independent easing toward a target."""
    return current + (target - current) * min(1.0, dt * speed)


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------


class Button:
    """A pill button. Optionally carries an icon and a keyboard shortcut hint."""

    def __init__(
        self,
        rect: pygame.Rect,
        label: str,
        on_click: Callable[[], None] | None = None,
        *,
        enabled: bool = True,
        bg_colour: tuple[int, int, int] = (32, 44, 56),
        hover_colour: tuple[int, int, int] = (48, 68, 86),
        text_colour: tuple[int, int, int] = C.INK_BRIGHT,
        border_colour: tuple[int, int, int] = (90, 120, 150),
        icon: str | None = None,
        accent: tuple[int, int, int] | None = None,
        shortcut: str = "",
        subtitle: str = "",
        align: str = "center",
        primary: bool = False,
    ) -> None:
        self.rect = pygame.Rect(rect)
        self.label = label
        self.on_click = on_click
        self.enabled = enabled
        self.hovered = False
        self.pressed = False
        self.bg_colour = bg_colour
        self.hover_colour = hover_colour
        self.text_colour = text_colour
        self.border_colour = border_colour
        self.icon = icon
        self.accent = accent
        self.shortcut = shortcut
        self.subtitle = subtitle
        self.align = align
        self.primary = primary
        self._hover = 0.0
        self._press = 0.0

    # -- interaction -------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> bool:
        if not self.enabled:
            self.hovered = self.pressed = False
            return False
        if event.type == pygame.MOUSEMOTION:
            self.hovered = self.rect.collidepoint(event.pos)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.hovered = True
                self.pressed = True
                self._press = 1.0
                if self.on_click:
                    self.on_click()
                return True
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.pressed = False
        return False

    def update(self, dt: float) -> None:
        self._hover = _approach(self._hover, 1.0 if self.hovered and self.enabled else 0.0, dt, 16)
        self._press = _approach(self._press, 0.0, dt, 9)

    # -- drawing -----------------------------------------------------------

    def draw(self, screen: pygame.Surface) -> None:
        rect = pygame.Rect(self.rect)
        radius = min(M.RADIUS, rect.height // 2)

        if not self.enabled:
            body = T.shade(self.bg_colour, 0.5)
            ink = C.INK_FAINT
            rim = T.alpha(self.border_colour, 60)
        else:
            body = T.mix(self.bg_colour, self.hover_colour, self._hover)
            if self.primary:
                body = T.mix(T.shade(C.GOLD_DEEP, 0.9), C.GOLD, self._hover * 0.7)
            ink = self.text_colour if not self.primary else C.INK_DARK
            rim = T.alpha(
                T.mix(self.border_colour, C.GOLD, self._hover * 0.5),
                int(120 + 100 * self._hover),
            )

        if self.enabled and self._hover > 0.05:
            T.drop_shadow(screen, rect, radius=radius, spread=int(6 + 8 * self._hover),
                          offset=(0, 3), strength=int(70 + 50 * self._hover))
        squish = int(self._press * 2)
        rect = rect.inflate(-squish * 2, -squish * 2)

        T.round_rect(screen, rect, body, radius=radius)
        screen.blit(
            T.vgradient(rect.width, rect.height, (255, 255, 255, 30), (0, 0, 0, 34)),
            rect.topleft,
        )
        T.round_rect(screen, rect, rim, radius=radius, width=1 if not self.primary else 2)

        pad = 10
        cx = rect.left + pad
        if self.icon:
            size = min(rect.height - 12, 22)
            draw_icon(screen, self.icon, (cx + size // 2, rect.centery), size, ink)
            cx += size + 8

        shortcut_w = 0
        if self.shortcut and rect.width > 150:
            key_font = T.mono(max(9, min(12, rect.height // 3)), bold=True)
            kw = key_font.size(self.shortcut)[0] + 10
            key_rect = pygame.Rect(rect.right - kw - 8, rect.centery - 9, kw, 18)
            T.round_rect(screen, key_rect, T.alpha(C.VOID, 110), radius=4)
            T.round_rect(screen, key_rect, T.alpha(ink, 70), radius=4, width=1)
            T.text(screen, self.shortcut, key_rect.center, key_font, T.alpha(ink, 210),
                   anchor="center", shadow=None)
            shortcut_w = kw + 12

        room = rect.right - cx - pad - shortcut_w
        label_size = max(10, min(15, rect.height // 2 - (5 if self.subtitle else 1)))
        label_font = T.ui(label_size, bold=True)
        label_y = rect.centery - (8 if self.subtitle else 0)
        if self.align == "left" or self.icon or self.subtitle:
            T.text(screen, self.label, (cx, label_y), label_font, ink,
                   anchor="midleft", max_width=room)
            if self.subtitle:
                T.text(screen, self.subtitle, (cx, rect.centery + 10),
                       T.ui(max(9, label_font.get_height() - 5)),
                       T.alpha(ink, 165), anchor="midleft", max_width=room, shadow=None)
        else:
            T.text(screen, self.label, (rect.centerx - shortcut_w // 2, rect.centery),
                   label_font, ink, anchor="center", max_width=room)


class IconButton(Button):
    """A round icon-only button: the info button, close crosses, nudges."""

    def __init__(
        self,
        rect: pygame.Rect,
        icon: str,
        on_click: Callable[[], None] | None = None,
        *,
        tooltip: str = "",
        accent: tuple[int, int, int] = C.GOLD,
        enabled: bool = True,
        badge_text: str = "",
    ) -> None:
        super().__init__(rect, "", on_click, enabled=enabled, icon=icon, accent=accent)
        self.tooltip = tooltip
        self.badge_text = badge_text

    def draw(self, screen: pygame.Surface) -> None:
        rect = pygame.Rect(self.rect)
        radius = min(rect.width, rect.height) // 2
        centre = rect.center
        accent = self.accent or C.GOLD
        lit = self._hover if self.enabled else 0.0

        if lit > 0.04:
            T.blit_glow(screen, centre, int(radius * 2.1), T.alpha(accent, int(70 * lit)))
        pygame.draw.circle(screen, T.mix((28, 38, 48), T.shade(accent, 0.55), lit * 0.75),
                           centre, radius)
        pygame.draw.circle(screen, T.alpha(T.mix(accent, C.INK_BRIGHT, lit * 0.4),
                                           int(110 + 130 * lit)), centre, radius, 1)
        ink = T.mix(C.INK_DIM, C.INK_BRIGHT, 0.45 + 0.55 * lit) if self.enabled else C.INK_FAINT
        draw_icon(screen, self.icon or "info", centre, int(radius * 1.25), ink)

        if self.badge_text:
            br = max(6, radius // 2)
            T.badge(screen, (rect.right - br, rect.top + br), br, self.badge_text,
                    bg=C.BLOOD, fg=C.INK_BRIGHT, fnt=T.ui(max(8, br), bold=True))


class Chip:
    """A small non-interactive label: a class tag, a count, a status."""

    def __init__(
        self,
        rect: pygame.Rect,
        label: str,
        *,
        colour: tuple[int, int, int] = C.GOLD,
        icon: str | None = None,
        filled: bool = False,
    ) -> None:
        self.rect = pygame.Rect(rect)
        self.label = label
        self.colour = colour
        self.icon = icon
        self.filled = filled

    @staticmethod
    def font(height: int) -> pygame.font.Font:
        return T.ui(max(8, height - 9), bold=True)

    @staticmethod
    def icon_size(height: int) -> int:
        return min(height - 5, 15)

    @classmethod
    def width_for(cls, label: str, *, icon: str | None = None, height: int = 22) -> int:
        """The width this chip needs. Callers must size with this, not by eye.

        Measuring with a different font from the one :meth:`draw` uses is how a
        chip ends up reading "Wiz\u2026".
        """
        width = 7 + cls.font(height).size(label)[0] + 8
        return width + (cls.icon_size(height) + 4 if icon else 0)

    def draw(self, screen: pygame.Surface) -> None:
        radius = self.rect.height // 2
        if self.filled:
            T.round_rect(screen, self.rect, self.colour, radius=radius)
            ink = T.readable_ink(self.colour)
        else:
            T.round_rect(screen, self.rect, T.alpha((10, 16, 22), 220), radius=radius)
            T.round_rect(screen, self.rect, T.alpha(self.colour, 200), radius=radius, width=1)
            ink = T.mix(self.colour, C.INK_BRIGHT, 0.55)
        x = self.rect.left + 7
        if self.icon:
            size = self.icon_size(self.rect.height)
            draw_icon(screen, self.icon, (x + size // 2, self.rect.centery), size, ink)
            x += size + 4
        if self.label:
            T.text(screen, self.label, (x, self.rect.centery),
                   self.font(self.rect.height), ink,
                   anchor="midleft", max_width=self.rect.right - x - 6, shadow=None)


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------


class CardSprite:
    """One card on the board: where it is, what state it is in, how it reacts.

    ``rect`` is the card's *resting* box. Hover lift and the pop given to a
    legal target are drawn as offsets from it, so layout code never has to
    account for animation.
    """

    def __init__(
        self,
        card_id: str,
        card_def: CardDef | None,
        rect: pygame.Rect,
        *,
        face_down: bool = False,
        tapped: bool = False,
        highlighted: bool = False,
        selected: bool = False,
        dimmed: bool = False,
        attachments: tuple[str, ...] = (),
        attachment_defs: tuple[Any, ...] = (),
        badge_text: str = "",
        badge_colour: tuple[int, int, int] = C.GOLD,
        rotation: float = 0.0,
        lift_on_hover: int = 14,
        owner_colour: tuple[int, int, int] | None = None,
        depth: float = 0.7,
    ) -> None:
        self.card_id = card_id
        self.card_def = card_def
        self.rect = pygame.Rect(rect)
        self.face_down = face_down
        self.tapped = tapped
        self.highlighted = highlighted
        self.selected = selected
        self.dimmed = dimmed
        self.attachments = attachments
        self.attachment_defs = attachment_defs
        self.badge_text = badge_text
        self.badge_colour = badge_colour
        self.rotation = rotation
        self.lift_on_hover = lift_on_hover
        self.owner_colour = owner_colour
        self.depth = max(0.0, min(1.0, depth))
        self.hovered = False
        self.hover = 0.0
        self.enter = 1.0  # opaque unless a zone explicitly starts an entrance
        self.clickable = True

    # -- state -------------------------------------------------------------

    def update(self, dt: float) -> None:
        self.hover = _approach(self.hover, 1.0 if self.hovered else 0.0, dt, 14)
        if self.enter < 1.0:
            self.enter = min(1.0, self.enter + dt * 4.0)

    def update_hover(self, pos: tuple[int, int]) -> bool:
        self.hovered = self.hit(pos)
        return self.hovered

    def hit(self, pos: tuple[int, int]) -> bool:
        return self.draw_rect().collidepoint(pos)

    def draw_rect(self) -> pygame.Rect:
        """Where the card is actually painted, including hover lift."""
        rect = pygame.Rect(self.rect)
        if self.hover > 0.01:
            rect.y -= int(self.lift_on_hover * T.ease_out_cubic(self.hover))
        return rect

    @property
    def centre(self) -> tuple[int, int]:
        return self.rect.center

    # -- drawing -----------------------------------------------------------

    def draw(self, screen: pygame.Surface) -> None:
        rect = self.draw_rect()
        w, h = rect.width, rect.height
        if w < 4 or h < 4:
            return

        # Far rows sit slightly squashed to imply table plane.
        squash = 1.0 - (1.0 - self.depth) * 0.04
        draw_h = max(4, int(h * squash))
        y_off = h - draw_h

        entering = self.enter < 0.999
        if entering:
            k = T.ease_out_back(self.enter)
            grow = int((1.0 - k) * -draw_h * 0.12)
            rect = rect.inflate(grow, grow)
            draw_h = max(4, int(rect.height * squash))
            y_off = rect.height - draw_h

        surf = render_card(
            self.card_def, rect.width, draw_h,
            tapped=self.tapped, highlighted=self.highlighted,
            face_down=self.face_down, dimmed=self.dimmed, selected=self.selected,
        )
        # Never ADD-blit white onto card faces: paper is already near-white, so
        # even a one-frame brighten saturates to a blank rectangle.

        # Depth-driven contact shadow.
        hover_k = T.ease_out_cubic(self.hover)
        shadow_off = int(2 + (1.0 - self.depth) * 6 + hover_k * 3)
        shadow_spread = int(6 + (1.0 - self.depth) * 8 + hover_k * 4)
        T.drop_shadow(
            screen, pygame.Rect(rect.left, rect.top + y_off, rect.width, draw_h),
            radius=9,
            spread=shadow_spread,
            offset=(0, shadow_off),
            strength=int(70 + 50 * self.depth + 30 * hover_k),
        )

        blit_rect = pygame.Rect(rect.left, rect.top + y_off, rect.width, draw_h)
        if self.rotation:
            surf = pygame.transform.rotozoom(surf, self.rotation, 1.0)
            blit_rect = surf.get_rect(center=blit_rect.center)
        if entering:
            surf = surf.copy()
            surf.set_alpha(int(255 * min(1.0, self.enter * 1.6)))
        screen.blit(surf, blit_rect.topleft)

        if self.owner_colour is not None and rect.width >= LOD_TINY:
            strip = pygame.Rect(rect.left + 4, rect.bottom - 4, rect.width - 8, 3)
            T.round_rect(screen, strip, self.owner_colour, radius=2)

        # Equipped Cheats/Hacks sit offset down-right so the host stays readable
        # and hoverable on its top-left.
        faces = self.attachment_defs or ()
        if faces and rect.width >= 48:
            aw = max(28, int(rect.width * 0.55))
            ah = int(aw * M.CARD_ASPECT)
            base_x = blit_rect.left + int(blit_rect.width * 0.32)
            base_y = blit_rect.top + int(blit_rect.height * 0.28)
            for i, adef in enumerate(faces[:3]):
                ox = base_x + i * max(6, aw // 8)
                oy = base_y + i * max(6, ah // 8)
                mini = render_card(adef, aw, ah)
                T.drop_shadow(
                    screen, pygame.Rect(ox, oy, aw, ah),
                    radius=6, spread=8, offset=(0, 3), strength=70,
                )
                screen.blit(mini, (ox, oy))
            if len(faces) > 3:
                r = max(7, rect.width // 12)
                centre = (blit_rect.right - r - 2, blit_rect.bottom - r - 2)
                pygame.draw.circle(screen, C.GOLD, centre, r)
                T.text(screen, f"+{len(faces) - 3}", centre, T.ui(max(8, r), bold=True),
                       C.INK_DARK, anchor="center", shadow=None)
        elif self.attachments and rect.width >= 40:
            r = max(7, rect.width // 11)
            centre = (rect.right - r - 3, rect.bottom - r - 3)
            pygame.draw.circle(screen, C.GOLD, centre, r)
            pygame.draw.circle(screen, C.INK_DARK, centre, r, 1)
            T.text(screen, f"+{len(self.attachments)}", centre,
                   T.ui(max(8, r), bold=True), C.INK_DARK, anchor="center", shadow=None)

        if self.badge_text and rect.width >= 40:
            r = max(8, rect.width // 10)
            centre = (rect.left + r + 2, rect.bottom - r - 2)
            pygame.draw.circle(screen, self.badge_colour, centre, r)
            pygame.draw.circle(screen, C.INK_BRIGHT, centre, r, 1)
            T.text(screen, self.badge_text, centre, T.ui(max(8, r), bold=True),
                   T.readable_ink(self.badge_colour), anchor="center", shadow=None)


#: What a row is fed: (id, def, face_down, tapped, highlighted, attachments).
#: Kept as a tuple rather than a dataclass because it is the shape the first
#: client used and its tests still speak it.
CardTuple = tuple[str, Any, bool, bool, bool, tuple[str, ...]]


class ZoneWidget:
    """A titled row of cards that fits itself into a rect.

    Three arrangements, chosen by ``mode``:

    * ``row`` — evenly spaced, overlapping only when it must.
    * ``fan`` — a hand: overlapped and rotated around a shallow arc.
    * ``stack`` — a deck: a few offset backs suggesting depth.
    """

    def __init__(
        self,
        rect: pygame.Rect,
        title: str = "",
        *,
        card_size: tuple[int, int] = (CARD_W, CARD_H),
        max_overlap: bool = True,
        mode: str = "row",
        align: str = "center",
        panel: bool = True,
        empty_hint: str = "",
    ) -> None:
        self.rect = pygame.Rect(rect)
        self.title = title
        self.card_size = card_size
        self.max_overlap = max_overlap
        self.mode = mode
        self.align = align
        self.panel = panel
        self.empty_hint = empty_hint
        self.sprites: list[CardSprite] = []
        self.selected_ids: set[str] = set()
        self._by_id: dict[str, CardSprite] = {}

    # -- content -----------------------------------------------------------

    def set_cards(self, cards_data: Sequence[CardTuple]) -> None:
        """Rebuild the row, preserving hover/entrance state per card id."""
        previous = self._by_id
        self.sprites = []
        self._by_id = {}
        if not cards_data:
            return

        boxes = self._boxes(len(cards_data))
        for (cid, cdef, face_down, tapped, highlighted, atts), (box, spin) in zip(
            cards_data, boxes, strict=False
        ):
            sprite = CardSprite(
                cid, cdef, box,
                face_down=face_down, tapped=tapped, highlighted=highlighted,
                selected=cid in self.selected_ids, attachments=atts, rotation=spin,
                lift_on_hover=18 if self.mode == "fan" else 12,
            )
            old = previous.get(cid)
            if old is not None:
                sprite.hover = old.hover
                sprite.hovered = old.hovered
                sprite.enter = old.enter
                sprite.attachment_defs = old.attachment_defs
            else:
                sprite.enter = 0.0  # only brand-new cards fade in
            self.sprites.append(sprite)
            self._by_id[cid] = sprite

    def _boxes(self, count: int) -> list[tuple[pygame.Rect, float]]:
        cw, ch = self.card_size
        head = 18 if self.title else 4
        inner = pygame.Rect(
            self.rect.left + 8, self.rect.top + head,
            max(8, self.rect.width - 16), max(8, self.rect.height - head - 6),
        )
        cw = min(cw, inner.width)
        ch = min(ch, inner.height)
        y = inner.top + (inner.height - ch) // 2

        if self.mode == "stack":
            out = []
            step = max(2, min(6, inner.width // max(1, count * 3)))
            for i in range(count):
                out.append((pygame.Rect(inner.left + i * step, y - i * 2, cw, ch), 0.0))
            return out

        gap = M.GAP_S if self.mode == "row" else -int(cw * 0.34)
        span = count * cw + (count - 1) * gap
        if span > inner.width and count > 1:
            step = (inner.width - cw) / (count - 1)
        else:
            step = cw + gap
        total = cw + step * (count - 1)
        if self.align == "left":
            x0 = inner.left
        elif self.align == "right":
            x0 = inner.right - total
        else:
            x0 = inner.left + (inner.width - total) / 2

        out = []
        for i in range(count):
            spin = 0.0
            dy = 0
            if self.mode == "fan" and count > 1:
                t = (i / (count - 1)) * 2 - 1  # -1 .. 1
                spin = -t * min(9.0, 16.0 / math.sqrt(count))
                dy = int(abs(t) ** 2 * min(20, ch * 0.11))
            out.append((pygame.Rect(int(x0 + step * i), y + dy, cw, ch), spin))
        return out

    # -- hit testing -------------------------------------------------------

    def get_card_at(self, pos: tuple[int, int]) -> CardSprite | None:
        for sprite in reversed(self.sprites):
            if sprite.hit(pos):
                return sprite
        return None

    def update_hover(self, pos: tuple[int, int]) -> CardSprite | None:
        top = self.get_card_at(pos)
        for sprite in self.sprites:
            sprite.hovered = sprite is top
        return top

    def clear_hover(self) -> None:
        for sprite in self.sprites:
            sprite.hovered = False

    def update(self, dt: float) -> None:
        for sprite in self.sprites:
            sprite.update(dt)

    def sprite(self, card_id: str) -> CardSprite | None:
        return self._by_id.get(card_id)

    # -- drawing -----------------------------------------------------------

    def draw(self, screen: pygame.Surface) -> None:
        if self.panel:
            pass  # no frosted panel — cards sit on the table
        if self.title:
            T.text(screen, self.title.upper(), (self.rect.left + 12, self.rect.top + 6),
                   T.ui(10, bold=True), T.alpha(C.INK_DIM, 235), shadow=None)
        if not self.sprites and self.empty_hint:
            T.text(screen, self.empty_hint, self.rect.center, T.ui(12, italic=True),
                   T.alpha(C.INK_FAINT, 200), anchor="center", shadow=None)
        # Hovered card last, so its lift and glow sit above its neighbours.
        hovered = None
        for sprite in self.sprites:
            if sprite.hovered:
                hovered = sprite
            else:
                sprite.draw(screen)
        if hovered is not None:
            hovered.draw(screen)


# ---------------------------------------------------------------------------
# Player badge (kept for the opponent rail's compact mode)
# ---------------------------------------------------------------------------


class PlayerBadge:
    """A one-line summary of a seat: leader, name, AP, counts, classes."""

    def __init__(
        self,
        rect: pygame.Rect,
        player_id: str,
        name: str,
        action_points: int,
        is_active: bool,
        leader_def: CardDef | None,
        hero_count: int,
        hand_count: int,
        slain_count: int,
        classes_present: tuple[str, ...],
        *,
        seat_colour: tuple[int, int, int] = C.GOLD,
        is_you: bool = False,
    ) -> None:
        self.rect = pygame.Rect(rect)
        self.player_id = player_id
        self.name = name
        self.action_points = action_points
        self.is_active = is_active
        self.leader_def = leader_def
        self.hero_count = hero_count
        self.hand_count = hand_count
        self.slain_count = slain_count
        self.classes_present = classes_present
        self.seat_colour = seat_colour
        self.is_you = is_you
        self.hovered = False
        self.highlighted = False

    def draw(self, screen: pygame.Surface) -> None:
        rect = self.rect
        rim = C.GOLD if self.highlighted else (
            self.seat_colour if self.is_active else T.alpha(C.GLASS_RIM, 90)
        )
        fill = C.GLASS if self.hovered or self.is_active else C.GLASS_SOFT
        T.glass(screen, rect, radius=M.RADIUS, fill=fill, rim=rim)
        if self.is_active:
            T.round_rect(screen, rect, rim, radius=M.RADIUS, width=2)

        lw = min(46, rect.height - 12)
        lh = int(lw * M.CARD_ASPECT)
        if lh > rect.height - 10:
            lh = rect.height - 10
            lw = int(lh / M.CARD_ASPECT)
        lx, ly = rect.left + 8, rect.centery - lh // 2
        if self.leader_def is not None:
            screen.blit(render_card(self.leader_def, lw, lh), (lx, ly))
        else:
            screen.blit(render_card_back(lw, lh), (lx, ly))

        x = lx + lw + 10
        T.text(screen, self.name, (x, rect.top + 8),
               T.ui(13, bold=True), C.INK_BRIGHT if self.is_active else C.INK,
               max_width=rect.right - x - 40)
        stats = (
            f"{L.ap_label(self.action_points)}  ·  {L.hand_label(self.hand_count)}"
            f"  ·  {L.PARTY} {self.hero_count}"
        )
        T.text(screen, stats, (x, rect.top + 26), T.ui(10), C.INK_DIM, shadow=None,
               max_width=rect.right - x - 12)

        dot_x = x
        for cls in sorted(self.classes_present):
            pygame.draw.circle(screen, T.CLASS_COLOURS.get(cls, C.INK_DIM),
                               (dot_x + 5, rect.top + 46), 5)
            dot_x += 14
        if self.slain_count:
            T.badge(screen, (rect.right - 16, rect.top + 16), 11, str(self.slain_count),
                    bg=C.BLOOD, fnt=T.ui(11, bold=True))


# ---------------------------------------------------------------------------
# Dice readout
# ---------------------------------------------------------------------------


class DiceWidget:
    """Shows the arithmetic of the most recent roll: dice, modifiers, total."""

    def __init__(self, rect: pygame.Rect) -> None:
        self.rect = pygame.Rect(rect)
        self.roll: Roll | None = None
        self.panel = True

    def set_roll(self, roll: Roll | None) -> None:
        self.roll = roll

    def draw(self, screen: pygame.Surface) -> None:
        if not self.roll:
            return
        rect = self.rect
        if self.panel:
            T.glass(screen, rect, radius=M.RADIUS, fill=C.GLASS_DEEP)

        roll = self.roll
        good = roll.band_tag in ("success", "slay")
        accent = C.GOOD if good else (C.BAD if roll.band_tag == "failure" else C.GOLD)

        T.text(screen, str(roll.kind).replace("_", " ").upper(),
               (rect.left + 10, rect.top + 7), T.ui(9, bold=True), C.INK_DIM, shadow=None)

        dice_text = " + ".join(str(d) for d in roll.raw) if roll.raw else "\u2026"
        T.text(screen, dice_text, (rect.left + 10, rect.top + 22), T.display(18), C.INK)

        y = rect.top + 46
        for mod in roll.modifiers[-3:]:
            colour = C.GOOD if mod.amount >= 0 else C.BAD
            T.text(screen, str(mod), (rect.left + 10, y), T.ui(10), colour, shadow=None,
                   max_width=rect.width - 20)
            y += 14

        band = f"  \u2192  {roll.band_tag}" if roll.band_tag else ""
        T.text(screen, f"{roll.total}{band}", (rect.left + 10, rect.bottom - 8),
               T.display(17), accent, anchor="bottomleft")


# ---------------------------------------------------------------------------
# Transient messages
# ---------------------------------------------------------------------------


class Toast:
    """A banner that fades itself out. Queues, so bursts do not stomp."""

    def __init__(self, rect: pygame.Rect) -> None:
        self.rect = pygame.Rect(rect)
        self.message = ""
        self.timer = 0.0
        self.duration = 3.0
        self.colour: tuple[int, int, int] = C.GOLD
        self.icon: str | None = None
        self._queue: list[tuple[str, float, tuple[int, int, int], str | None]] = []
        self._in = 0.0

    def show(
        self,
        message: str,
        duration: float = 3.0,
        *,
        colour: tuple[int, int, int] = C.GOLD,
        icon: str | None = None,
    ) -> None:
        if self.timer > 0.25 and message != self.message:
            self._queue.append((message, duration, colour, icon))
            if len(self._queue) > 6:
                del self._queue[0]
            return
        self.message, self.duration, self.colour, self.icon = message, duration, colour, icon
        self.timer = duration
        self._in = 0.0

    def update(self, dt: float) -> None:
        if self.timer > 0:
            self.timer -= dt
            self._in = min(1.0, self._in + dt * 6)
        elif self._queue:
            message, duration, colour, icon = self._queue.pop(0)
            self.show(message, duration, colour=colour, icon=icon)

    def draw(self, screen: pygame.Surface) -> None:
        if self.timer <= 0 or not self.message:
            return
        fade = min(1.0, self.timer / 0.4) * T.ease_out_cubic(self._in)
        rect = pygame.Rect(self.rect)
        rect.y -= int((1.0 - T.ease_out_cubic(self._in)) * 18)

        layer = T.surface(rect.size)
        local = pygame.Rect(0, 0, rect.width, rect.height)
        T.round_rect(layer, local, C.GLASS_DEEP, radius=M.RADIUS)
        T.round_rect(layer, local, T.alpha(self.colour, 220), radius=M.RADIUS, width=1)
        T.round_rect(layer, pygame.Rect(0, 0, 4, rect.height), self.colour, radius=2)

        x = 16
        if self.icon:
            draw_icon(layer, self.icon, (x + 10, local.centery), 20, self.colour)
            x += 30
        T.text(layer, self.message, (x, local.centery), T.ui(14, bold=True), C.INK_BRIGHT,
               anchor="midleft", max_width=local.width - x - 12, shadow=None)
        layer.set_alpha(int(255 * fade))
        screen.blit(layer, rect.topleft)


class Tooltip:
    """A dark label that follows the pointer. Positions itself onto the screen."""

    def __init__(self) -> None:
        self.text = ""
        self.pos = (0, 0)
        self.visible = False

    def show(self, text: str, pos: tuple[int, int]) -> None:
        self.text, self.pos, self.visible = text, pos, bool(text)

    def hide(self) -> None:
        self.visible = False

    def draw(self, screen: pygame.Surface) -> None:
        if not self.visible or not self.text:
            return
        fnt = T.ui(12)
        lines = T.wrap(self.text, fnt, 280)
        w = max(fnt.size(line)[0] for line in lines) + 20
        h = len(lines) * (fnt.get_linesize() + 1) + 12
        sw, sh = screen.get_size()
        x = min(self.pos[0] + 16, sw - w - 8)
        y = self.pos[1] + 18
        if y + h > sh - 8:
            y = self.pos[1] - h - 12
        rect = pygame.Rect(x, max(4, y), w, h)
        T.drop_shadow(screen, rect, radius=8, spread=10, offset=(0, 3), strength=90)
        T.round_rect(screen, rect, C.GLASS, radius=8)
        T.round_rect(screen, rect, T.alpha(C.GOLD, 140), radius=8, width=1)
        for i, line in enumerate(lines):
            T.text(screen, line, (rect.left + 10, rect.top + 6 + i * (fnt.get_linesize() + 1)),
                   fnt, C.INK, shadow=None)


# ---------------------------------------------------------------------------
# Log feed
# ---------------------------------------------------------------------------


@dataclass
class LogEntry:
    text: str
    colour: tuple[int, int, int] = C.INK_DIM
    icon: str | None = None
    age: float = 0.0


class LogFeed:
    """A scrolling record of what happened. New lines slide in and fade up."""

    def __init__(self, rect: pygame.Rect, *, limit: int = 200) -> None:
        self.rect = pygame.Rect(rect)
        self.entries: list[LogEntry] = []
        self.limit = limit
        self.scroll = 0

    def add(
        self, text: str, *, colour: tuple[int, int, int] = C.INK_DIM, icon: str | None = None
    ) -> None:
        self.entries.append(LogEntry(L.retheme_prompt(text), colour, icon))
        if len(self.entries) > self.limit:
            del self.entries[: len(self.entries) - self.limit]
        self.scroll = 0

    def update(self, dt: float) -> None:
        for entry in self.entries[-12:]:
            entry.age += dt

    def clear(self) -> None:
        self.entries.clear()

    def draw(self, screen: pygame.Surface, *, newest_first: bool = True) -> None:
        fnt = T.ui(16)
        line_h = fnt.get_linesize() + 4
        rows = max(1, self.rect.height // line_h)
        visible = list(reversed(self.entries[-(rows + self.scroll):])) if newest_first else \
            self.entries[-rows:]
        visible = visible[self.scroll:self.scroll + rows] if newest_first else visible

        clip = screen.get_clip()
        screen.set_clip(self.rect)
        for i, entry in enumerate(visible):
            y = self.rect.top + i * line_h
            slide = 0
            fade = 1.0
            if entry.age < 0.35:
                k = T.ease_out_cubic(entry.age / 0.35)
                slide = int((1 - k) * 18)
                fade = k
            x = self.rect.left + slide
            if entry.icon:
                draw_icon(screen, entry.icon, (x + 6, y + line_h // 2 - 1), T.s(16),
                          T.alpha(entry.colour, int(255 * fade))[:3])
                x += T.s(20)
            colour = T.alpha(entry.colour, int(230 * fade * (1.0 - i / (rows * 1.9))))
            T.text(screen, entry.text, (x, y), fnt, colour, shadow=None,
                   max_width=self.rect.right - x - 4)
        screen.set_clip(clip)


# ---------------------------------------------------------------------------
# Scrollable list + text input (the dev console and rules modal need these)
# ---------------------------------------------------------------------------


class ScrollView:
    """A clipped viewport with a wheel-driven offset and a slim scrollbar."""

    def __init__(self, rect: pygame.Rect) -> None:
        self.rect = pygame.Rect(rect)
        self.offset = 0
        self.content_height = 0

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEWHEEL:
            mouse = pygame.mouse.get_pos()
            if self.rect.collidepoint(mouse):
                self.scroll_by(-event.y * 48)
                return True
        return False

    def scroll_by(self, delta: int) -> None:
        limit = max(0, self.content_height - self.rect.height)
        self.offset = max(0, min(limit, self.offset + delta))

    def begin(self, screen: pygame.Surface) -> pygame.Rect | None:
        self._saved_clip = screen.get_clip()
        screen.set_clip(self.rect)
        return self._saved_clip

    def end(self, screen: pygame.Surface) -> None:
        screen.set_clip(getattr(self, "_saved_clip", None))
        limit = max(0, self.content_height - self.rect.height)
        if limit <= 0:
            return
        track = pygame.Rect(self.rect.right - 5, self.rect.top, 3, self.rect.height)
        T.round_rect(screen, track, (255, 255, 255, 22), radius=2)
        frac = self.rect.height / max(1, self.content_height)
        bar_h = max(24, int(self.rect.height * frac))
        bar_y = self.rect.top + int((self.rect.height - bar_h) * (self.offset / limit))
        T.round_rect(screen, pygame.Rect(track.left, bar_y, 3, bar_h),
                     T.alpha(C.GOLD, 170), radius=2)

    @property
    def content_top(self) -> int:
        return self.rect.top - self.offset


class TextField:
    """A single-line input. Used by the dev console's card search."""

    def __init__(
        self,
        rect: pygame.Rect,
        *,
        placeholder: str = "",
        on_change: Callable[[str], None] | None = None,
    ) -> None:
        self.rect = pygame.Rect(rect)
        self.value = ""
        self.placeholder = placeholder
        self.on_change = on_change
        self.focused = False
        self._caret = 0.0

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.focused = self.rect.collidepoint(event.pos)
            return self.focused
        if not self.focused:
            return False
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_BACKSPACE:
                self.value = self.value[:-1]
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE):
                self.focused = False
                return True
            elif event.unicode and event.unicode.isprintable():
                self.value += event.unicode
            else:
                return False
            if self.on_change:
                self.on_change(self.value)
            return True
        return False

    def update(self, dt: float) -> None:
        self._caret = (self._caret + dt) % 1.0

    def draw(self, screen: pygame.Surface) -> None:
        T.inset(screen, self.rect, radius=8, fill=(236, 244, 252, 220))
        rim = C.GOLD if self.focused else T.alpha(C.GLASS_RIM, 110)
        T.round_rect(screen, self.rect, rim, radius=8, width=1)
        fnt = T.ui(13)
        shown = self.value or self.placeholder
        # The well is deliberately light (it is the one bright surface in a dark
        # client), so the ink has to be the dark one. `C.INK` is near-white and
        # was invisible here — typed text simply did not appear.
        colour = C.INK_DARK if self.value else C.CARD_INK_DIM
        T.text(screen, shown, (self.rect.left + 10, self.rect.centery), fnt, colour,
               anchor="midleft", max_width=self.rect.width - 20, shadow=None)
        if self.focused and self._caret < 0.5:
            x = self.rect.left + 10 + min(fnt.size(self.value)[0], self.rect.width - 22)
            pygame.draw.line(screen, C.GOLD_DEEP, (x + 1, self.rect.top + 7),
                             (x + 1, self.rect.bottom - 7), 2)


class SegmentedControl:
    """A row of mutually exclusive options — the dev console's tab bar."""

    def __init__(
        self,
        rect: pygame.Rect,
        options: Sequence[str],
        *,
        index: int = 0,
        on_change: Callable[[int], None] | None = None,
    ) -> None:
        self.rect = pygame.Rect(rect)
        self.options = list(options)
        self.index = index
        self.on_change = on_change
        self.hover_index = -1

    def _slots(self) -> list[pygame.Rect]:
        n = max(1, len(self.options))
        w = self.rect.width / n
        return [
            pygame.Rect(int(self.rect.left + i * w), self.rect.top, int(w) - 2, self.rect.height)
            for i in range(n)
        ]

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEMOTION:
            self.hover_index = next(
                (i for i, slot in enumerate(self._slots()) if slot.collidepoint(event.pos)), -1
            )
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for i, slot in enumerate(self._slots()):
                if slot.collidepoint(event.pos):
                    self.index = i
                    if self.on_change:
                        self.on_change(i)
                    return True
        return False

    def draw(self, screen: pygame.Surface) -> None:
        T.round_rect(screen, self.rect, (16, 14, 30, 200), radius=self.rect.height // 2)
        for i, (slot, label) in enumerate(zip(self._slots(), self.options, strict=False)):
            chosen = i == self.index
            if chosen:
                T.round_rect(screen, slot.inflate(-2, -4), T.alpha(C.GOLD, 220),
                             radius=slot.height // 2)
            elif i == self.hover_index:
                T.round_rect(screen, slot.inflate(-2, -4), (255, 255, 255, 22),
                             radius=slot.height // 2)
            T.text(screen, label, slot.center,
                   T.ui(max(10, slot.height // 2 - 2), bold=True),
                   C.INK_DARK if chosen else C.INK_DIM, anchor="center", shadow=None,
                   max_width=slot.width - 8)


# ---------------------------------------------------------------------------
# Helpers shared by panels
# ---------------------------------------------------------------------------


def card_tuple(
    card_view: Any,
    card_def: Any,
    *,
    face_down: bool = False,
    highlighted: bool = False,
) -> CardTuple:
    """Build a row tuple from a ``CardView`` — the one place that mapping lives."""
    return (
        card_view.id, card_def, face_down,
        bool(getattr(card_view, "tapped", False)), highlighted,
        tuple(getattr(card_view, "attachments", ()) or ()),
    )


def class_chips(
    classes: Sequence[str], origin: tuple[int, int], *, height: int = 18, gap: int = 4
) -> list[Chip]:
    """A chip per class, laid out left to right from ``origin``."""
    out: list[Chip] = []
    x = origin[0]
    for cls in classes:
        label = T.CLASS_SHORT.get(cls, cls[:3].upper())
        icon = card_icon_name("hero", cls)
        w = Chip.width_for(label, icon=icon, height=height)
        out.append(Chip(pygame.Rect(x, origin[1], w, height), label,
                        colour=T.CLASS_COLOURS.get(cls, C.INK_DIM), icon=icon))
        x += w + gap
    return out


def facts_of(card_def: Any) -> Any:
    """Re-exported so panels do not each import the card renderer."""
    return card_facts(card_def)


__all__ = [
    "Button",
    "CardSprite",
    "CardTuple",
    "Chip",
    "DiceWidget",
    "IconButton",
    "LogEntry",
    "LogFeed",
    "PlayerBadge",
    "ScrollView",
    "SegmentedControl",
    "TextField",
    "Toast",
    "Tooltip",
    "ZoneWidget",
    "card_tuple",
    "class_chips",
    "facts_of",
]
