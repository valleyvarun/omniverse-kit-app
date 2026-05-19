"""Async dock-into-Home helper for the openusd_viewport package."""

import logging
from typing import Any, Callable, cast

import omni.kit.app
import omni.ui as ui
from omni.kit.viewport.window import ViewportWindow


LOGGER = logging.getLogger(__name__)


def set_updates_enabled(vp: ViewportWindow, enabled: bool) -> None:
    """Toggle RTX updates on a ``ViewportWindow``. Silent on failure."""
    try:
        api = cast(Any, vp).viewport_api
    except Exception:
        return
    if api is None:
        return
    try:
        api.updates_enabled = bool(enabled)
    except Exception:
        pass


async def dock_after_delay(
    vp: ViewportWindow,
    title: str,
    ctx_name: str,
    is_current: Callable[[], bool],
    is_still_active: Callable[[ViewportWindow], bool],
    on_set_active_context: Callable[[str], None],
    on_dock_changed: Callable[[str, bool], None] | None,
) -> None:
    """Dock ``vp`` into the Home window once Home has a valid dock_id.

    New ``ui.Window`` instances aren't part of the workspace docking
    graph immediately, so we poll until Home's main dock slot is ready
    and then force ``vp`` into that slot. ``is_current`` aborts on tab-switch;
    ``is_still_active(vp)`` aborts if the user already replaced this vp.
    """
    app = cast(Any, omni.kit.app.get_app())

    # Use Kit's own deferred_dock_in flow: it schedules the dock for the
    # next update tick AFTER the new window has been registered in the
    # workspace. A plain dock_in() on a brand-new ViewportWindow is a
    # no-op because the window doesn't exist in the docking graph yet.
    try:
        cast(Any, vp).deferred_dock_in("Home")
    except Exception:
        LOGGER.exception("deferred_dock_in failed for %s", title)

    # Give Kit a few frames to: (a) register the new window in the
    # workspace, (b) honor the deferred dock request, (c) settle the
    # dock_id. Then explicitly select-in-dock to bring the viewport
    # in front of Home.
    target: Any = None
    target_dock_id: int = -1
    for _ in range(60):
        try:
            await app.next_update_async()
        except Exception:
            return
        if not is_current() or not is_still_active(vp):
            return
        target = ui.Workspace.get_window("Home")
        if target is None:
            continue
        try:
            target_dock_id = int(target.dock_id)
        except Exception:
            target_dock_id = -1
        if target_dock_id < 0:
            continue
        try:
            vp_dock_id = int(cast(Any, vp).dock_id)
        except Exception:
            vp_dock_id = -1
        if vp_dock_id >= 0:
            break

    if target is None or target_dock_id < 0:
        LOGGER.warning("dock_after_delay: Home dock_id never became valid for %s", title)
        return
    if not is_current() or not is_still_active(vp):
        return

    # If deferred_dock_in landed the vp in some other node, fall back to
    # the high-level dock_in (best effort -- dock_id and selected_in_dock
    # are read-only on ViewportWindow, so we can't force them).
    try:
        if int(cast(Any, vp).dock_id) != target_dock_id:
            cast(Any, vp).dock_in(target, ui.DockPosition.SAME)
    except Exception:
        LOGGER.exception("post-deferred dock_in failed for %s", title)

    # Let the dock tree settle before flipping the selection. Without
    # this, focus()/show_window fired on the exact frame dock_id became
    # valid is a no-op and the user has to click the tab a second time
    # to actually bring the viewport in front of Home.
    for _ in range(3):
        try:
            await app.next_update_async()
        except Exception:
            return
        if not is_current() or not is_still_active(vp):
            return

    # Bring the freshly-docked viewport in front of Home. We do this
    # across two frames -- a single focus() on a just-docked window is
    # racy and sometimes leaves Home selected; calling it again on the
    # next frame is the belt-and-suspenders that makes single-click
    # switching reliable.
    for _ in range(2):
        try:
            cast(Any, vp).focus()
        except Exception:
            LOGGER.exception("vp.focus() failed for %s", title)
        try:
            ui.Workspace.show_window(title, True)
        except Exception:
            LOGGER.exception("Workspace.show_window(%s, True) failed", title)
        try:
            await app.next_update_async()
        except Exception:
            break
        if not is_current() or not is_still_active(vp):
            return

    set_updates_enabled(vp, True)
    if ctx_name:
        try:
            on_set_active_context(ctx_name)
        except Exception:
            pass
    if on_dock_changed is not None:
        try:
            on_dock_changed(title, True)
        except Exception:
            pass
