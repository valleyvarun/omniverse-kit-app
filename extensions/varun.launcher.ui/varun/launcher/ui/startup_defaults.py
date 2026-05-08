import logging
from typing import Any, cast

import omni.kit.app
import omni.kit.stage_templates as stage_templates
import omni.usd
from omni.kit.quicklayout import QuickLayout


LOGGER = logging.getLogger(__name__)
STAGE_TEMPLATES = cast(Any, stage_templates)


# Encapsulates the launcher's first-run defaults: dock layout, default stage,
# default light rig, and extra FPS pacing presets.
class StartupDefaults:
    DEFAULT_LIGHT_RIG = "Grey Studio"

    def __init__(self, layout_file: str) -> None:
        self._layout_file = layout_file

    # Load the saved dock layout from disk.
    async def load_layout(self) -> None:
        app = cast(Any, omni.kit.app.get_app())
        for _ in range(3):
            await app.next_update_async()

        try:
            QuickLayout.load_file(self._layout_file, True)
        except Exception:
            QuickLayout.load_file(self._layout_file)

        # Several windows (e.g. Stage) call `deferred_dock_in` from their
        # __init__, which fires a frame or two after the window is created
        # and can override our layout. Wait for those to settle, then
        # reload our layout so it always wins on startup.
        for _ in range(20):
            await app.next_update_async()
        try:
            QuickLayout.load_file(self._layout_file, True)
        except Exception:
            QuickLayout.load_file(self._layout_file)

    # Open an empty default stage in the viewport.
    async def create_default_stage(self) -> None:
        app = cast(Any, omni.kit.app.get_app())
        for _ in range(5):
            await app.next_update_async()

        usd_context = cast(Any, omni.usd.get_context())
        if usd_context.can_open_stage():
            STAGE_TEMPLATES.new_stage(template=None)

    # Apply the Grey Studio light rig to the active stage.
    async def apply_default_light_rig(self, rig_name: str | None = None) -> None:
        try:
            import omni.kit.commands as kit_commands
        except ImportError:
            return

        app = cast(Any, omni.kit.app.get_app())
        for _ in range(10):
            await app.next_update_async()

        rig = rig_name or self.DEFAULT_LIGHT_RIG
        try:
            cast(Any, kit_commands).execute("SetLightingMenuMode", lighting_mode=rig)
        except Exception as exc:
            LOGGER.warning("Failed to apply default light rig %r: %s", rig, exc)

    # Startup sequence: layout first, then stage, then light rig.
    async def initialize_layout_and_stage(self) -> None:
        await self.load_layout()
        await self.create_default_stage()
        await self.apply_default_light_rig()

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
