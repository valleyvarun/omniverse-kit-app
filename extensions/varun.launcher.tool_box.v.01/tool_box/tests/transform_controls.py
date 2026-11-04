"""Exercise real XYZ button callbacks, USD undo, and cursor synchronization."""

from __future__ import annotations

import math
from typing import Any
from unittest.mock import patch

import carb.settings
import omni.kit.actions.core
import omni.kit.app
import omni.kit.hotkeys.core
import omni.kit.undo
from omni import ui
from pxr import Gf, Usd, UsdGeom

from tool_box.core.tool import Tool
from tool_box.core.viewport import get_active_stage
from tool_box.shortcuts import ACTION_NAMESPACE
from tool_box.tools.drawing import method_runtime
from tool_box.tools.drawing.method_properties import CanvasProperties
from tool_box.tools.drawing.methods import CursorAnchor, DrawingMethods, Vec3
from tool_box.tools.transform.properties import TransformPropertiesPanel
from tool_box.tools.transform.tools import (
    OP_MOVE,
    OP_ROTATE,
    OP_SCALE,
    TRANSFORM_OP_SETTING,
)
from tool_box.ui.units import UNITS_SETTING, distance_units


def _noop() -> None:
    pass


async def run_shortcut_checks() -> dict[str, bool]:
    """Check native toolbar registration and the launcher's real transform actions."""
    app: Any = omni.kit.app.get_app()
    settings: Any = carb.settings.get_settings()
    registry: Any = omni.kit.hotkeys.core.get_hotkey_registry()
    binding: Any
    for binding in registry.get_all_hotkeys_for_extension("omni.kit.widget.toolbar"):
        assert binding.action_id not in (
            "toolbar::move",
            "toolbar::rotate",
            "toolbar::scale",
            "toolbar::select",
            "toolbar::select_mode",
        ), binding.action_id
    previous_op: Any = settings.get(TRANSFORM_OP_SETTING)
    actions: Any = omni.kit.actions.core.get_action_registry()
    try:
        settings.set_string(TRANSFORM_OP_SETTING, "select")
        await app.next_update_async()
        with patch("tool_box.shortcuts.viewport_rmb_down", return_value=True):
            actions.execute_action(ACTION_NAMESPACE, "scale")
        assert settings.get(TRANSFORM_OP_SETTING) == "select"
        with patch("tool_box.shortcuts.viewport_rmb_down", return_value=False):
            actions.execute_action(ACTION_NAMESPACE, "scale")
        assert settings.get(TRANSFORM_OP_SETTING) == OP_SCALE
    finally:
        settings.set(TRANSFORM_OP_SETTING, previous_op)
    return {"unused_native_toolbar_hotkeys_absent": True, "transform_action_reserves_rmb_navigation": True}


def _button(widget: ui.Widget, identifier: str) -> ui.Button | None:
    if isinstance(widget, ui.Button) and widget.identifier == identifier:
        return widget
    child: ui.Widget
    for child in ui.Inspector.get_children(widget):
        found: ui.Button | None = _button(child, identifier)
        if found is not None:
            return found
    return None


def _click(frame: ui.Frame, identifier: str) -> None:
    button: ui.Button | None = _button(frame, identifier)
    assert button is not None, f"Missing {identifier}"
    button.call_clicked_fn()


