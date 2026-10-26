# Python & library imports
import json
from pathlib import Path
from typing import Any, cast

# Omniverse & USD imports
import carb.settings
import omni.ui as ui

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

# app setting for window assignments
LAYOUT_SETTINGS_PATH = "/app/launcher/layout"


# ----------------------------------------------------------------------
# 2. WINDOW ASSIGNER
# ----------------------------------------------------------------------


class WindowAssigner:
    # Read window assignments and track the newest layout request.
    def __init__(self):
        settings: Any = carb.settings.get_settings()
        self.assignments = cast(dict[str, str], settings.get(LAYOUT_SETTINGS_PATH) or {})

        # main layout starts with its empty placeholder
        self.main_window_name = "main_window"

        # window currently occupying the main slot
        self._current_main = "main_window"

        # newest layout load request wins
        self._load_number = 0

    # get assigned window name
    def window_name(self, layout_name: str) -> str:
        return self.assignments.get(layout_name, layout_name)

    # set the window that replaces main_window
    def set_main_window(self, window_name: str):
        self.main_window_name = window_name

    # register a new load request and get its number
    def request_load(self) -> int:
        self._load_number += 1
        return self._load_number

    # check whether a load request is still the newest
    def is_latest(self, load_number: int) -> bool:
        return load_number == self._load_number

    # read the layout file with assignments applied
    def build_layout(self, layout_path: Path) -> Any:
        with layout_path.open(encoding="utf-8") as layout_file:
            layout = json.load(layout_file)
        self.apply_layout_assignments(layout)
        return layout

    # swap the main slot to the active window
    def update_main_slot(self):
        # hide the window previously in the main slot
        if self.main_window_name != self._current_main:
            previous_window = ui.Workspace.get_window(self._current_main)
            if previous_window:
                previous_window.visible = False
            self._current_main = self.main_window_name

        # show and select the active window
        active_window = ui.Workspace.get_window(self.main_window_name)
        if active_window:
            ui.Workspace.show_window(self.main_window_name, True)
            cast(Any, active_window).focus()

    # replace layout names with assignments
    def apply_layout_assignments(self, value: Any):
        if isinstance(value, dict):
            node = cast(dict[str, Any], value)
            # Let Kit position docked nodes for the current monitor and DPI scale.
            node.pop("position_x", None)
            node.pop("position_y", None)
            title = node.get("title")

            # replace main position with active tab window
            if title == "main_window":
                node["title"] = self.main_window_name
            elif title in self.assignments:
                node["title"] = self.assignments[title]

            for child in node.values():
                self.apply_layout_assignments(child)
        elif isinstance(value, list):
            for child in cast(list[Any], value):
                self.apply_layout_assignments(child)
