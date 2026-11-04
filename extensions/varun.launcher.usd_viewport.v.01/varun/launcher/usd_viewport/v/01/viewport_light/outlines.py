"""Thin, depth-tested polygon edges in a removable Light viewport session layer."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from omni.usd import editor
from pxr import Gf, Sdf, Tf, Usd, UsdGeom, UsdShade

_SURFACES: set[str] = {"Mesh", "Cube", "Sphere", "Cylinder", "Cone", "Capsule", "Plane", "NurbsPatch", "PointInstancer"}
_SOURCE: str = Path(__file__).with_name("outlines.glslfx").read_text(encoding="utf-8")


def _excluded(prim: Any) -> bool:
    """Keep live, saved and legacy drawing strokes out of the edge pass."""
    if not prim:
        return False
    if prim.HasAttribute("primvars:paintMode"):
        return True
    ancestor: Any = prim
    while ancestor and not ancestor.IsPseudoRoot():
        name: str = str(ancestor.GetName())
        if (
            name == "ThreeDDraw"
            or name.startswith("ViewportLight_")
            or bool(ancestor.GetCustomDataByKey("tool_box:drawingStroke"))
            or editor.is_hide_in_stage_window(ancestor)
        ):
            return True
        ancestor = ancestor.GetParent()
    return False


def _affected(source: str, changes: set[str]) -> bool:
    changed: str
    for changed in changes:
        if source == changed or source.startswith(changed.rstrip("/") + "/"):
            return True
    return False


class GeometryOutlines:
    """Reference live surfaces without changing their geometry or materials."""

    def __init__(self, viewport_api: Any, color: tuple[float, float, float, float]) -> None:
        self._api: Any = viewport_api
        self._color: tuple[float, float, float, float] = color
        self._stage: Any = None
        self._layer: Any = None
        self._material: Any = None
        self._shader: Any = None
        self._notice: Any = None
        self._path: str = ""
        self._sources: dict[str, str] = {}
        self._prototypes: dict[str, tuple[str, ...]] = {}
        self._dirty: set[str] = set()
        self._animated: set[str] = set()
        self._scan: bool = False
        self._updating: bool = False
        self._time: Any = None
        self._next_id: int = 0

    def set_color(self, color: tuple[float, float, float, float]) -> None:
        self._color = color
        if self._shader is not None:
            with cast(Any, Usd).EditContext(self._stage, self._layer):
                self._shader.CreateInput("outlineColor", Sdf.ValueTypeNames.Color3f).Set(
                    Gf.Vec3f(0.0 if color[0] > 0.3 else 1.0)
                )

    def _attach(self, stage: Any) -> None:
        self._stage = stage
        self._path = "/ViewportLight_Outlines"
        while stage.GetPrimAtPath(self._path):
            self._path += "_"
        self._layer = cast(Any, Sdf.Layer).CreateAnonymous("viewport_light_outlines.usda")
        stage.GetSessionLayer().subLayerPaths.insert(0, self._layer.identifier)
        with cast(Any, Usd).EditContext(stage, self._layer):
            root: Any = cast(Any, UsdGeom.Scope).Define(stage, self._path).GetPrim()
            editor.set_hide_in_stage_window(root, True)
            editor.set_no_delete(root, True)
            root.SetCustomDataByKey("viewportLight:overlay", True)
            self._material = cast(Any, UsdShade.Material).Define(stage, self._path + "/Material")
            self._shader = cast(Any, UsdShade.Shader).Define(stage, self._path + "/Material/Shader")
            self._shader.SetSourceCode(_SOURCE, "glslfx")
            self._material.CreateSurfaceOutput("glslfx").ConnectToSource(self._shader.ConnectableAPI(), "surface")
        self.set_color(self._color)
        self._notice = Tf.Notice.Register(Usd.Notice.ObjectsChanged, self._on_changed, stage)
        self._scan = True

    def _on_changed(self, notice: Any, _stage: Any) -> None:
        if self._updating:
            return
        path: Any
        for path in (*notice.GetResyncedPaths(), *notice.GetChangedInfoOnlyPaths()):
            prim_path: str = str(path.GetPrimPath())
            if prim_path.startswith("/ViewportLight_") or "ThreeDDraw" in prim_path.split("/"):
                continue
            prim: Any = self._stage.GetPrimAtPath(prim_path)
            if prim and prim.IsA(UsdGeom.Camera):
                continue
            if not path.IsPropertyPath():
                self._scan = True
                self._dirty.add(prim_path)
            elif str(path.name).startswith(("xformOp", "visibility", "purpose")):
                self._dirty.add(prim_path)
            elif str(path.name) == "prototypes":
                self._scan = True

    def update(self) -> None:
        stage: Any = self._api.stage
        if stage != self._stage:
            self.destroy()
            if stage is not None:
                self._attach(stage)
        if stage is None:
            return
        time: Any = self._api.time
        if time != self._time:
            self._dirty.update(self._animated)
            self._time = time
        if not self._scan and not self._dirty:
            return
        self._updating = True
        try:
            with cast(Any, Usd).EditContext(stage, self._layer):
                if self._scan:
                    self._discover()
                cache: Any = cast(Any, UsdGeom.XformCache)(time)
                source: str
                path: str
                for source, path in self._sources.items():
                    if _affected(source, self._dirty):
                        self._sync(stage.GetPrimAtPath(source), path, cache, time)
            self._dirty.clear()
            self._scan = False
        finally:
            self._updating = False

    def _discover(self) -> None:
        found: set[str] = set()
        prims: list[Any] = []
        iterator: Any = iter(cast(Any, Usd.PrimRange).Stage(self._stage, cast(Any, Usd).TraverseInstanceProxies()))
        prototype_roots: set[str] = set()
        prim: Any
        for prim in iterator:
            if _excluded(prim):
                iterator.PruneChildren()
            elif str(prim.GetTypeName()) in _SURFACES:
                prims.append(prim)
        target: Any
        for prim in prims:
            if prim.IsA(UsdGeom.PointInstancer) and not _excluded(prim):
                for target in UsdGeom.PointInstancer(prim).GetPrototypesRel().GetForwardedTargets():
                    prototype_roots.add(str(target))
        for prim in prims:
            source: str = str(prim.GetPath())
            if _affected(source, prototype_roots):
                continue
            found.add(source)
            path: str = self._sources.get(source, "")
            if not path:
                path = f"{self._path}/Object_{self._next_id}"
                self._next_id += 1
                self._sources[source] = path
                cast(Any, UsdGeom.Xform).Define(self._stage, path)
                overlay: Any = self._stage.DefinePrim(path + "/Surface", prim.GetTypeName())
                overlay.GetReferences().AddInternalReference(prim.GetPath())
                overlay.SetInstanceable(False)
                overlay.CreateAttribute("xformOpOrder", Sdf.ValueTypeNames.TokenArray).Set([])
                cast(Any, UsdShade.MaterialBindingAPI).Apply(overlay).Bind(self._material, "strongerThanDescendants")
                self._api.usd_context.set_pickable(path + "/Surface", False)
                self._dirty.add(source)
            # Each source gets its own overlay, so referenced children must not draw twice.
            child: Any
            for child in prim.GetAllChildren():
                self._stage.OverridePrim(path + "/Surface/" + str(child.GetName())).SetActive(False)
            if prim.IsA(UsdGeom.PointInstancer):
                self._sync_prototypes(prim, path)
        source: str
        for source in self._sources.keys() - found:
            path = self._sources.pop(source)
            self._api.usd_context.set_pickable(path + "/Surface", True)
            self._stage.RemovePrim(path)
            self._animated.discard(source)
            self._prototypes.pop(source, None)

    def _sync_prototypes(self, prim: Any, path: str) -> None:
        """Keep instanced copies at their instance positions, without stray prototype surfaces."""
        source: str = str(prim.GetPath())
        target: Any
        target_paths: list[str] = []
        for target in UsdGeom.PointInstancer(prim).GetPrototypesRel().GetForwardedTargets():
            target_paths.append(str(target))
        targets: tuple[str, ...] = tuple(target_paths)
        if self._prototypes.get(source) == targets:
            return
        self._stage.RemovePrim(path + "/Prototypes")
        cloned: list[str] = []
        index: int
        original: str
        for index, original in enumerate(targets):
            clone_path: str = f"{path}/Prototypes/Prototype_{index}"
            clone: Any = self._stage.DefinePrim(clone_path)
            clone.GetReferences().AddInternalReference(original)
            cast(Any, UsdShade.MaterialBindingAPI).Apply(clone).Bind(self._material, "strongerThanDescendants")
            if _excluded(self._stage.GetPrimAtPath(original)):
                cast(Any, UsdGeom.Imageable)(clone).CreateVisibilityAttr("invisible")
            cloned.append(clone_path)
        overlay: Any = cast(Any, UsdGeom.PointInstancer)(self._stage.GetPrimAtPath(path + "/Surface"))
        overlay.CreatePrototypesRel().SetTargets(cloned)
        self._prototypes[source] = targets

    def _sync(self, prim: Any, path: str, cache: Any, time: Any) -> None:
        source: str = str(prim.GetPath())
        resource: Any = self._stage.GetPrimAtPath(path)
        imageable: Any = cast(Any, UsdGeom.Imageable)(prim)
        resource.CreateAttribute("xformOp:transform", Sdf.ValueTypeNames.Matrix4d).Set(
            cache.GetLocalToWorldTransform(prim)
        )
        resource.CreateAttribute("xformOpOrder", Sdf.ValueTypeNames.TokenArray).Set(["xformOp:transform"])
        cast(Any, UsdGeom.Imageable)(resource).CreateVisibilityAttr().Set(imageable.ComputeVisibility(time))
        cast(Any, UsdGeom.Imageable)(resource).CreatePurposeAttr().Set(imageable.ComputePurpose())
        self._animated.discard(source)
        ancestor: Any = prim
        while ancestor and not ancestor.IsPseudoRoot():
            xformable: Any = cast(Any, UsdGeom.Xformable)(ancestor)
            visibility: Any = ancestor.GetAttribute("visibility")
            if (xformable and xformable.TransformMightBeTimeVarying()) or (
                visibility and visibility.ValueMightBeTimeVarying()
            ):
                self._animated.add(source)
                break
            ancestor = ancestor.GetParent()

    def destroy(self) -> None:
        if self._notice is not None:
            self._notice.Revoke()
            self._notice = None
        if self._stage is not None and self._layer is not None:
            session: Any = self._stage.GetSessionLayer()
            if self._layer.identifier in session.subLayerPaths:
                session.subLayerPaths.remove(self._layer.identifier)
            path: str
            for path in self._sources.values():
                self._api.usd_context.set_pickable(path + "/Surface", True)
        self._stage = None
        self._layer = None
        self._material = None
        self._shader = None
        self._path = ""
        self._sources.clear()
        self._prototypes.clear()
        self._dirty.clear()
        self._animated.clear()
        self._scan = False
        self._time = None
