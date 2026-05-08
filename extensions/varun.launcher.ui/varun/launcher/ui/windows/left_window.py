from pathlib import Path
from typing import Any

import omni.ui as ui
from omni.kit.window.filepicker import FilePickerDialog

from ..styles import AGENT_WINDOW_BACKGROUND_STYLE
from .window import LauncherWindow


PROJECT_ROOT = Path(__file__).resolve().parents[7]


class LeftWindow(LauncherWindow):
    def __init__(self) -> None:
        # Keep a handle to the folder dialog so it can be replaced or closed cleanly.
        self._folder_dialog: FilePickerDialog | None = None
        super().__init__(
            title="Explorer",
            width=100,
        )

    def _on_folder_dialog_apply(self, filename: str, dirname: str) -> None:
        # Close the popup after the user accepts the current folder selection.
        self._close_folder_dialog()

    def _on_folder_dialog_cancel(self, filename: str, dirname: str) -> None:
        # Close the popup when the user cancels the dialog.
        self._close_folder_dialog()

    def _filter_folder_items(self, item: Any) -> bool:
        # Only show folder entries inside the project-folder picker.
        return not item or bool(item.is_folder)

    def _show_project_folder_dialog(self) -> None:
        # Recreate the dialog each time so the popup opens in a clean state.
        if self._folder_dialog is not None:
            self._folder_dialog.destroy()

        self._folder_dialog = FilePickerDialog(
            "Open Project Folder",
            allow_multi_selection=False,
            apply_button_label="Open",
            click_apply_handler=self._on_folder_dialog_apply,
            click_cancel_handler=self._on_folder_dialog_cancel,
            item_filter_options=["Folders"],
            item_filter_fn=self._filter_folder_items,
            current_directory=str(PROJECT_ROOT),
        )

    def _close_folder_dialog(self) -> None:
        # Destroy the popup instance once it is no longer needed.
        if self._folder_dialog is not None:
            self._folder_dialog.destroy()
            self._folder_dialog = None

    def _build_ui(self) -> None:
        if not self._window:
            return

        with self._window.frame:
            with ui.ZStack():
                # Fill the panel with the same dark background used by the Agent side.
                ui.Rectangle(style=AGENT_WINDOW_BACKGROUND_STYLE)
                with ui.VStack(width=ui.Fraction(1), height=ui.Fraction(1)):
                    # Center the explorer action button in the middle of the panel.
                    ui.Spacer()
                    with ui.HStack(width=ui.Fraction(1), height=0):
                        ui.Spacer()
                        ui.Button("open project folder", width=135, height=32, clicked_fn=self._show_project_folder_dialog)
                        ui.Spacer()
                    ui.Spacer()