from __future__ import annotations

import logging
from typing import Any, cast

import omni.kit.app
from omni.ui import scene as sc
from pxr import Gf, Sdf, UsdGeom, UsdShade, Vt

from ..tool import Tool
from ...active_context import get_active_stage


LOGGER = logging.getLogger(__name__)


# In-memory record of every drawing plane's equation. The cursor and the 3D
# draw tool query this so they can render and stamp on the active plane
# (a drawing plane is a finite rendered rectangle but represents an
# INFINITE plane defined by (origin, normal)).
#
# Plane equation: N . (X - O) = 0  (i.e. N.X = N.O).
# `register` is called at plane creation with the plane's INITIAL world
# (origin, unit-normal). `info_for_path` re-derives a CURRENT (origin,
# normal) from the prim's world transform so the values stay correct if
# the user moves or rotates the plane after creation; it falls back to the
# registered values if the prim can't be inspected.
class PlaneRegistry:
    _planes: "dict[str, tuple[Gf.Vec3d, Gf.Vec3d]]" = {}

    @classmethod
    def register(cls, path: str, origin: "Gf.Vec3d", normal: "Gf.Vec3d") -> None:
        cls._planes[path] = (Gf.Vec3d(origin), Gf.Vec3d(normal))

    @classmethod
    def unregister(cls, path: str) -> None:
        cls._planes.pop(path, None)

    @classmethod
    def info(cls, path: str) -> "tuple[Gf.Vec3d, Gf.Vec3d] | None":
        return cls._planes.get(path)

    # Live (origin, normal) for `path`, derived from the prim's world xform
    # so it tracks user manipulation. Falls back to the registered values
    # if the prim is missing or its transform can't be evaluated.
    @classmethod
    def info_for_path(cls, path: str) -> "tuple[Gf.Vec3d, Gf.Vec3d] | None":
        try:
            stage = get_active_stage()
            if stage is not None:
                prim = stage.GetPrimAtPath(path)
                if prim and prim.IsValid():
                    xformable = cast(Any, UsdGeom).Xformable(prim)
                    xf = xformable.ComputeLocalToWorldTransform(0)
                    origin = xf.Transform(Gf.Vec3d(0.0, 0.0, 0.0))
                    # Mesh is built in the XZ plane, so its local normal is +Y.
                    normal = xf.TransformDir(Gf.Vec3d(0.0, 1.0, 0.0))
                    L = normal.GetLength()
                    if L > 1e-9:
                        return (Gf.Vec3d(origin), Gf.Vec3d(normal / L))
        except Exception:
            pass
        return cls._planes.get(path)


