"""Accent materials: reflective foil and emissive glow.

Two paths, one API:

* **ModernGL** — tiny fragment shaders on accent quads (foil band, emissive disc).
* **CPU** — cached gradient strips and ``blit_glow``, used when GL is missing,
  fails to init, or the video driver is dummy (tests / CI).

Call :func:`init` once after the display exists, :func:`resize` on window
changes, and :func:`shutdown` on quit. Drawing never raises: a failed GPU path
falls back silently so the board stays playable on any PC.
"""

from __future__ import annotations

import contextlib
import math
import time
from functools import lru_cache
from typing import Any

import pygame

from here_to_slay.ui.pygame import theme as T
from here_to_slay.ui.pygame.theme import C

_FOIL_VERT = """
#version 330
in vec2 in_vert;
in vec2 in_uv;
out vec2 v_uv;
void main() {
    v_uv = in_uv;
    gl_Position = vec4(in_vert, 0.0, 1.0);
}
"""

_FOIL_FRAG = """
#version 330
uniform vec3 u_tint;
uniform float u_time;
uniform float u_hover;
in vec2 v_uv;
out vec4 f_color;
void main() {
    float band = smoothstep(0.15, 0.35, v_uv.y) * (1.0 - smoothstep(0.55, 0.78, v_uv.y));
    float sweep = fract(v_uv.x * 0.85 + u_time * 0.35 + u_hover * 0.2);
    float glint = smoothstep(0.42, 0.5, sweep) * (1.0 - smoothstep(0.5, 0.58, sweep));
    vec3 metal = mix(u_tint * 0.55, vec3(1.0), 0.35 + 0.45 * glint);
    float a = band * (0.35 + 0.45 * glint + 0.2 * u_hover);
    f_color = vec4(metal, a);
}
"""

_EMISSIVE_FRAG = """
#version 330
uniform vec3 u_colour;
uniform float u_strength;
in vec2 v_uv;
out vec4 f_color;
void main() {
    vec2 p = v_uv * 2.0 - 1.0;
    float d = length(p);
    float core = exp(-d * d * 3.2);
    float halo = exp(-d * d * 1.1) * 0.45;
    float a = (core + halo) * u_strength;
    f_color = vec4(u_colour * a, a);
}
"""

_ctx: Any = None
_foil_prog: Any = None
_emissive_prog: Any = None
_quad_vbo: Any = None
_available = False
_forced_cpu = False
_time0 = time.perf_counter()


def available() -> bool:
    return _available and not _forced_cpu


def set_force_cpu(force: bool) -> None:
    global _forced_cpu
    _forced_cpu = bool(force)


def init(_screen: pygame.Surface | None = None) -> bool:
    """Try to stand up a tiny ModernGL context. Safe to call more than once."""
    global _ctx, _foil_prog, _emissive_prog, _quad_vbo, _available
    if _available:
        return True
    try:
        import moderngl
    except ImportError:
        _available = False
        return False
    try:
        # Standalone context: we composite shader output onto pygame Surfaces,
        # so we do not need to own the window's GL state.
        _ctx = moderngl.create_context(standalone=True, require=330)
        _foil_prog = _ctx.program(vertex_shader=_FOIL_VERT, fragment_shader=_FOIL_FRAG)
        _emissive_prog = _ctx.program(vertex_shader=_FOIL_VERT, fragment_shader=_EMISSIVE_FRAG)
        # Fullscreen quad in NDC with UVs.
        vertices = [
            -1.0, -1.0, 0.0, 0.0,
             1.0, -1.0, 1.0, 0.0,
            -1.0,  1.0, 0.0, 1.0,
            -1.0,  1.0, 0.0, 1.0,
             1.0, -1.0, 1.0, 0.0,
             1.0,  1.0, 1.0, 1.0,
        ]
        _quad_vbo = _ctx.buffer(bytes(_pack_f32(vertices)))
        _available = True
        return True
    except Exception:
        _ctx = None
        _foil_prog = None
        _emissive_prog = None
        _quad_vbo = None
        _available = False
        return False


def resize(_size: tuple[int, int]) -> None:
    """Viewport hook — standalone FBOs are sized per draw, so nothing to do."""


def shutdown() -> None:
    global _ctx, _foil_prog, _emissive_prog, _quad_vbo, _available
    for obj in (_quad_vbo, _foil_prog, _emissive_prog, _ctx):
        if obj is not None:
            with contextlib.suppress(Exception):
                obj.release()
    _ctx = None
    _foil_prog = None
    _emissive_prog = None
    _quad_vbo = None
    _available = False


def _pack_f32(values: list[float]) -> bytearray:
    import struct

    return bytearray(struct.pack(f"{len(values)}f", *values))


def _now() -> float:
    return time.perf_counter() - _time0


