# Omniverse & USD imports
import omni.ui as ui

from ..layers.c_layers import ClayersPanel

# Local imports
from ..startup.styles import AGENT_WINDOW_BACKGROUND_STYLE
from .window import LauncherWindow

# ----------------------------------------------------------------------
# 1. CLAYERS WINDOW
# ----------------------------------------------------------------------


class ClayersWindow(LauncherWindow):
    # Create the Clayers window and track its panel.
    def __init__(self) -> None:
        self._panel: ClayersPanel | None = None
        super().__init__(
            title="Clayers",
            width=260,
        )

    # Place the Clayers panel over the shared window background.
    def _build_ui(self) -> None:
        if not self._window:
            return

        with self._window.frame:
            with ui.ZStack():
                ui.Rectangle(style=AGENT_WINDOW_BACKGROUND_STYLE)
                content = ui.Frame()
        self._panel = ClayersPanel()
        self._panel.build(content)

    # Release the Clayers panel before destroying its window.
    def destroy(self) -> None:
        if self._panel is not None:
            self._panel.destroy()
            self._panel = None
        super().destroy()
