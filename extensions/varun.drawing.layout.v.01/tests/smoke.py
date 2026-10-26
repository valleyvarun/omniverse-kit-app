"""Run with Kit --enable omni.kit.renderer.capture --exec this-file."""

import asyncio
import gc
import json
import logging
from pathlib import Path
from typing import Any, cast

import carb.settings
import omni.kit.actions.core
import omni.kit.app
import omni.kit.renderer.capture
import omni.ui
import omni.usd
from omni.kit.viewport.window import get_viewport_window_instances
from pxr import UsdGeom
from tool_box.core.controller import ToolController
from tool_box.extension import ToolBoxExtension
from tool_box.tools.drawing import cursor
from tool_box.tools.drawing.curves.tool import ThreeDDrawCurvesTool
from tool_box.tools.drawing.plane.selection import PlaneSelection
from tool_box.tools.drawing.points.tool import ThreeDDrawTool
from tool_box.tools.transform.properties import TransformPropertiesPanel
from tool_box.ui.properties import ToolPropertiesPanel
from tool_box.ui.window import ToolBoxWindow

LOGGER: logging.Logger = logging.getLogger(__name__)


async def run() -> None:
    app: Any = omni.kit.app.get_app()
    settings: Any = carb.settings.get_settings()
    settings.set("/app/file/ignoreUnsavedOnExit", True)
    output: Path = Path(settings.get("/app/drawing/testOutput") or "_build/drawing_checks")
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}

    async def settle(count: int = 20) -> None:
        frame: int
        for frame in range(count):
            await app.next_update_async()

    async def capture(name: str) -> None:
        await settle()
        capture_api: Any = omni.kit.renderer.capture.acquire_renderer_capture_interface()
        capture_api.capture_next_frame_swapchain(str(output / f"{name}.png"))
        await settle(5)

    def check_layout() -> None:
        title: str
        windows: dict[str, Any] = {
            title: omni.ui.Workspace.get_window(title) for title in ("Tool Box", "Viewport", "Clayers", "Stage")
        }
        window: Any
        for title, window in windows.items():
            assert window and window.visible and window.docked, title
            assert not window.dock_tab_bar_visible, title
        left: Any = windows["Tool Box"]
        center: Any = windows["Viewport"]
        right: Any = windows["Clayers"]
        stage_window: Any = windows["Stage"]
        assert left.position_x < center.position_x < right.position_x
        assert abs(right.position_x - stage_window.position_x) < 2
        assert right.position_y < stage_window.position_y
        for title in ("Layer", "tabs_bar_window", "Explorer", "command_line"):
            window = omni.ui.Workspace.get_window(title)
            assert window is None or not window.visible, title

    try:
        await settle(120)
        check_layout()
        report["three_columns_without_tabs"] = True
        manager: Any = app.get_extension_manager()
        extension_id: str
        for extension_id in (
            "omni.hydra.rtx",
            "omni.kit.viewport.rtx",
            "varun.launcher.workspace.v.01",
            "varun.launcher.layout.v.01",
            "varun.launcher.home.v.01",
            "varun.launcher.command_line.v.01",
            "varun.launcher.explorer.v.01",
            "varun.launcher.plexus.v.01",
        ):
            assert not manager.is_extension_enabled(extension_id), extension_id
        assert settings.get("/renderer/enabled") == "pxr"
        assert settings.get("/exts/varun.launcher.usd_viewport.v.01/viewportLight/active")
        viewport_windows: list[Any] = list(cast(Any, get_viewport_window_instances)(usd_context_name=None))
        assert len(viewport_windows) == 1
        viewport: Any = viewport_windows[0]
        assert viewport.viewport_api.hydra_engine == "pxr"
        assert viewport.viewport_api.render_mode == "HdStormRendererPlugin"
        report["one_light_viewport_without_rtx"] = True

        extension: ToolBoxExtension | None = None
        obj: Any
        for obj in gc.get_objects():
            if type(obj) is ToolBoxExtension:
                extension = obj
                break
        assert extension is not None and extension._controller is not None and extension._window is not None
        controller: ToolController = extension._controller
        toolbox: ToolBoxWindow = extension._window
        assert not toolbox._plane_frame.visible
        await capture("startup")

        context: Any = omni.usd.get_context()
        stage: Any = context.get_stage()
        assert stage is not None
        cast(Any, UsdGeom.Cube).Define(stage, "/World/Cube")
        context.get_selection().set_selected_prim_paths(["/World/Cube"], True)
        registry: Any = omni.kit.actions.core.get_action_registry()
        registry.execute_action("tool_box", "move")
        await settle()
        panel: ToolPropertiesPanel
        for panel in toolbox._panels:
            if isinstance(panel, TransformPropertiesPanel):
                panel._on_field_changed(0, 12.0)
                values: tuple[float, float, float] | None = panel._read_values("move", "/World/Cube")
                assert values is not None and values[0] == 12.0
        assert toolbox._buttons["move"].visible and not toolbox._plane_frame.visible
        await capture("transform")
        report["sidebar_transform_edit"] = True

        registry.execute_action("tool_box", "drawing_plane")
        assert stage.GetPrimAtPath(PlaneSelection.get().active_plane())
        action_id: str
        tool: ThreeDDrawTool | ThreeDDrawCurvesTool
        prim_type: str
        for action_id, tool, prim_type in (
            ("draw_points", controller.points, "Xform"),
            ("draw_curves", controller.curves, "Xform"),
        ):
            registry.execute_action("tool_box", action_id)
            await settle()
            assert toolbox._plane_frame.visible
            button: omni.ui.Button
            name: str
            for name, button in toolbox._buttons.items():
                assert button.visible == (name == action_id)
            scene: Any
            for scene in cursor._scenes:
                if scene.viewport_api.usd_context_name == viewport.viewport_api.usd_context_name:
                    cursor._active_scene = scene
                    break
            else:
                raise AssertionError("Drawing cursor is missing from the permanent viewport")
            tool._on_draw_begin((0.0, 0.0, 0.0))
            tool._on_draw_change((20.0, 0.0, 0.0))
            tool._on_draw_end()
            prim: Any
            strokes: list[Any] = []
            for prim in stage.GetPrimAtPath("/World/ThreeDDraw").GetChildren():
                if prim.GetTypeName() == prim_type:
                    strokes.append(prim)
            assert (
                strokes
                and len(
                    cast(Any, UsdGeom.PointBased)(strokes[-1].GetChildren()[0] if prim_type == "Xform" else strokes[-1])
                    .GetPointsAttr()
                    .Get()
                )
                >= 2
            )
            await capture(action_id)
        report["point_and_curve_drawing"] = True
        registry.execute_action("tool_box", "cancel")
        await settle()
        assert not toolbox._plane_frame.visible
        assert all(button.visible for button in toolbox._buttons.values())
        report["escape_restores_chooser"] = True

        registry.execute_action("varun.drawing.layout.v.01", "reset_layout")
        await settle(60)
        check_layout()
        stage = None
        prim = None
        strokes.clear()
        await context.new_stage_async()
        await settle()
        assert list(cast(Any, get_viewport_window_instances)(usd_context_name=None)) == viewport_windows
        assert viewport.viewport_api.hydra_engine == "pxr"
        check_layout()
        report["reset_and_new_stage_keep_permanent_viewport"] = True
        report["passed"] = True
    except Exception:
        LOGGER.exception("3d-drawing smoke validation failed")
        report["passed"] = False
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("DRAWING_SMOKE " + json.dumps(report), flush=True)
        app.post_quit(0 if report.get("passed") else 1)


task: asyncio.Future[None] = asyncio.ensure_future(run())