async def run_checks(viewport: Any) -> dict[str, bool]:
    """Run in an isolated Kit app with an active viewport and open stage."""
    app: Any = omni.kit.app.get_app()
    stage: Any = viewport.usd_context.get_stage()
    assert stage is not None and stage == get_active_stage()
    settings: Any = carb.settings.get_settings()
    old_units: Any = settings.get(UNITS_SETTING)
    old_op: Any = settings.get(TRANSFORM_OP_SETTING)
    selection: Any = viewport.usd_context.get_selection()
    old_selection: list[str] = selection.get_selected_prim_paths()
    profile: DrawingMethods = DrawingMethods.get()
    old_cursor_enabled: bool = profile.cursor_3d
    anchor: CursorAnchor = method_runtime.get_anchor(viewport)
    old_position: Vec3 | None = anchor.position
    old_placing: bool = anchor.placing
    had_marker: bool = method_runtime._placement_cursor is not None
    window: ui.Window = ui.Window("Toolbox controls check", width=620, height=450)
    panel: TransformPropertiesPanel = TransformPropertiesPanel(_noop)
    tools: dict[str, Tool] = {
        "drawing_plane": Tool("Plane"),
        "draw_on_object": Tool("Object"),
        "cursor_3d": Tool("Cursor"),
    }
    canvas: CanvasProperties = CanvasProperties(tools)
    result: dict[str, bool] = {}
    path: str = "/ToolboxControlsCheck"
    assert not stage.GetPrimAtPath(path)
    cube: Any = UsdGeom.Cube.Define(stage, path)
    translate: Any = UsdGeom.Xformable(cube.GetPrim()).AddTranslateOp()
    translate.Set(Gf.Vec3d(10.0, 20.0, 30.0))
    selection.set_selected_prim_paths([path], False)
    try:
        # Every transform button dispatches through the real USD command path.
        op: str
        expected_step: float
        for op, expected_step in ((OP_MOVE, 0.1), (OP_ROTATE, 1.0), (OP_SCALE, 0.01)):
            settings.set_string(UNITS_SETTING, "m")
            settings.set_string(TRANSFORM_OP_SETTING, op)
            window.frame.clear()
            panel.build_into(window.frame)
            await app.next_update_async()
            before: tuple[float, float, float] | None = panel._read_values(op, path)
            assert before is not None
            assert panel._step_for_op(op) == expected_step
            _click(window.frame, "transform_x_increase")
            after: tuple[float, float, float] | None = panel._read_values(op, path)
            assert after is not None
            stage_step: float = expected_step / distance_units(stage).to_display if op == OP_MOVE else expected_step
            assert math.isclose(after[0] - before[0], stage_step, abs_tol=1e-6), (op, before, after)
            omni.kit.undo.undo()
            restored: tuple[float, float, float] | None = panel._read_values(op, path)
            assert restored is not None and math.isclose(restored[0], before[0], abs_tol=1e-6)
            _click(window.frame, "transform_z_decrease")
            after = panel._read_values(op, path)
            assert after is not None and math.isclose(after[2] - before[2], -stage_step, abs_tol=1e-6)
            omni.kit.undo.undo()
        result["move_rotate_scale_buttons_and_undo"] = True

        settings.set_string(UNITS_SETTING, "mm")
        settings.set_string(TRANSFORM_OP_SETTING, OP_MOVE)
        window.frame.clear()
        panel.build_into(window.frame)
        await app.next_update_async()
        before = panel._read_values(OP_MOVE, path)
        assert before is not None
        _click(window.frame, "transform_y_increase")
        after = panel._read_values(OP_MOVE, path)
        assert after is not None
        assert math.isclose(after[1] - before[1], 1.0 / distance_units(stage).to_display, abs_tol=1e-6)
        omni.kit.undo.undo()
        translate.Set(Gf.Vec3d(4.0, 5.0, 6.0))
        assert math.isclose(panel._field_models[0].get_value_as_float(), 4.0 * distance_units(stage).to_display)
        result["millimeter_step_and_external_transform_sync"] = True

        # Cursor fields reflect placement and write world coordinates in the same units.
        profile.set_option("cursor_3d", True)
        anchor.place((1.0, 2.0, 3.0))
        window.frame.clear()
        with window.frame:
            canvas.build()
        await app.next_update_async()
        _click(window.frame, "cursor_position_x_increase")
        assert anchor.position is not None
        assert math.isclose(anchor.position[0], 1.0 + 1.0 / distance_units(stage).to_display, abs_tol=1e-6)
        assert method_runtime._placement_cursor is not None
        anchor.place((7.0, 8.0, 9.0))
        assert math.isclose(canvas._cursor_models[2].get_value_as_float(), 9.0 * distance_units(stage).to_display)
        canvas._cursor_models[1].set_value(15.0)
        assert anchor.position is not None
        assert math.isclose(anchor.position[1], 15.0 / distance_units(stage).to_display, abs_tol=1e-6)
        assert anchor.position[0] == 7.0 and anchor.position[2] == 9.0 and not anchor.placing
        result["cursor_buttons_typed_values_and_placement_sync"] = True

        # Rebinding detaches the previous stage's cursor listener.
        other_stage: Any = Usd.Stage.CreateInMemory()
        other_anchor: CursorAnchor = CursorAnchor.get(str(other_stage.GetRootLayer().identifier))
        other_anchor.place((11.0, 12.0, 13.0))
        with patch.object(method_runtime, "get_anchor", return_value=other_anchor):
            canvas._bind_cursor()
            canvas._sync_cursor_fields()
            before_other: float = canvas._cursor_models[0].get_value_as_float()
            anchor.place((-1.0, -2.0, -3.0))
            assert canvas._cursor_models[0].get_value_as_float() == before_other
            other_anchor.place((21.0, 22.0, 23.0))
            assert math.isclose(canvas._cursor_models[0].get_value_as_float(), 21.0 * distance_units(stage).to_display)
        canvas._bind_cursor()
        other_anchor.remove_listener(canvas._sync_cursor_fields)
        result["cursor_listener_stage_isolation"] = True

        profile.set_option("cursor_3d", False)
        await app.next_update_async()
        await app.next_update_async()
        assert not canvas._cursor_models
        result["cursor_controls_hide_when_disabled"] = True
    finally:
        canvas.destroy()
        panel.destroy()
        window.destroy()
        if not had_marker:
            method_runtime.cancel_placement()
        if old_position is None:
            anchor.reset()
        else:
            anchor.place(old_position)
        if old_placing:
            anchor.request_placement()
        profile.set_option("cursor_3d", old_cursor_enabled)
        settings.set(UNITS_SETTING, old_units)
        settings.set(TRANSFORM_OP_SETTING, old_op)
        selection.set_selected_prim_paths(old_selection, False)
        stage.RemovePrim(path)
    return result
