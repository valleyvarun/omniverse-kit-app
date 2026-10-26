"""Small adapter for Kit 109's existing renderer selector."""

# Python & library imports
from typing import Any

# Omniverse & USD imports
import carb.settings
import omni.ui as ui
from omni.kit.viewport.menubar.core import ViewportMenuItem
from omni.kit.viewport.menubar.core.viewport_menu_model import (
    pop_from_scope,
    push_to_scope,
)
from omni.kit.viewport.menubar.render import SingleRenderMenuItem, get_instance

# Local imports
from .settings import LABEL, ROOT, STORM, THEME

# ----------------------------------------------------------------------
# 1. APPEARANCE MENU
# ----------------------------------------------------------------------


class AppearanceMenu(ViewportMenuItem):
    # Name the Viewport Light appearance submenu.
    def __init__(self):
        super().__init__(name="Viewport Light Appearance")

    # Build Light and Dark theme choices for the active Light viewport.
    def build_fn(self, factory_args):
        settings = carb.settings.get_settings()
        with ui.Menu(self.name, enabled=bool(settings.get(f"{ROOT}/active"))):
            for label, value in (("Light · Grey Studio", "light"), ("Dark", "dark")):
                ui.MenuItem(
                    label,
                    checkable=True,
                    checked=settings.get(THEME) == value,
                    triggered_fn=lambda value=value: settings.set(THEME, value),
                    hide_on_click=True,
                )


# ----------------------------------------------------------------------
# 2. LIGHT MENU
# ----------------------------------------------------------------------


class LightMenu:
    # Kit's renderer adapter supports dynamic registration and cleanup values.
    _extension: Any
    _container: Any
    _renamed: list[tuple[Any, str]]

    # Adapt Kit's renderer menu and attach the appearance submenu.
    def __init__(self):
        self._extension = get_instance()
        # Kit exposes a custom menu-item factory, but no public renderer-label
        # registry. This single SDK adapter also sets the collapsed menu's title.
        self._container = self._extension._render_menu
        self._renamed = []
        self.refresh_names()
        self._extension.register_menu_item_type(self._make_item)
        # The container's context manager clears existing custom items.
        # Push a scope directly so other extensions retain their entries.
        push_to_scope(self._container)
        try:
            self._appearance = AppearanceMenu()
        finally:
            pop_from_scope()

    # Rename Storm entries to Viewport Light.
    def refresh_names(self):
        if self._container is None:
            return
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
    def destroy(self):
        self._appearance.destroy()
        for renderer, old_name in self._renamed:
            renderer.displayName = old_name
        self._renamed.clear()
        if self._extension is not None:
            self._extension.register_menu_item_type(None)
        self._container = None
        self._extension = None
