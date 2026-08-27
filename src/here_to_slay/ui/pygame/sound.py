"""Sound effects, synthesised at startup. No audio files, no numpy.

The repository ships no audio, and adding binary assets for a card game's
clicks would be a poor trade. So the handful of cues the client needs are
generated: a short envelope over a waveform, written into a ``bytearray`` and
handed to ``pygame.mixer.Sound(buffer=...)``. Sixteen-bit little-endian stereo,
built with :mod:`array` — the same reason ``core/`` avoids heavyweight deps.

Every entry point degrades to silence. If the mixer will not open (a headless
CI box, a machine with no audio device, ``--mute``) :class:`SoundBoard` becomes
a no-op rather than an exception, because a card game that refuses to start
because it cannot beep is a worse card game.

A pack may add cues of its own without shipping audio or Python:
:meth:`SoundBoard.define` builds one from a declarative spec — layers of
waveform, envelope and pitch glide, the same parameters the built-in cues use.
:mod:`~here_to_slay.ui.pygame.cues` is what reads those out of a pack and
decides which cue a given moment plays.
"""

from __future__ import annotations

import array
import contextlib
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pygame

SAMPLE_RATE = 44100
CHANNELS = 2
AMPLITUDE = 12000


# ---------------------------------------------------------------------------
# Waveforms and envelopes
# ---------------------------------------------------------------------------


def _sine(phase: float) -> float:
    return math.sin(phase * math.tau)


def _triangle(phase: float) -> float:
    p = phase % 1.0
    return 4 * abs(p - 0.5) - 1


def _square(phase: float) -> float:
    return 1.0 if (phase % 1.0) < 0.5 else -1.0


def _saw(phase: float) -> float:
    return 2 * (phase % 1.0) - 1


class _Noise:
    """A deterministic LCG. Same noise every run, so nothing is surprising."""

    __slots__ = ("_state",)

    def __init__(self, seed: int = 12345) -> None:
        self._state = seed & 0xFFFFFFFF

    def __call__(self, _phase: float = 0.0) -> float:
        self._state = (1664525 * self._state + 1013904223) & 0xFFFFFFFF
        return (self._state / 0x7FFFFFFF) - 1.0


def _render(
    duration: float,
    frequency: Callable[[float], float] | float,
    *,
    wave: Callable[[float], float] = _sine,
    attack: float = 0.005,
    decay: float = 0.9,
    gain: float = 1.0,
    pan: float = 0.0,
) -> bytes:
    """One voice: a swept-frequency waveform under an AD envelope."""
    frames = max(1, int(SAMPLE_RATE * duration))
    buf = array.array("h", bytes(frames * CHANNELS * 2))
    phase = 0.0
    left = min(1.0, 1.0 - pan)
    right = min(1.0, 1.0 + pan)
    for i in range(frames):
        t = i / frames
        freq = frequency(t) if callable(frequency) else frequency
        phase += freq / SAMPLE_RATE
        # Attack then exponential decay: percussive, and never clicks at the
        # start or end of the buffer.
        if t < attack:
            env = t / attack
        else:
            env = math.exp(-(t - attack) / max(0.01, decay) * 5.0)
        value = wave(phase) * env * gain * AMPLITUDE
        sample = int(max(-32767, min(32767, value)))
        buf[i * CHANNELS] = int(sample * left)
        buf[i * CHANNELS + 1] = int(sample * right)
    return buf.tobytes()


def _mix(*layers: bytes) -> bytes:
    """Sum voices, clipping at the rails."""
    if not layers:
        return b""
    longest = max(len(layer) for layer in layers)
    out = array.array("h", bytes(longest))
    for layer in layers:
        view = array.array("h")
        view.frombytes(layer)
        for i, value in enumerate(view):
            total = out[i] + value
            out[i] = max(-32767, min(32767, total))
    return out.tobytes()


# ---------------------------------------------------------------------------
# The cues
# ---------------------------------------------------------------------------


def _glide(start: float, end: float, curve: float = 1.0) -> Callable[[float], float]:
    return lambda t: start + (end - start) * (t**curve)


