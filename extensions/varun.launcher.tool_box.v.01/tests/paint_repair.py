"""USD integration checks; these also run without a viewport or GPU."""

from __future__ import annotations

import asyncio
from threading import Event
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray
from pxr import Usd, UsdGeom, UsdShade
from tool_box.tools.drawing.brush import SHAPES, BrushSample, BrushShape
from tool_box.tools.drawing.points.mesh import PaintMesh
from tool_box.tools.drawing.points.repair import PaintRepair


async def run_checks() -> dict[str, object]:
    stage: Any = cast(Any, Usd.Stage).CreateInMemory()
    repair: PaintRepair = PaintRepair()
    shape: BrushShape
    index: int
    for shape in SHAPES:
        path: str = "/World/ThreeDDraw/Stroke_" + shape
        preview: PaintMesh = PaintMesh(stage, path, (1, 0, 0), (0, 0, -1), shape)
        for index in range(300):
            t: float = index / 299 * 12.0
            preview.add((float(np.sin(t) * 8), 0.0, float(np.sin(2 * t) * 4)), BrushSample(2.5, 0.25, 0.65))
        preview.flush()
        child: Any
        original_samples: int = 0
        for child in stage.GetPrimAtPath(path).GetChildren():
            original_samples += len(child.GetAttribute("paint:centerline").Get())
        assert await repair.repair(stage, path)
        prim: Any = stage.GetPrimAtPath(path)
        children: list[Any] = list(prim.GetChildren())
        assert len(children) == 1 and children[0].GetName() == "PaintSurface"
        assert prim.GetAttribute("paint:surfaceVersion").Get() == 1
        surface: Any = children[0]
        mesh: Any = cast(Any, UsdGeom.Mesh)(surface)
        positions: NDArray[np.float64] = np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float64)
        indices: NDArray[np.int32] = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(), dtype=np.int32)
        assert indices.max() < len(positions) and len(indices) % 3 == 0
        assert np.isfinite(positions).all() and np.allclose(positions[:, 1], 0.0)
        assert 0 < len(surface.GetAttribute("paint:centerline").Get()) <= original_samples
        assert np.allclose(surface.GetAttribute("paint:opacity").Get(), 0.65)
        assert surface.GetAttribute("primvars:paintMode").Get() == 9.0
        material: Any = cast(Any, UsdShade.MaterialBindingAPI)(surface).ComputeBoundMaterial()[0]
        assert material and str(material.GetPath()) == "/World/ThreeDDraw/_PaintRibbonMaterial"
        snapshot: str = stage.GetRootLayer().ExportToString()
        assert not await repair.repair(stage, path)
        assert stage.GetRootLayer().ExportToString() == snapshot
    reopened: Any = cast(Any, Usd.Stage).CreateInMemory()
    assert reopened.GetRootLayer().ImportFromString(stage.GetRootLayer().ExportToString())
    for shape in SHAPES:
        assert not await repair.repair(reopened, "/World/ThreeDDraw/Stroke_" + shape)
    # An interrupted worker never removes the visible original stroke.
    preview = PaintMesh(stage, "/World/ThreeDDraw/Cancelled", (1, 0, 0), (0, 0, -1), "circle")
    preview.add((0, 0, 0), BrushSample(2.5, 0.25, 0.65))
    preview.flush()
    snapshot = stage.GetRootLayer().ExportToString()
    cancellation: Event = Event()
    cancellation.set()
    try:
        await repair.repair(stage, "/World/ThreeDDraw/Cancelled", cancellation)
    except InterruptedError:
        pass
    else:
        raise AssertionError("Cancellation was ignored")
    assert stage.GetRootLayer().ExportToString() == snapshot
    # Opening an older drawing queues its repair automatically and serially.
    repair.open_stage(stage)
    assert repair._task is not None
    await repair._task
    assert stage.GetPrimAtPath("/World/ThreeDDraw/Cancelled").GetAttribute("paint:surfaceVersion").Get() == 1
    repair.destroy()
    await asyncio.sleep(0)
    return {
        "passed": True,
        "shapes": list(SHAPES),
        "usd_roundtrip": True,
        "cancellation_preserves_original": True,
        "existing_strokes_repaired": True,
    }
