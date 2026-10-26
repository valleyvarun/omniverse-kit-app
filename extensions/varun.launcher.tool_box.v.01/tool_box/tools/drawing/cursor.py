# Python & library imports
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeAlias, TypedDict, cast

import carb.input
import omni.ui

# Omniverse & USD imports
from omni.ui import scene as sc
from pxr import Gf  # hoisted out of the per-mousemove raycast hot path

from tool_box.core.viewport import get_active_stage
from tool_box.tools.drawing.brush import BrushConfig, BrushSample
from tool_box.tools.drawing.pen import PenInput

# Local imports
from tool_box.tools.drawing.plane.geometry import PlaneRegistry
from tool_box.tools.drawing.plane.selection import (
    DEFAULT_GROUND_PLANE_PATH,
    PlaneSelection,
)
from tool_box.tools.drawing.settings import CURSOR_FACTORY_ID, RING_COLOR
from tool_box.tools.drawing.shapes import outline

LOGGER: logging.Logger = logging.getLogger(__name__)

# ============================================================================
# 3D viewport cursor that tracks the mouse on the active drawing plane,
# renders a brush ring + stamp grid, and forwards LMB drag events to the
# 3D paint / 3D pipe tool below.
# ============================================================================

# One-frame lead for the visual ring: render at (current + (current - last))
# in NDC so the ring tracks the OS cursor through render latency. The TRUE
# world position (no lead) is what we report to brush callbacks, so strokes
# stay accurate. On the very first sample we have no `last`, so no lead.

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

# Type aliases.
WorldPos: TypeAlias = tuple[float, float, float]
DragCallback: TypeAlias = Callable[[WorldPos], None]


class _Listener(TypedDict):
    begin: DragCallback | None
    change: DragCallback | None
    end: Callable[[], None] | None


# Module-level listener that drag gestures forward events to.
_listener: _Listener | None = None

# Module-level reference to the latest active cursor scene.
_active_scene: _CursorScene | None = None

# Every live _CursorScene instance (one per viewport). hide() toggles them off.
_scenes: set[_CursorScene] = set()


# ----------------------------------------------------------------------
# 2. HELPER FUNCTIONS
# ----------------------------------------------------------------------


# Build an orthonormal in-plane basis (right, up) given a unit normal. Together
# with the normal these form a right-handed frame: right x up = normal.
def _basis_for_normal(normal: Gf.Vec3d) -> tuple[Gf.Vec3d, Gf.Vec3d]:
    ny = float(cast(Any, normal)[1])
    if abs(ny) < 0.9:
        ref = Gf.Vec3d(0.0, 1.0, 0.0)
    else:
        ref = Gf.Vec3d(1.0, 0.0, 0.0)
    right: Any = cast(Any, Gf).Cross(normal, ref)
    length = float(right.GetLength())
    if length < 1e-9:
        right = cast(Any, Gf).Cross(normal, Gf.Vec3d(0.0, 0.0, 1.0))
        length = float(right.GetLength())
        if length < 1e-9:
            return (Gf.Vec3d(1.0, 0.0, 0.0), Gf.Vec3d(0.0, 1.0, 0.0))
    right = right / length
    up: Any = cast(Any, Gf).Cross(normal, right)
    return (right, up)


# ----------------------------------------------------------------------
# 3. CURSOR3 D
# ----------------------------------------------------------------------


