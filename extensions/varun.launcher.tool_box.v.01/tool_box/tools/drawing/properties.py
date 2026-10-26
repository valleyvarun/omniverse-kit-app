"""Responsive brush sliders, pressure toggles, and brush shape selection."""

from collections.abc import Callable
from functools import partial
from typing import Any, cast

from omni import ui

from tool_box.tools.drawing.brush import SHAPES, BrushConfig, BrushKind, PressureTarget
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
        self._buttons: dict[PressureTarget, ui.Button] = {}
        self._brush_model: Any = None
        self._shape_model: Any = None
        self._field_subs: list[Any] = []
        self._suppress: bool = False
        self._was_tool: BrushKind | None = None

    def destroy(self) -> None:
        BrushConfig.get().remove_listener(self._cfg_listener)
        self._field_subs.clear()
        self._models.clear()
        self._buttons.clear()
        self._brush_model = self._shape_model = None

    def is_active(self) -> bool:
        return BrushConfig.get().active

    def build_into(self, frame: ui.Frame) -> None:
        cfg: BrushConfig = BrushConfig.get()
        self._field_subs.clear()
        self._models.clear()
        self._buttons.clear()
        stack: type[ui.Stack] = ui.VStack if use_stacked_settings() else ui.HStack
        with frame, ui.VStack(spacing=6, height=0):
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
        self._brush_model = self._models["size"]
        self._was_tool = cfg.active_tool
        self._sync()

    def _shape_picker(self, cfg: BrushConfig) -> None:
        with setting_controls("Shape", wide_width=200):
            combo: ui.ComboBox = ui.ComboBox(SHAPES.index(cfg.shape), "Circle", "Square", "Triangle")
            self._shape_model = combo.model
            self._field_subs.append(combo.model.subscribe_item_changed_fn(self._shape_changed))

    def _slider(
        self, target: PressureTarget, label: str, value: float, lo: float, hi: float, callback: Callable[[float], None]
    ) -> None:
        with setting_controls(label, wide_width=280):
            field: ui.FloatField = ui.FloatField(width=42, identifier=f"brush_{target}_value")
            field.model.set_value(value)
            ui.FloatSlider(
                field.model, min=lo, max=hi, step=0.1, style={"color": 0x00000000}, identifier=f"brush_{target}_slider"
            )
            self._buttons[target] = ui.Button(
                "P",
                width=24,
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
        finally:
            self._suppress = False
