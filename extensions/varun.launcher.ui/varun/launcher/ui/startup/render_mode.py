"""Performance / Quality render-mode toggle.

Adds three entries under the `Rendering` menu (created by Kit):

* **Quality Mode (RTX)** — switches the active Hydra delegate to RTX (the
  Omniverse path-traced renderer), bumps the framerate cap to 60, and enables
  vsync. Use this when visual quality matters more than CPU/GPU load.
* **Performance Mode (Storm)** — switches the active Hydra delegate to
  Pixar's Storm rasterizer (`omni.hydra.pxr`). No ray tracing, no denoiser,
  no DLSS. Caps framerate at 30 to keep the GPU mostly idle. Looks flat-shaded
  but runs smoothly on integrated graphics.
* **Ultra-Light Mode (Storm @ 15 FPS)** — Storm + 15 FPS cap, for the lowest
  possible idle cost (e.g. when the viewport is barely visible and you just
  need *something* in the dock).

The active mode is persisted to a carb setting so the launcher remembers it
across runs of the same app session.
"""

from __future__ import annotations

import logging
from typing import Any, cast

import carb.settings
import omni.kit.menu.utils
from omni.kit.menu.utils import MenuItemDescription


LOGGER = logging.getLogger(__name__)
MENU_UTILS = cast(Any, omni.kit.menu.utils)

# Carb setting keys ---------------------------------------------------
_ACTIVE_RENDERER = "/renderer/active"
_MAIN_FPS = "/app/runLoops/main/rateLimitFrequency"
_MAIN_RATE_LIMIT = "/app/runLoops/main/rateLimitEnabled"
_PRESENT_FPS = "/app/runLoops/present/rateLimitFrequency"
_PRESENT_RATE_LIMIT = "/app/runLoops/present/rateLimitEnabled"
_VIEWPORT_TICK_RATE = "/persistent/app/viewport/defaults/tickRate"
_VSYNC = "/app/vsync"
_DLSSG = "/rtx-transient/dlssg/enabled"

# Mode identifiers ----------------------------------------------------
MODE_QUALITY = "quality"
MODE_PERFORMANCE = "performance"
MODE_ULTRA_LIGHT = "ultra_light"


def _apply_settings(values: dict[str, object]) -> None:
    settings = cast(Any, carb.settings.get_settings())
    for key, value in values.items():
        try:
            settings.set(key, value)
        except Exception as exc:  # pragma: no cover - carb runtime dependent
            LOGGER.warning("Could not set %s=%r: %s", key, value, exc)


def _set_active_renderer(engine: str) -> None:
    """Switch the live viewport to a different Hydra render delegate.

    `engine` is one of: 'rtx', 'pxr' (Storm), 'iray'.
    """
    settings = cast(Any, carb.settings.get_settings())
    try:
        settings.set(_ACTIVE_RENDERER, engine)
    except Exception as exc:  # pragma: no cover - carb runtime dependent
        LOGGER.warning("Could not switch renderer to %r: %s", engine, exc)

    # Some viewport stacks need an explicit per-viewport call to actually
    # rebind the delegate; the setting alone may only affect future viewports.
    try:
        from omni.kit.viewport.utility import get_active_viewport  # type: ignore[import-not-found]

        vp = get_active_viewport()
        if vp is not None:
            try:
                vp.hydra_engine = engine  # newer API
            except Exception:
                try:
                    vp.set_hd_engine(engine)  # older API
                except Exception:
                    pass
    except ImportError:
        pass


def apply_quality_mode() -> None:
    """Full RTX with 60 FPS cap."""
    _set_active_renderer("rtx")
    _apply_settings(
        {
            _MAIN_RATE_LIMIT: True,
            _MAIN_FPS: 60,
            _PRESENT_RATE_LIMIT: True,
            _PRESENT_FPS: 60,
            _VIEWPORT_TICK_RATE: 60,
            _VSYNC: True,
            _DLSSG: False,
        }
    )


def apply_performance_mode() -> None:
    """Pixar Storm rasterizer with 30 FPS cap."""
    _set_active_renderer("pxr")
    _apply_settings(
        {
            _MAIN_RATE_LIMIT: True,
            _MAIN_FPS: 30,
            _PRESENT_RATE_LIMIT: True,
            _PRESENT_FPS: 30,
            _VIEWPORT_TICK_RATE: 30,
            _VSYNC: True,
            _DLSSG: False,
        }
    )


def apply_ultra_light_mode() -> None:
    """Storm + 15 FPS cap. Lowest idle cost."""
    _set_active_renderer("pxr")
    _apply_settings(
        {
            _MAIN_RATE_LIMIT: True,
            _MAIN_FPS: 15,
            _PRESENT_RATE_LIMIT: True,
            _PRESENT_FPS: 15,
            _VIEWPORT_TICK_RATE: 15,
            _VSYNC: True,
            _DLSSG: False,
        }
    )


class RenderModeMenu:
    """Adds three render-mode entries under the `Rendering` menu."""

    MENU_NAME = "Rendering"

    def __init__(self) -> None:
        self._items: list[MenuItemDescription] = []

    def register(self) -> None:
        self._items = [
            MenuItemDescription(
                name="Quality Mode (RTX)",
                onclick_fn=apply_quality_mode,
            ),
            MenuItemDescription(
                name="Performance Mode (Storm)",
                onclick_fn=apply_performance_mode,
            ),
            MenuItemDescription(
                name="Ultra-Light Mode (Storm @ 15 FPS)",
                onclick_fn=apply_ultra_light_mode,
            ),
        ]
        MENU_UTILS.add_menu_items(self._items, name=self.MENU_NAME)

    def deregister(self) -> None:
        if self._items:
            MENU_UTILS.remove_menu_items(self._items, name=self.MENU_NAME)
            self._items = []
