"""Completed pipe snapping and a one-second, session-only recognition flash."""

from __future__ import annotations

import time
from typing import Any, cast

import numpy as np
import omni.kit.app
from pxr import Sdf, Tf, Usd, UsdGeom

from tool_box.tools.drawing.surface import (
    FloatArray,
    SceneSurfaces,
    SurfaceHit,
    Vec2,
    Vec3,
    is_pipe_mesh,
)

ORANGE: Vec3 = (1.0, 0.38, 0.03)


class PipeTargets:
    def __init__(self, stage: Any) -> None:
        self._stage: Any = stage
        self._surfaces: SceneSurfaces | None = None
        self._notice: Any = cast(Any, Tf).Notice.Register(cast(Any, Usd).Notice.ObjectsChanged, self._changed, stage)
        self._layer: Any = None
        self._subscription: Any = None
        self._entered: str | None = None
        self._expires: float = 0.0
        self._editing: bool = False

    def _changed(self, notice: Any, _sender: Any) -> None:
        if self._editing:
            return
        path: Any
        for path in (*notice.GetResyncedPaths(), *notice.GetChangedInfoOnlyPaths()):
            prim_path: str = str(path.GetPrimPath())
            if prim_path.startswith("/World/ThreeDDraw/_"):
                continue
            if path.IsPropertyPath() and str(path.name).startswith(("primvars:", "normals")):
                continue
            if prim_path in ("/", "/World", "/World/ThreeDDraw") or prim_path.startswith("/World/ThreeDDraw/Stroke"):
                self._surfaces = None
                return

    def hit(self, mouse: Vec2, ndc_to_world: Any, metric: FloatArray) -> SurfaceHit | None:
        if self._surfaces is None:
            self._surfaces = SceneSurfaces(self._stage, pipes_only=True)
        inverse: FloatArray = np.asarray(ndc_to_world, dtype=np.float64)

        def ray(position: Vec2) -> tuple[Vec3, Vec3]:
            ends: FloatArray = (
                np.asarray(((position[0], position[1], -1, 1), (position[0], position[1], 1, 1))) @ inverse
            )
            ends = ends[:, :3] / ends[:, 3:]
            direction: FloatArray = ends[1] - ends[0]
            return (float(ends[0, 0]), float(ends[0, 1]), float(ends[0, 2])), (
                float(direction[0]),
                float(direction[1]),
                float(direction[2]),
            )

        origin: Vec3
        direction: Vec3
        origin, direction = ray(mouse)
        hit: SurfaceHit | None = self._surfaces.hit(origin, direction)
        if hit is not None:
            return hit
        world_to_ndc: FloatArray = np.asarray(np.linalg.inv(inverse), dtype=np.float64)
        nearby: Vec2 | None = self._surfaces.near_brush(mouse, metric, world_to_ndc)
        if nearby is None:
            return None
        origin, direction = ray(nearby)
        return self._surfaces.hit(origin, direction)

    def recognize(self, hit: SurfaceHit | None) -> None:
        root: str | None = hit.path.rsplit("/", 1)[0] if hit is not None else None
        if root == self._entered:
            return
        self._entered = root
        if root is None:
            return
        self._editing = True
        try:
            if self._layer is None:
                self._layer = cast(Any, Sdf.Layer).CreateAnonymous("pipe-recognition.usda")
                self._stage.GetSessionLayer().subLayerPaths.insert(0, self._layer.identifier)
            self._layer.Clear()
            with cast(Any, Usd.EditContext)(self._stage, self._layer):
                prim: Any
                for prim in self._stage.GetPrimAtPath(root).GetChildren():
                    if is_pipe_mesh(prim):
                        color: Any = cast(Any, UsdGeom.Mesh)(prim).GetDisplayColorPrimvar()
                        color.Set([ORANGE])
                        color.SetInterpolation("constant")
            self._expires = time.monotonic() + 1.0
        finally:
            self._editing = False
        if self._subscription is None:
            self._subscription = (
                cast(Any, omni.kit.app.get_app())
                .get_update_event_stream()
                .create_subscription_to_pop(self._tick, name="tool_box.pipe_recognition")
            )

    def _tick(self, _event: Any) -> None:
        if time.monotonic() >= self._expires:
            self._clear()

    def _clear(self) -> None:
        if self._expires == 0.0:
            return
        self._editing = True
        try:
            if self._layer is not None:
                self._layer.Clear()
        finally:
            self._editing = False
        self._subscription = None
        self._expires = 0.0

    def reset(self) -> None:
        self._entered = None
        self._clear()

    def destroy(self) -> None:
        self.reset()
        self._notice.Revoke()
        self._notice = None
        if self._layer is not None:
            session: Any = self._stage.GetSessionLayer()
            if self._layer.identifier in session.subLayerPaths:
                session.subLayerPaths.remove(self._layer.identifier)
            self._layer = None
        self._surfaces = None
