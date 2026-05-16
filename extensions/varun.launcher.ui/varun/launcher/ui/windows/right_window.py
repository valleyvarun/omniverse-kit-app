import omni.ui as ui

from ..startup.styles import AGENT_WINDOW_BACKGROUND_STYLE
from ..layers.c_layers import ClayersPanel
from .window import LauncherWindow


class ClayersWindow(LauncherWindow):
    def __init__(self) -> None:
        self._panel: ClayersPanel | None = None
        super().__init__(
            title="Clayers",
            width=260,
        )

    def _build_ui(self) -> None:
        if not self._window:
            return

        with self._window.frame:
            with ui.ZStack():
                ui.Rectangle(style=AGENT_WINDOW_BACKGROUND_STYLE)
                content = ui.Frame()
        self._panel = ClayersPanel()
        self._panel.build(content)

    def destroy(self) -> None:
        if self._panel is not None:
            self._panel.destroy()
            self._panel = None
        super().destroy()
