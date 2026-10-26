"""Sparse planar remeshing: each covered grid cell is drawn exactly once."""

from __future__ import annotations

import math
from dataclasses import dataclass
from threading import Event
from typing import TypeAlias

import numpy as np
from numpy.typing import NDArray

from tool_box.tools.drawing.brush import BrushShape

FloatArray: TypeAlias = NDArray[np.float64]
Cell: TypeAlias = tuple[int, int]
Value: TypeAlias = tuple[float, float, float, float]
MAX_VERTICES: int = 100_000


@dataclass(frozen=True)
class Surface:
    positions: NDArray[np.float32]
    indices: NDArray[np.int32]
    distance: NDArray[np.float32]
    dynamics: NDArray[np.float32]
    spacing: float


def _metric(points: FloatArray, shape: BrushShape) -> FloatArray:
    if shape == "circle":
        return np.linalg.norm(points, axis=1)
    if shape == "square":
        return np.max(np.abs(points), axis=1)
    return np.maximum(-2.0 * points[:, 1], math.sqrt(3.0) * np.abs(points[:, 0]) + points[:, 1])


def _field(
    points: FloatArray, a: FloatArray, b: FloatArray, av: FloatArray, bv: FloatArray, shape: BrushShape
) -> FloatArray:
    """Distance to a swept brush, with interpolated radius, hardness, and opacity."""
    offset: FloatArray = points - a
    edge: FloatArray = b - a
    dr: float = float(bv[0] - av[0])
    best: FloatArray = _metric(offset, shape) / av[0]
    weight: FloatArray = np.zeros(len(points), dtype=np.float64)
    candidates: list[FloatArray] = [np.ones(len(points), dtype=np.float64)]
    if shape == "circle":
        projection: FloatArray = offset @ edge
        denominator: FloatArray = float(edge @ edge) * av[0] + projection * dr
        candidates.append(
            np.clip(
                (projection * av[0] + np.sum(offset * offset, axis=1) * dr) / np.maximum(denominator, 1e-12), 0.0, 1.0
            )
        )
    else:
        planes: FloatArray = np.asarray(
            (
                [(1, 0), (0, 1), (-1, 0), (0, -1)]
                if shape == "square"
                else [(0, -2), (math.sqrt(3), 1), (-math.sqrt(3), 1)]
            ),
            dtype=np.float64,
        )
        base: FloatArray = offset @ planes.T
        slope: FloatArray = planes @ edge
        first: int
        last: int
        for first in range(len(planes)):
            for last in range(first + 1, len(planes)):
                difference: float = float(slope[first] - slope[last])
                if abs(difference) > 1e-12:
                    candidates.append(np.clip((base[:, first] - base[:, last]) / difference, 0.0, 1.0))
    candidate: FloatArray
    for candidate in candidates:
        distance: FloatArray = _metric(offset - candidate[:, None] * edge, shape) / np.maximum(
            av[0] + candidate * dr, 1e-9
        )
        closer: NDArray[np.bool_] = distance < best
        best[closer] = distance[closer]
        weight[closer] = candidate[closer]
    values: FloatArray = av + weight[:, None] * (bv - av)
    feather: FloatArray = np.clip((best - values[:, 1]) / np.maximum(1.0 - values[:, 1], 1e-5), 0.0, 1.0)
    coverage: FloatArray = values[:, 2] * (1.0 - feather * feather * (3.0 - 2.0 * feather))
    return np.column_stack((best, values[:, 1], values[:, 2], coverage))


