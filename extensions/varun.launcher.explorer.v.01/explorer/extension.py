"""Explorer window, Omniverse folder picker, and new-item dialogs."""

from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, cast

import carb.input
import omni.ext
import omni.kit.app
from omni import ui
from omni.kit.window.filepicker import FilePickerDialog

from . import filesystem as fs
from .tree import ExplorerTree, Node

ICONS: Path = Path(__file__).resolve().parent / "icons"
PROJECT_ROOT: Path = Path(__file__).resolve().parents[4]
ROW_HEIGHT: int = 28
INDENT_WIDTH: int = 16
TOGGLE_WIDTH: int = 20
TOGGLE_SIZE: int = 12
GUIDE_STYLE: dict[str, int] = {"background_color": 0xFF606060}
TEXT_STYLE: dict[str, int] = {"color": 0xFFD8D8D8}
ICON_BUTTON_STYLE: dict[str, dict[str, int]] = {
    "Button": {"background_color": 0, "border_width": 0, "border_radius": 3, "padding": 0, "margin": 0},
    "Button:hovered": {"background_color": 0xFF494949},
    "Button:pressed": {"background_color": 0xFF5C5C5C},
}


class ExplorerExtension(omni.ext.IExt):
    def on_startup(self, _ext_id: str) -> None:
        self._alive: bool = True
        self._folder_dialog: FilePickerDialog | None = None
        self._create_dialog: ui.Window | None = None
        self._create_model: ui.SimpleStringModel | None = None
        self._create_error: ui.Label | None = None
        self._create_button: ui.Button | None = None
        self._create_parent: Node | None = None
        self._create_directory: bool = False
        self._creating: bool = False
        self._dialog_number: int = 0
        self._tree_frame: ui.Frame | None = None
        self._displayed_root: Node | None = None
        self._status: ui.Label | None = None
        self._menu: ui.Menu | None = None
        self._retired_dialogs: list[FilePickerDialog | ui.Window] = []
        self._tree: ExplorerTree = ExplorerTree(self._on_changed)
        self._window: ui.Window = ui.Window(
            "Explorer", width=130, flags=ui.WINDOW_FLAGS_NO_TITLE_BAR | ui.WINDOW_FLAGS_NO_MOVE
        )
        self._window.frame.set_build_fn(self._build)
        self._window.set_key_pressed_fn(self._on_key)
        ui.Workspace.set_show_window_fn("Explorer", self._show)

    def _show(self, visible: bool) -> None:
        self._window.visible = visible

    def _on_changed(self) -> None:
        if not self._alive:
            return
        if self._tree.root is not self._displayed_root or self._tree.root is None:
            self._window.frame.rebuild()
        elif self._tree_frame is not None:
            self._tree_frame.rebuild()
        if self._status is not None:
            self._status.text = self._tree.error
            self._status.visible = bool(self._tree.error)

    # The initial state contains only the centered folder-opening action.
    def _build(self) -> None:
        self._displayed_root = self._tree.root
        self._tree_frame = None
        with ui.ZStack():
            if self._tree.root is None:
                with ui.VStack(spacing=0):
                    ui.Spacer()
                    with ui.HStack(height=32):
                        ui.Spacer()
                        ui.Button(
                            "Opening…" if self._tree.opening else "Open Folder",
                            width=120,
                            height=32,
                            identifier="explorer_open_folder",
                            style=TEXT_STYLE,
                            clicked_fn=self.show_folder_picker,
                            enabled=not self._tree.opening,
                        )
                        ui.Spacer()
                    ui.Spacer()
                    self._status = ui.Label(
                        self._tree.error,
                        word_wrap=True,
                        visible=bool(self._tree.error),
                        height=0,
                        style={"color": 0xFF9999FF},
                    )
                return
            with ui.VStack(spacing=0):
                with ui.VStack(height=36, spacing=0):
                    ui.Spacer(height=4)
                    with ui.HStack(height=ROW_HEIGHT, spacing=2):
                        ui.Spacer(width=4)
                        self._icon_button("folders", "Open Folder", self.show_folder_picker, "explorer_folders")
                        ui.Spacer()
                        self._icon_button(
                            "new_file", "New File", partial(self.show_create_dialog, False), "explorer_new_file"
                        )
                        self._icon_button(
                            "new_folder", "New Folder", partial(self.show_create_dialog, True), "explorer_new_folder"
                        )
                        ui.Spacer(width=4)
                    ui.Spacer(height=4)
                with ui.ScrollingFrame(horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF):
                    self._tree_frame = ui.Frame(build_fn=self._build_rows)
                self._status = ui.Label(
                    self._tree.error,
                    word_wrap=True,
                    visible=bool(self._tree.error),
                    height=0,
                    style={"color": 0xFF9999FF},
                )

    # Center the complete icon button inside its fixed-height row cell.
    def _icon_button(
        self,
        icon: str,
        tooltip: str,
        callback: Callable[[], None],
        identifier: str,
        width: int = 28,
        image_size: int = 20,
    ) -> None:
        with ui.VStack(width=width, height=ROW_HEIGHT, spacing=0):
            ui.Spacer()
            with ui.HStack(height=image_size, spacing=0):
                ui.Spacer()
                ui.Button(
                    "",
                    image_url=str(ICONS / f"{icon}.svg"),
                    image_width=image_size,
                    image_height=image_size,
                    width=image_size,
                    height=image_size,
                    identifier=identifier,
                    tooltip=tooltip,
                    clicked_fn=callback,
                    style=ICON_BUTTON_STYLE,
                )
                ui.Spacer()
            ui.Spacer()

    # Each ancestor owns a guide column through all its visible descendants.
    def _build_indent(self, depth: int) -> None:
        level: int
        with ui.HStack(width=4 + depth * INDENT_WIDTH, height=ROW_HEIGHT, spacing=0):
            ui.Spacer(width=4)
            for level in range(depth):
                with ui.HStack(width=INDENT_WIDTH, height=ROW_HEIGHT, spacing=0):
                    ui.Spacer(width=TOGGLE_WIDTH // 2)
                    ui.Rectangle(width=1, height=ROW_HEIGHT, style=GUIDE_STYLE, opaque_for_mouse_events=False)
                    ui.Spacer(width=INDENT_WIDTH - TOGGLE_WIDTH // 2 - 1)

    def _build_rows(self) -> None:
        node: Node
        depth: int
        with ui.VStack(spacing=0):
            for node, depth in self._tree.rows():
                with ui.ZStack(height=ROW_HEIGHT):
                    ui.Rectangle(style={"background_color": 0xFF554332 if node is self._tree.selected else 0})
                    # The control and name share one fixed-height horizontal row.
                    with ui.HStack(height=ROW_HEIGHT, spacing=0, identifier=f"explorer_row:{node.url}"):
                        self._build_indent(depth)
                        if node.directory:
                            with ui.ZStack(width=TOGGLE_WIDTH, height=ROW_HEIGHT):
                                if node.expanded:
                                    with ui.VStack(spacing=0):
                                        ui.Spacer(height=(ROW_HEIGHT + TOGGLE_SIZE) // 2)
                                        with ui.HStack(spacing=0):
                                            ui.Spacer(width=TOGGLE_WIDTH // 2)
                                            ui.Rectangle(width=1, style=GUIDE_STYLE, opaque_for_mouse_events=False)
                                            ui.Spacer()
                                self._icon_button(
                                    "minus" if node.expanded else "plus",
                                    "Collapse Folder" if node.expanded else "Expand Folder",
                                    partial(self._tree.toggle, node),
                                    f"explorer_toggle:{node.url}",
                                    width=TOGGLE_WIDTH,
                                    image_size=TOGGLE_SIZE,
                                )
                        else:
                            ui.Spacer(width=TOGGLE_WIDTH)
                        ui.Spacer(width=2)
                        label: ui.Label = ui.Label(
                            node.name,
                            width=ui.Fraction(1),
                            height=ROW_HEIGHT,
                            alignment=ui.Alignment.LEFT_CENTER,
                            style=TEXT_STYLE,
                            elided_text=True,
                            tooltip=node.url,
                            identifier=f"explorer_name:{node.url}",
                        )
                        label.set_mouse_pressed_fn(partial(self._on_row_pressed, node))
                        label.set_mouse_double_clicked_fn(partial(self._on_row_double_clicked, node))
                        ui.Spacer(width=4)
                if node.expanded and (node.loading or node.error or (node.loaded and not node.children)):
                    with ui.HStack(height=ROW_HEIGHT):
                        self._build_indent(depth + 1)
                        ui.Spacer(width=TOGGLE_WIDTH + 2)
                        ui.Label(
                            node.error or ("Loading…" if node.loading else "Empty folder"),
                            elided_text=True,
                            tooltip=node.error,
                            height=ROW_HEIGHT,
                            alignment=ui.Alignment.LEFT_CENTER,
                            style={"color": 0xFF9999FF if node.error else 0xFF999999},
                        )
            ui.Spacer()

    def _on_row_pressed(self, node: Node, x: float, y: float, button: int, _modifiers: int) -> None:
        self._tree.select(node)
        if button == 1:
            self._show_menu(x, y)

    def _on_row_double_clicked(self, node: Node, _x: float, _y: float, button: int, _modifiers: int) -> None:
        if button == 0 and node.directory:
            self._tree.toggle(node)

    # Files only select; opening applications and viewports is intentionally separate.
    def _on_key(self, key: int, _modifiers: int, pressed: bool) -> None:
        if not pressed or self._tree.root is None:
            return
        node: Node | None = self._tree.selected
        if key == int(carb.input.KeyboardInput.F5):
            self._tree.schedule(self._tree.refresh())
        elif node is not None:
            if key in (int(carb.input.KeyboardInput.ENTER), int(carb.input.KeyboardInput.RIGHT)):
                if node.directory and (not node.expanded or key == int(carb.input.KeyboardInput.ENTER)):
                    self._tree.toggle(node)
            elif key == int(carb.input.KeyboardInput.LEFT):
                if node.directory and node.expanded:
                    self._tree.toggle(node)
                elif node.parent is not None:
                    self._tree.select(node.parent)
            elif key in (int(carb.input.KeyboardInput.UP), int(carb.input.KeyboardInput.DOWN)):
                rows: list[tuple[Node, int]] = self._tree.rows()
                index: int
                row: tuple[Node, int]
                for index, row in enumerate(rows):
                    if row[0] is node:
                        target: int = index + (-1 if key == int(carb.input.KeyboardInput.UP) else 1)
                        self._tree.select(rows[max(0, min(target, len(rows) - 1))][0])
                        break

    def _show_menu(self, x: float, y: float) -> None:
        self._menu = ui.Menu("Explorer actions", menu_compatibility=False)
        with self._menu:
            ui.MenuItem("New File", triggered_fn=partial(self.show_create_dialog, False))
            ui.MenuItem("New Folder", triggered_fn=partial(self.show_create_dialog, True))
            ui.MenuItem("Refresh", triggered_fn=self._refresh)
            ui.MenuItem("Open Folder…", triggered_fn=self.show_folder_picker)
            ui.MenuItem("Close Folder", triggered_fn=self._tree.close_folder)
        cast(Any, self._menu).show_at(x, y)

    def _refresh(self) -> None:
        self._tree.schedule(self._tree.refresh())

    # Use the same folder-only Omniverse picker as the reference app.
    def show_folder_picker(self) -> None:
        self._close_picker()
        self._folder_dialog = FilePickerDialog(
            "Open Folder",
            allow_multi_selection=False,
            apply_button_label="Open",
            click_apply_handler=self._accept_folder,
            click_cancel_handler=self._cancel_folder,
            item_filter_options=["Folders"],
            item_filter_fn=self._folder_filter,
            current_directory=self._tree.root.url if self._tree.root else str(PROJECT_ROOT),
        )

    @staticmethod
    def _folder_filter(item: Any) -> bool:
        return not item or bool(item.is_folder)

    def _accept_folder(self, filename: str, dirname: str | None) -> None:
        if self._folder_dialog is None:
            return
        selections: list[str] = self._folder_dialog.get_current_selections() or []
        folder: str = dirname or self._folder_dialog.get_current_directory()
        if selections:
            folder = selections[0]
        elif filename:
            folder = (
                fs.normalize(filename)
                if ":" in filename or filename.startswith("/")
                else fs.child_url(fs.normalize(folder), filename)
            )
        self._close_picker()
        self._tree.schedule(self._tree.open_folder(folder))

    def _cancel_folder(self, _filename: str, _dirname: str | None) -> None:
        self._close_picker()

    def _close_picker(self) -> None:
        if self._folder_dialog is not None:
            self._retire_dialog(self._folder_dialog)
            self._folder_dialog = None

    # Kit cannot destroy a window from inside its own click callback.
    def _retire_dialog(self, dialog: FilePickerDialog | ui.Window) -> None:
        if not self._alive:
            dialog.destroy()
            return
        if isinstance(dialog, FilePickerDialog):
            dialog.hide()
        else:
            dialog.visible = False
        self._retired_dialogs.append(dialog)
        self._tree.schedule(self._destroy_dialog_later(dialog))

    async def _destroy_dialog_later(self, dialog: FilePickerDialog | ui.Window) -> None:
        await cast(Any, omni.kit.app.get_app()).next_update_async()
        dialog.destroy()
        self._retired_dialogs.remove(dialog)

    # Naming dialogs retain invalid input and show errors beside it.
    def show_create_dialog(self, directory: bool) -> None:
        parent: Node | None = self._tree.destination()
        if parent is None:
            return
        self._close_create_dialog()
        self._create_parent = parent
        self._create_directory = directory
        self._create_model = ui.SimpleStringModel("")
        self._create_dialog = ui.Window(
            "New Folder" if directory else "New File",
            width=390,
            height=160,
            flags=ui.WINDOW_FLAGS_NO_DOCKING | ui.WINDOW_FLAGS_NO_RESIZE,
        )
        self._create_dialog.set_key_pressed_fn(self._on_create_key)
        with self._create_dialog.frame, ui.VStack(spacing=8):
            ui.Label(f"Create in {parent.name}", height=24, tooltip=parent.url, style=TEXT_STYLE)
            field: ui.StringField = ui.StringField(self._create_model, height=28, identifier="explorer_create_name")
            self._create_error = ui.Label("", word_wrap=True, height=32, style={"color": 0xFF9999FF})
            with ui.HStack(height=28, spacing=8):
                ui.Spacer()
                self._create_button = ui.Button(
                    "Create", width=90, identifier="explorer_create_confirm", clicked_fn=self._submit_create
                )
                ui.Button("Cancel", width=90, clicked_fn=self._close_create_dialog)
        self._tree.schedule(self._focus_name(field, self._dialog_number))

    async def _focus_name(self, field: ui.StringField, dialog_number: int) -> None:
        await cast(Any, omni.kit.app.get_app()).next_update_async()
        if self._alive and self._create_dialog is not None and dialog_number == self._dialog_number:
            cast(Any, self._create_dialog).focus()
            cast(Any, field).focus_keyboard(True)

    def _on_create_key(self, key: int, _modifiers: int, pressed: bool) -> None:
        if pressed:
            if key == int(carb.input.KeyboardInput.ESCAPE):
                self._close_create_dialog()
            elif key in (int(carb.input.KeyboardInput.ENTER), int(carb.input.KeyboardInput.NUMPAD_ENTER)):
                self._submit_create()

    def _submit_create(self) -> None:
        if self._creating or self._create_model is None or self._create_parent is None:
            return
        self._creating = True
        if self._create_button is not None:
            self._create_button.enabled = False
        self._tree.schedule(
            self._create(self._create_parent, self._create_model.as_string, self._create_directory, self._dialog_number)
        )

    async def _create(self, parent: Node, name: str, directory: bool, dialog_number: int) -> None:
        error: OSError | ValueError
        try:
            await self._tree.create(parent, name, directory)
        except (OSError, ValueError) as error:
            if self._alive and dialog_number == self._dialog_number and self._create_error is not None:
                self._create_error.text = str(error)
        else:
            if self._alive and dialog_number == self._dialog_number:
                self._close_create_dialog()
        finally:
            if self._alive and dialog_number == self._dialog_number:
                self._creating = False
                if self._create_button is not None:
                    self._create_button.enabled = True

    def _close_create_dialog(self) -> None:
        self._dialog_number += 1
        if self._create_dialog is not None:
            self._retire_dialog(self._create_dialog)
            self._create_dialog = None
        self._create_model = self._create_error = self._create_button = self._create_parent = None
        self._creating = False

    def on_shutdown(self) -> None:
        self._alive = False
        self._close_picker()
        self._close_create_dialog()
        self._tree.destroy()
        dialog: FilePickerDialog | ui.Window
        for dialog in self._retired_dialogs:
            dialog.destroy()
        self._retired_dialogs.clear()
        self._menu = None
        cast(Any, ui.Workspace).set_show_window_fn("Explorer", None)
        self._window.destroy()
