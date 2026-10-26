"""Apps destination popup.

Floating popup opened when the user clicks the `Apps` logo on the Home
page. Layout (top -> bottom):

    [Search apps...]                 [Go to Store]
    -----------------------------------------------
    v Favorites
       [OpenUSD]

Common scaffolding (centering, base styling, window flags) lives in
`popup_windows.PopupWindow`.
"""

# Python & library imports
from typing import Callable

# Omniverse & USD imports
import omni.ui as ui

# Local imports
from ...startup.styles import AGENT_WINDOW_BACKGROUND_STYLE
from .popup_windows import (
    LABEL_GAP,
    LABEL_HEIGHT,
    LABEL_STYLE,
    LOGO_BUTTON_STYLE,
    LOGOS_DIR,
    PopupWindow,
)

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

# App entries shown in the Favorites grid. Each entry is
# (label, png filename); files live in `varun/launcher/ui/logos/`.
_FAVORITE_APPS: list[tuple[str, str]] = [
    ("OpenUSD", "openusd-logo.png"),
]


# Header / chrome metrics.
_HEADER_HEIGHT = 34
_HEADER_INNER_HEIGHT = 26
_HEADER_HORIZONTAL_PAD = 18
_HEADER_VERTICAL_PAD = 4
_STORE_BUTTON_WIDTH = 110
_STORE_ICON_SIZE = 16
_SECTION_PAD_X = 18
_SECTION_HEADER_HEIGHT = 36
_GRID_VERTICAL_PAD = 18
_GRID_HORIZONTAL_PAD = 18
_GRID_COLUMN_GAP = 24
_APP_ICON_SIZE = 60


# Local accents layered on top of the shared launcher palette
# (`AGENT_WINDOW_BACKGROUND_STYLE`) so this popup feels consistent with
# the rest of the chrome (Explorer / Agent panels, project folder dialog).
# Pill / input colours mirror Kit's FilePickerDialog: a flat dark slate
# inset (`0xFF1A1A1A`) with a 1-px subtle border, square-ish corners
# (radius 2), and neutral grey text. No bright accent colours.
_PANEL_STYLE = {"Rectangle": AGENT_WINDOW_BACKGROUND_STYLE}
_INPUT_BG = 0xFF1A1A1A
_INPUT_BORDER = 0xFF2A2A2A
_INPUT_BORDER_WIDTH = 1
_INPUT_RADIUS = 2
_PILL_STYLE = {
    "Rectangle": {
        "background_color": _INPUT_BG,
        "border_color": _INPUT_BORDER,
        "border_width": _INPUT_BORDER_WIDTH,
        "border_radius": _INPUT_RADIUS,
    },
}
_SEARCH_FIELD_STYLE = {
    "Field": {
        "background_color": 0x00000000,
        "color": 0xFFBBBBBB,
        "border_width": 0,
        "font_size": 14,
    },
}
_SEARCH_ICON_STYLE = {
    "color": 0xFF888888,
    "font_size": 14,
}
_STORE_BUTTON_STYLE = {
    "Button": {
        "background_color": 0x00000000,
        "color": 0xFFDDDDDD,
        "border_radius": _INPUT_RADIUS,
        "padding": 4,
        "font_size": 14,
    },
    "Button:hovered": {
        "background_color": 0x22FFFFFF,
    },
    "Button:pressed": {
        "background_color": 0x44FFFFFF,
    },
}
_SECTION_HEADER_LABEL_STYLE = {
    "color": 0xFFDDDDDD,
    "font_size": 14,
}
_SECTION_HEADER_ARROW_STYLE = {
    "color": 0xFF888888,
    "font_size": 12,
}
_DIVIDER_STYLE = {
    "Rectangle": {
        "background_color": 0xFF1F1F1F,
    },
}


# ----------------------------------------------------------------------
# 2. APPS WINDOW
# ----------------------------------------------------------------------


