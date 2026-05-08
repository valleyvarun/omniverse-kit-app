from __future__ import annotations

import time
from typing import Any, Callable, cast

import omni.usd
from omni.ui import scene as sc
from pxr import Gf  # hoisted out of the per-mousemove raycast hot path

from .drawing_plane import PlaneRegistry
from .layers import DEFAULT_GROUND_PLANE_PATH, LayerRegistry


_FACTORY_ID = "varun.launcher.ui.cursor_3d"

# Cursor appearance.
_RADIUS = 2.0
_GRID_N = 7                # grid resolution across diameter
_POINT_SIZE = 3.0
_POINT_COLOR = (1.0, 1.0, 1.0, 1.0)
_RING_COLOR = (1.0, 0.85, 0.2, 1.0)

# Velocity-based prediction for the visual ring. The ring is rendered at
# (current_pos + velocity * ahead_s) so it appears glued to the OS cursor at
# high mouse speeds. The TRUE cursor position is still reported to brush
# callbacks unmodified, so strokes remain accurate.
#
# `ahead_s` scales with mouse speed:
#   - Slow / stationary  => ~0 (no overshoot, ring is rock-steady)
#   - Faster             => leads more, up to _PREDICT_MAX_AHEAD_S
# This matches how pro paint apps mask render latency: the eye tolerates
# more lead at high speed (lag is most visible) and demands precision at
# low speed (lag is invisible).
_PREDICT_AHEAD_PER_NDC_PER_S = 1.0 / 30.0   # how aggressively lead scales with speed
_PREDICT_MAX_AHEAD_S = 2.0 / 60.0           # cap lead at ~2 frames @ 60 FPS
_PREDICT_MAX_NDC = 0.15                     # absolute clamp on offset magnitude (~15% of viewport)
_VELOCITY_SMOOTHING = 0.5                   # 0=no smoothing, 1=infinite (frozen)

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
# with the normal these form a right-handed frame: right x up = normal. Used
# to (a) orient the cursor's ring/dot grid flat onto the active plane and
# (b) lay grid points out within that plane for the brush stamps.
def _basis_for_normal(normal: "Gf.Vec3d") -> "tuple[Gf.Vec3d, Gf.Vec3d]":
    # Pick a reference vector that isn't (nearly) parallel to the normal.
    ny = float(cast(Any, normal)[1])
    if abs(ny) < 0.9:
        ref = Gf.Vec3d(0.0, 1.0, 0.0)
    else:
        ref = Gf.Vec3d(1.0, 0.0, 0.0)
    right: Any = cast(Any, Gf).Cross(normal, ref)
    length = float(right.GetLength())
    if length < 1e-9:
        # Degenerate (shouldn't happen with the choice above) - try Z.
        right = cast(Any, Gf).Cross(normal, Gf.Vec3d(0.0, 0.0, 1.0))
        length = float(right.GetLength())
        if length < 1e-9:
            return (Gf.Vec3d(1.0, 0.0, 0.0), Gf.Vec3d(0.0, 1.0, 0.0))
    right = right / length
    up: Any = cast(Any, Gf).Cross(normal, right)  # unit since normal,right are unit and perpendicular
    return (right, up)


class Cursor3D:
    def __init__(self) -> None:
        self._registration: Any = None

    def show(self) -> None:
        if self._registration is not None:
            return
        from omni.kit.viewport.registry import RegisterScene
        self._registration = cast(Any, RegisterScene)(_CursorScene, _FACTORY_ID)
        # In case any scene instances survived a previous hide() (the registry
        # may keep them alive even after destroy), make sure they're visible.
        for s in list(_scenes):
            try:
                s.set_visible(True)
            except Exception:
                pass

    def hide(self) -> None:
        # First, hide all live scene instances so the visuals disappear even
        # if the registry leaves them attached to viewports.
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

    # Returns the active cursor scene (for the first viewport), or None.
    @staticmethod
    def get_scene() -> Any:
        return _active_scene

    # Register callbacks for LMB drag events. Each callback receives the
    # cursor's current ground-plane world position (x, y, z).
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


