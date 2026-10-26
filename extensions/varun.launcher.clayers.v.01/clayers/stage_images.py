"""Reuse Stage icons so repeated row rebuilds cannot exhaust GPU texture descriptors."""

import weakref
from collections.abc import Callable
from functools import partial
from types import FunctionType, MethodType, SimpleNamespace
from typing import Any

import carb.events
import omni.kit.app
from omni import ui
from omni.kit.widget.stage.stage_icons import StageIcons
from omni.kit.window.stage.stage_window import StageWindow
from pxr import UsdGeom


class StageImages:
    def __init__(self) -> None:
        self._images: dict[tuple[str, int, int], ui.VectorImageProvider] = {}
        self._patches: list[tuple[weakref.ReferenceType[Any], str, Callable[..., Any]]] = []
        self._window: weakref.ReferenceType[StageWindow] | None = None
        self._update: carb.events.ISubscription | None = (
            omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(self._on_update)
        )

    def _image(self, source: str, width: int, height: int, **kwargs: Any) -> ui.ImageWithProvider:
        key: tuple[str, int, int] = (source, width, height)
        if key not in self._images:
            self._images[key] = ui.VectorImageProvider(source)
        return ui.ImageWithProvider(self._images[key], width=width, height=height, **kwargs)

    # The installed SDK exposes icon construction only on these delegate instances.
    def _patch(self, instance: Any, name: str, callback: Callable[..., Any]) -> None:
        if name in getattr(instance, "__dict__", {}) or not callable(getattr(instance, name, None)):
            return
        setattr(instance, name, MethodType(callback, instance))
        self._patches.append((weakref.ref(instance), name, callback))

    def _on_update(self, _event: carb.events.IEvent) -> None:
        window: ui.WindowHandle
        for window in ui.Workspace.get_windows():
            if not isinstance(window, StageWindow):
                continue
            if self._window is not None and self._window() is window:
                return
            self._window = weakref.ref(window)
            widget: Any = window.get_widget()
            if not callable(getattr(widget, "get_delegate", None)):
                continue
            delegate: Any = widget.get_delegate()
            if not isinstance(getattr(delegate, "set_column_delegates", None), MethodType):
                continue
            self._patch(delegate, "build_branch", self._branch)
            original: Callable[..., Any] = delegate.set_column_delegates.__func__

            def set_columns(owner: Any, columns: list[Any], build: Callable[..., Any] = original) -> None:
                build(owner, columns)
                self._columns(owner)

            self._patch(delegate, "set_column_delegates", set_columns)
            self._columns(delegate)
            widget.get_treeview().dirty_widgets()
            return

    def _columns(self, delegate: Any) -> None:
        name: Any = delegate.get_name_column_delegate()
        if name is not None and callable(getattr(name, "_NameColumnDelegate__get_all_icons_to_draw", None)):
            self._patch(name, "_NameColumnDelegate__draw_all_icons", self._name_icons)
            self._inactive_icons(name)
        column: Any
        for column in getattr(delegate, "_column_delegates", ()):
            if type(column).__name__ == "VisibilityColumnDelegate":
                self._patch(column, "build_widget", self._visibility)

    def _inactive_icons(self, delegate: Any) -> None:
        method: Any = getattr(delegate, "build_widget_sync", None)
        if not isinstance(method, MethodType) or "build_widget_sync" in getattr(delegate, "__dict__", {}):
            return
        # Keep SDK rename/search code; only this instance's inactive icon uses the provider path.
        namespace: SimpleNamespace = SimpleNamespace(**{**vars(ui), "Image": self._inactive_image})
        scope: dict[str, Any] = dict(method.__func__.__globals__)
        scope["ui"] = namespace
        function: FunctionType = FunctionType(
            method.__func__.__code__, scope, argdefs=method.__func__.__defaults__, closure=method.__func__.__closure__
        )
        self._patch(delegate, "build_widget_sync", function)

    def _inactive_image(self, source: str, **kwargs: Any) -> ui.ImageWithProvider:
        width: int = int(kwargs.pop("width", 20))
        height: int = int(kwargs.pop("height", 20))
        return self._image(source, width, height, **kwargs)

    def _branch(self, _delegate: Any, model: Any, item: Any, column_id: int, level: int, expanded: bool) -> None:
        if column_id != 0:
            return
        with ui.HStack(width=20 * (level + 1), height=0, skip_draw_when_clipped=True):
            ui.Spacer()
            if model.can_item_have_children(item):
                self._image(
                    StageIcons().get("Minus" if expanded else "Plus"), 10, 10, style_type_name_override="TreeView.Item"
                )
                ui.Spacer(width=5)

    def _name_icons(self, delegate: Any, item: Any, item_is_native: bool, is_highlighted: bool) -> None:
        sources: list[str] = delegate._NameColumnDelegate__get_all_icons_to_draw(item, item_is_native)
        icon_name: str = "object_icon" if is_highlighted else "object_icon_grey"
        layout: ui.ZStack = ui.ZStack(width=20, height=20, skip_draw_when_clipped=True)
        source: str
        with layout:
            for source in sources:
                self._image(source, 20, 20, name=icon_name, style_type_name_override="TreeView.Image")
        if item.instance_proxy:
            layout.set_tooltip("Instance Proxy")

    async def _visibility(self, _delegate: Any, _column_item: Any, **kwargs: Any) -> None:
        item: Any = kwargs.get("stage_item")
        if not item or not item.prim or not item.prim.IsA(UsdGeom.Imageable):
            return
        hidden: bool = item.visibility_model.get_value_as_bool()
        enabled: bool = not item.instance_proxy and item.active
        color: int = (0x88DDDCCC if hidden else 0xFF8A8777) if enabled else 0x608A8777
        with ui.ZStack(height=20, enabled=enabled, skip_draw_when_clipped=True):
            ui.Spacer(width=22)
            ui.InvisibleButton(
                width=22,
                height=20,
                identifier=f"stage_visibility:{item.prim.GetPath()}",
                clicked_fn=partial(item.visibility_model.set_value, not hidden),
            )
            self._image(
                StageIcons().get("eye_off" if hidden else "eye_on"),
                22,
                20,
                style={"color": color},
                opaque_for_mouse_events=False,
            )

    def destroy(self) -> None:
        self._update = None
        reference: weakref.ReferenceType[Any]
        name: str
        callback: Callable[..., Any]
        for reference, name, callback in reversed(self._patches):
            instance: Any = reference()
            if instance is not None and getattr(getattr(instance, name, None), "__func__", None) is callback:
                delattr(instance, name)
        self._patches.clear()
        self._window = None
        self._images.clear()
