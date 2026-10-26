"""Remember a drawing plane for each stage, independently of launcher panels."""

from collections.abc import Callable
from typing import Any

from tool_box.core.viewport import get_active_stage

DEFAULT_GROUND_PLANE_PATH = "__default_ground_plane__"
PLANE_ROOT = "/World/DrawingPlanes"


class PlaneSelection:
    _instance: "PlaneSelection | None" = None

    def __init__(self) -> None:
        self._selected: dict[str, str] = {}
        self._listeners: list[Callable[[], None]] = []

    @classmethod
    def get(cls) -> "PlaneSelection":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # Root-layer identifiers distinguish simultaneously open stages.
    def active_plane(self, viewport_api: Any = None) -> str:
        stage = get_active_stage(viewport_api)
        if stage is None:
            return DEFAULT_GROUND_PLANE_PATH
        path = self._selected.get(stage.GetRootLayer().identifier, DEFAULT_GROUND_PLANE_PATH)
        if path != DEFAULT_GROUND_PLANE_PATH and not stage.GetPrimAtPath(path):
            return DEFAULT_GROUND_PLANE_PATH
        return path

    def set_active_plane(self, path: str, viewport_api: Any = None) -> None:
        stage = get_active_stage(viewport_api)
        if stage is not None:
            self._selected[stage.GetRootLayer().identifier] = path
            self.notify()

    # Discover saved planes again when a stage is reopened.
    def paths(self) -> list[str]:
        stage = get_active_stage()
        parent = stage.GetPrimAtPath(PLANE_ROOT) if stage is not None else None
        return [str(prim.GetPath()) for prim in parent.GetChildren() if prim.GetTypeName() == "Mesh"] if parent else []

    def add_listener(self, callback: Callable[[], None]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def notify(self) -> None:
        for callback in tuple(self._listeners):
            callback()

    def destroy(self) -> None:
        self._listeners.clear()
        self._selected.clear()
        PlaneSelection._instance = None
