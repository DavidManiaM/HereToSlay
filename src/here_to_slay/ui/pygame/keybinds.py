"""Default action hotkeys for the pygame client.

Variants may override per-action via ``ActionDef.hotkey`` in rules YAML.
"""

from __future__ import annotations

from typing import Any

#: action id -> single-letter key (lowercase).
ACTION_KEYS: dict[str, str] = {
    "draw": "d",
    "play_hero": "h",
    "use_hero_ability": "a",
    "use_leader_ability": "s",
    "attack_monster": "f",
    "equip_item": "g",
    "cast_magic": "c",
    "discard_and_draw": "b",
}

#: Display costs shown on the action bar (what the player actually pays).
DISPLAY_COSTS: dict[str, int | str] = {
    "draw": 1,
    "play_hero": 1,
    "use_hero_ability": "1*",  # *free if entered this turn
    "use_leader_ability": 0,
    "attack_monster": 2,
    "equip_item": 1,
    "cast_magic": 1,
    "discard_and_draw": 3,
}


def hotkey_for(action_id: str, action_def: Any | None = None) -> str | None:
    """Resolve the hotkey for an action, preferring YAML overrides."""
    if action_def is not None:
        override = getattr(action_def, "hotkey", None)
        if override:
            return str(override).lower()
    return ACTION_KEYS.get(action_id)


def key_to_action(key_char: str) -> str | None:
    """Map a pressed letter to an action id, if any."""
    ch = key_char.lower()
    for action_id, letter in ACTION_KEYS.items():
        if letter == ch:
            return action_id
    return None


__all__ = ["ACTION_KEYS", "DISPLAY_COSTS", "hotkey_for", "key_to_action"]
