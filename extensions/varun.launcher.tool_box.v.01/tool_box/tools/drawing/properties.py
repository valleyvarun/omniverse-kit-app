"""Responsive brush sliders, pressure toggles, shape, and drawing method."""

from collections.abc import Callable
from functools import partial
from typing import Any, cast

import omni.ui

from tool_box.core.resources import ICONS_DIR
from tool_box.tools.drawing import method_runtime
from tool_box.tools.drawing.brush import SHAPES, BrushConfig, BrushKind, PressureTarget
from tool_box.tools.drawing.methods import DrawingMethods
from tool_box.ui.properties import (
    ToolPropertiesPanel,
    setting_controls,
    use_stacked_settings,
)


class ThreeDDrawPropertiesPanel(ToolPropertiesPanel):
    def __init__(self, request_refresh: Callable[[], None]) -> None:
        super().__init__(request_refresh)
        self._cfg_listener: Callable[[], None] = self._on_cfg_changed
        BrushConfig.get().add_listener(self._cfg_listener)
        self._models: dict[PressureTarget, Any] = {}
        self._buttons: dict[PressureTarget, omni.ui.Button] = {}
        self._brush_model: Any = None
        self._shape_model: Any = None
        self._axes_checkbox: omni.ui.CheckBox | None = None
        self._field_subs: list[Any] = []
        self._suppress: bool = False
        self._was_tool: BrushKind | None = None
        DrawingMethods.get().add_listener(self._sync)

    def destroy(self) -> None:
        BrushConfig.get().remove_listener(self._cfg_listener)
        DrawingMethods.get().remove_listener(self._sync)
        self._field_subs.clear()
        self._models.clear()
        self._buttons.clear()
        self._brush_model = self._shape_model = None
        self._axes_checkbox = None

    def is_active(self) -> bool:
        return BrushConfig.get().active

    def build_into(self, frame: omni.ui.Frame) -> None:
        cfg: BrushConfig = BrushConfig.get()
        self._field_subs.clear()
        self._models.clear()
        self._buttons.clear()
        stack: type[omni.ui.Stack] = omni.ui.VStack if use_stacked_settings() else omni.ui.HStack
        window: omni.ui.WindowHandle | None = omni.ui.Workspace.get_window("Tool Box")
        horizontal: bool = window is not None and window.width >= 950
        with frame, omni.ui.VStack(spacing=6, height=0, width=566 if horizontal else omni.ui.Fraction(1)):
            with stack(spacing=6, height=0):
                self._slider("size", "Brush Size", cfg.brush_radius, 0.5, 20.0, cfg.set_brush_radius)
                if not use_stacked_settings():
                    self._shape_picker(cfg)
            if cfg.active_tool == "paint":
                with stack(spacing=6, height=0):
                    self._slider("hardness", "Hardness", cfg.hardness * 100, 0, 100, self._set_hardness)
                    self._slider("opacity", "Opacity", cfg.opacity * 100, 0, 100, self._set_opacity)
            if use_stacked_settings():
                self._shape_picker(cfg)
            self._method_settings()
        self._brush_model = self._models["size"]
        self._was_tool = cfg.active_tool
        self._sync()

    def _shape_picker(self, cfg: BrushConfig) -> None:
        with setting_controls("Shape", wide_width=200):
            combo: omni.ui.ComboBox = omni.ui.ComboBox(SHAPES.index(cfg.shape), "Circle", "Square", "Triangle")
            self._shape_model = combo.model
            self._field_subs.append(combo.model.subscribe_item_changed_fn(self._shape_changed))

    def _method_settings(self) -> None:
        with omni.ui.VStack(spacing=4, height=0):
            omni.ui.Label("Method", height=24)
            with omni.ui.HStack(spacing=6, height=24):
                checkbox: omni.ui.CheckBox = omni.ui.CheckBox(
                    width=18,
                    identifier="drawing_axes_spokes",
                    tooltip="Enable world-axis spokes for constrained drawing",
                )
                checkbox.model.set_value(DrawingMethods.get().axes_spokes)
                self._axes_checkbox = checkbox
                omni.ui.Label("Axes spokes")
                self._field_subs.append(checkbox.model.subscribe_value_changed_fn(self._axes_changed))

    def _axes_changed(self, model: omni.ui.AbstractValueModel) -> None:
        if not self._suppress:
            method_runtime.set_option("axes_spokes", model.as_bool)

    def _slider(
        self, target: PressureTarget, label: str, value: float, lo: float, hi: float, callback: Callable[[float], None]
    ) -> None:
        with setting_controls(label, wide_width=280):
            field: omni.ui.FloatField = omni.ui.FloatField(width=42, identifier=f"brush_{target}_value")
            field.model.set_value(value)
            omni.ui.FloatSlider(
                field.model, min=lo, max=hi, step=0.1, style={"color": 0x00000000}, identifier=f"brush_{target}_slider"
            )
            self._buttons[target] = omni.ui.Button(
                image_url=str(ICONS_DIR / "pressure.svg"),
                image_width=20,
                image_height=20,
                width=24,
                height=24,
                tooltip=f"Stylus pressure controls {label.lower()}",
                clicked_fn=partial(self._toggle, target),
                identifier=f"brush_{target}_pressure",
                style={"Button:selected": {"background_color": 0xFF8A653A}},
            )
            self._models[target] = field.model

            def changed(model: Any) -> None:
                if not self._suppress:
                    callback(float(model.get_value_as_float()))

            self._field_subs.append(field.model.subscribe_value_changed_fn(changed))

    def _toggle(self, target: PressureTarget) -> None:
        cfg: BrushConfig = BrushConfig.get()
        cfg.set_pressure(target, not cfg.pressure[target])

    def _set_hardness(self, value: float) -> None:
        BrushConfig.get().set_hardness(value / 100.0)

    def _set_opacity(self, value: float) -> None:
        BrushConfig.get().set_opacity(value / 100.0)

    def _shape_changed(self, model: Any, item: Any) -> None:
        if not self._suppress:
            BrushConfig.get().set_shape(SHAPES[model.get_item_value_model().as_int])

    def _on_cfg_changed(self) -> None:
        cfg: BrushConfig = BrushConfig.get()
        if cfg.active_tool != self._was_tool:
            self._was_tool = cfg.active_tool
            self._refresh()
        else:
            self._sync()

    def _sync(self) -> None:
        cfg: BrushConfig = BrushConfig.get()
        self._suppress = True
        try:
            target: PressureTarget
            value: float
            values: tuple[tuple[PressureTarget, float], ...] = (
                ("size", cfg.brush_radius),
                ("hardness", cfg.hardness * 100),
                ("opacity", cfg.opacity * 100),
            )
            for target, value in values:
                if target in self._models:
                    self._models[target].set_value(value)
                    cast(Any, self._buttons[target]).selected = cfg.pressure[target]
            if self._shape_model is not None:
                self._shape_model.get_item_value_model().set_value(SHAPES.index(cfg.shape))
            if self._axes_checkbox is not None:
                self._axes_checkbox.model.set_value(DrawingMethods.get().axes_spokes)
        finally:
            self._suppress = False
