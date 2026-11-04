"""Run in an isolated drawing app with omni.kit.ui_test; set /app/tests/iconStrokes=400 for stress."""

import asyncio
import gc
import json
import logging
import math
import traceback
from pathlib import Path
from typing import Any

import carb.settings
import omni.kit.app
import omni.kit.ui_test
import omni.ui
from clayers.extension import ClayersExtension
from clayers.panel import ClayersPanel
from clayers.stage_images import StageImages
from omni.kit.widget.stage.stage_icons import StageIcons
from omni.kit.window.stage.stage_window import StageWindow
from pxr import UsdGeom
from tool_box.extension import ToolBoxExtension
from tool_box.tools.drawing.curves.tool import ThreeDDrawCurvesTool

LOGGER: logging.Logger = logging.getLogger(__name__)


async def run() -> None:
    app: Any = omni.kit.app.get_app()
    report: dict[str, Any] = {}
    output: Path = Path(__file__).resolve().parents[4] / "_build/clayers_icon_checks"
    output.mkdir(parents=True, exist_ok=True)

    async def settle(count: int = 20) -> None:
        frame: int
        for frame in range(count):
            await app.next_update_async()

    def stage_window() -> StageWindow:
        window: omni.ui.WindowHandle
        for window in omni.ui.Workspace.get_windows():
            if isinstance(window, StageWindow):
                return window
        raise AssertionError("Stage window missing")

    async def click(title: str, identifier: str = "", tooltip: str = "") -> None:
        omni.ui.Workspace.get_window(title).focus()
        await settle(2)
        control: Any
        for control in omni.kit.ui_test.find_all(f"{title}//Frame/**/InvisibleButton[*]"):
            if (identifier and control.widget.identifier == identifier) or (
                tooltip and control.widget.tooltip == tooltip
            ):
                assert control.size.x > 0 and control.size.y > 0
                # WidgetRef.click undocks windows, so click the actual docked hit target.
                await omni.kit.ui_test.emulate_mouse_move_and_click(control.center)
                await settle()
                return
        raise AssertionError(f"Control missing: {identifier or tooltip}")

    try:
        await settle(120)
        settings: Any = carb.settings.get_settings()
        settings.set("/app/file/ignoreUnsavedOnExit", True)
        count: int = int(settings.get("/app/tests/iconStrokes") or 30)
        omni.ui.Workspace.set_dock_id_width(omni.ui.Workspace.get_window("Clayers").dock_id, 340)
        await settle()
        layers: ClayersExtension | None = None
        toolbox: ToolBoxExtension | None = None
        obj: Any
        for obj in gc.get_objects():
            if isinstance(obj, ClayersExtension):
                layers = obj
            elif isinstance(obj, ToolBoxExtension):
                toolbox = obj
        assert layers is not None and toolbox is not None and toolbox._controller is not None
        panel: ClayersPanel = layers._panel
        helper: StageImages = layers._stage_images
        tool: ThreeDDrawCurvesTool = toolbox._controller.curves
        stage: Any = panel.model.stage
        tool.activate()
        stroke: int
        step: int
        for stroke in range(count):
            angle: float = stroke * math.tau / 44
            tool._on_draw_begin((math.cos(angle) * 40, stroke * 0.08, math.sin(angle) * 40))
            for step in range(12):
                tool._on_draw_change(
                    (math.cos(angle + step * 0.05) * 40, stroke * 0.08, math.sin(angle + step * 0.05) * 40)
                )
                if step % 5 == 0:
                    await settle(1)
            tool._on_draw_end()
            if stroke % 25 == 0:
                tree: Any = stage_window().get_widget().get_treeview()
                item: Any
                for item in tree.model.find_full_chain("/World/ThreeDDraw"):
                    tree.set_expanded(item, True, False)
        tool.deactivate()
        await settle()
        children: list[Any] = list(stage.GetPrimAtPath("/World/ThreeDDraw").GetChildren())
        last_path: str = str(children[-1].GetPath())
        owner: str | None = panel.model.group_for(last_path)
        assert owner is not None
        panel._expanded.add(panel.model.stroke_layer_key(owner))
        panel.model.refresh()
        await settle()
        panel._scroll.scroll_y = panel._scroll.scroll_y_max
        await settle()
        await click("Clayers", f"clayers_V:{last_path}")
        assert UsdGeom.Imageable(children[-1]).ComputeVisibility() == UsdGeom.Tokens.invisible
        await click("Clayers", f"clayers_V:{last_path}")
        assert UsdGeom.Imageable(children[-1]).ComputeVisibility() != UsdGeom.Tokens.invisible
        groups: int = len(panel.model.groups)
        await click("Clayers", tooltip="Add group")
        assert len(panel.model.groups) == groups + 1
        await click("Stage", "stage_visibility:/World")
        assert UsdGeom.Imageable(stage.GetPrimAtPath("/World")).ComputeVisibility() == UsdGeom.Tokens.invisible
        await click("Stage", "stage_visibility:/World")
        widget: Any = stage_window().get_widget()
        widget.show_inactive_prims = True
        await settle()
        tree = widget.get_treeview()
        for item in tree.model.find_full_chain(str(children[0].GetPath())):
            tree.set_expanded(item, True, False)
        await settle()
        for step in range(20):
            children[0].SetActive(step % 2 == 0)
            await settle(2)
        await settle()
        sources: set[str] = set()
        key: tuple[str, int, int]
        for key in helper._images:
            sources.add(key[0])
        assert StageIcons().get("active_off") in sources
        children[0].SetActive(True)
        widget._filter_by_text("Stroke")
        await settle()
        widget._filter_by_text("")
        await settle()
        tree = widget.get_treeview()
        item = tree.model.find("/World/ThreeDDraw")
        tree.set_expanded(item, False, False)
        await settle()
        tree.set_expanded(item, True, False)
        await settle()
        omni.ui.Workspace.show_window("Stage", False)
        await settle()
        omni.ui.Workspace.show_window("Stage", True)
        await settle()
        reopened_delegate: Any = stage_window().get_widget().get_delegate()
        assert reopened_delegate is not None
        assert "build_widget_sync" in reopened_delegate.get_name_column_delegate().__dict__
        assert len(panel._images) < 20 and len(helper._images) < 20
        report.update(strokes=count, clayers_providers=len(panel._images), stage_providers=len(helper._images))
        patches: list[Any] = list(helper._patches)
        helper.destroy()
        reference: Any
        method_name: str
        callback: Any
        for reference, method_name, callback in patches:
            target: Any = reference()
            assert target is None or getattr(getattr(target, method_name, None), "__func__", None) is not callback
        report["passed"] = True
    except Exception:
        LOGGER.exception("Icon resource regression failed")
        report.update(passed=False, error=traceback.format_exc())
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("ICON_RESOURCE_TEST " + json.dumps(report), flush=True)
        app.post_quit(0 if report.get("passed") else 1)


task: asyncio.Future[None] = asyncio.ensure_future(run())
