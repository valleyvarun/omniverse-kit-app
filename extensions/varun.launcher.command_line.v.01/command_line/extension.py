"""The launcher's command entry, matching the original top panel."""

import asyncio
import inspect
import logging
from typing import Any, cast

import carb.input
import omni.appwindow
import omni.ext
import omni.kit.actions.core
import omni.kit.app
import omni.kit.hotkeys.core
from omni import ui

from .commands import COMMANDS

EXTENSION_ID: str = "varun.launcher.command_line.v.01"
WINDOW_TITLE: str = "command_line"
LOGGER: logging.Logger = logging.getLogger(__name__)
COMMAND_ACTIONS: dict[str, tuple[str, str]] = {
    "home": ("varun.launcher.workspace.v.01", "select_home_tab"),
    "apps": ("varun.launcher.home.v.01", "show_apps"),
}


class CommandLineExtension(omni.ext.IExt):
    def on_startup(self, _ext_id: str) -> None:
        # Keep focus requests separate from commands already executing.
        self._alive: bool = True
        self._active: bool = False
        self._focus_generation: int = 0
        self._tasks: set[asyncio.Future[None]] = set()
        self._model: ui.SimpleStringModel = ui.SimpleStringModel("")
        self._actions: Any = omni.kit.actions.core.get_action_registry()
        self._hotkeys: Any = omni.kit.hotkeys.core.get_hotkey_registry()
        descriptions: list[str] = []
        name: str
        description: str
        for name, description in COMMANDS.items():
            descriptions.append(f"{name}: {description}")

        # Match the old black panel, bottom-aligned strip, and transparent field.
        self._window: ui.Window = ui.Window(
            WINDOW_TITLE,
            height=75,
            flags=ui.WINDOW_FLAGS_NO_TITLE_BAR | ui.WINDOW_FLAGS_NO_MOVE,
        )
        with self._window.frame, ui.ZStack():
            ui.Rectangle(style={"background_color": 0xFF000000})
            with ui.VStack(spacing=0):
                ui.Spacer()
                with ui.ZStack(height=26):
                    ui.Rectangle(style={"background_color": 0xFF1F1F1F})
                    with ui.HStack(spacing=0, height=26):
                        ui.Label("command: ", width=80, alignment=ui.Alignment.CENTER)
                        self._field: ui.StringField = ui.StringField(
                            self._model,
                            height=26,
                            identifier="command_input",
                            style={"background_color": 0x00000000, "border_width": 0},
                            tooltip="\n".join(descriptions),
                        )
        # Window-level keys also work when the pointer is outside the input.
        self._window.set_key_pressed_fn(self._on_key_pressed)
        self._field.set_mouse_pressed_fn(self._on_field_pressed)
        self._actions.register_action(EXTENSION_ID, "focus_command", self.activate, display_name="Focus Command")
        self._hotkeys.register_hotkey(
            hotkey_ext_id=EXTENSION_ID,
            key="C",
            action_ext_id=EXTENSION_ID,
            action_id="focus_command",
        )

        # A real click cancels deferred refocusing; navigation actions do not.
        self._input: Any = carb.input.acquire_input_interface()
        self._mouse: Any = omni.appwindow.get_default_app_window().get_mouse()
        self._edit_subscription: Any = self._model.subscribe_end_edit_fn(self._on_end_edit)
        self._window.set_focused_changed_fn(self._on_focus_changed)

    def activate(self) -> None:
        self._active = True
        self._focus_generation += 1
        self._focus_field()

    def deactivate(self) -> None:
        self._active = False
        self._focus_generation += 1
        cast(Any, self._field).focus_keyboard(False)

    def _focus_field(self) -> None:
        cast(Any, self._window).focus()
        cast(Any, self._field).focus_keyboard(True)

    def _on_field_pressed(self, _x: float, _y: float, _button: int, _modifiers: int) -> None:
        self._active = True
        self._focus_generation += 1

    # Enter queues its command before the field ends editing; clicks do not.
    def _on_end_edit(self, _model: ui.AbstractValueModel) -> None:
        if self._active and (not self._tasks or self._mouse_pressed()):
            self.deactivate()

    # Commands can focus another window, but a user click cancels refocusing.
    def _on_focus_changed(self, focused: bool) -> None:
        if not focused and self._active and (not self._tasks or self._mouse_pressed()):
            self.deactivate()

    def _mouse_pressed(self) -> bool:
        button: carb.input.MouseInput
        for button in (
            carb.input.MouseInput.LEFT_BUTTON,
            carb.input.MouseInput.RIGHT_BUTTON,
            carb.input.MouseInput.MIDDLE_BUTTON,
        ):
            if self._input.get_mouse_value(self._mouse, button) > 0:
                return True
        return False

    def _on_key_pressed(self, key: int, _modifiers: int, pressed: bool) -> None:
        if not pressed or not self._active:
            return
        if key == int(carb.input.KeyboardInput.ESCAPE):
            self.deactivate()
        elif key in (int(carb.input.KeyboardInput.ENTER), int(carb.input.KeyboardInput.NUMPAD_ENTER)):
            command: str = self._model.get_value_as_string().strip().lower()
            self._model.set_value("")
            task: asyncio.Future[None] = asyncio.ensure_future(self._execute(command, self._focus_generation))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _execute(self, command: str, generation: int) -> None:
        try:
            target: tuple[str, str] | None = COMMAND_ACTIONS.get(command)
            if target is not None:
                result: Any = self._actions.execute_action(*target)
                if inspect.isawaitable(result):
                    await result
            elif command:
                LOGGER.info("Unknown launcher command: %s", command)
            # Enter and the action can both change focus during the current frame.
            await cast(Any, omni.kit.app.get_app()).next_update_async()
        except Exception:
            LOGGER.exception("Could not execute launcher command %r", command)
        finally:
            if self._alive and self._active and generation == self._focus_generation:
                self._focus_field()

    def on_shutdown(self) -> None:
        self._alive = False
        self._active = False
        self._edit_subscription = None
        self._hotkeys.deregister_all_hotkeys_for_extension(EXTENSION_ID)
        self._actions.deregister_all_actions_for_extension(EXTENSION_ID)
        task: asyncio.Future[None]
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        self._window.destroy()
