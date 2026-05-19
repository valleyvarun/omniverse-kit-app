from __future__ import annotations

import logging
import weakref
from dataclasses import dataclass, field
from typing import Any, Callable, cast

import omni.ui as ui


LOGGER = logging.getLogger(__name__)


# Module-level registry of all live toggleable Tool instances. WeakSet so
# tools belonging to torn-down extensions don't keep them alive. Used by
# `set_active(True)` to enforce "only one toggleable tool active at a time"
# across every toolbar group (transform / 3D draw / future tools).
_ACTIVE_REGISTRY: "weakref.WeakSet[Tool]" = weakref.WeakSet()


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
# eq=False keeps default identity-based __eq__/__hash__ so instances are
# hashable (required for storing them in `_ACTIVE_REGISTRY`, a WeakSet).
@dataclass(eq=False)
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

    # Optional callback invoked when this tool is force-deactivated because
    # another toggleable tool became active. Owners (TransformTools / 3D Draw /
    # ...) should assign a function that tears down their tool's runtime state
    # (cursor, gizmo, listeners). If left None, only the highlight is cleared.
    on_deactivate: Callable[[], None] | None = None

    # Internal reference to the most recently built button, so set_active() can
    # update its selected state without the caller having to track the widget.
    _button: ui.Button | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.toggleable:
            _ACTIVE_REGISTRY.add(self)

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
        # If we're being turned ON and weren't already, broadcast a deactivation
        # to every OTHER active toggleable tool so only one ever stays lit.
        if active and not self.is_active:
            for other in list(_ACTIVE_REGISTRY):
                if other is self or not other.is_active:
                    continue
                if other.on_deactivate is not None:
                    try:
                        other.on_deactivate()
                    except Exception:
                        LOGGER.exception("on_deactivate for tool %r failed", other.name)
                # Belt-and-suspenders: ensure highlight clears even if the
                # owner's on_deactivate forgot to call set_active(False).
                if other.is_active:
                    other.is_active = False
                    if other._button is not None:
                        cast(Any, other._button).selected = False
        self.is_active = active
        if self._button is not None:
            cast(Any, self._button).selected = active
