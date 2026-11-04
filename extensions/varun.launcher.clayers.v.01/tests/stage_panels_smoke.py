"""Run in New Launcher with --enable omni.kit.ui_test --exec this-file."""

import asyncio
import gc
import importlib
import json
import logging
import time
import traceback
from pathlib import Path
from typing import Any

import omni.kit.actions.core
import omni.kit.app
import omni.kit.renderer.capture
import omni.kit.ui_test
import omni.kit.widget.layers
import omni.ui
from pxr import UsdGeom


async def run() -> None:
    app: Any = omni.kit.app.get_app()
    output: Path = Path(__file__).resolve().parents[4] / "_build/clayers_checks"
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}
    workspace: Any = None
    extension: Any = None
    stage_extension: Any = None

    async def settle(count: int = 20) -> None:
        frame: int
        for frame in range(count):
            await app.next_update_async()

    async def complete() -> None:
        deadline: float = time.monotonic() + 20
        while workspace._tasks:
            assert time.monotonic() < deadline, "Tab switching timed out"
            await app.next_update_async()
        await settle()

    async def capture(name: str) -> None:
        capture_api: Any = omni.kit.renderer.capture.acquire_renderer_capture_interface()
        capture_api.capture_next_frame_swapchain(str(output / f"{name}.png"))
        await settle(4)

    def docked() -> None:
        clayers: Any = omni.ui.Workspace.get_window("Clayers")
        layer: Any = omni.ui.Workspace.get_window("Layer")
        stage: Any = omni.ui.Workspace.get_window("Stage")
        assert clayers and layer and stage
        assert clayers.visible and layer.visible and stage.visible
        assert clayers.dock_id >= 0
        assert clayers.dock_id == layer.dock_id == stage.dock_id
        assert clayers.position_x > omni.ui.Workspace.get_window("Tool Box").position_x

    def widget(kind: str, identifier: str) -> Any:
        item: Any
        for item in omni.kit.ui_test.find_all(f"Clayers//Frame/**/{kind}[*]"):
            if item.widget.identifier == identifier:
                return item
        raise AssertionError(f"Missing Clayers widget: {identifier}")

    def check_alignment() -> None:
        header_name: Any = widget("Label", "clayers_header_name")
        header_type: Any = widget("Label", "clayers_header_type")
        row: Any
        for row in extension._panel.model.rows(extension._panel._expanded):
            name: Any = widget("Label", f"clayers_name:{row.key}")
            kind: Any = widget("Label", f"clayers_type:{row.key}")
            assert abs(name.position.x - header_name.position.x - row.depth * 14) < 1
            assert abs(kind.position.x - header_type.position.x) < 1, f"Type column misaligned: {row.name}"
            column: str
            control: Any
            for column in ("V", "L", "A"):
                header: Any = widget("Label", f"clayers_header_{column}")
                for control in omni.kit.ui_test.find_all("Clayers//Frame/**/InvisibleButton[*]"):
                    if control.widget.identifier == f"clayers_{column}:{row.key}":
                        assert abs(control.center.x - header.center.x) < 1, f"{column} misaligned: {row.name}"
                        assert abs(control.center.y - name.center.y) < 1

    try:
        await settle(120)
        command_type: Any = importlib.import_module("clayers.extension").ClayersExtension
        workspace_type: Any = importlib.import_module("varun.launcher.workspace.v.01.extension").MyExtension
        stage_type: Any = importlib.import_module("omni.kit.window.stage").StageExtension
        obj: Any
        for obj in gc.get_objects():
            if isinstance(obj, command_type):
                extension = obj
            elif isinstance(obj, workspace_type):
                workspace = obj
            elif isinstance(obj, stage_type):
                stage_extension = obj
        assert extension is not None and workspace is not None and stage_extension is not None
        model: Any = extension._panel.model
        layer_model: Any = omni.kit.widget.layers.get_layer_model()

        def layer_identifier() -> str | None:
            root: Any = layer_model.root_layer_item
            return str(root.identifier) if root is not None else None

        assert model.stage is None and model.rows(set()) == []
        assert layer_identifier() is None
        assert stage_extension._window.get_widget().get_model().get_item_children(None) == []
        docked()
        report["home_is_empty"] = True
        await capture("home")

        first: int = workspace.add_viewport_tab()
        await complete()
        first_stage: Any = model.stage
        assert first_stage == workspace._tabs[first].stage
        omni.ui.Workspace.get_window("Clayers").focus()
        await settle()
        check_alignment()
        geometry: Any = UsdGeom

        def create_plane(stage: Any, path: str) -> None:
            mesh: Any = geometry.Mesh.Define(stage, path)
            mesh.CreatePointsAttr([(-1, 0, -1), (1, 0, -1), (1, 0, 1), (-1, 0, 1)])
            mesh.CreateFaceVertexCountsAttr([4])
            mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])

        create_plane(first_stage, "/World/DrawingPlanes/Plane_A")
        stroke: Any = geometry.BasisCurves.Define(first_stage, "/World/ThreeDDraw/Stroke_A")
        stroke.CreateTypeAttr("linear")
        stroke.CreateCurveVertexCountsAttr([2])
        stroke.CreatePointsAttr([(0, 1, 0), (1, 1, 1)])
        stroke.CreateWidthsAttr([0.05, 0.05])
        await settle()
        assert layer_identifier() == first_stage.GetRootLayer().identifier
        assert stage_extension._window.get_widget().get_model().get_item_children(None)
        assert stage_extension._window.get_widget().get_model().stage == first_stage
        rows: list[Any] = model.rows(set(model.groups))
        keys: set[str] = set()
        row: Any
        for row in rows:
            keys.add(row.key)
        assert "/World/DrawingPlanes/Plane_A" in keys and "/World/ThreeDDraw/Stroke_A" not in keys
        owner: str = model.group_for("/World/ThreeDDraw/Stroke_A")
        assert owner is not None
        layer_key: str = model.stroke_layer_key(owner)
        assert layer_key in keys
        extension._panel._expanded.add(layer_key)
        model.refresh()
        await settle()
        assert any(row.key == "/World/ThreeDDraw/Stroke_A" for row in model.rows(extension._panel._expanded))
        model.activate("/World/DrawingPlanes/Plane_A")
        assert model.planes.active_plane() == "/World/DrawingPlanes/Plane_A"
        model.set_visible("/World/DrawingPlanes/Plane_A", False)
        assert not model.visible("/World/DrawingPlanes/Plane_A")
        model.toggle_lock("/World/DrawingPlanes/Plane_A")
        model.select(["/World/DrawingPlanes/Plane_A"])
        await settle()
        assert model.selected_paths() == []
        group: str = model.create_group("Review", members=["/World/DrawingPlanes/Plane_A"])
        model.activate(group)
        await settle()
        docked()
        report["viewport_content_and_controls"] = True
        check_alignment()
        await capture("first-stage")
        index: int
        for index in range(16):
            create_plane(first_stage, f"/World/DrawingPlanes/LayoutCheck_{index}")
        await settle()
        assert extension._panel._scroll.scroll_y_max > 0
        check_alignment()
        await capture("alignment-scrollbar")
        for index in range(16):
            first_stage.RemovePrim(f"/World/DrawingPlanes/LayoutCheck_{index}")
        await settle()
        check_alignment()
        report["header_and_control_alignment"] = True
        panel_title: str
        for panel_title in ("Layer", "Stage", "Clayers"):
            panel_window: Any = omni.ui.Workspace.get_window(panel_title)
            panel_window.focus()
            await settle()
            assert model.stage == first_stage
            await capture(f"panel-{panel_title.lower()}")

        second: int = workspace.add_viewport_tab()
        await complete()
        second_stage: Any = model.stage
        create_plane(second_stage, "/World/DrawingPlanes/Plane_B")
        await settle()
        assert second_stage != first_stage
        assert layer_identifier() == second_stage.GetRootLayer().identifier
        assert stage_extension._window.get_widget().get_model().stage == second_stage
        keys.clear()
        for row in model.rows(set(model.groups)):
            keys.add(row.key)
        assert "/World/DrawingPlanes/Plane_B" in keys and "/World/DrawingPlanes/Plane_A" not in keys
        item: Any
        for item in model.groups.values():
            assert item.name != "Review"
        report["second_viewport_is_isolated"] = True

        workspace._select_tab(first)
        await complete()
        assert model.stage == first_stage
        assert model.groups[group].name == "Review"
        assert model.planes.active_plane() == "/World/DrawingPlanes/Plane_A"
        assert not model.visible("/World/DrawingPlanes/Plane_A")
        assert model.locked("/World/DrawingPlanes/Plane_A")
        model.toggle_lock("/World/DrawingPlanes/Plane_A")
        model.toggle_lock(group)
        await settle()
        assert model.locked("/World/DrawingPlanes/Plane_A")
        model.toggle_lock(group)
        await settle()
        assert not model.locked("/World/DrawingPlanes/Plane_A")
        model.ungroup(group)
        assert model.group_for("/World/DrawingPlanes/Plane_A") is None
        model.select(["/World/DrawingPlanes/Plane_A"])
        await settle()
        assert "/World/DrawingPlanes/Plane_A" in extension._panel._selected
        report["return_restores_stage_state"] = True

        workspace.select_home_tab()
        await complete()
        assert model.stage is None and model.rows(set()) == []
        assert layer_identifier() is None
        assert stage_extension._window.get_widget().get_model().get_item_children(None) == []
        registry: Any = omni.kit.actions.core.get_action_registry()
        registry.execute_action("varun.launcher.layout.v.01", "reset_default_layout")
        await settle(50)
        docked()
        right_window: Any = omni.ui.Workspace.get_window("Clayers")
        right_bottom: Any = omni.ui.Workspace.get_window("right_bottom_window")
        assert right_bottom.visible and right_bottom.dock_id != right_window.dock_id
        assert 100 < omni.ui.Workspace.get_dock_id_height(right_window.dock_id) < 300
        await capture("home-return")
        workspace._select_tab(second)
        await complete()
        assert model.stage == second_stage
        docked()
        report["home_reset_and_switching"] = True
        report["passed"] = True
    except Exception:
        logging.getLogger(__name__).exception("Clayers integration check failed")
        report["passed"] = False
        report["error"] = traceback.format_exc()
        await capture("failure")
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("CLAYERS " + json.dumps(report), flush=True)
        # Test stages are disposable; do not leave the run waiting on a save dialog.
        app.post_uncancellable_quit(0 if report.get("passed") else 1)


asyncio.ensure_future(run())
