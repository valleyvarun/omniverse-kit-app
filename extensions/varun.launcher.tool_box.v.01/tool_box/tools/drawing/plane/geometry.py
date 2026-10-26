"""Resolve drawing-plane equations from live USD transforms."""

from __future__ import annotations

import logging
from typing import Any, ClassVar, cast

from pxr import Gf, UsdGeom

from tool_box.core.viewport import get_active_stage

LOGGER: logging.Logger = logging.getLogger(__name__)


class PlaneRegistry:
    _planes: ClassVar[dict[str, tuple[Gf.Vec3d, Gf.Vec3d]]] = {}

    # Cache the plane's initial origin and normal.
    @classmethod
    def register(cls, path: str, origin: Gf.Vec3d, normal: Gf.Vec3d) -> None:
        cls._planes[path] = (Gf.Vec3d(origin), Gf.Vec3d(normal))

    # Remove a plane from the geometry cache.
    @classmethod
    def unregister(cls, path: str) -> None:
        cls._planes.pop(path, None)

    # Release cached plane geometry when the toolbox shuts down.
    @classmethod
    def clear(cls) -> None:
        cls._planes.clear()

    # Return a plane's cached geometry if registered.
    @classmethod
    def info(cls, path: str) -> tuple[Gf.Vec3d, Gf.Vec3d] | None:
        return cls._planes.get(path)

    # Live (origin, normal) for `path`, derived from the prim's world xform
    # so it tracks user manipulation. Falls back to the registered values
    # if the prim is missing or its transform can't be evaluated.
    @classmethod
    def info_for_path(
        cls, path: str, viewport_api: Any = None
    ) -> tuple[Gf.Vec3d, Gf.Vec3d] | None:
        try:
            stage: Any = get_active_stage(viewport_api)
            if stage is not None:
                prim: Any = stage.GetPrimAtPath(path)
                if prim and prim.IsValid():
                    xformable: Any = cast(Any, UsdGeom).Xformable(prim)
                    xf: Any = xformable.ComputeLocalToWorldTransform(0)
                    origin: Gf.Vec3d = xf.Transform(Gf.Vec3d(0.0, 0.0, 0.0))
                    # Mesh is built in the XZ plane, so its local normal is +Y.
                    normal: Any = xf.TransformDir(Gf.Vec3d(0.0, 1.0, 0.0))
                    length: float = normal.GetLength()
                    if length > 1e-9:
                        return (Gf.Vec3d(origin), Gf.Vec3d(normal / length))
        except Exception:
            LOGGER.debug("Could not resolve drawing plane %s", path, exc_info=True)
        return None
