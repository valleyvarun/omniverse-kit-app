from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, cast

import omni.ui as ui


# Style for a square tool button in the viewport toolbar. The `:selected`
# pseudo-class is used to highlight stateful tools (Move / Rotate / Scale)
# while they are the active selection.
_TOOL_BUTTON_STYLE = {
    "Button": {
        "background_color": 0xFF2A2A2A,
        "border_radius": 6,
        "border_width": 1,
        "border_color": 0xFF1A1A1A,
        "padding": 8,
    },
    "Button:hovered": {"background_color": 0xFF3A3A3A},
    "Button:pressed": {"background_color": 0xFF555555},
    "Button:selected": {
        "background_color": 0xFF1B6FB8,    # NVIDIA-ish accent blue.
        "border_color": 0xFF66B0FF,
    },
    "Button.Label": {
        "color": 0xFFE0E0E0,
        "font_size": 14,
    },
    "Button.Label:selected": {
        "color": 0xFFFFFFFF,
    },
    "Button.Image": {
        "color": 0xFFE0E0E0,
    },
    "Button.Image:selected": {
        "color": 0xFFFFFFFF,
    },
}


# Generic launcher tool descriptor.
@dataclass
class Tool:
    name: str
    icon: str | None = None              # Path to the tool's icon image (optional).
    icon_text: str | None = None         # Short text label shown when no icon image is provided.
    shortcut: str | None = None          # Hotkey string, e.g. "C" or "CTRL + S".
    tooltip: str | None = None
    on_click: Callable[[], None] | None = None
    enabled: bool = True
    tags: list[str] = field(default_factory=list)  # type: ignore[type-arg]

    # Stateful tools (Move / Rotate / Scale) flip this on while they are the
    # active selection. Single-use tools like the Drawing Plane leave
    # `toggleable=False` and are always rendered in the inactive style.
    toggleable: bool = False
    is_active: bool = False

    # Internal reference to the most recently built button, so set_active() can
    # update its selected state without the caller having to track the widget.
    _button: ui.Button | None = field(default=None, init=False, repr=False, compare=False)

    def activate(self) -> None:
        if self.enabled and self.on_click is not None:
            self.on_click()

    # Build a square button widget for this tool. Call inside an omni.ui layout.
    def build_button(self, size: int = 36) -> ui.Button:
        tooltip = self.tooltip or self.name
        if self.shortcut:
            tooltip = f"{tooltip}  ({self.shortcut})"

        button = ui.Button(
            self.icon_text or "",
            image_url=self.icon or "",
            width=size,
            height=size,
            clicked_fn=self.activate,
            tooltip=tooltip,
            style=_TOOL_BUTTON_STYLE,
            enabled=self.enabled,
        )
        # Reflect any previously-set active state on the freshly built button.
        cast(Any, button).selected = bool(self.toggleable and self.is_active)
        self._button = button
        return button

    # Toggle the active state and refresh the button highlight. No-op for non-toggleable tools.
    def set_active(self, active: bool) -> None:
        if not self.toggleable:
            return
        self.is_active = active
        if self._button is not None:
            cast(Any, self._button).selected = active
