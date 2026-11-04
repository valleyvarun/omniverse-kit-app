"""Early GPU policy; deliberately has no renderer or UI dependencies."""

# Python & library imports
import logging
import sys
from typing import Any

# Omniverse & USD imports
import carb
import carb.settings
import omni.ext
import omni.kit.app

# Local imports
from .adapters import graphics_adapters, wants_rtx

LOGGER: logging.Logger = logging.getLogger(__name__)
_DEFERRED_RTX: tuple[str, str] = ("omni.hydra.rtx", "omni.kit.viewport.rtx")

# ----------------------------------------------------------------------
# 1. MAIN EXTENSION CLASS
# ----------------------------------------------------------------------


class GpuStartup(omni.ext.IExt):
    # ON STARTUP
    def on_startup(self, _ext_id: str) -> None:

        # --- 1) DETECT PHYSICAL ADAPTERS ---

        settings: Any = carb.settings.get_settings()
        adapters: list[tuple[int, str]]
        try:
            adapters = graphics_adapters()
        except Exception:
            LOGGER.exception("New Launcher: GPU detection failed; using compatibility mode")
            adapters = []

        # --- 2) DECIDE RTX ELIGIBILITY ---

        rtx: bool = wants_rtx(adapters, bool(settings.get("/app/launcher/forceLightViewport")))
        settings.set("/app/launcher/rtxEligible", rtx)
        # Keep the established DX12 RTX path on Windows; raster compatibility
        # and Linux use Vulkan. This must precede renderer.init.
        settings.set("/app/vulkan", sys.platform != "win32" or not rtx)

        # --- 3) PUBLISH STARTUP SETTINGS ---

        names: list[str] = []
        vendor: int
        name: str
        for vendor, name in adapters:
            names.append(name)
        settings.set("/app/launcher/detectedAdapters", names)
        settings.set("/exts/omni.kit.renderer.core/compatibilityMode", not rtx)
        settings.set("/exts/omni.kit.renderer.core/raytracingEnabled", rtx)
        # Only the launcher-owned startup exclusions are released, on an eligible GPU.
        if rtx and settings.get("/app/launcher/deferRtx"):
            excluded: list[str] = list(settings.get("/app/extensions/excluded") or [])
            remaining: list[str] = []
            name: str
            for name in excluded:
                if name not in _DEFERRED_RTX:
                    remaining.append(name)
            settings.set("/app/extensions/excluded", remaining)
            manager: Any = omni.kit.app.get_app().get_extension_manager()
            manager.set_extensions_excluded(remaining)
        carb.log_info(
            f"New Launcher: {'NVIDIA' if rtx else 'raster compatibility'} graphics startup; adapters={adapters}"
        )

    # ON SHUTDOWN
    def on_shutdown(self) -> None:
        pass
