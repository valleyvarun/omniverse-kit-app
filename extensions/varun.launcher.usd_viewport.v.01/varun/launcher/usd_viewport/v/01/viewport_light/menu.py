"""Adapt the native renderer label and lighting menu for Viewport Light."""

# Python & library imports
from typing import Any

# Omniverse & USD imports
import carb.settings
from omni.kit.viewport.menubar.render import SingleRenderMenuItem, get_instance

# Local imports
from .lighting_menu import LightingThemeMenu
from .settings import LABEL, ROOT, STORM

# ----------------------------------------------------------------------
# 1. LIGHT MENU
# ----------------------------------------------------------------------


class LightMenu:
    # Kit's renderer adapter supports dynamic registration and cleanup values.
    _extension: Any
    _container: Any
    _renamed: list[tuple[Any, str]]

    # Adapt Kit's renderer menu and reuse its lighting button for theme selection.
    def __init__(self) -> None:
        self._extension = get_instance()
        # Kit exposes a custom menu-item factory, but no public renderer-label
        # registry. This single SDK adapter also sets the collapsed menu's title.
        self._container = self._extension._render_menu
        self._renamed = []
        self.refresh_names()
        self._extension.register_menu_item_type(self._make_item)
        self._lighting: LightingThemeMenu = LightingThemeMenu()

    # Rename Storm entries to Viewport Light.
    def refresh_names(self) -> None:
        if self._container is None:
            return
        engine: str
        group: Any
        renderer: Any
        for engine, group in self._container.render_list.renderers:
            if engine == "pxr":
                for renderer in group.renderers:
                    if renderer.pluginID == STORM and renderer.displayName != LABEL:
                        self._renamed.append((renderer, renderer.displayName))
                        renderer.displayName = LABEL

    # Build a renderer entry and disable Light when native support is missing.
    def _make_item(
        self, display_name: str, engine_name: str, group: Any, renderer: Any, viewport_api: Any, **kwargs: Any
    ) -> SingleRenderMenuItem:
        if engine_name == "pxr" and renderer.pluginID == STORM:
            display_name = LABEL
            if not carb.settings.get_settings().get(f"{ROOT}/ready"):
                kwargs["enabled"] = False
        return SingleRenderMenuItem(display_name, engine_name, group, renderer, viewport_api, **kwargs)

    # Remove custom menu items and restore original renderer names.
    def destroy(self) -> None:
        self._lighting.destroy()
        renderer: Any
        old_name: str
        for renderer, old_name in self._renamed:
            renderer.displayName = old_name
        self._renamed.clear()
        if self._extension is not None:
            self._extension.register_menu_item_type(None)
        self._container = None
        self._extension = None
