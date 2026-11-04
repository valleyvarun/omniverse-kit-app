"""World-depth ruler geometry with a faint foreground pass for hidden portions."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias, TypedDict, cast

from omni.usd import editor
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade

from ..axis_colors import srgb_to_linear
from .settings import RULER_COLOR

Vec3: TypeAlias = tuple[float, float, float]
Vec2: TypeAlias = tuple[float, float]
Rect: TypeAlias = tuple[float, float, float, float]


class FontMetrics(TypedDict):
    characters: str
    cell_width: int
    cell_height: int
    font_size: int
    advances: dict[str, float]


_DIRECTORY: Path = Path(__file__).parent
_SOURCE: str = (_DIRECTORY / "ruler_depth.glslfx").read_text(encoding="utf-8")
_MARKER_SOURCE: str = (_DIRECTORY / "ruler_marker.glslfx").read_text(encoding="utf-8")
_FONT: FontMetrics = cast(FontMetrics, json.loads((_DIRECTORY / "ruler_font.json").read_text(encoding="utf-8-sig")))
_CHARACTERS: str = _FONT["characters"]
_ADVANCES: dict[str, float] = _FONT["advances"]
OCCLUDED_OPACITY: float = 0.16
MARKER_RADIUS_PIXELS: float = 4.5


@dataclass(frozen=True)
class RulerLine:
    start: Vec3
    end: Vec3
    color: Vec3
    thickness: float


@dataclass(frozen=True)
class RulerLabel:
    position: Vec3
    text: str
    color: Vec3
    size: float
    axis: int


class DepthRuler:
    """Keep guides out of saved assets, picking, and drawing-surface queries."""

    def __init__(self, viewport_api: Any) -> None:
        self._api: Any = viewport_api
        self._stage: Any = None
        self._layer: Any = None
        self._mesh: Any = None
        self._root: Any = None
        self._marker_root: Any = None
        self._marker_sphere: Any = None
        self._marker_translation: Any = None
        self._marker_position: Vec3 | None = None
        self._marker_key: tuple[Vec3, float] | None = None
        self._shaders: list[Any] = []
        self._path: str = ""
        self._resolution: tuple[float, float] = (0.0, 0.0)
        self._visible: bool = False
        self.lines: list[RulerLine] = []
        self.labels: list[RulerLabel] = []
        self.drawn_labels: list[RulerLabel] = []
        self._view: Gf.Matrix4d = Gf.Matrix4d(1.0)
        self._projection: Gf.Matrix4d = Gf.Matrix4d(1.0)
        self._view_key: tuple[float, ...] = ()

    def update(self, view: Gf.Matrix4d | None = None, projection: Gf.Matrix4d | None = None) -> bool:
        stage: Any = self._api.stage
        changed: bool = stage != self._stage
        if changed:
            self.destroy()
            if stage is not None:
                self._attach(stage)
        if self._stage is not None:
            # Keep label culling, marker size and later geometry writes on one frame.
            self._view = Gf.Matrix4d(view if view is not None else self._api.view)
            self._projection = Gf.Matrix4d(projection if projection is not None else self._api.projection)
            resolution: tuple[float, float] = (float(self._api.resolution[0]), float(self._api.resolution[1]))
            if resolution != self._resolution:
                with cast(Any, Usd).EditContext(self._stage, self._layer):
                    shader: Any
                    for shader in self._shaders:
                        shader.CreateInput("rulerResolution", Sdf.ValueTypeNames.Float2).Set(Gf.Vec2f(*resolution))
                self._resolution = resolution
            values: list[float] = list(resolution)
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
                self._refresh_labels()
                self._refresh_marker()
        return changed

    def _attach(self, stage: Any) -> None:
        self._stage = stage
        self._path = "/ViewportLight_Ruler"
        while stage.GetPrimAtPath(self._path):
            self._path += "_"
        self._layer = cast(Any, Sdf.Layer).CreateAnonymous("viewport_light_ruler.usda")
        stage.GetSessionLayer().subLayerPaths.insert(0, self._layer.identifier)
        with cast(Any, Usd).EditContext(stage, self._layer):
            self._root = cast(Any, UsdGeom.Scope).Define(stage, self._path)
            editor.set_hide_in_stage_window(self._root.GetPrim(), True)
            editor.set_no_delete(self._root.GetPrim(), True)
            self._root.GetPrim().SetCustomDataByKey("viewportLight:overlay", True)
            self._root.CreateVisibilityAttr("invisible")
            self._mesh = cast(Any, UsdGeom.Mesh).Define(stage, self._path + "/Visible")
            self._mesh.CreateSubdivisionSchemeAttr("none")
            self._mesh.CreateDoubleSidedAttr(True)
            self._mesh.CreateExtentAttr([(1, 1, 1), (-1, -1, -1)])
            ghost: Any = cast(Any, UsdGeom.Mesh).Define(stage, self._path + "/Occluded")
            ghost.GetPrim().GetReferences().AddInternalReference(self._path + "/Visible")
            name: str
            mesh: Any
            opacity: float
            ghost_value: float
            for name, mesh, opacity, ghost_value in (
                ("Depth", self._mesh, 1.0, 0.0),
                ("Faint", ghost, OCCLUDED_OPACITY, 1.0),
            ):
                material: Any = self._material(name, opacity, ghost_value)
                cast(Any, UsdShade.MaterialBindingAPI).Apply(mesh.GetPrim()).Bind(material)
                self._api.usd_context.set_pickable(str(mesh.GetPath()), False)
            self._attach_marker()

    def _attach_marker(self) -> None:
        """Create both marker passes under the existing editor-only ruler root."""
        path: str = self._path + "/Marker"
        self._marker_root = cast(Any, UsdGeom.Xform).Define(self._stage, path)
        self._marker_root.CreateVisibilityAttr("invisible")
        self._marker_translation = self._marker_root.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble)
        self._marker_sphere = cast(Any, UsdGeom.Sphere).Define(self._stage, path + "/Visible")
        self._marker_sphere.CreateExtentAttr([(1, 1, 1), (-1, -1, -1)])
        self._marker_sphere.CreateDoubleSidedAttr(False)
        ghost: Any = cast(Any, UsdGeom.Sphere).Define(self._stage, path + "/Occluded")
        ghost.GetPrim().GetReferences().AddInternalReference(path + "/Visible")
        name: str
        sphere: Any
        opacity: float
        ghost_value: float
        for name, sphere, opacity, ghost_value in (
            ("MarkerDepth", self._marker_sphere, 1.0, 0.0),
            ("MarkerFaint", ghost, OCCLUDED_OPACITY, 1.0),
        ):
            material: Any = cast(Any, UsdShade.Material).Define(self._stage, self._path + "/" + name + "Material")
            shader: Any = cast(Any, UsdShade.Shader).Define(self._stage, str(material.GetPath()) + "/Shader")
            shader.SetSourceCode(_MARKER_SOURCE, "glslfx")
            shader.CreateInput("markerColor", Sdf.ValueTypeNames.Float3).Set(Gf.Vec3f(*srgb_to_linear(RULER_COLOR[:3])))
            shader.CreateInput("markerOpacity", Sdf.ValueTypeNames.Float).Set(opacity)
            shader.CreateInput("markerGhost", Sdf.ValueTypeNames.Float).Set(ghost_value)
            material.CreateSurfaceOutput("glslfx").ConnectToSource(shader.ConnectableAPI(), "surface")
            cast(Any, UsdShade.MaterialBindingAPI).Apply(sphere.GetPrim()).Bind(material)
            self._api.usd_context.set_pickable(str(sphere.GetPath()), False)

    def _material(self, name: str, opacity: float, ghost: float) -> Any:
        path: str = self._path + "/" + name + "Material"
        material: Any = cast(Any, UsdShade.Material).Define(self._stage, path)
        shader: Any = cast(Any, UsdShade.Shader).Define(self._stage, path + "/Shader")
        shader.SetSourceCode(_SOURCE, "glslfx")
        shader.CreateInput("rulerGhost", Sdf.ValueTypeNames.Float).Set(ghost)
        shader.CreateInput("rulerOpacity", Sdf.ValueTypeNames.Float).Set(opacity)
        uv_reader: Any = cast(Any, UsdShade.Shader).Define(self._stage, path + "/UV")
        uv_reader.CreateIdAttr("UsdPrimvarReader_float2")
        uv_reader.CreateInput("varname", Sdf.ValueTypeNames.String).Set("rulerUv")
        texture: Any = cast(Any, UsdShade.Shader).Define(self._stage, path + "/Font")
        texture.CreateIdAttr("UsdUVTexture")
        texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath(str(_DIRECTORY / "ruler_font.png")))
        texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("raw")
        texture.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set("clamp")
        texture.CreateInput("wrapT", Sdf.ValueTypeNames.Token).Set("clamp")
        texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(uv_reader.ConnectableAPI(), "result")
        shader.CreateInput("rulerMask", Sdf.ValueTypeNames.Float).ConnectToSource(texture.ConnectableAPI(), "a")
        material.CreateSurfaceOutput("glslfx").ConnectToSource(shader.ConnectableAPI(), "surface")
        self._shaders.append(shader)
        return material

    def set_visible(self, visible: bool) -> None:
        if visible == self._visible:
            return
        self._visible = visible
        if self._root is not None:
            with cast(Any, Usd).EditContext(self._stage, self._layer):
                self._root.CreateVisibilityAttr("inherited" if visible else "invisible")

    def set_geometry(self, lines: list[RulerLine], labels: list[RulerLabel], marker: Vec3 | None = None) -> None:
        self.lines, self.labels = lines, labels
        self._marker_position = marker
        self._refresh_labels(force=True)
        self._refresh_marker()

    def _refresh_marker(self) -> None:
        """Keep the real sphere approximately nine pixels across during navigation."""
        if self._marker_root is None:
            return
        position: Vec3 | None = self._marker_position
        radius: float = 0.0
        if position is not None:
            projection: Gf.Matrix4d = self._projection
            clip: Gf.Vec4d = Gf.Vec4d(*position, 1.0) * self._view * projection
            projected_scale: float = min(
                self._resolution[0] * abs(float(projection[0][0])),
                self._resolution[1] * abs(float(projection[1][1])),
            )
            if clip[3] > 1e-8 and abs(clip[2]) <= clip[3] and projected_scale > 1e-8:
                radius = MARKER_RADIUS_PIXELS * 2.0 * float(clip[3]) / projected_scale
        with cast(Any, Usd).EditContext(self._stage, self._layer):
            if position is None or not math.isfinite(radius) or radius <= 0.0:
                if self._marker_key is not None:
                    self._marker_root.CreateVisibilityAttr("invisible")
                    self._marker_key = None
                return
            key: tuple[Vec3, float] = (position, radius)
            if key != self._marker_key:
                self._marker_translation.Set(Gf.Vec3d(*position))
                self._marker_sphere.CreateRadiusAttr(radius)
                self._marker_root.CreateVisibilityAttr("inherited")
                self._marker_key = key

    def _refresh_labels(self, force: bool = False) -> None:
        """Keep projected labels readable without changing their world values."""
        if self._mesh is None:
            return
        selected: list[RulerLabel] = []
        occupied: list[Rect] = []
        view: Gf.Matrix4d = self._view
        world_to_clip: Gf.Matrix4d = view * self._projection
        width: float
        height: float
        width, height = self._resolution
        candidates: list[tuple[float, int, Rect]] = []
        index: int
        label: RulerLabel
        for index, label in enumerate(self.labels):
            direction: Gf.Vec3d = Gf.Vec3d(0.0)
            direction[label.axis] = 1.0
            direction = view.TransformDir(direction)
            if math.hypot(direction[0], direction[1]) < 0.02:
                continue
            clip: Gf.Vec4d = Gf.Vec4d(*label.position, 1.0) * world_to_clip
            if clip[3] <= 1e-8 or abs(clip[2]) > clip[3]:
                continue
            x: float = (float(clip[0] / clip[3]) + 1.0) * width * 0.5 + 4.0
            y: float = (1.0 - float(clip[1] / clip[3])) * height * 0.5 - 4.0
            scale: float = label.size / float(_FONT["font_size"])
            text_width: float = 0.0
            character: str
            for character in label.text:
                text_width += _ADVANCES.get(character, 0.0) * scale
            bounds: Rect = (x, y - float(_FONT["cell_height"]) * scale, x + text_width, y)
            if bounds[0] < 2.0 or bounds[1] < 2.0 or bounds[2] > width - 2.0 or bounds[3] > height - 2.0:
                continue
            distance: float = (x - width * 0.5) ** 2 + (y - height * 0.5) ** 2
            candidates.append((distance, index, bounds))
        candidates.sort()
        chosen: set[int] = set()
        distance: float
        bounds: Rect
        for distance, index, bounds in candidates:
            other: Rect
            overlaps: bool = False
            for other in occupied:
                if (
                    bounds[0] < other[2] + 6.0
                    and bounds[2] + 6.0 > other[0]
                    and bounds[1] < other[3] + 6.0
                    and bounds[3] + 6.0 > other[1]
                ):
                    overlaps = True
                    break
            if not overlaps:
                chosen.add(index)
                occupied.append(bounds)
        for index, label in enumerate(self.labels):
            if index in chosen:
                selected.append(label)
        if force or selected != self.drawn_labels:
            self.drawn_labels = selected
            self._write_geometry()

    def _write_geometry(self) -> None:
        if self._mesh is None:
            return
        points: list[Vec3] = []
        others: list[Vec3] = []
        offsets: list[Vec2] = []
        colors: list[Vec3] = []
        kinds: list[float] = []
        thicknesses: list[float] = []
        starts: list[Vec3] = []
        ends: list[Vec3] = []
        uvs: list[Vec2] = []
        line: RulerLine
        for line in self.lines:
            points.extend((line.start, line.start, line.end, line.end))
            others.extend((line.end, line.end, line.start, line.start))
            expanded: float = line.thickness + 2.0
            offsets.extend(((expanded, -1.0), (expanded, 1.0), (expanded, -1.0), (expanded, 1.0)))
            colors.append(srgb_to_linear(line.color))
            kinds.append(0.0)
            thicknesses.append(line.thickness)
            starts.append(line.start)
            ends.append(line.end)
            uvs.extend([(0.0, 0.0)] * 4)
        label: RulerLabel
        for label in self.drawn_labels:
            scale: float = label.size / float(_FONT["font_size"])
            width: float = float(_FONT["cell_width"]) * scale
            height: float = float(_FONT["cell_height"]) * scale
            left: float = 4.0
            direction: Vec3 = (float(label.axis == 0), float(label.axis == 1), float(label.axis == 2))
            character: str
            for character in label.text:
                index: int = _CHARACTERS.find(character)
                if index < 0:
                    continue
                low: float = index / len(_CHARACTERS)
                high: float = (index + 1) / len(_CHARACTERS)
                points.extend([label.position] * 4)
                others.extend([direction] * 4)
                offsets.extend(((left, 4.0), (left + width, 4.0), (left + width, 4.0 + height), (left, 4.0 + height)))
                colors.append(srgb_to_linear(label.color))
                kinds.append(1.0)
                thicknesses.append(0.0)
                starts.append(label.position)
                ends.append(label.position)
                uvs.extend(((low, 0.0), (high, 0.0), (high, 1.0), (low, 1.0)))
                left += _ADVANCES[character] * scale
        with cast(Any, Usd).EditContext(self._stage, self._layer):
            self._mesh.CreatePointsAttr(points)
            self._mesh.CreateFaceVertexCountsAttr([4] * (len(points) // 4))
            self._mesh.CreateFaceVertexIndicesAttr(list(range(len(points))))
            self._mesh.CreateNormalsAttr([(0.0, 0.0, 1.0)])
            self._mesh.SetNormalsInterpolation("constant")
            primvars: Any = cast(Any, UsdGeom.PrimvarsAPI)(self._mesh)
            primvars.CreatePrimvar("rulerColor", Sdf.ValueTypeNames.Float3Array, "uniform").Set(colors)
            primvars.CreatePrimvar("rulerThickness", Sdf.ValueTypeNames.FloatArray, "uniform").Set(thicknesses)
            primvars.CreatePrimvar("rulerStart", Sdf.ValueTypeNames.Float3Array, "uniform").Set(starts)
            primvars.CreatePrimvar("rulerEnd", Sdf.ValueTypeNames.Float3Array, "uniform").Set(ends)
            primvars.CreatePrimvar("rulerOther", Sdf.ValueTypeNames.Float3Array, "vertex").Set(others)
            primvars.CreatePrimvar("rulerOffset", Sdf.ValueTypeNames.Float2Array, "vertex").Set(offsets)
            primvars.CreatePrimvar("rulerKind", Sdf.ValueTypeNames.FloatArray, "uniform").Set(kinds)
            primvars.CreatePrimvar("rulerUv", Sdf.ValueTypeNames.Float2Array, "vertex").Set(uvs)

    def destroy(self) -> None:
        if self._stage is not None and self._layer is not None:
            session: Any = self._stage.GetSessionLayer()
            if self._layer.identifier in session.subLayerPaths:
                session.subLayerPaths.remove(self._layer.identifier)
            name: str
            for name in ("Visible", "Occluded", "Marker/Visible", "Marker/Occluded"):
                self._api.usd_context.set_pickable(self._path + "/" + name, True)
        self._stage = None
        self._layer = None
        self._mesh = None
        self._root = None
        self._marker_root = None
        self._marker_sphere = None
        self._marker_translation = None
        self._marker_position = None
        self._marker_key = None
        self._shaders.clear()
        self._path = ""
        self._visible = False
        self._resolution = (0.0, 0.0)
        self._view = Gf.Matrix4d(1.0)
        self._projection = Gf.Matrix4d(1.0)
        self._view_key = ()
        self.lines.clear()
        self.labels.clear()
        self.drawn_labels.clear()
