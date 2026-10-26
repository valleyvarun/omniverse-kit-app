"""Communication Layers extension entry point."""

from typing import Any, cast

import carb.settings
import omni.ext
import omni.ui

from .panel import ClayersPanel
from .stage_images import StageImages


class ClayersExtension(omni.ext.IExt):
    def on_startup(self, _ext_id: str) -> None:
        self._window: omni.ui.Window = omni.ui.Window("Clayers", width=295, height=180)
        self._panel: ClayersPanel = ClayersPanel(self._window.frame)
        self._window.set_key_pressed_fn(self._panel.on_key)
        omni.ui.Workspace.set_show_window_fn("Clayers", self._show)
        settings: Any = carb.settings.get_settings()
        if not settings.get("/exts/varun.launcher.clayers.v.01/showUsdLayers"):
            omni.ui.Workspace.show_window("Layer", False)
        titles: tuple[str, ...] = (
            ("Layer", "Stage") if settings.get("/exts/varun.launcher.clayers.v.01/showUsdLayers") else ("Stage",)
        )
        title: str
        for title in titles:
            window: omni.ui.WindowHandle | None = omni.ui.Workspace.get_window(title)
            if window is None or not window.visible:
                omni.ui.Workspace.show_window(title, True)
        self._stage_images: StageImages = StageImages()

    def _show(self, visible: bool) -> None:
        self._window.visible = visible

    def on_shutdown(self) -> None:
        self._stage_images.destroy()
        cast(Any, omni.ui.Workspace).set_show_window_fn("Clayers", None)
        self._panel.destroy()
        self._window.destroy()
