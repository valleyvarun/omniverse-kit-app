"""CPU checks for object-canvas projection against the installed USD SDK."""

from __future__ import annotations

import math
import time
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray
from pxr import Gf, Usd, UsdGeom
from tool_box.tools.drawing.surface import Collider, SceneSurfaces, SurfaceHit


def _stage() -> Any:
    return cast(Any, Usd.Stage).CreateInMemory()


def _translated(schema: Any, translation: tuple[float, float, float]) -> None:
    schema.AddTranslateOp().Set(cast(Any, Gf.Vec3d)(*translation))


def _hit(scene: SceneSurfaces, origin: tuple[float, float, float], direction: tuple[float, float, float]) -> SurfaceHit:
    hit: SurfaceHit | None = scene.hit(origin, direction)
    assert hit is not None
    assert math.isclose(float(np.linalg.norm(hit.normal)), 1.0, abs_tol=1e-6)
    return hit


def run_checks() -> dict[str, object]:
    stage: Any = _stage()
    sphere: Any = cast(Any, UsdGeom.Sphere).Define(stage, "/World/Ball")
    sphere.CreateRadiusAttr(1.0)
    _translated(sphere, (2.0, 3.0, 4.0))
    sphere.AddScaleOp().Set(cast(Any, Gf.Vec3f)(2.0, 1.0, 0.5))
    scene: SceneSurfaces = SceneSurfaces(stage)
    hit: SurfaceHit = _hit(scene, (2.0, 3.0, 8.0), (0.0, 0.0, -3.0))
    np.testing.assert_allclose(hit.position, (2.0, 3.0, 4.5))
    np.testing.assert_allclose(hit.normal, (0.0, 0.0, 1.0))
    assert math.isclose(hit.distance, 3.5)
    hit = _hit(scene, (2.0, 3.0, 4.0), (1.0, 0.0, 0.0))
    np.testing.assert_allclose(hit.position, (4.0, 3.0, 4.0))
    x: float = math.sqrt(0.5) * 2.0
    z: float = math.sqrt(0.5) * 0.5
    hit = _hit(scene, (2.0 + x, 3.0, 7.0), (0.0, 0.0, -1.0))
    expected_normal: NDArray[np.float64] = np.asarray((x / 4.0, 0.0, z / 0.25), dtype=np.float64)
    expected_normal /= np.linalg.norm(expected_normal)
    np.testing.assert_allclose(hit.position, (2.0 + x, 3.0, 4.0 + z), atol=1e-7)
    np.testing.assert_allclose(hit.normal, expected_normal, atol=1e-7)
    collider: Collider | None = scene.get("/World/Ball")
    assert collider is not None
    np.testing.assert_allclose(collider.lo, (0.0, 2.0, 3.5))
    np.testing.assert_allclose(collider.hi, (4.0, 4.0, 4.5))
    positions: NDArray[np.float64]
    normals: NDArray[np.float64]
    valid: NDArray[np.bool_]
    positions, normals, valid = collider.raycast_many(
        np.asarray(((2, 3, 8), (20, 3, 8)), dtype=np.float64), (0.0, 0.0, -1.0)
    )
    assert valid.tolist() == [True, False]
    assert np.isnan(positions[1]).all() and np.isnan(normals[1]).all()
    assert scene.hit((2.0, 3.0, 8.0), (0.0, 0.0, 0.0)) is None

    stage = _stage()
    cube: Any = cast(Any, UsdGeom.Cube).Define(stage, "/World/Box")
    cube.CreateSizeAttr(2.0)
    cube.AddRotateZOp().Set(45.0)
    scene = SceneSurfaces(stage)
    hit = _hit(scene, (0.0, 0.0, 5.0), (0.0, 0.0, -1.0))
    np.testing.assert_allclose(hit.position, (0.0, 0.0, 1.0), atol=1e-7)
    hit = _hit(scene, (5.0, 0.25, 0.0), (-1.0, 0.0, 0.0))
    np.testing.assert_allclose(hit.position, (math.sqrt(2.0) - 0.25, 0.25, 0.0), atol=1e-7)
    np.testing.assert_allclose(hit.normal, (math.sqrt(0.5), math.sqrt(0.5), 0.0), atol=1e-7)
    assert scene.hit((0.0, 0.0, 5.0), (1.0, 0.0, 0.0)) is None

    stage = _stage()
    mesh: Any = cast(Any, UsdGeom.Mesh).Define(stage, "/World/Concave")
    mesh.CreatePointsAttr([(0, 0, 0), (3, 0, 0), (3, 1, 0), (1, 1, 0), (1, 3, 0), (0, 3, 0)])
    mesh.CreateFaceVertexCountsAttr([6])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3, 4, 5])
    scene = SceneSurfaces(stage)
    assert scene.hit((2.0, 2.0, 3.0), (0.0, 0.0, -1.0)) is None
    np.testing.assert_allclose(_hit(scene, (0.5, 2.0, 3.0), (0.0, 0.0, -1.0)).position, (0.5, 2.0, 0.0))
    np.testing.assert_allclose(_hit(scene, (2.0, 0.5, 3.0), (0.0, 0.0, -1.0)).normal, (0.0, 0.0, 1.0))
    mesh.CreateHoleIndicesAttr([0])
    assert SceneSurfaces(stage).hit((0.5, 2.0, 3.0), (0.0, 0.0, -1.0)) is None

    stage = _stage()
    path: str
    for path in ("/World/ThreeDDraw/Stroke", "/World/DrawingPlanes/Plane", "/World/Hidden", "/World/Visible"):
        cube = cast(Any, UsdGeom.Cube).Define(stage, path)
        if path == "/World/Hidden":
            cube.CreateVisibilityAttr("invisible")
        elif path == "/World/Visible":
            _translated(cube, (0.0, 0.0, -4.0))
    scene = SceneSurfaces(stage)
    assert _hit(scene, (0.0, 0.0, 5.0), (0.0, 0.0, -1.0)).path == "/World/Visible"
    assert scene.get("/World/Hidden") is None
    assert scene.get("/World/ThreeDDraw/Stroke") is None
    assert scene.get("/World/DrawingPlanes/Plane") is None

    stage = _stage()
    mesh = cast(Any, UsdGeom.Mesh).Define(stage, "/World/Normals")
    mesh.CreatePointsAttr([(0, 0, 0), (2, 0, 0), (0, 2, 0)])
    mesh.CreateFaceVertexCountsAttr([3])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
    mesh.CreateNormalsAttr([(0, 0, 1), (0.6, 0, 0.8), (0, 0.6, 0.8)])
    mesh.SetNormalsInterpolation("vertex")
    scene = SceneSurfaces(stage)
    hit = _hit(scene, (0.5, 0.5, 3.0), (0.0, 0.0, -1.0))
    expected_normal = np.asarray((0.15, 0.15, 0.9), dtype=np.float64)
    expected_normal /= np.linalg.norm(expected_normal)
    np.testing.assert_allclose(hit.normal, expected_normal, atol=1e-7)
    mesh.CreateNormalsAttr([(0, 0, 0), (0, 0, 0), (0, 0, 0)])
    hit = _hit(SceneSurfaces(stage), (0.5, 0.5, 3.0), (0.0, 0.0, -1.0))
    np.testing.assert_allclose(hit.normal, (0.0, 0.0, 1.0))
    mesh.CreateOrientationAttr("leftHanded")
    hit = _hit(SceneSurfaces(stage), (0.5, 0.5, 3.0), (0.0, 0.0, -1.0))
    np.testing.assert_allclose(hit.normal, (0.0, 0.0, -1.0))

    stage = _stage()
    parent: Any = cast(Any, UsdGeom.Xform).Define(stage, "/World/HiddenGroup")
    parent.CreateVisibilityAttr("invisible")
    cast(Any, UsdGeom.Cube).Define(stage, "/World/HiddenGroup/Box")
    cast(Any, UsdGeom.Mesh).Define(stage, "/World/Incomplete")
    cube = cast(Any, UsdGeom.Cube).Define(stage, "/World/Far")
    _translated(cube, (0.0, 0.0, -4.0))
    cast(Any, UsdGeom.Sphere).Define(stage, "/World/Near")
    scene = SceneSurfaces(stage)
    assert scene.get("/World/HiddenGroup/Box") is None
    assert scene.get("/World/Incomplete") is None
    assert _hit(scene, (0.0, 0.0, 5.0), (0.0, 0.0, -1.0)).path == "/World/Near"

    kind: str
    schema_class: Any
    for kind, schema_class in (("Cylinder", UsdGeom.Cylinder), ("Cone", UsdGeom.Cone), ("Capsule", UsdGeom.Capsule)):
        stage = _stage()
        primitive: Any = schema_class.Define(stage, "/World/Shape")
        primitive.CreateRadiusAttr(1.0)
        primitive.CreateHeightAttr(2.0)
        primitive.CreateAxisAttr("X")
        scene = SceneSurfaces(stage)
        hit = _hit(scene, (0.0, 0.0, 5.0), (0.0, 0.0, -1.0))
        np.testing.assert_allclose(hit.position, (0.0, 0.0, 0.5 if kind == "Cone" else 1.0), atol=1e-6)
        if kind == "Capsule":
            hit = _hit(scene, (5.0, 0.0, 0.0), (-1.0, 0.0, 0.0))
            np.testing.assert_allclose(hit.position, (2.0, 0.0, 0.0), atol=1e-6)
            collider = scene.get("/World/Shape")
            assert collider is not None
            np.testing.assert_allclose(collider.hi, (2.0, 1.0, 1.0), atol=1e-6)

    stage = _stage()
    mesh = cast(Any, UsdGeom.Mesh).Define(stage, "/World/Dense")
    resolution: int = 96
    grid: NDArray[np.float64] = np.linspace(-4.0, 4.0, resolution + 1)
    xx: NDArray[np.float64]
    yy: NDArray[np.float64]
    xx, yy = np.meshgrid(grid, grid)
    points: NDArray[np.float64] = np.column_stack((xx.ravel(), yy.ravel(), np.zeros(xx.size)))
    rows: NDArray[np.int64]
    columns: NDArray[np.int64]
    rows, columns = np.meshgrid(np.arange(resolution), np.arange(resolution), indexing="ij")
    first: NDArray[np.int64] = (rows * (resolution + 1) + columns).ravel()
    triangles: NDArray[np.int64] = np.concatenate(
        (
            np.column_stack((first, first + 1, first + resolution + 2)),
            np.column_stack((first, first + resolution + 2, first + resolution + 1)),
        )
    )
    mesh.CreatePointsAttr(points.tolist())
    mesh.CreateFaceVertexIndicesAttr(triangles.ravel().tolist())
    mesh.CreateFaceVertexCountsAttr([3] * len(triangles))
    start: float = time.perf_counter()
    scene = SceneSurfaces(stage)
    collider = scene.get("/World/Dense")
    assert collider is not None
    build_seconds: float = time.perf_counter() - start
    random: np.random.Generator = np.random.default_rng(12)
    origins: NDArray[np.float64] = np.column_stack((random.uniform(-3.9, 3.9, (30000, 2)), np.ones(30000)))
    start = time.perf_counter()
    positions, normals, valid = collider.raycast_many(origins, (0.0, 0.0, -1.0))
    projection_seconds: float = time.perf_counter() - start
    assert valid.all()
    np.testing.assert_allclose(positions[:, :2], origins[:, :2], atol=1e-7)
    np.testing.assert_allclose(positions[:, 2], 0.0, atol=1e-7)
    np.testing.assert_allclose(normals[:, 2], 1.0, atol=1e-7)
    return {
        "passed": True,
        "triangles": len(triangles),
        "projection_rays": len(origins),
        "bvh_build_seconds": build_seconds,
        "projection_seconds": projection_seconds,
    }
