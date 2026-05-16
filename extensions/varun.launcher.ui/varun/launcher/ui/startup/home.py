from pathlib import Path
from typing import Callable

import omni.ui as ui

from .styles import AGENT_WINDOW_BACKGROUND_STYLE


# Resolve to <python_package>/logos/<name>.png (sibling of the `startup` package).
_LOGOS_DIR = Path(__file__).resolve().parent.parent / "logos"

# The four logo buttons shown on the home page, arranged as a 2x2 grid:
#   Row 0: Plexus, Apps
#   Row 1: Nucleus, Market
# Each entry is (label, png filename); files live in `varun/launcher/ui/logos/`.
_LOGO_GRID: list[list[tuple[str, str]]] = [
    [("Plexus", "plexus-logo.png"), ("Agent", "apps-logo.png")],
    [("Nucleus", "nucleus-logo.png"), ("Market", "market-logo.png")],
]

_LOGO_BUTTON_SIZE = 115
_LABEL_GAP = 12
_LABEL_HEIGHT = 20
_COLUMN_GAP = 160
_ROW_GAP = 80

_LOGO_BUTTON_STYLE = {
    "Button": {
        "background_color": 0x00000000,
        "border_radius": 0,
        "padding": 0,
    },
    "Button:hovered": {
        "background_color": 0x22FFFFFF,
    },
    "Button:pressed": {
        "background_color": 0x44FFFFFF,
    },
}

_LABEL_STYLE = {
    "color": 0xFFDDDDDD,
    "font_size": 14,
}


class HomeWindow:
    """Home landing page shown in the main panel on app startup.

    Displays the four launcher logos as clickable buttons in a 2x2 grid
    with a label centered below each logo. Shares the main dock slot
    with the 3D viewport.
    """

    def __init__(self, on_logo_clicked: Callable[[str], None] | None = None) -> None:
        self._on_logo_clicked = on_logo_clicked
        self._window: ui.Window | None = ui.Window("Home")
        self._build_ui()

    @property
    def window(self) -> ui.Window | None:
        """The underlying ui.Window, exposed so MainWindow can hook dock callbacks."""
        return self._window

    def _build_ui(self) -> None:
        if self._window is None:
            return

        cell_height = _LOGO_BUTTON_SIZE + _LABEL_GAP + _LABEL_HEIGHT

        with self._window.frame:
            with ui.ZStack():
                ui.Rectangle(style=AGENT_WINDOW_BACKGROUND_STYLE)
                with ui.VStack():
                    ui.Spacer()
                    for row_index, row in enumerate(_LOGO_GRID):
                        if row_index > 0:
                            ui.Spacer(height=_ROW_GAP)
                        with ui.HStack(height=cell_height):
                            ui.Spacer()
                            for col_index, (label, filename) in enumerate(row):
                                if col_index > 0:
                                    ui.Spacer(width=_COLUMN_GAP)
                                self._build_logo_cell(label, filename)
                            ui.Spacer()
                    ui.Spacer()

    def _build_logo_cell(self, label: str, filename: str) -> None:
        with ui.VStack(width=_LOGO_BUTTON_SIZE):
            self._build_logo_button(label, filename)
            ui.Spacer(height=_LABEL_GAP)
            ui.Label(
                label,
                alignment=ui.Alignment.CENTER,
                height=_LABEL_HEIGHT,
                style=_LABEL_STYLE,
            )

    def _build_logo_button(self, label: str, filename: str) -> None:
        icon_path = _LOGOS_DIR / filename
        ui.Button(
            "",
            image_url=str(icon_path),
            width=_LOGO_BUTTON_SIZE,
            height=_LOGO_BUTTON_SIZE,
            clicked_fn=lambda name=label: self._handle_click(name),
            tooltip=label,
            style=_LOGO_BUTTON_STYLE,
        )

    def _handle_click(self, label: str) -> None:
        if self._on_logo_clicked is not None:
            self._on_logo_clicked(label)

    def destroy(self) -> None:
        if self._window is not None:
            self._window.destroy()
            self._window = None
