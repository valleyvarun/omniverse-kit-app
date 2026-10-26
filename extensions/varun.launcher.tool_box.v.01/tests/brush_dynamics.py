"""Run with --exec in a disposable 3d-drawing app; enable ui_test and renderer.capture."""

import asyncio
import ctypes
import gc
import json
import logging
import math
import traceback
from pathlib import Path
from time import perf_counter
from typing import Any, cast

import carb.input as ci
import carb.settings
import numpy as np
import omni.kit.actions.core
import omni.kit.app
import omni.kit.renderer.capture
import omni.kit.ui_test
from numpy.typing import NDArray
from omni.kit.ui_test.input import emulate_keyboard, emulate_mouse
from omni.kit.viewport.window import get_viewport_window_instances
from pxr import Gf, Usd, UsdGeom, UsdShade
from tool_box.core.controller import ToolController
from tool_box.extension import ToolBoxExtension
from tool_box.tools.drawing.brush import (
    SHAPES,
    BrushConfig,
    BrushSample,
    BrushShape,
    PressureTarget,
)
from tool_box.tools.drawing.curves.mesh import CAP_RINGS, PipeMesh
from tool_box.tools.drawing.pen import PenInfo, PenInput, PointerInfo
from tool_box.tools.drawing.points.mesh import CHUNK_SEGMENTS, JOIN_CONTEXT, PaintMesh
from tool_box.tools.drawing.properties import ThreeDDrawPropertiesPanel
from tool_box.tools.drawing.shapes import outline


def check_ink_messages() -> None:
    """Exercise the callback ABI and normalization; this is not a physical pen test."""
    assert ctypes.sizeof(PointerInfo) == 96 and ctypes.sizeof(PenInfo) == 120

    class User32:
        extra: int = 0xFF515700

        def GetPointerPenInfo(self, pointer_id: int, packet: Any) -> bool:
            pen: PenInfo = ctypes.cast(packet, ctypes.POINTER(PenInfo)).contents
            pen.pointerInfo.pointerFlags = 4
            pen.penMask = 1
            pen.pressure = 256
            return pointer_id == 7

        def GetMessageExtraInfo(self) -> int:
            return self.extra

    class Comctl32:
        def DefSubclassProc(self, hwnd: int, message: int, wp: int, lp: int) -> int:
            return 123

    user32: User32 = User32()
    pen: PenInput = PenInput()
    pen._user32 = user32
    pen._comctl32 = Comctl32()
    assert pen._message(0, 0x246, 7, 0, 0, 0) == 123 and pen.pressure == 0.25
    pen._message(0, 0x200, 0, 0, 0, 0)
    assert pen.pressure == 0.25
    user32.extra = 0
    pen._message(0, 0x200, 0, 0, 0, 0)
    assert pen.pressure is None


