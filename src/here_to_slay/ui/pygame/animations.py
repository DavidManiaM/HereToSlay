"""Cosmetic animation queue for the PyGame UI.

Animations (card slides, dice tumble, modifier pop-ins) are purely visual
and never gate or delay the engine.
"""

from __future__ import annotations

import random
from typing import Any

import pygame

from here_to_slay.ui.pygame.card_renderer import render_card, render_card_back
from here_to_slay.ui.pygame.colors import (
    DICE_BG,
    DICE_FG,
    HIGHLIGHT,
    get_font,
)


class Animation:
    """Base class for visual animations."""

    def __init__(self, duration: float) -> None:
        self.duration = max(0.01, duration)
        self.elapsed: float = 0.0

    @property
    def progress(self) -> float:
        """Normalized progress in [0.0, 1.0]."""
        return min(1.0, self.elapsed / self.duration)

    @property
    def finished(self) -> bool:
        return self.elapsed >= self.duration

    def update(self, dt: float) -> bool:
        """Advance time. Return True when finished."""
        self.elapsed += dt
        return self.finished

    def draw(self, screen: pygame.Surface) -> None:
        """Render the animation frame."""
        pass


class CardMoveAnimation(Animation):
    """Card sliding from start_pos to end_pos with ease-out."""

    def __init__(
        self,
        card_def: Any,
        start_pos: tuple[int, int],
        end_pos: tuple[int, int],
        size: tuple[int, int] = (100, 140),
        duration: float = 0.35,
        face_down: bool = False,
    ) -> None:
        super().__init__(duration)
        self.card_def = card_def
        self.start_pos = start_pos
        self.end_pos = end_pos
        self.size = size
        self.face_down = face_down
        w, h = size
        self.surf = (
            render_card_back(w, h)
            if face_down or card_def is None
            else render_card(card_def, w, h)
        )

    def draw(self, screen: pygame.Surface) -> None:
        p = self.progress
        # Ease-out cubic: 1 - (1-p)^3
        t = 1.0 - (1.0 - p) ** 3
        x = int(self.start_pos[0] + (self.end_pos[0] - self.start_pos[0]) * t)
        y = int(self.start_pos[1] + (self.end_pos[1] - self.start_pos[1]) * t)
        screen.blit(self.surf, (x, y))


class DiceRollAnimation(Animation):
    """Dice tumbling on screen before settling on final outcome."""

    def __init__(
        self,
        final_values: tuple[int, ...],
        rect: pygame.Rect,
        duration: float = 0.6,
    ) -> None:
        super().__init__(duration)
        self.final_values = final_values
        self.rect = rect
        self.current_values = list(final_values)
        self._tumble_timer = 0.0

    def update(self, dt: float) -> bool:
        finished = super().update(dt)
        self._tumble_timer += dt
        if not finished and self._tumble_timer >= 0.06:
            self._tumble_timer = 0.0
            self.current_values = [random.randint(1, 6) for _ in self.final_values]
        elif finished:
            self.current_values = list(self.final_values)
        return finished

    def draw(self, screen: pygame.Surface) -> None:
        if not self.final_values:
            return
        n = len(self.current_values)
        die_size = min(50, (self.rect.width - 20) // max(1, n))
        start_x = self.rect.centerx - (n * (die_size + 10) - 10) // 2
        y = self.rect.centery - die_size // 2

        font = get_font(max(14, die_size // 2), bold=True)
        for i, val in enumerate(self.current_values):
            x = start_x + i * (die_size + 10)
            die_rect = pygame.Rect(x, y, die_size, die_size)
            pygame.draw.rect(screen, DICE_BG, die_rect, border_radius=8)
            pygame.draw.rect(screen, DICE_FG, die_rect, 2, border_radius=8)

            txt = font.render(str(val), True, DICE_FG)
            tx = x + (die_size - txt.get_width()) // 2
            ty = y + (die_size - txt.get_height()) // 2
            screen.blit(txt, (tx, ty))


class ModifierPopAnimation(Animation):
    """Floating "+2" or "-1" text floating upwards and fading out."""

    def __init__(
        self,
        text: str,
        pos: tuple[int, int],
        colour: tuple[int, int, int] = HIGHLIGHT,
        duration: float = 0.8,
    ) -> None:
        super().__init__(duration)
        self.text = text
        self.pos = pos
        self.colour = colour

    def draw(self, screen: pygame.Surface) -> None:
        p = self.progress
        alpha = int(255 * (1.0 - p))
        y_offset = int(30 * p)
        x, y = self.pos[0], self.pos[1] - y_offset

        font = get_font(22, bold=True)
        txt = font.render(self.text, True, self.colour)
        txt.set_alpha(alpha)
        screen.blit(txt, (x, y))


class FlashAnimation(Animation):
    """Border flash / highlight pulse around a rect."""

    def __init__(
        self,
        rect: pygame.Rect,
        colour: tuple[int, int, int] = HIGHLIGHT,
        duration: float = 0.4,
    ) -> None:
        super().__init__(duration)
        self.rect = rect
        self.colour = colour

    def draw(self, screen: pygame.Surface) -> None:
        p = self.progress
        alpha = int(180 * (1.0 - abs(p - 0.5) * 2))
        flash_surf = pygame.Surface(
            (self.rect.width, self.rect.height), pygame.SRCALPHA
        )
        pygame.draw.rect(
            flash_surf,
            (*self.colour, alpha),
            (0, 0, self.rect.width, self.rect.height),
            3,
            border_radius=8,
        )
        screen.blit(flash_surf, self.rect.topleft)


class AnimationManager:
    """Queue and manager for all active visual animations."""

    def __init__(self) -> None:
        self.animations: list[Animation] = []

    def add(self, anim: Animation) -> None:
        self.animations.append(anim)

    def update(self, dt: float) -> None:
        for anim in self.animations:
            anim.update(dt)
        self.animations = [a for a in self.animations if not a.finished]

    def draw(self, screen: pygame.Surface) -> None:
        for anim in self.animations:
            anim.draw(screen)

    def clear(self) -> None:
        self.animations.clear()


__all__ = [
    "Animation",
    "AnimationManager",
    "CardMoveAnimation",
    "DiceRollAnimation",
    "FlashAnimation",
    "ModifierPopAnimation",
]
