"""Interactive and visual UI widgets for the PyGame client.

Provides reusable components:
- CardSprite: Renderable, clickable card
- ZoneWidget: Container arranging cards horizontally
- Button: Interactive styled button
- Toast: Floating temporary status banner
- DiceWidget: Visual display of active / recent rolls and modifiers
- PlayerBadge: Compact summary panel for opponents
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pygame

from here_to_slay.ui.pygame.card_renderer import (
    CARD_H,
    CARD_W,
    render_card,
)
from here_to_slay.ui.pygame.colors import (
    BG_PANEL,
    BG_PANEL_HOVER,
    BORDER_ACTIVE,
    BORDER_INACTIVE,
    BUTTON_BG,
    BUTTON_BORDER,
    BUTTON_HOVER,
    BUTTON_TEXT,
    CLASS_COLOURS,
    DICE_MODIFIER_NEG,
    DICE_MODIFIER_POS,
    HIGHLIGHT,
    TEXT,
    TEXT_BRIGHT,
    TEXT_DIM,
    TOAST_BG,
    TOAST_TEXT,
    WIN_GOLD,
    get_font,
)

if TYPE_CHECKING:
    from here_to_slay.content.schema import CardDef
    from here_to_slay.core.rolls import Roll


class Button:
    """A clickable, styled UI button."""

    def __init__(
        self,
        rect: pygame.Rect,
        label: str,
        on_click: Callable[[], None] | None = None,
        *,
        enabled: bool = True,
        bg_colour: tuple[int, int, int] = BUTTON_BG,
        hover_colour: tuple[int, int, int] = BUTTON_HOVER,
        text_colour: tuple[int, int, int] = BUTTON_TEXT,
        border_colour: tuple[int, int, int] = BUTTON_BORDER,
    ) -> None:
        self.rect = rect
        self.label = label
        self.on_click = on_click
        self.enabled = enabled
        self.hovered = False
        self.bg_colour = bg_colour
        self.hover_colour = hover_colour
        self.text_colour = text_colour
        self.border_colour = border_colour

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Handle mouse movement and clicks. Return True if clicked."""
        if not self.enabled:
            self.hovered = False
            return False

        if event.type == pygame.MOUSEMOTION:
            self.hovered = self.rect.collidepoint(event.pos)
        elif (
            event.type == pygame.MOUSEBUTTONDOWN
            and event.button == 1
            and self.rect.collidepoint(event.pos)
        ):
            if self.on_click:
                self.on_click()
            return True
        return False

    def draw(self, screen: pygame.Surface) -> None:
        bg = self.hover_colour if (self.hovered and self.enabled) else self.bg_colour
        if not self.enabled:
            # Dimmed
            bg = (max(20, bg[0] // 2), max(20, bg[1] // 2), max(20, bg[2] // 2))

        pygame.draw.rect(screen, bg, self.rect, border_radius=6)
        pygame.draw.rect(screen, self.border_colour, self.rect, 2, border_radius=6)

        font = get_font(max(11, min(16, self.rect.height // 2)), bold=True)
        txt_col = self.text_colour if self.enabled else TEXT_DIM
        txt = font.render(self.label, True, txt_col)
        tx = self.rect.centerx - txt.get_width() // 2
        ty = self.rect.centery - txt.get_height() // 2
        screen.blit(txt, (tx, ty))


class CardSprite:
    """A card visual instance on the board with hit testing and state."""

    def __init__(
        self,
        card_id: str,
        card_def: CardDef | None,
        rect: pygame.Rect,
        *,
        face_down: bool = False,
        tapped: bool = False,
        highlighted: bool = False,
        attachments: tuple[str, ...] = (),
    ) -> None:
        self.card_id = card_id
        self.card_def = card_def
        self.rect = rect
        self.face_down = face_down
        self.tapped = tapped
        self.highlighted = highlighted
        self.attachments = attachments
        self.hovered = False

    def update_hover(self, pos: tuple[int, int]) -> None:
        self.hovered = self.rect.collidepoint(pos)

    def draw(self, screen: pygame.Surface) -> None:
        w, h = self.rect.width, self.rect.height
        is_highlight = self.highlighted or (self.hovered and not self.face_down)

        surf = render_card(
            self.card_def,
            w,
            h,
            tapped=self.tapped,
            highlighted=is_highlight,
            face_down=self.face_down,
        )
        screen.blit(surf, self.rect.topleft)

        # Draw attachments badge if any
        if self.attachments:
            att_count = len(self.attachments)
            att_rect = pygame.Rect(self.rect.right - 22, self.rect.top + 2, 20, 20)
            pygame.draw.circle(screen, (220, 180, 40), att_rect.center, 10)
            pygame.draw.circle(screen, (50, 40, 10), att_rect.center, 10, 1)
            font = get_font(12, bold=True)
            txt = font.render(f"+{att_count}", True, (20, 20, 20))
            att_x = att_rect.centerx - txt.get_width() // 2
            att_y = att_rect.centery - txt.get_height() // 2
            screen.blit(txt, (att_x, att_y))


class ZoneWidget:
    """A container arranging cards in a row with responsive spacing."""

    def __init__(
        self,
        rect: pygame.Rect,
        title: str = "",
        *,
        card_size: tuple[int, int] = (CARD_W, CARD_H),
        max_overlap: bool = True,
    ) -> None:
        self.rect = rect
        self.title = title
        self.card_size = card_size
        self.max_overlap = max_overlap
        self.sprites: list[CardSprite] = []

    def set_cards(
        self,
        cards_data: list[tuple[str, Any, bool, bool, bool, tuple[str, ...]]],
    ) -> None:
        """Populate zone with list of (id, def, face_down, tapped, highlighted, attachments)."""
        self.sprites.clear()
        if not cards_data:
            return

        cw, ch = self.card_size
        count = len(cards_data)
        avail_w = self.rect.width - 20

        # Compute spacing
        if count == 1:
            step_x = 0
            start_x = self.rect.left + (self.rect.width - cw) // 2
        else:
            total_needed = count * cw + (count - 1) * 10
            if total_needed <= avail_w:
                step_x = cw + 10
                start_x = self.rect.left + 10
            else:
                step_x = max(24, (avail_w - cw) // (count - 1))
                start_x = self.rect.left + 10

        y = self.rect.top + (self.rect.height - ch) // 2

        for i, (cid, cdef, face_down, tapped, highlighted, atts) in enumerate(cards_data):
            x = start_x + i * step_x
            card_rect = pygame.Rect(x, y, cw, ch)
            sprite = CardSprite(
                card_id=cid,
                card_def=cdef,
                rect=card_rect,
                face_down=face_down,
                tapped=tapped,
                highlighted=highlighted,
                attachments=atts,
            )
            self.sprites.append(sprite)

    def get_card_at(self, pos: tuple[int, int]) -> CardSprite | None:
        # Check right-to-left so top overlapping cards get picked first
        for sprite in reversed(self.sprites):
            if sprite.rect.collidepoint(pos):
                return sprite
        return None

    def update_hover(self, pos: tuple[int, int]) -> None:
        top_hit = self.get_card_at(pos)
        for sprite in self.sprites:
            sprite.hovered = sprite is top_hit

    def draw(self, screen: pygame.Surface) -> None:
        # Optional panel background
        pygame.draw.rect(screen, BG_PANEL, self.rect, border_radius=8)
        pygame.draw.rect(screen, BORDER_INACTIVE, self.rect, 1, border_radius=8)

        if self.title:
            font = get_font(12, bold=True)
            txt = font.render(self.title, True, TEXT_DIM)
            screen.blit(txt, (self.rect.left + 10, self.rect.top + 4))

        for sprite in self.sprites:
            sprite.draw(screen)


class PlayerBadge:
    """Opponent summary badge displayed along the top."""

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
    ) -> None:
        self.rect = rect
        self.player_id = player_id
        self.name = name
        self.action_points = action_points
        self.is_active = is_active
        self.leader_def = leader_def
        self.hero_count = hero_count
        self.hand_count = hand_count
        self.slain_count = slain_count
        self.classes_present = classes_present
        self.hovered = False
        self.highlighted = False

    def draw(self, screen: pygame.Surface) -> None:
        bg = BG_PANEL_HOVER if self.hovered else BG_PANEL
        border_col = (
            HIGHLIGHT
            if self.highlighted
            else (BORDER_ACTIVE if self.is_active else BORDER_INACTIVE)
        )
        border_w = 3 if (self.is_active or self.highlighted) else 1

        pygame.draw.rect(screen, bg, self.rect, border_radius=8)
        pygame.draw.rect(screen, border_col, self.rect, border_w, border_radius=8)

        # Leader miniature
        lw, lh = 50, 70
        lx = self.rect.left + 8
        ly = self.rect.top + (self.rect.height - lh) // 2
        if self.leader_def:
            surf = render_card(self.leader_def, lw, lh)
            screen.blit(surf, (lx, ly))

        # Details text
        font_name = get_font(13, bold=True)
        font_stats = get_font(11)

        name_surf = font_name.render(self.name, True, TEXT_BRIGHT if self.is_active else TEXT)
        tx = lx + lw + 10
        ty = self.rect.top + 8
        screen.blit(name_surf, (tx, ty))

        # Stats
        stats_str = (
            f"AP: {self.action_points}  |  Hand: {self.hand_count}  |  "
            f"Heroes: {self.hero_count}/6  |  Slain: {self.slain_count}/3"
        )
        stats_surf = font_stats.render(stats_str, True, TEXT_DIM)
        screen.blit(stats_surf, (tx, ty + 20))

        # Class icons / dots
        dot_x = tx
        dot_y = ty + 42
        for cls in sorted(self.classes_present):
            col = CLASS_COLOURS.get(cls, (180, 180, 180))
            pygame.draw.circle(screen, col, (dot_x + 6, dot_y + 6), 5)
            dot_x += 16


class DiceWidget:
    """Displays recently resolved or in-flight rolls and modifiers."""

    def __init__(self, rect: pygame.Rect) -> None:
        self.rect = rect
        self.roll: Roll | None = None

    def set_roll(self, roll: Roll | None) -> None:
        self.roll = roll

    def draw(self, screen: pygame.Surface) -> None:
        if not self.roll:
            return

        pygame.draw.rect(screen, BG_PANEL, self.rect, border_radius=8)
        pygame.draw.rect(screen, BORDER_ACTIVE, self.rect, 2, border_radius=8)

        font_title = get_font(12, bold=True)
        title = f"Roll: {self.roll.kind.title()}"
        ts = font_title.render(title, True, TEXT_DIM)
        screen.blit(ts, (self.rect.left + 10, self.rect.top + 6))

        # Raw dice
        dice_str = " + ".join(str(d) for d in self.roll.raw) if self.roll.raw else "..."
        dice_font = get_font(20, bold=True)
        ds = dice_font.render(dice_str, True, TEXT_BRIGHT)
        screen.blit(ds, (self.rect.left + 10, self.rect.top + 28))

        # Modifiers & Total
        mod_y = self.rect.top + 58
        font_mods = get_font(11)
        for mod in self.roll.modifiers:
            col = DICE_MODIFIER_POS if mod.amount >= 0 else DICE_MODIFIER_NEG
            ms = font_mods.render(str(mod), True, col)
            screen.blit(ms, (self.rect.left + 10, mod_y))
            mod_y += 16

        # Total & band
        total_font = get_font(16, bold=True)
        band_str = f" → {self.roll.band_tag}" if self.roll.band_tag else ""
        tot_str = f"Total: {self.roll.total}{band_str}"
        tot_col = WIN_GOLD if self.roll.band_tag in ("success", "slay") else TEXT_BRIGHT
        tot_s = total_font.render(tot_str, True, tot_col)
        screen.blit(tot_s, (self.rect.left + 10, self.rect.bottom - 26))


class Toast:
    """Temporary status banner message with auto-fade."""

    def __init__(self, rect: pygame.Rect) -> None:
        self.rect = rect
        self.message: str = ""
        self.timer: float = 0.0
        self.duration: float = 3.0

    def show(self, message: str, duration: float = 3.0) -> None:
        self.message = message
        self.duration = duration
        self.timer = duration

    def update(self, dt: float) -> None:
        if self.timer > 0:
            self.timer -= dt

    def draw(self, screen: pygame.Surface) -> None:
        if self.timer <= 0 or not self.message:
            return

        alpha = min(255, int(255 * (self.timer / 0.5))) if self.timer < 0.5 else 230
        surf = pygame.Surface((self.rect.width, self.rect.height), pygame.SRCALPHA)
        pygame.draw.rect(
            surf,
            (*TOAST_BG[:3], alpha),
            (0, 0, self.rect.width, self.rect.height),
            border_radius=8,
        )
        pygame.draw.rect(
            surf,
            (*BORDER_ACTIVE[:3], alpha),
            (0, 0, self.rect.width, self.rect.height),
            2,
            border_radius=8,
        )

        font = get_font(14, bold=True)
        txt = font.render(self.message, True, TOAST_TEXT)
        tx = (self.rect.width - txt.get_width()) // 2
        ty = (self.rect.height - txt.get_height()) // 2
        surf.blit(txt, (tx, ty))

        screen.blit(surf, self.rect.topleft)


__all__ = [
    "Button",
    "CardSprite",
    "DiceWidget",
    "PlayerBadge",
    "Toast",
    "ZoneWidget",
]