def check_geometry() -> dict[str, object]:
    cfg: BrushConfig = BrushConfig()
    cfg.set_brush_radius(8.0)
    cfg.set_hardness(0.8)
    cfg.set_opacity(0.6)
    assert cfg.sample(0.25) == BrushSample(8.0, 0.8, 0.6)
    target: PressureTarget
    targets: tuple[PressureTarget, ...] = ("size", "hardness", "opacity")
    for target in targets:
        cfg.set_pressure(target, True)
    assert cfg.sample(0.25) == BrushSample(2.0, 0.2, 0.15)
    assert cfg.sample(None) == BrushSample(8.0, 0.8, 0.6)
    cfg.set_pressure("size", False)
    assert cfg.sample(0.5) == BrushSample(8.0, 0.4, 0.3)

    stage: Any = cast(Any, Usd.Stage).CreateInMemory()
    shape: BrushShape
    for shape in SHAPES:
        paint: PaintMesh = PaintMesh(stage, "/World/ThreeDDraw/Test_" + shape, (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), shape)
        paint.add((0.0, 0.0, 0.0), BrushSample(4.0, 0.25, 0.5))
        paint.flush()
        mesh: Any = cast(Any, UsdGeom.Mesh).Get(stage, "/World/ThreeDDraw/Test_" + shape + "/Paint_0000")
        positions: NDArray[np.float64] = np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float64)
        prim: Any = mesh.GetPrim()
        # The shader uses pressure samples to shape a connected planar stroke.
        assert list(prim.GetAttribute("paint:radius").Get()) == [4.0]
        assert list(prim.GetAttribute("paint:hardness").Get()) == [0.25]
        assert list(prim.GetAttribute("paint:opacity").Get()) == [0.5]
        assert prim.GetAttribute("paint:shape").Get() == shape
        assert len(positions) == 4
        assert np.isfinite(positions).all() and np.allclose(positions[:, 1], 0)

        pipe: PipeMesh = PipeMesh(stage, "/Pipe_" + shape, (0.0, 0.0, 0.0), 8.0, (0.0, 1.0, 0.0), shape, 1.0)
        pipe.add((10.0, 0.0, 0.0), 2.0)
        pipe.add((20.0, 0.0, 0.0), 4.0)
        assert pipe.flush()
        tube: Any = cast(Any, UsdGeom.Mesh).Get(stage, "/Pipe_" + shape + "/Tube_0000")
        vertices: NDArray[np.float64] = np.asarray(tube.GetPointsAttr().Get(), dtype=np.float64)
        normals: NDArray[np.float64] = np.asarray(tube.GetNormalsAttr().Get(), dtype=np.float64)
        assert np.isfinite(vertices).all() and np.isfinite(normals).all()
        assert np.allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-5)
        body_count: int = len(vertices) - 2 * (CAP_RINGS * len(outline(shape)) + 1)
        radial: NDArray[np.float64] = np.linalg.norm(vertices[:body_count, 1:], axis=1)
        factor: float = math.sqrt(2) if shape == "square" else 1.0
        assert abs(radial.min() - factor) < 1e-5 and abs(radial.max() - 4 * factor) < 1e-5
        counts: NDArray[np.int32] = np.asarray(tube.GetFaceVertexCountsAttr().Get(), dtype=np.int32)
        indices: NDArray[np.int32] = np.asarray(tube.GetFaceVertexIndicesAttr().Get(), dtype=np.int32)
        assert counts.sum() == len(indices) and indices.max() < len(vertices)

    long_paint: PaintMesh = PaintMesh(stage, "/World/ThreeDDraw/Long", (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), "circle")
    timings: list[float] = []
    frozen: Any = None
    index: int
    for index in range(1500):
        start: float = perf_counter()
        long_paint.add((index * 0.8, 0.0, math.sin(index * 0.03)), BrushSample(4.0, 0.4, 0.5))
        long_paint.flush()
        timings.append((perf_counter() - start) * 1000)
        if index == 500:
            frozen = stage.GetPrimAtPath("/World/ThreeDDraw/Long/Paint_0000").GetAttribute("points").Get()
    assert frozen == stage.GetPrimAtPath("/World/ThreeDDraw/Long/Paint_0000").GetAttribute("points").Get()
    chunks: list[Any] = list(stage.GetPrimAtPath("/World/ThreeDDraw/Long").GetChildren())
    for index, prim in enumerate(chunks):
        # Allow the provisional end segment and the two stroke caps.
        assert len(prim.GetAttribute("points").Get()) <= (CHUNK_SEGMENTS + JOIN_CONTEXT + 3) * 4
        assert np.allclose(prim.GetAttribute("paint:radius").Get(), 4.0)
        if index + 1 < len(chunks):
            section: int = (len(prim.GetAttribute("paint:centerline").Get()) - 2) * 4
            positions = np.asarray(prim.GetAttribute("points").Get(), dtype=np.float64)
            following: NDArray[np.float64] = np.asarray(
                chunks[index + 1].GetAttribute("points").Get(), dtype=np.float64
            )
            np.testing.assert_array_equal(positions[[section + 1, section + 2]], following[[0, 3]])
    reopened: Any = cast(Any, Usd.Stage).CreateInMemory()
    assert reopened.GetRootLayer().ImportFromString(stage.GetRootLayer().ExportToString())
    for shape in SHAPES:
        prim = stage.GetPrimAtPath("/World/ThreeDDraw/Test_" + shape + "/Paint_0000")
        restored: Any = reopened.GetPrimAtPath(prim.GetPath())
        attribute: Any
        for attribute in prim.GetAttributes():
            assert restored.GetAttribute(attribute.GetName()).Get() == attribute.Get()
        original_material: Any = cast(Any, UsdShade.MaterialBindingAPI)(prim).ComputeBoundMaterial()[0]
        restored_material: Any = cast(Any, UsdShade.MaterialBindingAPI)(restored).ComputeBoundMaterial()[0]
        assert restored_material.GetPath() == original_material.GetPath()
        original_shader: Any = original_material.GetSurfaceOutput("glslfx").GetConnectedSource()[0].GetPrim()
        restored_shader: Any = restored_material.GetSurfaceOutput("glslfx").GetConnectedSource()[0].GetPrim()
        assert cast(Any, UsdShade.Shader)(restored_shader).GetSourceCode("glslfx") == cast(Any, UsdShade.Shader)(
            original_shader
        ).GetSourceCode("glslfx")
    return {
        "pressure_mapping": True,
        "shapes_and_taper": True,
        "frozen_chunks_unchanged": True,
        "continuous_chunk_sections": True,
        "usd_roundtrip_with_embedded_shader": True,
        "paint_median_ms": float(np.median(timings)),
        "paint_p95_ms": float(np.percentile(timings, 95)),
    }


