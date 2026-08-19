"""High-level owner of the main dock slot.

`MainWindow` is intentionally thin: it stitches together the Home page
with the per-app tab managers (currently just the OpenUSD viewport, more
to follow) and keeps track of which tab is on top so other code can
react.

Per-app concerns (creating viewports, pausing RTX updates, naming new
tabs, ...) live inside their own modules under `ui/apps/`.

This module also owns the *fake* tab bar (`MainTabsWindow`) docked above
the main dock slot. Kit's native dock-tab bar is hidden on the dock node
holding Home + the viewports (see `layouts/default.json`); the buttons
in our fake bar drive which pane is on top via
`OpenUsdViewportManager.focus_tab(...)` / `home_window.focus()`. The
Home + viewport windows themselves are created with
`omni.ui.WINDOW_FLAGS_NO_TITLE_BAR` so they have no header of their
own.
"""

from typing import Any, Callable, cast

from ..active_context import (
    HOME_CONTEXT_NAME,
    ensure_home_context,
    set_active_context_name,
)
from ..apps.openusd_viewport import OpenUsdViewportManager
from ..startup.home import HomeWindow
from .destinations.apps_window import AppsWindow
from .main_tabs_window import MainTabsWindow


# Tab identifier for the Home page (viewport tab names are owned by the
# OpenUsdViewportManager).
TAB_HOME = "Home"


