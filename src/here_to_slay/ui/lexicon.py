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
EFFECTS_YOURS = "efectele tale"
EFFECTS_THEIRS = "efectele lui {name}"
EFFECT_PASSIVE = "Pasiv"
EFFECT_ABILITY = "Abilitate"
EFFECT_CHEAT = "Cheat"
EFFECT_HACK = "Hack"
EFFECT_FLAG = "Stare"
EFFECT_LEADER = "Șef"
EFFECT_TABLE = "Masă"
FLAG_UNCONTESTABLE_TITLE = "De necontestat"
FLAG_UNCONTESTABLE_DETAIL = "Cărțile jucate în această tură nu pot fi provocare"
FLAG_EXTRA_TURN_TITLE = "Tură extra"
FLAG_EXTRA_TURN_DETAIL = "Joacă încă o tură după aceasta"
FLAG_GENERIC = "stare = {value}"
FLAG_TABLE_GENERIC = "stare de masă = {value}"
RECENT = "Jurnal"
JOURNAL = "Jurnal"
PRESS_ROLL = 'Apasă np.random("") ca să rulezi'
FREE = "gratis"
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
EMPTY = "gol"
NOTHING_ACTIVE = "nimic activ"
THINKING = "{name} se gândește…"
YOUR_MOVE = "Mutarea ta"
CHOOSE_PLAYER = "Alege un jucător"
CHOOSE = "Alege"
CONFIRM = "Confirmi?"
WAIT = "Așteaptă…"
PASS = "Treci"
NO_LEGAL = "Nicio acțiune {label} legală acum"
PICK_TARGET = "Alege o țintă ({n} opțiuni)"
YOU = "Tu"
PLAYER_N = "Jucător {n}"
EQUIPPED_TO = "Echipat pe {name}"
ABILITY_READY = "Abilitate gata"
ABILITY_READY_ROLL = "Abilitate gata · {n}+ pentru succes"
ABILITY_USED = "Abilitate folosită în această tură"
PASSIVE_ABILITY = "Abilitate pasivă"
MORE_SCROLL = "+{n} în plus · derulează"
RIGHT_CLICK_PIN = "click-dreapta ca să fixezi"
CAM_HINT = "Q / E camere  ·  click pe un grup inamic"
SPECTATOR = "SPECTATOR · TOATE MÂINILE VIZIBILE"

# -- pause menu, settings, saves ---------------------------------------------
#
# The rows of the pause menu were literals in ``scenes.py`` until Phase 11 added
# three more. They are strings a player reads, so they belong here with the rest
# of the surface language - which is also what makes translating the menu one
# file's work instead of a grep through the board scene.

MENU_RESUME = "Resume"
MENU_RULES = "How to play"
MENU_LOG = "Game log"
MENU_SETTINGS = "Settings"
MENU_SAVE = "Save game"
MENU_LOAD = "Load game"
MENU_SOUND = "Sound"
MENU_ANIMATIONS = "Animations"
MENU_FULLSCREEN = "Fullscreen"
MENU_CONSOLE = "Developer console"
MENU_RESTART = "New game"
MENU_QUIT = "Quit"
MENU_RESUME_HINT = "Esc to resume"
MENU_SAME_PLAYERS = "same players"

SETTINGS_TITLE = "Settings"
SETTINGS_HINT = "saved to your profile"
SET_SOUND = "Sound"
SET_VOLUME = "Volume"
SET_ANIMATIONS = "Animations"
SET_SHAKE = "Screen shake"
SET_REACTION_TIMER = "Reaction countdown"
SET_AI_SPEED = "AI pace"
SET_UI_SCALE = "HUD scale"
SET_FULLSCREEN = "Fullscreen"
ON = "on"
OFF = "off"

