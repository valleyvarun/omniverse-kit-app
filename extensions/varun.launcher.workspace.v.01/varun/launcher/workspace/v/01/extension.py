# Python & library imports
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, cast

# Omniverse & USD imports
import carb
import carb.settings
import omni.ext
import omni.kit.actions.core
import omni.kit.app
import omni.usd
from omni import ui
from omni.kit.viewport.utility import (
    get_active_viewport_window,
    get_viewport_from_window_name,
)
from omni.kit.widget.prompt import PromptButtonInfo, PromptManager
from omni.kit.window.file_exporter import get_file_exporter
from pxr import Sdf, Usd, UsdGeom, UsdUtils

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

# layout action
LAYOUT_EXTENSION_ID: str = "varun.launcher.layout.v.01"
SHOW_MAIN_WINDOW_ACTION_ID: str = "show_main_window"

# workspace action
WORKSPACE_EXTENSION_ID: str = "varun.launcher.workspace.v.01"
ADD_VIEWPORT_TAB_ACTION_ID: str = "add_viewport_tab"

# workspace data
SAMPLE_USD_PATH: Path = Path(__file__).resolve().parents[5] / "data" / "viewport.usda"
HOME_TAB_ID: int = 0

# every viewport tab reuses this single window
VIEWPORT_WINDOW_NAME: str = "Viewport"

# per-stage mode cache used by the lighting menubar
LIGHTING_MODE_KEY: str = "/exts/omni.kit.viewport.menubar.lighting/lightingMode/{stage_id}"
DEFAULT_LIGHT_RIG: str = "Grey Studio"
AUTO_FRAME_MODE: str = "/persistent/app/viewport/autoFrame/mode"

# tab dimensions
TAB_WIDTH: int = 100
TAB_HEIGHT: int = 30
CLOSE_BUTTON_WIDTH: int = 22

# inactive tab appearance
INACTIVE_TAB_STYLE: dict[str, dict[str, int]] = {
    "Button": {
        "background_color": 0xFF333333,
        "border_radius": 0,
        "margin": 0,
        "padding": 0,
    },
    "Button.Label": {"color": 0xFFAAAAAA},
    "Button:hovered": {"background_color": 0xFF3D3D3D},
}

# active tab appearance
ACTIVE_TAB_STYLE: dict[str, dict[str, int]] = {
    "Button": {
        "background_color": 0xFF4A4A4A,
        "border_radius": 0,
        "margin": 0,
        "padding": 0,
    },
    "Button.Label": {"color": 0xFFFFFFFF},
    "Button:hovered": {"background_color": 0xFF4A4A4A},
}


# ----------------------------------------------------------------------
# 2. DATA STRUCTURE: WORKSPACE TAB
# ----------------------------------------------------------------------


@dataclass(eq=False)
class WorkspaceTab:
    # Permanent ID used to select this tab.
    tab_id: int
    # Window shown in the main layout slot.
    window_name: str
    # Retained stage, or None for Home and UI-only editors.
    stage: Usd.Stage | None
    # Saved file path and per-tab camera path.
    file_path: str | None = None
    camera_path: str | None = None
    label: str = "Viewport"

    # Choose Home, the file name, or a numbered editor label.
    @property
    def name(self) -> str:
        if self.tab_id == HOME_TAB_ID:
            return "Home"
        return Path(self.file_path).name if self.file_path else f"{self.label} {self.tab_id}"


# ----------------------------------------------------------------------
# 3. MAIN EXTENSION CLASS
# ----------------------------------------------------------------------


