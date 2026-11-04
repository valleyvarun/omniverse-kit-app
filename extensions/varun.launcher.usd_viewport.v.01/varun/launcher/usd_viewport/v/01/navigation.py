"""Mouse navigation modes and camera operations shared with the orientation control."""

import math
from typing import Any, Literal, TypeAlias, cast

import carb.input
import carb.settings
import omni.appwindow
import omni.kit.app
from omni.kit.viewport.menubar.core import ViewportButtonItem
from omni.kit.viewport.window import ViewportWindow
from omni.kit.widget.viewport.api import ViewportAPI
from omni.ui import scene as sc
from pxr import Gf, Sdf, Usd, UsdGeom

from .touch import LOCK_ATTRIBUTE, PIVOT_ATTRIBUTE, Point, ViewportCamera, _run_command

# Navigation is a session preference; new launches start in Orbit.
MODE_SETTING: str = "/exts/varun.launcher.usd_viewport.v.01/mouseNavigation"
ZOOM_SPEED_SETTING: str = "/persistent/exts/varun.launcher.usd_viewport.v.01/orbitZoomSpeed"
_VISIBLE_SETTING: str = "/exts/varun.launcher.usd_viewport.v.01/navigation_button/visible"
_ORDER_SETTING: str = "/exts/varun.launcher.usd_viewport.v.01/navigation_button/order"
_WHEEL_RATE: float = -math.log(0.88)
_SPEED_STEP: float = math.log(1.25)
_MIN_ZOOM_SPEED: float = 0.1
_MAX_ZOOM_SPEED: float = 10.0
NavigationMode: TypeAlias = Literal["Orbit", "Fly"]
Bindings: TypeAlias = dict[str, str]
_MODEL_VIEWPORTS: dict[int, ViewportAPI] = {}


def navigation_mode() -> NavigationMode:
    return "Fly" if carb.settings.get_settings().get(MODE_SETTING) == "Fly" else "Orbit"


def zoom_speed() -> float:
    """Read the persisted Orbit wheel multiplier, tolerating invalid saved values."""
    value: object = carb.settings.get_settings().get(ZOOM_SPEED_SETTING)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 1.0
    speed: float = float(value)
    return max(_MIN_ZOOM_SPEED, min(_MAX_ZOOM_SPEED, speed)) if math.isfinite(speed) else 1.0


def adjust_zoom_speed(amount: float) -> None:
    """Change speed continuously for fractional wheels and retain it across launches."""
    if not math.isfinite(amount) or amount == 0.0:
        return
    speed: float = math.exp(
        max(math.log(_MIN_ZOOM_SPEED), min(math.log(_MAX_ZOOM_SPEED), math.log(zoom_speed()) + amount * _SPEED_STEP))
    )
    carb.settings.get_settings().set(ZOOM_SPEED_SETTING, max(_MIN_ZOOM_SPEED, min(_MAX_ZOOM_SPEED, speed)))


def _shift_pressed() -> bool:
    # ScrollGesture.modifiers is a binding filter, not the current key state.
    input_interface: carb.input.IInput = carb.input.acquire_input_interface()
    app_window: omni.appwindow.IAppWindow | None = omni.appwindow.get_default_app_window()
    if app_window is None:
        return False
    keyboard: carb.input.Keyboard = app_window.get_keyboard()
    modifiers: int = input_interface.get_modifier_flags(
        modifiers=carb.input.KEYBOARD_MODIFIER_FLAG_SHIFT, input_devices=[keyboard]
    )
    return bool(modifiers & carb.input.KEYBOARD_MODIFIER_FLAG_SHIFT)


