from typing import Any, cast

import omni.ui as ui

from ..styles import AGENT_INPUT_FIELD_STYLE, MAIN_WINDOW_BACKGROUND_STYLE, TOP_INPUT_CONTAINER_STYLE
from .window import LauncherWindow


class TopWindow(LauncherWindow):
    def __init__(self) -> None:
        self._editor_model = ui.SimpleStringModel("")
        self._command_field: ui.StringField | None = None
        super().__init__(
            title="Top Window",
            height=70,
        )

    def _build_ui(self) -> None:
        if not self._window:
            return

        with self._window.frame:
            with ui.ZStack():
                ui.Rectangle(style=MAIN_WINDOW_BACKGROUND_STYLE)
                with ui.VStack(spacing=0):
                    ui.Spacer()
                    with ui.ZStack(height=26):
                        ui.Rectangle(style=TOP_INPUT_CONTAINER_STYLE)
                        with ui.HStack(spacing=0, height=26):
                            ui.Label("command: ", width=80, alignment=ui.Alignment.CENTER)
                            self._command_field = ui.StringField(
                                self._editor_model,
                                height=26,
                                style=AGENT_INPUT_FIELD_STYLE,
                            )

    # Focus the command StringField.
    def focus_command_field(self) -> None:
        if self._command_field is not None:
            try:
                cast(Any, self._command_field).focus_keyboard(True)
            except Exception:
                pass
