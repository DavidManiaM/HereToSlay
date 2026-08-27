"""The board scene: panels, the open question, and everything that reacts.

One scene draws the whole game. It owns the nine panels, the animation queue,
the log, the modal stack and the dev console, and it is the only place that
turns a click into a :class:`Decision`.

The loop each frame is deliberately one-directional:

1. read a fresh :class:`~here_to_slay.core.view.GameView` for the seat in focus;
2. diff it against last frame (``tracker.py``) and turn the differences into
   animations, sounds and log lines;
3. push the view into the panels, which are pure renderers;
4. read the presenter's open request and mark up the board it implies;
5. translate input into a decision and submit it.

Nothing here mutates game state. Step 5 is the only way information travels
back, which is what lets the same engine run under the CLI, an agent and a test
harness unchanged (``docs/architecture_notes.md §1``).

The one deliberate compromise is worth naming: the engine runs on another
thread, so ``engine.view()`` can occasionally be read mid-mutation. A torn read
raises, is caught, and the previous frame's view is drawn again — a stale frame
for 16 ms rather than a lock in the engine or a half-built board on screen.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pygame

from here_to_slay.core.interpreter import (
    CardsChosen,
    ChooseCards,
    ChooseIntent,
    ChooseOption,
    ChoosePlayer,
    Confirm,
    Confirmed,
    Decision,
    Intent,
    IntentChosen,
    Option,
    OptionChosen,
    PlayerChosen,
    ReactionChosen,
    ReactionPrompt,
    Request,
)
from here_to_slay.ui import lexicon as L
from here_to_slay.ui.pygame import materials
from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.animations import (
    AnimationManager,
    APSpendAnimation,
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
    TableLightSweep,
    TrailAnimation,
)
from here_to_slay.ui.pygame.art import library as art_library
from here_to_slay.ui.pygame.atmosphere import Atmosphere
from here_to_slay.ui.pygame.cameras import CameraDirector, CameraKind
from here_to_slay.ui.pygame.card_renderer import (
    cache_size,
    card_facts,
    clear_card_cache,
    render_card,
)
from here_to_slay.ui.pygame.cues import CueTable
from here_to_slay.ui.pygame.devconsole import DevConsole, draw_fps, draw_layout_debug
from here_to_slay.ui.pygame.icons import card_icon_name, draw_icon
from here_to_slay.ui.pygame.keybinds import ACTION_KEYS, DISPLAY_COSTS, hotkey_for, key_to_action
from here_to_slay.ui.pygame.overlays import (
    CardOverlay,
    GameOverOverlay,
    HandoverOverlay,
    LogOverlay,
    MenuItem,
    MenuOverlay,
    OverlayStack,
    RulesOverlay,
    SaveListOverlay,
    ScoreRow,
    SettingsOverlay,
)
from here_to_slay.ui.pygame.panels import (
    DEFAULT_SLAY_TARGET,
    REACTION_SECONDS,
    ActionBar,
    ActiveStack,
    CornerButtons,
    DeckArea,
    DicePanel,
    EffectsPanel,
    HandFan,
    MonsterRow,
    OpponentRail,
    PartyRow,
    ReactionTimer,
    TurnChip,
    _play_order,
)
from here_to_slay.ui.pygame.replay import ReplayBar, ReplayTransport
from here_to_slay.ui.pygame.sound import NULL_BOARD, SoundBoard
from here_to_slay.ui.pygame.theme import C, M
from here_to_slay.ui.pygame.tracker import (
    BoardTracker,
    CardAppeared,
    CardMoved,
    CardVanished,
    GameWon,
    PhaseChanged,
    PointsChanged,
    RollHappened,
    RollModified,
    TurnChanged,
    ZoneCountChanged,
    describe_move,
)
from here_to_slay.ui.pygame.widgets import Button, CardSprite, LogFeed, Toast, Tooltip

#: Actions that throw dice. The roll button offers whichever of these is legal,
#: because in this game dice are thrown *by effects* — a button that rolled
#: whenever you liked would be lying about the rules.
ROLL_ACTIONS = frozenset({"attack_monster", "use_hero_ability", "use_leader_ability"})

#: action id -> (icon, accent). Unknown actions fall back to a neutral chip, so
#: a variant's new action is listed rather than hidden.
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

#: role -> colour, shared by the log and the toast so a "bad" line is always red.
ROLE_COLOURS = {
    "good": C.GOOD, "bad": C.BAD, "warn": C.WARN, "dim": C.INK_DIM, "info": C.INFO,
}


@dataclass
class SceneHooks:
    """What the scene needs from whoever owns the window.

    Restarting a game means building a new ``Engine``, which the scene must not
    do — it would have to own the content pack, the seat list and the thread. So
    it asks.
    """

    new_game: Callable[..., None] | None = None
    quit: Callable[[], None] | None = None
    toggle_fullscreen: Callable[[], None] | None = None
    #: Capture the game and write it. Returns the line to show; raises
    #: ``SaveError`` when the engine is mid-action and a save would be a lie.
    save_game: Callable[[], str] | None = None
    #: Every readable save, newest first — the rows of the load screen.
    list_saves: Callable[[], Sequence[Any]] | None = None
    #: Restore one. Like ``new_game``, it replaces the engine, so it is the
    #: window's job rather than the board's.
    load_game: Callable[[Any], None] | None = None
    #: Current preferences, and where an edited copy goes. ``apply`` fires on
    #: every click so the volume slider is audible; ``save`` fires once, when
    #: the screen closes, because a disk write per nudge is absurd.
    settings: Callable[[], Any] | None = None
    save_settings: Callable[[Any], None] | None = None


@dataclass
class _Prompt:
    """The open question, reduced to what the board needs to draw."""

    request: Request | None = None
    text: str = ""
    hint: str = ""
    accent: tuple[int, int, int] = C.GOLD
    icon: str = "info"
    candidates: tuple[str, ...] = ()
    players: tuple[str, ...] = ()
    minimum: int = 0
    maximum: int = 0
    intents_by_card: dict[str, list[Intent]] = field(default_factory=dict)
    hidden: bool = False


class ActionMenu:
    """The floating list of things you may do, and the confirm/pass row."""

    def __init__(self, rect: pygame.Rect) -> None:
        self.rect = pygame.Rect(rect)
        self.title = ""
        self.buttons: list[Button] = []
        self.scroll = 0
        self.visible = False

    def build(
        self,
        title: str,
        entries: Sequence[tuple[str, str, str | None, tuple[int, int, int], Callable[[], None]]],
        *,
        rect: pygame.Rect | None = None,
    ) -> None:
        """``entries`` are ``(label, subtitle, icon, accent, on_click)``.

        Buttons are laid out top-down inside ``rect``; if they do not fit, the
        list scrolls. The panel itself never draws past ``rect.bottom``.
        """
        if rect is not None:
            self.rect = pygame.Rect(rect)
        self.title = title
        self.scroll = 0
        self.buttons = []
        if not entries or self.rect.height < 40:
            self.visible = False
            return
        row_h = 36 if len(entries) <= 6 else 30
        top = self.rect.top + 30
        width = max(40, self.rect.width - 24)
        for i, (label, subtitle, icon, accent, action) in enumerate(entries):
            self.buttons.append(Button(
                pygame.Rect(self.rect.left + 12, top + i * (row_h + 4), width, row_h),
                label, action, icon=icon, subtitle=subtitle, accent=accent,
                align="left", shortcut=str(i + 1) if i < 9 else "",
                primary=i == 0 and len(entries) == 1,
            ))
        self.visible = True

    def clear(self) -> None:
        self.buttons = []
        self.visible = False

    @property
    def content_bottom(self) -> int:
        return self.buttons[-1].rect.bottom + 10 if self.buttons else self.rect.top + 36

    def desired_height(self, entry_count: int) -> int:
        """Natural height for ``entry_count`` rows (before flex clamp)."""
        if entry_count <= 0:
            return 0
        row_h = 36 if entry_count <= 6 else 30
        return 30 + entry_count * (row_h + 4) + 10

    def handle_event(self, event: pygame.event.Event) -> bool:
        if not self.visible or self.rect.height < 20:
            return False
        if event.type == pygame.MOUSEWHEEL and self.rect.collidepoint(pygame.mouse.get_pos()):
            span = max(0, self.content_bottom - self.rect.bottom)
            self.scroll = max(-span, min(0, self.scroll + event.y * 40))
            return True
        for button in self.buttons:
            moved = pygame.Rect(button.rect)
            button.rect.top += self.scroll
            hit = button.handle_event(event)
            button.rect = moved
            if hit:
                return True
        return False

    def press(self, index: int) -> bool:
        if 0 <= index < len(self.buttons):
            button = self.buttons[index]
            if button.enabled and button.on_click:
                button.on_click()
                return True
        return False

    def update(self, dt: float) -> None:
        for button in self.buttons:
            button.update(dt)

    def draw(self, screen: pygame.Surface) -> None:
        if not self.visible or self.rect.height < 20:
            return
        # Always draw exactly inside the packed slot — bottom flush with effects.
        panel = pygame.Rect(self.rect)
        T.glass(screen, panel, radius=M.RADIUS_L, fill=C.GLASS,
                rim=T.alpha(C.GOLD, 160))
        T.text(screen, self.title.upper(), (panel.left + 14, panel.top + 8),
               T.ui(10, bold=True), C.GOLD, shadow=None, max_width=panel.width - 28)

        clip = screen.get_clip()
        screen.set_clip(panel)
        for button in self.buttons:
            saved = pygame.Rect(button.rect)
            button.rect.top += self.scroll
            if button.rect.bottom > panel.top and button.rect.top < panel.bottom:
                button.draw(screen)
            button.rect = saved
        screen.set_clip(clip)
        if self.content_bottom + self.scroll > panel.bottom + 2:
            T.text(
                screen, "▾",
                (panel.centerx, panel.bottom - 2),
                T.ui(10, bold=True), T.alpha(C.INK_FAINT, 200),
                anchor="midbottom", shadow=None,
            )


class PickTray:
    """A chooser for candidates that are not on the board.

    A blind pick from an opponent's hand has no sprite to click — the cards are
    hidden by design. Rather than leave the player with no way to answer, the
    tray lays out one face-down card per candidate.
    """

    def __init__(self) -> None:
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.sprites: list[CardSprite] = []
        self.title = ""

    def build(
        self,
        anchor: pygame.Rect,
        cards: Sequence[tuple[str, Any]],
        selected: set[str],
        *,
        title: str = "choose",
    ) -> None:
        self.title = title
        self.sprites = []
        if not cards:
            self.rect = pygame.Rect(0, 0, 0, 0)
            return
        cw = min(96, max(56, (anchor.width - 40) // max(1, len(cards)) - 10))
        ch = int(cw * M.CARD_ASPECT)
        gap = 10
        total = len(cards) * cw + (len(cards) - 1) * gap
        self.rect = pygame.Rect(0, 0, total + 28, ch + 46)
        self.rect.center = anchor.center
        x = self.rect.left + 14
        y = self.rect.top + 32
        for card_id, card_def in cards:
            self.sprites.append(CardSprite(
                card_id, card_def, pygame.Rect(x, y, cw, ch),
                face_down=card_def is None, highlighted=True,
                selected=card_id in selected, lift_on_hover=10,
            ))
            x += cw + gap

    @property
    def visible(self) -> bool:
        return bool(self.sprites)

    def card_at(self, pos: tuple[int, int]) -> CardSprite | None:
        for sprite in reversed(self.sprites):
            if sprite.hit(pos):
                return sprite
        return None

    def update(self, dt: float) -> None:
        for sprite in self.sprites:
            sprite.update(dt)

    def update_hover(self, pos: tuple[int, int]) -> CardSprite | None:
        top = self.card_at(pos)
        for sprite in self.sprites:
            sprite.hovered = sprite is top
        return top

    def draw(self, screen: pygame.Surface) -> None:
        if not self.visible:
            return
        T.glass(screen, self.rect, radius=M.RADIUS_L, fill=C.GLASS,
                rim=T.alpha(C.ARCANE, 180))
        T.text(screen, self.title.upper(), (self.rect.centerx, self.rect.top + 10),
               T.ui(10, bold=True), C.ARCANE, anchor="midtop", shadow=None)
        for i, sprite in enumerate(self.sprites):
            sprite.draw(screen)
            T.text(screen, str(i + 1), (sprite.rect.centerx, sprite.rect.bottom + 3),
                   T.ui(9, bold=True), C.INK_FAINT, anchor="midtop", shadow=None)


# ---------------------------------------------------------------------------
# The scene
# ---------------------------------------------------------------------------


class GameScene:
    """The board. Implements :class:`~.devconsole.DevHost`."""

    def __init__(
        self,
        engine: Any,
        presenter: Any,
        registry: Any,
        layout: Any,
        *,
        sound: SoundBoard | None = None,
        cues: CueTable | None = None,
        hooks: SceneHooks | None = None,
        reveal_all: bool = False,
        replay: ReplayTransport | None = None,
    ) -> None:
        self.engine = engine
        self.presenter = presenter
        self.registry = registry
        self.layout = layout
        self.sound = sound or NULL_BOARD
        self.hooks = hooks or SceneHooks()
        # Which cue a moment plays is data, and a loaded pack may re-point any
        # of it or add voices of its own (`cues.py`). Installing here rather
        # than in `app.py` keeps a scene built directly by a test audible too.
        self.cues = cues if cues is not None else CueTable.for_registry(registry)
        self.cues.install(self.sound)
        #: Set when this board is watching a recorded game rather than playing
        #: one. The scene does not otherwise branch on it: a replay is a
        #: `DecisionSource` reading a file, so everything else is unchanged.
        self.replay = replay
        self.replay_bar: ReplayBar | None = None
        if replay is not None:
            self.replay_bar = ReplayBar(replay)
            self.replay_bar.resize(layout)

        # -- panels --------------------------------------------------------
        self.corner = CornerButtons(layout)
        self.turn_chip = TurnChip(layout)
        self.action_bar = ActionBar(layout)
        self.reaction_timer = ReactionTimer()
        self.rail = OpponentRail(layout)
        self.active = ActiveStack(layout)
        self.decks = DeckArea(layout)
        self.monsters = MonsterRow(layout)
        self.party = PartyRow(layout)
        self.hand = HandFan(layout)
        self.dice = DicePanel(layout, on_roll=self._on_roll_pressed)
        self.effects = EffectsPanel(layout)

        self.corner.info_button.on_click = self.open_rules
        self.corner.log_button.on_click = self.open_log
        self.corner.menu_button.on_click = self.open_menu

        # -- presentation state --------------------------------------------
        self.fx = AnimationManager()
        self.atmosphere = Atmosphere()
        self.cameras = CameraDirector()
        self._cam_from: dict | None = None
        self._cam_to: dict | None = None
        self.tracker = BoardTracker()
        clear_card_cache()  # drop any faces bleached by an older brighten bug
        self.overlays = OverlayStack()
        self.menu = ActionMenu(layout.action_menu_rect)
        self.tray = PickTray()
        self.toast = Toast(layout.toast_rect)
        self.tooltip = Tooltip()
        self.log = LogFeed(pygame.Rect(0, 0, 320, 120), limit=400)

        self.flags: dict[str, bool] = {
            "reveal_all": reveal_all,
            "animations": True,
            # Separate from `animations` because it is the effect people turn
            # off first, and turning off *every* animation to stop the table
            # lurching is a poor trade.
            "shake": True,
            "sound": self.sound.enabled,
            "autoplay": False,
            "layout_debug": False,
            "fps": False,
            "force_cpu_materials": False,
            "reaction_timer": True,
        }

        self.seat: str = self._initial_seat()
        self.view: Any = None
        self.rolls: tuple[Any, ...] = ()
        self.prompt = _Prompt()
        self._request_id: tuple[Any, ...] | None = None
        self._action_filter: str | None = None
        self._armed_roll: Intent | None = None
        self._action_bar_entries: list = []
        self._menu_desired_h: int = 0
        self._reaction_deadline: float | None = None
        self._reaction_frozen = False
        #: The request already answered, kept until the engine asks the next
        #: one so a resolved menu is not redrawn for a second click.
        self._answered: Request | None = None
        self.selected: list[str] = []
        self.focus_card: str | None = None
        self.hovered: CardSprite | None = None
        self.detail_card: Any = None
        self.detail_anchor: pygame.Rect | None = None
        self.spawned: list[CardSprite] = []
        self.fps = 0.0
        self._handover_for: str | None = None
        self._game_over_shown = False
        self._slay_target = self._read_slay_target()
        self._refresh_view()

    # ------------------------------------------------------------------
    # Seats and view
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Sound
    # ------------------------------------------------------------------

    def cue(self, key: str, *, volume: float = 1.0) -> bool:
        """Play whatever this pack says ``key`` sounds like.

        The one place the board asks for sound. Every call names a *moment*
        (``zone.party``, ``band.success``, ``ui.close``) rather than a cue, so
        the mapping stays in `cues.py` where a pack can change it — and a moment
        this client has never seen still gets its family's fallback instead of
        silence.
        """
        resolved = self.cues.get(key)
        if not resolved:
            return False
        return self.sound.play(resolved.name, volume=resolved.volume * volume)

    def _initial_seat(self) -> str:
        humans = self.presenter.human_seats
        order = list(self.engine.state.turn_order)
        if humans:
            for pid in order:
                if pid in humans:
                    return pid
        return order[0] if order else ""

    def _read_slay_target(self) -> int:
        """Read the slay count out of the win condition rather than assuming 3.

        A variant that changes ``value: 3`` in ``rules.yaml`` gets a progress
        bar that agrees with it, with no UI change.
        """
        for victory in getattr(self.registry.rules, "victory", ()) or ():
            condition = getattr(victory, "condition", None)
            if condition is not None and getattr(condition, "op", "") == "slain_count":
                value = condition.param("value")
                if isinstance(value, int) and value > 0:
                    return value
        return DEFAULT_SLAY_TARGET

    def _focus_seat(self) -> str:
        if self.flags["reveal_all"]:
            # Spectator mode: follow whoever is acting. Still a legitimate
            # `engine.view(seat)` call — no redaction is bypassed — but it does
            # show hands the seat in front of the screen should not see, which
            # is why it lives behind a dev flag and says so on screen.
            return str(self.engine.state.active_player)
        request = self.presenter.pending_request
        if request is not None and self.presenter.is_human(request.requester):
            return str(request.requester)
        return self.seat

    def _refresh_view(self) -> None:
        seat = self._focus_seat()
        try:
            view = self.engine.view(seat)
            rolls = self.engine.recent_rolls
        except (RuntimeError, KeyError, AttributeError):
            return  # torn read from the engine thread; last frame's view stands
        self.seat = seat
        self.view = view
        self.rolls = rolls

    # ------------------------------------------------------------------
    # Frame
    # ------------------------------------------------------------------

    def resize(self, width: int, height: int) -> None:
        self.layout.rebuild(width, height)
        self.layout.apply_camera(self.cameras.active.key)
        self.corner.resize()
        self.toast.rect = pygame.Rect(self.layout.toast_rect)
        self.menu.rect = pygame.Rect(self.layout.action_menu_rect)
        if self.replay_bar is not None:
            self.replay_bar.resize(self.layout)
        self._request_id = None
        self._reaction_deadline = None

    def update(self, dt: float) -> None:
        self._refresh_view()
        if self.view is None:
            return

        you = self.view.players.get(self.view.seat)
        local_name = you.name if you is not None else "Tu"
        self.cameras.sync_from_view(self.view, local_label=local_name)
        n_players = len(self.view.players)
        if getattr(self.layout, "player_count", None) != n_players:
            self.layout.rebuild(
                self.layout.width, self.layout.height, player_count=n_players,
            )
        self.layout.apply_camera(self.cameras.active.key)
        if (
            self.cameras.blend < 1.0
            and self._cam_from is not None
            and self._cam_to is not None
        ):
            # Keep the target snapshot fresh for the active key, then ease.
            self._cam_to = self.layout.snapshot()
            t = T.ease_out_cubic(self.cameras.blend)
            self.layout.apply_lerp(self._cam_from, self._cam_to, t)
        self.cameras.update(dt)
        if self.cameras.blend >= 1.0:
            self._cam_from = None
            self._cam_to = None

        changes = self.tracker.poll(self.view, rolls=self.rolls)
        for change in changes:
            self._react(change)

        self._sync_prompt()
        self._sync_panels()
        self.turn_chip.sync(self.view)
        self._build_action_bar()
        self._tick_reaction_timer(dt)

        for panel in (self.corner, self.rail, self.active, self.monsters, self.party,
                      self.hand, self.dice):
            panel.update(dt)
        self.action_bar.update(dt)
        self.tray.update(dt)
        self.menu.update(dt)
        self.atmosphere.update(dt, (self.layout.width, self.layout.height))
        self.fx.update(dt)
        self.toast.update(dt)
        self.log.update(dt)
        for sprite in self.spawned:
            sprite.update(dt)

        for done in self.overlays.update(dt):
            self._overlay_finished(done)
        self._check_handover()
        self._check_game_over()

    def draw(self, screen: pygame.Surface) -> None:
        if self.view is None:
            screen.fill(C.VOID)
            return
        shake = (
            self.fx.shake_offset
            if self.flags["animations"] and self.flags["shake"]
            else (0, 0)
        )
        if shake == (0, 0):
            self._draw_board(screen)
        else:
            scratch = pygame.Surface(screen.get_size())
            self._draw_board(scratch)
            screen.fill(C.VOID)
            screen.blit(scratch, shake)

        self.fx.draw_top(screen)
        self.overlays.draw(screen)
        self.fx.draw_overlays(screen)
        if self.flags["layout_debug"]:
            draw_layout_debug(screen, self.layout.as_dict())
        if self.flags["fps"]:
            draw_fps(screen, self.fps, f"fx {self.fx.count()}")

    def _draw_board(self, screen: pygame.Surface) -> None:
        self._draw_backdrop(screen)
        cam = self.cameras.active

        if cam.kind == CameraKind.CENTRE:
            self.decks.draw(screen)
            self.monsters.draw(screen)
            self.rail.draw(screen)
            self.party.draw(screen)
            self.hand.draw(screen)
        elif cam.kind == CameraKind.OPPONENT:
            self.rail.draw(screen)
            self.hand.draw(screen)
            self.party.draw(screen)
        else:
            self.rail.draw(screen)
            self.decks.draw(screen)
            self.monsters.draw(screen)
            self.party.draw(screen)
            self.hand.draw(screen)

        self.dice.draw(screen, dice_hidden=not self.rolls)
        self.effects.draw(screen)
        self.corner.draw(screen)
        self.turn_chip.draw(screen)
        self._draw_camera_strip(screen)

        self.fx.draw(screen)
        self._draw_spawned(screen)
        self._draw_prompt(screen)
        self.action_bar.draw(screen)
        self.menu.draw(screen)
        self.reaction_timer.draw(screen)
        self.tray.draw(screen)
        self._draw_log(screen)
        # Limbo faces sit inside the merged journal (drawn after the frame).
        self.active.draw(screen)
        self._draw_detail(screen)
        self.toast.draw(screen)
        self.tooltip.draw(screen)
        if self.flags["reveal_all"]:
            self._draw_spectator_badge(screen)
        if self.replay_bar is not None:
            self.replay_bar.draw(screen)

    def _draw_camera_strip(self, screen: pygame.Surface) -> None:
        strip = getattr(self.layout, "camera_strip_rect", None)
        if strip is None or strip.width < 40:
            return
        n = len(self.cameras.views)
        if n <= 0:
            return
        gap = 6
        slot_w = max(48, (strip.width - gap * (n - 1)) // n)
        x = strip.left
        for i, view in enumerate(self.cameras.views):
            slot = pygame.Rect(x, strip.top, min(slot_w, 140), strip.height)
            active = i == self.cameras.index
            if active:
                T.round_rect(screen, slot, T.alpha(C.CYAN, 55), radius=8)
                T.round_rect(screen, slot, C.CYAN, radius=8, width=2)
            label = view.label
            if len(label) > 12:
                label = label[:11] + "\u2026"
            T.text(
                screen, label, slot.center,
                T.ui(13, bold=active), C.INK if active else C.INK_DIM,
                anchor="center", shadow=None, max_width=slot.width - 8,
            )
            x += slot_w + gap
        T.text(
            screen, L.CAM_HINT,
            (strip.centerx, strip.bottom + 12),
            T.ui(12), T.alpha(C.INK_FAINT, 200), anchor="midtop", shadow=None,
        )

    def _draw_backdrop(self, screen: pygame.Surface) -> None:
        active = str(self.view.active_player) if self.view else None
        active_index: int | None = None
        if self.view and active:
            order = _play_order(self.view)
            try:
                active_index = order.index(active)
            except ValueError:
                active_index = None
        self.atmosphere.draw(
            screen, self.layout,
            active_seat=active,
            active_index=active_index,
            camera_key=self.cameras.active.key,
        )

    def _draw_spectator_badge(self, screen: pygame.Surface) -> None:
        rect = pygame.Rect(0, 0, T.s(210), T.s(22))
        corner = self.layout.corner_buttons_rect
        rect.topright = (corner.right, corner.bottom + T.s(10))
        T.pill(screen, rect, "SPECTATOR \u00b7 ALL HANDS VISIBLE", bg=T.alpha(C.BLOOD, 70),
               fg=C.INK_BRIGHT, border=T.alpha(C.BLOOD, 190), fnt=T.ui(9, bold=True))

    # ------------------------------------------------------------------
    # Panel synchronisation
    # ------------------------------------------------------------------

    def _sync_panels(self) -> None:
        view = self.view
        prompt = self.prompt
        highlight = set(prompt.candidates)
        selected = set(self.selected)
        targets = set(prompt.players)
        acting = self._acting_player()

        self.rail.sync(view, self.registry, highlight_cards=highlight, target_players=targets)
        self.active.sync(
            view, self.registry,
            pending_note=self._active_note(), rolls=self.rolls,
        )
        self.decks.sync(view, self.registry)
        self.monsters.sync(
            view, self.registry,
            attackable=self._attackable(), highlight=highlight,
        )
        self.party.sync(
            view, self.registry, highlight=highlight, selected=selected,
            slay_target=self._slay_target,
        )
        self.hand.sync(
            view, self.registry, playable=self._playable(), highlight=highlight,
            selected=selected,
        )
        can_roll, roll_label = self._roll_state()
        self.dice.sync(
            view, rolls=self.rolls, acting=acting, can_roll=can_roll, roll_label=roll_label,
        )
        self.effects.sync(view, self.registry, acting=acting)
        self._sync_tray()

    def _acting_player(self) -> Any:
        """Whose abilities and points the bottom-right panels describe."""
        request = self.presenter.pending_request
        if request is not None and request.requester in self.view.players:
            return self.view.players[request.requester]
        return self.view.players.get(self.view.active_player)

    def _filtered_intents(self) -> list[Intent]:
        """Intents visible for highlighting and targeting."""
        request = self.prompt.request
        if not isinstance(request, ChooseIntent):
            return []
        intents = list(request.intents)
        if self._action_filter:
            intents = [i for i in intents if i.action == self._action_filter]
        if self.focus_card is not None:
            intents = [i for i in intents if i.card == self.focus_card]
        return intents

    def _attackable(self) -> set[str]:
        return {
            intent.card
            for intent in self._filtered_intents()
            if intent.action == "attack_monster" and intent.card
        }

    def _playable(self) -> set[str]:
        out: set[str] = set()
        hand = self.view.you.zone("hand")
        held = {cv.id for cv in (hand.cards if hand and hand.revealed else ())}
        filtered = self._filtered_intents()
        for intent in filtered:
            if intent.card and intent.card in held:
                out.add(intent.card)
        if not self._action_filter and not self.focus_card:
            for card_id in self.prompt.intents_by_card:
                if card_id in held:
                    out.add(card_id)
        return out

    def _sync_tray(self) -> None:
        """Show the tray only for candidates no panel can display."""
        prompt = self.prompt
        if not prompt.candidates or not isinstance(prompt.request, ChooseCards):
            self.tray.build(pygame.Rect(0, 0, 0, 0), (), set())
            return
        orphans: list[tuple[str, Any]] = []
        for card_id in prompt.candidates:
            if self._locate_card(card_id) is None:
                card = self._card_view(card_id)
                card_def = self.registry.get(card.def_id) if card is not None else None
                orphans.append((card_id, None if prompt.hidden else card_def))
        anchor = pygame.Rect(self.layout.monster_row_rect)
        self.tray.build(anchor, orphans, set(self.selected), title=prompt.text or "choose")

    def _status_line(self) -> str:
        thinking = self.presenter.thinking_seat
        if thinking is not None:
            player = self.view.players.get(thinking)
            return L.THINKING.format(name=player.name if player else thinking)
        if self.presenter.paused:
            return "engine paused"
        request = self.presenter.awaiting_human
        if request is not None:
            player = self.view.players.get(request.requester)
            who = player.name if player else request.requester
            return f"{who}: {self.prompt.text}" if self.prompt.text else who
        if self.engine.over:
            return "game over"
        return ""

    def _active_note(self) -> tuple[str, tuple[int, int, int], str | None] | None:
        request = self.presenter.pending_request
        if request is None:
            return None
        if isinstance(request, ReactionPrompt):
            return (
                f"{request.window.replace('_', ' ')} window open",
                C.ARCANE, "bolt",
            )
        if self.prompt.text:
            return (self.prompt.text, self.prompt.accent, self.prompt.icon)
        return None

    # ------------------------------------------------------------------
    # The open request
    # ------------------------------------------------------------------

    def _sync_prompt(self) -> None:
        """Point the board at whatever the engine is currently asking.

        Keyed on the request *object*, not its contents: two consecutive
        questions can look identical, and the board must be answering the one
        that is open rather than the one that was.
        """
        request = self.presenter.pending_request
        if request is not None and request is self._answered:
            # Already answered; the engine has not woken up yet. Sitting on the
            # empty menu avoids offering a button that would be ignored.
            return
        self._answered = None
        if request is self.prompt.request and self._identify(request) == self._request_id:
            return
        self._request_id = self._identify(request)
        self.selected.clear()
        self.focus_card = None
        self._action_filter = None
        self._armed_roll = None
        self._menu_desired_h = 0
        self.menu.clear()
        self.prompt = self._build_prompt(request)
        if isinstance(request, ReactionPrompt):
            self._reaction_deadline = time.monotonic() + REACTION_SECONDS
        else:
            self._reaction_deadline = None
        if request is not None and self.presenter.is_human(request.requester):
            self._build_menu()
            self.cue("ui.open", volume=0.35)

    @staticmethod
    def _identify(request: Request | None) -> tuple[Any, ...] | None:
        if request is None:
            return None
        # Requests are frozen dataclasses but not hashable in every case, so
        # identity is built from the fields that change what is drawn.
        return (
            type(request).__name__, request.requester, request.prompt,
            tuple(getattr(request, "candidates", ()) or ()),
            tuple(o.key for o in getattr(request, "options", ()) or ()),
            tuple(i.key() for i in getattr(request, "intents", ()) or ()),
            getattr(request, "window", ""),
        )

    def _build_prompt(self, request: Request | None) -> _Prompt:
        if request is None:
            return _Prompt()
        if isinstance(request, ChooseIntent):
            by_card: dict[str, list[Intent]] = {}
            for intent in request.intents:
                if intent.card:
                    by_card.setdefault(intent.card, []).append(intent)
                # Attack / target picks key the Monster (or Hero) as target.
                target = getattr(intent, "target", None)
                if target and target != intent.card:
                    by_card.setdefault(str(target), []).append(intent)
            return _Prompt(
                request=request, text=L.retheme_prompt(request.prompt or "Alege o acțiune"),
                hint="click pe un răspuns AI, sau din listă", accent=C.GOLD, icon="bolt",
                intents_by_card=by_card,
            )
        if isinstance(request, ChooseCards):
            span = (
                f"{request.minimum}" if request.minimum == request.maximum
                else f"{request.minimum}\u2013{request.maximum}"
            )
            return _Prompt(
                request=request,
                text=L.retheme_prompt(
                    request.prompt
                    or f"Alege {span} "
                       f"{L.HAND if request.maximum != 1 else L.HAND_ONE}"
                ),
                hint="click pe cardurile marcate, apoi confirmă",
                accent=C.ARCANE, icon="target",
                candidates=tuple(request.candidates),
                minimum=request.minimum, maximum=request.maximum,
                hidden=request.hidden,
            )
        if isinstance(request, ChoosePlayer):
            return _Prompt(
                request=request, text=L.retheme_prompt(request.prompt or "Alege un jucător"),
                hint="click pe un scaun de la masă", accent=C.FROST, icon="target",
                players=tuple(request.candidates),
            )
        if isinstance(request, ReactionPrompt):
            return _Prompt(
                request=request,
                text=L.retheme_prompt(
                    request.prompt or f"Răspunde la {request.window.replace('_', ' ')}?"
                ),
                hint="Space pentru a trece", accent=C.POISON, icon="challenge",
            )
        if isinstance(request, ChooseOption):
            return _Prompt(
                request=request,
                text=L.retheme_prompt(request.prompt or "Alege"),
                accent=C.GOLD, icon="scroll",
            )
        if isinstance(request, Confirm):
            return _Prompt(
                request=request,
                text=L.retheme_prompt(request.prompt or "Confirmi?"),
                hint="Enter da, Space nu", accent=C.GOOD, icon="check",
            )
        return _Prompt(
            request=request,
            text=L.retheme_prompt(request.prompt or "Așteaptă\u2026"),
        )

    def _build_menu(self) -> None:
        request = self.prompt.request
        entries: list[tuple[str, str, str | None, tuple[int, int, int], Callable[[], None]]] = []

        if isinstance(request, ChooseIntent):
            for intent in self._menu_intents(request):
                icon, accent = ACTION_STYLE.get(intent.action, ("bolt", C.GOLD))
                entries.append((
                    L.retheme_prompt(self._intent_label(intent)),
                    L.retheme_prompt(self._intent_note(intent)),
                    icon, accent,
                    self._pick_intent(intent),
                ))
            title = L.YOUR_MOVE
            if self.focus_card is not None:
                card = self._card_view(self.focus_card)
                name = self._card_name(card.def_id) if card else "card"
                title = f"{name} — {L.CHOOSE}"
        elif isinstance(request, ChooseOption):
            for option in request.options:
                entries.append((
                    L.retheme_prompt(option.label), "", "scroll", C.GOLD,
                    self._pick_option(option),
                ))
            title = L.CHOOSE
        elif isinstance(request, ReactionPrompt):
            for option in request.options:
                entries.append((
                    L.retheme_prompt(option.label), L.FREE, "bolt", C.POISON,
                    self._pick_reaction(option),
                ))
            entries.append((L.PASS, "", "close", C.INK_DIM, self._pass_reaction))
            title = L.retheme_prompt(request.window.replace("_", " "))
        elif isinstance(request, Confirm):
            entries = [
                ("Da", "", "check", C.GOOD, lambda: self._submit(Confirmed(True))),
                ("Nu", "", "close", C.BAD, lambda: self._submit(Confirmed(False))),
            ]
            title = L.CONFIRM
        elif isinstance(request, ChoosePlayer):
            for pid in request.candidates:
                player = self.view.players.get(pid)
                entries.append((
                    player.name if player else str(pid), "", "target",
                    T.seat_colour(player.seat) if player else C.GOLD,
                    self._pick_player(pid),
                ))
            title = L.CHOOSE_PLAYER
        elif isinstance(request, ChooseCards):
            self._build_confirm_row()
            return
        else:
            self.menu.clear()
            self._menu_desired_h = 0
            self._repack_right(menu_height=0)
            return

        self._menu_desired_h = self.menu.desired_height(len(entries))
        self._repack_right(menu_height=self._menu_desired_h)
        self.menu.build(title, entries, rect=pygame.Rect(self.layout.action_menu_rect))

    def _repack_right(self, *, menu_height: int | None = None) -> None:
        if menu_height is None:
            menu_height = self._menu_desired_h if self.menu.visible else 0
        n = len(self._action_bar_entries)
        rows = max(1, (n + 1) // 2) if n else 1
        self.layout.pack_right_column(rows, cols=2, menu_height=menu_height)
        if self._action_bar_entries:
            self.action_bar.build(self._action_bar_entries)
        self.effects.rect = pygame.Rect(self.layout.effects_rect)
        # Keep the live menu flush with the freshly packed slot.
        if self.menu.visible and self.menu.buttons:
            self._relayout_menu()

    def _relayout_menu(self) -> None:
        """Move existing menu buttons into the packed action_menu_rect."""
        slot = pygame.Rect(self.layout.action_menu_rect)
        if slot.height < 20:
            return
        old = pygame.Rect(self.menu.rect)
        dy = slot.top - old.top
        dx = slot.left - old.left
        self.menu.rect = slot
        for button in self.menu.buttons:
            button.rect.move_ip(dx, dy)
        # Clamp scroll so content stays reachable inside the new height.
        span = max(0, self.menu.content_bottom - self.menu.rect.bottom)
        self.menu.scroll = max(-span, min(0, self.menu.scroll))

    def _build_action_bar(self) -> None:
        request = self.presenter.awaiting_human
        if not isinstance(request, ChooseIntent):
            self._action_bar_entries = []
            self._repack_right()
            self.action_bar.build([])
            return
        by_action: dict[str, list[Intent]] = {}
        for intent in request.intents:
            by_action.setdefault(intent.action, []).append(intent)
        entries: list[tuple[str, str, str, str, bool, Callable[[], None]]] = []
        for action_id in ACTION_KEYS:
            action = self.registry.rules.action(action_id)
            if action is None:
                continue
            intents = by_action.get(action_id, [])
            key = hotkey_for(action_id, action) or "?"
            label = L.retheme_prompt((action.label or action_id.replace("_", " ")).split(" - ")[0])
            cost_val = DISPLAY_COSTS.get(action_id)
            if cost_val is None:
                cost_val = int((action.cost or {}).get("action_points", 0))
            cost_str = str(cost_val)
            enabled = bool(intents)

            def _cb(aid: str = action_id, group: list[Intent] = intents) -> Callable[[], None]:
                return lambda: self._handle_action(aid, group)

            entries.append((action_id, key, label, cost_str, enabled, _cb()))
        self._action_bar_entries = entries
        self._repack_right()
        self.action_bar.build(entries)

    def _handle_action(self, action_id: str, intents: list[Intent]) -> None:
        if not intents:
            action = self.registry.rules.action(action_id)
            label = action.label if action else action_id.replace("_", " ")
            self.toast.show(L.NO_LEGAL.format(label=label), colour=C.WARN, icon="close")
            return
        if action_id in ROLL_ACTIONS:
            if len(intents) == 1:
                self._arm_roll(intents[0])
                return
            self._action_filter = action_id
            self._build_menu()
            self.toast.show(
                L.PICK_TARGET.format(n=len(intents)),
                colour=C.CYAN, icon="target",
            )
            return
        if len(intents) == 1:
            self._submit(IntentChosen(intents[0]))
            return
        self._action_filter = action_id
        self._build_menu()
        self.toast.show(
            L.PICK_TARGET.format(n=len(intents)),
            colour=C.CYAN, icon="target",
        )

    def _tick_reaction_timer(self, dt: float) -> None:
        request = self.presenter.awaiting_human
        if not isinstance(request, ReactionPrompt):
            self.reaction_timer.sync(visible=False, rect=pygame.Rect(0, 0, 0, 0),
                                     title="", fraction=0.0, options=[])
            return
        if not self.flags.get("reaction_timer", True):
            self.reaction_timer.sync(visible=False, rect=pygame.Rect(0, 0, 0, 0),
                                     title="", fraction=0.0, options=[])
            return
        if self._reaction_deadline is None:
            self._reaction_deadline = time.monotonic() + REACTION_SECONDS
        frozen = bool(self.overlays.items) or self.presenter.paused or self._dev_console_open()
        if frozen:
            self._reaction_deadline += dt
        elif time.monotonic() >= self._reaction_deadline:
            self._pass_reaction()
            return
        remaining = max(0.0, self._reaction_deadline - time.monotonic())
        fraction = remaining / REACTION_SECONDS
        # Tick only in the final 3 seconds so a 10s window stays quiet.
        if remaining <= 3.0 and int(remaining * 4) != int((remaining + dt) * 4):
            self.cue("ui.click", volume=0.25)
        anchor = self.layout.left_rail_rect
        rect = pygame.Rect(anchor.left, anchor.top, max(220, anchor.width), T.s(80))
        options = [
            (option.label, option.key, self._pick_reaction(option))
            for option in request.options
        ]
        self.reaction_timer.sync(
            visible=True,
            rect=rect,
            title=self.prompt.text or request.prompt or "React?",
            fraction=fraction,
            options=options,
            seconds_left=remaining,
        )

    def _dev_console_open(self) -> bool:
        return any(isinstance(item, DevConsole) for item in self.overlays.items)

    def _menu_intents(self, request: ChooseIntent) -> list[Intent]:
        """Intents to list. Focusing a card narrows the menu to that card."""
        intents = list(request.intents)
        if self._action_filter is not None:
            intents = [i for i in intents if i.action == self._action_filter]
        if self.focus_card is not None:
            return [i for i in intents if i.card == self.focus_card]
        return [i for i in intents if not i.card] + [
            i for i in intents
            if i.card and self._locate_card(i.card) is None
        ]

    def _build_confirm_row(self) -> None:
        request = self.prompt.request
        if not isinstance(request, ChooseCards):
            return
        chosen = len(self.selected)
        enough = request.minimum <= chosen <= request.maximum
        label = (
            f"Confirm {chosen}/{request.maximum}" if request.maximum > 1
            else "Confirm"
        )
        entries: list[tuple[str, str, str | None, tuple[int, int, int], Callable[[], None]]] = [
            (label, "" if enough else f"pick at least {request.minimum}", "check",
             C.GOOD if enough else C.IDLE, self._confirm_cards),
        ]
        if request.minimum == 0:
            entries.append(("Choose none", "", "close", C.INK_DIM, self._confirm_cards))
        rect = pygame.Rect(self.layout.action_menu_rect)
        rect.height = 120
        self.menu.build("selection", entries, rect=rect)
        if not enough:
            self.menu.buttons[0].enabled = False

    def _intent_label(self, intent: Intent) -> str:
        label = intent.label or intent.action.replace("_", " ").title()
        # The engine's label already names the card; strip the duplicate when
        # the menu is already scoped to one card.
        if self.focus_card is not None and " - " in label:
            return label.split(" - ", 1)[0]
        return label

    def _intent_note(self, intent: Intent) -> str:
        action = self.registry.rules.action(intent.action)
        cost = int((getattr(action, "cost", None) or {}).get("action_points", 0)) if action else 0
        bits = [L.ap_label(cost) if cost else L.FREE]
        if intent.action in ROLL_ACTIONS:
            bits.append("np.random 2d6")
        return "  ·  ".join(bits)

    # -- submission --------------------------------------------------------

    def _arm_roll(self, intent: Intent) -> None:
        """Queue a roll action until the player presses np.random."""
        self._armed_roll = intent
        self.focus_card = getattr(intent, "card", None)
        self._action_filter = None
        self.menu.clear()
        self._menu_desired_h = 0
        self._repack_right(menu_height=0)
        self.toast.show(L.PRESS_ROLL, colour=C.FROST, icon="dice")

    def _pick_intent(self, intent: Intent) -> Callable[[], None]:
        if intent.action in ROLL_ACTIONS:
            return lambda: self._arm_roll(intent)
        return lambda: self._submit(IntentChosen(intent))

    def _pick_option(self, option: Option) -> Callable[[], None]:
        return lambda: self._submit(OptionChosen(option.key))

    def _pick_reaction(self, option: Option) -> Callable[[], None]:
        return lambda: self._submit(ReactionChosen(option.card))

    def _pick_player(self, pid: str) -> Callable[[], None]:
        return lambda: self._submit(PlayerChosen(pid))

    def _pass_reaction(self) -> None:
        self._submit(ReactionChosen(None))

    def _confirm_cards(self) -> None:
        self._submit(CardsChosen(tuple(self.selected)))

    def _submit(self, decision: Decision) -> bool:
        """Send an answer for the request the board is currently showing.

        Passing ``answering`` matters: the engine thread may have moved on
        since this menu was drawn, and an answer to a stale question would be
        validated against the new one.
        """
        if not self.presenter.submit_decision(decision, answering=self.prompt.request):
            return False
        self.cue("ui.click")
        self.selected.clear()
        self.focus_card = None
        self._armed_roll = None
        self.menu.clear()
        self._menu_desired_h = 0
        self._answered = self.prompt.request
        self._request_id = None
        return True

    # ------------------------------------------------------------------
    # Dice
    # ------------------------------------------------------------------

    def _roll_state(self) -> tuple[bool, str]:
        """Whether the roll button does anything, and what it should say."""
        intent = self._pending_roll_intent()
        if intent is not None:
            return True, L.ROLL_READY
        if self.rolls:
            return True, L.np_random_label(int(getattr(self.rolls[-1], "total", 0) or 0))
        return False, L.ROLL_READY

    def _pending_roll_intent(self) -> Intent | None:
        request = self.presenter.awaiting_human
        if not isinstance(request, ChooseIntent):
            self._armed_roll = None
            return None
        rolling = [i for i in request.intents if i.action in ROLL_ACTIONS]
        if not rolling:
            self._armed_roll = None
            return None
        # Only an explicitly armed roll (or a single focus) enables the button.
        if self._armed_roll is not None:
            for intent in rolling:
                if (
                    intent.action == self._armed_roll.action
                    and getattr(intent, "card", None) == getattr(self._armed_roll, "card", None)
                    and getattr(intent, "target", None) == getattr(self._armed_roll, "target", None)
                ):
                    return intent
            # Armed intent no longer legal.
            self._armed_roll = None
        if self.focus_card is not None:
            focused = [i for i in rolling if i.card == self.focus_card]
            if focused and self._armed_roll is not None:
                return focused[0]
        return None

    def _on_roll_pressed(self) -> None:
        intent = self._pending_roll_intent()
        if intent is not None:
            self._submit(IntentChosen(intent))
            return
        if self.rolls:
            self._animate_roll(self.rolls[-1], replay=True)

    def _animate_roll(self, roll: Any, *, replay: bool = False) -> None:
        area = pygame.Rect(self.dice.dice_area)
        values = tuple(getattr(roll, "raw", ()) or (1, 1))
        good = getattr(roll, "band_tag", "") in ("success", "slay")
        accent = C.GOOD if good else (C.BAD if roll.band_tag == "failure" else C.GOLD)
        if self.flags["animations"]:
            self.fx.add(DiceRollAnimation(
                values, area, 0.65, total=getattr(roll, "total", None), accent=accent,
            ))
        self.cue("game.roll")
        if not replay:
            self.cue("game.roll_land")

    # ------------------------------------------------------------------
    # Reacting to board changes
    # ------------------------------------------------------------------

    def _react(self, change: Any) -> None:
        if isinstance(change, CardMoved):
            self._on_card_moved(change)
        elif isinstance(change, CardAppeared):
            self._on_card_appeared(change)
        elif isinstance(change, CardVanished):
            self._on_card_vanished(change)
        elif isinstance(change, ZoneCountChanged):
            self._on_zone_changed(change)
        elif isinstance(change, TurnChanged):
            self._on_turn_changed(change)
        elif isinstance(change, PhaseChanged):
            pass  # phases are visible in the top bar; a banner each would nag
        elif isinstance(change, PointsChanged):
            self._on_points_changed(change)
        elif isinstance(change, RollHappened):
            self._animate_roll(change.roll)
            self._log_roll(change.roll)
        elif isinstance(change, RollModified):
            self._on_roll_modified(change)
        elif isinstance(change, GameWon):
            self._on_game_won(change)

    def _on_card_moved(self, change: CardMoved) -> None:
        name = self._card_name(change.def_id)
        owner = self._owner_name(change.to.owner or change.frm.owner)
        told = describe_move(change, card_name=name, owner_name=owner)
        if told is not None:
            text, icon, role = told
            self.log.add(text, colour=ROLE_COLOURS.get(role, C.INK_DIM), icon=icon)

        start = self._place_centre(change.frm)
        end = self._place_centre(change.to)
        card_def = self.registry.get(change.def_id)
        if self.flags["animations"] and start != end:
            width = max(48, min(self.layout.hand_card_w, 110))
            self.fx.add(CardMoveAnimation(
                card_def, self._corner(start, width), self._corner(end, width),
                self.layout.card_box(width), 0.45,
                face_down=False, flip=change.frm.zone in ("main_deck", "monster_deck"),
            ))

        # The *sound* of an arrival is a table lookup, so a zone this client has
        # never heard of is audible rather than silent (see `cues.py`). Only the
        # extra flourishes stay per-zone, because they are pictures rather than
        # rules and there is nothing sensible to invent for an unknown zone.
        if change.to.zone == "slain":
            self._celebrate_slay(change, end)
        else:
            self.cue(f"zone.{change.to.zone}")
            if self.flags["animations"]:
                if change.to.zone == "discard" and change.frm.zone == "limbo":
                    self.fx.add(RingBurstAnimation(end, C.BLOOD, 0.5, radius=70))
                elif change.to.zone == "limbo":
                    self.fx.add(RunePulseAnimation(end, C.ARCANE, 0.9, radius=80))
        if change.frm.owner and change.to.owner and change.frm.owner != change.to.owner:
            # A card changing hands is the one move that needs its own gesture.
            if self.flags["animations"]:
                self.fx.add(TrailAnimation(start, end, C.ROSE, 0.6))
            self.cue("game.steal")

    def _celebrate_slay(self, change: CardMoved, at: tuple[int, int]) -> None:
        name = self._card_name(change.def_id)
        who = self._owner_name(change.to.owner)
        self.cue("zone.slain")
        self.toast.show(f"{who} slew {name}!", colour=C.GOLD, duration=2.6, icon="skull")
        if not self.flags["animations"]:
            return
        self.fx.shake(5.0)
        self.fx.add(RingBurstAnimation(at, C.BLOOD, 0.8, radius=150, rings=4))
        self.fx.add(BannerAnimation("SLAIN", f"{who} \u2014 {name}", colour=C.BLOOD,
                                    icon="skull", duration=0.85, y_fraction=0.18))
        card_def = self.registry.get(change.def_id)
        slain_end = self._place_centre(change.to)
        width = max(48, self.layout.monster_card_w)
        fall = CardMoveAnimation(
            card_def, at, self._corner(slain_end, width),
            self.layout.card_box(width), 0.9,
            face_down=False, spin=-35.0, arc=60.0,
        )
        self.fx.add(fall)

    def _on_card_appeared(self, change: CardAppeared) -> None:
        if change.to.zone == "monster_row":
            self.log.add(f"{self._card_name(change.def_id)} appeared",
                         colour=C.WARN, icon="monster")
            if self.flags["animations"]:
                start = self._place_centre_of("monster_deck", None)
                end = self._place_centre(change.to)
                width = self.layout.monster_card_w
                self.fx.add(CardMoveAnimation(
                    self.registry.get(change.def_id),
                    self._corner(start, width), self._corner(end, width),
                    self.layout.card_box(width), 0.5, face_down=True, flip=True,
                ))
            self.cue("zone.monster_row")
        elif change.to.zone == "hand" and self.flags["animations"]:
            self._deal_flight(change.to, change.def_id)

    def _on_card_vanished(self, change: CardVanished) -> None:
        if self.flags["animations"]:
            self.fx.add(RingBurstAnimation(
                self._place_centre(change.frm), C.INK_DIM, 0.4, radius=54, rings=2
            ))

    def _on_zone_changed(self, change: ZoneCountChanged) -> None:
        # A hidden hand growing is the only evidence of a draw we are allowed to
        # see for an opponent, so it is what drives their deal animation.
        if change.zone == "hand" and change.delta > 0 and change.owner != self.seat:
            for i in range(min(change.delta, 5)):
                self._deal_flight_to_owner(change.owner, delay=i * 0.09)
            self.cue("game.deal")

    def _deal_flight(self, place: Any, def_id: str) -> None:
        start = self._place_centre_of("main_deck", None)
        end = self._place_centre(place)
        width = self.layout.hand_card_w
        self.fx.add(CardMoveAnimation(
            self.registry.get(def_id), self._corner(start, width), self._corner(end, width),
            self.layout.card_box(width), 0.42, face_down=True, flip=True,
        ))

    def _deal_flight_to_owner(self, owner: str | None, *, delay: float = 0.0) -> None:
        start = self._place_centre_of("main_deck", None)
        end = self._place_centre_of("hand", owner)
        width = max(40, self.layout.rail_card_w)
        self.fx.add(CardMoveAnimation(
            None, self._corner(start, width), self._corner(end, width),
            self.layout.card_box(width), 0.4, face_down=True, delay=delay, trail=False,
        ))

    def _on_turn_changed(self, change: TurnChanged) -> None:
        player = self.view.players.get(change.active_player)
        name = player.name if player else str(change.active_player)
        colour = T.seat_colour(player.seat) if player else C.GOLD
        self.log.add(f"Turn {change.turn_number} \u2014 {name}", colour=colour, icon="bolt")
        self.cue("game.turn")
        if self.flags["animations"]:
            self.fx.add(TableLightSweep(pygame.Rect(self.layout.table_rect), 1.4))
            self.fx.add(BannerAnimation(
                f"{name}'s turn", f"Turn {change.turn_number}", colour=colour,
                icon="leader", duration=0.85, y_fraction=0.18,
            ))

    def _on_points_changed(self, change: PointsChanged) -> None:
        if change.delta >= 0 or not self.flags["animations"]:
            return
        anchor = self.layout.dice_rect if change.player == self.seat else \
            (self.rail.strip_of(change.player).rect if self.rail.strip_of(change.player)
             else self.layout.dice_rect)
        player = self.view.players.get(change.player)
        remaining = int(player.action_points) if player else 0
        self.fx.add(APSpendAnimation((anchor.centerx, anchor.top + 8), remaining=remaining))

    def _on_roll_modified(self, change: RollModified) -> None:
        amount = int(getattr(change.modifier, "amount", 0) or 0)
        label = getattr(change.modifier, "label", "") or "modifier"
        sign = "+" if amount > 0 else ""
        area = pygame.Rect(self.dice.dice_area)
        self.log.add(f"{label}: {sign}{amount}",
                     colour=C.GOOD if amount > 0 else C.BAD, icon="modifier")
        self.cue("game.modifier")
        if self.flags["animations"]:
            self.fx.add(ModifierPopAnimation(
                f"{sign}{amount}", area.center, C.GOOD if amount > 0 else C.BAD, 1.1,
            ))

    def _log_roll(self, roll: Any) -> None:
        who = self._owner_name(getattr(roll, "roller", None))
        tag = getattr(roll, "band_tag", "") or ""
        colour = C.GOOD if tag in ("success", "slay") else (C.BAD if tag == "failure" else C.INK)
        self.log.add(f"{who} rolled {roll.describe()}", colour=colour, icon="dice")
        # A band's tag is base-pack convention, not an engine concept
        # (`rules_engine.md`, Phase 7 decision 4), so which of them cheer is a
        # table a variant can extend rather than a comparison in this file.
        self.cue(f"band.{tag}")

    def _on_game_won(self, change: GameWon) -> None:
        player = self.view.players.get(change.winner)
        name = player.name if player else str(change.winner)
        self.log.add(f"{name} wins!", colour=C.GOLD, icon="leader")
        self.cue("game.victory")
        if self.flags["animations"]:
            self.fx.add(ConfettiAnimation((self.layout.width, self.layout.height), 5.0))

    # ------------------------------------------------------------------
    # Locating things on screen
    # ------------------------------------------------------------------

    def _locate_card(self, card_id: str) -> CardSprite | None:
        """Find the sprite for a card, wherever on the board it is drawn."""
        for sprite in self._all_sprites():
            if sprite.card_id == card_id:
                return sprite
        return None

    def _all_sprites(self) -> list[CardSprite]:
        out: list[CardSprite] = []
        out.extend(self.hand.row.sprites)
        out.extend(self.party.row.sprites)
        if self.party.leader_sprite:
            out.append(self.party.leader_sprite)
        out.extend(self.monsters.sprites)
        out.extend(self.active.sprites)
        for strip in self.rail.strips:
            out.extend(strip.sprites)
            if strip.leader_sprite:
                out.append(strip.leader_sprite)
        out.extend(self.tray.sprites)
        out.extend(self.spawned)
        return out

    def _place_centre(self, place: Any) -> tuple[int, int]:
        return self._place_centre_of(place.zone, place.owner)

    def _place_centre_of(self, zone: str, owner: str | None) -> tuple[int, int]:
        layout = self.layout
        mine = owner is None or owner == self.seat
        if zone in ("main_deck", "discard", "monster_deck"):
            rect = self.decks.rect_of(zone)
            return rect.center if rect else layout.deck_area_rect.center
        if zone == "monster_row":
            return layout.monster_row_rect.center
        if zone == "limbo":
            return layout.left_rail_rect.center
        if mine:
            return {
                "hand": layout.hand_rect,
                "party": layout.party_rect,
                "leader": layout.leader_rect,
                "slain": layout.party_rect,
            }.get(zone, layout.board_rect).center
        strip = self.rail.strip_of(owner) if owner else None
        return strip.rect.center if strip else layout.right_rail_rect.center

    @staticmethod
    def _corner(centre: tuple[int, int], width: int) -> tuple[int, int]:
        """Animations take a top-left; panels think in centres."""
        height = int(width * M.CARD_ASPECT)
        return centre[0] - width // 2, centre[1] - height // 2

    def _card_view(self, card_id: str) -> Any:
        for zone in (self.view.zones or {}).values():
            for card in zone.cards:
                if card.id == card_id:
                    return card
        for player in (self.view.players or {}).values():
            for zone in (player.zones or {}).values():
                for card in zone.cards:
                    if card.id == card_id:
                        return card
        return None

    def _card_name(self, def_id: str) -> str:
        card_def = self.registry.get(def_id)
        return str(getattr(card_def, "name", None) or def_id or "a card")

    def _owner_name(self, player_id: str | None) -> str:
        if not player_id:
            return "someone"
        player = self.view.players.get(player_id) if self.view else None
        return player.name if player else str(player_id)

    # ------------------------------------------------------------------
    # Prompt, detail and log rendering
    # ------------------------------------------------------------------

    def _draw_prompt(self, screen: pygame.Surface) -> None:
        request = self.presenter.pending_request
        if request is None:
            return
        rect = pygame.Rect(self.layout.prompt_rect)
        prompt = self.prompt
        thinking = self.presenter.thinking_seat
        accent = C.IDLE if thinking else prompt.accent

        T.glass(screen, rect, radius=rect.height // 2, fill=C.GLASS,
                rim=T.alpha(accent, 200))
        T.round_rect(screen, pygame.Rect(rect.left, rect.top, 5, rect.height), accent,
                     radius=3)
        draw_icon(screen, prompt.icon, (rect.left + 26, rect.centery), 18, accent)

        player = self.view.players.get(request.requester)
        who = player.name if player else str(request.requester)
        if thinking:
            text, hint = L.THINKING.format(name=who), prompt.text
        else:
            text, hint = prompt.text or L.YOUR_MOVE, prompt.hint
        T.text(screen, text, (rect.left + 44, rect.centery - (7 if hint else 0)),
               T.ui(15, bold=True), C.INK_BRIGHT, anchor="midleft", max_width=rect.width - 120,
               shadow=None)
        if hint:
            T.text(screen, hint, (rect.left + 44, rect.centery + 12), T.ui(12),
                   C.INK_DIM, anchor="midleft", shadow=None, max_width=rect.width - 120)

        if isinstance(request, ChooseCards):
            badge = f"{len(self.selected)}/{request.maximum}"
            T.pill(screen, pygame.Rect(rect.right - 62, rect.centery - 11, 52, 22), badge,
                   bg=T.alpha(accent, 90), fg=C.INK_BRIGHT, border=T.alpha(accent, 200),
                   fnt=T.ui(11, bold=True))

    def _draw_detail(self, screen: pygame.Surface) -> None:
        """Hover detail: card face on the left, readable rules column on the right."""
        if self.detail_card is None or self.detail_anchor is None:
            return
        panel = self.layout.detail_at(self.detail_anchor)
        T.glass(screen, panel, radius=M.RADIUS_L, fill=C.GLASS,
                rim=T.alpha(C.GOLD, 140))

        pad = T.s(12)
        card_w = min(T.s(240), max(T.s(180), int(panel.width * 0.42)))
        card_h = int(card_w * M.CARD_ASPECT)
        # Cap card height so the panel stays compact.
        max_card_h = panel.height - pad * 2 - T.s(22)
        if card_h > max_card_h:
            card_h = max_card_h
            card_w = int(card_h / M.CARD_ASPECT)
        card_rect = pygame.Rect(panel.left + pad, panel.top + pad, card_w, card_h)
        T.drop_shadow(screen, card_rect, radius=10, spread=14, offset=(0, 6), strength=100)
        screen.blit(render_card(self.detail_card, card_w, card_h, detail=True),
                    card_rect.topleft)

        facts = card_facts(self.detail_card)
        col_x = card_rect.right + T.s(14)
        col_w = panel.right - col_x - pad
        y = panel.top + pad

        name = L.card_name(self.detail_card)
        T.text(screen, name, (col_x, y), T.display(16), C.INK_BRIGHT,
               max_width=col_w, shadow=(0, 0, 0, 120))
        y += T.display(16).get_linesize() + 4

        chip_y = y
        kind_label = facts.type_line or facts.kind
        chip_w = min(col_w, T.ui(10, bold=True).size(kind_label)[0] + T.s(16))
        T.pill(screen, pygame.Rect(col_x, chip_y, chip_w, T.s(20)), kind_label,
               bg=T.alpha(facts.accent, 70), fg=C.INK_BRIGHT,
               border=T.alpha(facts.accent, 180), fnt=T.ui(10, bold=True))
        if facts.card_class:
            class_label = L.class_label(facts.card_class)
            cw = min(col_w - chip_w - 6, T.ui(10, bold=True).size(class_label)[0] + T.s(16))
            T.pill(screen, pygame.Rect(col_x + chip_w + 6, chip_y, cw, T.s(20)), class_label,
                   bg=T.alpha(C.INK_FAINT, 40), fg=C.INK,
                   border=T.alpha(C.INK_FAINT, 100), fnt=T.ui(10, bold=True))
        y = chip_y + T.s(28)

        if facts.threshold is not None:
            roll_line = f"{facts.threshold}+ · {facts.threshold_label}".strip(" ·")
            T.text(screen, roll_line, (col_x, y), T.ui(11, bold=True),
                   C.GOLD, max_width=col_w, shadow=None)
            y += T.ui(11).get_linesize() + 4

        if facts.requirement:
            T.text(screen, f"Necesită: {L.requirement_label(facts.requirement)}",
                   (col_x, y), T.ui(11, bold=True),
                   C.WARN, max_width=col_w, shadow=None)
            y += T.ui(11).get_linesize() + 6

        text_value = L.card_text(self.detail_card)
        if text_value:
            # Unscaled serif so rules stay readable regardless of chrome UI scale.
            rules_font = T.font(14, family=T.FONT_SERIF)
            text_box = pygame.Rect(col_x, y, col_w, panel.bottom - y - T.s(26))
            T.draw_wrapped(screen, text_value, text_box,
                           rules_font, C.INK, line_gap=3)

        T.text(screen, L.RIGHT_CLICK_PIN, (panel.centerx, panel.bottom - T.s(12)),
               T.ui(9), C.INK_FAINT, anchor="center", shadow=None)
        draw_icon(screen, card_icon_name(facts.kind, facts.card_class),
                  (panel.right - T.s(18), panel.top + T.s(18)), 15, facts.accent)

    def _draw_log(self, screen: pygame.Surface) -> None:
        """Single journal: limbo notes + recent events above np.random."""
        rect = pygame.Rect(getattr(self.layout, "log_rect", None) or (0, 0, 0, 0))
        if rect.width < 40 or rect.height < 40:
            return
        T.glass(screen, rect, radius=T.s(10), fill=(14, 20, 26, 210),
                rim=T.alpha(C.INK_FAINT, 90))
        title = L.IN_PLAY if self.active.occupied else L.JOURNAL
        T.text(screen, title, (rect.left + T.s(10), rect.top + T.s(6)),
               T.ui(12, bold=True), T.alpha(C.INK_FAINT, 220), shadow=None)

        y = rect.top + T.s(24)
        # Active notes (pending prompt / recent rolls) share this feed.
        for note, colour, icon in self.active.notes[:3]:
            x = rect.left + T.s(10)
            if icon:
                draw_icon(screen, icon, (x + 6, y + 7), 13, colour)
                x += 19
            used = T.draw_wrapped(
                screen, note,
                pygame.Rect(x, y, rect.right - x - T.s(8), T.s(36)),
                T.ui(10), colour, line_gap=1,
            )
            y += max(16, used) + 4
            if y > rect.bottom - T.s(40):
                break

        # Limbo card strip claims vertical space when present.
        if self.active.sprites:
            y = max(y, self.active.sprites[-1].rect.bottom + 6)

        self.log.rect = pygame.Rect(
            rect.left + T.s(8), y,
            rect.width - T.s(16), max(20, rect.bottom - y - T.s(6)),
        )
        self.log.draw(screen, newest_first=True)

    def _draw_spawned(self, screen: pygame.Surface) -> None:
        if not self.spawned:
            return
        first = self.spawned[0].rect
        band = pygame.Rect(first.left - 12, first.top - 26,
                           self.spawned[-1].rect.right - first.left + 24, first.height + 40)
        T.glass(screen, band, radius=M.RADIUS_L, fill=C.GLASS,
                rim=T.alpha(C.ARCANE, 160))
        T.text(screen, "DEV SANDBOX \u00b7 NOT IN PLAY", (band.left + 12, band.top + 6),
               T.ui(9, bold=True), C.ARCANE, shadow=None)
        for sprite in self.spawned:
            sprite.draw(screen)

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> bool:
        if self.overlays.items:
            if self.overlays.handle_event(event):
                return True
            if self.overlays.busy:
                return True

        if event.type == pygame.KEYDOWN and self._hotkey(event):
            return True
        if self.replay_bar is not None and self.replay_bar.handle_event(event):
            return True
        if event.type == pygame.MOUSEMOTION:
            self._on_motion(event.pos)
        if self.corner.handle_event(event):
            return True
        if self.action_bar.handle_event(event):
            return True
        if self.dice.handle_event(event) or self.effects.handle_event(event):
            return True
        if self.menu.handle_event(event):
            return True
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            return self._on_right_click(event.pos)
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            return self._on_click(event.pos)
        return False

    def _hotkey(self, event: pygame.event.Event) -> bool:
        mods = pygame.key.get_mods()
        key = event.key

        request = self.presenter.awaiting_human
        if isinstance(request, ChooseIntent):
            ch = getattr(event, "unicode", "") or ""
            if not ch and pygame.K_a <= key <= pygame.K_z:
                ch = chr(ord("a") + key - pygame.K_a)
            if ch:
                action_id = key_to_action(ch)
                if action_id is not None:
                    intents = [i for i in request.intents if i.action == action_id]
                    return self._handle_action_key(action_id, intents)

        if self.replay is not None and self._replay_key(key):
            return True
        if key == pygame.K_q or key == pygame.K_LEFT:
            self._switch_camera(self.cameras.prev)
            return True
        # Ctrl+Shift+E is the dev console's neighbour, not a camera step.
        if (key in (pygame.K_e, pygame.K_RIGHT)) and not (
            (mods & pygame.KMOD_CTRL) and (mods & pygame.KMOD_SHIFT)
        ):
            self._switch_camera(self.cameras.next)
            return True
        if key == pygame.K_d and (mods & pygame.KMOD_CTRL) and (mods & pygame.KMOD_SHIFT):
            self.open_dev_console()
            return True
        if key in (pygame.K_i, pygame.K_F1):
            self.open_rules()
            return True
        if key == pygame.K_l:
            self.open_log()
            return True
        if key == pygame.K_ESCAPE:
            if self._action_filter is not None:
                self._action_filter = None
                self._build_menu()
                return True
            self.open_menu()
            return True
        if key == pygame.K_m:
            self.dev_toggle("sound")
            return True
        if key == pygame.K_o:
            self.open_settings()
            return True
        if key == pygame.K_F2 and self.hooks.save_game is not None:
            self.save_game()
            return True
        if key == pygame.K_F9 and self.hooks.load_game is not None:
            self.open_saves()
            return True
        if key == pygame.K_F3:
            self.dev_toggle("fps")
            return True
        if key == pygame.K_F4:
            self.dev_toggle("layout_debug")
            return True
        if key == pygame.K_F11 and self.hooks.toggle_fullscreen:
            self.hooks.toggle_fullscreen()
            return True
        if key == pygame.K_r:
            self._on_roll_pressed()
            return True

        if request is None:
            return False
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            return self._confirm_key()
        if key == pygame.K_SPACE:
            return self._decline_key()
        if pygame.K_1 <= key <= pygame.K_9:
            index = key - pygame.K_1
            if isinstance(request, ReactionPrompt) and index < len(request.options):
                self._submit(ReactionChosen(request.options[index].card))
                return True
            if self.menu.press(index):
                return True
            if self.tray.visible and index < len(self.tray.sprites):
                self._toggle_card(self.tray.sprites[index].card_id)
                return True
        if key == pygame.K_TAB and self.prompt.candidates:
            self._cycle_candidate()
            return True
        return False

    def _replay_key(self, key: int) -> bool:
        """Transport keys, live only while watching a recorded game.

        Checked before the camera keys, because Space would otherwise fall to
        ``_decline_key`` — which does nothing in a replay (no seat is human) and
        would therefore swallow the most obvious "pause" key in the client.
        """
        transport = self.replay
        if transport is None:
            return False
        if key == pygame.K_SPACE:
            self.toast.show(
                "playing" if transport.toggle() else "paused",
                colour=C.GOLD, duration=1.0, icon="play" if transport.playing else "pause",
            )
            return True
        if key in (pygame.K_PERIOD, pygame.K_RIGHTBRACKET):
            transport.step()
            return True
        if key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
            self.toast.show(f"{transport.faster():g}x", colour=C.GOLD, duration=1.0, icon="bolt")
            return True
        if key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            self.toast.show(f"{transport.slower():g}x", colour=C.GOLD, duration=1.0, icon="bolt")
            return True
        return False

    def _handle_action_key(self, action_id: str, intents: list[Intent]) -> bool:
        self._handle_action(action_id, intents)
        return True

    def _confirm_key(self) -> bool:
        request = self.presenter.awaiting_human
        if isinstance(request, ChooseCards):
            if request.minimum <= len(self.selected) <= request.maximum:
                self._confirm_cards()
                return True
            self.toast.show(f"Pick at least {request.minimum}", colour=C.WARN, icon="target")
            return True
        if isinstance(request, Confirm):
            self._submit(Confirmed(True))
            return True
        if self.menu.buttons:
            return self.menu.press(0)
        return False

    def _decline_key(self) -> bool:
        request = self.presenter.awaiting_human
        if isinstance(request, ReactionPrompt):
            self._pass_reaction()
            return True
        if isinstance(request, Confirm):
            self._submit(Confirmed(False))
            return True
        if isinstance(request, ChooseCards) and request.minimum == 0:
            self._submit(CardsChosen(()))
            return True
        if self.focus_card is not None or self._action_filter is not None:
            self.focus_card = None
            self._action_filter = None
            self._build_menu()
            return True
        return False

    def _cycle_candidate(self) -> None:
        candidates = [c for c in self.prompt.candidates]
        if not candidates:
            return
        current = self.selected[-1] if self.selected else None
        index = candidates.index(current) + 1 if current in candidates else 0
        self.selected = [candidates[index % len(candidates)]]
        self._build_confirm_row()

    def _on_motion(self, pos: tuple[int, int]) -> None:
        hits = [
            self.tray.update_hover(pos),
            self.hand.update_hover(pos),
            self.party.update_hover(pos),
            self.monsters.update_hover(pos),
            self.active.update_hover(pos),
            self.rail.update_hover(pos),
        ]
        hovered = next((h for h in hits if h is not None), None)
        for sprite in self.spawned:
            if sprite.update_hover(pos):
                hovered = sprite
        self.decks.update_hover(pos)
        self.hovered = hovered

        if hovered is not None and hovered.card_def is not None and not hovered.face_down:
            self.detail_card = hovered.card_def
            self.detail_anchor = pygame.Rect(hovered.rect)
        else:
            self.detail_card = None
            self.detail_anchor = None

        tip = self._tooltip_for(pos)
        if tip:
            self.tooltip.show(tip, pos)
        else:
            self.tooltip.hide()

    def _tooltip_for(self, pos: tuple[int, int]) -> str:
        for button in self.corner.buttons:
            if button.rect.collidepoint(pos):
                return button.tooltip
        slot = self.decks.slot_at(pos)
        if slot == "main_deck":
            return "Main deck \u2014 Heroes, Items, Magic, Modifiers, Challenges"
        if slot == "discard":
            return "Discard \u2014 the burnt cards"
        if slot == "monster_deck":
            return "Monster deck \u2014 refills the row"
        return ""

    def _on_click(self, pos: tuple[int, int]) -> bool:
        if self.tray.visible:
            sprite = self.tray.card_at(pos)
            if sprite is not None:
                self._toggle_card(sprite.card_id)
                return True

        # Camera strip jump targets.
        if self._click_camera_strip(pos):
            return True

        sprite = (
            self.hand.card_at(pos) or self.party.card_at(pos) or self.monsters.card_at(pos)
            or self.active.card_at(pos) or self._rail_card_at(pos)
        )
        if sprite is not None and self._on_card_click(sprite):
            return True

        if self.prompt.players:
            player = self.rail.player_at(pos)
            if player in self.prompt.players:
                self._submit(PlayerChosen(player))
                return True
            if self.view.you.id in self.prompt.players and (
                self.layout.party_rect.collidepoint(pos)
                or self.layout.leader_rect.collidepoint(pos)
            ):
                self._submit(PlayerChosen(self.view.you.id))
                return True

        # Click an opponent pile → look at their seat (animated camera move).
        opponent = self.rail.player_at(pos)
        if (
            opponent
            and not self.prompt.players
            and self.cameras.active.player_id != opponent
        ):
            self._switch_camera(lambda: self.cameras.jump_opponent(opponent))
            return True

        if sprite is not None:
            self.open_card(sprite.card_def)
            return True
        if self.focus_card is not None:
            self.focus_card = None
            self._build_menu()
            return True
        return False

    def _switch_camera(self, action: Callable[[], Any]) -> None:
        """Animate a real viewpoint move: snapshot → reframe → ease with blend."""
        self._cam_from = self.layout.snapshot()
        action()
        self.layout.apply_camera(self.cameras.active.key)
        self._cam_to = self.layout.snapshot()
        self.cameras.blend = 0.0
        # Ease immediately into the first frame so the jump is never instant.
        self.layout.apply_lerp(self._cam_from, self._cam_to, 0.0)

    def _click_camera_strip(self, pos: tuple[int, int]) -> bool:
        strip = getattr(self.layout, "camera_strip_rect", None)
        if strip is None or not strip.collidepoint(pos):
            return False
        n = len(self.cameras.views)
        if n <= 0:
            return False
        gap = 6
        slot_w = max(48, (strip.width - gap * (n - 1)) // n)
        x = strip.left
        for i, _view in enumerate(self.cameras.views):
            slot = pygame.Rect(x, strip.top, min(slot_w, 140), strip.height)
            if slot.collidepoint(pos):
                if self.cameras.index != i:
                    target = i

                    def _jump(idx: int = target) -> None:
                        self.cameras.index = idx
                        self.cameras.blend = 0.0

                    self._switch_camera(_jump)
                return True
            x += slot_w + gap
        return False

    def _rail_card_at(self, pos: tuple[int, int]) -> CardSprite | None:
        for strip in self.rail.strips:
            hit = strip.card_at(pos)
            if hit is not None:
                return hit
        return None

    def _on_card_click(self, sprite: CardSprite) -> bool:
        request = self.presenter.awaiting_human
        if request is None:
            return False
        if isinstance(request, ChooseCards):
            if sprite.card_id in self.prompt.candidates:
                self._toggle_card(sprite.card_id)
                return True
            return False
        if isinstance(request, ChooseIntent):
            intents = self.prompt.intents_by_card.get(sprite.card_id, [])
            if len(intents) == 1:
                intent = intents[0]
                if intent.action in ROLL_ACTIONS:
                    self._arm_roll(intent)
                else:
                    self._submit(IntentChosen(intent))
                return True
            if intents:
                # Two things you could do with this card: scope the menu to it
                # rather than opening a second popup.
                self.focus_card = sprite.card_id
                self._build_menu()
                self.cue("ui.hover")
                return True
        return False

    def _toggle_card(self, card_id: str) -> None:
        request = self.presenter.awaiting_human
        if not isinstance(request, ChooseCards) or card_id not in request.candidates:
            return
        if card_id in self.selected:
            self.selected.remove(card_id)
        else:
            if len(self.selected) >= request.maximum:
                # At a maximum of one, clicking another card should move the
                # pick rather than refuse it.
                if request.maximum == 1:
                    self.selected.clear()
                else:
                    self.toast.show(f"You may choose {request.maximum}", colour=C.WARN,
                                    icon="target")
                    return
            self.selected.append(card_id)
        self.cue("ui.click", volume=0.6)
        self._build_confirm_row()
        if request.minimum == request.maximum == len(self.selected):
            self._confirm_cards()

    def _on_right_click(self, pos: tuple[int, int]) -> bool:
        sprite = (
            self.hand.card_at(pos) or self.party.card_at(pos) or self.monsters.card_at(pos)
            or self.active.card_at(pos) or self._rail_card_at(pos)
            or (self.tray.card_at(pos) if self.tray.visible else None)
        )
        if sprite is not None and sprite.card_def is not None:
            self.open_card(sprite.card_def)
            return True
        slot = self.decks.slot_at(pos)
        if slot == "discard" and self.decks.discard_top is not None:
            self.open_card(self.decks.discard_top)
            return True
        return False

    # ------------------------------------------------------------------
    # Overlays
    # ------------------------------------------------------------------

    def open_rules(self) -> None:
        self.cue("ui.open")
        self.overlays.toggle(lambda: RulesOverlay(self.layout, self.registry), RulesOverlay)

    def open_log(self) -> None:
        self.cue("ui.open")
        self.overlays.toggle(
            lambda: LogOverlay(self.layout, list(self.log.entries)), LogOverlay
        )

    def open_card(self, card_def: Any) -> None:
        if card_def is None:
            return
        self.cue("ui.hover")
        self.overlays.push(CardOverlay(self.layout, card_def))

    def open_menu(self) -> None:
        if self.overlays.items:
            self.overlays.pop()
            return
        self.cue("ui.open")
        items = [
            MenuItem("resume", L.MENU_RESUME, icon="play"),
            MenuItem("rules", L.MENU_RULES, icon="info", subtitle="I or F1"),
            MenuItem("log", L.MENU_LOG, icon="scroll", subtitle="L"),
            MenuItem("settings", L.MENU_SETTINGS, icon="gear", subtitle="O"),
        ]
        # Save and load appear only when the window offers them. A replay
        # viewer has no game to save and a headless test scene has no disk, and
        # a menu row that cannot do anything is worse than a missing one.
        if self.hooks.save_game is not None:
            items.append(MenuItem("save", L.MENU_SAVE, icon="deck", subtitle="F2"))
        if self.hooks.load_game is not None:
            items.append(MenuItem("load", L.MENU_LOAD, icon="scroll", subtitle="F9"))
        items += [
            MenuItem("fullscreen", L.MENU_FULLSCREEN, icon="eye", subtitle="F11"),
            MenuItem("console", L.MENU_CONSOLE, icon="flask", subtitle="Ctrl+Shift+D"),
            MenuItem("restart", L.MENU_RESTART, icon="dice", subtitle=L.MENU_SAME_PLAYERS),
            MenuItem("quit", L.MENU_QUIT, icon="close", danger=True),
        ]
        self.overlays.push(MenuOverlay(self.layout, items, subtitle=L.MENU_RESUME_HINT))

    def open_settings(self) -> None:
        """The settings screen, seeded from whatever the window has loaded."""
        if self.hooks.settings is None:
            self.toast.show("no settings in this session", colour=C.WARN, icon="close")
            return
        self.cue("ui.open")
        self.overlays.toggle(
            lambda: SettingsOverlay(
                self.layout, self.hooks.settings(), on_change=self.apply_settings,
            ),
            SettingsOverlay,
        )

    def apply_settings(self, settings: Any) -> None:
        """Make preferences true on this board, right now.

        Called on every click in the settings screen and once at startup, so
        there is exactly one place that knows which preference means what to a
        scene. Anything that belongs to the *window* — fullscreen, HUD scale,
        AI pace — is the app's half and is not touched here.
        """
        self.flags["sound"] = bool(settings.sound)
        self.flags["animations"] = bool(settings.animations)
        self.flags["shake"] = bool(settings.shake)
        self.flags["reaction_timer"] = bool(settings.reaction_timer)
        self.sound.enabled = bool(settings.sound)
        self.sound.set_volume(float(settings.volume))
        self.fx.enabled = bool(settings.animations)
        if not settings.animations:
            self.fx.clear()

    def save_game(self) -> None:
        """Save through the window, and say what happened either way.

        A refusal is normal rather than exceptional: an AI seat deliberating is
        the engine mid-step, and `Engine.savepoint` says no. Telling the player
        to try again in a moment is the honest answer; writing a save that
        describes a position nobody was in is not.
        """
        if self.hooks.save_game is None:
            return
        try:
            message = self.hooks.save_game()
        except Exception as exc:
            from here_to_slay.core.savegame import SaveError

            refused = isinstance(exc, SaveError) and not self.engine.savepoint
            self.toast.show(
                L.SAVE_REFUSED if refused else L.SAVE_FAILED.format(why=exc),
                colour=C.WARN if refused else C.BAD, icon="close", duration=3.0,
            )
            self.cue("ui.error")
            return
        self.toast.show(message, colour=C.GOOD, icon="check", duration=2.6)
        self.cue("ui.save")

    def open_saves(self) -> None:
        """The load screen. Reads headers only — no game is replayed to list it."""
        if self.hooks.list_saves is None:
            return
        self.cue("ui.open")
        self.overlays.toggle(
            lambda: SaveListOverlay(self.layout, self.hooks.list_saves()), SaveListOverlay
        )

    def open_dev_console(self) -> None:
        self.cue("ui.open")
        self.overlays.toggle(lambda: DevConsole(self.layout, self), DevConsole)

    def _overlay_finished(self, overlay: Any) -> None:
        self.cue("ui.close")
        result = overlay.result
        if isinstance(overlay, HandoverOverlay):
            self.presenter.acknowledge_transition()
            self._handover_for = None
            self.tracker.reset()  # the incoming seat sees a different board
            return
        if isinstance(overlay, GameOverOverlay):
            if result == "restart" and self.hooks.new_game:
                self.hooks.new_game()
            elif self.hooks.quit:
                self.hooks.quit()
            return
        if isinstance(overlay, SaveListOverlay):
            chosen = overlay.chosen()
            if chosen is not None and self.hooks.load_game is not None:
                self.hooks.load_game(chosen)
            return
        if isinstance(overlay, SettingsOverlay):
            if self.hooks.save_settings is not None:
                self.hooks.save_settings(overlay.result)
            return
        if isinstance(overlay, MenuOverlay):
            self._menu_action(str(result or "resume"))

    def _menu_action(self, key: str) -> None:
        if key == "rules":
            self.open_rules()
        elif key == "log":
            self.open_log()
        elif key == "settings":
            self.open_settings()
        elif key == "save":
            self.save_game()
        elif key == "load":
            self.open_saves()
        elif key in ("sound", "animations"):
            self.dev_toggle(key)
            self.open_menu()
        elif key == "fullscreen" and self.hooks.toggle_fullscreen:
            self.hooks.toggle_fullscreen()
        elif key == "console":
            self.open_dev_console()
        elif key == "restart" and self.hooks.new_game:
            self.hooks.new_game()
        elif key == "quit" and self.hooks.quit:
            self.hooks.quit()

    def _check_handover(self) -> None:
        seat = self.presenter.transition_seat
        if seat is None or seat == self._handover_for:
            return
        if self.overlays.has(HandoverOverlay):
            return
        player = self.view.players.get(seat)
        self._handover_for = seat
        self.overlays.push(HandoverOverlay(
            self.layout, player.name if player else str(seat),
            seat_colour=T.seat_colour(player.seat) if player else C.GOLD,
            turn=self.view.turn_number,
        ))

    def _check_game_over(self) -> None:
        if self._game_over_shown or not self.engine.over:
            return
        self._game_over_shown = True
        winner = self.engine.winner
        rows: list[ScoreRow] = []
        for pid in self.view.turn_order:
            player = self.view.players[pid]
            party = player.zone("party")
            slain = player.zone("slain")
            leader = player.zone("leader")
            classes = tuple(sorted({
                cls for cards in ((party.cards if party else ()), (leader.cards if leader else ()))
                for cv in cards
                if (cls := getattr(self.registry.get(cv.def_id), "card_class", None))
            }))
            rows.append(ScoreRow(
                name=player.name,
                detail=(
                    f"{len(slain.cards) if slain else 0} slain \u00b7 "
                    f"{len(party.cards) if party else 0} in party"
                ),
                colour=T.seat_colour(player.seat),
                winner=pid == winner,
                classes=classes,
            ))
        winner_player = self.view.players.get(winner) if winner else None
        self.overlays.push(GameOverOverlay(
            self.layout,
            f"{winner_player.name} wins" if winner_player else "No winner",
            rows,
            reason=self._victory_reason(winner) if winner else "the turn limit ran out",
            winner_colour=T.seat_colour(winner_player.seat) if winner_player else C.INK_DIM,
            turns=self.view.turn_number,
        ))

    def _victory_reason(self, winner: str) -> str:
        """Which condition fired, read off the board rather than the engine."""
        player = self.view.players.get(winner)
        if player is None:
            return ""
        slain = player.zone("slain")
        if slain and len(slain.cards) >= self._slay_target:
            return f"slew {len(slain.cards)} monsters"
        return "assembled a Hero of every class"

    # ------------------------------------------------------------------
    # DevHost
    # ------------------------------------------------------------------

    def dev_cards(self) -> Sequence[Any]:
        return list(self.registry.cards.values())

    def dev_spawn_card(self, card_def: Any) -> None:
        """Fly a card in and park it in the sandbox tray.

        Presentation only, and labelled as such on screen: the UI layer cannot
        add a card to a live game, and pretending otherwise would make the
        console a liar.
        """
        width = max(64, min(self.layout.hand_card_w, 108))
        height = int(width * M.CARD_ASPECT)
        row = pygame.Rect(self.layout.monster_row_rect)
        index = len(self.spawned)
        if index >= 8:
            self.spawned.pop(0)
            index = len(self.spawned)
            for i, sprite in enumerate(self.spawned):
                sprite.rect.left = row.left + 24 + i * (width + 8)
        rect = pygame.Rect(row.left + 24 + index * (width + 8), row.top + 30, width, height)
        self.spawned.append(CardSprite(
            f"__dev_{index}_{getattr(card_def, 'id', '?')}", card_def, rect, lift_on_hover=16,
        ))
        if self.flags["animations"]:
            self.fx.add(CardMoveAnimation(
                card_def, (self.layout.width // 2, self.layout.height + 40), rect.topleft,
                (width, height), 0.5, spin=180.0,
            ))
            self.fx.add(ParticleBurstAnimation(rect.center, (C.ARCANE, C.GOLD), 0.6, count=10))
        self.cue("zone.party")

    def dev_inspect_card(self, card_def: Any) -> None:
        self.overlays.push(CardOverlay(self.layout, card_def))

    def dev_clear_spawned(self) -> None:
        self.spawned.clear()

    def dev_fx_names(self) -> Sequence[str]:
        return tuple(_DEV_FX)

    def dev_play_fx(self, name: str) -> None:
        runner = _DEV_FX.get(name)
        if runner is not None:
            runner(self)

    def dev_sound_names(self) -> Sequence[str]:
        return self.sound.cue_names or ()

    def dev_play_sound(self, name: str) -> None:
        self.sound.play(name)

    def dev_new_game(self, *, players: int, ai_seats: int, seed: int | None) -> None:
        if self.hooks.new_game:
            self.hooks.new_game(players=players, ai_seats=ai_seats, seed=seed)

    def dev_flags(self) -> Mapping[str, bool]:
        return dict(self.flags)

    def dev_toggle(self, flag: str) -> bool:
        state = not self.flags.get(flag, False)
        self.flags[flag] = state
        if flag == "sound":
            self.sound.enabled = state
            if state:
                self.cue("ui.click")
        elif flag == "animations":
            self.fx.enabled = state
            if not state:
                self.fx.clear()
        elif flag == "autoplay":
            self.presenter.set_human_seats(set() if state else None)
        elif flag == "reveal_all":
            self.tracker.reset()
        elif flag == "force_cpu_materials":
            materials.set_force_cpu(state)
        self.toast.show(
            f"{flag.replace('_', ' ')}: {'on' if state else 'off'}",
            colour=C.GOOD if state else C.INK_DIM, icon="check" if state else "close",
        )
        return state

    def dev_paused(self) -> bool:
        return bool(self.presenter.paused)

    def dev_toggle_pause(self) -> bool:
        return self.presenter.toggle_pause()

    def dev_step(self) -> None:
        self.presenter.step()

    def dev_let_ai_decide(self) -> None:
        """Answer the open request with the presenter's agent."""
        request = self.presenter.awaiting_human
        agent = getattr(self.presenter, "agent", None)
        if request is None or agent is None:
            self.toast.show("nothing to answer, or no agent", colour=C.WARN, icon="close")
            return
        self._submit(agent.answer(request))

    def dev_stats(self) -> Sequence[tuple[str, str]]:
        art = art_library().stats()
        state = self.engine.state
        return (
            ("fps", f"{self.fps:.1f}"),
            ("animations live", str(self.fx.count())),
            ("card surfaces cached", str(cache_size())),
            ("art found / invented", f"{art['resolved']} / {art['placeholders']}"),
            ("art surfaces cached", str(art["cached_surfaces"])),
            ("cards in registry", str(len(self.registry.cards))),
            ("content hash", self.registry.content_hash[:16]),
            ("seat in focus", f"{self.seat} ({self.view.you.name})" if self.view else "-"),
            ("turn / phase", f"{state.turn_number} / {state.phase}"),
            ("decisions answered", str(getattr(self.presenter, "decisions_made", 0))),
            ("log entries", str(len(self.log.entries))),
            ("rolls seen", str(len(self.rolls))),
            ("human seats", ", ".join(sorted(self.presenter.human_seats or {"all"}))),
            ("sound", "on" if self.sound.enabled and self.sound.available else "off"),
        )

    def dev_layout_rects(self) -> Mapping[str, tuple[int, int, int, int]]:
        return self.layout.as_dict()