# Public entry point: register/deregister the cursor scene with the viewport.
class Cursor3D:
    # Keep the cursor scene registration handle.
    def __init__(self) -> None:
        self._registration: Any = None

    # Register the cursor scene and reveal retained scene instances.
    def show(self) -> None:
        if self._registration is not None:
            return
        from omni.kit.viewport.registry import RegisterScene

        PenInput.get().start()
        self._registration = cast(Any, RegisterScene)(_CursorScene, CURSOR_FACTORY_ID)
        # Reactivate any previously hidden scenes the registry kept alive.
        s: _CursorScene
        for s in list(_scenes):
            try:
                s.set_visible(True)
            except Exception:
                LOGGER.debug("Drawing tool SDK operation failed", exc_info=True)

    # Hide cursor scenes and release their registration.
    def hide(self) -> None:
        PenInput.get().stop()
        s: _CursorScene
        for s in list(_scenes):
            try:
                s.set_visible(False)
            except Exception:
                LOGGER.debug("Drawing tool SDK operation failed", exc_info=True)
        if self._registration is None:
            return
        try:
            self._registration.destroy()
        except Exception:
            LOGGER.debug("Drawing tool SDK operation failed", exc_info=True)
        self._registration = None

    # Input callbacks always resolve the scene that received the gesture.
    # Return the most recently activated cursor scene.
    @staticmethod
    def get_scene() -> _CursorScene | None:
        return _active_scene

    # Store callbacks for drag begin, movement, and completion.
    def set_listener(
        self,
        on_begin: DragCallback | None = None,
        on_change: DragCallback | None = None,
        on_end: Callable[[], None] | None = None,
    ) -> None:
        global _listener
        _listener = {"begin": on_begin, "change": on_change, "end": on_end}

    # Disconnect the drawing callbacks.
    def clear_listener(self) -> None:
        global _listener
        _listener = None

    @staticmethod
    def sample() -> BrushSample:
        return BrushConfig.get().sample(PenInput.get().pressure)

    def refresh(self) -> None:
        scene: Any
        for scene in tuple(_scenes):
            scene.refresh_brush()

    # Read the current brush radius.
    @staticmethod
    def radius() -> float:
        return BrushConfig.get().brush_radius


# ----------------------------------------------------------------------
# 4. CURSOR SCENE
# ----------------------------------------------------------------------


