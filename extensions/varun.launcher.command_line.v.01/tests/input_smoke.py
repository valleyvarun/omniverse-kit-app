"""Run in New Launcher with --enable omni.kit.ui_test --exec this-file."""

import asyncio
import gc
import importlib
import json
import logging
import time
import traceback
from pathlib import Path
from typing import Any

import carb.input
import omni.kit.actions.core
import omni.kit.app
import omni.kit.renderer.capture
import omni.usd
from omni import ui
from omni.kit import ui_test


async def run() -> None:
    app: Any = omni.kit.app.get_app()
    output: Path = Path(__file__).resolve().parents[4] / "_build/command_line_checks"
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}
    command: Any = None
    workspace: Any = None

    async def settle(count: int = 12) -> None:
        frame: int
        for frame in range(count):
            await app.next_update_async()

    async def complete() -> None:
        deadline: float = time.monotonic() + 20
        while command._tasks or workspace._tasks:
            assert time.monotonic() < deadline, "Command or tab switch timed out"
            await app.next_update_async()
        await settle()

    async def key(value: carb.input.KeyboardInput) -> None:
        await ui_test.emulate_keyboard_press(value)
        await settle(3)

    async def type_text(value: str) -> None:
        await ui_test.emulate_char_press(value)
        await settle(3)

    async def capture(name: str) -> None:
        capture_api: Any = omni.kit.renderer.capture.acquire_renderer_capture_interface()
        capture_api.capture_next_frame_swapchain(str(output / f"{name}.png"))
        await settle(4)

    try:
        await settle(100)
        command_type: Any = importlib.import_module("command_line.extension").CommandLineExtension
        workspace_type: Any = importlib.import_module("varun.launcher.workspace.v.01.extension").MyExtension
        obj: Any
        for obj in gc.get_objects():
            if isinstance(obj, command_type):
                command = obj
            elif isinstance(obj, workspace_type):
                workspace = obj
        assert command is not None and workspace is not None
        workspace.add_viewport_tab()
        await complete()
        await settle(40)
        context: Any = omni.usd.get_context()
        viewport_stage: Any = context.get_stage()
        await capture("idle")

        # The activation key is consumed, and subsequent letters go to the field.
        await key(carb.input.KeyboardInput.C)
        assert command._active
        assert command._model.get_value_as_string() == "", "Activation inserted the shortcut letter"
        await type_text("apps")
        assert command._model.get_value_as_string() == "apps"
        await key(carb.input.KeyboardInput.ENTER)
        await complete()
        assert ui.Workspace.get_window("apps").visible, "Apps command did not open the popup"
        assert command._active and command._model.get_value_as_string() == ""
        await capture("apps")

        # No second activation: Enter keeps the caret ready for the next command.
        await type_text("home")
        assert command._model.get_value_as_string() == "home", "Apps stole keyboard focus"
        await key(carb.input.KeyboardInput.ENTER)
        await complete()
        assert workspace._active_tab_id == 0
        assert ui.Workspace.get_window("home").visible
        assert workspace._tabs[1].stage == viewport_stage
        await key(carb.input.KeyboardInput.C)
        await type_text("c")
        assert command._model.get_value_as_string() == "c", "Focused C was treated as a shortcut"
        report["commands_and_persistent_focus"] = True
        await capture("home-focused")

        # Escape releases entry; typing into another field must not trigger C.
        await key(carb.input.KeyboardInput.ESCAPE)
        assert not command._active
        fixture: ui.Window = ui.Window("Command input test", width=300, height=100)
        with fixture.frame:
            other: ui.StringField = ui.StringField()
        await settle()
        await ui_test.emulate_mouse_move_and_click(
            ui_test.Vec2(other.screen_position_x + 10, other.screen_position_y + 10)
        )
        await settle()
        await key(carb.input.KeyboardInput.C)
        await type_text("c")
        assert other.model.get_value_as_string() == "c"
        assert not command._active
        fixture.destroy()
        await settle()
        report["escape_and_other_text_fields"] = True

        # A mouse click outside the entry releases it without executing its text.
        await key(carb.input.KeyboardInput.C)
        await key(carb.input.KeyboardInput.BACKSPACE)
        await type_text("apps")
        assert command._model.get_value_as_string() == "apps"
        popup: Any = ui.Workspace.get_window("apps")
        popup.visible = False
        await ui_test.emulate_mouse_move_and_click(ui_test.Vec2(30, 300))
        await settle()
        assert not command._active
        assert not popup.visible, "Click-away executed the command"
        assert command._model.get_value_as_string() == "apps"
        report["click_away_does_not_execute"] = True

        # Clicking the field activates it, including after a click-away.
        await ui_test.emulate_mouse_move_and_click(
            ui_test.Vec2(command._field.screen_position_x + 100, command._field.screen_position_y + 10)
        )
        await settle()
        assert command._active
        await key(carb.input.KeyboardInput.NUMPAD_ENTER)
        await complete()
        assert popup.visible and command._active
        await key(carb.input.KeyboardInput.ENTER)
        await complete()
        await type_text("unknown")
        await key(carb.input.KeyboardInput.ENTER)
        await complete()
        await type_text("ready")
        assert command._model.get_value_as_string() == "ready"
        report["mouse_activation_keypad_and_empty_commands"] = True
        report["passed"] = True
    except Exception:
        logging.getLogger(__name__).exception("Command line input check failed")
        report["passed"] = False
        report["error"] = traceback.format_exc()
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("COMMAND_LINE " + json.dumps(report), flush=True)
        app.post_quit(0 if report.get("passed") else 1)


asyncio.ensure_future(run())
