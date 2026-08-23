"""Game scenes for the PyGame client.

Defines:
- Scene: Base class
- GameScene: Main board, zone rendering, request handling, and user input
- InterstitialScene: Hot-seat privacy transition screen
- GameOverScene: End game victory announcement
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pygame

from here_to_slay.core.interpreter import (
    CardsChosen,
    ChooseCards,
    ChooseIntent,
    ChooseOption,
    ChoosePlayer,
    Confirm,
    Confirmed,
    IntentChosen,
    OptionChosen,
    PlayerChosen,
    ReactionChosen,
    ReactionPrompt,
)
from here_to_slay.ui.pygame.animations import (
    AnimationManager,
    DiceRollAnimation,
    ModifierPopAnimation,
)
from here_to_slay.ui.pygame.card_renderer import CARD_H, CARD_W
from here_to_slay.ui.pygame.colors import (
    BG,
    BG_PANEL,
    BORDER_INACTIVE,
    BUTTON_BG,
    BUTTON_HOVER,
    HIGHLIGHT,
    INTERSTITIAL_BG,
    INTERSTITIAL_TEXT,
    TEXT_BRIGHT,
    TEXT_DIM,
    WIN_GOLD,
    get_font,
)
from here_to_slay.ui.pygame.layout import LayoutManager
from here_to_slay.ui.pygame.widgets import (
    Button,
    CardSprite,
    DiceWidget,
    PlayerBadge,
    Toast,
    ZoneWidget,
)

if TYPE_CHECKING:
    from here_to_slay.content.registry import ContentRegistry
    from here_to_slay.core.engine import Engine
    from here_to_slay.core.view import GameView
    from here_to_slay.ui.pygame.presenter import PygamePresenter


class Scene:
    """Base class for all application scenes."""

    def handle_event(self, event: pygame.event.Event) -> None:
        pass

    def update(self, dt: float) -> None:
        pass

    def draw(self, screen: pygame.Surface) -> None:
        pass

    def resize(self, w: int, h: int) -> None:
        pass


# ---------------------------------------------------------------------------
# Interstitial Scene (Seat Transition)
# ---------------------------------------------------------------------------


class InterstitialScene(Scene):
    """Hot-seat privacy screen displayed between player seat changes."""

    def __init__(self, target_player_name: str, on_continue: Any) -> None:
        self.target_player_name = target_player_name
        self.on_continue = on_continue

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type in (pygame.MOUSEBUTTONDOWN, pygame.KEYDOWN):
            self.on_continue()

    def draw(self, screen: pygame.Surface) -> None:
        w, h = screen.get_size()
        screen.fill(INTERSTITIAL_BG)

        font_title = get_font(32, bold=True)
        font_sub = get_font(18)

        title = font_title.render(
            f"Passing turn to {self.target_player_name}", True, HIGHLIGHT
        )
        sub = font_sub.render(
            "Click anywhere or press any key when ready...", True, INTERSTITIAL_TEXT
        )

        screen.blit(title, ((w - title.get_width()) // 2, h // 2 - 40))
        screen.blit(sub, ((w - sub.get_width()) // 2, h // 2 + 20))


# ---------------------------------------------------------------------------
# Game Over Scene
# ---------------------------------------------------------------------------


class GameOverScene(Scene):
    """Displayed when the game reaches a terminal state."""

    def __init__(self, winner_name: str | None, on_exit: Any) -> None:
        self.winner_name = winner_name
        self.on_exit = on_exit
        self.btn_exit = Button(pygame.Rect(0, 0, 160, 44), "Exit Game", on_exit)

    def resize(self, w: int, h: int) -> None:
        self.btn_exit.rect = pygame.Rect((w - 160) // 2, h // 2 + 50, 160, 44)

    def handle_event(self, event: pygame.event.Event) -> None:
        self.btn_exit.handle_event(event)

    def draw(self, screen: pygame.Surface) -> None:
        w, h = screen.get_size()
        self.btn_exit.rect = pygame.Rect((w - 160) // 2, h // 2 + 50, 160, 44)

        # Semi-transparent overlay
        overlay = pygame.Surface((w, h), pygame.SRCALPHA)
        overlay.fill((10, 10, 15, 230))
        screen.blit(overlay, (0, 0))

        font_big = get_font(36, bold=True)
        if self.winner_name:
            txt = font_big.render(f"🏆 {self.winner_name} Wins!", True, WIN_GOLD)
        else:
            txt = font_big.render("Game Over (Turn cap reached)", True, TEXT_BRIGHT)

        screen.blit(txt, ((w - txt.get_width()) // 2, h // 2 - 40))
        self.btn_exit.draw(screen)


# ---------------------------------------------------------------------------
# Main Game Scene
# ---------------------------------------------------------------------------


class GameScene(Scene):
    """The full interactive board game view."""

    def __init__(
        self,
        engine: Engine,
        presenter: PygamePresenter,
        registry: ContentRegistry,
        layout: LayoutManager,
    ) -> None:
        self.engine = engine
        self.presenter = presenter
        self.registry = registry
        self.layout = layout
        self.anim_mgr = AnimationManager()

        # UI Components
        self.toast = Toast(layout.toast_rect)
        self.dice_widget = DiceWidget(layout.dice_rect)

        self.zone_monsters = ZoneWidget(layout.monster_row_rect, title="Monster Row")
        self.zone_decks = ZoneWidget(layout.shared_decks_rect, title="Decks & Discard")
        self.zone_party = ZoneWidget(layout.player_party_rect, title="Your Party")
        self.zone_hand = ZoneWidget(layout.player_hand_rect, title="Your Hand")

        self.opponent_badges: list[PlayerBadge] = []
        self.leader_sprite: CardSprite | None = None

        # Request UI State
        self.menu_buttons: list[Button] = []
        self.chosen_card_ids: list[str] = []
        self._last_rolls_shown = 0

    def resize(self, w: int, h: int) -> None:
        self.layout.rebuild(w, h)
        self.toast.rect = self.layout.toast_rect
        self.dice_widget.rect = self.layout.dice_rect
        self.zone_monsters.rect = self.layout.monster_row_rect
        self.zone_decks.rect = self.layout.shared_decks_rect
        self.zone_party.rect = self.layout.player_party_rect
        self.zone_hand.rect = self.layout.player_hand_rect

    # -----------------------------------------------------------------------
    # Update & Event Loop
    # -----------------------------------------------------------------------

    def update(self, dt: float) -> None:
        self.anim_mgr.update(dt)
        self.toast.update(dt)
        self._sync_rolls()

    def _sync_rolls(self) -> None:
        rolls = self.engine.recent_rolls
        if len(rolls) > self._last_rolls_shown:
            latest = rolls[-1]
            self.dice_widget.set_roll(latest)
            if latest.raw:
                self.anim_mgr.add(
                    DiceRollAnimation(latest.raw, self.layout.dice_rect, duration=0.4)
                )
            if latest.modifiers:
                last_mod = latest.modifiers[-1]
                sign = "+" if last_mod.amount >= 0 else ""
                self.anim_mgr.add(
                    ModifierPopAnimation(
                        f"{sign}{last_mod.amount}",
                        (self.layout.dice_rect.left, self.layout.dice_rect.top - 10),
                    )
                )
            self._last_rolls_shown = len(rolls)

    def handle_event(self, event: pygame.event.Event) -> None:
        # Update mouse hovers
        if event.type == pygame.MOUSEMOTION:
            pos = event.pos
            self.zone_monsters.update_hover(pos)
            self.zone_decks.update_hover(pos)
            self.zone_party.update_hover(pos)
            self.zone_hand.update_hover(pos)
            if self.leader_sprite:
                self.leader_sprite.update_hover(pos)
            for badge in self.opponent_badges:
                badge.hovered = badge.rect.collidepoint(pos)

        # Buttons
        for btn in self.menu_buttons:
            if btn.handle_event(event):
                return

        # Click handling for pending requests
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self._handle_click(event.pos)

    def _handle_click(self, pos: tuple[int, int]) -> None:
        req = self.presenter.pending_request
        if req is None:
            return

        if isinstance(req, ChooseCards):
            card = (
                self.zone_hand.get_card_at(pos)
                or self.zone_party.get_card_at(pos)
                or self.zone_monsters.get_card_at(pos)
                or self.zone_decks.get_card_at(pos)
            )
            if card and card.card_id in req.candidates:
                cid = card.card_id
                if cid in self.chosen_card_ids:
                    self.chosen_card_ids.remove(cid)
                else:
                    if len(self.chosen_card_ids) < req.maximum:
                        self.chosen_card_ids.append(cid)
                        # Auto-submit if exact requirement reached and single-card pick
                        if (
                            req.minimum == req.maximum == 1
                            and len(self.chosen_card_ids) == 1
                        ):
                            self.presenter.submit_decision(
                                CardsChosen(tuple(self.chosen_card_ids))
                            )
                            self.chosen_card_ids.clear()
                            return
                self._rebuild_request_ui(req)

        elif isinstance(req, ChoosePlayer):
            for badge in self.opponent_badges:
                if badge.rect.collidepoint(pos) and badge.player_id in req.candidates:
                    self.presenter.submit_decision(PlayerChosen(badge.player_id))  # type: ignore[arg-type]
                    return

    # -----------------------------------------------------------------------
    # Rendering
    # -----------------------------------------------------------------------

    def draw(self, screen: pygame.Surface) -> None:
        screen.fill(BG)

        # Get the view for current player
        req = self.presenter.pending_request
        seat = req.requester if req else self.engine.state.active_player
        try:
            view = self.engine.view(seat)
        except Exception:
            return

        self._sync_view_to_widgets(view, req)

        # 1. Draw Zones
        self._draw_header(screen, view)
        self._draw_opponents(screen)
        self.zone_monsters.draw(screen)
        self.zone_decks.draw(screen)
        self._draw_leader(screen)
        self.zone_party.draw(screen)
        self.zone_hand.draw(screen)

        # 2. Draw Widgets & Overlays
        self.dice_widget.draw(screen)
        self.toast.draw(screen)
        self.anim_mgr.draw(screen)

        # 3. Draw Request Prompt & Buttons
        self._draw_request_ui(screen, req)

    def _sync_view_to_widgets(
        self, view: GameView, req: Any
    ) -> None:
        # 1. Monster Row
        m_zone = view.zone("monster_row")
        m_cards = []
        if m_zone:
            for cv in m_zone.cards:
                cdef = self.registry.get(cv.def_id)
                highlight = bool(req and isinstance(req, ChooseCards) and cv.id in req.candidates)
                m_cards.append((cv.id, cdef, False, False, highlight, ()))
        self.zone_monsters.set_cards(m_cards)

        # 2. Shared Decks
        d_cards = []
        main_d = view.zone("main_deck")
        discard_d = view.zone("discard")
        monster_d = view.zone("monster_deck")
        if main_d:
            d_cards.append(("main_deck", None, True, False, False, ()))
        if discard_d and discard_d.cards:
            top = discard_d.cards[-1]
            cdef = self.registry.get(top.def_id)
            d_cards.append((top.id, cdef, False, False, False, ()))
        if monster_d:
            d_cards.append(("monster_deck", None, True, False, False, ()))
        self.zone_decks.set_cards(d_cards)

        # 3. Your Leader
        leader_zone = view.you.zone("leader")
        if leader_zone and leader_zone.cards:
            l_cv = leader_zone.cards[0]
            ldef = self.registry.get(l_cv.def_id)
            lr = self.layout.player_leader_rect
            self.leader_sprite = CardSprite(l_cv.id, ldef, lr)
        else:
            self.leader_sprite = None

        # 4. Your Party
        party_zone = view.you.zone("party")
        p_cards = []
        if party_zone:
            for cv in party_zone.cards:
                cdef = self.registry.get(cv.def_id)
                highlight = bool(
                    req and isinstance(req, ChooseCards) and cv.id in req.candidates
                )
                if cv.id in self.chosen_card_ids:
                    highlight = True
                p_cards.append((cv.id, cdef, False, cv.tapped, highlight, cv.attachments))
        self.zone_party.set_cards(p_cards)

        # 5. Your Hand
        hand_zone = view.you.zone("hand")
        h_cards = []
        if hand_zone:
            for cv in hand_zone.cards:
                cdef = self.registry.get(cv.def_id)
                highlight = bool(
                    req and isinstance(req, ChooseCards) and cv.id in req.candidates
                )
                if cv.id in self.chosen_card_ids:
                    highlight = True
                h_cards.append((cv.id, cdef, False, False, highlight, ()))
        self.zone_hand.set_cards(h_cards)

        # 6. Opponents
        opps = view.opponents()
        self.opponent_badges.clear()
        if opps:
            avail_w = self.layout.opponents_rect.width
            badge_w = min(280, (avail_w - 10 * (len(opps) - 1)) // len(opps))
            badge_h = self.layout.opponents_rect.height
            by = self.layout.opponents_rect.top

            for i, opp in enumerate(opps):
                bx = self.layout.opponents_rect.left + i * (badge_w + 10)
                brect = pygame.Rect(bx, by, badge_w, badge_h)

                l_zone = opp.zone("leader")
                ldef = (
                    self.registry.get(l_zone.cards[0].def_id)
                    if l_zone and l_zone.cards
                    else None
                )

                p_zone = opp.zone("party")
                h_zone = opp.zone("hand")
                s_zone = opp.zone("slain")

                # Count distinct hero classes
                classes: set[str] = set()
                if ldef and getattr(ldef, "card_class", None):
                    classes.add(ldef.card_class)
                if p_zone:
                    for c in p_zone.cards:
                        cd = self.registry.get(c.def_id)
                        if cd and getattr(cd, "card_class", None):
                            classes.add(cd.card_class)

                badge = PlayerBadge(
                    brect,
                    player_id=opp.id,
                    name=opp.name,
                    action_points=opp.action_points,
                    is_active=opp.is_active,
                    leader_def=ldef,
                    hero_count=len(p_zone.cards) if p_zone else 0,
                    hand_count=h_zone.size if h_zone else 0,
                    slain_count=len(s_zone.cards) if s_zone else 0,
                    classes_present=tuple(classes),
                )
                if req and isinstance(req, ChoosePlayer) and opp.id in req.candidates:
                    badge.highlighted = True
                self.opponent_badges.append(badge)

        self._rebuild_request_ui(req)

    def _rebuild_request_ui(self, req: Any) -> None:
        self.menu_buttons.clear()
        if req is None:
            return

        if isinstance(req, ChooseIntent):
            intents = list(req.intents)
            menu_r = self.layout.action_menu_rect
            btn_h = 36
            for i, intent in enumerate(intents):
                label = intent.label or intent.key()
                by = menu_r.top + i * (btn_h + 6)
                if by + btn_h > menu_r.bottom:
                    break
                brect = pygame.Rect(menu_r.left, by, menu_r.width, btn_h)

                def make_cb(it: Any) -> Any:
                    return lambda: self.presenter.submit_decision(IntentChosen(it))

                self.menu_buttons.append(
                    Button(
                        brect,
                        label,
                        make_cb(intent),
                        bg_colour=BUTTON_BG,
                        hover_colour=BUTTON_HOVER,
                    )
                )

        elif isinstance(req, ChooseCards):
            menu_r = self.layout.action_menu_rect
            can_submit = len(self.chosen_card_ids) >= req.minimum
            btn_w = 180
            brect = pygame.Rect(
                (self.layout.width - btn_w) // 2,
                menu_r.bottom - 44,
                btn_w,
                40,
            )

            def submit_cards() -> None:
                self.presenter.submit_decision(CardsChosen(tuple(self.chosen_card_ids)))
                self.chosen_card_ids.clear()

            lbl = f"Confirm ({len(self.chosen_card_ids)}/{req.maximum})"
            self.menu_buttons.append(
                Button(
                    brect,
                    lbl,
                    submit_cards if can_submit else None,
                    enabled=can_submit,
                    bg_colour=(50, 150, 50) if can_submit else BUTTON_BG,
                )
            )

        elif isinstance(req, ChooseOption):
            menu_r = self.layout.action_menu_rect
            btn_h = 38
            for i, opt in enumerate(req.options):
                by = menu_r.top + i * (btn_h + 6)
                brect = pygame.Rect(menu_r.left, by, menu_r.width, btn_h)

                def make_opt_cb(key: str) -> Any:
                    return lambda: self.presenter.submit_decision(OptionChosen(key))

                self.menu_buttons.append(
                    Button(brect, opt.label, make_opt_cb(opt.key))
                )

        elif isinstance(req, ReactionPrompt):
            menu_r = self.layout.action_menu_rect
            btn_h = 36
            for i, opt in enumerate(req.options):
                by = menu_r.top + i * (btn_h + 6)
                brect = pygame.Rect(menu_r.left, by, menu_r.width, btn_h)
                lbl = opt.label or f"Play card {opt.card}"

                def make_react_cb(cid: Any) -> Any:
                    return lambda: self.presenter.submit_decision(ReactionChosen(cid))

                self.menu_buttons.append(Button(brect, lbl, make_react_cb(opt.card)))

            # Pass button
            pass_y = menu_r.top + len(req.options) * (btn_h + 6)
            pass_rect = pygame.Rect(menu_r.left, pass_y, menu_r.width, btn_h)
            self.menu_buttons.append(
                Button(
                    pass_rect,
                    "Pass",
                    lambda: self.presenter.submit_decision(ReactionChosen(None)),
                    bg_colour=(120, 50, 50),
                    hover_colour=(160, 60, 60),
                )
            )

        elif isinstance(req, Confirm):
            menu_r = self.layout.action_menu_rect
            btn_w = 120
            yes_rect = pygame.Rect(menu_r.centerx - btn_w - 10, menu_r.centery, btn_w, 40)
            no_rect = pygame.Rect(menu_r.centerx + 10, menu_r.centery, btn_w, 40)
            self.menu_buttons.append(
                Button(
                    yes_rect,
                    "Yes",
                    lambda: self.presenter.submit_decision(Confirmed(True)),
                    bg_colour=(40, 140, 40),
                )
            )
            self.menu_buttons.append(
                Button(
                    no_rect,
                    "No",
                    lambda: self.presenter.submit_decision(Confirmed(False)),
                    bg_colour=(140, 40, 40),
                )
            )

    # -----------------------------------------------------------------------
    # Section Drawers
    # -----------------------------------------------------------------------

    def _draw_header(self, screen: pygame.Surface, view: GameView) -> None:
        hr = self.layout.header_rect
        pygame.draw.rect(screen, BG_PANEL, hr)
        pygame.draw.line(screen, BORDER_INACTIVE, (0, hr.bottom), (hr.right, hr.bottom), 1)

        font = get_font(13, bold=True)
        active_name = view.players[view.active_player].name
        hdr_str = (
            f"Turn {view.turn_number}  |  Phase: {view.phase.upper()}  |  Active: {active_name}"
        )
        txt = font.render(hdr_str, True, TEXT_BRIGHT)
        screen.blit(txt, (14, (hr.height - txt.get_height()) // 2))

        # AP Display
        ap_str = f"Your AP: {view.you.action_points}"
        ap_txt = font.render(ap_str, True, WIN_GOLD if view.is_your_turn else TEXT_DIM)
        ap_x = hr.right - ap_txt.get_width() - 14
        ap_y = (hr.height - ap_txt.get_height()) // 2
        screen.blit(ap_txt, (ap_x, ap_y))

    def _draw_opponents(self, screen: pygame.Surface) -> None:
        for badge in self.opponent_badges:
            badge.draw(screen)

    def _draw_leader(self, screen: pygame.Surface) -> None:
        lr = self.layout.player_leader_rect
        pygame.draw.rect(screen, BG_PANEL, lr, border_radius=8)
        pygame.draw.rect(screen, BORDER_INACTIVE, lr, 1, border_radius=8)

        font = get_font(11, bold=True)
        txt = font.render("Leader", True, TEXT_DIM)
        screen.blit(txt, (lr.left + 6, lr.top + 2))

        if self.leader_sprite:
            # Draw smaller leader card
            cw = min(lr.width - 12, CARD_W)
            ch = min(lr.height - 24, CARD_H)
            self.leader_sprite.rect = pygame.Rect(
                lr.left + (lr.width - cw) // 2,
                lr.top + 18,
                cw,
                ch,
            )
            self.leader_sprite.draw(screen)

    def _draw_request_ui(self, screen: pygame.Surface, req: Any) -> None:
        if req is None:
            return

        # Prompt Banner
        pr = self.layout.prompt_rect
        pygame.draw.rect(screen, (30, 30, 42, 220), pr, border_radius=8)
        pygame.draw.rect(screen, HIGHLIGHT, pr, 2, border_radius=8)

        prompt_str = req.prompt or "What do you do?"
        if isinstance(req, ChooseCards):
            prompt_str = req.prompt or f"Choose {req.minimum}..{req.maximum} card(s):"

        font = get_font(14, bold=True)
        ptxt = font.render(prompt_str, True, TEXT_BRIGHT)
        screen.blit(ptxt, (pr.centerx - ptxt.get_width() // 2, pr.centery - ptxt.get_height() // 2))

        # Menu Buttons
        for btn in self.menu_buttons:
            btn.draw(screen)


__all__ = ["GameOverScene", "GameScene", "InterstitialScene", "Scene"]
