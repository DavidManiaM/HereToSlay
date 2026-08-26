"""Multi-camera director for the table.

Views cycle: local (party + hand) → each opponent's deployed persoane → centre
(besties + decks) → local. Arrow keys and clicking an opponent pile switch
the active camera.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any


class CameraKind(Enum):
    LOCAL = auto()
    OPPONENT = auto()
    CENTRE = auto()


@dataclass(slots=True)
class CameraView:
    kind: CameraKind
    """Stable id for cycling: ``local``, ``centre``, or a player id."""
    key: str
    label: str
    player_id: str | None = None


class CameraDirector:
    """Owns the ordered list of cameras and the active index."""

    def __init__(self) -> None:
        self.views: list[CameraView] = [
            CameraView(CameraKind.LOCAL, "local", "Tu"),
            CameraView(CameraKind.CENTRE, "centre", "Besties"),
        ]
        self.index = 0
        self.blend = 1.0  # 0..1 settle after a switch

    @property
    def active(self) -> CameraView:
        if not self.views:
            return CameraView(CameraKind.LOCAL, "local", "Tu")
        return self.views[self.index % len(self.views)]

    def sync_from_view(self, game_view: Any, *, local_label: str = "Tu") -> None:
        """Rebuild cycle from play order: you → opponents → centre."""
        if game_view is None:
            return
        order = list(getattr(game_view, "turn_order", ()) or ())
        you = getattr(game_view, "seat", None)
        if you and you in order:
            # Rotate so local is first.
            i = order.index(you)
            order = order[i:] + order[:i]
        views: list[CameraView] = [
            CameraView(CameraKind.LOCAL, "local", local_label, player_id=you),
        ]
        for pid in order:
            if pid == you:
                continue
            player = game_view.players.get(pid)
            name = player.name if player is not None else str(pid)
            views.append(CameraView(CameraKind.OPPONENT, pid, name, player_id=pid))
        views.append(CameraView(CameraKind.CENTRE, "centre", "Besties"))

        prev = self.active.key
        self.views = views
        for i, v in enumerate(self.views):
            if v.key == prev:
                self.index = i
                break
        else:
            self.index = 0

    def next(self) -> CameraView:
        if self.views:
            self.index = (self.index + 1) % len(self.views)
            self.blend = 0.0
        return self.active

    def prev(self) -> CameraView:
        if self.views:
            self.index = (self.index - 1) % len(self.views)
            self.blend = 0.0
        return self.active

    def jump(self, key: str) -> CameraView:
        for i, v in enumerate(self.views):
            if v.key == key or v.player_id == key:
                self.index = i
                self.blend = 0.0
                return self.active
        return self.active

    def jump_opponent(self, player_id: str) -> CameraView:
        return self.jump(player_id)

    def update(self, dt: float) -> None:
        if self.blend < 1.0:
            # ~0.45s settle — slow enough to read as a camera move.
            self.blend = min(1.0, self.blend + dt * 2.2)


__all__ = ["CameraDirector", "CameraKind", "CameraView"]
