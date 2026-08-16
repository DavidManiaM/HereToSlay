"""``render.py`` — ``rich``-based board renderer for the CLI.

Everything here is a *pure transformation*: ``GameView`` in, ``rich``
renderables out. No input, no engine calls, no side effects.

The renderer is the CLI's read path. The presenter (``ui/cli/presenter.py``)
is the write path — it calls ``render_board`` before every prompt so the
player always sees an up-to-date board.

Layout (top → bottom)
---------------------
1. Header bar  — turn / phase / whose turn / content hash tail
2. Monster row — name, requirement text if any, roll bands summary
3. Shared decks — main deck size, discard top, monster deck size
4. Separator
5. Player panels (you first, then opponents in seat order):
      • leader name | AP | hand count
      • party: heroes + attached items, tapped flag
      • slain pile count
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from here_to_slay.core.view import CardView, GameView, PlayerView, ZoneView

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------

_CLASS_COLOUR: dict[str, str] = {
    "bard": "magenta",
    "fighter": "red",
    "guardian": "blue",
    "ranger": "green",
    "thief": "yellow",
    "wizard": "cyan",
}
_KIND_COLOUR: dict[str, str] = {
    "hero": "bright_white",
    "monster": "red",
    "item": "bright_yellow",
    "magic": "bright_cyan",
    "modifier": "bright_green",
    "challenge": "bright_red",
    "party_leader": "bright_magenta",
}

_AP_STYLE = "bold bright_cyan"
_INACTIVE_STYLE = "dim"
_ACTIVE_STYLE = "bold"


def _icons() -> dict[str, str]:
    """Pick an icon set the console can actually encode.

    A legacy Windows console runs a non-UTF-8 code page (cp1250 here), where
    printing an emoji raises ``UnicodeEncodeError`` and takes the whole game
    down mid-board. Probing the encoding once and falling back to ASCII keeps
    the CLI playable everywhere, which matters because the CLI is the primary
    way a new card gets tested.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    rich = {
        "tapped": " ⟳",
        "slain": "💀",
        "hero": "⚔ ",
        "item": "  ↳ ",
        "monster": "👾 ",
        "roll": "🎲",
        "trophy": "🏆",
        "sep": "•",
        "bullet": "•",
        "active": "▶",
        "arrow": "→",
        "rule": "─",
    }
    try:
        "".join(rich.values()).encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return {
            "tapped": " (used)",
            "slain": "[x]",
            "hero": "+ ",
            "item": "  -> ",
            "monster": "M ",
            "roll": "#",
            "trophy": "*",
            "sep": "|",
            "bullet": "-",
            "active": ">",
            "arrow": "->",
            "rule": "-",
        }
    return rich


ICONS = _icons()

_TAPPED_ICON = ICONS["tapped"]
_SLAIN_ICON = ICONS["slain"]
_HERO_ICON = ICONS["hero"]
_ITEM_ICON = ICONS["item"]
_MONSTER_ICON = ICONS["monster"]
_ROLL_ICON = ICONS["roll"]
_TROPHY_ICON = ICONS["trophy"]
_SEP = ICONS["sep"]
_BULLET = ICONS["bullet"]
_ACTIVE_ICON = ICONS["active"]
_ARROW = ICONS["arrow"]
_RULE_CHAR = ICONS["rule"]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def render_board(view: GameView, registry: Any = None) -> Group:  # type: ignore[type-arg]
    """The full board as a ``rich.console.Group``, ready to ``console.print``."""
    parts: list[Any] = [
        _render_header(view),
        _render_monster_row(view, registry),
        _render_shared_decks(view),
        Rule(style="dim", characters=_RULE_CHAR),
        *_render_all_players(view, registry),
    ]
    return Group(*parts)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def _render_header(view: GameView) -> Text:
    active_name = _player_name(view, view.active_player)
    header = Text()
    header.append(f" Turn {view.turn_number}", style="bold")
    header.append(f"  {_SEP}  ", style="dim")
    header.append(view.phase, style="italic")
    header.append(f"  {_SEP}  ", style="dim")
    header.append(f"{active_name}'s turn", style="bold bright_yellow")
    if view.winner:
        header.append(
            f"  {_TROPHY_ICON} {_player_name(view, view.winner)} wins!",
            style="bold bright_green",
        )
    header.append(f"  [{view.content_hash[:8]}]", style="dim")
    return header


