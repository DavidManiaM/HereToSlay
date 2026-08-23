"""Tests for the PyGame UI layer (Phase 9).

Tests procedural card rendering, widgets, animations, layout manager,
and the cross-thread presenter DecisionSource without requiring a GUI window.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pygame
import pytest

from here_to_slay.content.loader import load_pack
from here_to_slay.core.engine import Engine
from here_to_slay.core.interpreter import (
    ChooseCards,
    ChooseIntent,
    ChooseOption,
    ChoosePlayer,
    Intent,
    IntentChosen,
    Option,
    OptionChosen,
)
from here_to_slay.core.rolls import Modifier, Roll
from here_to_slay.ui.pygame.animations import (
    AnimationManager,
    CardMoveAnimation,
    DiceRollAnimation,
    FlashAnimation,
    ModifierPopAnimation,
)
from here_to_slay.ui.pygame.card_renderer import (
    CARD_H,
    CARD_W,
    clear_card_cache,
    render_card,
    render_card_back,
)
from here_to_slay.ui.pygame.colors import CLASS_COLOURS, KIND_COLOURS, get_font
from here_to_slay.ui.pygame.layout import LayoutManager
from here_to_slay.ui.pygame.presenter import PygamePresenter
from here_to_slay.ui.pygame.scenes import GameOverScene, GameScene, InterstitialScene
from here_to_slay.ui.pygame.widgets import (
    Button,
    DiceWidget,
    PlayerBadge,
    Toast,
    ZoneWidget,
)

# Ensure headless pygame runs cleanly on any platform / CI
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

pygame.init()
pygame.font.init()

BASE_PACK = Path(__file__).resolve().parent.parent / "data" / "base"


@pytest.fixture
def registry():
    return load_pack(BASE_PACK)


@pytest.fixture
def engine(registry):
    return Engine.new(registry, ["Alice", "Bob"], seed=42)


# ---------------------------------------------------------------------------
# 1. Colors & Fonts
# ---------------------------------------------------------------------------


def test_colors_and_fonts() -> None:
    font = get_font(14, bold=True)
    assert font is not None
    assert "fighter" in CLASS_COLOURS
    assert "hero" in KIND_COLOURS


# ---------------------------------------------------------------------------
# 2. Card Renderer
# ---------------------------------------------------------------------------


def test_card_renderer_procedural(registry) -> None:
    clear_card_cache()

    # Render card back
    back_surf = render_card_back(CARD_W, CARD_H)
    assert back_surf.get_size() == (CARD_W, CARD_H)

    # Render Hero card
    hero_def = registry["base.hero.bad_axe"]
    hero_surf = render_card(hero_def, CARD_W, CARD_H)
    assert hero_surf.get_size() == (CARD_W, CARD_H)

    # Render Monster card
    monster_def = registry.of_kind("monster")[0]
    monster_surf = render_card(monster_def, CARD_W, CARD_H)
    assert monster_surf.get_size() == (CARD_W, CARD_H)

    # Render Leader card
    leader_def = registry.of_kind("party_leader")[0]
    leader_surf = render_card(leader_def, CARD_W, CARD_H)
    assert leader_surf.get_size() == (CARD_W, CARD_H)

    # Render variants: tapped, highlighted
    tapped_surf = render_card(hero_def, CARD_W, CARD_H, tapped=True)
    assert tapped_surf.get_size() == (CARD_H, CARD_W)  # 90 deg rotation swaps w & h

    high_surf = render_card(hero_def, CARD_W, CARD_H, highlighted=True)
    assert high_surf.get_size() == (CARD_W, CARD_H)


# ---------------------------------------------------------------------------
# 3. Layout Manager
# ---------------------------------------------------------------------------


def test_layout_manager_resizing() -> None:
    layout = LayoutManager(1280, 800)
    assert layout.header_rect.width == 1280
    assert layout.player_hand_rect.bottom <= 800

    layout.rebuild(1920, 1080)
    assert layout.header_rect.width == 1920
    assert layout.player_party_rect.top > layout.monster_row_rect.bottom
    assert layout.opponents_rect.width > 0


# ---------------------------------------------------------------------------
# 4. Widgets
# ---------------------------------------------------------------------------


def test_button_widget() -> None:
    clicked = []
    btn = Button(pygame.Rect(10, 10, 100, 40), "Click Me", on_click=lambda: clicked.append(True))

    surf = pygame.Surface((200, 200))
    btn.draw(surf)

    # Mouse motion over button
    ev_motion = pygame.event.Event(pygame.MOUSEMOTION, {"pos": (20, 20)})
    btn.handle_event(ev_motion)
    assert btn.hovered is True

    # Mouse click on button
    ev_click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (20, 20), "button": 1})
    handled = btn.handle_event(ev_click)
    assert handled is True
    assert clicked == [True]


def test_zone_widget_and_card_sprite(registry) -> None:
    zone = ZoneWidget(pygame.Rect(0, 0, 400, 150), title="Test Zone")
    hero = registry["base.hero.bad_axe"]

    cards_data = [
        ("c1", hero, False, False, False, ()),
        ("c2", hero, False, True, True, ("att1",)),
    ]
    zone.set_cards(cards_data)
    assert len(zone.sprites) == 2

    # Test hit test
    sprite0 = zone.sprites[0]
    hit = zone.get_card_at(sprite0.rect.center)
    assert hit is sprite0

    surf = pygame.Surface((400, 150))
    zone.draw(surf)


def test_dice_and_toast_widgets() -> None:
    surf = pygame.Surface((300, 300))

    # Toast
    toast = Toast(pygame.Rect(10, 10, 200, 40))
    toast.show("Action Complete!", duration=2.0)
    assert toast.timer == 2.0
    toast.draw(surf)
    toast.update(1.0)
    assert toast.timer == 1.0

    # DiceWidget
    dice_w = DiceWidget(pygame.Rect(10, 60, 200, 120))
    roll = Roll(id="r1", kind="ability", raw=(4, 5))
    roll.add(Modifier(amount=2, label="Leader"))
    roll.band_tag = "success"
    dice_w.set_roll(roll)
    dice_w.draw(surf)


def test_player_badge(registry) -> None:
    leader = registry.of_kind("party_leader")[0]
    badge = PlayerBadge(
        rect=pygame.Rect(0, 0, 240, 90),
        player_id="p2",
        name="Bob",
        action_points=3,
        is_active=True,
        leader_def=leader,
        hero_count=2,
        hand_count=5,
        slain_count=1,
        classes_present=("fighter", "bard"),
    )
    surf = pygame.Surface((300, 200))
    badge.draw(surf)


# ---------------------------------------------------------------------------
# 5. Animations
# ---------------------------------------------------------------------------


def test_animation_manager(registry) -> None:
    mgr = AnimationManager()
    hero = registry["base.hero.bad_axe"]

    move_anim = CardMoveAnimation(hero, (0, 0), (100, 100), duration=0.2)
    dice_anim = DiceRollAnimation((3, 4), pygame.Rect(0, 0, 100, 100), duration=0.2)
    pop_anim = ModifierPopAnimation("+2", (50, 50), duration=0.2)
    flash_anim = FlashAnimation(pygame.Rect(10, 10, 50, 50), duration=0.2)

    mgr.add(move_anim)
    mgr.add(dice_anim)
    mgr.add(pop_anim)
    mgr.add(flash_anim)

    surf = pygame.Surface((300, 300))
    mgr.draw(surf)
    assert len(mgr.animations) == 4

    # Advance past duration
    mgr.update(0.3)
    assert len(mgr.animations) == 0


# ---------------------------------------------------------------------------
# 6. PygamePresenter (Cross-Thread DecisionSource)
# ---------------------------------------------------------------------------


def test_presenter_cross_thread_round_trip(engine) -> None:
    presenter = PygamePresenter(engine)
    request = ChooseIntent("p1", intents=(Intent("draw"), Intent("end_turn")))

    decision_received = []

    def engine_worker():
        dec = presenter.answer(request)
        decision_received.append(dec)

    thread = threading.Thread(target=engine_worker, daemon=True)
    thread.start()

    # Wait until presenter sees request
    while presenter.pending_request is None:
        pass

    assert presenter.pending_request == request

    # Submit decision from GUI thread
    presenter.submit_decision(IntentChosen(Intent("draw")))
    thread.join(timeout=2.0)

    assert len(decision_received) == 1
    assert decision_received[0] == IntentChosen(Intent("draw"))


def test_presenter_seat_transition(engine) -> None:
    presenter = PygamePresenter(engine)
    req1 = ChooseIntent("p1", intents=(Intent("draw"),))
    req2 = ChooseIntent("p2", intents=(Intent("draw"),))

    # First request sets _last_seat to p1
    def worker1():
        presenter.answer(req1)

    t1 = threading.Thread(target=worker1, daemon=True)
    t1.start()
    while presenter.pending_request is None:
        pass
    presenter.submit_decision(IntentChosen(Intent("draw")))
    t1.join()

    # Second request with different player triggers transition
    def worker2():
        presenter.answer(req2)

    t2 = threading.Thread(target=worker2, daemon=True)
    t2.start()

    # Wait until transition is flagged
    while presenter.transition_seat is None:
        pass
    assert presenter.transition_seat == "p2"

    # Acknowledge transition
    presenter.acknowledge_transition()
    assert presenter.transition_seat is None

    presenter.submit_decision(IntentChosen(Intent("draw")))
    t2.join()
    presenter.close()


# ---------------------------------------------------------------------------
# 7. Scenes
# ---------------------------------------------------------------------------


def test_scenes_rendering_and_events(engine, registry) -> None:
    layout = LayoutManager(1280, 800)
    presenter = PygamePresenter(engine, registry)
    game_scene = GameScene(engine, presenter, registry, layout)

    screen = pygame.Surface((1280, 800))
    game_scene.draw(screen)

    # Test ChooseOption request UI
    opt_req = ChooseOption("p1", options=(Option("opt1", "Option 1"), Option("opt2", "Option 2")))
    presenter._pending_request = opt_req
    game_scene.draw(screen)
    assert len(game_scene.menu_buttons) == 2

    # Click button
    btn = game_scene.menu_buttons[0]
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": btn.rect.center, "button": 1})
    game_scene.handle_event(ev)
    assert presenter._current_decision == OptionChosen("opt1")

    # Test InterstitialScene
    continued = []
    inter_scene = InterstitialScene("Bob", lambda: continued.append(True))
    inter_scene.draw(screen)
    inter_scene.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (100, 100), "button": 1}))
    assert continued == [True]

    # Test GameOverScene
    exited = []
    go_scene = GameOverScene("Alice", lambda: exited.append(True))
    go_scene.resize(1280, 800)
    go_scene.draw(screen)
    go_scene.handle_event(
        pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, {"pos": go_scene.btn_exit.rect.center, "button": 1}
        )
    )
    assert exited == [True]
    presenter.close()


def test_mouse_driven_game_loop(registry) -> None:
    """Test full mouse-driven game loop running headlessly with real events."""
    engine = Engine.new(registry, ["Alice", "Bob"], seed=100, max_turns=3)
    presenter = PygamePresenter(engine, registry)
    layout = LayoutManager(1280, 800)
    game_scene = GameScene(engine, presenter, registry, layout)
    screen = pygame.Surface((1280, 800))

    engine_error = []
    status_box = []

    def engine_thread_fn():
        try:
            res = engine.run(presenter)
            status_box.append(res)
        except Exception as e:
            engine_error.append(e)

    th = threading.Thread(target=engine_thread_fn, daemon=True)
    th.start()

    # Driver loop simulating GUI ticks and mouse clicks
    max_steps = 100
    steps = 0
    while th.is_alive() and steps < max_steps:
        steps += 1
        game_scene.update(0.016)
        game_scene.draw(screen)

        # Handle seat transitions
        if presenter.transition_seat:
            presenter.acknowledge_transition()
            continue

        req = presenter.pending_request
        if req is not None:
            # Click first legal button or card
            if game_scene.menu_buttons:
                btn = game_scene.menu_buttons[0]
                ev = pygame.event.Event(
                    pygame.MOUSEBUTTONDOWN, {"pos": btn.rect.center, "button": 1}
                )
                game_scene.handle_event(ev)
            elif isinstance(req, ChooseCards) and req.candidates:
                # Find the card sprite and click it
                target_id = req.candidates[0]
                clicked = False
                for zone in (
                    game_scene.zone_hand,
                    game_scene.zone_party,
                    game_scene.zone_monsters,
                ):
                    for sp in zone.sprites:
                        if sp.card_id == target_id:
                            ev = pygame.event.Event(
                                pygame.MOUSEBUTTONDOWN, {"pos": sp.rect.center, "button": 1}
                            )
                            game_scene.handle_event(ev)
                            clicked = True
                            break
                    if clicked:
                        break
            elif isinstance(req, ChoosePlayer) and game_scene.opponent_badges:
                badge = game_scene.opponent_badges[0]
                ev = pygame.event.Event(
                    pygame.MOUSEBUTTONDOWN, {"pos": badge.rect.center, "button": 1}
                )
                game_scene.handle_event(ev)

        pygame.time.wait(10)

    presenter.close()
    th.join(timeout=3.0)
    assert not engine_error, f"Engine raised error during mouse-driven game: {engine_error}"
    assert steps > 0
