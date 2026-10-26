# Python & library imports
from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager

# --- Standard library imports ---------------------------------------------
# `typing` for static-analysis friendliness.
from typing import Any

# --- Carb / omni / pxr imports --------------------------------------------
# `omni.ui` -> immediate-mode widgets (HStack, Label, FloatDrag, ...).
import omni.ui

from tool_box.shortcuts import WINDOW_TITLE

# Each tool group supplies its own panel using this shared interface.

# ============================================================================
# SHARED PANEL BASE + HELPERS
# ============================================================================

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

# Axis labels and matching colors used by transform-style XYZ rows.
# omni.ui colors are 0xAABBGGRR -> X red, Y green, Z blue.
AXIS_LABELS: tuple[str, ...] = ("X", "Y", "Z")
AXIS_COLORS: tuple[int, ...] = (0xFF5555FF, 0xFF55FF55, 0xFFFF7755)
LOGGER: logging.Logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 2. TOOL PROPERTIES PANEL
# ----------------------------------------------------------------------


# Base interface for the per-tool property panels. The window owns ONE
# `ui.Frame` and a list of panels; each tick the window asks each panel
# `is_active()` and renders the first one that says yes by calling
# `build_into(frame)`. Panels manage their own subscriptions and call
# `_refresh()` to ask the window to re-evaluate.
class ToolPropertiesPanel:
    # Store the callback used to request a panel rebuild.
    def __init__(self, request_refresh: Callable[[], None]) -> None:
        # Stash the callback the window passed us so we can ping it later.
        self._request_refresh: Callable[[], None] = request_refresh

    # Returns True when this panel wants to be the visible one. Subclasses override.
    def is_active(self) -> bool:
        return False

    # Build the panel UI into the given (freshly cleared) frame. Subclasses override.
    def build_into(self, frame: omni.ui.Frame) -> None:
        del frame  # unused in base class

    # Drop subscriptions / listeners. Called by the window on destroy.
    def destroy(self) -> None:
        pass

    # Convenience used by subclasses: ask the parent window to rebuild.
    def _refresh(self) -> None:
        try:
            self._request_refresh()
        except Exception:
            LOGGER.exception("Could not refresh tool properties")


# ----------------------------------------------------------------------
# 3. HELPER FUNCTIONS
# ----------------------------------------------------------------------


def use_stacked_settings() -> bool:
    window: omni.ui.WindowHandle | None = omni.ui.Workspace.get_window(WINDOW_TITLE)
    return window is not None and window.width < 560


@contextmanager
def setting_controls(label: str, label_width: int = 80, wide_width: int | None = None) -> Iterator[None]:
    """Place controls below their label when the toolbox is narrow."""
    stacked: bool = use_stacked_settings()
    stack_type: type[omni.ui.Stack] = omni.ui.VStack if stacked else omni.ui.HStack
    with stack_type(
        spacing=4,
        height=0,
        width=omni.ui.Fraction(1) if stacked or wide_width is None else omni.ui.Pixel(wide_width),
    ):
        omni.ui.Label(label, height=24, width=omni.ui.Fraction(1) if stacked else omni.ui.Pixel(label_width))
        with omni.ui.HStack(spacing=4, height=24):
            yield


# Build a labeled X/Y/Z row of three FloatDrag fields. Returns the value
# models AND the subscription handles so the caller can keep them alive
# (omni.ui drops listeners as soon as the subscription is GC'd).
def build_xyz_row(
    label: str,
    initial: tuple[float, float, float],
    step: float,
    on_changed: Callable[[int, float], None],
) -> tuple[list[Any], list[Any]]:
    # Output collectors (one entry per axis).
    models: list[Any] = []
    subs: list[Any] = []
    # Stack axes in narrow windows; preserve the horizontal launcher controls.
    sidebar: bool = use_stacked_settings()
    stack_type: type[omni.ui.Stack] = omni.ui.VStack if sidebar else omni.ui.HStack
    with stack_type(spacing=8, height=0 if sidebar else 24):
        if not sidebar:
            omni.ui.Spacer(width=8)
        omni.ui.Label(label, width=70, height=24)
        # Iterate over (X, Y, Z) along with their colors.
        axis_idx: int
        axis: str
        color: int
        for axis_idx, (axis, color) in enumerate(zip(AXIS_LABELS, AXIS_COLORS)):
            # Per-axis sub-stack: colored letter label + drag field.
            with omni.ui.HStack(spacing=2, width=omni.ui.Fraction(1) if sidebar else omni.ui.Pixel(140), height=24):
                omni.ui.Label(axis, width=14, style={"color": color, "font_size": 14})
                # FloatDrag = field you can also drag horizontally to change.
                field: omni.ui.FloatDrag = omni.ui.FloatDrag(min=-1e9, max=1e9, step=step)
                field.model.set_value(initial[axis_idx])

                # Bind the axis before subscribing to later edits.
                def changed(model: Any, index: int = axis_idx) -> None:
                    on_changed(index, model.get_value_as_float())

                sub: Any = field.model.subscribe_value_changed_fn(changed)
                models.append(field.model)
                subs.append(sub)
        # Trailing flexible spacer pushes everything left.
        if not sidebar:
            omni.ui.Spacer()
    return models, subs
