"""Temporary axis views for a placed cursor in the existing viewport."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any, cast

from pxr import Gf, Sdf, Usd, UsdGeom

from tool_box.tools.drawing.methods import AXES, Vec3

_cleanup_tasks: set[asyncio.Task[None]] = set()


@dataclass
class _ViewState:
    viewport: Any
    stage: Any
    layer: Any
    original_path: str
    original_transform: Any
    original_perspective: bool
    distance: float
    span: float
    far_clip: float
    camera_path: str
    cleanup_layers: tuple[Any, ...]


class CursorView:
    """Own removable session cameras; never edit the user's camera or root layer."""

    def __init__(self) -> None:
        self._views: dict[int, _ViewState] = {}

    def _capture(self, viewport: Any, position: Vec3) -> _ViewState | None:
        stage: Any = getattr(viewport, "stage", None)
        if stage is None:
            return None
        original_path: str = str(viewport.camera_path)
        original: Any = cast(Any, UsdGeom.Camera).Get(stage, original_path)
        transform: Any = (
            Gf.Matrix4d()
            .SetLookAt(Gf.Vec3d(position[0], position[1], position[2] + 100.0), Gf.Vec3d(*position), Gf.Vec3d(0, 1, 0))
            .GetInverse()
        )
        perspective: bool = True
        horizontal: float = 20.955
        focal: float = 50.0
        far_clip: float = 1000000.0
        if original:
            transform = original.ComputeLocalToWorldTransform(0)
            perspective = str(original.GetProjectionAttr().Get()) != "orthographic"
            horizontal = float(original.GetHorizontalApertureAttr().Get() or horizontal)
            focal = float(original.GetFocalLengthAttr().Get() or focal)
            clipping: Any = original.GetClippingRangeAttr().Get()
            if clipping is not None:
                far_clip = float(clipping[1])
        eye: Any = transform.ExtractTranslation()
        distance: float = max(1.0, math.dist((float(eye[0]), float(eye[1]), float(eye[2])), position))
        span: float = max(0.01, horizontal * distance / max(focal, 0.001) if perspective else horizontal * 0.1)
        layer: Any = cast(Any, Sdf.Layer).CreateAnonymous("toolbox-cursor-view.usda")
        path_prefix: str = f"/__ToolBoxCursorView_{id(viewport):x}"
        camera_path: str = path_prefix
        suffix: int = 0
        while stage.GetPrimAtPath(camera_path):
            suffix += 1
            camera_path = f"{path_prefix}_{suffix}"
        cleanup_layers: tuple[Any, ...] = (
            stage.GetRootLayer(),
            stage.GetEditTarget().GetLayer(),
            stage.GetSessionLayer(),
        )
        stage.GetSessionLayer().subLayerPaths.insert(0, layer.identifier)
        return _ViewState(
            viewport,
            stage,
            layer,
            original_path,
            transform,
            perspective,
            distance,
            span,
            far_clip,
            camera_path,
            cleanup_layers,
        )

    def switch(self, viewport: Any, axis: int, position: Vec3, side: int = 1) -> bool:
        """1/2/3 look from +/-X/Y/Z; 0 returns to a normal perspective view."""
        if axis not in (0, 1, 2, 3):
            raise ValueError("Cursor camera axis must be 0, 1, 2, or 3")
        key: int = id(viewport)
        state: _ViewState | None = self._views.get(key)
        if state is not None and state.stage != getattr(viewport, "stage", None):
            self._remove(state)
            del self._views[key]
            state = None
        if state is None:
            state = self._capture(viewport, position)
            if state is None:
                return False
            self._views[key] = state
        if axis == 0:
            return self.restore(viewport)
        direction: Vec3 = AXES[axis - 1]
        sign: float = -1.0 if side < 0 else 1.0
        eye: Gf.Vec3d = Gf.Vec3d(
            position[0] + direction[0] * state.distance * sign,
            position[1] + direction[1] * state.distance * sign,
            position[2] + direction[2] * state.distance * sign,
        )
        up: Gf.Vec3d = Gf.Vec3d(0, 0, -1) if axis == 2 else Gf.Vec3d(0, 1, 0)
        # Z-up stages keep Z upright in side views and Y up when looking down Z.
        if UsdGeom.GetStageUpAxis(state.stage) == UsdGeom.Tokens.z:
            up = Gf.Vec3d(0, 1, 0) if axis == 3 else Gf.Vec3d(0, 0, 1)
        transform: Any = Gf.Matrix4d().SetLookAt(eye, Gf.Vec3d(*position), up).GetInverse()
        resolution: Any = getattr(viewport, "resolution", (1, 1))
        aspect: float = max(float(resolution[0]), 1.0) / max(float(resolution[1]), 1.0)
        with cast(Any, Usd.EditContext)(state.stage, state.layer):
            camera: Any = cast(Any, UsdGeom.Camera).Define(state.stage, state.camera_path)
            camera.GetPrim().SetCustomDataByKey("omni:kit:hidden", True)
            camera.CreateProjectionAttr().Set("orthographic")
            camera.CreateHorizontalApertureAttr().Set(state.span * 10.0)
            camera.CreateVerticalApertureAttr().Set(state.span * 10.0 / aspect)
            camera.CreateClippingRangeAttr().Set((0.01, max(state.far_clip, state.distance * 20.0)))
            camera.CreateFocusDistanceAttr().Set(state.distance)
            camera.MakeMatrixXform().Set(transform)
            viewport.camera_path = state.camera_path
        self._clear_leaked_specs(state)
        return True

    def restore(self, viewport: Any) -> bool:
        state: _ViewState | None = self._views.get(id(viewport))
        if state is None:
            return False
        if state.original_perspective and state.stage.GetPrimAtPath(state.original_path):
            with cast(Any, Usd.EditContext)(state.stage, state.layer):
                viewport.camera_path = state.original_path
            self._remove(state)
            del self._views[id(viewport)]
            return True
        # A user who entered from an orthographic camera still gets perspective on 0.
        resolution: Any = getattr(viewport, "resolution", (1, 1))
        aspect: float = max(float(resolution[0]), 1.0) / max(float(resolution[1]), 1.0)
        with cast(Any, Usd.EditContext)(state.stage, state.layer):
            camera: Any = cast(Any, UsdGeom.Camera).Define(state.stage, state.camera_path)
            camera.GetPrim().SetCustomDataByKey("omni:kit:hidden", True)
            camera.CreateProjectionAttr().Set("perspective")
            camera.CreateHorizontalApertureAttr().Set(20.955)
            camera.CreateVerticalApertureAttr().Set(20.955 / aspect)
            camera.CreateFocalLengthAttr().Set(20.955 * state.distance / state.span)
            camera.CreateClippingRangeAttr().Set((0.01, max(state.far_clip, state.distance * 20.0)))
            camera.MakeMatrixXform().Set(state.original_transform)
            viewport.camera_path = state.camera_path
        self._clear_leaked_specs(state)
        return True

    @staticmethod
    def _clear_leaked_specs(state: _ViewState) -> None:
        # Native viewport code may author the helper outside our session edit context.
        edit: Any = cast(Any, Sdf.BatchNamespaceEdit)()
        edit.Add(state.camera_path, cast(Any, Sdf.Path).emptyPath)
        layer: Any
        for layer in state.cleanup_layers:
            if layer.GetPrimAtPath(state.camera_path):
                layer.Apply(edit)

    @staticmethod
    def _remove(state: _ViewState) -> None:
        try:
            import omni.kit.app

            loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
            app: Any = omni.kit.app.get_app()
        except (ImportError, RuntimeError):
            CursorView._finish_removal(state)
            return
        if app is None:
            CursorView._finish_removal(state)
            return
        task: asyncio.Task[None] = loop.create_task(CursorView._remove_after_updates(state, app))
        _cleanup_tasks.add(task)
        task.add_done_callback(_cleanup_tasks.discard)

    @staticmethod
    async def _remove_after_updates(state: _ViewState, app: Any) -> None:
        try:
            update: int
            for update in range(3):
                await app.next_update_async()
        finally:
            CursorView._finish_removal(state)

    @staticmethod
    def _finish_removal(state: _ViewState) -> None:
        session: Any = state.stage.GetSessionLayer()
        if state.layer.identifier in session.subLayerPaths:
            session.subLayerPaths.remove(state.layer.identifier)
        CursorView._clear_leaked_specs(state)

    def destroy(self) -> None:
        state: _ViewState
        for state in tuple(self._views.values()):
            if (
                getattr(state.viewport, "stage", None) == state.stage
                and str(state.viewport.camera_path) == state.camera_path
                and state.stage.GetPrimAtPath(state.original_path)
            ):
                with cast(Any, Usd.EditContext)(state.stage, state.layer):
                    state.viewport.camera_path = state.original_path
            self._remove(state)
        self._views.clear()
