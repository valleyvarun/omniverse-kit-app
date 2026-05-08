from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

import carb.settings
import omni.usd

from .tool import Tool


LOGGER = logging.getLogger(__name__)


# Setting path read by omni.kit.manipulator.transform / .prim to pick the active gizmo.
TRANSFORM_OP_SETTING = "/app/transform/operation"

OP_SELECT = "select"
OP_MOVE = "move"
OP_ROTATE = "rotate"
OP_SCALE = "scale"


# Resolve to <python_package>/logos/<name>.svg (sibling of the `tools` package).
_ICONS_DIR = Path(__file__).resolve().parent.parent / "logos"


# Switch the active viewport transform manipulator (Move / Rotate / Scale / Select).
def set_transform_op(op: str) -> None:
    settings = cast(Any, carb.settings.get_settings())
    settings.set_string(TRANSFORM_OP_SETTING, op)


# Builds the standard Move / Rotate / Scale toolbar tools that match USD Composer.
class TransformTools:
    def __init__(self) -> None:
        self._tools_by_op: dict[str, Tool] = {}
        self._setting_sub: Any = None
        self._selection_sub: Any = None

    def make_tools(self) -> list[Tool]:
        # Build one toggleable Tool per transform op and remember them so we
        # can flip is_active when the carb setting changes.
        specs = (
            (OP_MOVE, "Move", "move.svg", "M", "Move (translate) the selected prim."),
            (OP_ROTATE, "Rotate", "rotate.svg", "R", "Rotate the selected prim."),
            (OP_SCALE, "Scale", "scale.svg", "S", "Scale the selected prim."),
        )
        tools: list[Tool] = []
        for op, name, icon, shortcut, tooltip in specs:
            tool = Tool(
                name=name,
                icon=str(_ICONS_DIR / icon),
                shortcut=shortcut,
                tooltip=tooltip,
                on_click=lambda op=op: set_transform_op(op),
                toggleable=True,
            )
            self._tools_by_op[op] = tool
            tools.append(tool)

        # Subscribe to the setting so external changes (hotkeys, other UIs) also
        # update the button highlight.
        settings = cast(Any, carb.settings.get_settings())
        try:
            self._setting_sub = settings.subscribe_to_node_change_events(
                TRANSFORM_OP_SETTING, self._on_setting_changed
            )
        except Exception as exc:  # pragma: no cover - depends on carb runtime
            LOGGER.warning("Could not subscribe to %s: %s", TRANSFORM_OP_SETTING, exc)

        # Subscribe to USD selection changes so we can clear the highlight when
        # the user deselects (Esc / clicks empty space) — no transform tool is
        # actually being applied without a selection.
        try:
            usd_context = cast(Any, omni.usd.get_context())
            event_stream = usd_context.get_stage_event_stream()
            self._selection_sub = event_stream.create_subscription_to_pop(
                self._on_stage_event, name="varun.launcher.ui transform selection"
            )
        except Exception as exc:  # pragma: no cover - depends on omni.usd runtime
            LOGGER.warning("Could not subscribe to stage events: %s", exc)

        # Default to no active tool until the user picks one with a selection.
        self._refresh_active()

        return tools

    def destroy(self) -> None:
        if self._setting_sub is not None:
            try:
                settings = cast(Any, carb.settings.get_settings())
                settings.unsubscribe_to_change_events(self._setting_sub)
            except Exception:
                pass
            self._setting_sub = None
        # omni.usd subscriptions are released by dropping the handle.
        self._selection_sub = None
        self._tools_by_op.clear()

    # carb settings change callback. Signature: (item, event_type) -> None.
    def _on_setting_changed(self, *_args: object, **_kwargs: object) -> None:
        self._refresh_active()

    # USD stage event callback. Re-sync on selection-changed events.
    def _on_stage_event(self, event: Any) -> None:
        try:
            event_type = int(event.type)
            selection_changed = int(omni.usd.StageEventType.SELECTION_CHANGED)
        except Exception:
            return
        if event_type == selection_changed:
            self._refresh_active()

    # Read current selection + setting and update which button is highlighted.
    def _refresh_active(self) -> None:
        # Nothing selected -> no transform op is being applied, clear highlight.
        if not self._has_selection():
            self._sync_active(None)
            return
        settings = cast(Any, carb.settings.get_settings())
        current = settings.get(TRANSFORM_OP_SETTING) or OP_SELECT
        self._sync_active(current)

    # True iff the active stage has at least one prim selected.
    def _has_selection(self) -> bool:
        try:
            usd_context = cast(Any, omni.usd.get_context())
            selection = usd_context.get_selection()
            paths = selection.get_selected_prim_paths()
        except Exception:
            return False
        return bool(paths)

    # Highlight the matching tool button and clear the others. Pass None to clear all.
    def _sync_active(self, op: str | None) -> None:
        for tool_op, tool in self._tools_by_op.items():
            tool.set_active(op is not None and tool_op == op)
