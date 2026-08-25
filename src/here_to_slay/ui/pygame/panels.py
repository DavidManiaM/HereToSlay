"""The board's regions, each one a self-contained panel.

Every panel takes a rect and a slice of ``GameView`` and knows how to draw
itself and answer "what did I just get clicked on?". The scene owns *when*
they are drawn and *what a click means*; a panel never touches the engine.

The regions, and why each is where it is:

* :class:`CornerButtons` (top right) — rules, log and menu. Deliberately the
  only permanent chrome along the top edge: whose go it is is told by the
  table, not by a status bar.
* :class:`OpponentRail` (right) — every opponent's deployed cards, always
  visible, ordered so the **next** player is at the top and play runs downward
  back to you. Hovering a rail expands it.
* :class:`ActiveStack` (left) — cards mid-resolution: the Hero being played,
  the Challenge answering it, the Magic on the table.
* :class:`DeckArea` (top centre) — draw pile and discard ("the burnt cards"),
  plus the Monster deck.
* :class:`MonsterRow` (centre) — the Monsters that can be attacked now, with
  their requirement and whether you meet it.
* :class:`PartyRow` (lower centre) — your Leader and your deployed Heroes.
* :class:`HandFan` (bottom centre) — your hand.
* :class:`DicePanel` (bottom left) — the dice, the roll button, action points.
* :class:`EffectsPanel` (bottom right) — passives, equipment and flags in force
  for whoever is currently acting.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import pygame

from here_to_slay.ui import lexicon as L
from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.card_renderer import card_facts, render_card, render_card_back
from here_to_slay.ui.pygame.icons import card_icon_name, draw_icon
from here_to_slay.ui.pygame.theme import C, M
from here_to_slay.ui.pygame.widgets import (
    Button,
    CardSprite,
    Chip,
    IconButton,
    ZoneWidget,
)

#: Victory needs three slain Monsters or all six classes; both are shown as
#: progress, and both numbers come from the rules rather than being assumed.
DEFAULT_SLAY_TARGET = 3


def _initials(name: str) -> str:
    parts = [p for p in name.replace("_", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:1].upper()
    # "Player 2" -> "P2" is far more useful than "PL".
    if parts[-1].isdigit():
        return (parts[0][:1] + parts[-1]).upper()
    return (parts[0][:1] + parts[-1][:1]).upper()


def _panel_title(
    screen: pygame.Surface, rect: pygame.Rect, title: str, *, icon: str | None = None,
    right: str = "", colour: tuple[int, int, int] = C.INK_DIM,
) -> pygame.Rect:
    """Floating caption — no panel chrome. Returns the content rect below."""
    x = rect.left + 8
    if icon:
        draw_icon(screen, icon, (x + 7, rect.top + 10), 14, colour)
        x += 20
    T.text(screen, title.upper(), (x, rect.top + 4), T.ui(10, bold=True),
           T.alpha(colour, 220), shadow=None)
    if right:
        T.text(screen, right, (rect.right - 8, rect.top + 4), T.ui(10, bold=True),
               T.alpha(C.INK_FAINT, 220), anchor="topright", shadow=None)
    return pygame.Rect(rect.left, rect.top + 22, rect.width, rect.height - 22)


# ---------------------------------------------------------------------------
# Corner buttons
# ---------------------------------------------------------------------------


class CornerButtons:
    """Rules, log and menu, floating in the top-right corner.

    All that survives of the old full-width top bar. Turn, phase and whose go
    it is are told by the table itself now, not by a strip of chrome.
    """

    def __init__(self, layout: Any) -> None:
        self.layout = layout
        self.info_button = IconButton(layout.info_button_rect, "info",
                                      tooltip="Rules & help  (F1)")
        self.log_button = IconButton(layout.log_button_rect, "scroll",
                                     tooltip="Game log  (L)")
        self.menu_button = IconButton(layout.menu_button_rect, "gear",
                                      tooltip="Settings & keys  (Esc)")

    @property
    def buttons(self) -> tuple[IconButton, ...]:
        return (self.info_button, self.log_button, self.menu_button)

    def resize(self) -> None:
        self.info_button.rect = pygame.Rect(self.layout.info_button_rect)
        self.log_button.rect = pygame.Rect(self.layout.log_button_rect)
        self.menu_button.rect = pygame.Rect(self.layout.menu_button_rect)

    def update(self, dt: float) -> None:
        for button in self.buttons:
            button.update(dt)

    def handle_event(self, event: pygame.event.Event) -> bool:
        return any(button.handle_event(event) for button in self.buttons)

    def draw(self, screen: pygame.Surface) -> None:
        cluster = self.layout.corner_buttons_rect.inflate(T.s(10), T.s(10))
        T.round_rect(screen, cluster, (12, 18, 24, 130), radius=cluster.height // 2)
        for button in self.buttons:
            button.draw(screen)


# ---------------------------------------------------------------------------
# Turn chip — quiet "whose turn" pill
# ---------------------------------------------------------------------------


class TurnChip:
    """Small top-left pill: seat colour, name, turn number, AP pips."""

    def __init__(self, layout: Any) -> None:
        self.layout = layout
        self.name = ""
        self.colour: tuple[int, int, int] = C.GOLD
        self.turn_number = 0
        self.action_points = 0
        self.max_points = 3

    def sync(self, view: Any) -> None:
        player = view.players.get(view.active_player)
        self.name = player.name if player else str(view.active_player)
        self.colour = T.seat_colour(player.seat) if player else C.GOLD
        self.turn_number = int(getattr(view, "turn_number", 0) or 0)
        self.action_points = int(player.action_points) if player else 0

    def draw(self, screen: pygame.Surface) -> None:
        rect = pygame.Rect(self.layout.turn_chip_rect)
        T.pill(screen, rect, "", bg=T.alpha((16, 24, 28), 160),
               fg=C.INK_DIM, border=T.alpha(C.INK_FAINT, 80), fnt=T.ui(T.s(9)))
        dot_r = max(4, T.s(5))
        dot_c = (rect.left + T.s(10), rect.centery)
        pygame.draw.circle(screen, self.colour, dot_c, dot_r)
        label = f"{self.name}  T{self.turn_number}"
        T.text(screen, label, (rect.left + T.s(20), rect.centery), T.ui(T.s(9)),
               T.alpha(C.INK_DIM, 210), anchor="midleft", shadow=None)
        # AP pips on the right
        pip_r = max(3, T.s(3))
        x = rect.right - T.s(8)
        for i in range(self.max_points):
            filled = i < self.action_points
            cx = x - i * (pip_r * 2 + T.s(4))
            col = self.colour if filled else T.alpha(C.INK_FAINT, 120)
            pygame.draw.circle(screen, col, (cx, rect.centery), pip_r)


# ---------------------------------------------------------------------------
# Action bar — one chip per legal action
# ---------------------------------------------------------------------------


@dataclass
class ActionChip:
    action_id: str
    key: str
    label: str
    cost: str
    enabled: bool
    rect: pygame.Rect
    on_click: Callable[[], None]


class ActionBar:
    """Bottom-centre row of action chips with keys and AP costs."""

    def __init__(self, layout: Any) -> None:
        self.layout = layout
        self.chips: list[ActionChip] = []
        self._hover: ActionChip | None = None

    def build(
        self,
        entries: list[tuple[str, str, str, str, bool, Callable[[], None]]],
    ) -> None:
        """Each entry: (action_id, key, label, cost, enabled, callback)."""
        rect = pygame.Rect(self.layout.action_bar_rect)
        if not entries or rect.width < 40:
            self.chips = []
            return
        gap = T.s(4)
        chip_h = rect.height
        # Measure widths
        widths: list[int] = []
        for _aid, key, label, cost, _en, _cb in entries:
            text = f"[{key.upper()}] {label} ({cost})"
            widths.append(T.s(12) + T.ui(T.s(10), bold=True).size(text)[0] + T.s(10))
        total = sum(widths) + gap * max(0, len(entries) - 1)
        x = rect.centerx - total // 2
        chips: list[ActionChip] = []
        for (action_id, key, label, cost, enabled, cb), w in zip(entries, widths, strict=True):
            chip_rect = pygame.Rect(x, rect.top, w, chip_h)
            chips.append(ActionChip(action_id, key, label, cost, enabled, chip_rect, cb))
            x += w + gap
        self.chips = chips

    def update(self, dt: float) -> None:
        del dt

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEMOTION:
            pos = event.pos
            self._hover = next((c for c in self.chips if c.enabled and c.rect.collidepoint(pos)), None)
            return False
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            chip = self._hover or next(
                (c for c in self.chips if c.enabled and c.rect.collidepoint(event.pos)), None
            )
            if chip is not None:
                chip.on_click()
                return True
        return False

    def draw(self, screen: pygame.Surface) -> None:
        for chip in self.chips:
            lit = chip is self._hover
            if not chip.enabled:
                bg = T.alpha((28, 32, 38), 120)
            else:
                bg = T.alpha((22, 30, 36), 220 if lit else 180)
            border = T.alpha(C.CYAN if lit and chip.enabled else C.INK_FAINT, 180 if chip.enabled else 80)
            fg = C.INK_BRIGHT if chip.enabled else C.INK_FAINT
            accent = ACTION_STYLE.get(chip.action_id, ("bolt", C.GOLD))[1]
            text = f"[{chip.key.upper()}] {chip.label} ({chip.cost})"
            T.pill(screen, chip.rect, text, bg=bg, fg=fg, border=border,
                   fnt=T.ui(T.s(10), bold=True))
            if chip.enabled:
                bar = pygame.Rect(chip.rect.left, chip.rect.top, 3, chip.rect.height)
                T.round_rect(screen, bar, accent, radius=2)


#: Imported late to avoid circular imports — matches scenes.ACTION_STYLE.
ACTION_STYLE: dict[str, tuple[str, tuple[int, int, int]]] = {
    "draw": ("hand", C.FROST),
    "play_hero": ("hero", C.GOOD),
    "equip_item": ("item", C.GOLD),
    "cast_magic": ("magic", C.ARCANE),
    "use_hero_ability": ("bolt", C.GOLD),
    "use_leader_ability": ("leader", C.GOLD),
    "attack_monster": ("monster", C.BLOOD),
    "discard_and_draw": ("discard", C.WARN),
}


# ---------------------------------------------------------------------------
# Reaction timer — 3s auto-pass arc
# ---------------------------------------------------------------------------

REACTION_SECONDS = 3.0


class ReactionTimer:
    """Compact reaction panel with draining arc and option keys."""

    def __init__(self) -> None:
        self.visible = False
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.title = ""
        self.fraction = 1.0
        self.options: list[tuple[str, str, Callable[[], None]]] = ()

    def sync(
        self,
        *,
        visible: bool,
        rect: pygame.Rect,
        title: str,
        fraction: float,
        options: list[tuple[str, str, Callable[[], None]]],
    ) -> None:
        self.visible = visible
        self.rect = pygame.Rect(rect)
        self.title = title
        self.fraction = max(0.0, min(1.0, fraction))
        self.options = tuple(options)

    def draw(self, screen: pygame.Surface) -> None:
        if not self.visible or self.rect.width < 20:
            return
        T.glass(screen, self.rect, radius=T.s(10), fill=(18, 26, 32, 230),
                rim=T.alpha(C.POISON, 180))
        T.text(screen, self.title, (self.rect.left + T.s(12), self.rect.top + T.s(8)),
               T.ui(T.s(11), bold=True), C.INK_BRIGHT, anchor="topleft", shadow=None,
               max_width=self.rect.width - T.s(40))
        # Draining arc
        cx = self.rect.right - T.s(22)
        cy = self.rect.centery
        r = T.s(14)
        arc_rect = pygame.Rect(cx - r, cy - r, r * 2, r * 2)
        pygame.draw.circle(screen, T.alpha(C.INK_FAINT, 60), arc_rect.center, r, 2)
        if self.fraction > 0.01:
            end = -math.pi / 2 + self.fraction * math.tau
            pygame.draw.arc(screen, C.POISON, arc_rect, -math.pi / 2, end, 3)
        # Option chips
        y = self.rect.top + T.s(28)
        for i, (label, key, _cb) in enumerate(self.options):
            chip = pygame.Rect(self.rect.left + T.s(8), y, self.rect.width - T.s(16), T.s(22))
            T.pill(screen, chip, f"[{i + 1}] {label}  ({key})", bg=T.alpha(C.ARCANE, 50),
                   fg=C.INK_BRIGHT, border=T.alpha(C.ARCANE, 120), fnt=T.ui(T.s(9), bold=True))
            y += T.s(26)


def _play_order(view: Any) -> list[str]:
    """Seats starting with the viewer, then in turn order back round to them.

    The whole layout hangs off this: "after your turn, the top player is next,
    and the turns go down until it is your turn again".
    """
    order = list(view.turn_order)
    if not order:
        return [view.seat]
    if view.seat in order:
        i = order.index(view.seat)
        return order[i:] + order[:i]
    return order


# ---------------------------------------------------------------------------
# Opponent rail
# ---------------------------------------------------------------------------


class OpponentStrip:
    """One opponent's slice of the right rail: header plus their board.

    Collapsed it shows their Leader, initials, AP and counts, with their party
    as a tight row of minis. Expanded (on hover) it grows to full-size cards so
    you can read what they have deployed without leaving the board.
    """

    def __init__(self, player_id: str) -> None:
        self.player_id = player_id
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.name = ""
        self.initials = "?"
        self.colour: tuple[int, int, int] = C.GOLD
        self.seat = 0
        self.is_active = False
        self.action_points = 0
        self.hand_size = 0
        self.slain: list[Any] = []
        self.party: list[tuple[Any, Any]] = []
        self.leader: tuple[Any, Any] | None = None
        self.classes: tuple[str, ...] = ()
        self.expand = 0.0
        self.hovered = False
        self.highlighted = False
        self.targetable = False
        self.sprites: list[CardSprite] = []
        self.leader_sprite: CardSprite | None = None

    # -- interaction -------------------------------------------------------

    def update(self, dt: float) -> None:
        self.expand = _ease(self.expand, 1.0 if self.hovered else 0.0, dt, 9.0)
        for sprite in self.sprites:
            sprite.update(dt)
        if self.leader_sprite:
            self.leader_sprite.update(dt)

    def card_at(self, pos: tuple[int, int]) -> CardSprite | None:
        for sprite in reversed(self.sprites):
            if sprite.hit(pos):
                return sprite
        if self.leader_sprite and self.leader_sprite.hit(pos):
            return self.leader_sprite
        return None

    def update_hover(self, pos: tuple[int, int]) -> CardSprite | None:
        self.hovered = self.rect.collidepoint(pos)
        top = self.card_at(pos) if self.hovered else None
        for sprite in (*self.sprites, *( [self.leader_sprite] if self.leader_sprite else [])):
            sprite.hovered = sprite is top
        return top

    # -- layout ------------------------------------------------------------

    def layout_cards(self, base_card_w: int, highlight_ids: set[str]) -> None:
        # Compact header so cards have room inside the seat wedge.
        head = 36 if self.rect.height >= 140 else max(22, self.rect.height // 5)
        grow = T.ease_out_cubic(self.expand)
        card_w = int(base_card_w * (1.0 + 1.25 * grow))
        card_h = int(card_w * M.CARD_ASPECT)
        body = pygame.Rect(self.rect.left + 6, self.rect.top + head,
                           self.rect.width - 12, max(8, self.rect.height - head - 4))

        lw = min(int(base_card_w * 1.15), max(28, body.width // 4))
        lh = int(lw * M.CARD_ASPECT)
        self.leader_sprite = None
        if self.leader is not None:
            cv, cdef = self.leader
            self.leader_sprite = CardSprite(
                cv.id, cdef,
                pygame.Rect(body.left, body.top, lw, min(lh, body.height)),
                highlighted=cv.id in highlight_ids, owner_colour=self.colour,
                lift_on_hover=6, depth=0.55,
            )

        self.sprites = []
        if not self.party:
            return
        start_x = body.left + (lw + 6 if self.leader_sprite else 0)
        room = max(card_w, body.right - start_x)
        n = len(self.party)
        step = card_w + 4 if (card_w + 4) * n <= room else max(10, (room - card_w) // max(1, n - 1))
        rows = 1
        if grow > 0.4 and (card_w + 4) * n > room:
            rows = 2
            step = card_w + 4
        for i, (cv, cdef) in enumerate(self.party):
            row = i % rows
            col = i // rows
            x = start_x + col * step
            y = body.top + row * int(card_h * 0.42)
            # Fit cards inside the wedge so set_clip does not erase them.
            draw_h = min(card_h, max(24, body.bottom - y))
            draw_w = max(20, int(draw_h / M.CARD_ASPECT)) if draw_h < card_h else card_w
            self.sprites.append(CardSprite(
                cv.id, cdef, pygame.Rect(int(x), int(y), draw_w, draw_h),
                tapped=False, highlighted=cv.id in highlight_ids,
                attachments=tuple(cv.attachments or ()),
                badge_text="\u25cf" if cv.tapped else "",
                badge_colour=C.IDLE, owner_colour=self.colour, lift_on_hover=8,
                depth=0.5,
            ))

    @property
    def wants_height(self) -> int:
        """Extra pixels this strip would like while expanded."""
        return int(T.ease_out_cubic(self.expand) * 118)

    # -- drawing -----------------------------------------------------------

    def draw(self, screen: pygame.Surface) -> None:
        rect = self.rect
        lit = T.ease_out_cubic(self.expand)
        rim = (
            C.GOLD if (self.highlighted or self.targetable)
            else (self.colour if self.is_active else T.alpha(C.GLASS_RIM, 80))
        )
        if self.is_active:
            T.blit_glow(screen, rect.center, rect.width // 2 + 12, T.alpha(self.colour, 34))
        if lit > 0.02 or self.is_active or self.highlighted:
            T.drop_shadow(screen, rect, radius=M.RADIUS, spread=10, offset=(0, 4), strength=50)
        if self.is_active or self.highlighted:
            T.round_rect(screen, rect, rim, radius=M.RADIUS, width=2)
            T.round_rect(screen, pygame.Rect(rect.left, rect.top + 6, 3, rect.height - 12),
                         self.colour, radius=2)

        # Header: initial badge, name, AP, hand, slain.
        pip_r = 15
        pip_c = (rect.left + 8 + pip_r, rect.top + 8 + pip_r)
        pygame.draw.circle(screen, T.mix((220, 230, 240), self.colour,
                                         0.85 if self.is_active else 0.35), pip_c, pip_r)
        pygame.draw.circle(screen, self.colour, pip_c, pip_r,
                           3 if self.is_active else 1)
        T.text(screen, self.initials, pip_c, T.display(15),
               C.INK_DARK, anchor="center", shadow=None)

        x = pip_c[0] + pip_r + 8
        T.text(screen, self.name, (x, rect.top + 9), T.ui(12, bold=True),
               C.INK_BRIGHT, max_width=rect.right - x - 78)
        stat_font = T.ui(10, bold=True)
        stats = pygame.Rect(rect.right - 74, rect.top + 7, 66, 17)
        draw_icon(screen, "bolt", (stats.left + 6, stats.centery), 12,
                  C.GOLD if self.action_points else C.INK_FAINT)
        T.text(screen, str(self.action_points), (stats.left + 15, stats.centery), stat_font,
               C.GOLD if self.action_points else C.INK_FAINT, anchor="midleft", shadow=None)
        draw_icon(screen, "hand", (stats.left + 33, stats.centery), 12, C.INK_DIM)
        T.text(screen, str(self.hand_size), (stats.left + 42, stats.centery), stat_font,
               C.INK_DIM, anchor="midleft", shadow=None)
        if self.slain:
            draw_icon(screen, "skull", (stats.left + 58, stats.centery), 12, C.BLOOD)
            T.text(screen, str(len(self.slain)), (rect.right - 8, stats.centery),
                   stat_font, C.BLOOD, anchor="midright", shadow=None)

        # Class dots: the six-class win condition at a glance.
        dot_x = x
        for cls in T.CLASS_COLOURS:
            has = cls in self.classes
            pygame.draw.circle(
                screen, T.CLASS_COLOURS[cls] if has else (190, 200, 212),
                (dot_x + 4, rect.top + 33), 4,
            )
            if not has:
                pygame.draw.circle(screen, (150, 162, 178), (dot_x + 4, rect.top + 33), 4, 1)
            dot_x += 12

        if not self.party and not self.leader_sprite:
            T.text(screen, L.NO_PARTY, (rect.centerx, rect.centery + 10),
                   T.ui(10, italic=True), T.alpha(C.INK_FAINT, 190), anchor="center",
                   shadow=None)

        clip = screen.get_clip()
        screen.set_clip(rect.inflate(0, -2))
        if self.leader_sprite:
            self.leader_sprite.draw(screen)
        hovered = None
        for sprite in self.sprites:
            if sprite.hovered:
                hovered = sprite
            else:
                sprite.draw(screen)
        if hovered:
            hovered.draw(screen)
        screen.set_clip(clip)


class OpponentRail:
    """Opponent seat wedges around the table (plus/hex), in play order.

    Index 0 of :meth:`layout.seats` is always the local player; opponents map
    onto seats 1..n-1. The class name is historical — there is no right rail.
    """

    def __init__(self, layout: Any) -> None:
        self.layout = layout
        self.strips: list[OpponentStrip] = []
        self._by_id: dict[str, OpponentStrip] = {}

    def sync(
        self,
        view: Any,
        registry: Any,
        *,
        highlight_cards: set[str] | None = None,
        target_players: set[str] | None = None,
    ) -> None:
        highlight_cards = highlight_cards or set()
        target_players = target_players or set()
        order = [pid for pid in _play_order(view) if pid != view.seat]
        n_players = len(view.players)
        if getattr(self.layout, "player_count", None) != n_players:
            self.layout.rebuild(self.layout.width, self.layout.height, player_count=n_players)

        kept: dict[str, OpponentStrip] = {}
        self.strips = []
        for pid in order:
            strip = self._by_id.get(pid) or OpponentStrip(pid)
            player = view.players[pid]
            strip.name = player.name
            strip.initials = _initials(player.name)
            strip.colour = T.seat_colour(player.seat)
            strip.seat = player.seat
            strip.is_active = player.is_active
            strip.action_points = player.action_points
            hand = player.zone("hand")
            strip.hand_size = hand.size if hand else 0
            slain = player.zone("slain")
            strip.slain = list(slain.cards) if slain else []
            party = player.zone("party")
            strip.party = [
                (cv, registry.get(cv.def_id)) for cv in (party.cards if party else ())
            ]
            leader_zone = player.zone("leader")
            strip.leader = None
            if leader_zone and leader_zone.cards:
                cv = leader_zone.cards[0]
                strip.leader = (cv, registry.get(cv.def_id))
            strip.classes = _classes_of(strip.party, strip.leader)
            strip.targetable = pid in target_players
            strip.highlighted = pid in target_players
            self.strips.append(strip)
            kept[pid] = strip
        self._by_id = kept
        self._place(highlight_cards)

    def _place(self, highlight_cards: set[str]) -> None:
        focus = getattr(self.layout, "focus_rect", None)
        focus_key = getattr(self.layout, "camera_key", "local")
        seats = [s for s in getattr(self.layout, "seats", ()) if not s.is_local]

        for i, strip in enumerate(self.strips):
            focused = focus_key == strip.player_id and focus is not None and focus.width > 40
            if focused:
                strip.rect = pygame.Rect(focus)
                card_w = max(110, getattr(self.layout, "seat_card_w", 140))
                strip.expand = 1.0
            elif i < len(seats):
                anchor = seats[i]
                strip.rect = pygame.Rect(anchor.rect)
                card_w = max(36, getattr(anchor, "card_w", 48))
            else:
                strip.rect = pygame.Rect(12, self.layout.board_rect.top + 40 + i * 90, 140, 80)
                card_w = 44
            strip.layout_cards(card_w, highlight_cards)

    def update(self, dt: float) -> None:
        for strip in self.strips:
            strip.update(dt)

    def update_hover(self, pos: tuple[int, int]) -> CardSprite | None:
        hit = None
        for strip in self.strips:
            found = strip.update_hover(pos)
            hit = found or hit
        return hit

    def clear_hover(self) -> None:
        for strip in self.strips:
            strip.hovered = False
            for sprite in strip.sprites:
                sprite.hovered = False

    def player_at(self, pos: tuple[int, int]) -> str | None:
        for strip in self.strips:
            if strip.rect.collidepoint(pos):
                return strip.player_id
        return None

    def strip_of(self, player_id: str) -> OpponentStrip | None:
        return self._by_id.get(player_id)

    def draw(self, screen: pygame.Surface) -> None:
        for strip in self.strips:
            strip.draw(screen)


def _classes_of(
    party: Sequence[tuple[Any, Any]], leader: tuple[Any, Any] | None
) -> tuple[str, ...]:
    """Every Hero class a seat covers — Leaders count, per the rulebook."""
    found: set[str] = set()
    for _cv, cdef in party:
        cls = getattr(cdef, "card_class", None)
        if cls:
            found.add(cls)
    if leader is not None:
        cls = getattr(leader[1], "card_class", None)
        if cls:
            found.add(cls)
    return tuple(sorted(found))


def _ease(current: float, target: float, dt: float, speed: float) -> float:
    return current + (target - current) * min(1.0, dt * speed)


# ---------------------------------------------------------------------------
# Left rail: cards in flight
# ---------------------------------------------------------------------------


class ActiveStack:
    """Cards currently resolving, and whatever is modifying them.

    ``limbo`` is the engine's holding zone for a card mid-play — exactly the
    "a Challenge or a Magic somebody is using right now" the player needs to
    see. It is also where the pending request's subject shows up, so this panel
    doubles as "what is the game asking about?".
    """

    def __init__(self, layout: Any) -> None:
        self.layout = layout
        self.sprites: list[CardSprite] = []
        self.notes: list[tuple[str, tuple[int, int, int], str | None]] = []
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.pulse = 0.0

    @property
    def occupied(self) -> bool:
        return bool(self.sprites or self.notes)

    def sync(
        self,
        view: Any,
        registry: Any,
        *,
        pending_note: tuple[str, tuple[int, int, int], str | None] | None = None,
        extra_cards: Sequence[Any] = (),
        rolls: Sequence[Any] = (),
    ) -> None:
        self.rect = pygame.Rect(self.layout.left_rail_rect)
        # Float the active stack near the upper-left of the table, not a side column.
        table = getattr(self.layout, "table_rect", self.layout.board_rect)
        self.rect = pygame.Rect(
            table.left + 8,
            table.top + 8,
            max(160, min(220, table.width // 5)),
            max(180, min(320, table.height // 2)),
        )
        limbo = view.zone("limbo")
        cards = list(limbo.cards) if limbo else []
        cards.extend(extra_cards)

        head = 26
        card_w = min(self.rect.width - 20, 128)
        card_h = int(card_w * M.CARD_ASPECT)
        self.sprites = []
        for i, cv in enumerate(cards[:3]):
            y = self.rect.top + head + i * (card_h + 8)
            self.sprites.append(CardSprite(
                cv.id, registry.get(cv.def_id),
                pygame.Rect(self.rect.left + (self.rect.width - card_w) // 2, y, card_w, card_h),
                highlighted=True, lift_on_hover=6,
            ))

        self.notes = []
        if pending_note is not None:
            self.notes.append(pending_note)
        for roll in list(rolls)[-2:]:
            who = view.players.get(roll.roller)
            name = who.name if who is not None else "someone"
            self.notes.append((f"{name}: {roll.describe()}", C.FROST, "dice"))

    def update(self, dt: float) -> None:
        self.pulse += dt
        for sprite in self.sprites:
            sprite.update(dt)

    def card_at(self, pos: tuple[int, int]) -> CardSprite | None:
        for sprite in reversed(self.sprites):
            if sprite.hit(pos):
                return sprite
        return None

    def update_hover(self, pos: tuple[int, int]) -> CardSprite | None:
        top = self.card_at(pos)
        for sprite in self.sprites:
            sprite.hovered = sprite is top
        return top

    def draw(self, screen: pygame.Surface) -> None:
        if not self.occupied:
            return
        rect = self.rect
        # Static accent bar — no pulsing wash over the active stack.
        T.round_rect(
            screen, pygame.Rect(rect.left + 4, rect.top + 22, 3, max(12, rect.height - 30)),
            C.ARCANE, radius=2,
        )
        _panel_title(screen, rect, L.IN_PLAY, icon="bolt", colour=C.ARCANE)

        hovered = None
        for sprite in self.sprites:
            if sprite.hovered:
                hovered = sprite
            else:
                sprite.draw(screen)
        if hovered:
            hovered.draw(screen)

        y = (
            self.sprites[-1].rect.bottom + 10 if self.sprites
            else rect.top + 32
        )
        for note, colour, icon in self.notes:
            if y > rect.bottom - 18:
                break
            x = rect.left + 10
            if icon:
                draw_icon(screen, icon, (x + 6, y + 7), 13, colour)
                x += 19
            used = T.draw_wrapped(
                screen, note, pygame.Rect(x, y, rect.right - x - 10, rect.bottom - y - 4),
                T.ui(10), colour, line_gap=1,
            )
            y += max(16, used) + 6


# ---------------------------------------------------------------------------
# Deck area
# ---------------------------------------------------------------------------


class DeckArea:
    """Draw pile, discard pile ("burnt"), and the Monster deck."""

    def __init__(self, layout: Any) -> None:
        self.layout = layout
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.slots: list[tuple[str, pygame.Rect, int, Any, str]] = []
        self.hovered_key: str | None = None
        self.discard_top: Any = None

    def sync(self, view: Any, registry: Any) -> None:
        self.rect = pygame.Rect(self.layout.deck_area_rect)
        cw = self.layout.deck_card_w
        ch = int(cw * M.CARD_ASPECT)
        body = _panel_body(self.rect)
        entries: list[tuple[str, int, Any, str]] = []

        deck = view.zone("main_deck")
        entries.append(("main_deck", deck.size if deck else 0, None, L.DECK_DRAW))
        discard = view.zone("discard")
        self.discard_top = (
            registry.get(discard.cards[-1].def_id) if discard and discard.cards else None
        )
        entries.append(("discard", discard.size if discard else 0, self.discard_top, L.DECK_DISCARD))
        monsters = view.zone("monster_deck")
        entries.append(("monster_deck", monsters.size if monsters else 0, None, L.DECK_MONSTER))

        gap = 22
        total = len(entries) * cw + (len(entries) - 1) * gap
        x = body.centerx - total // 2
        y = body.centery - ch // 2
        self.slots = []
        for key, size, top, label in entries:
            self.slots.append((key, pygame.Rect(x, y, cw, ch), size, top, label))
            x += cw + gap

    def slot_at(self, pos: tuple[int, int]) -> str | None:
        for key, rect, _size, _top, _label in self.slots:
            if rect.collidepoint(pos):
                return key
        return None

    def update_hover(self, pos: tuple[int, int]) -> None:
        self.hovered_key = self.slot_at(pos)

    def rect_of(self, key: str) -> pygame.Rect | None:
        for slot_key, rect, _s, _t, _l in self.slots:
            if slot_key == key:
                return rect
        return None

    def draw(self, screen: pygame.Surface) -> None:
        _panel_title(screen, self.rect, L.TABLE, icon="scroll")

        for key, rect, size, top, label in self.slots:
            hot = key == self.hovered_key
            tone = {"main_deck": 0, "discard": 0, "monster_deck": 1}.get(key, 0)
            if size <= 0 and top is None:
                T.inset(screen, rect, radius=8)
                T.text(screen, "empty", rect.center, T.ui(9, italic=True),
                       T.alpha(C.INK_FAINT, 190), anchor="center", shadow=None)
            elif key == "discard" and top is not None:
                if hot:
                    T.blit_glow(screen, rect.center, rect.width, T.alpha(C.EMBER, 70))
                screen.blit(render_card(top, rect.width, rect.height, dimmed=not hot),
                            rect.topleft)
            else:
                # A stack of backs, so pile depth is visible at a glance.
                depth = min(4, max(1, size // 8))
                for d in range(depth, 0, -1):
                    off = d * 2
                    screen.blit(render_card_back(rect.width, rect.height, tone=tone),
                                (rect.left - off // 2, rect.top - off))
                if hot:
                    T.round_rect(screen, rect, C.GOLD, radius=8, width=2)

            T.text(screen, label.upper(), (rect.centerx, rect.bottom + 4), T.ui(9, bold=True),
                   T.alpha(C.INK_DIM, 235), anchor="midtop", shadow=None)
            T.badge(screen, (rect.centerx, rect.top - 4), 11, str(size),
                    bg=C.BLOOD if key == "discard" else C.INK,
                    fg=C.INK_BRIGHT, ring=T.alpha(C.GOLD, 150), fnt=T.ui(10, bold=True))


def _panel_body(rect: pygame.Rect, head: int = 22) -> pygame.Rect:
    return pygame.Rect(rect.left, rect.top + head, rect.width, max(8, rect.height - head - 4))


# ---------------------------------------------------------------------------
# Monster row
# ---------------------------------------------------------------------------


class MonsterRow:
    """The Monsters on the table, with requirement and slay threshold."""

    def __init__(self, layout: Any) -> None:
        self.layout = layout
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.sprites: list[CardSprite] = []
        self.meta: dict[str, tuple[Any, bool]] = {}
        self.pulse = 0.0

    def sync(
        self,
        view: Any,
        registry: Any,
        *,
        attackable: set[str] | None = None,
        highlight: set[str] | None = None,
    ) -> None:
        attackable = attackable or set()
        highlight = highlight or set()
        self.rect = pygame.Rect(self.layout.monster_row_rect)
        zone = view.zone("monster_row")
        cards = list(zone.cards) if zone else []
        capacity = (zone.capacity if zone and zone.capacity else max(3, len(cards))) or 3

        body = _panel_body(self.rect, 24)
        cw = min(self.layout.monster_card_w, (body.width - 40) // max(1, capacity) - 14)
        cw = max(58, cw)
        ch = int(cw * M.CARD_ASPECT)
        slot_gap = 18
        total = capacity * cw + (capacity - 1) * slot_gap
        x0 = body.centerx - total // 2
        y = body.centery - ch // 2

        self.sprites = []
        self.meta = {}
        self._slots = [
            pygame.Rect(x0 + i * (cw + slot_gap), y, cw, ch) for i in range(capacity)
        ]
        for i, cv in enumerate(cards[:capacity]):
            cdef = registry.get(cv.def_id)
            can = cv.id in attackable
            self.meta[cv.id] = (cdef, can)
            self.sprites.append(CardSprite(
                cv.id, cdef, self._slots[i],
                highlighted=cv.id in highlight or can,
                dimmed=not can and bool(attackable),
                lift_on_hover=16,
            ))

    def update(self, dt: float) -> None:
        self.pulse += dt
        for sprite in self.sprites:
            sprite.update(dt)

    def card_at(self, pos: tuple[int, int]) -> CardSprite | None:
        for sprite in reversed(self.sprites):
            if sprite.hit(pos):
                return sprite
        return None

    def update_hover(self, pos: tuple[int, int]) -> CardSprite | None:
        top = self.card_at(pos)
        for sprite in self.sprites:
            sprite.hovered = sprite is top
        return top

    def draw(self, screen: pygame.Surface) -> None:
        count = len(self.sprites)
        _panel_title(screen, self.rect, L.BESTIES_ROW, icon="monster",
                     right=f"{count} pe masă", colour=C.BLOOD)

        for slot in getattr(self, "_slots", ()):
            if not any(s.rect.colliderect(slot) for s in self.sprites):
                T.inset(screen, slot, radius=10)
                T.text(screen, "empty", slot.center, T.ui(10, italic=True),
                       T.alpha(C.INK_FAINT, 160), anchor="center", shadow=None)

        hovered = None
        for sprite in self.sprites:
            if sprite.hovered:
                hovered = sprite
                continue
            self._draw_monster(screen, sprite)
        if hovered:
            self._draw_monster(screen, hovered)

    def _draw_monster(self, screen: pygame.Surface, sprite: CardSprite) -> None:
        cdef, can_attack = self.meta.get(sprite.card_id, (None, False))
        rect = sprite.draw_rect()
        sprite.draw(screen)
        if can_attack:
            # Static cyan rim — legal befriend target, no throb.
            T.round_rect(screen, rect.inflate(4, 4), C.CYAN, radius=11, width=2)

        facts = card_facts(cdef)
        chip_y = rect.bottom + 4
        if facts.requirement and rect.width >= 70:
            fnt = T.ui(9, bold=True)
            label = T.ellipsise(facts.requirement, fnt, rect.width - 8)
            w = fnt.size(label)[0] + 16
            # Solid dark chip + bright text so requirements read on the felt.
            bg = T.alpha((10, 18, 22), 230)
            fg = C.GOOD if can_attack else C.WARN
            chip = pygame.Rect(rect.centerx - w // 2, chip_y, w, 18)
            T.round_rect(screen, chip, bg, radius=9)
            T.round_rect(screen, chip, T.alpha(fg, 200), radius=9, width=1)
            T.text(screen, label, chip.center, fnt, fg, anchor="center", shadow=None)
            chip_y += 20
        if can_attack and rect.width >= 70:
            T.text(screen, L.CAN_BEFRIEND, (rect.centerx, chip_y), T.ui(9, bold=True),
                   C.CYAN, anchor="midtop", shadow=None)


# ---------------------------------------------------------------------------
# Your side of the table
# ---------------------------------------------------------------------------


class PartyRow:
    """Your Leader plus your deployed Heroes — "where you deploy cards"."""

    def __init__(self, layout: Any) -> None:
        self.layout = layout
        self.row = ZoneWidget(pygame.Rect(0, 0, 0, 0), L.PARTY, mode="row",
                              empty_hint=L.NO_PARTY, panel=False)
        self.leader_sprite: CardSprite | None = None
        self.classes: tuple[str, ...] = ()
        self.slain = 0
        self.slay_target = DEFAULT_SLAY_TARGET

    def sync(
        self,
        view: Any,
        registry: Any,
        *,
        highlight: set[str] | None = None,
        selected: set[str] | None = None,
        slay_target: int = DEFAULT_SLAY_TARGET,
    ) -> None:
        highlight = highlight or set()
        self.slay_target = slay_target
        self.row.rect = pygame.Rect(self.layout.party_rect)
        self.row.card_size = self.layout.card_box(self.layout.party_card_w)
        self.row.selected_ids = set(selected or ())

        you = view.you
        party = you.zone("party")
        cards = list(party.cards) if party else []
        self.row.set_cards([
            (cv.id, registry.get(cv.def_id), False, cv.tapped, cv.id in highlight,
             tuple(cv.attachments or ()))
            for cv in cards
        ])

        leader_zone = you.zone("leader")
        self.leader_sprite = None
        leader_pair = None
        if leader_zone and leader_zone.cards:
            cv = leader_zone.cards[0]
            cdef = registry.get(cv.def_id)
            leader_pair = (cv, cdef)
            lr = pygame.Rect(self.layout.leader_rect)
            cw = min(lr.width - 12, int((lr.height - 26) / M.CARD_ASPECT))
            ch = int(cw * M.CARD_ASPECT)
            self.leader_sprite = CardSprite(
                cv.id, cdef,
                pygame.Rect(lr.centerx - cw // 2, lr.top + 20, cw, ch),
                tapped=cv.tapped, highlighted=cv.id in highlight, lift_on_hover=10,
            )

        self.classes = _classes_of([(cv, registry.get(cv.def_id)) for cv in cards], leader_pair)
        slain_zone = you.zone("slain")
        self.slain = len(slain_zone.cards) if slain_zone else 0

    def update(self, dt: float) -> None:
        self.row.update(dt)
        if self.leader_sprite:
            self.leader_sprite.update(dt)

    def card_at(self, pos: tuple[int, int]) -> CardSprite | None:
        if self.leader_sprite and self.leader_sprite.hit(pos):
            return self.leader_sprite
        return self.row.get_card_at(pos)

    def update_hover(self, pos: tuple[int, int]) -> CardSprite | None:
        hit = self.row.update_hover(pos)
        if self.leader_sprite:
            self.leader_sprite.hovered = self.leader_sprite.hit(pos)
            if self.leader_sprite.hovered:
                hit = self.leader_sprite
        return hit

    def clear_hover(self) -> None:
        self.row.clear_hover()
        if self.leader_sprite:
            self.leader_sprite.hovered = False

    def draw(self, screen: pygame.Surface) -> None:
        lr = pygame.Rect(self.layout.leader_rect)
        _panel_title(screen, lr, L.LEADER, icon="leader", colour=C.GOLD)
        if self.leader_sprite:
            self.leader_sprite.draw(screen)
        else:
            T.text(screen, "\u2013", lr.center, T.ui(16), C.INK_FAINT, anchor="center")

        self.row.draw(screen)

        # Progress toward the two victory conditions, along the bottom edge.
        rect = self.row.rect
        bar = pygame.Rect(rect.left + 12, rect.bottom - 15, 108, 6)
        T.progress_bar(screen, bar, len(self.classes) / max(1, len(T.CLASS_COLOURS)),
                       fill=C.ARCANE)
        T.text(screen, f"{len(self.classes)}/{len(T.CLASS_COLOURS)} clase",
               (bar.right + 8, bar.centery), T.ui(9, bold=True), C.INK_FAINT,
               anchor="midleft", shadow=None)
        bar2 = pygame.Rect(bar.left + 190, bar.top, 78, 6)
        T.progress_bar(screen, bar2, self.slain / max(1, self.slay_target), fill=C.BLOOD)
        T.text(screen, f"{self.slain}/{self.slay_target} {L.SLAYN}",
               (bar2.right + 8, bar2.centery), T.ui(9, bold=True), C.INK_FAINT,
               anchor="midleft", shadow=None)

        dot_x = rect.right - 12 - len(T.CLASS_COLOURS) * 13
        for cls in T.CLASS_COLOURS:
            has = cls in self.classes
            centre = (dot_x + 5, rect.bottom - 12)
            pygame.draw.circle(screen, T.CLASS_COLOURS[cls] if has else (190, 200, 212), centre, 5)
            if has:
                pygame.draw.circle(screen, C.INK, centre, 5, 1)
            dot_x += 13


class HandFan:
    """Your hand, fanned. Playable cards float up; the rest sit dim."""

    def __init__(self, layout: Any) -> None:
        self.layout = layout
        self.row = ZoneWidget(pygame.Rect(0, 0, 0, 0), "", mode="fan", panel=False,
                              empty_hint=L.EMPTY_HAND)
        self.playable: set[str] = set()
        self.hidden_count = 0
        self.owner_name = ""

    def sync(
        self,
        view: Any,
        registry: Any,
        *,
        playable: set[str] | None = None,
        highlight: set[str] | None = None,
        selected: set[str] | None = None,
        reveal: bool = True,
    ) -> None:
        self.playable = playable or set()
        highlight = highlight or set()
        rect = pygame.Rect(self.layout.hand_rect)
        self.row.rect = pygame.Rect(rect.left + 6, rect.top + 16, rect.width - 12, rect.height - 22)
        self.row.card_size = self.layout.card_box(self.layout.hand_card_w)
        self.row.selected_ids = set(selected or ())

        you = view.you
        hand = you.zone("hand")
        self.owner_name = you.name
        if hand is None:
            self.hidden_count = 0
            self.row.set_cards([])
            return
        if not hand.revealed or not reveal:
            self.hidden_count = hand.size
            self.row.set_cards([
                (f"__hidden_{i}", None, True, False, False, ()) for i in range(hand.size)
            ])
            return
        self.hidden_count = 0
        self.row.set_cards([
            (cv.id, registry.get(cv.def_id), False, False,
             cv.id in highlight or cv.id in self.playable, ())
            for cv in hand.cards
        ])
        for sprite in self.row.sprites:
            if self.playable and sprite.card_id not in self.playable and \
                    sprite.card_id not in highlight:
                sprite.dimmed = True

    def update(self, dt: float) -> None:
        self.row.update(dt)

    def card_at(self, pos: tuple[int, int]) -> CardSprite | None:
        return self.row.get_card_at(pos)

    def update_hover(self, pos: tuple[int, int]) -> CardSprite | None:
        return self.row.update_hover(pos)

    def clear_hover(self) -> None:
        self.row.clear_hover()

    def draw(self, screen: pygame.Surface) -> None:
        rect = pygame.Rect(self.layout.hand_rect)
        count = len(self.row.sprites)
        _panel_title(screen, rect, f"{self.owner_name} \u00b7 {L.HAND}", icon="hand",
                     right=f"{count}")
        self.row.draw(screen)


# ---------------------------------------------------------------------------
# Bottom left: dice and action points
# ---------------------------------------------------------------------------


class DicePanel:
    """Dice, the roll button, and the acting player's action points.

    Here to Slay has no mana: the spendable resource is **action points**, three
    a turn, and that is what this shows. The roll button is enabled only when
    the engine is actually waiting for a roll to be confirmed — dice in this
    game are thrown by effects, so a button that rolled whenever you liked
    would be lying about the rules.
    """

    def __init__(self, layout: Any, *, on_roll: Callable[[], None] | None = None) -> None:
        self.layout = layout
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.roll: Any = None
        self.history: list[Any] = []
        self.action_points = 0
        self.max_points = 3
        self.owner_name = ""
        self.owner_colour: tuple[int, int, int] = C.GOLD
        self.is_you = True
        self.roll_button = Button(pygame.Rect(0, 0, 0, 0), L.ROLL_READY, on_roll,
                                 icon=None, enabled=False, primary=True, shortcut="R")
        self.rolling = 0.0
        self.dice_area = pygame.Rect(0, 0, 0, 0)
        self.pulse = 0.0
        self._display_total: int | None = None
        self._scramble = 0.0

    def sync(
        self,
        view: Any,
        *,
        rolls: Sequence[Any] = (),
        acting: Any = None,
        can_roll: bool = False,
        roll_label: str = "Roll",
    ) -> None:
        self.rect = pygame.Rect(self.layout.dice_rect)
        actor = acting if acting is not None else view.players[view.active_player]
        self.action_points = actor.action_points
        self.max_points = max(3, self.action_points)
        self.owner_name = actor.name
        self.owner_colour = T.seat_colour(actor.seat)
        self.is_you = bool(actor.is_you)
        self.history = list(rolls)[-4:]
        self.roll = self.history[-1] if self.history else None
        if self.roll is not None:
            self._display_total = int(getattr(self.roll, "total", 0) or 0)
        elif not can_roll:
            self._display_total = self._display_total  # keep last shown
        else:
            self._display_total = None

        body = _panel_body(self.rect, 22)
        self.dice_area = pygame.Rect(body.left + 10, body.top + 26, body.width - 20, 62)
        self.roll_button.rect = pygame.Rect(
            body.left + 10, body.bottom - 40, body.width - 20, 34
        )
        self.roll_button.enabled = can_roll
        if can_roll:
            self.roll_button.label = L.ROLL_READY
        elif self._display_total is not None:
            self.roll_button.label = L.np_random_label(self._display_total)
        else:
            self.roll_button.label = roll_label if roll_label else L.ROLL_READY

    def update(self, dt: float) -> None:
        self.pulse += dt
        self.roll_button.update(dt)

    def handle_event(self, event: pygame.event.Event) -> bool:
        return self.roll_button.handle_event(event)

    def draw(self, screen: pygame.Surface, *, dice_hidden: bool = False) -> None:
        _panel_title(screen, self.rect, f"{L.DICE} & {L.AP}", icon="dice")

        body = _panel_body(self.rect, 22)

        pip_y = body.top + 8
        label = L.ACTION_POINTS_YOURS if self.is_you else L.ACTION_POINTS_THEIRS.format(
            name=self.owner_name
        )
        T.text(screen, label.upper(), (body.left + 10, pip_y - 2), T.ui(9, bold=True),
               T.alpha(self.owner_colour, 235), shadow=None)
        pip_x = body.right - 12 - self.max_points * 17
        for i in range(self.max_points):
            centre = (pip_x + i * 17 + 7, pip_y + 6)
            filled = i < self.action_points
            if filled:
                pygame.draw.circle(screen, C.GOLD, centre, 7)
                draw_icon(screen, "bolt", centre, 11, C.INK_DARK)
            else:
                pygame.draw.circle(screen, (190, 200, 212), centre, 7)
                pygame.draw.circle(screen, (150, 162, 178), centre, 7, 1)

        # Code-styled roll well: shows np.random("N") as the result readout.
        T.inset(screen, self.dice_area, radius=M.RADIUS)
        mono = T.mono(max(12, min(18, self.dice_area.width // 14)))
        if dice_hidden and self._display_total is None:
            T.text(screen, L.ROLL_READY, self.dice_area.center, mono,
                   T.alpha(C.INK_FAINT, 200), anchor="center", shadow=None)
        elif self._display_total is not None:
            caption = L.np_random_label(self._display_total)
            good = False
            accent = C.GOLD
            if self.roll is not None:
                good = self.roll.band_tag in ("success", "slay")
                accent = C.GOOD if good else (
                    C.BAD if self.roll.band_tag == "failure" else C.GOLD
                )
            T.text(screen, caption, self.dice_area.center, mono, accent,
                   anchor="center", shadow=None)
            if self.roll is not None and self.roll.band_tag:
                T.text(screen, str(self.roll.band_tag).upper(),
                       (self.dice_area.centerx, self.dice_area.bottom - 4),
                       T.ui(9, bold=True), accent, anchor="midbottom", shadow=None)
        else:
            T.text(screen, L.ROLL_READY, self.dice_area.center, mono,
                   C.INK_DIM, anchor="center", shadow=None)

        if len(self.history) > 1:
            y = self.dice_area.bottom + 4
            for past in reversed(self.history[:-1][-2:]):
                line = L.np_random_label(int(getattr(past, "total", 0) or 0))
                T.text(screen, line, (body.left + 10, y), T.mono(10),
                       T.alpha(C.INK_FAINT, 210), shadow=None, max_width=body.width - 20)
                y += 12

        self.roll_button.draw(screen)


# ---------------------------------------------------------------------------
# Bottom right: what is in force
# ---------------------------------------------------------------------------


@dataclass
class EffectEntry:
    """One line in the effects panel."""

    title: str
    detail: str
    icon: str
    colour: tuple[int, int, int]
    source: str = ""


class EffectsPanel:
    """Passives, equipment and flags in force for whoever is acting.

    Everything here is *derived* from the acting seat's cards and flags rather
    than from a list the engine maintains, which means a new Hero with a new
    passive shows up the moment its YAML exists.
    """

    def __init__(self, layout: Any) -> None:
        self.layout = layout
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.entries: list[EffectEntry] = []
        self.owner_name = ""
        self.owner_colour: tuple[int, int, int] = C.GOLD
        self.is_you = True
        self.scroll = 0

    def sync(self, view: Any, registry: Any, *, acting: Any = None) -> None:
        self.rect = pygame.Rect(self.layout.effects_rect)
        actor = acting if acting is not None else view.players[view.active_player]
        self.owner_name = actor.name
        self.owner_colour = T.seat_colour(actor.seat)
        self.is_you = bool(actor.is_you)
        self.entries = list(collect_effects(view, registry, actor))

    def handle_event(self, event: pygame.event.Event) -> bool:
        if event.type == pygame.MOUSEWHEEL and self.rect.collidepoint(pygame.mouse.get_pos()):
            self.scroll = max(0, min(max(0, len(self.entries) - 3), self.scroll - event.y))
            return True
        return False

    def draw(self, screen: pygame.Surface) -> None:
        who = "ale tale" if self.is_you else f"ale lui {self.owner_name}"
        _panel_title(screen, self.rect, f"{L.EFFECTS} {who}", icon="flask",
                     right=str(len(self.entries)) if self.entries else "",
                     colour=self.owner_colour)

        body = _panel_body(self.rect, 24)
        if not self.entries:
            T.text(screen, "nothing active", body.center, T.ui(11, italic=True),
                   T.alpha(C.INK_FAINT, 200), anchor="center", shadow=None)
            return

        row_h = 40
        rows = max(1, body.height // row_h)
        window = self.entries[self.scroll:self.scroll + rows]
        clip = screen.get_clip()
        screen.set_clip(body)
        for i, entry in enumerate(window):
            row = pygame.Rect(body.left + 8, body.top + i * row_h, body.width - 16, row_h - 5)
            T.round_rect(screen, row, T.alpha(entry.colour, 26), radius=8)
            T.round_rect(screen, pygame.Rect(row.left, row.top + 3, 3, row.height - 6),
                         entry.colour, radius=2)
            draw_icon(screen, entry.icon, (row.left + 18, row.top + 13), 15, entry.colour)
            T.text(screen, entry.title, (row.left + 32, row.top + 5), T.ui(11, bold=True),
                   C.INK, max_width=row.width - 40, shadow=None)
            if entry.detail:
                T.text(screen, entry.detail, (row.left + 32, row.top + 20), T.ui(9),
                       T.alpha(C.INK_DIM, 230), max_width=row.width - 40, shadow=None)
        screen.set_clip(clip)

        if len(self.entries) > rows:
            T.text(screen, f"+{len(self.entries) - rows - self.scroll} more \u00b7 scroll",
                   (body.centerx, body.bottom - 2), T.ui(9), T.alpha(C.INK_FAINT, 200),
                   anchor="midbottom", shadow=None)


#: Player flags the base pack sets, and how to phrase them for a human.
FLAG_LABELS: dict[str, tuple[str, str, str]] = {
    "uncontestable": (
        "Cannot be challenged", "Cards you play this turn cannot be challenged", "guardian",
    ),
    "extra_turn": ("Extra turn queued", "Takes another turn after this one", "bolt"),
}


def collect_effects(view: Any, registry: Any, actor: Any) -> list[EffectEntry]:
    """Everything currently modifying ``actor``, as display rows."""
    out: list[EffectEntry] = []

    leader = actor.zone("leader")
    if leader and leader.cards:
        cdef = registry.get(leader.cards[0].def_id)
        if cdef is not None:
            facts = card_facts(cdef)
            out.append(EffectEntry(
                title=f"{cdef.name} (Leader)",
                detail=cdef.text or "Party Leader",
                icon="leader",
                colour=T.class_colour(facts.card_class, "party_leader"),
                source=cdef.id,
            ))

    party = actor.zone("party")
    for cv in (party.cards if party else ()):
        cdef = registry.get(cv.def_id)
        if cdef is None:
            continue
        facts = card_facts(cdef)
        colour = T.class_colour(facts.card_class, facts.kind)
        triggers = getattr(cdef, "triggers", ()) or ()
        ability = getattr(cdef, "ability", None)
        is_passive = bool(triggers) or (
            ability is not None and getattr(ability, "activation", "") == "passive"
        )
        if is_passive:
            out.append(EffectEntry(
                title=cdef.name,
                detail=cdef.text or "Passive ability",
                icon=card_icon_name(facts.kind, facts.card_class),
                colour=colour,
                source=cdef.id,
            ))
        elif ability is not None and not cv.tapped:
            out.append(EffectEntry(
                title=cdef.name,
                detail=(
                    f"Ability ready \u00b7 {facts.threshold}+ to succeed"
                    if facts.threshold else "Ability ready"
                ),
                icon="bolt", colour=C.GOLD, source=cdef.id,
            ))
        elif ability is not None and cv.tapped:
            out.append(EffectEntry(
                title=cdef.name, detail="Ability already used this turn",
                icon="close", colour=C.IDLE, source=cdef.id,
            ))
        # Equipment attached to this Hero is a buff on the Hero, so it reads
        # as its own row pointing at its host.
        for item_id in (cv.attachments or ()):
            item_view = _find_card(view, item_id)
            item_def = registry.get(item_view.def_id) if item_view else None
            if item_def is not None:
                out.append(EffectEntry(
                    title=item_def.name,
                    detail=f"Equipped to {cdef.name} \u00b7 {item_def.text}".strip(" \u00b7"),
                    icon="item", colour=C.GOLD, source=item_def.id,
                ))

    for key, value in (actor.flags or {}).items():
        if not value:
            continue
        label = FLAG_LABELS.get(key)
        if label is not None:
            out.append(EffectEntry(label[0], label[1], label[2], C.ARCANE, key))
        else:
            out.append(EffectEntry(
                key.replace("_", " ").title(), f"flag = {value}", "flask", C.ARCANE, key
            ))

    for key, value in (view.flags or {}).items():
        if value:
            out.append(EffectEntry(
                key.replace("_", " ").title(), f"table flag = {value}", "eye", C.FROST, key
            ))
    return out


def _find_card(view: Any, card_id: str) -> Any:
    for zone in (view.zones or {}).values():
        for card in getattr(zone, "cards", ()):
            if card.id == card_id:
                return card
    for player in (view.players or {}).values():
        for zone in (player.zones or {}).values():
            for card in getattr(zone, "cards", ()):
                if card.id == card_id:
                    return card
    return None


__all__ = [
    "DEFAULT_SLAY_TARGET",
    "FLAG_LABELS",
    "ActiveStack",
    "CornerButtons",
    "DeckArea",
    "DicePanel",
    "EffectEntry",
    "EffectsPanel",
    "HandFan",
    "MonsterRow",
    "OpponentRail",
    "OpponentStrip",
    "PartyRow",
    "collect_effects",
]
