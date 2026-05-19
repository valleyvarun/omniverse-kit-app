"""3D Draw freehand-drawing tool (the toolbar entry point).

Helper modules:
  - `brush_config.py`         singleton state (radius, listeners).
  - `cursor_3d.py`            viewport cursor + scene + gestures.
  - `stroke_resampler.py`     polyline -> uniformly spaced stamps (pure math).
  - `viewport_suppression.py` hide selection / context-menu while active.
  - `escape_handler.py`       deactivate on Esc.

Architecture:
  - `activate()` shows the cursor, suppresses viewport interactions, and
    starts listening for keyboard + brush-config changes.
  - On LMB-down the tool creates a temp `_LiveStroke` Xform parent and
    its first chunk `UsdGeom.Points` prim, then starts a per-frame tick.
    Each tick resamples the cursor polyline into stamps, appends sphere
    positions to a numpy buffer, and rewrites ONLY the active chunk's
    points attr. Once a chunk fills (CHUNK_SPHERE_LIMIT), it is frozen
    and a new sibling chunk is opened.
  - On LMB-up the temp parent is deleted and a single final
    `UsdGeom.Points` prim `Stroke_NN` is created with the full buffer.
  - Geometry: each "sphere" is a `UsdGeom.Points` point rendered by
    Hydra as a screen-facing disk (~2 triangles), NOT a tessellated
    sphere mesh (~200-500 tris). This is roughly 50-250x cheaper per
    instance in vertex-shader work and looks identical at the small
    radii used for drawing.
  - `deactivate()` reverses everything from `activate()`.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import numpy as np
import omni.kit.app
import omni.usd
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt

from .brush_config import BrushConfig
from .cursor_3d import Cursor3D
from .escape_handler import EscapeHandler
from .stroke_resampler import resample_polyline
from ...active_context import get_active_stage, get_active_usd_context
from .tool_settings import (
    CHUNK_SPHERE_LIMIT,
    DRAW_ROOT,
    EMIT_INTERVAL,
    ICONS_DIR,
    INITIAL_STROKE_CAPACITY,
    LIVE_STROKE_PATH,
    SAMPLE_MIN_DISTANCE,
    SPHERE_COLOR,
)
from .viewport_suppression import ViewportSuppression
from ..tool import Tool


LOGGER = logging.getLogger(__name__)


class ThreeDDrawTool:
    NAME = "three_d_draw"
    LABEL = "3D"
    SHORTCUT = "D"
    TOOLTIP = "3D Draw"
    ICON_NAME = "3d_draw.svg"

    def __init__(self) -> None:
        self._tool: Tool | None = None
        self._is_active = False
        self._cursor: Cursor3D | None = None
        # ----- Drawing state -----
        # Whether the cursor's LMB is currently held.
        self._drawing = False
        # Latest cursor world-space position (for the per-tick polyline tail).
        self._draw_pos: tuple[float, float, float] | None = None
        # Per-stroke cursor samples (this tick's segment of the polyline).
        self._stroke_samples: list[tuple[float, float, float]] = []
        # Last emitted stamp center, carried across ticks for spacing continuity.
        self._last_stamp_pos: tuple[float, float, float] | None = None
        # Distance walked since `_last_stamp_pos` (carry into resample_polyline).
        self._stamp_carry = 0.0
        # Per-frame tick accumulator (gates emission to EMIT_INTERVAL).
        self._accum = 0.0
        # Per-frame update subscription handle.
        self._update_sub: Any = None
        # ----- Stroke buffer -----
        # Pre-allocated numpy float32 (capacity, 3) buffer; capacity-doubling.
        self._positions: Any = None
        self._count: int = 0
        # Chunk paths created under LIVE_STROKE_PATH for the in-progress
        # stroke. The last entry is the active chunk being written each tick.
        self._chunk_paths: list[str] = []
        # Buffer index (sphere count) where the active chunk begins.
        # Active chunk's slice is `self._positions[_chunk_buf_start:_count]`.
        self._chunk_buf_start: int = 0
        # ----- Helpers -----
        # Hide selection / context-menu layers while the tool is active.
        self._viewport_suppression = ViewportSuppression()
        # Esc key -> deactivate.
        self._escape = EscapeHandler()
        # Listener cb subscribed on BrushConfig while the tool is active.
        self._brush_listener: Any = None

    # BUILD THE TOOLBAR TOOL DESCRIPTOR.
    def make_tool(self) -> Tool:
        icon_path = ICONS_DIR / self.ICON_NAME
        tool = Tool(
            name=self.NAME,
            icon=str(icon_path) if icon_path.exists() else None,
            icon_text=None if icon_path.exists() else self.LABEL,
            shortcut=self.SHORTCUT,
            tooltip=self.TOOLTIP,
            on_click=self._on_clicked,
            toggleable=True,
            # Mutual exclusion: if another toggleable tool is picked while we
            # are active, tear down our cursor / brush listeners cleanly.
            on_deactivate=self.deactivate,
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

    # ACTIVATE THE TOOL: CLEAR SELECTION, SUPPRESS VIEWPORT INTERACTIONS, SHOW CURSOR.
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
        # Tell the Tool Properties panel we're active and listen for slider edits.
        cfg = BrushConfig.get()
        cfg.set_active(True)
        self._brush_listener = self._on_brush_config_changed
        cfg.add_listener(self._brush_listener)

    # DEACTIVATE THE TOOL AND RESTORE VIEWPORT STATE.
    def deactivate(self) -> None:
        if not self._is_active:
            return
        self._is_active = False
        if self._tool is not None:
            self._tool.set_active(False)
        # Drop BrushConfig hook + flag the panel inactive.
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
            usd_context = get_active_usd_context()
            selection = usd_context.get_selection()
            selection.clear_selected_prim_paths()
        except Exception as exc:
            LOGGER.warning("3D Draw: could not clear selection: %s", exc)

    # BRUSHCONFIG CHANGE: REBUILD THE CURSOR SCENE SO RING + GRID PICK UP NEW VALUES.
    # (Sphere-radius changes are picked up automatically on the next stroke,
    # and don't require a cursor rebuild.)
    def _on_brush_config_changed(self) -> None:
        if not self._is_active or self._cursor is None:
            return
        # Tear down the current cursor scene...
        self._cursor.clear_listener()
        self._cursor.hide()
        self._cursor = None
        # ...then re-create it; the new _CursorScene snapshots fresh values.
        self._cursor = Cursor3D()
        self._cursor.show()
        self._cursor.set_listener(
            on_begin=self._on_draw_begin,
            on_change=self._on_draw_change,
            on_end=self._on_draw_end,
        )

    # ----- Drawing (sphere emission while LMB held) -----

    # CURSOR LMB-DOWN: START A NEW STROKE AND EMIT THE FIRST STAMP.
    def _on_draw_begin(self, pos: tuple[float, float, float]) -> None:
        self._draw_pos = pos
        self._stroke_samples = [pos]
        self._last_stamp_pos = None
        self._stamp_carry = 0.0
        self._drawing = True
        # Reset the per-stroke buffer + chunk bookkeeping.
        self._positions = None
        self._count = 0
        self._chunk_paths = []
        self._chunk_buf_start = 0
        # Defensive: clear any leftover live-stroke parent from a prior session.
        self._delete_prim(LIVE_STROKE_PATH)
        # Create the live-stroke parent + first empty chunk Points prim.
        self._create_live_parent()
        first_chunk = self._create_chunk_points(0)
        if first_chunk is not None:
            self._chunk_paths.append(first_chunk)
        # Force immediate processing on the first tick.
        self._accum = EMIT_INTERVAL
        self._tick(0.0)
        self._start_emitting()

    # CURSOR MOVE: APPEND THE POSITION TO THE CURRENT STROKE'S SAMPLE BUFFER.
    # Throttled by distance: drops samples that haven't moved at least
    # SAMPLE_MIN_DISTANCE world units, so the polyline doesn't flood when
    # the OS sends hundreds of mouse-move events per second.
    def _on_draw_change(self, pos: tuple[float, float, float]) -> None:
        self._draw_pos = pos
        last = self._stroke_samples[-1] if self._stroke_samples else None
        if last is not None:
            dx = pos[0] - last[0]
            dy = pos[1] - last[1]
            dz = pos[2] - last[2]
            if dx * dx + dy * dy + dz * dz < SAMPLE_MIN_DISTANCE * SAMPLE_MIN_DISTANCE:
                return
        self._stroke_samples.append(pos)

    # CURSOR LMB-UP: FLUSH REMAINING SAMPLES, COLLAPSE CHUNKS INTO ONE
    # FINAL POINTS PRIM, ASSIGN TO THE GROUP REGISTRY.
    def _on_draw_end(self) -> None:
        # Flush remaining samples into the buffer + the active chunk.
        if self._drawing:
            self._tick(EMIT_INTERVAL)
        self._drawing = False
        # Replace the chunked live stroke with a single final Points prim.
        stroke_path: str | None = None
        if self._count > 0:
            stroke_path = self._finalize_stroke()
        # Always remove the temp parent (whether or not we created a final).
        self._delete_prim(LIVE_STROKE_PATH)
        # Hand the finished stroke to the layers/groups registry.
        if stroke_path:
            try:
                from ...layers.layers import GroupRegistry
                registry = GroupRegistry.get()
                if registry is not None:
                    registry.assign_new_stroke(stroke_path)
            except Exception:
                pass
        # Reset state.
        self._chunk_paths = []
        self._chunk_buf_start = 0
        self._stroke_samples = []
        self._last_stamp_pos = None
        self._stamp_carry = 0.0
        self._stop_emitting()

    # SUBSCRIBE TO PER-FRAME UPDATES SO _TICK RUNS WHILE DRAWING.
    def _start_emitting(self) -> None:
        if self._update_sub is not None:
            return
        try:
            app = cast(Any, omni.kit.app.get_app())
            stream = app.get_update_event_stream()
            self._update_sub = stream.create_subscription_to_pop(
                self._on_update, name="varun.launcher.ui.three_d_draw.emit"
            )
        except Exception:
            self._update_sub = None

    # UNSUBSCRIBE THE PER-FRAME UPDATE.
    def _stop_emitting(self) -> None:
        if self._update_sub is not None:
            try:
                self._update_sub.unsubscribe()
            except Exception:
                pass
            self._update_sub = None

    # PER-FRAME CALLBACK: FORWARD DT INTO THE STAMP SCHEDULER.
    def _on_update(self, e: Any) -> None:
        if not self._drawing:
            return
        try:
            dt = float(e.payload.get("dt", 1.0 / 60.0))
        except Exception:
            dt = 1.0 / 60.0
        self._tick(dt)

    # SCHEDULE AND ACCUMULATE STAMPS FOR THE CURRENT STROKE.
    # Each tick appends new sphere positions to the per-stroke numpy
    # buffer, then writes ONLY the active chunk's slice (positions added
    # since the chunk was opened) to that chunk's Points prim. When a
    # chunk fills CHUNK_SPHERE_LIMIT, it is frozen (never written again --
    # Hydra keeps a stable GPU buffer for it) and a new sibling chunk is
    # opened. Per-tick GPU upload is bounded by the chunk size, so per-
    # stroke total work is O(N) instead of the O(N^2) of one big instancer.
    def _tick(self, dt: float) -> None:
        # Throttle to EMIT_INTERVAL.
        self._accum += dt
        if self._accum < EMIT_INTERVAL:
            return
        self._accum = 0.0
        # Build this tick's polyline from the last stamp plus new samples.
        samples = self._stroke_samples
        if not samples:
            return
        polyline: list[tuple[float, float, float]] = []
        if self._last_stamp_pos is not None:
            polyline.append(self._last_stamp_pos)
        polyline.extend(samples)
        # Carry only the latest sample into the next tick.
        self._stroke_samples = [samples[-1]]
        if len(polyline) < 2:
            # First tick: emit a single stamp at the starting point.
            stamps = [polyline[0]]
            self._last_stamp_pos = polyline[0]
            self._stamp_carry = 0.0
        else:
            # Resample the polyline into uniformly spaced stamps.
            spacing = float(BrushConfig.get().stamp_spacing)
            stamps, self._last_stamp_pos, self._stamp_carry = resample_polyline(
                polyline, spacing, self._stamp_carry,
                self._last_stamp_pos is None,
            )

        if not stamps:
            return
        # One sphere per stamp -- the brush IS the sphere. No grid expansion.
        positions = stamps
        if not positions:
            return

        # Append to the pre-allocated numpy buffer.
        try:
            new_arr = np.asarray(positions, dtype=np.float32).reshape(-1, 3)
            self._append_positions(new_arr)
        except Exception as exc:
            LOGGER.warning("3D Draw: failed to append stroke positions: %s", exc)
            return

        # Write the active chunk's slice and roll over once it's full.
        self._write_active_chunk()
        if (self._count - self._chunk_buf_start) >= CHUNK_SPHERE_LIMIT:
            self._roll_chunk()

    # ----- Buffer / USD authoring / orphan cleanup -----

    # Append (M, 3) float32 positions to the per-stroke buffer.
    # Capacity-doubles on overflow so per-tick append is amortized O(1).
    def _append_positions(self, new_arr: Any) -> None:
        m = int(new_arr.shape[0])
        if m <= 0:
            return
        n = self._count
        needed = n + m
        if self._positions is None:
            cap = max(INITIAL_STROKE_CAPACITY, needed)
            self._positions = np.empty((cap, 3), dtype=np.float32)
        elif int(self._positions.shape[0]) < needed:
            cap = int(self._positions.shape[0])
            while cap < needed:
                cap *= 2
            new_buf = np.empty((cap, 3), dtype=np.float32)
            new_buf[:n] = self._positions[:n]
            self._positions = new_buf
        self._positions[n:needed] = new_arr
        self._count = needed

    # Create the temp Xform parents that hold the live stroke's chunks.
    # No shared prototype is needed -- each chunk is a self-contained
    # UsdGeom.Points prim.
    def _create_live_parent(self) -> None:
        try:
            stage = get_active_stage()
            if stage is None:
                return
            with cast(Any, Usd).EditContext(stage, stage.GetRootLayer()):
                if not stage.GetPrimAtPath(DRAW_ROOT).IsValid():
                    cast(Any, UsdGeom).Xform.Define(stage, Sdf.Path(DRAW_ROOT))
                if not stage.GetPrimAtPath(LIVE_STROKE_PATH).IsValid():
                    cast(Any, UsdGeom).Xform.Define(stage, Sdf.Path(LIVE_STROKE_PATH))
        except Exception as exc:
            LOGGER.warning("3D Draw: failed to create live parent: %s", exc)

    # Create chunk N as a child UsdGeom.Points prim under LIVE_STROKE_PATH.
    # Returns the chunk prim path, or None on failure.
    def _create_chunk_points(self, chunk_index: int) -> str | None:
        try:
            stage = get_active_stage()
            if stage is None:
                return None
            chunk_path = f"{LIVE_STROKE_PATH}/Chunk_{chunk_index:03d}"
            with cast(Any, Usd).EditContext(stage, stage.GetRootLayer()):
                self._define_points_prim(stage, chunk_path)
            return chunk_path
        except Exception as exc:
            LOGGER.warning("3D Draw: failed to create chunk points prim: %s", exc)
            return None

    # Define a UsdGeom.Points prim at `path` with constant-interpolation
    # displayColor + widths (one value covers all points), so per-tick
    # writes only need to update the positions array. Caller owns the
    # EditContext.
    def _define_points_prim(self, stage: Any, path: str) -> Any:
        UG = cast(Any, UsdGeom)
        if not stage.GetPrimAtPath(DRAW_ROOT).IsValid():
            UG.Xform.Define(stage, Sdf.Path(DRAW_ROOT))
        points = UG.Points.Define(stage, Sdf.Path(path))
        # Color via displayColor only -- one constant value for the whole prim.
        color_attr = points.CreateDisplayColorAttr()
        color_attr.Set(cast(Any, Vt).Vec3fArray([Gf.Vec3f(*SPHERE_COLOR)]))
        try:
            points.SetDisplayColorPrimvar().SetInterpolation("constant")
        except Exception:
            pass
        # Width = sphere diameter; one constant value.
        diameter = float(BrushConfig.get().sphere_radius) * 2.0
        widths_attr = points.CreateWidthsAttr()
        widths_attr.Set(cast(Any, Vt).FloatArray([diameter]))
        try:
            points.SetWidthsInterpolation("constant")
        except Exception:
            pass
        # Bind shared FlatPoints MDL material so points render as flat
        # circles (no specular / 3D shading) and skip the lit pipeline.
        try:
            mat = self._ensure_flat_points_material(stage)
            if mat is not None:
                cast(Any, UsdShade).MaterialBindingAPI(points.GetPrim()).Bind(mat)
        except Exception as exc:
            LOGGER.debug("3D Draw: failed to bind FlatPoints material: %s", exc)
        return points

    # Define (idempotent) the shared FlatPoints MDL material under DRAW_ROOT
    # and return it. One material is used for all strokes / chunks so MDL
    # compilation happens at most once per session.
    def _ensure_flat_points_material(self, stage: Any) -> Any:
        US = cast(Any, UsdShade)
        mat_path = Sdf.Path(f"{DRAW_ROOT}/_FlatPointsMat")
        existing = stage.GetPrimAtPath(mat_path)
        if existing and existing.IsValid():
            return US.Material(existing)
        if not stage.GetPrimAtPath(DRAW_ROOT).IsValid():
            cast(Any, UsdGeom).Xform.Define(stage, Sdf.Path(DRAW_ROOT))
        material = US.Material.Define(stage, mat_path)
        shader_path = cast(Any, mat_path).AppendChild("Shader")
        shader = US.Shader.Define(stage, shader_path)
        shader.CreateImplementationSourceAttr().Set(US.Tokens.sourceAsset)
        shader.SetSourceAsset(Sdf.AssetPath("FlatPoints.mdl"), "mdl")
        shader.SetSourceAssetSubIdentifier("FlatPoints", "mdl")
        # Defaults from FlatPoints.mdl are fine (use_display_color=true,
        # convert_srgb_to_linear=true, intensity_scale=5000) -- displayColor
        # on each Points prim drives the rendered color.
        material.CreateSurfaceOutput("mdl").ConnectToSource(
            shader.ConnectableAPI(), "out"
        )
        return material

    # Push the active chunk's slice (buffer[_chunk_buf_start:_count]) into
    # its UsdGeom.Points prim's `points` attr. Widths + color are constant
    # primvars set at chunk creation, so nothing else needs rewriting.
    def _write_active_chunk(self) -> None:
        if not self._chunk_paths:
            return
        chunk_path = self._chunk_paths[-1]
        start = self._chunk_buf_start
        end = self._count
        if end <= start or self._positions is None:
            return
        try:
            stage = get_active_stage()
            if stage is None:
                return
            prim = stage.GetPrimAtPath(chunk_path)
            if not prim.IsValid():
                return
            points = cast(Any, UsdGeom).Points(prim)
            with cast(Any, Usd).EditContext(stage, stage.GetRootLayer()):
                buf = self._positions[start:end]
                points.GetPointsAttr().Set(
                    cast(Any, Vt).Vec3fArray.FromNumpy(buf)
                )
        except Exception as exc:
            LOGGER.warning("3D Draw: failed to write chunk points: %s", exc)

    # Freeze the active chunk and open a new sibling chunk for subsequent
    # ticks. Called from `_tick` once the active chunk hits CHUNK_SPHERE_LIMIT.
    def _roll_chunk(self) -> None:
        next_index = len(self._chunk_paths)
        new_chunk = self._create_chunk_points(next_index)
        if new_chunk is None:
            return
        self._chunk_paths.append(new_chunk)
        self._chunk_buf_start = self._count

    # Collapse all chunks into a single final UsdGeom.Points prim at the
    # next available `Stroke_NN` path. Returns the final stroke path, or
    # None. Caller is responsible for deleting LIVE_STROKE_PATH afterwards.
    def _finalize_stroke(self) -> str | None:
        if self._positions is None or self._count <= 0:
            return None
        try:
            stage = get_active_stage()
            if stage is None:
                return None
            stroke_path = self._allocate_stroke_path(stage)
            with cast(Any, Usd).EditContext(stage, stage.GetRootLayer()):
                points = self._define_points_prim(stage, stroke_path)
                buf = self._positions[: self._count]
                points.GetPointsAttr().Set(
                    cast(Any, Vt).Vec3fArray.FromNumpy(buf)
                )
            return stroke_path
        except Exception as exc:
            LOGGER.warning("3D Draw: failed to finalize stroke: %s", exc)
            return None

    # Remove any prim at `path` from the root layer.
    def _delete_prim(self, path: str) -> None:
        try:
            stage = get_active_stage()
            if stage is None:
                return
            with cast(Any, Usd).EditContext(stage, stage.GetRootLayer()):
                if stage.GetPrimAtPath(path).IsValid():
                    stage.RemovePrim(Sdf.Path(path))
        except Exception:
            pass

    # Pick a unique path under DRAW_ROOT for the new stroke's PointInstancer.
    def _allocate_stroke_path(self, stage: Any) -> str:
        try:
            with cast(Any, Usd).EditContext(stage, stage.GetRootLayer()):
                if not stage.GetPrimAtPath(DRAW_ROOT).IsValid():
                    cast(Any, UsdGeom).Xform.Define(stage, Sdf.Path(DRAW_ROOT))
            base = f"{DRAW_ROOT}/Stroke"
            return cast(Any, omni.usd.get_stage_next_free_path)(stage, base, False)
        except Exception:
            return f"{DRAW_ROOT}/Stroke"
