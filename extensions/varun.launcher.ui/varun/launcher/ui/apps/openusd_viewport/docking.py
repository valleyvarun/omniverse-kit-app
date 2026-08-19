"""Viewport RTX-pause helper for the openusd_viewport package.

Originally also held an async ``dock_after_delay`` coroutine used to
dock a lazily-created ``ViewportWindow`` into Home. The package now
builds the viewport eagerly in ``OpenUsdViewportManager.__init__`` and
docks it once via ``deferred_dock_in``, so all that remains here is the
``updates_enabled`` toggle (used to pause RTX when the viewport is
behind Home).
"""

from typing import Any, cast

from omni.kit.viewport.window import ViewportWindow


def set_updates_enabled(vp: ViewportWindow, enabled: bool) -> None:
    """Toggle RTX updates on a ``ViewportWindow``. Silent on failure."""
    try:
        api = cast(Any, vp).viewport_api
    except Exception:
        return
    if api is None:
        return
    try:
        api.updates_enabled = bool(enabled)
    except Exception:
        pass