# Camera changes reuse touch navigation without altering its gesture handling.
class NavigationCamera(ViewportCamera):
    def align_axis(self, axis: int, sign: int) -> None:
        """Center the world origin from one of the six world-axis directions."""
        if axis not in (0, 1, 2) or sign not in (-1, 1):
            raise ValueError("An axis view requires axis 0, 1, or 2 and sign -1 or 1")
        backward: Gf.Vec3d = Gf.Vec3d(0.0)
        backward[axis] = float(sign)
        z_up: bool = UsdGeom.GetStageUpAxis(self._viewport.stage) == UsdGeom.Tokens.z
        up: Gf.Vec3d = Gf.Vec3d(0.0, 0.0, 1.0) if z_up else Gf.Vec3d(0.0, 1.0, 0.0)
        if abs(Gf.Dot(up, backward)) > 0.999:
            up = Gf.Vec3d(0.0, 1.0, 0.0) if z_up else Gf.Vec3d(0.0, 0.0, -1.0)
        distance: float = self._offset.GetLength()
        # Axis clicks remove the displaced orbit pivot left by panning or flying.
        target: Gf.Vec3d = Gf.Vec3d(0.0)
        eye: Gf.Vec3d = target + backward * distance
        world: Gf.Matrix4d = Gf.Matrix4d().SetLookAt(eye, target, up).GetInverse()
        self._move(world)
        _run_command(
            "ChangePropertyCommand",
            prop_path=self._camera.GetPath().AppendProperty(PIVOT_ATTRIBUTE),
            value=Gf.Vec3d(0.0, 0.0, -distance),
            prev=self._offset,
            type_to_create_if_not_exist=Sdf.ValueTypeNames.Vector3d,
            usd_context_name=self._viewport.usd_context_name,
        )

    def orbit_from_gizmo(self, dx: float, dy: float) -> None:
        """Allow free rotation after dragging an orthographic orientation view."""
        if self._orthographic:
            _run_command(
                "ChangePropertyCommand",
                prop_path=self._camera.GetProjectionAttr().GetPath(),
                value=UsdGeom.Tokens.perspective,
                prev=UsdGeom.Tokens.orthographic,
                usd_context_name=self._viewport.usd_context_name,
            )
            self._orthographic = False
        self.orbit(dx, dy)


def find_camera(viewport: ViewportAPI | None) -> NavigationCamera | None:
    """Return an editable camera, respecting Kit's camera lock."""
    stage: Usd.Stage | None = viewport.stage if viewport is not None else None
    if viewport is None or stage is None:
        return None
    prim: Usd.Prim = stage.GetPrimAtPath(viewport.camera_path)
    if not prim or not prim.IsA(UsdGeom.Camera):
        return None
    lock: Usd.Attribute = prim.GetAttribute(LOCK_ATTRIBUTE)
    if lock and lock.Get():
        return None
    return NavigationCamera(viewport, UsdGeom.Camera(prim))


