"""Session-only ground grid rendered at its world depth by Storm."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, TypeAlias, cast

from omni.usd import editor
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

from ..axis_colors import AXIS_COLORS, srgb_to_linear

Vec3: TypeAlias = tuple[float, float, float]
Color4: TypeAlias = tuple[float, float, float, float]
GeometryKey: TypeAlias = tuple[int, float, tuple[int, int], int, Color4, float]
Bounds: TypeAlias = tuple[float, float, float, float]

_SOURCE: str = Path(__file__).with_suffix(".glslfx").read_text(encoding="utf-8")


def _ground_bounds(
    world: Gf.Matrix4d, projection: Gf.Matrix4d, up: int, step: float, center: tuple[int, int]
) -> tuple[Bounds, float] | None:
    """Intersect the camera frustum with ground in coordinates near the camera."""
    axes: tuple[int, int] = (0, 1 if up == 2 else 2)
    camera: Gf.Vec3d = world.ExtractTranslation()
    inverse: Gf.Matrix4d = projection.GetInverse()
    corners: list[Gf.Vec3d] = []
    heights: list[float] = []
    far: float = 1.0
    z: float
    y: float
    x: float
    for z in (-1.0, 1.0):
        for y in (-1.0, 1.0):
            for x in (-1.0, 1.0):
                homogeneous: Gf.Vec4d = Gf.Vec4d(x, y, z, 1.0) * inverse
                eye: Gf.Vec3d = Gf.Vec3d(homogeneous[0], homogeneous[1], homogeneous[2])
                if abs(homogeneous[3]) > 1e-12:
                    eye /= homogeneous[3]
                else:
                    # Infinite-far projections get a distant, smoothly faded end.
                    depth: float = max(abs(camera[up]) * 1e6, step * 1e6, 1.0)
                    eye *= depth / max(abs(eye[2]), 1e-12)
                if z > 0.0:
                    far = max(far, abs(float(eye[2])))
                offset: Gf.Vec3d = world.TransformDir(eye)
                corners.append(offset)
                heights.append(float(camera[up] + offset[up]))
    intersections: list[Gf.Vec3d] = []
    index: int
    height: float
    for index, height in enumerate(heights):
        if abs(height) <= max(1e-9, step * 1e-9):
            intersections.append(corners[index])
        bit: int
        for bit in (1, 2, 4):
            other: int = index ^ bit
            if other > index and height * heights[other] < 0.0:
                fraction: float = -height / (heights[other] - height)
                intersections.append(corners[index] + (corners[other] - corners[index]) * fraction)
    if not intersections:
        return None
    local_u: list[float] = []
    local_v: list[float] = []
    hit: Gf.Vec3d
    for hit in intersections:
        local_u.append(float(camera[axes[0]] - center[0] * step + hit[axes[0]]))
        local_v.append(float(camera[axes[1]] - center[1] * step + hit[axes[1]]))
    low_u: float = min(local_u)
    high_u: float = max(local_u)
    low_v: float = min(local_v)
    high_v: float = max(local_v)
    padding: float = max(high_u - low_u, high_v - low_v, step) * 1e-5
    return (low_u - padding, high_u + padding, low_v - padding, high_v + padding), far


class DepthGrid:
    """Keep the ground guide out of saved assets, picking, and drawing surfaces."""

    def __init__(self, viewport_api: Any) -> None:
        self._api: Any = viewport_api
        self._stage: Any = None
        self._layer: Any = None
        self._root: Any = None
        self._mesh: Any = None
        self._shader: Any = None
        self._translation: Any = None
        self._path: str = ""
        self._visible: bool = False
        self._resolution: tuple[int, int] = (0, 0)
        self._geometry: GeometryKey | None = None
        self._view: Gf.Matrix4d = viewport_api.view
        self._projection: Gf.Matrix4d = viewport_api.projection
        self._view_key: tuple[float, ...] = ()

    def update(self, view: Gf.Matrix4d | None = None, projection: Gf.Matrix4d | None = None) -> bool:
        """Attach to the current stage and report stage or resolution changes."""
        stage: Any = self._api.stage
        changed: bool = stage != self._stage
        if changed:
            self.destroy()
            if stage is not None:
                self._attach(stage)
        resolution: tuple[int, int] = (int(self._api.resolution[0]), int(self._api.resolution[1]))
        if resolution != self._resolution:
            self._resolution = resolution
            changed = True
        self._view = view if view is not None else self._api.view
        self._projection = projection if projection is not None else self._api.projection
        values: list[float] = []
        matrix: Gf.Matrix4d
        row: int
        column: int
        for matrix in (self._view, self._projection):
            for row in range(4):
                for column in range(4):
                    values.append(float(matrix[row][column]))
        view_key: tuple[float, ...] = tuple(values)
        if view_key != self._view_key:
            self._view_key = view_key
            self._refresh_patch()
        return changed

    def _attach(self, stage: Any) -> None:
        self._stage = stage
        self._path = "/ViewportLight_Grid"
        while stage.GetPrimAtPath(self._path):
            self._path += "_"
        self._layer = cast(Any, Sdf.Layer).CreateAnonymous("viewport_light_grid.usda")
        stage.GetSessionLayer().subLayerPaths.insert(0, self._layer.identifier)
        with cast(Any, Usd).EditContext(stage, self._layer):
            self._root = cast(Any, UsdGeom.Xform).Define(stage, self._path)
            editor.set_hide_in_stage_window(self._root.GetPrim(), True)
            editor.set_no_delete(self._root.GetPrim(), True)
            self._root.GetPrim().SetCustomDataByKey("viewportLight:overlay", True)
            self._root.CreateVisibilityAttr("invisible")
            self._translation = self._root.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble)
            self._mesh = cast(Any, UsdGeom.Mesh).Define(stage, self._path + "/Ground")
            self._mesh.CreateSubdivisionSchemeAttr("none")
            self._mesh.CreateDoubleSidedAttr(True)
            # Empty bounds prevent editor guides from affecting scene framing.
            self._mesh.CreateExtentAttr([(1, 1, 1), (-1, -1, -1)])
            self._mesh.CreateFaceVertexCountsAttr([4])
            self._mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
            self._mesh.SetNormalsInterpolation("constant")
            material: Any = cast(Any, UsdShade.Material).Define(stage, self._path + "/Material")
            self._shader = cast(Any, UsdShade.Shader).Define(stage, self._path + "/Material/Shader")
            self._shader.SetSourceCode(_SOURCE, "glslfx")
            material.CreateSurfaceOutput("glslfx").ConnectToSource(self._shader.ConnectableAPI(), "surface")
            cast(Any, UsdShade.MaterialBindingAPI).Apply(self._mesh.GetPrim()).Bind(material)
            self._api.usd_context.set_pickable(str(self._mesh.GetPath()), False)

    def set_geometry(
        self, up: int, step: float, center: tuple[int, int], radius: int, color: Color4, level_blend: float = 0.0
    ) -> None:
        """Set world-aligned spacing; camera coverage replaces the legacy radius."""
        key: GeometryKey = (up, step, center, radius, color, level_blend)
        if self._mesh is None or key == self._geometry:
            return
        if up not in (1, 2) or not math.isfinite(step) or step <= 0.0 or radius <= 0:
            raise ValueError("Grid requires Y/Z up, positive finite spacing, and a positive radius")
        axes: tuple[int, int] = (0, 1 if up == 2 else 2)
        normal: Vec3 = (0.0, float(up == 1), float(up == 2))
        dark: bool = color[0] < 0.3
        grey: float = 0.75 if dark else 0.28
        contrast: float = 3.0 if dark else 1.0
        with cast(Any, Usd).EditContext(self._stage, self._layer):
            self._mesh.CreateNormalsAttr([normal])
            level: int
            for level in range(1, 3):
                period: int = 10**level
                self._shader.CreateInput(f"gridPhase{level}", Sdf.ValueTypeNames.Float2).Set(
                    Gf.Vec2f((center[0] % period) / period, (center[1] % period) / period)
                )
            self._shader.CreateInput("gridLevelBlend", Sdf.ValueTypeNames.Float).Set(level_blend)
            self._shader.CreateInput("gridContrast", Sdf.ValueTypeNames.Float).Set(contrast)
            self._shader.CreateInput("gridColor", Sdf.ValueTypeNames.Float3).Set(
                Gf.Vec3f(*srgb_to_linear((grey, grey, grey)))
            )
            self._shader.CreateInput("gridAxisU", Sdf.ValueTypeNames.Float3).Set(
                Gf.Vec3f(*srgb_to_linear(AXIS_COLORS[axes[0]]))
            )
            self._shader.CreateInput("gridAxisV", Sdf.ValueTypeNames.Float3).Set(
                Gf.Vec3f(*srgb_to_linear(AXIS_COLORS[axes[1]]))
            )
        self._geometry = key
        self._refresh_patch()

    def _refresh_patch(self) -> None:
        if self._mesh is None or self._geometry is None:
            return
        up: int = self._geometry[0]
        step: float = self._geometry[1]
        center: tuple[int, int] = self._geometry[2]
        coverage: tuple[Bounds, float] | None = _ground_bounds(
            self._view.GetInverse(), self._projection, up, step, center
        )
        with cast(Any, Usd).EditContext(self._stage, self._layer):
            self._mesh.CreateVisibilityAttr("inherited" if coverage is not None else "invisible")
            if coverage is None:
                return
            bounds: Bounds = coverage[0]
            axes: tuple[int, int] = (0, 1 if up == 2 else 2)
            translation: list[float] = [0.0, 0.0, 0.0]
            translation[axes[0]], translation[axes[1]] = center[0] * step, center[1] * step
            points: list[Vec3] = []
            coordinates: list[tuple[float, float]] = []
            u: float
            v: float
            for u, v in (
                (bounds[0], bounds[2]),
                (bounds[1], bounds[2]),
                (bounds[1], bounds[3]),
                (bounds[0], bounds[3]),
            ):
                point: list[float] = [0.0, 0.0, 0.0]
                point[axes[0]], point[axes[1]] = u, v
                points.append((point[0], point[1], point[2]))
                coordinates.append((u / step, v / step))
            self._translation.Set(Gf.Vec3d(*translation))
            self._mesh.CreatePointsAttr(points)
            primvars: Any = cast(Any, UsdGeom.PrimvarsAPI)(self._mesh)
            primvars.CreatePrimvar("gridCoord", Sdf.ValueTypeNames.Float2Array, "vertex").Set(coordinates)
            enabled: tuple[bool, bool] = (
                bounds[0] <= -center[0] * step <= bounds[1],
                bounds[2] <= -center[1] * step <= bounds[3],
            )
            positions: tuple[float, float] = (
                float(-center[0]) if enabled[0] else 0.0,
                float(-center[1]) if enabled[1] else 0.0,
            )
            self._shader.CreateInput("gridAxisPosition", Sdf.ValueTypeNames.Float2).Set(Gf.Vec2f(*positions))
            self._shader.CreateInput("gridAxisEnabled", Sdf.ValueTypeNames.Float2).Set(
                Gf.Vec2f(float(enabled[0]), float(enabled[1]))
            )
            self._shader.CreateInput("gridFar", Sdf.ValueTypeNames.Float).Set(coverage[1])

    def set_visible(self, visible: bool) -> None:
        if visible == self._visible:
            return
        self._visible = visible
        if self._root is not None:
            with cast(Any, Usd).EditContext(self._stage, self._layer):
                self._root.CreateVisibilityAttr("inherited" if visible else "invisible")

    def destroy(self) -> None:
        if self._stage is not None and self._layer is not None:
            session: Any = self._stage.GetSessionLayer()
            if self._layer.identifier in session.subLayerPaths:
                session.subLayerPaths.remove(self._layer.identifier)
            self._api.usd_context.set_pickable(self._path + "/Ground", True)
        self._stage = None
        self._layer = None
        self._root = None
        self._mesh = None
        self._shader = None
        self._translation = None
        self._path = ""
        self._visible = False
        self._resolution = (0, 0)
        self._geometry = None
        self._view_key = ()
