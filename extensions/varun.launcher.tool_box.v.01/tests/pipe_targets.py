"""CPU USD regression for pipe recognition, brush overlap, and temporary color."""

from typing import Any, cast

import numpy as np
from pxr import Gf, Usd, UsdGeom
from tool_box.tools.drawing.curves.mesh import PipeMesh
from tool_box.tools.drawing.pipe_targets import ORANGE, PipeTargets
from tool_box.tools.drawing.surface import FloatArray, SceneSurfaces, SurfaceHit


def run_checks() -> dict[str, object]:
    stage: Any = cast(Any, Usd.Stage).CreateInMemory()
    pipe: PipeMesh = PipeMesh(stage, "/World/ThreeDDraw/Stroke", (-5, 3, 0), 0.4, (0, 1, 0))
    pipe.add((0, 3, 0))
    pipe.add((5, 3, 0))
    pipe.flush()
    mesh: Any = cast(Any, UsdGeom.Mesh).Get(stage, "/World/ThreeDDraw/Stroke/Tube_0000")
    mesh.GetDisplayColorAttr().Set([(0.2, 0.4, 0.6)])
    camera: Any = Gf.Camera()
    camera.projection = Gf.Camera.Orthographic
    camera.horizontalAperture = camera.verticalAperture = 200.0
    camera.transform = Gf.Matrix4d().SetLookAt(Gf.Vec3d(0, 20, 0), Gf.Vec3d(0), Gf.Vec3d(0, 0, -1)).GetInverse()
    inverse: Any = (camera.frustum.ComputeViewMatrix() * camera.frustum.ComputeProjectionMatrix()).GetInverse()
    targets: PipeTargets = PipeTargets(stage)
    metric: FloatArray = np.eye(2, dtype=np.float64) * 10.0
    hit: SurfaceHit | None = targets.hit((0, 0), inverse, metric)
    assert hit is not None and hit.path.endswith("/Stroke/Tube_0000")
    assert hit.position[1] > 3.3
    # The center ray misses this thin pipe; the brush footprint still overlaps it.
    edge: SurfaceHit | None = targets.hit((0, -0.10), inverse, metric)
    assert edge is not None
    assert targets.hit((0, -0.20), inverse, metric) is None
    assert targets.hit((0, -0.10), inverse, np.diag((10.0, 40.0))) is None
    assert SceneSurfaces(stage).hit((0, 20, 0), (0, -1, 0)) is None
    saved: str = stage.GetRootLayer().ExportToString()
    targets.recognize(hit)
    assert np.allclose(mesh.GetDisplayColorAttr().Get()[0], ORANGE)
    assert stage.GetRootLayer().ExportToString() == saved
    cached: SceneSurfaces | None = targets._surfaces
    targets._expires = 0.001
    targets._tick(None)
    assert np.allclose(mesh.GetDisplayColorAttr().Get()[0], (0.2, 0.4, 0.6))
    targets.recognize(hit)
    assert targets._expires == 0, "Continued hover must not retrigger the flash"
    assert targets._surfaces is cached, "Color flashes must not invalidate geometry"
    targets.recognize(None)
    targets.recognize(hit)
    assert np.allclose(mesh.GetDisplayColorAttr().Get()[0], ORANGE)
    targets.reset()
    assert np.allclose(mesh.GetDisplayColorAttr().Get()[0], (0.2, 0.4, 0.6))
    cast(Any, UsdGeom.Imageable)(mesh.GetPrim()).MakeInvisible()
    assert targets.hit((0, 0), inverse, metric) is None
    cast(Any, UsdGeom.Imageable)(mesh.GetPrim()).MakeVisible()
    assert targets.hit((0, 0), inverse, metric) is not None
    stage.RemovePrim("/World/ThreeDDraw/Stroke")
    live: PipeMesh = PipeMesh(stage, "/World/ThreeDDraw/_LiveStroke", (-5, 3, 0), 1, (0, 1, 0))
    live.add((5, 3, 0))
    live.flush()
    assert targets.hit((0, 0), inverse, metric) is None
    fresh: PipeMesh = PipeMesh(stage, "/World/ThreeDDraw/Stroke_2", (-5, 3, 0), 0.4, (0, 1, 0))
    fresh.add((5, 3, 0))
    fresh.flush()
    assert targets.hit((0, 0), inverse, metric) is not None
    targets.destroy()
    assert not stage.GetSessionLayer().subLayerPaths
    return {
        "passed": True,
        "brush_edge_overlap": True,
        "one_shot_flash": True,
        "saved_color_unchanged": True,
        "visibility_and_new_strokes": True,
        "live_stroke_excluded": True,
    }
