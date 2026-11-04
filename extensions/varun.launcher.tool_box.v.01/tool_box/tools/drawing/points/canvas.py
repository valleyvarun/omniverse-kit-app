"""Project the existing planar paint mesh onto a local USD object canvas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias, cast

import numpy as np
from numpy.typing import NDArray
from pxr import Sdf, UsdGeom, Vt

from tool_box.tools.drawing.brush import BrushSample, BrushShape
from tool_box.tools.drawing.canvas import get_surfaces
from tool_box.tools.drawing.points.mesh import PaintMesh, StrokePoint, Vec3
from tool_box.tools.drawing.surface import Collider

FloatArray: TypeAlias = NDArray[np.float64]
IndexArray: TypeAlias = NDArray[np.int32]
CANVAS_PATH: str = "paint:canvasPath"


@dataclass
class CanvasGeometry:
    points: FloatArray
    indices: IndexArray
    vertex: dict[str, FloatArray]
    uniform: dict[str, FloatArray]


class PaintCanvas:
    def __init__(self, collider: Collider, origin: Vec3, normal: Vec3, offset: float) -> None:
        self.collider: Collider = collider
        self.origin: FloatArray = np.asarray(origin, dtype=np.float64)
        self.normal: FloatArray = np.asarray(normal, dtype=np.float64)
        self.normal /= max(float(np.linalg.norm(self.normal)), 1e-12)
        self.offset: float = offset

    @classmethod
    def create(cls, stage: Any, path: str, origin: Vec3, normal: Vec3, radius: float) -> PaintCanvas | None:
        collider: Collider | None = get_surfaces(stage=stage).get(path)
        return cls(collider, origin, normal, max(1e-5, radius * 0.002)) if collider is not None else None

    @classmethod
    def read(cls, stage: Any, prim: Any) -> PaintCanvas | None:
        path: Any = prim.GetAttribute(CANVAS_PATH).Get()
        if not path:
            return None
        collider: Collider | None = get_surfaces(stage=stage).get(str(path))
        if collider is None:
            return None
        origin: Any = prim.GetAttribute("paint:canvasOrigin").Get()
        normal: Any = prim.GetAttribute("paint:canvasNormal").Get()
        return cls(
            collider,
            (float(origin[0]), float(origin[1]), float(origin[2])),
            (float(normal[0]), float(normal[1]), float(normal[2])),
            float(prim.GetAttribute("paint:canvasOffset").Get()),
        )

    def write(self, prim: Any) -> None:
        prim.CreateAttribute(CANVAS_PATH, Sdf.ValueTypeNames.String, custom=True).Set(self.collider.path)
        prim.CreateAttribute("paint:canvasOrigin", Sdf.ValueTypeNames.Double3, custom=True).Set(tuple(self.origin))
        prim.CreateAttribute("paint:canvasNormal", Sdf.ValueTypeNames.Double3, custom=True).Set(tuple(self.normal))
        prim.CreateAttribute("paint:canvasOffset", Sdf.ValueTypeNames.Double, custom=True).Set(self.offset)

    def planar(self, position: Vec3) -> Vec3:
        point: FloatArray = np.asarray(position, dtype=np.float64)
        point -= self.normal * float(np.dot(point - self.origin, self.normal))
        return float(point[0]), float(point[1]), float(point[2])

    @staticmethod
    def capture(mesh: Any) -> CanvasGeometry:
        """Copy USD arrays on the app thread before numerical projection."""
        points: FloatArray = np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float64)
        indices: IndexArray = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(), dtype=np.int32).reshape(-1, 3)
        primvars: Any = cast(Any, UsdGeom.PrimvarsAPI)(mesh)
        vertex: dict[str, FloatArray] = {}
        uniform: dict[str, FloatArray] = {}
        variable: Any
        for variable in primvars.GetPrimvars():
            interpolation: str = str(variable.GetInterpolation())
            if interpolation in ("vertex", "uniform"):
                values: FloatArray = np.asarray(variable.Get(), dtype=np.float64)
                (vertex if interpolation == "vertex" else uniform)[str(variable.GetPrimvarName())] = values
        return CanvasGeometry(points, indices, vertex, uniform)

    def project_data(self, data: CanvasGeometry, refine: bool = False) -> CanvasGeometry:
        """Pure array work; finished strokes can run this on the repair worker."""
        points: FloatArray = data.points
        indices: IndexArray = data.indices
        vertex: dict[str, FloatArray] = data.vertex
        uniform: dict[str, FloatArray] = data.uniform
        name: str
        values: FloatArray
        # A bounded preview subdivision follows curved surfaces between stroke centers.
        level: int
        for level in range(2 if refine else 0):
            corners: FloatArray = points[indices]
            count: int = len(corners)
            weights: FloatArray = np.asarray(
                ((1, 0, 0), (0, 1, 0), (0, 0, 1), (0.5, 0.5, 0), (0, 0.5, 0.5), (0.5, 0, 0.5)),
                dtype=np.float64,
            )
            points = np.einsum("ij,njk->nik", weights, corners).reshape(-1, 3)
            for name, values in vertex.items():
                vertex[name] = np.einsum("ij,njk->nik", weights, values[indices]).reshape(-1, values.shape[-1])
            for name, values in uniform.items():
                uniform[name] = np.repeat(values, 4, axis=0)
            indices = (
                np.arange(count, dtype=np.int32)[:, None, None] * 6
                + np.asarray(((0, 3, 5), (3, 1, 4), (5, 4, 2), (3, 4, 5)), dtype=np.int32)
            ).reshape(-1, 3)
        low: FloatArray = np.asarray(self.collider.lo, dtype=np.float64)
        high: FloatArray = np.asarray(self.collider.hi, dtype=np.float64)
        far: float = float(np.dot(np.where(self.normal >= 0, high, low), self.normal))
        far += max(1e-3, float(np.linalg.norm(high - low)) * 0.01)
        origins: FloatArray = points + (far - points @ self.normal)[:, None] * self.normal
        projected: FloatArray
        _normals: FloatArray
        valid: NDArray[np.bool_]
        projected, _normals, valid = self.collider.raycast_many(origins, -self.normal)
        faces: NDArray[np.bool_] = np.all(valid[indices], axis=1)
        indices = indices[faces]
        # Unreferenced misses stay finite; only hit triangles are rendered.
        projected[~valid] = points[~valid]
        projected[valid] += self.normal * self.offset
        for name, values in uniform.items():
            uniform[name] = values[faces]
        return CanvasGeometry(projected, indices, vertex, uniform)

    @staticmethod
    def apply(mesh: Any, data: CanvasGeometry) -> bool:
        """Keep shader coordinates planar while geometry follows the object."""
        mesh.GetPointsAttr().Set(cast(Any, Vt).Vec3fArray.FromNumpy(data.points.astype(np.float32)))
        mesh.GetFaceVertexIndicesAttr().Set(cast(Any, Vt).IntArray.FromNumpy(data.indices.ravel()))
        mesh.GetFaceVertexCountsAttr().Set([3] * len(data.indices))
        primvars: Any = cast(Any, UsdGeom.PrimvarsAPI)(mesh)
        name: str
        values: FloatArray
        for name, values in data.vertex.items():
            primvars.GetPrimvar(name).Set(cast(Any, Vt).Vec2fArray.FromNumpy(values.astype(np.float32)))
        for name, values in data.uniform.items():
            primvars.GetPrimvar(name).Set(cast(Any, Vt).FloatArray.FromNumpy(values.astype(np.float32)))
        if len(data.indices):
            referenced: FloatArray = data.points[np.unique(data.indices)]
            mesh.GetExtentAttr().Set(
                cast(Any, Vt).Vec3fArray.FromNumpy(
                    np.asarray([referenced.min(axis=0), referenced.max(axis=0)], dtype=np.float32)
                )
            )
        return bool(len(data.indices))

    def project(self, mesh: Any, refine: bool = False) -> bool:
        return self.apply(mesh, self.project_data(self.capture(mesh), refine))


class SurfacePaintMesh(PaintMesh):
    def __init__(self, stage: Any, path: str, right: Vec3, up: Vec3, shape: BrushShape, canvas: PaintCanvas) -> None:
        super().__init__(stage, path, right, up, shape)
        self.canvas: PaintCanvas = canvas

    def add(self, position: Vec3, sample: BrushSample) -> bool:
        return super().add(self.canvas.planar(position), sample)

    def _write(
        self,
        index: int,
        samples: list[StrokePoint],
        before: StrokePoint | None = None,
        after: StrokePoint | None = None,
    ) -> None:
        super()._write(index, samples, before, after)
        self.canvas.write(self._stage.GetPrimAtPath(self._path))
        mesh: Any = cast(Any, UsdGeom.Mesh).Get(self._stage, f"{self._path}/Paint_{index:04d}")
        self.canvas.project(mesh, refine=True)
