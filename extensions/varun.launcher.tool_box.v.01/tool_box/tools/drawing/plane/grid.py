"""Register a camera-centered grid on each viewport's drawing plane."""

from __future__ import annotations

import logging
from typing import Any, cast

import omni.kit.app
from omni.kit.scene_view.opengl import ViewportOpenGLSceneView
from omni.ui import scene as sc
from pxr import Gf

from .geometry import PlaneRegistry

LOGGER = logging.getLogger(__name__)

_GRID_FACTORY_ID = "tool_box.plane_grid"

# CELL is world-units between adjacent grid lines. HALF is the lattice
# half-extent in world units around the camera. (2*HALF/CELL + 1) lines
# per axis.
_GRID_CELL = 50.0
_GRID_HALF = 500.0
_GRID_COLOR = (0.80, 0.70, 0.90, 0.55)  # Pale purple, near-white.
_GRID_THICKNESS = 1.0

# Every live _PlaneGridScene instance (one per viewport).
_grid_scenes: "set[_PlaneGridScene]" = set()


# ----------------------------------------------------------------------
# 5. HELPER FUNCTIONS
# ----------------------------------------------------------------------


# Build an orthonormal in-plane basis (right, up) given a unit normal.
# Right x up = normal.
def _basis_for_normal(normal: "Gf.Vec3d") -> "tuple[Gf.Vec3d, Gf.Vec3d]":
    nv: Any = normal
    if abs(float(nv[1])) < 0.9:
        ref = Gf.Vec3d(0.0, 1.0, 0.0)
    else:
        ref = Gf.Vec3d(1.0, 0.0, 0.0)
    right: Any = cast(Any, Gf).Cross(nv, ref)
    length = float(right.GetLength())
    if length < 1e-9:
        right = cast(Any, Gf).Cross(nv, Gf.Vec3d(0.0, 0.0, 1.0))
        length = float(right.GetLength())
        if length < 1e-9:
            return (Gf.Vec3d(1.0, 0.0, 0.0), Gf.Vec3d(0.0, 1.0, 0.0))
    right = right / length
    up: Any = cast(Any, Gf).Cross(nv, right)
    return (right, up)


# ----------------------------------------------------------------------
# 6. PLANE GRID SCENE
# ----------------------------------------------------------------------


