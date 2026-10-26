"""Incremental round pipes for both Storm and RTX viewports."""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, TypeAlias, cast

import numpy as np
from numpy.typing import NDArray
from pxr import UsdGeom, Vt

from tool_box.tools.drawing.brush import BrushShape, minimum_point_distance
from tool_box.tools.drawing.shapes import outline

Vec3: TypeAlias = tuple[float, float, float]
FloatArray: TypeAlias = NDArray[np.float64]
Ring: TypeAlias = tuple[FloatArray, FloatArray]
SIDES: int = 24
CHUNK_RINGS: int = 128
CAP_RINGS: int = 5


@lru_cache(maxsize=256)
def _topology(
    rings: int, start_cap: bool, end_cap: bool, sides: int = SIDES
) -> tuple[NDArray[np.int32], NDArray[np.int32]]:
    """Reuse body quads and hemispherical ends with a single tip vertex."""
    first: NDArray[np.int32] = np.arange((rings - 1) * sides, dtype=np.int32)
    next_side: NDArray[np.int32] = first // sides * sides + (first + 1) % sides
    indices: NDArray[np.int32] = np.stack((first, next_side, next_side + sides, first + sides), axis=1).ravel()
    counts: NDArray[np.int32] = np.full(len(first), 4, dtype=np.int32)
    offset: int = rings * sides
    cap: bool
    reverse: bool
    for cap, reverse in ((start_cap, True), (end_cap, False)):
        if cap:
            base: int = 0 if reverse else (rings - 1) * sides
            rows: NDArray[np.int32] = np.concatenate(
                (
                    np.arange(base, base + sides, dtype=np.int32),
                    np.arange(offset, offset + CAP_RINGS * sides, dtype=np.int32),
                )
            ).reshape(-1, sides)
            bands: NDArray[np.int32] = np.stack(
                (rows[:-1], np.roll(rows[:-1], -1, axis=1), np.roll(rows[1:], -1, axis=1), rows[1:]), axis=-1
            ).reshape(-1, 4)
            tip: int = offset + CAP_RINGS * sides
            fan: NDArray[np.int32] = np.stack(
                (rows[-1], np.roll(rows[-1], -1), np.full(sides, tip, dtype=np.int32)), axis=1
            )
            if reverse:
                bands = bands[:, ::-1]
                fan = fan[:, ::-1]
            indices = np.concatenate((indices, bands.ravel(), fan.ravel()))
            counts = np.concatenate((counts, np.full(len(bands), 4, dtype=np.int32), np.full(sides, 3, dtype=np.int32)))
            offset = tip + 1
    return counts, indices


