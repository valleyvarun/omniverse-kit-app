# Python & library imports
import asyncio
from pathlib import Path
from typing import Any, cast

# Omniverse & USD imports
import omni.ext
import omni.kit.actions.core
import omni.kit.app
from omni import ui

# Local imports
from .assign_window import WindowAssigner

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

# layout file location
LAYOUT_PATH: Path = Path(__file__).resolve().parents[5] / "layouts" / "default.json"

# layout action identifiers
ACTION_EXTENSION_ID: str = "varun.launcher.layout.v.01"
RESET_LAYOUT_ACTION_ID: str = "reset_default_layout"
SHOW_MAIN_WINDOW_ACTION_ID: str = "show_main_window"

# change these; the rest of the layout is derived from them
TABS_WINDOW_HEIGHT: int = 30
TOP_WINDOW_HEIGHT: int = 75
# Leave room for the dock tab and controls; this is not a minimum height.
BOTTOM_WINDOW_HEIGHT: int = 100
BOTTOM_WINDOW_MAX_HEIGHT: int = 300
MAIN_WINDOW_HEIGHT: int = 560
RIGHT_TOP_WINDOW_HEIGHT: int = 180
LEFT_WINDOW_WIDTH: int = 130
MAIN_COLUMN_WIDTH: int = 1015
RIGHT_COLUMN_WIDTH: int = 295

TOP_WINDOW_WIDTH: int = LEFT_WINDOW_WIDTH + MAIN_COLUMN_WIDTH
LEFT_WINDOW_HEIGHT: int = TABS_WINDOW_HEIGHT + MAIN_WINDOW_HEIGHT + BOTTOM_WINDOW_HEIGHT
RIGHT_BOTTOM_WINDOW_HEIGHT: int = TOP_WINDOW_HEIGHT + LEFT_WINDOW_HEIGHT - RIGHT_TOP_WINDOW_HEIGHT

WINDOW_SIZES: tuple[tuple[str, int, int], ...] = (
    ("main_window", MAIN_COLUMN_WIDTH, MAIN_WINDOW_HEIGHT),
    ("tabs_window", MAIN_COLUMN_WIDTH, TABS_WINDOW_HEIGHT),
    ("top_window", TOP_WINDOW_WIDTH, TOP_WINDOW_HEIGHT),
    ("left_window", LEFT_WINDOW_WIDTH, LEFT_WINDOW_HEIGHT),
    ("bottom_window", MAIN_COLUMN_WIDTH, BOTTOM_WINDOW_HEIGHT),
    ("right_top_window", RIGHT_COLUMN_WIDTH, RIGHT_TOP_WINDOW_HEIGHT),
    ("right_bottom_window", RIGHT_COLUMN_WIDTH, RIGHT_BOTTOM_WINDOW_HEIGHT),
)


# ----------------------------------------------------------------------
# 2. MAIN EXTENSION CLASS
# ----------------------------------------------------------------------


