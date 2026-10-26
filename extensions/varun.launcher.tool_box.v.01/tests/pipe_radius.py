"""Run in a disposable 3d-drawing app with renderer.capture and ui_test enabled."""

import asyncio
import gc
import json
import logging
import math
from pathlib import Path
from time import perf_counter
from typing import Any, cast

import carb.input
import carb.settings
import numpy as np
import omni.kit.actions.core
import omni.kit.app
import omni.kit.renderer.capture
import omni.kit.ui_test
from numpy.typing import NDArray
from omni.kit.ui_test.input import emulate_key_combo, emulate_mouse
from omni.kit.viewport.window import get_viewport_window_instances
from pxr import Gf, Usd, UsdGeom
from tool_box.core.controller import ToolController
from tool_box.extension import ToolBoxExtension
from tool_box.tools.drawing.brush import BrushConfig
from tool_box.tools.drawing.curves.mesh import CAP_RINGS, CHUNK_RINGS, SIDES, PipeMesh
from tool_box.tools.drawing.curves.tool import DRAW_ROOT, LIVE_STROKE_PATH

LOGGER: logging.Logger = logging.getLogger(__name__)


def check_pipe(stage: Any, path: str, radius: float) -> int:
    """Check actual USD topology, round cross sections, smooth normals, and seams."""
    previous: NDArray[np.float64] | None = None
    previous_normals: NDArray[np.float64] | None = None
    vertices: int = 0
    caps: int = 0
    prim: Any
    for prim in stage.GetPrimAtPath(path).GetChildren():
        assert prim.GetTypeName() == "Mesh"
        mesh: Any = cast(Any, UsdGeom.Mesh)(prim)
        points: NDArray[np.float64] = np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float64)
        normals: NDArray[np.float64] = np.asarray(mesh.GetNormalsAttr().Get(), dtype=np.float64)
        counts: NDArray[np.int32] = np.asarray(mesh.GetFaceVertexCountsAttr().Get(), dtype=np.int32)
        cap_count: int = int(np.count_nonzero(counts == 3)) // SIDES
        caps += cap_count
        indices: NDArray[np.int32] = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(), dtype=np.int32)
        assert counts.sum() == len(indices) and indices.min() >= 0 and indices.max() < len(points)
        assert len(points) == len(normals) and np.isfinite(points).all() and np.isfinite(normals).all()
        assert mesh.GetNormalsInterpolation() == "vertex" and mesh.GetSubdivisionSchemeAttr().Get() == "none"
        rings: int = (len(points) - cap_count * (CAP_RINGS * SIDES + 1)) // SIDES
        assert 2 <= rings <= CHUNK_RINGS
        surface: NDArray[np.float64] = points[: rings * SIDES].reshape(rings, SIDES, 3)
        radial: NDArray[np.float64] = surface - surface.mean(axis=1, keepdims=True)
        assert np.allclose(np.linalg.norm(radial, axis=2), radius, atol=2e-4)
        assert np.allclose(radial.reshape(-1, 3) / radius, normals[: rings * SIDES], atol=2e-4)
        assert np.allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-5)
        cap_index: int
        for cap_index in range(cap_count):
            start_cap: bool = previous is None and cap_index == 0
            center: NDArray[np.float64] = surface[0 if start_cap else -1].mean(axis=0)
            offset: int = rings * SIDES + cap_index * (CAP_RINGS * SIDES + 1)
            dome: NDArray[np.float64] = points[offset : offset + CAP_RINGS * SIDES + 1] - center
            assert np.allclose(np.linalg.norm(dome, axis=1), radius, atol=2e-4)
            assert np.allclose(dome / radius, normals[offset : offset + len(dome)], atol=2e-4)
            assert np.linalg.norm(dome[-1]) > radius * 0.99
        if previous is not None:
            assert previous_normals is not None
            assert np.array_equal(previous, surface[0]), "Pipe chunk seam has a crack"
            assert np.array_equal(previous_normals, normals[:SIDES]), "Pipe chunk seam has a shading break"
        previous = surface[-1]
        previous_normals = normals[(rings - 1) * SIDES : rings * SIDES]
        vertices += len(points)
    assert vertices > 0 and caps == 2
    return vertices


