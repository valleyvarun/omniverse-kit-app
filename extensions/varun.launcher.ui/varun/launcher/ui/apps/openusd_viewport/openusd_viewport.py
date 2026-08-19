"""OpenUSD viewport tab management.

Single shared ``UsdContext`` + single ``ViewportWindow``. Each tab
owns an in-memory ``Usd.Stage`` (registered in ``UsdUtils.StageCache``)
and switching tabs simply re-attaches a different stage_id to the
context. No disk round-trip; no per-tab UsdContext (which on this
hardware costs ~1.5 GB VRAM each, regardless of stage contents).

Lifecycle:

* ``__init__``        -- create shared context AND the single
                         ``ViewportWindow``, then dock it into Home in
                         the background. Eager construction is what
                         makes the tab-button click instant: by the
                         time the user hits Viewport, the window is
                         already sized to Home's slot and just needs
                         to be brought to the front.
* first ``focus_tab`` -- bring the existing viewport to front; create
                         the tab's stage, attach + apply light rig.
* subsequent focus    -- bring viewport to front; re-attach the
                         tab's existing stage. No viewport teardown.
* ``suspend``         -- user moved to Home. Kit's
                         ``selected_in_dock_changed_fn`` pauses RTX
                         automatically when Home gets selected.
* ``close_viewport``  -- release that tab's stage from the cache.
* ``destroy``         -- tear down viewport, release every stage.
"""

import asyncio
import gc
import logging
from typing import Any, Callable, cast

import omni.kit.app
import omni.ui as ui
import omni.usd
from omni.kit.viewport.window import ViewportWindow

from ...active_context import set_active_context_name
from .docking import set_updates_enabled
from .save_prompt import show_save_prompt
from .stage import (
    apply_lighting_mode,
    attach_stage,
    clear_selection,
    create_empty_stage,
    get_lighting_mode,
    refresh_stage_id,
    release_stage,
)
from .tab import (
    DEFAULT_LIGHT_RIG,
    FIRST_VIEWPORT_TITLE,
    SHARED_CTX_NAME,
    Tab,
    ViewportDockCallback,
)


LOGGER = logging.getLogger(__name__)


def _suppress_native_tab_bar(vp: ViewportWindow) -> None:
    """Hide Kit's dock-tab strip on ``vp``. Safe to call repeatedly.

    Kit can re-enable the strip on every ``dock_in`` (the new dock node
    inherits defaults from Kit, not from our prior assignment), so this
    has to be re-applied after the viewport lands in Home's slot.
    """
    for attr in ("dock_tab_bar_visible", "dock_tab_bar_enabled"):
        try:
            setattr(vp, attr, False)
        except Exception:
            pass


