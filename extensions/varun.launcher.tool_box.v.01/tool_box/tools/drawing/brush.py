"""Shared brush settings and per-sample pressure evaluation."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, Literal, TypeAlias

LOGGER: logging.Logger = logging.getLogger(__name__)
BrushShape: TypeAlias = Literal["circle", "square", "triangle"]
BrushKind: TypeAlias = Literal["paint", "pipe"]
PressureTarget: TypeAlias = Literal["size", "hardness", "opacity"]
SHAPES: tuple[BrushShape, ...] = ("circle", "square", "triangle")


def minimum_point_distance(first_radius: float, last_radius: float) -> float:
    """Reject stationary input and sub-brush jitter, independent of event rate."""
    return max(0.025, min(first_radius, last_radius) * 0.2)


@dataclass(frozen=True)
class BrushSample:
    radius: float
    hardness: float
    opacity: float

    def blend(self, other: BrushSample, t: float) -> BrushSample:
        return BrushSample(
            self.radius + (other.radius - self.radius) * t,
            self.hardness + (other.hardness - self.hardness) * t,
            self.opacity + (other.opacity - self.opacity) * t,
        )


class BrushConfig:
    BRUSH_RADIUS_DEFAULT: float = 2.0
    BRUSH_RADIUS_MIN: float = 0.5
    BRUSH_RADIUS_MAX: float = 20.0
    STAMP_SPACING_FRAC: float = 0.2
    _instance: ClassVar[BrushConfig | None] = None

    def __init__(self) -> None:
        self.brush_radius: float = self.BRUSH_RADIUS_DEFAULT
        self.hardness: float = 1.0
        self.opacity: float = 1.0
        self.shape: BrushShape = "circle"
        self.pressure: dict[PressureTarget, bool] = {"size": False, "hardness": False, "opacity": False}
        self.active_tool: BrushKind | None = None
        self._listeners: list[Callable[[], None]] = []

    @classmethod
    def get(cls) -> BrushConfig:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def active(self) -> bool:
        return self.active_tool is not None

    @property
    def sphere_radius(self) -> float:
        return self.brush_radius

    @property
    def stamp_spacing(self) -> float:
        return max(0.01, self.brush_radius * self.STAMP_SPACING_FRAC)

    def set_active(self, value: bool, tool: BrushKind = "paint") -> None:
        selected: BrushKind | None = tool if value else None
        if selected != self.active_tool:
            self.active_tool = selected
            self._notify()

    def set_brush_radius(self, value: float) -> None:
        self._set_float("brush_radius", value, self.BRUSH_RADIUS_MIN, self.BRUSH_RADIUS_MAX)

    def set_hardness(self, value: float) -> None:
        self._set_float("hardness", value, 0.0, 1.0)

    def set_opacity(self, value: float) -> None:
        self._set_float("opacity", value, 0.0, 1.0)

    def _set_float(self, name: str, value: float, lo: float, hi: float) -> None:
        value = self._clamp(value, lo, hi)
        if abs(float(getattr(self, name)) - value) > 1e-6:
            setattr(self, name, value)
            self._notify()

    def set_shape(self, shape: BrushShape) -> None:
        if shape in SHAPES and shape != self.shape:
            self.shape = shape
            self._notify()

    def set_pressure(self, target: PressureTarget, enabled: bool) -> None:
        if self.pressure[target] != enabled:
            self.pressure[target] = enabled
            self._notify()

    def sample(self, pressure: float | None = None) -> BrushSample:
        # Missing pressure is an ordinary mouse, never simulated pressure.
        strength: float = 1.0 if pressure is None else self._clamp(pressure, 0.0, 1.0)
        return BrushSample(
            max(0.005, self.brush_radius * (strength if self.pressure["size"] else 1.0)),
            self.hardness * (strength if self.pressure["hardness"] else 1.0),
            self.opacity * (strength if self.pressure["opacity"] else 1.0),
        )

    def adjust(self, dx: float, dy: float) -> None:
        """Logical pixels: up increases size; right increases paint hardness."""
        self.set_brush_radius(self.brush_radius * math.exp(max(-2.0, min(2.0, -dy * 0.01))))
        if self.active_tool == "paint":
            self.set_hardness(self.hardness + dx * 0.005)

    def add_listener(self, callback: Callable[[], None]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify(self) -> None:
        callback: Callable[[], None]
        for callback in tuple(self._listeners):
            try:
                callback()
            except Exception:
                LOGGER.exception("Could not update brush settings")

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value)) if math.isfinite(value) else lo
