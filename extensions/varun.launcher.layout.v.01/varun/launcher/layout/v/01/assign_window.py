from typing import Any, cast

import carb.settings

# app setting for window assignments
LAYOUT_SETTINGS_PATH = "/app/launcher/layout"


class WindowAssigner:

	def __init__(self):
		settings: Any = carb.settings.get_settings()
		self.assignments = cast(
			dict[str, str], settings.get(LAYOUT_SETTINGS_PATH) or {}
		)

		# main layout starts with its empty placeholder
		self.main_window_name = "main_window"

	# get assigned window name
	def window_name(self, layout_name: str) -> str:
		return self.assignments.get(layout_name, layout_name)

	# set the window that replaces main_window
	def set_main_window(self, window_name: str):
		self.main_window_name = window_name

	# replace layout names with assignments
	def apply_layout_assignments(self, value: Any):
		if isinstance(value, dict):
			node = cast(dict[str, Any], value)
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