class OpenUsdViewportManager:
    """Owns viewport tabs. One UsdContext, one ViewportWindow, N stages."""

    def __init__(
        self,
        on_dock_changed: ViewportDockCallback | None = None,
        auto_create_first: bool = True,
        on_tab_renamed: Callable[[str, str], None] | None = None,
    ) -> None:
        self._on_dock_changed = on_dock_changed
        # Fired (old_title, new_title) when a tab is saved to / opened
        # from a file and its label should change to the file name.
        self._on_tab_renamed = on_tab_renamed
        self._tabs: list[Tab] = []
        # The single ViewportWindow, created eagerly below and kept
        # alive until ``destroy``. Eager construction trades a one-time
        # ~1.5 GB VRAM bump at startup for instant tab switching --
        # clicking the Viewport tab no longer has to wait for the
        # window to be allocated and docked.
        self._active_vp: ViewportWindow | None = None
        self._active_title: str | None = None
        # Incremented on every switch so in-flight attach coroutines for
        # superseded clicks can bail.
        self._gen: int = 0
        self._alive: bool = True
        # Serializes attach operations so a rapid A->B->C click sequence
        # doesn't overlap two attaches on the shared context.
        self._lock: asyncio.Lock = asyncio.Lock()
        self._pending_tasks: list[asyncio.Task[Any]] = []
        # Subscription to the shared context's stage events, used to
        # rename the active tab after a Save / Open gives its stage a
        # real file path.
        self._stage_event_sub: Any = None

        try:
            cast(Any, omni.usd).create_context(SHARED_CTX_NAME)
        except Exception as exc:
            LOGGER.warning(
                "Could not create shared context %r: %s",
                SHARED_CTX_NAME, exc,
            )

        self._subscribe_stage_events()

        if auto_create_first:
            self._tabs.append(Tab(title=FIRST_VIEWPORT_TITLE))

        # Build the viewport now, while Home is being constructed in the
        # same module. The dock graph has Home's slot ready by the next
        # frame; we just defer dock_in to that frame so the brand-new
        # window joins Home's node instead of becoming a floater.
        self._build_viewport()

    def create_default_viewport(self) -> None:
        if any(t.title == FIRST_VIEWPORT_TITLE for t in self._tabs):
            return
        self._tabs.append(Tab(title=FIRST_VIEWPORT_TITLE))

    # PUBLIC API ----------------------------------------------------------

    @property
    def first_viewport_title(self) -> str:
        return FIRST_VIEWPORT_TITLE

    def spawn_new_tab(self) -> str:
        index = len(self._tabs) + 1
        while True:
            title = f"{FIRST_VIEWPORT_TITLE} {index}"
            if not any(t.title == title for t in self._tabs):
                break
            index += 1
        self._tabs.append(Tab(title=title))
        self.focus_tab(title)
        return title

    def set_updates(self, title: str, enabled: bool) -> None:
        if self._active_vp is None or self._active_title != title:
            return
        set_updates_enabled(self._active_vp, enabled)

    def focus_tab(self, title: str) -> bool:
        tab = self._find_tab(title)
        if tab is None:
            return False

        if self._active_title == title and self._active_vp is not None:
            # Already active -- just refocus.
            set_updates_enabled(self._active_vp, True)
            self._raise_vp()
            if self._on_dock_changed is not None:
                try:
                    self._on_dock_changed(title, True)
                except Exception:
                    pass
            return True

        # Capture prev tab's viewport_api state synchronously, before
        # the attach swaps the context out from under it.
        prev_tab = self._find_tab(self._active_title) if self._active_title else None
        if prev_tab is not None and self._active_vp is not None:
            prev_tab.stage_id = refresh_stage_id(SHARED_CTX_NAME, prev_tab.stage_id)
            self._save_viewport_state(prev_tab, self._active_vp)

        self._active_title = title
        self._gen += 1
        my_gen = self._gen

        # The viewport was built in ``__init__``; clicking a tab is
        # just bringing the existing window in front of Home and
        # swapping which in-memory stage it shows.
        if self._active_vp is not None:
            set_updates_enabled(self._active_vp, True)
            self._raise_vp()

        try:
            set_active_context_name(SHARED_CTX_NAME)
        except Exception:
            pass
        if self._on_dock_changed is not None:
            try:
                self._on_dock_changed(title, True)
            except Exception:
                pass

        self._spawn_task(self._run_attach(tab, my_gen))
        return True

    def suspend(self) -> None:
        """User moved to Home. Pause RTX; leave the viewport docked."""
        self._gen += 1
        if self._active_vp is not None:
            set_updates_enabled(self._active_vp, False)
        # Capture the outgoing tab's state so a later refocus restores it.
        prev_tab = self._find_tab(self._active_title) if self._active_title else None
        if prev_tab is not None and self._active_vp is not None:
            prev_tab.stage_id = refresh_stage_id(SHARED_CTX_NAME, prev_tab.stage_id)
            self._save_viewport_state(prev_tab, self._active_vp)
        self._active_title = None

    def close_viewport(self, title: str) -> bool:
        tab = self._find_tab(title)
        if tab is None:
            return False
        was_active = self._active_title == title
        try:
            self._tabs.remove(tab)
        except ValueError:
            pass
        if was_active:
            self._active_title = None
            self._gen += 1
        release_stage(tab.stage_id)
        return True

    def close_viewport_with_prompt(
        self,
        title: str,
        on_confirmed: Callable[[], None] | None = None,
        on_cancelled: Callable[[], None] | None = None,
    ) -> None:
        tab = self._find_tab(title)
        if tab is None:
            if on_confirmed is not None:
                on_confirmed()
            return

        # Dirty when: a) user has unsaved edits, OR b) tab was never
        # saved to a real file. Non-active tabs are checked via flag
        # because their stage isn't attached to the context.
        is_active = self._active_title == title
        never_saved = not bool(tab.user_path)
        dirty = never_saved
        ctx: Any = None
        if is_active:
            try:
                ctx = cast(Any, omni.usd).get_context(SHARED_CTX_NAME)
            except Exception:
                ctx = None
            if ctx is not None and not dirty:
                try:
                    dirty = bool(ctx.has_pending_edit())
                except Exception:
                    dirty = False

        if not dirty:
            self.close_viewport(title)
            if on_confirmed is not None:
                on_confirmed()
            return

        # Inactive dirty tab: focus it first so the prompt acts on its stage.
        if not is_active:
            self.focus_tab(title)
            try:
                ctx = cast(Any, omni.usd).get_context(SHARED_CTX_NAME)
            except Exception:
                ctx = None

        if ctx is None:
            self.close_viewport(title)
            if on_confirmed is not None:
                on_confirmed()
            return

        show_save_prompt(
            title=title,
            ctx=ctx,
            do_close=lambda: self.close_viewport(title),
            on_confirmed=on_confirmed,
            on_cancelled=on_cancelled,
        )

    def destroy(self) -> None:
        self._alive = False
        if self._stage_event_sub is not None:
            try:
                self._stage_event_sub.unsubscribe()
            except Exception:
                pass
            self._stage_event_sub = None
        for task in self._pending_tasks:
            try:
                if not task.done():
                    task.cancel()
            except Exception:
                pass
        self._pending_tasks.clear()
        self._teardown_viewport()
        for tab in self._tabs:
            release_stage(tab.stage_id)
        self._tabs.clear()
        self._active_title = None

    # INTERNAL ------------------------------------------------------------

    def _find_tab(self, title: str | None) -> Tab | None:
        if not title:
            return None
        for t in self._tabs:
            if t.title == title:
                return t
        return None

    # TAB RENAME ON SAVE / OPEN -------------------------------------------

    def _subscribe_stage_events(self) -> None:
        try:
            ctx = cast(Any, omni.usd).get_context(SHARED_CTX_NAME)
            stream = ctx.get_stage_event_stream()
            self._stage_event_sub = stream.create_subscription_to_pop(
                self._on_stage_event,
                name="varun.launcher.ui.viewport_tab_rename",
            )
        except Exception:
            self._stage_event_sub = None

    def _on_stage_event(self, event: Any) -> None:
        # Rename the active tab after a Save or Open binds its stage to a
        # real file. Both events also fire for our own in-memory stage
        # attaches, but those carry an ``anon:`` identifier and are
        # ignored in ``_maybe_rename_active_tab``.
        try:
            etype = int(event.type)
        except Exception:
            return
        interesting: list[int] = []
        for attr in ("SAVED", "OPENED"):
            try:
                interesting.append(int(getattr(omni.usd.StageEventType, attr)))
            except Exception:
                pass
        if etype in interesting:
            self._maybe_rename_active_tab()

    @staticmethod
    def _title_from_identifier(identifier: str) -> str:
        """Derive a tab label (the file name) from a root-layer identifier."""
        name = identifier.replace("\\", "/").rstrip("/")
        return name.rsplit("/", 1)[-1]

    def _maybe_rename_active_tab(self) -> None:
        title = self._active_title
        if not title:
            return
        tab = self._find_tab(title)
        if tab is None:
            return
        try:
            ctx = cast(Any, omni.usd).get_context(SHARED_CTX_NAME)
            stage = ctx.get_stage() if ctx is not None else None
            identifier = str(stage.GetRootLayer().identifier) if stage is not None else ""
        except Exception:
            identifier = ""
        # Only real on-disk / server files rename a tab; anonymous
        # in-memory stages (our default tabs) keep their "Viewport" label.
        if not identifier or identifier.startswith("anon:"):
            return

        # First time this tab is bound to THIS file (Save As / Open).
        newly_bound = tab.user_path != identifier

        # Remember the file the tab is now bound to (drives the dirty-close
        # prompt) and re-cache the stage id in case Save As / Open swapped
        # the stage object.
        tab.user_path = identifier
        tab.stage_id = refresh_stage_id(SHARED_CTX_NAME, tab.stage_id)

        # A native Save As / Open re-binds the shared context to a fresh
        # on-disk stage. The viewport keeps showing the pre-save stage
        # (so existing strokes stay visible) while new authoring goes to
        # the new stage Hydra isn't displaying -- which is why strokes
        # drawn after a save vanish. Re-attach the current stage (same
        # path tab-switching uses) so drawing and rendering converge.
        # ``newly_bound`` guards against the re-attach's own OPENED event
        # re-triggering us in a loop.
        if newly_bound and tab.stage_id is not None:
            self._spawn_task(self._resync_after_file_bind(tab, self._gen))

        new_title = self._title_from_identifier(identifier)
        if not new_title or new_title == title:
            return
        # Don't clobber another tab that already uses this label.
        if any(t is not tab and t.title == new_title for t in self._tabs):
            return

        tab.title = new_title
        self._active_title = new_title
        if self._on_tab_renamed is not None:
            try:
                self._on_tab_renamed(title, new_title)
            except Exception:
                pass

    def _spawn_task(self, coro: Any) -> None:
        try:
            task = asyncio.ensure_future(coro)
        except Exception as exc:
            LOGGER.warning("Could not schedule task: %s", exc)
            return
        self._pending_tasks.append(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: "asyncio.Task[Any]") -> None:
        try:
            self._pending_tasks.remove(task)
        except ValueError:
            pass

    async def _run_attach(self, tab: Tab, my_gen: int) -> None:
        async with self._lock:
            if not self._alive or my_gen != self._gen:
                return
            if self._active_vp is not None:
                set_updates_enabled(self._active_vp, False)
            clear_selection(SHARED_CTX_NAME)

            first_focus = tab.stage_id is None
            if first_focus:
                stage_id = create_empty_stage()
                if stage_id is None:
                    return
                tab.stage_id = stage_id

            ok = await attach_stage(SHARED_CTX_NAME, cast(int, tab.stage_id))
            if not ok or not self._alive or my_gen != self._gen:
                return

            # Re-apply this tab's lighting on EVERY focus (not just the
            # first) so switching tabs never silently reverts to Stage
            # Lights. A brand-new tab defaults to the Grey Studio rig;
            # after that we honour whatever the user last chose for it
            # (captured in ``_save_viewport_state`` when they switch away).
            mode = tab.state.lighting_mode
            if mode is None:
                mode = DEFAULT_LIGHT_RIG
            await apply_lighting_mode(SHARED_CTX_NAME, mode)
            if not self._alive or my_gen != self._gen:
                return
            tab.state.lighting_mode = mode

            if first_focus:
                # Light rig may rebind the stage object behind our back;
                # re-cache so future re-attaches use the current one.
                tab.stage_id = refresh_stage_id(SHARED_CTX_NAME, tab.stage_id)

            if self._active_vp is not None:
                self._restore_viewport_state(tab, self._active_vp)
                set_updates_enabled(self._active_vp, True)

    async def _resync_after_file_bind(self, tab: Tab, my_gen: int) -> None:
        """Re-attach ``tab``'s stage so the viewport shows the same stage that
        post-save / post-open authoring targets.

        Without this, strokes drawn right after a native Save As / Open are
        authored into the freshly-bound stage while the viewport is still
        displaying the previous one, so they appear to vanish. Save As also
        gives the stage a new lighting cache, so we re-apply the tab's saved
        lighting mode to keep it from reverting to Stage Lights.
        """
        async with self._lock:
            if not self._alive or my_gen != self._gen or tab.stage_id is None:
                return
            ok = await attach_stage(SHARED_CTX_NAME, cast(int, tab.stage_id))
            if not ok or not self._alive or my_gen != self._gen:
                return
            mode = tab.state.lighting_mode
            if mode is None:
                mode = DEFAULT_LIGHT_RIG
            await apply_lighting_mode(SHARED_CTX_NAME, mode)

    def _build_viewport(self) -> None:
        """Construct + dock the single ViewportWindow at manager init time.

        ``visible=False`` for the first few frames so the user never
        sees the brand-new window as a centered floater; the async
        helper below flips it on after Kit has settled the dock graph.
        Because this all happens at startup (in parallel with Home and
        QuickLayout's restore), it's invisible to the user -- the
        viewport is fully docked and just waiting in the background by
        the time they ever click its tab.
        """
        flags = ui.WINDOW_FLAGS_NO_TITLE_BAR
        try:
            vp = ViewportWindow(
                FIRST_VIEWPORT_TITLE,
                usd_context_name=SHARED_CTX_NAME,
                flags=flags,
                visible=False,
            )
        except Exception as exc:
            LOGGER.warning("ViewportWindow construction failed: %s", exc)
            return
        self._active_vp = vp

        _suppress_native_tab_bar(vp)
        # Pause RTX until the user actually focuses the viewport tab.
        # Kit's dock-state handler will re-enable it once the user
        # selects the tab, but doing it explicitly here covers the
        # window of frames before the first ``__dock_changed`` fires.
        set_updates_enabled(vp, False)

        # Tell Kit to dock this window the moment Home is in the dock
        # graph. ``deferred_dock_in`` is fire-and-forget; the async
        # task below just waits for the dock to actually land before
        # making the window visible (otherwise the user sees a
        # full-screen floater for ~1s while Kit catches up).
        try:
            cast(Any, vp).deferred_dock_in("Home")
        except Exception:
            LOGGER.exception("deferred_dock_in('Home') failed")

        task = asyncio.ensure_future(self._await_initial_dock(vp))
        self._pending_tasks.append(task)
        task.add_done_callback(self._on_task_done)

    async def _await_initial_dock(self, vp: ViewportWindow) -> None:
        """Wait for ``vp`` to STABLY land in Home's dock, then reveal it.

        QuickLayout's ``restore_workspace`` runs a few frames into
        startup and can briefly undock/redock things, so we don't
        trust the very first ``docked == True`` reading. We wait
        until the docked state has held for several consecutive
        frames, then make the viewport visible. If the dock never
        lands we fall back to an explicit ``dock_in(Home, SAME)``.

        The viewport is kept ``visible=False`` for the whole wait so
        the user never sees a 1280x720 floater while Kit settles.
        """
        app = cast(Any, omni.kit.app.get_app())

        # Phase 1: wait for the docked state to stabilize. The
        # consecutive-True requirement guards against QuickLayout
        # briefly undocking us mid-restore.
        stable_target = 10
        stable = 0
        for _ in range(240):
            try:
                await app.next_update_async()
            except Exception:
                return
            if not self._alive or self._active_vp is not vp:
                return
            if self._vp_is_home_docked(vp):
                stable += 1
                if stable >= stable_target:
                    break
            else:
                stable = 0
        else:
            stable = 0

        # Phase 2: if we never got a stable dock, force one.
        if stable < stable_target:
            LOGGER.warning(
                "viewport never auto-docked into Home; falling back to "
                "explicit dock_in()"
            )
            home = ui.Workspace.get_window("Home")
            if home is not None:
                try:
                    cast(Any, vp).dock_in(home, ui.DockPosition.SAME)
                except Exception:
                    LOGGER.exception("fallback dock_in(Home) failed")
                for _ in range(30):
                    try:
                        await app.next_update_async()
                    except Exception:
                        return
                    if self._vp_is_home_docked(vp):
                        break

        if not self._alive or self._active_vp is not vp:
            return

        # Re-apply tab-bar suppression once the viewport actually
        # joined Home's dock node (Kit resets dock properties when a
        # window joins a new node).
        _suppress_native_tab_bar(vp)
        # Only reveal the viewport if it's actually docked into Home.
        # Showing an undocked viewport at startup is the "huge random
        # floater" bug; better to leave it hidden than to flash a
        # 1280x720 black square over the layout. ``focus_tab`` will
        # try again the next time the user clicks the tab.
        if self._vp_is_home_docked(vp):
            try:
                cast(Any, vp).visible = True
            except Exception:
                LOGGER.exception("vp.visible = True failed")
        # Stay paused -- Home is still on top. Kit's
        # ``__selected_in_dock_changed`` callback will flip
        # updates_enabled back on once the user clicks our tab.
        set_updates_enabled(vp, False)

    @staticmethod
    def _vp_is_home_docked(vp: ViewportWindow) -> bool:
        """True iff ``vp`` is docked into the same node as Home."""
        try:
            if not bool(cast(Any, vp).docked):
                return False
        except Exception:
            return False
        try:
            vp_dock = int(cast(Any, vp).dock_id)
        except Exception:
            return False
        home = ui.Workspace.get_window("Home")
        if home is None:
            return False
        try:
            home_dock = int(cast(Any, home).dock_id)
        except Exception:
            return False
        return vp_dock >= 0 and vp_dock == home_dock

    def _raise_vp(self) -> None:
        if self._active_vp is None:
            return
        vp = self._active_vp
        # Last-resort fallback: if _await_initial_dock failed (e.g. on
        # an older session whose workspace restore stranded the
        # viewport as a floater), redock NOW so making it visible
        # doesn't spawn a full-screen floater over the rest of the UI.
        if not self._vp_is_home_docked(vp):
            home = ui.Workspace.get_window("Home")
            if home is not None:
                try:
                    cast(Any, vp).dock_in(home, ui.DockPosition.SAME)
                except Exception:
                    LOGGER.exception("emergency dock_in(Home) failed")
                _suppress_native_tab_bar(vp)
        # Cover the case where the user clicks before _await_initial_dock
        # has flipped visible -- the focus() call below is a no-op on a
        # hidden window, so make sure it's shown first.
        try:
            cast(Any, vp).visible = True
        except Exception:
            pass
        try:
            cast(Any, vp).focus()
        except Exception:
            pass
        try:
            ui.Workspace.show_window(FIRST_VIEWPORT_TITLE, True)
        except Exception:
            pass

    def _teardown_viewport(self) -> None:
        vp = self._active_vp
        self._active_vp = None
        if vp is None:
            return
        set_updates_enabled(vp, False)
        try:
            cast(Any, vp).visible = False
        except Exception:
            pass
        try:
            vp.destroy()
        except Exception as exc:
            LOGGER.warning("ViewportWindow.destroy() failed: %s", exc)
        del vp
        gc.collect()

    def _save_viewport_state(self, tab: Tab, vp: ViewportWindow) -> None:
        # Capture the tab's current lighting mode so it's restored (not
        # reset to Stage Lights) the next time the user comes back to it.
        # Done before the viewport_api guard so it runs even if the api
        # isn't available yet.
        current_mode = get_lighting_mode(SHARED_CTX_NAME)
        if current_mode is not None:
            tab.state.lighting_mode = current_mode

        try:
            api = cast(Any, vp).viewport_api
        except Exception:
            api = None
        if api is None:
            return
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

    def _restore_viewport_state(self, tab: Tab, vp: ViewportWindow) -> None:
        try:
            api = cast(Any, vp).viewport_api
        except Exception:
            api = None
        if api is None:
            return
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
