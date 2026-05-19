"""Shared scaffolding for floating destination popup windows.

Centralizes the bits that every popup opened from Home reuses:
  - A no-docking / no-collapse floating ui.Window configured to a fixed
    initial size.
  - Re-centering over the main panel (the dock slot shared by `Home` and
    `Viewport`) every time the popup is shown.
  - Styling for transparent logo buttons + their labels.

Concrete popups (e.g. `apps_window.AppsWindow`) subclass `PopupWindow`
and only implement `_build_body()` plus any extra entries / callbacks.
"""

from pathlib import Path
from typing import Any, cast

import omni.ui as ui


# Resolve to <python_package>/logos/<name>.png (same folder Home uses).
# Exposed so subclasses can build full icon paths without duplicating the
# `.parent.parent.parent` dance.
LOGOS_DIR = Path(__file__).resolve().parent.parent.parent / "logos"


# Default popup size. Subclasses can override by passing width/height to
# `PopupWindow.__init__`.
DEFAULT_WINDOW_WIDTH = 720
DEFAULT_WINDOW_HEIGHT = 480


# Standard sizing for a logo button + its label below.
LOGO_BUTTON_SIZE = 115
LABEL_GAP = 12
LABEL_HEIGHT = 20
COLUMN_GAP = 60


# Transparent logo-button styling (matches the Home page buttons so the
# popups feel like a continuation of the launcher surface).
LOGO_BUTTON_STYLE = {
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


LABEL_STYLE = {
    "color": 0xFFDDDDDD,
    "font_size": 14,
}


class PopupWindow:
    """Floating popup window centered over the main panel.

    Subclasses set `TITLE` and implement `_build_body()` to fill the
    window frame. They may optionally override `WINDOW_WIDTH` /
    `WINDOW_HEIGHT` class attributes for a different size.
    """

    TITLE: str = "Popup"
    WINDOW_WIDTH: int = DEFAULT_WINDOW_WIDTH
    WINDOW_HEIGHT: int = DEFAULT_WINDOW_HEIGHT

    def __init__(self) -> None:
        self._window: ui.Window | None = None

    # PUBLIC API ----------------------------------------------------------

    def show(self) -> None:
        """Create (if needed) and present the popup, centered on screen."""
        if self._window is None:
            self._window = ui.Window(
                self.TITLE,
                width=self.WINDOW_WIDTH,
                height=self.WINDOW_HEIGHT,
                flags=(
                    ui.WINDOW_FLAGS_NO_DOCKING
                    | ui.WINDOW_FLAGS_NO_COLLAPSE
                    | ui.WINDOW_FLAGS_NO_SCROLLBAR
                ),
            )
            with self._window.frame:
                self._build_body()
        # Force-center every time it's reopened so the user always sees it
        # in the middle even if they dragged it before closing.
        self._center_on_main_panel()
        self._window.visible = True
        # Bring to front in case it was already open but hidden behind
        # another floating window.
        try:
            cast(Any, self._window).focus()
        except Exception:
            pass

    def hide(self) -> None:
        if self._window is not None:
            self._window.visible = False

    def destroy(self) -> None:
        if self._window is not None:
            self._window.destroy()
            self._window = None

    # SUBCLASS HOOK -------------------------------------------------------

    def _build_body(self) -> None:
        """Override to populate the window frame. Default is empty."""
        ui.Spacer()

    # INTERNAL ------------------------------------------------------------

    def _center_on_main_panel(self) -> None:
        """Position the window centered over the main panel.

        The main panel is the dock slot shared by the `Home` and `Viewport`
        tabs. We look up whichever of those is currently visible to get its
        screen rect, then center on it. Falls back to the whole app window
        if neither tab is found (e.g. very early during startup).
        """
        if self._window is None:
            return
        try:
            dpi = float(ui.Workspace.get_dpi_scale() or 1.0)
        except Exception:
            dpi = 1.0

        # ui.Window.position_x / width are already in DIP units (the same
        # space we assign back to position_x / position_y), so no DPI
        # conversion is needed when we anchor to another ui.Window.
        target_rect: tuple[float, float, float, float] | None = None
        for title in ("Home", "Viewport"):
            try:
                other = ui.Workspace.get_window(title)
            except Exception:
                other = None
            if other is None:
                continue
            try:
                if not bool(other.visible):
                    continue
                x = float(other.position_x)
                y = float(other.position_y)
                w = float(other.width)
                h = float(other.height)
            except Exception:
                continue
            if w <= 0.0 or h <= 0.0:
                continue
            target_rect = (x, y, w, h)
            break

        if target_rect is None:
            # Fall back to the full app window (DPI-scaled pixels -> DIP).
            try:
                screen_w = float(ui.Workspace.get_main_window_width()) / dpi
                screen_h = float(ui.Workspace.get_main_window_height()) / dpi
            except Exception:
                return
            target_rect = (0.0, 0.0, screen_w, screen_h)

        x, y, w, h = target_rect
        try:
            self._window.position_x = max(0.0, x + (w - float(self.WINDOW_WIDTH)) * 0.5)
            self._window.position_y = max(0.0, y + (h - float(self.WINDOW_HEIGHT)) * 0.5)
        except Exception:
            pass
