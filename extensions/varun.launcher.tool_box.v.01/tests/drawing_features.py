"""Small, capped Kit integration check for brush profiles, canvases, and methods."""

from __future__ import annotations

import asyncio
import gc
import json
import logging
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
import omni.kit.ui_test
import omni.ui
from numpy.typing import NDArray
from omni.kit.ui_test.input import emulate_key_combo
from omni.kit.viewport.window import get_viewport_window_instances
from pxr import Gf, UsdGeom
from tool_box.core.controller import ToolController
from tool_box.extension import ToolBoxExtension
from tool_box.tools.drawing import method_runtime
from tool_box.tools.drawing.brush import BrushConfig
from tool_box.tools.drawing.cursor import Cursor3D
from tool_box.tools.drawing.methods import CursorAnchor, DrawingMethods
from tool_box.tools.drawing.properties import ThreeDDrawPropertiesPanel
from tool_box.ui.properties import ToolPropertiesPanel
from tool_box.ui.window import ToolBoxWindow

OUTPUT: Path = Path(__file__).resolve().parents[4] / "_build/shared_canvas_checks/kit"


async def mouse(event_type: ci.MouseEventType, position: omni.kit.ui_test.Vec2) -> None:
    window: Any = omni.appwindow.get_default_app_window()
    scaled: omni.kit.ui_test.Vec2 = position * omni.ui.Workspace.get_dpi_scale()
    provider: Any = ci.acquire_input_provider()
    provider.buffer_mouse_event(
        window.get_mouse(),
        event_type,
        (scaled.x / omni.ui.Workspace.get_main_window_width(), scaled.y / omni.ui.Workspace.get_main_window_height()),
        0,
        scaled.to_tuple(),
    )


