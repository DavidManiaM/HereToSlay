"""User preferences, persisted between runs.

Deliberately **not** part of the game. Nothing here can change what a card
does, who wins, or what a seed deals — the whole file is presentation and
comfort, which is why it lives in ``ui/`` and why no part of ``core/`` may read
it. ``Game = f(content_hash, seed, max_turns, decisions)`` still holds with this
file deleted, corrupt, or set to anything at all.

Two rules the implementation follows:

* **A bad settings file never stops the game.** Missing, unreadable, truncated
  or holding a string where a float belongs: every one of those loads the
  defaults and carries on. A card game that refuses to start because its
  preferences JSON is malformed is a worse card game.
* **The command line wins.** ``--no-sound`` means no sound even if the stored
  setting says otherwise, because a flag is the more recent instruction. The
  client applies the file first and the flags after
  (:func:`~here_to_slay.ui.pygame.app.launch`).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any

SETTINGS_FORMAT = 1

#: Override for tests and for anyone who keeps a portable install.
ENV_DIR = "HTS_CONFIG_DIR"
FILE_NAME = "settings.json"


def config_dir() -> Path:
    """Where preferences live. ``$HTS_CONFIG_DIR`` wins if it is set."""
    override = os.environ.get(ENV_DIR)
    if override:
        return Path(override)
    return Path.home() / ".here_to_slay"


def settings_path() -> Path:
    return config_dir() / FILE_NAME


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the settings screen can change. All of it is cosmetic."""

    sound: bool = True
    #: master cue volume, 0.0 to 1.0
    volume: float = 0.55
    animations: bool = True
    #: screen shake, separately, because it is the effect people turn off first
    shake: bool = True
    #: the 10-second auto-pass countdown on reaction windows
    reaction_timer: bool = True
    #: how long an AI seat "thinks" before its move lands, in seconds
    ai_delay: float = 0.55
    #: HUD scale multiplier; below 1.0 shrinks the chrome so the board grows
    ui_scale: float = 1.0
    fullscreen: bool = False
    window_width: int = 1920
    window_height: int = 1080

    # -- validation --------------------------------------------------------

    def normalised(self) -> Settings:
        """The same settings with every number pulled back into range.

        Applied on load *and* on save, so a hand-edited file with
        ``"volume": 40`` is quietly clamped rather than blowing an eardrum.
        """
        return replace(
            self,
            volume=round(_clamp(float(self.volume), 0.0, 1.0), 3),
            ai_delay=round(_clamp(float(self.ai_delay), 0.0, 3.0), 3),
            ui_scale=round(_clamp(float(self.ui_scale), 0.6, 2.0), 3),
            window_width=int(_clamp(int(self.window_width), 640, 8192)),
            window_height=int(_clamp(int(self.window_height), 480, 8192)),
        )

    # -- serialisation -----------------------------------------------------

    def as_data(self) -> dict[str, Any]:
        data = asdict(self.normalised())
        data["format"] = SETTINGS_FORMAT
        return data

    @classmethod
    def from_data(cls, data: Any) -> Settings:
        """Build from whatever was in the file, field by field.

        Per-field rather than ``cls(**data)`` on purpose: one unknown key or one
        wrong type would otherwise throw away every *good* setting next to it,
        and a settings file that resets itself is worse than one that ignores a
        line.
        """
        if not isinstance(data, dict):
            return cls()
        known = {field.name: field.type for field in fields(cls)}
        values: dict[str, Any] = {}
        for name in known:
            if name not in data:
                continue
            raw = data[name]
            current = getattr(cls(), name)
            try:
                if isinstance(current, bool):
                    values[name] = bool(raw)
                elif isinstance(current, int):
                    values[name] = int(raw)
                elif isinstance(current, float):
                    values[name] = float(raw)
                else:  # pragma: no cover - no string settings today
                    values[name] = type(current)(raw)
            except (TypeError, ValueError):
                continue
        return cls(**values).normalised()

    # -- files -------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str | None = None) -> Settings:
        """Read preferences. Any failure at all yields the defaults."""
        target = Path(path) if path is not None else settings_path()
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return cls()
        try:
            return cls.from_data(json.loads(text))
        except ValueError:
            return cls()

    def save(self, path: Path | str | None = None) -> Path | None:
        """Write preferences, returning the path — or ``None`` if it failed.

        A read-only home directory is a reason not to remember the volume, not
        a reason to interrupt a game, so this reports rather than raises.
        """
        target = Path(path) if path is not None else settings_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(self.as_data(), indent=2, sort_keys=True), encoding="utf-8"
            )
        except OSError:
            return None
        return target

    # -- editing -----------------------------------------------------------

    def with_change(self, name: str, value: Any) -> Settings:
        """A copy with one field changed, clamped. Unknown names are ignored."""
        if name not in {field.name for field in fields(self)}:
            return self
        return replace(self, **{name: value}).normalised()

    def toggled(self, name: str) -> Settings:
        current = getattr(self, name, None)
        if not isinstance(current, bool):
            return self
        return self.with_change(name, not current)


__all__ = ["ENV_DIR", "FILE_NAME", "SETTINGS_FORMAT", "Settings", "config_dir", "settings_path"]
