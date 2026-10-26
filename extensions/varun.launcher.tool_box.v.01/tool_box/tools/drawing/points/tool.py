"""3D paint stroke input, pressure sampling, and bounded mesh uploads."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import omni.kit.app
import omni.usd
from pxr import Sdf, Usd, UsdGeom

from tool_box.core.tool import Tool
from tool_box.core.viewport import get_active_stage, get_active_usd_context
from tool_box.tools.drawing.brush import BrushConfig
from tool_box.tools.drawing.cursor import Cursor3D
from tool_box.tools.drawing.points.mesh import PaintMesh
from tool_box.tools.drawing.settings import (
    DRAW_ROOT,
    EMIT_INTERVAL,
    ICONS_DIR,
    LIVE_STROKE_PATH,
)
from tool_box.tools.drawing.viewport_suppression import ViewportSuppression

LOGGER: logging.Logger = logging.getLogger(__name__)


class ThreeDDrawTool:
    NAME: str = "3D paint"
    LABEL: str = "3D paint"
    TOOLTIP: str = "3D paint"
    ICON_NAME: str = "brush_oval.png"

    def __init__(self) -> None:
        self._tool: Tool | None = None
        self._is_active: bool = False
        self._stroke_stage: Any = None
        self._cursor: Cursor3D | None = None
        self._drawing: bool = False
        self._mesh: PaintMesh | None = None
        self._dirty: bool = False
        self._update_sub: Any = None
        self._accum: float = 0.0
        self._viewport_suppression: ViewportSuppression = ViewportSuppression()
        self._brush_listener: Callable[[], None] | None = None

    # --- TOOL BUTTON AND ACTIVATION ---

    # BUILD THE TOOLBAR TOOL DESCRIPTOR.
    def make_tool(self) -> Tool:
        icon_path: Path = ICONS_DIR / self.ICON_NAME
        tool: Tool = Tool(
            name=self.NAME,
            icon=str(icon_path) if icon_path.exists() else None,
            icon_text=None if icon_path.exists() else self.LABEL,
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
        if get_active_stage() is None:
            return
        self._is_active = True
        if self._tool is not None:
            self._tool.set_active(True)
        self._clear_selection()
        self._viewport_suppression.enter()
        self._cursor = Cursor3D()
        self._cursor.show()
        self._cursor.set_listener(
            on_begin=self._on_draw_begin,
            on_change=self._on_draw_change,
            on_end=self._on_draw_end,
        )
        # Tell the Tool Properties panel we're active and listen for slider edits.
        cfg: BrushConfig = BrushConfig.get()
        cfg.set_active(True, "paint")
        self._brush_listener = self._on_brush_config_changed
        cfg.add_listener(self._brush_listener)

    # DEACTIVATE THE TOOL AND RESTORE VIEWPORT STATE.
    def deactivate(self) -> None:
        if not self._is_active:
            return
        if self._drawing:
            self._on_draw_end()
        self._is_active = False
        if self._tool is not None:
            self._tool.set_active(False)
        # Drop BrushConfig hook + flag the panel inactive.
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
            selection: Any = usd_context.get_selection()
            selection.clear_selected_prim_paths()
        except Exception:
            LOGGER.exception("3D paint: could not clear selection")

    # Update the outline without interrupting a drag.
    def _on_brush_config_changed(self) -> None:
        if not self._is_active or self._cursor is None:
            return
        self._cursor.refresh()

    def _on_draw_begin(self, pos: tuple[float, float, float]) -> None:
        if self._drawing:
            self._on_draw_end()
        scene: Any = Cursor3D.get_scene()
        self._stroke_stage = get_active_stage(scene.viewport_api if scene is not None else None)
        if self._stroke_stage is None:
            return
        self._delete_prim(LIVE_STROKE_PATH)
        right: tuple[float, float, float] = (1.0, 0.0, 0.0)
        up: tuple[float, float, float] = (0.0, 0.0, -1.0)
        if scene is not None:
            right = (float(scene._basis_r[0]), float(scene._basis_r[1]), float(scene._basis_r[2]))
            up = (float(scene._basis_u[0]), float(scene._basis_u[1]), float(scene._basis_u[2]))
        self._mesh = PaintMesh(self._stroke_stage, LIVE_STROKE_PATH, right, up, BrushConfig.get().shape)
        self._mesh.add(pos, Cursor3D.sample())
        self._dirty = True
        self._drawing = True
        self._accum = EMIT_INTERVAL
        self._flush_mesh()
        self._start_emitting()

    def _on_draw_change(self, pos: tuple[float, float, float]) -> None:
        if self._drawing and self._mesh is not None:
            self._dirty = self._mesh.add(pos, Cursor3D.sample()) or self._dirty

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
            LOGGER.exception("3D paint: could not finish stroke")
        finally:
            self._delete_prim(LIVE_STROKE_PATH)
            self._drawing = False
            self._dirty = False
            self._mesh = None
            self._stop_emitting()
            self._stroke_stage = None

    # --- FRAME UPDATES ---

    # Subscribe to frame updates while painting.
    def _start_emitting(self) -> None:
        if self._update_sub is not None:
            return
        try:
            stream: Any = cast(Any, omni.kit.app.get_app()).get_update_event_stream()
            self._update_sub = stream.create_subscription_to_pop(
                self._on_update,
                name="tool_box.three_d_draw.emit",
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
            LOGGER.exception("3D paint: could not update stroke")

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
