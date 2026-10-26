"""Run in New Launcher with --enable omni.kit.ui_test --exec this-file."""

import asyncio
import gc
import importlib
import json
import logging
import time
import traceback
from pathlib import Path
from typing import Any, cast

import omni.kit.app
import omni.kit.renderer.capture
import omni.usd
from omni import ui
from omni.kit import ui_test
from omni.kit.widget.graph import GraphView
from pxr import Usd


async def run() -> None:
    app: Any = omni.kit.app.get_app()
    output: Path = Path(__file__).resolve().parents[4] / "_build/plexus_checks"
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, bool | str] = {}
    workspace: Any = None
    plexus: Any = None
    toolbox: Any = None

    async def settle(count: int = 30) -> None:
        frame: int
        for frame in range(count):
            await app.next_update_async()

    async def complete() -> None:
        deadline: float = time.monotonic() + 20
        await settle(3)
        while workspace._tasks:
            assert time.monotonic() < deadline, "Tab switching timed out"
            await app.next_update_async()
        await settle(35)

    async def click(window: str, identifier: str) -> None:
        button: Any
        for button in ui_test.find_all(f"{window}//Frame/**/Button[*]"):
            if button.widget.identifier == identifier:
                await button.focus()
                # WidgetRef.click() undocks windows; send a direct mouse click.
                await ui_test.emulate_mouse_move_and_click(button.center)
                return
        raise AssertionError(f"Missing button {identifier}")

    def check_editor() -> None:
        assert plexus._window.visible and plexus._window.docked
        assert not ui.Workspace.get_window("home").visible
        assert not ui.Workspace.get_window("Viewport").visible
        assert not toolbox._window._tools.visible
        assert isinstance(plexus._editor._graph_view, GraphView)
        assert plexus._editor._catalog_widget is not None
        assert plexus._graph_model.nodes == []
        assert cast(Any, omni.usd.get_context()).get_stage() is None
        assert workspace._tabs[workspace._active_tab_id].stage is None

    try:
        await settle(120)
        workspace_type: Any = importlib.import_module("varun.launcher.workspace.v.01.extension").MyExtension
        plexus_type: Any = importlib.import_module("plexus.extension").PlexusExtension
        toolbox_type: Any = importlib.import_module("tool_box.extension").ToolBoxExtension
        obj: Any
        for obj in gc.get_objects():
            if isinstance(obj, workspace_type):
                workspace = obj
            elif isinstance(obj, plexus_type):
                plexus = obj
            elif isinstance(obj, toolbox_type):
                toolbox = obj
        assert workspace is not None and plexus is not None and toolbox is not None
        assert workspace._active_tab_id == 0 and not plexus._window.visible
        assert not toolbox._window._tools.visible
        manager: Any = app.get_extension_manager()
        assert not manager.is_extension_enabled("omni.graph.window.action")
        assert not manager.is_extension_enabled("omni.graph.bundle.action")
        report["startup_home_without_action_graph"] = True

        await click("home", "home_icon:plexus")
        await complete()
        first: int = workspace._active_tab_id
        assert first != 0 and workspace._tabs[first].label == "Plexus"
        check_editor()
        editor: Any = plexus._editor
        report["home_icon_opens_native_editor_without_stage"] = True

        capture: Any = omni.kit.renderer.capture.acquire_renderer_capture_interface()
        capture.capture_next_frame_swapchain(str(output / "plexus-ui.png"))
        await settle(60)

        # The native catalog's search must remain usable with no node definitions.
        field: Any
        for field in ui_test.find_all("Plexus//Frame/**/StringField[*]"):
            field.widget.model.set_value("test")
        await settle()
        check_editor()
        for field in ui_test.find_all("Plexus//Frame/**/StringField[*]"):
            field.widget.model.set_value("")
        report["empty_catalog_search"] = True

        await click("tabs_bar_window", "workspace_tab:0")
        await complete()
        assert not plexus._window.visible and not toolbox._window._tools.visible
        await click("tabs_bar_window", f"workspace_tab:{first}")
        await complete()
        check_editor()
        assert plexus._editor is editor
        report["home_round_trip_retains_editor"] = True

        workspace.select_home_tab()
        await complete()
        await click("home", "home_label:plexus")
        await complete()
        second: int = workspace._active_tab_id
        assert second != first
        check_editor()
        report["home_label_opens_tab"] = True

        viewport_id: int = workspace.add_viewport_tab()
        await complete()
        viewport_stage: Usd.Stage = workspace._tabs[viewport_id].stage
        marker: Usd.Prim = viewport_stage.DefinePrim("/PlexusRoundTrip", "Xform")
        assert marker.IsValid()
        assert ui.Workspace.get_window("Viewport").visible and toolbox._window._tools.visible
        assert not plexus._window.visible
        workspace._select_tab(first)
        await complete()
        check_editor()
        workspace._select_tab(viewport_id)
        await complete()
        assert cast(Any, omni.usd.get_context()).get_stage() == viewport_stage
        assert viewport_stage.GetPrimAtPath("/PlexusRoundTrip")
        assert toolbox._window._tools.visible
        report["viewport_round_trip_preserves_stage"] = True

        workspace._select_tab(second)
        workspace.select_home_tab()
        workspace._select_tab(viewport_id)
        workspace._select_tab(first)
        await complete()
        check_editor()
        assert plexus._editor is editor
        report["rapid_tab_switching"] = True

        await click("tabs_bar_window", f"workspace_close:{first}")
        await complete()
        assert first not in workspace._tabs
        workspace._select_tab(second)
        await complete()
        check_editor()
        await click("tabs_bar_window", f"workspace_close:{second}")
        await complete()
        assert second not in workspace._tabs and not plexus._window.visible
        report["ui_tabs_close_without_save_prompt"] = True
        report["passed"] = True
    except Exception:
        logging.getLogger(__name__).exception("Plexus UI smoke test failed")
        report["passed"] = False
        report["error"] = traceback.format_exc()
    finally:
        (output / "ui_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("PLEXUS UI SMOKE " + json.dumps(report), flush=True)
        app.post_uncancellable_quit(0 if report.get("passed") else 1)


asyncio.ensure_future(run())