def draw_foil(
    dest: pygame.Surface,
    rect: pygame.Rect,
    tint: tuple[int, int, int],
    *,
    hover: float = 0.0,
) -> None:
    """Reflective band across the upper third of ``rect``."""
    if rect.width < 8 or rect.height < 12:
        return
    band = pygame.Rect(rect.left + 4, rect.top + max(4, rect.height // 10),
                       rect.width - 8, max(6, rect.height // 5))
    if available() and _try_shader_foil(dest, band, tint, hover):
        return
    _cpu_foil(dest, band, tint, hover)


def draw_emissive(
    dest: pygame.Surface,
    centre: tuple[int, int],
    radius: int,
    colour: tuple[int, int, int] = C.CYAN,
    *,
    strength: float = 0.55,
) -> None:
    """Soft additive glow under a card, pip, or AP dot."""
    radius = max(4, int(radius))
    if available() and _try_shader_emissive(dest, centre, radius, colour, strength):
        return
    T.blit_glow(dest, centre, radius, T.alpha(colour, int(110 * strength)), power=2.1)


def _try_shader_foil(
    dest: pygame.Surface,
    band: pygame.Rect,
    tint: tuple[int, int, int],
    hover: float,
) -> bool:
    try:
        assert _ctx is not None and _foil_prog is not None and _quad_vbo is not None
        w, h = max(2, band.width), max(2, band.height)
        fbo_tex = _ctx.texture((w, h), 4)
        fbo = _ctx.framebuffer(color_attachments=[fbo_tex])
        fbo.use()
        _ctx.clear(0, 0, 0, 0)
        _foil_prog["u_tint"].value = (tint[0] / 255.0, tint[1] / 255.0, tint[2] / 255.0)
        _foil_prog["u_time"].value = _now()
        _foil_prog["u_hover"].value = max(0.0, min(1.0, hover))
        vao = _ctx.simple_vertex_array(
            _foil_prog, _quad_vbo, "in_vert", "in_uv",
        )
        vao.render()
        data = fbo_tex.read()
        surf = pygame.image.frombuffer(data, (w, h), "RGBA")
        # OpenGL origin is bottom-left.
        surf = pygame.transform.flip(surf, False, True)
        dest.blit(surf, band.topleft)
        fbo.release()
        fbo_tex.release()
        vao.release()
        return True
    except Exception:
        return False


def _try_shader_emissive(
    dest: pygame.Surface,
    centre: tuple[int, int],
    radius: int,
    colour: tuple[int, int, int],
    strength: float,
) -> bool:
    try:
        assert _ctx is not None and _emissive_prog is not None and _quad_vbo is not None
        size = radius * 2
        fbo_tex = _ctx.texture((size, size), 4)
        fbo = _ctx.framebuffer(color_attachments=[fbo_tex])
        fbo.use()
        _ctx.clear(0, 0, 0, 0)
        _emissive_prog["u_colour"].value = (
            colour[0] / 255.0, colour[1] / 255.0, colour[2] / 255.0,
        )
        _emissive_prog["u_strength"].value = max(0.05, min(1.5, strength))
        vao = _ctx.simple_vertex_array(
            _emissive_prog, _quad_vbo, "in_vert", "in_uv",
        )
        vao.render()
        data = fbo_tex.read()
        surf = pygame.image.frombuffer(data, (size, size), "RGBA")
        surf = pygame.transform.flip(surf, False, True)
        dest.blit(
            surf, (centre[0] - radius, centre[1] - radius),
            special_flags=pygame.BLEND_RGBA_ADD,
        )
        fbo.release()
        fbo_tex.release()
        vao.release()
        return True
    except Exception:
        return False


@lru_cache(maxsize=96)
def _foil_strip(width: int, height: int, tint: tuple[int, int, int]) -> pygame.Surface:
    surf = T.surface((width, height))
    for x in range(width):
        t = x / max(1, width - 1)
        # Metallic ramp: dark → tint → white → tint
        if t < 0.35:
            k = t / 0.35
            col = T.lerp_colour(T.shade(tint, 0.45), tint, k)
        elif t < 0.55:
            k = (t - 0.35) / 0.2
            col = T.lerp_colour(tint, (255, 255, 255), k)
        else:
            k = (t - 0.55) / 0.45
            col = T.lerp_colour((255, 255, 255), T.shade(tint, 0.55), k)
        pygame.draw.line(surf, (*col[:3], 160), (x, 0), (x, height))
    return surf


def _cpu_foil(
    dest: pygame.Surface,
    band: pygame.Rect,
    tint: tuple[int, int, int],
    hover: float,
) -> None:
    strip = _foil_strip(max(2, band.width), max(2, band.height), tint)
    # Sliding glint window.
    phase = (_now() * 0.4 + hover * 0.25) % 1.0
    glint_x = int(phase * (band.width + 20)) - 10
    dest.blit(strip, band.topleft)
    glint = T.surface((max(8, band.width // 5), band.height))
    glint.blit(
        T.hgradient(glint.get_width(), glint.get_height(),
                    (255, 255, 255, 0), (255, 255, 255, int(90 + 80 * hover))),
        (0, 0),
    )
    glint.blit(
        T.hgradient(glint.get_width(), glint.get_height(),
                    (255, 255, 255, int(90 + 80 * hover)), (255, 255, 255, 0)),
        (0, 0),
        special_flags=pygame.BLEND_RGBA_MAX,
    )
    clip = dest.get_clip()
    dest.set_clip(band)
    dest.blit(glint, (band.left + glint_x, band.top), special_flags=pygame.BLEND_RGBA_ADD)
    dest.set_clip(clip)


def pulse_strength(seconds: float | None = None, period: float = 1.8) -> float:
    t = seconds if seconds is not None else _now()
    return 0.45 + 0.55 * (0.5 + 0.5 * math.sin(t * (2 * math.pi / period)))


__all__ = [
    "available",
    "draw_emissive",
    "draw_foil",
    "init",
    "pulse_strength",
    "resize",
    "set_force_cpu",
    "shutdown",
]
