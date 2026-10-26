"""Visual regression for sharp joins, chunk boundaries, and native paint selection."""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import math
import traceback
from pathlib import Path
from typing import Any, cast

import carb.settings
import carb.windowing
import numpy as np
import omni.appwindow
import omni.kit.app
import omni.kit.renderer.capture
import PIL.Image
from numpy.typing import NDArray
from omni.kit.viewport.window import get_viewport_window_instances
from pxr import Gf, UsdGeom, UsdShade
from tool_box.extension import ToolBoxExtension
from tool_box.tools.drawing.brush import BrushSample
from tool_box.tools.drawing.points import mesh as paint_mesh
from tool_box.tools.drawing.points.repair import PaintRepair

OUTPUT: Path = Path(__file__).resolve().parents[4] / "_build/stroke_upgrade_checks/quality"


async def run() -> None:
    app: Any = omni.kit.app.get_app()
    report: dict[str, object] = {}
    OUTPUT.mkdir(parents=True, exist_ok=True)

    async def settle(count: int = 30) -> None:
        frame: int
        for frame in range(count):
            await app.next_update_async()

    async def capture(name: str) -> None:
        await settle()
        omni.kit.renderer.capture.acquire_renderer_capture_interface().capture_next_frame_swapchain(
            str(OUTPUT / f"{name}.png")
        )
        await settle(80)
        assert (OUTPUT / f"{name}.png").is_file()

    try:
        carb.settings.get_settings().set("/app/file/ignoreUnsavedOnExit", True)
        carb.windowing.acquire_windowing_interface().hide_window(omni.appwindow.get_default_app_window().get_window())
        await settle(120)
        api: Any = next(iter(get_viewport_window_instances(usd_context_name=None))).viewport_api
        api.resolution = (640, 480)
        stage: Any = api.stage
        assert api.render_mode == "HdStormRendererPlugin"
        assert carb.settings.get_settings().get("/app/runLoops/main/rateLimitFrequency") == 30
        camera: Any = cast(Any, UsdGeom.Camera).Define(stage, "/World/QualityCamera")
        camera.CreateProjectionAttr().Set("orthographic")
        camera.CreateHorizontalApertureAttr().Set(800.0)
        camera.CreateVerticalApertureAttr().Set(800.0)
        camera.CreateClippingRangeAttr().Set((0.1, 1000.0))
        camera.AddTransformOp().Set(
            Gf.Matrix4d().SetLookAt(Gf.Vec3d(0, 130, 0), Gf.Vec3d(0), Gf.Vec3d(0, 0, -1)).GetInverse()
        )
        api.camera_path = str(camera.GetPath())
        group: str
        chunk_size: int
        row: int
        index: int
        repair: PaintRepair = PaintRepair()
        for group, chunk_size in (("Reference", 8192), ("Chunked", 128)):
            cast(Any, UsdGeom.Xform).Define(stage, f"/World/Checks/{group}")
            paint_mesh.CHUNK_SEGMENTS = chunk_size
            for row in range(3):
                stroke: paint_mesh.PaintMesh = paint_mesh.PaintMesh(
                    stage, f"/World/Checks/{group}/Stroke_{row}", (1, 0, 0), (0, 0, -1), "circle"
                )
                for index in range(3600):
                    t: float = index / 3599
                    stroke.add(
                        (
                            -30 + 60 * t,
                            0,
                            (row - 1) * 16 + 4 * (2 / math.pi) * math.asin(math.sin(t * math.tau * (8 + row))),
                        ),
                        BrushSample(2.5, 0.25, 0.65),
                    )
                    if index % 30 == 0:
                        stroke.flush()
                stroke.flush()
                assert await repair.repair(stage, f"/World/Checks/{group}/Stroke_{row}")
                assert not await repair.repair(stage, f"/World/Checks/{group}/Stroke_{row}")
            cast(Any, UsdGeom.Imageable)(stage.GetPrimAtPath(f"/World/Checks/{group}")).MakeInvisible()
        paint_mesh.CHUNK_SEGMENTS = 128
        reference: Any = cast(Any, UsdGeom.Imageable)(stage.GetPrimAtPath("/World/Checks/Reference"))
        chunked: Any = cast(Any, UsdGeom.Imageable)(stage.GetPrimAtPath("/World/Checks/Chunked"))
        reference.MakeVisible()
        await capture("unbounded")
        reference.MakeInvisible()
        chunked.MakeVisible()
        await capture("chunked")
        await capture("idle")
        # A saved material with the old selection bug is refreshed on stage activation.
        shader: Any = cast(Any, UsdShade.Shader).Get(stage, "/World/ThreeDDraw/_PaintRibbonMaterial/Storm")
        source: str = str(shader.GetSourceCode("glslfx"))
        shader.SetSourceCode(source.replace("vec4(color.rgb, coverage)", "vec4(0.0, 0.0, 0.0, coverage)"), "glslfx")
        extension: ToolBoxExtension | None = None
        obj: Any
        for obj in gc.get_objects():
            if type(obj) is ToolBoxExtension:
                extension = obj
                break
        assert extension is not None
        extension._on_viewport_changed()
        assert shader.GetSourceCode("glslfx") == source
        api.usd_context.get_selection().set_selected_prim_paths(["/World/Checks/Chunked"], False)
        await capture("selected")
        # Compare the central drawing area; exclude toolbars and the Stage tree.
        captures: dict[str, NDArray[np.int16]] = {}
        name: str
        for name in ("unbounded", "chunked", "idle", "selected"):
            pixels: NDArray[np.int16] = np.asarray(
                cast(Any, PIL.Image).open(OUTPUT / f"{name}.png").convert("RGB"), dtype=np.int16
            )
            height: int = pixels.shape[0]
            width: int = pixels.shape[1]
            captures[name] = pixels[int(height * 0.15) : int(height * 0.875), int(width * 0.12) : int(width * 0.75)]
        delta: int = int(np.max(np.abs(captures["unbounded"] - captures["chunked"])))
        assert delta <= 2, f"Chunk boundary changed the rendered stroke: {delta}"
        assert np.array_equal(captures["chunked"], captures["idle"]), "Stationary strokes flicker"
        dark_pixels: int = int(np.count_nonzero(np.max(captures["chunked"], axis=2) < 20))
        assert dark_pixels == 0, f"Overlapping paint triangles: {dark_pixels} dark pixels"
        selected: NDArray[np.int16] = captures["selected"]
        orange: NDArray[np.bool_] = (selected[:, :, 0] > selected[:, :, 1] * 1.15) & (
            selected[:, :, 1] > selected[:, :, 2] * 1.3
        )
        assert int(np.count_nonzero(orange)) > 1000, "Selected paint is not orange"
        report["maximum_chunk_pixel_difference"] = delta
        report["idle_pixels_unchanged"] = True
        report["no_dark_join_triangles"] = True
        report["orange_selection"] = True
        report["saved_material_refresh"] = True
        report["completed_strokes_remeshed_once"] = True
        report["passed"] = True
    except Exception:
        logging.getLogger(__name__).exception("Stroke quality regression failed")
        report["error"] = traceback.format_exc()
        report["passed"] = False
    finally:
        paint_mesh.CHUNK_SEGMENTS = 128
        (OUTPUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report), flush=True)
        app.post_quit(0 if report.get("passed") else 1)


task: asyncio.Future[None] = asyncio.ensure_future(run())
