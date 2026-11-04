"""CPU math and USD-only camera tests; no app or renderer is started."""

from __future__ import annotations

import asyncio
import math
from typing import Any, cast

from pxr import Gf, Sdf, Usd, UsdGeom
from tool_box.tools.drawing.cursor_camera import CursorView
from tool_box.tools.drawing.methods import (
    AXES,
    AxisIndex,
    CursorAnchor,
    DrawingMethods,
    Vec3,
    axis_plane_normal,
    axis_point_from_ray,
    choose_axis,
    intersect_plane,
)


def check_methods() -> None:
    paint: DrawingMethods = DrawingMethods()
    pipe: DrawingMethods = DrawingMethods()
    calls: list[bool] = []

    def changed() -> None:
        calls.append(True)

    paint.add_listener(changed)
    paint.set_option("drawing_plane", True)
    paint.set_option("draw_on_object", True)
    paint.set_option("axes_spokes", True)
    paint.set_option("cursor_3d", True)
    paint.set_option("cursor_3d", True)
    assert len(calls) == 4
    assert not pipe.drawing_plane and not pipe.draw_on_object and not pipe.axes_spokes and not pipe.cursor_3d
    paint.remove_listener(changed)
    paint.set_option("cursor_3d", False)
    assert len(calls) == 4
    assert DrawingMethods.get("paint") is DrawingMethods.get("pipe")
    anchor: CursorAnchor = CursorAnchor.get("test-stage-A")
    other: CursorAnchor = CursorAnchor.get("test-stage-B")
    anchor.reset()
    other.reset()
    anchor.request_placement()
    assert anchor.placing and anchor.position is None
    anchor.place((2, 3, 4))
    assert not anchor.placing and other.position is None
    assert anchor.set_mode(1) == (1, 1)
    assert anchor.set_mode(1) == (1, -1)
    assert anchor.plane() == ((2, 3, 4), AXES[0])
    assert anchor.set_mode(2) == (2, 1)
    assert anchor.set_mode(2) == (2, -1)
    assert anchor.set_mode(3) == (3, 1)
    assert anchor.set_mode(0) == (0, 1)
    assert anchor.plane() == ((2, 3, 4), AXES[2])
    assert choose_axis((30, 2), ((1, 0), (0, 1), (-1, 1))) == 0
    assert choose_axis((-30, -2), ((1, 0), (0, 1), (-1, 1))) == 0
    assert choose_axis((0, 30), ((1, 0), (0, 0), (0, 1))) == 2
    assert choose_axis((0, 0), ((1, 0), (0, 1), None)) is None
    assert choose_axis((2, 3), (None, (0, 0), None)) is None
    point: Vec3 | None = axis_point_from_ray((0, 0, 0), 1, (10, 10, 10), (-10, -5, -10))
    assert point is not None and math.dist(point, (0, 5, 0)) < 1e-8
    assert axis_point_from_ray((0, 0, 0), 1, (10, 10, 10), (0, 1, 0)) is None
    assert axis_point_from_ray((0, 0, 0), 1, (10, 10, 10), (10, 5, 10)) is None
    index: int
    direction: Vec3
    for index, direction in enumerate(AXES):
        normal: Vec3 = axis_plane_normal(cast(AxisIndex, index), (0.3, -0.7, 0.6))
        dot: float = 0.0
        squared: float = 0.0
        component: int
        for component in range(3):
            dot += normal[component] * direction[component]
            squared += normal[component] * normal[component]
        assert abs(dot) < 1e-8
        assert abs(math.sqrt(squared) - 1.0) < 1e-8
    assert axis_plane_normal(1, (0, 1, 0)) == AXES[2]
    assert intersect_plane((0, 10, 0), (0, -1, 0), (3, 2, 4), (0, 1, 0)) == (0, 2, 0)
    assert intersect_plane((0, 10, 0), (1, 0, 0), (0, 0, 0), (0, 1, 0)) is None
    assert intersect_plane((0, 10, 0), (0, 1, 0), (0, 0, 0), (0, 1, 0)) is None


class TestViewport:
    def __init__(self, stage: Any, camera_path: str) -> None:
        self.stage: Any = stage
        self._camera_path: str = camera_path
        self.resolution: tuple[int, int] = (640, 480)

    @property
    def camera_path(self) -> str:
        return self._camera_path

    @camera_path.setter
    def camera_path(self, value: str) -> None:
        # Native viewport code can author into root even inside a session edit context.
        spec: Any = cast(Any, Sdf).CreatePrimInLayer(self.stage.GetRootLayer(), value)
        spec.specifier = cast(Any, Sdf).SpecifierDef
        spec.typeName = "Camera"
        self._camera_path = value


