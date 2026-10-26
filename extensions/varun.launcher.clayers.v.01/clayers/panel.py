"""Communication-layer rows and controls, styled after the original launcher."""

from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import carb.input
from omni import ui
from tool_box.tools.drawing.plane.selection import DEFAULT_GROUND_PLANE_PATH

from .model import ROOT_GROUP, CommunicationLayers, Group, Row

ICONS: Path = Path(__file__).resolve().parent / "icons"
ROW_HEIGHT: int = 28
NAME_GUTTER: int = 28
TYPE_WIDTH: int = 64
CONTROL_WIDTH: int = 28
LABEL_STYLE: dict[str, int] = {"color": 0xFFCCCCCC}
HEADER_STYLE: dict[str, int] = {"color": 0xFFAAAAAA}


class ClayersPanel:
    def __init__(self, frame: ui.Frame) -> None:
        self._frame: ui.Frame = frame
        self._expanded: set[str] = set()
        self._selected: set[str] = set()
        self._stage_id: str = ""
        self._row_frame: ui.Frame | None = None
        self._header_row: ui.HStack | None = None
        self._footer: ui.HStack | None = None
        self._scroll: Any = None
        self._header_gutter: Any = None
        self._footer_gutter: Any = None
        self._images: dict[tuple[str, int], ui.VectorImageProvider] = {}
        self.model: CommunicationLayers = CommunicationLayers(self.refresh)
        self._build()

    def _build(self) -> None:
        with self._frame, ui.ZStack():
            ui.Rectangle(style={"background_color": 0xFF333333})
            with ui.VStack(spacing=0):
                with ui.ZStack(height=ROW_HEIGHT):
                    ui.Rectangle(style={"background_color": 0xFF2A2A2A})
                    self._header_row = ui.HStack(height=ROW_HEIGHT, spacing=0)
                    with self._header_row:
                        ui.Spacer(width=NAME_GUTTER)
                        ui.Label(
                            "Name",
                            width=ui.Fraction(1),
                            height=ROW_HEIGHT,
                            alignment=ui.Alignment.LEFT_CENTER,
                            style=HEADER_STYLE,
                            identifier="clayers_header_name",
                        )
                        ui.Label(
                            "Type",
                            width=TYPE_WIDTH,
                            height=ROW_HEIGHT,
                            alignment=ui.Alignment.LEFT_CENTER,
                            style=HEADER_STYLE,
                            identifier="clayers_header_type",
                        )
                        label: str
                        for label in ("V", "L", "A"):
                            ui.Label(
                                label,
                                width=CONTROL_WIDTH,
                                height=ROW_HEIGHT,
                                alignment=ui.Alignment.CENTER,
                                style=HEADER_STYLE,
                                identifier=f"clayers_header_{label}",
                            )
                        self._header_gutter = ui.Spacer(width=0)
                self._scroll = ui.ScrollingFrame(
                    horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                    vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_AS_NEEDED,
                )
                with self._scroll:
                    self._row_frame = ui.Frame(build_fn=self._build_rows)
                with ui.ZStack(height=ROW_HEIGHT):
                    ui.Rectangle(style={"background_color": 0xFF2A2A2A})
                    self._footer = ui.HStack(spacing=4, enabled=False)
                    with self._footer:
                        ui.Spacer(width=6)
                        self._button("add_group", "Add group", partial(self._add_group, False))
                        self._button("group", "Group selected", partial(self._add_group, True))
                        self._button("ungroup", "Ungroup", self._ungroup)
                        ui.Spacer()
                        self._footer_gutter = ui.Spacer(width=0)
        self._scroll.set_computed_content_size_changed_fn(self._sync_gutter)
        self._header_row.set_computed_content_size_changed_fn(self._sync_gutter)
        self._row_frame.set_computed_content_size_changed_fn(self._sync_gutter)

    # Match the actual viewport width, including scrollbar changes after rebuilding rows.
    def _sync_gutter(self) -> None:
        if self._header_row is None or self._row_frame is None or self._row_frame.computed_content_width <= 0:
            return
        width: ui.Pixel = ui.Pixel(
            max(0.0, self._header_row.computed_content_width - self._row_frame.computed_content_width)
        )
        self._header_gutter.width = width
        self._footer_gutter.width = width

    def refresh(self) -> None:
        stage_id: str = self.model.stage.GetRootLayer().identifier if self.model.stage is not None else ""
        if stage_id != self._stage_id:
            self._stage_id = stage_id
            self._selected.clear()
            self._expanded = set(self.model.groups)
        paths: set[str] = set(self.model.selected_paths())
        key: str
        for key in self._selected:
            if key in self.model.groups:
                paths.add(key)
        self._selected = paths
        if self._footer is not None:
            self._footer.enabled = self.model.stage is not None
        if self._row_frame is not None:
            self._row_frame.rebuild()

    def _button(self, icon: str, tooltip: str, callback: Callable[[], None]) -> None:
        with ui.ZStack(width=28, height=ROW_HEIGHT):
            ui.Rectangle(style={"background_color": 0xFF2E2E2E, "border_radius": 3})
            ui.InvisibleButton(
                width=28,
                height=ROW_HEIGHT,
                tooltip=tooltip,
                clicked_fn=callback,
            )
            with ui.HStack():
                ui.Spacer()
                with ui.VStack(width=13):
                    ui.Spacer()
                    self._image(icon, 13)
                    ui.Spacer()
                ui.Spacer()

    # Reuse provider textures when USD notices rebuild the rows.
    def _image(self, icon: str, size: int) -> None:
        key: tuple[str, int] = (icon, size)
        if key not in self._images:
            self._images[key] = ui.VectorImageProvider(str(ICONS / f"{icon}.svg"))
        ui.ImageWithProvider(self._images[key], width=size, height=size, opaque_for_mouse_events=False)

    def _icon(
        self,
        icon: str,
        tooltip: str,
        callback: Callable[[], None],
        identifier: str = "",
        width: int = CONTROL_WIDTH,
        size: int = 16,
    ) -> None:
        # Center the native image button within the same column used by its header.
        with ui.VStack(width=width, height=ROW_HEIGHT, spacing=0):
            ui.Spacer()
            with ui.HStack(height=size, spacing=0):
                ui.Spacer()
                with ui.ZStack(width=size, height=size):
                    ui.InvisibleButton(
                        width=size,
                        height=size,
                        tooltip=tooltip,
                        clicked_fn=callback,
                        identifier=identifier,
                    )
                    self._image(icon, size)
                ui.Spacer()
            ui.Spacer()

    def _build_rows(self) -> None:
        active_plane: str = self.model.planes.active_plane()
        row: Row
        with ui.VStack(spacing=0):
            for row in self.model.rows(self._expanded):
                self._build_row(row, active_plane)
            ui.Spacer()

    def _build_row(self, row: Row, active_plane: str) -> None:
        active: bool = self.model.groups[row.key].active if row.kind == "Group" else row.key == active_plane
        background: int = (0xFF1F5F1F if row.kind == "Group" else 0xFF3A2D4D) if active else 0
        with ui.ZStack(height=ROW_HEIGHT, skip_draw_when_clipped=True):
            ui.Rectangle(style={"background_color": background})
            with ui.HStack(height=ROW_HEIGHT, spacing=0):
                ui.Spacer(width=8)
                if row.kind == "Group":
                    self._icon(
                        "minus" if row.key in self._expanded else "plus",
                        "Collapse or expand group",
                        partial(self._toggle_expanded, row.key),
                        f"clayers_toggle:{row.key}",
                        width=NAME_GUTTER - 8,
                        size=12,
                    )
                else:
                    ui.Spacer(width=NAME_GUTTER - 8)
                with ui.HStack(width=ui.Fraction(1), height=ROW_HEIGHT, spacing=0):
                    if row.depth:
                        ui.Spacer(width=14 * row.depth)
                    name: ui.Label = ui.Label(
                        row.name,
                        width=ui.Fraction(1),
                        height=ROW_HEIGHT,
                        alignment=ui.Alignment.LEFT_CENTER,
                        style=LABEL_STYLE,
                        elided_text=True,
                        tooltip=row.name,
                        identifier=f"clayers_name:{row.key}",
                    )
                    name.set_mouse_pressed_fn(partial(self._select, row))
                ui.Label(
                    row.kind,
                    width=TYPE_WIDTH,
                    height=ROW_HEIGHT,
                    alignment=ui.Alignment.LEFT_CENTER,
                    style=LABEL_STYLE,
                    identifier=f"clayers_type:{row.key}",
                )
                if row.key == DEFAULT_GROUND_PLANE_PATH:
                    ui.Spacer(width=2 * CONTROL_WIDTH)
                else:
                    visible: bool = (
                        self.model.groups[row.key].visible if row.kind == "Group" else self.model.visible(row.key)
                    )
                    locked: bool = (
                        self.model.group_locked(row.key) if row.kind == "Group" else self.model.locked(row.key)
                    )
                    self._icon(
                        "eye_on" if visible else "eye_off",
                        "Toggle visibility",
                        partial(self.model.set_visible, row.key, not visible),
                        f"clayers_V:{row.key}",
                    )
                    self._icon(
                        "lock_locked" if locked else "lock_unlocked",
                        "Toggle lock",
                        partial(self.model.toggle_lock, row.key),
                        f"clayers_L:{row.key}",
                    )
                if row.kind in ("Plane", "Group"):
                    self._icon(
                        "active_on" if active else "active_off",
                        "Make active",
                        partial(self.model.activate, row.key),
                        f"clayers_A:{row.key}",
                    )
                else:
                    ui.Spacer(width=CONTROL_WIDTH)
            if row.key in self._selected:
                ui.Rectangle(
                    style={"background_color": 0, "border_color": 0xFF3399FF, "border_width": 1.5},
                    opaque_for_mouse_events=False,
                )

    def _toggle_expanded(self, key: str) -> None:
        if key in self._expanded:
            self._expanded.remove(key)
        else:
            self._expanded.add(key)
        self.model.refresh()

    def _select(self, row: Row, _x: float, _y: float, button: int, modifiers: int) -> None:
        if button != 0:
            return
        if modifiers & int(carb.input.KEYBOARD_MODIFIER_FLAG_CONTROL):
            self._selected.symmetric_difference_update({row.key})
        else:
            self._selected = {row.key}
        paths: list[str] = []
        key: str
        for key in self._selected:
            paths.extend(self.model.group_paths(key) if key in self.model.groups else [key])
        self.model.select(paths)
        self.model.refresh()

    def on_key(self, key: int, _modifiers: int, pressed: bool) -> None:
        if pressed and key == int(carb.input.KeyboardInput.ESCAPE):
            self._selected.clear()
            self.model.select([])
            self.model.refresh()

    def _add_group(self, with_selection: bool) -> None:
        if self.model.stage is None:
            return
        paths: list[str] = self.model.selected_paths() if with_selection else []
        if with_selection and not paths:
            return
        parent: str = ROOT_GROUP
        key: str
        for key in sorted(self._selected):
            parent = (
                self.model.groups[key].parent if key in self.model.groups else self.model.group_for(key) or ROOT_GROUP
            )
            break
        names: set[str] = set()
        group: Group
        for group in self.model.groups.values():
            names.add(group.name)
        index: int = 1
        while f"Group {index}" in names:
            index += 1
        key = self.model.create_group(f"Group {index}", parent, paths)
        self._selected = {key}
        self._expanded.add(key)

    def _ungroup(self) -> None:
        key: str
        for key in tuple(self._selected):
            if key in self.model.groups:
                self.model.ungroup(key)
        self._selected.clear()

    def destroy(self) -> None:
        self.model.destroy()
        self._frame.clear()
        self._images.clear()
