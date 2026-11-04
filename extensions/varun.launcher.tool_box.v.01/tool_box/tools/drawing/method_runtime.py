"""Coordinate canvas choices and cursor views without changing the brush tools."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

import omni.kit.app

from tool_box.core.viewport import get_active_stage, get_active_viewport_api
from tool_box.tools.drawing.brush import BrushConfig
from tool_box.tools.drawing.cursor_camera import CursorView
from tool_box.tools.drawing.methods import (
    CursorAnchor,
    DrawingMethods,
    OptionName,
    Vec3,
)
from tool_box.tools.drawing.viewport_suppression import ViewportSuppression

if TYPE_CHECKING:
    from tool_box.tools.drawing.cursor import Cursor3D

_views: CursorView | None = None
_placement_cursor: Cursor3D | None = None
_placement_task: asyncio.Task[None] | None = None
_placement_suppression: ViewportSuppression | None = None


def get_anchor(viewport: Any = None) -> CursorAnchor:
    stage: Any = get_active_stage(viewport)
    key: str = str(stage.GetRootLayer().identifier) if stage is not None else ""
    return CursorAnchor.get(key)


def _finish() -> None:
    from tool_box.tools.drawing.cursor import Cursor3D

    Cursor3D.finish_gestures()


def _refresh() -> None:
    from tool_box.tools.drawing.cursor import Cursor3D

    Cursor3D.refresh_methods()


def set_option(name: OptionName, enabled: bool) -> None:
    global _views
    _finish()
    profile: DrawingMethods = DrawingMethods.get(BrushConfig.get().current_tool)
    profile.set_option(name, enabled)
    if name == "cursor_3d" and not enabled:
        cancel_placement()
        if _views is not None:
            _views.destroy()
            _views = None
    _refresh()


def toggle_option(name: OptionName) -> None:
    profile: DrawingMethods = DrawingMethods.get(BrushConfig.get().current_tool)
    set_option(name, not bool(getattr(profile, name)))


def place_cursor() -> None:
    global _placement_cursor, _placement_task, _placement_suppression
    from tool_box.tools.drawing.cursor import Cursor3D

    if get_active_stage() is None:
        return
    _finish()
    DrawingMethods.get().set_option("cursor_3d", True)
    get_anchor().request_placement()
    if not BrushConfig.get().active:
        if _placement_cursor is None:
            _placement_cursor = Cursor3D()
            _placement_cursor.clear_listener()
            _placement_cursor.show()
        if _placement_suppression is None:
            _placement_suppression = ViewportSuppression()
            _placement_suppression.enter()
        if _placement_task is None or _placement_task.done():
            _placement_task = asyncio.ensure_future(_wait_for_placement(get_anchor()))
    _refresh()


def set_cursor_position(position: Vec3) -> None:
    """Numeric placement uses the same stage anchor and visible marker as a click."""
    global _placement_cursor
    from tool_box.tools.drawing.cursor import Cursor3D

    if get_active_stage() is None:
        return
    _finish()
    get_anchor().place(position)
    if not BrushConfig.get().active and _placement_cursor is None:
        _placement_cursor = Cursor3D()
        _placement_cursor.clear_listener()
        _placement_cursor.show()
    _refresh()


async def _wait_for_placement(anchor: CursorAnchor) -> None:
    global _placement_suppression
    from tool_box.tools.drawing.cursor import Cursor3D

    app: Any = cast(Any, omni.kit.app.get_app())
    while anchor.placing or Cursor3D.gesture_active():
        await app.next_update_async()
    # Keep the marker visible, but release normal viewport interaction after mouse-up.
    if _placement_suppression is not None:
        _placement_suppression.exit()
        _placement_suppression = None
    _refresh()


def cancel_placement() -> None:
    global _placement_cursor, _placement_task, _placement_suppression
    if _placement_task is not None:
        _placement_task.cancel()
        _placement_task = None
    if _placement_cursor is not None:
        _placement_cursor.hide()
        _placement_cursor = None
    if _placement_suppression is not None:
        _placement_suppression.exit()
        _placement_suppression = None
    get_anchor().cancel_placement()
    _refresh()


def switch_view(axis: int) -> bool:
    global _views
    config: BrushConfig = BrushConfig.get()
    profile: DrawingMethods = DrawingMethods.get(config.current_tool)
    viewport: Any = get_active_viewport_api()
    if not profile.cursor_3d or viewport is None:
        return False
    _finish()
    anchor: CursorAnchor = get_anchor(viewport)
    if anchor.position is None:
        place_cursor()
        return False
    selected: int
    side: int
    selected, side = anchor.set_mode(axis)
    views: CursorView = _views if _views is not None else CursorView()
    _views = views
    changed: bool = views.switch(viewport, selected, anchor.position, side)
    _refresh()
    return changed


def destroy() -> None:
    global _views
    _finish()
    cancel_placement()
    if _views is not None:
        _views.destroy()
        _views = None
    get_anchor().cancel_placement()
