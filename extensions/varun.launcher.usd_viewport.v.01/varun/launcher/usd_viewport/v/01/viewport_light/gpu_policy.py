"""New Launcher: raster-compatible startup with optional NVIDIA RTX engines."""

# Omniverse & USD imports
import carb
import carb.settings
import omni.appwindow
import omni.gpu_foundation_factory
import omni.kit.app
from omni.kit.renderer.bind import get_renderer_interface

# ----------------------------------------------------------------------
# 1. HELPER FUNCTIONS
# ----------------------------------------------------------------------


# Start with Storm and enable RTX only when its GPU and extensions qualify.
def configure_renderers():

    # --- 1) ESTABLISH THE STORM FALLBACK ---

    settings = carb.settings.get_settings()
    settings.set("/renderer/enabled", "pxr")
    settings.set("/app/launcher/rtxAvailable", False)
    if not settings.get("/app/launcher/rtxEligible"):
        return
    try:
        # --- 2) CONFIRM KIT SEES AN NVIDIA DEVICE ---

        window = omni.appwindow.get_default_app_window()
        device = get_renderer_interface().get_graphics_device(window)
        names = omni.gpu_foundation_factory.get_gpus_list(device)
        settings.set("/app/launcher/graphicsDevices", names)
        # Confirm Kit's graphics foundation can enumerate an NVIDIA adapter too.
        if not any("nvidia" in name.lower() for name in names):
            return

        # --- 3) LOAD THE OPTIONAL RTX EXTENSIONS ---

        manager = omni.kit.app.get_app().get_extension_manager()
        # Dependency filters were resolved before the early GPU bootstrap chose
        # the graphics API, so explicitly load the matching shader cache.
        backend = "vulkan" if settings.get("/app/vulkan") else "d3d12"
        for extension in (f"omni.hydra.rtx.shadercache.{backend}", "omni.hydra.rtx", "omni.kit.viewport.rtx"):
            if not manager.set_extension_enabled_immediate(extension, True):
                carb.log_warn(f"New Launcher: {extension} unavailable; using Viewport Light.")
                return
        # Publish RTX availability only after every required extension loads.
        settings.set("/renderer/enabled", "pxr,rtx")
        settings.set("/app/launcher/rtxAvailable", True)
    except Exception as error:
        carb.log_warn(f"New Launcher: optional RTX initialization failed; using Viewport Light: {error}")
