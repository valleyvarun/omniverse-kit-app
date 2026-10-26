"""Early GPU policy; deliberately has no renderer or UI dependencies."""

# Omniverse & USD imports
# Python & library imports
import sys

import carb
import carb.settings
import omni.ext

# Local imports
from .adapters import graphics_adapters, wants_rtx

# ----------------------------------------------------------------------
# 1. MAIN EXTENSION CLASS
# ----------------------------------------------------------------------


class GpuStartup(omni.ext.IExt):
    # ON STARTUP
    def on_startup(self, _ext_id):

        # --- 1) DETECT PHYSICAL ADAPTERS ---

        settings = carb.settings.get_settings()
        try:
            adapters = graphics_adapters()
        except Exception as error:
            carb.log_warn(f"New Launcher: GPU detection failed; using compatibility mode: {error}")
            adapters = []

        # --- 2) DECIDE RTX ELIGIBILITY ---

        rtx = wants_rtx(adapters, bool(settings.get("/app/launcher/forceLightViewport")))
        settings.set("/app/launcher/rtxEligible", rtx)
        # Keep the established DX12 RTX path on Windows; raster compatibility
        # and Linux use Vulkan. This must precede renderer.init.
        settings.set("/app/vulkan", sys.platform != "win32" or not rtx)

        # --- 3) PUBLISH STARTUP SETTINGS ---

        settings.set("/app/launcher/detectedAdapters", [name for _, name in adapters])
        settings.set("/exts/omni.kit.renderer.core/compatibilityMode", not rtx)
        settings.set("/exts/omni.kit.renderer.core/raytracingEnabled", rtx)
        carb.log_info(
            f"New Launcher: {'NVIDIA' if rtx else 'raster compatibility'} graphics startup; adapters={adapters}"
        )

    # ON SHUTDOWN
    def on_shutdown(self):
        pass