# Per-viewport scene that draws the brush ring + grid and routes mouse input.
class _CursorScene:
    # Create cursor visuals, gesture handlers, and plane tracking.
    def __init__(self, desc: dict[str, Any]) -> None:
        global _active_scene
        self._viewport_api: Any = desc.get("viewport_api")
        self._up_axis = self._detect_up_axis()
        self._world_pos: WorldPos | None = None
        # Snapshot live brush settings (a config change rebuilds the scene).
        cfg = BrushConfig.get()
        self._brush_radius: float = float(cfg.brush_radius)
        self._brush_signature: tuple[float, str] | None = None
        self._brush_outline: Any = None
        # Last NDC sample, used to lead the ring by one frame.
        self._last_ndc: tuple[float, float] | None = None
        self._visible = True

        # Active drawing-plane state: origin, unit normal, in-plane basis.
        self._origin: Gf.Vec3d = Gf.Vec3d(0.0, 0.0, 0.0)
        self._normal: Gf.Vec3d = Gf.Vec3d(0.0, 1.0, 0.0)
        self._basis_r: Gf.Vec3d = Gf.Vec3d(1.0, 0.0, 0.0)
        self._basis_u: Gf.Vec3d = Gf.Vec3d(0.0, 0.0, 1.0)
        self._orient_transform: Any = None
        self._layer_listener_cb: Any = None
        self._refresh_active_plane()

        _active_scene = self
        _scenes.add(self)

        # Outer wrapper Transform whose `visible` we toggle.
        self._root = sc.Transform(visible=True)
        with self._root:
            # Inner Transform we move to follow the cursor.
            self._transform = sc.Transform()
            with self._transform:
                self._orient_transform = sc.Transform(transform=self._compute_orient_matrix())
                with self._orient_transform:
                    self._brush_outline = sc.Curve([(0, 0, 0), (1, 0, 0)])
                    self.refresh_brush()
            # Hover for cursor tracking; Drag (LMB) forwards to listener.
            self._hover_screen = sc.Screen(gesture=_Hover(self))
            self._drag_screen = sc.Screen(gestures=[_Drag(self), _Drag(self, ADJUST_MODIFIERS)])

        # Re-orient the ring when the drawing-plane picker changes.
        self._layer_listener_cb = self._on_active_plane_changed
        registry = PlaneSelection.get()
        if registry is not None:
            registry.add_listener(self._layer_listener_cb)

    # Register this scene as a viewport manipulator.
    @property
    def categories(self) -> tuple[str, ...]:
        return ("manipulator",)

    # Return the scene registration name.
    @property
    def name(self) -> str:
        return "ThreeDDrawCursor"

    # Read or update scene visibility.
    @property
    def visible(self) -> bool:
        return self._visible

    # Read or update scene visibility.
    @visible.setter
    def visible(self, value: bool) -> None:
        self.set_visible(bool(value))

    # Toggle visuals + gesture screens so the cursor truly disappears.
    def set_visible(self, value: bool) -> None:
        self._visible = bool(value)
        try:
            if self._root is not None:
                self._root.visible = self._visible
        except Exception:
            LOGGER.debug("Drawing tool SDK operation failed", exc_info=True)

    # Release the plane listener and cursor scene references.
    def destroy(self) -> None:
        global _active_scene
        if self._layer_listener_cb is not None:
            registry = PlaneSelection.get()
            if registry is not None:
                try:
                    registry.remove_listener(self._layer_listener_cb)
                except Exception:
                    LOGGER.debug("Drawing tool SDK operation failed", exc_info=True)
            self._layer_listener_cb = None
        _scenes.discard(self)
        if _active_scene is self:
            _active_scene = None
        self._transform = None
        self._orient_transform = None
        self._root = None

    @property
    def viewport_api(self) -> Any:
        return self._viewport_api

    # Read the stage up axis, falling back to Y.
    def _detect_up_axis(self) -> str:
        try:
            from pxr import UsdGeom

            stage = get_active_stage(self._viewport_api)
            if stage is not None:
                return str(cast(Any, UsdGeom).GetStageUpAxis(stage))
        except Exception:
            LOGGER.debug("Drawing tool SDK operation failed", exc_info=True)
        return "Y"

    # Resolve the currently-active drawing plane. Falls back to the world
    # ground plane (origin at world origin, normal along the stage up axis).
    def _active_plane_info(self) -> tuple[Gf.Vec3d, Gf.Vec3d]:
        registry = PlaneSelection.get()
        path = registry.active_plane(self._viewport_api) if registry is not None else DEFAULT_GROUND_PLANE_PATH
        if path != DEFAULT_GROUND_PLANE_PATH:
            info = PlaneRegistry.info_for_path(path, self._viewport_api)
            if info is not None:
                return info
        if self._up_axis == "Z":
            return (Gf.Vec3d(0.0, 0.0, 0.0), Gf.Vec3d(0.0, 0.0, 1.0))
        return (Gf.Vec3d(0.0, 0.0, 0.0), Gf.Vec3d(0.0, 1.0, 0.0))

    # Recompute (origin, normal, R, U) from the currently-active plane.
    def _refresh_active_plane(self) -> None:
        origin, normal_in = self._active_plane_info()
        nrm: Any = normal_in
        length = float(nrm.GetLength())
        if length > 1e-9:
            nrm = nrm / length
        else:
            nrm = Gf.Vec3d(0.0, 1.0, 0.0)
        right, up = _basis_for_normal(nrm)
        self._origin = origin
        self._normal = nrm
        self._basis_r = right
        self._basis_u = up

    # 4x4 column-major matrix that maps Arc-local (X, Y, Z) to plane (R, U, N).
    def _compute_orient_matrix(self) -> list[float]:
        right, up, normal = self._basis_r, self._basis_u, self._normal
        return [
            float(cast(Any, right)[0]),
            float(cast(Any, right)[1]),
            float(cast(Any, right)[2]),
            0.0,
            float(cast(Any, up)[0]),
            float(cast(Any, up)[1]),
            float(cast(Any, up)[2]),
            0.0,
            float(cast(Any, normal)[0]),
            float(cast(Any, normal)[1]),
            float(cast(Any, normal)[2]),
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
        ]

    # PlaneSelection change hook -- re-derive basis + push fresh matrix.
    def _on_active_plane_changed(self) -> None:
        self._up_axis = self._detect_up_axis()
        self._refresh_active_plane()
        if self._orient_transform is not None:
            try:
                self._orient_transform.transform = self._compute_orient_matrix()
            except Exception:
                LOGGER.debug("Drawing tool SDK operation failed", exc_info=True)

    # Move cursor under given NDC mouse coords. Returns the TRUE world position
    # (no lead) so brush callbacks stay accurate; the visible transform is
    # offset by one sample of mouse motion to mask render latency.
    def update(self, mouse_ndc: tuple[float, float]) -> WorldPos | None:
        if self._transform is None or self._viewport_api is None:
            return None
        # Note: plane refresh + orient-matrix rewrite are NOT done here.
        # They happen only when the active plane actually changes (via the
        # PlaneSelection listener). Doing them on every mouse event was
        # invalidating the omni.ui.scene transform every frame.
        self.refresh_brush()
        p = self._raycast(mouse_ndc)
        if p is None:
            return None
        self._world_pos = p
        # One-frame lead in NDC: predicted = current + (current - last).
        visual_p: WorldPos = p
        last = self._last_ndc
        if last is not None:
            predicted = (
                mouse_ndc[0] + (mouse_ndc[0] - last[0]),
                mouse_ndc[1] + (mouse_ndc[1] - last[1]),
            )
            pp = self._raycast(predicted)
            if pp is not None:
                visual_p = pp
        self._last_ndc = mouse_ndc
        self._transform.transform = [
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            0,
            0,
            0,
            1,
            0,
            float(visual_p[0]),
            float(visual_p[1]),
            float(visual_p[2]),
            1,
        ]
        return p

    def refresh_brush(self) -> None:
        if self._brush_outline is None:
            return
        cfg: BrushConfig = BrushConfig.get()
        radius: float = Cursor3D.sample().radius
        signature: tuple[float, str] = (radius, cfg.shape)
        if signature == self._brush_signature:
            return
        self._brush_signature = signature
        positions: list[tuple[float, float, float]] = []
        vertex: Any
        for vertex in outline(cfg.shape):
            positions.append((float(vertex[0]) * radius, float(vertex[1]) * radius, 0.0))
        positions.append(positions[0])
        self._brush_outline.positions = positions
        self._brush_outline.colors = [RING_COLOR] * len(positions)
        self._brush_outline.thicknesses = [2.0] * len(positions)

    # Return the last unpredicted cursor position.
    @property
    def world_pos(self) -> WorldPos | None:
        return self._world_pos

    # Return the stage up axis used by this cursor.
    @property
    def up_axis(self) -> str:
        return self._up_axis

    # Intersect the mouse ray with the active drawing plane.
    def _raycast(self, mouse_ndc: tuple[float, float]) -> tuple[float, float, float] | None:
        # Transform the near and far mouse positions into a world-space ray.
        ndc_to_world = getattr(self._viewport_api, "ndc_to_world", None)
        if ndc_to_world is None:
            return None
        mx, my = mouse_ndc
        try:
            n = ndc_to_world.Transform(Gf.Vec3d(mx, my, -1.0))
            f = ndc_to_world.Transform(Gf.Vec3d(mx, my, 1.0))
        except Exception:
            LOGGER.debug("Drawing tool SDK operation failed", exc_info=True)
            return None
        # Generic plane intersection (infinite plane).
        nrm = cast(Any, self._normal)
        org = cast(Any, self._origin)
        nx, ny, nz = float(n[0]), float(n[1]), float(n[2])
        fx, fy, fz = float(f[0]), float(f[1]), float(f[2])
        pnx, pny, pnz = float(nrm[0]), float(nrm[1]), float(nrm[2])
        pox, poy, poz = float(org[0]), float(org[1]), float(org[2])
        # Ignore rays parallel to the drawing plane.
        denom = pnx * (fx - nx) + pny * (fy - ny) + pnz * (fz - nz)
        if abs(denom) < 1e-9:
            return None
        num = pnx * (pox - nx) + pny * (poy - ny) + pnz * (poz - nz)
        t = num / denom
        # Ignore intersections behind the ray origin.
        if t < 0.0:
            return None
        return (nx + (fx - nx) * t, ny + (fy - ny) * t, nz + (fz - nz) * t)