# ---------------------------------------------------------------------------
# Dev FX catalogue
# ---------------------------------------------------------------------------


def _fx_card_flight(scene: GameScene) -> None:
    card = next(iter(scene.registry.cards.values()), None)
    width = scene.layout.hand_card_w
    scene.fx.add(CardMoveAnimation(
        card, scene._corner(scene.layout.deck_area_rect.center, width),
        scene._corner(scene.layout.party_rect.center, width),
        scene.layout.card_box(width), 0.6, flip=True, face_down=True,
    ))


def _fx_dice(scene: GameScene) -> None:
    scene.fx.add(DiceRollAnimation((4, 5), pygame.Rect(scene.dice.dice_area), 1.0, total=9))


def _fx_slay(scene: GameScene) -> None:
    at = scene.layout.monster_row_rect.center
    scene.fx.shake(12.0)
    scene.fx.add(RingBurstAnimation(at, C.BLOOD, 0.9, radius=170, rings=4))
    scene.fx.add(ParticleBurstAnimation(at, (C.BLOOD, C.EMBER, C.GOLD), 0.85, count=18))
    scene.fx.add(BannerAnimation("SLAIN", "a Monster falls", colour=C.BLOOD, icon="skull"))


def _fx_challenge(scene: GameScene) -> None:
    scene.fx.add(BannerAnimation("CHALLENGED", "both sides roll", colour=C.EMBER,
                                 icon="challenge"))
    scene.fx.add(FlashAnimation(scene.layout.hand_rect, C.EMBER, 0.6))
    scene.fx.add(RunePulseAnimation(scene.layout.left_rail_rect.center, C.EMBER, 1.1, radius=110))
    scene.fx.add(EmberRainAnimation(
        (scene.layout.width, scene.layout.height), 1.2, count=14,
        origin=scene.layout.left_rail_rect.center,
    ))