# The native camera Screen owns these gestures, so other UI retains mouse priority.
class OrbitDragGesture(sc.DragGesture):
    def __init__(
        self,
        viewport: ViewportAPI | None = None,
        model: Any = None,
        configure_model: Any = None,
        flight_mode: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._viewport: ViewportAPI = viewport if viewport is not None else _MODEL_VIEWPORTS[id(model)]
        self._previous: Point | None = None
        self.check_mouse_moved = True

    def _mouse(self) -> Point:
        payload: Any = self.sender.gesture_payload
        return float(payload.mouse[0]), float(payload.mouse[1])

    def on_began(self) -> None:
        self._previous = self._mouse()

    def on_changed(self) -> None:
        current: Point = self._mouse()
        previous: Point | None = self._previous
        self._previous = current
        camera: NavigationCamera | None = find_camera(self._viewport)
        if previous is not None and camera is not None:
            camera.orbit(current[0] - previous[0], current[1] - previous[1])

    def on_ended(self) -> None:
        self._previous = None


class CursorZoomGesture(sc.ScrollGesture):
    def __init__(
        self,
        viewport: ViewportAPI | None = None,
        model: Any = None,
        configure_model: Any = None,
        flight_mode: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._viewport: ViewportAPI = viewport if viewport is not None else _MODEL_VIEWPORTS[id(model)]

    def on_ended(self) -> None:
        scroll: Any = self.scroll
        amount: float = float(scroll[1])
        if not math.isfinite(amount) or amount == 0.0:
            return
        if _shift_pressed():
            adjust_zoom_speed(amount)
            return
        camera: NavigationCamera | None = find_camera(self._viewport)
        if camera is None:
            return
        payload: Any = self.sender.gesture_payload
        cursor: Point = float(payload.mouse[0]), float(payload.mouse[1])
        factor: float = math.exp(max(-2.0, min(2.0, -amount * _WHEEL_RATE * zoom_speed())))
        camera.zoom(cursor, factor)


# Replace only mouse look, flight, and wheel bindings in this viewport.
class MouseNavigation:
    def __init__(self, window: ViewportWindow) -> None:
        self._window: ViewportWindow = window
        self._manipulator: Any = None
        self._original_bindings: Bindings | str | None = None
        self._applied_mode: NavigationMode | None = None
        self._model_key: int | None = None
        self._bindings_path: str = f"{MODE_SETTING}Bindings/{id(self)}"
        carb.settings.get_settings().set_default(MODE_SETTING, "Orbit")
        carb.settings.get_settings().set_default(ZOOM_SPEED_SETTING, 1.0)
        self._mode_sub: Any = omni.kit.app.SettingChangeSubscription(MODE_SETTING, self._refresh)
        self._update_sub: Any = (
            cast(Any, omni.kit.app.get_app())
            .get_update_event_stream()
            .create_subscription_to_pop(self._refresh, name="USD viewport mouse navigation")
        )
        self._refresh()

    def _refresh(self, *_: Any) -> None:
        item: Any = self._window._find_viewport_layer("Camera", "manipulator")
        layer: Any = getattr(item, "layer", None)
        manipulator: Any = getattr(layer, "manipulator", None)
        if manipulator is None:
            return
        if manipulator is not self._manipulator:
            if self._model_key is not None:
                _MODEL_VIEWPORTS.pop(self._model_key, None)
            self._manipulator = manipulator
            self._original_bindings = manipulator.bindings
            self._applied_mode = None
            self._model_key = id(manipulator.model)
            viewport: ViewportAPI | None = self._window.viewport_api
            if viewport is None:
                return
            _MODEL_VIEWPORTS[self._model_key] = viewport
        mode: NavigationMode = navigation_mode()
        if mode == self._applied_mode:
            return
        if mode == "Fly":
            self._replace_bindings(self._original_bindings)
        else:
            original: Any = self._original_bindings
            if isinstance(original, str):
                original = carb.settings.get_settings().get(original)
            if original is None:
                original = manipulator.get_default_bindings()
            bindings: Bindings = dict(original)
            name: str
            for name in ("LookGesture", "FlightMode", "FlightSpeedGesture", "ZoomScrollGesture"):
                bindings.pop(name, None)
            bindings[f"{__name__}.OrbitDragGesture"] = "RightButton"
            bindings[f"{__name__}.CursorZoomGesture"] = "Any"
            self._replace_bindings(bindings)
        self._applied_mode = mode

    def _replace_bindings(self, bindings: Bindings | str | None) -> None:
        # Use setting paths: Kit 109's string-to-dict setter has an unsubscribe bug.
        if isinstance(bindings, str):
            self._manipulator.bindings = bindings
            return
        carb.settings.get_settings().set(
            self._bindings_path, bindings if bindings is not None else self._manipulator.get_default_bindings()
        )
        self._manipulator.bindings = self._bindings_path

    def destroy(self) -> None:
        self._mode_sub = None
        self._update_sub = None
        if self._manipulator is not None:
            self._replace_bindings(self._original_bindings)
        if self._model_key is not None:
            _MODEL_VIEWPORTS.pop(self._model_key, None)
        self._model_key = None
        if isinstance(self._original_bindings, str):
            carb.settings.get_settings().destroy_item(self._bindings_path)
        self._manipulator = None
        self._original_bindings = None


class NavigationButton(ViewportButtonItem):
    def __init__(self) -> None:
        settings: carb.settings.ISettings = carb.settings.get_settings()
        settings.set_default(MODE_SETTING, "Orbit")
        settings.set_default(ZOOM_SPEED_SETTING, 1.0)
        settings.set_default(_VISIBLE_SETTING, True)
        settings.set_default(_ORDER_SETTING, 70)
        super().__init__(
            text=f"Mouse: {navigation_mode()}",
            name="LauncherMouseNavigation",
            onclick_fn=self._toggle,
            visible_setting_path=_VISIBLE_SETTING,
            order_setting_path=_ORDER_SETTING,
        )
        self._mode_sub: Any = omni.kit.app.SettingChangeSubscription(MODE_SETTING, self._refresh)
        self._speed_sub: omni.kit.app.SettingChangeSubscription | None = omni.kit.app.SettingChangeSubscription(
            ZOOM_SPEED_SETTING, self._refresh
        )

    def build_fn(self, factory: dict[Any, Any]) -> None:
        super().build_fn(factory)
        self._refresh()

    @staticmethod
    def _toggle() -> None:
        carb.settings.get_settings().set(MODE_SETTING, "Fly" if navigation_mode() == "Orbit" else "Orbit")

    def _refresh(self, *_: Any) -> None:
        mode: NavigationMode = navigation_mode()
        speed: float = zoom_speed()
        if self.button is not None:
            self.button.text = f"Mouse: Orbit ({speed:.3g}x)" if mode == "Orbit" else "Mouse: Fly"
            self.button.tooltip = (
                f"Orbit: right-drag to orbit; wheel zooms at the cursor ({speed:.3g}x). "
                "Shift+wheel forward/backward increases/decreases zoom speed. Click for Fly."
                if mode == "Orbit"
                else "Fly: right-drag to look; WASDQE to move. Click for Orbit."
            )

    def destroy(self) -> None:
        self._mode_sub = None
        self._speed_sub = None
        super().destroy()
