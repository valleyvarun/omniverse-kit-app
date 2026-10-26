"""Raster-only integration check, executed by Kit through run_compatibility.py."""

# Python & library imports
import asyncio
import json
import traceback
from pathlib import Path
from typing import Any

# Omniverse & USD imports
import carb.settings
import omni.kit.app
import omni.kit.renderer.capture
import omni.ui as ui
from omni.kit.viewport.utility import frame_viewport_prims, get_active_viewport_window
from pxr import UsdGeom

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

ROOT = "/exts/varun.launcher.usd_viewport.v.01/viewportLight"
THEME = "/persistent/exts/varun.launcher.usd_viewport.v.01/viewportLight/theme"


# ----------------------------------------------------------------------
# 2. HELPER FUNCTIONS
# ----------------------------------------------------------------------


# Check raster compatibility, theme captures, and rendered frames in Kit.
async def run():
    # Prepare a report and capture directory for the selected GPU.
    app: Any = omni.kit.app.get_app()
    settings = carb.settings.get_settings()
    gpu = settings.get("/renderer/activeGpu")
    output = Path(__file__).resolve().parents[1] / "_build" / f"compatibility_gpu_{gpu}"
    output.mkdir(parents=True, exist_ok=True)
    report = {"requested_gpu": gpu}

    # Wait for stage and rendering updates.
    async def settle(count=20):
        for _ in range(count):
            await app.next_update_async()

    try:
        # --- 1) VERIFY RASTER-ONLY STARTUP ---

        await settle(40)
        manager = app.get_extension_manager()
        report["compatibility_mode"] = settings.get("/exts/omni.kit.renderer.core/compatibilityMode")
        report["rtx_extension_loaded"] = manager.is_extension_enabled("omni.hydra.rtx")
        report["enabled_engines"] = settings.get("/renderer/enabled")
        assert report["compatibility_mode"]
        assert not report["rtx_extension_loaded"], "The host independently loaded the RTX renderer"
        assert report["enabled_engines"] == "pxr"
        assert settings.get(f"{ROOT}/ready"), "Native viewport policy did not load"

        # --- 2) CREATE AND FRAME THE TEST CUBE ---

        window = get_active_viewport_window()
        assert window is not None
        window.visible = True
        window.width, window.height = 1100, 700
        window.position_x, window.position_y = 0, 28
        panel = ui.Workspace.get_window("Render Settings")
        if panel:
            panel.visible = False
        api = window.viewport_api
        await api.usd_context.new_stage_async()
        await settle()
        stage = api.stage
        UsdGeom.Cube.Define(stage, "/World/Cube").CreateSizeAttr(2)
        await settle()
        frame_viewport_prims(api, ["/World/Cube"])
        api.usd_context.get_selection().set_selected_prim_paths(["/World/Cube"], True)
        await settle(30)

        # --- 3) VERIFY RENDERED FRAMES AND THEME CAPTURES ---

        report["engine"] = api.hydra_engine
        report["delegate"] = api.render_mode
        assert report["engine"] == "pxr"
        assert report["delegate"] == "HdStormRendererPlugin"
        assert settings.get(f"{ROOT}/active")
        report["frame_info"] = api.frame_info
        assert "frame_number" in api.frame_info, "PXR produced no frame; check the log for GPU/OpenGL interop errors"
        first_frame = api.frame_info["frame_number"]
        for theme in ("light", "dark"):
            settings.set(THEME, theme)
            await settle()
            capture = omni.kit.renderer.capture.acquire_renderer_capture_interface()
            capture.capture_next_frame_swapchain(str(output / f"{theme}.png"))
            await settle()
            assert (output / f"{theme}.png").is_file()
        report["frame_info"] = api.frame_info
        assert api.frame_info["frame_number"] > first_frame
        report["passed"] = True
    except Exception:
        report["passed"] = False
        report["error"] = traceback.format_exc()
    finally:
        # Write the compatibility report and return its exit code.
        (output / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print("VIEWPORT_LIGHT_COMPATIBILITY_REPORT " + json.dumps(report, default=str), flush=True)
        app.post_quit(0 if report.get("passed") else 1)


# ----------------------------------------------------------------------
# 3. MODULE SETUP
# ----------------------------------------------------------------------

asyncio.ensure_future(run())
