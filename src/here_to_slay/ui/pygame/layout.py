"""Resolution-independent anchored layout for the PyGame UI.

Calculates pixel rects from proportional coordinates so resizing the window
automatically updates all zones, card slots, and panels.
"""

from __future__ import annotations

from dataclasses import dataclass

import pygame


@dataclass
class LayoutManager:
    """Manages screen regions for the main game view."""

    width: int = 1280
    height: int = 800

    header_rect: pygame.Rect = None  # type: ignore[assignment]
    opponents_rect: pygame.Rect = None  # type: ignore[assignment]
    monster_row_rect: pygame.Rect = None  # type: ignore[assignment]
    shared_decks_rect: pygame.Rect = None  # type: ignore[assignment]
    player_leader_rect: pygame.Rect = None  # type: ignore[assignment]
    player_party_rect: pygame.Rect = None  # type: ignore[assignment]
    player_hand_rect: pygame.Rect = None  # type: ignore[assignment]
    prompt_rect: pygame.Rect = None  # type: ignore[assignment]
    action_menu_rect: pygame.Rect = None  # type: ignore[assignment]
    dice_rect: pygame.Rect = None  # type: ignore[assignment]
    toast_rect: pygame.Rect = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.rebuild(self.width, self.height)

    def rebuild(self, w: int, h: int) -> None:
        """Recompute all bounding boxes for screen dimensions (w, h)."""
        self.width = max(800, w)
        self.height = max(600, h)
        w, h = self.width, self.height

        # 1. Header (top 40px)
        header_h = max(36, int(h * 0.05))
        self.header_rect = pygame.Rect(0, 0, w, header_h)

        # 2. Opponents Strip (under header, height ~110px)
        opp_h = max(90, int(h * 0.14))
        self.opponents_rect = pygame.Rect(10, header_h + 5, w - 20, opp_h)

        # 3. Center Section (Monsters & Decks)
        center_y = header_h + opp_h + 10
        center_h = max(160, int(h * 0.26))

        # Left 72% for Monster row, right 28% for shared decks
        monster_w = int((w - 30) * 0.72)
        decks_w = (w - 30) - monster_w
        self.monster_row_rect = pygame.Rect(10, center_y, monster_w, center_h)
        self.shared_decks_rect = pygame.Rect(10 + monster_w + 10, center_y, decks_w, center_h)

        # 4. Player Party & Leader Row
        party_y = center_y + center_h + 10
        party_h = max(150, int(h * 0.24))

        leader_w = max(100, int(w * 0.11))
        self.player_leader_rect = pygame.Rect(10, party_y, leader_w, party_h)
        self.player_party_rect = pygame.Rect(
            10 + leader_w + 10, party_y, w - 30 - leader_w, party_h
        )

        # 5. Hand Area (Bottom)
        hand_y = party_y + party_h + 10
        hand_h = h - hand_y - 10
        self.player_hand_rect = pygame.Rect(10, hand_y, w - 20, hand_h)

        # 6. Action / Prompt Overlay Area (middle-bottom center float)
        menu_w = min(500, int(w * 0.45))
        menu_h = min(280, int(h * 0.40))
        self.action_menu_rect = pygame.Rect(
            (w - menu_w) // 2, (h - menu_h) // 2, menu_w, menu_h
        )

        prompt_w = min(600, int(w * 0.55))
        prompt_h = 44
        self.prompt_rect = pygame.Rect(
            (w - prompt_w) // 2, self.action_menu_rect.top - prompt_h - 8, prompt_w, prompt_h
        )

        # 7. Dice Widget Area (floating near center right)
        dice_w = min(240, int(w * 0.22))
        dice_h = min(160, int(h * 0.22))
        self.dice_rect = pygame.Rect(
            w - dice_w - 20, center_y + 10, dice_w, dice_h
        )

        # 8. Toast Area (top center)
        toast_w = min(500, int(w * 0.45))
        toast_h = 50
        self.toast_rect = pygame.Rect((w - toast_w) // 2, header_h + 10, toast_w, toast_h)


__all__ = ["LayoutManager"]
