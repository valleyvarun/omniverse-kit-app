"""Shared types and constants for the openusd_viewport package."""

from dataclasses import dataclass, field
from typing import Callable


# Title of the very first viewport tab.
FIRST_VIEWPORT_TITLE = "Viewport"

# Default light rig applied to each tab's stage on first focus.
DEFAULT_LIGHT_RIG = "Grey Studio"

# Single UsdContext shared by every viewport tab. Created once when the
# manager starts and never destroyed. Tab switches re-attach a
# different in-memory stage to it.
SHARED_CTX_NAME = "viewport_shared"


# Callback signature: ``(tab_name, is_selected)``.
ViewportDockCallback = Callable[[str, bool], None]


@dataclass
class ViewportState:
    """Per-tab viewport settings restored after a tab switch."""

    camera_path: str = ""
    hydra_engine: str | None = None
    render_mode: str | None = None
    resolution_scale: float | None = None
    fill_frame: bool | None = None
    lock_to_render_result: bool | None = None
    display_render_var: str | None = None
    render_product_path: str | None = None
    # Lighting menubar mode for this tab: ``""`` = Stage Lights, a rig
    # name (e.g. ``"Grey Studio"``), ``"camera"`` / ``"off"``. ``None``
    # means "never set" -- the manager applies the default rig the first
    # time the tab is focused, then re-applies the saved value on every
    # subsequent switch so it never silently reverts to Stage Lights.
    lighting_mode: str | None = None


@dataclass
class Tab:
    """Per-tab metadata.

    ``title`` is what the user sees in the fake tab bar. ``stage_id``
    is the ``UsdUtils.StageCache`` long-int id of the tab's in-memory
    ``Usd.Stage``; ``None`` until the tab is focused for the first
    time. After a user-initiated Save As, ``user_path`` holds the file
    the stage is bound to (empty until then).
    """

    title: str
    stage_id: int | None = None
    user_path: str = ""
    state: ViewportState = field(default_factory=ViewportState)
