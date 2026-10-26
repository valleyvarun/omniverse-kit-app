"""Run in a disposable drawing app with ui_test and renderer.capture enabled."""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import math
import traceback
from pathlib import Path
from typing import Any, TypeAlias, cast

import carb.input as ci
import carb.settings
import carb.windowing
import numpy as np
import omni.appwindow
import omni.kit.app
import omni.kit.renderer.capture
import omni.kit.ui_test
import omni.ui
from numpy.typing import NDArray
from omni.kit.viewport.window import get_viewport_window_instances
from pxr import Gf, Usd, UsdGeom
from tool_box.core.controller import ToolController
from tool_box.extension import ToolBoxExtension
from tool_box.tools.drawing.brush import (
    SHAPES,
    BrushConfig,
    BrushSample,
    BrushShape,
    minimum_point_distance,
)
from tool_box.tools.drawing.curves.mesh import CAP_RINGS, PipeMesh
from tool_box.tools.drawing.curves.tool import ThreeDDrawCurvesTool
from tool_box.tools.drawing.points.mesh import PaintMesh
from tool_box.tools.drawing.points.tool import ThreeDDrawTool
from tool_box.tools.drawing.shapes import outline

OUTPUT: Path = Path(__file__).resolve().parents[4] / "_build/slow_stroke_checks/final"
Vec3: TypeAlias = tuple[float, float, float]


async def emulate_mouse(event_type: ci.MouseEventType, pos: omni.kit.ui_test.Vec2 | None = None) -> None:
    """Inject Kit events without moving the user's physical cursor."""
    window: Any = omni.appwindow.get_default_app_window()
    position: omni.kit.ui_test.Vec2 = (
        pos if pos is not None else omni.kit.ui_test.Vec2()
    ) * omni.ui.Workspace.get_dpi_scale()
    provider: Any = ci.acquire_input_provider()
    provider.buffer_mouse_event(
        window.get_mouse(),
        event_type,
        (
            position.x / omni.ui.Workspace.get_main_window_width(),
            position.y / omni.ui.Workspace.get_main_window_height(),
        ),
        0,
        position.to_tuple(),
    )


def samples(kind: int, count: int = 1200) -> list[Vec3]:
    result: list[Vec3] = []
    index: int
    for index in range(count):
        t: float = index / (count - 1)
        if kind == 0:
            x: float = 34 * t
            z: float = 0.07 * math.sin(index * 1.7)
        elif kind == 1:
            x = 12 * math.sin(t * math.tau * 2)
            z = 5 * math.sin(t * math.tau * 4)
        elif kind == 2:
            x = 15 * math.sin(t * math.tau * 6)
            z = 0.15 * math.cos(t * math.tau * 6)
        else:
            x = 34 * t + 1.2 * math.sin(t * math.tau * 12)
            z = 1.2 * math.cos(t * math.tau * 12)
        result.append((x, 0.0, z))
    return result


