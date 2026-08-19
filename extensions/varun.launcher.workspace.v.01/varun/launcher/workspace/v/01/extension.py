import asyncio
from pathlib import Path
from typing import Any, cast

import omni.ext
import omni.kit.actions.core
import omni.kit.app
import omni.usd
from omni import ui

# layout action
LAYOUT_EXTENSION_ID = "varun.launcher.layout.v.01"
SHOW_MAIN_WINDOW_ACTION_ID = "show_main_window"

# workspace data
SAMPLE_USD_PATH = Path(__file__).resolve().parents[5] / "data" / "viewport.usda"
DEFAULT_TAB_NUMBER = 0
WORKSPACE_TABS = [
    (0, "home", ""),
    (1, "Viewport", str(SAMPLE_USD_PATH)),
]

# tab dimensions
TAB_WIDTH = 100
TAB_HEIGHT = 30

# inactive tab appearance
INACTIVE_TAB_STYLE = {
    "Button": {
        "background_color": 0xFF333333,
        "border_radius": 0,
        "margin": 0,
        "padding": 0,
    },
    "Button.Label": {"color": 0xFFAAAAAA},
    "Button:hovered": {"background_color": 0xFF3D3D3D},
}

# active tab appearance
ACTIVE_TAB_STYLE = {
    "Button": {
        "background_color": 0xFF4A4A4A,
        "border_radius": 0,
        "margin": 0,
        "padding": 0,
    },
    "Button.Label": {"color": 0xFFFFFFFF},
    "Button:hovered": {"background_color": 0xFF4A4A4A},
}


class MyExtension(omni.ext.IExt):

    # ON STARTUP
    def on_startup(self, _ext_id: str):
        print("[varun.launcher.workspace.v.01] Extension startup")
        self._alive = True

        # create tabs bar window
        flags = ui.WINDOW_FLAGS_NO_TITLE_BAR | ui.WINDOW_FLAGS_NO_MOVE
        self._window = ui.Window(
            "tabs_bar_window",
            width=1015,
            height=30,
            flags=flags,
            padding_x=0,
            padding_y=0,
        )
        self._active_tab = DEFAULT_TAB_NUMBER
        self._window.frame.set_build_fn(self._build_tabs)

        # layout action controls which tab window is visible
        for _, window_name, _ in WORKSPACE_TABS:
            tab_window = ui.Workspace.get_window(window_name)
            if tab_window:
                tab_window.visible = False

        asyncio.ensure_future(self._initialize_workspace())

    # load the default tab after layout startup
    async def _initialize_workspace(self):
        kit_app = cast(Any, omni.kit.app.get_app())
        for _ in range(5):
            await kit_app.next_update_async()

        await self._show_active_tab(self._active_tab)

    # build tabs bar
    def _build_tabs(self):
        with ui.HStack(spacing=1):
            for tab_number, window_name, _ in WORKSPACE_TABS:
                ui.Button(
                    window_name.title(),
                    width=TAB_WIDTH,
                    height=TAB_HEIGHT,
                    style=self._tab_style(tab_number),
                    clicked_fn=lambda number=tab_number: self._select_tab(number),
                )
            ui.Spacer()

    # return active or inactive style
    def _tab_style(self, tab_number: int):
        if tab_number == self._active_tab:
            return ACTIVE_TAB_STYLE
        return INACTIVE_TAB_STYLE

    # select tab and refresh UI
    def _select_tab(self, tab_number: int):
        if not any(tab[0] == tab_number for tab in WORKSPACE_TABS):
            return

        self._active_tab = tab_number
        self._window.frame.rebuild()
        asyncio.ensure_future(self._show_active_tab(tab_number))

    # hand the active tab window to main_window
    async def _show_active_tab(self, tab_number: int):
        tab_data = next((tab for tab in WORKSPACE_TABS if tab[0] == tab_number), None)
        if not tab_data:
            return

        _, window_name, usd_path = tab_data
        if not self._alive or tab_number != self._active_tab:
            return

        # hand active window to the layout extension
        action_registry: Any = omni.kit.actions.core.get_action_registry()
        action_registry.execute_action(
            LAYOUT_EXTENSION_ID,
            SHOW_MAIN_WINDOW_ACTION_ID,
            window_name,
        )

        # load the tab's USD file
        if usd_path:
            omni.usd.get_context().open_stage(usd_path)

    # ON SHUTDOWN
    def on_shutdown(self):
        print("[varun.launcher.workspace.v.01] Extension shutdown")
        self._alive = False
        self._window.destroy()
