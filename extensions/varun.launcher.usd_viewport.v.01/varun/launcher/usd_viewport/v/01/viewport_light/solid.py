"""Temporary raster depth and height cues for Storm's default solid surfaces."""

from pathlib import Path
from typing import Any, cast

from omni.usd import editor
from pxr import Gf, Sdf, Tf, Usd, UsdGeom, UsdShade

_SOURCE: str = Path(__file__).with_name("solid.glslfx").read_text(encoding="utf-8")


class SolidShading:
    """Keep all appearance opinions in a removable session layer."""

    def __init__(self, viewport_api: Any) -> None:
        self._api: Any = viewport_api
        self._stage: Any = None
        self._layer: Any = None
        self._resource_stage: Any = None
        self._material: Any = None
        self._shader: Any = None
        self._notice: Any = None
        self._path: str = ""
        self._bound_roots: set[str] = set()
        self._material_outputs: set[str] = set()
        self._metrics: tuple[str, float] | None = None
        self._dirty: bool = False
        self._updating: bool = False

    # Camera movement is handled entirely by the shader's built-in view matrices.
    def update(self) -> None:
        stage: Any = self._api.stage
        if stage != self._stage:
            self.destroy()
            if stage is not None:
                self._attach(stage)
        if self._dirty:
            self._refresh()

    # Create one shared fallback material without touching the user's edit target.
    def _attach(self, stage: Any) -> None:
        self._stage = stage
        self._path = "/ViewportLight_Solid"
        while stage.GetPrimAtPath(self._path):
            self._path += "_"
        self._layer = cast(Any, Sdf.Layer).CreateAnonymous("viewport_light_solid.usda")
        self._resource_stage = cast(Any, Usd.Stage).Open(self._layer)
        root: Any = cast(Any, UsdGeom.Scope).Define(self._resource_stage, self._path).GetPrim()
        editor.set_hide_in_stage_window(root, True)
        editor.set_no_delete(root, True)
        self._material = cast(Any, UsdShade.Material).Define(self._resource_stage, self._path + "/Material")
        self._shader = cast(Any, UsdShade.Shader).Define(self._resource_stage, self._path + "/Material/Shader")
        self._shader.SetSourceCode(_SOURCE, "glslfx")
        self._material.CreateSurfaceOutput("glslfx").ConnectToSource(self._shader.ConnectableAPI(), "surface")
        stage.GetSessionLayer().subLayerPaths.insert(0, self._layer.identifier)
        self._notice = Tf.Notice.Register(Usd.Notice.ObjectsChanged, self._on_changed, stage)
        self._dirty = True

    # Ignore animation and geometry values; only topology, bindings and metadata need a refresh.
    def _on_changed(self, notice: Any, _stage: Any) -> None:
        if self._updating:
            return
        path: Any
        for path in notice.GetResyncedPaths():
            if not path.IsPropertyPath() and not str(path).startswith(self._path + "/"):
                self._dirty = True
                return
        for path in (*notice.GetResyncedPaths(), *notice.GetChangedInfoOnlyPaths()):
            name: str = str(path.name)
            if path == Sdf.Path.absoluteRootPath or name.startswith(("material:binding", "outputs:")):
                self._dirty = True
                return

    # Inspect opinions beneath our layer so later user bindings take precedence.
    def _authored_elsewhere(self, property_value: Any) -> bool:
        if not property_value:
            return False
        spec: Any
        for spec in property_value.GetPropertyStack():
            if spec.layer != self._layer:
                return True
        return False

    # Remove just the owned property, preserving other overrides below this path.
    def _remove_override(self, path: str, property_name: str) -> None:
        prim: Any = self._resource_stage.GetPrimAtPath(path)
        prim.RemoveProperty(property_name)
        if property_name == "material:binding":
            prim.ClearMetadata("apiSchemas")
        if not prim.GetAuthoredProperties() and not prim.GetAllChildren():
            self._resource_stage.RemovePrim(path)

    # Inherit the fallback at roots; explicit raster materials keep their own shading.
    def _refresh(self) -> None:
        self._updating = True
        self._dirty = False
        try:
            metrics: tuple[str, float] = (
                str(UsdGeom.GetStageUpAxis(self._stage)),
                float(UsdGeom.GetStageMetersPerUnit(self._stage)),
            )
            if metrics != self._metrics:
                up: Gf.Vec3f = Gf.Vec3f(0, 0, 1) if metrics[0] == "Z" else Gf.Vec3f(0, 1, 0)
                self._shader.CreateInput("studioUp", Sdf.ValueTypeNames.Float3).Set(up)
                self._shader.CreateInput("studioMetersPerUnit", Sdf.ValueTypeNames.Float).Set(metrics[1])
                self._metrics = metrics
            roots: set[str] = set()
            outputs: set[str] = set()
            path: str
            prim: Any
            for prim in self._stage.GetPseudoRoot().GetChildren():
                if str(prim.GetPath()) == self._path or editor.is_hide_in_stage_window(prim):
                    continue
                # A root's authored bindings must remain authoritative, including collections.
                has_binding: bool = False
                relationship: Any
                for relationship in prim.GetRelationships():
                    if str(relationship.GetName()).startswith("material:binding") and self._authored_elsewhere(
                        relationship
                    ):
                        has_binding = True
                        break
                if has_binding:
                    continue
                path = str(prim.GetPath())
                roots.add(path)
                if path not in self._bound_roots:
                    override: Any = self._resource_stage.OverridePrim(path)
                    cast(Any, UsdShade.MaterialBindingAPI).Apply(override).Bind(self._material)
            for path in self._bound_roots - roots:
                self._remove_override(path, "material:binding")
            self._bound_roots = roots

            # MDL-only assets also use Storm's fallback today. Give that fallback the same cues.
            for prim in self._stage.Traverse():
                if str(prim.GetPath()).startswith(self._path + "/") or not prim.IsA(UsdShade.Material):
                    continue
                material: Any = UsdShade.Material(prim)
                if (
                    self._authored_elsewhere(material.GetSurfaceOutput("glslfx").GetAttr())
                    or material.ComputeSurfaceSource()[0]
                ):
                    continue
                path = str(prim.GetPath())
                outputs.add(path)
                if path not in self._material_outputs:
                    override_material: Any = UsdShade.Material(self._resource_stage.OverridePrim(path))
                    override_material.CreateSurfaceOutput("glslfx").ConnectToSource(
                        self._shader.ConnectableAPI(), "surface"
                    )
            for path in self._material_outputs - outputs:
                self._remove_override(path, "outputs:glslfx:surface")
            self._material_outputs = outputs
        finally:
            self._updating = False

    # Renderer switches, stage changes and shutdown remove every temporary opinion.
    def destroy(self) -> None:
        if self._notice is not None:
            self._notice.Revoke()
            self._notice = None
        if self._stage is not None and self._layer is not None:
            session: Any = self._stage.GetSessionLayer()
            if self._layer.identifier in session.subLayerPaths:
                session.subLayerPaths.remove(self._layer.identifier)
        self._stage = None
        self._layer = None
        self._resource_stage = None
        self._material = None
        self._shader = None
        self._path = ""
        self._bound_roots.clear()
        self._material_outputs.clear()
        self._metrics = None
        self._dirty = False
