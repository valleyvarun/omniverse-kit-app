# Python & library imports
import ctypes
import sys
import time
from ctypes import wintypes
from typing import Any, cast

# Omniverse & USD imports
import carb.input
import carb.settings
import omni.kit.app
import omni.timeline
from omni.kit.viewport.window import ViewportWindow
from omni.kit.widget.viewport.api import ViewportAPI
from pxr import Tf, Usd

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

# Kit defaults to its "No Pacing" preset: 120 Hz, vsync off, and presenting even unchanged frames.
# Its "60" preset caps at 60 Hz and presents only new frames; the viewport's pacing menu can change it.
PRESET_SETTING: str = "/exts/omni.kit.viewport.menubar.framerate/currentPreset"
PRESET: str = "60"

# the same cap for hosts without the pacing menu
LOOPS: tuple[str, ...] = ("main", "present", "rendering_0")
ACTIVE_FPS: int = 60

# full-rate time after the last input or scene change, and after startup
ACTIVE_SECONDS: float = 1.0
STARTUP_SECONDS: float = 5.0

# idle frames wait for a window event, but at most this long, so background work keeps moving
IDLE_WAIT_MS: int = 100

# MsgWaitForMultipleObjectsEx: wake for any input, including touch and pen, even if already seen
QS_ALLINPUT: int = 0x1CFF
MWMO_INPUTAVAILABLE: int = 0x0004


# ----------------------------------------------------------------------
# 2. FRAME PACING
# ----------------------------------------------------------------------


class FramePacing:
    # Cap Kit's frame rate and, like Blender, stop redrawing while nothing changes.
    def __init__(self, window: ViewportWindow) -> None:

        # --- 1) CAP THE FRAME RATE ---

        self._window: ViewportWindow = window
        self._settings: Any = carb.settings.get_settings()
        self._previous: dict[str, Any] = {PRESET_SETTING: self._settings.get(PRESET_SETTING)}
        self._settings.set(PRESET_SETTING, PRESET)
        loop: str
        for loop in LOOPS:
            path: str = f"/app/runLoops/{loop}/rateLimitFrequency"
            self._previous[path] = self._settings.get(path)
            self._settings.set(path, ACTIVE_FPS)

        # --- 2) WATCH FOR INPUT AND SCENE CHANGES ---

        self._active_until: float = time.monotonic() + STARTUP_SECONDS
        self._timeline: Any = omni.timeline.get_timeline_interface()
        self._input: Any = carb.input.acquire_input_interface()
        # First in line (order 0), so UI widgets cannot consume events before they count as activity.
        self._input_sub: int | None = self._input.subscribe_to_input_events(self._on_input, order=0)
        self._stage: Usd.Stage | None = None
        self._stage_listener: Any = None
        self._stage_events: Any = None
        self._render_sub: Any = None
        viewport: ViewportAPI | None = window.viewport_api
        if viewport is not None:
            self._stage_events = viewport.usd_context.get_stage_event_stream().create_subscription_to_pop(
                self._on_stage_event, name="varun.launcher.usd_viewport frame pacing"
            )
            # Resizes and camera switches change what the viewport shows.
            self._render_sub = viewport.subscribe_to_render_settings_change(self._mark_active)
        self._watch_stage()

        # --- 3) SLEEP UNTIL WINDOW EVENTS WHILE IDLE ---

        self._user32: Any = None
        if sys.platform == "win32":
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._user32.MsgWaitForMultipleObjectsEx.argtypes = [
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            self._user32.MsgWaitForMultipleObjectsEx.restype = wintypes.DWORD
        self._update_sub: Any = (
            cast(Any, omni.kit.app.get_app())
            .get_update_event_stream()
            .create_subscription_to_pop(self._on_update, name="varun.launcher.usd_viewport frame pacing")
        )

    # Keep full-rate frames for a moment after any change.
    def _mark_active(self, *_: Any) -> None:
        self._active_until = max(self._active_until, time.monotonic() + ACTIVE_SECONDS)

    # Keyboard, mouse, and pen input; returning True leaves the event for everyone else.
    def _on_input(self, _event: Any) -> bool:
        self._mark_active()
        return True

    # Opening, closing, selection, and loading all count as activity.
    def _on_stage_event(self, _event: Any) -> None:
        self._mark_active()
        self._watch_stage()

    # Listen for edits to the viewport's stage, including camera moves from any tool or touch.
    def _watch_stage(self) -> None:
        viewport: ViewportAPI | None = self._window.viewport_api
        stage: Usd.Stage | None = viewport.stage if viewport is not None else None
        if stage == self._stage:
            return
        if self._stage_listener is not None:
            self._stage_listener.Revoke()
        self._stage = stage
        self._stage_listener = (
            cast(Any, Tf.Notice).Register(Usd.Notice.ObjectsChanged, self._mark_active, stage)
            if stage is not None
            else None
        )

    # Run at full rate while anything changes; otherwise wait for the next window event.
    def _on_update(self, _event: Any) -> None:
        # Playback and RTX's progressive refinement need every frame.
        viewport: ViewportAPI | None = self._window.viewport_api
        if self._timeline.is_playing() or (viewport is not None and viewport.hydra_engine != "pxr"):
            self._mark_active()
        if self._user32 is None or time.monotonic() < self._active_until:
            return

        # Like Blender's event loop, an idle app sleeps until input arrives instead of redrawing.
        self._user32.MsgWaitForMultipleObjectsEx(0, None, IDLE_WAIT_MS, QS_ALLINPUT, MWMO_INPUTAVAILABLE)

    # Stop watching and restore Kit's pacing preset and frame caps.
    def destroy(self) -> None:
        self._update_sub = None
        self._stage_events = None
        self._render_sub = None
        if self._stage_listener is not None:
            self._stage_listener.Revoke()
            self._stage_listener = None
        if self._input_sub is not None:
            self._input.unsubscribe_to_input_events(self._input_sub)
            self._input_sub = None
        path: str
        value: Any
        for path, value in self._previous.items():
            if value is not None:
                self._settings.set(path, value)
