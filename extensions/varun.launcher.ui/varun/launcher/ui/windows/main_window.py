from typing import Any, Callable, cast

from omni.kit.viewport.window import ViewportWindow

from ..startup.home import HomeWindow


# Tab identifiers for the main panel.
TAB_HOME = "Home"
TAB_VIEWPORT = "Viewport"


class MainWindow:
    """Owns the two windows that share the main dock slot (Home + Viewport)
    and tracks which tab is currently active.

    `ViewportWindow` already pauses its own RTX updates when `selected_in_dock`
    is False (built-in behavior in omni.kit.viewport.window). This class makes
    that switch observable so other code can react (e.g. tools that only make
    sense while the viewport is active) and avoids any wasted work in the
    inactive tab.
    """

    def __init__(self) -> None:
        # Create the 3D viewport first, then the Home page on top so Home is
        # the selected tab on app startup. The two windows share dock slot 13
        # (see layouts/default.json).
        self._viewport_window: ViewportWindow | None = ViewportWindow(TAB_VIEWPORT)
        self._home_window: HomeWindow | None = HomeWindow()

        # Active-tab tracking + listeners.
        self._active_tab: str = TAB_HOME
        self._on_active_tab_changed: list[Callable[[str], None]] = []

        # Hook the per-window selected_in_dock callback so we always know
        # which tab is on top. ui.Window exposes set_selected_in_dock_changed_fn.
        def _on_viewport_dock(is_selected: bool) -> None:
            self._on_dock_changed(TAB_VIEWPORT, bool(is_selected))

        def _on_home_dock(is_selected: bool) -> None:
            self._on_dock_changed(TAB_HOME, bool(is_selected))

        viewport_any = cast(Any, self._viewport_window)
        # ViewportWindow registers its own selected_in_dock callback for
        # pausing rendering; chain ours so both run.
        self._prev_viewport_dock_cb = getattr(viewport_any, "_selected_in_dock_changed_fn", None)
        viewport_any.set_selected_in_dock_changed_fn(_on_viewport_dock)

        home_window = self._home_window.window
        if home_window is not None:
            cast(Any, home_window).set_selected_in_dock_changed_fn(_on_home_dock)

        # Force the viewport to stop updating until the user clicks its tab.
        self._set_viewport_updates(False)

    # Public API ----------------------------------------------------------

    @property
    def active_tab(self) -> str:
        return self._active_tab

    def add_active_tab_listener(self, callback: Callable[[str], None]) -> None:
        """Subscribe to active-tab changes. Callback receives the new tab name."""
        self._on_active_tab_changed.append(callback)

    def remove_active_tab_listener(self, callback: Callable[[str], None]) -> None:
        if callback in self._on_active_tab_changed:
            self._on_active_tab_changed.remove(callback)

    # Internal ------------------------------------------------------------

    def _on_dock_changed(self, tab: str, is_selected: bool) -> None:
        # First, let ViewportWindow run its own logic (it already pauses RTX
        # when not selected). Then update our own active-tab state.
        if tab == TAB_VIEWPORT and self._viewport_window is not None:
            # ViewportWindow.__selected_in_dock_changed is private; replicate
            # its single side-effect here so we don't need to reach into it.
            self._set_viewport_updates(is_selected)

        if not is_selected:
            # Only fire on the tab that just became active.
            return
        if tab == self._active_tab:
            return
        self._active_tab = tab
        for cb in list(self._on_active_tab_changed):
            try:
                cb(tab)
            except Exception:  # pragma: no cover - listener errors shouldn't break us
                pass

    def _set_viewport_updates(self, enabled: bool) -> None:
        if self._viewport_window is None:
            return
        viewport_any = cast(Any, self._viewport_window)
        try:
            viewport_api = viewport_any.viewport_api
        except Exception:
            return
        if viewport_api is None:
            return
        try:
            viewport_api.updates_enabled = bool(enabled)
        except Exception:
            pass

    # Lifecycle -----------------------------------------------------------

    def destroy(self) -> None:
        # Tear down both windows when the extension shuts down.
        self._on_active_tab_changed.clear()
        if self._home_window is not None:
            self._home_window.destroy()
            self._home_window = None
        if self._viewport_window is not None:
            self._viewport_window.destroy()
            self._viewport_window = None
