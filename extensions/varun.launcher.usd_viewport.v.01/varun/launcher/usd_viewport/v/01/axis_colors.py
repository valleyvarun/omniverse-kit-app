"""Shared Omniverse axis colors for UI guides and Storm materials."""

from typing import TypeAlias

from omni.kit.manipulator.transform.style import COLOR_X, COLOR_Y, COLOR_Z

Color3: TypeAlias = tuple[float, float, float]


def _from_abgr(color: int) -> Color3:
    """Decode the native transform palette using its abgr_to_color convention."""
    return (color & 0xFF) / 255.0, ((color >> 8) & 0xFF) / 255.0, ((color >> 16) & 0xFF) / 255.0


# Native CameraAxisLayer uses this same palette rounded to four decimal places.
AXIS_COLORS: tuple[Color3, Color3, Color3] = (_from_abgr(COLOR_X), _from_abgr(COLOR_Y), _from_abgr(COLOR_Z))


def _linear_channel(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def srgb_to_linear(color: Color3) -> Color3:
    """Invert Storm's hdx colorCorrection.glslfx display transform."""
    return _linear_channel(color[0]), _linear_channel(color[1]), _linear_channel(color[2])
