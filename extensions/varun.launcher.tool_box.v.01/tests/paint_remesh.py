"""CPU-only regressions for self-crossing paint, shared triangles, and bounded work."""

from __future__ import annotations

import math
from threading import Event
from time import perf_counter

import numpy as np
from numpy.typing import NDArray
from tool_box.tools.drawing.brush import SHAPES, BrushShape
from tool_box.tools.drawing.points.remesh import MAX_VERTICES, Surface, remesh


def check_surface(surface: Surface) -> None:
    vertices: NDArray[np.float64] = surface.positions.astype(np.float64)
    triangles: NDArray[np.float64] = vertices[surface.indices.reshape(-1, 3)]
    a: NDArray[np.float64] = triangles[:, 1] - triangles[:, 0]
    b: NDArray[np.float64] = triangles[:, 2] - triangles[:, 0]
    area: NDArray[np.float64] = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
    assert len(vertices) <= MAX_VERTICES
    assert np.allclose(area, surface.spacing**2, rtol=2e-4, atol=1e-7)
    assert len(np.unique(surface.positions, axis=0)) == len(vertices), "Duplicated vertices"
    faces: NDArray[np.int32] = np.sort(surface.indices.reshape(-1, 3), axis=1)
    assert len(np.unique(faces, axis=0)) == len(faces), "Stacked faces"
    edges: NDArray[np.int32] = np.sort(np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [0, 2]])), axis=1)
    counts: NDArray[np.int64] = np.unique(edges, axis=0, return_counts=True)[1].astype(np.int64)
    assert counts.max() <= 2, "Non-manifold paint surface"
    assert np.isfinite(surface.distance).all() and np.isfinite(surface.dynamics).all()


def run_checks() -> dict[str, object]:
    t: NDArray[np.float64] = np.linspace(0.0, math.tau * 2, 120)
    patterns: dict[str, NDArray[np.float64]] = {
        "zigzag": np.column_stack((t * 2, np.arcsin(np.sin(t * 3)) * 3)),
        "crossing": np.column_stack((np.sin(t) * 8, np.sin(t * 2) * 4)),
        "tight_loops": np.column_stack((t * 2 + np.sin(t * 6), np.cos(t * 6))),
        "stationary": np.zeros((20, 2), dtype=np.float64),
    }
    timings: dict[str, float] = {}
    shape: BrushShape
    name: str
    centers: NDArray[np.float64]
    for shape in SHAPES:
        for name, centers in patterns.items():
            values: NDArray[np.float64] = np.tile(np.asarray([2.5, 0.25, 0.65]), (len(centers), 1))
            start: float = perf_counter()
            surface: Surface = remesh(centers, values, shape)
            timings[f"{shape}_{name}_ms"] = (perf_counter() - start) * 1000.0
            check_surface(surface)
            repeated: Surface = remesh(
                np.concatenate((centers, centers[::-1])), np.concatenate((values, values[::-1])), shape
            )
            np.testing.assert_array_equal(surface.positions, repeated.positions)
            np.testing.assert_array_equal(surface.indices, repeated.indices)
            np.testing.assert_allclose(surface.distance, repeated.distance, atol=2e-6)
            np.testing.assert_array_equal(surface.dynamics, repeated.dynamics)
    centers = np.column_stack((np.linspace(0, 200, 1000), np.sin(np.linspace(0, 30, 1000)) * 3))
    values = np.column_stack((np.linspace(0.25, 2.5, 1000), np.linspace(0.0, 1.0, 1000), np.linspace(0.2, 1.0, 1000)))
    start = perf_counter()
    surface = remesh(centers, values, "circle")
    check_surface(surface)
    timings["long_variable_pressure_ms"] = (perf_counter() - start) * 1000.0
    cancelled: Event = Event()
    cancelled.set()
    try:
        remesh(centers, values, "circle", cancelled)
    except InterruptedError:
        pass
    else:
        raise AssertionError("Cancelled remeshing continued")
    return {"passed": True, "timings": timings, "long_stroke_vertices": len(surface.positions)}
