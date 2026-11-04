"""Edit SHORTCUTS here, then reload Tool Box to apply the new bindings."""

from collections.abc import Callable, Mapping
from functools import partial
from typing import Any, cast

import carb.input
import omni.kit.actions.core as actions
import omni.kit.app
import omni.kit.hotkeys.core as hotkeys
from omni.kit.viewport.window import get_viewport_window_instances

from tool_box.tools.drawing.methods import DrawingMethods

# ----------------------------------------------------------------------
# 1. EDITABLE SHORTCUTS
# ----------------------------------------------------------------------

# Use Kit key strings such as "M", "SHIFT+D", or "CTRL+ALT+P".
# None leaves a tool available through its button and registered action.
SHORTCUTS: dict[str, str | None] = {
    "move": "M",
    "rotate": "R",
    "scale": "S",
    "draw_points": "B",
    "draw_curves": "SHIFT+D",
    "drawing_plane": None,
    "cancel": "ESCAPE",
    "cursor_view_0": "0",
    "cursor_view_1": "1",
    "cursor_view_2": "2",
    "cursor_view_3": "3",
}

ACTION_NAMESPACE: str = "tool_box"
WINDOW_TITLE: str = "Tool Box"
ACTION_DISPLAY_NAMES: dict[str, str] = {"draw_points": "3D paint", "draw_curves": "3D pipe"}


def viewport_rmb_down() -> bool:
    """Reserve transform keys for navigation while RMB is held in a viewport."""
    input_interface: Any = carb.input.acquire_input_interface()
    if input_interface.get_mouse_value(None, carb.input.MouseInput.RIGHT_BUTTON) <= 0.0:
        return False
    window: Any
    for window in cast(Any, get_viewport_window_instances)(usd_context_name=None):
        if window.focused:
            return True
    return False


# ----------------------------------------------------------------------
# 2. SHORTCUT LIFECYCLE
# ----------------------------------------------------------------------


class ShortcutManager:
    # Window-scoped keys take precedence over global host shortcuts.
    def __init__(self, callbacks: Mapping[str, Callable[[], None]]) -> None:
        self._actions: Any = cast(Any, actions).get_action_registry()
        self._hotkeys: Any = cast(Any, hotkeys).get_hotkey_registry()
        self._bindings: dict[str, list[Any]] = {}
        self._cursor_views_enabled: bool = False
        self._callbacks: dict[str, Callable[[], None]] = dict(callbacks)
        action_id: str
        for action_id in callbacks:
            self._actions.register_action(
                ACTION_NAMESPACE,
                action_id,
                partial(self._invoke, action_id),
                display_name=ACTION_DISPLAY_NAMES.get(action_id, action_id.replace("_", " ").title()),
                description="Tool Box viewport tool",
                tag="Tool Box",
            )
        self._update(None)
        self._subscription: Any = (
            cast(Any, omni.kit.app.get_app())
            .get_update_event_stream()
            .create_subscription_to_pop(self._update, name="tool_box.shortcuts")
        )

    def _invoke(self, action_id: str) -> None:
        if action_id in ("move", "rotate", "scale") and viewport_rmb_down():
            return
        self._callbacks[action_id]()

    # Include viewports created after the extension starts.
    def _update(self, _event: Any) -> None:
        cursor_enabled: bool = DrawingMethods.get().cursor_3d
        if cursor_enabled != self._cursor_views_enabled:
            self._clear_bindings()
            self._cursor_views_enabled = cursor_enabled
        windows: set[str] = {WINDOW_TITLE}
        window: Any
        for window in cast(Any, get_viewport_window_instances)(usd_context_name=None):
            windows.add(str(window.title))
        title: str
        binding: Any
        for title in self._bindings.keys() - windows:
            for binding in self._bindings.pop(title):
                self._hotkeys.deregister_hotkey(binding)
        for title in windows - self._bindings.keys():
            bindings: list[Any] = []
            action_id: str
            key: str | None
            for action_id, key in SHORTCUTS.items():
                if not key or action_id not in self._callbacks:
                    continue
                if action_id.startswith("cursor_view_") and (title == WINDOW_TITLE or not cursor_enabled):
                    continue
                scope: Any = hotkeys.HotkeyFilter(windows=[title])
                # Keep an existing host binding for this exact window intact.
                if self._hotkeys.get_hotkey_for_filter(key, scope) is not None:
                    continue
                binding = self._hotkeys.register_hotkey(
                    hotkey_ext_id=ACTION_NAMESPACE,
                    key=key,
                    action_ext_id=ACTION_NAMESPACE,
                    action_id=action_id,
                    filter=scope,
                )
                if binding is not None:
                    bindings.append(binding)
            self._bindings[title] = bindings

    def _clear_bindings(self) -> None:
        bindings: list[Any]
        binding: Any
        for bindings in self._bindings.values():
            for binding in bindings:
                self._hotkeys.deregister_hotkey(binding)
        self._bindings.clear()

    def destroy(self) -> None:
        self._subscription = None
        self._clear_bindings()
        self._actions.deregister_all_actions_for_extension(ACTION_NAMESPACE)
        self._callbacks.clear()
