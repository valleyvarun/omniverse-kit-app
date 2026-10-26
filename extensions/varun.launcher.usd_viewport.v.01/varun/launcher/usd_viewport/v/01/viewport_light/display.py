"""GPU texture presentation. No CPU image copies and no input interception."""

# Python & library imports
from collections.abc import Sequence
from typing import Any

# Omniverse & USD imports
import omni.usd
from omni import ui
from omni.kit.widget.viewport.display_delegate import ViewportDisplayDelegate

# Local imports
from .depth_background import DepthBackground
from .grid import StudioGrid

# ----------------------------------------------------------------------
# 1. STUDIO DISPLAY
# ----------------------------------------------------------------------


class StudioDisplay(ViewportDisplayDelegate):
    # Track the viewport, backdrop color, and grid handles.
    def __init__(self, viewport_api: Any, color: Sequence[float]) -> None:
        super().__init__(viewport_api)
        self._api: Any = viewport_api
        self._color: tuple[float, float, float, float] = (color[0], color[1], color[2], color[3])
        self._backdrop: ui.Rectangle | None = None
        self._frame: ui.Frame | None = None
        self._grid: StudioGrid | None = None
        self._depth_background: DepthBackground = DepthBackground(viewport_api)
        self._cover: ui.Rectangle | None = None
        self._stage_events: Any = None
        self._stage_ready: bool = False
        self._transition_held: bool = False
        self._pending_frames: int = 2

    # Stack the studio backdrop, grid, and GPU-rendered scene.
    def create(self, ui_frame: ui.Frame, prev_delegate: Any, *args: Any, **kwargs: Any) -> ui.ZStack:
        # Draw the backdrop first, then the grid, then the scene texture.
        with ui_frame:
            stack: ui.ZStack = ui.ZStack()
            with stack:
                backdrop: ui.Rectangle = ui.Rectangle(style={"background_color": ui.color(*self._color)})
                grid_frame: ui.Frame = ui.Frame()
                frame: ui.Frame = ui.Frame()
                cover: ui.Rectangle = ui.Rectangle(style={"background_color": ui.color(*self._color)})
        # Delegate texture presentation to Kit's existing display implementation.
        super().create(frame, prev_delegate, *args, **kwargs)
        self._backdrop, self._frame = backdrop, frame
        self._cover = cover
        with grid_frame:
            self._grid = StudioGrid(self._api, self._color)
        self._stage_ready = self._api.stage is not None
        self._stage_events = self._api.usd_context.get_stage_event_stream().create_subscription_to_pop(
            self._on_stage_event, name="Viewport Light display transition"
        )
        return stack

    # Cover stale render resources until the incoming stage produces an image.
    def begin_transition(self) -> None:
        self._transition_held = True
        self._pending_frames = 2
        if self._cover is not None:
            self._cover.visible = True

    # Wait for fresh render results after the stage and camera have been restored.
    def end_transition(self) -> None:
        self._transition_held = False
        self._pending_frames = 2

    # File operations also invalidate the previously presented image.
    def _on_stage_event(self, event: Any) -> None:
        if event.type in (int(omni.usd.StageEventType.CLOSING), int(omni.usd.StageEventType.CLOSED)):
            self._depth_background.destroy()
            self._stage_ready = False
            self._pending_frames = 2
            if self._cover is not None:
                self._cover.visible = True
        elif event.type == int(omni.usd.StageEventType.OPENED):
            self._stage_ready = True

    # Update the backdrop and grid when the theme changes.
    def set_color(self, color: Sequence[float]) -> None:
        rgba: tuple[float, float, float, float] = (color[0], color[1], color[2], color[3])
        if rgba == self._color:
            return
        self._color = rgba
        if self._backdrop:
            self._backdrop.style = {"background_color": ui.color(*color)}
        if self._grid:
            self._grid.set_color(rgba)
        if self._cover is not None:
            self._cover.style = {"background_color": ui.color(*rgba)}

    # Refresh the grid before presenting the rendered texture.
    def update(
        self,
        viewport_api: Any,
        texture: Any,
        view: Any,
        projection: Any,
        presentation_key: int = 0,
        metadata: Any = None,
    ) -> tuple[int, int]:
        if not self._stage_ready or viewport_api.stage is None:
            return self.size
        self._depth_background.update()
        if self._grid:
            self._grid.update()
        size: tuple[int, int] = super().update(viewport_api, texture, view, projection, presentation_key, metadata)
        if not self._transition_held:
            self._pending_frames = max(0, self._pending_frames - 1)
            if self._cover is not None and self._pending_frames == 0:
                self._cover.visible = False
        return size

    # Release the grid and display widgets.
    def destroy(self) -> None:
        self._depth_background.destroy()
        self._stage_events = None
        self._stage_ready = False
        if self._grid:
            self._grid.destroy()
            self._grid = None
        super().destroy()
        self._backdrop = None
        self._frame = None
        self._cover = None
