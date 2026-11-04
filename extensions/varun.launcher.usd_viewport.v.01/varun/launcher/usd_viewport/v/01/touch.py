# Python & library imports
import ctypes
import logging
import math
import sys
from ctypes import wintypes
from typing import Any, Literal, TypeAlias, cast

# Omniverse & USD imports
import carb.windowing
import omni.appwindow
import omni.kit.app
import omni.kit.commands
from omni import ui
from omni.kit.viewport.window import ViewportWindow
from omni.kit.widget.viewport.api import ViewportAPI
from pxr import Gf, Sdf, Usd, UsdGeom

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

LOGGER: logging.Logger = logging.getLogger(__name__)

# (x, y) in main-window pixels for fingers, or in NDC for viewport positions
Point: TypeAlias = tuple[float, float]
Gesture: TypeAlias = Literal["", "zoom", "orbit", "pan", "ended"]

# Windows pointer messages and the touch pointer type
WM_POINTERUPDATE: int = 0x0245
WM_POINTERDOWN: int = 0x0246
WM_POINTERUP: int = 0x0247
WM_POINTERCAPTURECHANGED: int = 0x024C
PT_TOUCH: int = 2

# Kit's camera attributes: the orbit pivot (a camera-space offset) and the navigation lock
PIVOT_ATTRIBUTE: str = "omni:kit:centerOfInterest"
LOCK_ATTRIBUTE: str = "omni:kit:cameraLock"
DEFAULT_PIVOT_DISTANCE: float = 100.0

# two fingers must move this far (in points) before zoom or orbit is chosen
GESTURE_THRESHOLD: float = 10.0

# Kit's mouse tumble rate: dragging across the whole viewport turns 180 degrees
ORBIT_DEGREES: float = 90.0


# ----------------------------------------------------------------------
# 2. HELPERS
# ----------------------------------------------------------------------


# Average finger positions.
def _center(points: list[Point]) -> Point:
    count: int = len(points)
    return sum(point[0] for point in points) / count, sum(point[1] for point in points) / count


# Run a Kit command without undo; like Kit's mouse navigation, edits use the default time (no keys).
def _run_command(name: str, **kwargs: Any) -> None:
    command: omni.kit.commands.Command | None = omni.kit.commands.create(name, **kwargs)
    if command is not None:
        command.do()


# ----------------------------------------------------------------------
# 3. VIEWPORT CAMERA
# ----------------------------------------------------------------------


