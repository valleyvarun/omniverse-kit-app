"""Distance values shared by transform and 3D cursor controls."""

from dataclasses import dataclass
from typing import Any

import carb.settings
from pxr import UsdGeom

UNITS_SETTING: str = "/persistent/exts/varun.launcher.usd_viewport.v.01/units"


@dataclass(frozen=True)
class DistanceUnits:
    name: str
    to_display: float
    step: float


def distance_units(stage: Any) -> DistanceUnits:
    """Match the viewport units while keeping USD edits in stage coordinates."""
    settings: Any = carb.settings.get_settings()
    name: str = "mm" if settings.get(UNITS_SETTING) == "mm" else "m"
    meters: float = float(UsdGeom.GetStageMetersPerUnit(stage)) if stage is not None else 1.0
    return DistanceUnits(name, meters / (0.001 if name == "mm" else 1.0), 1.0 if name == "mm" else 0.1)
