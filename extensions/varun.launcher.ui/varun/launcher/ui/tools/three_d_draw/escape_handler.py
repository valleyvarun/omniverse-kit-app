"""Subscribe to global keyboard events while the 3D Draw tool is active and
fire a callback on Esc (used to deactivate the tool).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, cast

import carb.input
import omni.appwindow


LOGGER = logging.getLogger(__name__)


class EscapeHandler:
    def __init__(self) -> None:
        self._sub: Any = None
        self._on_escape: Callable[[], None] | None = None

    # Subscribe to global keyboard events; `on_escape` fires on Esc key-press.
    def subscribe(self, on_escape: Callable[[], None]) -> None:
        if self._sub is not None:
            return
        self._on_escape = on_escape
        try:
            app_window: Any = omni.appwindow.get_default_app_window()  # type: ignore[reportUnknownMemberType]
            keyboard = app_window.get_keyboard()
            input_iface = cast(Any, carb.input.acquire_input_interface())
            self._sub = input_iface.subscribe_to_keyboard_events(
                keyboard, self._on_keyboard_event
            )
        except Exception as exc:
            LOGGER.warning("3D Draw: could not subscribe to keyboard events: %s", exc)
            self._sub = None

    # Drop the keyboard subscription if any.
    def unsubscribe(self) -> None:
        if self._sub is None:
            self._on_escape = None
            return
        try:
            app_window: Any = omni.appwindow.get_default_app_window()  # type: ignore[reportUnknownMemberType]
            keyboard = app_window.get_keyboard()
            input_iface = cast(Any, carb.input.acquire_input_interface())
            input_iface.unsubscribe_to_keyboard_events(keyboard, self._sub)
        except Exception:
            pass
        self._sub = None
        self._on_escape = None

    # Returning True keeps the event propagating to other consumers.
    def _on_keyboard_event(self, event: Any) -> bool:
        try:
            event_type = event.type
            key = event.input
        except Exception:
            return True

        if (
            event_type == carb.input.KeyboardEventType.KEY_PRESS
            and key == carb.input.KeyboardInput.ESCAPE
            and self._on_escape is not None
        ):
            try:
                self._on_escape()
            except Exception as exc:
                LOGGER.warning("3D Draw: escape handler raised: %s", exc)
        return True
