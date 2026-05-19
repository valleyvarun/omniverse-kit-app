"""OpenUSD viewport tab management.

Only ONE ``ViewportWindow`` is alive at any given moment. Tab metadata
(title, UsdContext name, and lightweight viewport state) lives in
``_tabs``; the actual Kit widget is created lazily inside ``focus_tab``
and destroyed inside ``suspend`` / ``close_viewport``. UsdContexts stay
alive across switch-away so the stage and camera prims remain available
when the user comes back, but the Hydra render pipeline (render product,
render targets, scene index -- the GPU-heavy bits) gets fully torn down.
This bounds viewport GPU memory to a single visible viewport regardless
of how many tabs the user opens.

The named UsdContexts are deliberately kept alive after their tab is
closed: ``ManipulatorSelector`` caches a strong C++ pointer per name
and segfaults on the next hot-reload if the context dangles. Context
names use a monotonic counter so a freshly-spawned tab never inherits
a closed tab's contents.

Implementation is split across this package:

* ``tab``         -- ``Tab`` dataclass + shared constants/types.
* ``stage``       -- USD stage lifecycle (init, clear selection, release).
* ``docking``     -- async dock-into-Home helper + updates_enabled toggle.
* ``save_prompt`` -- Save / Don't Save / Cancel dirty-close flow.
"""

import asyncio
import gc
import logging
from typing import Any, Callable, cast

import omni.ui as ui
import omni.usd
from omni.kit.viewport.window import ViewportWindow

from ...active_context import set_active_context_name
from .docking import dock_after_delay, set_updates_enabled
from .save_prompt import show_save_prompt
from .stage import clear_selection, initialize_viewport_stage, release_stage
from .tab import FIRST_VIEWPORT_TITLE, Tab, ViewportDockCallback


LOGGER = logging.getLogger(__name__)


