"""Tests for the pygame client.

Everything here runs headless against SDL's dummy video and audio drivers, so
the whole client is exercised in CI without a window: real surfaces, real
layout arithmetic, real event dispatch.

The tests are deliberately about the *seams* rather than pixels:

* every module imports and draws without raising, at several window sizes and
  player counts (a crash in a rarely-seen panel is the classic UI bug);
* the presenter's cross-thread contract holds, including AI seats and pause;
* the tracker turns two views into the right list of changes;
* and a whole game can be played to completion through synthesised mouse
  clicks, which is the only test that proves the board is actually operable.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from here_to_slay.ai import RandomAgent
from here_to_slay.content.loader import load_pack
from here_to_slay.core.engine import Engine
from here_to_slay.core.ids import zone_id
from here_to_slay.core.interpreter import (
    CardsChosen,
    ChooseCards,
    ChooseIntent,
    ChooseOption,
    ChoosePlayer,
    Confirm,
    Confirmed,
    Intent,
    IntentChosen,
    Option,
    OptionChosen,
    ReactionPrompt,
)
from here_to_slay.core.rolls import Modifier, Roll
from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.animations import (
    AnimationManager,
    BannerAnimation,
    CardMoveAnimation,
    ConfettiAnimation,
    DiceRollAnimation,
    EmberRainAnimation,
    FlashAnimation,
    ModifierPopAnimation,
    ParticleBurstAnimation,
    RingBurstAnimation,
    RunePulseAnimation,
    SpotlightAnimation,
    TrailAnimation,
    die_face,
)
from here_to_slay.ui.pygame.app import GameSetup
from here_to_slay.ui.pygame.art import library, procedural_art
from here_to_slay.ui.pygame.atmosphere import Atmosphere, blit_card_sheen
from here_to_slay.ui.pygame.card_renderer import (
    CARD_H,
    CARD_W,
    cache_size,
    card_facts,
    clear_card_cache,
    render_card,
    render_card_back,
)
from here_to_slay.ui.pygame.devconsole import DevConsole, DevHost, draw_fps
from here_to_slay.ui.pygame.icons import ICONS, draw_icon
from here_to_slay.ui.pygame.layout import MIN_H, MIN_W, LayoutManager
from here_to_slay.ui.pygame.overlays import (
    CardOverlay,
    GameOverOverlay,
    HandoverOverlay,
    LogOverlay,
    MenuItem,
    MenuOverlay,
    OverlayStack,
    RulesOverlay,
    ScoreRow,
    rules_pages,
)
from here_to_slay.ui.pygame.presenter import PygamePresenter
from here_to_slay.ui.pygame.scenes import _DEV_FX, GameScene, SceneHooks
from here_to_slay.ui.pygame.sound import NULL_BOARD, SoundBoard
from here_to_slay.ui.pygame.theme import C
from here_to_slay.ui.pygame.tracker import (
    BoardTracker,
    CardMoved,
    Place,
    RollHappened,
    TurnChanged,
    ZoneCountChanged,
    describe_move,
)
from here_to_slay.ui.pygame.widgets import (
    Button,
    CardSprite,
    IconButton,
    LogFeed,
    ScrollView,
    SegmentedControl,
    TextField,
    Toast,
    Tooltip,
    ZoneWidget,
)

pygame.init()
pygame.font.init()

BASE_PACK = Path(__file__).resolve().parent.parent / "data" / "base"
SIZES = ((MIN_W, MIN_H), (1280, 800), (1600, 900), (1920, 1080), (2560, 1400))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def registry():
    return load_pack(BASE_PACK)


@pytest.fixture
def engine(registry):
    return Engine.new(registry, ["Alice", "Bob"], seed=42)


@pytest.fixture
def screen():
    return pygame.Surface((1600, 900))


def _build_scene(registry, names, *, seed=7, ai_seats=0, max_turns=0, size=(1600, 900)):
    """A scene wired exactly as ``app.py`` wires it, minus the window."""
    engine = Engine.new(registry, list(names), seed=seed, max_turns=max_turns)
    order = list(engine.state.turn_order)
    human = order[: len(order) - ai_seats] if ai_seats else None
    presenter = PygamePresenter(
        engine, registry,
        human_seats=human,
        agent=RandomAgent(seed=seed) if ai_seats else None,
        ai_delay=0.0,
    )
    layout = LayoutManager(*size)
    scene = GameScene(engine, presenter, registry, layout, hooks=SceneHooks())
    return engine, presenter, scene


def _click(scene: GameScene, pos: tuple[int, int], button: int = 1) -> bool:
    scene.handle_event(pygame.event.Event(pygame.MOUSEMOTION, {"pos": pos}))
    return scene.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": pos, "button": button})
    )


def _press(scene: GameScene, key: int, mods: int = 0) -> bool:
    pygame.key.set_mods(mods)
    try:
        return scene.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": key, "mod": mods}))
    finally:
        pygame.key.set_mods(0)


def _ask(presenter: PygamePresenter, request) -> None:
    """Publish a request as the engine thread would, without a thread.

    Most scene tests care about "given this question, what can I click?", not
    about the airlock, so they post the question straight into the presenter
    and read the decision back out instead of running the engine for real.
    """
    presenter._pending_request = request
    presenter._current_decision = None


# ---------------------------------------------------------------------------
# Theme, icons, art
# ---------------------------------------------------------------------------


def test_theme_primitives(screen) -> None:
    assert T.ui(12) is T.ui(12), "fonts must be cached, they are built per frame"
    assert T.class_colour("fighter", "hero") != T.class_colour("wizard", "hero")
    assert T.seat_colour(0) != T.seat_colour(1)
    assert len(T.seat_colour(99)) == 3, "seat colours must wrap, never index-error"

    assert T.alpha(C.GOLD, 128) == (*C.GOLD, 128)
    assert T.mix((0, 0, 0), (255, 255, 255), 0.5)[0] == pytest.approx(127, abs=2)
    for k in (0.0, 0.5, 1.0):
        assert 0.0 <= T.ease_out_cubic(k) <= 1.0

    T.glass(screen, pygame.Rect(10, 10, 200, 100))
    T.round_rect(screen, pygame.Rect(10, 10, 200, 100), C.GOLD, radius=8, width=2)
    T.pill(screen, pygame.Rect(10, 130, 120, 22), "hello")
    T.progress_bar(screen, pygame.Rect(10, 160, 120, 6), 0.5)
    T.text(screen, "wrapped text " * 8, (10, 200), T.ui(12), C.INK, max_width=180)
    T.draw_wrapped(screen, "body " * 40, pygame.Rect(10, 240, 200, 90), T.ui(11), C.INK)
    screen.blit(T.vignette((320, 240)), (0, 0))


def test_every_icon_draws(screen) -> None:
    for name in ICONS:
        assert draw_icon(screen, name, (40, 40), 20, C.GOLD), name
    assert not draw_icon(screen, "no_such_icon", (40, 40), 20, C.GOLD)


def test_art_resolves_or_invents(registry) -> None:
    art = library()
    for card in registry.cards.values():
        assert art.art(card, (120, 168)).get_size() == (120, 168), card.id
        assert isinstance(art.has_art(card), bool)
    stats = art.stats()
    assert stats["resolved"] + stats["placeholders"] == len(registry.cards)

    # Placeholders must be deterministic, or a card would shimmer between frames.
    first = procedural_art("base.hero.bad_axe", "hero", "fighter", (80, 80))
    second = procedural_art("base.hero.bad_axe", "hero", "fighter", (80, 80))
    assert _pixels(first) == _pixels(second)
    other = procedural_art("base.hero.other", "hero", "fighter", (80, 80))
    assert _pixels(other) != _pixels(first), "each card needs its own sigil"


def _pixels(surface: pygame.Surface) -> bytes:
    return pygame.image.tobytes(surface, "RGBA")


# ---------------------------------------------------------------------------
# Card renderer
# ---------------------------------------------------------------------------


def test_every_card_renders(registry) -> None:
    clear_card_cache()
    for card in registry.cards.values():
        surf = render_card(card, CARD_W, CARD_H)
        assert surf.get_size() == (CARD_W, CARD_H), card.id
        facts = card_facts(card)
        assert facts.kind == card.kind
    assert cache_size() > 0
    assert render_card_back(60, 84).get_size() == (60, 84)

    hero = registry["base.hero.bad_axe"]
    assert render_card(hero, CARD_W, CARD_H, tapped=True).get_size() == (CARD_H, CARD_W)
    for kwargs in ({"highlighted": True}, {"selected": True}, {"dimmed": True},
                   {"detail": True}, {"face_down": True}):
        assert render_card(hero, CARD_W, CARD_H, **kwargs).get_size() == (CARD_W, CARD_H)

    before = cache_size()
    render_card(hero, CARD_W, CARD_H)
    assert cache_size() == before, "an identical render must hit the cache"


def test_card_facts_are_read_from_data(registry) -> None:
    """Thresholds are read off tagged roll bands, never hardcoded per card."""
    monster = registry.of_kind("monster")[0]
    facts = card_facts(monster)
    assert facts.kind == "monster"
    assert facts.threshold is None or facts.threshold > 0


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", SIZES)
def test_layout_regions_stay_on_screen(size) -> None:
    layout = LayoutManager(*size)
    window = pygame.Rect(0, 0, layout.width, layout.height)
    for name, box in layout.as_dict().items():
        rect = pygame.Rect(box)
        assert rect.width >= 0 and rect.height >= 0, name
        if name != "detail_rect":  # positioned per-hover by detail_at()
            assert window.contains(rect) or window.colliderect(rect), name

    assert layout.board_rect.top == 0
    assert layout.hand_rect.bottom <= layout.height
    assert layout.monster_row_rect.top >= layout.deck_area_rect.bottom
    assert layout.party_rect.top >= layout.monster_row_rect.bottom
    assert layout.right_rail_rect.right <= layout.width
    assert layout.hand_rect.right <= layout.effects_rect.left
    assert layout.dice_rect.right <= layout.hand_rect.left


def test_layout_clamps_and_detail_stays_visible() -> None:
    layout = LayoutManager(320, 240)
    assert (layout.width, layout.height) == (MIN_W, MIN_H)
    for anchor in (
        pygame.Rect(0, 0, 90, 130),
        pygame.Rect(layout.width - 100, layout.height - 140, 90, 130),
    ):
        detail = layout.detail_at(anchor)
        assert detail.left >= 0
        assert detail.right <= layout.width
        assert detail.bottom <= layout.height


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


def test_button_and_icon_button(screen) -> None:
    clicks: list[str] = []
    button = Button(pygame.Rect(10, 10, 160, 40), "Do it", lambda: clicks.append("go"),
                    icon="bolt", subtitle="3 AP", shortcut="1")
    button.handle_event(pygame.event.Event(pygame.MOUSEMOTION, {"pos": (20, 20)}))
    assert button.hovered
    assert button.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (20, 20), "button": 1})
    )
    assert clicks == ["go"]

    button.enabled = False
    assert not button.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, {"pos": (20, 20), "button": 1})
    )
    assert clicks == ["go"], "a disabled button must not fire"
    button.update(0.016)
    button.draw(screen)

    icon = IconButton(pygame.Rect(200, 10, 30, 30), "info", tooltip="Rules")
    icon.update(0.016)
    icon.draw(screen)
    assert icon.tooltip == "Rules"


def test_card_sprite_hover_and_zone(registry, screen) -> None:
    hero = registry["base.hero.bad_axe"]
    sprite = CardSprite("c1", hero, pygame.Rect(20, 20, 100, 140))
    assert sprite.update_hover(sprite.rect.center)
    sprite.update(0.5)
    assert sprite.draw_rect().top < sprite.rect.top, "hover must lift the card"
    assert not sprite.update_hover((0, 0))
    sprite.draw(screen)

    zone = ZoneWidget(pygame.Rect(0, 300, 600, 200), "party", mode="row")
    zone.set_cards([
        ("c1", hero, False, False, False, ()),
        ("c2", hero, False, True, True, ("att",)),
        ("c3", None, True, False, False, ()),
    ])
    assert len(zone.sprites) == 3
    assert zone.sprite("c2") is not None
    assert zone.get_card_at(zone.sprites[0].rect.center) is zone.sprites[0]
    zone.update(0.016)
    zone.draw(screen)

    fan = ZoneWidget(pygame.Rect(0, 500, 600, 200), "hand", mode="fan")
    fan.set_cards([(f"h{i}", hero, False, False, False, ()) for i in range(9)])
    fan.update(0.016)
    fan.draw(screen)
    assert all(s.rect.right <= fan.rect.right + 40 for s in fan.sprites)


def test_toast_queues_and_tooltip_stays_on_screen(screen) -> None:
    toast = Toast(pygame.Rect(10, 10, 300, 44))
    toast.show("first", 1.0, colour=C.GOLD, icon="bolt")
    toast.show("second", 1.0)
    assert toast.message == "first"
    toast.update(1.2)
    toast.update(0.016)
    assert toast.message == "second", "the queued message must follow"
    toast.draw(screen)

    tip = Tooltip()
    tip.show("a long tooltip that would run off the edge " * 2, (1590, 890))
    tip.draw(screen)
    tip.hide()
    tip.draw(screen)


def test_log_feed_and_scrollers(screen) -> None:
    feed = LogFeed(pygame.Rect(10, 10, 300, 120), limit=5)
    for i in range(12):
        feed.add(f"line {i}", colour=C.INK_DIM, icon="dice")
    assert len(feed.entries) == 5, "the feed must forget, not grow forever"
    feed.update(0.016)
    feed.draw(screen)

    view = ScrollView(pygame.Rect(10, 200, 300, 100))
    view.content_height = 900
    view.scroll_by(240)
    assert view.offset == 240
    assert view.content_top == view.rect.top - 240
    view.scroll_by(10_000)
    assert view.offset == 800, "scrolling must clamp at the bottom of the content"
    view.scroll_by(-10_000)
    assert view.offset == 0, "scrolling must clamp at the top"
    view.begin(screen)
    view.end(screen)

    changes: list[str] = []
    field = TextField(pygame.Rect(10, 320, 200, 30), placeholder="seed",
                      on_change=changes.append)
    assert field.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {"pos": (20, 330), "button": 1}
    ))
    assert field.focused
    for key, char in ((pygame.K_4, "4"), (pygame.K_2, "2"), (pygame.K_7, "7")):
        field.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": key, "unicode": char}))
    assert field.value == "427"
    field.handle_event(pygame.event.Event(
        pygame.KEYDOWN, {"key": pygame.K_BACKSPACE, "unicode": ""}
    ))
    assert field.value == "42"
    assert changes[-1] == "42"
    field.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_RETURN,
                                                           "unicode": "\r"}))
    assert not field.focused
    field.update(0.016)
    field.draw(screen)

    seg = SegmentedControl(pygame.Rect(10, 360, 300, 28), ("A", "B", "C"))
    seg.handle_event(pygame.event.Event(
        pygame.MOUSEBUTTONDOWN, {"pos": (280, 374), "button": 1}
    ))
    assert seg.index == 2
    seg.draw(screen)


# ---------------------------------------------------------------------------
# Animations
# ---------------------------------------------------------------------------


def test_animation_manager_lifecycle(registry, screen) -> None:
    hero = registry["base.hero.bad_axe"]
    fx = AnimationManager(cap=6)
    animations = [
        CardMoveAnimation(hero, (0, 0), (400, 300), (80, 112), 0.2, flip=True, spin=90),
        DiceRollAnimation((3, 4), pygame.Rect(20, 20, 160, 70), 0.2, total=7),
        ModifierPopAnimation("+2", (200, 200), C.GOOD, 0.2),
        FlashAnimation(pygame.Rect(10, 10, 90, 90), C.GOLD, 0.2),
        RingBurstAnimation((300, 300), C.BLOOD, 0.2),
        ParticleBurstAnimation((300, 300), (C.GOLD,), 0.2, count=8),
        TrailAnimation((0, 0), (200, 200), C.ROSE, 0.2),
        BannerAnimation("SLAIN", "a monster falls", colour=C.BLOOD, duration=0.2),
        ConfettiAnimation((640, 480), 0.2, count=12),
        SpotlightAnimation(pygame.Rect(100, 100, 200, 120), 0.2),
        EmberRainAnimation((320, 240), 0.2, count=8),
        RunePulseAnimation((160, 120), C.ARCANE, 0.2, radius=40),
    ]
    for anim in animations:
        fx.add(anim)
    assert fx.count() <= 6, "the cap must drop the oldest cosmetic"

    fx.shake(12.0)
    fx.flash(C.GOLD, 0.2)
    fx.update(0.016)
    assert fx.shake_offset != (0, 0)
    fx.draw(screen)
    fx.draw_top(screen)
    fx.draw_overlays(screen)

    for _ in range(40):
        fx.update(0.05)
    assert fx.count() == 0
    assert fx.shake_offset == (0, 0)

    fx.enabled = False
    fx.add(FlashAnimation(pygame.Rect(0, 0, 10, 10), C.GOLD, 0.2))
    assert fx.count() == 0, "disabling animations must actually stop them"


def test_atmosphere_and_die_faces(screen) -> None:
    table = Atmosphere()
    table.update(0.016, (1600, 900))
    layout = LayoutManager(1600, 900)
    table.draw(screen, layout)
    table.update(0.5, (1280, 800))
    table.draw(screen, LayoutManager(1280, 800))

    blit_card_sheen(screen, pygame.Rect(40, 40, 120, 168), 0.8)
    blit_card_sheen(screen, pygame.Rect(40, 40, 120, 168), 0.0)

    face = die_face(32, 5, 6, C.GOLD)
    assert face.get_size() == (32, 32)
    assert die_face(32, 5, 6, C.GOLD) is face, "die faces must be cached"


def test_delayed_animation_waits(screen) -> None:
    anim = ModifierPopAnimation("-1", (10, 10), C.BAD, 0.2, delay=0.5)
    fx = AnimationManager()
    fx.add(anim)
    fx.update(0.1)
    assert not anim.started
    fx.draw(screen)
    fx.update(0.6)
    assert anim.started


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


def test_tracker_reports_moves_and_counts(engine) -> None:
    tracker = BoardTracker()
    engine.start()
    view = engine.view("p1")
    assert tracker.poll(view) == [], "the first frame is the baseline, not news"

    # Move a card by hand: the tracker only ever sees two views.
    hand = engine.state.zone_of("hand", "p1")
    card_id = hand.top()[0]
    engine.state.move_card(card_id, zone_id("party", "p1"))
    changes = tracker.poll(engine.view("p1"))
    moves = [c for c in changes if isinstance(c, CardMoved)]
    assert len(moves) == 1
    assert moves[0].frm.zone == "hand"
    assert moves[0].to.zone == "party"
    told = describe_move(moves[0], card_name="Bad Axe", owner_name="Alice")
    assert told is not None and "Alice" in told[0]

    # An opponent's hidden hand can only report a count.
    other = engine.state.zone_of("hand", "p2")
    engine.state.move_card(other.top()[0], "discard")
    counts = [c for c in tracker.poll(engine.view("p1")) if isinstance(c, ZoneCountChanged)]
    assert any(c.zone == "hand" and c.owner == "p2" and c.delta == -1 for c in counts)


def test_tracker_reports_turns_and_rolls(engine) -> None:
    tracker = BoardTracker()
    engine.start()
    tracker.poll(engine.view("p1"))

    engine.state.turn_number += 1
    engine.state.active_player = "p2"
    changes = tracker.poll(engine.view("p1"))
    turns = [c for c in changes if isinstance(c, TurnChanged)]
    assert turns and turns[0].active_player == "p2"

    roll = Roll(id="r1", kind="ability", raw=(4, 5))
    changes = tracker.poll(engine.view("p1"), rolls=(roll,))
    assert any(isinstance(c, RollHappened) for c in changes)
    # A Modifier added to an existing roll is a separate event, not a new roll.
    roll.add(Modifier(amount=2, label="Leader"))
    changes = tracker.poll(engine.view("p1"), rolls=(roll,))
    assert [c.kind for c in changes] == ["roll_modified"]


def test_place_reads_cleanly() -> None:
    assert str(Place("party", "p1")) == "party:p1"
    assert str(Place("main_deck")) == "main_deck"


# ---------------------------------------------------------------------------
# Sound
# ---------------------------------------------------------------------------


def test_sound_is_optional_and_never_raises() -> None:
    assert NULL_BOARD.play("slay") is False
    silent = SoundBoard(enabled=False)
    for cue in silent.cue_names:
        silent.play(cue)
    assert silent.cue_names, "the cue table must not be empty"
    assert silent.play("no_such_cue") is False
    silent.set_volume(2.0)
    assert silent.volume == 1.0
    silent.toggle()
    silent.stop()


# ---------------------------------------------------------------------------
# Overlays
# ---------------------------------------------------------------------------


def test_rules_pages_come_from_content(registry) -> None:
    pages = rules_pages(registry)
    assert pages, "the rules modal must have content"
    text = " ".join(
        " ".join((line.text, line.value, *(label for label, _c, _i in line.chips)))
        for page in pages.values()
        for line in page
    )
    for action in registry.rules.actions:
        if action.enabled:
            assert action.label in text, f"{action.id} is missing from the rules page"
    for cls in registry.rules.classes:
        assert cls.title() in text, f"class {cls} is missing from the rules page"
    for victory in registry.rules.victory:
        assert victory.text in text, f"{victory.id} is missing from the rules page"
    assert str(registry.rules.turn.action_points_per_turn) in text
    assert str(registry.rules.setup.starting_hand) in text


def test_every_overlay_draws(registry, screen) -> None:
    layout = LayoutManager(1600, 900)
    stack = OverlayStack()
    rows = [
        ScoreRow("Alice", "3 slain", colour=C.GOLD, winner=True, classes=("bard", "fighter")),
        ScoreRow("Bob", "1 slain", colour=C.FROST),
    ]
    overlays = [
        RulesOverlay(layout, registry),
        CardOverlay(layout, registry["base.hero.bad_axe"]),
        LogOverlay(layout, LogFeed(pygame.Rect(0, 0, 10, 10)).entries),
        MenuOverlay(layout, [
            MenuItem("resume", "Resume", icon="play"),
            MenuItem("sound", "Sound", state=lambda: "on"),
            MenuItem("quit", "Quit", danger=True),
        ]),
        HandoverOverlay(layout, "Bob", turn=3),
        GameOverOverlay(layout, "Alice wins", rows, reason="slew 3 monsters", turns=9),
    ]
    for overlay in overlays:
        stack.push(overlay)
        stack.update(0.2)
        stack.draw(screen)
    assert stack.busy
    assert stack.has(RulesOverlay)


def test_rules_overlay_tabs_and_scroll(registry, screen) -> None:
    layout = LayoutManager(1600, 900)
    overlay = RulesOverlay(layout, registry)
    for _ in range(len(overlay.names) + 1):
        overlay.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_RIGHT}))
        overlay.update(0.05)
        overlay.draw(screen)
    assert 0 <= overlay.index < len(overlay.names)
    pygame.mouse.set_pos(layout.modal_rect.center)
    overlay.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, {"y": -4, "x": 0}))
    overlay.draw(screen)


def test_overlay_dismissal(registry, screen) -> None:
    layout = LayoutManager(1600, 900)
    stack = OverlayStack()
    card = stack.push(CardOverlay(layout, registry["base.hero.bad_axe"]))
    assert stack.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_ESCAPE}))
    for _ in range(30):
        stack.update(0.05)
    assert card.done and not stack.items

    hand_over = stack.push(HandoverOverlay(layout, "Bob"))
    stack.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_ESCAPE}))
    stack.update(0.05)
    assert not hand_over.done, "the hot-seat screen must not be escapable"
    stack.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_RETURN}))
    assert stack.update(0.05) and hand_over.result == "ready"


def test_toggle_opens_then_closes(registry) -> None:
    layout = LayoutManager(1600, 900)
    stack = OverlayStack()
    stack.toggle(lambda: RulesOverlay(layout, registry), RulesOverlay)
    assert stack.has(RulesOverlay)
    stack.toggle(lambda: RulesOverlay(layout, registry), RulesOverlay)
    assert not stack.has(RulesOverlay)


# ---------------------------------------------------------------------------
# Presenter
# ---------------------------------------------------------------------------


def test_presenter_round_trip(engine) -> None:
    presenter = PygamePresenter(engine)
    request = ChooseIntent("p1", intents=(Intent("draw"), Intent("end_turn")))
    answers: list[Any] = []

    thread = threading.Thread(target=lambda: answers.append(presenter.answer(request)),
                              daemon=True)
    thread.start()
    _spin(lambda: presenter.pending_request is not None)
    assert presenter.awaiting_human is request

    assert presenter.submit_decision(IntentChosen(Intent("draw")))
    thread.join(timeout=2.0)
    assert answers == [IntentChosen(Intent("draw"))]
    assert presenter.decisions_made == 1
    assert not presenter.submit_decision(IntentChosen(Intent("draw"))), "nothing to answer"


def test_presenter_hot_seat_transition(engine) -> None:
    presenter = PygamePresenter(engine)
    for seat in ("p1", "p2"):
        request = ChooseIntent(seat, intents=(Intent("draw"),))
        thread = threading.Thread(target=lambda r=request: presenter.answer(r), daemon=True)
        thread.start()
        if seat == "p2":
            _spin(lambda: presenter.transition_seat == "p2")
            assert presenter.awaiting_human is not None
            presenter.acknowledge_transition()
            assert presenter.transition_seat is None
        _spin(lambda: presenter.pending_request is not None)
        presenter.submit_decision(IntentChosen(Intent("draw")))
        thread.join(timeout=2.0)
    presenter.close()


def test_presenter_answers_for_ai_seats(engine) -> None:
    presenter = PygamePresenter(
        engine, human_seats=["p1"], agent=RandomAgent(seed=1), ai_delay=0.0
    )
    assert presenter.is_human("p1")
    assert not presenter.is_human("p2")

    request = ChooseIntent("p2", intents=(Intent("draw"),))
    answers: list[Any] = []
    thread = threading.Thread(target=lambda: answers.append(presenter.answer(request)),
                              daemon=True)
    thread.start()
    thread.join(timeout=2.0)
    assert answers == [IntentChosen(Intent("draw"))]
    assert presenter.thinking_seat is None
    presenter.close()


def test_presenter_pause_blocks_the_engine(engine) -> None:
    presenter = PygamePresenter(
        engine, human_seats=[], agent=RandomAgent(seed=1), ai_delay=0.0
    )
    assert presenter.toggle_pause() is True
    request = ChooseIntent("p1", intents=(Intent("draw"),))
    answers: list[Any] = []
    thread = threading.Thread(target=lambda: answers.append(presenter.answer(request)),
                              daemon=True)
    thread.start()
    thread.join(timeout=0.3)
    assert answers == [], "a paused engine must not resolve anything"
    presenter.resume()
    thread.join(timeout=2.0)
    assert len(answers) == 1
    presenter.close()


def test_presenter_drops_an_answer_to_a_superseded_question(engine) -> None:
    """A click that lands after the engine moved on must not be applied.

    Without this the answer would be validated against the *new* request, which
    is how a menu built one frame earlier crashes the engine thread.
    """
    presenter = PygamePresenter(engine)
    first = ChooseIntent("p1", intents=(Intent("draw"),))
    second = ChooseIntent("p1", intents=(Intent("draw"),))
    _ask(presenter, second)

    assert presenter.submit_decision(IntentChosen(Intent("draw")), answering=first) is False
    assert presenter._current_decision is None
    assert presenter.submit_decision(IntentChosen(Intent("draw")), answering=second) is True
    presenter.close()


def test_scene_never_answers_a_question_it_did_not_draw(registry, screen) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    status = engine.start()
    _ask(presenter, status.request)
    scene.update(0.016)
    scene.draw(screen)
    button = scene.menu.buttons[0]

    # The engine swaps to a different question between the frame and the click.
    _ask(presenter, Confirm("p1", prompt="Are you sure?"))
    _click(scene, button.rect.center)
    assert presenter._current_decision is None, "a stale menu must not reach the engine"

    # The next frame redraws for the question that is actually open.
    scene.update(0.016)
    scene.draw(screen)
    assert _press(scene, pygame.K_RETURN)
    assert presenter._current_decision == Confirmed(True)
    presenter.close()


def test_scene_ignores_a_second_click_on_an_answered_menu(registry, screen) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    status = engine.start()
    _ask(presenter, status.request)
    scene.update(0.016)
    scene.draw(screen)

    button = scene.menu.buttons[0]
    assert _click(scene, button.rect.center)
    chosen = presenter._current_decision

    # The engine has not woken yet: the same request is still pending.
    scene.update(0.016)
    assert not scene.menu.buttons, "an answered menu must not invite a second click"
    assert presenter._current_decision == chosen
    presenter.close()


def test_presenter_close_unblocks(engine) -> None:
    presenter = PygamePresenter(engine)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            presenter.answer(ChooseIntent("p1", intents=(Intent("draw"),)))
        except InterruptedError as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    _spin(lambda: presenter.pending_request is not None)
    presenter.close()
    thread.join(timeout=2.0)
    assert errors and presenter.closed


def _spin(predicate, timeout: float = 2.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the presenter")
        time.sleep(0.001)


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("players", [2, 3, 4, 5, 6])
def test_scene_draws_for_every_player_count(registry, players, screen) -> None:
    names = [f"P{i + 1}" for i in range(players)]
    engine, presenter, scene = _build_scene(registry, names)
    engine.start()
    for _ in range(3):
        scene.update(0.016)
        scene.draw(screen)
    assert len(scene.rail.strips) == players - 1, "every opponent needs a rail strip"
    assert scene.turn_chip.name, "turn chip shows the active player"
    presenter.close()


@pytest.mark.parametrize("size", SIZES)
def test_scene_draws_at_every_window_size(registry, size) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob", "Cleo"], size=size)
    engine.start()
    surface = pygame.Surface((scene.layout.width, scene.layout.height))
    scene.update(0.016)
    scene.draw(surface)
    scene.resize(1024, 700)
    scene.update(0.016)
    scene.draw(pygame.Surface((1024, 700)))
    presenter.close()


def test_scene_turn_order_starts_after_you(registry) -> None:
    """"After your turn the top player is next" — the rail is in play order."""
    engine, presenter, scene = _build_scene(registry, ["A", "B", "C", "D"])
    engine.start()
    scene.update(0.016)
    order = list(engine.state.turn_order)
    you = order.index(scene.seat)
    expected = [order[(you + i) % len(order)] for i in range(1, len(order))]
    assert [strip.player_id for strip in scene.rail.strips] == expected
    assert scene.turn_chip.turn_number >= 1
    presenter.close()


def test_scene_answers_an_intent_by_clicking(registry, screen) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    status = engine.start()
    _ask(presenter, status.request)
    scene.update(0.016)
    scene.draw(screen)

    assert isinstance(presenter.awaiting_human, ChooseIntent)
    assert scene.menu.visible, "a pending intent must offer a menu"
    labels = [button.label for button in scene.menu.buttons]
    assert labels, "the action menu must list the legal intents"
    button = scene.menu.buttons[0]
    assert _click(scene, button.rect.center)
    assert isinstance(presenter._current_decision, IntentChosen)
    presenter.close()


def test_scene_answers_choose_cards_by_clicking_a_card(registry, screen) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    engine.start()
    hand = engine.view("p1").you.zone("hand")
    candidates = tuple(card.id for card in hand.cards[:2])
    _ask(presenter, ChooseCards(
        "p1", prompt="Discard a card", candidates=candidates, minimum=1, maximum=1,
    ))
    scene.update(0.016)
    scene.draw(screen)
    assert set(scene.prompt.candidates) == set(candidates)

    sprite = scene._locate_card(candidates[0])
    assert sprite is not None, "a candidate in your own hand must be clickable"
    _click(scene, sprite.rect.center)
    assert presenter._current_decision == CardsChosen((candidates[0],))
    presenter.close()


def test_scene_offers_a_tray_for_unreachable_candidates(registry, screen) -> None:
    """A blind pick from a hidden hand has no sprite, so the tray provides one."""
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    engine.start()
    hidden = engine.state.zone_of("hand", "p2").top(2)
    _ask(presenter, ChooseCards(
        "p1", prompt="Take a card", candidates=tuple(hidden), minimum=1, maximum=1, hidden=True,
    ))
    scene.update(0.016)
    scene.draw(screen)
    assert scene.tray.visible and len(scene.tray.sprites) == 2
    assert all(s.face_down for s in scene.tray.sprites), "a hidden pick must stay hidden"
    _click(scene, scene.tray.sprites[0].rect.center)
    assert presenter._current_decision == CardsChosen((hidden[0],))
    presenter.close()


def test_scene_answers_choose_player_from_the_rail(registry, screen) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob", "Cleo"])
    engine.start()
    _ask(presenter, ChoosePlayer("p1", prompt="Pick a rival", candidates=("p2", "p3")))
    scene.update(0.016)
    scene.draw(screen)
    strip = scene.rail.strip_of("p2")
    assert strip is not None and strip.targetable
    _click(scene, strip.rect.center)
    assert presenter._current_decision is not None
    presenter.close()


def test_scene_handles_options_confirms_and_reactions(registry, screen) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    engine.start()

    _ask(presenter, ChooseOption(
        "p1", prompt="Pick one", options=(Option("a", "Alpha"), Option("b", "Beta")),
    ))
    scene.update(0.016)
    scene.draw(screen)
    assert len(scene.menu.buttons) == 2
    _click(scene, scene.menu.buttons[1].rect.center)
    assert presenter._current_decision == OptionChosen("b")

    _ask(presenter, Confirm("p1", prompt="Are you sure?"))
    scene.update(0.016)
    scene.draw(screen)
    assert _press(scene, pygame.K_RETURN)
    assert presenter._current_decision is not None

    _ask(presenter, ReactionPrompt(
        "p1", prompt="Challenge?", window="on_play", options=(Option("c1", "Challenge"),),
    ))
    scene.update(0.016)
    scene.draw(screen)
    assert scene.active.occupied, "an open window must show in the left rail"
    assert _press(scene, pygame.K_SPACE)
    assert presenter._current_decision is not None
    presenter.close()


def test_scene_hover_shows_the_enlarged_card(registry, screen) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    engine.start()
    scene.update(0.016)
    scene.draw(screen)

    sprite = scene.hand.row.sprites[0]
    scene.handle_event(pygame.event.Event(pygame.MOUSEMOTION, {"pos": sprite.rect.center}))
    assert scene.detail_card is not None
    scene.draw(screen)

    scene.handle_event(pygame.event.Event(pygame.MOUSEMOTION, {"pos": (2, 2)}))
    assert scene.detail_card is None

    # Right-click pins the card in a modal instead.
    _click(scene, sprite.rect.center, button=3)
    assert scene.overlays.has(CardOverlay)
    presenter.close()


def test_scene_hover_expands_an_opponent_strip(registry, screen) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob", "Cleo"])
    engine.start()
    scene.update(0.016)
    strip = scene.rail.strips[0]
    collapsed = strip.expand
    scene.handle_event(pygame.event.Event(pygame.MOUSEMOTION, {"pos": strip.rect.center}))
    for _ in range(30):
        scene.update(0.033)
    assert strip.expand > collapsed, "hovering a rail strip must enlarge it"
    scene.draw(screen)
    presenter.close()


def test_scene_hotkeys_open_the_overlays(registry, screen) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    engine.start()
    scene.update(0.016)

    for key, kind in ((pygame.K_i, RulesOverlay), (pygame.K_l, LogOverlay),
                      (pygame.K_ESCAPE, MenuOverlay)):
        assert _press(scene, key)
        assert scene.overlays.has(kind), kind.__name__
        scene.update(0.016)
        scene.draw(screen)
        scene.overlays.clear()
        for _ in range(20):
            scene.update(0.05)

    assert _press(scene, pygame.K_d, pygame.KMOD_LCTRL | pygame.KMOD_LSHIFT)
    assert scene.overlays.has(DevConsole)
    presenter.close()


def test_scene_reacts_to_the_board(registry, screen) -> None:
    """A move on the table must produce a log line, a sound cue and animations."""
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    engine.start()
    scene.update(0.016)
    before = len(scene.log.entries)

    card_id = engine.state.zone_of("hand", "p1").top()[0]
    engine.state.move_card(card_id, zone_id("party", "p1"))
    scene.update(0.016)
    assert len(scene.log.entries) > before
    assert scene.fx.count() > 0, "a card move must be animated"
    scene.draw(screen)
    presenter.close()


def test_scene_celebrates_a_slain_monster(registry, screen) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    engine.start()
    scene.update(0.016)

    monster_id = engine.state.zone_of("monster_row").top()[0]
    engine.state.move_card(monster_id, zone_id("slain", "p1"))
    scene.update(0.016)
    assert scene.toast.message and "slew" in scene.toast.message
    assert scene.fx.shake_offset != (0, 0) or scene.fx.count() > 0
    scene.draw(screen)
    presenter.close()


def test_scene_shows_the_game_over_screen(registry, screen) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    engine.start()
    scene.update(0.016)
    engine.state.winner = "p1"
    scene.update(0.016)
    assert scene.overlays.has(GameOverOverlay)
    scene.draw(screen)
    presenter.close()


def test_scene_shows_the_handover_screen(registry, screen) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    engine.start()
    scene.update(0.016)
    presenter._transition_target = "p2"
    scene.update(0.016)
    assert scene.overlays.has(HandoverOverlay)
    scene.draw(screen)
    _press(scene, pygame.K_RETURN)
    scene.update(0.016)
    assert presenter.transition_seat is None, "confirming must release the engine"
    presenter.close()


def test_scene_reveal_all_is_opt_in(registry, screen) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    engine.start()
    scene.update(0.016)
    assert scene.view.you.zone("hand").revealed
    assert not scene.view.players["p2"].zone("hand").revealed

    scene.flags["reveal_all"] = True
    engine.state.active_player = "p2"
    scene.update(0.016)
    scene.draw(screen)
    assert scene.seat == "p2", "spectator mode follows whoever is acting"
    presenter.close()


# ---------------------------------------------------------------------------
# Dev console
# ---------------------------------------------------------------------------


def test_scene_satisfies_the_dev_host_protocol(registry) -> None:
    _engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    assert isinstance(scene, DevHost)
    presenter.close()


def test_dev_console_tabs_and_actions(registry, screen) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    engine.start()
    scene.update(0.016)
    console = DevConsole(scene.layout, scene)

    for index in range(len(DevConsole.TABS)):
        console.index = index
        console.update(0.1)
        console.draw(screen)

    console.index = 0
    console.search.focused = True
    for key, char in ((pygame.K_a, "a"), (pygame.K_x, "x"), (pygame.K_e, "e")):
        console.handle_event(pygame.event.Event(pygame.KEYDOWN, {"key": key, "unicode": char}))
    console.update(0.1)
    console.draw(screen)
    assert console.search.value == "axe"
    assert console.filtered, "the card search must find something"
    assert len(console.filtered) < len(console.card_rows), "the search must narrow the list"
    assert all("axe" in row.haystack for row in console.filtered)
    presenter.close()


def test_dev_console_spawns_cards_and_plays_effects(registry, screen) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    engine.start()
    scene.update(0.016)

    card = registry["base.hero.bad_axe"]
    for _ in range(10):
        scene.dev_spawn_card(card)
    assert 0 < len(scene.spawned) <= 8, "the sandbox tray must not grow forever"
    scene.update(0.016)
    scene.draw(screen)
    scene.dev_clear_spawned()
    assert not scene.spawned

    assert set(scene.dev_fx_names()) == set(_DEV_FX)
    for name in scene.dev_fx_names():
        scene.dev_play_fx(name)
        scene.update(0.016)
        scene.draw(screen)
    scene.overlays.clear()

    for cue in scene.dev_sound_names():
        scene.dev_play_sound(cue)

    assert dict(scene.dev_flags())
    for flag in list(scene.dev_flags()):
        state = scene.dev_toggle(flag)
        assert scene.dev_flags()[flag] is state
        scene.dev_toggle(flag)

    assert scene.dev_toggle_pause() is True
    assert scene.dev_paused()
    scene.dev_step()
    scene.dev_toggle_pause()
    assert scene.dev_stats()
    assert scene.dev_layout_rects()
    assert scene.dev_cards()
    scene.dev_inspect_card(card)
    assert scene.overlays.has(CardOverlay)
    presenter.close()


def test_dev_console_can_start_a_new_game(registry) -> None:
    _engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    calls: list[dict] = []
    scene.hooks = SceneHooks(new_game=lambda **kw: calls.append(kw))
    scene.dev_new_game(players=5, ai_seats=4, seed=99)
    assert calls == [{"players": 5, "ai_seats": 4, "seed": 99}]
    presenter.close()


def test_fps_readout_draws(screen) -> None:
    draw_fps(screen, 59.7, "fx 3")


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------


def test_game_setup_resizes_sensibly() -> None:
    setup = GameSetup(names=("Alice", "Bob"), seed=1)
    grown = setup.resized(6, 5, "abc")
    assert grown.names == ("Alice", "Bob", "Jucător 3", "Jucător 4", "Jucător 5", "Jucător 6")
    assert grown.ai_seats == 5
    assert grown.human_names == ("Alice",)
    assert grown.seed == "abc"

    shrunk = grown.resized(2, 9, 2)
    assert shrunk.names == ("Alice", "Bob")
    assert shrunk.ai_seats == 1, "somebody has to hold the mouse"

    assert setup.resized(1, 0, 0).names == ("Alice", "Bob"), "two players is the floor"


def test_ui_scale_round_trip() -> None:
    before = T.get_scale()
    try:
        T.set_scale(0.85)
        assert T.get_scale() == 0.85
        fnt_a = T.ui(12)
        T.set_scale(1.2)
        fnt_b = T.ui(12)
        assert fnt_a.get_height() != fnt_b.get_height()
        assert T.s(10) == max(1, round(10 * 1.2))
    finally:
        T.set_scale(before)


def test_action_bar_lists_legal_intents(registry) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    engine.start()
    _ask(presenter, ChooseIntent("p1", intents=(Intent("draw"), Intent("play_hero", card="c1"))))
    scene.update(0.016)
    enabled = [c.action_id for c in scene.action_bar.chips if c.enabled]
    assert "draw" in enabled
    presenter.close()


def test_action_key_submits_draw(registry) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    engine.start()
    _ask(presenter, ChooseIntent("p1", intents=(Intent("draw"),)))
    scene.update(0.016)
    assert _press(scene, pygame.K_d)
    assert isinstance(presenter._current_decision, IntentChosen)
    assert presenter._current_decision.intent.action == "draw"
    presenter.close()


def test_action_key_enters_targeting_when_many_heroes(registry) -> None:
    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    engine.start()
    _ask(presenter, ChooseIntent(
        "p1",
        intents=(
            Intent("play_hero", card="c1"),
            Intent("play_hero", card="c2"),
        ),
    ))
    scene.update(0.016)
    assert _press(scene, pygame.K_h)
    assert scene._action_filter == "play_hero"
    presenter.close()


def test_reaction_timer_auto_passes(registry) -> None:
    import time as time_module

    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    engine.start()
    _ask(presenter, ReactionPrompt(
        "p1", prompt="Challenge?", window="on_play", options=(Option("c1", "Challenge"),),
    ))
    scene.update(0.016)
    scene._reaction_deadline = time_module.monotonic() - 0.01
    scene.update(0.016)
    assert presenter._current_decision is not None
    presenter.close()


def test_reaction_timer_freezes_while_overlay_open(registry) -> None:

    engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
    engine.start()
    _ask(presenter, ReactionPrompt(
        "p1", prompt="Challenge?", window="on_play", options=(Option("c1", "Challenge"),),
    ))
    scene.update(0.016)
    deadline = scene._reaction_deadline
    assert deadline is not None
    scene.open_rules()
    scene.update(1.0)
    assert scene._reaction_deadline > deadline
    presenter.close()


# ---------------------------------------------------------------------------
# The whole thing, driven by clicks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("players,ai_seats", [(2, 0), (4, 2), (6, 5)])
def test_a_whole_game_can_be_played_with_the_mouse(registry, players, ai_seats) -> None:
    """Drive the real client until the engine stops asking questions.

    This is the test that would catch a board you cannot actually play: it only
    ever clicks things the scene drew, and it never calls into the engine.
    """
    names = [f"P{i + 1}" for i in range(players)]
    engine, presenter, scene = _build_scene(
        registry, names, seed=17, ai_seats=ai_seats, max_turns=6,
    )
    screen = pygame.Surface((scene.layout.width, scene.layout.height))
    failures: list[BaseException] = []

    def drive_engine() -> None:
        try:
            engine.run(presenter)
        except InterruptedError:
            pass
        except BaseException as exc:  # pragma: no cover - reported below
            failures.append(exc)

    thread = threading.Thread(target=drive_engine, daemon=True)
    thread.start()

    clicks = 0
    for _ in range(4000):
        if not thread.is_alive():
            break
        scene.update(0.016)
        scene.draw(screen)
        if scene.overlays.has(HandoverOverlay):
            _press(scene, pygame.K_RETURN)
            continue
        if presenter.awaiting_human is None:
            pygame.time.wait(1)
            continue
        if _answer_with_the_mouse(scene):
            clicks += 1

    presenter.close()
    thread.join(timeout=3.0)
    assert not failures, f"engine raised while being played by mouse: {failures}"
    assert clicks > 5, "the driver never managed to click anything"
    assert engine.over or engine.state.turn_number > 1

    scene.update(0.016)
    scene.draw(screen)
    presenter.close()


def _answer_with_the_mouse(scene: GameScene) -> bool:
    """Click whatever the board is offering. Mirrors what a player can see."""
    request = scene.presenter.awaiting_human
    if isinstance(request, ChooseCards):
        for card_id in scene.prompt.candidates:
            sprite = scene._locate_card(card_id)
            if sprite is not None:
                return _click(scene, sprite.rect.center)
        if scene.tray.visible:
            return _click(scene, scene.tray.sprites[0].rect.center)
        return _press(scene, pygame.K_RETURN)
    if isinstance(request, ChoosePlayer):
        for pid in scene.prompt.players:
            strip = scene.rail.strip_of(pid)
            if strip is not None:
                return _click(scene, strip.rect.center)
        return _click(scene, scene.layout.party_rect.center)
    for button in scene.menu.buttons:
        if button.enabled:
            return _click(scene, button.rect.center)
    return _press(scene, pygame.K_RETURN)


# ---------------------------------------------------------------------------
# Phase 10 — the client under a variant pack
# ---------------------------------------------------------------------------

VARIANT_PACK = Path(__file__).resolve().parent.parent / "data" / "variants" / "overclock"


@pytest.fixture
def variant_registry():
    """The sample variant, with its plugin installed only for one test."""
    from here_to_slay.core.registry import temporarily
    from here_to_slay.modding import load_plugins

    with temporarily():
        registry = load_pack(VARIANT_PACK, search_paths=[BASE_PACK.parent])
        load_plugins(registry)
        yield registry


def test_the_board_draws_a_variant_with_a_seventh_class(variant_registry, screen) -> None:
    """A pack the UI has never seen must render, not crash.

    Every class-keyed lookup in the client falls back rather than raising, so a
    `hacker` with no colour and no icon still gets a card, a chip and a dot.
    """
    engine, presenter, scene = _build_scene(variant_registry, ["Alice", "Bob", "Cid"])
    status = engine.start()
    if getattr(status, "request", None) is not None:
        _ask(presenter, status.request)
    scene.update(0.016)
    scene.draw(screen)
    presenter.close()


def test_the_class_tracker_counts_the_rule_sets_classes_not_the_palette(
    variant_registry, registry, screen
) -> None:
    """`T.CLASS_COLOURS` is a palette, not a rule. A seven-class pack must show
    seven dots and a 0/7 bar, or the tracker quietly lies about the win."""
    _, presenter, scene = _build_scene(variant_registry, ["Alice", "Bob"])
    scene.update(0.016)
    scene.draw(screen)
    assert len(scene.party.all_classes) == 7
    assert "hacker" in scene.party.all_classes
    assert all(len(strip.all_classes) == 7 for strip in scene.rail.strips)
    presenter.close()

    _, base_presenter, base_scene = _build_scene(registry, ["Alice", "Bob"])
    base_scene.update(0.016)
    base_scene.draw(screen)
    assert len(base_scene.party.all_classes) == 6
    base_presenter.close()


def test_the_rules_screen_describes_the_loaded_pack(variant_registry) -> None:
    """The rules overlay reads `RuleSet`, so a variant's screen is the variant's."""
    from here_to_slay.ui.pygame.overlays import rules_pages

    pages = rules_pages(variant_registry)
    flat = " ".join(
        str(getattr(line, "text", "") or "") for page in pages.values() for line in page
    )
    assert "The 7 classes" in flat
    assert "Tine patru carti in cache" in flat  # the variant's own win condition


# ---------------------------------------------------------------------------
# Phase 11 — settings, saves, cues, the replay viewer, and the caches
# ---------------------------------------------------------------------------


class TestTheSettingsScreen:
    """Preferences are cosmetic, persisted, and applied the moment they change."""

    def test_every_row_names_a_real_setting(self) -> None:
        """The overlay knows no preference names; it reads them off the rows.

        Which means a row naming a field that does not exist would silently do
        nothing rather than fail — so the check belongs here.
        """
        from dataclasses import fields

        from here_to_slay.ui.pygame.overlays import SETTING_ROWS
        from here_to_slay.ui.settings import Settings

        known = {field.name for field in fields(Settings)}
        assert {row.key for row in SETTING_ROWS} <= known

    def test_clicking_a_toggle_changes_one_setting_and_reports_it(self, screen) -> None:
        from here_to_slay.ui.pygame.overlays import SettingsOverlay
        from here_to_slay.ui.settings import Settings

        layout = LayoutManager(1600, 900)
        seen: list[Any] = []
        overlay = SettingsOverlay(layout, Settings(), on_change=seen.append)

        row, _kind, button = next(
            entry for entry in overlay.buttons if entry[0].key == "sound"
        )
        assert row.key == "sound"
        button.on_click()

        assert seen and seen[-1].sound is False
        assert overlay.settings.sound is False
        assert overlay.settings.volume == Settings().volume, "one setting, not all of them"

    def test_a_range_nudges_and_clamps(self, screen) -> None:
        from here_to_slay.ui.pygame.overlays import SettingsOverlay
        from here_to_slay.ui.settings import Settings

        overlay = SettingsOverlay(LayoutManager(1600, 900), Settings(volume=0.98))
        plus = next(
            b for r, kind, b in overlay.buttons if r.key == "volume" and kind == "plus"
        )
        for _ in range(6):
            plus.on_click()
        assert overlay.settings.volume == 1.0

    def test_it_draws_at_every_size(self, screen) -> None:
        from here_to_slay.ui.pygame.overlays import SettingsOverlay
        from here_to_slay.ui.settings import Settings

        for size in SIZES:
            overlay = SettingsOverlay(LayoutManager(*size), Settings())
            overlay.update(0.05)
            overlay.draw(screen)

    def test_the_scene_applies_them(self, registry, screen) -> None:
        from here_to_slay.ui.settings import Settings

        _engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
        scene.apply_settings(
            Settings(sound=False, animations=False, shake=False, reaction_timer=False)
        )
        assert scene.flags["sound"] is False
        assert scene.flags["animations"] is False
        assert scene.flags["shake"] is False
        assert scene.fx.enabled is False
        scene.apply_settings(Settings(volume=0.3))
        assert scene.sound.volume == pytest.approx(0.3)
        presenter.close()

    def test_a_corrupt_file_loads_the_defaults(self, tmp_path: Path) -> None:
        from here_to_slay.ui.settings import Settings

        path = tmp_path / "settings.json"
        path.write_text("{ this is not json", encoding="utf-8")
        assert Settings.load(path) == Settings()

    def test_one_bad_value_does_not_discard_the_good_ones(self, tmp_path: Path) -> None:
        from here_to_slay.ui.settings import Settings

        path = tmp_path / "settings.json"
        path.write_text('{"volume": "loud", "animations": false}', encoding="utf-8")
        loaded = Settings.load(path)
        assert loaded.animations is False
        assert loaded.volume == Settings().volume

    def test_a_round_trip_is_lossless(self, tmp_path: Path) -> None:
        from here_to_slay.ui.settings import Settings

        original = Settings(sound=False, volume=0.25, ai_delay=1.5, ui_scale=0.8)
        path = tmp_path / "settings.json"
        original.save(path)
        assert Settings.load(path) == original


class TestSoundIsATable:
    """A variant's zone must be audible, and its band tag must be able to cheer.

    This is the Phase 10 class-tracker bug in another costume: the engine was
    data-driven and the board's ``if change.to.zone == "party"`` ladder was not.
    """

    def test_an_unknown_zone_falls_back_rather_than_going_silent(self) -> None:
        from here_to_slay.ui.pygame.cues import CueTable

        table = CueTable()
        assert table.zone("a_zone_nobody_has_written_yet")
        assert table.zone("slain").name == "slay"

    def test_an_unknown_band_tag_is_deliberately_silent(self) -> None:
        from here_to_slay.ui.pygame.cues import CueTable

        table = CueTable()
        assert not table.band("wibble")
        assert table.band("success").name == "success"

    def test_a_pack_may_repoint_a_cue_and_declare_a_new_voice(
        self, variant_registry
    ) -> None:
        from here_to_slay.ui.pygame.cues import CueTable
        from here_to_slay.ui.pygame.sound import SoundBoard

        table = CueTable.for_registry(variant_registry)
        assert table.zone("cache").name == "cache_write", "the pack's own zone"
        assert table.band("fail").name == "failure", "the pack's own band tag"

        board = SoundBoard(enabled=False)
        assert table.install(board) == 1
        assert "cache_write" in board.cue_names

    def test_the_base_pack_is_unchanged_by_that(self, registry) -> None:
        from here_to_slay.ui.pygame.cues import CueTable

        table = CueTable.for_registry(registry)
        assert table.zone("cache").name == "card_play", "the generic fallback"
        assert not table.band("fail")

    def test_a_declared_voice_actually_synthesises(self) -> None:
        from here_to_slay.ui.pygame.sound import recipe_from_spec

        data = recipe_from_spec({
            "layers": [
                {"duration": 0.05, "frequency": [200, 800], "wave": "square"},
                {"duration": 0.05, "frequency": 300, "wave": "noise", "seed": 3},
            ]
        })()
        assert len(data) == pytest.approx(int(0.05 * 44100) * 4, rel=0.05)

    def test_a_nonsense_spec_makes_silence_rather_than_an_exception(self) -> None:
        """A typo in a pack's audio must never be why a game will not start."""
        from here_to_slay.ui.pygame.sound import recipe_from_spec

        assert recipe_from_spec({"duration": "banana", "wave": "kazoo"})()

    def test_a_broken_sounds_file_is_skipped(self, tmp_path: Path) -> None:
        from here_to_slay.ui.pygame.cues import CueTable

        bad = tmp_path / "sounds.yaml"
        bad.write_text("cues: [this is a list, not a mapping", encoding="utf-8")
        table = CueTable()
        assert table.load_file(bad) is False
        assert table.zone("slain").name == "slay", "the base table survived"

    def test_the_board_asks_for_moments_not_cues(self) -> None:
        """Every sound the board plays goes through ``cue()``, so a pack can
        re-point it. Anything left calling the board directly is a hole."""
        import re

        source = (
            Path(__file__).resolve().parent.parent
            / "src" / "here_to_slay" / "ui" / "pygame" / "scenes.py"
        ).read_text(encoding="utf-8")
        direct = re.findall(r"self\.sound\.play\(([^)]*)\)", source)
        # Two lines legitimately name a cue rather than a moment: `cue()` itself,
        # which is the table lookup, and `dev_play_sound`, where a developer
        # picked the cue by name — the console is *for* naming cues.
        allowed = {"resolved.name, volume=resolved.volume * volume", "name"}
        assert set(direct) == allowed, f"un-tabled sound calls: {sorted(set(direct) - allowed)}"