async def run() -> None:
    app: Any = omni.kit.app.get_app()
    output: Path = Path(
        str(carb.settings.get_settings().get("/exts/tool_box/brushTestOutput") or "_build/brush_dynamics_checks")
    )
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}

    async def settle(n: int = 20) -> None:
        i: int
        for i in range(n):
            await app.next_update_async()

    async def capture(name: str) -> None:
        await settle()
        omni.kit.renderer.capture.acquire_renderer_capture_interface().capture_next_frame_swapchain(
            str(output / (name + ".png"))
        )
        await settle(40)

    try:
        carb.settings.get_settings().set("/app/file/ignoreUnsavedOnExit", True)
        await settle(100)
        ext: ToolBoxExtension = next(obj for obj in gc.get_objects() if type(obj) is ToolBoxExtension)
        assert ext._controller is not None and ext._window is not None
        tools: ToolController = ext._controller
        panel: ThreeDDrawPropertiesPanel = next(
            item for item in ext._window._panels if isinstance(item, ThreeDDrawPropertiesPanel)
        )
        viewport: Any = next(iter(cast(Any, get_viewport_window_instances)(usd_context_name=None)))
        stage: Any = viewport.viewport_api.usd_context.get_stage()
        registry: Any = omni.kit.actions.core.get_action_registry()
        cfg: BrushConfig = BrushConfig.get()
        check_ink_messages()
        report["ink_callback_with_test_packets"] = True
        report["geometry"] = check_geometry()
        registry.execute_action("tool_box", "draw_points")
        await settle()
        report["ink_hook"] = PenInput.get().available
        assert PenInput.get().available, "Windows Ink hook was not installed"
        assert set(panel._models) == {"size", "hardness", "opacity"}
        assert len(panel._buttons) == 3
        panel._toggle("size")
        assert cfg.pressure["size"] and cast(Any, panel._buttons["size"]).selected
        panel._toggle("size")
        await capture("paint_controls")
        cfg.set_brush_radius(6.0)
        shape: BrushShape
        z: float
        index: int
        hardness: float
        opacity: float
        for index, shape in enumerate(SHAPES):
            cfg.set_shape(shape)
            for z, hardness, opacity in ((-20.0, 1.0, 1.0), (0.0, 0.1, 1.0), (20.0, 1.0, 0.25)):
                cfg.set_hardness(hardness)
                cfg.set_opacity(opacity)
                tools.points._on_draw_begin(((index - 1) * 24.0, 3.0, z))
                tools.points._on_draw_end()
        registry.execute_action("tool_box", "draw_curves")
        await settle()
        assert set(panel._models) == {"size"}
        report["tool_specific_settings"] = True
        await capture("pipe_controls")
        for index, shape in enumerate(SHAPES):
            cfg.set_shape(shape)
            tools.curves._on_draw_begin((-30.0, 5.0, 45.0 + index * 14))
            tools.curves._on_draw_change((0.0, 5.0, 45.0 + index * 14))
            tools.curves._on_draw_change((30.0, 5.0, 45.0 + index * 14))
            tools.curves._on_draw_end()
        tools.cancel()
        camera: Any = cast(Any, UsdGeom.Camera).Define(stage, "/World/BrushTestCamera")
        camera.CreateProjectionAttr().Set("orthographic")
        camera.CreateHorizontalApertureAttr().Set(1600.0)
        camera.CreateVerticalApertureAttr().Set(1600.0)
        camera.AddTransformOp().Set(
            Gf.Matrix4d().SetLookAt(Gf.Vec3d(0, 150, 30), Gf.Vec3d(0, 0, 30), Gf.Vec3d(0, 0, -1)).GetInverse()
        )
        viewport.viewport_api.camera_path = str(camera.GetPath())
        assert viewport.viewport_api.render_mode == "HdStormRendererPlugin"
        assert not app.get_extension_manager().is_extension_enabled("omni.hydra.rtx")
        await capture("brush_shapes")
        stage.GetRootLayer().Export(str(output / "brush_shapes.usda"))
        stage.RemovePrim("/World/ThreeDDraw")
        cfg.set_shape("circle")
        cfg.set_brush_radius(4.0)
        cfg.set_hardness(0.5)
        registry.execute_action("tool_box", "draw_points")
        await settle()
        viewport.focus()
        bounds: Any = viewport.frame
        cx: float = float(bounds.screen_position_x + bounds.computed_width * 0.5)
        cy: float = float(bounds.screen_position_y + bounds.computed_height * 0.5)
        await emulate_mouse(ci.MouseEventType.MOVE, omni.kit.ui_test.Vec2(cx, cy))
        await settle(4)
        await emulate_keyboard(ci.KeyboardEventType.KEY_PRESS, ci.KeyboardInput.LEFT_CONTROL)
        await emulate_keyboard(ci.KeyboardEventType.KEY_PRESS, ci.KeyboardInput.LEFT_ALT)
        await settle(3)
        await emulate_mouse(ci.MouseEventType.LEFT_BUTTON_DOWN)
        await settle(3)
        await emulate_mouse(ci.MouseEventType.MOVE, omni.kit.ui_test.Vec2(cx + 40, cy - 60))
        await settle(4)
        report["adjusted_size"] = cfg.brush_radius
        report["adjusted_hardness"] = cfg.hardness
        assert cfg.brush_radius > 4.0 and cfg.hardness > 0.5
        await emulate_mouse(ci.MouseEventType.MOVE, omni.kit.ui_test.Vec2(cx, cy))
        await settle(4)
        assert abs(cfg.brush_radius - 4.0) < 0.05 and abs(cfg.hardness - 0.5) < 0.02
        report["both_adjustment_directions"] = True
        assert not stage.GetPrimAtPath("/World/ThreeDDraw")
        await emulate_keyboard(ci.KeyboardEventType.KEY_RELEASE, ci.KeyboardInput.LEFT_ALT)
        await emulate_keyboard(ci.KeyboardEventType.KEY_RELEASE, ci.KeyboardInput.LEFT_CONTROL)
        await settle(2)
        await emulate_mouse(ci.MouseEventType.MOVE, omni.kit.ui_test.Vec2(cx + 60, cy - 60))
        await settle(3)
        await emulate_mouse(ci.MouseEventType.MOVE, omni.kit.ui_test.Vec2(cx + 120, cy - 60))
        await settle(3)
        await emulate_mouse(ci.MouseEventType.LEFT_BUTTON_UP)
        await settle(4)
        assert stage.GetPrimAtPath("/World/ThreeDDraw")
        report["modifier_release_resumes_paint"] = True
        tools.cancel()
        report["passed"] = True
    except Exception:
        logging.getLogger(__name__).exception("Brush dynamics validation failed")
        report["error"] = traceback.format_exc()
        report["passed"] = False
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2))
        print("BRUSH_TEST " + json.dumps(report), flush=True)
        app.post_quit(0 if report.get("passed") else 1)


task: asyncio.Future[None] = asyncio.ensure_future(run())
