"""3D pipe input, bounded mesh updates, and stroke lifecycle."""

# Python & library imports
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, cast

# Omniverse & USD imports
import omni.kit.app
import omni.usd
from pxr import Sdf, Usd, UsdGeom

from tool_box.core.tool import Tool
from tool_box.core.viewport import get_active_stage, get_active_usd_context

# Local imports
from tool_box.tools.drawing.brush import BrushConfig
from tool_box.tools.drawing.cursor import Cursor3D
from tool_box.tools.drawing.curves.mesh import PipeMesh, Vec3
from tool_box.tools.drawing.curves.surface import SurfacePipeMesh
from tool_box.tools.drawing.plane.geometry import PlaneRegistry
from tool_box.tools.drawing.plane.selection import (
    DEFAULT_GROUND_PLANE_PATH,
    PlaneSelection,
)
from tool_box.tools.drawing.viewport_suppression import ViewportSuppression

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

LOGGER: logging.Logger = logging.getLogger(__name__)


# Points and curves share the same stroke parent and brush defaults.
DRAW_ROOT: str = "/World/ThreeDDraw"
LIVE_STROKE_PATH: str = f"{DRAW_ROOT}/_LiveStrokeCurves"
EMIT_INTERVAL: float = 0.016


# ----------------------------------------------------------------------
# 2. 3D PIPE TOOL
# ----------------------------------------------------------------------


