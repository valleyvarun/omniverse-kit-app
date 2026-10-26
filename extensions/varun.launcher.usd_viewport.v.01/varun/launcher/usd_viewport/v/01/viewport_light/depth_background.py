"""Preserve Storm transparency over the studio UI without coloring the background."""

from typing import Any, cast

from omni.usd import editor
from pxr import Sdf, Usd, UsdGeom, UsdShade

# Storm leaves translucent-only pixels at clear depth; Kit's final blit rejects them.
# Early tests write a depth a few float ULPs inside far clip, then discard all color.
_SHADER: str = """-- glslfx version 0.1
-- configuration
{"techniques":{"default":{
    "surfaceShader":{"source":["Depth.Surface"]},
    "displacementShader":{"source":["Depth.Displacement"]}
}}}
-- glsl Depth.Surface
layout(early_fragment_tests) in;
vec4 surfaceShader(vec4 Peye, vec3 Neye, vec4 color, vec4 patchCoord)
{ discard; return vec4(0.0); }
-- glsl Depth.Displacement
vec4 displacementShader(int index, vec4 Peye, vec3 Neye, vec4 patchCoord)
{
    return vec4(inverse(GetProjectionMatrix()) * vec4(HdGet_points(index).xy, 0.999999, 1.0));
}
"""


class DepthBackground:
    """Own two invisible, nonpickable triangles in a removable session sublayer."""

    def __init__(self, viewport_api: Any) -> None:
        self._api: Any = viewport_api
        self._stage: Any = None
        self._layer: Any = None
        self._path: str = ""

    # The shader follows each viewport's projection; camera moves need no USD writes.
    def update(self) -> None:
        stage: Any = self._api.stage
        if stage == self._stage:
            return
        self.destroy()
        if stage is None:
            return
        path: str = "/ViewportLight_Depth"
        while stage.GetPrimAtPath(path):
            path += "_"
        layer: Any = cast(Any, Sdf.Layer).CreateAnonymous("viewport_light_depth.usda")
        resource_stage: Any = cast(Any, Usd.Stage).Open(layer)
        root: Any = cast(Any, UsdGeom.Scope).Define(resource_stage, path).GetPrim()
        editor.set_hide_in_stage_window(root, True)
        editor.set_no_delete(root, True)
        mesh: Any = cast(Any, UsdGeom.Mesh).Define(resource_stage, path + "/Quad")
        mesh.CreateSubdivisionSchemeAttr().Set("none")
        mesh.CreateDoubleSidedAttr().Set(True)
        mesh.CreatePointsAttr().Set([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)])
        mesh.CreateFaceVertexCountsAttr().Set([3, 3])
        mesh.CreateFaceVertexIndicesAttr().Set([0, 1, 2, 0, 2, 3])
        # Empty bounds exclude this resource from framing and disable Storm frustum culling.
        mesh.CreateExtentAttr().Set([(1, 1, 1), (-1, -1, -1)])
        material: Any = cast(Any, UsdShade.Material).Define(resource_stage, path + "/Material")
        shader: Any = cast(Any, UsdShade.Shader).Define(resource_stage, path + "/Material/Shader")
        shader.SetSourceCode(_SHADER, "glslfx")
        material.CreateSurfaceOutput("glslfx").ConnectToSource(shader.ConnectableAPI(), "surface")
        cast(Any, UsdShade.MaterialBindingAPI).Apply(mesh.GetPrim()).Bind(material)
        self._stage, self._layer, self._path = stage, layer, path
        stage.GetSessionLayer().subLayerPaths.insert(0, layer.identifier)
        self._api.usd_context.set_pickable(path + "/Quad", False)

    # Remove only our own layer; user edits and their current edit target stay intact.
    def destroy(self) -> None:
        if self._stage is not None and self._layer is not None:
            session: Any = self._stage.GetSessionLayer()
            identifier: str = self._layer.identifier
            if identifier in session.subLayerPaths:
                session.subLayerPaths.remove(identifier)
            self._api.usd_context.set_pickable(self._path + "/Quad", True)
        self._stage, self._layer, self._path = None, None, ""