def _fx_modifier(scene: GameScene) -> None:
    area = pygame.Rect(scene.dice.dice_area)
    scene.fx.add(ModifierPopAnimation("+2", area.center, C.GOOD, 1.1))
    scene.fx.add(ModifierPopAnimation("-3", (area.centerx + 40, area.centery), C.BAD, 1.1))


def _fx_confetti(scene: GameScene) -> None:
    scene.fx.add(ConfettiAnimation((scene.layout.width, scene.layout.height), 4.5))


def _fx_banner(scene: GameScene) -> None:
    scene.fx.add(BannerAnimation(
        L.YOUR_MOVE, L.ap_label(3), colour=C.GOLD, icon="leader",
    ))


def _fx_trail(scene: GameScene) -> None:
    scene.fx.add(TrailAnimation(
        scene.layout.right_rail_rect.center, scene.layout.party_rect.center, C.ROSE, 0.8,
    ))


def _fx_deal(scene: GameScene) -> None:
    for i in range(5):
        scene._deal_flight_to_owner(scene.seat, delay=i * 0.09)


def _fx_shake(scene: GameScene) -> None:
    scene.fx.shake(16.0)


def _fx_flash(scene: GameScene) -> None:
    scene.fx.flash(C.GOLD_PALE, 0.35)


