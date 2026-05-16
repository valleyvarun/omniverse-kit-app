from __future__ import annotations

# --- Standard library imports ---------------------------------------------
# `typing` for static-analysis friendliness.
from typing import Any, Callable

# --- Carb / omni / pxr imports --------------------------------------------
# `omni.ui` -> immediate-mode widgets (HStack, Label, FloatDrag, ...).
import omni.ui as ui

# --- Local imports --------------------------------------------------------
# Window background style + base docked-window class for our extension.
# Per-tool panels live next to their tool's settings (e.g.
# `three_d_draw/tool_settings.py`, `transform/tool_settings.py`) and are
# imported lazily inside `ToolPropertiesWindow.__init__` to avoid
# module-load cycles (those modules import `ToolPropertiesPanel` and
# `_build_xyz_row` from this one).
from ..startup.styles import AGENT_WINDOW_BACKGROUND_STYLE
from ..windows.window import LauncherWindow


# ============================================================================
# SHARED PANEL BASE + HELPERS
# ============================================================================

# Axis labels and matching colors used by transform-style XYZ rows.
# omni.ui colors are 0xAABBGGRR -> X red, Y green, Z blue.
AXIS_LABELS = ("X", "Y", "Z")
AXIS_COLORS = (0xFF5555FF, 0xFF55FF55, 0xFFFF7755)


# Base interface for the per-tool property panels. The window owns ONE
# `ui.Frame` and a list of panels; each tick the window asks each panel
# `is_active()` and renders the first one that says yes by calling
# `build_into(frame)`. Panels manage their own subscriptions and call
# `_refresh()` to ask the window to re-evaluate.
class ToolPropertiesPanel:
    def __init__(self, request_refresh: Callable[[], None]) -> None:
        # Stash the callback the window passed us so we can ping it later.
        self._request_refresh = request_refresh

    # Returns True when this panel wants to be the visible one. Subclasses override.
    def is_active(self) -> bool:
        return False

    # Build the panel UI into the given (freshly cleared) frame. Subclasses override.
    def build_into(self, frame: ui.Frame) -> None:
        del frame  # unused in base class

    # Drop subscriptions / listeners. Called by the window on destroy.
    def destroy(self) -> None:
        pass

    # Convenience used by subclasses: ask the parent window to rebuild.
    def _refresh(self) -> None:
        try:
            self._request_refresh()
        except Exception:
            # The window may already be torn down; ignore.
            pass


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
    # Outer row: small left padding, the row label, then three field groups.
    with ui.HStack(spacing=8, height=24):
        ui.Spacer(width=8)
        ui.Label(label, width=70)
        # Iterate over (X, Y, Z) along with their colors.
        for axis_idx, (axis, color) in enumerate(zip(AXIS_LABELS, AXIS_COLORS)):
            # Per-axis sub-stack: colored letter label + drag field.
            with ui.HStack(spacing=2, width=140):
                ui.Label(axis, width=14, style={"color": color, "font_size": 14})
                # FloatDrag = field you can also drag horizontally to change.
                field = ui.FloatDrag(min=-1e9, max=1e9, step=step)
                field.model.set_value(initial[axis_idx])
                # Capture axis_idx in default-arg to avoid late-binding in lambda.
                sub = field.model.subscribe_value_changed_fn(
                    lambda m, idx=axis_idx: on_changed(idx, m.get_value_as_float())
                )
                models.append(field.model)
                subs.append(sub)
        # Trailing flexible spacer pushes everything left.
        ui.Spacer()
    return models, subs


# ============================================================================
# BOTTOM-DOCK WINDOW
# Owns a single content frame and a list of per-tool panels. The first
# panel whose `is_active()` returns True is rendered into the frame.
# ============================================================================
class ToolPropertiesWindow(LauncherWindow):
    def __init__(self) -> None:
        # Lazy imports: each per-tool panel lives next to its tool's
        # settings (`three_d_draw/tool_settings.py`,
        # `transform/tool_settings.py`) and imports `ToolPropertiesPanel`
        # from this module. Importing them at module top would cycle.
        from .three_d_draw.tool_settings import ThreeDDrawPropertiesPanel
        from .transform.tool_settings import TransformPropertiesPanel

        # The frame the active panel renders into. Created in `_build_ui`.
        self._content_frame: ui.Frame | None = None

        # Per-tool panels in priority order. The first whose `is_active()`
        # returns True is shown. Each panel gets `_refresh` so it can ask
        # the window to re-evaluate when its visibility/state changes.
        self._panels: list[ToolPropertiesPanel] = [
            ThreeDDrawPropertiesPanel(self._refresh),
            TransformPropertiesPanel(self._refresh),
        ]
        # Currently-displayed panel (debug aid; not strictly required).
        self._current_panel: ToolPropertiesPanel | None = None

        # Build the docked window shell. This calls `_build_ui()` below.
        super().__init__(title="Tool Properties", height=60)

        # Initial render so we don't show empty UI on launch.
        self._refresh()

    # Tear down all panels and clear UI state.
    def destroy(self) -> None:
        for panel in self._panels:
            try:
                panel.destroy()
            except Exception:
                pass
        self._panels = []
        self._current_panel = None
        self._content_frame = None
        super().destroy()

    # Build the static window shell (background + content frame).
    # Panel UIs are rebuilt INTO `_content_frame` on every refresh.
    def _build_ui(self) -> None:
        if not self._window:
            return
        with self._window.frame:
            # ZStack so the background rectangle sits behind the panel UI.
            with ui.ZStack():
                ui.Rectangle(style=AGENT_WINDOW_BACKGROUND_STYLE)
                self._content_frame = ui.Frame()

    # Pick the first active panel, clear the frame, and let it build.
    # Called by panels (via `_refresh`) whenever their visibility might
    # have changed (selection, tool change, BrushConfig toggle, ...).
    def _refresh(self) -> None:
        if self._content_frame is None:
            return
        active = self._first_active_panel()
        self._current_panel = active
        # Always start from a clean slate so stale widgets don't linger.
        self._content_frame.clear()
        if active is None:
            return
        try:
            active.build_into(self._content_frame)
        except Exception:
            # Don't let a panel build failure leak partial UI into the dock.
            self._content_frame.clear()

    # Iterate panels in priority order; return the first active one.
    def _first_active_panel(self) -> ToolPropertiesPanel | None:
        for panel in self._panels:
            try:
                if panel.is_active():
                    return panel
            except Exception:
                continue
        return None


# Public API of this module: just the window class.
__all__: list[str] = ["ToolPropertiesWindow"]
