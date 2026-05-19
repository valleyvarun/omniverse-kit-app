"""Centralized settings and the properties panel for the Transform tools.

The user-facing `TransformPropertiesPanel` (rendered in the bottom Tool
Properties dock when Move / Rotate / Scale is active) lives here so every
Transform-specific knob is colocated. The underlying op constants and the
`TRANSFORM_OP_SETTING` carb key remain in `transform_tools.py`, which is
where the toolbar tools register them.
"""

from __future__ import annotations

# --- Standard library imports ---------------------------------------------
# `logging` for non-fatal warnings; `typing` for static-analysis friendliness.
import logging
from typing import Any, Callable, cast

# --- Carb / omni / pxr imports --------------------------------------------
# `carb.settings`         -> read/subscribe to the active transform-tool key.
# `omni.kit.commands`     -> issue undoable USD edits (TransformPrimSRTCommand).
# `omni.ui`               -> immediate-mode widgets (HStack, Label, FloatField, ...).
# `omni.usd`              -> stage access, selection, stage-event stream, helpers.
# `pxr` (Gf, Tf, Usd)     -> USD math types + change-notice subscriptions.
import carb.settings
import omni.kit.commands
import omni.ui as ui
import omni.usd
from pxr import Gf, Tf, Usd  # type: ignore[reportMissingTypeStubs]

from ...active_context import get_active_usd_context

# --- Local imports --------------------------------------------------------
# Generic panel base + shared XYZ row builder live in `tool_properties.py`
# alongside the dock window. `OP_*` and `TRANSFORM_OP_SETTING` come from
# the transform tool module right next door.
from ..tool_properties import ToolPropertiesPanel, build_xyz_row
from .transform_tools import OP_MOVE, OP_ROTATE, OP_SCALE, TRANSFORM_OP_SETTING


# Module-wide logger; warnings only (failures here are non-fatal UI glitches).
LOGGER = logging.getLogger(__name__)