def _recipes() -> dict[str, Callable[[], bytes]]:
    """Name -> builder. Built lazily so startup does not pay for unused cues."""
    return {
        # UI
        "click": lambda: _render(0.05, 1500, wave=_triangle, decay=0.06, gain=0.35),
        "hover": lambda: _render(0.03, 2400, wave=_sine, decay=0.04, gain=0.14),
        "open": lambda: _render(0.20, _glide(420, 900), wave=_triangle, decay=0.16, gain=0.3),
        "close": lambda: _render(0.18, _glide(880, 380), wave=_triangle, decay=0.14, gain=0.28),
        "error": lambda: _mix(
            _render(0.22, 190, wave=_square, decay=0.13, gain=0.24),
            _render(0.22, 143, wave=_square, decay=0.13, gain=0.20),
        ),
        # Cards
        "card_deal": lambda: _render(0.13, _glide(1900, 520, 0.5), wave=_Noise(7),
                                     decay=0.08, gain=0.30),
        "card_play": lambda: _mix(
            _render(0.15, _glide(1500, 700), wave=_Noise(11), decay=0.09, gain=0.26),
            _render(0.22, 620, wave=_triangle, decay=0.13, gain=0.20),
        ),
        "card_discard": lambda: _render(0.17, _glide(900, 260, 0.7), wave=_Noise(23),
                                        decay=0.11, gain=0.26),
        # Dice
        "dice_roll": lambda: _mix(*[
            _render(0.30, _glide(1200 + i * 260, 380), wave=_Noise(31 + i * 5),
                    attack=0.002, decay=0.09, gain=0.16, pan=(-0.4 + 0.4 * i))
            for i in range(3)
        ]),
        "dice_land": lambda: _mix(
            _render(0.09, 240, wave=_Noise(41), decay=0.05, gain=0.3),
            _render(0.14, 150, wave=_sine, decay=0.09, gain=0.24),
        ),
        # Outcomes
        "success": lambda: _mix(
            _render(0.42, 784, wave=_triangle, decay=0.24, gain=0.20),
            _render(0.42, 1046, wave=_sine, decay=0.20, gain=0.16),
            _render(0.52, 1568, wave=_sine, attack=0.09, decay=0.24, gain=0.11),
        ),
        "failure": lambda: _mix(
            _render(0.40, _glide(360, 180), wave=_saw, decay=0.24, gain=0.18),
            _render(0.40, _glide(240, 120), wave=_triangle, decay=0.24, gain=0.14),
        ),
        "slay": lambda: _mix(
            _render(0.60, _glide(120, 62), wave=_sine, decay=0.34, gain=0.40),
            _render(0.34, _glide(1600, 300), wave=_Noise(59), decay=0.18, gain=0.24),
            _render(0.60, 392, wave=_triangle, attack=0.06, decay=0.30, gain=0.16),
        ),
        "challenge": lambda: _mix(
            _render(0.34, _glide(900, 1500), wave=_square, decay=0.16, gain=0.18),
            _render(0.34, 300, wave=_saw, decay=0.18, gain=0.16),
        ),
        "modifier": lambda: _render(0.22, _glide(660, 1320), wave=_triangle,
                                    decay=0.14, gain=0.20),
        "turn": lambda: _mix(
            _render(0.34, 523, wave=_sine, decay=0.20, gain=0.18),
            _render(0.40, 784, wave=_sine, attack=0.07, decay=0.22, gain=0.13),
        ),
        "victory": lambda: _mix(*[
            _render(1.10, freq, wave=_triangle, attack=0.02 + i * 0.12,
                    decay=0.55, gain=0.15)
            for i, freq in enumerate((523, 659, 784, 1046))
        ]),
    }


# ---------------------------------------------------------------------------
# Cues declared as data
# ---------------------------------------------------------------------------

#: Waveform names a pack may use. Anything else falls back to a sine, because a
#: typo in a sound file must not be able to stop a game.
WAVES: dict[str, Callable[[float], float]] = {
    "sine": _sine,
    "triangle": _triangle,
    "square": _square,
    "saw": _saw,
}

#: Bounds on what a declared cue may ask for. A pack is content, not code: it
#: should not be able to allocate a minute of audio or blow out the mix.
MAX_DURATION = 3.0
MAX_LAYERS = 6


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _frequency(value: Any) -> Callable[[float], float] | float:
    """``440``, ``[220, 880]`` (a glide), or ``[220, 880, 0.5]`` (curved)."""
    if isinstance(value, (list, tuple)) and value:
        start = _number(value[0], 440.0)
        end = _number(value[1], start) if len(value) > 1 else start
        curve = _number(value[2], 1.0) if len(value) > 2 else 1.0
        return _glide(start, end, max(0.05, curve))
    return max(20.0, _number(value, 440.0))


