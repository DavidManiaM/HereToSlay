"""Table geometry: you at the south, opponents on a plus/hex ring.

The board is one oval table, not a dashboard of panels. Named rects still exist
as *role anchors* (hand, monsters, decks, chrome) so hit-tests and animations
have somewhere to point — they are not a chrome grid the player is meant to
read as UI chrome.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pygame

from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.theme import M

MIN_W = 1024
MIN_H = 640
NARROW = 1180


def _lerp_rect(a: pygame.Rect, b: pygame.Rect, t: float) -> pygame.Rect:
    return pygame.Rect(
        int(round(a.x + (b.x - a.x) * t)),
        int(round(a.y + (b.y - a.y) * t)),
        max(0, int(round(a.w + (b.w - a.w) * t))),
        max(0, int(round(a.h + (b.h - a.h) * t))),
    )


@dataclass(slots=True)
class SeatAnchor:
    """One chair around the table.

    ``angle`` is radians in screen space: 0 = east, π/2 = south (local),
    π = west, 3π/2 = north. Local player is always south.
    """

    index: int
    angle: float
    is_local: bool
    centre: tuple[int, int]
    rect: pygame.Rect
    party_rect: pygame.Rect
    card_w: int


@dataclass
class LayoutManager:
    """Computes seat anchors and role rects from the window size."""

    width: int = 1920
    height: int = 1080
    player_count: int = 4

    board_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    bottom_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    table_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))

    #: The three chrome buttons live in one small top-right cluster now that
    #: the full-width bar they used to sit in is gone.
    corner_buttons_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    info_button_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    log_button_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    menu_button_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))

    # Role anchors (not a rail HUD)
    left_rail_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    right_rail_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    deck_area_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    monster_row_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    party_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    leader_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    dice_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    hand_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    effects_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))

    prompt_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    action_menu_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    toast_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    detail_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    modal_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    turn_chip_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    action_bar_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    #: Recent-events feed, stacked just above the dice / np.random panel.
    log_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))

    hand_card_w: int = M.CARD_W
    party_card_w: int = M.CARD_W
    monster_card_w: int = M.CARD_W
    deck_card_w: int = 76
    rail_card_w: int = 72
    seat_card_w: int = 72

    seats: list[SeatAnchor] = field(default_factory=list)
    compact: bool = False
    left_rail_floating: bool = True
    #: Active camera key after :meth:`apply_camera` (``local`` / ``centre`` / player id).
    camera_key: str = "local"
    #: Large stage used by the focused opponent camera.
    focus_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))
    camera_strip_rect: pygame.Rect = field(default_factory=lambda: pygame.Rect(0, 0, 0, 0))

    def __post_init__(self) -> None:
        self.rebuild(self.width, self.height, player_count=self.player_count)

    def rebuild(
        self, w: int, h: int, *, player_count: int | None = None
    ) -> None:
        self.width = max(MIN_W, int(w))
        self.height = max(MIN_H, int(h))
        if player_count is not None:
            self.player_count = max(2, min(6, int(player_count)))
        w, h = self.width, self.height
        gap = T.s(M.GAP)
        self.compact = w < NARROW

        hand_h = max(168, min(230, int(h * 0.24)))
        self.bottom_rect = pygame.Rect(0, h - hand_h, w, hand_h)
        # No top bar: the board owns the window from the very first row.
        self.board_rect = pygame.Rect(0, 0, w, h - hand_h)

        self._build_corner_buttons()
        self._build_camera_strip()
        self._build_table(gap)
        self._build_centre(gap)
        self._build_local(gap)
        self._build_seats(gap)
        self._build_floating(gap)

        self.right_rail_rect = pygame.Rect(w - 8, self.board_rect.top, 8, self.board_rect.height)
        self.left_rail_rect = pygame.Rect(gap, self.chrome_top, 200,
                                          max(120, self.board_rect.height // 2))
        self.left_rail_floating = True
        self.focus_rect = pygame.Rect(self.table_rect)
        # Re-apply last camera so resize keeps the framing.
        self.apply_camera(self.camera_key)

    def apply_camera(self, key: str) -> None:
        """Reframe role rects for the active camera. Call after rebuild or on cycle."""
        self.camera_key = key or "local"
        gap = T.s(M.GAP)
        w, h = self.width, self.height
        board = self.board_rect
        top = max(board.top + gap, self.chrome_top)
        bottom = self.bottom_rect.top - gap

        if self.camera_key == "local":
            # Angled “your seat”: party mid-low, hand large in foreground,
            # besties in the middle of the table with decks just above them.
            hand_h = max(200, min(280, int(h * 0.30)))
            self.bottom_rect = pygame.Rect(0, h - hand_h, w, hand_h)
            self._pack_side_chrome(gap, hand_h=hand_h)

            # Besties sit mid-table; decks sit just above them (clear of the
            # camera strip / whose-turn labels).
            table = self.table_rect
            centre_w = min(w - 160, max(420, int(table.width * 0.55)))
            monster_h = max(120, min(170, int(h * 0.18)))
            deck_h = 72
            # Prefer the geometric middle of the table, then clamp so decks fit
            # under the chrome and party still fits below.
            ideal_monster_top = table.centery - monster_h // 2 - T.s(10)
            min_monster_top = self.chrome_top + T.s(56) + deck_h + gap
            monster_top = max(min_monster_top, ideal_monster_top)
            self.monster_row_rect = pygame.Rect(
                w // 2 - centre_w // 2, monster_top, centre_w, monster_h,
            )
            self.monster_card_w = max(64, min(96, int(monster_h / M.CARD_ASPECT)))
            self.deck_area_rect = pygame.Rect(
                w // 2 - 220,
                self.monster_row_rect.top - gap - deck_h,
                440,
                deck_h,
            )
            self.deck_card_w = 52

            party_h = max(140, min(200, int(h * 0.24)))
            party_w = min(
                self.hand_rect.width,
                max(400, int(w * 0.55)),
            )
            party_top = self.hand_rect.top - party_h - 12
            # Stay below the besties / monster strip; never climb into it.
            party_top = max(self.monster_row_rect.bottom + gap, party_top)
            if party_top + party_h > self.hand_rect.top - gap:
                party_h = max(100, self.hand_rect.top - gap - party_top)
            leader_w = max(110, min(160, int(party_w * 0.15)))
            party_left = self.hand_rect.centerx - party_w // 2
            self.leader_rect = pygame.Rect(party_left, party_top, leader_w, party_h)
            self.party_rect = pygame.Rect(
                self.leader_rect.right + gap, party_top,
                party_w - leader_w - gap, party_h,
            )
            self.party_card_w = max(84, min(132, int(party_h / M.CARD_ASPECT)))

            self.focus_rect = pygame.Rect(0, 0, 0, 0)
            self._place_opponent_wedges(gap, scale=1.0)

        elif self.camera_key == "centre":
            hand_h = max(120, min(150, int(h * 0.16)))
            self.bottom_rect = pygame.Rect(0, h - hand_h, w, hand_h)
            self._pack_side_chrome(gap, hand_h=hand_h, compact=True)

            centre_w = min(w - 80, max(640, int(w * 0.78)))
            monster_h = max(220, min(360, int(h * 0.42)))
            self.deck_area_rect = pygame.Rect(w // 2 - 280, top, 560, 90)
            self.monster_row_rect = pygame.Rect(
                w // 2 - centre_w // 2, self.deck_area_rect.bottom + gap,
                centre_w, monster_h,
            )
            self.monster_card_w = max(106, min(176, int(monster_h / M.CARD_ASPECT)))
            self.deck_card_w = 72
            party_h = 88
            party_w = min(self.hand_rect.width, 520)
            self.leader_rect = pygame.Rect(
                self.hand_rect.centerx - party_w // 2,
                self.hand_rect.top - party_h - 8, 80, party_h,
            )
            self.party_rect = pygame.Rect(
                self.leader_rect.right + 8, self.leader_rect.top,
                party_w - 88, party_h,
            )
            self.party_card_w = 56
            self.focus_rect = pygame.Rect(0, 0, 0, 0)
            self._place_opponent_wedges(gap, scale=0.78)

        else:
            hand_h = max(100, min(130, int(h * 0.14)))
            self.bottom_rect = pygame.Rect(0, h - hand_h, w, hand_h)
            self._pack_side_chrome(gap, hand_h=hand_h, compact=True)

            focus_h = max(280, min(480, bottom - top - 40))
            focus_w = min(w - 80, max(700, int(w * 0.82)))
            self.focus_rect = pygame.Rect(
                w // 2 - focus_w // 2, top + 40, focus_w, focus_h
            )
            self.seat_card_w = max(97, min(158, int((focus_h - 60) / M.CARD_ASPECT)))
            self.rail_card_w = self.seat_card_w
            self.monster_row_rect = pygame.Rect(gap, top + 8, 200, 70)
            self.monster_card_w = 44
            self.deck_area_rect = pygame.Rect(w - 220, top + 8, 200, 60)
            self.deck_card_w = 40
            self.leader_rect = pygame.Rect(gap, self.bottom_rect.top - 70, 60, 64)
            self.party_rect = pygame.Rect(self.leader_rect.right + 4, self.leader_rect.top, 200, 64)
            self.party_card_w = 44
            self._place_opponent_wedges(gap, scale=0.7, focused_key=self.camera_key)

        self.action_menu_rect = pygame.Rect(
            self.action_bar_rect.left,
            max(self.chrome_top, self.effects_rect.top - T.s(200)),
            self.action_bar_rect.width,
            T.s(180),
        )
        self.prompt_rect = pygame.Rect(
            int(w * 0.18), self.chrome_top + T.s(28), int(w * 0.64), T.s(48)
        )

    def _pack_side_chrome(
        self, gap: int, *, hand_h: int, compact: bool = False,
    ) -> None:
        """Left: log above dice. Right: effects above a flex action grid.

        Columns sit beside the hand and grow upward so nothing overlaps the
        cards. ``pack_right_column`` tightens the action grid after chips are built.
        """
        w, h = self.width, self.height
        # Left stays compact; right is wider so action labels stay readable.
        left_w = max(200, min(280 if not compact else 220, int(w * (0.14 if not compact else 0.12))))
        right_w = max(300, min(420 if not compact else 340, int(w * (0.22 if not compact else 0.18))))
        pad = max(8, gap)

        # --- left column: dice at bottom, merged journal above -----------
        dice_h = max(150, min(hand_h - pad, int(hand_h * 0.92)))
        self.dice_rect = pygame.Rect(gap, h - pad - dice_h, left_w, dice_h)
        log_h = T.s(160) if compact else T.s(200)
        self.log_rect = pygame.Rect(
            gap,
            max(self.chrome_top + pad, self.dice_rect.top - gap - log_h),
            left_w,
            log_h,
        )
        # Keep left_rail in sync for anything still keyed off it.
        self.left_rail_rect = pygame.Rect(
            self.log_rect.left, self.log_rect.top,
            left_w, self.dice_rect.bottom - self.log_rect.top,
        )

        # --- right column: action grid, effects, then YOUR MOVE above ----
        action_seed_h = T.s(160) if compact else T.s(200)
        self.action_bar_rect = pygame.Rect(
            w - right_w - gap, h - pad - action_seed_h, right_w, action_seed_h,
        )
        effects_h = max(T.s(140), min(hand_h + T.s(20), int(hand_h * 0.9)))
        self.effects_rect = pygame.Rect(
            self.action_bar_rect.left,
            max(self.chrome_top + pad, self.action_bar_rect.top - gap - effects_h),
            right_w,
            effects_h,
        )
        # Seed the action menu above effects (tightened when the menu builds).
        menu_h = T.s(200)
        self.action_menu_rect = pygame.Rect(
            self.action_bar_rect.left,
            max(self.chrome_top + pad, self.effects_rect.top - gap - menu_h),
            right_w,
            menu_h,
        )

        # --- hand between the columns ------------------------------------
        hand_left = self.dice_rect.right + gap
        hand_right = self.action_bar_rect.left - gap
        self.hand_rect = pygame.Rect(
            hand_left, h - pad - (hand_h - pad),
            max(240, hand_right - hand_left),
            hand_h - pad,
        )
        aspect_pad = 36 if not compact else 24
        self.hand_card_w = max(
            56 if compact else 97,
            min(88 if compact else 148, int((self.hand_rect.height - aspect_pad) / M.CARD_ASPECT)),
        )

    def pack_right_column(
        self,
        action_rows: int,
        *,
        cols: int = 2,
        menu_height: int = 0,
    ) -> None:
        """Flex-pack the right chrome from the bottom up.

        Stack (bottom → top): action grid → effects → YOUR MOVE.
        YOUR MOVE's bottom edge is always flush with the top of effects
        (minus ``gap``); its height shrinks to whatever space remains.
        """
        gap = T.s(M.GAP)
        pad = max(8, gap)
        chip_h = T.s(40)
        row_gap = T.s(6)
        rows = max(1, action_rows)
        left = self.action_bar_rect.left
        width = self.action_bar_rect.width
        ceiling = self.chrome_top + pad
        bottom = self.height - pad

        # 1) Action grid — sized to its chip rows, anchored to the bottom.
        action_needed = pad + rows * chip_h + max(0, rows - 1) * row_gap + pad
        action_top = max(ceiling, bottom - action_needed)
        self.action_bar_rect = pygame.Rect(left, action_top, width, bottom - action_top)

        # 2) Effects — preferred height, but yield space to the menu when needed.
        y = self.action_bar_rect.top - gap
        preferred_effects = T.s(150)
        min_effects = T.s(88)
        min_menu = T.s(56) if menu_height > 0 else 0
        available = max(0, y - ceiling)

        if menu_height > 0:
            # Reserve room for a usable menu; effects take the rest (capped).
            effects_h = min(
                preferred_effects,
                max(min_effects, available - gap - min(menu_height, available // 2)),
            )
            # If still tight, shrink effects further so the menu can fit.
            if available < effects_h + gap + min_menu:
                effects_h = max(min_effects, available - gap - min_menu)
        else:
            effects_h = min(preferred_effects, max(min_effects, available))

        effects_bottom = y
        effects_top = max(ceiling, effects_bottom - effects_h)
        self.effects_rect = pygame.Rect(
            left, effects_top, width, max(min_effects, effects_bottom - effects_top),
        )

        # 3) YOUR MOVE — bottom flush with effects top; height = leftover.
        if menu_height > 0:
            menu_bottom = self.effects_rect.top - gap
            space = max(0, menu_bottom - ceiling)
            menu_h = min(max(min_menu, menu_height), space) if space else 0
            menu_top = menu_bottom - menu_h
            self.action_menu_rect = pygame.Rect(left, menu_top, width, menu_h)
        else:
            self.action_menu_rect = pygame.Rect(left, self.effects_rect.top, width, 0)
        del cols


    def _place_opponent_wedges(
        self,
        gap: int,
        *,
        scale: float = 1.0,
        focused_key: str | None = None,
    ) -> None:
        """Lay opponent parties on the oval as seat wedges (not top thumbnails)."""
        opponents = [s for s in self.seats if not s.is_local]
        if not opponents:
            return
        table = self.table_rect
        cx, cy = table.centerx, table.centery
        rx = table.width * 0.42
        ry = table.height * 0.38
        n_all = max(2, self.player_count)
        step = (2 * math.pi) / n_all
        for i, seat in enumerate(opponents):
            # seats[0] is local; opponents are seats 1..n-1 → angles match _build_seats.
            angle = (math.pi / 2) - (i + 1) * step
            x = int(cx + rx * math.cos(angle))
            y = int(cy + ry * math.sin(angle))
            if focused_key and self.focus_rect.width > 40:
                # Focused seat is drawn into focus_rect by OpponentRail; park
                # the others as compact side wedges so the stage stays clear.
                pass
            card_w = max(56, min(110, int(min(table.width, table.height) * 0.075 * scale)))
            wedge_w = max(150, min(280, int(table.width * 0.18 * scale)))
            wedge_h = max(130, min(220, int(table.height * 0.38 * scale)))
            deg = math.degrees(angle) % 360
            if 200 < deg < 340:  # north-ish
                wedge_w = max(wedge_w, int(table.width * 0.26 * scale))
                wedge_h = max(120, int(table.height * 0.28 * scale))
            elif deg < 50 or deg > 310 or 130 < deg < 230:
                wedge_w = max(140, int(table.width * 0.15 * scale))
                wedge_h = max(150, int(table.height * 0.42 * scale))
            rect = pygame.Rect(0, 0, wedge_w, wedge_h)
            rect.center = (x, y)
            # Stay clear of the left log/dice column and the right effects/actions column.
            left_clear = max(gap, getattr(self, "dice_rect", pygame.Rect(0, 0, 0, 0)).right + gap)
            right_clear = min(
                self.width - gap,
                getattr(self, "action_bar_rect", pygame.Rect(self.width, 0, 0, 0)).left - gap,
            )
            if right_clear <= left_clear:
                left_clear, right_clear = gap, self.width - gap
            rect.clamp_ip(pygame.Rect(
                left_clear, self.chrome_top,
                max(80, right_clear - left_clear),
                max(80, self.bottom_rect.top - self.chrome_top - gap),
            ))
            if rect.colliderect(self.monster_row_rect.inflate(-40, -20)):
                if x < cx:
                    rect.right = min(rect.right, self.monster_row_rect.left - gap)
                else:
                    rect.left = max(rect.left, self.monster_row_rect.right + gap)
            head = 40
            party = pygame.Rect(
                rect.left + 8, rect.top + head,
                max(20, rect.width - 16), max(40, rect.height - head - 8),
            )
            seat.angle = angle
            seat.rect = rect
            seat.party_rect = party
            seat.centre = rect.center
            seat.card_w = min(card_w, max(48, party.height * 10 // 14))
            self.seat_card_w = max(self.seat_card_w, seat.card_w)
            self.rail_card_w = self.seat_card_w

    #: Rect / int fields that participate in camera blends.
    _LERP_RECTS = (
        "bottom_rect", "table_rect", "deck_area_rect", "monster_row_rect",
        "party_rect", "leader_rect", "dice_rect", "hand_rect", "effects_rect",
        "focus_rect", "action_menu_rect", "prompt_rect", "action_bar_rect",
        "log_rect",
    )
    _LERP_INTS = (
        "hand_card_w", "party_card_w", "monster_card_w", "deck_card_w",
        "rail_card_w", "seat_card_w",
    )

    def snapshot(self) -> dict[str, Any]:
        """Freeze role rects / card sizes / seat wedges for a camera blend."""
        data: dict[str, Any] = {
            name: pygame.Rect(getattr(self, name)) for name in self._LERP_RECTS
        }
        for name in self._LERP_INTS:
            data[name] = int(getattr(self, name))
        data["seats"] = [
            {
                "rect": pygame.Rect(s.rect),
                "party_rect": pygame.Rect(s.party_rect),
                "centre": s.centre,
                "card_w": s.card_w,
                "angle": s.angle,
            }
            for s in self.seats
        ]
        return data

    def apply_lerp(self, frm: dict, to: dict, t: float) -> None:
        """Blend layout geometry from ``frm`` toward ``to`` (t in 0..1)."""
        t = max(0.0, min(1.0, float(t)))
        for name in self._LERP_RECTS:
            a, b = frm.get(name), to.get(name)
            if isinstance(a, pygame.Rect) and isinstance(b, pygame.Rect):
                setattr(self, name, _lerp_rect(a, b, t))
        for name in self._LERP_INTS:
            if name in frm and name in to:
                setattr(self, name, int(round(frm[name] + (to[name] - frm[name]) * t)))
        seats_a = frm.get("seats") or []
        seats_b = to.get("seats") or []
        for i, seat in enumerate(self.seats):
            if i >= len(seats_a) or i >= len(seats_b):
                break
            a, b = seats_a[i], seats_b[i]
            seat.rect = _lerp_rect(a["rect"], b["rect"], t)
            seat.party_rect = _lerp_rect(a["party_rect"], b["party_rect"], t)
            seat.centre = (
                int(round(a["centre"][0] + (b["centre"][0] - a["centre"][0]) * t)),
                int(round(a["centre"][1] + (b["centre"][1] - a["centre"][1]) * t)),
            )
            seat.card_w = int(round(a["card_w"] + (b["card_w"] - a["card_w"]) * t))
            seat.angle = a["angle"] + (b["angle"] - a["angle"]) * t

    @property
    def chrome_top(self) -> int:
        """First y a board layer may use, below the floating top-edge chrome."""
        return self.camera_strip_rect.bottom + T.s(6)

    def _build_corner_buttons(self) -> None:
        """Rules / log / menu, clustered in the top-right corner."""
        btn = T.s(34)
        pad = T.s(6)
        y = T.s(6)
        width = btn * 3 + pad * 2
        right = self.width - T.s(M.GAP)
        self.corner_buttons_rect = pygame.Rect(right - width, y, width, btn)
        self.info_button_rect = pygame.Rect(self.corner_buttons_rect.left, y, btn, btn)
        self.log_button_rect = pygame.Rect(self.info_button_rect.right + pad, y, btn, btn)
        self.menu_button_rect = pygame.Rect(self.log_button_rect.right + pad, y, btn, btn)

    def _build_camera_strip(self) -> None:
        w = self.width
        self.camera_strip_rect = pygame.Rect(
            int(w * 0.22), T.s(6), int(w * 0.56), T.s(24)
        )

    def _build_table(self, gap: int) -> None:
        board = self.board_rect
        # Oval play surface inset from the board band.
        pad_x = max(36, int(self.width * 0.04))
        pad_y = max(20, int(board.height * 0.04))
        self.table_rect = pygame.Rect(
            pad_x,
            board.top + pad_y,
            self.width - pad_x * 2,
            max(200, board.height - pad_y * 2),
        )

    def _build_centre(self, gap: int) -> None:
        table = self.table_rect
        # Besties + decks occupy the upper-centre of the oval.
        centre_w = min(table.width - 80, max(420, int(table.width * 0.55)))
        monster_h = max(140, min(220, int(table.height * 0.42)))
        deck_h = max(88, min(120, int(table.height * 0.22)))

        self.monster_row_rect = pygame.Rect(
            table.centerx - centre_w // 2,
            table.top + int(table.height * 0.08),
            centre_w,
            monster_h,
        )
        deck_w = min(centre_w, 480)
        self.deck_area_rect = pygame.Rect(
            table.centerx - deck_w // 2,
            self.monster_row_rect.top - deck_h + gap,
            deck_w,
            deck_h,
        )
        if self.deck_area_rect.top < table.top:
            self.deck_area_rect.top = table.top
            self.monster_row_rect.top = self.deck_area_rect.bottom + gap // 2

        self.monster_card_w = max(88, min(160, int(monster_h / M.CARD_ASPECT)))
        self.deck_card_w = max(56, min(88, int(deck_h * 0.55)))

    def _build_local(self, gap: int) -> None:
        bottom = self.bottom_rect
        table = self.table_rect
        # Floating chrome: dice + effects on the flanks of the hand.
        chrome_w = max(200, min(280, int(self.width * 0.17)))
        inner_h = bottom.height - gap
        self.dice_rect = pygame.Rect(gap, bottom.top + gap // 2, chrome_w, inner_h)
        self.effects_rect = pygame.Rect(
            self.width - chrome_w - gap, bottom.top + gap // 2, chrome_w, inner_h
        )
        self.hand_rect = pygame.Rect(
            self.dice_rect.right + gap,
            bottom.top + gap // 2,
            max(280, self.effects_rect.left - self.dice_rect.right - gap * 2),
            inner_h,
        )
        self.hand_card_w = max(100, min(150, int(inner_h / M.CARD_ASPECT) - 8))

        # Local party sits on the south lip of the table, above the hand.
        party_h = max(100, min(150, int(table.height * 0.28)))
        party_w = min(table.width - 120, max(360, int(table.width * 0.62)))
        party_top = min(
            table.bottom - party_h - gap,
            self.hand_rect.top - party_h - gap // 2,
        )
        leader_w = max(96, min(140, int(party_w * 0.16)))
        self.leader_rect = pygame.Rect(
            table.centerx - party_w // 2, party_top, leader_w, party_h
        )
        self.party_rect = pygame.Rect(
            self.leader_rect.right + gap, party_top,
            party_w - leader_w - gap, party_h,
        )
        self.party_card_w = max(72, min(132, int(party_h / M.CARD_ASPECT)))

    def _build_seats(self, gap: int) -> None:
        n = self.player_count
        table = self.table_rect
        cx, cy = table.centerx, table.centery
        rx = table.width * 0.42
        ry = table.height * 0.38
        # Counter-clockwise from south so "next" sits to the local player's left (east).
        step = (2 * math.pi) / n
        self.seats = []
        for i in range(n):
            angle = (math.pi / 2) - i * step
            x = int(cx + rx * math.cos(angle))
            y = int(cy + ry * math.sin(angle))
            is_local = i == 0
            if is_local:
                # Local uses leader/party/hand rects; seat rect is a soft hit zone.
                rect = pygame.Rect(self.party_rect)
                party = pygame.Rect(self.party_rect)
                card_w = self.party_card_w
            else:
                # Side/north wedges — larger than the old 34–56px rail minis.
                card_w = max(64, min(96, int(min(table.width, table.height) * 0.07)))
                self.seat_card_w = card_w
                self.rail_card_w = card_w
                wedge_w = max(160, min(260, int(table.width * 0.18)))
                wedge_h = max(120, min(200, int(table.height * 0.36)))
                # North gets a wider short strip; sides get taller narrow ones.
                deg = math.degrees(angle) % 360
                if 200 < deg < 340:  # north-ish
                    wedge_w = max(wedge_w, int(table.width * 0.28))
                    wedge_h = max(110, int(table.height * 0.28))
                elif deg < 50 or deg > 310 or 130 < deg < 230:
                    wedge_w = max(140, int(table.width * 0.14))
                    wedge_h = max(150, int(table.height * 0.42))
                rect = pygame.Rect(0, 0, wedge_w, wedge_h)
                rect.center = (x, y)
                # Keep wedges inside the window, above the hand band.
                rect.clamp_ip(pygame.Rect(
                    gap, self.chrome_top,
                    self.width - gap * 2, self.bottom_rect.top - self.chrome_top - gap,
                ))
                # Avoid covering the monster row centre too heavily.
                if rect.colliderect(self.monster_row_rect.inflate(-40, -20)):
                    if x < cx:
                        rect.right = min(rect.right, self.monster_row_rect.left - gap)
                    else:
                        rect.left = max(rect.left, self.monster_row_rect.right + gap)
                party = pygame.Rect(rect.left + 8, rect.top + 36, rect.width - 16, rect.height - 44)
                card_w = min(card_w, max(56, party.height * 10 // 14))

            self.seats.append(SeatAnchor(
                index=i, angle=angle, is_local=is_local,
                centre=(rect.centerx, rect.centery),
                rect=rect, party_rect=party, card_w=card_w,
            ))

    def _build_floating(self, gap: int) -> None:
        w, h = self.width, self.height
        self.turn_chip_rect = pygame.Rect(T.s(8), self.camera_strip_rect.bottom + T.s(4),
                                          T.s(260), T.s(24))
        chip_h = T.s(34)
        bar_w = min(int(w * 0.72), T.s(860))
        self.action_bar_rect = pygame.Rect(
            w // 2 - bar_w // 2,
            self.bottom_rect.top - chip_h - T.s(8),
            bar_w,
            chip_h,
        )
        self.prompt_rect = pygame.Rect(
            int(w * 0.18), self.chrome_top + T.s(28), int(w * 0.64), T.s(44)
        )
        self.action_menu_rect = pygame.Rect(
            self.dice_rect.left,
            max(self.chrome_top, self.dice_rect.top - 280),
            self.dice_rect.width,
            260,
        )
        self.toast_rect = pygame.Rect(int(w * 0.25), h - 72, int(w * 0.5), 40)
        self.detail_rect = pygame.Rect(0, 0, T.s(520), T.s(460))
        self.modal_rect = pygame.Rect(
            int(w * 0.18), int(h * 0.1), int(w * 0.64), int(h * 0.78)
        )

    def seat_at(self, index: int) -> SeatAnchor | None:
        if 0 <= index < len(self.seats):
            return self.seats[index]
        return None

    def detail_at(self, anchor: pygame.Rect) -> pygame.Rect:
        rect = pygame.Rect(self.detail_rect)
        rect.topleft = (anchor.right + 12, anchor.top)
        if rect.right > self.width - 8:
            rect.right = anchor.left - 12
        if rect.bottom > self.height - 8:
            rect.bottom = self.height - 8
        if rect.top < 8:
            rect.top = 8
        return rect

    def card_box(self, width: int) -> tuple[int, int]:
        return int(width), int(width * M.CARD_ASPECT)

    def as_dict(self) -> dict[str, tuple[int, int, int, int]]:
        out: dict[str, tuple[int, int, int, int]] = {}
        for name, value in self.__dict__.items():
            if isinstance(value, pygame.Rect):
                out[name] = (value.x, value.y, value.w, value.h)
        for i, seat in enumerate(self.seats):
            out[f"seat_{i}"] = (seat.rect.x, seat.rect.y, seat.rect.w, seat.rect.h)
        return out


__all__ = ["MIN_H", "MIN_W", "NARROW", "LayoutManager", "SeatAnchor"]