class PipeMesh:
    """Keep completed chunks unchanged and upload only a bounded live tail."""

    def __init__(
        self,
        stage: Any,
        path: str,
        start: Vec3,
        radius: float,
        normal: Vec3,
        shape: BrushShape = "circle",
        start_radius: float | None = None,
    ) -> None:
        self._stage: Any = stage
        self._path: str = path
        self._radius: float = radius
        self._start_radius: float = radius if start_radius is None else start_radius
        self._control_radius: float = self._start_radius
        self._shape: BrushShape = shape
        self._profile: FloatArray = outline(shape)
        self._sides: int = len(self._profile)
        self._normal: FloatArray = np.asarray(normal, dtype=np.float64)
        self._normal /= np.linalg.norm(self._normal)
        self._start: FloatArray = np.asarray(start, dtype=np.float64)
        self._control: FloatArray | None = None
        self._rings: list[Ring] = []
        self._completed: list[tuple[int, list[Ring]]] = []
        self._chunk: int = 0
        self._start_tangent: FloatArray | None = None
        self._last_ring_position: FloatArray | None = None
        self._last_ring_radius: float = self._start_radius

    def add(self, position: Vec3, radius: float | None = None) -> bool:
        """Quadratic midpoint joins suppress jitter without delaying the cursor tip."""
        next_radius: float = self._radius if radius is None else radius
        point: FloatArray = np.asarray(position, dtype=np.float64)
        last: FloatArray = self._control if self._control is not None else self._start
        if float(np.linalg.norm(point - last)) < minimum_point_distance(next_radius, self._control_radius):
            return False
        if self._control is None:
            self._start_tangent = point - self._start
            self._append(
                self._ring(
                    self._start,
                    self._start_tangent,
                    self._start_radius,
                    (next_radius - self._start_radius) / float(np.linalg.norm(self._start_tangent)),
                ),
                self._start,
                self._start_radius,
            )
        else:
            end: FloatArray = (self._control + point) * 0.5
            end_radius: float = (self._control_radius + next_radius) * 0.5
            # Limit bend error and cap work for unusually large cursor jumps.
            bend: float = float(np.linalg.norm(self._start - 2.0 * self._control + end))
            steps: int = min(32, max(1, math.ceil(math.sqrt(bend / (4.0 * self._radius * 0.02)))))
            step: int
            for step in range(1, steps + 1):
                t: float = step / steps
                center: FloatArray = (1 - t) ** 2 * self._start + 2 * (1 - t) * t * self._control + t * t * end
                tangent: FloatArray = (1 - t) * (self._control - self._start) + t * (end - self._control)
                if float(np.linalg.norm(tangent)) > 1e-9:
                    ring_radius: float = (
                        (1 - t) ** 2 * self._start_radius + 2 * (1 - t) * t * self._control_radius + t * t * end_radius
                    )
                    slope: float = (
                        (1 - t) * (self._control_radius - self._start_radius) + t * (end_radius - self._control_radius)
                    ) / float(np.linalg.norm(tangent))
                    self._append(self._ring(center, tangent, ring_radius, slope), center, ring_radius)
            self._start = end
            self._start_radius = end_radius
        self._control = point
        self._control_radius = next_radius
        return True

    def _ring(self, center: FloatArray, tangent: FloatArray, radius: float, slope: float = 0.0) -> Ring:
        side: FloatArray = np.cross(tangent, self._normal).astype(np.float64, copy=False)
        side /= np.linalg.norm(side)
        radial: FloatArray = self._profile[:, :1] * self._normal + self._profile[:, 1:] * side
        normals: FloatArray = radial / np.linalg.norm(radial, axis=1, keepdims=True) - slope * tangent / np.linalg.norm(
            tangent
        )
        normals /= np.linalg.norm(normals, axis=1, keepdims=True)
        return center + radius * radial, normals

    def _append(self, ring: Ring, center: FloatArray, radius: float) -> None:
        if self._last_ring_position is not None and float(
            np.linalg.norm(center - self._last_ring_position)
        ) < minimum_point_distance(radius, self._last_ring_radius):
            return
        self._rings.append(ring)
        self._last_ring_position = center
        self._last_ring_radius = radius
        if len(self._rings) == CHUNK_RINGS:
            self._completed.append((self._chunk, self._rings[:-1]))
            self._chunk += 1
            self._rings = self._rings[-2:]

    def flush(self) -> bool:
        """Author the frozen pieces once and the preview tail once per frame."""
        if self._control is None:
            return False
        cast(Any, UsdGeom.Xform).Define(self._stage, self._path)
        index: int
        rings: list[Ring]
        for index, rings in self._completed:
            self._write(index, rings, None)
        self._completed.clear()
        tangent: FloatArray = self._control - self._start
        tail: list[Ring] = self._rings.copy()
        if self._last_ring_position is None or float(
            np.linalg.norm(self._control - self._last_ring_position)
        ) >= minimum_point_distance(self._control_radius, self._last_ring_radius):
            tail.append(
                self._ring(
                    self._control,
                    tangent,
                    self._control_radius,
                    (self._control_radius - self._start_radius) / float(np.linalg.norm(tangent)),
                )
            )
        if len(tail) < 2:
            return False
        self._write(self._chunk, tail, tangent)
        return True

    def _write(self, index: int, rings: list[Ring], end_tangent: FloatArray | None) -> None:
        mesh: Any = cast(Any, UsdGeom.Mesh).Define(self._stage, f"{self._path}/Tube_{index:04d}")
        ring_arrays: FloatArray = np.asarray(rings, dtype=np.float64)
        points: FloatArray = ring_arrays[:, 0].reshape(-1, 3)
        normals: FloatArray = ring_arrays[:, 1].reshape(-1, 3)
        cap_points: FloatArray
        cap_tangent: FloatArray | None
        for cap_points, cap_tangent in (
            (rings[0][0], -self._start_tangent if index == 0 and self._start_tangent is not None else None),
            (rings[-1][0], end_tangent),
        ):
            if cap_tangent is not None:
                center: FloatArray = cap_points.mean(axis=0)
                radial: FloatArray = cap_points - center
                radius: float = float(np.linalg.norm(radial, axis=1).mean())
                direction: FloatArray = cap_tangent / np.linalg.norm(cap_tangent)
                angles: FloatArray = np.arange(1, CAP_RINGS + 1, dtype=np.float64) * (math.pi / (2 * (CAP_RINGS + 1)))
                cosine: FloatArray = np.cos(angles)[:, None, None]
                sine: FloatArray = np.sin(angles)[:, None, None]
                dome: FloatArray = center + cosine * radial + sine * radius * direction
                dome_normals: FloatArray = (
                    cosine * radial / np.linalg.norm(radial, axis=1, keepdims=True) + sine * direction
                )
                points = np.concatenate((points, dome.reshape(-1, 3), (center + radius * direction)[None, :]))
                normals = np.concatenate((normals, dome_normals.reshape(-1, 3), direction[None, :]))
        counts: NDArray[np.int32]
        indices: NDArray[np.int32]
        counts, indices = _topology(len(rings), index == 0, end_tangent is not None, self._sides)
        if self._shape != "circle":
            # Flat normals on polygon faces preserve the square/triangle corners.
            expanded: FloatArray = normals[indices].copy()
            quads: int = (len(rings) - 1) * self._sides
            corners: FloatArray = points[indices[: quads * 4].reshape(-1, 4)]
            face_normals: FloatArray = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]).astype(
                np.float64
            )
            face_normals /= np.maximum(np.linalg.norm(face_normals, axis=1, keepdims=True), 1e-12)
            expanded[: quads * 4] = np.repeat(face_normals, 4, axis=0)
            normals = expanded
        mesh.CreateSubdivisionSchemeAttr().Set("none")
        mesh.CreatePointsAttr().Set(cast(Any, Vt).Vec3fArray.FromNumpy(points.astype(np.float32)))
        mesh.CreateNormalsAttr().Set(cast(Any, Vt).Vec3fArray.FromNumpy(normals.astype(np.float32)))
        mesh.SetNormalsInterpolation("vertex" if self._shape == "circle" else "faceVarying")
        mesh.CreateFaceVertexCountsAttr().Set(cast(Any, Vt).IntArray.FromNumpy(counts))
        mesh.CreateFaceVertexIndicesAttr().Set(cast(Any, Vt).IntArray.FromNumpy(indices))
        mesh.CreateExtentAttr().Set(
            cast(Any, Vt).Vec3fArray.FromNumpy(np.asarray([points.min(axis=0), points.max(axis=0)], dtype=np.float32))
        )
        mesh.CreateDisplayColorAttr().Set([(0.0, 0.0, 0.0)])
