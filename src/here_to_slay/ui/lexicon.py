"""Romanian tech surface language for Here to Slay.

Mechanical ids stay English (``hero``, ``monster``, ``action_points``, …).
Everything a player reads — kind labels, chrome, verbs — goes through here.
"""

from __future__ import annotations

from typing import Any

# -- kind labels -------------------------------------------------------------

KIND_LABEL: dict[str, str] = {
    "hero": "Persoană",
    "monster": "Bestie",
    "item": "Cheat",  # blessing / own-side unelte; cursed → Hack via tags
    "magic": "Script",
    "modifier": "Download/Upload Speed",
    "challenge": "Provocare",
    "party_leader": "Șeful grupului",
}

KIND_LABEL_PLURAL: dict[str, str] = {
    "hero": "Persoane",
    "monster": "Besties",
    "item": "Cheats",
    "magic": "Scripts",
    "modifier": "Download/Upload Speed",
    "challenge": "Provocări",
    "party_leader": "Șefi de grup",
}

#: Items tagged ``cursed`` (equipped on an opponent's persoane) — unelte blestemate.
HACK = "Hack"
HACKS = "Hacks"
CHEAT = "Cheat"
CHEATS = "Cheats"

CLASS_LABEL: dict[str, str] = {
    "bard": "Bard",
    "fighter": "Fighter",
    "guardian": "Guardian",
    "ranger": "Ranger",
    "thief": "Thief",
    "wizard": "Wizard",
}

# -- chrome ------------------------------------------------------------------

AP = "prompts"
AP_ONE = "prompt"
HAND = "răspunsuri AI"
HAND_ONE = "răspuns AI"
PARTY = "grupul tău"
LEADER = "șeful grupului"
BESTIES = "besties"
BESTIES_ROW = "besties · împrietenește-te"
DECK_DRAW = "inbox"
DECK_DISCARD = "trash"
DECK_MONSTER = "besties"
IN_PLAY = "în execuție"
DICE = "np.random"
ACTION_POINTS_YOURS = "prompturile tale"
ACTION_POINTS_THEIRS = "prompturile lui {name}"
CAN_BEFRIEND = "POȚI ÎMPRIETENI"
EMPTY_HAND = "niciun răspuns AI"
NO_PARTY = "nicio persoană în grup"
TABLE = "masa"
EFFECTS = "efecte active"
RECENT = "RECENT"
TURN = "Tura"
PASS_DEVICE = "Pasează dispozitivul către"
READY = "Sunt gata"
VICTORY = "Victorie"
PAUSED = "Pauză"
HOW_TO_PLAY = "Cum se joacă"
GAME_LOG = "Jurnal"
ROLL_READY = 'np.random("")'
ATTACK = "împrietenește"
SLAY = "împrietenește"
SLAYN = "besties împrietenite"


def kind_label(
    kind: str,
    *,
    plural: bool = False,
    tags: Any = (),
) -> str:
    """Display label for a card kind.

    Cursed items (``tags`` containing ``cursed``) are **Hacks** — unelte
    blestemate placed on another player's persoane. Challenges stay
    **Provocare**.
    """
    tag_set = {str(t).lower() for t in (tags or ())}
    if kind == "item" and "cursed" in tag_set:
        return HACKS if plural else HACK
    table = KIND_LABEL_PLURAL if plural else KIND_LABEL
    return table.get(kind, kind.replace("_", " ").title())


def class_label(card_class: str | None) -> str:
    if not card_class:
        return ""
    return CLASS_LABEL.get(card_class, card_class.title())


def type_line(
    kind: str,
    card_class: str | None = None,
    *,
    tags: Any = (),
) -> str:
    """Footer / detail type line on a card face."""
    label = kind_label(kind, tags=tags)
    if kind == "party_leader":
        return label
    cls = class_label(card_class)
    if cls and kind in ("hero", "party_leader"):
        return f"{cls} · {label}"
    return label


def ap_label(n: int) -> str:
    return f"{n} {AP_ONE if n == 1 else AP}"


def hand_label(n: int) -> str:
    return f"{n} {HAND_ONE if n == 1 else HAND}"


def np_random_label(total: int | None = None) -> str:
    """Dice control caption: idle quotes empty, resolved quotes hold 2–12."""
    if total is None:
        return ROLL_READY
    return f'np.random("{int(total)}")'


def card_name(card_def: Any) -> str:
    """Display name; optional ``display_name`` attr, else definition name."""
    override = getattr(card_def, "display_name", None)
    if override:
        return str(override)
    return str(getattr(card_def, "name", None) or getattr(card_def, "id", "card"))


def card_text(card_def: Any) -> str:
    override = getattr(card_def, "display_text", None)
    if override:
        return str(override)
    return str(getattr(card_def, "text", None) or "")


def retheme_prompt(text: str) -> str:
    """Light English→RO tech substitutions for leftover engine prompts."""
    if not text:
        return text
    replacements = (
        ("action point", "prompt"),
        ("Action point", "Prompt"),
        ("Action Point", "Prompt"),
        ("Hero", "Persoană"),
        ("hero", "persoană"),
        ("Monster", "Bestie"),
        ("monster", "bestie"),
        ("Leader", "Șeful grupului"),
        ("leader", "șeful grupului"),
        ("Challenge", "Provocare"),
        ("challenge", "provocare"),
        ("Cursed item", "Hack"),
        ("cursed item", "hack"),
        ("Cursed Item", "Hack"),
        ("Modifier", "download/upload speed"),
        ("Modifier", "Download/Upload Speed"),
        ("Magic", "Script"),
        ("magic", "script"),
        ("Item", "Cheat"),
        ("item", "cheat"),
        ("attack", "împrietenește"),
        ("Attack", "Împrietenește"),
        ("slay", "împrietenește"),
        ("Slay", "Împrietenește"),
        ("destroy", "șterge"),
        ("Destroy", "Șterge"),
        ("roll", "np.random"),
        ("Roll", "np.random"),
        ("hand", "răspunsuri AI"),
        ("Hand", "Răspunsuri AI"),
    )
    out = text
    for old, new in replacements:
        out = out.replace(old, new)
    return out


__all__ = [
    "AP",
    "ATTACK",
    "BESTIES",
    "BESTIES_ROW",
    "CAN_BEFRIEND",
    "CHEAT",
    "CHEATS",
    "CLASS_LABEL",
    "DECK_DISCARD",
    "DECK_DRAW",
    "DECK_MONSTER",
    "DICE",
    "EFFECTS",
    "EMPTY_HAND",
    "GAME_LOG",
    "HACK",
    "HACKS",
    "HAND",
    "HOW_TO_PLAY",
    "IN_PLAY",
    "KIND_LABEL",
    "LEADER",
    "NO_PARTY",
    "PARTY",
    "PASS_DEVICE",
    "PAUSED",
    "READY",
    "RECENT",
    "ROLL_READY",
    "SLAY",
    "SLAYN",
    "TABLE",
    "TURN",
    "VICTORY",
    "ap_label",
    "card_name",
    "card_text",
    "class_label",
    "hand_label",
    "kind_label",
    "np_random_label",
    "retheme_prompt",
    "type_line",
]