class OpenUsdViewportManager:
    """Owns viewport tabs. Keeps exactly one ``ViewportWindow`` alive."""

    def __init__(
        self,
        on_dock_changed: ViewportDockCallback | None = None,
        auto_create_first: bool = True,
    ) -> None:
        self._on_dock_changed = on_dock_changed

        # Per-tab metadata, in tab-bar order.
        self._tabs: list[Tab] = []
        # The single live ViewportWindow + which tab it backs. None when
        # the user is on Home or right after a tear-down.
        self._active_vp: ViewportWindow | None = None
        self._active_title: str | None = None
        # Monotonic; never decremented. Each tab gets its own
        # viewport_N context, never reused after close.
        self._next_context_index: int = 1
        # In-flight async tasks (stage init + dock-after-delay). Stored
        # so destroy() can cancel them before they touch a torn-down vp.
        self._pending_tasks: list[asyncio.Task[Any]] = []
        # Incremented on every focus/suspend. Async tasks capture the
        # value at scheduling time and abort if it has moved on, so a
        # quick "click A -> click B -> click A" sequence doesn't leave
        # B's dock-after-delay task stomping on A's freshly-spawned vp.
        self._gen: int = 0
        self._alive: bool = True
        if auto_create_first:
            self._register_tab(FIRST_VIEWPORT_TITLE)

    def create_default_viewport(self) -> None:
        """Register the first tab if not already present. No vp creation."""
        if any(t.title == FIRST_VIEWPORT_TITLE for t in self._tabs):
            return
        self._register_tab(FIRST_VIEWPORT_TITLE)

    # PUBLIC API ----------------------------------------------------------

    @property
    def first_viewport_title(self) -> str:
        return FIRST_VIEWPORT_TITLE

    def spawn_new_tab(self) -> str:
        """Register a new tab and immediately focus it.

        Returns the new tab's title.
        """
        index = len(self._tabs) + 1
        # Sequential index may collide with an old tab that was closed,
        # so step past any collisions to keep titles unique.
        while True:
            title = f"{FIRST_VIEWPORT_TITLE} {index}"
            if not any(t.title == title for t in self._tabs):
                break
            index += 1
        self._register_tab(title)
        self.focus_tab(title)
        return title

    def set_updates(self, title: str, enabled: bool) -> None:
        """Enable/pause RTX updates. Only meaningful for the active tab."""
        if self._active_vp is None or self._active_title != title:
            return
        set_updates_enabled(self._active_vp, enabled)

    def focus_tab(self, title: str) -> bool:
        """Make ``title`` the active tab.

        Destroys the currently-active ``ViewportWindow`` (releasing its
        GPU memory) and creates a fresh one bound to ``title``'s
        UsdContext. The stage inside that context, if any, is reused.
        """
        tab = self._find_tab(title)
        if tab is None:
            return False
        if self._active_title == title and self._active_vp is not None:
            # Already active -- just re-raise it in case Home or
            # something else stole focus. Also un-pause if suspend()
            # left RTX disabled. `selected_in_dock` is read-only on
            # ViewportWindow; `focus()` is the supported way to bring
            # a docked window to front.
            set_updates_enabled(self._active_vp, True)
            try:
                cast(Any, self._active_vp).focus()
            except Exception:
                pass
            try:
                ui.Workspace.show_window(title, True)
            except Exception:
                pass
            return True
        self._teardown_active()
        self._spawn(tab)
        return True

    def suspend(self) -> None:
        """Destroy the active ViewportWindow when the user moves to Home."""
        self._teardown_active()

    def close_viewport(self, title: str) -> bool:
        """Drop a tab. If active, tear down its ViewportWindow first."""
        tab = self._find_tab(title)
        if tab is None:
            return False
        if self._active_title == title:
            self._teardown_active()
        try:
            self._tabs.remove(tab)
        except ValueError:
            pass
        # Free the stage's USD + Hydra memory; keep the empty UsdContext
        # alive so ManipulatorSelector's cached C++ pointer doesn't dangle.
        release_stage(tab.ctx_name)
        return True

    def close_viewport_with_prompt(
        self,
        title: str,
        on_confirmed: Callable[[], None] | None = None,
        on_cancelled: Callable[[], None] | None = None,
    ) -> None:
        """Close a tab, prompting Save / Don't Save / Cancel if dirty."""
        tab = self._find_tab(title)
        if tab is None:
            if on_confirmed is not None:
                on_confirmed()
            return

        ctx_name = tab.ctx_name
        ctx: Any = None
        if ctx_name:
            try:
                ctx = cast(Any, omni.usd).get_context(ctx_name)
            except Exception:
                ctx = None

        dirty = False
        if ctx is not None:
            try:
                dirty = bool(ctx.has_pending_edit())
            except Exception:
                dirty = False

        if not dirty:
            self.close_viewport(title)
            if on_confirmed is not None:
                on_confirmed()
            return

        # The save-prompt module needs a way to actually close the tab
        # (without firing on_confirmed itself -- the prompt module wraps
        # that). Pass our raw close_viewport as the do_close hook.
        show_save_prompt(
            title=title,
            ctx=ctx,
            do_close=lambda: self.close_viewport(title),
            on_confirmed=on_confirmed,
            on_cancelled=on_cancelled,
        )

    def destroy(self) -> None:
        # Mark dead first so any coroutine waking mid-tear-down bails
        # before touching USD.
        self._alive = False
        for task in self._pending_tasks:
            try:
                if not task.done():
                    task.cancel()
            except Exception:
                pass
        self._pending_tasks.clear()
        self._teardown_active()
        for tab in self._tabs:
            release_stage(tab.ctx_name)
        self._tabs.clear()

    # INTERNAL ------------------------------------------------------------

    def _register_tab(self, title: str) -> Tab:
        context_name = f"viewport_{self._next_context_index}"
        self._next_context_index += 1
        try:
            cast(Any, omni.usd).create_context(context_name)
        except Exception:
            context_name = ""  # Fall back to global context.
        tab = Tab(title=title, ctx_name=context_name)
        self._tabs.append(tab)
        return tab

    def _find_tab(self, title: str) -> Tab | None:
        for t in self._tabs:
            if t.title == title:
                return t
        return None

    def _teardown_active(self) -> None:
        """Destroy the active ViewportWindow. Stage in its context stays."""
        # Bump generation FIRST so any in-flight dock-after-delay /
        # init coroutine for the vp being torn down aborts cleanly.
        self._gen += 1
        vp = self._active_vp
        tab: Tab | None = None
        ctx_name = ""
        if self._active_title is not None:
            tab = self._find_tab(self._active_title)
            if tab is not None:
                ctx_name = tab.ctx_name
        self._active_vp = None
        self._active_title = None
        if vp is None:
            return
        if tab is not None:
            self._save_viewport_state(tab, vp)
        # Pause updates first so the renderer isn't midway through a frame.
        set_updates_enabled(vp, False)
        # Clear selection so manipulators unregister before destroy().
        # Without this, destroying a multi-context ViewportWindow has
        # been observed to segfault inside manipulator.selector._refresh.
        clear_selection(ctx_name)
        try:
            cast(Any, vp).visible = False
        except Exception:
            pass
        try:
            vp.destroy()
        except Exception as exc:
            LOGGER.warning("ViewportWindow.destroy() failed: %s", exc)
        # Drop the last Python ref BEFORE forcing a GC pass. ViewportWindow
        # teardown leaves the Hydra texture / render product / scene
        # delegate to Python GC, and without a deterministic collection
        # the GPU memory lingers until the next allocation pressure --
        # which is exactly the bug we're fixing.
        del vp
        gc.collect()

    def _spawn(self, tab: Tab) -> None:
        no_title_bar = ui.WINDOW_FLAGS_NO_TITLE_BAR
        if tab.ctx_name:
            vp = ViewportWindow(tab.title, usd_context_name=tab.ctx_name, flags=no_title_bar)
        else:
            vp = ViewportWindow(tab.title, flags=no_title_bar)
        self._active_vp = vp
        self._active_title = tab.title
        self._gen += 1
        my_gen = self._gen
        self._restore_viewport_state(tab, vp)

        # Capture the generation in stale-check closures the async
        # helpers use to bail out cleanly when the user moves on.
        def _is_current() -> bool:
            return self._alive and my_gen == self._gen

        def _is_still_active(check_vp: ViewportWindow) -> bool:
            return self._active_vp is check_vp

        # Stage init runs once per context; bails out early if the stage
        # is already open (re-focus case).
        init_task = asyncio.ensure_future(
            initialize_viewport_stage(tab.ctx_name, _is_current)
        )
        self._pending_tasks.append(init_task)
        init_task.add_done_callback(self._on_task_done)

        # Dock into Home (the main dock slot's owner). Async because
        # the new ui.Window doesn't get a valid dock_id for a few frames.
        dock_task = asyncio.ensure_future(
            dock_after_delay(
                vp,
                tab.title,
                tab.ctx_name,
                is_current=_is_current,
                is_still_active=_is_still_active,
                on_set_active_context=set_active_context_name,
                on_dock_changed=self._on_dock_changed,
            )
        )
        self._pending_tasks.append(dock_task)
        dock_task.add_done_callback(self._on_task_done)

        # Flip the active UsdContext now so Stage / Layer / etc. follow
        # the new tab even before the dock callback runs.
        if tab.ctx_name:
            try:
                set_active_context_name(tab.ctx_name)
            except Exception:
                pass

    def _save_viewport_state(self, tab: Tab, vp: ViewportWindow) -> None:
        """Remember viewport choices before destroying the heavy window."""
        try:
            api = cast(Any, vp).viewport_api
        except Exception:
            api = None
        if api is not None:
            for attr in (
                "camera_path",
                "hydra_engine",
                "render_mode",
                "resolution_scale",
                "fill_frame",
                "lock_to_render_result",
                "display_render_var",
                "render_product_path",
            ):
                try:
                    value = getattr(api, attr)
                except Exception:
                    continue
                if attr == "camera_path":
                    value = str(value) if value else ""
                setattr(tab.state, attr, value)
        try:
            ctx = cast(Any, omni.usd).get_context(tab.ctx_name)
            selection = ctx.get_selection() if ctx is not None else None
            paths = cast(list[Any], selection.get_selected_prim_paths() if selection is not None else [])
            tab.state.selected_paths = [str(path) for path in paths]
        except Exception:
            tab.state.selected_paths = []

    def _restore_viewport_state(self, tab: Tab, vp: ViewportWindow) -> None:
        """Apply saved viewport choices to a newly-created ViewportWindow."""
        try:
            api = cast(Any, vp).viewport_api
        except Exception:
            api = None
        if api is not None:
            for attr in (
                "hydra_engine",
                "render_mode",
                "resolution_scale",
                "fill_frame",
                "lock_to_render_result",
                "display_render_var",
                "render_product_path",
            ):
                value = getattr(tab.state, attr)
                if value is None:
                    continue
                try:
                    setattr(api, attr, value)
                except Exception:
                    pass
            if tab.state.camera_path:
                try:
                    api.camera_path = tab.state.camera_path
                except Exception:
                    pass
        if tab.state.selected_paths:
            try:
                ctx = cast(Any, omni.usd).get_context(tab.ctx_name)
                selection = ctx.get_selection() if ctx is not None else None
                if selection is not None:
                    selection.set_selected_prim_paths(tab.state.selected_paths, False)
            except Exception:
                pass

    def _on_task_done(self, task: "asyncio.Task[Any]") -> None:
        try:
            self._pending_tasks.remove(task)
        except ValueError:
            pass
