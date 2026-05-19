"""Re-bind Kit's Stage and Layer panels to the active viewport's UsdContext.

Both panels are created once at Kit startup, against whichever UsdContext
`omni.usd.get_context()` returns at that moment (the global, "" context).
They cache that context object internally, so the global-`get_context`
shim in `active_context.py` does NOT help them after construction --
they never re-call `get_context` on their own.

Strategy:

* On every viewport-tab switch, destroy + recreate both panel windows.
  The recreate hits `ui.Workspace.show_window(...)`, which goes through
  each extension's `show_window(value=True)` callback, which constructs
  a fresh `StageWindow` / `LayerWindow` -- and at that moment our shim
  intercepts the unnamed `get_context()` call and routes it to the
  currently-active named context.

* For Stage, that is enough: `StageWindow()` is called with no args, so
  it picks up the shim-redirected context automatically.

* For Layer, the `LayerExtension` instance also caches `_usd_context`
  (and a `_layers` handle derived from it) at startup, and re-creating
  `LayerWindow` reuses those cached values. We mutate them in-place
  before re-showing so the new `LayerWindow` binds to the active
  context.

Both windows preserve their dock position across the destroy/recreate
because they call `deferred_dock_in(...)` in their constructors, which
re-attaches them to whichever dock node has the matching window name.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import omni.kit.app
import omni.ui as ui
import omni.usd

from ..active_context import (
    add_active_context_listener,
    remove_active_context_listener,
)


LOGGER = logging.getLogger(__name__)


# Workspace titles of the Kit-owned panels we manage.
STAGE_WINDOW = "Stage"
LAYER_WINDOW = "Layer"

# Sibling tabs that share the dock group with Stage/Layer. We don't
# destroy these, but we need to know about them so that if the user has
# one of them selected when a tab switch fires, we can restore the
# selection after recreating Stage+Layer (which would otherwise pull the
# Stage tab to the front of the group).
SIBLING_WINDOWS: tuple[str, ...] = ("Clayers",)


class KitPanelRebinder:
    """Keeps Kit's Stage + Layer panels bound to the active viewport's stage."""

    def __init__(self) -> None:
        self._installed = False
        # Coalesce rapid-fire context changes (e.g. dock callbacks firing
        # selected=False on the old tab and selected=True on the new tab
        # back-to-back) so we only rebuild once.
        self._pending_task: asyncio.Task[Any] | None = None

    def apply(self) -> None:
        if self._installed:
            return
        add_active_context_listener(self._on_active_context_changed)
        self._installed = True

    # Trigger a rebuild now (used to do the initial bind after startup).
    def rebind_now(self) -> None:
        self._on_active_context_changed()

    def destroy(self) -> None:
        if not self._installed:
            return
        remove_active_context_listener(self._on_active_context_changed)
        self._installed = False
        task, self._pending_task = self._pending_task, None
        if task is not None and not task.done():
            try:
                task.cancel()
            except Exception:
                pass

    # ---------------------------------------------------------------- internals

    def _on_active_context_changed(self) -> None:
        # Debounce: if a rebuild is already queued, let it run.
        if self._pending_task is not None and not self._pending_task.done():
            return
        try:
            self._pending_task = asyncio.ensure_future(self._rebuild_async())
        except Exception:
            self._pending_task = None

    async def _rebuild_async(self) -> None:
        try:
            # Update LayerExtension's cached context BEFORE the rebuild so
            # the recreated LayerWindow binds to the new context.
            self._retarget_layer_extension()

            # Remember which tab in the Stage/Layer/Clayers dock group is
            # currently selected so we can restore it after recreate.
            # `ui.Workspace.show_window` brings the new Stage tab to the
            # front of its group, which is exactly the behaviour the user
            # complained about; this snapshot lets us undo it.
            previously_selected = self._snapshot_selected_sibling()

            # Tear down both windows. Each extension's
            # `_visibility_changed_fn` schedules an async destroy when
            # `window.visible` flips to False.
            self._hide(STAGE_WINDOW)
            self._hide(LAYER_WINDOW)

            # Wait a few frames so the async destroys actually run before
            # we ask the workspace to show new instances.
            app = cast(Any, omni.kit.app.get_app())
            for _ in range(3):
                try:
                    await app.next_update_async()
                except Exception:
                    return

            # Recreate both windows. `ui.Workspace.show_window(name)`
            # invokes the extension's registered `show_window(True)`,
            # which constructs a fresh window and (for Stage) calls
            # `omni.usd.get_context("")` -- our shim then redirects that
            # to the currently-active named context.
            self._show(STAGE_WINDOW)
            self._show(LAYER_WINDOW)

            # Let the new windows finish docking before we adjust the
            # selected tab; setting `selected_in_dock` before the new
            # Stage window has attached to the dock node is a no-op.
            for _ in range(2):
                try:
                    await app.next_update_async()
                except Exception:
                    return

            # Restore whichever tab the user actually had selected before
            # the rebuild. If nothing was tracked (or that tab no longer
            # exists), leave the workspace's default selection alone.
            if previously_selected:
                self._select_in_dock(previously_selected)
        finally:
            self._pending_task = None

    # Find which window among Stage / Layer / SIBLING_WINDOWS is currently
    # the selected tab in its dock group. Returns the window name, or ""
    # if none of them are selected (or queryable).
    @staticmethod
    def _snapshot_selected_sibling() -> str:
        for name in (STAGE_WINDOW, LAYER_WINDOW, *SIBLING_WINDOWS):
            try:
                win = ui.Workspace.get_window(name)
            except Exception:
                win = None
            if win is None:
                continue
            try:
                if bool(getattr(win, "selected_in_dock", False)):
                    return name
            except Exception:
                continue
        return ""

    # Force the named window to be the selected tab in its dock group.
    @staticmethod
    def _select_in_dock(window_name: str) -> None:
        try:
            win = ui.Workspace.get_window(window_name)
        except Exception:
            win = None
        if win is None:
            return
        try:
            cast(Any, win).selected_in_dock = True
        except Exception:
            try:
                cast(Any, win).focus()
            except Exception:
                pass

    # Swap the Layer extension's cached `_usd_context` (and its derived
    # `_layers` handle) to the active context. The shim's
    # `omni.usd.get_context()` call returns whichever context is active
    # right now.
    @staticmethod
    def _retarget_layer_extension() -> None:
        try:
            from omni.kit.widget.layers.extension import _get_instance  # type: ignore[import-not-found]
        except Exception:
            return
        try:
            ext = _get_instance()
        except Exception:
            ext = None
        if ext is None:
            return

        try:
            new_ctx = omni.usd.get_context()  # shim redirects to active
        except Exception:
            return
        if new_ctx is None:
            return

        try:
            setattr(ext, "_usd_context", new_ctx)
        except Exception:
            return

        # Refresh the `_layers` handle that the extension hands to its
        # menu plumbing. If the import fails we just leave the stale one
        # in place; Layer panel content is what the user cares about.
        try:
            from omni.kit.usd import layers as _layers_mod  # type: ignore[import-not-found]

            setattr(ext, "_layers", _layers_mod.get_layers(new_ctx))
        except Exception:
            pass

    @staticmethod
    def _hide(window_name: str) -> None:
        try:
            win = ui.Workspace.get_window(window_name)
        except Exception:
            win = None
        if win is None:
            return
        try:
            win.visible = False
        except Exception:
            pass

    @staticmethod
    def _show(window_name: str) -> None:
        try:
            ui.Workspace.show_window(window_name)
        except Exception:
            pass
