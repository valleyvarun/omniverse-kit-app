"""OpenUSD viewport package.

Re-exports the public API so existing callers can keep importing from
``..apps.openusd_viewport`` after the module was split into a package.
"""

from .openusd_viewport import OpenUsdViewportManager
from .tab import DEFAULT_LIGHT_RIG, FIRST_VIEWPORT_TITLE, ViewportDockCallback

__all__ = [
    "DEFAULT_LIGHT_RIG",
    "FIRST_VIEWPORT_TITLE",
    "OpenUsdViewportManager",
    "ViewportDockCallback",
]
