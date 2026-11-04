"""CPU USD regression checks for curved paint canvases and spatial pipe frames."""

from __future__ import annotations

import math
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray
from pxr import Usd, UsdGeom
from tool_box.tools.drawing.brush import BrushSample
from tool_box.tools.drawing.curves.surface import SurfacePipeMesh
from tool_box.tools.drawing.points.canvas import PaintCanvas, SurfacePaintMesh
from tool_box.tools.drawing.points.repair import PaintRepair


def assert_surface(stage: Any, path: str, sphere: bool) -> int:
    prim: Any = stage.GetPrimAtPath(path)
    child: Any
    vertices: int = 0
    for child in prim.GetChildren():
        if child.GetTypeName() != "Mesh":
            continue
        mesh: Any = cast(Any, UsdGeom.Mesh)(child)
        points: NDArray[np.float64] = np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float64)
        indices: NDArray[np.int32] = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(), dtype=np.int32)
        assert len(indices) > 0 and np.all(np.isfinite(points))
        used: NDArray[np.float64] = points[np.unique(indices)].copy()
        used[:, 2] -= 0.0004
        if sphere:
            assert np.max(np.abs(np.linalg.norm(used, axis=1) - 2.0)) < 1e-5
            assert np.ptp(used[:, 2]) > 0.01
        else:
            assert np.allclose(used[:, 2], 2.0, atol=1e-6)
        variables: Any = cast(Any, UsdGeom.PrimvarsAPI)(mesh)
        assert len(variables.GetPrimvar("paintCoord").Get()) == len(points)
        assert len(variables.GetPrimvar("paintDynamics").Get()) == len(points)
        mode: Any = variables.GetPrimvar("paintMode")
        if mode.GetInterpolation() == "uniform":
            assert len(mode.Get()) == len(indices) // 3
        vertices += len(points)
    return vertices


async def run_checks() -> dict[str, object]:
    stage: Any = cast(Any, Usd.Stage).CreateInMemory()
    cast(Any, UsdGeom.Cube).Define(stage, "/World/Cube").CreateSizeAttr().Set(4.0)
    cast(Any, UsdGeom.Sphere).Define(stage, "/World/Sphere").CreateRadiusAttr().Set(2.0)
    sphere: bool
    totals: dict[str, int] = {}
    for sphere in (False, True):
        object_path: str = "/World/Sphere" if sphere else "/World/Cube"
        path: str = "/World/ThreeDDraw/SphereStroke" if sphere else "/World/ThreeDDraw/CubeStroke"
        canvas: PaintCanvas | None = PaintCanvas.create(stage, object_path, (0, 0, 2), (0, 0, 1), 0.2)
        assert canvas is not None
        mesh: SurfacePaintMesh = SurfacePaintMesh(stage, path, (1, 0, 0), (0, 1, 0), "circle", canvas)
        value: np.float64
        for value in np.linspace(-0.75, 0.75, 16):
            x: float = float(value)
            y: float = 0.12 * math.sin(x * 4)
            z: float = math.sqrt(4 - x * x - y * y) if sphere else 2.0
            mesh.add((x, y, z), BrushSample(0.2, 0.7, 0.8))
            mesh.flush()
        totals[object_path + "_live"] = assert_surface(stage, path, sphere)
        assert PaintCanvas.read(stage, stage.GetPrimAtPath(path)) is not None
        repair: PaintRepair = PaintRepair()
        assert await repair.repair(stage, path)
        assert stage.GetPrimAtPath(path + "/PaintSurface")
        totals[object_path + "_finished"] = assert_surface(stage, path, sphere)
        assert not await repair.repair(stage, path), "Completed surfaces must not be rebuilt on reopen"
        repair.destroy()
    # A footprint near the silhouette clips missed rays instead of emitting invalid triangles.
    edge_canvas: PaintCanvas | None = PaintCanvas.create(stage, "/World/Sphere", (0, 0, 2), (0, 0, 1), 0.2)
    assert edge_canvas is not None
    edge: SurfacePaintMesh = SurfacePaintMesh(
        stage, "/World/ThreeDDraw/Edge", (1, 0, 0), (0, 1, 0), "circle", edge_canvas
    )
    edge.add((1.9, 0, math.sqrt(4 - 1.9**2)), BrushSample(0.2, 0.7, 0.8))
    edge.flush()
    assert_surface(stage, "/World/ThreeDDraw/Edge", True)
    stage.RemovePrim("/World/Sphere")
    before: str = stage.GetRootLayer().ExportToString()
    repair = PaintRepair()
    assert not await repair.repair(stage, "/World/ThreeDDraw/Edge")
    assert stage.GetPrimAtPath("/World/ThreeDDraw/Edge/Paint_0000")
    assert stage.GetRootLayer().ExportToString() == before
    pipe: SurfacePipeMesh = SurfacePipeMesh(stage, "/World/ThreeDDraw/Pipe", (0, 0, 0), 0.2, (0, 0, 1))
    tangent: tuple[float, float, float]
    for tangent in ((0, 0, 1), (0.001, 0, 1), (1, 0, 0), (0, 1, 0), (0, 0, -1)):
        direction: NDArray[np.float64] = np.asarray(tangent, dtype=np.float64)
        points: NDArray[np.float64]
        normals: NDArray[np.float64]
        points, normals = pipe._ring(np.zeros(3, dtype=np.float64), direction, 0.2)
        assert np.all(np.isfinite(points)) and np.all(np.isfinite(normals))
        assert np.allclose(points @ direction, 0, atol=1e-8)
        assert np.allclose(np.linalg.norm(points, axis=1), 0.2)
    return {"passed": True, "surface_vertices": totals, "silhouette_clipped": True, "pipe_parallel_normal": True}