# ============================================================================
# TRANSFORM PANEL (Move / Rotate / Scale)
# Shows an X/Y/Z row that reads from / writes to the selected prim's
# local SRT via the standard TransformPrimSRTCommand.
# ============================================================================
class TransformPropertiesPanel(ToolPropertiesPanel):
    def __init__(self, request_refresh: Callable[[], None]) -> None:
        super().__init__(request_refresh)

        # --- Subscription / listener handles (cleared on destroy). ---
        # `_setting_sub`: active-tool change in carb settings.
        # `_stage_sub`  : selection / stage open / stage close events.
        # `_objects_changed_listener`: pxr Tf notice for live USD edits.
        self._setting_sub: Any = None
        self._stage_sub: Any = None
        self._objects_changed_listener: Any = None

        # Live FloatDrag models + their subscription handles for the
        # currently-displayed XYZ row.
        self._field_models: list[Any] = []
        self._field_subs: list[Any] = []

        # When True, programmatic model writes will NOT issue write-back
        # USD commands. Used during _sync_fields_from_stage().
        self._suppress_writes = False

        # Last (op, prim path) we built widgets for; tracked so external
        # USD changes can be funneled into the right fields.
        self._current_op: str | None = None
        self._current_path: str | None = None

        # Subscribe: when the active transform tool changes (e.g. user
        # clicks Move/Rotate/Scale), rebuild the panel.
        settings = cast(Any, carb.settings.get_settings())
        try:
            self._setting_sub = settings.subscribe_to_node_change_events(
                TRANSFORM_OP_SETTING, lambda *_args: self._refresh()  # type: ignore[reportUnknownLambdaType]
            )
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("Could not subscribe to %s: %s", TRANSFORM_OP_SETTING, exc)

        # Subscribe: stage events (selection change / open / close) -> rebuild.
        try:
            usd_context = get_active_usd_context()
            event_stream = usd_context.get_stage_event_stream()
            self._stage_sub = event_stream.create_subscription_to_pop(
                self._on_stage_event, name="varun.launcher.ui transform properties"
            )
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("Could not subscribe to stage events: %s", exc)

    # Drop every subscription we hold. Called by the window when it's torn down.
    def destroy(self) -> None:
        # Carb settings unsubscribe.
        if self._setting_sub is not None:
            try:
                settings = cast(Any, carb.settings.get_settings())
                settings.unsubscribe_to_change_events(self._setting_sub)
            except Exception:
                pass
            self._setting_sub = None
        # Stage event sub: assigning None drops our reference -> auto-unsub.
        self._stage_sub = None
        # Tf.Notice sub.
        self._unregister_usd_listener()
        # Drop widget refs.
        self._field_models = []
        self._field_subs = []

    # ----- Window-facing API -----

    # Show this panel only when an actual transform tool is selected and
    # we have a single selected prim to edit.
    def is_active(self) -> bool:
        op = self._active_op()
        return op in (OP_MOVE, OP_ROTATE, OP_SCALE) and self._selected_path() is not None

    # Build the XYZ row into the supplied (cleared) frame.
    def build_into(self, frame: ui.Frame) -> None:
        # Re-read state at build time (selection / op may have just changed).
        op = self._active_op()
        path = self._selected_path()
        if op not in (OP_MOVE, OP_ROTATE, OP_SCALE) or not path:
            return
        # Pull current Translate/Rotate/Scale vector for the active op.
        values = self._read_values(op, path)
        if values is None:
            return

        # Cache (op, path) so external-edit notices can route correctly.
        self._current_op = op
        self._current_path = path
        # Register a live USD notice so gizmo / property-panel edits keep
        # our fields up to date without rebuilding the panel.
        self._register_usd_listener()

        # Actual UI: a vertical stack with top spacer + the XYZ row.
        with frame:
            with ui.VStack(spacing=4):
                ui.Spacer(height=6)
                self._field_models, self._field_subs = build_xyz_row(
                    label=self._label_for_op(op),
                    initial=values,
                    step=self._step_for_op(op),
                    on_changed=self._on_field_changed,
                )

    # ----- Stage / USD event handlers -----

    # Stage event handler: rebuild on selection / open / close. We compare
    # the integer event type against known StageEventType values.
    def _on_stage_event(self, event: Any) -> None:
        try:
            event_type = int(event.type)
            selection_changed = int(omni.usd.StageEventType.SELECTION_CHANGED)
            opened = int(omni.usd.StageEventType.OPENED)
            closed = int(omni.usd.StageEventType.CLOSED)
        except Exception:
            return
        if event_type in (selection_changed, opened, closed):
            self._refresh()

    # Tf.Notice callback: an arbitrary USD object changed. If any change
    # touches our currently-edited prim path, sync the field values from
    # USD without rebuilding (so the user keeps focus / cursor).
    def _on_objects_changed(self, notice: Any, _stage: Any) -> None:
        if not self._current_path:
            return
        try:
            # Property-only changes (most attribute edits).
            for path in notice.GetChangedInfoOnlyPaths():
                if str(path).startswith(self._current_path):
                    self._sync_fields_from_stage()
                    return
            # Structural changes (resyncs).
            for path in notice.GetResyncedPaths():
                if str(path).startswith(self._current_path):
                    self._sync_fields_from_stage()
                    return
        except Exception:
            pass

    # ----- Helpers -----

    # Read the current transform op string from carb settings (or None).
    def _active_op(self) -> str | None:
        settings = cast(Any, carb.settings.get_settings())
        return settings.get(TRANSFORM_OP_SETTING) or None

    # Return the path of the first selected prim (or None).
    def _selected_path(self) -> str | None:
        try:
            usd_context = get_active_usd_context()
            paths = usd_context.get_selection().get_selected_prim_paths()
        except Exception:
            return None
        return paths[0] if paths else None

    # Map an op key to a human label shown in the row.
    def _label_for_op(self, op: str) -> str:
        return {OP_MOVE: "Translate", OP_ROTATE: "Rotate", OP_SCALE: "Scale"}.get(op, op)

    # Drag step: scale moves in 0.01 increments, translate/rotate in 1.0.
    def _step_for_op(self, op: str) -> float:
        return 0.01 if op == OP_SCALE else 1.0

    # Subscribe to USD ObjectsChanged for the current stage (idempotent).
    def _register_usd_listener(self) -> None:
        if self._objects_changed_listener is not None:
            return
        try:
            usd_context = get_active_usd_context()
            stage = usd_context.get_stage()
            if stage is not None:
                self._objects_changed_listener = cast(Any, Tf.Notice).Register(
                    cast(Any, Usd.Notice).ObjectsChanged, self._on_objects_changed, stage
                )
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("Could not register USD listener: %s", exc)

    # Drop the USD ObjectsChanged subscription (if any).
    def _unregister_usd_listener(self) -> None:
        if self._objects_changed_listener is not None:
            try:
                self._objects_changed_listener.Revoke()
            except Exception:
                pass
            self._objects_changed_listener = None

    # Read the SRT vector matching the active op (translate / rotate / scale).
    def _read_values(self, op: str, path: str) -> tuple[float, float, float] | None:
        try:
            usd_context = get_active_usd_context()
            stage = usd_context.get_stage()
            if stage is None:
                return None
            prim = stage.GetPrimAtPath(path)
            if not prim:
                return None
            # Standard helper: returns (scale, rot_euler, rot_order, translation).
            get_srt = cast(Any, omni.usd).get_local_transform_SRT
            scale, rot_euler, _order, translation = get_srt(
                prim, cast(Any, Usd.TimeCode).Default()
            )
        except Exception as exc:
            LOGGER.warning("Failed to read transform for %s: %s", path, exc)
            return None
        # Pick the vector for the requested op.
        if op == OP_MOVE:
            v = translation
        elif op == OP_ROTATE:
            v = rot_euler
        else:
            v = scale
        return (float(v[0]), float(v[1]), float(v[2]))

    # Push current USD values into the field models WITHOUT triggering the
    # write-back path (otherwise we'd ping-pong writes back to USD).
    def _sync_fields_from_stage(self) -> None:
        if not self._current_op or not self._current_path or not self._field_models:
            return
        values = self._read_values(self._current_op, self._current_path)
        if values is None:
            return
        self._suppress_writes = True
        try:
            for model, value in zip(self._field_models, values):
                # Skip identical writes to avoid extra notify churn.
                if abs(model.get_value_as_float() - value) > 1e-6:
                    model.set_value(value)
        finally:
            self._suppress_writes = False

    # Field edit -> issue undoable TransformPrimSRTCommand for the changed axis.
    def _on_field_changed(self, axis_idx: int, value: float) -> None:
        # Suppress when we ourselves wrote the field (sync from USD).
        if self._suppress_writes:
            return
        if not self._current_op or not self._current_path:
            return
        # Read the current vector; we only mutate the changed axis.
        current = self._read_values(self._current_op, self._current_path)
        if current is None:
            return
        # No-op writes? Skip.
        if abs(current[axis_idx] - value) < 1e-9:
            return
        new_vec = list(current)
        new_vec[axis_idx] = value
        # Build the keyword for the right SRT component.
        gf_vec3d = cast(Any, Gf).Vec3d
        kwargs: dict[str, Any] = {"path": self._current_path}
        if self._current_op == OP_MOVE:
            kwargs["new_translation"] = gf_vec3d(*new_vec)
        elif self._current_op == OP_ROTATE:
            kwargs["new_rotation_euler"] = gf_vec3d(*new_vec)
        else:
            kwargs["new_scale"] = gf_vec3d(*new_vec)
        # Execute via the kit command system so it shows in undo history.
        try:
            cast(Any, omni.kit.commands).execute("TransformPrimSRTCommand", **kwargs)
        except Exception as exc:
            LOGGER.warning("TransformPrimSRTCommand failed: %s", exc)