# ---------------------------------------------------------------------------
# Monster row
# ---------------------------------------------------------------------------


def _render_monster_row(view: GameView, registry: Any = None) -> Panel:
    zone = view.zone("monster_row")
    if zone is None or not zone.cards:
        empty = Text("(no monsters in row)", style="dim italic")
        return Panel(empty, title="[bold red]Monster Row[/bold red]", border_style="red")

    table = Table.grid(padding=(0, 2))
    table.add_column("monster", no_wrap=False)
    for card in zone.cards:
        table.add_row(_render_monster_card(card, registry))

    return Panel(table, title="[bold red]Monster Row[/bold red]", border_style="red")


def _band_text(band: Any) -> str:
    """A band as a player reads it: ``8+``, ``5-7``, ``4-``.

    An open bound and the range separator used to be the same en dash, which
    rendered as ``--12`` and ``2--`` — unreadable in either direction.
    """
    low, high = band.min, band.max
    if low is not None and high is not None:
        return f"{low}-{high}" if low != high else str(low)
    if low is not None:
        return f"{low}+"
    if high is not None:
        return f"{high}-"
    return "any"


def _render_monster_card(card: CardView, registry: Any = None) -> Text:
    t = Text()
    t.append(f"{_MONSTER_ICON}", style="red")
    name = _card_name(card, registry)
    t.append(name, style="bold red")
    # Pull requirement / roll text from registry if available
    if registry is not None:
        defn = registry.get(card.def_id)
        if defn is not None:
            if getattr(defn, "requirement_text", None):
                t.append(f"\n    Req: {defn.requirement_text}", style="dim yellow")
            roll = getattr(defn, "roll", None)
            if roll is not None:
                t.append(f"\n    Roll: {roll.dice}", style="dim")
                for band in getattr(roll, "outcomes", []):
                    t.append(f"\n      {_band_text(band)}: {band.effect.op}", style="dim italic")
    return t


# ---------------------------------------------------------------------------
# Shared decks
# ---------------------------------------------------------------------------


def _render_shared_decks(view: GameView) -> Text:
    parts: list[str] = []

    deck = view.zone("main_deck")
    if deck is not None:
        parts.append(f"Main deck: {deck.size}")

    discard = view.zone("discard")
    if discard is not None:
        if discard.cards:
            top = discard.cards[-1]
            parts.append(f"Discard: {len(discard.cards)} (top: {top.def_id.rsplit('.', 1)[-1]})")
        else:
            parts.append("Discard: empty")

    mdeck = view.zone("monster_deck")
    if mdeck is not None:
        parts.append(f"Monster deck: {mdeck.size}")

    t = Text("  ".join(parts), style="dim")
    return t


# ---------------------------------------------------------------------------
# Player panels
# ---------------------------------------------------------------------------


def _render_all_players(view: GameView, registry: Any = None) -> list[Panel]:
    # You first, then opponents in seat order
    order = [view.seat, *[p.id for p in view.opponents()]]
    return [_render_player(view, view.players[pid], registry) for pid in order if pid in view.players]


def _render_player(view: GameView, player: PlayerView, registry: Any = None) -> Panel:
    is_active = player.is_active
    is_you = player.is_you

    title_text = Text()
    if is_you:
        title_text.append(f"{_ACTIVE_ICON} ", style="bright_yellow")
    title_text.append(player.name, style=_ACTIVE_STYLE if is_active else _INACTIVE_STYLE)
    if is_you:
        title_text.append(" (you)", style="dim")
    title_text.append(f"  AP: {player.action_points}", style=_AP_STYLE)

    content = Table.grid(padding=(0, 1))
    content.add_column("info", no_wrap=False)

    # Leader
    leader_zone = player.zone("leader")
    if leader_zone and leader_zone.cards:
        card = leader_zone.cards[0]
        leader_text = Text()
        leader_text.append("Leader: ", style="dim")
        leader_text.append(_card_name(card, registry), style="bold bright_magenta")
        content.add_row(leader_text)

    # Party (Heroes + attached Items)
    party_zone = player.zone("party")
    if party_zone and party_zone.cards:
        content.add_row(_render_party(party_zone, registry))

    # Hand
    hand_zone = player.zone("hand")
    if hand_zone is not None:
        hand_text = _render_hand(hand_zone, is_you, registry)
        content.add_row(hand_text)

    # Slain
    slain_zone = player.zone("slain")
    if slain_zone is not None:
        slain_text = Text()
        slain_text.append(f"{_SLAIN_ICON} Slain: {slain_zone.size}", style="dim")
        content.add_row(slain_text)

    border = "bright_yellow" if is_active else ("cyan" if is_you else "dim")
    return Panel(content, title=title_text, border_style=border)