async def run() -> None:
    app: Any = omni.kit.app.get_app()
    report: dict[str, object] = {}
    OUTPUT.mkdir(parents=True, exist_ok=True)

    async def settle(count: int = 8) -> None:
        frame: int
        for frame in range(count):
            await app.next_update_async()

    async def capture(name: str) -> None:
        await settle()
        omni.kit.renderer.capture.acquire_renderer_capture_interface().capture_next_frame_swapchain(
            str(OUTPUT / f"{name}.png")
        )
        await settle(12)

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
        assert carb.settings.get_settings().get("/app/runLoops/main/rateLimitFrequency") == 30
        extension: ToolBoxExtension | None = None
        obj: Any
        for obj in gc.get_objects():
            if type(obj) is ToolBoxExtension:
                extension = obj
                break
        assert extension is not None and extension._controller is not None and extension._window is not None
        tools: ToolController = extension._controller
        window: ToolBoxWindow = extension._window
        omni.ui.Workspace.set_dock_id_width(window.window.dock_id, 170)
        right_window: Any = omni.ui.Workspace.get_window("Clayers")
        omni.ui.Workspace.set_dock_id_width(right_window.dock_id, 300)
        await settle(8)
        cfg: BrushConfig = BrushConfig.get()
        camera: Any = cast(Any, UsdGeom.Camera).Define(stage, "/World/FeatureCamera")
        camera.CreateClippingRangeAttr().Set((0.1, 1000.0))
        camera.AddTransformOp().Set(
            Gf.Matrix4d().SetLookAt(Gf.Vec3d(60, 65, 90), Gf.Vec3d(0, 0, 0), Gf.Vec3d(0, 1, 0)).GetInverse()
        )
        api.camera_path = str(camera.GetPath())
        tools.points.activate()
        cfg.set_brush_radius(3.0)
        cfg.set_hardness(0.7)
        cfg.set_pressure("size", True)
        tools.curves.activate()
        assert cfg.brush_radius == 2.0 and not cfg.pressure["size"]
        cfg.set_brush_radius(1.5)
        tools.points.activate()
        assert cfg.brush_radius == 3.0 and cfg.pressure["size"]
        cfg.set_pressure("size", False)
        panel: ThreeDDrawPropertiesPanel | None = None
        candidate: ToolPropertiesPanel
        for candidate in window._panels:
            if isinstance(candidate, ThreeDDrawPropertiesPanel):
                panel = candidate
                break
        assert panel is not None
        assert panel._buttons["size"].image_url.endswith("pressure.svg")
        report["independent_brushes_and_pressure_icon"] = True
        await settle(12)
        viewport.focus()
        bounds: Any = viewport.frame
        center: omni.kit.ui_test.Vec2 = omni.kit.ui_test.Vec2(
            float(bounds.screen_position_x + bounds.computed_width * 0.5),
            float(bounds.screen_position_y + bounds.computed_height * 0.5),
        )

        def roots() -> list[Any]:
            root: Any = stage.GetPrimAtPath("/World/ThreeDDraw")
            result: list[Any] = []
            prim: Any
            if root:
                for prim in root.GetChildren():
                    if prim.GetName().startswith("Stroke"):
                        result.append(prim)
            return result

        async def drag(dx: float, dy: float) -> Any:
            before: int = len(roots())
            await mouse(ci.MouseEventType.MOVE, center)
            await settle(3)
            await mouse(ci.MouseEventType.LEFT_BUTTON_DOWN, center)
            await settle(3)
            step: int
            for step in range(1, 9):
                await mouse(
                    ci.MouseEventType.MOVE, omni.kit.ui_test.Vec2(center.x + dx * step / 8, center.y + dy * step / 8)
                )
                await settle(2)
            await mouse(ci.MouseEventType.LEFT_BUTTON_UP, omni.kit.ui_test.Vec2(center.x + dx, center.y + dy))
            await settle(18)
            assert len(roots()) == before + 1, "Gesture did not create exactly one stroke"
            return roots()[-1]

        # The shared canvas buttons work before any brush is selected.
        tools.cancel()
        await settle()
        assert "axes_spokes" not in tools.tools
        assert set(window._canvas.checkboxes) == {"drawing_plane", "draw_on_object", "cursor_3d"}

        async def click(position: omni.kit.ui_test.Vec2) -> None:
            await mouse(ci.MouseEventType.MOVE, position)
            await settle(2)
            await mouse(ci.MouseEventType.LEFT_BUTTON_DOWN, position)
            await settle(2)
            await mouse(ci.MouseEventType.LEFT_BUTTON_UP, position)
            await settle(6)

        button: omni.ui.Button | None = tools.tools["cursor_3d"]._button
        assert button is not None
        await click(
            omni.kit.ui_test.Vec2(
                button.screen_position_x + button.computed_width / 2,
                button.screen_position_y + button.computed_height / 2,
            )
        )
        assert method_runtime.get_anchor().placing and not cfg.active
        viewport.focus()
        await click(center)
        idle_scene: Any = Cursor3D.get_scene()
        assert method_runtime.get_anchor().position is not None
        assert not method_runtime.get_anchor().placing and not cfg.active and not roots()
        assert method_runtime._placement_suppression is None
        assert idle_scene._anchor_transform.visible and not idle_scene._drag_screen.visible
        report["cursor_button_places_without_brush_or_stroke"] = True
        await capture("shared_canvas_idle")
        tools.tools["cursor_3d"].activate()
        assert method_runtime.get_anchor().placing
        tools.cancel()
        assert not method_runtime.get_anchor().placing and method_runtime._placement_cursor is None
        tools.tools["cursor_3d"].activate()
        tools.points.activate()
        assert method_runtime._placement_cursor is None and not method_runtime.get_anchor().placing
        assert method_runtime._placement_suppression is None
        report["placement_cancel_and_brush_handoff"] = True
        method_runtime.set_option("cursor_3d", False)
        await settle()
        assert window._canvas._frame is not None and window._canvas._frame.visible
        assert not hasattr(panel, "_methods")
        checkbox: omni.ui.CheckBox = window._canvas.checkboxes["draw_on_object"]
        object_button: omni.ui.Button | None = tools.tools["draw_on_object"]._button
        assert object_button is not None and checkbox.screen_position_y > object_button.screen_position_y
        await click(
            omni.kit.ui_test.Vec2(
                checkbox.screen_position_x + checkbox.computed_width / 2,
                checkbox.screen_position_y + checkbox.computed_height / 2,
            )
        )
        assert DrawingMethods.get().draw_on_object
        tools.curves.activate()
        assert DrawingMethods.get("pipe").draw_on_object
        tools.points.activate()
        method_runtime.set_option("draw_on_object", False)
        await settle()
        viewport.focus()
        report["shared_canvas_checkbox_and_active_tool_ui"] = True
        await drag(65, 12)
        report["default_ground_drag"] = True
        method_runtime.set_option("axes_spokes", True)
        stroke: Any = await drag(90, -25)
        centers: list[Any] = []
        child: Any
        for child in stroke.GetChildren():
            attr: Any = child.GetAttribute("paint:centerline")
            if attr and attr.Get():
                centers.extend(attr.Get())
        array: NDArray[np.float64] = np.asarray(centers, dtype=np.float64)
        assert len(array) >= 2 and np.count_nonzero(np.ptp(array, axis=0) > 0.001) == 1
        report["axes_drag_locks_world_axis"] = True
        method_runtime.set_option("axes_spokes", False)
        method_runtime.set_option("cursor_3d", True)
        method_runtime.place_cursor()
        before: int = len(roots())
        await mouse(ci.MouseEventType.MOVE, center)
        await settle(2)
        await mouse(ci.MouseEventType.LEFT_BUTTON_DOWN, center)
        await settle(2)
        await mouse(ci.MouseEventType.LEFT_BUTTON_UP, center)
        await settle(4)
        anchor: CursorAnchor = CursorAnchor.get(str(stage.GetRootLayer().identifier))
        assert anchor.position is not None and len(roots()) == before
        original_path: str = str(api.camera_path)
        await emulate_key_combo("KEY_1", human_delay_speed=100)
        await settle(3)
        assert anchor.axis == 1 and anchor.side == 1
        await emulate_key_combo("KEY_1", human_delay_speed=100)
        await settle(3)
        assert anchor.side == -1
        await emulate_key_combo("KEY_2", human_delay_speed=100)
        await settle(3)
        assert anchor.axis == 2
        await emulate_key_combo("KEY_3", human_delay_speed=100)
        await settle(3)
        assert anchor.axis == 3
        await emulate_key_combo("KEY_0", human_delay_speed=100)
        await settle(3)
        assert str(api.camera_path) == original_path
        assert "__ToolBoxCursorView_" not in stage.GetRootLayer().ExportToString()
        report["cursor_place_and_axis_views"] = True
        report["session_camera_cleanup"] = True
        await capture("cursor_controls")
        method_runtime.set_option("cursor_3d", False)
        method_runtime.set_option("draw_on_object", True)
        sphere: Any = cast(Any, UsdGeom.Sphere).Define(stage, "/World/CanvasSphere")
        sphere.CreateRadiusAttr().Set(18.0)
        sphere.CreateDisplayColorAttr().Set([(0.3, 0.55, 0.8)])
        await settle(5)
        scene: Any = Cursor3D.get_scene()
        assert scene is not None
        assert scene.update((0.0, 0.0)) is not None and scene.surface_path == "/World/CanvasSphere"
        stroke = await drag(32, 0)
        vertices: list[Any] = []
        for child in stroke.GetChildren():
            attr = child.GetAttribute("points")
            if attr and attr.Get():
                vertices.extend(attr.Get())
        array = np.asarray(vertices, dtype=np.float64)
        assert len(array) > 0 and np.max(np.abs(np.linalg.norm(array, axis=1) - 18.0)) < 0.25
        assert np.min(array @ np.asarray(scene.projection_normal, dtype=np.float64)) > 0
        report["paint_conforms_to_object"] = True
        await capture("surface_paint")
        tools.curves.activate()
        assert DrawingMethods.get("pipe").draw_on_object
        await drag(-35, 15)
        report["pipe_object_drag"] = True
        tools.cancel()
        await capture("categories")
        # Rebuild the same tool panel in a wide floating host, then cross its layout breakpoint.
        window.destroy()
        carb.settings.get_settings().set("/app/launcher/layout/left_window", "")
        carb.settings.get_settings().set("/app/launcher/layout/bottom_window", "")
        window = ToolBoxWindow(tools)
        extension._window = window
        window.window.width = 1050
        window.window.height = 390
        window.window.position_x = 10
        window.window.position_y = 100
        tools.points.activate()
        method_runtime.set_option("drawing_plane", True)
        method_runtime.set_option("cursor_3d", True)
        await capture("wide_controls")
        assert window._settings_layout == (False, True)
        window.window.width = 800
        await capture("resized_controls")
        assert window._settings_layout == (False, False)
        report["wide_layout_resize"] = True
        window.destroy()
        carb.settings.get_settings().set("/app/launcher/layout/bottom_window", "Tool Box")
        window = ToolBoxWindow(tools)
        extension._window = window
        window.window.position_x = 10
        window.window.position_y = 100
        await capture("bottom_controls")
        assert set(window._canvas.checkboxes) == {"drawing_plane", "draw_on_object", "cursor_3d"}
        report["bottom_canvas_scrolls_in_short_host"] = True
        stage.GetRootLayer().Export(str(OUTPUT / "features.usda"))
        report["passed"] = True
    except Exception:
        logging.getLogger(__name__).exception("Drawing feature integration failed")
        report["error"] = traceback.format_exc()
        report["passed"] = False
    finally:
        (OUTPUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)
        app.post_quit(0 if report.get("passed") else 1)


task: asyncio.Future[None] = asyncio.ensure_future(run())
