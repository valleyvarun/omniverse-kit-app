"""Shared canvas choices, cursor placement, and world-axis constraints."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import ClassVar, Literal, TypeAlias, cast

from tool_box.tools.drawing.brush import BrushKind

Vec2: TypeAlias = tuple[float, float]
Vec3: TypeAlias = tuple[float, float, float]
AxisIndex: TypeAlias = Literal[0, 1, 2]
OptionName: TypeAlias = Literal["drawing_plane", "draw_on_object", "axes_spokes", "cursor_3d"]
AXES: tuple[Vec3, Vec3, Vec3] = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


class DrawingMethods:
    """Canvas switches shared by all drawing tools."""

    _instance: ClassVar[DrawingMethods | None] = None

    def __init__(self) -> None:
        self.drawing_plane: bool = False
        self.draw_on_object: bool = False
        self.axes_spokes: bool = False
        self.cursor_3d: bool = False
        self._listeners: list[Callable[[], None]] = []

    @classmethod
    def get(cls, kind: BrushKind = "paint") -> DrawingMethods:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def set_option(self, name: OptionName, enabled: bool) -> None:
        if bool(getattr(self, name)) != enabled:
            setattr(self, name, enabled)
            callback: Callable[[], None]
            for callback in tuple(self._listeners):
                callback()

    def add_listener(self, callback: Callable[[], None]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)


class CursorAnchor:
    """The placed 3D cursor is shared by tools within each stage."""

    _instances: ClassVar[dict[str, CursorAnchor]] = {}

    def __init__(self) -> None:
        self.position: Vec3 | None = None
        self.placing: bool = False
        self.axis: int = 0
        self.side: int = 1
        self._plane_axis: AxisIndex = 1
        self._listeners: list[Callable[[], None]] = []

    @classmethod
    def get(cls, stage_key: str = "") -> CursorAnchor:
        if stage_key not in cls._instances:
            cls._instances[stage_key] = cls()
        return cls._instances[stage_key]

    def request_placement(self) -> None:
        self.placing = True
        self._notify()

    def cancel_placement(self) -> None:
        if self.placing:
            self.placing = False
            self._notify()

    def place(self, position: Vec3) -> None:
        coordinate: float
        for coordinate in position:
            if not math.isfinite(coordinate):
                raise ValueError("The 3D cursor requires a finite position")
        self.position = position
        self.placing = False
        self._notify()

    def set_mode(self, axis: int) -> tuple[int, int]:
        if axis not in (0, 1, 2, 3):
            raise ValueError("Cursor view must be perspective, X, Y, or Z")
        self.side = -self.side if axis != 0 and axis == self.axis else 1
        self.axis = axis
        if axis != 0:
            self._plane_axis = cast(AxisIndex, axis - 1)
        self._notify()
        return self.axis, self.side

    def plane(self) -> tuple[Vec3, Vec3] | None:
        if self.position is None:
            return None
        # Perspective retains the last selected construction plane.
        return self.position, AXES[self._plane_axis]

    def reset(self) -> None:
        self.position = None
        self.placing = False
        self.axis = 0
        self.side = 1
        self._plane_axis = 1
        self._notify()

    def add_listener(self, callback: Callable[[], None]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify(self) -> None:
        callback: Callable[[], None]
        for callback in tuple(self._listeners):
            callback()


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def choose_axis(delta: Vec2, projected_axes: tuple[Vec2 | None, Vec2 | None, Vec2 | None]) -> AxisIndex | None:
    """Choose the closest visible spoke in screen space, in either direction."""
    if math.hypot(*delta) < 1e-8:
        return None
    selected: AxisIndex | None = None
    best: float = -1.0
    index: int
    projected: Vec2 | None
    for index, projected in enumerate(projected_axes):
        if projected is None:
            continue
        length: float = math.hypot(*projected)
        if length < 1e-7:
            continue
        score: float = abs(delta[0] * projected[0] + delta[1] * projected[1]) / length
        if score > best:
            best = score
            selected = cast(AxisIndex, index)
    return selected


def axis_point_from_ray(start: Vec3, axis: AxisIndex, ray_origin: Vec3, ray_direction: Vec3) -> Vec3 | None:
    """Find the closest point on a full world axis; normal-to-canvas axes work too."""
    length: float = math.sqrt(_dot(ray_direction, ray_direction))
    if length < 1e-10:
        return None
    ray: Vec3 = (ray_direction[0] / length, ray_direction[1] / length, ray_direction[2] / length)
    direction: Vec3 = AXES[axis]
    difference: Vec3 = (start[0] - ray_origin[0], start[1] - ray_origin[1], start[2] - ray_origin[2])
    parallel: float = _dot(direction, ray)
    denominator: float = 1.0 - parallel * parallel
    if denominator < 1e-6:
        return None
    distance: float = (parallel * _dot(ray, difference) - _dot(direction, difference)) / denominator
    if _dot(ray, difference) + parallel * distance < 0.0:
        return None
    return (
        start[0] + direction[0] * distance,
        start[1] + direction[1] * distance,
        start[2] + direction[2] * distance,
    )


def axis_plane_normal(axis: AxisIndex, view_forward: Vec3) -> Vec3:
    """A stable drawing plane contains the locked axis and faces the camera."""
    direction: Vec3 = AXES[axis]
    projection: float = _dot(view_forward, direction)
    normal: Vec3 = (
        view_forward[0] - direction[0] * projection,
        view_forward[1] - direction[1] * projection,
        view_forward[2] - direction[2] * projection,
    )
    length: float = math.sqrt(_dot(normal, normal))
    if length < 1e-8:
        return AXES[1 if axis != 1 else 2]
    return normal[0] / length, normal[1] / length, normal[2] / length


def intersect_plane(ray_origin: Vec3, ray_direction: Vec3, origin: Vec3, normal: Vec3) -> Vec3 | None:
    denominator: float = _dot(ray_direction, normal)
    if abs(denominator) < 1e-9:
        return None
    difference: Vec3 = (origin[0] - ray_origin[0], origin[1] - ray_origin[1], origin[2] - ray_origin[2])
    distance: float = _dot(difference, normal) / denominator
    if distance < 0.0:
        return None
    return (
        ray_origin[0] + ray_direction[0] * distance,
        ray_origin[1] + ray_direction[1] * distance,
        ray_origin[2] + ray_direction[2] * distance,
    )
