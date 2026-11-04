"""Bounded Storm viewport check for pipe recognition and branching along an axis."""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import time
import traceback
from pathlib import Path
from typing import Any, cast

import carb.input as ci
import carb.settings
import carb.windowing
import numpy as np
import omni.appwindow
import omni.kit.app
import omni.kit.renderer.capture
import omni.ui
import PIL.Image
from numpy.typing import NDArray
from omni.kit.viewport.window import get_viewport_window_instances
from pxr import Gf, UsdGeom
from tool_box.extension import ToolBoxExtension
from tool_box.tools.drawing import method_runtime
from tool_box.tools.drawing.brush import BrushConfig
from tool_box.tools.drawing.cursor import Cursor3D
from tool_box.tools.drawing.curves.mesh import PipeMesh
from tool_box.tools.drawing.pipe_targets import ORANGE

OUTPUT: Path = Path(__file__).resolve().parents[4] / "_build/pipe_spokes_checks/kit"


async def run() -> None:
    app: Any = omni.kit.app.get_app()
    report: dict[str, object] = {}
    OUTPUT.mkdir(parents=True, exist_ok=True)

    async def settle(count: int = 8) -> None:
        frame: int
        for frame in range(count):
            await app.next_update_async()

    async def capture(name: str) -> None:
        await settle(4)
        omni.kit.renderer.capture.acquire_renderer_capture_interface().capture_next_frame_swapchain(
            str(OUTPUT / f"{name}.png")
        )
        await settle(8)

    async def mouse(event: ci.MouseEventType, x: float, y: float) -> None:
        scale: float = omni.ui.Workspace.get_dpi_scale()
        window: Any = omni.appwindow.get_default_app_window()
        provider: Any = ci.acquire_input_provider()
        provider.buffer_mouse_event(
            window.get_mouse(),
            event,
            (
                x * scale / omni.ui.Workspace.get_main_window_width(),
                y * scale / omni.ui.Workspace.get_main_window_height(),
            ),
            0,
            (x * scale, y * scale),
        )

    try:
        carb.settings.get_settings().set("/app/file/ignoreUnsavedOnExit", True)
        carb.windowing.acquire_windowing_interface().hide_window(omni.appwindow.get_default_app_window().get_window())
        await settle(90)
        viewport: Any = next(iter(get_viewport_window_instances(usd_context_name=None)))
        api: Any = viewport.viewport_api
        api.resolution = (640, 480)
        stage: Any = api.stage
        assert api.render_mode == "HdStormRendererPlugin"
        assert not app.get_extension_manager().is_extension_enabled("omni.hydra.rtx")
        extension: ToolBoxExtension | None = None
        obj: Any
        for obj in gc.get_objects():
            if type(obj) is ToolBoxExtension:
                extension = obj
                break
        assert extension is not None and extension._controller is not None
        camera: Any = cast(Any, UsdGeom.Camera).Define(stage, "/World/PipeRecognitionCamera")
        camera.CreateProjectionAttr().Set("orthographic")
        camera.CreateHorizontalApertureAttr().Set(240)
        camera.CreateVerticalApertureAttr().Set(240)
        camera.AddTransformOp().Set(
            Gf.Matrix4d().SetLookAt(Gf.Vec3d(0, 20, 0), Gf.Vec3d(0), Gf.Vec3d(0, 0, -1)).GetInverse()
        )
        api.camera_path = str(camera.GetPath())
        pipe: PipeMesh = PipeMesh(stage, "/World/ThreeDDraw/Stroke", (-8, 3, 0), 0.4, (0, 1, 0))
        pipe.add((0, 3, 0))
        pipe.add((8, 3, 0))
        pipe.flush()
        extension._controller.curves.activate()
        BrushConfig.get().set_brush_radius(1.0)
        await settle(30)
        scene: Any = Cursor3D.get_scene()
        assert scene is not None
        ndc: tuple[float, float] = scene._project((0, 0, 0.9))
        ground: Any = scene.update(ndc)
        assert abs(ground[1]) < 1e-6
        saved: str = stage.GetRootLayer().ExportToString()
        selected: list[str] = api.usd_context.get_selection().get_selected_prim_paths()
        mesh: Any = cast(Any, UsdGeom.Mesh).Get(stage, "/World/ThreeDDraw/Stroke/Tube_0000")
        method_runtime.set_option("axes_spokes", True)
        recognized: Any = scene.update(ndc)
        assert scene._pipe_hover and recognized[1] > 3.0
        assert np.allclose(mesh.GetDisplayColorAttr().Get()[0], ORANGE)
        await capture("recognized")
        pixels: NDArray[np.uint8] = np.asarray(cast(Any, PIL.Image).open(OUTPUT / "recognized.png").convert("RGB"))
        region: NDArray[np.float64] = pixels[
            pixels.shape[0] // 3 : pixels.shape[0] * 3 // 4, pixels.shape[1] // 4 : pixels.shape[1] * 3 // 4
        ].astype(np.float64)
        orange_pixels: int = int(
            np.count_nonzero(
                (region[:, :, 0] > 70)
                & (region[:, :, 0] > region[:, :, 1] * 1.2)
                & (region[:, :, 1] > region[:, :, 2] * 1.3)
            )
        )
        assert orange_pixels > 100, "Recognized pipe must render orange, not only change USD attributes"
        report["orange_pixels"] = orange_pixels
        assert stage.GetRootLayer().ExportToString() == saved
        assert api.usd_context.get_selection().get_selected_prim_paths() == selected
        deadline: float = time.monotonic() + 1.2
        while time.monotonic() < deadline:
            await settle(1)
        scene.update(ndc)
        assert np.allclose(mesh.GetDisplayColorAttr().Get()[0], (0, 0, 0))
        await capture("expired")
        report["one_second_flash_without_color_or_selection_edits"] = True
        # Actual mouse drag must start at the recognized elevated pipe, not at ground height.
        scene.update((0.9, 0.9))
        viewport.focus()
        bounds: Any = viewport.frame
        x: float = float(bounds.screen_position_x + (ndc[0] + 1) * bounds.computed_width * 0.5)
        y: float = float(bounds.screen_position_y + (1 - ndc[1]) * bounds.computed_height * 0.5)
        await mouse(ci.MouseEventType.MOVE, x, y)
        await settle(3)
        assert scene._pipe_hover
        start_y: float = scene.world_pos[1]
        await mouse(ci.MouseEventType.LEFT_BUTTON_DOWN, x, y)
        await settle(2)
        step: int
        for step in range(1, 9):
            await mouse(ci.MouseEventType.MOVE, x + 16 * step, y)
            await settle(2)
        await mouse(ci.MouseEventType.LEFT_BUTTON_UP, x + 128, y)
        await settle(12)
        branches: list[Any] = []
        prim: Any
        for prim in stage.GetPrimAtPath("/World/ThreeDDraw").GetChildren():
            if prim.GetName().startswith("Stroke_"):
                branches.append(prim)
        assert len(branches) == 1
        points: NDArray[np.float64] = np.asarray(
            branches[0].GetChildren()[0].GetAttribute("points").Get(), dtype=np.float64
        )
        assert abs(float(points[:, 1].min()) - (start_y - 1)) < 0.1
        report["axis_branch_starts_on_pipe"] = True
        method_runtime.set_option("axes_spokes", False)
        assert np.allclose(mesh.GetDisplayColorAttr().Get()[0], (0, 0, 0))
        await capture("branch")
        report["passed"] = True
    except Exception:
        logging.getLogger(__name__).exception("Pipe recognition viewport check failed")
        report["error"] = traceback.format_exc()
        report["passed"] = False
    finally:
        (OUTPUT / "report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)
        app.post_quit(0 if report.get("passed") else 1)


task: asyncio.Future[None] = asyncio.ensure_future(run())
