"""Exercise toolbox shortcuts and drawing through Kit's input event system."""

import asyncio
import gc
import json
import logging
import traceback
from pathlib import Path
from typing import Any, cast

import carb.settings
import omni.kit.app
import omni.kit.renderer.capture
import omni.usd
from omni.kit import ui_test
from omni.kit.ui_test.input import (
    emulate_key_combo,
    emulate_mouse_drag_and_drop,
    emulate_mouse_move,
)
from omni.kit.viewport.window import ViewportWindow
from pxr import Gf, UsdGeom
from tool_box.extension import ToolBoxExtension

LOGGER: logging.Logger = logging.getLogger(__name__)


async def run() -> None:
    app: Any = omni.kit.app.get_app()
    settings: Any = carb.settings.get_settings()
    output = Path(settings.get("/exts/tool_box/testReport") or "tool_box_input.json")
    report: dict[str, Any] = {}
    viewport: Any = None
    context: Any = None

    async def settle(count: int = 10) -> None:
        for _ in range(count):
            await app.next_update_async()

    try:
        await settle(40)
        extension: Any = next(obj for obj in gc.get_objects() if isinstance(obj, ToolBoxExtension))
        tools = extension._controller
        toolbox = extension._window.window
        toolbox.position_x, toolbox.position_y = 800, 40
        cast(Any, omni.usd).create_context("tool_box_input")
        context = omni.usd.get_context("tool_box_input")
        await context.new_stage_async()
        viewport = ViewportWindow("Input Viewport", usd_context_name="tool_box_input", width=760, height=600)
        viewport.position_x, viewport.position_y = 10, 40
        stage = context.get_stage()
        UsdGeom.Cube.Define(stage, "/World/Cube").CreateSizeAttr(20)
        camera = UsdGeom.Camera.Define(stage, "/Camera")
        camera.AddTransformOp().Set(
            Gf.Matrix4d().SetLookAt(Gf.Vec3d(0, 300, 300), Gf.Vec3d(0, 0, 0), Gf.Vec3d(0, 1, 0)).GetInverse()
        )
        viewport.viewport_api.camera_path = "/Camera"
        await settle(25)
        viewport.focus()
        await emulate_mouse_move(ui_test.Vec2(380, 330), human_delay_speed=100)
        await settle()
        context.get_selection().set_selected_prim_paths(["/World/Cube"], True)
        await settle()
        for key, operation in (("M", "move"), ("R", "rotate"), ("S", "scale")):
            await emulate_key_combo(key, human_delay_speed=100)
            await settle()
            assert settings.get("/app/transform/operation") == operation
        report["keyboard_transform_shortcuts"] = True

        key: str
        prim_type: str
        for key, prim_type in (("D", "Xform"), ("SHIFT+D", "Xform")):
            await emulate_key_combo(key, human_delay_speed=100)
            await settle()
            assert tools.points._is_active if key == "D" else tools.curves._is_active
            await emulate_mouse_drag_and_drop(ui_test.Vec2(300, 350), ui_test.Vec2(480, 380), human_delay_speed=100)
            await settle()
            parent = stage.GetPrimAtPath("/World/ThreeDDraw")
            assert parent and any(prim.GetTypeName() == prim_type for prim in parent.GetChildren()), prim_type
        report["mouse_draws_points_and_curves"] = True

        await emulate_key_combo("ESCAPE", human_delay_speed=100)
        await settle()
        assert not tools.points._is_active and not tools.curves._is_active
        report["keyboard_escape"] = True
        # Save the actual floating UI before closing the test host.
        capture: Any = omni.kit.renderer.capture.acquire_renderer_capture_interface()
        capture.capture_next_frame_swapchain(str(output.with_suffix(".png")))
        await settle(10)
        report["passed"] = True
    except Exception:
        LOGGER.exception("Toolbox input smoke test failed")
        report["passed"] = False
        report["error"] = traceback.format_exc()
    finally:
        if viewport is not None:
            viewport.destroy()
        if context is not None:
            await context.close_stage_async()
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("TOOL_BOX_INPUT " + json.dumps(report), flush=True)
        app.post_quit(0 if report.get("passed") else 1)


asyncio.ensure_future(run())