class TestTheReplayViewer:
    """Watching a logged game is the same board with a different DecisionSource."""

    def _log(self, registry, *, turns: int = 4):
        engine = Engine.new(registry, ["Alice", "Bob"], seed="watch", max_turns=turns)
        engine.run(RandomAgent(seed=11))
        return engine.log

    def test_it_answers_every_seat_from_the_log(self, registry, screen) -> None:
        from here_to_slay.ui.pygame.replay import ReplayTransport

        log = self._log(registry)
        engine, source = Engine.replaying(registry, log)
        transport = ReplayTransport(source, interval=0.0)
        presenter = PygamePresenter(
            engine, registry, human_seats=[], agent=transport, ai_delay=0.0
        )
        layout = LayoutManager(1600, 900)
        scene = GameScene(
            engine, presenter, registry, layout, hooks=SceneHooks(), replay=transport
        )

        thread = threading.Thread(target=_quietly, args=(engine, presenter), daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and transport.position < transport.total:
            scene.update(1 / 60)
            scene.draw(screen)
            time.sleep(0.002)

        assert transport.position == transport.total == len(log)
        transport.close()
        presenter.close()
        thread.join(timeout=3)

    def test_pausing_stops_it_and_step_lets_exactly_one_through(self, registry) -> None:
        from here_to_slay.ui.pygame.replay import ReplayTransport

        log = self._log(registry)
        engine, source = Engine.replaying(registry, log)
        transport = ReplayTransport(source, interval=0.0, playing=False)
        presenter = PygamePresenter(
            engine, registry, human_seats=[], agent=transport, ai_delay=0.0
        )
        thread = threading.Thread(target=_quietly, args=(engine, presenter), daemon=True)
        thread.start()
        try:
            time.sleep(0.3)
            assert transport.position == 0, "paused means paused"

            transport.step()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and transport.position < 1:
                time.sleep(0.01)
            assert transport.position == 1

            time.sleep(0.3)
            assert transport.position == 1, "one step is one decision"
        finally:
            transport.close()
            presenter.close()
            thread.join(timeout=3)

    def test_speed_walks_the_ladder_and_stops_at_the_ends(self, registry) -> None:
        from here_to_slay.ui.pygame.replay import SPEEDS, ReplayTransport

        _engine, source = Engine.replaying(registry, self._log(registry))
        transport = ReplayTransport(source)
        for _ in range(len(SPEEDS) + 3):
            transport.faster()
        assert transport.speed == SPEEDS[-1]
        for _ in range(len(SPEEDS) + 3):
            transport.slower()
        assert transport.speed == SPEEDS[0]

    def test_the_bar_draws_and_its_buttons_drive_the_transport(
        self, registry, screen
    ) -> None:
        from here_to_slay.ui.pygame.replay import ReplayBar, ReplayTransport

        _engine, source = Engine.replaying(registry, self._log(registry))
        transport = ReplayTransport(source, playing=False)
        for size in SIZES:
            bar = ReplayBar(transport)
            bar.resize(LayoutManager(*size))
            bar.draw(screen)
            assert bar.rect.bottom <= size[1]

        bar = ReplayBar(transport)
        bar.resize(LayoutManager(1600, 900))
        play_rect = next(r for k, r, _i, _t in bar._button_rects() if k == "play")
        assert bar.handle_event(
            pygame.event.Event(
                pygame.MOUSEBUTTONDOWN, {"pos": play_rect.center, "button": 1}
            )
        )
        assert transport.playing is True
        assert bar.tooltip_at(play_rect.center)

    def test_space_is_play_pause_only_in_a_replay(self, registry) -> None:
        """Space is 'decline' during a game. A viewer has nothing to decline,
        and would otherwise have no pause key at all."""
        from here_to_slay.ui.pygame.replay import ReplayTransport

        engine, source = Engine.replaying(registry, self._log(registry))
        transport = ReplayTransport(source, playing=True)
        presenter = PygamePresenter(
            engine, registry, human_seats=[], agent=transport, ai_delay=0.0
        )
        scene = GameScene(
            engine, presenter, registry, LayoutManager(1600, 900),
            hooks=SceneHooks(), replay=transport,
        )
        assert _press(scene, pygame.K_SPACE)
        assert transport.playing is False
        assert _press(scene, pygame.K_PERIOD)
        transport.close()
        presenter.close()

    def test_a_board_with_no_replay_ignores_the_transport_keys(self, registry) -> None:
        _engine, presenter, scene = _build_scene(registry, ["Alice", "Bob"])
        assert scene.replay is None
        assert scene.replay_bar is None
        assert not scene._replay_key(pygame.K_SPACE)
        presenter.close()

    def test_the_end_of_a_partial_log_is_reported_not_crashed(self, registry) -> None:
        """A save is a partial log. Running out is the expected end of one."""
        from here_to_slay.core.errors import ReplayExhausted
        from here_to_slay.ui.pygame.replay import ReplayTransport

        full = self._log(registry, turns=6)
        partial = full.truncated(max(1, len(full) // 2))
        engine, source = Engine.replaying(registry, partial)
        transport = ReplayTransport(source, interval=0.0)

        with pytest.raises(ReplayExhausted):
            engine.run(transport)

        assert transport.exhausted
        assert not transport.playing
        assert "end of log" in transport.status_line()


class TestTheCachesAreBounded:
    """An unbounded surface cache is a leak with a long fuse: it only shows up
    after enough distinct card sizes, which is what a resize produces."""

    def test_faces_evict_once_the_limit_is_passed(self, registry) -> None:
        from here_to_slay.ui.pygame import card_renderer as CR

        clear_card_cache()
        card = next(iter(registry.cards.values()))
        for width in range(20, 20 + CR.FACE_CACHE_LIMIT + 120):
            render_card(card, width, int(width * 1.4))

        assert len(CR._face_cache) <= CR.FACE_CACHE_LIMIT
        assert len(CR._base_cache) <= CR.BASE_CACHE_LIMIT
        clear_card_cache()

    def test_the_plain_face_is_shared_by_every_flag_variant(self, registry) -> None:
        from here_to_slay.ui.pygame import card_renderer as CR

        clear_card_cache()
        card = next(iter(registry.cards.values()))
        render_card(card, 120, 168)
        composed = len(CR._base_cache)
        for flags in ({"dimmed": True}, {"highlighted": True}, {"selected": True},
                      {"tapped": True}):
            render_card(card, 120, 168, **flags)

        assert len(CR._face_cache) == 5, "five distinct results"
        assert len(CR._base_cache) == composed, "composed once"
        clear_card_cache()

    def test_glyphs_evict_too(self) -> None:
        T.clear_font_cache()
        fnt = T.ui(12)
        surface = pygame.Surface((400, 60), pygame.SRCALPHA)
        for i in range(T.GLYPH_CACHE_LIMIT + 200):
            T.text(surface, f"line {i}", (0, 0), fnt, C.INK)
        assert len(T._glyph_cache) <= T.GLYPH_CACHE_LIMIT

    def test_a_repeated_label_renders_once(self) -> None:
        """`pygame.font.Font.render` cannot be patched (its attributes are
        read-only), so the claim is checked where it is observable: the same
        string, font and colour is one cache entry and one surface object."""
        T.clear_font_cache()
        fnt = T.ui(12)
        surface = pygame.Surface((400, 60), pygame.SRCALPHA)

        first = T.render_text("Turn 4", fnt, C.INK)
        for _ in range(20):
            T.text(surface, "Turn 4", (0, 0), fnt, C.INK, shadow=None)

        assert len(T._glyph_cache) == 1
        assert T.render_text("Turn 4", fnt, C.INK) is first

    def test_an_uncacheable_font_still_renders(self) -> None:
        """A `Font` built outside `T.font` is not in the id table, and must fall
        straight through rather than being cached under somebody else's key."""
        T.clear_font_cache()
        stranger = pygame.font.SysFont("dejavusans", 14)
        assert T.render_text("hello", stranger, C.INK).get_width() > 0
        assert not T._glyph_cache

    def test_dragging_a_resize_repaints_the_felt_once_not_once_per_frame(
        self, registry
    ) -> None:
        """The 139 ms-a-frame defect.

        The table oval is a fraction of the window, so a window being dragged
        asks the size-keyed cache for a size it has never seen on every single
        frame — and painting one is a 2x-supersampled ellipse stack with a tiled
        grain layer, about 58 ms. `_Layer` stretches the art it has until the
        drag stops.
        """
        from here_to_slay.ui.pygame import atmosphere as A

        _engine, presenter, scene = _build_scene(
            registry, ["Alice", "Bob"], size=(1500, 844)
        )
        for _ in range(4):  # settle, and let the first felt be built
            scene.update(1 / 60)
            scene.draw(pygame.Surface((1500, 844)))

        widths = list(range(1520, 1760, 12))
        before = A._table.cache_info().misses
        for width in widths:  # what dragging a window edge looks like
            height = int(width * 9 / 16)
            scene.resize(width, height)
            scene.update(1 / 60)
            scene.draw(pygame.Surface((width, height)))

        rebuilds = A._table.cache_info().misses - before
        assert rebuilds <= 2, (
            f"the felt was repainted {rebuilds} times over {len(widths)} frames of "
            f"drag - _Layer is not holding"
        )
        presenter.close()


def _quietly(engine, presenter) -> None:
    """Run the engine and swallow the shutdown exceptions a viewer causes."""
    import contextlib

    with contextlib.suppress(BaseException):
        engine.run(presenter)


class TestTheWindowsHalfOfSaveAndLoad:
    """`PygameApp` owns the engine, so saving and loading are its job.

    Driven without `run()`: the frame loop is opened, stepped by hand and shut
    down, which is what lets the whole save/load path be exercised headlessly.
    """

    @pytest.fixture
    def app(self, registry, tmp_path, monkeypatch):
        from here_to_slay.ui.pygame.app import GameSetup, PygameApp

        # Never write to the developer's real profile from a test.
        monkeypatch.setenv("HTS_CONFIG_DIR", str(tmp_path / "config"))
        built = PygameApp(
            registry,
            GameSetup(names=("Alice", "Bob"), seed="appsave"),
            width=MIN_W, height=MIN_H, sound=False,
            save_dir=tmp_path / "saves",
        )
        built._open_window()
        built._begin(built.setup)
        for _ in range(300):  # let the engine thread reach its first question
            built._advance(1 / 60)
            if built.engine is not None and built.engine.pending is not None:
                break
            time.sleep(0.01)
        yield built
        built._end_game()
        built._shutdown()

    def test_it_saves_at_an_open_question(self, app, tmp_path) -> None:
        assert app.engine.pending is not None
        assert app.engine.savepoint

        message = app.save_game()

        assert "Saved" in message
        assert len(list((tmp_path / "saves").glob("*.hts.json"))) == 1
        assert len(app.list_saves()) == 1

    def test_loading_queues_a_restart_carrying_the_restored_engine(self, app) -> None:
        """The replay happens before the running game is torn down.

        A restore can fail — edited cards, a missing plugin — and a failure that
        had already destroyed the game in progress would be unforgivable.
        """
        app.save_game()
        before = app.engine.state.snapshot()
        game = app.list_saves()[0]

        app.load_game(game)

        assert app._restart is not None
        setup, engine = app._restart
        assert engine is not None
        assert engine.state.snapshot() == before
        assert setup.names == ("Alice", "Bob")

    def test_a_save_it_cannot_read_reports_instead_of_restarting(self, app) -> None:
        class Broken:
            def restore(self, _content):
                raise RuntimeError("nope")

        app.load_game(Broken())

        assert app._restart is None, "the running game must survive a failed load"

    def test_settings_reach_both_the_window_and_the_board(self, app, tmp_path) -> None:
        from here_to_slay.ui.settings import Settings

        app.save_settings(Settings(sound=False, volume=0.2, animations=False, ai_delay=1.2))

        assert app.sound.enabled is False
        assert app.sound.volume == pytest.approx(0.2)
        assert app.presenter.ai_delay == pytest.approx(1.2)
        assert app.scene.flags["animations"] is False
        assert (tmp_path / "config" / "settings.json").is_file()
        assert Settings.load().volume == pytest.approx(0.2)

    def test_a_playing_window_offers_save_and_load(self, app) -> None:
        hooks = app._hooks()
        assert hooks.save_game is not None
        assert hooks.load_game is not None
        assert hooks.new_game is not None

    def test_a_replay_window_offers_neither(self, registry, tmp_path) -> None:
        """There is no live game to write down and nothing to re-deal, and a
        menu row that cannot do anything is worse than a missing one."""
        from here_to_slay.ui.pygame.app import GameSetup, PygameApp
        from here_to_slay.ui.pygame.replay import ReplayTransport

        engine = Engine.new(registry, ["Alice", "Bob"], seed="watched", max_turns=3)
        engine.run(RandomAgent(seed=4))
        _replaying, source = Engine.replaying(registry, engine.log)
        app = PygameApp(
            registry, GameSetup(names=("Alice", "Bob")),
            sound=False, save_dir=tmp_path, replay=ReplayTransport(source),
        )
        hooks = app._hooks()
        assert hooks.save_game is None
        assert hooks.load_game is None
        assert hooks.new_game is None
        assert hooks.settings is not None, "preferences still apply while watching"
