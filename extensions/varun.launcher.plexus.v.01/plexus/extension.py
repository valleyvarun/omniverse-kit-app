"""Host the reusable OmniGraph editor UI in the launcher's main workspace."""

from typing import cast

import omni.ext
import omni.kit.actions.core
from omni import ui
from omni.kit.graph.editor.core import GraphEditorCoreWidget

from .models import EmptyCatalogModel, EmptyGraphModel

EXTENSION_ID: str = "varun.launcher.plexus.v.01"
WORKSPACE_ID: str = "varun.launcher.workspace.v.01"
WINDOW_NAME: str = "Plexus"


class PlexusExtension(omni.ext.IExt):
    def on_startup(self, _ext_id: str) -> None:
        # Native canvas and catalog, ready for a future custom node model.
        self._graph_model: EmptyGraphModel = EmptyGraphModel()
        self._catalog_model: EmptyCatalogModel = EmptyCatalogModel()
        self._window: ui.Window = ui.Window(
            WINDOW_NAME,
            width=1015,
            height=560,
            visible=False,
            flags=ui.WINDOW_FLAGS_NO_TITLE_BAR | ui.WINDOW_FLAGS_NO_MOVE,
        )
        with self._window.frame:
            self._editor: GraphEditorCoreWidget = GraphEditorCoreWidget(
                model=self._graph_model,
                catalog_model=self._catalog_model,
            )
        omni.kit.actions.core.get_action_registry().register_action(
            EXTENSION_ID, "open_plexus", self.open_plexus, display_name="Open Plexus"
        )

    # UI-only tabs have no USD stage, save prompt, or execution engine.
    def open_plexus(self) -> int:
        return cast(
            int,
            omni.kit.actions.core.get_action_registry().execute_action(
                WORKSPACE_ID, "add_editor_tab", WINDOW_NAME, "Plexus"
            ),
        )

    def on_shutdown(self) -> None:
        omni.kit.actions.core.get_action_registry().deregister_all_actions_for_extension(EXTENSION_ID)
        self._editor.destroy()
        self._graph_model.destroy()
        self._window.destroy()