def benchmark() -> dict[str, object]:
    stage: Any = cast(Any, Usd.Stage).CreateInMemory()
    pipe: PipeMesh = PipeMesh(stage, "/Pipe", (0.0, 0.0, 0.0), 2.0, (0.0, 1.0, 0.0))
    timings: list[float] = []
    frozen: Any = None
    index: int
    for index in range(1, 1501):
        start: float = perf_counter()
        pipe.add((float(index), 0.0, 20.0 * math.sin(index * 0.03)))
        pipe.flush()
        timings.append((perf_counter() - start) * 1000.0)
        if index == 500:
            frozen = stage.GetPrimAtPath("/Pipe/Tube_0000").GetAttribute("points").Get()
    assert frozen == stage.GetPrimAtPath("/Pipe/Tube_0000").GetAttribute("points").Get()
    return {
        "samples": 1500,
        "vertices": check_pipe(stage, "/Pipe", 2.0),
        "median_ms": float(np.median(timings)),
        "p95_ms": float(np.percentile(timings, 95)),
        "early_median_ms": float(np.median(timings[100:400])),
        "late_median_ms": float(np.median(timings[-300:])),
        "completed_chunk_unchanged": True,
    }


async def run() -> None:
    app: Any = omni.kit.app.get_app()
    settings: Any = carb.settings.get_settings()
    output: Path = Path(settings.get("/exts/tool_box/pipeTestOutput") or "_build/pipe_geometry_checks")
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}

    async def settle(count: int = 20) -> None:
        frame: int
        for frame in range(count):
            await app.next_update_async()

    async def capture(name: str) -> None:
        await settle(30)
        interface: Any = omni.kit.renderer.capture.acquire_renderer_capture_interface()
        interface.capture_next_frame_swapchain(str(output / f"{name}.png"))
        await settle(60)
        assert (output / f"{name}.png").is_file()

    try:
        settings.set("/app/file/ignoreUnsavedOnExit", True)
        await settle(100)
        extension: ToolBoxExtension | None = None
        obj: Any
        for obj in gc.get_objects():
            if type(obj) is ToolBoxExtension:
                extension = obj
                break
        assert extension is not None and extension._controller is not None and extension._window is not None
        controller: ToolController = extension._controller
        viewport: Any = next(iter(cast(Any, get_viewport_window_instances)(usd_context_name=None)))
        assert viewport.viewport_api.render_mode == "HdStormRendererPlugin"
        manager: Any = app.get_extension_manager()
        assert not manager.is_extension_enabled("omni.hydra.rtx")
        assert not manager.is_extension_enabled("omni.kit.viewport.rtx")
        report["storm_without_rtx"] = True
        stage: Any = viewport.viewport_api.usd_context.get_stage()
        registry: Any = omni.kit.actions.core.get_action_registry()
        action_id: str
        name: str
        for action_id, name in (("draw_points", "3D paint"), ("draw_curves", "3D pipe")):
            assert controller.tools[action_id].name == name
            assert extension._window._buttons[action_id].tooltip.startswith(name)
            assert registry.get_action("tool_box", action_id).display_name == name
        report["tool_names"] = True

        registry.execute_action("tool_box", "draw_curves")
        await settle()
        radius: float
        z: float
        widget_type: str
        for radius, z, widget_type in ((2.0, -25.0, "FloatField"), (8.0, 25.0, "FloatSlider")):
            control: Any = omni.kit.ui_test.find_all(f"Tool Box//Frame/**/{widget_type}[*]")[0].widget
            control.model.set_value(radius)
            await settle(5)
            assert BrushConfig.get().brush_radius == radius
            controller.curves._on_draw_begin((-35.0, 12.0, z))
            controller.curves._on_draw_change((0.0, 12.0, z))
            controller.curves._on_draw_change((35.0, 12.0, z))
            await settle(5)
            check_pipe(stage, LIVE_STROKE_PATH, radius)
            controller.curves._on_draw_end()
            assert not stage.GetPrimAtPath(LIVE_STROKE_PATH)
            saved: Any = stage.GetPrimAtPath(DRAW_ROOT).GetChildren()[-1]
            check_pipe(stage, str(saved.GetPath()), radius)
        report["live_and_saved_radii"] = [2.0, 8.0]
        controller.cancel()
        camera: Any = cast(Any, UsdGeom.Camera).Define(stage, "/World/PipeCheckCamera")
        transform: Any = camera.AddTransformOp()
        transform.Set(
            Gf.Matrix4d().SetLookAt(Gf.Vec3d(90, 120, 100), Gf.Vec3d(0, 10, 0), Gf.Vec3d(0, 1, 0)).GetInverse()
        )
        viewport.viewport_api.camera_path = str(camera.GetPath())
        await capture("pipe_radii")
        stage.RemovePrim(DRAW_ROOT)

        # A real curved mouse drag, including keyboard activation and mouse-up.
        transform.Set(Gf.Matrix4d().SetLookAt(Gf.Vec3d(0, 160, 0), Gf.Vec3d(0, 0, 0), Gf.Vec3d(0, 0, -1)).GetInverse())
        BrushConfig.get().set_brush_radius(3.0)
        await settle()
        viewport.focus()
        bounds: Any = viewport.frame
        cx: float = float(bounds.screen_position_x + bounds.computed_width * 0.5)
        cy: float = float(bounds.screen_position_y + bounds.computed_height * 0.55)
        drag_radius: float = min(float(bounds.computed_width), float(bounds.computed_height)) * 0.25
        await emulate_mouse(carb.input.MouseEventType.MOVE, omni.kit.ui_test.Vec2(cx + drag_radius, cy))
        await settle(4)
        await emulate_key_combo("SHIFT+D", human_delay_speed=100)
        await settle(4)
        assert controller.curves._is_active
        await emulate_mouse(carb.input.MouseEventType.LEFT_BUTTON_DOWN)
        await settle(3)
        index: int
        for index in range(1, 121):
            angle: float = index * 1.5 * math.pi / 120
            await emulate_mouse(
                carb.input.MouseEventType.MOVE,
                omni.kit.ui_test.Vec2(cx + drag_radius * math.cos(angle), cy + drag_radius * math.sin(angle)),
            )
            await settle(1)
        await capture("curved_live")
        assert stage.GetPrimAtPath(LIVE_STROKE_PATH)
        await emulate_mouse(carb.input.MouseEventType.LEFT_BUTTON_UP)
        await settle(5)
        assert not stage.GetPrimAtPath(LIVE_STROKE_PATH)
        saved = stage.GetPrimAtPath(DRAW_ROOT).GetChildren()[-1]
        report["curved_drag_vertices"] = check_pipe(stage, str(saved.GetPath()), 3.0)
        await emulate_key_combo("ESCAPE", human_delay_speed=100)
        await capture("curved_top")
        transform.Set(Gf.Matrix4d().SetLookAt(Gf.Vec3d(80, 65, 100), Gf.Vec3d(0, 0, 0), Gf.Vec3d(0, 1, 0)).GetInverse())
        await capture("curved_oblique")
        transform.Set(Gf.Matrix4d().SetLookAt(Gf.Vec3d(110, 6, 0), Gf.Vec3d(0, 0, 0), Gf.Vec3d(0, 1, 0)).GetInverse())
        await capture("curved_side")
        stage.GetRootLayer().Export(str(output / "curved_pipe.usda"))
        report["long_stroke_cpu"] = benchmark()
        report["passed"] = True
    except Exception:
        LOGGER.exception("3D pipe validation failed")
        report["passed"] = False
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("PIPE_GEOMETRY " + json.dumps(report), flush=True)
        app.post_quit(0 if report.get("passed") else 1)


task: asyncio.Future[None] = asyncio.ensure_future(run())