def _layer_bytes(spec: Mapping[str, Any]) -> bytes:
    wave_name = str(spec.get("wave", "sine")).lower()
    if wave_name == "noise":
        wave: Callable[[float], float] = _Noise(int(_number(spec.get("seed"), 7)))
    else:
        wave = WAVES.get(wave_name, _sine)
    return _render(
        max(0.01, min(MAX_DURATION, _number(spec.get("duration"), 0.2))),
        _frequency(spec.get("frequency", spec.get("freq", 440))),
        wave=wave,
        attack=max(0.0, min(0.5, _number(spec.get("attack"), 0.005))),
        decay=max(0.01, min(2.0, _number(spec.get("decay"), 0.12))),
        gain=max(0.0, min(1.0, _number(spec.get("gain"), 0.25))),
        pan=max(-1.0, min(1.0, _number(spec.get("pan"), 0.0))),
    )


def recipe_from_spec(spec: Mapping[str, Any]) -> Callable[[], bytes]:
    """Turn a declared cue into a builder, without rendering anything yet.

    Lazily, like every built-in cue: a pack that declares eight sounds and plays
    two should synthesise two.
    """
    layers = spec.get("layers")
    parts: list[Mapping[str, Any]]
    if isinstance(layers, (list, tuple)) and layers:
        parts = [layer for layer in layers if isinstance(layer, Mapping)][:MAX_LAYERS]
    else:
        parts = [spec]
    if not parts:
        parts = [{}]
    return lambda: _mix(*[_layer_bytes(part) for part in parts])


# ---------------------------------------------------------------------------
# Board
# ---------------------------------------------------------------------------


class SoundBoard:
    """Owns the mixer and the cue table. Silent if anything goes wrong."""

    def __init__(self, *, enabled: bool = True, volume: float = 0.55) -> None:
        self.enabled = enabled
        self.volume = max(0.0, min(1.0, volume))
        self.available = False
        self._sounds: dict[str, pygame.mixer.Sound] = {}
        self._recipes = _recipes()
        self._failed: set[str] = set()
        if enabled:
            self._open_mixer()

    def _open_mixer(self) -> None:
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=CHANNELS, buffer=512)
            pygame.mixer.set_num_channels(24)
            self.available = pygame.mixer.get_init() is not None
        except pygame.error:
            self.available = False

    # -- playing -----------------------------------------------------------

    def play(self, name: str, *, volume: float = 1.0) -> bool:
        """Play a cue by name, reporting whether it actually made a sound.

        Unknown or broken cues are ignored rather than raised: a missing sound
        must never be the reason a turn cannot be taken. A cue that fails once
        is remembered and not retried.
        """
        if not (self.enabled and self.available) or name in self._failed:
            return False
        sound = self._sounds.get(name)
        if sound is None:
            sound = self._build(name)
            if sound is None:
                self._failed.add(name)
                return False
        try:
            sound.set_volume(self.volume * max(0.0, min(1.0, volume)))
            sound.play()
        except pygame.error:
            self._failed.add(name)
            return False
        return True

    def _build(self, name: str) -> pygame.mixer.Sound | None:
        recipe = self._recipes.get(name)
        if recipe is None:
            return None
        try:
            sound = pygame.mixer.Sound(buffer=recipe())
        except (pygame.error, ValueError, MemoryError):
            return None
        self._sounds[name] = sound
        return sound

    def define(self, name: str, spec: Mapping[str, Any]) -> bool:
        """Add a cue declared as data. Returns whether the spec was usable.

        Overwriting a built-in is allowed on purpose — that is how a variant
        re-voices ``slay`` without renaming every reference to it. The cue is
        only *described* here; nothing is synthesised until it first plays.
        """
        if not name or not isinstance(spec, Mapping):
            return False
        self._recipes[str(name)] = recipe_from_spec(spec)
        self._sounds.pop(str(name), None)
        self._failed.discard(str(name))
        return True

    def preload(self, names: Sequence[str] | None = None) -> int:
        """Build cues ahead of time so the first play does not hitch."""
        if not (self.enabled and self.available):
            return 0
        for name in names or tuple(self._recipes):
            if name not in self._sounds:
                self._build(name)
        return len(self._sounds)

    # -- control -----------------------------------------------------------

    def toggle(self) -> bool:
        self.enabled = not self.enabled
        if self.enabled and not self.available:
            self._open_mixer()
        return self.enabled

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, volume))

    @property
    def cue_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._recipes))

    def stop(self) -> None:
        if self.available:
            with contextlib.suppress(pygame.error):
                pygame.mixer.stop()


#: Shared silent fallback, so callers can write ``board.play(...)`` freely.
NULL_BOARD = SoundBoard(enabled=False)


__all__ = ["AMPLITUDE", "CHANNELS", "NULL_BOARD", "SAMPLE_RATE", "SoundBoard"]
