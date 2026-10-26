# Python & library imports
import asyncio
from collections.abc import Awaitable
from typing import Any, cast

# Omniverse & USD imports
import carb.settings
import omni.ext
import omni.kit.app
import omni.kit.commands
import omni.kit.stage_templates as stage_templates
import omni.ui as ui
import omni.usd
from omni.kit.viewport.window import ViewportWindow
from pxr import UsdUtils

# Local imports
from .hud import HudToggleButton
from .viewport_light import ViewportLight

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

LIGHT_RIG = "Grey Studio"

# per-stage mode cache maintained by the lighting menubar
LIGHTING_MODE_KEY = "/exts/omni.kit.viewport.menubar.lighting/lightingMode/{stage_id}"


# ----------------------------------------------------------------------
# 2. MAIN EXTENSION CLASS
# ----------------------------------------------------------------------


class MyExtension(omni.ext.IExt):
    # Native subscriptions and pending async work have separate lifetimes.
    _stage_events: Any
    _tasks: set[asyncio.Future[None]]

    # ON STARTUP
    def on_startup(self, _ext_id: str) -> None:
        print("[varun.launcher.usd_viewport.v.01] Extension startup")

        # --- 1) DISABLE AUTOMATIC RIG REPLACEMENT ---

        # our ext-toml default loses to the menubar's (it loads first); force the value
        cast(Any, carb.settings.get_settings()).set(
            "/persistent/exts/omni.kit.viewport.menubar.lighting/autoLightRig/enabled",
            False,
        )

        # --- 2) CHOOSE RENDERERS AND CREATE THE VIEWPORT ---

        # no header, not movable
        flags = ui.WINDOW_FLAGS_NO_TITLE_BAR | ui.WINDOW_FLAGS_NO_MOVE

        # create USD viewport (size comes from the dock layout)
        settings = carb.settings.get_settings()
        if settings.get("/exts/omni.kit.renderer.core/compatibilityMode"):
            settings.set("/renderer/enabled", "pxr")
        if settings.get("/app/launcher/autoGpu"):
            from .viewport_light.gpu_policy import configure_renderers

            configure_renderers()
        settings.set("/renderer/active", "pxr")
        settings.set("/pxr/rendermode", "HdStormRendererPlugin")
        self._window = ViewportWindow(
            "Viewport",
            flags=flags,
        )

        # --- 3) INSTALL LIGHTING AND HUD CONTROLS ---

        self._tasks = set()
        self._light_viewport = ViewportLight(self._window, lambda: self._track_task(self._apply_light_rig()))
        self._hud = HudToggleButton()

        # --- 4) WATCH STAGES AND SCHEDULE STARTUP ---

        # every opened or attached stage gets the light rig
        self._stage_events = (
            omni.usd.get_context()
            .get_stage_event_stream()
            .create_subscription_to_pop(self._on_stage_event, name="varun.launcher.usd_viewport light rig")
        )

        self._track_task(self._create_stage())

    # Track background work so shutdown can cancel it.
    def _track_task(self, coroutine: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # create initial USD stage
    async def _create_stage(self) -> None:
        kit_app = cast(Any, omni.kit.app.get_app())
        for _ in range(5):
            await kit_app.next_update_async()

        if omni.usd.get_context().can_open_stage():
            cast(Any, stage_templates).new_stage(template="empty")

    # Restore RTX lighting after a stage opens outside Light mode.
    def _on_stage_event(self, event: Any) -> None:
        if event.type == int(omni.usd.StageEventType.OPENED):
            if not self._light_viewport.active:
                self._track_task(self._apply_light_rig())

    # apply the tab's saved lighting mode, or the default rig
    async def _apply_light_rig(self) -> None:
        context: Any = omni.usd.get_context()
        kit_app = cast(Any, omni.kit.app.get_app())

        # stage binding can lag the OPENED event by a few frames
        for _ in range(30):
            if context.get_stage() is not None:
                break
            await kit_app.next_update_async()
        else:
            return

        # run after the lighting menubar's own OPENED handler so ours wins
        for _ in range(2):
            await kit_app.next_update_async()

        if self._light_viewport.active:
            return

        stage_id = UsdUtils.StageCache.Get().GetId(context.get_stage()).ToLongInt()
        settings: Any = carb.settings.get_settings()
        mode_key = LIGHTING_MODE_KEY.format(stage_id=stage_id)
        saved_mode = settings.get(mode_key)

        # the command skips rigs whose name already matches this cache key,
        # without creating the rig lights; clear it so the apply is real
        settings.set(mode_key, "")
        cast(Any, omni.kit.commands).execute(
            "SetLightingMenuModeCommand",
            lighting_mode=LIGHT_RIG if saved_mode is None else str(saved_mode),
            usd_context_name="",
        )

    # ON SHUTDOWN
    def on_shutdown(self) -> None:
        print("[varun.launcher.usd_viewport.v.01] Extension shutdown")
        # Stop stage callbacks and cancel pending lighting work.
        self._stage_events = None
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        # Release menu controls and appearance before destroying the viewport.
        self._hud.destroy()
        self._light_viewport.destroy()
        self._window.destroy()
