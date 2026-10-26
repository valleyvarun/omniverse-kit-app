"""Local, non-instanced long-stroke load test in a disposable drawing app."""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import math
import subprocess
import traceback
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol, cast

import carb.settings
import carb.windowing
import numpy as np
import omni.appwindow
import omni.kit.app
import omni.kit.renderer.capture
import psutil
from omni.kit.viewport.window import get_viewport_window_instances
from pxr import Gf, Sdf, Usd, UsdGeom
from tool_box.core.controller import ToolController
from tool_box.extension import ToolBoxExtension
from tool_box.tools.drawing.brush import BrushConfig, BrushSample
from tool_box.tools.drawing.curves.mesh import PipeMesh
from tool_box.tools.drawing.points.mesh import PaintMesh

OUTPUT: Path = Path(__file__).resolve().parents[4] / "_build/stroke_upgrade_checks/load"


class MemoryUsage(Protocol):
    rss: int
    vms: int


class ProcessInfo(Protocol):
    def memory_info(self) -> MemoryUsage: ...


async def run() -> None:
    app: Any = omni.kit.app.get_app()
    settings: Any = carb.settings.get_settings()
    report: dict[str, Any] = {"samples_per_stroke": 1000, "instancing": False, "pipe_fraction": 0.2, "checkpoints": []}
    process: ProcessInfo = cast(Any, psutil).Process()
    OUTPUT.mkdir(parents=True, exist_ok=True)

    async def settle(count: int = 30) -> None:
        frame: int
        for frame in range(count):
            await app.next_update_async()

    def save() -> None:
        (OUTPUT / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    try:
        settings.set("/app/file/ignoreUnsavedOnExit", True)
        carb.windowing.acquire_windowing_interface().hide_window(omni.appwindow.get_default_app_window().get_window())
        await settle(140)
        viewport: Any = next(iter(get_viewport_window_instances(usd_context_name=None)))
        api: Any = viewport.viewport_api
        stage: Any = api.stage
        assert api.render_mode == "HdStormRendererPlugin"
        assert not app.get_extension_manager().is_extension_enabled("omni.hydra.rtx")
        controller: ToolController | None = None
        obj: Any
        for obj in gc.get_objects():
            if type(obj) is ToolBoxExtension:
                controller = obj._controller
                break
        assert controller is not None
        cfg: BrushConfig = BrushConfig.get()
        cfg.set_brush_radius(0.5)
        cfg.set_hardness(0.5)
        cfg.set_opacity(0.85)
        report["resolution"] = list(api.resolution)
        report["renderer"] = api.render_mode
        camera: Any = cast(Any, UsdGeom.Camera).Define(stage, "/World/LoadCamera")
        camera.CreateProjectionAttr().Set("orthographic")
        camera.CreateClippingRangeAttr().Set((0.1, 100000.0))
        camera.CreateHorizontalApertureAttr().Set(8000.0)
        camera.CreateVerticalApertureAttr().Set(8000.0)
        camera_op: Any = camera.AddTransformOp()
        api.camera_path = str(camera.GetPath())
        # Create ordinary unique USD meshes, not instances or shared references.
        library: Any = cast(Any, Usd.Stage).CreateInMemory()
        variant: int
        index: int
        for variant in range(5):
            name: str = f"/Template{variant}"
            paint: PaintMesh | None = PaintMesh(library, name, (1, 0, 0), (0, 0, -1), "circle") if variant else None
            pipe: PipeMesh | None = None if variant else PipeMesh(library, name, (0, 0, 0), 0.5, (0, 1, 0))
            for index in range(1000):
                point: tuple[float, float, float] = (index * 0.12, 0.0, 3 * math.sin(index * 0.02 + variant))
                if paint is not None:
                    paint.add(point, BrushSample(0.5, 0.5, 0.85))
                elif pipe is not None:
                    pipe.add(point)
            if paint is not None:
                paint.flush()
            elif pipe is not None:
                pipe.flush()
        cast(Any, UsdGeom.Xform).Define(stage, "/World/ThreeDDraw")
        cast(Any, Sdf).CopySpec(
            library.GetRootLayer(),
            "/World/ThreeDDraw/_PaintRibbonMaterial",
            stage.GetRootLayer(),
            "/World/ThreeDDraw/_PaintRibbonMaterial",
        )
        template_vertices: dict[int, int] = {}
        template_meshes: dict[int, int] = {}
        prim: Any
        for variant in range(5):
            template_vertices[variant] = 0
            template_meshes[variant] = 0
            for prim in library.GetPrimAtPath(f"/Template{variant}").GetChildren():
                template_vertices[variant] += len(prim.GetAttribute("points").Get())
                template_meshes[variant] += 1
        report["template_vertices"] = template_vertices
        report["template_meshes"] = template_meshes
        total: int = 0
        vertex_total: int = 0
        mesh_total: int = 0
        target: int
        for target in (0, 100, 500, 1000, 2000, 3000, 5000, 8000, 10000):
            build_start: float = perf_counter()
            while total < target:
                batch_start: int = total
                with cast(Any, Sdf).ChangeBlock():
                    for index in range(min(10, target - total)):
                        variant = total % 5
                        path: str = f"/World/ThreeDDraw/Stroke_{total:05d}"
                        cast(Any, Sdf).CopySpec(
                            library.GetRootLayer(), f"/Template{variant}", stage.GetRootLayer(), path
                        )
                        # Set transforms after the change block closes.
                        total += 1
                        vertex_total += template_vertices[variant]
                        mesh_total += template_meshes[variant]
                for index in range(batch_start, total):
                    path = f"/World/ThreeDDraw/Stroke_{index:05d}"
                    transform: Any = cast(Any, UsdGeom.Xform)(stage.GetPrimAtPath(path))
                    column: int = index % 50
                    row: int = index // 50
                    transform.AddTranslateOp().Set((column * 125.0 - 3125.0, (index % 7) * 1.5, row * 12.0 - 600.0))
                await settle(1)
                if total % 100 == 0:
                    save()
                    print(f"LOAD progress {total} strokes RSS={process.memory_info().rss / 2**30:.2f} GiB", flush=True)
                if process.memory_info().vms > 10 * 2**30 or cast(Any, psutil).virtual_memory().available < 2.5 * 2**30:
                    report["stop_reason"] = "Memory headroom reached during construction"
                    break
            camera.CreateHorizontalApertureAttr().Set(65000.0)
            camera.CreateVerticalApertureAttr().Set(65000.0)
            camera_op.Set(
                Gf.Matrix4d().SetLookAt(Gf.Vec3d(0, 9000, 0), Gf.Vec3d(0, 0, 0), Gf.Vec3d(0, 0, -1)).GetInverse()
            )
            await settle(60)
            entry: dict[str, Any] = {
                "strokes": total,
                "mesh_prims": mesh_total,
                "vertices": vertex_total,
                "batch_build_s": perf_counter() - build_start,
                "rss_gib": process.memory_info().rss / 2**30,
                "private_gib": process.memory_info().vms / 2**30,
                "available_ram_gib": cast(Any, psutil).virtual_memory().available / 2**30,
            }
            view: str
            for view in ("overview", "close"):
                aperture: float = 65000.0 if view == "overview" else 1600.0
                camera.CreateHorizontalApertureAttr().Set(aperture)
                camera.CreateVerticalApertureAttr().Set(aperture)
                await settle(30)
                timings: list[float] = []
                frame: int
                last_number: int = int(api.frame_info.get("frame_number", 0))
                for frame in range(90):
                    x: float = math.sin(frame * 0.06) * (100.0 if view == "overview" else 10.0)
                    z: float = -600.0 + min(total // 100, 30) * 12 if view == "close" else 0.0
                    start: float = perf_counter()
                    camera_op.Set(
                        Gf.Matrix4d()
                        .SetLookAt(Gf.Vec3d(x, 9000, z), Gf.Vec3d(x, 0, z), Gf.Vec3d(0, 0, -1))
                        .GetInverse()
                    )
                    await settle(1)
                    while int(api.frame_info.get("frame_number", 0)) <= last_number:
                        assert perf_counter() - start < 15.0, "Renderer stopped advancing"
                        await settle(1)
                    last_number = int(api.frame_info.get("frame_number", 0))
                    timings.append((perf_counter() - start) * 1000)
                entry[view] = {
                    "median_ms": float(np.median(timings)),
                    "p95_ms": float(np.percentile(timings, 95)),
                    "fps": 1000 / float(np.median(timings)),
                }
            # Exercise the actual tool's live upload and final stroke registration.
            controller.points.activate()
            destination: str = controller.points._allocate_stroke_path(stage)
            draw_z: float = -600.0 + min(total // 100, 30) * 12
            controller.points._on_draw_begin((-60.0, 15.0, draw_z))
            draw_cpu: list[float] = []
            draw_frames: list[float] = []
            for index in range(180):
                start = perf_counter()
                controller.points._on_draw_change((-60.0 + index * 0.65, 15.0, draw_z + 4 * math.sin(index * 0.06)))
                controller.points._flush_mesh()
                draw_cpu.append((perf_counter() - start) * 1000.0)
                await settle(1)
                draw_frames.append((perf_counter() - start) * 1000.0)
            start = perf_counter()
            controller.points._on_draw_end()
            entry["finish_stroke_ms"] = (perf_counter() - start) * 1000.0
            assert stage.GetPrimAtPath(destination), "Live stroke was not registered"
            controller.cancel()
            entry["draw_cpu"] = {"median_ms": float(np.median(draw_cpu)), "p95_ms": float(np.percentile(draw_cpu, 95))}
            entry["draw_frame"] = {
                "median_ms": float(np.median(draw_frames)),
                "p95_ms": float(np.percentile(draw_frames, 95)),
            }
            if target in (1000, 5000, 8000, 10000):
                await settle(20)
                omni.kit.renderer.capture.acquire_renderer_capture_interface().capture_next_frame_swapchain(
                    str(OUTPUT / f"close_{target}.png")
                )
                await settle(60)
            stage.RemovePrim(destination)
            await settle(20)
            entry["frame_info"] = dict(api.frame_info)
            gpu: subprocess.CompletedProcess[str] = await asyncio.to_thread(
                subprocess.run,
                ["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader,nounits"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            entry["gpu_memory_mib"] = gpu.stdout.strip()
            if gpu.returncode == 0:
                fields: list[str] = gpu.stdout.strip().splitlines()[0].split(",")
                if float(fields[-1]) - float(fields[-2]) < 768.0:
                    report["stop_reason"] = "GPU memory headroom below 768 MiB"

            report["checkpoints"].append(entry)
            save()
            print("LOAD checkpoint " + json.dumps(entry, default=str), flush=True)
            if "stop_reason" in report or entry["overview"]["median_ms"] > 200.0:
                report.setdefault("stop_reason", "Overview frame time exceeded 200 ms")
                break
        report["passed"] = True
    except Exception:
        logging.getLogger(__name__).exception("Stroke load test failed")
        report["error"] = traceback.format_exc()
        report["passed"] = False
    finally:
        save()
        app.post_quit(0 if report.get("passed") else 1)


task: asyncio.Future[None] = asyncio.ensure_future(run())
