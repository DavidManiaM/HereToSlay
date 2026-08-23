"""Where everything goes. One place that owns the screen's geometry.

The board is arranged the way a player sitting at the table would see it, and
that arrangement is the *specification*, not an accident of the drawing order:

::

    ┌──────────────────────────── top bar ──────────────────────── [i][?] ──┐
    │        │   deck · discard · monster deck        │                     │
    │ active │                                       │   opponents         │
    │ stack  │        M O N S T E R   R O W           │   (next player      │
    │ (left) │                                       │    at the top)      │
    │        ├───────────────────────────────────────┤                     │
    │        │   your leader + party                 │                     │
    ├────────┴───────────────────────────────────────┴─────────────────────┤
    │  dice / AP   │           your hand              │  active effects     │
    └──────────────────────────────────────────────────────────────────────┘

Everything is derived from ``(width, height)`` with proportional weights and
minimum sizes, so the window resizes without any panel doing its own maths.
Rails collapse on narrow windows rather than squeezing the board.

The rect names from the first version of this client (``header_rect``,
``opponents_rect``, ``player_hand_rect`` …) are kept as aliases: they are what
the existing UI tests assert on, and a layout rename is not worth breaking a
regression suite over.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pygame

from here_to_slay.ui.pygame.theme import M

#: Below this width the side rails give up space to the board.
NARROW = 1180
#: Below this width the left rail becomes an overlay instead of a column.
VERY_NARROW = 980

MIN_W = 1024
MIN_H = 640


@dataclass
class LayoutManager:
    """Computes every rect the board needs from the window size."""

    width: int = 1600
    height: int = 900

    # -- bands -------------------------------------------------------------
    topbar_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    board_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    bottom_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))

    # -- top bar contents --------------------------------------------------
    turn_pips_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    info_button_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    log_button_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    menu_button_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))

    # -- rails -------------------------------------------------------------
    left_rail_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    right_rail_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))

    # -- centre column -----------------------------------------------------
    deck_area_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    monster_row_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    party_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    leader_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))

    # -- bottom band -------------------------------------------------------
    dice_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    hand_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    effects_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))

    # -- floating ----------------------------------------------------------
    prompt_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    action_menu_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    toast_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    detail_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    modal_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))

    # -- derived card sizes ------------------------------------------------
    hand_card_w: int = M.CARD_W
    party_card_w: int = M.CARD_W
    monster_card_w: int = M.CARD_W
    deck_card_w: int = 76
    rail_card_w: int = 46

    compact: bool = False
    left_rail_floating: bool = False

    def __post_init__(self) -> None:
        self.rebuild(self.width, self.height)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def rebuild(self, w: int, h: int) -> None:
        self.width = max(MIN_W, int(w))
        self.height = max(MIN_H, int(h))
        w, h = self.width, self.height
        gap = M.GAP

        self.compact = w < NARROW
        self.left_rail_floating = w < VERY_NARROW

        # -- horizontal bands ------------------------------------------
        top_h = max(48, min(64, int(h * 0.062)))
        bottom_h = max(176, min(258, int(h * 0.255)))
        self.topbar_rect = pygame.Rect(0, 0, w, top_h)
        self.bottom_rect = pygame.Rect(0, h - bottom_h, w, bottom_h)
        self.board_rect = pygame.Rect(0, top_h, w, h - top_h - bottom_h)

        self._build_topbar(top_h)
        self._build_rails(gap)
        self._build_centre(gap)
        self._build_bottom(gap)
        self._build_floating(gap)

    def _build_topbar(self, top_h: int) -> None:
        btn = top_h - 16
        y = 8
        right = self.width - M.GAP
        self.menu_button_rect = pygame.Rect(right - btn, y, btn, btn)
        self.log_button_rect = pygame.Rect(right - btn * 2 - 8, y, btn, btn)
        self.info_button_rect = pygame.Rect(right - btn * 3 - 16, y, btn, btn)
        # Turn pips sit between the title block and the buttons, right-aligned
        # so they stay near the eye's resting place on the toolbar.
        pips_w = min(int(self.width * 0.42), 92 * 6)
        self.turn_pips_rect = pygame.Rect(
            self.info_button_rect.left - 16 - pips_w, y, pips_w, btn
        )

    def _build_rails(self, gap: int) -> None:
        board = self.board_rect
        right_w = M.RAIL_R if not self.compact else max(232, int(self.width * 0.2))
        right_w = min(right_w, int(self.width * 0.26))
        left_w = 0 if self.left_rail_floating else (M.RAIL_L if not self.compact else 176)

        # The rail stops at the bottom band: the effects panel lives under it,
        # and a rail that ran to the window edge would be drawn over.
        self.right_rail_rect = pygame.Rect(
            self.width - right_w - gap, board.top + gap // 2, right_w, board.height - gap,
        )
        if self.left_rail_floating:
            # Floating: an overlay strip the scene only shows when it has
            # something to put in it, so a narrow window keeps its board.
            self.left_rail_rect = pygame.Rect(gap, board.top + gap, 200, board.height - gap * 2)
        else:
            self.left_rail_rect = pygame.Rect(
                gap, board.top + gap // 2, left_w, board.height - gap
            )

    def _build_centre(self, gap: int) -> None:
        board = self.board_rect
        left = (gap if self.left_rail_floating else self.left_rail_rect.right) + gap
        right = self.right_rail_rect.left - gap
        centre = pygame.Rect(left, board.top + gap // 2, max(320, right - left), board.height - gap)

        # The board splits into: decks strip, monster row, own party.
        deck_h = max(92, min(132, int(centre.height * 0.26)))
        party_h = max(112, min(210, int(centre.height * 0.40)))
        monster_h = max(120, centre.height - deck_h - party_h - gap * 2)

        # Decks are a compact cluster centred over the monster row, leaving the
        # flanks free for the roll readout and the turn banner.
        deck_w = min(centre.width, 520)
        self.deck_area_rect = pygame.Rect(
            centre.centerx - deck_w // 2, centre.top, deck_w, deck_h
        )
        self.monster_row_rect = pygame.Rect(
            centre.left, self.deck_area_rect.bottom + gap, centre.width, monster_h
        )
        party_top = self.monster_row_rect.bottom + gap
        leader_w = max(88, min(132, int(centre.width * 0.11)))
        self.leader_rect = pygame.Rect(centre.left, party_top, leader_w, party_h)
        self.party_rect = pygame.Rect(
            self.leader_rect.right + gap, party_top,
            centre.width - leader_w - gap, party_h,
        )

        self.deck_card_w = max(52, min(84, int(deck_h * 0.52)))
        self.monster_card_w = max(72, min(150, int(monster_h / M.CARD_ASPECT)))
        self.party_card_w = max(64, min(132, int(party_h / M.CARD_ASPECT)))

    def _build_bottom(self, gap: int) -> None:
        bottom = self.bottom_rect
        dice_w = max(216, min(300, int(self.width * 0.19)))
        fx_w = max(216, min(320, int(self.width * 0.21)))
        if dice_w + fx_w > self.width * 0.62:
            dice_w = fx_w = int(self.width * 0.30)

        inner_h = bottom.height - gap
        self.dice_rect = pygame.Rect(gap, bottom.top + gap // 2, dice_w, inner_h)
        self.effects_rect = pygame.Rect(
            self.width - fx_w - gap, bottom.top + gap // 2, fx_w, inner_h
        )
        self.hand_rect = pygame.Rect(
            self.dice_rect.right + gap, bottom.top + gap // 2,
            max(260, self.effects_rect.left - self.dice_rect.right - gap * 2), inner_h,
        )
        self.hand_card_w = max(72, min(146, int((self.hand_rect.height - 40) / M.CARD_ASPECT)))
        self.rail_card_w = max(34, min(56, int(self.right_rail_rect.width * 0.16)))

    def _build_floating(self, gap: int) -> None:
        w, h = self.width, self.height
        # The prompt banner rides just above the hand, where the eye already is
        # when choosing a card.
        prompt_w = min(int(w * 0.5), 720)
        prompt_h = 46
        self.prompt_rect = pygame.Rect(
            (w - prompt_w) // 2, self.bottom_rect.top - prompt_h - gap, prompt_w, prompt_h
        )
        # The action menu floats over the board's left flank: beside the
        # Monster row (whose cards are centred), below the deck cluster, and
        # clear of the hand it is talking about.
        menu_w = min(360, max(268, int(w * 0.23)))
        menu_top = self.deck_area_rect.bottom + gap
        available = self.board_rect.bottom - menu_top - gap
        menu_h = min(available, 460)
        self.action_menu_rect = pygame.Rect(
            self.monster_row_rect.left + gap,
            menu_top + max(0, (available - menu_h) // 2),
            menu_w, menu_h,
        )
        toast_w = min(560, int(w * 0.44))
        self.toast_rect = pygame.Rect(
            (w - toast_w) // 2, self.topbar_rect.bottom + gap, toast_w, 46
        )
        detail_w = min(340, max(240, int(w * 0.2)))
        self.detail_rect = pygame.Rect(0, 0, detail_w, int(detail_w * M.CARD_ASPECT) + 120)
        modal_w = min(1080, int(w * 0.8))
        modal_h = min(840, int(h * 0.86))
        self.modal_rect = pygame.Rect((w - modal_w) // 2, (h - modal_h) // 2, modal_w, modal_h)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def card_box(self, width: int) -> tuple[int, int]:
        return width, round(width * M.CARD_ASPECT)

    def detail_at(self, anchor: pygame.Rect) -> pygame.Rect:
        """Place the hover-detail card near ``anchor`` without leaving the screen."""
        rect = pygame.Rect(self.detail_rect)
        rect.centery = anchor.centery
        rect.left = anchor.right + M.GAP
        if rect.right > self.width - M.GAP:
            rect.right = anchor.left - M.GAP
        rect.left = max(M.GAP, min(rect.left, self.width - rect.width - M.GAP))
        rect.top = max(self.topbar_rect.bottom + M.GAP_S,
                       min(rect.top, self.height - rect.height - M.GAP_S))
        return rect

    def centre_of(self, rect: pygame.Rect) -> tuple[int, int]:
        return rect.center

    def as_dict(self) -> dict[str, tuple[int, int, int, int]]:
        """Every named region — the dev console's layout inspector reads this."""
        return {
            name: tuple(value)
            for name, value in vars(self).items()
            if isinstance(value, pygame.Rect)
        }


__all__ = ["MIN_H", "MIN_W", "NARROW", "VERY_NARROW", "LayoutManager"]
