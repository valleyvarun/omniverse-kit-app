# Python & library imports
from pathlib import Path
from typing import Any, cast

# Omniverse & USD imports
import omni.kit.actions.core
import omni.ui as ui

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

LOGOS_PATH = Path(__file__).parent / "logos"
WINDOW_WIDTH = 800
WINDOW_HEIGHT = 450
THUMBNAIL_WIDTH = 120
THUMBNAIL_HEIGHT = 90
IMAGE_SIZE = 40
GRID_SPACING = 12
GRID_PADDING = 8
GRID_TOP_PADDING = 16

# workspace tab action
WORKSPACE_EXTENSION_ID = "varun.launcher.workspace.v.01"
ADD_VIEWPORT_TAB_ACTION_ID = "add_viewport_tab"


# ----------------------------------------------------------------------
# 2. APPS WINDOW
# ----------------------------------------------------------------------


class AppsWindow:
    # Prepare popup state and the app thumbnail list.
    def __init__(self):
        self._window: ui.Window | None = None
        self._apps = [("openusd-logo.png", "OpenUSD")]
        self._apps.extend(("people2-icon.png", "dummy") for _ in range(15))

    # create and show popup
    def show(self):
        if self._window is None:
            self._window = ui.Window(
                "apps",
                width=WINDOW_WIDTH,
                height=WINDOW_HEIGHT,
                flags=ui.WINDOW_FLAGS_NO_DOCKING,
                visible=False,
            )
            assert self._window is not None
            self._window.frame.set_build_fn(self._build_content)
            self._window.set_width_changed_fn(self._on_width_changed)

        assert self._window is not None
        self._center_on_main_window()
        self._window.visible = True
        cast(Any, self._window).focus()

    # build app thumbnails
    def _build_content(self):
        if self._window is None:
            # Stop if the popup has not been created yet.
            return

        available_width = max(THUMBNAIL_WIDTH, self._window.width - GRID_PADDING * 2)
        # Fit as many thumbnail columns as the current width allows.
        columns = max(
            1,
            int((available_width + GRID_SPACING) // (THUMBNAIL_WIDTH + GRID_SPACING)),
        )

        # Keep the grid vertically scrollable without a horizontal scrollbar.
        with (
            ui.ScrollingFrame(
                horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
            ),
            ui.VStack(spacing=GRID_SPACING),
        ):
            ui.Spacer(height=GRID_TOP_PADDING)
            for row_start in range(0, len(self._apps), columns):
                row = self._apps[row_start : row_start + columns]
                with ui.HStack(height=THUMBNAIL_HEIGHT, spacing=0):
                    ui.Spacer(width=GRID_PADDING)
                    for index, (image_name, label) in enumerate(row):
                        if index > 0:
                            ui.Spacer(width=GRID_SPACING)
                        self._build_thumbnail(image_name, label)
                    ui.Spacer()

    # rebuild grid when popup width changes
    def _on_width_changed(self, _width: float):
        if self._window is not None:
            self._window.frame.rebuild()

    # build one app thumbnail
    def _build_thumbnail(self, image_name: str, label: str):
        with ui.VStack(
            width=THUMBNAIL_WIDTH,
            height=THUMBNAIL_HEIGHT,
            spacing=6,
            mouse_pressed_fn=lambda *_, name=label: self._on_app_clicked(name),
        ):
            with ui.HStack(height=IMAGE_SIZE, spacing=0):
                ui.Spacer()
                ui.Image(
                    str(LOGOS_PATH / image_name),
                    width=IMAGE_SIZE,
                    height=IMAGE_SIZE,
                    fill_policy=ui.FillPolicy.PRESERVE_ASPECT_FIT,
                )
                ui.Spacer()
            ui.Label(
                label,
                width=THUMBNAIL_WIDTH,
                height=24,
                alignment=ui.Alignment.CENTER,
            )

    # open a new viewport tab for the OpenUSD app
    def _on_app_clicked(self, label: str):
        if label != "OpenUSD":
            return

        action_registry: Any = omni.kit.actions.core.get_action_registry()
        action_registry.execute_action(
            WORKSPACE_EXTENSION_ID,
            ADD_VIEWPORT_TAB_ACTION_ID,
        )

        # close the popup after launching
        if self._window is not None:
            self._window.visible = False

    # center popup over main window
    def _center_on_main_window(self):
        if self._window is None:
            return
        # Prefer Home as the anchor, with the layout placeholder as fallback.

        main_window: Any = ui.Workspace.get_window("home")
        if main_window is None:
            main_window = ui.Workspace.get_window("main_window")
        if main_window is None:
            return

        self._window.position_x = main_window.position_x + (main_window.width - WINDOW_WIDTH) * 0.5
        self._window.position_y = main_window.position_y + (main_window.height - WINDOW_HEIGHT) * 0.5

    # destroy popup
    def destroy(self):
        if self._window is not None:
            self._window.destroy()
            self._window = None
