# Python & library imports
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Omniverse & USD imports
import omni.ext
import omni.kit.actions.core
from omni import ui

# Local imports
from .apps import AppsWindow

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

LOGOS_PATH: Path = Path(__file__).parent / "logos"
IMAGE_SIZE: int = 120
ITEM_WIDTH: int = 150
ITEM_HEIGHT: int = 165
GRID_WIDTH: int = 430
GRID_HEIGHT: int = 365
CLICKABLE_STYLE: dict[str, dict[str, int]] = {
    "Button": {"background_color": 0x00000000, "border_radius": 0},
    "Button:hovered": {"background_color": 0x22FFFFFF},
    "Button:pressed": {"background_color": 0x44FFFFFF},
    "Button.Label": {"color": 0xFFD8D8D8, "font_size": 15},
}


# ----------------------------------------------------------------------
# 2. MAIN EXTENSION CLASS
# ----------------------------------------------------------------------


class MyExtension(omni.ext.IExt):
    # ON STARTUP
    def on_startup(self, _ext_id: str) -> None:
        print("[varun.launcher.home.v.01] Extension startup")

        # --- 1) PREPARE THE APPS POPUP ---

        self._apps_window: AppsWindow = AppsWindow()
        action_registry: Any = omni.kit.actions.core.get_action_registry()
        action_registry.register_action(
            "varun.launcher.home.v.01", "show_apps", self._apps_window.show, display_name="Show Apps"
        )

        # no header, not movable
        flags: int = ui.WINDOW_FLAGS_NO_TITLE_BAR | ui.WINDOW_FLAGS_NO_MOVE

        # --- 2) CREATE THE HOME WINDOW ---

        # create home window
        self._window: ui.Window = ui.Window(
            "home",
            width=1015,
            height=560,
            flags=flags,
            padding_x=0,
            padding_y=0,
        )

        # --- 3) BUILD THE DESTINATION GRID ---

        # build centered logo grid
        with self._window.frame, ui.VStack(spacing=0):
            ui.Spacer()
            with ui.HStack(height=GRID_HEIGHT, spacing=0):
                ui.Spacer()
                with ui.VStack(width=GRID_WIDTH, spacing=40):
                    with ui.HStack(height=ITEM_HEIGHT, spacing=0):
                        self._build_item("plexus-logo.png", "Plexus", self._open_plexus)
                        ui.Spacer()
                        self._build_item("apps-logo.png", "Apps", self._apps_window.show)

                    with ui.HStack(height=ITEM_HEIGHT, spacing=0):
                        self._build_item("nucleus-logo.png", "Nucleus", self._do_nothing)
                        ui.Spacer()
                        self._build_item("market-logo.png", "Market", self._do_nothing)
                ui.Spacer()
            ui.Spacer()

    # build logo and label
    def _build_item(
        self,
        image_name: str,
        label: str,
        clicked_fn: Callable[[], None] | None = None,
    ) -> None:
        with ui.VStack(width=ITEM_WIDTH, height=ITEM_HEIGHT, spacing=20):
            # Draw the logo as a button only when a callback exists.
            with ui.HStack(height=IMAGE_SIZE, spacing=0):
                ui.Spacer()
                if clicked_fn:
                    ui.Button(
                        "",
                        image_url=str(LOGOS_PATH / image_name),
                        width=IMAGE_SIZE,
                        height=IMAGE_SIZE,
                        clicked_fn=clicked_fn,
                        identifier=f"home_icon:{label.lower()}",
                        style=CLICKABLE_STYLE,
                    )
                else:
                    ui.Image(
                        str(LOGOS_PATH / image_name),
                        width=IMAGE_SIZE,
                        height=IMAGE_SIZE,
                        fill_policy=ui.FillPolicy.PRESERVE_ASPECT_FIT,
                    )
                ui.Spacer()
            if clicked_fn:
                # Use the same click behavior for the label below the logo.
                ui.Button(
                    label,
                    width=ITEM_WIDTH,
                    height=24,
                    clicked_fn=clicked_fn,
                    identifier=f"home_label:{label.lower()}",
                    style=CLICKABLE_STYLE,
                )
            else:
                ui.Label(
                    label,
                    width=ITEM_WIDTH,
                    height=24,
                    alignment=ui.Alignment.CENTER,
                    style={"color": 0xFFD8D8D8, "font_size": 15},
                )

    def _open_plexus(self) -> None:
        omni.kit.actions.core.get_action_registry().execute_action("varun.launcher.plexus.v.01", "open_plexus")

    # placeholder click action
    def _do_nothing(self) -> None:
        pass

    # ON SHUTDOWN
    def on_shutdown(self) -> None:
        print("[varun.launcher.home.v.01] Extension shutdown")
        action_registry: Any = omni.kit.actions.core.get_action_registry()
        action_registry.deregister_all_actions_for_extension("varun.launcher.home.v.01")
        self._apps_window.destroy()
        self._window.destroy()
