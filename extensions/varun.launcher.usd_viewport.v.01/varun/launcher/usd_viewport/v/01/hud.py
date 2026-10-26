# Python & library imports
from typing import Any, Optional, cast

# Omniverse & USD imports
import carb.settings
from omni.kit.viewport.menubar.core import ViewportButtonItem

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

_VISIBLE_SETTING = "/exts/varun.launcher.usd_viewport.v.01/hud_toggle/visible"
_ORDER_SETTING = "/exts/varun.launcher.usd_viewport.v.01/hud_toggle/order"


# ----------------------------------------------------------------------
# 2. HUD TOGGLE BUTTON
# ----------------------------------------------------------------------


class HudToggleButton:
    # Register the HUD toggle beside the framerate menu.
    def __init__(self) -> None:
        settings = carb.settings.get_settings()
        settings.set_default(_VISIBLE_SETTING, True)
        # Framerate menu is at order 90; 85 sits just to its left.
        settings.set_default(_ORDER_SETTING, 85)

        self._button: Optional[ViewportButtonItem] = ViewportButtonItem(
            text="HUD",
            name="LauncherHUDToggle",
            onclick_fn=self._on_click,
            visible_setting_path=_VISIBLE_SETTING,
            order_setting_path=_ORDER_SETTING,
        )

    # Toggle every viewport HUD using the first viewport's current state.
    def _on_click(self) -> None:
        from omni.kit.viewport.window import get_viewport_window_instances

        # Collect IDs for every live viewport window.
        settings = carb.settings.get_settings()
        ids: list[str] = []
        for window in get_viewport_window_instances(None) or ():
            vp = getattr(cast(Any, window), "viewport_api", None)
            if vp is not None:
                ids.append(str(vp.id))
        if not ids:
            return

        # Choose one new HUD state and apply it to all collected IDs.
        first_key = f"/persistent/app/viewport/{ids[0]}/hud/visible"
        current = settings.get(first_key)
        new_value = not bool(current) if current is not None else True
        for vp_id in ids:
            settings.set(f"/persistent/app/viewport/{vp_id}/hud/visible", new_value)

    # Remove the HUD toggle button.
    def destroy(self) -> None:
        if self._button is not None:
            self._button.destroy()
            self._button = None