# ----------------------------------------------------------------------
# 5. HOVER
# ----------------------------------------------------------------------


# Hover gesture: tracks mouse motion to update the brush ring position.
class _Hover(sc.HoverGesture):  # type: ignore[misc]
    # Bind the hover gesture to its cursor scene.
    def __init__(self, scene: _CursorScene) -> None:
        cast(Any, super()).__init__()
        self._scene = scene

    # Move the visible brush ring with the mouse.
    def on_changed(self) -> None:
        if not self._scene.visible:
            return
        try:
            m = self.sender.gesture_payload.mouse
            self._scene.update((float(m[0]), float(m[1])))
        except Exception:
            LOGGER.debug("Drawing tool SDK operation failed", exc_info=True)


# ----------------------------------------------------------------------
# 6. DRAG
# ----------------------------------------------------------------------


ADJUST_MODIFIERS: int = carb.input.KEYBOARD_MODIFIER_FLAG_CONTROL | carb.input.KEYBOARD_MODIFIER_FLAG_ALT


class _Drag(sc.DragGesture):
    """Route drawing and Ctrl+Alt adjustment without rebuilding the input scene."""

    def __init__(self, scene: _CursorScene, modifiers: int = 0) -> None:
        cast(Any, super()).__init__(modifiers=modifiers)
        self._scene: _CursorScene = scene
        self._mode: str = ""
        self._last_mouse: tuple[float, float] | None = None

    def on_began(self) -> None:
        global _active_scene
        _active_scene = self._scene
        self._scene._on_active_plane_changed()
        self._mode = ""
        self._last_mouse = None
        self._route()

    def on_changed(self) -> None:
        self._route()

    def _route(self) -> None:
        if not self._scene.visible:
            return
        try:
            mouse: Any = self.sender.gesture_payload.mouse
            ndc: tuple[float, float] = (float(mouse[0]), float(mouse[1]))
            flags: int = carb.input.acquire_input_interface().get_modifier_flags(
                device_types=[carb.input.DeviceType.KEYBOARD]
            )
            adjusting: bool = flags & ADJUST_MODIFIERS == ADJUST_MODIFIERS
            pos: WorldPos | None = self._scene.update(ndc)
            if adjusting:
                if self._mode == "draw":
                    end_callback: Callable[[], None] | None = _listener["end"] if _listener else None
                    if end_callback is not None:
                        end_callback()
                if self._mode == "adjust" and self._last_mouse is not None:
                    # Convert viewport NDC to logical pixels so monitor DPI does not alter sensitivity.
                    resolution: Any = self._scene.viewport_api.resolution
                    dpi: float = max(1.0, omni.ui.Workspace.get_dpi_scale())
                    BrushConfig.get().adjust(
                        (ndc[0] - self._last_mouse[0]) * float(resolution[0]) / (2 * dpi),
                        -(ndc[1] - self._last_mouse[1]) * float(resolution[1]) / (2 * dpi),
                    )
                self._mode = "adjust"
            elif pos is not None and (self._mode != "draw" or ndc != self._last_mouse):
                callback: DragCallback | None = (
                    (_listener["change"] if self._mode == "draw" else _listener["begin"]) if _listener else None
                )
                if callback is not None:
                    callback(pos)
                self._mode = "draw"
            self._last_mouse = ndc
        except Exception:
            LOGGER.exception("Could not process brush gesture")

    def on_ended(self) -> None:
        if self._mode == "draw":
            callback: Callable[[], None] | None = _listener["end"] if _listener else None
            if callback is not None:
                callback()
        self._mode = ""
        self._last_mouse = None
