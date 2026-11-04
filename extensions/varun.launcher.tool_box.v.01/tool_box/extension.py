"""Start and stop the toolbox without owning the host app or viewport."""

from collections.abc import Callable
from functools import partial
from typing import Any, cast

import omni.ext
import omni.kit.menu.utils as menu_utils
import omni.ui

from tool_box.core.controller import ToolController
from tool_box.core.tool import Tool
from tool_box.core.viewport import ViewportMonitor, get_active_stage
from tool_box.shortcuts import WINDOW_TITLE, ShortcutManager
from tool_box.tools.drawing import canvas, method_runtime
from tool_box.tools.drawing.plane.geometry import PlaneRegistry
from tool_box.tools.drawing.plane.selection import PlaneSelection
from tool_box.tools.drawing.points.mesh import ensure_paint_material
from tool_box.ui.window import ToolBoxWindow


class ToolBoxExtension(omni.ext.IExt):
    # Keep lifecycle handles explicit for editor diagnostics.
    def __init__(self) -> None:
        super().__init__()
        self._controller: ToolController | None = None
        self._window: ToolBoxWindow | None = None
        self._shortcuts: ShortcutManager | None = None
        self._monitor: ViewportMonitor | None = None
        self._menu: list[menu_utils.MenuItemDescription] = []

    def on_startup(self, _ext_id: str) -> None:
        self._controller = ToolController()
        self._window = ToolBoxWindow(self._controller)
        # Bind each tool to a callback with the same signature.
        callbacks: dict[str, Callable[[], None]] = {}
        name: str
        tool: Tool
        for name, tool in self._controller.tools.items():
            callbacks[name] = tool.activate
        callbacks["cancel"] = self._controller.cancel
        axis: int
        for axis in (0, 1, 2, 3):
            callbacks[f"cursor_view_{axis}"] = partial(self._switch_view, axis)
        self._shortcuts = ShortcutManager(callbacks)
        self._monitor = ViewportMonitor(self._on_viewport_changed)
        self._menu = [menu_utils.MenuItemDescription(name=WINDOW_TITLE, onclick_fn=self._show)]
        cast(Any, menu_utils).add_menu_items(self._menu, "Window")
        omni.ui.Workspace.set_show_window_fn(WINDOW_TITLE, self._show)

    @staticmethod
    def _switch_view(axis: int) -> None:
        method_runtime.switch_view(axis)

    def _show(self, visible: bool = True) -> None:
        if self._window is not None:
            if visible:
                self._window.show()
            else:
                self._window.window.visible = False

    def _on_viewport_changed(self) -> None:
        stage: Any = get_active_stage()
        if stage is not None and stage.GetPrimAtPath("/World/ThreeDDraw/_PaintRibbonMaterial"):
            ensure_paint_material(stage)
        if self._controller is not None:
            self._controller.cancel()
            method_runtime.destroy()
            canvas.destroy()
            self._controller.points.repairs.open_stage(stage)
        PlaneSelection.get().notify()
        if self._window is not None:
            self._window.on_viewport_changed()

    # Release input callbacks before widgets, tools, and shared state.
    def on_shutdown(self) -> None:
        cast(Any, omni.ui.Workspace).set_show_window_fn(WINDOW_TITLE, None)
        if self._menu:
            cast(Any, menu_utils).remove_menu_items(self._menu, "Window")
            self._menu.clear()
        name: str
        for name in ("_monitor", "_shortcuts", "_window", "_controller"):
            instance: Any = getattr(self, name)
            if instance is not None:
                instance.destroy()
                setattr(self, name, None)
        PlaneRegistry.clear()
        PlaneSelection.get().destroy()
