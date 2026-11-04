"""Create translucent USD planes and select them for drawing."""

from __future__ import annotations

import logging
from typing import Any, cast

from pxr import Gf, Sdf, UsdGeom, UsdShade, Vt

from tool_box.core.tool import Tool
from tool_box.core.viewport import get_active_stage

from .geometry import PlaneRegistry

LOGGER = logging.getLogger(__name__)


class DrawingPlane:
    LABEL = "DP"  # Icon text shown on the toolbar button.
    NAME = "Drawing Plane"
    BASE_PATH = "/World/DrawingPlanes/DrawingPlane"
    SIZE = 100.0  # Half-extent of the square plane in stage units.
    COLOR = (0.6, 0.2, 0.9)  # Purple.
    OPACITY = 0.5  # 50% see-through.

    # Start numbering newly created drawing planes.
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

    # --- DRAWING PLANE CREATION ---

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
        mesh.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(-size, 0.0, -size), Gf.Vec3f(size, 0.0, size)]))
        # Display color is a fallback for the Storm/Hydra display; RTX uses the bound material.
        mesh.CreateDisplayColorAttr([Gf.Vec3f(*self.COLOR)])
        mesh.CreateDisplayOpacityAttr([self.OPACITY])
        mesh.CreateDoubleSidedAttr(True)

        # Bind a translucent UsdPreviewSurface so RTX renders the plane see-through.
        self._bind_translucent_material(stage, mesh, prim_path)

        # The mesh lies in its local XZ plane (normal +Y). On Z-up stages, tip it
        # 90 degrees about X with Kit's standard ops so it still lies flat.
        normal: Gf.Vec3d = Gf.Vec3d(0.0, 1.0, 0.0)
        if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z:
            xformable: Any = UsdGeom.Xformable(mesh)
            xformable.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, 0.0))
            xformable.AddRotateXYZOp().Set(Gf.Vec3f(90.0, 0.0, 0.0))
            xformable.AddScaleOp().Set(Gf.Vec3f(1.0, 1.0, 1.0))
            normal = Gf.Vec3d(0.0, 0.0, 1.0)

        # Stash the plane's equation in memory so the cursor / 3D paint / 3D pipe tool
        # can render and stamp on it. PlaneRegistry.info_for_path() will re-derive
        # these from the prim's world xform whenever the user moves or rotates the plane.
        PlaneRegistry.register(prim_path, Gf.Vec3d(0.0, 0.0, 0.0), normal)

        from tool_box.tools.drawing.plane.selection import PlaneSelection

        PlaneSelection.get().set_active_plane(prim_path)
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
