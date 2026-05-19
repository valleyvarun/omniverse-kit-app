"""Shared types and constants for the openusd_viewport package."""

from dataclasses import dataclass, field
from typing import Callable


# Title of the very first viewport tab. Referenced by `layouts/default.json`
# only as a placeholder name -- with lazy creation the window may not
# exist when the layout loads, which is harmless: the dock slot just
# stays empty until the user activates the tab.
FIRST_VIEWPORT_TITLE = "Viewport"

# Default light rig applied to every freshly-spawned viewport stage.
DEFAULT_LIGHT_RIG = "Grey Studio"


# Callback signature: ``(tab_name, is_selected)``.
ViewportDockCallback = Callable[[str, bool], None]


@dataclass
class ViewportState:
    """Small state restored after a ViewportWindow is respawned."""

    camera_path: str = ""
    hydra_engine: str | None = None
    render_mode: str | None = None
    resolution_scale: float | None = None
    fill_frame: bool | None = None
    lock_to_render_result: bool | None = None
    display_render_var: str | None = None
    render_product_path: str | None = None
    selected_paths: list[str] = field(default_factory=list)


@dataclass
class Tab:
    """Per-tab metadata. Title is what the user sees; ``ctx_name`` is the
    UsdContext bound to this tab. ``state`` keeps lightweight viewport
    choices while the heavy ViewportWindow is destroyed.
    """

    title: str
    ctx_name: str
    state: ViewportState = field(default_factory=ViewportState)
