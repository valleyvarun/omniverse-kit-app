from __future__ import annotations

from typing import Any, Callable, cast

from omni.ui import scene as sc
from pxr import Gf  # hoisted out of the per-mousemove raycast hot path

from .drawing_plane import PlaneRegistry
from .tool_settings import (
    CURSOR_FACTORY_ID,
    RING_COLOR,
)
from ...layers.layers import DEFAULT_GROUND_PLANE_PATH, LayerRegistry
from ...active_context import get_active_stage


# ============================================================================
# 3D viewport cursor that tracks the mouse on the active drawing plane,
# renders a brush ring + stamp grid, and forwards LMB drag events to the
# 3D Draw tool below.
# ============================================================================

# One-frame lead for the visual ring: render at (current + (current - last))
# in NDC so the ring tracks the OS cursor through render latency. The TRUE
# world position (no lead) is what we report to brush callbacks, so strokes
# stay accurate. On the very first sample we have no `last`, so no lead.

# Type aliases.
WorldPos = tuple[float, float, float]
DragCallback = Callable[[WorldPos], None]

# Module-level listener that drag gestures forward events to.
_listener: dict[str, Any] | None = None

# Module-level reference to the latest active cursor scene.
_active_scene: Any = None

# Every live _CursorScene instance (one per viewport). hide() toggles them off.
_scenes: set[Any] = set()


# Build an orthonormal in-plane basis (right, up) given a unit normal. Together
# with the normal these form a right-handed frame: right x up = normal.
def _basis_for_normal(normal: "Gf.Vec3d") -> "tuple[Gf.Vec3d, Gf.Vec3d]":
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


# Public entry point: register/deregister the cursor scene with the viewport.
class Cursor3D:
    def __init__(self) -> None:
        self._registration: Any = None

    def show(self) -> None:
        if self._registration is not None:
            return
        from omni.kit.viewport.registry import RegisterScene
        self._registration = cast(Any, RegisterScene)(_CursorScene, CURSOR_FACTORY_ID)
        # Reactivate any previously hidden scenes the registry kept alive.
        for s in list(_scenes):
            try:
                s.set_visible(True)
            except Exception:
                pass

    def hide(self) -> None:
        for s in list(_scenes):
            try:
                s.set_visible(False)
            except Exception:
                pass
        if self._registration is None:
            return
        try:
            self._registration.destroy()
        except Exception:
            pass
        self._registration = None

    @staticmethod
    def get_scene() -> Any:
        return _active_scene

    def set_listener(
        self,
        on_begin: DragCallback | None = None,
        on_change: DragCallback | None = None,
        on_end: Callable[[], None] | None = None,
    ) -> None:
        global _listener
        _listener = {"begin": on_begin, "change": on_change, "end": on_end}

    def clear_listener(self) -> None:
        global _listener
        _listener = None

    @staticmethod
    def radius() -> float:
        from .brush_config import BrushConfig
        return BrushConfig.get().brush_radius