def _fx_toast(scene: GameScene) -> None:
    scene.toast.show("Something happened!", colour=C.GOLD, duration=2.4, icon="bolt")


def _fx_spotlight(scene: GameScene) -> None:
    scene.fx.add(SpotlightAnimation(scene.layout.monster_row_rect, 1.4))


def _fx_ring(scene: GameScene) -> None:
    scene.fx.add(RingBurstAnimation(scene.layout.board_rect.center, C.ARCANE, 0.7, radius=140))


def _fx_sparks(scene: GameScene) -> None:
    scene.fx.add(ParticleBurstAnimation(
        scene.layout.party_rect.center, (C.GOLD, C.GOLD_PALE, C.EMBER), 0.75, count=16,
    ))


def _fx_ember_rain(scene: GameScene) -> None:
    scene.fx.add(EmberRainAnimation(
        (scene.layout.width, scene.layout.height), 1.4, count=18,
        origin=scene.layout.monster_row_rect.center,
    ))


def _fx_rune_pulse(scene: GameScene) -> None:
    scene.fx.add(RunePulseAnimation(
        scene.layout.monster_row_rect.center, C.ARCANE, 1.2, radius=130,
    ))


def _fx_handover(scene: GameScene) -> None:
    player = scene.view.players.get(scene.view.active_player)
    scene.overlays.push(HandoverOverlay(
        scene.layout, player.name if player else "Next player",
        seat_colour=T.seat_colour(player.seat) if player else C.GOLD,
        turn=scene.view.turn_number,
    ))


