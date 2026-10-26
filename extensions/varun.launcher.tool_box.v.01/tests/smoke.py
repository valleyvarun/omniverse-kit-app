"""Run with Kit --exec in a viewport host; all stages are disposable."""

import asyncio
import gc
import json
import logging
import traceback
from pathlib import Path
from typing import Any, cast

import carb.settings
import omni.kit.actions.core as actions
import omni.kit.app
import omni.kit.hotkeys.core as hotkeys
import omni.usd
from omni import ui
from omni.kit.viewport.window import ViewportWindow
from pxr import Gf, UsdGeom
from tool_box.core.viewport import get_active_stage, get_active_usd_context
from tool_box.extension import ToolBoxExtension
from tool_box.shortcuts import SHORTCUTS, WINDOW_TITLE
from tool_box.tools.drawing import cursor
from tool_box.tools.drawing.plane.selection import PlaneSelection
from tool_box.tools.transform.properties import TransformPropertiesPanel

LOGGER: logging.Logger = logging.getLogger(__name__)


async def run() -> None:
    app: Any = omni.kit.app.get_app()
    settings: Any = carb.settings.get_settings()
    report: dict[str, Any] = {}
    output = Path(settings.get("/exts/tool_box/testReport") or "tool_box_test.json")
    windows: list[Any] = []
    contexts: list[Any] = []

    async def settle(count: int = 15) -> None:
        for _ in range(count):
            await app.next_update_async()

    try:
        await settle(40)
        extension = next(obj for obj in gc.get_objects() if isinstance(obj, ToolBoxExtension))
        assert extension._controller is not None and extension._window is not None
        controller = extension._controller
        window = extension._window
        assert window.window.visible and not window.window.docked
        assert set(controller.tools) == {"move", "rotate", "scale", "draw_points", "draw_curves", "drawing_plane"}
        report["floating_window_and_six_tools"] = True

        # Two named contexts prove the extension does not depend on the default stage.
        for name in ("tool_box_test_a", "tool_box_test_b"):
            cast(Any, omni.usd).create_context(name)
            context: Any = omni.usd.get_context(name)
            await context.new_stage_async()
            contexts.append(context)
            windows.append(ViewportWindow(name, usd_context_name=name, width=600, height=450))
            UsdGeom.Cube.Define(context.get_stage(), "/World/Cube")
        await settle()
        windows[0].focus()
        await settle()
        assert get_active_stage() == contexts[0].get_stage(), {
            "focused": [(w.title, w.focused, w.viewport_api.usd_context_name) for w in windows],
            "active": str(get_active_usd_context().get_name()) if get_active_usd_context() else None,
        }
        assert get_active_usd_context(windows[1].viewport_api) == contexts[1]
        report["named_viewport_routing"] = True

        registry: Any = cast(Any, actions).get_action_registry()
        key_registry: Any = cast(Any, hotkeys).get_hotkey_registry()
        for action_id, key in SHORTCUTS.items():
            assert registry.get_action("tool_box", action_id) is not None
            if key:
                assert (
                    key_registry.get_hotkey("tool_box", key, hotkeys.HotkeyFilter(windows=[WINDOW_TITLE])) is not None
                )
        report["actions_and_shortcuts"] = True

        # Transform values and undoable commands must use the named context.
        contexts[0].get_selection().set_selected_prim_paths(["/World/Cube"], True)
        await settle()
        panel = next(panel for panel in window._panels if isinstance(panel, TransformPropertiesPanel))
        for action_id, value in (("move", 12.0), ("rotate", 25.0), ("scale", 2.0)):
            registry.execute_action("tool_box", action_id)
            await settle(3)
            assert controller.tools[action_id].is_active
            panel._on_field_changed(0, value)
            await settle(3)
            values = panel._read_values(action_id, "/World/Cube")
            assert values is not None and abs(values[0] - value) < 1e-5
        untouched = UsdGeom.Xformable(contexts[1].get_stage().GetPrimAtPath("/World/Cube"))
        assert untouched.ComputeLocalToWorldTransform(0) == Gf.Matrix4d(1)
        report["transform_tools_and_property_edits"] = True

        registry.execute_action("tool_box", "drawing_plane")
        plane_path = PlaneSelection.get().active_plane()
        assert contexts[0].get_stage().GetPrimAtPath(plane_path)
        assert not contexts[1].get_stage().GetPrimAtPath(plane_path)
        report["drawing_plane_and_picker"] = True

        # Exercise the same stroke callbacks used by viewport drag gestures.
        action_id: str
        tool: Any
        prim_type: str
        for action_id, tool, prim_type in (
            ("draw_points", controller.points, "Xform"),
            ("draw_curves", controller.curves, "Xform"),
        ):
            registry.execute_action("tool_box", action_id)
            await settle(4)
            scenes = [s for s in cursor._scenes if s.viewport_api.usd_context_name == "tool_box_test_a"]
            assert scenes, "Cursor scene was not installed on the named viewport"
            cursor._active_scene = scenes[0]
            tool._on_draw_begin((0.0, 0.0, 0.0))
            tool._on_draw_change((20.0, 0.0, 0.0))
            tool._on_draw_end()
            stage = contexts[0].get_stage()
            p: Any
            strokes: list[Any] = []
            for p in stage.GetPrimAtPath("/World/ThreeDDraw").GetChildren():
                if p.GetTypeName() == prim_type:
                    strokes.append(p)
            assert (
                strokes
                and len(
                    UsdGeom.PointBased(strokes[-1].GetChildren()[0] if prim_type == "Xform" else strokes[-1])
                    .GetPointsAttr()
                    .Get()
                )
                >= 2
            )
            assert not contexts[1].get_stage().GetPrimAtPath("/World/ThreeDDraw")
            assert not stage.GetPrimAtPath("/World/ThreeDDraw/_LiveStroke")
            assert not stage.GetPrimAtPath("/World/ThreeDDraw/_LiveStrokeCurves")
        report["point_and_curve_geometry"] = True

        registry.execute_action("tool_box", "cancel")
        assert not controller.points._is_active and not controller.curves._is_active
        assert not cursor._listener
        windows[1].focus()
        await settle()
        assert get_active_stage() == contexts[1].get_stage()
        report["escape_and_viewport_switch"] = True

        # Disable and re-enable without leaving actions or cursor scenes behind.
        manager: Any = app.get_extension_manager()
        ext_id: str = manager.get_enabled_extension_id("varun.launcher.tool_box.v.01")
        del extension
        manager.set_extension_enabled_immediate(ext_id, False)
        await settle(3)
        assert registry.get_action("tool_box", "move") is None
        assert not cursor._scenes
        manager.set_extension_enabled_immediate(ext_id, True)
        await settle(10)
        assert ui.Workspace.get_window(WINDOW_TITLE) is not None
        assert registry.get_action("tool_box", "move") is not None
        report["disable_and_reload_cleanup"] = True
        report["passed"] = True
    except Exception:
        LOGGER.exception("Toolbox smoke validation failed")
        report["passed"] = False
        report["error"] = traceback.format_exc()
    finally:
        for window in windows:
            window.destroy()
        for context in contexts:
            await context.close_stage_async()
            cast(Any, omni.usd).destroy_context(context.get_name())
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("TOOL_BOX_TEST " + json.dumps(report), flush=True)
        app.post_quit(0 if report.get("passed") else 1)


asyncio.ensure_future(run())
