"""Camera-linked CAD guide drawn by omni.ui.scene below the GPU scene texture."""

# Python & library imports
import math
from typing import Any

# Omniverse & USD imports
import carb.settings
from omni.ui import scene as sc
from pxr import UsdGeom

# ----------------------------------------------------------------------
# 1. STUDIO GRID
# ----------------------------------------------------------------------


class StudioGrid:
    # Register the grid scene and its viewport visibility setting.
    def __init__(self, viewport_api: Any, color: tuple[float, float, float, float]) -> None:
        self._api: Any = viewport_api
        self._settings: Any = carb.settings.get_settings()
        self._visibility: str = f"/persistent/app/viewport/{viewport_api.id}/guide/grid/visible"
        self._color: tuple[float, float, float, float] = color
        self._key: tuple[int, float, tuple[int, int]] | None = None
        self._view: sc.SceneView | None = sc.SceneView(aspect_ratio_policy=sc.AspectRatioPolicy.PRESERVE_ASPECT_FIT)
        viewport_api.add_scene_view(self._view)

    # Invalidate the cached grid after a theme change.
    def set_color(self, color: tuple[float, float, float, float]) -> None:
        self._color = color
        self._key = None

    # Rebuild the camera-centered guide when its scale or position changes.
    def update(self) -> None:
        if self._api is None or self._view is None:
            return
        # Skip grid work when no stage or visible guide is available.
        stage: Any = self._api.stage
        visible: Any = self._settings.get(self._visibility)
        self._view.visible = stage is not None and visible is not False
        if not self._view.visible:
            return
        # Choose the ground-plane axes from the stage up axis.
        up: int = 2 if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z else 1
        axes: tuple[int, int] = (0, 1 if up == 2 else 2)
        camera: Any = self._api.view.GetInverse().ExtractTranslation()
        # Decade-spaced cells stay useful from millimetre parts to large sites.
        height: float = max(abs(float(camera[up])), 1e-6)
        step: float = 10.0 ** math.floor(math.log10(height * 0.15))
        center: tuple[int, int] = (
            math.floor(camera[axes[0]] / step / 10) * 10,
            math.floor(camera[axes[1]] / step / 10) * 10,
        )
        key: tuple[int, float, tuple[int, int]] = (up, step, center)
        # Reuse the existing lines until the camera enters a new grid region.
        if key == self._key:
            return
        self._key = key
        self._view.scene.clear()
        dark: bool = self._color[0] < 0.3
        grey: float = 0.45 if dark else 0.28
        # Use the same subdivisions across the full grid extent.
        radius: int = 400
        fade_start: int = 80

        def point(u: int, v: int) -> list[float]:
            value: list[float] = [0.0, 0.0, 0.0]
            value[axes[0]], value[axes[1]] = u * step, v * step
            return value

        # Draw major and minor lines with fading edges and colored world axes.
        axis: int
        offset: int
        with self._view.scene:
            for axis in (0, 1):
                for offset in range(-radius, radius + 1):
                    index: int = center[1 - axis] + offset
                    start: list[int] = [0, 0]
                    end: list[int] = [0, 0]
                    start[axis], end[axis] = center[axis] - radius, center[axis] + radius
                    start[1 - axis] = end[1 - axis] = index
                    fade: float = min(1.0, max(0.0, (radius - abs(offset)) / (radius - fade_start)))
                    fade = fade * fade * (3.0 - 2.0 * fade)
                    alpha: float = (0.5 if index % 10 == 0 else 0.22) * fade
                    color: tuple[float, float, float, float] = (grey, grey, grey, alpha)
                    if index == 0:
                        # X red; Y green or Z blue, matching Kit's axis widget.
                        color = (
                            (0.65, 0.23, 0.22, 0.6)
                            if axis == 0
                            else (0.25, 0.48, 0.32, 0.6) if up == 2 else (0.25, 0.40, 0.62, 0.6)
                        )
                    sc.Line(point(*start), point(*end), color=color, thickness=1.0)

    # Detach the grid scene from the viewport and release it.
    def destroy(self) -> None:
        if self._view is not None:
            if self._api is not None:
                self._api.remove_scene_view(self._view)
            self._view.scene.clear()
            self._view.destroy()
            self._view = None
        self._api = None
