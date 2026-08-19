import asyncio
import json
from pathlib import Path
from typing import Any, cast

import omni.ext
import omni.kit.actions.core
import omni.kit.app
from omni import ui

from .assign_window import WindowAssigner

# layout file location
LAYOUT_PATH = Path(__file__).resolve().parents[5] / "layouts" / "default.json"

# layout action identifiers
ACTION_EXTENSION_ID = "varun.launcher.layout.v.01"
RESET_LAYOUT_ACTION_ID = "reset_default_layout"
SHOW_MAIN_WINDOW_ACTION_ID = "show_main_window"

# fixed window heights
TABS_WINDOW_HEIGHT = 30
TOP_WINDOW_HEIGHT = 75
BOTTOM_WINDOW_HEIGHT = 65


class MyExtension(omni.ext.IExt):

    # ON STARTUP
    def on_startup(self, _ext_id: str):
        print("[varun.launcher.layout.v.01] Extension startup")

        # create window assignment controller
        self._window_assigner = WindowAssigner()
        self._layout_load_number = 0

        # window currently occupying the main slot
        self._current_main = "main_window"

        # register layout reset action
        action_registry: Any = omni.kit.actions.core.get_action_registry()
        action_registry.register_action(
            ACTION_EXTENSION_ID,
            RESET_LAYOUT_ACTION_ID,
            self.reset_default_layout,
            display_name="Reset to Default Layout",
            description="Restore the launcher window layout.",
        )
        action_registry.register_action(
            ACTION_EXTENSION_ID,
            SHOW_MAIN_WINDOW_ACTION_ID,
            self.show_main_window,
            display_name="Show Main Window",
            description="Show a window in the launcher main area.",
        )

        # no header, not movable
        flags = ui.WINDOW_FLAGS_NO_TITLE_BAR | ui.WINDOW_FLAGS_NO_MOVE

        # store created layout windows
        self._windows: list[ui.Window] = []

        # create windows
        for name, width, height in (
            ("main_window", 1015, 560),
            ("tabs_window", 1015, 30),
            ("top_window", 1145, 75),
            ("left_window", 130, 655),
            ("bottom_window", 1015, 65),
            ("right_top_window", 295, 180),
            ("right_bottom_window", 295, 475),
        ):
            if name not in self._window_assigner.assignments:
                self._windows.append(
                    ui.Window(name, width=width, height=height, flags=flags)
                )

        # start height enforcement
        self._enforce_window_heights = True
        asyncio.ensure_future(self._keep_window_heights_fixed())

        # load default layout
        self.reset_default_layout()

    # replace main_window and reload layout
    def show_main_window(self, window_name: str):
        self._window_assigner.set_main_window(window_name)
        self._request_layout_load()

    # start a layout load with the latest window name
    def _request_layout_load(self):
        self._layout_load_number += 1
        asyncio.ensure_future(self._load_layout(self._layout_load_number))

    # enforces fixed height of 'tabs_window' and 'top_window'
    async def _keep_window_heights_fixed(self):
        kit_app: Any = omni.kit.app.get_app()
        while self._enforce_window_heights:
            await kit_app.next_update_async()
            if not self._enforce_window_heights:
                return

            # keep tabs window height fixed
            tabs_window = ui.Workspace.get_window(
                self._window_assigner.window_name("tabs_window")
            )
            if tabs_window and tabs_window.dock_id >= 0:
                ui.Workspace.set_dock_id_height(
                    tabs_window.dock_id, TABS_WINDOW_HEIGHT
                )

            # find top and bottom windows
            top_window = ui.Workspace.get_window(
                self._window_assigner.window_name("top_window")
            )
            bottom_window = ui.Workspace.get_window(
                self._window_assigner.window_name("bottom_window")
            )
            if not top_window or not bottom_window:
                continue

            # restore heights after top resize
            top_height_changed = abs(top_window.height - TOP_WINDOW_HEIGHT) > 0.5
            if top_window.dock_id >= 0 and top_height_changed:
                ui.Workspace.set_dock_id_height(
                    top_window.dock_id, TOP_WINDOW_HEIGHT
                )

                if bottom_window.dock_id >= 0:
                    ui.Workspace.set_dock_id_height(
                        bottom_window.dock_id, BOTTOM_WINDOW_HEIGHT
                    )

    # reset layout to default
    def reset_default_layout(self):
        self._request_layout_load()

    # load layout from JSON file
    async def _load_layout(self, load_number: int):
        kit_app: Any = omni.kit.app.get_app()

        # wait for windows to initialize
        for _ in range(3):
            await kit_app.next_update_async()

        # read default layout
        with LAYOUT_PATH.open(encoding="utf-8") as layout_file:
            layout = json.load(layout_file)

        # ignore an older tab request
        if load_number != self._layout_load_number:
            return

        # apply window assignments
        self._window_assigner.apply_layout_assignments(layout)

        # restore resolved layout
        cast(Any, ui.Workspace).restore_workspace(layout, True)

        # wait for late-created windows to settle
        for _ in range(20):
            await kit_app.next_update_async()

        # ignore an older tab request
        if load_number != self._layout_load_number:
            return

        # restore again so the layout wins over late dock calls
        cast(Any, ui.Workspace).restore_workspace(layout, True)
        await kit_app.next_update_async()

        # hide the window previously in the main slot
        new_main = self._window_assigner.main_window_name
        if new_main != self._current_main:
            previous_window = ui.Workspace.get_window(self._current_main)
            if previous_window:
                previous_window.visible = False
            self._current_main = new_main

        # show and select the active window
        active_window = ui.Workspace.get_window(new_main)
        if active_window:
            active_window.visible = True
            cast(Any, active_window).selected_in_dock = True

    # ON SHUTDOWN
    def on_shutdown(self):
        print("[varun.launcher.layout.v.01] Extension shutdown")

        self._enforce_window_heights = False

        action_registry: Any = omni.kit.actions.core.get_action_registry()
        action_registry.deregister_all_actions_for_extension(ACTION_EXTENSION_ID)

        for window in self._windows:
            window.destroy()
