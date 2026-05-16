"""Shared mutable state for the 3D Draw tool.

Process-wide singleton. The Tool Properties slider writes to it; the
viewport cursor and the stroke emitter read from it. Listeners are
notified on any value change (including `active`) so dependent UIs
(e.g. the properties panel, the cursor scene) can refresh.
"""

from __future__ import annotations

from typing import Callable


class BrushConfig:
    # Brush ring radius in world units (the only user-controllable knob).
    # Each stamp emits ONE sphere of this radius (no grid expansion).
    BRUSH_RADIUS_DEFAULT = 2.0
    BRUSH_RADIUS_MIN = 0.5
    BRUSH_RADIUS_MAX = 20.0

    # Stamp spacing along a stroke is `brush_radius * STAMP_SPACING_FRAC`.
    # 1.25 gives ~37% overlap (radius/spacing = 0.8) between consecutive
    # spheres of size = brush -- still reads as a continuous tube while
    # cutting per-stroke point count by ~40% vs the old 0.75.
    STAMP_SPACING_FRAC = 1.25

    # Process-wide singleton so cursor / tool / UI all share the same state.
    _instance: "BrushConfig | None" = None

    def __init__(self) -> None:
        self.brush_radius: float = self.BRUSH_RADIUS_DEFAULT
        # Whether the 3D Draw tool is currently active; toggled by the tool.
        self._active: bool = False
        # Listeners notified on any value / active-state change.
        self._listeners: list[Callable[[], None]] = []

    # Lazy global accessor.
    @classmethod
    def get(cls) -> "BrushConfig":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # Per-stamp sphere radius: equals brush_radius (one sphere per stamp,
    # the size of the brush itself). Kept as `sphere_radius` for symmetry
    # with existing call sites.
    @property
    def sphere_radius(self) -> float:
        return self.brush_radius

    # World-space distance between consecutive stamps along a stroke.
    # Adaptive so smaller brushes don't leave gaps.
    @property
    def stamp_spacing(self) -> float:
        return max(0.05, self.brush_radius * self.STAMP_SPACING_FRAC)

    # ----- Active state -----
    @property
    def active(self) -> bool:
        return self._active

    def set_active(self, value: bool) -> None:
        value = bool(value)
        if self._active != value:
            self._active = value
            self._notify()

    # ----- Brush radius (the only mutable parameter) -----
    def set_brush_radius(self, value: float) -> None:
        v = self._clamp(value, self.BRUSH_RADIUS_MIN, self.BRUSH_RADIUS_MAX)
        if abs(v - self.brush_radius) > 1e-6:
            self.brush_radius = v
            self._notify()

    # ----- Listener registry -----
    def add_listener(self, cb: Callable[[], None]) -> None:
        if cb not in self._listeners:
            self._listeners.append(cb)

    def remove_listener(self, cb: Callable[[], None]) -> None:
        try:
            self._listeners.remove(cb)
        except ValueError:
            pass

    # Fire all listeners; swallow per-listener exceptions.
    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                pass

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return lo
        return max(lo, min(hi, v))
