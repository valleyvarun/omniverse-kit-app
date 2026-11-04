"""Interactive screen-space orientation axes for the Light viewport."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeAlias, cast

import carb.settings
import omni.kit.app
from omni import ui
from omni.kit.viewport.window import ViewportWindow
from omni.kit.widget.viewport.api import ViewportAPI
from omni.ui import scene as sc
from pxr import Gf

from .axis_colors import AXIS_COLORS
from .navigation import NavigationCamera, find_camera

Point: TypeAlias = tuple[float, float]
Color: TypeAlias = tuple[float, float, float, float]

SIZE: float = 136.0
_LENGTH: float = 0.63
_RADIUS: float = 0.17
_DRAG_THRESHOLD: float = 4.0


@dataclass(frozen=True)
class AxisHandle:
    """One projected world-axis endpoint, ordered from back to front."""

    axis: int
    sign: int
    x: float
    y: float
    depth: float


def _axis_depth(handle: AxisHandle) -> float:
    return handle.depth


def projected_axes(view: Gf.Matrix4d) -> list[AxisHandle]:
    """Project world directions using only the camera rotation."""
    rotation: Gf.Matrix4d = view.GetOrthonormalized()
    result: list[AxisHandle] = []
    axis: int
    sign: int
    for axis in range(3):
        for sign in (-1, 1):
            direction: Gf.Vec3d = Gf.Vec3d(0.0)
            direction[axis] = float(sign)
            local: Gf.Vec3d = rotation.TransformDir(direction)
            result.append(AxisHandle(axis, sign, float(local[0]) * _LENGTH, float(local[1]) * _LENGTH, float(local[2])))
    return sorted(result, key=_axis_depth)


class OrientationGizmo:
    """Own a corner overlay; only its small canvas consumes mouse input."""

    def __init__(self, window: ViewportWindow, active_fn: Callable[[], bool]) -> None:
        self._window: ViewportWindow = window
        self._active_fn: Callable[[], bool] = active_fn
        self._frame: ui.Frame = window.get_frame("varun.launcher.orientation")
        self._scene: sc.SceneView | None = None
        self._input_area: ui.Rectangle | None = None
        self._axis_path: str = ""
        self._saved_axis: bool | None = None
        self._active: bool = False
        self._rotation: tuple[float, ...] = ()
        self._handles: list[AxisHandle] = []
        self._press: Point | None = None
        self._last: Point | None = None
        self._pressed_handle: AxisHandle | None = None
        self._dragged: bool = False

        # Keep the rest of the viewport transparent to mouse and touch input.
        with self._frame, ui.VStack():
            ui.Spacer()
            with ui.HStack(height=SIZE):
                ui.Spacer(width=8)
                with ui.ZStack(width=SIZE, height=SIZE):
                    self._scene = sc.SceneView(width=SIZE, height=SIZE, identifier="LauncherOrientationGizmo")
                    # Keep mouse capture separate from SceneView's internal gesture routing.
                    self._input_area = ui.Rectangle(
                        style={"background_color": ui.color(0.0, 0.0, 0.0, 0.0)},
                        opaque_for_mouse_events=True,
                        mouse_pressed_fn=self._on_pressed,
                        mouse_moved_fn=self._on_moved,
                        mouse_released_fn=self._on_released,
                        tooltip="Drag to orbit. Click an axis for its view; click again for the opposite side.",
                        identifier="LauncherOrientationGizmoInput",
                    )
                ui.Spacer()
            ui.Spacer(height=8)
        self._frame.visible = False
        self._update_sub: Any = (
            cast(Any, omni.kit.app.get_app())
            .get_update_event_stream()
            .create_subscription_to_pop(self._on_update, name="varun.launcher.usd_viewport orientation")
        )

    # Replace Kit's small axis only while this Light overlay is active.
    def _set_active(self, active: bool, viewport: ViewportAPI | None) -> None:
        if active == self._active:
            return
        settings: Any = carb.settings.get_settings()
        if active and viewport is not None:
            self._axis_path = f"/persistent/app/viewport/{viewport.id}/guide/axis/visible"
            previous: object = settings.get(self._axis_path)
            self._saved_axis = bool(previous) if previous is not None else None
            settings.set(self._axis_path, False)
        elif self._axis_path:
            if self._saved_axis is None:
                settings.destroy_item(self._axis_path)
            else:
                settings.set(self._axis_path, self._saved_axis)
            self._axis_path = ""
        self._active = active
        self._frame.visible = active
        self._rotation = ()
        self._cancel_drag()

    # Update only when the camera rotates, rather than rebuilding every idle frame.
    def _on_update(self, _event: Any) -> None:
        viewport: ViewportAPI | None = self._window.viewport_api
        self._set_active(self._active_fn() and viewport is not None, viewport)
        if not self._active or viewport is None:
            return
        self._frame.visible = self._window.visible and viewport.stage is not None
        if not self._frame.visible:
            self._cancel_drag()
            return
        view: Gf.Matrix4d = viewport.view
        values: list[float] = []
        row: int
        column: int
        for row in range(3):
            for column in range(3):
                values.append(float(view[row][column]))
        rotation: tuple[float, ...] = tuple(values)
        if rotation != self._rotation:
            self._rotation = rotation
            self._handles = projected_axes(view)
            self._draw()

    # Screen-space circles stay round while their centers follow world directions.
    def _draw(self) -> None:
        scene: sc.SceneView | None = self._scene
        if scene is None:
            return
        scene.scene.clear()
        handle: AxisHandle
        with scene.scene:
            # Draw all spokes first, then back-to-front filled circles and labels.
            for handle in self._handles:
                color: Color = (*AXIS_COLORS[handle.axis], 1.0)
                sc.Line(
                    (0.0, 0.0, 0.0),
                    (handle.x, handle.y, 0.0),
                    color=color,
                    thickness=2.0 if handle.sign > 0 else 1.0,
                )
            for handle in self._handles:
                color = (*AXIS_COLORS[handle.axis], 1.0)
                with sc.Transform(transform=sc.Matrix44.get_translation_matrix(handle.x, handle.y, 0.0)):
                    sc.Arc(
                        _RADIUS,
                        axis=2,
                        begin=0.0,
                        end=math.tau,
                        tesselation=48,
                        wireframe=False,
                        color=color if handle.sign > 0 else (0.11, 0.12, 0.13, 0.94),
                    )
                    if handle.sign < 0:
                        sc.Arc(
                            _RADIUS,
                            axis=2,
                            begin=0.0,
                            end=math.tau,
                            tesselation=48,
                            wireframe=True,
                            color=color,
                            thickness=1.5,
                        )
                    sc.Label(
                        ("" if handle.sign > 0 else "-") + "XYZ"[handle.axis],
                        color=(0.06, 0.07, 0.08, 1.0) if handle.sign > 0 else color,
                        alignment=ui.Alignment.CENTER,
                        size=14 if handle.sign > 0 else 11,
                    )

    # UI callbacks use screen-space logical pixels, independent of Windows DPI.
    def _hit_handle(self, x: float, y: float) -> AxisHandle | None:
        scene: sc.SceneView | None = self._scene
        if scene is None or scene.computed_width <= 0 or scene.computed_height <= 0:
            return None
        local_x: float = 2.0 * (x - scene.screen_position_x) / scene.computed_width - 1.0
        local_y: float = 1.0 - 2.0 * (y - scene.screen_position_y) / scene.computed_height
        handle: AxisHandle
        for handle in reversed(self._handles):
            if math.hypot(local_x - handle.x, local_y - handle.y) <= _RADIUS:
                return handle
        return None

    def _on_pressed(self, x: float, y: float, button: int, _modifiers: int) -> None:
        if button != 0 or not self._active:
            return
        self._press = self._last = (x, y)
        self._pressed_handle = self._hit_handle(x, y)
        self._dragged = False

    def _on_moved(self, x: float, y: float, _buttons: int, _modifiers: int) -> None:
        if self._press is None or self._last is None:
            return
        current: Point = (x, y)
        if not self._dragged and math.dist(current, self._press) < _DRAG_THRESHOLD:
            return
        self._dragged = True
        camera: NavigationCamera | None = find_camera(self._window.viewport_api)
        if camera is not None:
            camera.orbit_from_gizmo(2.0 * (x - self._last[0]) / SIZE, -2.0 * (y - self._last[1]) / SIZE)
        self._last = current

    def _on_released(self, x: float, y: float, button: int, _modifiers: int) -> None:
        if button != 0:
            return
        handle: AxisHandle | None = self._pressed_handle
        if self._press is not None and not self._dragged and handle is not None and self._hit_handle(x, y) == handle:
            camera: NavigationCamera | None = find_camera(self._window.viewport_api)
            if camera is not None:
                # Opposite endpoints overlap in an aligned view; repeat clicks flip sides.
                sign: int = -handle.sign if handle.depth > 0.9999 else handle.sign
                camera.align_axis(handle.axis, sign)
        self._cancel_drag()

    def _cancel_drag(self) -> None:
        self._press = None
        self._last = None
        self._pressed_handle = None
        self._dragged = False

    # Restore the native indicator and release overlay callbacks before its window.
    def destroy(self) -> None:
        self._update_sub = None
        self._set_active(False, self._window.viewport_api)
        if self._scene is not None:
            self._scene.scene.clear()
            self._scene.destroy()
            self._scene = None
        self._frame.clear()
        self._frame.visible = False
        self._input_area = None
        self._handles.clear()
