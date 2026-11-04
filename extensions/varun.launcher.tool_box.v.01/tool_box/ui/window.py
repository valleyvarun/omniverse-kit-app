"""Dockable tool categories and responsive active-tool settings."""

from typing import Any, cast

import carb.settings
import omni.ui

from tool_box.core.controller import ToolController
from tool_box.core.tool import Tool
from tool_box.core.viewport import get_active_stage
from tool_box.shortcuts import WINDOW_TITLE
from tool_box.tools.drawing.method_properties import CanvasProperties
from tool_box.tools.drawing.properties import ThreeDDrawPropertiesPanel
from tool_box.tools.transform.properties import TransformPropertiesPanel
from tool_box.ui.properties import (
    ToolPropertiesPanel,
    use_stacked_settings,
)


class ToolBoxWindow:
    def __init__(self, controller: ToolController) -> None:
        self._controller: ToolController = controller
        self._content: Any = None
        self._panels: list[ToolPropertiesPanel] = []
        self._tools: omni.ui.Container | None = None
        self._groups: list[tuple[omni.ui.Container, omni.ui.Label, tuple[str, ...]]] = []
        self._buttons: dict[str, omni.ui.Button] = {}
        self._canvas: CanvasProperties = CanvasProperties(controller.tools)
        settings: Any = carb.settings.get_settings()
        bottom_docked: bool = settings.get("/app/launcher/layout/bottom_window") == WINDOW_TITLE
        side_docked: bool = settings.get("/app/launcher/layout/left_window") == WINDOW_TITLE
        # The launcher restores this window into its assigned bottom slot.
        if bottom_docked:
            self.window: Any = omni.ui.Window(
                WINDOW_TITLE,
                width=1015,
                height=100,
                flags=omni.ui.WINDOW_FLAGS_NO_MOVE,
                padding_x=4,
                padding_y=2,
            )
            with self.window.frame, omni.ui.HStack(spacing=12) as self._tools:
                self._build_group("Transform", ("move", "rotate", "scale"))
                self._build_group("Drawing", ("draw_points", "draw_curves"))
                with omni.ui.ScrollingFrame(
                    width=142, horizontal_scrollbar_policy=omni.ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF
                ):
                    self._canvas.build()
                omni.ui.Rectangle(width=1, style={"background_color": 0xFF444444})
                with omni.ui.ScrollingFrame(horizontal_scrollbar_policy=omni.ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF):
                    self._content = omni.ui.Frame(height=0)
        elif side_docked:
            self.window = omni.ui.Window(WINDOW_TITLE, width=340, height=850, padding_x=8, padding_y=8)
            with (
                self.window.frame,
                omni.ui.ScrollingFrame(horizontal_scrollbar_policy=omni.ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF),
                omni.ui.VStack(spacing=12, height=0) as self._tools,
            ):
                self._build_group("Transform", ("move", "rotate", "scale"))
                self._build_group("Drawing", ("draw_points", "draw_curves"))
                self._canvas.build()
                self._content = omni.ui.Frame(height=0)
        else:
            self.window = omni.ui.Window(
                WINDOW_TITLE, width=650, height=230, dockPreference=omni.ui.DockPreference.DISABLED
            )
            with self.window.frame, omni.ui.VStack(spacing=8) as self._tools:
                with omni.ui.HStack(height=0, spacing=20):
                    omni.ui.Spacer(width=8)
                    self._build_group("Transform", ("move", "rotate", "scale"))
                    self._build_group("Drawing", ("draw_points", "draw_curves"))
                    self._canvas.build()
                    omni.ui.Spacer()
                omni.ui.Rectangle(height=1, style={"background_color": 0xFF444444})
                with omni.ui.ScrollingFrame(horizontal_scrollbar_policy=omni.ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF):
                    self._content = omni.ui.Frame(height=0)
        self._settings_layout: tuple[bool, bool] = (use_stacked_settings(), self.window.width >= 950)
        self._panels = [ThreeDDrawPropertiesPanel(self.refresh), TransformPropertiesPanel(self.refresh)]
        self.refresh()
        self._sync_visibility()
        self.window.set_width_changed_fn(self._on_width_changed)

    # Rebuild only when resizing crosses the settings layout breakpoint.
    def _on_width_changed(self, width: float) -> None:
        layout: tuple[bool, bool] = (use_stacked_settings(), width >= 950)
        if layout != self._settings_layout:
            self._settings_layout = layout
            self.refresh()

    # Home detaches the stage; keep the docked window but hide all tool content.
    def _sync_visibility(self) -> None:
        if self._tools is not None:
            self._tools.visible = get_active_stage() is not None

    def _build_group(self, title: str, action_ids: tuple[str, ...]) -> None:
        group: omni.ui.Container
        with omni.ui.VStack(width=len(action_ids) * 44 - 6, spacing=4) as group:
            label: omni.ui.Label = omni.ui.Label(title, height=18)
            self._groups.append((group, label, action_ids))
            with omni.ui.HStack(spacing=6):
                action_id: str
                for action_id in action_ids:
                    button: omni.ui.Button = self._controller.tools[action_id].build_button(size=38)
                    if button.image_url.endswith(".png"):
                        style: dict[str, dict[str, int]] = cast(dict[str, dict[str, int]], button.style)
                        button.style = {**style, "Button": {**style["Button"], "padding": 2}}
                        button.image_width = omni.ui.Pixel(28)
                        button.image_height = omni.ui.Pixel(28)
                    self._buttons[action_id] = button

    # Collapse the chooser to the active tool until it is deselected.
    def _sync_tool_controls(self) -> None:
        active_id: str | None = None
        action_id: str
        tool: Tool
        for action_id, tool in self._controller.tools.items():
            if tool.is_active:
                active_id = action_id
                break
        button: omni.ui.Button
        for action_id, button in self._buttons.items():
            button.visible = active_id is None or action_id == active_id
        group: omni.ui.Container
        label: omni.ui.Label
        action_ids: tuple[str, ...]
        for group, label, action_ids in self._groups:
            group.visible = active_id is None or active_id in action_ids
            group.width = omni.ui.Pixel(len(action_ids) * 44 - 6 if active_id is None else 38)
            label.visible = active_id is None

    # Show brush controls or the selected prim's transform fields.
    def refresh(self) -> None:
        self._sync_tool_controls()
        if self._content is None:
            return
        self._content.clear()
        panel: ToolPropertiesPanel
        for panel in self._panels:
            if panel.is_active():
                panel.build_into(self._content)
                return

    def on_viewport_changed(self) -> None:
        self._sync_visibility()
        self._canvas.refresh()
        self._controller.transforms.bind_context()
        panel: ToolPropertiesPanel
        for panel in self._panels:
            if isinstance(panel, TransformPropertiesPanel):
                panel.bind_context()
        self.refresh()

    def show(self) -> None:
        self.window.visible = True
        self.window.focus()

    def destroy(self) -> None:
        self.window.set_width_changed_fn(None)
        self._canvas.destroy()
        panel: ToolPropertiesPanel
        for panel in self._panels:
            panel.destroy()
        self._panels.clear()
        self._content = None
        self._tools = None
        self._groups.clear()
        self._buttons.clear()
        self.window.destroy()