# Per-viewport scene that draws the active drawing plane's grid. The grid
# is a fixed lattice of sc.Line segments in local XY; a single
# sc.Transform carries (a) the rotation that aligns local XY to the
# active plane's (right, up) basis and (b) a translation that snaps the
# grid to the nearest cell under the camera's projection onto the plane.
class _PlaneGridScene:
    # Create grid transforms and track the viewport and active plane.
    def __init__(self, desc: "dict[str, Any]") -> None:
        self._viewport_api: Any = desc.get("viewport_api")
        self._origin: Gf.Vec3d = Gf.Vec3d(0.0, 0.0, 0.0)
        self._normal: Gf.Vec3d = Gf.Vec3d(0.0, 1.0, 0.0)
        self._basis_r: Gf.Vec3d = Gf.Vec3d(1.0, 0.0, 0.0)
        self._basis_u: Gf.Vec3d = Gf.Vec3d(0.0, 0.0, 1.0)
        self._has_active_plane = False
        self._visible = True

        # Clip grid lines against this viewport's scene depth.
        self._scene_view: Any = ViewportOpenGLSceneView(self._viewport_api)
        with self._scene_view.scene:
            self._root: Any = sc.Transform(visible=False)
            with self._root:
                # Orient and center the grid on the active plane.
                self._xform: Any = sc.Transform()
                with self._xform:
                    self._build_lines()

        _grid_scenes.add(self)
        self.refresh_active_plane()

    # Release this grid scene and its transforms.
    def destroy(self) -> None:
        _grid_scenes.discard(self)
        if self._scene_view is not None:
            self._scene_view.destroy()
            self._scene_view = None
        self._root = None
        self._xform = None

    # This viewport layer has no nested layers.
    @property
    def layers(self) -> tuple[()]:
        return ()

    # Group this layer with viewport manipulators.
    @property
    def categories(self) -> tuple[str, ...]:
        return ("manipulator",)

    # Return the scene registration name.
    @property
    def name(self) -> str:
        return "DrawingPlaneGrid"

    # Read or update scene visibility.
    @property
    def visible(self) -> bool:
        return self._visible

    # Read or update scene visibility.
    @visible.setter
    def visible(self, value: bool) -> None:
        self._visible = bool(value)
        self._set_visible(self._visible and self._has_active_plane)

    # --- GRID GEOMETRY AND ACTIVE PLANE ---

    # Draw evenly spaced lines in the local grid plane.
    def _build_lines(self) -> None:
        n = int(_GRID_HALF / _GRID_CELL)
        extent = n * _GRID_CELL
        for i in range(-n, n + 1):
            v = i * _GRID_CELL
            sc.Line([-extent, v, 0.0], [extent, v, 0.0], color=_GRID_COLOR, thickness=_GRID_THICKNESS)
            sc.Line([v, -extent, 0.0], [v, extent, 0.0], color=_GRID_COLOR, thickness=_GRID_THICKNESS)

    # Resolve the selected plane's current position and orientation.
    def refresh_active_plane(self) -> None:
        from tool_box.tools.drawing.plane.selection import (
            DEFAULT_GROUND_PLANE_PATH,
            PlaneSelection,
        )

        registry = PlaneSelection.get()
        path = registry.active_plane(self._viewport_api) if registry is not None else DEFAULT_GROUND_PLANE_PATH
        if path == DEFAULT_GROUND_PLANE_PATH:
            self._set_visible(False)
            self._has_active_plane = False
            return
        info = PlaneRegistry.info_for_path(path, self._viewport_api)
        if info is None:
            self._set_visible(False)
            self._has_active_plane = False
            return
        origin, normal = info
        nrm: Any = normal
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
        self._has_active_plane = True
        self._set_visible(self._visible)
        self._update_transform()

    # Apply visibility to the scene root.
    def _set_visible(self, value: bool) -> None:
        if self._root is None:
            return
        try:
            self._root.visible = value
        except Exception:
            pass

    # Refresh the active plane and grid transform each frame.
    def tick(self) -> None:
        if self._xform is None:
            return
        # Re-read the active plane each frame so manipulator-driven moves /
        # rotations to the plane prim show up immediately. PlaneRegistry's
        # info_for_path() already pulls a fresh world xform every call.
        self.refresh_active_plane()

    # Read the camera position in world coordinates.
    def _camera_world_pos(self) -> "Gf.Vec3d | None":
        if self._viewport_api is None:
            return None
        try:
            xf: Any = getattr(self._viewport_api, "transform", None)
            if xf is None:
                return None
            return Gf.Vec3d(float(xf[12]), float(xf[13]), float(xf[14]))
        except Exception:
            return None

    # --- CAMERA-RELATIVE GRID PLACEMENT ---

    # Keep the plane grid aligned and centered near the camera.
    def _update_transform(self) -> None:
        cam = self._camera_world_pos()
        rv: Any = self._basis_r
        uv: Any = self._basis_u
        nv: Any = self._normal
        ov: Any = self._origin
        if cam is not None:
            cv: Any = cam
            d = (
                (float(cv[0]) - float(ov[0])) * float(nv[0])
                + (float(cv[1]) - float(ov[1])) * float(nv[1])
                + (float(cv[2]) - float(ov[2])) * float(nv[2])
            )
            px = float(cv[0]) - d * float(nv[0])
            py = float(cv[1]) - d * float(nv[1])
            pz = float(cv[2]) - d * float(nv[2])
            dx = px - float(ov[0])
            dy = py - float(ov[1])
            dz = pz - float(ov[2])
            ar = dx * float(rv[0]) + dy * float(rv[1]) + dz * float(rv[2])
            au = dx * float(uv[0]) + dy * float(uv[1]) + dz * float(uv[2])
            sr = round(ar / _GRID_CELL) * _GRID_CELL
            su = round(au / _GRID_CELL) * _GRID_CELL
            tx = float(ov[0]) + sr * float(rv[0]) + su * float(uv[0])
            ty = float(ov[1]) + sr * float(rv[1]) + su * float(uv[1])
            tz = float(ov[2]) + sr * float(rv[2]) + su * float(uv[2])
        else:
            tx, ty, tz = float(ov[0]), float(ov[1]), float(ov[2])
        try:
            self._xform.transform = [
                float(rv[0]),
                float(rv[1]),
                float(rv[2]),
                0.0,
                float(uv[0]),
                float(uv[1]),
                float(uv[2]),
                0.0,
                float(nv[0]),
                float(nv[1]),
                float(nv[2]),
                0.0,
                tx,
                ty,
                tz,
                1.0,
            ]
        except Exception:
            pass