class AppsWindow(PopupWindow):
    """Floating `Apps` popup centered over the main window."""

    TITLE = "Apps"
    WINDOW_WIDTH = 820
    WINDOW_HEIGHT = 480

    # Store the app-launch callback for this popup.
    def __init__(self, on_app_clicked: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self._on_app_clicked = on_app_clicked

    # SUBCLASS HOOK -------------------------------------------------------

    # Build the search header, divider, and favorite apps.
    def _build_body(self) -> None:
        # Full-bleed panel background so the popup reads as one surface
        # rather than ImGui's default flat grey.
        with ui.ZStack():
            ui.Rectangle(style=_PANEL_STYLE)
            with ui.VStack(spacing=0):
                self._build_header()
                self._build_divider()
                self._build_favorites_section()
                ui.Spacer()

    # INTERNAL ------------------------------------------------------------

    # Build the panel header.
    def _build_header(self) -> None:
        with ui.HStack(height=_HEADER_HEIGHT):
            ui.Spacer(width=_HEADER_HORIZONTAL_PAD)
            with ui.VStack():
                ui.Spacer(height=_HEADER_VERTICAL_PAD)
                with ui.HStack(height=_HEADER_INNER_HEIGHT):
                    self._build_search_field()
                    ui.Spacer(width=_HEADER_HORIZONTAL_PAD)
                    self._build_go_to_store_button()
                ui.Spacer(height=_HEADER_VERTICAL_PAD)
            ui.Spacer(width=_HEADER_HORIZONTAL_PAD)

    # Build the search icon and text field.
    def _build_search_field(self) -> None:
        # Magnifying-glass glyph + StringField stacked horizontally inside
        # a single rounded "pill" background (matches the Agent input pill).
        with ui.ZStack():
            ui.Rectangle(style=_PILL_STYLE)
            with ui.HStack():
                ui.Spacer(width=10)
                ui.Image(
                    str(LOGOS_DIR / "search.svg"),
                    width=14,
                    height=14,
                    style=_SEARCH_ICON_STYLE,
                )
                ui.Spacer(width=6)
                ui.StringField(style=_SEARCH_FIELD_STYLE)
                ui.Spacer(width=8)

    # Place the store icon and label over its click target.
    def _build_go_to_store_button(self) -> None:
        with ui.ZStack(width=_STORE_BUTTON_WIDTH):
            ui.Rectangle(style=_PILL_STYLE)
            # The actual click target. Transparent so the pill background
            # shows through; the icon + label are drawn on top.
            ui.Button(
                "",
                clicked_fn=self._handle_go_to_store,
                style=_STORE_BUTTON_STYLE,
            )
            # Icon + label overlay. Pointer events fall through to the
            # button underneath because Image/Label don't capture clicks.
            with ui.HStack():
                ui.Spacer(width=14)
                with ui.VStack(width=_STORE_ICON_SIZE):
                    ui.Spacer()
                    ui.Image(
                        str(LOGOS_DIR / "store-logo.png"),
                        width=_STORE_ICON_SIZE,
                        height=_STORE_ICON_SIZE,
                    )
                    ui.Spacer()
                ui.Spacer(width=8)
                ui.Label(
                    "Store",
                    alignment=ui.Alignment.LEFT_CENTER,
                    style={"color": 0xFFFFFFFF, "font_size": 14},
                )
                ui.Spacer(width=14)

    # Draw the separator below the header.
    def _build_divider(self) -> None:
        with ui.HStack(height=1):
            ui.Rectangle(height=1, style=_DIVIDER_STYLE)

    # Add spacing above the favorite app grid.
    def _build_favorites_section(self) -> None:
        with ui.VStack():
            ui.Spacer(height=_GRID_VERTICAL_PAD)
            self._build_favorites_grid()

    # Draw the section title and disclosure marker.
    def _build_section_header(self, title: str) -> None:
        with ui.HStack(height=_SECTION_HEADER_HEIGHT):
            ui.Spacer(width=_SECTION_PAD_X)
            ui.Label(
                "\u2193",  # DOWNWARDS ARROW (section expanded)
                width=18,
                alignment=ui.Alignment.CENTER,
                style=_SECTION_HEADER_ARROW_STYLE,
            )
            ui.Spacer(width=8)
            ui.Label(title, style=_SECTION_HEADER_LABEL_STYLE)
            ui.Spacer()

    # Arrange favorite apps in a single row.
    def _build_favorites_grid(self) -> None:
        cell_height = _APP_ICON_SIZE + LABEL_GAP + LABEL_HEIGHT
        with ui.HStack(height=cell_height):
            ui.Spacer(width=_GRID_HORIZONTAL_PAD)
            for col_index, (label, filename) in enumerate(_FAVORITE_APPS):
                if col_index > 0:
                    ui.Spacer(width=_GRID_COLUMN_GAP)
                self._build_app_cell(label, filename)
            ui.Spacer()

    # Build an app's logo button and label.
    def _build_app_cell(self, label: str, filename: str) -> None:
        with ui.VStack(width=_APP_ICON_SIZE):
            icon_path = LOGOS_DIR / filename
            ui.Button(
                "",
                image_url=str(icon_path),
                width=_APP_ICON_SIZE,
                height=_APP_ICON_SIZE,
                clicked_fn=lambda name=label: self._handle_click(name),
                tooltip=label,
                style=LOGO_BUTTON_STYLE,
            )
            ui.Spacer(height=LABEL_GAP)
            ui.Label(
                label,
                alignment=ui.Alignment.CENTER,
                height=LABEL_HEIGHT,
                style=LABEL_STYLE,
            )

    # Forward the chosen app to the owning window.
    def _handle_click(self, label: str) -> None:
        if self._on_app_clicked is not None:
            self._on_app_clicked(label)

    # Reserved for the store action.
    def _handle_go_to_store(self) -> None:
        # Placeholder; wire to a real store action when one exists.
        pass
