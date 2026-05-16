"""3D Draw (BasisCurves variant) -- experimental.

Same UX as the production 3D Draw tool but each stroke becomes a single
`UsdGeom.BasisCurves` prim (a ribbon/tube with constant width) instead
of N analytic-sphere `UsdGeom.Points`. Each stroke is therefore one
Hydra prim (one BLAS, one TLAS entry), one BVH primitive, and ~2
triangles per polyline segment -- substantially cheaper than the dot
stamp approach for long strokes and large stroke counts.

Trade-offs vs production:
  - Visual: continuous smooth tube instead of stamped airbrush dots.
  - Geometry root: `/World/ThreeDDrawCurves/` (sibling of, never
    overlapping, the production `/World/ThreeDDraw/` tree).
  - No FlatPoints material binding (BasisCurves is rendered as real
    geometry; uses default lit shading via `displayColor`).
  - No PointInstancer / chunking -- one curve prim per stroke, written
    incrementally during the live drag.

Reuses (READ-ONLY) from the production tool:
  - BrushConfig          shared brush radius slider state.
  - Cursor3D             viewport cursor + drag gestures.
  - EscapeHandler        deactivate-on-Esc.
  - ViewportSuppression  hide selection / context-menu while drawing.
  - Tool                 toolbar button descriptor.

The production tool's modules are NOT modified. This file owns its own
constants, USD root, and stroke state; nothing leaks back the other way.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import numpy as np
import omni.kit.app
import omni.usd
from pxr import Gf, Sdf, Usd, UsdGeom, Vt

from ..brush_config import BrushConfig
from ..cursor_3d import Cursor3D
from ..escape_handler import EscapeHandler
from ..viewport_suppression import ViewportSuppression
from ...tool import Tool


LOGGER = logging.getLogger(__name__)


# --- Local constants (production `tool_settings.py` is left untouched) ----

# Same USD root as the production tool. The clayers panel filters strokes
# by this exact path prefix (see `layers.STROKES_ROOT`), so authoring under
# a separate root would make curve strokes invisible in the layers UI.
# Prim *type* is not filtered -- BasisCurves and Points prims can coexist
# as siblings here without colliding (they're different prim names).
DRAW_ROOT = "/World/ThreeDDraw"

# Temp prim path used while the LMB is held; replaced by a real Stroke_NN
# prim on LMB-up. Distinct name from the production tool's `_LiveStroke`
# so simultaneous use of both tools can't cross the streams.
LIVE_STROKE_PATH = f"{DRAW_ROOT}/_LiveStrokeCurves"

# Per-frame tick interval (seconds). Matches production EMIT_INTERVAL so
# the perf comparison is apples-to-apples.
EMIT_INTERVAL = 0.016

# Minimum world-space distance between consecutive raw cursor samples.
# Same value as production so the live polyline density is comparable.
SAMPLE_MIN_DISTANCE = 0.1

# Stroke color (RGB, 0..1). Same as the production sphere color.
STROKE_COLOR = (0.0, 0.0, 0.0)


class ThreeDDrawCurvesTool:
    NAME = "three_d_draw_curves"
    LABEL = "3DC"
    SHORTCUT = "SHIFT+D"
    TOOLTIP = "3D Draw (BasisCurves, experimental)"

    def __init__(self) -> None:
        self._tool: Tool | None = None
        self._is_active = False
        self._cursor: Cursor3D | None = None
        # ----- Drawing state -----
        self._drawing = False
        # Raw cursor samples, used directly as curve control points.
        # No resampling: the BasisCurves renderer interpolates between
        # control points, so dense uniform spacing isn't required.
        self._samples: list[tuple[float, float, float]] = []
        # Set by `_on_draw_change` whenever new samples arrive; consumed
        # by `_tick` which rewrites the live curve at most once per frame.
        self._dirty = False
        # Per-frame tick scheduling (mirrors production tool).
        self._update_sub: Any = None
        self._accum = 0.0
        # ----- Helpers (private instances; never shared with production) -----
        self._viewport_suppression = ViewportSuppression()
        self._escape = EscapeHandler()
        self._brush_listener: Any = None

    # BUILD THE TOOLBAR TOOL DESCRIPTOR.
    def make_tool(self) -> Tool:
        tool = Tool(
            name=self.NAME,
            icon=None,
            icon_text=self.LABEL,
            shortcut=self.SHORTCUT,
            tooltip=self.TOOLTIP,
            on_click=self._on_clicked,
            toggleable=True,
        )
        self._tool = tool
        return tool

    # CLEANUP ON SHUTDOWN.
    def destroy(self) -> None:
        self.deactivate()
        self._tool = None

    # TOGGLE THE TOOL.
    def _on_clicked(self) -> None:
        if self._is_active:
            self.deactivate()
        else:
            self.activate()

    # ACTIVATE: CLEAR SELECTION, SUPPRESS VIEWPORT INTERACTIONS, SHOW CURSOR.
    def activate(self) -> None:
        if self._is_active:
            return
        self._is_active = True
        if self._tool is not None:
            self._tool.set_active(True)
        self._clear_selection()
        self._escape.subscribe(self.deactivate)
        self._viewport_suppression.enter()
        self._cursor = Cursor3D()
        self._cursor.show()
        self._cursor.set_listener(
            on_begin=self._on_draw_begin,
            on_change=self._on_draw_change,
            on_end=self._on_draw_end,
        )
        cfg = BrushConfig.get()
        cfg.set_active(True)
        self._brush_listener = self._on_brush_config_changed
        cfg.add_listener(self._brush_listener)

    # DEACTIVATE AND RESTORE VIEWPORT STATE.
    def deactivate(self) -> None:
        if not self._is_active:
            return
        self._is_active = False
        if self._tool is not None:
            self._tool.set_active(False)
        cfg = BrushConfig.get()
        if self._brush_listener is not None:
            cfg.remove_listener(self._brush_listener)
            self._brush_listener = None
        cfg.set_active(False)
        self._escape.unsubscribe()
        self._viewport_suppression.exit()
        self._stop_emitting()
        self._drawing = False
        if self._cursor is not None:
            self._cursor.clear_listener()
            self._cursor.hide()
            self._cursor = None

    # CLEAR THE CURRENT VIEWPORT SELECTION.
    def _clear_selection(self) -> None:
        try:
            usd_context = cast(Any, omni.usd.get_context())
            usd_context.get_selection().clear_selected_prim_paths()
        except Exception as exc:
            LOGGER.warning("3D Draw (curves): could not clear selection: %s", exc)

    # BRUSHCONFIG CHANGE: REBUILD CURSOR SO RING PICKS UP NEW VALUES.
    # Stroke width is sampled at curve creation, so changes apply to the
    # next stroke. (Same behavior as the production tool.)
    def _on_brush_config_changed(self) -> None:
        if not self._is_active or self._cursor is None:
            return
        self._cursor.clear_listener()
        self._cursor.hide()
        self._cursor = None
        self._cursor = Cursor3D()
        self._cursor.show()
        self._cursor.set_listener(
            on_begin=self._on_draw_begin,
            on_change=self._on_draw_change,
            on_end=self._on_draw_end,
        )

    # ----- Drawing -----

    # CURSOR LMB-DOWN: start a new stroke + create the live BasisCurves prim
    # with a single control point. Subsequent ticks rewrite its `points`
    # attr as more samples arrive.
    def _on_draw_begin(self, pos: tuple[float, float, float]) -> None:
        self._samples = [pos]
        self._dirty = True
        self._drawing = True
        # Defensive: clear any leftover live prim from a prior session.
        self._delete_prim(LIVE_STROKE_PATH)
        # Create the live BasisCurves prim with the starting point.
        self._define_curve(LIVE_STROKE_PATH, self._samples)
        self._dirty = False
        self._accum = EMIT_INTERVAL
        self._start_emitting()

    # CURSOR MOVE: distance-throttled append (matches production).
    def _on_draw_change(self, pos: tuple[float, float, float]) -> None:
        last = self._samples[-1] if self._samples else None
        if last is not None:
            dx = pos[0] - last[0]
            dy = pos[1] - last[1]
            dz = pos[2] - last[2]
            if dx * dx + dy * dy + dz * dz < SAMPLE_MIN_DISTANCE * SAMPLE_MIN_DISTANCE:
                return
        self._samples.append(pos)
        self._dirty = True

    # CURSOR LMB-UP: flush any pending samples, allocate a real Stroke_NN
    # prim with the final points, and delete the live prim.
    def _on_draw_end(self) -> None:
        # Final flush of the live prim (to keep it visually in sync if it
        # survives somehow; mostly defensive).
        if self._dirty and self._samples:
            self._write_curve(LIVE_STROKE_PATH, self._samples)
            self._dirty = False
        self._drawing = False
        # Allocate the permanent stroke prim. BasisCurves needs >=2 points.
        stroke_path: str | None = None
        if len(self._samples) >= 2:
            try:
                stage = cast(Any, omni.usd.get_context()).get_stage()
                if stage is not None:
                    stroke_path = self._allocate_stroke_path(stage)
                    self._define_curve(stroke_path, self._samples)
            except Exception as exc:
                LOGGER.warning("3D Draw (curves): finalize failed: %s", exc)
                stroke_path = None
        # Always remove the temp live prim.
        self._delete_prim(LIVE_STROKE_PATH)
        # Register the stroke with the layers/groups panel (clayers) so it
        # shows up alongside production-tool strokes.
        if stroke_path:
            try:
                from ....layers.layers import GroupRegistry
                registry = GroupRegistry.get()
                if registry is not None:
                    registry.assign_new_stroke(stroke_path)
            except Exception:
                pass
        self._samples = []
        self._stop_emitting()

    # ----- Per-frame ticking -----

    def _start_emitting(self) -> None:
        if self._update_sub is not None:
            return
        try:
            stream = cast(Any, omni.kit.app.get_app()).get_update_event_stream()
            self._update_sub = stream.create_subscription_to_pop(
                self._on_update,
                name="varun.launcher.ui.three_d_draw_curves.emit",
            )
        except Exception:
            self._update_sub = None

    def _stop_emitting(self) -> None:
        if self._update_sub is not None:
            try:
                self._update_sub.unsubscribe()
            except Exception:
                pass
            self._update_sub = None

    def _on_update(self, e: Any) -> None:
        if not self._drawing:
            return
        try:
            dt = float(e.payload.get("dt", 1.0 / 60.0))
        except Exception:
            dt = 1.0 / 60.0
        self._accum += dt
        if self._accum < EMIT_INTERVAL:
            return
        self._accum = 0.0
        # Rewrite the live curve once per tick if new samples arrived.
        if self._dirty and self._samples:
            self._write_curve(LIVE_STROKE_PATH, self._samples)
            self._dirty = False

    # ----- USD authoring -----

    # Ensure /World/ThreeDDrawCurves exists. Caller owns the EditContext.
    def _ensure_root(self, stage: Any) -> None:
        if not stage.GetPrimAtPath(DRAW_ROOT).IsValid():
            cast(Any, UsdGeom).Xform.Define(stage, Sdf.Path(DRAW_ROOT))

    # Create a UsdGeom.BasisCurves prim at `path` with type=linear,
    # constant width = brush diameter, constant displayColor, and the
    # given control points. Idempotent on `path` (overwrites in place).
    def _define_curve(
        self,
        path: str,
        samples: list[tuple[float, float, float]],
    ) -> None:
        try:
            stage = cast(Any, omni.usd.get_context()).get_stage()
            if stage is None:
                return
            UG = cast(Any, UsdGeom)
            with cast(Any, Usd).EditContext(stage, stage.GetRootLayer()):
                self._ensure_root(stage)
                curves = UG.BasisCurves.Define(stage, Sdf.Path(path))
                # Linear type: each segment is a straight ribbon between
                # consecutive control points. Cheaper than cubic; renders
                # as a flat tube/ribbon following the polyline directly.
                curves.CreateTypeAttr().Set(UG.Tokens.linear)
                curves.CreateWrapAttr().Set(UG.Tokens.nonperiodic)
                # Constant width across the whole curve (single value).
                width = float(BrushConfig.get().sphere_radius) * 2.0
                widths_attr = curves.CreateWidthsAttr()
                widths_attr.Set(cast(Any, Vt).FloatArray([width]))
                try:
                    curves.SetWidthsInterpolation("constant")
                except Exception:
                    pass
                # Constant displayColor (single value covers the prim).
                color_attr = curves.CreateDisplayColorAttr()
                color_attr.Set(
                    cast(Any, Vt).Vec3fArray([Gf.Vec3f(*STROKE_COLOR)])
                )
                try:
                    curves.GetDisplayColorPrimvar().SetInterpolation("constant")
                except Exception:
                    pass
                self._write_points(curves, samples)
        except Exception as exc:
            LOGGER.warning("3D Draw (curves): define failed: %s", exc)

    # Rewrite only the `points` + `curveVertexCounts` attrs of an existing
    # BasisCurves prim. Width / color / type are set once at define time
    # and never re-uploaded.
    def _write_curve(
        self,
        path: str,
        samples: list[tuple[float, float, float]],
    ) -> None:
        try:
            stage = cast(Any, omni.usd.get_context()).get_stage()
            if stage is None:
                return
            prim = stage.GetPrimAtPath(path)
            if not prim.IsValid():
                return
            curves = cast(Any, UsdGeom).BasisCurves(prim)
            with cast(Any, Usd).EditContext(stage, stage.GetRootLayer()):
                self._write_points(curves, samples)
        except Exception as exc:
            LOGGER.warning("3D Draw (curves): write failed: %s", exc)

    # Push `samples` (Python list of (x,y,z)) to the curve's points +
    # curveVertexCounts attrs. Uses numpy + FromNumpy to avoid pybind's
    # per-element boxing penalty (same trick the production tool uses).
    def _write_points(self, curves: Any, samples: list[tuple[float, float, float]]) -> None:
        n = len(samples)
        if n < 1:
            return
        arr = np.asarray(samples, dtype=np.float32).reshape(-1, 3)
        curves.GetPointsAttr().Set(cast(Any, Vt).Vec3fArray.FromNumpy(arr))
        curves.GetCurveVertexCountsAttr().Set(cast(Any, Vt).IntArray([n]))

    # Remove any prim at `path` from the root layer.
    def _delete_prim(self, path: str) -> None:
        try:
            stage = cast(Any, omni.usd.get_context()).get_stage()
            if stage is None:
                return
            with cast(Any, Usd).EditContext(stage, stage.GetRootLayer()):
                if stage.GetPrimAtPath(path).IsValid():
                    stage.RemovePrim(Sdf.Path(path))
        except Exception:
            pass

    # Pick a unique `Stroke_NN` path under DRAW_ROOT.
    def _allocate_stroke_path(self, stage: Any) -> str:
        try:
            with cast(Any, Usd).EditContext(stage, stage.GetRootLayer()):
                self._ensure_root(stage)
            return cast(Any, omni.usd.get_stage_next_free_path)(
                stage, f"{DRAW_ROOT}/Stroke", False
            )
        except Exception:
            return f"{DRAW_ROOT}/Stroke"