class MyExtension(omni.ext.IExt):
    # Kit owns these handles; their native APIs are dynamically bound.
    _window: Any
    _stage_cache: Any
    _stage_events: Any
    # Keep tab ownership and pending work explicitly typed.
    _tabs: dict[int, WorkspaceTab]
    _context_tab_id: int | None
    _tasks: set[asyncio.Future[None]]
    _alive: bool
    _active_tab_id: int
    _next_tab_id: int
    _switch_lock: asyncio.Lock
    _switching: bool

    # ON STARTUP
    def on_startup(self, _ext_id: str) -> None:
        print("[varun.launcher.workspace.v.01] Extension startup")
        # Allow pending work to check whether the extension is still running.
        self._alive = True

        # --- 1) CREATE THE TAB BAR ---

        # create tabs bar window
        flags: int = ui.WINDOW_FLAGS_NO_TITLE_BAR | ui.WINDOW_FLAGS_NO_MOVE
        self._window = ui.Window(
            "tabs_bar_window",
            width=1015,
            height=30,
            flags=flags,
            padding_x=0,
            padding_y=0,
        )

        # --- 2) INITIALIZE TAB DATA ---

        self._active_tab_id = HOME_TAB_ID
        self._next_tab_id = 1
        self._tabs = {HOME_TAB_ID: WorkspaceTab(HOME_TAB_ID, "home", None)}
        # Kit removes stages from its global cache on close. This separate cache
        # keeps inactive tabs alive, including their unsaved edits/session layers.
        self._stage_cache = Usd.StageCache()
        self._context_tab_id = None
        # Serialize changes to the shared USD context.
        self._switch_lock = asyncio.Lock()
        self._switching = False
        self._tasks = set()
        self._window.frame.set_build_fn(self._build_tabs)

        # --- 3) SET UP STAGE LISTENERS ---

        # rename the active tab when a usd file is opened
        self._stage_events = (
            omni.usd.get_context()
            .get_stage_event_stream()
            .create_subscription_to_pop(self._on_stage_event, name="varun.launcher.workspace tab names")
        )

        # --- 4) REGISTER THE ADD-TAB ACTION ---

        # let other extensions add viewport tabs
        action_registry: Any = omni.kit.actions.core.get_action_registry()
        action_registry.register_action(
            WORKSPACE_EXTENSION_ID,
            ADD_VIEWPORT_TAB_ACTION_ID,
            self.add_viewport_tab,
            display_name="Add Viewport Tab",
            description="Add a workspace tab for the shared viewport window.",
        )
        action_registry.register_action(
            WORKSPACE_EXTENSION_ID,
            "add_editor_tab",
            self.add_editor_tab,
            display_name="Add Editor Tab",
            description="Open a UI-only editor in the main workspace.",
        )
        action_registry.register_action(
            WORKSPACE_EXTENSION_ID,
            "select_home_tab",
            self.select_home_tab,
            display_name="Select Home Tab",
        )

        # --- 5) SELECT THE STARTUP TAB ---

        # hide tab windows until a tab is selected
        window_names: list[str] = [VIEWPORT_WINDOW_NAME]
        tab: WorkspaceTab
        for tab in self._tabs.values():
            window_names.append(tab.window_name)
        window_name: str
        for window_name in window_names:
            tab_window: Any = ui.Workspace.get_window(window_name)
            if tab_window:
                tab_window.visible = False

        self._track_task(self._initialize_workspace())

    # Track background work so shutdown can cancel it.
    def _track_task(self, coroutine: Awaitable[None]) -> asyncio.Future[None]:
        task: asyncio.Future[None] = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # load the default tab after layout startup
    async def _initialize_workspace(self) -> None:
        kit_app: Any = omni.kit.app.get_app()
        frame: int
        for frame in range(5):
            await kit_app.next_update_async()

        await self._show_active_tab(self._active_tab_id)

    # build tabs bar
    def _build_tabs(self) -> None:
        with ui.HStack(spacing=1):
            tab_id: int
            tab: WorkspaceTab
            for tab_id, tab in self._tabs.items():
                style: dict[str, dict[str, int]] = self._tab_style(tab_id)

                # Only Home is permanent; UI-only editor tabs can close.
                if tab_id == HOME_TAB_ID:
                    ui.Button(
                        tab.name,
                        width=TAB_WIDTH,
                        height=TAB_HEIGHT,
                        style=style,
                        identifier=f"workspace_tab:{tab_id}",
                        clicked_fn=partial(self._select_tab, tab_id),
                    )
                    continue

                # closable tab with an X button
                with ui.HStack(width=TAB_WIDTH, spacing=0):
                    ui.Button(
                        tab.name,
                        width=TAB_WIDTH - CLOSE_BUTTON_WIDTH,
                        height=TAB_HEIGHT,
                        style=style,
                        tooltip=(
                            tab.name if tab.stage is None else f"{tab.label} {tab_id}: {tab.file_path or 'Unsaved'}"
                        ),
                        identifier=f"workspace_tab:{tab_id}",
                        clicked_fn=partial(self._select_tab, tab_id),
                    )
                    ui.Button(
                        "X",
                        width=CLOSE_BUTTON_WIDTH,
                        height=TAB_HEIGHT,
                        style=style,
                        identifier=f"workspace_close:{tab_id}",
                        clicked_fn=partial(self._on_tab_close_clicked, tab),
                    )
            ui.Spacer()

    # return active or inactive style
    def _tab_style(self, tab_id: int) -> dict[str, dict[str, int]]:
        if tab_id == self._active_tab_id:
            return ACTIVE_TAB_STYLE
        return INACTIVE_TAB_STYLE

    # select tab and refresh UI
    def _select_tab(self, tab_id: int) -> asyncio.Future[None] | None:
        if tab_id not in self._tabs or tab_id == self._active_tab_id:
            return

        self._remember_stage()
        self._active_tab_id = tab_id
        self._window.frame.rebuild()
        return self._track_task(self._show_active_tab(tab_id))

    # Return completion so command entry can restore focus after navigation.
    def select_home_tab(self) -> asyncio.Future[None] | None:
        return self._select_tab(HOME_TAB_ID)

    # add a numbered tab for the shared viewport window
    def add_viewport_tab(self) -> int:
        # create an in-memory stage with the sample file as a sublayer
        stage: Usd.Stage = Usd.Stage.CreateInMemory()
        stage.GetRootLayer().subLayerPaths.append(str(SAMPLE_USD_PATH))

        # use Kit's default up axis like new stages do (the viewport extension makes it Z)
        up_axis: Any = carb.settings.get_settings().get("/persistent/app/stage/upAxis")
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y if up_axis == "Y" else UsdGeom.Tokens.z)

        # preset the new stage's lighting mode to the default rig
        stage_id: int = UsdUtils.StageCache.Get().Insert(stage).ToLongInt()
        cast(Any, carb.settings.get_settings()).set(LIGHTING_MODE_KEY.format(stage_id=stage_id), DEFAULT_LIGHT_RIG)

        tab: WorkspaceTab = self._add_stage_tab(stage)
        self._select_tab(tab.tab_id)
        return tab.tab_id

    # Editors without a USD document use the same main-window tab bar.
    def add_editor_tab(self, window_name: str, label: str) -> int:
        tab: WorkspaceTab = WorkspaceTab(self._next_tab_id, window_name, None, label=label)
        self._next_tab_id += 1
        self._tabs[tab.tab_id] = tab
        self._select_tab(tab.tab_id)
        return tab.tab_id

    # Assign a permanent tab ID and retain the stage in the cache.
    def _add_stage_tab(self, stage: Usd.Stage) -> WorkspaceTab:
        tab: WorkspaceTab = WorkspaceTab(self._next_tab_id, VIEWPORT_WINDOW_NAME, stage)
        self._next_tab_id += 1  # Never renumber or reuse an ID during this session.
        self._stage_cache.Insert(stage)
        self._tabs[tab.tab_id] = tab
        return tab

    # handle stage events from the shared usd context
    def _on_stage_event(self, event: Any) -> None:
        if event.type in (int(omni.usd.StageEventType.OPENED), int(omni.usd.StageEventType.SAVED)):
            self._remember_stage()

    # File > Open/New/Save As belong to the tab actually attached to the context,
    # not a different tab the user may have just clicked while loading.
    def _remember_stage(self) -> None:
        if self._switching:
            return
        stage: Usd.Stage | None = cast(Any, omni.usd.get_context()).get_stage()
        if not stage:
            return

        owner: WorkspaceTab | None = None
        candidate: WorkspaceTab
        for candidate in self._tabs.values():
            if candidate.stage == stage:
                owner = candidate
                break
        if owner is None and self._context_tab_id is not None and self._context_tab_id not in self._tabs:
            return  # A closing tab may still be attached until its switch finishes.
        tab: WorkspaceTab | None = owner or (
            self._tabs.get(self._context_tab_id) if self._context_tab_id is not None else None
        )
        # Resolve the tab that owns the currently attached stage.
        if tab is None:
            # Ignore the viewport extension's anonymous startup stage on Home.
            if stage.GetRootLayer().anonymous:
                return
            tab = self._add_stage_tab(stage)
            self._context_tab_id = tab.tab_id
            self._active_tab_id = tab.tab_id
            self._track_task(self._show_active_tab(tab.tab_id))
        if tab.stage != stage:
            # Retain a replacement stage and discard the previous camera reference.
            self._stage_cache.Insert(stage)
            self._stage_cache.Erase(tab.stage)
            tab.stage = stage
            tab.camera_path = None
        root: Sdf.Layer = stage.GetRootLayer()
        # Refresh the label after Open, New, or Save As.
        file_path: str | None = None if root.anonymous else root.identifier
        if tab.file_path != file_path or owner is None:
            tab.file_path = file_path
            self._window.frame.rebuild()

    # ask to save the tab's stage before closing
    def _on_tab_close_clicked(self, tab: WorkspaceTab) -> None:
        if tab.stage is None:
            self._close_tab(tab)
            return
        PromptManager.post_simple_prompt(
            "Close Tab",
            "Would you like to save this stage?",
            ok_button_info=PromptButtonInfo("Save", partial(self._save_and_close_tab, tab)),
            middle_button_info=PromptButtonInfo("Don't Save", partial(self._close_tab, tab)),
            cancel_button_info=PromptButtonInfo("Cancel", self._cancel_close),
            modal=True,
        )

    @staticmethod
    def _cancel_close() -> None:
        pass

    # save the tab's stage to a picked file, then close the tab
    def _save_and_close_tab(self, tab: WorkspaceTab) -> None:
        stage: Usd.Stage | None = tab.stage
        if stage is None:
            return

        # export_handler gets (filename without extension, dirname, extension, selections)
        def on_export(filename: str, dirname: str, extension: str = "", selections: Any = None) -> None:
            if dirname and not dirname.endswith("/"):
                dirname += "/"
            stage.GetRootLayer().Export(f"{dirname}{filename}{extension}")
            self._close_tab(tab)

        cast(Any, get_file_exporter()).show_window(
            title="Save Stage As",
            export_button_label="Save",
            export_handler=on_export,
        )

    # remove the tab and release its stage
    def _close_tab(self, tab: WorkspaceTab) -> None:
        if self._tabs.get(tab.tab_id) is not tab or tab.tab_id == HOME_TAB_ID:
            return

        ids: list[int] = list(self._tabs)
        index: int = ids.index(tab.tab_id)
        stage: Usd.Stage | None = tab.stage
        del self._tabs[tab.tab_id]

        if stage is not None:
            self._stage_cache.Erase(stage)
            # Kit still needs the attached/in-flight stage until it detaches it.
            if not self._switching and cast(Any, omni.usd.get_context()).get_stage() != stage:
                UsdUtils.StageCache.Get().Erase(stage)

        # keep the active tab valid and refresh the bar
        if self._active_tab_id == tab.tab_id:
            # Do not adopt the just-closed stage again while selecting a neighbor.
            self._active_tab_id = ids[index - 1]
            self._track_task(self._show_active_tab(self._active_tab_id))
        self._window.frame.rebuild()

    # hand the active tab window to main_window
    async def _show_active_tab(self, tab_id: int) -> None:

        # --- 1) WAIT FOR THE SHARED CONTEXT ---

        async with self._switch_lock:
            context: Any = omni.usd.get_context()
            while self._alive and not context.can_open_stage():
                await cast(Any, omni.kit.app.get_app()).next_update_async()
            if self._alive:
                self._remember_stage()
            if not self._alive or tab_id != self._active_tab_id or tab_id not in self._tabs:
                return

            # --- 2) SAVE THE OUTGOING CAMERA ---

            tab: WorkspaceTab = self._tabs[tab_id]
            viewport: Any = get_viewport_from_window_name(VIEWPORT_WINDOW_NAME)
            previous: WorkspaceTab | None = (
                self._tabs.get(self._context_tab_id) if self._context_tab_id is not None else None
            )
            if previous and previous.stage is not None and previous.stage == context.get_stage() and viewport:
                previous.camera_path = str(viewport.camera_path)

            # --- 3) PRESERVE THE INCOMING VIEW ---

            settings: Any = carb.settings.get_settings()
            auto_frame: Any = settings.get(AUTO_FRAME_MODE)
            restore_view: bool = tab.camera_path is not None
            if restore_view:
                # Camera transforms already live in the retained stage. Prevent
                # Kit's file-open auto-framing from overwriting them on reattach.
                settings.set(AUTO_FRAME_MODE, "never")

            # --- 4) ATTACH THE REQUESTED STAGE ---

            window: Any = get_active_viewport_window(window_name=VIEWPORT_WINDOW_NAME)
            display: Any = window.viewport_widget.display_delegate if window else None
            begin_transition: Callable[[], None] | None = getattr(display, "begin_transition", None)
            end_transition: Callable[[], None] | None = getattr(display, "end_transition", None)
            if begin_transition is not None:
                begin_transition()
            self._switching = True
            try:
                if begin_transition is not None:
                    # Submit the cover before releasing the image used by the UI.
                    await cast(Any, omni.kit.app.get_app()).next_update_async()
                    await cast(Any, omni.kit.app.get_app()).next_update_async()
                    if tab_id != self._active_tab_id or tab_id not in self._tabs:
                        return
                if tab.stage is None:
                    if context.get_stage() is not None:
                        await context.close_stage_async()
                elif context.get_stage() != tab.stage:
                    success: bool
                    error: str
                    success, error = await context.attach_stage_async(tab.stage)
                    if not success:
                        carb.log_error(f"Could not switch to {tab.label} {tab_id}: {error}")
                        self._active_tab_id = self._context_tab_id or HOME_TAB_ID
                        self._window.frame.rebuild()
                        return
                self._context_tab_id = tab_id if tab.stage is not None else None
                if restore_view and viewport and tab.stage is not None:
                    # Let the stage-open handlers finish choosing their camera.
                    await cast(Any, omni.kit.app.get_app()).next_update_async()
                    if tab.stage.GetPrimAtPath(tab.camera_path):
                        viewport.camera_path = tab.camera_path
            # Restore automatic framing even when attachment fails.
            finally:
                if restore_view:
                    if auto_frame is None:
                        settings.destroy_item(AUTO_FRAME_MODE)
                    else:
                        settings.set(AUTO_FRAME_MODE, auto_frame)
                self._switching = False
                if end_transition is not None:
                    end_transition()

            # --- 5) SHOW THE SELECTED WINDOW ---

            if tab_id == self._active_tab_id and tab_id in self._tabs:
                omni.kit.actions.core.get_action_registry().execute_action(
                    LAYOUT_EXTENSION_ID,
                    SHOW_MAIN_WINDOW_ACTION_ID,
                    tab.window_name,
                )

    # ON SHUTDOWN
    def on_shutdown(self) -> None:
        print("[varun.launcher.workspace.v.01] Extension shutdown")
        # Stop listeners and cancel pending tab switches.
        self._alive = False
        self._stage_events = None
        task: asyncio.Future[None]
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

        # Remove actions registered by this extension.
        action_registry: Any = omni.kit.actions.core.get_action_registry()
        action_registry.deregister_all_actions_for_extension(WORKSPACE_EXTENSION_ID)

        # remove tab stages from the usd stage cache
        tab: WorkspaceTab
        for tab in self._tabs.values():
            if tab.stage is not None and cast(Any, omni.usd.get_context()).get_stage() != tab.stage:
                UsdUtils.StageCache.Get().Erase(tab.stage)
        self._stage_cache.Clear()
        self._tabs.clear()

        self._window.destroy()