def _render_party(zone: ZoneView, registry: Any = None) -> Text:
    t = Text()
    t.append("Party:\n", style="dim")
    attachment_ids = set()
    # Collect all attached cards
    for card in zone.cards:
        for att_id in card.attachments:
            attachment_ids.add(att_id)

    for card in zone.cards:
        if card.id in attachment_ids:
            continue  # printed under parent
        name = _card_name(card, registry)
        colour = _card_colour(card, registry)
        t.append(f"  {_HERO_ICON}", style=colour)
        t.append(name, style=f"bold {colour}")
        if card.tapped:
            t.append(_TAPPED_ICON, style="dim")
        t.append("\n")
        # Attached items
        for att_id in card.attachments:
            att = next((c for c in zone.cards if c.id == att_id), None)
            if att is not None:
                att_name = _card_name(att, registry)
                t.append(f"{_ITEM_ICON}", style="yellow")
                t.append(att_name, style="yellow")
                t.append("\n")
    return t


def _render_hand(zone: ZoneView, is_you: bool, registry: Any = None) -> Text:
    t = Text()
    if not is_you:
        t.append(f"Hand: {zone.size} card(s)", style="dim")
        return t

    if not zone.cards:
        t.append("Hand: (empty)", style="dim")
        return t

    t.append("Hand:\n", style="dim")
    for card in zone.cards:
        name = _card_name(card, registry)
        colour = _card_colour(card, registry)
        t.append(f"  {_BULLET} ", style="dim")
        t.append(name, style=colour)
        t.append("\n")
    return t


# ---------------------------------------------------------------------------
# Roll display
# ---------------------------------------------------------------------------


def render_roll(roll: Any) -> Text:
    """Pretty-print a completed roll: dice → modifiers → total → band hit.

    Called by the presenter after a ``roll.resolved`` event, so the player
    can see exactly how the number was reached before the outcome runs.
    """
    t = Text()
    t.append(f"  {_ROLL_ICON} Roll", style="bold")
    if roll.kind and roll.kind != "generic":
        t.append(f" ({roll.kind})", style="dim")
    t.append(": ", style="dim")
    t.append(roll.describe(), style="bright_white")
    t.append(f"  → total {roll.total}", style="bold bright_cyan")
    return t


def render_roll_result(roll: Any, band_label: str = "") -> Text:
    """One-line summary shown after the outcome band runs."""
    t = Text()
    dice_str = "+".join(str(d) for d in roll.raw) or "–"
    t.append(f"  {_ROLL_ICON} {roll.dice}: ", style="dim")
    t.append(f"[{dice_str}]", style="bold")
    if roll.modifiers:
        for mod in roll.modifiers:
            sign = "+" if mod.amount >= 0 else ""
            source = mod.label or mod.source or "?"
            t.append(f" {sign}{mod.amount}", style="bright_green" if mod.amount >= 0 else "bright_red")
            t.append(f"({source})", style="dim")
    t.append(f" = {roll.total}", style="bold bright_cyan")
    if band_label:
        t.append(f"  → {band_label}", style="italic yellow")
    return t


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _player_name(view: GameView, pid: Any) -> str:
    p = view.players.get(pid)
    return p.name if p else str(pid)


def _card_name(card: CardView, registry: Any = None) -> str:
    if registry is not None:
        defn = registry.get(card.def_id)
        if defn is not None:
            return defn.name
    # Fallback: prettify the slug portion of the def_id
    return card.def_id.rsplit(".", 1)[-1].replace("_", " ").title()


def _card_colour(card: CardView, registry: Any = None) -> str:
    if registry is not None:
        defn = registry.get(card.def_id)
        if defn is not None:
            card_class = getattr(defn, "card_class", None)
            if card_class and card_class in _CLASS_COLOUR:
                return _CLASS_COLOUR[card_class]
            kind = getattr(defn, "kind", "")
            return _KIND_COLOUR.get(kind, "white")
    return "white"


__all__ = ["render_board", "render_roll", "render_roll_result"]
