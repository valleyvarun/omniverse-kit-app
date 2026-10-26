"""Run with Kit --exec; writes captures and a JSON report beside the native build."""

# Python & library imports
import asyncio
import json
import time
import traceback
from pathlib import Path
from typing import Any, cast

# Omniverse & USD imports
import carb.settings
import omni.kit.app
import omni.kit.renderer.capture
import omni.ui as ui
import omni.usd
from omni.kit.viewport.utility import (
    capture_viewport_to_file,
    frame_viewport_prims,
    get_active_viewport_window,
)
from pxr import Gf, Sdf, UsdGeom, UsdLux

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

ROOT = "/exts/varun.launcher.usd_viewport.v.01/viewportLight"
THEME = "/persistent/exts/varun.launcher.usd_viewport.v.01/viewportLight/theme"
OUTPUT = Path(__file__).resolve().parents[1] / "_build" / "validation"


# ----------------------------------------------------------------------
# 2. HELPER FUNCTIONS
# ----------------------------------------------------------------------


# Check themes, renderer switching, and preservation of scene data.
async def run():
    # Prepare report output and asynchronous test helpers.
    app: Any = omni.kit.app.get_app()
    report = {}
    OUTPUT.mkdir(parents=True, exist_ok=True)

    # Allow stage and viewport callbacks to settle.
    async def settle(count=20):
        for _ in range(count):
            await app.next_update_async()

    # Capture the composed viewport window.
    async def capture(name):
        interface = omni.kit.renderer.capture.acquire_renderer_capture_interface()
        interface.capture_next_frame_swapchain(str(OUTPUT / f"{name}.png"))
        await settle()

    # Wait for a renderer transition with a timeout.
    async def wait_until(predicate, message):
        deadline = time.monotonic() + 60
        while not predicate():
            assert time.monotonic() < deadline, message
            await app.next_update_async()
        await settle(5)

    # Copy authored asset content for before-and-after comparison.
    def asset_text(stage):
        snapshot = Sdf.Layer.CreateAnonymous()
        Sdf.CopySpec(stage.GetRootLayer(), "/World", snapshot, "/World")
        return snapshot.ExportToString()

    try:
        # --- 1) PREPARE A DISPOSABLE TEST SCENE ---

        await settle(40)
        settings = carb.settings.get_settings()
        for name in ("Render Settings",):
            panel = ui.Workspace.get_window(name)
            if panel:
                panel.visible = False
        assert settings.get(f"{ROOT}/ready"), "Native viewport policy did not load"
        window = get_active_viewport_window()
        assert window is not None
        window.visible = True
        window.width, window.height = 1180, 760
        window.position_x, window.position_y = 0, 28
        api = window.viewport_api
        context = api.usd_context
        await context.new_stage_async()
        await settle()
        stage = context.get_stage()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
        UsdGeom.Xform.Define(stage, "/World")
        cube = UsdGeom.Cube.Define(stage, "/World/Cube")
        cube.CreateSizeAttr(2)
        UsdGeom.Xformable(cube).AddTranslateOp().Set(Gf.Vec3d(-2, 1, 0))
        sphere = UsdGeom.Sphere.Define(stage, "/World/Sphere")
        UsdGeom.Xformable(sphere).AddTranslateOp().Set(Gf.Vec3d(1, 1, 0))
        cylinder = UsdGeom.Cylinder.Define(stage, "/World/Cylinder")
        cylinder.CreateAxisAttr("Y")
        cylinder.CreateHeightAttr(2)
        UsdGeom.Xformable(cylinder).AddTranslateOp().Set(Gf.Vec3d(4, 1, 0))
        UsdLux.DistantLight.Define(stage, "/World/UserLight").CreateIntensityAttr(100)
        await settle()
        frame_viewport_prims(api, ["/World"])
        context.get_selection().set_selected_prim_paths(["/World/Cube"], True)
        await settle(30)
        root_before = stage.GetRootLayer().ExportToString()
        asset_before = asset_text(stage)
        camera_before = api.camera_path
        selection_before = context.get_selection().get_selected_prim_paths()

        # --- 2) CHECK LIGHT RENDERING AND THEMES ---

        report["default_engine"] = api.hydra_engine
        report["default_delegate"] = api.render_mode
        report["available_renderers"] = settings.get("/pxr/renderers")
        report["rendered_frame"] = api.frame_info.get("frame_number")
        report["alpha_settings"] = [
            settings.get("/rtx/post/backgroundZeroAlpha/" + name) for name in ("enabled", "outputAlphaInComposite")
        ]
        assert api.hydra_engine == "pxr" and api.render_mode == "HdStormRendererPlugin"
        assert settings.get(f"{ROOT}/active")
        assert (
            UsdGeom.Imageable(stage.GetPrimAtPath("/World/UserLight")).ComputeVisibility() == UsdGeom.Tokens.invisible
        )
        for value in ("light", "dark"):
            settings.set(THEME, value)
            await settle(30)
            assert settings.get(f"{ROOT}/studio") == f"{'grey' if value == 'light' else 'dark'}_studio"
            await capture(value)
            raw_capture = capture_viewport_to_file(api, str(OUTPUT / f"{value}_raw.png"))
            await raw_capture.wait_for_result()
        report["theme_switching"] = True
        assert stage.GetRootLayer().ExportToString() == root_before
        report["themes_leave_root_unchanged"] = True

        # --- 3) CHECK MENU CHOICES AND RENDERER SWITCHING ---

        from omni.kit.viewport.menubar.render import get_instance

        renderers = [
            r.displayName for _, g in cast(Any, get_instance())._render_menu.render_list.renderers for r in g.renderers
        ]
        assert "Viewport Light" in renderers
        assert any(name.startswith("RTX -") for name in renderers)
        report["menu_choices"] = renderers

        settings.set("/renderer/active", "rtx")
        settings.set("/rtx/rendermode", "RaytracedLighting")
        api.set_hd_engine("rtx", "RaytracedLighting")
        await wait_until(
            lambda: api.hydra_engine == "rtx" and not settings.get(f"{ROOT}/active"), "RTX switch timed out"
        )
        assert not settings.get(f"{ROOT}/active")
        assert not any("viewport_light_studio" in p for p in stage.GetSessionLayer().subLayerPaths)
        await capture("rtx")
        settings.set("/renderer/active", "pxr")
        settings.set("/pxr/rendermode", "HdStormRendererPlugin")
        api.set_hd_engine("pxr", "HdStormRendererPlugin")
        await wait_until(
            lambda: api.hydra_engine == "pxr" and settings.get(f"{ROOT}/active"), "Viewport Light switch timed out"
        )
        assert settings.get(f"{ROOT}/active")
        assert api.camera_path == camera_before
        assert context.get_selection().get_selected_prim_paths() == selection_before
        root_after = stage.GetRootLayer().ExportToString()
        (OUTPUT / "root_before.usda").write_text(root_before)
        (OUTPUT / "root_after.usda").write_text(root_after)
        # Kit's RTX bridge writes its render-product settings under /Render.
        # Our studio must leave the authored asset and lights untouched.
        assert asset_text(stage) == asset_before
        assert "ViewportLight_Studio" not in root_after
        report["renderer_round_trip"] = True
        report["asset_layer_unchanged"] = True
        report["camera_and_selection_preserved"] = True

        # --- 4) CHECK STAGE REPLACEMENT ---

        await context.new_stage_async()
        await settle(25)
        assert settings.get(f"{ROOT}/active")
        report["stage_replacement"] = True
        report["passed"] = True
    except Exception:
        report["passed"] = False
        report["error"] = traceback.format_exc()
    finally:
        # Save the report and exit with a matching result code.
        (OUTPUT / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print("VIEWPORT_LIGHT_REPORT " + json.dumps(report, default=str), flush=True)
        # These are disposable test stages. Close them explicitly so a host
        # launcher with a save-on-exit prompt can also run this test unattended.
        await cast(Any, omni.usd.get_context()).close_stage_async()
        app.post_quit(0 if report.get("passed") else 1)


# ----------------------------------------------------------------------
# 3. MODULE SETUP
# ----------------------------------------------------------------------

asyncio.ensure_future(run())