def check_geometry() -> dict[str, int]:
    stage: Any = cast(Any, Usd.Stage).CreateInMemory()
    result: dict[str, int] = {}
    shape: BrushShape
    kind: int
    point: Vec3 = (0.0, 0.0, 0.0)
    for shape in SHAPES:
        for kind in range(4):
            path: str = f"/World/ThreeDDraw/{shape}_{kind}"
            paint: PaintMesh = PaintMesh(stage, path, (1, 0, 0), (0, 0, -1), shape)
            sample: BrushSample = BrushSample(2.5, 0.25, 0.65)
            for point in samples(kind):
                paint.add(point, sample)
                paint.flush()
            saved: str = stage.GetRootLayer().ExportToString()
            index: int
            for index in range(60):
                assert not paint.add(point, sample)
                paint.flush()
            assert saved == stage.GetRootLayer().ExportToString()
            points_count: int = 0
            prim: Any
            for prim in stage.GetPrimAtPath(path).GetChildren():
                centers: NDArray[np.float64] = np.asarray(prim.GetAttribute("paint:centerline").Get(), dtype=np.float64)
                gaps: NDArray[np.float64] = np.linalg.norm(np.diff(centers, axis=0), axis=1)
                assert np.all(gaps >= minimum_point_distance(2.5, 2.5) - 1e-5), (shape, kind, gaps.min())
                points_count += len(centers)
                vertices: NDArray[np.float64] = np.asarray(prim.GetAttribute("points").Get(), dtype=np.float64)
                indices: NDArray[np.int32] = np.asarray(prim.GetAttribute("faceVertexIndices").Get(), dtype=np.int32)
                triangles: NDArray[np.float64] = vertices[indices.reshape(-1, 3)]
                normals: NDArray[np.float64] = np.cross(
                    triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
                ).astype(np.float64, copy=False)
                paired: NDArray[np.float64] = np.sum(normals[::2] * normals[1::2], axis=1)
                assert np.all(paired >= -1e-6), (shape, kind, paired.min())
            assert points_count < 600
            result[f"{shape}_{kind}"] = points_count

        pipe: PipeMesh = PipeMesh(stage, "/Pipe_" + shape, (0, 0, 0), 2.5, (0, 1, 0), shape)
        for point in samples(0):
            pipe.add(point)
            pipe.flush()
        assert not pipe.add(point)
        sides: int = len(outline(shape))
        children: list[Any] = list(stage.GetPrimAtPath("/Pipe_" + shape).GetChildren())
        for index, prim in enumerate(children):
            vertices = np.asarray(prim.GetAttribute("points").Get(), dtype=np.float64)
            caps: int = int(index == 0) + int(index == len(children) - 1)
            centers = vertices[: len(vertices) - caps * (CAP_RINGS * sides + 1)].reshape(-1, sides, 3).mean(axis=1)
            gaps = np.linalg.norm(np.diff(centers, axis=0), axis=1)
            assert np.all(gaps >= 0.5 - 1e-5), (shape, gaps.min())
    return result