class ViewportCamera:
    # Read the camera's transform, view direction, and orbit pivot for one frame.
    def __init__(self, viewport: ViewportAPI, camera: UsdGeom.Camera) -> None:
        self._viewport: ViewportAPI = viewport
        self._camera: UsdGeom.Camera = camera
        self._time: Usd.TimeCode = viewport.time
        self._world: Gf.Matrix4d = camera.ComputeLocalToWorldTransform(self._time)
        self._forward: Gf.Vec3d = self._world.TransformDir(Gf.Vec3d(0.0, 0.0, -1.0)).GetNormalized()
        self._orthographic: bool = camera.GetProjectionAttr().Get(self._time) == UsdGeom.Tokens.orthographic

        # Kit keeps the pivot as a camera-space offset; fall back like Kit when it is unset.
        attribute: Usd.Attribute = camera.GetPrim().GetAttribute(PIVOT_ATTRIBUTE)
        offset: Any = attribute.Get() if attribute else None
        if offset is None or not 1e-6 < offset.GetLength() < math.inf:
            focus: float = float(camera.GetFocusDistanceAttr().Get(self._time) or 0.0)
            offset = Gf.Vec3d(0.0, 0.0, -(focus if focus > 0.0 else DEFAULT_PIVOT_DISTANCE))
        self._offset: Gf.Vec3d = Gf.Vec3d(offset)
        self._pivot: Gf.Vec3d = self._world.Transform(self._offset)

    # Zoom toward an NDC point; the scene under the fingers stays in place.
    def zoom(self, ndc: Point, factor: float) -> None:
        anchor: Gf.Vec3d | None = self._plane_point(ndc)
        if anchor is None:
            return

        # Orthographic views shrink their visible area and slide toward the anchor.
        if self._orthographic:
            self._move(self._world * Gf.Matrix4d().SetTranslate((anchor - self._pivot) * (1.0 - factor)))
            attribute: Usd.Attribute
            for attribute in (self._camera.GetHorizontalApertureAttr(), self._camera.GetVerticalApertureAttr()):
                aperture: float = float(attribute.Get(self._time))
                _run_command(
                    "ChangePropertyCommand",
                    prop_path=attribute.GetPath(),
                    value=aperture * factor,
                    prev=aperture,
                    usd_context_name=self._viewport.usd_context_name,
                )
            return

        # Perspective views move toward the anchor and bring the pivot closer by the same factor.
        eye: Gf.Vec3d = self._world.ExtractTranslation()
        self._move(self._world * Gf.Matrix4d().SetTranslate((anchor - eye) * (1.0 - factor)))
        _run_command(
            "ChangePropertyCommand",
            prop_path=self._camera.GetPath().AppendProperty(PIVOT_ATTRIBUTE),
            value=self._offset * factor,
            prev=self._offset,
            type_to_create_if_not_exist=Sdf.ValueTypeNames.Vector3d,
            usd_context_name=self._viewport.usd_context_name,
        )

    # Orbit around the pivot: sideways about the stage up axis, up and down about the camera's right axis.
    def orbit(self, dx: float, dy: float) -> None:
        # Orthographic views stay aligned, like Kit's mouse tumble.
        if self._orthographic:
            return
        stage_up: Any = UsdGeom.GetStageUpAxis(self._viewport.stage)
        up: Gf.Vec3d = Gf.Vec3d(0.0, 0.0, 1.0) if stage_up == UsdGeom.Tokens.z else Gf.Vec3d(0.0, 1.0, 0.0)
        right: Gf.Vec3d = self._world.TransformDir(Gf.Vec3d(1.0, 0.0, 0.0)).GetNormalized()

        # The scene follows the fingers; tilting stops just short of straight up or down.
        turn: float = -ORBIT_DEGREES * dx
        elevation: float = math.degrees(math.acos(max(-1.0, min(1.0, Gf.Dot(self._forward, up)))))
        tilt: float = max(elevation - 179.0, min(elevation - 1.0, ORBIT_DEGREES * dy))

        # Rotate the whole camera rigidly around the pivot.
        to_pivot: Gf.Matrix4d = Gf.Matrix4d().SetTranslate(self._pivot)
        tilting: Gf.Matrix4d = Gf.Matrix4d().SetRotate(Gf.Rotation(right, tilt))
        turning: Gf.Matrix4d = Gf.Matrix4d().SetRotate(Gf.Rotation(up, turn))
        self._move(self._world * to_pivot.GetInverse() * tilting * turning * to_pivot)

    # Pan so the scene point under the fingers follows them.
    def pan(self, start: Point, end: Point) -> None:
        grabbed: Gf.Vec3d | None = self._plane_point(start)
        target: Gf.Vec3d | None = self._plane_point(end)
        if grabbed is not None and target is not None:
            self._move(self._world * Gf.Matrix4d().SetTranslate(grabbed - target))

    # Find the scene point under an NDC position, on the plane through the pivot facing the camera.
    def _plane_point(self, ndc: Point) -> Gf.Vec3d | None:
        ndc_to_world: Gf.Matrix4d = (self._world.GetInverse() * self._viewport.projection).GetInverse()
        near: Gf.Vec3d = ndc_to_world.Transform(Gf.Vec3d(ndc[0], ndc[1], 0.0))
        direction: Gf.Vec3d = ndc_to_world.Transform(Gf.Vec3d(ndc[0], ndc[1], 0.5)) - near
        facing: float = Gf.Dot(direction, self._forward)
        if abs(facing) < 1e-12:
            return None
        return near + direction * (Gf.Dot(self._pivot - near, self._forward) / facing)

    # Write a new world transform in the parent's space; Kit's command keeps session cameras in the session layer.
    def _move(self, world: Gf.Matrix4d) -> None:
        parent: Gf.Matrix4d = self._camera.ComputeParentToWorldTransform(self._time)
        _run_command(
            "TransformPrimCommand",
            path=self._camera.GetPath(),
            new_transform_matrix=world * parent.GetInverse(),
            usd_context_name=self._viewport.usd_context_name,
        )


# ----------------------------------------------------------------------
# 4. TOUCH NAVIGATION
# ----------------------------------------------------------------------