# Encapsulates the "Drawing Plane" tool behaviour.
class DrawingPlane:
    LABEL = "DP"             # Icon text shown on the toolbar button.
    NAME = "Drawing Plane"
    BASE_PATH = "/World/DrawingPlanes/DrawingPlane"
    SIZE = 100.0             # Half-extent of the square plane in stage units.
    COLOR = (0.6, 0.2, 0.9)  # Purple.
    OPACITY = 0.5            # 50% see-through.

    def __init__(self) -> None:
        self._counter = 0

    # Build a Tool descriptor that the toolbar can render.
    def make_tool(self) -> Tool:
        return Tool(
            name=self.NAME,
            icon_text=self.LABEL,
            tooltip="Create a purple drawing plane in the current stage.",
            on_click=self.create,
        )

    # Spawn one purple semi-transparent plane in the active stage.
    def create(self) -> None:
        stage = get_active_stage()
        if stage is None:
            LOGGER.warning("DrawingPlane: no active stage.")
            return

        prim_path = self._next_path(stage)
        mesh = cast(Any, UsdGeom.Mesh).Define(stage, prim_path)
        size = self.SIZE
        mesh.CreatePointsAttr(
            [
                Gf.Vec3f(-size, 0.0, -size),
                Gf.Vec3f(size, 0.0, -size),
                Gf.Vec3f(size, 0.0, size),
                Gf.Vec3f(-size, 0.0, size),
            ]
        )
        mesh.CreateFaceVertexCountsAttr([4])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        mesh.CreateExtentAttr(
            Vt.Vec3fArray([Gf.Vec3f(-size, 0.0, -size), Gf.Vec3f(size, 0.0, size)])
        )
        # Display color is a fallback for the Storm/Hydra display; RTX uses the bound material.
        mesh.CreateDisplayColorAttr([Gf.Vec3f(*self.COLOR)])
        mesh.CreateDisplayOpacityAttr([self.OPACITY])
        mesh.CreateDoubleSidedAttr(True)

        # Bind a translucent UsdPreviewSurface so RTX renders the plane see-through.
        self._bind_translucent_material(stage, mesh, prim_path)

        # Place the new plane into the active group, if any.
        try:
            from ...layers.layers import GroupRegistry
            groups = GroupRegistry.get()
            if groups is not None:
                groups.assign_new_stroke(prim_path)
        except Exception:
            pass

        # Stash the plane's equation in memory so the cursor / 3D draw tool
        # can render and stamp on it. Mesh is built in local XZ plane at
        # y=0, so the initial world (origin, normal) = ((0,0,0), (0,1,0)).
        # PlaneRegistry.info_for_path() will re-derive these from the prim's
        # world xform whenever the user moves or rotates the plane.
        PlaneRegistry.register(
            prim_path,
            Gf.Vec3d(0.0, 0.0, 0.0),
            Gf.Vec3d(0.0, 1.0, 0.0),
        )

        LOGGER.info("DrawingPlane: created %s", prim_path)

    # Create an OmniPBR (MDL) material with opacity and bind it to the mesh.
    # RTX honours UsdPreviewSurface opacity inconsistently; OmniPBR with
    # enable_opacity is the canonical Omniverse path for translucent surfaces.
    def _bind_translucent_material(self, stage: Any, mesh: Any, prim_path: str) -> None:
        shade = cast(Any, UsdShade)
        sdf = cast(Any, Sdf)
        material_path = f"{prim_path}/Material"
        shader_path = f"{material_path}/Shader"

        material = shade.Material.Define(stage, material_path)
        shader = shade.Shader.Define(stage, shader_path)

        # Point the shader at OmniPBR.mdl and the OmniPBR sub-identifier.
        shader.SetSourceAsset("OmniPBR.mdl", "mdl")
        shader.SetSourceAssetSubIdentifier("OmniPBR", "mdl")
        shader.GetImplementationSourceAttr().Set(shade.Tokens.sourceAsset)

        color = Gf.Vec3f(*self.COLOR)
        shader.CreateInput("diffuse_color_constant", sdf.ValueTypeNames.Color3f).Set(color)
        shader.CreateInput("diffuse_tint", sdf.ValueTypeNames.Color3f).Set(color)
        shader.CreateInput("enable_opacity", sdf.ValueTypeNames.Bool).Set(True)
        shader.CreateInput("enable_opacity_texture", sdf.ValueTypeNames.Bool).Set(False)
        shader.CreateInput("opacity_constant", sdf.ValueTypeNames.Float).Set(self.OPACITY)
        shader.CreateInput("opacity_mode", sdf.ValueTypeNames.Int).Set(1)  # 1 = Opacity
        shader.CreateInput("opacity_threshold", sdf.ValueTypeNames.Float).Set(0.0)
        shader.CreateInput("reflection_roughness_constant", sdf.ValueTypeNames.Float).Set(0.6)
        shader.CreateInput("metallic_constant", sdf.ValueTypeNames.Float).Set(0.0)

        # Connect the MDL surface output so RTX uses this material.
        material.CreateSurfaceOutput("mdl").ConnectToSource(shader.ConnectableAPI(), "out")
        material.CreateDisplacementOutput("mdl").ConnectToSource(shader.ConnectableAPI(), "out")
        material.CreateVolumeOutput("mdl").ConnectToSource(shader.ConnectableAPI(), "out")

        shade.MaterialBindingAPI(mesh.GetPrim()).Bind(material)

    # Pick a unique prim path so repeated clicks don't overwrite the previous plane.
    def _next_path(self, stage: Any) -> str:
        while True:
            path = f"{self.BASE_PATH}_{self._counter:03d}"
            self._counter += 1
            if not stage.GetPrimAtPath(Sdf.Path(path)).IsValid():
                return path


# ============================================================================
# Active-drawing-plane GRID
# ============================================================================
# Renders a finite purple grid lattice on whichever drawing plane is active
# in CLayers. The lattice is built once and re-anchored every frame to the
# camera's projection on the plane, snapped to the nearest cell, so a
# small mesh follows the camera and looks larger than it is.
# ----------------------------------------------------------------------------

_GRID_FACTORY_ID = "varun.launcher.ui.plane_grid"

# CELL is world-units between adjacent grid lines. HALF is the lattice
# half-extent in world units around the camera. (2*HALF/CELL + 1) lines
# per axis.
_GRID_CELL = 50.0
_GRID_HALF = 500.0
_GRID_COLOR = (0.80, 0.70, 0.90, 0.55)  # Pale purple, near-white.
_GRID_THICKNESS = 1.0

# Every live _PlaneGridScene instance (one per viewport).
_grid_scenes: "set[_PlaneGridScene]" = set()


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


