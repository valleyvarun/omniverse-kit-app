"""Render and validate temporary Light geometry outlines in an isolated Kit process."""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from pathlib import Path
from typing import Any, cast

import carb.settings
import omni.kit.app
import omni.kit.renderer.capture
from omni.kit.viewport.utility import (
    capture_viewport_to_file,
    get_active_viewport_window,
)
from pxr import Gf, Sdf, UsdGeom, UsdShade
from tool_box.tools.drawing.surface import SceneSurfaces

OUTPUT_SETTING: object = carb.settings.get_settings().get("/app/viewportLight/outlineTestOutput")
OUTPUT: Path = (
    Path(OUTPUT_SETTING)
    if isinstance(OUTPUT_SETTING, str) and OUTPUT_SETTING
    else Path(__file__).resolve().parents[1] / "_build" / "outlines"
)


async def run() -> None:
    app: Any = omni.kit.app.get_app()
    report: dict[str, Any] = {}
    OUTPUT.mkdir(parents=True, exist_ok=True)

    def checkpoint(phase: str) -> None:
        (OUTPUT / "outlines-progress.json").write_text(json.dumps({"phase": phase}), encoding="utf-8")

    async def settle(count: int = 30) -> None:
        frame: int
        for frame in range(count):
            await app.next_update_async()

    async def capture(name: str, api: Any) -> None:
        checkpoint("capture_" + name)
        await settle(40)
        result: Any = capture_viewport_to_file(api, str(OUTPUT / f"outline-{name}-raw.png"))
        await asyncio.wait_for(result.wait_for_result(), timeout=45.0)
        omni.kit.renderer.capture.acquire_renderer_capture_interface().capture_next_frame_swapchain(
            str(OUTPUT / f"outline-{name}.png")
        )
        await settle(12)

    try:
        checkpoint("startup")
        await settle(180)
        settings: Any = carb.settings.get_settings()
        settings.set("/app/file/ignoreUnsavedOnExit", True)
        window: Any = get_active_viewport_window()
        api: Any = window.viewport_api
        checkpoint("create_stage")
        await asyncio.wait_for(api.usd_context.new_stage_async(), timeout=45.0)
        checkpoint("populate")
        await settle()
        stage: Any = api.usd_context.get_stage()
        UsdGeom.SetStageUpAxis(stage, "Z")
        UsdGeom.SetStageMetersPerUnit(stage, 0.01)
        cube: Any = cast(Any, UsdGeom.Cube).Define(stage, "/World/Cube")
        cube.CreateSizeAttr(100)
        cube.AddTranslateOp().Set(Gf.Vec3d(-85, 0, 50))
        cube.CreateDisplayColorAttr([(0.6, 0.6, 0.6)])
        sphere: Any = cast(Any, UsdGeom.Sphere).Define(stage, "/World/Sphere")
        sphere.CreateRadiusAttr(55)
        sphere.AddTranslateOp().Set(Gf.Vec3d(65, 0, 55))
        plane: Any = cast(Any, UsdGeom.Mesh).Define(stage, "/World/Plane")
        plane.CreatePointsAttr([(-230, -130, 0), (230, -130, 0), (230, 160, 0), (-230, 160, 0)])
        plane.CreateFaceVertexCountsAttr([3, 3])
        plane.CreateFaceVertexIndicesAttr([0, 1, 2, 0, 2, 3])
        plane.CreateSubdivisionSchemeAttr("none")
        plane.CreateDoubleSidedAttr(True)
        plane.CreateDisplayColorAttr([(0.6, 0.6, 0.6)])
        pipe: Any = cast(Any, UsdGeom.Cylinder).Define(stage, "/World/ThreeDDraw/Stroke/Tube_0")
        pipe.CreateRadiusAttr(12)
        pipe.CreateHeightAttr(110)
        pipe.AddTranslateOp().Set(Gf.Vec3d(0, -90, 55))
        material: Any = cast(Any, UsdShade.Material).Define(stage, "/World/Material")
        shader: Any = cast(Any, UsdShade.Shader).Define(stage, "/World/Material/Shader")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.2, 0.5, 0.8))
        material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        cast(Any, UsdShade.MaterialBindingAPI).Apply(sphere.GetPrim()).Bind(material)
        camera: Any = cast(Any, UsdGeom.Camera).Define(stage, "/Camera")
        camera.CreateClippingRangeAttr((1.0, 10000.0))
        camera.CreateFocalLengthAttr(45)
        camera.AddTransformOp().Set(
            Gf.Matrix4d().SetLookAt(Gf.Vec3d(480, -650, 420), Gf.Vec3d(0, 0, 35), Gf.Vec3d(0, 0, 1)).GetInverse()
        )
        api.camera_path = "/Camera"
        root_before: str = stage.GetRootLayer().ExportToString()
        target_before: Any = stage.GetEditTarget().GetLayer()
        await settle(80)
        outlines: Any = window.viewport_widget.display_delegate._outlines
        assert set(outlines._sources) == {"/World/Cube", "/World/Sphere", "/World/Plane"}, outlines._sources
        root_before = stage.GetRootLayer().ExportToString()
        assert stage.GetEditTarget().GetLayer() == target_before
        assert UsdShade.MaterialBindingAPI(sphere).ComputeBoundMaterial()[0].GetPath() == material.GetPath()
        report["stroke_exclusion_materials_and_session_isolation"] = True
        await capture("light", api)
        assert stage.GetRootLayer().ExportToString() == root_before
        settings.set("/persistent/exts/varun.launcher.usd_viewport.v.01/viewportLight/theme", "dark")
        await capture("dark", api)
        report["theme_colors"] = list(outlines._shader.GetInput("outlineColor").Get())
        assert report["theme_colors"] == [1, 1, 1], report
        cube.GetOrderedXformOps()[0].Set(Gf.Vec3d(-85, 0, 85))
        await settle()
        helper: Any = stage.GetPrimAtPath(outlines._sources["/World/Cube"])
        assert helper.GetAttribute("xformOp:transform").Get().ExtractTranslation() == Gf.Vec3d(-85, 0, 85)
        report["geometry_move"] = True

        # Instance proxies, inherited visibility, live topology and transform changes.
        cast(Any, UsdGeom.Xform).Define(stage, "/Model")
        proto_cube: Any = cast(Any, UsdGeom.Cube).Define(stage, "/Model/Cube")
        proto_cube.CreateSizeAttr(20)
        instance: Any = cast(Any, UsdGeom.Xform).Define(stage, "/Instance").GetPrim()
        instance.GetReferences().AddInternalReference("/Model")
        instance.SetInstanceable(True)
        await settle()
        instance_source: str = "/Instance/Cube"
        assert instance_source in outlines._sources
        instance_overlay: Any = stage.GetPrimAtPath(outlines._sources[instance_source] + "/Surface")
        assert instance_overlay.GetAttribute("size").Get() == 20
        proto_cube.CreateSizeAttr(25)
        await settle()
        assert instance_overlay.GetAttribute("size").Get() == 25
        UsdGeom.Imageable(instance).CreateVisibilityAttr("invisible")
        await settle()
        assert UsdGeom.Imageable(instance_overlay).ComputeVisibility() == "invisible"
        report["instances_geometry_and_inherited_visibility"] = True
        deleted_path: str = outlines._sources["/Model/Cube"]
        stage.RemovePrim("/Model/Cube")
        await settle()
        assert "/Model/Cube" not in outlines._sources
        assert not stage.GetPrimAtPath(deleted_path)
        report["deletion_cleanup"] = True
        instancer: Any = cast(Any, UsdGeom.PointInstancer).Define(stage, "/Scatter")
        cast(Any, UsdGeom.Sphere).Define(stage, "/Scatter/Proto").CreateRadiusAttr(10)
        instancer.CreatePrototypesRel().SetTargets(["/Scatter/Proto"])
        instancer.CreateProtoIndicesAttr([0, 0])
        instancer.CreatePositionsAttr([(100, 100, 20), (140, 100, 20)])
        await settle()
        assert "/Scatter" in outlines._sources and "/Scatter/Proto" not in outlines._sources
        scatter: Any = UsdGeom.PointInstancer(stage.GetPrimAtPath(outlines._sources["/Scatter"] + "/Surface"))
        assert list(scatter.GetPositionsAttr().Get()) == list(instancer.GetPositionsAttr().Get())
        assert str(scatter.GetPrototypesRel().GetTargets()[0]).startswith(outlines._path)
        report["point_instancers"] = True
        surfaces: SceneSurfaces = SceneSurfaces(stage)
        surface_path: str
        for surface_path in surfaces._paths:
            assert not surface_path.startswith(outlines._path)
        report["outlines_not_drawing_canvases"] = True
        checkpoint("renderer_switch")
        # Switching renderers removes the entire display layer and restores it on return.
        if app.get_extension_manager().is_extension_enabled("omni.hydra.rtx"):
            old_layer: str = outlines._layer.identifier
            api.set_hd_engine("rtx", "RaytracedLighting")
            await settle(100)
            assert old_layer not in stage.GetSessionLayer().subLayerPaths
            api.set_hd_engine("pxr", "HdStormRendererPlugin")
            await settle(100)
            outlines = window.viewport_widget.display_delegate._outlines
            assert outlines._stage == stage and outlines._layer.identifier != old_layer
            report["renderer_round_trip"] = True
        else:
            report["renderer_round_trip"] = "RTX extension is not loaded in this test app"
        old_session: Any = stage.GetSessionLayer()
        old_layer = outlines._layer.identifier
        checkpoint("stage_cleanup")
        await asyncio.wait_for(api.usd_context.new_stage_async(), timeout=45.0)
        await settle(60)
        assert not old_session or old_layer not in old_session.subLayerPaths
        assert outlines._stage == api.stage and outlines._layer.identifier != old_layer
        report["stage_cleanup"] = True
        report["passed"] = True
        checkpoint("complete")
    except Exception:
        logging.getLogger(__name__).exception("Outline validation failed")
        report["error"] = traceback.format_exc()
        report["passed"] = False
    finally:
        (OUTPUT / "outlines-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        app.post_quit(0 if report.get("passed") else 1)


task: asyncio.Future[None] = asyncio.ensure_future(run())