async def run() -> None:
    app: Any = omni.kit.app.get_app()
    report: dict[str, object] = {}
    OUTPUT.mkdir(parents=True, exist_ok=True)

    async def settle(count: int = 20) -> None:
        frame: int
        for frame in range(count):
            await app.next_update_async()

    async def capture(name: str) -> None:
        path: Path = OUTPUT / f"{name}.png"
        path.unlink(missing_ok=True)
        await settle(20)
        omni.kit.renderer.capture.acquire_renderer_capture_interface().capture_next_frame_swapchain(str(path))
        frame: int
        for frame in range(200):
            await app.next_update_async()
            if path.exists():
                return
        raise AssertionError(f"Capture missing: {path}")

    try:
        carb.settings.get_settings().set("/app/file/ignoreUnsavedOnExit", True)
        await settle(130)
        carb.windowing.acquire_windowing_interface().hide_window(omni.appwindow.get_default_app_window().get_window())
        report["geometry"] = check_geometry()
        obj: object
        ext: ToolBoxExtension | None = None
        for obj in gc.get_objects():
            if type(obj) is ToolBoxExtension:
                ext = obj
                break
        assert ext is not None and ext._controller is not None
        tools: ToolController = ext._controller
        viewport: Any = next(iter(get_viewport_window_instances(usd_context_name=None)))
        api: Any = viewport.viewport_api
        stage: Any = api.stage
        camera: Any = cast(Any, UsdGeom.Camera).Define(stage, "/World/SlowCamera")
        camera.CreateProjectionAttr().Set("orthographic")
        camera.CreateHorizontalApertureAttr().Set(1000.0)
        camera.CreateVerticalApertureAttr().Set(1000.0)
        camera.CreateClippingRangeAttr().Set((0.1, 1000.0))
        camera.AddTransformOp().Set(
            Gf.Matrix4d().SetLookAt(Gf.Vec3d(0, 140, 0), Gf.Vec3d(0), Gf.Vec3d(0, 0, -1)).GetInverse()
        )
        api.camera_path = str(camera.GetPath())
        cfg: BrushConfig = BrushConfig.get()
        cfg.set_brush_radius(2.5)
        cfg.set_hardness(0.25)
        cfg.set_opacity(0.65)
        tool: ThreeDDrawTool | ThreeDDrawCurvesTool
        row: int
        for row, tool in enumerate((tools.points, tools.curves)):
            tool.activate()
            await settle()
            viewport.focus()
            frame: Any = viewport.frame
            cx: float = float(frame.screen_position_x + frame.computed_width * 0.25)
            cy: float = float(frame.screen_position_y + frame.computed_height * (0.35 + row * 0.3))
            await emulate_mouse(ci.MouseEventType.MOVE, omni.kit.ui_test.Vec2(cx, cy))
            await emulate_mouse(ci.MouseEventType.LEFT_BUTTON_DOWN)
            step: int
            for step in range(130):
                await emulate_mouse(
                    ci.MouseEventType.MOVE, omni.kit.ui_test.Vec2(cx + step * 2, cy + math.sin(step * 1.7))
                )
                await settle(2)
                if step == 65:
                    await settle(4)
                    saved: str = stage.GetRootLayer().ExportToString()
                    await settle(120)
                    held: str = stage.GetRootLayer().ExportToString()
                    if saved != held:
                        (OUTPUT / "before_hold.usda").write_text(saved, encoding="utf-8")
                        (OUTPUT / "after_hold.usda").write_text(held, encoding="utf-8")
                    assert saved == held, "Stationary gesture changed the stroke"
                    assert not tool._dirty
            await settle()
            await capture("live_paint" if row == 0 else "live_pipe")
            await emulate_mouse(ci.MouseEventType.LEFT_BUTTON_UP)
            await settle()
            tool.deactivate()
        report["slow_mouse_drags_and_pauses"] = True
        tools.points.activate()
        await settle()
        viewport.focus()
        cy = float(frame.screen_position_y + frame.computed_height * 0.5)
        await emulate_mouse(ci.MouseEventType.MOVE, omni.kit.ui_test.Vec2(cx, cy + 15))
        await emulate_mouse(ci.MouseEventType.LEFT_BUTTON_DOWN)
        for step in range(361):
            angle: float = step * math.tau / 60
            await emulate_mouse(
                ci.MouseEventType.MOVE,
                omni.kit.ui_test.Vec2(cx + step * 0.8 + 15 * math.sin(angle), cy + 15 * math.cos(angle)),
            )
            await settle(2)
        stage.GetRootLayer().Export(str(OUTPUT / "live_tight_loops.usda"))
        assert tools.points._mesh is not None
        point: tuple[Vec3, BrushSample]
        loop_positions: list[Vec3] = []
        for point in tools.points._mesh._points:
            loop_positions.append(point[0])
        loop_centers: NDArray[np.float64] = np.asarray(loop_positions, dtype=np.float64)
        assert float(np.ptp(loop_centers, axis=0).max()) > 15.0, "Loop gesture did not travel across the viewport"
        await capture("live_tight_loops")
        await emulate_mouse(ci.MouseEventType.LEFT_BUTTON_UP)
        await settle()
        tools.points.deactivate()
        report["slow_tight_loops_mouse"] = True
        await capture("slow_finished")
        await capture("idle_a")
        await capture("idle_b")
        report["passed"] = True
    except Exception:
        logging.getLogger(__name__).exception("Slow stroke validation failed")
        report["passed"] = False
        report["error"] = traceback.format_exc()
    finally:
        (OUTPUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)
        app.post_quit(0 if report.get("passed") else 1)


task: asyncio.Future[None] = asyncio.ensure_future(run())
