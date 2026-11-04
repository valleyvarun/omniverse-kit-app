"""Local, cached USD surface queries for drawing canvases."""

from typing import Any, cast

from pxr import Sdf, Tf, Usd

from tool_box.core.viewport import get_active_stage
from tool_box.tools.drawing.methods import Vec3
from tool_box.tools.drawing.surface import SceneSurfaces, SurfaceHit


class _StageSurfaces:
    def __init__(self, stage: Any) -> None:
        self.stage: Any = stage
        self.surfaces: SceneSurfaces | None = None
        self._overlay_roots: set[str] = set()
        root: Any
        for root in stage.GetPseudoRoot().GetChildren():
            if root.GetCustomDataByKey("viewportLight:overlay"):
                self._overlay_roots.add(str(root.GetPath()))
        self.notice: Any = cast(Any, Tf).Notice.Register(cast(Any, Usd).Notice.ObjectsChanged, self._changed, stage)

    # Remember marked ancestors so removing an overlay also leaves surface caches intact.
    def _is_overlay(self, path: Any) -> bool:
        ancestor_path: Any = path.GetPrimPath()
        while ancestor_path and ancestor_path != Sdf.Path.absoluteRootPath:
            text: str = str(ancestor_path)
            ancestor: Any = self.stage.GetPrimAtPath(ancestor_path)
            if ancestor:
                if ancestor.GetCustomDataByKey("viewportLight:overlay"):
                    self._overlay_roots.add(text)
                    return True
                # A user prim reusing a former helper path must invalidate normally.
                self._overlay_roots.discard(text)
            elif text in self._overlay_roots:
                return True
            ancestor_path = ancestor_path.GetParentPath()
        return False

    def _changed(self, notice: Any, _sender: Any) -> None:
        path: Any
        for path in (*notice.GetResyncedPaths(), *notice.GetChangedInfoOnlyPaths()):
            text: str = str(path.GetPrimPath())
            if text.startswith(("/World/ThreeDDraw", "/World/DrawingPlanes", "/__ToolBox", "/OmniverseKit_Persp")):
                continue
            if self._is_overlay(path):
                continue
            prim: Any = self.stage.GetPrimAtPath(path.GetPrimPath())
            if prim and prim.GetTypeName() in ("Camera", "Material", "Shader"):
                continue
            self.surfaces = None
            return

    def get(self) -> SceneSurfaces:
        if self.surfaces is None:
            self.surfaces = SceneSurfaces(self.stage)
        return self.surfaces

    def destroy(self) -> None:
        self.notice.Revoke()
        self.notice = None
        self.surfaces = None
        self._overlay_roots.clear()


_stages: dict[str, _StageSurfaces] = {}


def get_surfaces(viewport: Any = None, *, stage: Any = None) -> SceneSurfaces:
    resolved: Any = stage if stage is not None else get_active_stage(viewport)
    if resolved is None:
        raise ValueError("Surface drawing requires an open stage")
    key: str = str(resolved.GetRootLayer().identifier)
    entry: _StageSurfaces | None = _stages.get(key)
    if entry is None or entry.stage != resolved:
        if entry is not None:
            entry.destroy()
        entry = _StageSurfaces(resolved)
        _stages[key] = entry
    return entry.get()


def surface_hit(viewport: Any, origin: Vec3, direction: Vec3) -> SurfaceHit | None:
    if get_active_stage(viewport) is None:
        return None
    return get_surfaces(viewport).hit(origin, direction)


def destroy() -> None:
    entry: _StageSurfaces
    for entry in _stages.values():
        entry.destroy()
    _stages.clear()
