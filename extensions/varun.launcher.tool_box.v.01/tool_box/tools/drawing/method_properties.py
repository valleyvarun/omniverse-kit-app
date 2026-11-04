"""Shared canvas buttons and checkboxes, independent of brush settings."""

import math
from functools import partial
from typing import Any, cast

import carb.settings
import omni.ui

from tool_box.core.tool import Tool
from tool_box.core.viewport import get_active_stage
from tool_box.tools.drawing import method_runtime
from tool_box.tools.drawing.methods import (
    CursorAnchor,
    DrawingMethods,
    OptionName,
    Vec3,
)
from tool_box.tools.drawing.plane.selection import (
    DEFAULT_GROUND_PLANE_PATH,
    PlaneSelection,
)
from tool_box.ui.properties import build_xyz_row
from tool_box.ui.units import UNITS_SETTING, DistanceUnits, distance_units


class CanvasProperties:
    def __init__(self, tools: dict[str, Tool]) -> None:
        self._tools: dict[str, Tool] = tools
        self._subscriptions: list[Any] = []
        self._paths: list[str] = []
        self._building: bool = False
        self._frame: omni.ui.Frame | None = None
        self._anchor: CursorAnchor | None = None
        self._cursor_models: list[omni.ui.AbstractValueModel] = []
        self._cursor_units: DistanceUnits = distance_units(None)
        self._syncing_cursor: bool = False
        settings: Any = carb.settings.get_settings()
        self._units_subscription: Any = settings.subscribe_to_node_change_events(UNITS_SETTING, self._units_changed)
        self.checkboxes: dict[OptionName, omni.ui.CheckBox] = {}
        DrawingMethods.get().add_listener(self.refresh)
        PlaneSelection.get().add_listener(self.refresh)

    def destroy(self) -> None:
        DrawingMethods.get().remove_listener(self.refresh)
        PlaneSelection.get().remove_listener(self.refresh)
        if self._anchor is not None:
            self._anchor.remove_listener(self._sync_cursor_fields)
            self._anchor = None
        settings: Any = carb.settings.get_settings()
        settings.unsubscribe_to_change_events(self._units_subscription)
        self._units_subscription = None
        self._cursor_models.clear()
        self._subscriptions.clear()
        self.checkboxes.clear()
        self._frame = None

    def build(self) -> None:
        frame: omni.ui.Frame = omni.ui.Frame(width=126, height=0, identifier="shared_canvas")
        self._frame = frame
        # Seed content so a short scrolling host can measure its initial height.
        with frame:
            self._build()
        frame.set_build_fn(self._build)

    def refresh(self) -> None:
        self._bind_cursor()
        if not self._building and self._frame is not None:
            self._frame.rebuild()

    def _units_changed(self, *_args: object) -> None:
        self.refresh()

    # Keep edits and external placement tied to the active viewport's stage.
    def _bind_cursor(self) -> None:
        anchor: CursorAnchor = method_runtime.get_anchor()
        if anchor is self._anchor:
            return
        if self._anchor is not None:
            self._anchor.remove_listener(self._sync_cursor_fields)
        self._anchor = anchor
        anchor.add_listener(self._sync_cursor_fields)

    def _build(self) -> None:
        self._building = True
        self._subscriptions.clear()
        self._cursor_models.clear()
        self.checkboxes.clear()
        self._bind_cursor()
        options: DrawingMethods = DrawingMethods.get()
        try:
            with omni.ui.VStack(spacing=4, height=0):
                omni.ui.Label("Canvas", height=18)
                with omni.ui.HStack(spacing=6, height=62):
                    name: OptionName
                    for name in ("drawing_plane", "draw_on_object", "cursor_3d"):
                        self._option(name, bool(getattr(options, name)))
                if options.drawing_plane:
                    self._plane_picker()
                if options.cursor_3d:
                    self._cursor_location()
                    with omni.ui.HStack(spacing=2, height=24):
                        axis: int
                        label: str
                        for axis, label in ((1, "1 X"), (2, "2 Y"), (3, "3 Z"), (0, "0")):
                            omni.ui.Button(
                                label,
                                clicked_fn=partial(method_runtime.switch_view, axis),
                                tooltip=(
                                    "Perspective" if axis == 0 else "View perpendicular to this axis; repeat to flip"
                                ),
                            )
        finally:
            self._building = False

    def _cursor_location(self) -> None:
        self._cursor_units = distance_units(get_active_stage())
        position: Vec3 = (
            self._anchor.position if self._anchor is not None and self._anchor.position else (0.0, 0.0, 0.0)
        )
        scale: float = self._cursor_units.to_display
        subscriptions: list[Any]
        self._cursor_models, subscriptions = build_xyz_row(
            label=f"3D cursor ({self._cursor_units.name})",
            initial=(position[0] * scale, position[1] * scale, position[2] * scale),
            step=self._cursor_units.step,
            on_changed=self._cursor_field_changed,
            stacked=True,
            identifier="cursor_position",
        )
        self._subscriptions.extend(subscriptions)

    def _sync_cursor_fields(self) -> None:
        if self._anchor is None:
            return
        position: Vec3 = self._anchor.position or (0.0, 0.0, 0.0)
        self._syncing_cursor = True
        try:
            model: omni.ui.AbstractValueModel
            value: float
            for model, value in zip(self._cursor_models, position):
                model.set_value(value * self._cursor_units.to_display)
        finally:
            self._syncing_cursor = False

    def _cursor_field_changed(self, axis: int, value: float) -> None:
        if self._building or self._syncing_cursor or get_active_stage() is None:
            return
        if self._anchor is None or self._anchor is not method_runtime.get_anchor():
            self.refresh()
            return
        if not math.isfinite(value):
            self._sync_cursor_fields()
            return
        position: Vec3 = self._anchor.position or (0.0, 0.0, 0.0)
        changed: list[float] = list(position)
        changed[axis] = value / self._cursor_units.to_display
        method_runtime.set_cursor_position((changed[0], changed[1], changed[2]))

    def _option(self, name: OptionName, enabled: bool) -> None:
        tool: Tool = self._tools[name]
        with omni.ui.VStack(width=38, spacing=4):
            button: omni.ui.Button = tool.build_button(size=38)
            button.identifier = f"canvas_{name}"
            style: dict[str, dict[str, int]] = cast(dict[str, dict[str, int]], button.style)
            button.style = {
                **style,
                "Button": {**style["Button"], "padding": 2},
                "Button.Image": {"color": 0xFFFFFFFF},
            }
            button.image_width = omni.ui.Pixel(28)
            button.image_height = omni.ui.Pixel(28)
            with omni.ui.HStack(height=20):
                omni.ui.Spacer(width=10)
                checkbox: omni.ui.CheckBox = omni.ui.CheckBox(
                    width=18, identifier=f"drawing_{name}", tooltip=f"Enable {tool.name} canvas"
                )
                checkbox.model.set_value(enabled)
                self.checkboxes[name] = checkbox

                def changed(model: Any) -> None:
                    if not self._building:
                        method_runtime.set_option(name, bool(model.as_bool))

                self._subscriptions.append(checkbox.model.subscribe_value_changed_fn(changed))

    def _plane_picker(self) -> None:
        selection: PlaneSelection = PlaneSelection.get()
        self._paths = [DEFAULT_GROUND_PLANE_PATH, *selection.paths()]
        active: str = selection.active_plane()
        index: int = self._paths.index(active) if active in self._paths else 0
        labels: list[str] = ["Ground"]
        path: str
        for path in self._paths[1:]:
            labels.append(path.rsplit("/", 1)[-1])
        omni.ui.Label("Plane", height=18)
        combo: omni.ui.ComboBox = omni.ui.ComboBox(index, *labels, height=24, identifier="canvas_plane_picker")
        self._subscriptions.append(combo.model.subscribe_item_changed_fn(self._plane_changed))

    def _plane_changed(self, model: Any, _item: Any) -> None:
        index: int = int(model.get_item_value_model().as_int)
        if not self._building and 0 <= index < len(self._paths):
            PlaneSelection.get().set_active_plane(self._paths[index])