# Per-viewport scene that draws the active drawing plane's grid. The grid
# is a fixed lattice of sc.Line segments in local XY; a single
# sc.Transform carries (a) the rotation that aligns local XY to the
# active plane's (right, up) basis and (b) a translation that snaps the
# grid to the nearest cell under the camera's projection onto the plane.
class _PlaneGridScene:
    def __init__(self, desc: "dict[str, Any]") -> None:
        self._viewport_api: Any = desc.get("viewport_api")
        self._origin: Gf.Vec3d = Gf.Vec3d(0.0, 0.0, 0.0)
        self._normal: Gf.Vec3d = Gf.Vec3d(0.0, 1.0, 0.0)
        self._basis_r: Gf.Vec3d = Gf.Vec3d(1.0, 0.0, 0.0)
        self._basis_u: Gf.Vec3d = Gf.Vec3d(0.0, 0.0, 1.0)
        self._has_active_plane = False
        # omni.kit.viewport.window's _SceneItem reads `visible` on the
        # scene instance during construction; without this attribute it
        # raises AttributeError and the layer fails to register.
        self._visible = True

        # Outer wrapper: visibility flips here when no plane is active.
        self._root: Any = sc.Transform(visible=False)
        with self._root:
            # Inner Transform: 4x4 updated every frame to (orient | translate).
            self._xform: Any = sc.Transform()
            with self._xform:
                self._build_lines()

        _grid_scenes.add(self)
        self.refresh_active_plane()

    def destroy(self) -> None:
        _grid_scenes.discard(self)
        self._root = None
        self._xform = None

    @property
    def categories(self) -> tuple[str, ...]:
        return ("manipulator",)

    @property
    def name(self) -> str:
        return "DrawingPlaneGrid"

    @property
    def visible(self) -> bool:
        return self._visible

    @visible.setter
    def visible(self, value: bool) -> None:
        self._visible = bool(value)
        self._set_visible(self._visible and self._has_active_plane)

    def _build_lines(self) -> None:
        n = int(_GRID_HALF / _GRID_CELL)
        extent = n * _GRID_CELL
        for i in range(-n, n + 1):
            v = i * _GRID_CELL
            sc.Line([-extent, v, 0.0], [extent, v, 0.0],
                    color=_GRID_COLOR, thickness=_GRID_THICKNESS)
            sc.Line([v, -extent, 0.0], [v, extent, 0.0],
                    color=_GRID_COLOR, thickness=_GRID_THICKNESS)

    def refresh_active_plane(self) -> None:
        from ...layers.layers import DEFAULT_GROUND_PLANE_PATH, LayerRegistry
        registry = LayerRegistry.get()
        path = registry.active_plane() if registry is not None else DEFAULT_GROUND_PLANE_PATH
        if path == DEFAULT_GROUND_PLANE_PATH:
            self._set_visible(False)
            self._has_active_plane = False
            return
        info = PlaneRegistry.info_for_path(path)
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
        self._set_visible(True)
        self._update_transform()

    def _set_visible(self, value: bool) -> None:
        if self._root is None:
            return
        try:
            self._root.visible = value
        except Exception:
            pass

    def tick(self) -> None:
        if self._xform is None:
            return
        # Re-read the active plane each frame so manipulator-driven moves /
        # rotations to the plane prim show up immediately. PlaneRegistry's
        # info_for_path() already pulls a fresh world xform every call.
        self.refresh_active_plane()

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
                float(rv[0]), float(rv[1]), float(rv[2]), 0.0,
                float(uv[0]), float(uv[1]), float(uv[2]), 0.0,
                float(nv[0]), float(nv[1]), float(nv[2]), 0.0,
                tx, ty, tz, 1.0,
            ]
        except Exception:
            pass


# Lifecycle owner: registers _PlaneGridScene with the viewport registry,
# subscribes to LayerRegistry for active-plane changes, and drives a
# per-frame tick so every grid follows its camera.
class PlaneGridManager:
    _instance: "PlaneGridManager | None" = None

    def __init__(self) -> None:
        self._registration: Any = None
        self._layer_listener_cb: Any = None
        self._update_sub: Any = None

    def apply(self) -> None:
        try:
            from omni.kit.viewport.registry import RegisterScene
            self._registration = cast(Any, RegisterScene)(_PlaneGridScene, _GRID_FACTORY_ID)
        except Exception:
            LOGGER.exception("PlaneGridManager: failed to register viewport scene")
            return

        from ...layers.layers import LayerRegistry
        self._layer_listener_cb = self._on_layer_change
        registry = LayerRegistry.get()
        if registry is not None:
            registry.add_listener(self._layer_listener_cb)

        try:
            app: Any = cast(Any, omni.kit.app.get_app())
            stream: Any = getattr(app, "get_update_event_stream")()
            sub_fn: Any = getattr(stream, "create_subscription_to_pop")
            self._update_sub = sub_fn(
                self._on_update, name="varun.launcher.ui.plane_grid.tick"
            )
        except Exception:
            LOGGER.exception("PlaneGridManager: failed to subscribe to update stream")

        PlaneGridManager._instance = self

    def destroy(self) -> None:
        if self._update_sub is not None:
            try:
                self._update_sub.unsubscribe()
            except Exception:
                pass
            self._update_sub = None
        if self._layer_listener_cb is not None:
            from ...layers.layers import LayerRegistry
            registry = LayerRegistry.get()
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

    def _on_layer_change(self) -> None:
        for s in list(_grid_scenes):
            try:
                s.refresh_active_plane()
            except Exception:
                pass

    def _on_update(self, _evt: Any) -> None:
        for s in list(_grid_scenes):
            try:
                s.tick()
            except Exception:
                pass