def _vertices(
    centers: FloatArray, values: FloatArray, shape: BrushShape, spacing: float, cancel: Event
) -> tuple[dict[Cell, Value], set[Cell]] | None:
    vertices: dict[Cell, Value] = {}
    cells: set[Cell] = set()
    segment: int
    for segment in range(max(1, len(centers) - 1)):
        if cancel.is_set():
            raise InterruptedError("Paint remeshing cancelled")
        following: int = min(segment + 1, len(centers) - 1)
        start: FloatArray = centers[segment]
        end: FloatArray = centers[following]
        start_values: FloatArray = values[segment]
        end_values: FloatArray = values[following]
        radius: float = float(max(start_values[0], end_values[0]))
        # Long input jumps are tiled along their length, never as a huge rectangular allocation.
        steps: int = max(1, math.ceil(float(np.linalg.norm(end - start)) / max(radius, spacing)))
        step: int
        for step in range(steps):
            if cancel.is_set():
                raise InterruptedError("Paint remeshing cancelled")
            a: FloatArray = start + (end - start) * (step / steps)
            b: FloatArray = start + (end - start) * ((step + 1) / steps)
            low: NDArray[np.int64] = np.floor((np.minimum(a, b) - radius) / spacing).astype(np.int64)
            high: NDArray[np.int64] = np.ceil((np.maximum(a, b) + radius) / spacing).astype(np.int64)
            if int(np.prod(high - low + 1)) > MAX_VERTICES:
                return None
            gx: NDArray[np.int64]
            gy: NDArray[np.int64]
            gx, gy = np.meshgrid(np.arange(low[0], high[0] + 1), np.arange(low[1], high[1] + 1), indexing="ij")
            keys: NDArray[np.int64] = np.column_stack((gx.ravel(), gy.ravel()))
            field: FloatArray = _field(keys.astype(np.float64) * spacing, start, end, start_values, end_values, shape)
            index: int
            for index in range(len(keys)):
                key: Cell = (int(keys[index, 0]), int(keys[index, 1]))
                value: Value = (
                    float(field[index, 0]),
                    float(field[index, 1]),
                    float(field[index, 2]),
                    float(field[index, 3]),
                )
                previous: Value | None = vertices.get(key)
                if previous is None or value[3] > previous[3] or (value[3] == previous[3] and value[0] < previous[0]):
                    vertices[key] = value
                if key[0] < high[0] and key[1] < high[1]:
                    cells.add(key)
            if len(vertices) > MAX_VERTICES:
                return None
    return vertices, cells


def remesh(centers: FloatArray, values: FloatArray, shape: BrushShape, cancel: Event | None = None) -> Surface:
    """Build one shared-vertex surface; retracing a stroke never stacks faces."""
    cancellation: Event = cancel if cancel is not None else Event()
    if len(centers) == 0 or centers.shape[1] != 2 or values.shape != (len(centers), 3):
        raise ValueError("A paint stroke requires planar positions and radius/hardness/opacity samples")
    if not np.isfinite(centers).all() or not np.isfinite(values).all() or np.any(values[:, 0] <= 0):
        raise ValueError("Invalid paint samples")
    spacing: float = max(float(values[:, 0].min()) / 6.0, float(values[:, 0].max()) / 32.0, 0.001)
    built: tuple[dict[Cell, Value], set[Cell]] | None = _vertices(centers, values, shape, spacing, cancellation)
    while built is None:
        spacing *= 2.0
        built = _vertices(centers, values, shape, spacing, cancellation)
    vertices: dict[Cell, Value]
    cells: set[Cell]
    vertices, cells = built
    positions: list[tuple[float, float]] = []
    fields: list[Value] = []
    indices: list[int] = []
    lookup: dict[Cell, int] = {}
    cell: Cell
    for cell in sorted(cells):
        corners: tuple[Cell, ...] = (cell, (cell[0] + 1, cell[1]), (cell[0] + 1, cell[1] + 1), (cell[0], cell[1] + 1))
        corner: Cell
        if min(vertices[corner][0] for corner in corners) > 1.0:
            continue
        face: list[int] = []
        for corner in corners:
            if corner not in lookup:
                lookup[corner] = len(positions)
                positions.append((corner[0] * spacing, corner[1] * spacing))
                fields.append(vertices[corner])
            face.append(lookup[corner])
        indices.extend((face[0], face[1], face[2], face[0], face[2], face[3]))
    data: NDArray[np.float32] = np.asarray(fields, dtype=np.float32)
    return Surface(
        np.asarray(positions, dtype=np.float32),
        np.asarray(indices, dtype=np.int32),
        data[:, 0].copy(),
        data[:, 1:3].copy(),
        spacing,
    )
