"""Centralized constants and the properties panel for the 3D Draw tool.

All tunable values used by `three_d_draw.py` and `cursor_3d.py` live here so
they can be reviewed and adjusted in one place. The user-facing
`ThreeDDrawPropertiesPanel` (rendered in the bottom Tool Properties dock)
also lives here so every 3D-Draw-specific setting -- defaults and the UI
that exposes them -- is in a single file. Mutable runtime state and
classes (e.g. `BrushConfig`, the cursor scene registry) remain in their
original modules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import omni.ui as ui

from .brush_config import BrushConfig
from ..tool_properties import ToolPropertiesPanel


# --- File-system locations --------------------------------------------------
# Directory holding tool icons (`ui/logos/`). `tool_settings.py` lives at
# `ui/tools/three_d_draw/`, so three `.parent`s reach the `ui/` package root.
ICONS_DIR = Path(__file__).resolve().parent.parent.parent / "logos"


# --- Carb / Kit setting keys ------------------------------------------------
# Toggle for the viewport's right-click context menu. The 3D Draw tool
# suppresses this while active so LMB-drag isn't preempted by the menu.
VIEWPORT_CONTEXT_MENU_SETTING = "/exts/omni.kit.window.viewport/showContextMenu"


# --- Sentinels --------------------------------------------------------------
# Marker for "no previous value captured" when restoring carb settings.
UNSET = object()


# --- Stroke / draw constants ------------------------------------------------
# RGB color (0..1) applied to every stroke's prototype sphere.
SPHERE_COLOR = (0.0, 0.0, 0.0)

# Tick interval (seconds) at which the stroke emitter consumes buffered points.
EMIT_INTERVAL = 0.016

# USD root path under which all 3D Draw geometry is created.
DRAW_ROOT = "/World/ThreeDDraw"

# Temp parent path used for the in-progress stroke (chunked PointInstancers
# live as its children). Removed and replaced by a single PointInstancer at
# `Stroke_NN` on mouse-up.
LIVE_STROKE_PATH = f"{DRAW_ROOT}/_LiveStroke"

# Max spheres per chunk PointInstancer during the live stroke. Once a chunk
# fills, it is frozen (never written again) and a new sibling chunk is
# started. This bounds the per-tick rewrite cost so FPS stays flat.
# 20000 keeps the per-tick array Set cheap (~240 KB memcpy) while keeping
# the number of chunk prims (and Hydra draw calls) low for long strokes.
CHUNK_SPHERE_LIMIT = 20000


# --- Cursor scene registration ----------------------------------------------
# Factory id used when registering the cursor scene with the viewport.
CURSOR_FACTORY_ID = "varun.launcher.ui.cursor_3d"


# --- Cursor appearance ------------------------------------------------------
# RGBA color of the brush ring.
RING_COLOR = (1.0, 0.85, 0.2, 1.0)


# Initial pre-allocated capacity (in spheres) for the per-stroke positions
# buffer. The buffer is doubled on overflow so per-tick append is amortized
# O(1) instead of O(N) (which is what `np.concatenate` was doing).
INITIAL_STROKE_CAPACITY = 4096

# Minimum world-space distance a cursor sample must move before it's
# appended to the per-stroke sample buffer. The OS can deliver hundreds of
# mouse-move events per second; without this, fast brushes flood the
# polyline with redundant points. Well below brush stamp spacing so
# resampling fidelity is unchanged.
SAMPLE_MIN_DISTANCE = 0.1


# ============================================================================
# 3D DRAW PANEL
# Brush-size slider (with paired value field) that mutates the shared
# BrushConfig singleton. Lives in this file so every 3D-Draw setting --
# defaults plus the UI that exposes them -- is colocated.
# ============================================================================
class ThreeDDrawPropertiesPanel(ToolPropertiesPanel):
    def __init__(self, request_refresh: Callable[[], None]) -> None:
        super().__init__(request_refresh)
        # Subscribe to BrushConfig change notifications so the panel can
        # rebuild on tool toggle and resync values on parameter changes.
        self._cfg_listener = self._on_cfg_changed
        BrushConfig.get().add_listener(self._cfg_listener)

        # Live model ref: kept so subscriptions stay alive AND so the
        # listener can sync the value back on programmatic changes.
        self._brush_model: Any = None
        # Subscription handles for the field model (kept alive intentionally).
        self._field_subs: list[Any] = []

        # Suppress write-back when we set models from BrushConfig
        # (otherwise we'd write back into BrushConfig in a loop).
        self._suppress = False

        # Tracks whether the panel was active on the previous notification.
        # If active state flipped -> rebuild; if same -> just sync values.
        self._was_active: bool | None = None

    # Drop the BrushConfig listener and clear widget refs.
    def destroy(self) -> None:
        try:
            BrushConfig.get().remove_listener(self._cfg_listener)
        except Exception:
            pass
        self._brush_model = None
        self._field_subs = []

    # Show this panel iff the 3D Draw tool is currently active.
    def is_active(self) -> bool:
        return BrushConfig.get().active

    # Build the single brush-size slider row (the only user-controllable knob).
    def build_into(self, frame: ui.Frame) -> None:
        cfg = BrushConfig.get()
        with frame:
            # Outer vertical stack with top spacer for breathing room.
            with ui.VStack(spacing=4):
                ui.Spacer(height=6)
                with ui.HStack(spacing=6, height=24):
                    ui.Spacer(width=8)
                    # ---- Brush size slider (float) ----
                    self._brush_model = self._build_float_slider(
                        label="Brush Size",
                        value=cfg.brush_radius,
                        lo=cfg.BRUSH_RADIUS_MIN,
                        hi=cfg.BRUSH_RADIUS_MAX,
                        step=0.1,
                        on_changed=lambda v: cfg.set_brush_radius(v),
                    )
                    # Right-side flexible spacer.
                    ui.Spacer()
        # Remember active state for later toggle detection.
        self._was_active = True

    # Build one [Label | FloatField | FloatSlider] group.
    # The field and slider SHARE one model so editing either updates the
    # other. The slider's text color is set transparent so only the bar
    # is visible (the user wants the value only in the small box).
    def _build_float_slider(
        self,
        label: str,
        value: float,
        lo: float,
        hi: float,
        step: float,
        on_changed: Callable[[float], None],
    ) -> Any:
        with ui.HStack(spacing=4, width=300):
            # Row label on the far left (fixed width).
            ui.Label(label, width=80)
            # Value field (small box) - left of the slider, shares its model.
            field = ui.FloatField(width=50)
            field.model.set_value(float(value))
            # Slider takes the remaining space; transparent text via style.
            slider = ui.FloatSlider(field.model, min=lo, max=hi, step=step,
                                    style={"color": 0x00000000})
            # Subscribe once on the shared model; both widgets fire it.
            sub = field.model.subscribe_value_changed_fn(
                lambda m, cb=on_changed: self._on_slider_changed(
                    cb, m.get_value_as_float()
                )
            )
            self._field_subs.append(sub)
            # `slider` is parented by the HStack; del just silences linters.
            del slider
            return field.model

    # Same as above but for integer values.
    def _build_int_slider(
        self,
        label: str,
        value: int,
        lo: int,
        hi: int,
        on_changed: Callable[[int], None],
    ) -> Any:
        with ui.HStack(spacing=4, width=260):
            ui.Label(label, width=80)
            field = ui.IntField(width=50)
            field.model.set_value(int(value))
            slider = ui.IntSlider(field.model, min=lo, max=hi,
                                  style={"color": 0x00000000})
            sub = field.model.subscribe_value_changed_fn(
                lambda m, cb=on_changed: self._on_slider_changed(
                    cb, m.get_value_as_int()
                )
            )
            self._field_subs.append(sub)
            del slider
            return field.model

    # Forward slider edits to the BrushConfig setter unless we're syncing.
    def _on_slider_changed(self, cb: Callable[[Any], None], value: Any) -> None:
        if self._suppress:
            return
        try:
            cb(value)
        except Exception:
            pass

    # BrushConfig listener:
    #   - Active state toggled -> ask the window to rebuild (us in / out).
    #   - Same active state    -> just push current values into widgets.
    def _on_cfg_changed(self) -> None:
        active_now = BrushConfig.get().active
        if self._was_active != active_now:
            self._was_active = active_now
            self._refresh()
            return
        self._sync_models_from_cfg()

    # Push BrushConfig values into the widget model without firing back.
    def _sync_models_from_cfg(self) -> None:
        cfg = BrushConfig.get()
        self._suppress = True
        try:
            if self._brush_model is not None:
                self._brush_model.set_value(float(cfg.brush_radius))
        finally:
            self._suppress = False