def check_cameras() -> None:
    stage: Any = cast(Any, Usd.Stage).CreateInMemory()
    original: Any = cast(Any, UsdGeom.Camera).Define(stage, "/Camera")
    original.CreateProjectionAttr().Set("perspective")
    original.MakeMatrixXform().Set(
        Gf.Matrix4d().SetLookAt(Gf.Vec3d(80, 100, 120), Gf.Vec3d(2, 3, 4), Gf.Vec3d(0, 1, 0)).GetInverse()
    )
    viewport: TestViewport = TestViewport(stage, "/Camera")
    occupied_path: str = f"/__ToolBoxCursorView_{id(viewport):x}"
    cast(Any, UsdGeom.Camera).Define(stage, occupied_path)
    controller: CursorView = CursorView()
    saved: str = stage.GetRootLayer().ExportToString()
    axis: int
    side: int
    for axis in (1, 2, 3):
        for side in (1, -1):
            assert controller.switch(viewport, axis, (2, 3, 4), side)
            camera: Any = cast(Any, UsdGeom.Camera).Get(stage, viewport.camera_path)
            assert camera.GetProjectionAttr().Get() == "orthographic"
            transform: Any = camera.ComputeLocalToWorldTransform(0)
            eye: Any = transform.ExtractTranslation()
            aim: Any = transform.TransformDir(Gf.Vec3d(0, 0, -1))
            assert float(eye[axis - 1] - (2, 3, 4)[axis - 1]) * side > 0
            assert abs(float(aim[axis - 1]) + side) < 1e-8
            assert len(camera.GetOrderedXformOps()) == 1
            assert str(viewport.camera_path) != occupied_path
            assert stage.GetRootLayer().ExportToString() == saved
    assert controller.switch(viewport, 0, (2, 3, 4))
    assert viewport.camera_path == "/Camera"
    assert len(stage.GetSessionLayer().subLayerPaths) == 0
    assert stage.GetRootLayer().ExportToString() == saved
    original.CreateProjectionAttr().Set("orthographic")
    saved = stage.GetRootLayer().ExportToString()
    assert controller.switch(viewport, 1, (2, 3, 4))
    assert controller.switch(viewport, 0, (2, 3, 4))
    camera = cast(Any, UsdGeom.Camera).Get(stage, viewport.camera_path)
    assert camera.GetProjectionAttr().Get() == "perspective"
    assert stage.GetRootLayer().ExportToString() == saved
    controller.destroy()
    assert viewport.camera_path == "/Camera"
    assert len(stage.GetSessionLayer().subLayerPaths) == 0
    assert stage.GetRootLayer().ExportToString() == saved


async def check_deferred_camera_cleanup() -> None:
    stage: Any = cast(Any, Usd.Stage).CreateInMemory()
    original: Any = cast(Any, UsdGeom.Camera).Define(stage, "/Camera")
    original.CreateProjectionAttr().Set("perspective")
    viewport: TestViewport = TestViewport(stage, "/Camera")
    controller: CursorView = CursorView()
    saved: str = stage.GetRootLayer().ExportToString()
    assert controller.switch(viewport, 1, (2, 3, 4))
    state: Any = controller._views.pop(id(viewport))
    viewport.camera_path = "/Camera"

    class PendingCameraUpdates:
        def __init__(self) -> None:
            self.updates: int = 0

        async def next_update_async(self) -> None:
            assert state.layer.identifier in stage.GetSessionLayer().subLayerPaths
            self.updates += 1
            spec: Any = cast(Any, Sdf).CreatePrimInLayer(stage.GetRootLayer(), state.camera_path)
            spec.specifier = cast(Any, Sdf).SpecifierDef
            spec.typeName = "Camera"
            await asyncio.sleep(0)

    app: PendingCameraUpdates = PendingCameraUpdates()
    await controller._remove_after_updates(state, app)
    assert app.updates == 3
    assert len(stage.GetSessionLayer().subLayerPaths) == 0
    assert stage.GetRootLayer().ExportToString() == saved


def run_checks() -> dict[str, bool]:
    check_methods()
    check_cameras()
    asyncio.run(check_deferred_camera_cleanup())
    return {
        "passed": True,
        "shared_canvas_switches": True,
        "world_axis_constraints": True,
        "per_stage_cursor": True,
        "session_only_axis_cameras": True,
    }
