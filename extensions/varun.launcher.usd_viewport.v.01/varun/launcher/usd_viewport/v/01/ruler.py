# Python & library imports
from functools import partial
from pathlib import Path
from typing import Any

# Omniverse & USD imports
import carb.settings
import omni.kit.app
from omni import ui
from omni.kit.viewport.menubar.core import ViewportButtonItem, ViewportMenuDelegate

# Local imports
from .viewport_light.settings import (
    DEFAULT_UNIT,
    RULER,
    UNIT_METERS,
    UNITS,
)

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

# Menubar placement: HUD sits at order 85, then Units at 80, then the ruler toggle at 75.
_RULER_VISIBLE: str = "/exts/varun.launcher.usd_viewport.v.01/ruler_button/visible"
_RULER_ORDER: str = "/exts/varun.launcher.usd_viewport.v.01/ruler_button/order"
_UNITS_VISIBLE: str = "/exts/varun.launcher.usd_viewport.v.01/units_button/visible"
_UNITS_ORDER: str = "/exts/varun.launcher.usd_viewport.v.01/units_button/order"

# Keep the ruler icon white in both button states.
_ICON: Path = Path(__file__).resolve().parents[5] / "data" / "icons" / "ruler.svg"
_ICON_COLOR: tuple[float, float, float] = (1.0, 1.0, 1.0)

# menu text for each unit symbol
_LABELS: dict[str, str] = {"m": "Meters (m)", "mm": "Millimeters (mm)"}


# Return whether the 3D ruler is shown; it is on unless switched off.
def ruler_visible() -> bool:
    return carb.settings.get_settings().get(RULER) is not False


# Return the selected unit symbol; anything unknown falls back to metres.
def selected_unit() -> str:
    value: Any = carb.settings.get_settings().get(UNITS)
    return value if value in UNIT_METERS else DEFAULT_UNIT


# ----------------------------------------------------------------------
# 2. RULER BUTTON
# ----------------------------------------------------------------------


class RulerButton(ViewportButtonItem):
    # Register the ruler toggle beside the Units button; the 3D ruler starts on.
    def __init__(self) -> None:
        settings: Any = carb.settings.get_settings()
        settings.set_default(RULER, True)
        settings.set_default(_RULER_VISIBLE, True)
        settings.set_default(_RULER_ORDER, 75)

        super().__init__(
            name="LauncherRuler",
            onclick_fn=self._toggle,
            visible_setting_path=_RULER_VISIBLE,
            order_setting_path=_RULER_ORDER,
            style={
                "Menu.Button.Image": {"image_url": str(_ICON), "color": ui.color(*_ICON_COLOR)},
                "Menu.Button.Image:selected": {"color": ui.color(*_ICON_COLOR)},
            },
        )
        self.selected = ruler_visible()
        self._ruler_sub: Any = omni.kit.app.SettingChangeSubscription(RULER, self._refresh)

    # Rebuilt buttons keep their tooltip.
    def build_fn(self, factory: dict[Any, Any]) -> None:
        super().build_fn(factory)
        if self.button is not None:
            self.button.tooltip = "3D ruler"

    # Show or hide the 3D ruler.
    @staticmethod
    def _toggle() -> None:
        carb.settings.get_settings().set(RULER, not ruler_visible())

    # Reflect ruler visibility in the button's selection state.
    def _refresh(self, *_: Any) -> None:
        self.selected = ruler_visible()

    # Stop following the setting and remove the button.
    def destroy(self) -> None:
        self._ruler_sub = None
        super().destroy()


# ----------------------------------------------------------------------
# 3. UNITS BUTTON
# ----------------------------------------------------------------------


class UnitsButton(ViewportButtonItem):
    # Register the Units button beside the HUD toggle and follow the unit setting.
    def __init__(self) -> None:
        settings: Any = carb.settings.get_settings()
        settings.set_default(UNITS, DEFAULT_UNIT)
        settings.set_default(_UNITS_VISIBLE, True)
        settings.set_default(_UNITS_ORDER, 80)

        self._items: dict[str, ui.MenuItem] = {}
        super().__init__(
            text=f"Units: {selected_unit()}",
            name="LauncherUnits",
            onclick_fn=self.on_right_clicked_fn,
            build_menu_fn=self._build_unit_items,
            visible_setting_path=_UNITS_VISIBLE,
            order_setting_path=_UNITS_ORDER,
            has_triangle=True,
        )
        self._units_sub: Any = omni.kit.app.SettingChangeSubscription(UNITS, self._refresh)

    # Rebuilt buttons always show the current unit.
    def build_fn(self, factory: dict[Any, Any]) -> None:
        super().build_fn(factory)
        self._refresh()

    # One checkable entry per unit; left or right click opens this menu. Kit types the callback with one unused argument.
    def _build_unit_items(self, _unused: None = None) -> list[ui.MenuItem]:
        self._items = {}
        unit: str
        for unit in UNIT_METERS:
            self._items[unit] = ui.MenuItem(
                _LABELS[unit],
                checkable=True,
                checked=unit == selected_unit(),
                triggered_fn=partial(self._select, unit),
                delegate=ViewportMenuDelegate(),
            )
        return list(self._items.values())

    # Store the chosen unit; the grid numbers follow the setting.
    def _select(self, unit: str) -> None:
        carb.settings.get_settings().set(UNITS, unit)
        # Re-tick even when the unit did not change, since clicking toggles the item.
        self._refresh()

    # Show the unit on the button and tick it in the menu.
    def _refresh(self, *_: Any) -> None:
        unit: str = selected_unit()
        if self.button is not None:
            self.button.text = f"Units: {unit}"
        name: str
        item: ui.MenuItem
        for name, item in self._items.items():
            item.checked = name == unit

    # Stop following the setting and remove the button.
    def destroy(self) -> None:
        self._units_sub = None
        self._items = {}
        super().destroy()
