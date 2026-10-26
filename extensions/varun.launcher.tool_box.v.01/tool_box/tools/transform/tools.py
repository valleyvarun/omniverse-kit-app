# Python & library imports
from __future__ import annotations

import logging
from typing import Any, cast

# Omniverse & USD imports
import carb.settings
import omni.usd

# Local imports
from tool_box.core.resources import ICONS_DIR as _ICONS_DIR
from tool_box.core.tool import Tool
from tool_box.core.viewport import get_active_usd_context

# ----------------------------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# ----------------------------------------------------------------------

LOGGER = logging.getLogger(__name__)


# Setting path read by omni.kit.manipulator.transform / .prim to pick the active gizmo.
TRANSFORM_OP_SETTING = "/app/transform/operation"

OP_SELECT = "select"
OP_MOVE = "move"
OP_ROTATE = "rotate"
OP_SCALE = "scale"


# ----------------------------------------------------------------------
# 2. HELPER FUNCTIONS
# ----------------------------------------------------------------------


# Switch the active viewport transform manipulator (Move / Rotate / Scale / Select).
def set_transform_op(op: str) -> None:
    settings = cast(Any, carb.settings.get_settings())
    settings.set_string(TRANSFORM_OP_SETTING, op)


# ----------------------------------------------------------------------
# 3. TRANSFORM TOOLS
# ----------------------------------------------------------------------


# Builds the standard Move / Rotate / Scale toolbar tools that match USD Composer.
class TransformTools:
    # Track transform buttons and their subscriptions.
    def __init__(self) -> None:
        self._tools_by_op: dict[str, Tool] = {}
        self._setting_sub: Any = None
        self._selection_sub: Any = None

    # Create Move, Rotate, and Scale tools and synchronize their highlights.
    def make_tools(self) -> list[Tool]:
        # Build one toggleable Tool per transform op and remember them so we
        # can flip is_active when the carb setting changes.
        specs = (
            (OP_MOVE, "Move", "move.svg", "Move (translate) the selected prim."),
            (OP_ROTATE, "Rotate", "rotate.svg", "Rotate the selected prim."),
            (OP_SCALE, "Scale", "scale.svg", "Scale the selected prim."),
        )
        tools: list[Tool] = []
        for op, name, icon, tooltip in specs:
            tool = Tool(
                name=name,
                icon=str(_ICONS_DIR / icon),
                tooltip=tooltip,
                on_click=lambda op=op: self._activate_op(op),
                toggleable=True,
                # When another toggleable tool (e.g. 3D paint / 3D pipe) becomes active,
                # drop the gizmo by switching the transform op to "select".
                on_deactivate=lambda op=op: self._deactivate_op(op),
            )
            self._tools_by_op[op] = tool
            tools.append(tool)

        # Subscribe to the setting so external changes (hotkeys, other UIs) also
        # update the button highlight.
        settings = cast(Any, carb.settings.get_settings())
        try:
            self._setting_sub = settings.subscribe_to_node_change_events(TRANSFORM_OP_SETTING, self._on_setting_changed)
        except Exception as exc:  # pragma: no cover - depends on carb runtime
            LOGGER.warning("Could not subscribe to %s: %s", TRANSFORM_OP_SETTING, exc, exc_info=True)

        # Subscribe to USD selection changes so we can clear the highlight when
        # the user deselects (Esc / clicks empty space) — no transform tool is
        # actually being applied without a selection.
        self.bind_context()

        # Default to no active tool until the user picks one with a selection.
        self._refresh_active()

        return tools

    # Activate a transform even when drawing has cleared the selection.
    def _activate_op(self, op: str) -> None:
        # Stop a drawing tool even when there is no current prim selection.
        self._tools_by_op[op].set_active(True)
        set_transform_op(op)

    # Follow selection events when the host switches to another viewport.
    def bind_context(self) -> None:
        self._selection_sub = None
        context = get_active_usd_context()
        if context is not None:
            self._selection_sub = context.get_stage_event_stream().create_subscription_to_pop(
                self._on_stage_event, name="tool_box.transform.selection"
            )
        self._refresh_active()

    # Release transform-setting and selection subscriptions.
    def destroy(self) -> None:
        if self._setting_sub is not None:
            try:
                settings = cast(Any, carb.settings.get_settings())
                settings.unsubscribe_to_change_events(self._setting_sub)
            except Exception:
                LOGGER.debug("Drawing tool SDK operation failed", exc_info=True)
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
            LOGGER.debug("Drawing tool SDK operation failed", exc_info=True)
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
            usd_context = get_active_usd_context()
            selection = usd_context.get_selection()
            paths = selection.get_selected_prim_paths()
        except Exception:
            LOGGER.debug("Drawing tool SDK operation failed", exc_info=True)
            return False
        return bool(paths)

    # Highlight the matching tool button and clear the others. Pass None to clear all.
    def _sync_active(self, op: str | None) -> None:
        for tool_op, tool in self._tools_by_op.items():
            tool.set_active(op is not None and tool_op == op)

    # Called by Tool's mutual-exclusion when another toggleable tool is picked.
    # Switch the carb op to "select" so the manipulator gizmo disappears; the
    # setting subscription will then clear the highlight via _refresh_active.
    def _deactivate_op(self, op: str) -> None:
        settings = cast(Any, carb.settings.get_settings())
        try:
            current = settings.get(TRANSFORM_OP_SETTING) or OP_SELECT
        except Exception:
            LOGGER.debug("Drawing tool SDK operation failed", exc_info=True)
            current = OP_SELECT
        if current == op:
            set_transform_op(OP_SELECT)
        # Drop the highlight immediately even if the setting callback is async.
        tool = self._tools_by_op.get(op)
        if tool is not None and tool.is_active:
            tool.is_active = False
            if tool._button is not None:
                cast(Any, tool._button).selected = False
