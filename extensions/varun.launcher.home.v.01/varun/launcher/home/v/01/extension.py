
from collections.abc import Callable
from pathlib import Path

import omni.ext
from omni import ui

from .apps import AppsWindow

LOGOS_PATH = Path(__file__).parent / "logos"
IMAGE_SIZE = 120
ITEM_WIDTH = 150
ITEM_HEIGHT = 165
GRID_WIDTH = 430
GRID_HEIGHT = 365
CLICKABLE_STYLE = {
    "Button": {"background_color": 0x00000000, "border_radius": 0},
    "Button:hovered": {"background_color": 0x22FFFFFF},
    "Button:pressed": {"background_color": 0x44FFFFFF},
    "Button.Label": {"color": 0xFFD8D8D8, "font_size": 15},
}


class MyExtension(omni.ext.IExt):

    # ON STARTUP
    def on_startup(self, _ext_id: str):
        print("[varun.launcher.home.v.01] Extension startup")

        self._apps_window = AppsWindow()

        # no header, not movable
        flags = ui.WINDOW_FLAGS_NO_TITLE_BAR | ui.WINDOW_FLAGS_NO_MOVE

        # create home window
        self._window = ui.Window(
            "home",
            width=1015,
            height=560,
            flags=flags,
            padding_x=0,
            padding_y=0,
        )

        # build centered logo grid
        with self._window.frame, ui.VStack(spacing=0):
            ui.Spacer()
            with ui.HStack(height=GRID_HEIGHT, spacing=0):
                ui.Spacer()
                with ui.VStack(width=GRID_WIDTH, spacing=40):
                    with ui.HStack(height=ITEM_HEIGHT, spacing=0):
                        self._build_item("plexus-logo.png", "Plexus", self._do_nothing)
                        ui.Spacer()
                        self._build_item(
                            "apps-logo.png", "Apps", self._apps_window.show
                        )

                    with ui.HStack(height=ITEM_HEIGHT, spacing=0):
                        self._build_item(
                            "nucleus-logo.png", "Nucleus", self._do_nothing
                        )
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
    ):
        with ui.VStack(width=ITEM_WIDTH, height=ITEM_HEIGHT, spacing=20):
            with ui.HStack(height=IMAGE_SIZE, spacing=0):
                ui.Spacer()
                if clicked_fn:
                    ui.Button(
                        "",
                        image_url=str(LOGOS_PATH / image_name),
                        width=IMAGE_SIZE,
                        height=IMAGE_SIZE,
                        clicked_fn=clicked_fn,
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
                ui.Button(
                    label,
                    width=ITEM_WIDTH,
                    height=24,
                    clicked_fn=clicked_fn,
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

    # placeholder click action
    def _do_nothing(self):
        pass

    # ON SHUTDOWN
    def on_shutdown(self):
        print("[varun.launcher.home.v.01] Extension shutdown")
        self._apps_window.destroy()
        self._window.destroy()