class MyExtension(omni.ext.IExt):
    # ON STARTUP
    def on_startup(self, _ext_id: str) -> None:
        print("[varun.launcher.layout.v.01] Extension startup")

        # --- 1) PREPARE WINDOW ASSIGNMENTS ---

        # create window assignment controller
        self._window_assigner: WindowAssigner = WindowAssigner()
        self._laid_out_windows: set[str] = set()

        # --- 2) REGISTER LAYOUT ACTIONS ---

        # register layout reset action
        action_registry: Any = omni.kit.actions.core.get_action_registry()
        action_registry.register_action(
            ACTION_EXTENSION_ID,
            RESET_LAYOUT_ACTION_ID,
            self.reset_default_layout,
            display_name="Reset to Default Layout",
            description="Restore the launcher window layout.",
        )
        action_registry.register_action(
            ACTION_EXTENSION_ID,
            SHOW_MAIN_WINDOW_ACTION_ID,
            self.show_main_window,
            display_name="Show Main Window",
            description="Show a window in the launcher main area.",
        )

        # --- 3) CREATE PLACEHOLDER WINDOWS ---

        # no header, not movable
        flags: int = ui.WINDOW_FLAGS_NO_TITLE_BAR | ui.WINDOW_FLAGS_NO_MOVE

        # store created layout windows
        self._windows: list[ui.Window] = []

        # create windows
        name: str
        width: int
        height: int
        for name, width, height in WINDOW_SIZES:
            if name not in self._window_assigner.assignments:
                self._windows.append(ui.Window(name, width=width, height=height, flags=flags))

        # --- 4) ENFORCE SIZES AND RESTORE THE LAYOUT ---

        # start height enforcement
        self._dpi_scale: float = ui.Workspace.get_dpi_scale()
        self._enforce_window_heights: bool = True
        asyncio.ensure_future(self._keep_window_heights_fixed())

        # load default layout
        self.reset_default_layout()

    # Replace the main window without discarding the user's bottom height.
    def show_main_window(self, window_name: str) -> None:
        # Reuse initialized docks; stage tabs already share the same window.
        previous_name: str = self._window_assigner.main_window_name
        previous_window: ui.WindowHandle | None = ui.Workspace.get_window(previous_name)
        active_window: ui.WindowHandle | None = ui.Workspace.get_window(window_name)
        if (
            self._layout_ready
            and window_name in self._laid_out_windows
            and previous_window
            and active_window
            and previous_window.dock_id >= 0
        ):
            if window_name != previous_name:
                active_window.visible = True
                if active_window.dock_id != previous_window.dock_id:
                    active_window.dock_in(previous_window, ui.DockPosition.SAME)
            self._window_assigner.set_main_window(window_name)
            self._window_assigner.update_main_slot()
            return

        bottom_window: Any = ui.Workspace.get_window(self._window_assigner.window_name("bottom_window"))
        bottom_height: float = float(BOTTOM_WINDOW_HEIGHT)
        if bottom_window and bottom_window.dock_id >= 0:
            bottom_height = min(ui.Workspace.get_dock_id_height(bottom_window.dock_id), BOTTOM_WINDOW_MAX_HEIGHT)
        self._window_assigner.set_main_window(window_name)
        self._request_layout(bottom_height)

    # Fix top and tab heights; only cap the resizable bottom dock.
    async def _keep_window_heights_fixed(self) -> None:
        kit_app: Any = omni.kit.app.get_app()
        while self._enforce_window_heights:
            await kit_app.next_update_async()
            if not self._enforce_window_heights:
                return

            scale: float = ui.Workspace.get_dpi_scale()
            if scale != self._dpi_scale:
                self._dpi_scale = scale
                # Kit retains old text measurements across monitor changes.
                # Reapply the same style to remeasure menus, tabs and toolbars;
                # no widgets, windows or stages need to be recreated.
                style: Any = ui.Style.get_instance()
                style.default = style.default

            name: str
            height: int
            for name, height in (
                ("tabs_window", TABS_WINDOW_HEIGHT),
                ("top_window", TOP_WINDOW_HEIGHT),
            ):
                window: Any = ui.Workspace.get_window(self._window_assigner.window_name(name))
                if window and window.dock_id >= 0 and abs(ui.Workspace.get_dock_id_height(window.dock_id) - height) > 1:
                    ui.Workspace.set_dock_id_height(window.dock_id, height)

            bottom_window: Any = ui.Workspace.get_window(self._window_assigner.window_name("bottom_window"))
            if (
                bottom_window
                and bottom_window.dock_id >= 0
                and ui.Workspace.get_dock_id_height(bottom_window.dock_id) > BOTTOM_WINDOW_MAX_HEIGHT + 1
            ):
                ui.Workspace.set_dock_id_height(bottom_window.dock_id, BOTTOM_WINDOW_MAX_HEIGHT)

    # write the constant sizes onto every window node in the layout tree
    def _apply_layout_sizes(self, layout: Any, bottom_height: float = BOTTOM_WINDOW_HEIGHT) -> None:
        # Resolve sizes for both placeholder and assigned window names.
        sizes: dict[str, tuple[float, float]] = {}
        name: str
        width: int
        height: int
        for name, width, height in WINDOW_SIZES:
            size: tuple[float, float] = (float(width), float(height))
            if name == "bottom_window":
                size = (float(width), bottom_height)
            elif name == "main_window":
                size = (float(width), MAIN_WINDOW_HEIGHT + BOTTOM_WINDOW_HEIGHT - bottom_height)
            sizes[name] = size
            sizes[self._window_assigner.window_name(name)] = size
        sizes[self._window_assigner.main_window_name] = (
            float(MAIN_COLUMN_WIDTH),
            MAIN_WINDOW_HEIGHT + BOTTOM_WINDOW_HEIGHT - bottom_height,
        )

        # Apply dimensions recursively to every matching layout node.
        def apply(node: Any) -> None:
            if isinstance(node, dict):
                title: Any = node.get("title")
                size: tuple[float, float] | None = sizes.get(title) if isinstance(title, str) else None
                if size is not None:
                    node["width"], node["height"] = size
                child: Any
                for child in node.values():
                    apply(child)
            elif isinstance(node, list):
                for child in node:
                    apply(child)

        apply(layout)

    # Explicit reset restores the default bottom height.
    def reset_default_layout(self) -> None:
        self._request_layout(float(BOTTOM_WINDOW_HEIGHT))

    # Keep each layout request paired with its requested bottom height.
    def _request_layout(self, bottom_height: float) -> None:
        self._layout_ready: bool = False
        load_number: int = self._window_assigner.request_load()
        asyncio.ensure_future(self._load_layout(load_number, bottom_height))

    # load layout from JSON file
    async def _load_layout(self, load_number: int, bottom_height: float) -> None:
        kit_app: Any = omni.kit.app.get_app()

        # wait for windows to initialize
        frame: int
        for frame in range(3):
            await kit_app.next_update_async()

        # read layout with assignments applied, then write sizes from the constants
        layout: Any = self._window_assigner.build_layout(LAYOUT_PATH)
        self._apply_layout_sizes(layout, bottom_height)

        # ignore an older tab request
        if not self._window_assigner.is_latest(load_number):
            return

        # restore resolved layout
        cast(Any, ui.Workspace).restore_workspace(layout, True)

        # wait for late-created windows to settle
        for frame in range(20):
            await kit_app.next_update_async()

        # ignore an older tab request
        if not self._window_assigner.is_latest(load_number):
            return

        # restore again so the layout wins over late dock calls
        cast(Any, ui.Workspace).restore_workspace(layout, True)
        await kit_app.next_update_async()

        # Workspace restoration scales dock ratios; restore the chosen height once.
        bottom_window: Any = ui.Workspace.get_window(self._window_assigner.window_name("bottom_window"))
        if bottom_window and bottom_window.dock_id >= 0:
            ui.Workspace.set_dock_id_height(bottom_window.dock_id, bottom_height)

        # swap the visible window in the main slot
        self._window_assigner.update_main_slot()
        self._layout_ready = True
        self._laid_out_windows.add(self._window_assigner.main_window_name)

    # ON SHUTDOWN
    def on_shutdown(self) -> None:
        print("[varun.launcher.layout.v.01] Extension shutdown")
        # Stop the height loop before releasing layout windows.

        self._enforce_window_heights = False

        action_registry: Any = omni.kit.actions.core.get_action_registry()
        action_registry.deregister_all_actions_for_extension(ACTION_EXTENSION_ID)

        window: ui.Window
        for window in self._windows:
            window.destroy()
