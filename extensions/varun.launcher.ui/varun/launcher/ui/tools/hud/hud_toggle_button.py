"""Viewport menubar button that toggles the per-viewport stats HUD.

Registers a small text button to the left of the FrameRate ("Pacing Speed")
menu. Clicking it flips the persistent ``hud/visible`` setting for every
available viewport so the FPS / GPU / memory / resolution overlay shows or
hides on demand.
"""

from __future__ import annotations

from typing import Any, List, Optional, cast

import carb.settings

from omni.kit.viewport.menubar.core import ViewportButtonItem


_EXT_SETTINGS_ROOT = "/exts/varun.launcher.ui/hud_toggle"
_VISIBLE_SETTING = f"{_EXT_SETTINGS_ROOT}/visible"
_ORDER_SETTING = f"{_EXT_SETTINGS_ROOT}/order"


def _viewport_hud_setting(viewport_api_id: str) -> str:
    return f"/persistent/app/viewport/{viewport_api_id}/hud/visible"


def _iter_viewport_ids() -> List[str]:
    try:
        from omni.kit.viewport.utility import get_active_viewport_and_window  # type: ignore[reportUnknownVariableType]
        from omni.kit.viewport.window import get_viewport_window_instances
    except ImportError:
        return []

    ids: List[str] = []
    try:
        for window in get_viewport_window_instances() or ():  # type: ignore[reportUnknownVariableType]
            vp = getattr(cast(Any, window), "viewport_api", None)
            if vp is not None:
                ids.append(str(vp.id))
    except Exception:
        pass

    if not ids:
        try:
            vp, _ = cast(Any, get_active_viewport_and_window)()
            if vp is not None:
                ids.append(str(vp.id))
        except Exception:
            pass

    return ids


class HudToggleButton:
    """Registers a viewport menubar button that toggles the HUD overlay."""

    def __init__(self) -> None:
        settings = carb.settings.get_settings()
        settings.set_default(_VISIBLE_SETTING, True)
        # Framerate (Pacing Speed) menu is at order 90 on the right-hand cluster.
        # Use a slightly smaller value so this button sits just to its left.
        settings.set_default(_ORDER_SETTING, 85)

        self._button: Optional[ViewportButtonItem] = ViewportButtonItem(
            text="HUD",
            name="LauncherHUDToggle",
            onclick_fn=self._on_click,
            visible_setting_path=_VISIBLE_SETTING,
            order_setting_path=_ORDER_SETTING,
        )

    def _on_click(self) -> None:
        settings = carb.settings.get_settings()
        ids = _iter_viewport_ids()
        if not ids:
            return

        # Use the first viewport's current state to determine the new value
        # so all viewports are toggled together in lock-step.
        first_key = _viewport_hud_setting(ids[0])
        current = settings.get(first_key)
        new_value = not bool(current) if current is not None else True

        for vp_id in ids:
            settings.set(_viewport_hud_setting(vp_id), new_value)

    def destroy(self) -> None:
        if self._button is not None:
            self._button.destroy()
            self._button = None
