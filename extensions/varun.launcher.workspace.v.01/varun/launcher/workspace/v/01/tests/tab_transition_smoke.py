"""Run with New Launcher --exec to check docking and capture tab transitions."""

import asyncio
import gc
import importlib
import json
import logging
import time
import traceback
from pathlib import Path
from typing import Any

import omni.kit.app
import omni.kit.renderer.capture
import omni.usd
from omni import ui
from omni.kit.viewport.utility import get_active_viewport_window


async def run() -> None:
    app: Any = omni.kit.app.get_app()
    context: Any = omni.usd.get_context()
    output: Path = Path(__file__).resolve().parents[9] / "_build/tab_transition_checks"
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}

    async def settle(count: int = 30) -> None:
        frame: int
        for frame in range(count):
            await app.next_update_async()

    async def capture(name: str) -> None:
        capture_api: Any = omni.kit.renderer.capture.acquire_renderer_capture_interface()
        capture_api.capture_next_frame_swapchain(str(output / f"{name}.png"))
        await settle(3)

    async def wait_for_switch() -> None:
        deadline: float = time.monotonic() + 20
        while workspace._tasks or not context.can_open_stage() or not layout._layout_ready:
            assert time.monotonic() < deadline, "Tab switch did not finish within 20 seconds"
            await app.next_update_async()
        await settle(20)

    try:
        await settle(80)
        workspace_type: Any = importlib.import_module("varun.launcher.workspace.v.01.extension").MyExtension
        layout_type: Any = importlib.import_module("varun.launcher.layout.v.01.extension").MyExtension
        workspace: Any = None
        layout: Any = None
        obj: Any
        for obj in gc.get_objects():
            if isinstance(obj, workspace_type):
                workspace = obj
            elif isinstance(obj, layout_type):
                layout = obj
        assert workspace is not None and layout is not None
        first: int = workspace.add_viewport_tab()
        await wait_for_switch()
        second: int = workspace.add_viewport_tab()
        await wait_for_switch()
        load_number: int = layout._window_assigner._load_number
        bottom: Any = ui.Workspace.get_window("Tool Box")
        ui.Workspace.set_dock_id_height(bottom.dock_id, 180)
        await settle()
        bottom_height: float = ui.Workspace.get_dock_id_height(bottom.dock_id)
        window: Any = get_active_viewport_window()
        display: Any = window.viewport_widget.display_delegate
        await capture("before")
        report["initial_geometry"] = {
            "viewport": [window.width, window.height],
            "main_width": ui.Workspace.get_main_window_width(),
            "dock_width": ui.Workspace.get_window("Viewport").width,
        }

        # Observe early frames as well as the settled image in both directions.
        index: int
        tab_id: int
        for index, tab_id in enumerate((0, second, first, second, 0, first)):
            workspace._select_tab(tab_id)
            await capture(f"switch-{index}-early")
            sample: int
            for sample in range(4):
                await capture(f"switch-{index}-frame-{sample}")
            await wait_for_switch()
            assert workspace._active_tab_id == tab_id
            assert layout._window_assigner._load_number == load_number, "Tab switch reloaded the workspace"
            assert abs(ui.Workspace.get_dock_id_height(bottom.dock_id) - bottom_height) <= 1
            if tab_id:
                assert context.get_stage() == workspace._tabs[tab_id].stage
                assert not display._cover.visible, "Transition cover did not clear on a rendered frame"
            else:
                assert context.get_stage() is None
                assert not ui.Workspace.get_window("Viewport").visible
            await capture(f"switch-{index}-settled")
        report["stable_docking_and_bottom_height"] = True
        report["cover_clears_after_render"] = True

        # Repeated selection must not enqueue work; rapid switches keep the latest tab.
        pending: int = len(workspace._tasks)
        workspace._select_tab(first)
        assert len(workspace._tasks) == pending
        for tab_id in (second, 0, first, second, first):
            workspace._select_tab(tab_id)
            await settle(1)
        await wait_for_switch()
        assert workspace._context_tab_id == first
        assert layout._window_assigner._load_number == load_number
        assert not display._cover.visible
        report["repeated_and_rapid_selection"] = True
        report["passed"] = True
    except Exception:
        logging.getLogger(__name__).exception("Tab transition check failed")
        report["passed"] = False
        report["error"] = traceback.format_exc()
    finally:
        (output / "transition-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("TAB_TRANSITION " + json.dumps(report), flush=True)
        await context.close_stage_async()
        app.post_quit(0 if report.get("passed") else 1)


asyncio.ensure_future(run())
