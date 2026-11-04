"""Connect the native viewport policy to Kit's existing window and controls."""

# Python & library imports
import asyncio
import logging
from typing import Any, cast

# Omniverse & USD imports
import carb
import carb.settings
import omni.kit.app
import omni.usd

# Local imports
from .display import StudioDisplay
from .menu import LightMenu
from .settings import ROOT, STORM
from .studio import StudioRig

LOGGER: logging.Logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# 1. VIEWPORT LIGHT
# ----------------------------------------------------------------------


class ViewportLight:
    # Track the studio appearance and subscribe to renderer and stage changes.
    def __init__(self, window, on_rtx_active=None):

        # --- 1) PREPARE APPEARANCE STATE ---

        self._window = window
        self._settings = carb.settings.get_settings()
        self._rig = StudioRig()
        self._display = None
        self._previous_display = None
        self._pending = None
        self._alive = True
        self._on_rtx_active = on_rtx_active
        self._missing_reported = False

        # --- 2) SUBSCRIBE TO VIEWPORT CHANGES ---

        self._menu = LightMenu()
        self._render_sub = window.viewport_api.subscribe_to_render_settings_change(self._schedule_sync)
        self._subscriptions = [
            omni.kit.app.SettingChangeSubscription(path, self._schedule_sync)
            for path in (
                f"{ROOT}/background",
                f"{ROOT}/ready",
                "/renderer/enabled",
                "/renderer/active",
                "/pxr/rendermode",
                "/rtx/rendermode",
            )
        ]
        self._stage_sub = window.viewport_api.usd_context.get_stage_event_stream().create_subscription_to_pop(
            self._on_stage_event, name="Viewport Light stage lifecycle"
        )
        self._schedule_sync()

    # Check whether this viewport is using the Storm delegate.
    @property
    def active(self):
        if self._window is None:
            return False
        api = self._window.viewport_api
        return api.hydra_engine == "pxr" and api.render_mode == STORM

    # Detach lights on close and resync after a stage opens.
    def _on_stage_event(self, event):
        if event.type in (int(omni.usd.StageEventType.CLOSING), int(omni.usd.StageEventType.CLOSED)):
            self._rig.detach()
        elif event.type in (int(omni.usd.StageEventType.OPENED), int(omni.usd.StageEventType.SETTINGS_LOADED)):
            self._schedule_sync()

    # Coalesce changes into one pending synchronization task.
    def _schedule_sync(self, *_):
        if self._alive and (self._pending is None or self._pending.done()):
            self._pending = asyncio.ensure_future(self._sync_next_update())

    # Wait one frame, then update the viewport appearance.
    async def _sync_next_update(self):
        await cast(Any, omni.kit.app.get_app()).next_update_async()
        self._pending = None
        if not self._alive:
            return
        try:
            self._sync()
        except Exception:
            self._release_appearance()
            self._settings.set(f"{ROOT}/active", False)
            LOGGER.exception("Viewport Light could not activate")

    # Match the studio display and rig to the active renderer.
    def _sync(self):
        if self._window is None:
            return
        self._menu.refresh_names()
        # Require native settings support before installing the studio appearance.
        if not self._settings.get(f"{ROOT}/ready"):
            self._release_appearance()
            if self._settings.get(f"{ROOT}/active"):
                self._settings.set(f"{ROOT}/active", False)
            if not self._missing_reported:
                carb.log_error(
                    "Viewport Light native plugin is unavailable. Run viewport_light/scripts/build_native.py."
                )
                self._missing_reported = True
            return
        self._missing_reported = False
        # Publish whether this viewport is using Light mode.
        active = self.active
        if self._settings.get(f"{ROOT}/active") != active:
            self._settings.set(f"{ROOT}/active", active)
        if active:
            # Install or refresh the custom backdrop and temporary light rig.
            color: Any = self._settings.get(f"{ROOT}/background")
            if self._display is None:
                self._previous_display = self._window.viewport_widget.display_delegate
                self._display = StudioDisplay(self._window.viewport_api, color)
                self._window.viewport_widget.display_delegate = self._display
            self._display.set_color(color)
            self._rig.attach(self._window.viewport_api.stage)
        else:
            # Restore the previous appearance and reactivate RTX lighting when needed.
            was_active = self._display is not None
            self._release_appearance()
            if was_active and self._window.viewport_api.hydra_engine == "rtx" and self._on_rtx_active:
                self._on_rtx_active()

    # Remove studio lighting and restore the previous display delegate.
    def _release_appearance(self):
        self._rig.detach()
        if self._display is not None:
            if self._window is not None:
                self._window.viewport_widget.display_delegate = self._previous_display
            self._display.destroy()
            self._display = None
            self._previous_display = None

    # Cancel pending work and restore the previous viewport appearance.
    def destroy(self):
        # Cancel deferred work before dropping callbacks and UI handles.
        self._alive = False
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None
        self._stage_sub = None
        self._render_sub = None
        self._subscriptions.clear()
        self._release_appearance()
        self._settings.set(f"{ROOT}/active", False)
        self._menu.destroy()
        self._on_rtx_active = None
        self._window = None
