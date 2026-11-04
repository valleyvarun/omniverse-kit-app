"""Camera-linked, depth-tested grid and ruler for the Light viewport."""

# Python & library imports
import math
import sys
from typing import Any

# Omniverse & USD imports
import carb.settings
from pxr import Gf, Usd, UsdGeom

# Local imports
from ..axis_colors import AXIS_COLORS
from ..touch import DEFAULT_PIVOT_DISTANCE, PIVOT_ATTRIBUTE
from .grid_depth import DepthGrid
from .ruler_depth import DepthRuler, RulerLabel, RulerLine
from .settings import DEFAULT_UNIT, RULER, RULER_COLOR, STORM, UNIT_METERS, UNITS

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

# numbered major steps on each side of the ruler crossing, and the number size in pixels
RULER_COUNT: int = 8
LABEL_SIZE: float = 13.0
GRID_RADIUS: int = 400
RULER_THICKNESS: float = 1.25
# Maximum ground-plane distance from the camera, ignoring its height.
RULER_RADIUS_METERS: float = 20.0


# Match the navigation pivot so orbiting never changes the grid scale.
def _pivot_offset(viewport_api: Any) -> Gf.Vec3d:
    camera: UsdGeom.Camera = UsdGeom.Camera(viewport_api.stage.GetPrimAtPath(viewport_api.camera_path))
    if camera:
        attribute: Usd.Attribute = camera.GetPrim().GetAttribute(PIVOT_ATTRIBUTE)
        offset: Any = attribute.Get() if attribute else None
        if offset is not None and 1e-6 < offset.GetLength() < math.inf:
            return Gf.Vec3d(offset)
        focus: float = float(camera.GetFocusDistanceAttr().Get(viewport_api.time) or 0.0)
        if math.isfinite(focus) and focus > 0.0:
            return Gf.Vec3d(0.0, 0.0, -focus)
    return Gf.Vec3d(0.0, 0.0, -DEFAULT_PIVOT_DISTANCE)


# Measure the view at the navigation pivot without depending on orbit elevation.
def _grid_view_height(viewport_api: Any, projection: Gf.Matrix4d | None = None) -> float:
    matrix: Gf.Matrix4d = projection if projection is not None else viewport_api.projection
    distance: float = _pivot_offset(viewport_api).GetLength()
    if abs(matrix[3][3]) > 0.5:
        # Orthographic zoom changes aperture, not the camera's distance.
        distance = 1.0
    return distance / max(abs(matrix[1][1]), 1e-12)


# Share the displayed minor-grid spacing with cursor placement.
def grid_spacing(viewport_api: Any, unit: str, projection: Gf.Matrix4d | None = None) -> tuple[float, int]:
    to_display: float = float(UsdGeom.GetStageMetersPerUnit(viewport_api.stage)) / UNIT_METERS[unit]
    scale: float = max(_grid_view_height(viewport_api, projection) * to_display, 1e-6)
    exponent: int = math.floor(math.log10(scale * 0.15) + 1e-10)
    return 10.0**exponent / to_display, exponent


def snap_cursor_to_grid(viewport_api: Any, position: tuple[float, float, float]) -> tuple[float, float, float]:
    settings: Any = carb.settings.get_settings()
    stage: Any = viewport_api.stage
    visibility: str = f"/persistent/app/viewport/{viewport_api.id}/guide/grid/visible"
    if (
        stage is None
        or viewport_api.render_mode != STORM
        or settings.get(RULER) is False
        or settings.get(visibility) is False
    ):
        return position
    unit: Any = settings.get(UNITS)
    unit = unit if unit in UNIT_METERS else DEFAULT_UNIT
    up: int = 2 if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z else 1
    step: float = grid_spacing(viewport_api, unit)[0]
    snapped: list[float] = list(position)
    axis: int
    for axis in range(3):
        snapped[axis] = 0.0 if axis == up else round(position[axis] / step) * step
    return snapped[0], snapped[1], snapped[2]


# ----------------------------------------------------------------------
# 2. STUDIO GRID
# ----------------------------------------------------------------------


