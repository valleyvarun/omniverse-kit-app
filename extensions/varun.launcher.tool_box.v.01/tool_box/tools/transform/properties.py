"""Live, undoable transform fields in the viewport's display units."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any, cast

import carb.settings
import omni.kit.commands
import omni.usd
from omni import ui
from pxr import Gf, Tf, Usd

from tool_box.core.viewport import get_active_stage, get_active_usd_context
from tool_box.tools.transform.tools import (
    OP_MOVE,
    OP_ROTATE,
    OP_SCALE,
    TRANSFORM_OP_SETTING,
)
from tool_box.ui.properties import ToolPropertiesPanel, build_xyz_row
from tool_box.ui.units import UNITS_SETTING, DistanceUnits, distance_units

LOGGER: logging.Logger = logging.getLogger(__name__)


class TransformPropertiesPanel(ToolPropertiesPanel):
    def __init__(self, request_refresh: Callable[[], None]) -> None:
        super().__init__(request_refresh)
        self._setting_subscriptions: list[Any] = []
        self._stage_sub: Any = None
        self._objects_changed_listener: Any = None
        self._field_models: list[ui.AbstractValueModel] = []
        self._field_subs: list[Any] = []
        self._suppress_writes: bool = False
        self._current_op: str | None = None
        self._current_path: str | None = None
        self._current_stage: Any = None
        self._units: DistanceUnits = distance_units(None)
        settings: Any = carb.settings.get_settings()
        key: str
        for key in (TRANSFORM_OP_SETTING, UNITS_SETTING):
            self._setting_subscriptions.append(settings.subscribe_to_node_change_events(key, self._on_setting_changed))
        self.bind_context()

    # Rebind the selection and USD listeners when the host changes viewport.
    def bind_context(self) -> None:
        self._stage_sub = None
        self._unregister_usd_listener()
        context: Any = get_active_usd_context()
        if context is not None:
            self._stage_sub = context.get_stage_event_stream().create_subscription_to_pop(
                self._on_stage_event, name="tool_box.transform.properties"
            )
        self._current_op = self._current_path = None
        self._current_stage = None
        self._refresh()

    def destroy(self) -> None:
        settings: Any = carb.settings.get_settings()
        subscription: Any
        for subscription in self._setting_subscriptions:
            settings.unsubscribe_to_change_events(subscription)
        self._setting_subscriptions.clear()
        self._stage_sub = None
        self._unregister_usd_listener()
        self._field_models.clear()
        self._field_subs.clear()
        self._current_stage = None

    def is_active(self) -> bool:
        return self._active_op() in (OP_MOVE, OP_ROTATE, OP_SCALE) and self._selected_path() is not None

    def build_into(self, frame: ui.Frame) -> None:
        op: str | None = self._active_op()
        path: str | None = self._selected_path()
        if op not in (OP_MOVE, OP_ROTATE, OP_SCALE) or not path:
            return
        stage: Any = get_active_stage()
        self._units = distance_units(stage)
        values: tuple[float, float, float] | None = self._read_values(op, path)
        if values is None:
            return
        self._current_op, self._current_path, self._current_stage = op, path, stage
        self._register_usd_listener()
        self._field_subs.clear()
        with frame, ui.VStack(spacing=4):
            ui.Spacer(height=6)
            self._field_models, self._field_subs = build_xyz_row(
                label=self._label_for_op(op),
                initial=self._display_values(op, values),
                step=self._step_for_op(op),
                on_changed=self._on_field_changed,
            )

    # Stage and setting callbacks keep the visible controls current.
    def _on_setting_changed(self, *_args: object) -> None:
        self._refresh()

    def _on_stage_event(self, event: Any) -> None:
        if int(event.type) in (
            int(omni.usd.StageEventType.SELECTION_CHANGED),
            int(omni.usd.StageEventType.OPENED),
            int(omni.usd.StageEventType.CLOSED),
        ):
            self._refresh()

    def _on_objects_changed(self, notice: Any, _stage: Any) -> None:
        if not self._current_path:
            return
        path: Any
        for path in (*notice.GetChangedInfoOnlyPaths(), *notice.GetResyncedPaths()):
            if str(path) == "/":
                self._refresh()
                return
            if str(path.GetPrimPath()) == self._current_path:
                self._sync_fields_from_stage()
                return

    def _active_op(self) -> str | None:
        settings: Any = carb.settings.get_settings()
        value: Any = settings.get(TRANSFORM_OP_SETTING)
        return str(value) if value else None

    def _selected_path(self) -> str | None:
        context: Any = get_active_usd_context()
        if context is None:
            return None
        paths: list[str] = context.get_selection().get_selected_prim_paths()
        return paths[0] if paths else None

    def _label_for_op(self, op: str) -> str:
        return {OP_MOVE: f"Move ({self._units.name})", OP_ROTATE: "Rotate (deg)", OP_SCALE: "Scale"}.get(op, op)

    def _step_for_op(self, op: str) -> float:
        return self._units.step if op == OP_MOVE else 0.01 if op == OP_SCALE else 1.0

    def _display_values(self, op: str, values: tuple[float, float, float]) -> tuple[float, float, float]:
        factor: float = self._units.to_display if op == OP_MOVE else 1.0
        return values[0] * factor, values[1] * factor, values[2] * factor

    def _register_usd_listener(self) -> None:
        if self._objects_changed_listener is not None:
            return
        stage: Any = get_active_stage()
        if stage is not None:
            self._objects_changed_listener = cast(Any, Tf.Notice).Register(
                cast(Any, Usd.Notice).ObjectsChanged, self._on_objects_changed, stage
            )

    def _unregister_usd_listener(self) -> None:
        if self._objects_changed_listener is not None:
            self._objects_changed_listener.Revoke()
            self._objects_changed_listener = None

    # Keep local SRT values in stage coordinates until they reach the UI.
    def _read_values(self, op: str, path: str) -> tuple[float, float, float] | None:
        stage: Any = get_active_stage()
        if stage is None:
            return None
        prim: Any = stage.GetPrimAtPath(path)
        if not prim:
            return None
        try:
            scale: Any
            rotation: Any
            _order: Any
            translation: Any
            scale, rotation, _order, translation = cast(Any, omni.usd).get_local_transform_SRT(
                prim, cast(Any, Usd.TimeCode).Default()
            )
        except Exception:
            LOGGER.exception("Failed to read transform for %s", path)
            return None
        values: Any = translation if op == OP_MOVE else rotation if op == OP_ROTATE else scale
        return float(values[0]), float(values[1]), float(values[2])

    def _sync_fields_from_stage(self) -> None:
        if not self._current_op or not self._current_path or self._current_stage != get_active_stage():
            return
        values: tuple[float, float, float] | None = self._read_values(self._current_op, self._current_path)
        if values is None:
            return
        self._suppress_writes = True
        try:
            model: ui.AbstractValueModel
            value: float
            for model, value in zip(self._field_models, self._display_values(self._current_op, values)):
                if abs(model.get_value_as_float() - value) > 1e-6:
                    model.set_value(value)
        finally:
            self._suppress_writes = False

    # Buttons and typed edits both use the standard undoable USD command.
    def _on_field_changed(self, axis_idx: int, value: float) -> None:
        if self._suppress_writes or not self._current_op or not self._current_path:
            return
        if self._current_stage != get_active_stage() or self._selected_path() != self._current_path:
            return
        if not math.isfinite(value):
            self._sync_fields_from_stage()
            return
        current: tuple[float, float, float] | None = self._read_values(self._current_op, self._current_path)
        if current is None:
            return
        coordinate: float = value / self._units.to_display if self._current_op == OP_MOVE else value
        if abs(current[axis_idx] - coordinate) < 1e-9:
            return
        changed: list[float] = list(current)
        changed[axis_idx] = coordinate
        context: Any = get_active_usd_context()
        if context is None:
            return
        key: str = {
            OP_MOVE: "new_translation",
            OP_ROTATE: "new_rotation_euler",
            OP_SCALE: "new_scale",
        }[self._current_op]
        kwargs: dict[str, Any] = {
            "path": self._current_path,
            "usd_context_name": context.get_name(),
            key: Gf.Vec3d(*changed),
        }
        try:
            cast(Any, omni.kit.commands).execute("TransformPrimSRTCommand", **kwargs)
        except Exception:
            LOGGER.exception("TransformPrimSRTCommand failed")
