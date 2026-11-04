"""Local USD surface queries with analytic primitives and bounded mesh BVH batches."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, TypeAlias, cast

import numpy as np
from numpy.typing import NDArray
from pxr import Usd, UsdGeom

Vec3: TypeAlias = tuple[float, float, float]
Vec2: TypeAlias = tuple[float, float]
FloatArray: TypeAlias = NDArray[np.float64]
IndexArray: TypeAlias = NDArray[np.int32]
LOGGER: logging.Logger = logging.getLogger(__name__)
RAY_BATCH: int = 4096
LEAF_TRIANGLES: int = 16
_GEOMETRY: set[str] = {"Mesh", "Sphere", "Cube", "Cylinder", "Cone", "Capsule"}
_EXCLUDED: tuple[str, ...] = ("/World/ThreeDDraw", "/World/DrawingPlanes")


def is_pipe_mesh(prim: Any) -> bool:
    path: str = str(prim.GetPath())
    parts: list[str] = path.split("/")
    return (
        prim.GetTypeName() == "Mesh"
        and len(parts) == 5
        and parts[1:3] == ["World", "ThreeDDraw"]
        and (parts[3] == "Stroke" or parts[3].startswith("Stroke_"))
        and parts[4].startswith("Tube_")
    )


@dataclass(frozen=True)
class SurfaceHit:
    position: Vec3
    normal: Vec3
    path: str
    distance: float


def _vec(value: FloatArray) -> Vec3:
    return float(value[0]), float(value[1]), float(value[2])


def _box_interval(
    origins: FloatArray, directions: FloatArray, low: FloatArray, high: FloatArray
) -> tuple[FloatArray, FloatArray]:
    parallel: NDArray[np.bool_] = np.abs(directions) < 1e-12
    inverse: FloatArray = np.divide(1.0, directions, out=np.zeros_like(directions), where=~parallel)
    first: FloatArray = (low - origins) * inverse
    last: FloatArray = (high - origins) * inverse
    entry: FloatArray = np.where(parallel, -np.inf, np.minimum(first, last)).max(axis=1)
    leave: FloatArray = np.where(parallel, np.inf, np.maximum(first, last)).min(axis=1)
    outside: NDArray[np.bool_] = np.any(parallel & ((origins < low) | (origins > high)), axis=1)
    leave[outside] = -np.inf
    return entry, leave


def _ray_order(origins: FloatArray) -> NDArray[np.int64]:
    if len(origins) <= RAY_BATCH:
        return np.arange(len(origins), dtype=np.int64)
    # Nearby rays share BVH nodes, even when paint vertices arrive out of order.
    points: FloatArray = np.where(np.isfinite(origins), origins, 0.0)
    low: FloatArray = points.min(axis=0)
    span: FloatArray = np.maximum(points.max(axis=0) - low, 1e-12)
    cells: NDArray[np.uint64] = np.asarray((points - low) / span * 1023.0, dtype=np.uint64)
    codes: NDArray[np.uint64] = np.zeros(len(origins), dtype=np.uint64)
    bit: int
    axis: int
    for bit in range(10):
        for axis in range(3):
            codes |= ((cells[:, axis] >> bit) & 1) << (bit * 3 + axis)
    return np.argsort(codes, kind="stable").astype(np.int64)


class Collider:
    """Immutable collision data; batched queries can run away from the USD thread."""

    def __init__(self, path: str, matrix: FloatArray, low: FloatArray, high: FloatArray) -> None:
        self.path: str = path
        self._linear: FloatArray = matrix[:3, :3].copy()
        self._inverse: FloatArray = np.asarray(np.linalg.inv(self._linear), dtype=np.float64)
        self._translation: FloatArray = matrix[3, :3].copy()
        self._low: FloatArray = low
        self._high: FloatArray = high
        if (
            not np.isfinite(matrix).all()
            or not np.isfinite(low).all()
            or not np.isfinite(high).all()
            or np.any(high < low)
        ):
            raise ValueError("Surface has invalid transform or bounds")
        sides: IndexArray = np.indices((2, 2, 2), dtype=np.int32).reshape(3, -1).T
        corners: FloatArray = np.where(sides, high, low)
        world: FloatArray = corners @ self._linear + self._translation
        self.lo: Vec3 = _vec(world.min(axis=0))
        self.hi: Vec3 = _vec(world.max(axis=0))

    def _intersect(self, origins: FloatArray, directions: FloatArray) -> tuple[FloatArray, FloatArray]:
        raise NotImplementedError

    def raycast_many(
        self, origins: FloatArray, directions: FloatArray | Vec3
    ) -> tuple[FloatArray, FloatArray, NDArray[np.bool_]]:
        origin: FloatArray = np.asarray(origins, dtype=np.float64).reshape(-1, 3)
        direction: FloatArray = np.broadcast_to(np.asarray(directions, dtype=np.float64), origin.shape).copy()
        lengths: FloatArray = np.linalg.norm(direction, axis=1)
        valid: NDArray[np.bool_] = (
            np.isfinite(origin).all(axis=1) & np.isfinite(direction).all(axis=1) & (lengths > 1e-12)
        )
        direction /= np.maximum(lengths[:, None], 1e-12)
        positions: FloatArray = np.full(origin.shape, np.nan, dtype=np.float64)
        normals: FloatArray = np.full(origin.shape, np.nan, dtype=np.float64)
        order: NDArray[np.int64] = _ray_order(origin)
        start: int
        for start in range(0, len(origin), RAY_BATCH):
            stop: int = min(start + RAY_BATCH, len(origin))
            batch: NDArray[np.int64] = order[start:stop]
            local_origin: FloatArray = (origin[batch] - self._translation) @ self._inverse
            local_direction: FloatArray = direction[batch] @ self._inverse
            distance: FloatArray
            local_normal: FloatArray
            distance, local_normal = self._intersect(local_origin, local_direction)
            hits: NDArray[np.bool_] = valid[batch] & np.isfinite(distance) & (distance > 1e-7)
            valid[batch] = hits
            ids: NDArray[np.int64] = np.flatnonzero(hits).astype(np.int64)
            selected: NDArray[np.int64] = batch[ids]
            positions[selected] = origin[selected] + direction[selected] * distance[ids, None]
            world_normal: FloatArray = local_normal[ids] @ self._inverse.T
            world_normal /= np.maximum(np.linalg.norm(world_normal, axis=1, keepdims=True), 1e-12)
            normals[selected] = world_normal
        return positions, normals, valid


class _Sphere(Collider):
    def __init__(self, path: str, matrix: FloatArray, radius: float) -> None:
        if not math.isfinite(radius) or radius <= 0.0:
            raise ValueError("Sphere radius must be positive")
        self._radius: float = radius
        super().__init__(path, matrix, np.full(3, -radius), np.full(3, radius))

    def _intersect(self, origins: FloatArray, directions: FloatArray) -> tuple[FloatArray, FloatArray]:
        a: FloatArray = np.sum(directions * directions, axis=1)
        b: FloatArray = np.sum(origins * directions, axis=1)
        c: FloatArray = np.sum(origins * origins, axis=1) - self._radius**2
        discriminant: FloatArray = b * b - a * c
        root: FloatArray = np.sqrt(np.maximum(discriminant, 0.0))
        near: FloatArray = (-b - root) / np.maximum(a, 1e-20)
        far: FloatArray = (-b + root) / np.maximum(a, 1e-20)
        distance: FloatArray = np.where(near > 1e-7, near, far)
        distance[(discriminant < 0) | (distance <= 1e-7) | (a < 1e-20)] = np.inf
        point: FloatArray = origins + directions * np.where(np.isfinite(distance), distance, 0.0)[:, None]
        return distance, point / self._radius


class _Cube(Collider):
    def _intersect(self, origins: FloatArray, directions: FloatArray) -> tuple[FloatArray, FloatArray]:
        entry: FloatArray
        leave: FloatArray
        entry, leave = _box_interval(origins, directions, self._low, self._high)
        distance: FloatArray = np.where(entry > 1e-7, entry, leave)
        distance[(leave < np.maximum(entry, 1e-7)) | (distance <= 1e-7)] = np.inf
        point: FloatArray = origins + directions * np.where(np.isfinite(distance), distance, 0.0)[:, None]
        axis: NDArray[np.int64] = np.argmax(np.abs(point), axis=1).astype(np.int64)
        normal: FloatArray = np.zeros_like(point)
        ids: NDArray[np.int64] = np.arange(len(point), dtype=np.int64)
        normal[ids, axis] = np.sign(point[ids, axis])
        return distance, normal


@dataclass
class _Node:
    low: FloatArray
    high: FloatArray
    first: int = 0
    count: int = 0
    left: int = -1
    right: int = -1


class _Mesh(Collider):
    def __init__(
        self, path: str, matrix: FloatArray, triangles: FloatArray, vertex_normals: FloatArray | None = None
    ) -> None:
        edge1: FloatArray = triangles[:, 1] - triangles[:, 0]
        edge2: FloatArray = triangles[:, 2] - triangles[:, 0]
        normals: FloatArray = np.cross(edge1, edge2).astype(np.float64, copy=False)
        usable: NDArray[np.bool_] = np.isfinite(triangles).all(axis=(1, 2)) & (np.linalg.norm(normals, axis=1) > 1e-12)
        self._triangles: FloatArray = triangles[usable]
        self._normals: FloatArray = normals[usable]
        self._vertex_normals: FloatArray | None = vertex_normals[usable] if vertex_normals is not None else None
        if not len(self._triangles):
            raise ValueError("Surface has no nondegenerate triangles")
        self._nodes: list[_Node] = []
        self._order_list: list[int] = []
        self._centers: FloatArray = self._triangles.mean(axis=1)
        self._build(np.arange(len(self._triangles), dtype=np.int32))
        self._order: IndexArray = np.asarray(self._order_list, dtype=np.int32)
        self._order_list.clear()
        self._screen_matrix: FloatArray | None = None
        self._screen_triangles: FloatArray = np.empty((0, 3, 2), dtype=np.float64)
        self._screen_bounds: list[tuple[FloatArray, FloatArray]] = []
        super().__init__(path, matrix, self._nodes[0].low, self._nodes[0].high)

    def near_brush(self, mouse: Vec2, metric: FloatArray, matrix: FloatArray) -> tuple[Vec2, float] | None:
        """Find exact projected triangle-edge proximity, using the existing BVH."""
        if self._screen_matrix is None or not np.array_equal(matrix, self._screen_matrix):
            world: FloatArray = self._triangles @ self._linear + self._translation
            clip: FloatArray = np.concatenate((world, np.ones((*world.shape[:2], 1))), axis=2) @ matrix
            self._screen_triangles = np.divide(
                clip[:, :, :2], clip[:, :, 3:], out=np.full_like(clip[:, :, :2], np.nan), where=clip[:, :, 3:] > 1e-9
            )
            self._screen_bounds = []
            node: _Node
            for node in self._nodes:
                self._screen_bounds.append((np.full(2, np.inf), np.full(2, -np.inf)))
            index: int
            for index in range(len(self._nodes) - 1, -1, -1):
                node = self._nodes[index]
                if node.count:
                    triangles: FloatArray = self._screen_triangles[self._order[node.first : node.first + node.count]]
                    triangles = triangles[np.isfinite(triangles).all(axis=(1, 2))]
                    if len(triangles):
                        self._screen_bounds[index] = (triangles.min(axis=(0, 1)), triangles.max(axis=(0, 1)))
                else:
                    self._screen_bounds[index] = (
                        np.minimum(self._screen_bounds[node.left][0], self._screen_bounds[node.right][0]),
                        np.maximum(self._screen_bounds[node.left][1], self._screen_bounds[node.right][1]),
                    )
            self._screen_matrix = matrix.copy()
        extent: FloatArray = np.linalg.norm(np.linalg.inv(metric), axis=0)
        center: FloatArray = np.asarray(mouse, dtype=np.float64)
        best: float = 1.0
        result: Vec2 | None = None
        stack: list[int] = [0]
        while stack:
            index = stack.pop()
            low: FloatArray
            high: FloatArray
            low, high = self._screen_bounds[index]
            if np.any(low > center + extent) or np.any(high < center - extent):
                continue
            node = self._nodes[index]
            if not node.count:
                stack.extend((node.left, node.right))
                continue
            triangles = self._screen_triangles[self._order[node.first : node.first + node.count]]
            triangles = triangles[np.isfinite(triangles).all(axis=(1, 2))]
            if not len(triangles):
                continue
            starts: FloatArray = (triangles - center) @ metric
            edges: FloatArray = (np.roll(triangles, -1, axis=1) - triangles) @ metric
            lengths: FloatArray = np.sum(edges * edges, axis=2)
            factors: FloatArray = np.clip(-np.sum(starts * edges, axis=2) / np.maximum(lengths, 1e-20), 0, 1)
            distances: FloatArray = np.sum((starts + factors[:, :, None] * edges) ** 2, axis=2)
            minimum: int = int(np.argmin(distances))
            face: int = minimum // 3
            corner: int = minimum % 3
            distance: float = float(distances[face, corner])
            if distance <= best:
                point: FloatArray = triangles[face, corner] + factors[face, corner] * (
                    triangles[face, (corner + 1) % 3] - triangles[face, corner]
                )
                result, best = (float(point[0]), float(point[1])), distance
        return (result, best) if result is not None else None

    def _build(self, ids: IndexArray) -> int:
        triangles: FloatArray = self._triangles[ids]
        node: _Node = _Node(triangles.min(axis=(0, 1)), triangles.max(axis=(0, 1)))
        index: int = len(self._nodes)
        self._nodes.append(node)
        if len(ids) <= LEAF_TRIANGLES:
            node.first, node.count = len(self._order_list), len(ids)
            self._order_list.extend(ids.tolist())
        else:
            axis: int = int(np.argmax(np.ptp(self._centers[ids], axis=0)))
            middle: int = len(ids) // 2
            order: IndexArray = np.argpartition(self._centers[ids, axis], middle).astype(np.int32)
            node.left = self._build(ids[order[:middle]])
            node.right = self._build(ids[order[middle:]])
        return index

    def _intersect(self, origins: FloatArray, directions: FloatArray) -> tuple[FloatArray, FloatArray]:
        nearest: FloatArray = np.full(len(origins), np.inf, dtype=np.float64)
        normals: FloatArray = np.zeros_like(origins)
        stack: list[tuple[int, IndexArray]] = [(0, np.arange(len(origins), dtype=np.int32))]
        while stack:
            node_index: int
            rays: IndexArray
            node_index, rays = stack.pop()
            node: _Node = self._nodes[node_index]
            entry: FloatArray
            leave: FloatArray
            entry, leave = _box_interval(origins[rays], directions[rays], node.low, node.high)
            rays = rays[(leave >= np.maximum(entry, 1e-7)) & (entry <= nearest[rays])]
            if not len(rays):
                continue
            if not node.count:
                stack.append((node.right, rays))
                stack.append((node.left, rays))
                continue
            faces: IndexArray = self._order[node.first : node.first + node.count]
            triangles: FloatArray = self._triangles[faces]
            edge1: FloatArray = triangles[:, 1] - triangles[:, 0]
            edge2: FloatArray = triangles[:, 2] - triangles[:, 0]
            pvec: FloatArray = np.cross(directions[rays, None, :], edge2[None, :, :]).astype(np.float64, copy=False)
            determinant: FloatArray = np.einsum("tj,rtj->rt", edge1, pvec)
            invertible: NDArray[np.bool_] = np.abs(determinant) > 1e-12
            inverse: FloatArray = np.divide(1.0, determinant, out=np.zeros_like(determinant), where=invertible)
            offset: FloatArray = origins[rays, None, :] - triangles[None, :, 0, :]
            u: FloatArray = np.einsum("rtj,rtj->rt", offset, pvec) * inverse
            qvec: FloatArray = np.cross(offset, edge1[None, :, :]).astype(np.float64, copy=False)
            v: FloatArray = np.einsum("rj,rtj->rt", directions[rays], qvec) * inverse
            distance: FloatArray = np.einsum("tj,rtj->rt", edge2, qvec) * inverse
            valid: NDArray[np.bool_] = (
                invertible & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1.0 + 1e-9) & (distance > 1e-7)
            )
            distance[~valid] = np.inf
            column: NDArray[np.int64] = np.argmin(distance, axis=1).astype(np.int64)
            row: NDArray[np.int64] = np.arange(len(rays), dtype=np.int64)
            hit: FloatArray = distance[row, column]
            closer: NDArray[np.bool_] = hit < nearest[rays]
            selected: IndexArray = rays[closer]
            nearest[selected] = hit[closer]
            chosen_faces: IndexArray = faces[column[closer]]
            if self._vertex_normals is None:
                normals[selected] = self._normals[chosen_faces]
            else:
                hu: FloatArray = u[row, column][closer, None]
                hv: FloatArray = v[row, column][closer, None]
                values: FloatArray = self._vertex_normals[chosen_faces]
                normals[selected] = values[:, 0] * (1.0 - hu - hv) + values[:, 1] * hu + values[:, 2] * hv
                unusable: NDArray[np.bool_] = ~np.isfinite(normals[selected]).all(axis=1) | (
                    np.linalg.norm(normals[selected], axis=1) < 1e-12
                )
                normals[selected[unusable]] = self._normals[chosen_faces[unusable]]
        return nearest, normals


def _triangulate(points: FloatArray, polygon: IndexArray) -> list[tuple[int, int, int]]:
    """Ear clipping keeps concave USD polygons from creating false surface hits."""
    if len(polygon) < 3:
        return []
    if len(polygon) == 3:
        return [(0, 1, 2)]
    vertices: FloatArray = points[polygon]
    normal: FloatArray = np.sum(np.cross(vertices, np.roll(vertices, -1, axis=0)), axis=0).astype(np.float64)
    projected: FloatArray = np.delete(vertices, int(np.argmax(np.abs(normal))), axis=1)
    area: float = float(
        np.sum(projected[:, 0] * np.roll(projected[:, 1], -1) - projected[:, 1] * np.roll(projected[:, 0], -1))
    )
    sign: float = 1.0 if area >= 0 else -1.0
    edges: FloatArray = np.roll(projected, -1, axis=0) - projected
    following: FloatArray = np.roll(edges, -1, axis=0)
    turns: FloatArray = (edges[:, 0] * following[:, 1] - edges[:, 1] * following[:, 0]) * sign
    if np.all(turns >= 0.0):
        fan: list[tuple[int, int, int]] = []
        corner: int
        for corner in range(1, len(polygon) - 1):
            fan.append((0, corner, corner + 1))
        return fan
    remaining: list[int] = list(range(len(polygon)))
    result: list[tuple[int, int, int]] = []
    while len(remaining) > 3:
        found: bool = False
        index: int
        for index in range(len(remaining)):
            a: int = remaining[index - 1]
            b: int = remaining[index]
            c: int = remaining[(index + 1) % len(remaining)]
            ab: FloatArray = projected[b] - projected[a]
            ac: FloatArray = projected[c] - projected[a]
            cross: float = float(ab[0] * ac[1] - ab[1] * ac[0]) * sign
            if cross <= 1e-12:
                continue
            others: list[int] = []
            item: int
            for item in remaining:
                if item not in (a, b, c):
                    others.append(item)
            inside: NDArray[np.bool_] = np.ones(len(others), dtype=np.bool_)
            first: int
            last: int
            for first, last in ((a, b), (b, c), (c, a)):
                edge: FloatArray = projected[last] - projected[first]
                offset: FloatArray = projected[others] - projected[first]
                inside &= (edge[0] * offset[:, 1] - edge[1] * offset[:, 0]) * sign >= -1e-12
            if np.any(inside):
                continue
            result.append((a, b, c))
            remaining.pop(index)
            found = True
            break
        if not found:
            return []
    result.append((remaining[0], remaining[1], remaining[2]))
    return result


def _mesh_data(prim: Any) -> tuple[FloatArray, FloatArray | None]:
    mesh: Any = cast(Any, UsdGeom.Mesh)(prim)
    points: FloatArray = np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float64)
    indices: IndexArray = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(), dtype=np.int32)
    counts: IndexArray = np.asarray(mesh.GetFaceVertexCountsAttr().Get(), dtype=np.int32)
    if points.ndim != 2 or points.shape[1] != 3 or indices.ndim != 1 or counts.ndim != 1 or np.any(counts < 0):
        raise ValueError("Surface mesh has invalid point or topology arrays")
    holes: set[int] = set(mesh.GetHoleIndicesAttr().Get() or [])
    corners: list[tuple[int, int, int]] = []
    faces: list[int] = []
    offset: int = 0
    face: int
    count_value: np.int32
    for face, count_value in enumerate(counts):
        count: int = int(count_value)
        polygon: IndexArray = indices[offset : offset + count]
        if (
            face not in holes
            and len(polygon) == count
            and count >= 3
            and polygon.min() >= 0
            and polygon.max() < len(points)
        ):
            triangle: tuple[int, int, int]
            for triangle in _triangulate(points, polygon):
                corners.append((offset + triangle[0], offset + triangle[1], offset + triangle[2]))
                faces.append(face)
        offset += count
    corner_ids: IndexArray = np.asarray(corners, dtype=np.int32).reshape(-1, 3)
    vertex_ids: IndexArray = indices[corner_ids]
    triangles: FloatArray = points[vertex_ids]
    normals: FloatArray | None = None
    authored: Any = mesh.GetNormalsAttr().Get()
    if authored:
        values: FloatArray = np.asarray(authored, dtype=np.float64)
        interpolation: str = str(mesh.GetNormalsInterpolation())
        if interpolation in ("vertex", "varying") and len(values) == len(points):
            normals = values[vertex_ids]
        elif interpolation == "faceVarying" and len(values) == len(indices):
            normals = values[corner_ids]
        elif interpolation == "uniform" and len(values) == len(counts):
            normals = np.repeat(values[np.asarray(faces, dtype=np.int32), None, :], 3, axis=1)
        elif interpolation == "constant" and len(values) == 1:
            normals = np.broadcast_to(values, triangles.shape).copy()
    if str(mesh.GetOrientationAttr().Get()) == "leftHanded":
        triangles = triangles[:, ::-1]
        if normals is not None:
            normals = normals[:, ::-1]
    return triangles, normals


def _round_primitive(prim: Any) -> FloatArray:
    kind: str = str(prim.GetTypeName())
    radius: float = float(prim.GetAttribute("radius").Get())
    height: float = float(prim.GetAttribute("height").Get())
    axis: str = str(prim.GetAttribute("axis").Get())
    if not math.isfinite(radius) or radius <= 0.0 or not math.isfinite(height) or height < 0.0:
        raise ValueError("Surface primitive has invalid dimensions")
    rings: list[tuple[float, float]] = []
    if kind == "Capsule":
        angle: np.float64
        for angle in np.linspace(-math.pi / 2.0, 0.0, 9):
            rings.append((radius * math.cos(float(angle)), -height * 0.5 + radius * math.sin(float(angle))))
        for angle in np.linspace(0.0, math.pi / 2.0, 9):
            rings.append((radius * math.cos(float(angle)), height * 0.5 + radius * math.sin(float(angle))))
    else:
        rings = [(radius, -height * 0.5), (0.0 if kind == "Cone" else radius, height * 0.5)]
    sides: int = 64
    angles: FloatArray = np.arange(sides, dtype=np.float64) * math.tau / sides
    profile: FloatArray = np.column_stack((np.cos(angles), np.sin(angles)))
    vertices: list[FloatArray] = []
    ring_radius: float
    z: float
    for ring_radius, z in rings:
        if abs(ring_radius) < radius * 1e-12:
            ring_radius = 0.0
        vertices.append(np.column_stack((profile * ring_radius, np.full(sides, z))))
    points: FloatArray = np.concatenate(vertices)
    triangles: list[tuple[int, int, int]] = []
    ring: int
    side: int
    for ring in range(len(rings) - 1):
        for side in range(sides):
            a: int = ring * sides + side
            b: int = ring * sides + (side + 1) % sides
            triangles.extend(((a, b, b + sides), (a, b + sides, a + sides)))
    if kind != "Capsule":
        bottom: int = len(points)
        top: int = bottom + 1
        points = np.concatenate((points, np.asarray([(0, 0, -height * 0.5), (0, 0, height * 0.5)], dtype=np.float64)))
        for side in range(sides):
            following: int = (side + 1) % sides
            triangles.extend(((bottom, following, side), (top, sides + side, sides + following)))
    if axis == "X":
        points = np.column_stack((points[:, 2], points[:, 1], -points[:, 0]))
    elif axis == "Y":
        points = np.column_stack((points[:, 0], points[:, 2], -points[:, 1]))
    return points[np.asarray(triangles, dtype=np.int32)]


class SceneSurfaces:
    """Recreate after geometry/xform changes; colliders are built only when queried."""

    def __init__(self, stage: Any, *, pipes_only: bool = False) -> None:
        self._stage: Any = stage
        self._pipes_only: bool = pipes_only
        self._colliders: dict[str, Collider | None] = {}
        self._paths: list[str] = []
        lows: list[Vec3] = []
        highs: list[Vec3] = []
        self._xforms: Any = cast(Any, UsdGeom.XformCache)(cast(Any, Usd.TimeCode).Default())
        bounds: Any = cast(Any, UsdGeom.BBoxCache)(cast(Any, Usd.TimeCode).Default(), ["default", "render", "proxy"])
        prim: Any
        for prim in stage.Traverse():
            if not self._eligible(prim):
                continue
            box: Any = bounds.ComputeWorldBound(prim).ComputeAlignedRange()
            if box.IsEmpty():
                continue
            self._paths.append(str(prim.GetPath()))
            lows.append(_vec(np.asarray(box.GetMin(), dtype=np.float64)))
            highs.append(_vec(np.asarray(box.GetMax(), dtype=np.float64)))
        self._lows: FloatArray = np.asarray(lows, dtype=np.float64).reshape(-1, 3)
        self._highs: FloatArray = np.asarray(highs, dtype=np.float64).reshape(-1, 3)
        self._screen_matrix: FloatArray | None = None
        self._screen_lows: FloatArray = np.empty((0, 2), dtype=np.float64)
        self._screen_highs: FloatArray = np.empty((0, 2), dtype=np.float64)
        self._screen_front: NDArray[np.bool_] = np.empty(0, dtype=np.bool_)

    def _eligible(self, prim: Any) -> bool:
        path: str = str(prim.GetPath())
        if self._pipes_only:
            return is_pipe_mesh(prim) and cast(Any, UsdGeom.Imageable)(prim).ComputeVisibility() != "invisible"
        prefix: str
        for prefix in _EXCLUDED:
            if path == prefix or path.startswith(prefix + "/"):
                return False
        # Viewport outlines are display helpers, never drawing canvases.
        ancestor: Any = prim
        while ancestor and not ancestor.IsPseudoRoot():
            if ancestor.GetCustomDataByKey("viewportLight:overlay"):
                return False
            ancestor = ancestor.GetParent()
        return (
            bool(prim)
            and prim.GetTypeName() in _GEOMETRY
            and cast(Any, UsdGeom.Imageable)(prim).ComputeVisibility() != "invisible"
        )

    def get(self, path: str) -> Collider | None:
        if path in self._colliders:
            return self._colliders[path]
        prim: Any = self._stage.GetPrimAtPath(path)
        if not prim or not self._eligible(prim):
            self._colliders[path] = None
            return None
        try:
            matrix: FloatArray = np.asarray(self._xforms.GetLocalToWorldTransform(prim), dtype=np.float64)
            kind: str = str(prim.GetTypeName())
            collider: Collider
            if kind == "Sphere":
                collider = _Sphere(path, matrix, float(cast(Any, UsdGeom.Sphere)(prim).GetRadiusAttr().Get()))
            elif kind == "Cube":
                half: float = float(cast(Any, UsdGeom.Cube)(prim).GetSizeAttr().Get()) * 0.5
                if half <= 0.0:
                    raise ValueError("Cube size must be positive")
                collider = _Cube(path, matrix, np.full(3, -half), np.full(3, half))
            elif kind == "Mesh":
                triangles: FloatArray
                normals: FloatArray | None
                triangles, normals = _mesh_data(prim)
                collider = _Mesh(path, matrix, triangles, normals)
            else:
                collider = _Mesh(path, matrix, _round_primitive(prim))
            self._colliders[path] = collider
        except (TypeError, ValueError, RuntimeError, np.linalg.LinAlgError):
            LOGGER.debug("Skipping invalid object canvas %s", path, exc_info=True)
            self._colliders[path] = None
        return self._colliders[path]

    def near_brush(self, mouse: Vec2, metric: FloatArray, matrix: FloatArray) -> Vec2 | None:
        """Broad-phase bounds keep unrelated long strokes out of brush picking."""
        if not len(self._paths):
            return None
        if self._screen_matrix is None or not np.array_equal(matrix, self._screen_matrix):
            sides: IndexArray = np.indices((2, 2, 2), dtype=np.int32).reshape(3, -1).T
            corners: FloatArray = np.where(sides[None, :, :], self._highs[:, None, :], self._lows[:, None, :])
            clip: FloatArray = np.concatenate((corners, np.ones((*corners.shape[:2], 1))), axis=2) @ matrix
            projected: FloatArray = clip[:, :, :2] / np.maximum(clip[:, :, 3:], 1e-9)
            self._screen_lows = projected.min(axis=1)
            self._screen_highs = projected.max(axis=1)
            self._screen_front = np.any(clip[:, :, 3] > 1e-9, axis=1)
            self._screen_matrix = matrix.copy()
        extent: FloatArray = np.linalg.norm(np.linalg.inv(metric), axis=0)
        center: FloatArray = np.asarray(mouse, dtype=np.float64)
        candidates: NDArray[np.int64] = np.flatnonzero(
            np.all(self._screen_lows <= center + extent, axis=1)
            & np.all(self._screen_highs >= center - extent, axis=1)
            & self._screen_front
        ).astype(np.int64)
        best: float = 1.0
        result: Vec2 | None = None
        index: np.int64
        for index in candidates:
            collider: Collider | None = self.get(self._paths[int(index)])
            if isinstance(collider, _Mesh):
                hit: tuple[Vec2, float] | None = collider.near_brush(mouse, metric, matrix)
                if hit is not None and hit[1] <= best:
                    result, best = hit
        return result

    def hit(self, ray_origin: Vec3, ray_direction: Vec3) -> SurfaceHit | None:
        if not self._paths:
            return None
        origin: FloatArray = np.asarray(ray_origin, dtype=np.float64)
        direction: FloatArray = np.asarray(ray_direction, dtype=np.float64)
        length: float = float(np.linalg.norm(direction))
        if not np.isfinite(origin).all() or not math.isfinite(length) or length < 1e-12:
            return None
        direction /= length
        entry: FloatArray
        leave: FloatArray
        entry, leave = _box_interval(
            np.broadcast_to(origin, self._lows.shape),
            np.broadcast_to(direction, self._lows.shape),
            self._lows,
            self._highs,
        )
        candidates: NDArray[np.int64] = np.flatnonzero(leave >= np.maximum(entry, 1e-7)).astype(np.int64)
        candidates = candidates[np.argsort(entry[candidates])]
        result: SurfaceHit | None = None
        nearest: float = math.inf
        index: np.int64
        for index in candidates:
            if entry[index] > nearest:
                break
            collider: Collider | None = self.get(self._paths[int(index)])
            if collider is None:
                continue
            positions: FloatArray
            normals: FloatArray
            valid: NDArray[np.bool_]
            positions, normals, valid = collider.raycast_many(origin[None, :], _vec(direction))
            if valid[0]:
                distance: float = float(np.linalg.norm(positions[0] - origin))
                if distance < nearest:
                    nearest = distance
                    result = SurfaceHit(_vec(positions[0]), _vec(normals[0]), collider.path, distance)
        return result
