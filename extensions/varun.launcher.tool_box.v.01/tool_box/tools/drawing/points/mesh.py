"""Planar paint segments: compact quad geometry and analytic brush coverage."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeAlias, cast

import numpy as np
from numpy.typing import NDArray
from pxr import Sdf, UsdGeom, UsdShade, Vt

from tool_box.tools.drawing.brush import BrushSample, BrushShape, minimum_point_distance
from tool_box.tools.drawing.shapes import outline

Vec3: TypeAlias = tuple[float, float, float]
StrokePoint: TypeAlias = tuple[Vec3, BrushSample]
FloatArray: TypeAlias = NDArray[np.float32]
CHUNK_SEGMENTS: int = 128
_SHAPE_IDS: dict[BrushShape, float] = {"circle": 0.0, "square": 1.0, "triangle": 2.0}


def _separated(first: StrokePoint, last: StrokePoint) -> bool:
    return math.dist(first[0], last[0]) >= minimum_point_distance(first[1].radius, last[1].radius)


@lru_cache(maxsize=1)
def _storm_source() -> str:
    return (Path(__file__).parents[4] / "data/shaders/paint.glslfx").read_text(encoding="utf-8")


@lru_cache(maxsize=CHUNK_SEGMENTS + 4)
def _topology(segments: int) -> NDArray[np.int32]:
    offsets: NDArray[np.int32] = np.arange(segments, dtype=np.int32)[:, None] * 4
    return (offsets + np.asarray([0, 1, 2, 0, 2, 3], dtype=np.int32)).ravel()


def _indices(points: FloatArray) -> NDArray[np.int32]:
    """Keep the diagonal inside a concave segment instead of overlapping its triangles."""
    quads: NDArray[np.float64] = points.reshape(-1, 4, 3).astype(np.float64)
    first: NDArray[np.float64] = np.cross(quads[:, 1] - quads[:, 0], quads[:, 2] - quads[:, 0]).astype(
        np.float64, copy=False
    )
    second: NDArray[np.float64] = np.cross(quads[:, 2] - quads[:, 0], quads[:, 3] - quads[:, 0]).astype(
        np.float64, copy=False
    )
    concave: NDArray[np.bool_] = np.sum(first * second, axis=1) < 0.0
    indices: NDArray[np.int32] = _topology(len(quads)).reshape(-1, 6).copy()
    offsets: NDArray[np.int32] = np.flatnonzero(concave).astype(np.int32)[:, None] * 4
    indices[concave] = offsets + np.asarray([1, 2, 3, 1, 3, 0], dtype=np.int32)
    return indices.ravel()


@dataclass(frozen=True)
class SegmentGeometry:
    points: FloatArray
    coordinates: FloatArray
    sides: FloatArray
    dynamics: FloatArray
    modes: FloatArray
    sections: FloatArray


def _sample_data(samples: list[StrokePoint]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    centers: list[Vec3] = []
    values: list[tuple[float, float, float]] = []
    point: StrokePoint
    for point in samples:
        centers.append(point[0])
        values.append((point[1].radius, point[1].hardness, point[1].opacity))
    return np.asarray(centers, dtype=np.float64), np.asarray(values, dtype=np.float64)


def _geometry(
    samples: list[StrokePoint],
    right: Vec3,
    up: Vec3,
    shape: BrushShape,
    before: StrokePoint | None = None,
    after: StrokePoint | None = None,
) -> SegmentGeometry:
    """Join one quad per segment with shared sections and separate endpoint caps."""
    centers: NDArray[np.float64]
    values: NDArray[np.float64]
    centers, values = _sample_data(samples)
    basis: NDArray[np.float64] = np.asarray([right, up], dtype=np.float64)
    profile: NDArray[np.float64] = outline(shape)
    if len(samples) == 1:
        low: NDArray[np.float64] = profile.min(axis=0)
        high: NDArray[np.float64] = profile.max(axis=0)
        coordinates: NDArray[np.float64] = np.asarray(
            [low, (high[0], low[1]), high, (low[0], high[1])], dtype=np.float64
        )
        points: NDArray[np.float64] = centers[0] + coordinates @ basis * values[0, 0]
        return SegmentGeometry(
            points.astype(np.float32),
            coordinates.astype(np.float32),
            np.ones((4, 2), dtype=np.float32),
            np.tile(values[0, 1:], (4, 1)).astype(np.float32),
            np.asarray([4.0 + _SHAPE_IDS[shape]], dtype=np.float32),
            np.empty((0, 2, 3), dtype=np.float32),
        )
    delta: NDArray[np.float64] = np.diff(centers, axis=0) @ basis.T
    first: NDArray[np.float64] = (centers[0] - np.asarray(before[0])) @ basis.T if before is not None else delta[0]
    last: NDArray[np.float64] = (np.asarray(after[0]) - centers[-1]) @ basis.T if after is not None else delta[-1]
    incoming: NDArray[np.float64] = np.concatenate((first[None, :], delta), axis=0)
    outgoing: NDArray[np.float64] = np.concatenate((delta, last[None, :]), axis=0)
    adjacent_length: NDArray[np.float64] = np.minimum(
        np.linalg.norm(incoming, axis=1), np.linalg.norm(outgoing, axis=1)
    )
    incoming /= np.maximum(np.linalg.norm(incoming, axis=1, keepdims=True), 1e-9)
    outgoing /= np.maximum(np.linalg.norm(outgoing, axis=1, keepdims=True), 1e-9)
    tangents: NDArray[np.float64] = incoming + outgoing
    lengths: NDArray[np.float64] = np.linalg.norm(tangents, axis=1, keepdims=True)
    tangents = np.where(lengths > 1e-4, tangents / np.maximum(lengths, 1e-9), outgoing)
    normals: NDArray[np.float64] = np.stack((-tangents[:, 1], tangents[:, 0]), axis=1)
    left: NDArray[np.float64] = np.ones(len(samples)) if shape == "circle" else np.max(normals @ profile.T, axis=1)
    right_side: NDArray[np.float64] = (
        np.ones(len(samples)) if shape == "circle" else np.max(-normals @ profile.T, axis=1)
    )
    # Keep joins within the brush footprint instead of extending a pointed miter.
    widths: NDArray[np.float64] = np.stack((right_side, left), axis=1) * values[:, :1]
    brush_widths: NDArray[np.float64] = widths.copy()
    # Trim the inside of a tight bend before its offset edges fold across each other.
    half_sine: NDArray[np.float64] = np.sqrt(np.maximum(0.0, (1.0 - np.sum(incoming * outgoing, axis=1)) * 0.5))
    inner_limit: NDArray[np.float64] = adjacent_length / np.maximum(2.0 * half_sine, 1e-9)
    turn: NDArray[np.float64] = incoming[:, 0] * outgoing[:, 1] - incoming[:, 1] * outgoing[:, 0]
    widths[:, 1] = np.where(turn > 0.0, np.minimum(widths[:, 1], inner_limit), widths[:, 1])
    widths[:, 0] = np.where(turn < 0.0, np.minimum(widths[:, 0], inner_limit), widths[:, 0])
    directions: NDArray[np.float64] = normals @ basis
    sections: NDArray[np.float64] = np.stack(
        (centers - directions * widths[:, :1], centers + directions * widths[:, 1:]), axis=1
    )
    # Keep abrupt reversals from crossing the two edges of a segment quad.
    reverse: NDArray[np.bool_] = np.sum(normals[:-1] * normals[1:], axis=1) < 0.0
    end_sections: NDArray[np.float64] = sections[1:].copy()
    end_widths: NDArray[np.float64] = widths[1:].copy()
    end_sides: NDArray[np.float64] = brush_widths[1:].copy()
    end_sections[reverse] = end_sections[reverse, ::-1]
    end_widths[reverse] = end_widths[reverse, ::-1]
    end_sides[reverse] = end_sides[reverse, ::-1]
    quad_points: NDArray[np.float64] = np.stack(
        (sections[:-1, 0], end_sections[:, 0], end_sections[:, 1], sections[:-1, 1]), axis=1
    )
    quad_cross: NDArray[np.float64] = np.stack(
        (-widths[:-1, 0], -end_widths[:, 0], end_widths[:, 1], widths[:-1, 1]), axis=1
    )
    quad_coords: NDArray[np.float64] = np.stack((quad_cross, np.zeros_like(quad_cross)), axis=-1)
    quad_sides: NDArray[np.float64] = np.stack((brush_widths[:-1], end_sides, end_sides, brush_widths[:-1]), axis=1)
    quad_dynamics: NDArray[np.float64] = np.stack(
        (values[:-1, 1:], values[1:, 1:], values[1:, 1:], values[:-1, 1:]), axis=1
    )
    modes: list[float] = [0.0] * (len(samples) - 1)
    # Round only the outside sector of tight joins, without layering whole disks.
    tight: NDArray[np.bool_] = inner_limit < np.max(brush_widths, axis=1)
    join_ids: NDArray[np.int64] = np.flatnonzero(tight).astype(np.int64)
    if len(join_ids):
        sign: NDArray[np.float64] = -np.sign(turn[join_ids])[:, None]
        incoming_normal: NDArray[np.float64] = np.stack((-incoming[join_ids, 1], incoming[join_ids, 0]), axis=1) * sign
        outgoing_normal: NDArray[np.float64] = np.stack((-outgoing[join_ids, 1], outgoing[join_ids, 0]), axis=1) * sign
        incoming_support: NDArray[np.float64] = (
            np.ones(len(join_ids)) if shape == "circle" else np.max(incoming_normal @ profile.T, axis=1)
        )
        outgoing_support: NDArray[np.float64] = (
            np.ones(len(join_ids)) if shape == "circle" else np.max(outgoing_normal @ profile.T, axis=1)
        )
        first_outer: NDArray[np.float64] = incoming_normal * incoming_support[:, None]
        last_outer: NDArray[np.float64] = outgoing_normal * outgoing_support[:, None]
        corner: NDArray[np.float64] = (
            normals[join_ids] * sign / np.maximum(np.sum(tangents[join_ids] * outgoing[join_ids], axis=1), 0.5)[:, None]
        )
        corner *= np.maximum(incoming_support, outgoing_support)[:, None]
        outer_column: NDArray[np.int64] = (turn[join_ids] < 0.0).astype(np.int64)
        inner: NDArray[np.float64] = ((sections[join_ids, 1 - outer_column] - centers[join_ids]) @ basis.T) / values[
            join_ids, :1
        ]
        join_coords: NDArray[np.float64] = np.stack((inner, first_outer, corner, last_outer), axis=1)
        join_points: NDArray[np.float64] = (
            centers[join_ids, None, :] + (join_coords @ basis) * values[join_ids, :1, None]
        )
        previous: NDArray[np.bool_] = join_ids > 0
        following: NDArray[np.bool_] = join_ids < len(samples) - 1
        quad_points[join_ids[previous] - 1, 1 + outer_column[previous]] = join_points[previous, 1]
        quad_points[join_ids[following], 3 * outer_column[following]] = join_points[following, 3]
        # The preceding chunk owns the join at its shared last point.
        quad_points = np.concatenate((quad_points, join_points[previous]), axis=0)
        quad_coords = np.concatenate((quad_coords, join_coords[previous]), axis=0)
        quad_sides = np.concatenate((quad_sides, np.ones((int(previous.sum()), 4, 2))), axis=0)
        quad_dynamics = np.concatenate(
            (quad_dynamics, np.repeat(values[join_ids[previous], None, 1:], 4, axis=1)), axis=0
        )
        modes.extend([4.0 + _SHAPE_IDS[shape]] * int(previous.sum()))
    endpoint: int
    outward: float
    is_end: bool
    for endpoint, outward, is_end in ((0, -1.0, before is None), (-1, 1.0, after is None)):
        if not is_end:
            continue
        extension: NDArray[np.float64] = tangents[endpoint] @ basis * values[endpoint, 0] * outward
        cap: NDArray[np.float64] = np.asarray(
            [
                sections[endpoint, 0],
                sections[endpoint, 1],
                sections[endpoint, 1] + extension,
                sections[endpoint, 0] + extension,
            ]
        )
        cap_coords: NDArray[np.float64] = np.asarray(
            [
                (-widths[endpoint, 0], 0.0),
                (widths[endpoint, 1], 0.0),
                (widths[endpoint, 1], 1.0),
                (-widths[endpoint, 0], 1.0),
            ]
        )
        quad_points = np.concatenate((quad_points, cap[None, :, :]), axis=0)
        quad_coords = np.concatenate((quad_coords, cap_coords[None, :, :]), axis=0)
        quad_sides = np.concatenate((quad_sides, np.tile(widths[endpoint], (1, 4, 1))), axis=0)
        quad_dynamics = np.concatenate((quad_dynamics, np.tile(values[endpoint, 1:], (1, 4, 1))), axis=0)
        modes.append(1.0 + _SHAPE_IDS[shape])
    return SegmentGeometry(
        quad_points.reshape(-1, 3).astype(np.float32),
        quad_coords.reshape(-1, 2).astype(np.float32),
        quad_sides.reshape(-1, 2).astype(np.float32),
        quad_dynamics.reshape(-1, 2).astype(np.float32),
        np.asarray(modes, dtype=np.float32),
        sections.astype(np.float32),
    )


class PaintMesh:
    """Smooth a short live tail; finished geometry and its editable samples stay unchanged."""

    def __init__(self, stage: Any, path: str, right: Vec3, up: Vec3, shape: BrushShape) -> None:
        self._stage: Any = stage
        self._path: str = path
        self._right: Vec3 = right
        self._up: Vec3 = up
        self._shape: BrushShape = shape
        self._points: list[StrokePoint] = []
        self._frozen: list[tuple[int, list[StrokePoint], StrokePoint | None, StrokePoint | None]] = []
        self._before: StrokePoint | None = None
        self._chunk: int = 0
        self._anchor: StrokePoint | None = None
        self._control: StrokePoint | None = None
        self._dirty: bool = False

    def add(self, position: Vec3, sample: BrushSample) -> bool:
        point: StrokePoint = (position, sample)
        if self._anchor is None:
            self._anchor = point
            self._append(point)
            return True
        if not _separated(self._control or self._anchor, point):
            return False
        if self._control is None:
            self._control = point
            self._dirty = True
            return True
        a: NDArray[np.float64] = np.asarray(self._anchor[0], dtype=np.float64)
        b: NDArray[np.float64] = np.asarray(self._control[0], dtype=np.float64)
        c: NDArray[np.float64] = np.asarray(position, dtype=np.float64)
        incoming: NDArray[np.float64] = b - a
        outgoing: NDArray[np.float64] = c - b
        if float(np.dot(incoming, outgoing)) < -0.5 * float(np.linalg.norm(incoming) * np.linalg.norm(outgoing)):
            # Close a reversal with round ends instead of folding one ribbon through itself.
            if _separated(self._points[-1], self._control):
                self._points.append(self._control)
            elif len(self._points) > 1:
                self._points[-1] = self._control
            self._frozen.append((self._chunk, self._points, self._before, None))
            self._chunk += 1
            self._before = None
            self._points = [self._control]
            self._anchor = self._control
            self._control = point
            self._dirty = True
            return True
        end: NDArray[np.float64] = (b + c) * 0.5
        end_sample: BrushSample = self._control[1].blend(sample, 0.5)
        tolerance: float = max(0.001, min(self._anchor[1].radius, self._control[1].radius, sample.radius) * 0.02)
        bend: float = float(np.linalg.norm(a - 2.0 * b + end))
        steps: int = min(32, max(1, math.ceil(math.sqrt(bend / (4.0 * tolerance)))))
        step: int
        for step in range(1, steps + 1):
            t: float = step / steps
            center: NDArray[np.float64] = (1.0 - t) ** 2 * a + 2.0 * t * (1.0 - t) * b + t * t * end
            dynamics: BrushSample = (
                self._anchor[1].blend(self._control[1], t).blend(self._control[1].blend(end_sample, t), t)
            )
            self._append(((float(center[0]), float(center[1]), float(center[2])), dynamics))
        self._anchor = ((float(end[0]), float(end[1]), float(end[2])), end_sample)
        self._control = point
        self._dirty = True
        return True

    def _append(self, point: StrokePoint) -> None:
        if self._points and not _separated(self._points[-1], point):
            return
        self._points.append(point)
        self._dirty = True
        # Freeze only after the following point fixes the shared seam direction.
        if len(self._points) == CHUNK_SEGMENTS + 2:
            completed: list[StrokePoint] = self._points[:-1]
            self._frozen.append((self._chunk, completed, self._before, point))
            self._before = completed[-2]
            self._chunk += 1
            self._points = [completed[-1], point]

    def flush(self) -> None:
        if not self._dirty:
            return
        cast(Any, UsdGeom.Xform).Define(self._stage, self._path)
        index: int
        samples: list[StrokePoint]
        before: StrokePoint | None
        after: StrokePoint | None
        for index, samples, before, after in self._frozen:
            self._write(index, samples, before, after)
        self._frozen.clear()
        tail: list[StrokePoint] = self._points.copy()
        if self._control is not None and (not tail or _separated(tail[-1], self._control)):
            tail.append(self._control)
        if tail:
            self._write(self._chunk, tail, self._before)
        self._dirty = False

    def _write(
        self,
        index: int,
        samples: list[StrokePoint],
        before: StrokePoint | None = None,
        after: StrokePoint | None = None,
    ) -> None:
        geometry: SegmentGeometry = _geometry(samples, self._right, self._up, self._shape, before, after)
        segments: int = len(geometry.modes)
        mesh: Any = cast(Any, UsdGeom.Mesh).Define(self._stage, f"{self._path}/Paint_{index:04d}")
        mesh.CreateSubdivisionSchemeAttr().Set("none")
        mesh.CreateDoubleSidedAttr().Set(True)
        mesh.CreatePointsAttr().Set(cast(Any, Vt).Vec3fArray.FromNumpy(geometry.points))
        mesh.CreateFaceVertexCountsAttr().Set([3] * (segments * 2))
        mesh.CreateFaceVertexIndicesAttr().Set(cast(Any, Vt).IntArray.FromNumpy(_indices(geometry.points)))
        mesh.CreateDisplayColorAttr().Set([(0.0, 0.0, 0.0)])
        primvars: Any = cast(Any, UsdGeom.PrimvarsAPI)(mesh)
        name: str
        values: FloatArray
        for name, values in (
            ("paintCoord", geometry.coordinates),
            ("paintSides", geometry.sides),
            ("paintDynamics", geometry.dynamics),
        ):
            primvars.CreatePrimvar(name, Sdf.ValueTypeNames.Float2Array, "vertex").Set(
                cast(Any, Vt).Vec2fArray.FromNumpy(values)
            )
        primvars.CreatePrimvar("paintMode", Sdf.ValueTypeNames.FloatArray, "uniform").Set(
            cast(Any, Vt).FloatArray.FromNumpy(np.repeat(geometry.modes, 2))
        )
        mesh.CreateExtentAttr().Set(
            cast(Any, Vt).Vec3fArray.FromNumpy(
                np.asarray([geometry.points.min(axis=0), geometry.points.max(axis=0)], dtype=np.float32)
            )
        )
        self._write_source(mesh.GetPrim(), samples)
        cast(Any, UsdShade.MaterialBindingAPI).Apply(mesh.GetPrim()).Bind(self._material())

    def _write_source(self, prim: Any, samples: list[StrokePoint]) -> None:
        """Keep the editable centerline beside each bounded rendering cache."""
        positions: NDArray[np.float64]
        values: NDArray[np.float64]
        positions, values = _sample_data(samples)
        centers: FloatArray = positions.astype(np.float32)
        dynamics: FloatArray = values.astype(np.float32)
        prim.CreateAttribute("paint:centerline", Sdf.ValueTypeNames.Point3fArray, custom=True).Set(
            cast(Any, Vt).Vec3fArray.FromNumpy(centers)
        )
        name: str
        column: int
        for name, column in (("radius", 0), ("hardness", 1), ("opacity", 2)):
            prim.CreateAttribute(f"paint:{name}", Sdf.ValueTypeNames.FloatArray, custom=True).Set(
                cast(Any, Vt).FloatArray.FromNumpy(dynamics[:, column].copy())
            )
        prim.CreateAttribute("paint:shape", Sdf.ValueTypeNames.Token, custom=True).Set(self._shape)
        prim.CreateAttribute("paint:right", Sdf.ValueTypeNames.Float3, custom=True).Set(self._right)
        prim.CreateAttribute("paint:up", Sdf.ValueTypeNames.Float3, custom=True).Set(self._up)

    def _material(self) -> Any:
        return ensure_paint_material(self._stage)


def ensure_paint_material(stage: Any) -> Any:
    """Refresh embedded local shader code while keeping saved stroke geometry."""
    path: str = "/World/ThreeDDraw/_PaintRibbonMaterial"
    material: Any = cast(Any, UsdShade.Material).Get(stage, path)
    source: str = _storm_source()
    existing: Any = cast(Any, UsdShade.Shader).Get(stage, path + "/Storm")
    if material and existing and existing.GetSourceCode("glslfx") == source and existing.GetInput("paintPoint0"):
        return material
    material = cast(Any, UsdShade.Material).Define(stage, path)
    shader: Any = cast(Any, UsdShade.Shader).Define(stage, path + "/Storm")
    shader.SetSourceCode(source, "glslfx")
    name: str
    size: int
    inputs: dict[str, int] = {
        "paintCoord": 2,
        "paintSides": 2,
        "paintDynamics": 2,
        "paintMode": 1,
        "paintPosition": 2,
        "paintVersion": 2,
        "paintHardness": 4,
        "paintPoint0": 4,
        "paintPoint1": 4,
        "paintPoint2": 4,
        "paintPoint3": 4,
    }
    for name, size in inputs.items():
        suffix: str = "" if size == 1 else str(size)
        reader: Any = cast(Any, UsdShade.Shader).Define(stage, f"{path}/{name}")
        reader.CreateIdAttr("UsdPrimvarReader_float" + suffix)
        reader.CreateInput("varname", Sdf.ValueTypeNames.String).Set(name)
        value_type: Any = getattr(Sdf.ValueTypeNames, "Float" + suffix)
        reader.CreateInput("fallback", value_type).Set(0.0 if size == 1 else (0.0,) * size)
        shader.CreateInput(name, value_type).ConnectToSource(reader.ConnectableAPI(), "result")
    material.CreateSurfaceOutput("glslfx").ConnectToSource(shader.ConnectableAPI(), "surface")
    return material
