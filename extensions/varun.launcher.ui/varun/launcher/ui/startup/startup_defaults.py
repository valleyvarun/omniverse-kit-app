import logging
from typing import Any, cast

import carb.settings
import omni.kit.app
from omni.kit.quicklayout import QuickLayout


LOGGER = logging.getLogger(__name__)

# Carb settings outlive the extension's Python module, so they're the
# reliable way to tell a true first-launch apart from a hot-reload.
_INITIALIZED_SETTING = "/exts/varun.launcher.ui/startup_initialized"
# Bump this whenever ``layouts/default.json`` changes shape in a way
# users should pick up automatically (e.g. tab-bar height tweak).
# Stored alongside ``_INITIALIZED_SETTING``; a mismatch forces a one-
# shot reload of the layout from disk, then the new version is stored.
_LAYOUT_VERSION_SETTING = "/exts/varun.launcher.ui/layout_version"
_LAYOUT_VERSION = 6


# Encapsulates the launcher's first-run defaults: dock layout and extra FPS
# pacing presets. Per-viewport stage + light-rig setup lives in
# `apps/openusd_viewport.py` so every viewport is treated identically.
class StartupDefaults:
    def __init__(self, layout_file: str) -> None:
        self._layout_file = layout_file

    @staticmethod
    def _is_first_run() -> bool:
        settings = cast(Any, carb.settings.get_settings())
        return not bool(settings.get(_INITIALIZED_SETTING))

    @staticmethod
    def _layout_out_of_date() -> bool:
        settings = cast(Any, carb.settings.get_settings())
        return int(settings.get(_LAYOUT_VERSION_SETTING) or 0) != _LAYOUT_VERSION

    @staticmethod
    def _mark_initialized() -> None:
        settings = cast(Any, carb.settings.get_settings())
        settings.set(_INITIALIZED_SETTING, True)
        settings.set(_LAYOUT_VERSION_SETTING, _LAYOUT_VERSION)

    # Load the saved dock layout from disk. On first run we double-load with
    # a settle window so we win over other extensions' late `deferred_dock_in`
    # calls (Stage, Layer, etc.). On hot-reload nothing destructive runs, so
    # one pass is enough and avoids re-scrambling the other windows.
    async def load_layout(self, first_run: bool) -> None:
        app = cast(Any, omni.kit.app.get_app())
        for _ in range(3):
            await app.next_update_async()

        try:
            QuickLayout.load_file(self._layout_file, True)
        except Exception:
            QuickLayout.load_file(self._layout_file)

        if not first_run:
            return

        for _ in range(20):
            await app.next_update_async()
        try:
            QuickLayout.load_file(self._layout_file, True)
        except Exception:
            QuickLayout.load_file(self._layout_file)

    # Startup sequence. On first launch: load the saved dock layout. On
    # hot-reload (extension Python module reloaded after a code edit): do
    # nothing -- Kit's workspace re-attaches our recreated windows to
    # their existing dock slots by title, and any further layout work
    # would just scramble whatever the user has since arranged. Per-
    # viewport stage + light-rig setup is handled by OpenUsdViewportManager.
    async def initialize_layout(self) -> None:
        if not self._is_first_run() and not self._layout_out_of_date():
            return
        await self.load_layout(first_run=True)
        self._mark_initialized()

    # Add a fixed-FPS option to the viewport's Pacing Speed menu.
    @staticmethod
    def add_fps_pacing_preset(fps: int, name: str | None = None) -> None:
        # Locate the preset list used by the framerate menubar.
        try:
            from omni.kit.window.preferences.scripts.pages import developer_page as presets_module
        except ImportError:
            try:
                from omni.kit.window.preferences.scripts.pages import (  # type: ignore[no-redef]
                    rendering_page as presets_module,
                )
            except ImportError:
                return

        presets = getattr(presets_module, "THREAD_SYNC_PRESETS", None)
        if presets is None:
            return

        # The framerate menubar (Kit 109) hardcodes a skip of the preset whose
        # name is exactly "120", so callers can pass a custom display name.
        preset_name = name if name is not None else str(fps)

        # Skip if this preset is already registered.
        if any(existing == preset_name for existing, _ in presets):
            return

        # Settings for a vsynced preset at the given FPS.
        preset: dict[str, Any] = {  # type: ignore[reportUnknownVariableType]
            "/app/runLoops/main/rateLimitEnabled": True,
            "/app/runLoops/main/rateLimitFrequency": fps,
            "/app/runLoops/main/rateLimitUsePrecisionSleep": True,
            "/app/runLoops/main/syncToPresent": True,
            "/app/runLoops/present/rateLimitEnabled": True,
            "/app/runLoops/present/rateLimitFrequency": fps,
            "/app/runLoops/present/rateLimitUsePrecisionSleep": True,
            "/app/runLoops/rendering_0/rateLimitEnabled": True,
            "/app/runLoops/rendering_0/rateLimitFrequency": fps,
            "/app/runLoops/rendering_0/rateLimitUsePrecisionSleep": True,
            "/app/runLoops/rendering_0/syncToPresent": True,
            "/app/runLoops/rendering_1/rateLimitEnabled": True,
            "/app/runLoops/rendering_1/rateLimitFrequency": fps,
            "/app/runLoops/rendering_1/rateLimitUsePrecisionSleep": True,
            "/app/runLoops/rendering_1/syncToPresent": True,
            "/app/runLoopsGlobal/syncToPresent": True,
            "/app/vsync": True,
            "/exts/omni.kit.renderer.core/present/enabled": True,
            "/exts/omni.kit.renderer.core/present/presentAfterRendering": True,
            "/persistent/app/viewport/defaults/tickRate": fps,
            "/rtx-transient/dlssg/enabled": False,
        }
        presets.append((preset_name, preset))
