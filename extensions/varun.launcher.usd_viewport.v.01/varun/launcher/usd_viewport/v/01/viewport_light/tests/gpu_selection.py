"""Run with New Launcher --exec to verify automatic or forced-Light GPU policy."""

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
import omni.usd
from omni.kit.viewport.utility import frame_viewport_prims, get_active_viewport_window
from pxr import UsdGeom

# ----------------------------------------------------------------------
# 1. HELPER FUNCTIONS
# ----------------------------------------------------------------------


# Check GPU startup policy and available renderer switching.
async def run():
    # Prepare the report for automatic or forced-Light startup.
    app: Any = omni.kit.app.get_app()
    settings = carb.settings.get_settings()
    report = {}
    light_only = bool(settings.get("/app/launcher/forceLightViewport"))
    output = Path(__file__).resolve().parents[1] / "_build" / ("gpu_light.json" if light_only else "gpu_auto.json")

    # Wait for a rendered frame or fail after the deadline.
    async def wait_until(predicate):
        deadline = time.monotonic() + 90
        while not predicate():
            assert time.monotonic() < deadline, "Timed out waiting for a rendered frame"
            await app.next_update_async()

    try:
        # --- 1) VERIFY THE INITIAL STORM VIEWPORT ---

        for _ in range(40):
            await app.next_update_async()
        window: Any = get_active_viewport_window()
        window.visible = True
        window.width, window.height = 1000, 700
        api = window.viewport_api
        await api.usd_context.new_stage_async()
        UsdGeom.Cube.Define(api.stage, "/Cube").CreateSizeAttr(100)
        frame_viewport_prims(api, ["/Cube"])
        await wait_until(lambda: api.frame_info.get("frame_number", 0) > 0)
        assert api.hydra_engine == "pxr" and api.render_mode == "HdStormRendererPlugin"

        # --- 2) CHECK GPU POLICY AND MENU CHOICES ---

        report["adapters"] = settings.get("/app/launcher/detectedAdapters")
        report["active_devices"] = settings.get("/app/launcher/graphicsDevices")
        report["rtx_available"] = bool(settings.get("/app/launcher/rtxAvailable"))
        report["compatibility_mode"] = settings.get("/exts/omni.kit.renderer.core/compatibilityMode")
        manager = app.get_extension_manager()
        report["rtx_loaded"] = manager.is_extension_enabled("omni.hydra.rtx")
        from omni.kit.viewport.menubar.render import get_instance

        labels = [
            r.displayName
            for _, group in cast(Any, get_instance())._render_menu.render_list.renderers
            for r in group.renderers
        ]
        report["menu"] = labels
        assert "Viewport Light" in labels
        assert any(label.startswith("RTX -") for label in labels) == report["rtx_available"]

        # --- 3) CHECK LIGHT-ONLY OR RTX SWITCHING ---

        if light_only:
            assert not report["rtx_available"] and not report["rtx_loaded"]
            assert report["compatibility_mode"] and settings.get("/renderer/enabled") == "pxr"
        elif settings.get("/app/launcher/rtxEligible"):
            assert report["rtx_available"] and report["rtx_loaded"]
            previous_handle = api.frame_info["viewport_handle"]
            api.set_hd_engine("rtx", "RaytracedLighting")
            await wait_until(
                lambda: (
                    api.hydra_engine == "rtx"
                    and api.frame_info.get("frame_number", 0) > 0
                    and api.frame_info.get("viewport_handle") not in (None, previous_handle)
                )
            )
            frame = api.frame_info["frame_number"]
            await wait_until(lambda: api.frame_info.get("frame_number", 0) > frame)
            report["rtx_rendered_frames"] = True
            rtx_handle = api.frame_info["viewport_handle"]
            api.set_hd_engine("pxr", "HdStormRendererPlugin")
            await wait_until(
                lambda: (
                    api.hydra_engine == "pxr"
                    and api.frame_info.get("frame_number", 0) > 0
                    and api.frame_info.get("viewport_handle") not in (None, rtx_handle)
                )
            )
        report["passed"] = True
    except Exception:
        report["passed"] = False
        report["error"] = traceback.format_exc()
    finally:
        # Write the result and close the disposable test stage.
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("GPU_SELECTION " + json.dumps(report), flush=True)
        await cast(Any, omni.usd.get_context()).close_stage_async()
        app.post_quit(0 if report.get("passed") else 1)


# ----------------------------------------------------------------------
# 2. MODULE SETUP
# ----------------------------------------------------------------------

asyncio.ensure_future(run())