class ThreeDDrawCurvesTool:
    NAME: str = "3D pipe"
    LABEL: str = "3D pipe"
    TOOLTIP: str = "3D pipe"

    # Track the cursor, active curve, and pending stroke samples.
    def __init__(self) -> None:
        self._tool: Tool | None = None
        self._is_active: bool = False
        self._stroke_stage: Any = None
        self._cursor: Cursor3D | None = None
        self._drawing: bool = False
        self._mesh: PipeMesh | None = None
        self._dirty: bool = False
        self._update_sub: Any = None
        self._accum: float = 0.0
        self._viewport_suppression: ViewportSuppression = ViewportSuppression()
        self._brush_listener: Callable[[], None] | None = None

    # --- TOOL BUTTON AND ACTIVATION ---

    # BUILD THE TOOLBAR TOOL DESCRIPTOR.
    def make_tool(self) -> Tool:
        tool: Tool = Tool(
            name=self.NAME,
            icon=None,
            icon_text=self.LABEL,
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

    # ACTIVATE: CLEAR SELECTION, SUPPRESS VIEWPORT INTERACTIONS, SHOW CURSOR.
    def activate(self) -> None:
        if self._is_active:
            return
        if get_active_stage() is None:
            return
        from tool_box.tools.drawing.method_runtime import cancel_placement

        cancel_placement()
        self._is_active = True
        if self._tool is not None:
            self._tool.set_active(True)
        self._clear_selection()
        self._viewport_suppression.enter()
        cfg: BrushConfig = BrushConfig.get()
        cfg.set_active(True, "pipe")
        self._cursor = Cursor3D()
        self._cursor.show()
        self._cursor.set_listener(
            on_begin=self._on_draw_begin,
            on_change=self._on_draw_change,
            on_end=self._on_draw_end,
        )
        self._brush_listener = self._on_brush_config_changed
        cfg.add_listener(self._brush_listener)

    # DEACTIVATE AND RESTORE VIEWPORT STATE.
    def deactivate(self) -> None:
        if not self._is_active:
            return
        if self._drawing:
            self._on_draw_end()
        self._is_active = False
        if self._tool is not None:
            self._tool.set_active(False)
        cfg: BrushConfig = BrushConfig.get()
        if self._brush_listener is not None:
            cfg.remove_listener(self._brush_listener)
            self._brush_listener = None
        cfg.set_active(False)
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
            usd_context: Any = get_active_usd_context()
            usd_context.get_selection().clear_selected_prim_paths()
        except Exception as exc:
            LOGGER.warning("3D pipe: could not clear selection: %s", exc, exc_info=True)

    # Update the outline without interrupting a drag.
    # Radius and pressure are sampled for each new point.
    def _on_brush_config_changed(self) -> None:
        if not self._is_active or self._cursor is None:
            return
        self._cursor.refresh()

    # --- STROKE INPUT ---

    def _on_draw_begin(self, pos: Vec3) -> None:
        if self._drawing:
            self._on_draw_end()
        scene: Any = Cursor3D.get_scene()
        viewport_api: Any = scene.viewport_api if scene is not None else None
        self._stroke_stage = get_active_stage(viewport_api)
        if self._stroke_stage is None:
            return
        self._delete_prim(LIVE_STROKE_PATH)
        normal: Vec3 = (
            (0.0, 0.0, 1.0) if str(cast(Any, UsdGeom).GetStageUpAxis(self._stroke_stage)) == "Z" else (0.0, 1.0, 0.0)
        )
        path: str = PlaneSelection.get().active_plane(viewport_api)
        if path != DEFAULT_GROUND_PLANE_PATH:
            info: Any = PlaneRegistry.info_for_path(path, viewport_api)
            if info is not None:
                normal = (float(info[1][0]), float(info[1][1]), float(info[1][2]))
        if scene is not None:
            normal = (float(scene._normal[0]), float(scene._normal[1]), float(scene._normal[2]))
        mesh_type: type[PipeMesh] = SurfacePipeMesh if scene is not None and scene.draw_on_surface else PipeMesh
        self._mesh = mesh_type(
            self._stroke_stage,
            LIVE_STROKE_PATH,
            pos,
            BrushConfig.get().brush_radius,
            normal,
            BrushConfig.get().shape,
            Cursor3D.sample().radius,
        )
        self._dirty = False
        self._drawing = True
        self._accum = EMIT_INTERVAL
        self._start_emitting()

    def _on_draw_change(self, pos: Vec3) -> None:
        if self._drawing and self._mesh is not None:
            self._dirty = self._mesh.add(pos, Cursor3D.sample().radius) or self._dirty

    def _flush_mesh(self) -> None:
        if not self._dirty or self._mesh is None or self._stroke_stage is None:
            return
        with cast(Any, Usd).EditContext(self._stroke_stage, self._stroke_stage.GetRootLayer()):
            self._ensure_root(self._stroke_stage)
            self._mesh.flush()
        self._dirty = False

    def _on_draw_end(self) -> None:
        if not self._drawing:
            return
        stage: Any = self._stroke_stage
        try:
            self._flush_mesh()
            if stage is not None and stage.GetPrimAtPath(LIVE_STROKE_PATH):
                # Preserve the finished meshes without rebuilding their geometry.
                layer: Any = stage.GetRootLayer()
                path: str = self._allocate_stroke_path(stage)
                cast(Any, Sdf).CopySpec(layer, LIVE_STROKE_PATH, layer, path)
        except Exception:
            LOGGER.exception("3D pipe: could not finish stroke")
        finally:
            self._delete_prim(LIVE_STROKE_PATH)
            self._drawing = False
            self._dirty = False
            self._mesh = None
            self._stop_emitting()
            self._stroke_stage = None

    # ----- Per-frame ticking -----

    # --- FRAME UPDATES AND CURVE SAMPLES ---

    # Subscribe to frame updates while drawing a curve.
    def _start_emitting(self) -> None:
        if self._update_sub is not None:
            return
        try:
            stream: Any = cast(Any, omni.kit.app.get_app()).get_update_event_stream()
            self._update_sub = stream.create_subscription_to_pop(
                self._on_update,
                name="tool_box.three_d_draw_curves.emit",
            )
        except Exception:
            LOGGER.debug("Drawing tool SDK operation failed", exc_info=True)
            self._update_sub = None

    # Release the drawing update subscription.
    def _stop_emitting(self) -> None:
        if self._update_sub is not None:
            try:
                self._update_sub.unsubscribe()
            except Exception:
                LOGGER.debug("Drawing tool SDK operation failed", exc_info=True)
            self._update_sub = None

    # Upload at most once per frame; previously completed chunks stay untouched.
    def _on_update(self, e: Any) -> None:
        if not self._drawing:
            return
        dt: float = float(e.payload.get("dt", 1.0 / 60.0))
        self._accum += dt
        if self._accum < EMIT_INTERVAL:
            return
        self._accum = 0.0
        try:
            self._flush_mesh()
        except Exception:
            LOGGER.exception("3D pipe: could not update stroke")

    # --- USD STROKE OWNERSHIP ---

    def _ensure_root(self, stage: Any) -> None:
        if not stage.GetPrimAtPath(DRAW_ROOT).IsValid():
            cast(Any, UsdGeom).Xform.Define(stage, Sdf.Path(DRAW_ROOT))

    # Remove any prim at `path` from the root layer.
    def _delete_prim(self, path: str) -> None:
        try:
            stage: Any = self._stroke_stage
            if stage is None:
                return
            with cast(Any, Usd).EditContext(stage, stage.GetRootLayer()):
                if stage.GetPrimAtPath(path).IsValid():
                    stage.RemovePrim(Sdf.Path(path))
        except Exception:
            LOGGER.debug("Drawing tool SDK operation failed", exc_info=True)

    # Pick a unique `Stroke_NN` path under DRAW_ROOT.
    def _allocate_stroke_path(self, stage: Any) -> str:
        try:
            with cast(Any, Usd).EditContext(stage, stage.GetRootLayer()):
                self._ensure_root(stage)
            return cast(Any, omni.usd.get_stage_next_free_path)(stage, f"{DRAW_ROOT}/Stroke", False)
        except Exception:
            LOGGER.debug("Drawing tool SDK operation failed", exc_info=True)
            return f"{DRAW_ROOT}/Stroke"
