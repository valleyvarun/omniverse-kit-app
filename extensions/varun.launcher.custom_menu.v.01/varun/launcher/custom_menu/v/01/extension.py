
from typing import Any, cast

import omni.ext
import omni.kit.actions.core
import omni.kit.menu.utils
from omni.kit.menu.utils import MenuItemDescription

# extension that owns the layout action
LAYOUT_ACTION_EXTENSION_ID = "varun.launcher.layout.v.01"

# action that resets the layout
RESET_LAYOUT_ACTION_ID = "reset_default_layout"

# Kit action API
ACTIONS = cast(Any, omni.kit.actions.core)

# Kit menu API
MENU_UTILS = cast(Any, omni.kit.menu.utils)


class MyExtension(omni.ext.IExt):

    # ON STARTUP
    def on_startup(self, _ext_id: str):
        print("[varun.launcher.custom_menu.v.01] Extension startup")

        # add reset option to Window menu
        self._window_menu_items = [
            MenuItemDescription(
                name="Reset to Default Layout",
                onclick_fn=self._reset_default_layout,
            )
        ]
        MENU_UTILS.add_menu_items(self._window_menu_items, name="Window")

    # run layout reset action
    def _reset_default_layout(self):
        ACTIONS.execute_action(
            LAYOUT_ACTION_EXTENSION_ID,
            RESET_LAYOUT_ACTION_ID,
        )

    # ON SHUTDOWN
    def on_shutdown(self):
        print("[varun.launcher.custom_menu.v.01] Extension shutdown")

        # remove custom menu items
        MENU_UTILS.remove_menu_items(
            self._window_menu_items,
            name="Window",
        )
        self._window_menu_items = []