class MainWindow:
    """Stitches Home + per-app tabs into the main dock slot."""

    def __init__(self) -> None:
        # Home is not a viewport -- it has no stage. We still bind it to a
        # dedicated empty UsdContext so Kit-owned panels that follow the
        # active context (Stage, Layer, etc.) flip to an empty stage when
        # the user is on the Home tab, instead of holding onto whichever
        # viewport's stage was last shown.
        ensure_home_context()

        # The fake tab bar above the main dock slot. Built BEFORE the
        # viewport manager + Home so the JSON-driven layout has the
        # window available to dock by name on first run.
        self._main_tabs: MainTabsWindow | None = MainTabsWindow(
            on_tab_clicked=self._on_tab_button_clicked,
            on_tab_close=self._on_tab_close_clicked,
        )

        # Create Home FIRST so it owns the main dock slot, then the
        # default viewport. The layout JSON marks Home as
        # `selected_in_dock: true` and Viewport as `false`, so Home
        # wins on startup with no extra focus juggling needed.
        self._home_window: HomeWindow | None = HomeWindow(
            on_logo_clicked=self._on_home_logo_clicked
        )
        self._viewports = OpenUsdViewportManager(
            on_dock_changed=self._on_viewport_dock,
            on_tab_renamed=self._on_viewport_tab_renamed,
        )

        # Seed the tab bar.
        # Home is never closable (no X overlay).
        self._main_tabs.add_tab(TAB_HOME, closable=False)
        self._main_tabs.add_tab(self._viewports.first_viewport_title)
        self._main_tabs.set_active(TAB_HOME)

        # Floating destination windows opened from Home logo buttons.
        # Created lazily on first click (see `_on_home_logo_clicked`).
        self._apps_window: AppsWindow | None = None

        # Active-tab tracking + listeners.
        self._active_tab: str = TAB_HOME
        self._on_active_tab_changed: list[Callable[[str], None]] = []

        # Track when Home is selected; viewport tabs are reported by the
        # OpenUsdViewportManager via _on_viewport_dock.
        home_window = self._home_window.window
        if home_window is not None:
            def _on_home_dock(is_selected: bool) -> None:
                self._on_dock_changed(TAB_HOME, bool(is_selected))

            cast(Any, home_window).set_selected_in_dock_changed_fn(_on_home_dock)

    # PUBLIC API ----------------------------------------------------------

    @property
    def active_tab(self) -> str:
        return self._active_tab

    def add_active_tab_listener(self, callback: Callable[[str], None]) -> None:
        """Subscribe to active-tab changes. Callback receives the new tab name."""
        self._on_active_tab_changed.append(callback)

    def remove_active_tab_listener(self, callback: Callable[[str], None]) -> None:
        if callback in self._on_active_tab_changed:
            self._on_active_tab_changed.remove(callback)

    # INTERNAL ------------------------------------------------------------

    def _on_viewport_dock(self, name: str, is_selected: bool) -> None:
        self._on_dock_changed(name, is_selected)

    def _on_viewport_tab_renamed(self, old_name: str, new_name: str) -> None:
        # A viewport tab was saved to (or opened from) a file, so its
        # label changes from "Viewport N" to the file name. Keep our
        # active-tab tracking and the fake tab bar in sync.
        if self._active_tab == old_name:
            self._active_tab = new_name
        if self._main_tabs is not None:
            self._main_tabs.rename_tab(old_name, new_name)

    def _on_dock_changed(self, tab: str, is_selected: bool) -> None:
        if not is_selected:
            # Only fire on the tab that just became active.
            return
        if tab == self._active_tab:
            return
        self._active_tab = tab
        # Keep the fake tab bar's highlight in sync regardless of how the
        # selection changed (button click, viewport spawn, hotkey, ...).
        if self._main_tabs is not None:
            self._main_tabs.set_active(tab)
        # When Home becomes the selected tab, flip the active context to
        # the empty `home` context so Stage / Layer / CLayers all clear
        # out, AND tell the viewport manager to tear down its live
        # ViewportWindow so the Hydra pipeline releases its GPU memory.
        # Viewport tabs set their own active context inside
        # `OpenUsdViewportManager._dock_after_delay`.
        if tab == TAB_HOME:
            set_active_context_name(HOME_CONTEXT_NAME)
            try:
                self._viewports.suspend()
            except Exception:
                pass
        for cb in list(self._on_active_tab_changed):
            try:
                cb(tab)
            except Exception:  # pragma: no cover - listener errors shouldn't break us
                pass

    # TAB BAR DISPATCH ----------------------------------------------------

    def _focus_home_now(self) -> None:
        home = self._home_window.window if self._home_window is not None else None
        if home is None:
            return
        try:
            cast(Any, home).selected_in_dock = True
        except Exception:
            pass
        try:
            cast(Any, home).focus()
        except Exception:
            pass

    def _on_tab_button_clicked(self, name: str) -> None:
        # User clicked one of the buttons in the fake tab bar. Focus the
        # matching pane; the dock-changed callback then propagates back
        # through `_on_dock_changed` and updates everything else.
        if name == TAB_HOME:
            home = self._home_window.window if self._home_window is not None else None
            if home is None:
                return
            try:
                cast(Any, home).selected_in_dock = True
            except Exception:
                pass
            try:
                cast(Any, home).focus()
            except Exception:
                pass
            return
        # Otherwise it's a viewport tab.
        self._viewports.focus_tab(name)

    def _on_tab_close_clicked(self, name: str) -> None:
        # User clicked the X overlay on a (closable) tab. Home is
        # added with ``closable=False`` so it never reaches us, but
        # guard anyway in case the caller wires it differently.
        if name == TAB_HOME:
            return

        was_active = self._active_tab == name

        def _on_confirmed() -> None:
            # Only refocus Home + drop the tab from the bar once the
            # viewport is actually gone. If the user cancels (or a save
            # fails) the viewport stays put and the tab must stay too.
            if was_active:
                self._focus_home_now()
            if self._main_tabs is not None:
                self._main_tabs.remove_tab(name)

        # Dirty stage -> Save / Don't Save / Cancel prompt; clean -> close now.
        self._viewports.close_viewport_with_prompt(
            name, on_confirmed=_on_confirmed
        )

    # LIFECYCLE -----------------------------------------------------------

    def destroy(self) -> None:
        self._on_active_tab_changed.clear()
        if self._apps_window is not None:
            self._apps_window.destroy()
            self._apps_window = None
        if self._home_window is not None:
            self._home_window.destroy()
            self._home_window = None
        self._viewports.destroy()
        if self._main_tabs is not None:
            self._main_tabs.destroy()
            self._main_tabs = None

    # HOME LOGO DISPATCH --------------------------------------------------

    def _on_home_logo_clicked(self, label: str) -> None:
        if label == "Apps":
            if self._apps_window is None:
                self._apps_window = AppsWindow(on_app_clicked=self._on_app_clicked)
            self._apps_window.show()

    # APPS POPUP DISPATCH -------------------------------------------------

    def _on_app_clicked(self, label: str) -> None:
        if label == "OpenUSD":
            title = self._viewports.spawn_new_tab()
            # Register a button for the new viewport so the user can
            # switch back to it later. The new viewport is also raised
            # inside `_dock_into_first`; the dock callback will then
            # call `set_active(...)` on the tab bar via
            # `_on_dock_changed`.
            if self._main_tabs is not None:
                self._main_tabs.add_tab(title)
            if self._apps_window is not None:
                self._apps_window.hide()