class StudioGrid:
    # Register the depth guides and their viewport visibility setting.
    def __init__(self, viewport_api: Any, color: tuple[float, float, float, float]) -> None:
        self._api: Any = viewport_api
        self._settings: Any = carb.settings.get_settings()
        self._visibility: str = f"/persistent/app/viewport/{viewport_api.id}/guide/grid/visible"
        self._color: tuple[float, float, float, float] = color
        self._key: tuple[int, float, tuple[int, int], float] | None = None
        self._ruler_key: tuple[int, float, tuple[float, float, float], tuple[int, int], str, bool] | None = None
        self._grid: DepthGrid = DepthGrid(viewport_api)
        self._ruler: DepthRuler = DepthRuler(viewport_api)

    # Invalidate the cached grid after a theme change.
    def set_color(self, color: tuple[float, float, float, float]) -> None:
        self._color = color
        self._key = None
        self._ruler_key = None

    # Rebuild for scale changes, grid recentering, or a changed ruler anchor.
    def update(self, view: Gf.Matrix4d | None = None, projection: Gf.Matrix4d | None = None) -> None:
        if self._api is None:
            return
        frame_view: Gf.Matrix4d = view if view is not None else self._api.view
        frame_projection: Gf.Matrix4d = projection if projection is not None else self._api.projection
        if self._grid.update(frame_view, frame_projection):
            self._key = None
        if self._ruler.update(frame_view, frame_projection):
            self._ruler_key = None
        # Skip grid work when no stage or visible guide is available.
        stage: Any = self._api.stage
        visible: Any = self._settings.get(self._visibility)
        show_grid: bool = stage is not None and visible is not False
        self._grid.set_visible(show_grid)
        if not show_grid:
            self._ruler.set_visible(False)
            return

        # --- 1) CHOOSE SPACING IN THE DISPLAY UNIT ---

        # Choose the ground-plane axes from the stage up axis.
        up: int = 2 if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z else 1
        axes: tuple[int, int] = (0, 1 if up == 2 else 2)
        world: Gf.Matrix4d = frame_view.GetInverse()
        camera: Gf.Vec3d = world.ExtractTranslation()
        # Convert stage units to metres or millimetres so lines land on round values.
        unit: Any = self._settings.get(UNITS)
        unit = unit if unit in UNIT_METERS else DEFAULT_UNIT
        step: float
        exponent: int
        step, exponent = grid_spacing(self._api, unit, frame_projection)
        level_blend: float = max(
            0.0, min(1.0, (_grid_view_height(self._api, frame_projection) * 0.15 / step - 1.0) / 9.0)
        )

        # --- 2) REUSE THE LINES UNTIL THE CAMERA ENTERS A NEW REGION ---

        # Keep nearby ground covered as the camera travels away from the world origin.
        center: tuple[int, int] = (
            math.floor(camera[axes[0]] / step / 10) * 10,
            math.floor(camera[axes[1]] / step / 10) * 10,
        )
        key: tuple[int, float, tuple[int, int], float] = (up, step, center, level_blend)
        if key != self._key:
            self._key = key
            self._grid.set_geometry(up, step, center, GRID_RADIUS, self._color, level_blend)

        # --- 3) ANCHOR THE RULER TO THE PLACED CURSOR OR FOLLOW THE VIEW CENTER ---

        show_ruler: bool = self._settings.get(RULER) is not False
        if not show_ruler:
            self._ruler.set_visible(False)
            return
        origin: tuple[float, float, float] | None = self._cursor_origin(stage)
        anchored: bool = origin is not None
        ruler_center: tuple[int, int] = (0, 0) if anchored else center
        if origin is None:
            radius: float = RULER_RADIUS_METERS / float(UsdGeom.GetStageMetersPerUnit(stage))
            pivot: Gf.Vec3d = world.Transform(_pivot_offset(self._api))
            origin = self._view_center(world, camera, up, radius, pivot)
        self._ruler.set_visible(True)
        ruler_key: tuple[int, float, tuple[float, float, float], tuple[int, int], str, bool] = (
            up,
            step,
            origin,
            ruler_center,
            unit,
            anchored,
        )
        if ruler_key != self._ruler_key:
            self._ruler_key = ruler_key
            corner: Gf.Vec4d = Gf.Vec4d(1.0, 1.0, 1.0, 1.0) * frame_projection.GetInverse()
            extent: float = (
                Gf.Vec3d(corner[0], corner[1], corner[2]).GetLength() / abs(corner[3])
                if abs(corner[3]) > 1e-12
                else step * 1e6
            )
            self._build_ruler(up, axes, step, origin, ruler_center, exponent + 1, anchored, extent)

    # Toolbox is optional; read its existing per-stage cursor without loading the extension.
    @staticmethod
    def _cursor_origin(stage: Any) -> tuple[float, float, float] | None:
        if "tool_box.tools.drawing.methods" not in sys.modules:
            return None
        from tool_box.tools.drawing.methods import CursorAnchor, DrawingMethods

        if not DrawingMethods.get().cursor_3d:
            return None
        stage_key: str = str(stage.GetRootLayer().identifier)
        return CursorAnchor.get(stage_key).position

    @staticmethod
    def _view_center(
        world: Gf.Matrix4d, camera: Gf.Vec3d, up: int, radius: float, pivot: Gf.Vec3d | None = None
    ) -> tuple[float, float, float]:
        """Keep the center-ray ground target within a horizontal radius of the camera."""
        height: float = float(camera[up])
        forward: Gf.Vec3d = world.TransformDir(Gf.Vec3d(0.0, 0.0, -1.0)).GetNormalized()
        point: Gf.Vec3d = Gf.Vec3d(camera)
        point[up] = 0.0
        displacement: Gf.Vec3d
        if abs(height) <= radius * 1e-10 and abs(forward[up]) <= 1e-10:
            # A ray lying in the ground has no unique intersection; retain the orbit pivot.
            displacement = (pivot - point) if pivot is not None else forward * radius
        elif abs(forward[up]) > 1e-10:
            # Reflect away-facing rays toward ground to stay continuous across ground and horizon.
            displacement = forward * abs(height / forward[up])
        else:
            # At the horizon, keep the bounded target ahead on the ground.
            displacement = Gf.Vec3d(forward)
            displacement[up] = 0.0
            displacement = displacement.GetNormalized() * radius
        displacement[up] = 0.0
        length: float = displacement.GetLength()
        if length > 1e-12:
            point += displacement * (min(length, radius) / length)
        return float(point[0]), float(point[1]), float(point[2])

    # Span the grid with thin RGB axes and yellow major-step labels.
    def _build_ruler(
        self,
        up: int,
        axes: tuple[int, int],
        step: float,
        origin: tuple[float, float, float],
        center: tuple[int, int],
        exponent: int,
        anchored: bool,
        extent: float,
    ) -> None:
        lines: list[RulerLine] = []
        labels: list[RulerLabel] = []
        major: float = step * 10.0
        decimals: int = max(0, -exponent)
        axis: int
        offset: int
        for axis in (axes[0], axes[1], up):
            # Keep cursor axes centered on the anchor; otherwise follow the grid bounds.
            start: list[float] = list(origin)
            end: list[float] = list(origin)
            middle: float = origin[axis] if anchored else (center[axes.index(axis)] * step if axis in axes else 0.0)
            start[axis] = min(middle, origin[axis]) - extent
            end[axis] = max(middle, origin[axis]) + extent
            if axis == up:
                # The vertical ruler must reach its ground marker even at a raised cursor.
                start[axis] = min(start[axis], 0.0)
                end[axis] = max(end[axis], 0.0)
            lines.append(
                RulerLine((start[0], start[1], start[2]), (end[0], end[1], end[2]), AXIS_COLORS[axis], RULER_THICKNESS)
            )
            for offset in range(-RULER_COUNT, RULER_COUNT + 1):
                # Tick positions and values always use world coordinates, even at a cursor anchor.
                index: int = round(origin[axis] / major) + offset
                position: list[float] = list(origin)
                position[axis] = index * major
                if math.isclose(position[axis], origin[axis], rel_tol=0.0, abs_tol=major * 1e-9):
                    continue
                text: str = "0" if index == 0 else f"{index * 10.0**exponent:.{decimals}f}"
                labels.append(
                    RulerLabel((position[0], position[1], position[2]), text, RULER_COLOR[:3], LABEL_SIZE, axis)
                )
        marker: list[float] = list(origin)
        marker[up] = 0.0
        self._ruler.set_geometry(lines, labels, (marker[0], marker[1], marker[2]))

    def clear_stage(self) -> None:
        self._grid.destroy()
        self._ruler.destroy()
        self._key = None
        self._ruler_key = None

    # Remove the temporary guide layers from the stage.
    def destroy(self) -> None:
        self._grid.destroy()
        self._ruler.destroy()
        self._api = None
