"""Finish paint meshes serially after drawing; USD edits stay on Kit's main thread."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from threading import Event
from typing import Any, cast

import numpy as np
import omni.kit.app
from numpy.typing import NDArray
from pxr import Sdf, Usd, UsdGeom, UsdShade, Vt

from tool_box.tools.drawing.brush import SHAPES, BrushSample, BrushShape
from tool_box.tools.drawing.points.canvas import (
    CANVAS_PATH,
    CanvasGeometry,
    PaintCanvas,
)
from tool_box.tools.drawing.points.mesh import (
    PaintMesh,
    StrokePoint,
    Vec3,
    ensure_paint_material,
)
from tool_box.tools.drawing.points.remesh import Surface, remesh

LOGGER: logging.Logger = logging.getLogger(__name__)


class PaintRepair:
    def __init__(self) -> None:
        self._pending: deque[tuple[Any, str]] = deque()
        self._task: asyncio.Future[None] | None = None
        self._cancel: Event = Event()

    def queue(self, stage: Any, path: str) -> None:
        self._pending.append((stage, path))
        if self._task is None or self._task.done():
            self._cancel = Event()
            self._task = asyncio.ensure_future(self._run())

    def open_stage(self, stage: Any) -> None:
        self.destroy()
        if stage is None:
            return
        root: Any = stage.GetPrimAtPath("/World/ThreeDDraw")
        if root:
            child: Any
            for child in root.GetChildren():
                if child.GetTypeName() == "Xform":
                    self.queue(stage, str(child.GetPath()))

    async def _run(self) -> None:
        try:
            while self._pending:
                await cast(Any, omni.kit.app.get_app()).next_update_async()
                stage: Any
                path: str
                stage, path = self._pending.popleft()
                try:
                    await self.repair(stage, path, self._cancel)
                except Exception:
                    LOGGER.exception("Could not remesh paint stroke %s; its original mesh is preserved", path)
        except (asyncio.CancelledError, InterruptedError):
            pass

    async def repair(self, stage: Any, path: str, cancellation: Event | None = None) -> bool:
        prim: Any = stage.GetPrimAtPath(path)
        if not prim or prim.GetAttribute("paint:surfaceVersion").Get() == 1:
            return False
        canvas: PaintCanvas | None = PaintCanvas.read(stage, prim)
        if prim.GetAttribute(CANVAS_PATH).Get() and canvas is None:
            return False
        sources: list[Any] = []
        child: Any
        for child in prim.GetChildren():
            if child.GetTypeName() == "Mesh" and child.GetAttribute("paint:centerline"):
                sources.append(child)
        if not sources:
            return False

        def source_path(item: Any) -> str:
            return str(item.GetPath())

        sources.sort(key=source_path)
        first: Any = sources[0]
        shape_text: str = str(first.GetAttribute("paint:shape").Get())
        if shape_text not in SHAPES:
            raise ValueError("Unknown saved brush shape")
        shape: BrushShape = cast(BrushShape, shape_text)
        right_value: Any = first.GetAttribute("paint:right").Get()
        up_value: Any = first.GetAttribute("paint:up").Get()
        right: Vec3 = (float(right_value[0]), float(right_value[1]), float(right_value[2]))
        up: Vec3 = (float(up_value[0]), float(up_value[1]), float(up_value[2]))
        samples: list[StrokePoint] = []
        for child in sources:
            center: Any
            radius: float
            hardness: float
            opacity: float
            for center, radius, hardness, opacity in zip(
                child.GetAttribute("paint:centerline").Get(),
                child.GetAttribute("paint:radius").Get(),
                child.GetAttribute("paint:hardness").Get(),
                child.GetAttribute("paint:opacity").Get(),
            ):
                position: Vec3 = (float(center[0]), float(center[1]), float(center[2]))
                sample: StrokePoint = (position, BrushSample(float(radius), float(hardness), float(opacity)))
                if not samples or sample != samples[-1]:
                    samples.append(sample)
        if not samples:
            return False
        centers: NDArray[np.float64] = np.asarray([sample[0] for sample in samples], dtype=np.float64)
        values: NDArray[np.float64] = np.asarray(
            [(sample[1].radius, sample[1].hardness, sample[1].opacity) for sample in samples], dtype=np.float64
        )
        basis: NDArray[np.float64] = np.asarray([right, up], dtype=np.float64)
        origin: NDArray[np.float64] = centers[0].copy()
        planar: NDArray[np.float64] = (centers - origin) @ basis.T
        surface: Surface = await asyncio.to_thread(remesh, planar, values, shape, cancellation)
        if (cancellation is not None and cancellation.is_set()) or not stage.GetPrimAtPath(path):
            return False
        positions: NDArray[np.float32] = (origin + surface.positions @ basis).astype(np.float32)
        # Prepare the complete replacement off-stage, then swap the visible meshes together.
        temporary: Any = cast(Any, Usd.Stage).CreateInMemory()
        mesh: Any = cast(Any, UsdGeom.Mesh).Define(temporary, "/Surface")
        mesh.CreateSubdivisionSchemeAttr().Set("none")
        mesh.CreateDoubleSidedAttr().Set(True)
        mesh.CreatePointsAttr().Set(cast(Any, Vt).Vec3fArray.FromNumpy(positions))
        mesh.CreateFaceVertexCountsAttr().Set([3] * (len(surface.indices) // 3))
        mesh.CreateFaceVertexIndicesAttr().Set(cast(Any, Vt).IntArray.FromNumpy(surface.indices))
        mesh.CreateDisplayColorAttr().Set([(0.0, 0.0, 0.0)])
        mesh.CreateExtentAttr().Set(
            cast(Any, Vt).Vec3fArray.FromNumpy(
                np.asarray([positions.min(axis=0), positions.max(axis=0)], dtype=np.float32)
            )
        )
        primvars: Any = cast(Any, UsdGeom.PrimvarsAPI)(mesh)
        coordinates: NDArray[np.float32] = np.column_stack(
            (surface.distance, np.zeros(len(positions), dtype=np.float32))
        )
        primvars.CreatePrimvar("paintCoord", Sdf.ValueTypeNames.Float2Array, "vertex").Set(
            cast(Any, Vt).Vec2fArray.FromNumpy(coordinates)
        )
        primvars.CreatePrimvar("paintDynamics", Sdf.ValueTypeNames.Float2Array, "vertex").Set(
            cast(Any, Vt).Vec2fArray.FromNumpy(surface.dynamics)
        )
        primvars.CreatePrimvar("paintMode", Sdf.ValueTypeNames.Float, "constant").Set(9.0)
        material: Any = ensure_paint_material(stage)
        cast(Any, UsdShade.MaterialBindingAPI).Apply(mesh.GetPrim()).Bind(material)
        source_writer: PaintMesh = PaintMesh(stage, path, right, up, shape)
        source_writer._write_source(mesh.GetPrim(), samples)
        if canvas is not None:
            projection: CanvasGeometry = await asyncio.to_thread(canvas.project_data, canvas.capture(mesh))
            if (cancellation is not None and cancellation.is_set()) or not stage.GetPrimAtPath(path):
                return False
            if not canvas.apply(mesh, projection):
                return False
        old_paths: list[Any] = [child.GetPath() for child in sources]
        with cast(Any, Usd).EditContext(stage, stage.GetRootLayer()):
            with cast(Any, Sdf).ChangeBlock():
                copied: bool = bool(
                    cast(Any, Sdf).CopySpec(
                        temporary.GetRootLayer(), "/Surface", stage.GetRootLayer(), path + "/PaintSurface"
                    )
                )
                if not copied:
                    raise RuntimeError("Could not install the completed paint surface")
                old_path: Any
                for old_path in old_paths:
                    stage.RemovePrim(old_path)
            prim.CreateAttribute("paint:surfaceVersion", Sdf.ValueTypeNames.Int, custom=True).Set(1)
        return True

    def destroy(self) -> None:
        self._cancel.set()
        self._pending.clear()
        if self._task is not None:
            self._task.cancel()
            self._task = None
