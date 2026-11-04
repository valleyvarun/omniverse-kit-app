"""CPU checks for independent paint and pipe brush settings."""

from __future__ import annotations

import math

from tool_box.tools.drawing.brush import (
    BrushConfig,
    BrushKind,
    BrushSample,
    BrushShape,
    PressureTarget,
)


def run_checks() -> dict[str, object]:
    cfg: BrushConfig = BrushConfig()
    observed: list[tuple[BrushKind | None, float, BrushShape]] = []

    def changed() -> None:
        observed.append((cfg.active_tool, cfg.brush_radius, cfg.shape))

    cfg.add_listener(changed)
    cfg.set_active(True, "paint")
    cfg.set_brush_radius(12.0)
    cfg.set_hardness(0.8)
    cfg.set_opacity(0.6)
    cfg.set_shape("triangle")
    target: PressureTarget
    for target in ("size", "hardness", "opacity"):
        cfg.set_pressure(target, True)
    paint_pressure: dict[PressureTarget, bool] = cfg.pressure
    cfg.set_active(False)
    cfg.set_active(True, "pipe")
    assert cfg.brush_radius == cfg.BRUSH_RADIUS_DEFAULT
    assert cfg.hardness == cfg.opacity == 1.0
    assert cfg.shape == "circle" and not any(cfg.pressure.values())
    assert cfg.pressure is not paint_pressure
    assert observed[-1] == ("pipe", cfg.BRUSH_RADIUS_DEFAULT, "circle")
    cfg.set_brush_radius(5.0)
    cfg.set_hardness(0.3)
    cfg.set_opacity(0.4)
    cfg.set_shape("square")
    cfg.adjust(40.0, -20.0)
    pipe_radius: float = cfg.brush_radius
    assert pipe_radius > 5.0 and cfg.hardness == 0.3
    cfg.set_active(True, "paint")
    assert cfg.brush_radius == 12.0 and cfg.shape == "triangle"
    assert cfg.hardness == 0.8 and cfg.opacity == 0.6 and all(cfg.pressure.values())
    assert observed[-1] == ("paint", 12.0, "triangle")
    sample: BrushSample = cfg.sample(0.25)
    assert sample.radius == 3.0
    assert math.isclose(sample.hardness, 0.2) and math.isclose(sample.opacity, 0.15)
    assert cfg.sample(None) == BrushSample(12.0, 0.8, 0.6)
    cfg.adjust(20.0, 0.0)
    assert math.isclose(cfg.hardness, 0.9)
    cfg.set_pressure("size", False)
    cfg.set_active(False)
    assert cfg.active_tool is None and cfg.brush_radius == 12.0
    cfg.set_active(True, "pipe")
    assert cfg.brush_radius == pipe_radius and cfg.shape == "square"
    assert cfg.hardness == 0.3 and cfg.opacity == 0.4 and not any(cfg.pressure.values())
    cfg.set_pressure("size", True)
    cfg.set_active(True, "paint")
    assert not cfg.pressure["size"] and cfg.pressure["hardness"] and cfg.pressure["opacity"]
    cfg.remove_listener(changed)
    return {
        "passed": True,
        "independent_values": True,
        "independent_pressure": True,
        "independent_adjustment": True,
        "listener_switches": True,
    }
