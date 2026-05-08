import asyncio
import logging
from pathlib import Path

# Kit framework imports.
import omni.ext

# Launcher window and tool imports.
from .hotkeys import HotkeyManager
from .layout_docking import LayoutDocking
from .startup_defaults import StartupDefaults
from .tools.hud_toggle_button import HudToggleButton
from .tools.drawing_plane import PlaneGridManager
from .tools.layers import GroupRegistry, LayerRegistry, VisibilityManager
from .tools.selection import (
    LockManager,
    SelectionDirectionFilter,
    SelectionStyle,
    SelectionSync,
    ViewportXformFilter,
)
from .tools.stage import StageLockColumn
from .windows.bottom_window import BottomWindow
from .windows.left_window import LeftWindow
from .windows.main_window import MainWindow
from .windows.right_window import ClayersWindow
from .windows.top_window import TopWindow


# Module-level constants.
LOGGER = logging.getLogger(__name__)
EXTENSION_PATH = Path(__file__).resolve().parents[3]


# Launcher extension entry point.
class MyExtension(omni.ext.IExt):
    def __init__(self) -> None:
        super().__init__()
        # Window and tool instances.
        self._bottom_window: BottomWindow | None = None
        self._left_window: LeftWindow | None = None
        self._main_window: MainWindow | None = None
        self._clayers_window: ClayersWindow | None = None
        self._top_window: TopWindow | None = None
        self._hud_toggle_button: HudToggleButton | None = None
        self._hotkey_manager: HotkeyManager | None = None
        self._startup_defaults: StartupDefaults | None = None
        self._layout_docking: LayoutDocking | None = None
        self._selection_style: SelectionStyle | None = None
        self._selection_direction_filter: SelectionDirectionFilter | None = None
        self._viewport_xform_filter: ViewportXformFilter | None = None
        self._selection_sync: SelectionSync | None = None
        self._lock_manager: LockManager | None = None
        self._visibility_manager: VisibilityManager | None = None
        self._layer_registry: LayerRegistry | None = None
        self._group_registry: GroupRegistry | None = None
        self._plane_grid_manager: PlaneGridManager | None = None
        self._stage_lock_column: StageLockColumn | None = None

    # Extension startup.
    def on_startup(self, ext_id: str) -> None:
        print(f"[{ext_id}] startup")

        # Set up startup defaults (layout, stage, light rig, pacing presets).
        layout_file = str(EXTENSION_PATH / "layouts" / "default.json")
        self._startup_defaults = StartupDefaults(layout_file)
        StartupDefaults.add_fps_pacing_preset(15)
        StartupDefaults.add_fps_pacing_preset(30)

        # Apply visual tweaks.
        self._layout_docking = LayoutDocking(self._startup_defaults)
        self._layout_docking.apply_dock_style()
        self._selection_style = SelectionStyle()
        self._selection_style.apply()
        self._selection_direction_filter = SelectionDirectionFilter()
        self._selection_direction_filter.apply()
        self._viewport_xform_filter = ViewportXformFilter()
        self._viewport_xform_filter.apply()
        self._selection_sync = SelectionSync()
        self._selection_sync.apply()

        # Add a Lock column to the Stage window with full enforcement.
        self._lock_manager = LockManager()
        self._lock_manager.apply()
        # Central visibility + layer-registry singletons consumed by the
        # CLayers window and (via listeners) reflected in Stage/Layer panels.
        self._visibility_manager = VisibilityManager()
        self._visibility_manager.apply()
        self._layer_registry = LayerRegistry()
        self._layer_registry.apply()
        self._group_registry = GroupRegistry()
        self._group_registry.apply()
        # Purple grid that renders on whichever drawing plane is active in
        # CLayers. Camera-following so it appears infinite.
        self._plane_grid_manager = PlaneGridManager()
        self._plane_grid_manager.apply()
        self._stage_lock_column = StageLockColumn(self._lock_manager)
        self._stage_lock_column.apply()

        # Build all docked windows.
        self._top_window = TopWindow()
        self._left_window = LeftWindow()
        self._clayers_window = ClayersWindow()
        self._bottom_window = BottomWindow()
        self._main_window = MainWindow()

        # Register the viewport HUD toggle button.
        self._hud_toggle_button = HudToggleButton()

        # Schedule layout load and default stage creation.
        asyncio.ensure_future(self._startup_defaults.initialize_layout_and_stage())

        # Add menu entries and hotkeys.
        self._layout_docking.add_window_menu_items()
        self._hotkey_manager = HotkeyManager(lambda: self._top_window)
        self._hotkey_manager.register()

    # Extension shutdown.
    def on_shutdown(self) -> None:
        print("[varun.launcher.ui] shutdown")

        if self._hotkey_manager is not None:
            self._hotkey_manager.deregister()
            self._hotkey_manager = None

        if self._layout_docking is not None:
            self._layout_docking.remove_window_menu_items()
            self._layout_docking = None

        if self._selection_style is not None:
            self._selection_style.destroy()
            self._selection_style = None

        if self._selection_direction_filter is not None:
            self._selection_direction_filter.destroy()
            self._selection_direction_filter = None

        if self._viewport_xform_filter is not None:
            self._viewport_xform_filter.destroy()
            self._viewport_xform_filter = None

        if self._selection_sync is not None:
            self._selection_sync.destroy()
            self._selection_sync = None

        if self._stage_lock_column is not None:
            self._stage_lock_column.destroy()
            self._stage_lock_column = None

        if self._plane_grid_manager is not None:
            self._plane_grid_manager.destroy()
            self._plane_grid_manager = None

        if self._layer_registry is not None:
            self._layer_registry.destroy()
            self._layer_registry = None

        if self._group_registry is not None:
            self._group_registry.destroy()
            self._group_registry = None

        if self._visibility_manager is not None:
            self._visibility_manager.destroy()
            self._visibility_manager = None

        if self._lock_manager is not None:
            self._lock_manager.destroy()
            self._lock_manager = None

        if self._hud_toggle_button:
            self._hud_toggle_button.destroy()
            self._hud_toggle_button = None

        if self._top_window:
            self._top_window.destroy()
            self._top_window = None

        if self._left_window:
            self._left_window.destroy()
            self._left_window = None

        if self._clayers_window:
            self._clayers_window.destroy()
            self._clayers_window = None

        if self._bottom_window:
            self._bottom_window.destroy()
            self._bottom_window = None

        if self._main_window:
            self._main_window.destroy()
            self._main_window = None
