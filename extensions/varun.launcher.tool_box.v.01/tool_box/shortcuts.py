"""Edit SHORTCUTS here, then reload Tool Box to apply the new bindings."""

from collections.abc import Callable, Mapping
from typing import Any, cast

import omni.kit.actions.core as actions
import omni.kit.app
import omni.kit.hotkeys.core as hotkeys
from omni.kit.viewport.window import get_viewport_window_instances

# ----------------------------------------------------------------------
# 1. EDITABLE SHORTCUTS
# ----------------------------------------------------------------------

# Use Kit key strings such as "M", "SHIFT+D", or "CTRL+ALT+P".
# None leaves a tool available through its button and registered action.
SHORTCUTS: dict[str, str | None] = {
    "move": "M",
    "rotate": "R",
    "scale": "S",
    "draw_points": "D",
    "draw_curves": "SHIFT+D",
    "drawing_plane": None,
    "cancel": "ESCAPE",
}

ACTION_NAMESPACE = "tool_box"
WINDOW_TITLE = "Tool Box"
ACTION_DISPLAY_NAMES: dict[str, str] = {"draw_points": "3D paint", "draw_curves": "3D pipe"}

# ----------------------------------------------------------------------
# 2. SHORTCUT LIFECYCLE
# ----------------------------------------------------------------------


class ShortcutManager:
    # Window-scoped keys take precedence over global host shortcuts.
    def __init__(self, callbacks: Mapping[str, Callable[[], None]]) -> None:
        self._actions: Any = cast(Any, actions).get_action_registry()
        self._hotkeys: Any = cast(Any, hotkeys).get_hotkey_registry()
        self._bindings: dict[str, list[Any]] = {}
        self._callbacks = dict(callbacks)
        for action_id, callback in callbacks.items():
            self._actions.register_action(
                ACTION_NAMESPACE,
                action_id,
                callback,
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

    # Include viewports created after the extension starts.
    def _update(self, _event: Any) -> None:
        windows = {WINDOW_TITLE}
        windows.update(str(window.title) for window in cast(Any, get_viewport_window_instances)(usd_context_name=None))
        for title in self._bindings.keys() - windows:
            for binding in self._bindings.pop(title):
                self._hotkeys.deregister_hotkey(binding)
        for title in windows - self._bindings.keys():
            bindings: list[Any] = []
            for action_id, key in SHORTCUTS.items():
                if not key or action_id not in self._callbacks:
                    continue
                scope = hotkeys.HotkeyFilter(windows=[title])
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

    def destroy(self) -> None:
        self._subscription = None
        for bindings in self._bindings.values():
            for binding in bindings:
                self._hotkeys.deregister_hotkey(binding)
        self._bindings.clear()
        self._actions.deregister_all_actions_for_extension(ACTION_NAMESPACE)
        self._callbacks.clear()