SAVES_TITLE = "Load game"
SAVES_EMPTY = "No saved games yet"
SAVED_TO = "Saved -> {name}"
SAVE_REFUSED = "Cannot save mid-action - try again in a moment"
SAVE_FAILED = "Could not save: {why}"
LOAD_FAILED = "Could not load: {why}"
LOADED = "Loaded {name}"


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
    """Dice control caption: idle quotes empty, resolved quotes hold 2-12."""
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
    # Longer phrases first so "Hero of any class" is not mangled by "Hero".
    replacements = (
        ("is thinking…", "se gândește…"),
        ("is thinking...", "se gândește…"),
        ("Your move", "Mutarea ta"),
        ("your move", "Mutarea ta"),
        ("nothing active", "nimic activ"),
        ("Choose a player", "Alege un jucător"),
        ("Pick a target", "Alege o țintă"),
        ("Equipped to", "Echipat pe"),
        ("Ability ready", "Abilitate gata"),
        ("Ability already used this turn", "Abilitate folosită în această tură"),
        ("Passive ability", "Abilitate pasivă"),
        ("Heroes of any class", "Persoane de orice clasă"),
        ("Hero of any class", "Persoană de orice clasă"),
        ("of any class", "de orice clasă"),
        ("action points", "prompt-uri"),
        ("Action points", "Prompt-uri"),
        ("action point", "prompt"),
        ("Action point", "Prompt"),
        ("Action Point", "Prompt"),
        ("(1 AP)", "(1 prompt)"),
        ("(2 AP)", "(2 prompt-uri)"),
        ("(3 AP)", "(3 prompt-uri)"),
        ("1 AP", "1 prompt"),
        ("2 AP", "2 prompt-uri"),
        ("3 AP", "3 prompt-uri"),
        ("Party Leader", "Șeful grupului"),
        ("party leader", "șeful grupului"),
        (" and ", " și "),
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
        ("modifier", "download/upload speed"),
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
        ("rolls 2d6", "np.random 2d6"),
        ("roll", "np.random"),
        ("Roll", "np.random"),
        ("hand", "răspunsuri AI"),
        ("Hand", "Răspunsuri AI"),
        ("Player", "Jucător"),
        ("player", "jucător"),
        ("cancelled", "anulat"),
        ("Cancelled", "Anulat"),
        ("was cancelled", "a fost anulat"),
    )
    out = text
    for old, new in replacements:
        out = out.replace(old, new)
    return out


def requirement_label(text: str) -> str:
    """Monster party requirement, always Romanian."""
    return retheme_prompt(text or "")


__all__ = [
    "ABILITY_READY",
    "ABILITY_READY_ROLL",
    "ABILITY_USED",
    "AP",
    "ATTACK",
    "BESTIES",
    "BESTIES_ROW",
    "CAM_HINT",
    "CAN_BEFRIEND",
    "CHEAT",
    "CHEATS",
    "CHOOSE",
    "CHOOSE_PLAYER",
    "CLASS_LABEL",
    "CONFIRM",
    "DECK_DISCARD",
    "DECK_DRAW",
    "DECK_MONSTER",
    "DICE",
    "EFFECTS",
    "EFFECTS_THEIRS",
    "EFFECTS_YOURS",
    "EFFECT_ABILITY",
    "EFFECT_CHEAT",
    "EFFECT_FLAG",
    "EFFECT_HACK",
    "EFFECT_LEADER",
    "EFFECT_PASSIVE",
    "EFFECT_TABLE",
    "EMPTY",
    "EMPTY_HAND",
    "EQUIPPED_TO",
    "FLAG_EXTRA_TURN_DETAIL",
    "FLAG_EXTRA_TURN_TITLE",
    "FLAG_GENERIC",
    "FLAG_TABLE_GENERIC",
    "FLAG_UNCONTESTABLE_DETAIL",
    "FLAG_UNCONTESTABLE_TITLE",
    "FREE",
    "GAME_LOG",
    "HACK",
    "HACKS",
    "HAND",
    "HOW_TO_PLAY",
    "IN_PLAY",
    "JOURNAL",
    "KIND_LABEL",
    "LEADER",
    "MORE_SCROLL",
    "NOTHING_ACTIVE",
    "NO_LEGAL",
    "NO_PARTY",
    "PARTY",
    "PASS",
    "PASSIVE_ABILITY",
    "PASS_DEVICE",
    "PAUSED",
    "PICK_TARGET",
    "PLAYER_N",
    "PRESS_ROLL",
    "READY",
    "RECENT",
    "RIGHT_CLICK_PIN",
    "ROLL_READY",
    "SLAY",
    "SLAYN",
    "SPECTATOR",
    "TABLE",
    "THINKING",
    "TURN",
    "VICTORY",
    "WAIT",
    "YOU",
    "YOUR_MOVE",
    "ap_label",
    "card_name",
    "card_text",
    "class_label",
    "hand_label",
    "kind_label",
    "np_random_label",
    "requirement_label",
    "retheme_prompt",
    "type_line",
]
