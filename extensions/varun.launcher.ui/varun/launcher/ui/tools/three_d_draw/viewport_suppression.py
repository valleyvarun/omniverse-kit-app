"""Hide viewport selection / context-menu interactions while the 3D Draw tool is active.

The freehand-draw cursor needs the viewport to ignore left-click selection,
object-click manipulation, and right-click context menus. We hide the
relevant manipulator layers and override the corresponding carb setting
on `enter()`, then restore everything on `exit()`.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import carb.settings

from .tool_settings import UNSET, VIEWPORT_CONTEXT_MENU_SETTING


LOGGER = logging.getLogger(__name__)


class ViewportSuppression:
    def __init__(self) -> None:
        # (layer, prior_visibility) entries, restored in reverse on exit.
        self._hidden_layers: list[tuple[Any, bool]] = []
        # Prior value of the viewport context-menu setting, or UNSET.
        self._prev_context_menu: Any = UNSET

    # Disable viewport selection layers and the right-click context menu.
    def enter(self) -> None:
        # Look up the viewport window factory.
        try:
            from omni.kit.viewport.window import get_viewport_window_instances
        except ImportError as exc:
            LOGGER.warning("3D Draw: omni.kit.viewport.window unavailable: %s", exc)
        else:
            get_windows_any = cast(Any, get_viewport_window_instances)
            # Enumerate all open viewport windows.
            try:
                windows = list(get_windows_any())
            except Exception as exc:
                LOGGER.warning("3D Draw: could not enumerate viewport windows: %s", exc)
                windows = []

            # Viewport layers to hide while drawing.
            targets = [
                ("Selection", "manipulator"),
                ("ObjectClick", "manipulator"),
                ("ContextMenu", "manipulator"),
            ]
            # Hide each target layer per window and remember its prior visibility.
            for window in windows:
                find_layer = getattr(window, "_find_viewport_layer", None)
                if find_layer is None:
                    continue
                for layer_name, category in targets:
                    try:
                        layer = find_layer(layer_name, category)
                    except Exception:
                        layer = None
                    if layer is None:
                        continue
                    try:
                        prev_visible = bool(layer.visible)
                        layer.visible = False
                        self._hidden_layers.append((layer, prev_visible))
                    except Exception as exc:
                        LOGGER.warning(
                            "3D Draw: could not hide %s layer: %s", layer_name, exc
                        )

        # Override the viewport context-menu setting and remember the prior value.
        try:
            settings = cast(Any, carb.settings.get_settings())
            self._prev_context_menu = settings.get(VIEWPORT_CONTEXT_MENU_SETTING)
            settings.set_bool(VIEWPORT_CONTEXT_MENU_SETTING, False)
        except Exception as exc:
            LOGGER.warning("3D Draw: could not disable viewport context menu: %s", exc)
            self._prev_context_menu = UNSET

    # Restore viewport layers and context-menu setting to their prior state.
    def exit(self) -> None:
        # Re-show every layer we hid.
        for layer, prev_visible in self._hidden_layers:
            try:
                layer.visible = prev_visible
            except Exception:
                pass
        self._hidden_layers.clear()

        # Restore the previous context-menu setting (or remove our override).
        if self._prev_context_menu is not UNSET:
            try:
                settings = cast(Any, carb.settings.get_settings())
                if self._prev_context_menu is None:
                    # Drop the override so the kit default applies.
                    try:
                        settings.destroy_item(VIEWPORT_CONTEXT_MENU_SETTING)
                    except Exception:
                        settings.set_bool(VIEWPORT_CONTEXT_MENU_SETTING, True)
                else:
                    settings.set_bool(
                        VIEWPORT_CONTEXT_MENU_SETTING, bool(self._prev_context_menu)
                    )
            except Exception as exc:
                LOGGER.warning("3D Draw: could not restore context menu setting: %s", exc)
        self._prev_context_menu = UNSET