class TouchNavigation:
    # Record fingers from the window hook and move the camera once per frame.
    def __init__(self, window: ViewportWindow) -> None:

        # --- 1) PREPARE TOUCH STATE ---

        self._window: ViewportWindow = window
        # live finger positions in main-window pixels, written by the window hook
        self._contacts: dict[int, Point] = {}
        # finger positions at the last frame, and where the current fingers landed
        self._previous: dict[int, Point] = {}
        self._start: dict[int, Point] = {}
        self._gesture: Gesture = ""
        # viewport (left, top, width, height) in main-window pixels; None while hidden
        self._viewport_rect: tuple[float, float, float, float] | None = None
        self._hwnd: int | None = None
        self._callback: Any = None
        self._user32: Any = None
        self._comctl32: Any = None

        # --- 2) HOOK THE MAIN WINDOW'S TOUCH INPUT ---

        if sys.platform == "win32":
            self._install_hook()

        # --- 3) MOVE THE CAMERA ONCE PER FRAME ---

        self._update_sub: Any = (
            cast(Any, omni.kit.app.get_app())
            .get_update_event_stream()
            .create_subscription_to_pop(self._on_update, name="varun.launcher.usd_viewport touch navigation")
        )

    # Subclass the main window so touch messages reach this class before Kit.
    def _install_hook(self) -> None:
        try:
            # Read the native window handle from Kit's windowing capsule.
            native: Any = carb.windowing.acquire_windowing_interface().get_native_window(
                omni.appwindow.get_default_app_window().get_window()
            )
            capsule_name: Any = ctypes.pythonapi.PyCapsule_GetName
            capsule_name.argtypes = [ctypes.py_object]
            capsule_name.restype = ctypes.c_char_p
            capsule_pointer: Any = ctypes.pythonapi.PyCapsule_GetPointer
            capsule_pointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
            capsule_pointer.restype = ctypes.c_void_p
            hwnd: int = int(capsule_pointer(native, capsule_name(native)))

            # Declare the Win32 functions the hook uses.
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._user32.GetPointerType.argtypes = [wintypes.UINT, ctypes.POINTER(wintypes.UINT)]
            self._user32.GetPointerType.restype = wintypes.BOOL
            self._user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
            self._user32.ScreenToClient.restype = wintypes.BOOL
            self._comctl32 = ctypes.WinDLL("comctl32", use_last_error=True)
            callback_type: Any = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t,
                wintypes.HWND,
                wintypes.UINT,
                ctypes.c_size_t,
                ctypes.c_ssize_t,
                ctypes.c_size_t,
                ctypes.c_size_t,
            )
            self._comctl32.SetWindowSubclass.argtypes = [wintypes.HWND, callback_type, ctypes.c_size_t, ctypes.c_size_t]
            self._comctl32.SetWindowSubclass.restype = wintypes.BOOL
            self._comctl32.RemoveWindowSubclass.argtypes = [wintypes.HWND, callback_type, ctypes.c_size_t]
            self._comctl32.RemoveWindowSubclass.restype = wintypes.BOOL
            self._comctl32.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
            self._comctl32.DefSubclassProc.restype = ctypes.c_ssize_t

            # Install the hook; the callback object must stay alive while it is installed.
            self._callback = callback_type(self._message)
            if not self._comctl32.SetWindowSubclass(hwnd, self._callback, id(self), 0):
                raise ctypes.WinError(ctypes.get_last_error())
            self._hwnd = hwnd
        except (OSError, TypeError, ValueError, AttributeError):
            LOGGER.exception("Touch navigation could not hook the main window")

    # Track fingers that land on the viewport; their messages never reach Kit as mouse input.
    def _message(self, hwnd: int, message: int, wparam: int, lparam: int, subclass: int, data: int) -> int:
        try:
            pointer: int = wparam & 0xFFFF
            if message in (WM_POINTERDOWN, WM_POINTERUPDATE, WM_POINTERUP) and self._is_touch(pointer):
                position: Point = self._client_position(hwnd, lparam)
                ndc: Point | None = self._to_ndc(position)

                # A finger joins only when it lands inside the visible viewport.
                if message == WM_POINTERDOWN and ndc is not None and abs(ndc[0]) <= 1.0 and abs(ndc[1]) <= 1.0:
                    self._contacts[pointer] = position

                # Consume every message from a tracked finger.
                if pointer in self._contacts:
                    if message == WM_POINTERUP:
                        del self._contacts[pointer]
                    else:
                        self._contacts[pointer] = position
                    return 0
            elif message == WM_POINTERCAPTURECHANGED:
                # A cancelled touch ends like a lifted finger.
                self._contacts.pop(pointer, None)
        except Exception:
            LOGGER.exception("Could not read touch input")
        return int(self._comctl32.DefSubclassProc(hwnd, message, wparam, lparam))

    # Check whether a pointer comes from a finger rather than a pen or mouse.
    def _is_touch(self, pointer: int) -> bool:
        pointer_type: wintypes.UINT = wintypes.UINT()
        found: bool = bool(self._user32.GetPointerType(pointer, ctypes.byref(pointer_type)))
        return found and pointer_type.value == PT_TOUCH

    # Convert a message's screen position (two signed 16-bit values) to main-window pixels.
    def _client_position(self, hwnd: int, lparam: int) -> Point:
        point: wintypes.POINT = wintypes.POINT(ctypes.c_short(lparam).value, ctypes.c_short(lparam >> 16).value)
        self._user32.ScreenToClient(hwnd, ctypes.byref(point))
        return float(point.x), float(point.y)

    # Convert main-window pixels to viewport NDC, where the viewport spans -1 to 1 with y up.
    def _to_ndc(self, position: Point) -> Point | None:
        rect: tuple[float, float, float, float] | None = self._viewport_rect
        if rect is None or rect[2] <= 0.0 or rect[3] <= 0.0:
            return None
        return 2.0 * (position[0] - rect[0]) / rect[2] - 1.0, 1.0 - 2.0 * (position[1] - rect[1]) / rect[3]

    # Turn this frame's finger movement into one camera move.
    def _on_update(self, _event: Any) -> None:

        # --- 1) CACHE THE VIEWPORT RECTANGLE ---

        # The window hook reads this copy instead of calling omni.ui itself.
        scale: float = ui.Workspace.get_dpi_scale()
        frame: ui.Frame = self._window.frame
        self._viewport_rect = (
            (
                frame.screen_position_x * scale,
                frame.screen_position_y * scale,
                frame.computed_width * scale,
                frame.computed_height * scale,
            )
            if self._window.visible
            else None
        )

        # --- 2) FOLLOW THE FINGER SET ---

        contacts: dict[int, Point] = dict(self._contacts)
        previous: dict[int, Point] = self._previous
        self._previous = contacts
        if contacts.keys() != previous.keys():
            # A new finger starts a fresh gesture; lifting one ends it until every finger is up.
            self._start = contacts
            self._gesture = "" if len(contacts) > len(previous) else "ended"
            return
        if self._gesture == "ended" or len(contacts) not in (1, 2):
            return

        # --- 3) CHOOSE THE GESTURE ---

        ids: list[int] = sorted(contacts)
        now: list[Point] = [contacts[pointer] for pointer in ids]
        before: list[Point] = [previous[pointer] for pointer in ids]
        if len(ids) == 1:
            # One finger pans.
            self._gesture = "pan"
        elif not self._gesture:
            # Two fingers zoom when their spread changes most, otherwise they orbit.
            start: list[Point] = [self._start[pointer] for pointer in ids]
            stretch: float = abs(math.dist(now[0], now[1]) - math.dist(start[0], start[1]))
            travel: float = math.dist(_center(now), _center(start))
            if max(stretch, travel) < GESTURE_THRESHOLD * scale:
                return
            self._gesture = "zoom" if stretch > travel else "orbit"

        # --- 4) MOVE THE CAMERA ---

        center: Point | None = self._to_ndc(_center(now))
        last_center: Point | None = self._to_ndc(_center(before))
        camera: ViewportCamera | None = self._find_camera()
        if center is None or last_center is None or camera is None:
            return
        if self._gesture == "zoom":
            # Spreading the fingers apart shrinks the distance to the pinch point.
            camera.zoom(center, math.dist(before[0], before[1]) / max(math.dist(now[0], now[1]), 1.0))
        elif self._gesture == "orbit":
            camera.orbit(center[0] - last_center[0], center[1] - last_center[1])
        else:
            camera.pan(last_center, center)

    # Return the viewport camera, or None when there is no stage or the camera is locked.
    def _find_camera(self) -> ViewportCamera | None:
        viewport: ViewportAPI | None = self._window.viewport_api
        stage: Usd.Stage | None = viewport.stage if viewport is not None else None
        if viewport is None or stage is None:
            return None
        prim: Usd.Prim = stage.GetPrimAtPath(viewport.camera_path)
        if not prim or not prim.IsA(UsdGeom.Camera):
            return None

        # Locked cameras ignore navigation, like Kit's mouse controls.
        lock: Usd.Attribute = prim.GetAttribute(LOCK_ATTRIBUTE)
        if lock and lock.Get():
            return None
        return ViewportCamera(viewport, UsdGeom.Camera(prim))

    # Remove the window hook and stop frame updates.
    def destroy(self) -> None:
        self._update_sub = None
        if self._hwnd is not None:
            self._comctl32.RemoveWindowSubclass(self._hwnd, self._callback, id(self))
            self._hwnd = None
        self._callback = None
        self._contacts.clear()