# Per-viewport scene that draws the brush ring + grid and routes mouse input.
class _CursorScene:
    def __init__(self, desc: dict[str, Any]) -> None:
        global _active_scene
        from .brush_config import BrushConfig
        self._viewport_api: Any = desc.get("viewport_api")
        self._up_axis = self._detect_up_axis()
        self._world_pos: WorldPos | None = None
        # Snapshot live brush settings (a config change rebuilds the scene).
        cfg = BrushConfig.get()
        self._brush_radius: float = float(cfg.brush_radius)
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
                    sc.Arc(radius=self._brush_radius, tesselation=64, wireframe=True,
                           thickness=2.0, color=RING_COLOR)
            # Hover for cursor tracking; Drag (LMB) forwards to listener.
            self._hover_screen = sc.Screen(gesture=_Hover(self))
            self._drag_screen = sc.Screen(gesture=_Drag(self))

        # Re-orient ring when the user picks a different drawing plane in CLayers.
        self._layer_listener_cb = self._on_active_plane_changed
        registry = LayerRegistry.get()
        if registry is not None:
            registry.add_listener(self._layer_listener_cb)

    @property
    def categories(self) -> tuple[str, ...]:
        return ("manipulator",)

    @property
    def name(self) -> str:
        return "ThreeDDrawCursor"

    @property
    def visible(self) -> bool:
        return self._visible

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
            pass

    def destroy(self) -> None:
        global _active_scene
        if self._layer_listener_cb is not None:
            registry = LayerRegistry.get()
            if registry is not None:
                try:
                    registry.remove_listener(self._layer_listener_cb)
                except Exception:
                    pass
            self._layer_listener_cb = None
        _scenes.discard(self)
        if _active_scene is self:
            _active_scene = None
        self._transform = None
        self._orient_transform = None
        self._root = None

    def _detect_up_axis(self) -> str:
        try:
            from pxr import UsdGeom
            stage = get_active_stage(self._viewport_api)
            if stage is not None:
                return str(cast(Any, UsdGeom).GetStageUpAxis(stage))
        except Exception:
            pass
        return "Y"

    # Resolve the currently-active drawing plane. Falls back to the world
    # ground plane (origin at world origin, normal along the stage up axis).
    def _active_plane_info(self) -> "tuple[Gf.Vec3d, Gf.Vec3d]":
        registry = LayerRegistry.get()
        path = registry.active_plane() if registry is not None else DEFAULT_GROUND_PLANE_PATH
        if path != DEFAULT_GROUND_PLANE_PATH:
            info = PlaneRegistry.info_for_path(path)
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
            float(cast(Any, right)[0]), float(cast(Any, right)[1]), float(cast(Any, right)[2]), 0.0,
            float(cast(Any, up)[0]),    float(cast(Any, up)[1]),    float(cast(Any, up)[2]),    0.0,
            float(cast(Any, normal)[0]), float(cast(Any, normal)[1]), float(cast(Any, normal)[2]), 0.0,
            0.0, 0.0, 0.0, 1.0,
        ]

    # LayerRegistry change hook -- re-derive basis + push fresh matrix.
    def _on_active_plane_changed(self) -> None:
        self._refresh_active_plane()
        if self._orient_transform is not None:
            try:
                self._orient_transform.transform = self._compute_orient_matrix()
            except Exception:
                pass

    # Move cursor under given NDC mouse coords. Returns the TRUE world position
    # (no lead) so brush callbacks stay accurate; the visible transform is
    # offset by one sample of mouse motion to mask render latency.
    def update(self, mouse_ndc: tuple[float, float]) -> WorldPos | None:
        if self._transform is None or self._viewport_api is None:
            return None
        # Note: plane refresh + orient-matrix rewrite are NOT done here.
        # They happen only when the active plane actually changes (via the
        # LayerRegistry listener). Doing them on every mouse event was
        # invalidating the omni.ui.scene transform every frame.
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
            1, 0, 0, 0,
            0, 1, 0, 0,
            0, 0, 1, 0,
            float(visual_p[0]), float(visual_p[1]), float(visual_p[2]), 1,
        ]
        return p

    @property
    def world_pos(self) -> WorldPos | None:
        return self._world_pos

    @property
    def up_axis(self) -> str:
        return self._up_axis

    def _raycast(self, mouse_ndc: tuple[float, float]) -> tuple[float, float, float] | None:
        ndc_to_world = getattr(self._viewport_api, "ndc_to_world", None)
        if ndc_to_world is None:
            return None
        mx, my = mouse_ndc
        try:
            n = ndc_to_world.Transform(Gf.Vec3d(mx, my, -1.0))
            f = ndc_to_world.Transform(Gf.Vec3d(mx, my, 1.0))
        except Exception:
            return None
        # Generic plane intersection (infinite plane).
        nrm = cast(Any, self._normal)
        org = cast(Any, self._origin)
        nx, ny, nz = float(n[0]), float(n[1]), float(n[2])
        fx, fy, fz = float(f[0]), float(f[1]), float(f[2])
        pnx, pny, pnz = float(nrm[0]), float(nrm[1]), float(nrm[2])
        pox, poy, poz = float(org[0]), float(org[1]), float(org[2])
        denom = pnx * (fx - nx) + pny * (fy - ny) + pnz * (fz - nz)
        if abs(denom) < 1e-9:
            return None
        num = pnx * (pox - nx) + pny * (poy - ny) + pnz * (poz - nz)
        t = num / denom
        if t < 0.0:
            return None
        return (nx + (fx - nx) * t, ny + (fy - ny) * t, nz + (fz - nz) * t)


# Hover gesture: tracks mouse motion to update the brush ring position.
class _Hover(sc.HoverGesture):  # type: ignore[misc]
    def __init__(self, scene: _CursorScene) -> None:
        cast(Any, super()).__init__()
        self._scene = scene

    def on_changed(self) -> None:
        if not self._scene.visible:
            return
        try:
            m = self.sender.gesture_payload.mouse
            self._scene.update((float(m[0]), float(m[1])))
        except Exception:
            pass


# LMB drag gesture: forwards begin / change / end events to the listener.
class _Drag(sc.DragGesture):  # type: ignore[misc]
    def __init__(self, scene: _CursorScene) -> None:
        cast(Any, super()).__init__()
        self._scene = scene

    def on_began(self) -> None:
        if not self._scene.visible:
            return
        try:
            m = self.sender.gesture_payload.mouse
            pos = self._scene.update((float(m[0]), float(m[1])))
            if pos is None:
                pos = self._scene.world_pos
            cb = (_listener or {}).get("begin")
            if cb is not None and pos is not None:
                cb(pos)
        except Exception:
            pass

    def on_changed(self) -> None:
        if not self._scene.visible:
            return
        try:
            m = self.sender.gesture_payload.mouse
            pos = self._scene.update((float(m[0]), float(m[1])))
            if pos is None:
                pos = self._scene.world_pos
            cb = (_listener or {}).get("change")
            if cb is not None and pos is not None:
                cb(pos)
        except Exception:
            pass

    def on_ended(self) -> None:
        if not self._scene.visible:
            return
        try:
            cb = (_listener or {}).get("end")
            if cb is not None:
                cb()
        except Exception:
            pass
