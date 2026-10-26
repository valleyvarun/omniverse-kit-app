"""Resolve the host's active viewport without changing global USD APIs."""

from collections.abc import Callable
from typing import Any, cast

import omni.kit.app
import omni.usd
from omni.kit.viewport.utility import get_active_viewport
from omni.kit.viewport.window import get_viewport_window_instances

# ----------------------------------------------------------------------
# 1. VIEWPORT ACCESS
# ----------------------------------------------------------------------


def get_active_viewport_api() -> Any:
    window: Any
    for window in cast(Any, get_viewport_window_instances)(usd_context_name=None):
        if window.focused:
            return window.viewport_api
    return cast(Any, get_active_viewport)(usd_context_name=None)


def get_active_usd_context(viewport_api: Any = None) -> Any:
    # An input event's viewport takes precedence over window focus.
    viewport: Any = viewport_api if viewport_api is not None else get_active_viewport_api()
    if viewport is None:
        return None
    context: Any = getattr(viewport, "usd_context", None)
    if context is not None:
        return context
    return cast(Any, omni.usd).get_context(str(viewport.usd_context_name or ""))


def get_active_stage(viewport_api: Any = None) -> Any:
    context: Any = get_active_usd_context(viewport_api)
    return context.get_stage() if context is not None else None


# ----------------------------------------------------------------------
# 2. VIEWPORT AND STAGE CHANGES
# ----------------------------------------------------------------------


class ViewportMonitor:
    # Watch stage identity as well as context: tabs may share one context.
    def __init__(self, on_changed: Callable[[], None]) -> None:
        self._on_changed: Callable[[], None] = on_changed
        self._context: Any = None
        self._stage: Any = None
        self._viewport: Any = None
        self._subscription: Any = (
            cast(Any, omni.kit.app.get_app())
            .get_update_event_stream()
            .create_subscription_to_pop(self._on_update, name="tool_box.viewport")
        )
        self._on_update(None)

    def _on_update(self, _event: Any) -> None:
        viewport: Any = get_active_viewport_api()
        context: Any = get_active_usd_context(viewport) if viewport is not None else None
        stage: Any = context.get_stage() if context is not None else None
        if viewport != self._viewport or context != self._context or stage != self._stage:
            self._viewport, self._context, self._stage = viewport, context, stage
            self._on_changed()

    def destroy(self) -> None:
        self._subscription = None
        self._context = self._stage = self._viewport = None