# ----------------------------------------------------------------------
# 7. PLANE GRID MANAGER
# ----------------------------------------------------------------------


# Lifecycle owner: registers _PlaneGridScene with the viewport registry,
# subscribes to PlaneSelection for active-plane changes, and drives a
# per-frame tick so every grid follows its camera.
class PlaneGridManager:
    _instance: "PlaneGridManager | None" = None

    # Track grid registration and update subscriptions.
    def __init__(self) -> None:
        self._registration: Any = None
        self._layer_listener_cb: Any = None
        self._update_sub: Any = None

    # Register the grid scene and listen for plane and frame changes.
    def apply(self) -> None:
        try:
            from omni.kit.viewport.registry import RegisterViewportLayer

            self._registration = cast(Any, RegisterViewportLayer)(_PlaneGridScene, _GRID_FACTORY_ID)
        except Exception:
            LOGGER.exception("PlaneGridManager: failed to register viewport scene")
            return

        from tool_box.tools.drawing.plane.selection import PlaneSelection

        self._layer_listener_cb = self._on_layer_change
        registry = PlaneSelection.get()
        if registry is not None:
            registry.add_listener(self._layer_listener_cb)

        try:
            app: Any = cast(Any, omni.kit.app.get_app())
            stream: Any = getattr(app, "get_update_event_stream")()
            sub_fn: Any = getattr(stream, "create_subscription_to_pop")
            self._update_sub = sub_fn(self._on_update, name="tool_box.plane_grid.tick")
        except Exception:
            LOGGER.exception("PlaneGridManager: failed to subscribe to update stream")

        PlaneGridManager._instance = self

    # Release grid scenes, registration, and subscriptions.
    def destroy(self) -> None:
        if self._update_sub is not None:
            try:
                self._update_sub.unsubscribe()
            except Exception:
                pass
            self._update_sub = None
        if self._layer_listener_cb is not None:
            from tool_box.tools.drawing.plane.selection import PlaneSelection

            registry = PlaneSelection.get()
            if registry is not None:
                try:
                    registry.remove_listener(self._layer_listener_cb)
                except Exception:
                    pass
            self._layer_listener_cb = None
        if self._registration is not None:
            try:
                self._registration.destroy()
            except Exception:
                pass
            self._registration = None
        if PlaneGridManager._instance is self:
            PlaneGridManager._instance = None

    # Refresh all grids after the active drawing plane changes.
    def _on_layer_change(self) -> None:
        for s in list(_grid_scenes):
            try:
                s.refresh_active_plane()
            except Exception:
                pass

    # Update each live grid scene for the current frame.
    def _on_update(self, _evt: Any) -> None:
        for s in list(_grid_scenes):
            try:
                s.tick()
            except Exception:
                pass