def _fx_game_over(scene: GameScene) -> None:
    rows = []
    for i, player in enumerate(scene.view.players.values()):
        slain = player.zone("slain")
        rows.append(ScoreRow(
            player.name, f"{len(slain.cards) if slain else 0} slain",
            colour=T.seat_colour(player.seat), winner=i == 0,
            classes=("bard", "fighter") if i == 0 else (),
        ))
    scene.overlays.push(GameOverOverlay(
        scene.layout, f"{rows[0].name} wins" if rows else "Nobody wins", rows,
        reason="a developer said so", turns=scene.view.turn_number,
    ))


#: name -> what it does. The dev console's FX tab is generated from this, so a
#: new animation becomes testable by adding one entry.
_DEV_FX: dict[str, Callable[[GameScene], None]] = {
    "card_flight": _fx_card_flight,
    "deal_hand": _fx_deal,
    "dice_roll": _fx_dice,
    "modifier_pop": _fx_modifier,
    "slay_monster": _fx_slay,
    "challenge": _fx_challenge,
    "ring_burst": _fx_ring,
    "sparks": _fx_sparks,
    "ember_rain": _fx_ember_rain,
    "rune_pulse": _fx_rune_pulse,
    "trail": _fx_trail,
    "banner": _fx_banner,
    "confetti": _fx_confetti,
    "screen_shake": _fx_shake,
    "screen_flash": _fx_flash,
    "spotlight": _fx_spotlight,
    "toast": _fx_toast,
    "handover_screen": _fx_handover,
    "game_over_screen": _fx_game_over,
}


__all__ = ["ROLL_ACTIONS", "ActionMenu", "GameScene", "PickTray", "SceneHooks"]