class _CursorScene:
    def __init__(self, desc: dict[str, Any]) -> None:
        global _active_scene
        self._viewport_api: Any = desc.get("viewport_api")
        self._up_axis = self._detect_up_axis()
        self._world_pos: WorldPos | None = None
        # Memoized grid-points list (constant; rebuilt only if constants change).
        self._grid_points: list[list[float]] = self._make_grid_points()
        # Velocity prediction state (NDC space, smoothed across samples).
        self._last_ndc: tuple[float, float] | None = None
        self._last_ndc_t: float = 0.0
        self._vel_ndc: tuple[float, float] = (0.0, 0.0)
        self._visible = True

        # Active drawing-plane state. Origin O, unit normal N, and an
        # orthonormal in-plane basis (R, U). The cursor's ring lies in
        # span(R, U) and the brush stamp grid is laid out in the same
        # basis so visual dots and brush stamps coincide. These are
        # refreshed on construction and whenever LayerRegistry signals an
        # active-plane change (see _on_active_plane_changed).
        self._origin: Gf.Vec3d = Gf.Vec3d(0.0, 0.0, 0.0)
        self._normal: Gf.Vec3d = Gf.Vec3d(0.0, 1.0, 0.0)
        self._basis_r: Gf.Vec3d = Gf.Vec3d(1.0, 0.0, 0.0)
        self._basis_u: Gf.Vec3d = Gf.Vec3d(0.0, 0.0, 1.0)
        self._orient_transform: Any = None
        self._layer_listener_cb: Any = None
        self._refresh_active_plane()

        _active_scene = self
        _scenes.add(self)

        # Outer wrapper Transform whose `visible` we toggle via set_visible().
        self._root = sc.Transform(visible=True)
        with self._root:
            # Inner Transform we move to follow the cursor.
            self._transform = sc.Transform()
            with self._transform:
                self._orient_transform = sc.Transform(transform=self._compute_orient_matrix())
                with self._orient_transform:
                    # Outer ring.
                    sc.Arc(radius=_RADIUS, tesselation=64, wireframe=True,
                           thickness=2.0, color=_RING_COLOR)
                    # Grid of points inside the ring.
                    pts = self._grid_points
                    sc.Points(
                        pts,
                        sizes=[_POINT_SIZE] * len(pts),
                        colors=[_POINT_COLOR] * len(pts),
                    )

            # Hover for cursor tracking; Drag (LMB) forwards to listener.
            self._hover_screen = sc.Screen(gesture=_Hover(self))
            self._drag_screen = sc.Screen(gesture=_Drag(self))

        # Listen for active-plane changes so the ring re-orients in real time
        # when the user picks a different drawing plane in CLayers.
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

    # Toggle both the visuals and the gesture screens so the cursor truly
    # disappears (and stops capturing hover/drag) when the tool is inactive.
    def set_visible(self, value: bool) -> None:
        self._visible = bool(value)
        try:
            if self._root is not None:
                self._root.visible = self._visible
        except Exception:
            pass

    def destroy(self) -> None:
        global _active_scene
        # Drop our LayerRegistry subscription before tearing down scene state.
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
            stage = cast(Any, omni.usd.get_context()).get_stage()
            if stage is not None:
                return str(cast(Any, UsdGeom).GetStageUpAxis(stage))
        except Exception:
            pass
        return "Y"

    # Resolve the currently-active drawing plane to a (origin, unit normal)
    # pair. Falls back to the world ground plane (origin at world origin,
    # normal along the stage up axis) when no plane is active or the
    # active prim can't be inspected.
    def _active_plane_info(self) -> "tuple[Gf.Vec3d, Gf.Vec3d]":
        registry = LayerRegistry.get()
        path = registry.active_plane() if registry is not None else DEFAULT_GROUND_PLANE_PATH
        if path != DEFAULT_GROUND_PLANE_PATH:
            info = PlaneRegistry.info_for_path(path)
            if info is not None:
                return info
        # Ground-plane fallback.
        if self._up_axis == "Z":
            return (Gf.Vec3d(0.0, 0.0, 0.0), Gf.Vec3d(0.0, 0.0, 1.0))
        return (Gf.Vec3d(0.0, 0.0, 0.0), Gf.Vec3d(0.0, 1.0, 0.0))

    # Recompute (origin, normal, R, U) from the currently-active plane.
    # Caller is responsible for pushing the new orient matrix into the
    # scene transform if/when needed.
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

    # 4x4 column-major matrix that takes the Arc's local frame (X, Y, Z) to
    # the active plane's world frame (R, U, N). Local +Z (the Arc's normal)
    # ends up along the plane normal, so the ring lies flat on the plane.
    def _compute_orient_matrix(self) -> list[float]:
        right, up, normal = self._basis_r, self._basis_u, self._normal
        return [
            float(cast(Any, right)[0]), float(cast(Any, right)[1]), float(cast(Any, right)[2]), 0.0,
            float(cast(Any, up)[0]),    float(cast(Any, up)[1]),    float(cast(Any, up)[2]),    0.0,
            float(cast(Any, normal)[0]), float(cast(Any, normal)[1]), float(cast(Any, normal)[2]), 0.0,
            0.0, 0.0, 0.0, 1.0,
        ]

    # LayerRegistry change hook: an active-plane switch (or any registry
    # change) re-derives the basis and pushes a fresh matrix into the
    # already-built orient transform. The next mouse-move event will then
    # re-raycast against the new plane and reposition the cursor.
    def _on_active_plane_changed(self) -> None:
        self._refresh_active_plane()
        if self._orient_transform is not None:
            try:
                self._orient_transform.transform = self._compute_orient_matrix()
            except Exception:
                pass

    # Grid of points in the Arc's local XY plane, clipped to a disk of radius _RADIUS.
    def _make_grid_points(self) -> list[list[float]]:
        pts: list[list[float]] = []
        if _GRID_N <= 1:
            return [[0.0, 0.0, 0.0]]
        step = (2.0 * _RADIUS) / (_GRID_N - 1)
        r2 = _RADIUS * _RADIUS
        for i in range(_GRID_N):
            for j in range(_GRID_N):
                x = -_RADIUS + i * step
                y = -_RADIUS + j * step
                if x * x + y * y <= r2:
                    pts.append([x, y, 0.0])
        return pts

    # Move cursor to ground point under given NDC mouse coords. Returns the
    # TRUE world position (no prediction) so brush callbacks stay accurate.
    # The ring's visible transform is offset by a 1-frame velocity prediction
    # to mask render latency at high mouse speeds.
    def update(self, mouse_ndc: tuple[float, float]) -> WorldPos | None:
        if self._transform is None or self._viewport_api is None:
            return None
        # Re-read the active plane's pose every frame so manipulator-driven
        # moves / rotations of the plane prim show up immediately on the
        # cursor (raycast, ring orient, and dot grid all use these cached
        # basis vectors). Cheap: one ComputeLocalToWorldTransform + a cross.
        self._refresh_active_plane()
        if self._orient_transform is not None:
            try:
                self._orient_transform.transform = self._compute_orient_matrix()
            except Exception:
                pass
        p = self._raycast(mouse_ndc)
        if p is None:
            return None
        self._world_pos = p
        # Predict where the cursor will be when this frame is actually shown,
        # using a smoothed NDC velocity. Stationary mouse => zero offset.
        predicted_ndc = self._update_velocity_and_predict(mouse_ndc)
        visual_p: WorldPos = p
        if predicted_ndc is not None:
            pp = self._raycast(predicted_ndc)
            if pp is not None:
                visual_p = pp
        self._transform.transform = [
            1, 0, 0, 0,
            0, 1, 0, 0,
            0, 0, 1, 0,
            float(visual_p[0]), float(visual_p[1]), float(visual_p[2]), 1,
        ]
        return p

    # Update the smoothed NDC velocity from the latest sample and return the
    # predicted NDC position one frame ahead, or None on the first sample.
    def _update_velocity_and_predict(
        self, mouse_ndc: tuple[float, float]
    ) -> tuple[float, float] | None:
        now = time.perf_counter()
        last = self._last_ndc
        last_t = self._last_ndc_t
        self._last_ndc = mouse_ndc
        self._last_ndc_t = now
        if last is None:
            self._vel_ndc = (0.0, 0.0)
            return None
        dt = now - last_t
        if dt <= 1e-6:
            return None
        inst_vx = (mouse_ndc[0] - last[0]) / dt
        inst_vy = (mouse_ndc[1] - last[1]) / dt
        # Exponential smoothing so single noisy samples don't kick the ring.
        s = _VELOCITY_SMOOTHING
        vx = s * self._vel_ndc[0] + (1.0 - s) * inst_vx
        vy = s * self._vel_ndc[1] + (1.0 - s) * inst_vy
        self._vel_ndc = (vx, vy)
        # Speed-dependent lead: faster mouse => lead further ahead, up to a
        # frame-or-two cap. Stationary mouse => zero lead (no jitter/overshoot).
        speed = (vx * vx + vy * vy) ** 0.5
        ahead = speed * _PREDICT_AHEAD_PER_NDC_PER_S
        if ahead > _PREDICT_MAX_AHEAD_S:
            ahead = _PREDICT_MAX_AHEAD_S
        ox = vx * ahead
        oy = vy * ahead
        # Hard absolute clamp so direction reversals don't fling the ring
        # across the viewport.
        mag2 = ox * ox + oy * oy
        max2 = _PREDICT_MAX_NDC * _PREDICT_MAX_NDC
        if mag2 > max2 and mag2 > 0.0:
            scale = (max2 / mag2) ** 0.5
            ox *= scale
            oy *= scale
        return (mouse_ndc[0] + ox, mouse_ndc[1] + oy)

    @property
    def world_pos(self) -> WorldPos | None:
        return self._world_pos

    @property
    def up_axis(self) -> str:
        return self._up_axis

    # World positions of every grid point inside the ring at the current cursor center.
    def grid_world_positions(self) -> list[WorldPos]:
        if self._world_pos is None:
            return []
        return self.grid_world_positions_at(self._world_pos)

    # Same, but at an arbitrary world center (used to interpolate brush stamps along motion).
    # Grid points (which live in the Arc's local XY plane) are mapped into
    # world space via the active plane's basis: world = center + x*R + y*U.
    # This guarantees the rendered dot grid and the brush stamp grid always
    # coincide on the ACTIVE plane, regardless of its orientation.
    def grid_world_positions_at(self, center: WorldPos) -> list[WorldPos]:
        cx, cy, cz = center
        rv = cast(Any, self._basis_r)
        uv = cast(Any, self._basis_u)
        rx, ry, rz = float(rv[0]), float(rv[1]), float(rv[2])
        ux, uy, uz = float(uv[0]), float(uv[1]), float(uv[2])
        out: list[WorldPos] = []
        append = out.append
        for x, y, _z in self._grid_points:
            append((
                cx + x * rx + y * ux,
                cy + x * ry + y * uy,
                cz + x * rz + y * uz,
            ))
        return out

    @staticmethod
    def radius() -> float:
        return _RADIUS

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
        # Generic plane intersection: solve nrm . (n + t*(f - n)) = nrm . O for t.
        # The active drawing plane is finite as a rectangle but represents
        # an INFINITE plane, so we don't clamp t to the rectangle's extent.
        nrm = cast(Any, self._normal)
        org = cast(Any, self._origin)
        nx, ny, nz = float(n[0]), float(n[1]), float(n[2])
        fx, fy, fz = float(f[0]), float(f[1]), float(f[2])
        pnx, pny, pnz = float(nrm[0]), float(nrm[1]), float(nrm[2])
        pox, poy, poz = float(org[0]), float(org[1]), float(org[2])
        denom = pnx * (fx - nx) + pny * (fy - ny) + pnz * (fz - nz)
        if abs(denom) < 1e-9:
            return None  # ray is parallel to the plane
        num = pnx * (pox - nx) + pny * (poy - ny) + pnz * (poz - nz)
        t = num / denom
        if t < 0.0:
            return None  # plane is behind the camera near-plane
        return (nx + (fx - nx) * t, ny + (fy - ny) * t, nz + (fz - nz) * t)

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
