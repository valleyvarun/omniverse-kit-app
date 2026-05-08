import omni.ui as ui

from ..styles import AGENT_WINDOW_BACKGROUND_STYLE
from ..tools import DrawingPlane, ThreeDDrawTool, Tool, TransformTools
from .window import LauncherWindow


class BottomWindow(LauncherWindow):
    def __init__(self) -> None:
        # Tool instances kept alive so their callbacks stay valid.
        self._drawing_plane = DrawingPlane()
        self._transform_tools = TransformTools()
        self._three_d_draw = ThreeDDrawTool()
        self._tools: list[Tool] = [
            *self._transform_tools.make_tools(),
            self._three_d_draw.make_tool(),
            self._drawing_plane.make_tool(),
        ]

        # Create the docked Tools panel at the bottom of the layout.
        super().__init__(
            title="Tools",
            height=60,
        )

    def _build_ui(self) -> None:
        if not self._window:
            return

        with self._window.frame:
            with ui.ZStack():
                # Match the Explorer's dark background.
                ui.Rectangle(style=AGENT_WINDOW_BACKGROUND_STYLE)
                with ui.VStack():
                    ui.Spacer(height=6)
                    with ui.HStack(spacing=6):
                        ui.Spacer(width=8)
                        for tool in self._tools:
                            tool.build_button()
                        ui.Spacer()
