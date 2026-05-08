import asyncio
import logging
from typing import Any, cast

import omni.kit.menu.utils
from omni.kit.menu.utils import MenuItemDescription

from .startup_defaults import StartupDefaults


LOGGER = logging.getLogger(__name__)
MENU_UTILS = cast(Any, omni.kit.menu.utils)


# Manages dock styling and the "Apply Default Layout" Window menu entry.
class LayoutDocking:
    def __init__(self, startup_defaults: StartupDefaults) -> None:
        self._startup_defaults = startup_defaults
        self._default_layout_menu_items: list[MenuItemDescription] = []

    # Match the dock splitter look used by the Composer-style layout.
    def apply_dock_style(self) -> None:
        try:
            import omni.kit.imgui as _imgui

            imgui = _imgui.acquire_imgui()
            if imgui.is_valid():
                imgui.push_style_var_float(_imgui.StyleVar.DockSplitterSize, 2)
        except ImportError:
            LOGGER.warning("omni.kit.imgui not available; using default dock splitter size")

    # Add an "Apply Default Layout" entry to the Window menu.
    def add_window_menu_items(self) -> None:
        async def _apply_default_layout() -> None:
            await self._startup_defaults.load_layout()

        self._default_layout_menu_items = [
            MenuItemDescription(
                name="Apply Default Layout",
                onclick_fn=lambda: asyncio.ensure_future(_apply_default_layout()),
            )
        ]
        MENU_UTILS.add_menu_items(self._default_layout_menu_items, name="Window")

    # Remove the menu entries on shutdown.
    def remove_window_menu_items(self) -> None:
        if self._default_layout_menu_items:
            MENU_UTILS.remove_menu_items(self._default_layout_menu_items, name="Window")
            self._default_layout_menu_items = []
