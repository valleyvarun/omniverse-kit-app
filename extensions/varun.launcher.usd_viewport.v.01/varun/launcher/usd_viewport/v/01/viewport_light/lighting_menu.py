"""Use Kit's existing lighting button for the active Light viewport theme."""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal, TypeAlias, cast

import carb.settings
import omni.kit.app
from omni import ui
from omni.kit.viewport.menubar.core.viewport_menu_model import get_item

from .settings import STORM, THEME

Theme: TypeAlias = Literal["light", "dark"]
MENU_IDENTIFIER: str = "ViewportLightThemeMenu"


@dataclass
class _MenuOverride:
    context: Any
    menu: ui.Menu
    native_build: Callable[[], None]
    native_text: str
    native_identifier: str
    theme: Theme | None = None


class LightingThemeMenu:
    """Replace only existing menu callbacks; Kit retains ownership of its button."""

    def __init__(self) -> None:
        self._container: Any = None
        self._menus: dict[int, _MenuOverride] = {}
        self._update_sub: Any = (
            cast(Any, omni.kit.app.get_app())
            .get_update_event_stream()
            .create_subscription_to_pop(self._sync, name="Viewport Light lighting menu")
        )
        self._sync()

    @staticmethod
    def _is_light(context: Any) -> bool:
        viewport: Any = context.viewport_api
        return viewport.hydra_engine == "pxr" and viewport.render_mode == STORM

    @staticmethod
    def _theme() -> Theme:
        return "dark" if carb.settings.get_settings().get(THEME) == "dark" else "light"

    # Kit 107.3 keeps contexts private; use public ui.Menu callbacks on their widgets.
    def _contexts(self) -> dict[int, Any]:
        return cast(dict[int, Any], getattr(self._container, "_MenuContainer__menu_contexts", {}))

    def _sync(self, _event: Any = None) -> None:
        container: Any = get_item("Lighting")
        if container is not self._container:
            self._restore_all()
            self._container = container
        if self._container is None:
            return
        contexts: dict[int, Any] = self._contexts()
        rebuild: bool = False
        viewport_id: int
        context: Any
        for viewport_id, context in tuple(contexts.items()):
            menu: ui.Menu | None = context.root_menu
            previous: _MenuOverride | None = self._menus.get(viewport_id)
            if previous is not None and (previous.context is not context or previous.menu is not menu):
                # Kit already destroyed this widget while rebuilding the menubar.
                self._menus.pop(viewport_id)
                previous = None
            if menu is None:
                continue
            if not self._is_light(context):
                if previous is not None:
                    self._restore(previous)
                    self._menus.pop(viewport_id)
                    rebuild = True
                continue
            if previous is None:
                native_build: Callable[[], None] = partial(self._container._MenuContainer__build_root_menu, context)
                previous = _MenuOverride(context, menu, native_build, menu.text, menu.identifier)
                self._menus[viewport_id] = previous
                # Old native items must not update the theme caption on rig notifications.
                context.menu_items.clear()
                context.import_item = None
                menu.set_on_build_fn(partial(self._build_themes, previous))
                menu.identifier = MENU_IDENTIFIER
            theme: Theme = self._theme()
            caption: str = "Light mode" if theme == "light" else "Dark mode"
            if previous.theme != theme:
                previous.theme = theme
                menu.invalidate()
            if menu.text != caption:
                menu.text = caption
        for viewport_id in self._menus.keys() - contexts.keys():
            self._menus.pop(viewport_id)
        if rebuild:
            # Native rebuilding releases its old collections and creates a fresh context.
            self._container._invalidate()

    def _build_themes(self, override: _MenuOverride) -> None:
        if not self._is_light(override.context):
            override.native_build()
            return
        theme: Theme = self._theme()
        choices: tuple[tuple[str, Theme], ...] = (("Light mode", "light"), ("Dark mode", "dark"))
        label: str
        value: Theme
        for label, value in choices:
            ui.MenuItem(
                label,
                identifier="ViewportLightThemeLight" if value == "light" else "ViewportLightThemeDark",
                checkable=True,
                checked=theme == value,
                toggle=False,
                hide_on_click=True,
                triggered_fn=partial(self._choose_theme, value),
            )

    @staticmethod
    def _choose_theme(theme: Theme) -> None:
        carb.settings.get_settings().set(THEME, theme)

    @staticmethod
    def _restore(override: _MenuOverride) -> None:
        override.menu.set_on_build_fn(override.native_build)
        override.menu.text = override.native_text
        override.menu.identifier = override.native_identifier
        override.menu.invalidate()

    def _restore_all(self) -> None:
        contexts: dict[int, Any] = self._contexts()
        restored: bool = False
        viewport_id: int
        override: _MenuOverride
        for viewport_id, override in self._menus.items():
            if contexts.get(viewport_id) is override.context and override.context.root_menu is override.menu:
                self._restore(override)
                restored = True
        self._menus.clear()
        if restored:
            self._container._invalidate()

    def destroy(self) -> None:
        self._update_sub = None
        self._restore_all()
        self._container = None
